"""
Shared Pydantic schemas for the reconstruction API.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, ConfigDict


class JobStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    UPLOADING_ASSETS = "uploading_assets"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(str, Enum):
    DOWNLOAD_BUNDLE = "download_bundle"
    BACKGROUND_STRIP = "background_strip"
    COLMAP_SPARSE = "colmap_sparse"
    COLMAP_DENSE = "colmap_dense"
    OPENMVS_FUSION = "openmvs_fusion"
    MESH_POSTPROCESS = "mesh_postprocess"
    EXPORT_PLY = "export_ply"
    EXPORT_GLB = "export_glb"
    UPLOAD_ASSETS = "upload_assets"


class ShotUploadItem(BaseModel):
    rgb: Optional[str] = None
    mask: Optional[str] = None
    depth: Optional[str] = None
    metadata: Optional[str] = None
    raw: Optional[str] = None


class BundleInitRequest(BaseModel):
    bundle_id: str
    session_id: str
    num_shots: int
    manifest_size_bytes: int
    manifest_etag: str


class BundleInitResponse(BaseModel):
    upload_id: str
    job_id: str
    shot_uploads: dict[str, ShotUploadItem] = Field(default_factory=dict)
    expires_at: Optional[datetime] = None


class BundleFinalizeResponse(BaseModel):
    bundle_id: str
    job_id: str
    status: JobStatus
    message: str
    asset_url: Optional[str] = None


class JobProgress(BaseModel):
    stage: JobStage
    percent: float = Field(ge=0.0, le=100.0)
    message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class JobResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_id: str
    bundle_id: str
    status: JobStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    current_stage: Optional[JobStage] = None
    progress_percent: float = 0.0
    progress: list[JobProgress] = Field(default_factory=list)
    error_message: Optional[str] = None
    num_shots: int = 0

    ply_url: Optional[str] = None
    glb_url: Optional[str] = None
    ply_size_bytes: int = 0
    glb_size_bytes: int = 0
    num_vertices: int = 0
    num_faces: int = 0

    viewer_url: Optional[str] = None


class AssetInfo(BaseModel):
    asset_id: str
    bundle_id: str
    job_id: str
    format: str
    size_bytes: int
    url: str
    created_at: datetime


# ------------------------------------------------------------------
# Live stream (real-time artifact detection feed)
# ------------------------------------------------------------------
class LiveDetection(BaseModel):
    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: list[float] = Field(description="[x, y, w, h] normalized 0..1")


class LiveFrameResponse(BaseModel):
    timestamp: datetime
    width: int
    height: int
    detections: list[LiveDetection] = Field(default_factory=list)
    depth_min: float = 0.0
    depth_max: float = 0.0
    depth_mean: float = 0.0
    image_url: str = "/api/v1/live/frame/image"
