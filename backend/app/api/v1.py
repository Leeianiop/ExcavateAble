"""
FastAPI v1 API router.

Endpoints match the edge upload client protocol:
  POST /bundles/init
  PUT  /bundles/{bundle_id}/shots/{shot_id}/{role}
  POST /bundles/{bundle_id}/finalize
  GET  /jobs/{job_id}
  GET  /assets/{job_id}/ply
  GET  /assets/{job_id}/glb
  GET  /assets
  GET  /health
"""

from __future__ import annotations

import json as _json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status, Query
from fastapi.responses import FileResponse, RedirectResponse, Response
from datetime import datetime

from ..config import Settings, get_settings
from ..schemas import (
    AssetInfo,
    BundleFinalizeResponse,
    BundleInitRequest,
    BundleInitResponse,
    JobResponse,
    ShotUploadItem,
    LiveFrameResponse,
    LiveDetection,
)
from ..storage import ObjectStore
from ..storage.job_store import JobStore

logger = logging.getLogger(__name__)

v1_router = APIRouter()

_OBJECT_STORE: ObjectStore | None = None
_JOB_STORE: JobStore | None = None

# In-memory live frame store (latest frame only — digital twin feed)
_live_frame: dict | None = None


def get_object_store(settings: Settings = Depends(get_settings)) -> ObjectStore:
    global _OBJECT_STORE
    if _OBJECT_STORE is None:
        _OBJECT_STORE = ObjectStore(settings)
        _OBJECT_STORE.initialize()
    return _OBJECT_STORE


def get_job_store(settings: Settings = Depends(get_settings)) -> JobStore:
    global _JOB_STORE
    if _JOB_STORE is None:
        _JOB_STORE = JobStore(settings)
        _JOB_STORE.initialize()
    return _JOB_STORE


@v1_router.get("/health")
def health_check(
    store: ObjectStore = Depends(get_object_store),
    jobs: JobStore = Depends(get_job_store),
) -> dict:
    return {
        "status": "ok",
        "object_store": "ok" if store._use_fs or store._client is not None else "unknown",
        "job_store": "ok",
    }


# ------------------------------------------------------------------
# Bundle upload (matches edge_node.pipeline.upload_client protocol)
# ------------------------------------------------------------------
@v1_router.post("/bundles/init", response_model=BundleInitResponse)
def init_bundle(
    req: BundleInitRequest,
    store: ObjectStore = Depends(get_object_store),
    jobs: JobStore = Depends(get_job_store),
    settings: Settings = Depends(get_settings),
) -> BundleInitResponse:
    state = jobs.create_bundle(
        bundle_id=req.bundle_id,
        session_id=req.session_id,
        num_shots=req.num_shots,
        manifest_etag=req.manifest_etag,
    )

    shot_uploads: dict[str, ShotUploadItem] = {}
    for i in range(req.num_shots):
        shot_id = f"{req.session_id}_{i:03d}"
        roles: dict[str, Optional[str]] = {}
        for role in ("rgb", "mask", "depth", "metadata", "raw"):
            key = ObjectStore.bundle_key(req.bundle_id, shot_id, role)
            roles[role] = store.presigned_put_url(
                settings.minio_bundle_bucket, key, expires_seconds=3600
            )
        shot_uploads[shot_id] = ShotUploadItem(**roles)

    return BundleInitResponse(
        upload_id=state.upload_id,
        job_id=state.job_id or "",
        shot_uploads=shot_uploads,
    )


