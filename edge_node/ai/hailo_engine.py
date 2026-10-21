"""
Hailo-8L AI Inference Engine
Wraps HailoRT runtime for running compiled .hef models on the Hailo-8L AI HAT.
Supports background segmentation (YOLOv8-seg) and monocular depth estimation (DPT-Lite).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import HailoConfig

logger = logging.getLogger(__name__)

# Hailo SDK symbols are resolved lazily so the module imports cleanly on
# dev machines (Windows/macOS) where the Hailo runtime is not installed.
_HAILO_REQUIRED_SYMBOLS = (
    "HEF",
    "VDevice",
    "HailoStreamInterface",
    "InferVStreams",
    "ConfigureParams",
    "InputVStreamParams",
    "OutputVStreamParams",
    "FormatType",
)
# Optional symbols that may not exist in older HailoRT versions.
_HAILO_OPTIONAL_SYMBOLS = (
    "HailoSchedulingAlgorithm",
)

_HAILO_AVAILABLE = False
for _pkg in ("hailo_platform", "hailort"):
    try:
        _mod = __import__(_pkg, fromlist=list(_HAILO_REQUIRED_SYMBOLS))
        for _name in _HAILO_REQUIRED_SYMBOLS:
            globals()[_name] = getattr(_mod, _name)
        # Try optional symbols individually (don't fail the whole import).
        for _name in _HAILO_OPTIONAL_SYMBOLS:
            try:
                globals()[_name] = getattr(_mod, _name)
            except AttributeError:
                globals()[_name] = None
        _HAILO_AVAILABLE = True
        logger.info(f"Hailo SDK loaded from package: {_pkg}")
        break
    except ImportError:
        continue
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Failed to import Hailo symbols from {_pkg}: {exc}")
        continue

if not _HAILO_AVAILABLE:
    logger.warning(
        "Hailo SDK not installed. Running AI in mock/simulation mode. "
        "Install from: https://github.com/hailo-ai/hailort"
    )


@dataclass
class InferenceStats:
    preprocess_ms: float = 0.0
    infer_ms: float = 0.0
    postprocess_ms: float = 0.0
    total_ms: float = 0.0

    def __post_init__(self) -> None:
        if self.total_ms == 0.0:
            self.total_ms = self.preprocess_ms + self.infer_ms + self.postprocess_ms


@dataclass
class SegmentationResult:
    mask: np.ndarray
    class_ids: np.ndarray
    scores: np.ndarray
    boxes_xyxy: np.ndarray
    stats: InferenceStats

    @property
    def foreground_mask(self) -> np.ndarray:
        if self.mask.ndim == 3:
            return self.mask.any(axis=0).astype(np.uint8)
        return self.mask.astype(np.uint8)


@dataclass
class DepthResult:
    depth_map: np.ndarray
    stats: InferenceStats

    def normalized(self) -> np.ndarray:
        d = self.depth_map.astype(np.float32)
        if d.max() - d.min() < 1e-6:
            return np.zeros_like(d)
        return (d - d.min()) / (d.max() - d.min())


class HailoInferenceEngine:
    # Class-level diagnostic flag (used in the static _yolov8_seg_decode).
    _logged_conf_stats: bool = False

    def __init__(self, config: HailoConfig):
        self.config = config
        self._target = None  # VDevice (shared across models)
        self._scheduler_enabled = False
        self._seg_hef = None
        self._depth_hef = None
        self._seg_network_group = None
        self._depth_network_group = None
        self._seg_activated_ctx = None
        self._depth_activated_ctx = None
        self._seg_infer = None
        self._depth_infer = None
        self._initialized = False
        self._initialized_models: set[str] = set()
        self._logged_seg_shapes = False

    def _resolve_hef_path(self, model_path: str) -> Path:
        """Resolve a model path. Relative paths are searched in:
        1. current working directory
        2. edge_node package directory (edge_node/<path>)
        Also tries swapping '-' and '_' in the filename for robustness.
        """
        candidates: list[Path] = []
        p = Path(model_path)
        candidates.append(p)
        # Try swapping - and _ in the filename
        alt_name = p.name.replace("-", "_") if "-" in p.name else p.name.replace("_", "-")
        if alt_name != p.name:
            candidates.append(p.with_name(alt_name))

        edge_root = Path(__file__).resolve().parent.parent  # edge_node/
        for c in list(candidates):
            candidates.append(edge_root / c)

        for c in candidates:
            if c.exists():
                return c
        return p  # return original so error message shows the expected path

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def hailo_available(self) -> bool:
        return _HAILO_AVAILABLE

    def initialize(self) -> None:
        if self._initialized:
            return

        if not _HAILO_AVAILABLE:
            logger.info("Hailo SDK unavailable — using mock AI inference mode")
            self._initialized = True
            return

        try:
            self._load_segmentation_model()
            self._load_depth_model()
            self._initialized = True
            logger.info(
                f"Hailo inference engine initialized: device_id={self.config.device_id} "
                f"models={sorted(self._initialized_models)}"
            )
        except Exception as exc:
            logger.error(f"Hailo init failed, falling back to mock mode: {exc}")
            self._cleanup()
            self._initialized = True

    def close(self) -> None:
        self._cleanup()
        self._initialized = False
        logger.info("Hailo inference engine closed")

    def run_segmentation(self, rgb_image: np.ndarray) -> SegmentationResult:
        if not self._initialized:
            raise RuntimeError("Engine not initialized; call initialize() first")

        t_start = time.perf_counter()
        if not _HAILO_AVAILABLE or "segmentation" not in self._initialized_models:
            return self._mock_segmentation(rgb_image, t_start)

        t0 = time.perf_counter()
        input_tensor = self._preprocess_seg(rgb_image)
        t_pre = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        raw_output = self._infer_segmentation(input_tensor)
        t_infer = (time.perf_counter() - t1) * 1000

        t2 = time.perf_counter()
        mask, class_ids, scores, boxes = self._postprocess_seg(raw_output, rgb_image.shape[:2])
        t_post = (time.perf_counter() - t2) * 1000

        if len(class_ids) == 0 or not np.any(mask):
            h_mask, h_class_ids, h_scores, h_boxes = self._heuristic_artifact_segmentation(
                rgb_image
            )
            if len(h_class_ids) > 0:
                logger.info(
                    "Segmentation fallback used heuristic artifact detection: "
                    f"{len(h_class_ids)} object(s)"
                )
                mask, class_ids, scores, boxes = h_mask, h_class_ids, h_scores, h_boxes

        stats = InferenceStats(
            preprocess_ms=t_pre,
            infer_ms=t_infer,
            postprocess_ms=t_post,
            total_ms=(time.perf_counter() - t_start) * 1000,
        )
        logger.debug(
            f"Segmentation infer: {stats.infer_ms:.1f}ms (total {stats.total_ms:.1f}ms)"
        )
        return SegmentationResult(
            mask=mask,
            class_ids=class_ids,
            scores=scores,
            boxes_xyxy=boxes,
            stats=stats,
        )

    def run_depth(self, rgb_image: np.ndarray) -> DepthResult:
        if not self._initialized:
            raise RuntimeError("Engine not initialized; call initialize() first")

        t_start = time.perf_counter()
        if not _HAILO_AVAILABLE or "depth" not in self._initialized_models:
            return self._mock_depth(rgb_image, t_start)

        t0 = time.perf_counter()
        input_tensor = self._preprocess_depth(rgb_image)
        t_pre = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        raw_output = self._infer_depth(input_tensor)
        t_infer = (time.perf_counter() - t1) * 1000

        t2 = time.perf_counter()
        depth_map = self._postprocess_depth(raw_output, rgb_image.shape[:2])
        t_post = (time.perf_counter() - t2) * 1000

        stats = InferenceStats(
            preprocess_ms=t_pre,
            infer_ms=t_infer,
            postprocess_ms=t_post,
            total_ms=(time.perf_counter() - t_start) * 1000,
        )
        logger.debug(
            f"Depth infer: {stats.infer_ms:.1f}ms (total {stats.total_ms:.1f}ms)"
        )
        return DepthResult(depth_map=depth_map, stats=stats)

    def run_fused_pipeline(
        self, rgb_image: np.ndarray
    ) -> tuple[SegmentationResult, DepthResult]:
        seg = self.run_segmentation(rgb_image)
        depth = self.run_depth(rgb_image)
        return seg, depth

    # ------------------------------------------------------------------
    # Segmentation model management
    # ------------------------------------------------------------------
    def _get_target(self):
        """Return the shared VDevice, creating it on first use.

        Enables the ROUND_ROBIN scheduler so that multiple network groups
        (segmentation + depth) can be active simultaneously. Without the
        scheduler, only one network can be activated at a time.
        """
        if self._target is not None:
            return self._target

        sched_algo = globals().get("HailoSchedulingAlgorithm")
        if sched_algo is not None:
            try:
                params = VDevice.create_params()
                params.scheduling_algorithm = sched_algo.ROUND_ROBIN
                self._target = VDevice(params=params)
                self._scheduler_enabled = True
                logger.info("Hailo VDevice created with ROUND_ROBIN scheduler")
                return self._target
            except Exception as exc:
                logger.warning(
                    f"Could not enable Hailo scheduler ({exc}); "
                    f"falling back to default VDevice (single active network)"
                )

        self._target = VDevice()
        self._scheduler_enabled = False
        logger.info("Hailo VDevice created without scheduler")
        return self._target

    def _load_segmentation_model(self) -> None:
        hef_path = self._resolve_hef_path(self.config.segmentation_model_path)
        if not hef_path.exists():
            logger.warning(
                f"Segmentation HEF not found at {hef_path}; seg will run in mock mode"
            )
            return

        hef = HEF(str(hef_path))
        target = self._get_target()
        configure_params = ConfigureParams.create_from_hef(
            hef, interface=HailoStreamInterface.PCIe
        )
        network_groups = target.configure(hef, configure_params)
        network_group = network_groups[0]

        input_vstreams_params = InputVStreamParams.make(
            network_group, quantized=False, format_type=FormatType.FLOAT32
        )
        output_vstreams_params = OutputVStreamParams.make(
            network_group, quantized=False, format_type=FormatType.FLOAT32
        )
        # InferVStreams takes the CONFIGURED network group, not the activated
        # context. When the scheduler is enabled, activation is automatic and
        # calling activate() is deprecated. Without the scheduler, we must
        # activate explicitly (and only one network can be active at a time).
        activated_ctx = None
        if not self._scheduler_enabled:
            activated_ctx = network_group.activate()
            activated_ctx.__enter__()

        infer = InferVStreams(
            network_group, input_vstreams_params, output_vstreams_params
        )
        # InferVStreams is a context manager; __enter__() sets up the
        # internal _infer_pipeline required by infer().
        infer.__enter__()

        self._seg_hef = hef
        self._seg_network_group = network_group
        self._seg_activated_ctx = activated_ctx
        self._seg_infer = infer
        self._initialized_models.add("segmentation")
        logger.info(f"Segmentation model loaded: {hef_path.name}")

    def _preprocess_seg(self, rgb: np.ndarray) -> np.ndarray:
        h, w = self.config.input_height, self.config.input_width
        img_h, img_w = rgb.shape[:2]

        scale = min(w / img_w, h / img_h)
        new_w, new_h = int(img_w * scale), int(img_h * scale)
        pad_w, pad_h = (w - new_w) // 2, (h - new_h) // 2

        try:
            import cv2
            resized = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        except ImportError:
            resized = self._numpy_resize(rgb, new_h, new_w)

        padded = np.zeros((h, w, 3), dtype=np.uint8)
        padded[pad_h:pad_h + new_h, pad_w:pad_w + new_w] = resized

        tensor = padded.astype(np.float32) / 255.0
        # Hailo requires C-contiguous NCHW float32 input.
        return np.ascontiguousarray(np.transpose(tensor, (2, 0, 1))[np.newaxis, ...])

    def _infer_segmentation(self, tensor: np.ndarray) -> dict:
        input_name = self._seg_hef.get_input_vstream_infos()[0].name
        return self._seg_infer.infer({input_name: tensor})

    def _postprocess_seg(
        self, raw: dict, original_shape: tuple[int, int]
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        outputs = list(raw.values())
        # Log output shapes once at INFO level so we can verify the model's
        # raw feature-map structure without enabling DEBUG noise.
        if not self._logged_seg_shapes:
            for i, o in enumerate(outputs):
                logger.info(f"  seg output[{i}] shape={o.shape} dtype={o.dtype}")
            self._logged_seg_shapes = True
        if len(outputs) < 3:
            return (
                np.zeros(original_shape, dtype=np.uint8),
                np.array([], dtype=np.int32),
                np.array([], dtype=np.float32),
                np.zeros((0, 4), dtype=np.float32),
            )

        boxes, scores, class_ids, masks = self._yolov8_seg_decode(
            outputs,
            input_size=(self.config.input_height, self.config.input_width),
            conf_threshold=self.config.threshold,
            nms_threshold=self.config.nms_threshold,
        )

        if not self._logged_seg_shapes:
            # Already logged shapes above; now log detection stats.
            logger.info(
                f"  seg decode: {len(boxes)} boxes kept "
                f"(threshold={self.config.threshold}, nms={self.config.nms_threshold})"
            )

        if len(masks) == 0:
            seg_mask = np.zeros(original_shape, dtype=np.uint8)
        else:
            try:
                import cv2
                full_masks = []
                for m in masks:
                    m_rs = cv2.resize(m, (original_shape[1], original_shape[0]))
                    full_masks.append((m_rs > 0.5).astype(np.uint8))
                seg_mask = (np.stack(full_masks, axis=0).any(axis=0)).astype(np.uint8)
            except ImportError:
                seg_mask = np.ones(original_shape, dtype=np.uint8)

        return seg_mask, class_ids, scores, boxes

    # ------------------------------------------------------------------
    # Depth model management
    # ------------------------------------------------------------------
    def _load_depth_model(self) -> None:
        hef_path = self._resolve_hef_path(self.config.depth_model_path)
        if not hef_path.exists():
            logger.warning(
                f"Depth HEF not found at {hef_path}; depth will run in mock mode"
            )
            return

        hef = HEF(str(hef_path))
        target = self._get_target()
        configure_params = ConfigureParams.create_from_hef(
            hef, interface=HailoStreamInterface.PCIe
        )
        network_groups = target.configure(hef, configure_params)
        network_group = network_groups[0]

        input_vstreams_params = InputVStreamParams.make(
            network_group, quantized=False, format_type=FormatType.FLOAT32
        )
        output_vstreams_params = OutputVStreamParams.make(
            network_group, quantized=False, format_type=FormatType.FLOAT32
        )
        activated_ctx = None
        if not self._scheduler_enabled:
            activated_ctx = network_group.activate()
            activated_ctx.__enter__()

        infer = InferVStreams(
            network_group, input_vstreams_params, output_vstreams_params
        )
        infer.__enter__()

        self._depth_hef = hef
        self._depth_network_group = network_group
        self._depth_activated_ctx = activated_ctx
        self._depth_infer = infer
        self._initialized_models.add("depth")
        logger.info(f"Depth model loaded: {hef_path.name}")

    def _preprocess_depth(self, rgb: np.ndarray) -> np.ndarray:
        h, w = self.config.depth_input_height, self.config.depth_input_width
        try:
            import cv2
            resized = cv2.resize(rgb, (w, h), interpolation=cv2.INTER_CUBIC)
        except ImportError:
            resized = self._numpy_resize(rgb, h, w)
        # SCDepthV3 / fast_depth expect simple [0,1] normalization (no ImageNet mean/std)
        tensor = resized.astype(np.float32) / 255.0
        return np.ascontiguousarray(np.transpose(tensor, (2, 0, 1))[np.newaxis, ...])

    def _infer_depth(self, tensor: np.ndarray) -> dict:
        input_name = self._depth_hef.get_input_vstream_infos()[0].name
        return self._depth_infer.infer({input_name: tensor})

    def _postprocess_depth(self, raw: dict, original_shape: tuple[int, int]) -> np.ndarray:
        output_tensors = list(raw.values())
        if not output_tensors:
            return np.zeros(original_shape, dtype=np.float32)
        out = output_tensors[0]
        if out.ndim == 4:
            out = out[0, 0]
        elif out.ndim == 3:
            out = out[0]

        try:
            import cv2
            depth = cv2.resize(
                out.astype(np.float32),
                (original_shape[1], original_shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )
        except ImportError:
            depth = self._numpy_resize(out, original_shape[0], original_shape[1]).astype(np.float32)

        if depth.min() < depth.max():
            depth = (depth - depth.min()) / (depth.max() - depth.min())
        return depth

    # ------------------------------------------------------------------
    # Mock fallbacks (when Hailo SDK unavailable or HEF missing)
    # ------------------------------------------------------------------
    def _mock_segmentation(
        self, rgb: np.ndarray, t_start: float
    ) -> SegmentationResult:
        time.sleep(0.02)
        h_mask, h_class_ids, h_scores, h_boxes = self._heuristic_artifact_segmentation(rgb)
        if len(h_class_ids) > 0:
            mask = h_mask
            class_ids = h_class_ids
            scores = h_scores
            boxes = h_boxes
        else:
            h, w = rgb.shape[:2]
            rng = np.random.default_rng(h * w % (2**16))
            yy, xx = np.ogrid[:h, :w]
            cx = w * 0.5 + rng.uniform(-w * 0.1, w * 0.1)
            cy = h * 0.5 + rng.uniform(-h * 0.1, h * 0.1)
            rx, ry = w * 0.35, h * 0.35
            ellipse = ((xx - cx) ** 2) / rx**2 + ((yy - cy) ** 2) / ry**2
            mask = (ellipse <= 1.0).astype(np.uint8)
            class_ids = np.array([0], dtype=np.int32)
            scores = np.array([0.92], dtype=np.float32)
            boxes = np.array(
                [[max(0, cx - rx), max(0, cy - ry), min(w, cx + rx), min(h, cy + ry)]],
                dtype=np.float32,
            )

        elapsed = (time.perf_counter() - t_start) * 1000
        stats = InferenceStats(
            preprocess_ms=elapsed * 0.15,
            infer_ms=elapsed * 0.6,
            postprocess_ms=elapsed * 0.25,
            total_ms=elapsed,
        )
        return SegmentationResult(
            mask=mask,
            class_ids=class_ids,
            scores=scores,
            boxes_xyxy=boxes,
            stats=stats,
        )

    def _mock_depth(self, rgb: np.ndarray, t_start: float) -> DepthResult:
        time.sleep(0.015)
        h, w = rgb.shape[:2]
        yy, xx = np.ogrid[:h, :w]
        cx, cy = w / 2, h / 2
        depth = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        depth = depth.max() - depth
        depth = depth / depth.max() if depth.max() > 0 else depth

        elapsed = (time.perf_counter() - t_start) * 1000
        stats = InferenceStats(
            preprocess_ms=elapsed * 0.15,
            infer_ms=elapsed * 0.6,
            postprocess_ms=elapsed * 0.25,
            total_ms=elapsed,
        )
        return DepthResult(depth_map=depth.astype(np.float32), stats=stats)

    def _heuristic_artifact_segmentation(
        self, rgb: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Fallback detector for dark demo artifacts held close to the lens.

        This is intentionally simple and demo-friendly: when the real model
        does not recognize the object class, we look for large dark blobs and
        label them as `artifact` (class_id = -1). It works well for the black,
        rock-like wrapped objects used in the demo photos.

        The detector is gated to *close-range* objects, roughly 10 cm from the
        camera. There is no metric depth on this RGB-only path, so apparent
        blob size stands in for distance: a wrapped rock filling a large part
        of the frame is reported, while a similar object further away is not.
        """
        h, w = rgb.shape[:2]
        frame_area = float(h * w)

        # Close-range gate (apparent size as a proxy for ~10 cm distance).
        close_min_area_ratio = 0.12   # blob must cover >= 12% of the frame
        close_max_area_ratio = 0.85   # ...but not the entire frame
        close_min_span_ratio = 0.38   # longest box side >= 38% of the frame
        close_center_tolerance = 0.42  # box centre within the middle 42%

        min_area = int(frame_area * close_min_area_ratio)
        max_area = int(frame_area * close_max_area_ratio)

        def _is_close(x: float, y: float, bw: float, bh: float, area: int) -> bool:
            """True when a blob looks like it is right in front of the lens."""
            if area < min_area or area > max_area:
                return False
            span = max(bw / float(w), bh / float(h))
            if span < close_min_span_ratio:
                return False
            cx = (x + bw / 2.0) / float(w)
            cy = (y + bh / 2.0) / float(h)
            half = close_center_tolerance / 2.0
            return (
                (0.5 - half) <= cx <= (0.5 + half)
                and (0.5 - half) <= cy <= (0.5 + half)
            )

        try:
            import cv2

            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            gray = cv2.GaussianBlur(gray, (5, 5), 0)
            dark_threshold = int(
                np.clip(min(118, np.percentile(gray, 28)), 45, 118)
            )
            mask = (gray <= dark_threshold).astype(np.uint8) * 255

            kernel = np.ones((7, 7), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

            num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)

            boxes: list[list[float]] = []
            scores: list[float] = []
            class_ids: list[int] = []
            masks: list[np.ndarray] = []

            for idx in range(1, num_labels):
                x, y, bw, bh, area = stats[idx]
                area = int(area)
                if not _is_close(x, y, bw, bh, area):
                    continue

                area_ratio = area / frame_area
                size_ratio = max(bw / float(w), bh / float(h))
                confidence = min(0.97, 0.62 + area_ratio * 1.6 + size_ratio * 0.12)

                comp_mask = (labels == idx).astype(np.uint8)
                boxes.append([float(x), float(y), float(x + bw), float(y + bh)])
                scores.append(float(confidence))
                class_ids.append(-1)
                masks.append(comp_mask)

            if boxes:
                order = np.argsort(np.array(scores))[::-1]
                return (
                    np.stack([masks[i] for i in order], axis=0).astype(np.uint8),
                    np.array([class_ids[i] for i in order], dtype=np.int32),
                    np.array([scores[i] for i in order], dtype=np.float32),
                    np.array([boxes[i] for i in order], dtype=np.float32),
                )
        except Exception as exc:
            logger.debug(f"Heuristic artifact detection skipped: {exc}")

        # Minimal numpy-only fallback: one big dark region if present.
        gray = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]).astype(
            np.float32
        )
        dark = gray <= np.clip(np.percentile(gray, 25), 45, 118)
        ys, xs = np.where(dark)
        if len(xs) >= min_area:
            x1, x2 = xs.min(), xs.max()
            y1, y2 = ys.min(), ys.max()
            bw, bh = int(x2 - x1 + 1), int(y2 - y1 + 1)
            if _is_close(float(x1), float(y1), float(bw), float(bh), int(len(xs))):
                area_ratio = len(xs) / frame_area
                size_ratio = max(bw / float(w), bh / float(h))
                confidence = min(0.9, 0.6 + area_ratio * 1.4 + size_ratio * 0.12)
                return (
                    dark.astype(np.uint8),
                    np.array([-1], dtype=np.int32),
                    np.array([confidence], dtype=np.float32),
                    np.array([[float(x1), float(y1), float(x2), float(y2)]], dtype=np.float32),
                )

        return (
            np.zeros((h, w), dtype=np.uint8),
            np.array([], dtype=np.int32),
            np.array([], dtype=np.float32),
            np.zeros((0, 4), dtype=np.float32),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _cleanup(self) -> None:
        # Close InferVStreams (HailoRT 4.x uses __exit__; some versions also
        # expose close()). Try both defensively.
        for attr in ("_seg_infer", "_depth_infer"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.__exit__(None, None, None)
                except Exception:
                    try:
                        obj.close()
                    except Exception:
                        pass
                setattr(self, attr, None)
        # Exit activation context managers (paired with __enter__ in _load_*)
        for attr in ("_seg_activated_ctx", "_depth_activated_ctx"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.__exit__(None, None, None)
                except Exception:
                    pass
                setattr(self, attr, None)
        for attr in ("_seg_network_group", "_depth_network_group"):
            obj = getattr(self, attr, None)
            if obj is not None:
                setattr(self, attr, None)
        # VDevice uses release() to free the device. Fall back to dropping the
        # reference if release() is unavailable.
        if self._target is not None:
            try:
                self._target.release()
            except Exception:
                pass
            self._target = None
        self._scheduler_enabled = False
        self._initialized_models.clear()

    @staticmethod
    def _numpy_resize(arr: np.ndarray, new_h: int, new_w: int) -> np.ndarray:
        old_h, old_w = arr.shape[:2]
        row_idx = (np.linspace(0, old_h - 1, new_h)).astype(np.int64)
        col_idx = (np.linspace(0, old_w - 1, new_w)).astype(np.int64)
        if arr.ndim == 3:
            return arr[row_idx[:, None], col_idx[None, :], :]
        return arr[row_idx[:, None], col_idx[None, :]]

    @staticmethod
    def _yolov8_seg_decode(
        outputs: list[np.ndarray],
        input_size: tuple[int, int],
        conf_threshold: float,
        nms_threshold: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[np.ndarray]]:
        """Decode YOLOv8-seg raw feature maps.

        The Hailo YOLOv8-seg HEF does **not** output pre-decoded boxes/scores.
        Instead it emits 10 raw tensors (NHWC layout observed on Hailo):
          - 3 detection scales (P3/P4/P5), each with 3 heads:
              * box   : 64 channels (DFL: 4 sides x 16 bins, reg_max=16)
              * cls   : nc channels (class logits, requires sigmoid)
              * coeff : 32 channels (mask coefficients)
          - 1 mask prototype : 32 channels at P2 resolution (in/4)

        This function classifies each tensor by shape, applies DFL decoding,
        dist2bbox, sigmoid, confidence filtering, NMS, and generates
        per-instance masks from coefficients x prototype.
        """
        if not outputs:
            return np.zeros((0, 4)), np.array([]), np.array([]), []

        in_h, in_w = input_size

        proto: Optional[np.ndarray] = None
        # grid_size -> {"box": chw, "cls": chw, "coeff": chw}
        scales: dict[int, dict[str, np.ndarray]] = {}

        # --- Pass 1: locate the mask prototype and detect layout convention ---
        # The proto tensor is unambiguous: 32 channels at P2 (in/4) resolution.
        # 32 is never a valid grid size for 640-class inputs, so we can reliably
        # tell NCHW [32,160,160] from NHWC [160,160,32].
        layout_is_nchw: Optional[bool] = None
        proto_spatial = (in_h // 4, in_w // 4)

        for t in outputs:
            arr = t[0] if (t.ndim == 4 and t.shape[0] == 1) else (
                t if t.ndim == 3 else None
            )
            if arr is None or arr.ndim != 3:
                continue
            d0, d1, d2 = arr.shape
            if d0 == 32 and (d1, d2) == proto_spatial:
                proto = arr
                layout_is_nchw = True
                break
            if d2 == 32 and (d0, d1) == proto_spatial:
                proto = np.transpose(arr, (2, 0, 1))
                layout_is_nchw = False
                break

        if proto is None or layout_is_nchw is None:
            return np.zeros((0, 4)), np.array([]), np.array([]), []

        # --- Pass 2: classify every tensor using the detected convention ---
        for t in outputs:
            arr = t[0] if (t.ndim == 4 and t.shape[0] == 1) else (
                t if t.ndim == 3 else None
            )
            if arr is None or arr.ndim != 3:
                continue
            chw = arr if layout_is_nchw else np.transpose(arr, (2, 0, 1))
            c, h, w = chw.shape
            grid = h

            # Skip the proto tensor we already captured.
            if c == 32 and h == proto_spatial[0] and w == proto_spatial[1]:
                continue
            # Box head: 4 channels (post-DFL) or 4*reg_max=64 channels (raw DFL,
            # reg_max=16). Coeff head: 32 channels. Everything else is cls.
            if c in (4, 64):
                scales.setdefault(grid, {})["box"] = chw
            elif c == 32:
                scales.setdefault(grid, {})["coeff"] = chw
            else:
                scales.setdefault(grid, {})["cls"] = chw

        if not scales:
            return np.zeros((0, 4)), np.array([]), np.array([]), []

        all_boxes: list[np.ndarray] = []
        all_scores: list[np.ndarray] = []
        all_class_ids: list[np.ndarray] = []
        all_coeffs: list[np.ndarray] = []
        cls_needs_sigmoid: bool | None = None  # auto-detected from first scale

        for grid, heads in scales.items():
            if "box" not in heads or "cls" not in heads:
                continue

            stride = in_h // grid  # 8, 16, or 32
            box = heads["box"]      # [4 or 64, grid, grid]
            cls = heads["cls"]      # [nc, grid, grid]
            coeff = heads.get("coeff")  # [32, grid, grid] or None
            nc = cls.shape[0]
            box_ch = box.shape[0]

            # Flatten spatial dims -> [N, ...]
            box_flat = box.reshape(box_ch, -1).T    # [N, 4 or 64]
            cls_flat = cls.reshape(nc, -1).T        # [N, nc]
            coeff_flat = (
                coeff.reshape(32, -1).T if coeff is not None else None
            )  # [N, 32]

            # DFL (Distribution Focal Loss) decoding.
            # YOLOv8 box head may output 4*reg_max channels (reg_max=16 -> 64).
            # Reshape to [N, 4, 16], softmax over bins, weighted sum with
            # [0..15] to recover the 4 distance values.
            if box_ch == 64:
                dfl = box_flat.reshape(-1, 4, 16).astype(np.float32)
                # Numerically stable softmax over the 16-bin axis.
                dfl = dfl - dfl.max(axis=2, keepdims=True)
                dfl = np.exp(dfl)
                dfl = dfl / dfl.sum(axis=2, keepdims=True)
                weights = np.arange(16, dtype=np.float32)
                box_flat = (dfl * weights).sum(axis=2)  # [N, 4]

            # Grid cell centre coordinates (YOLOv8 uses offset 0.5).
            rows = np.arange(grid)
            cols = np.arange(grid)
            col_grid, row_grid = np.meshgrid(cols, rows)
            cx = (col_grid.reshape(-1) + 0.5) * stride
            cy = (row_grid.reshape(-1) + 0.5) * stride

            # dist2bbox: box channels are (left, top, right, bottom) distances
            # in units of stride.
            left, top, right, bottom = (
                box_flat[:, 0], box_flat[:, 1],
                box_flat[:, 2], box_flat[:, 3],
            )
            x1 = cx - left * stride
            y1 = cy - top * stride
            x2 = cx + right * stride
            y2 = cy + bottom * stride
            boxes = np.stack([x1, y1, x2, y2], axis=1).astype(np.float32)

            # Sigmoid class logits -> confidence + class id.
            # Some Hailo-compiled models already apply sigmoid to the class
            # head internally. We detect this from the value range: raw logits
            # typically span [-5, +5] or more, while already-sigmoided values
            # are strictly in [0, 1]. Double-sigmoiding would squash all
            # scores to ~0.5-0.6 and make real detections invisible.
            cls_f32 = cls_flat.astype(np.float32)
            if cls_needs_sigmoid is None:
                cls_max = float(cls_f32.max())
                cls_min = float(cls_f32.min())
                cls_needs_sigmoid = not (cls_min >= -0.05 and cls_max <= 1.05)
                logger.info(
                    f"  cls sigmoid: {'applied' if cls_needs_sigmoid else 'skipped (model already sigmoided)'} "
                    f"(raw range=[{cls_min:.3f}, {cls_max:.3f}])"
                )
            if cls_needs_sigmoid:
                scores = 1.0 / (1.0 + np.exp(-cls_f32))
            else:
                scores = np.clip(cls_f32, 0.0, 1.0)
            confidences = scores.max(axis=1)
            class_ids = scores.argmax(axis=1).astype(np.int32)

            all_boxes.append(boxes)
            all_scores.append(confidences)
            all_class_ids.append(class_ids)
            if coeff_flat is not None:
                all_coeffs.append(coeff_flat)

        if not all_boxes:
            return np.zeros((0, 4)), np.array([]), np.array([]), []

        boxes = np.concatenate(all_boxes, axis=0)
        confidences = np.concatenate(all_scores, axis=0)
        class_ids = np.concatenate(all_class_ids, axis=0)
        coeffs = (
            np.concatenate(all_coeffs, axis=0) if all_coeffs else None
        )

        # Diagnostic: log confidence distribution on first frame so we can
        # tell whether the model produces real detections or just noise.
        if not HailoInferenceEngine._logged_conf_stats:
            HailoInferenceEngine._logged_conf_stats = True
            if len(confidences) > 0:
                logger.info(
                    f"  conf stats: n={len(confidences)} "
                    f"min={confidences.min():.4f} "
                    f"max={confidences.max():.4f} "
                    f"mean={confidences.mean():.4f} "
                    f">=0.5:{int((confidences >= 0.5).sum())} "
                    f">=0.7:{int((confidences >= 0.7).sum())}"
                )

        # Confidence filter.
        keep = confidences >= conf_threshold
        boxes = boxes[keep]
        confidences = confidences[keep]
        class_ids = class_ids[keep]
        if coeffs is not None:
            coeffs = coeffs[keep]

        if len(boxes) == 0:
            return np.zeros((0, 4)), np.array([]), np.array([]), []

        # Min box size filter: discard tiny boxes that are almost certainly
        # noise (a real object can't be fewer than 5 pixels on a side at
        # 640x640 input resolution).
        min_size = 5.0
        sizes_ok = (
            (boxes[:, 2] - boxes[:, 0] >= min_size) &
            (boxes[:, 3] - boxes[:, 1] >= min_size)
        )
        boxes = boxes[sizes_ok]
        confidences = confidences[sizes_ok]
        class_ids = class_ids[sizes_ok]
        if coeffs is not None:
            coeffs = coeffs[sizes_ok]

        if len(boxes) == 0:
            return np.zeros((0, 4)), np.array([]), np.array([]), []

        # Limit to top-K highest-confidence detections before NMS to avoid
        # pathological O(n^2) slowdown when thousands of boxes pass the
        # confidence threshold.
        top_k = 1000
        if len(boxes) > top_k:
            top_idx = np.argpartition(confidences, -top_k)[-top_k:]
            boxes = boxes[top_idx]
            confidences = confidences[top_idx]
            class_ids = class_ids[top_idx]
            if coeffs is not None:
                coeffs = coeffs[top_idx]

        # Non-maximum suppression.
        nms_keep = HailoInferenceEngine._nms(boxes, confidences, nms_threshold)
        final_boxes = boxes[nms_keep]
        final_scores = confidences[nms_keep]
        final_class_ids = class_ids[nms_keep]

        # Generate per-instance masks: sigmoid(coeff @ proto).
        final_masks: list[np.ndarray] = []
        if coeffs is not None and proto is not None:
            proto_h, proto_w = proto.shape[1], proto.shape[2]
            proto_flat = proto.reshape(32, -1)  # [32, proto_h*proto_w]
            for idx in nms_keep:
                c = coeffs[idx]  # [32]
                mask = 1.0 / (1.0 + np.exp(-(c @ proto_flat)))
                final_masks.append(mask.reshape(proto_h, proto_w))

        return final_boxes, final_scores, final_class_ids, final_masks

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
        if len(boxes) == 0:
            return []
        x1 = boxes[:, 0]
        y1 = boxes[:, 1]
        x2 = boxes[:, 2]
        y2 = boxes[:, 3]
        areas = (x2 - x1 + 1e-6) * (y2 - y1 + 1e-6)
        order = scores.argsort()[::-1]
        keep: list[int] = []
        while len(order) > 0:
            i = order[0]
            keep.append(int(i))
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            w = np.maximum(0.0, xx2 - xx1 + 1e-6)
            h = np.maximum(0.0, yy2 - yy1 + 1e-6)
            inter = w * h
            iou = inter / (areas[i] + areas[order[1:]] - inter)
            inds = np.where(iou <= iou_threshold)[0]
            order = order[inds + 1]
        return keep

    def __enter__(self) -> "HailoInferenceEngine":
        self.initialize()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
