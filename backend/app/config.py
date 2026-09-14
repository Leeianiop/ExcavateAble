"""
Backend configuration loader (Pydantic BaseSettings + .env).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "3D Scan Backend"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    minio_bundle_bucket: str = "scan-bundles"
    minio_asset_bucket: str = "scan-assets"

    temp_dir: Path = Path("./data/tmp")
    worker_concurrency: int = 2

    colmap_bin: str = Field(default="colmap", description="Path/command to COLMAP binary")
    openmvs_dir: str = Field(
        default="/usr/local/bin/OpenMVS",
        description="Directory containing OpenMVS binaries (InterfaceCOLMAP, DensifyPointCloud, ReconstructMesh, TextureMesh)",
    )

    glb_texture_size: int = 4096
    ply_decimate_target_ratio: float = 0.5
    max_upload_size_mb: int = 8192
    bundle_ttl_days: int = 30

    @field_validator("temp_dir")
    @classmethod
    def _ensure_dir(cls, v: Path) -> Path:
        v = Path(v)
        v.mkdir(parents=True, exist_ok=True)
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