@v1_router.put("/bundles/{bundle_id}/shots/{shot_id}/{role}")
async def upload_shot_file(
    bundle_id: str,
    shot_id: str,
    role: str,
    request: Request,
    x_content_sha256: Optional[str] = Header(default=None),
    store: ObjectStore = Depends(get_object_store),
    jobs: JobStore = Depends(get_job_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    if role not in {"rgb", "mask", "depth", "metadata", "raw"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown role: {role}")
    state = jobs.get_bundle(bundle_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Bundle {bundle_id} not initialized")

    raw = await request.body()
    expected_mb = settings.max_upload_size_mb
    if len(raw) > expected_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"File exceeds {expected_mb}MB")

    key = ObjectStore.bundle_key(bundle_id, shot_id, role)
    ctype = request.headers.get("content-type", "application/octet-stream")
    try:
        obj = store.put_bytes(
            bucket=settings.minio_bundle_bucket,
            key=key,
            data=raw,
            content_type=ctype,
            expected_etag=x_content_sha256,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    jobs.mark_shot_file(bundle_id, shot_id, role)
    return {"status": "stored", "key": key, "size_bytes": obj.size_bytes, "etag": obj.etag}


@v1_router.post("/bundles/{bundle_id}/finalize", response_model=BundleFinalizeResponse)
async def finalize_bundle(
    bundle_id: str,
    request: Request,
    x_manifest_sha256: Optional[str] = Header(default=None),
    store: ObjectStore = Depends(get_object_store),
    jobs: JobStore = Depends(get_job_store),
    settings: Settings = Depends(get_settings),
) -> BundleFinalizeResponse:
    state = jobs.get_bundle(bundle_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Bundle {bundle_id} not initialized")

    raw = await request.body()
    actual_etag = ObjectStore.etag_sha256(raw)
    if actual_etag != state.manifest_etag:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Manifest ETag mismatch: declared {state.manifest_etag[:12]}… "
            f"!= received {actual_etag[:12]}…",
        )
    if x_manifest_sha256 and x_manifest_sha256 != actual_etag:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Manifest header ETag mismatch")

    try:
        manifest = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid manifest JSON: {exc}")

    manifest_key = ObjectStore.manifest_key(bundle_id)
    store.put_bytes(
        bucket=settings.minio_bundle_bucket,
        key=manifest_key,
        data=raw,
        content_type="application/json",
    )

    job_id = jobs.finalize_bundle(bundle_id)
    if job_id is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to finalize bundle")

    try:
        from ..workers.tasks import reconstruct_3d
        reconstruct_3d.apply_async(args=[job_id, bundle_id], queue="reconstruction")
    except Exception as exc:
        logger.warning(f"Could not enqueue Celery task (running inline fallback): {exc}")
        from threading import Thread

        def _inline_run():
            try:
                from ..workers import tasks as _tasks
                from ..workers.reconstruction import ReconstructionWorker
                _tasks._object_store = store
                _tasks._job_store = jobs
                _tasks._engine = ReconstructionWorker(settings)
                reconstruct_3d.apply(args=[job_id, bundle_id]).get()
            except Exception:
                logger.exception(f"Inline reconstruction failed for job {job_id}")

        Thread(target=_inline_run, daemon=True).start()

    return BundleFinalizeResponse(
        bundle_id=bundle_id,
        job_id=job_id,
        status="queued",
        message=f"Bundle queued for reconstruction. Poll GET /api/v1/jobs/{job_id}",
    )


# ------------------------------------------------------------------
# Job status polling
# ------------------------------------------------------------------
@v1_router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str,
    jobs: JobStore = Depends(get_job_store),
) -> JobResponse:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Job {job_id} not found")
    return job


# ------------------------------------------------------------------
# Assets (PLY / GLB + listing)
# ------------------------------------------------------------------
@v1_router.get("/assets/{job_id}/ply")
def get_ply(
    job_id: str,
    store: ObjectStore = Depends(get_object_store),
    jobs: JobStore = Depends(get_job_store),
    settings: Settings = Depends(get_settings),
):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Job {job_id} not found")
    if job.ply_url:
        return RedirectResponse(job.ply_url)
    key = ObjectStore.asset_key(job_id, "ply")
    if not store.exists(settings.minio_asset_bucket, key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PLY not yet generated")
    if store._use_fs:
        return FileResponse(store._resolve(settings.minio_asset_bucket, key), media_type="application/octet-stream", filename=f"{job_id}.ply")
    return RedirectResponse(
        store.presigned_get_url(settings.minio_asset_bucket, key)
    )


@v1_router.get("/assets/{job_id}/glb")
def get_glb(
    job_id: str,
    store: ObjectStore = Depends(get_object_store),
    jobs: JobStore = Depends(get_job_store),
    settings: Settings = Depends(get_settings),
):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Job {job_id} not found")
    if job.glb_url:
        return RedirectResponse(job.glb_url)
    key = ObjectStore.asset_key(job_id, "glb")
    if not store.exists(settings.minio_asset_bucket, key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "GLB not yet generated")
    if store._use_fs:
        return FileResponse(store._resolve(settings.minio_asset_bucket, key), media_type="model/gltf-binary", filename=f"{job_id}.glb")
    return RedirectResponse(
        store.presigned_get_url(settings.minio_asset_bucket, key)
    )


@v1_router.get("/assets", response_model=list[AssetInfo])
def list_assets(
    job_id: Optional[str] = Query(default=None),
    store: ObjectStore = Depends(get_object_store),
    jobs: JobStore = Depends(get_job_store),
    settings: Settings = Depends(get_settings),
) -> list[AssetInfo]:
    out: list[AssetInfo] = []
    if job_id:
        job = jobs.get_job(job_id)
        if job is None:
            return out
        for fmt in ("ply", "glb"):
            key = ObjectStore.asset_key(job_id, fmt)
            if store.exists(settings.minio_asset_bucket, key):
                if store._use_fs:
                    url = f"/api/v1/assets/{job_id}/{fmt}"
                else:
                    url = store.presigned_get_url(settings.minio_asset_bucket, key)
                size = 0
                if fmt == "ply":
                    size = job.ply_size_bytes
                elif fmt == "glb":
                    size = job.glb_size_bytes
                out.append(AssetInfo(
                    asset_id=f"{job_id}.{fmt}",
                    bundle_id=job.bundle_id,
                    job_id=job.job_id,
                    format=fmt,
                    size_bytes=size,
                    url=url,
                    created_at=job.created_at,
                ))
    return out


# ------------------------------------------------------------------
# Live stream (real-time artifact detection feed — digital twin)
# ------------------------------------------------------------------
@v1_router.post("/live/frame", response_model=LiveFrameResponse)
async def upload_live_frame(request: Request) -> LiveFrameResponse:
    """Receive a live frame + AI detections from the edge node."""
    global _live_frame
    form = await request.form()
    image = form.get("image")
    metadata_raw = form.get("metadata")

    if image is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing 'image' field")

    image_bytes = await image.read() if hasattr(image, "read") else image
    if isinstance(image_bytes, str):
        image_bytes = image_bytes.encode("utf-8")

    meta: dict = {}
    if metadata_raw:
        if hasattr(metadata_raw, "read"):
            metadata_raw = await metadata_raw.read()
        if isinstance(metadata_raw, bytes):
            metadata_raw = metadata_raw.decode("utf-8")
        try:
            meta = _json.loads(metadata_raw)
        except _json.JSONDecodeError:
            meta = {}

    detections = [
        LiveDetection(**d) for d in meta.get("detections", [])
    ]

    _live_frame = {
        "image_bytes": image_bytes,
        "response": LiveFrameResponse(
            timestamp=datetime.utcnow(),
            width=int(meta.get("width", 0)),
            height=int(meta.get("height", 0)),
            detections=detections,
            depth_min=float(meta.get("depth_min", 0.0)),
            depth_max=float(meta.get("depth_max", 0.0)),
            depth_mean=float(meta.get("depth_mean", 0.0)),
            image_url="/api/v1/live/frame/image",
        ),
    }
    return _live_frame["response"]


@v1_router.get("/live/frame", response_model=LiveFrameResponse)
def get_live_frame() -> LiveFrameResponse:
    """Return the latest live frame metadata (detections, depth stats)."""
    if _live_frame is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No live frame yet — edge node must start streaming first",
        )
    return _live_frame["response"]


@v1_router.get("/live/frame/image")
def get_live_frame_image() -> Response:
    """Return the latest live frame as a JPEG image."""
    if _live_frame is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No live frame yet — edge node must start streaming first",
        )
    return Response(
        content=_live_frame["image_bytes"],
        media_type="image/jpeg",
    )
