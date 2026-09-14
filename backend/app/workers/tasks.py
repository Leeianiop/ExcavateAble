"""
Celery task queue for asynchronous 3D reconstruction jobs.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from celery import Celery
from celery.signals import worker_process_init

from ..config import get_settings, Settings
from ..storage import ObjectStore
from ..storage.job_store import JobStore
from ..workers.reconstruction import ReconstructionWorker

logger = logging.getLogger(__name__)

_settings: Settings = get_settings()

celery = Celery(
    "scan_worker",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
)
celery.conf.update(
    task_track_started=True,
    task_time_limit=12 * 3600,
    task_soft_time_limit=11 * 3600,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=5,
    accept_content=["json", "pickle"],
    result_serializer="json",
)

_engine: ReconstructionWorker | None = None
_object_store: ObjectStore | None = None
_job_store: JobStore | None = None


@worker_process_init.connect
def _init_worker_process(*args, **kwargs) -> None:
    global _engine, _object_store, _job_store
    s = get_settings()
    _object_store = ObjectStore(s)
    _object_store.initialize()
    _job_store = JobStore(s)
    _job_store.initialize()
    _engine = ReconstructionWorker(s)
    logger.info("Celery worker process initialized")


def _progress_cb(job_id: str):
    def cb(stage, pct, msg):
        _job_store.update_stage(job_id, stage, pct, msg)
    return cb


@celery.task(bind=True, name="reconstruct_3d", queue="reconstruction")
def reconstruct_3d(self, job_id: str, bundle_id: str) -> dict:
    global _engine, _object_store, _job_store
    assert _engine and _object_store and _job_store

    bundle_root = _settings.temp_dir / f"dl_{job_id}"
    output_root = _settings.temp_dir / f"out_{job_id}"
    try:
        _job_store.mark_job_running(job_id)

        bundle_root.mkdir(parents=True, exist_ok=True)
        output_root.mkdir(parents=True, exist_ok=True)

        manifest_bytes = _object_store.get_bytes(
            _settings.minio_bundle_bucket,
            ObjectStore.manifest_key(bundle_id),
        )
        import json as _json
        manifest = _json.loads(manifest_bytes)

        shots = manifest.get("shots", [])
        progress_cb = _progress_cb(job_id)
        progress_cb(
            __import__("..schemas", fromlist=["JobStage"]).JobStage.DOWNLOAD_BUNDLE,
            2.0,
            f"Downloading {len(shots)} shots from object store",
        )

        for shot in shots:
            shot_id = shot["shot_id"]
            for role in ("rgb", "mask", "depth", "metadata", "raw"):
                rel = shot.get("files", {}).get(role)
                if not rel:
                    continue
                key = ObjectStore.bundle_key(bundle_id, shot_id, role)
                if not _object_store.exists(_settings.minio_bundle_bucket, key):
                    continue
                local_path = bundle_root / Path(rel).name
                _object_store.download_to_file(
                    _settings.minio_bundle_bucket, key, local_path
                )
                shot["files"][role] = str(local_path.resolve())

        manifest_path = bundle_root / "manifest.json"
        manifest_path.write_bytes(manifest_bytes)

        result = _engine.run(
            job_id=job_id,
            bundle_dir=bundle_root,
            manifest=manifest,
            output_dir=output_root,
            progress=progress_cb,
        )

        ply_key = ObjectStore.asset_key(job_id, "ply")
        glb_key = ObjectStore.asset_key(job_id, "glb")
        _object_store.put_file(
            _settings.minio_asset_bucket,
            ply_key,
            result.ply_path,
            content_type="application/octet-stream",
        )
        _object_store.put_file(
            _settings.minio_asset_bucket,
            glb_key,
            result.glb_path,
            content_type="model/gltf-binary",
        )

        ply_url = _object_store.presigned_get_url(_settings.minio_asset_bucket, ply_key)
        glb_url = _object_store.presigned_get_url(_settings.minio_asset_bucket, glb_key)

        viewer_url = f"/viewer?job_id={job_id}"

        _job_store.complete_job(
            job_id=job_id,
            ply_url=ply_url,
            glb_url=glb_url,
            ply_size_bytes=result.ply_size_bytes,
            glb_size_bytes=result.glb_size_bytes,
            num_vertices=result.num_vertices,
            num_faces=result.num_faces,
            viewer_url=viewer_url,
        )

        return {
            "job_id": job_id,
            "status": "completed",
            "num_vertices": result.num_vertices,
            "num_faces": result.num_faces,
            "ply_size_bytes": result.ply_size_bytes,
            "glb_size_bytes": result.glb_size_bytes,
        }

    except Exception as exc:
        logger.exception(f"Job {job_id} failed")
        _job_store.fail_job(job_id, str(exc))
        self.update_state(state="FAILURE", meta={"error": str(exc)})
        raise
    finally:
        if bundle_root.exists():
            shutil.rmtree(bundle_root, ignore_errors=True)
        if _settings.debug is False and output_root.exists():
            shutil.rmtree(output_root, ignore_errors=True)
