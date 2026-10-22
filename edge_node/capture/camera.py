"""
Raspberry Pi Camera Module Capture Driver
Uses Picamera2 for Raspberry Pi 5 compatibility.
Supports RAW+JPEG paired capture, EXIF metadata, and multi-shot orchestration.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import CaptureConfig

logger = logging.getLogger(__name__)

try:
    from picamera2 import Picamera2, Preview
    from libcamera import controls
    _PICAMERA_AVAILABLE = True
except ImportError:
    _PICAMERA_AVAILABLE = False
    logger.warning(
        "Picamera2 not installed. Running in mock/simulation mode. "
        "Install with: sudo apt install python3-picamera2"
    )


@dataclass
class ShotMetadata:
    shot_id: str
    shot_index: int
    timestamp_ns: int
    iso: int
    shutter_speed_us: int
    exposure_time_us: int
    awb_gains: tuple[float, float]
    lens_position: float
    sensor_temperature_c: float = 0.0
    width: int = 0
    height: int = 0
    camera_intrinsics: Optional[dict] = None


@dataclass
class CapturedShot:
    shot_id: str
    shot_index: int
    timestamp_ns: int
    rgb_array: np.ndarray
    raw_array: Optional[np.ndarray]
    metadata: ShotMetadata
    jpeg_path: Optional[Path] = None
    raw_path: Optional[Path] = None

    @property
    def rgb(self) -> np.ndarray:
        return self.rgb_array

    @property
    def raw(self) -> Optional[np.ndarray]:
        return self.raw_array


class CameraCapture:
    def __init__(self, config: CaptureConfig):
        self.config = config
        self._picam: Optional["Picamera2"] = None
        self._initialized = False
        self._session_id: str = uuid.uuid4().hex[:12]

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def using_hardware(self) -> bool:
        return self._picam is not None

    @property
    def session_id(self) -> str:
        return self._session_id

    def initialize(self) -> None:
        if self._initialized:
            return

        if not _PICAMERA_AVAILABLE:
            logger.info("Picamera2 unavailable — using mock camera mode")
            self._initialized = True
            return

        try:
            self._picam = Picamera2()

            capture_cfg = self._picam.create_still_configuration(
                main={
                    "size": (self.config.width, self.config.height),
                    "format": "RGB888",
                },
                raw={
                    "size": (self.config.width, self.config.height),
                    "format": self._picam.sensor_format,
                },
                buffer_count=2,
            )
            self._picam.configure(capture_cfg)

            # Set controls defensively — individual controls like AeMode may not
            # be advertised by all sensors (e.g. IMX708 on Camera Module v3).
            control_map = {
                "AwbMode": self._parse_awb_mode(self.config.awb_mode),
                "AeMode": self._parse_exposure_mode(self.config.exposure_mode),
                "AnalogueGain": self.config.iso / 100.0 if self.config.iso > 0 else 1.0,
            }
            if self.config.shutter_speed > 0:
                control_map["ExposureTime"] = int(self.config.shutter_speed)
            for name, value in control_map.items():
                try:
                    self._picam.set_controls({name: value})
                except Exception as exc:
                    logger.warning(f"Camera control '{name}' not supported: {exc}")

            self._picam.start()
            time.sleep(2.0)
            logger.info(
                f"Camera initialized: {self.config.width}x{self.config.height} "
                f"@ ISO {self.config.iso}, AWB={self.config.awb_mode}"
            )
            self._initialized = True
        except Exception as exc:
            logger.warning(f"Camera HW init failed ({exc}) — falling back to mock camera mode")
            self._picam = None
            self._initialized = True

    def close(self) -> None:
        if self._picam is not None:
            self._picam.stop()
            self._picam.close()
            self._picam = None
        self._initialized = False
        logger.info("Camera closed")

    def capture_single(self, shot_index: int) -> CapturedShot:
        if not self._initialized:
            raise RuntimeError("Camera not initialized; call initialize() first")

        shot_id = f"{self._session_id}_{shot_index:03d}"
        timestamp_ns = time.time_ns()

        if self._picam is None:
            return self._capture_mock(shot_id, shot_index, timestamp_ns)

        request = self._picam.capture_request()

        try:
            rgb_array = request.make_array("main")
            raw_array: Optional[np.ndarray] = None
            if self.config.save_raw:
                try:
                    raw_array = request.make_array("raw")
                except Exception as exc:
                    logger.warning(f"RAW capture unavailable for shot {shot_index}: {exc}")

            meta = request.get_metadata()

            metadata = ShotMetadata(
                shot_id=shot_id,
                shot_index=shot_index,
                timestamp_ns=timestamp_ns,
                iso=int(meta.get("AnalogueGain", 1.0) * 100),
                shutter_speed_us=int(meta.get("ExposureTime", 0)),
                exposure_time_us=int(meta.get("ExposureTime", 0)),
                awb_gains=(
                    float(meta.get("ColourGains", (1.0, 1.0))[0]),
                    float(meta.get("ColourGains", (1.0, 1.0))[1]),
                ),
                lens_position=float(meta.get("LensPosition", 0.0)),
                sensor_temperature_c=float(meta.get("SensorTemperature", 0.0)),
                width=self.config.width,
                height=self.config.height,
                camera_intrinsics=self._extract_intrinsics(meta),
            )

            logger.debug(
                f"Shot {shot_index:03d} captured in "
                f"{(time.time_ns() - timestamp_ns) / 1e6:.1f}ms"
            )
            return CapturedShot(
                shot_id=shot_id,
                shot_index=shot_index,
                timestamp_ns=timestamp_ns,
                rgb_array=rgb_array,
                raw_array=raw_array,
                metadata=metadata,
            )
        finally:
            request.release()

    def capture_session(
        self,
        output_dir: Optional[str | Path] = None,
        num_shots: Optional[int] = None,
        shot_interval: Optional[float] = None,
    ) -> list[CapturedShot]:
        n = num_shots or self.config.num_shots
        interval = shot_interval if shot_interval is not None else self.config.shot_interval_sec

        logger.info(f"Starting capture session: {n} shots, interval {interval}s")
        shots: list[CapturedShot] = []

        save_dir: Optional[Path] = None
        if output_dir is not None:
            save_dir = Path(output_dir) / self._session_id
            save_dir.mkdir(parents=True, exist_ok=True)

        for i in range(n):
            shot = self.capture_single(i)
            if save_dir is not None:
                self._save_shot(shot, save_dir)
            shots.append(shot)

            if i < n - 1 and interval > 0:
                time.sleep(interval)

        logger.info(f"Capture session complete: {len(shots)} shots -> {save_dir or 'memory'}")
        return shots

    def _save_shot(self, shot: CapturedShot, directory: Path) -> None:
        try:
            from PIL import Image
        except ImportError:
            logger.warning("Pillow not installed; skipping disk save")
            return

        if self.config.save_jpeg:
            jpeg_path = directory / f"{shot.shot_id}.jpg"
            img = Image.fromarray(shot.rgb_array)
            img.save(jpeg_path, format="JPEG", quality=self.config.jpeg_quality)
            shot.jpeg_path = jpeg_path

        if self.config.save_raw and shot.raw_array is not None:
            raw_path = directory / f"{shot.shot_id}.raw.npy"
            np.save(raw_path, shot.raw_array)
            shot.raw_path = raw_path

        meta_path = directory / f"{shot.shot_id}.meta.json"
        import json
        with open(meta_path, "w") as f:
            json.dump(shot.metadata.__dict__, f, indent=2, default=str)

    def _capture_mock(
        self, shot_id: str, shot_index: int, timestamp_ns: int
    ) -> CapturedShot:
        h, w = self.config.height, self.config.width
        yy, xx = np.mgrid[0:h, 0:w]

        # Make mock mode unmistakable in the browser instead of showing
        # random static that looks like a broken JPEG stream.
        rgb = np.zeros((h, w, 3), dtype=np.uint8)
        rgb[..., 0] = (xx * 255 // max(w - 1, 1)).astype(np.uint8)
        rgb[..., 1] = (yy * 255 // max(h - 1, 1)).astype(np.uint8)
        rgb[..., 2] = 96

        # Overlay a simple grid so scale/orientation remain easy to inspect.
        grid_mask = ((xx % 64) < 2) | ((yy % 64) < 2)
        rgb[grid_mask] = np.array([255, 255, 255], dtype=np.uint8)

        # Add a centered warning banner using plain array slicing so we do not
        # depend on extra drawing libraries on the Pi.
        banner_h = max(64, h // 7)
        y0 = max(0, (h - banner_h) // 2)
        y1 = min(h, y0 + banner_h)
        rgb[y0:y1, :, :] = np.array([20, 20, 20], dtype=np.uint8)
        rgb[y0:y0 + 4, :, :] = np.array([239, 68, 68], dtype=np.uint8)
        rgb[y1 - 4:y1, :, :] = np.array([239, 68, 68], dtype=np.uint8)

        raw = ((xx + yy) % 4096).astype(np.uint16)
        metadata = ShotMetadata(
            shot_id=shot_id,
            shot_index=shot_index,
            timestamp_ns=timestamp_ns,
            iso=self.config.iso,
            shutter_speed_us=self.config.shutter_speed,
            exposure_time_us=self.config.shutter_speed,
            awb_gains=(1.2, 1.8),
            lens_position=1.0,
            sensor_temperature_c=42.0,
            width=self.config.width,
            height=self.config.height,
            camera_intrinsics={
                "fx": self.config.width * 1.2,
                "fy": self.config.height * 1.2,
                "cx": self.config.width / 2.0,
                "cy": self.config.height / 2.0,
                "distortion": [0.0, 0.0, 0.0, 0.0, 0.0],
            },
        )
        return CapturedShot(
            shot_id=shot_id,
            shot_index=shot_index,
            timestamp_ns=timestamp_ns,
            rgb_array=rgb,
            raw_array=raw if self.config.save_raw else None,
            metadata=metadata,
        )

    @staticmethod
    def _parse_awb_mode(mode: str) -> int:
        mapping = {
            "auto": 0,
            "tungsten": 1,
            "fluorescent": 2,
            "indoor": 3,
            "daylight": 4,
            "cloudy": 5,
            "custom": 6,
        }
        return mapping.get(mode.lower(), 0)

    @staticmethod
    def _parse_exposure_mode(mode: str) -> int:
        mapping = {
            "auto": 0,
            "normal": 1,
            "short": 2,
            "long": 3,
            "custom": 4,
        }
        return mapping.get(mode.lower(), 0)

    @staticmethod
    def _extract_intrinsics(meta: dict) -> Optional[dict]:
        try:
            fx = float(meta.get("SensorBlackLevels", [0, 0, 0, 0])[0]) or None
        except Exception:
            fx = None
        if fx is None:
            return None
        return {"model_notes": "camera intrinsics placeholder"}

    def __enter__(self) -> "CameraCapture":
        self.initialize()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
