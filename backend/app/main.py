"""
FastAPI application entry point.

Run locally:
    cd backend && pip install -r requirements.txt
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

The edge node points its backend_url at http://<this-machine-ip>:8000
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import v1_router
from .config import get_settings

_settings = get_settings()

logging.basicConfig(
    level=logging.DEBUG if _settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)-24s | %(message)s",
)

app = FastAPI(
    title=_settings.app_name,
    version="1.0.0",
    description="3D Scanning Web App — Reconstruction Backend (FastAPI + Redis + MinIO + COLMAP/OpenMVS)",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(v1_router, prefix=_settings.api_v1_prefix)


@app.get("/")
def root() -> dict:
    return {
        "service": _settings.app_name,
        "version": "1.0.0",
        "docs": "/docs",
        "api_v1": _settings.api_v1_prefix,
    }


@app.on_event("startup")
def _startup() -> None:
    from .storage import ObjectStore
    from .storage.job_store import JobStore

    store = ObjectStore(_settings)
    store.initialize()
    jobs = JobStore(_settings)
    jobs.initialize()
    logging.getLogger(__name__).info("Startup complete — services initialized")
