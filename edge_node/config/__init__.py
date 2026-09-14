"""
Edge Node Configuration Loader
Loads and validates YAML configuration for the 3D scanning capture node.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class CaptureConfig:
    width: int = 4056
    height: int = 3040
    framerate: int = 30
    iso: int = 100
    shutter_speed: int = 0
    awb_mode: str = "auto"
    exposure_mode: str = "auto"
    num_shots: int = 20
    shot_interval_sec: float = 1.5
    save_raw: bool = True
    save_jpeg: bool = True
    jpeg_quality: int = 95


@dataclass
class HailoConfig:
    segmentation_model_path: str = "./models/yolov8n-seg.hef"
    depth_model_path: str = "./models/dpt_lite.hef"
    input_width: int = 640
    input_height: int = 640
    depth_input_width: int = 518
    depth_input_height: int = 518
    threshold: float = 0.5
    nms_threshold: float = 0.45
    device_id: int = 0


@dataclass
class UploadConfig:
    backend_url: str = "http://localhost:8000"
    api_key: str = ""
    upload_timeout_sec: int = 120
    max_retries: int = 3
    retry_delay_sec: int = 5


@dataclass
class StorageConfig:
    local_dir: str = "./captures"
    keep_local_after_upload: bool = True
    max_local_gb: int = 10


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "./logs/edge_node.log"
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5


@dataclass
class EdgeNodeConfig:
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    hailo: HailoConfig = field(default_factory=HailoConfig)
    upload: UploadConfig = field(default_factory=UploadConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def from_yaml(cls, path: str | os.PathLike) -> "EdgeNodeConfig":
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, "r") as f:
            raw = yaml.safe_load(f) or {}

        return cls(
            capture=CaptureConfig(**(raw.get("capture") or {})),
            hailo=HailoConfig(**(raw.get("hailo") or {})),
            upload=UploadConfig(**(raw.get("upload") or {})),
            storage=StorageConfig(**(raw.get("storage") or {})),
            logging=LoggingConfig(**(raw.get("logging") or {})),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []

        if self.capture.width < 640 or self.capture.height < 480:
            errors.append("Capture resolution too low (min 640x480)")
        if self.capture.num_shots < 5:
            errors.append("num_shots must be >= 5 for usable reconstruction")
        if self.capture.shot_interval_sec < 0.2:
            errors.append("shot_interval_sec too short (min 0.2s)")

        hef_ext = ".hef"
        if not self.hailo.segmentation_model_path.endswith(hef_ext):
            errors.append(f"Segmentation model must be {hef_ext} file")
        if not self.hailo.depth_model_path.endswith(hef_ext):
            errors.append(f"Depth model must be {hef_ext} file")

        if not self.upload.backend_url.startswith(("http://", "https://")):
            errors.append("backend_url must start with http:// or https://")

        return errors
