"""
In-memory + Redis job registry + bundle state tracker.
Stores upload sessions, job status, and final asset URLs.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..config import Settings
from ..schemas import JobProgress, JobResponse, JobStage, JobStatus

logger = logging.getLogger(__name__)


@dataclass
class BundleUploadState:
    bundle_id: str
    session_id: str
    upload_id: str
    num_shots: int
    manifest_etag: str
    shot_files_received: dict[str, set[str]] = field(default_factory=dict)
    created_at_ns: int = field(default_factory=time.time_ns)
    job_id: Optional[str] = None


class JobStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._bundles: dict[str, BundleUploadState] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._redis = None
        self._redis_available = False
        self._state_path: Path = settings.temp_dir / "job_state.json"

    def initialize(self) -> None:
        try:
            import redis as redis_lib
            self._redis = redis_lib.Redis.from_url(self.settings.redis_url, decode_responses=True)
            self._redis.ping()
            self._redis_available = True
            logger.info("Redis-backed job store connected")
        except Exception as exc:
            logger.warning(f"Redis unavailable ({exc}); using in-memory job store")
            self._redis_available = False

        self._load_from_disk()

    # ------------------------------------------------------------------
    # Bundle upload state
    # ------------------------------------------------------------------
    def create_bundle(self, bundle_id: str, session_id: str, num_shots: int, manifest_etag: str) -> BundleUploadState:
        with self._lock:
            upload_id = f"upl_{uuid.uuid4().hex[:12]}"
            job_id = f"job_{bundle_id}_{uuid.uuid4().hex[:8]}"
            state = BundleUploadState(
                bundle_id=bundle_id,
                session_id=session_id,
                upload_id=upload_id,
                num_shots=num_shots,
                manifest_etag=manifest_etag,
                job_id=job_id,
            )
            self._bundles[bundle_id] = state
            self._ensure_job(job_id, bundle_id, num_shots, status=JobStatus.QUEUED)
            self._persist()
            return state

    def mark_shot_file(self, bundle_id: str, shot_id: str, role: str) -> None:
        with self._lock:
            state = self._bundles.get(bundle_id)
            if state is None:
                raise KeyError(f"Bundle {bundle_id} unknown")
            state.shot_files_received.setdefault(shot_id, set()).add(role)

    def get_bundle(self, bundle_id: str) -> Optional[BundleUploadState]:
        return self._bundles.get(bundle_id)

    def finalize_bundle(self, bundle_id: str) -> Optional[str]:
        with self._lock:
            state = self._bundles.get(bundle_id)
            if state is None:
                return None
            self._update_job_status(state.job_id, JobStatus.PENDING)
            self._persist()
            return state.job_id

    # ------------------------------------------------------------------
    # Job state
    # ------------------------------------------------------------------
    def _ensure_job(self, job_id: str, bundle_id: str, num_shots: int, status: JobStatus) -> None:
        if job_id in self._jobs:
            return
        self._jobs[job_id] = {
            "job_id": job_id,
            "bundle_id": bundle_id,
            "status": status.value,
            "num_shots": num_shots,
            "created_at": datetime.utcnow().isoformat(),
            "started_at": None,
            "finished_at": None,
            "current_stage": None,
            "progress_percent": 0.0,
            "progress": [],
            "error_message": None,
            "ply_url": None,
            "glb_url": None,
            "ply_size_bytes": 0,
            "glb_size_bytes": 0,
            "num_vertices": 0,
            "num_faces": 0,
            "viewer_url": None,
        }

    def mark_job_running(self, job_id: str) -> None:
        with self._lock:
            self._jobs.setdefault(job_id, {})
            self._jobs[job_id]["status"] = JobStatus.RUNNING.value
            self._jobs[job_id]["started_at"] = datetime.utcnow().isoformat()
            self._persist()

    def update_stage(
        self,
        job_id: str,
        stage: JobStage,
        percent: float,
        message: Optional[str] = None,
    ) -> None:
        with self._lock:
            job = self._jobs.setdefault(job_id, {})
            job["status"] = JobStatus.RUNNING.value
            job["current_stage"] = stage.value
            job["progress_percent"] = min(max(percent, 0.0), 100.0)
            prog = JobProgress(
                stage=stage,
                percent=min(max(percent, 0.0), 100.0),
                message=message,
                started_at=datetime.utcnow(),
            )
            existing = [p for p in job.get("progress", []) if p.get("stage") != stage.value]
            existing.append(asdict(prog))
            job["progress"] = existing
            self._persist()

    def complete_job(
        self,
        job_id: str,
        ply_url: str,
        glb_url: str,
        ply_size_bytes: int,
        glb_size_bytes: int,
        num_vertices: int,
        num_faces: int,
        viewer_url: Optional[str] = None,
    ) -> None:
        with self._lock:
            job = self._jobs.setdefault(job_id, {})
            job["status"] = JobStatus.COMPLETED.value
            job["finished_at"] = datetime.utcnow().isoformat()
            job["progress_percent"] = 100.0
            job["ply_url"] = ply_url
            job["glb_url"] = glb_url
            job["ply_size_bytes"] = ply_size_bytes
            job["glb_size_bytes"] = glb_size_bytes
            job["num_vertices"] = num_vertices
            job["num_faces"] = num_faces
            job["viewer_url"] = viewer_url
            self._persist()

    def fail_job(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs.setdefault(job_id, {})
            job["status"] = JobStatus.FAILED.value
            job["finished_at"] = datetime.utcnow().isoformat()
            job["error_message"] = error
            self._persist()

    def get_job(self, job_id: str) -> Optional[JobResponse]:
        with self._lock:
            raw = self._jobs.get(job_id)
            if raw is None:
                return None
            progress = [
                JobProgress(
                    stage=JobStage(p["stage"]),
                    percent=float(p["percent"]),
                    message=p.get("message"),
                    started_at=datetime.fromisoformat(p["started_at"]) if p.get("started_at") else None,
                    finished_at=datetime.fromisoformat(p["finished_at"]) if p.get("finished_at") else None,
                )
                for p in raw.get("progress", [])
            ]
            return JobResponse(
                job_id=raw["job_id"],
                bundle_id=raw["bundle_id"],
                status=JobStatus(raw["status"]),
                created_at=datetime.fromisoformat(raw["created_at"]),
                started_at=datetime.fromisoformat(raw["started_at"]) if raw.get("started_at") else None,
                finished_at=datetime.fromisoformat(raw["finished_at"]) if raw.get("finished_at") else None,
                current_stage=JobStage(raw["current_stage"]) if raw.get("current_stage") else None,
                progress_percent=float(raw.get("progress_percent", 0.0)),
                progress=progress,
                error_message=raw.get("error_message"),
                num_shots=int(raw.get("num_shots", 0)),
                ply_url=raw.get("ply_url"),
                glb_url=raw.get("glb_url"),
                ply_size_bytes=int(raw.get("ply_size_bytes", 0)),
                glb_size_bytes=int(raw.get("glb_size_bytes", 0)),
                num_vertices=int(raw.get("num_vertices", 0)),
                num_faces=int(raw.get("num_faces", 0)),
                viewer_url=raw.get("viewer_url"),
            )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _persist(self) -> None:
        try:
            payload = {
                "bundles": {
                    k: {
                        **{kk: vv for kk, vv in asdict(v).items() if kk != "shot_files_received"},
                        "shot_files_received": {sid: list(roles) for sid, roles in v.shot_files_received.items()},
                    }
                    for k, v in self._bundles.items()
                },
                "jobs": self._jobs,
            }
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_path, "w") as f:
                json.dump(payload, f, indent=2, default=str)
        except Exception as exc:
            logger.debug(f"Could not persist job state to disk: {exc}")

    def _load_from_disk(self) -> None:
        if not self._state_path.exists():
            return
        try:
            with open(self._state_path) as f:
                data = json.load(f)
            for k, v in (data.get("bundles") or {}).items():
                self._bundles[k] = BundleUploadState(
                    bundle_id=v["bundle_id"],
                    session_id=v["session_id"],
                    upload_id=v["upload_id"],
                    num_shots=v["num_shots"],
                    manifest_etag=v["manifest_etag"],
                    shot_files_received={sid: set(roles) for sid, roles in (v.get("shot_files_received") or {}).items()},
                    created_at_ns=int(v.get("created_at_ns", time.time_ns())),
                    job_id=v.get("job_id"),
                )
            for k, v in (data.get("jobs") or {}).items():
                self._jobs[k] = v
            logger.info(f"Restored {len(self._bundles)} bundles / {len(self._jobs)} jobs from disk")
        except Exception as exc:
            logger.warning(f"Could not restore persisted job state: {exc}")
