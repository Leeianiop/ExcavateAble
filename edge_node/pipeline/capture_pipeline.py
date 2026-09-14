"""
Fused Capture + AI Pipeline
Orchestrates camera capture with Hailo-8L segmentation/depth inference
to produce per-shot capture bundles (RGB + mask + depth + metadata).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from ..ai import DepthResult, HailoInferenceEngine, SegmentationResult
from ..capture import CameraCapture, CapturedShot
from ..config import EdgeNodeConfig

logger = logging.getLogger(__name__)


@dataclass
class ProcessedShot:
    shot_id: str
    shot_index: int
    session_id: str
    timestamp_ns: int
    rgb_path: Path
    raw_path: Optional[Path]
    mask_path: Path
    depth_path: Path
    metadata_path: Path
    segmentation: SegmentationResult = field(repr=False)
    depth: DepthResult = field(repr=False)
    shot: CapturedShot = field(repr=False)

    def to_dict(self) -> dict:
        return {
            "shot_id": self.shot_id,
            "shot_index": self.shot_index,
            "session_id": self.session_id,
            "timestamp_ns": self.timestamp_ns,
            "files": {
                "rgb": str(self.rgb_path),
                "raw": str(self.raw_path) if self.raw_path else None,
                "mask": str(self.mask_path),
                "depth": str(self.depth_path),
                "metadata": str(self.metadata_path),
            },
            "camera_metadata": self.shot.metadata.__dict__,
            "inference_stats": {
                "segmentation_ms": self.segmentation.stats.total_ms,
                "depth_ms": self.depth.stats.total_ms,
            },
        }


@dataclass
class CaptureBundle:
    bundle_id: str
    session_id: str
    shots: list[ProcessedShot]
    created_at_ns: int
    manifest_path: Path

    @property
    def num_shots(self) -> int:
        return len(self.shots)

    @property
    def total_size_bytes(self) -> int:
        total = 0
        for shot in self.shots:
            for p in (shot.rgb_path, shot.raw_path, shot.mask_path, shot.depth_path, shot.metadata_path):
                if p is not None and p.exists():
                    total += p.stat().st_size
        return total

    def manifest(self) -> dict:
        return {
            "bundle_id": self.bundle_id,
            "session_id": self.session_id,
            "created_at_ns": self.created_at_ns,
            "num_shots": self.num_shots,
            "bundle_version": "1.0",
            "hardware": {
                "sbc": "Raspberry Pi 5 (8GB)",
                "camera": "Raspberry Pi Camera Module",
                "ai_accelerator": "Hailo-8L AI HAT (13 TOPS)",
            },
            "shots": [s.to_dict() for s in self.shots],
        }


class CapturePipeline:
    def __init__(self, config: EdgeNodeConfig):
        self.config = config
        self.camera = CameraCapture(config.capture)
        self.ai = HailoInferenceEngine(config.hailo)

    @property
    def initialized(self) -> bool:
        return self.camera.initialized and self.ai.initialized

    def initialize(self) -> None:
        logger.info("Initializing capture pipeline...")
        self.camera.initialize()
        self.ai.initialize()
        logger.info(
            f"Capture pipeline ready. Hailo mode: "
            f"{'HW' if self.ai.hailo_available else 'MOCK'}"
        )

    def close(self) -> None:
        self.ai.close()
        self.camera.close()
        logger.info("Capture pipeline closed")

    def run_session(
        self,
        output_dir: Optional[str | Path] = None,
        num_shots: Optional[int] = None,
        shot_interval: Optional[float] = None,
    ) -> CaptureBundle:
        if not self.initialized:
            raise RuntimeError("Pipeline not initialized; call initialize() first")

        out_dir = Path(output_dir or self.config.storage.local_dir)
        session_out = out_dir / self.camera.session_id
        session_out.mkdir(parents=True, exist_ok=True)

        n = num_shots or self.config.capture.num_shots
        interval = (
            shot_interval
            if shot_interval is not None
            else self.config.capture.shot_interval_sec
        )

        logger.info(
            f"Starting fused capture+AI session: shots={n}, interval={interval}s, "
            f"out={session_out}"
        )
        processed: list[ProcessedShot] = []
        t_session_start = time.perf_counter()

        for i in range(n):
            shot = self.camera.capture_single(i)
            if interval > 0 and i < n - 1:
                time.sleep(interval)

            processed_shot = self._process_shot(shot, session_out)
            processed.append(processed_shot)
            logger.info(
                f"Shot {i + 1}/{n} [{processed_shot.shot_id}] "
                f"seg={processed_shot.segmentation.stats.infer_ms:.1f}ms "
                f"depth={processed_shot.depth.stats.infer_ms:.1f}ms"
            )

        bundle = self._finalize_bundle(processed, session_out)
        elapsed = time.perf_counter() - t_session_start
        logger.info(
            f"Capture bundle {bundle.bundle_id} complete: {bundle.num_shots} shots, "
            f"{bundle.total_size_bytes / (1024 * 1024):.1f}MB in {elapsed:.1f}s"
        )
        return bundle

    def _process_shot(self, shot: CapturedShot, session_dir: Path) -> ProcessedShot:
        seg, depth = self.ai.run_fused_pipeline(shot.rgb_array)

        rgb_path = session_dir / f"{shot.shot_id}_rgb.jpg"
        raw_path: Optional[Path] = None
        mask_path = session_dir / f"{shot.shot_id}_mask.png"
        depth_path = session_dir / f"{shot.shot_id}_depth.npy"
        meta_path = session_dir / f"{shot.shot_id}_meta.json"

        self._save_rgb(shot.rgb_array, rgb_path, self.config.capture.jpeg_quality)
        if shot.raw_array is not None:
            raw_path = session_dir / f"{shot.shot_id}_raw.npy"
            np.save(raw_path, shot.raw_array)
        self._save_mask(seg.foreground_mask, mask_path)
        np.save(depth_path, depth.depth_map.astype(np.float32))

        metadata = {
            "shot_id": shot.shot_id,
            "shot_index": shot.shot_index,
            "session_id": self.camera.session_id,
            "timestamp_ns": shot.timestamp_ns,
            "camera": shot.metadata.__dict__,
            "inference": {
                "segmentation_ms": seg.stats.total_ms,
                "depth_ms": depth.stats.total_ms,
                "segmentation_classes": seg.class_ids.tolist(),
                "segmentation_scores": seg.scores.tolist(),
            },
        }
        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2, default=str)

        return ProcessedShot(
            shot_id=shot.shot_id,
            shot_index=shot.shot_index,
            session_id=self.camera.session_id,
            timestamp_ns=shot.timestamp_ns,
            rgb_path=rgb_path,
            raw_path=raw_path,
            mask_path=mask_path,
            depth_path=depth_path,
            metadata_path=meta_path,
            segmentation=seg,
            depth=depth,
            shot=shot,
        )

    def _finalize_bundle(
        self, shots: list[ProcessedShot], session_dir: Path
    ) -> CaptureBundle:
        bundle_id = f"bundle_{self.camera.session_id}_{uuid.uuid4().hex[:8]}"
        manifest_path = session_dir / f"manifest_{bundle_id}.json"
        bundle = CaptureBundle(
            bundle_id=bundle_id,
            session_id=self.camera.session_id,
            shots=shots,
            created_at_ns=time.time_ns(),
            manifest_path=manifest_path,
        )
        with open(manifest_path, "w") as f:
            json.dump(bundle.manifest(), f, indent=2, default=str)
        return bundle

    @staticmethod
    def _save_rgb(arr: np.ndarray, path: Path, quality: int) -> None:
        try:
            from PIL import Image
            Image.fromarray(arr).save(path, format="JPEG", quality=quality)
            return
        except ImportError:
            pass
        try:
            import cv2
            bgr = arr[..., ::-1]
            cv2.imwrite(str(path), bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            return
        except ImportError:
            np.save(path.with_suffix(".npy"), arr)

    @staticmethod
    def _save_mask(mask: np.ndarray, path: Path) -> None:
        scaled = (mask.astype(np.uint8) * 255).clip(0, 255)
        try:
            from PIL import Image
            Image.fromarray(scaled, mode="L").save(path, format="PNG")
            return
        except ImportError:
            pass
        try:
            import cv2
            cv2.imwrite(str(path), scaled)
            return
        except ImportError:
            np.save(path.with_suffix(".npy"), mask)

    def __enter__(self) -> "CapturePipeline":
        self.initialize()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
