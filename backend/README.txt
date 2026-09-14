# 3D Scanning Web App — Backend Reconstruction Service
# =====================================================
# Raspberry Pi 5 / Pi Camera / Hailo-8L upload target
#
# Default localdev runtime mode: NO external binaries needed. If COLMAP and
# OpenMVS are unavailable, the worker synthesizes a valid PLY mesh + GLB
# placeholder so you can fully exercise the upload → job → asset pipeline
# (perfect for frontend viewer integration).
#
# For production-grade photogrammetry, install COLMAP + OpenMVS system-wide
# and point OPENMVS_DIR / COLMAP_BIN at them.

services:
  1.  Bring the stack up:
      ```
      cd backend
      cp .env.example .env
      docker compose up --build -d
      ```

  2.  Verify it's alive:
      ```
      curl http://localhost:8000/
      curl http://localhost:8000/api/v1/health
      ```

  3.  Point the edge node at it:
      ```
      cd ..
      python -m edge_node capture-and-upload \
        --backend http://<your-ip>:8000 \
        --shots 20 --interval 1.5 --wait
      ```
      (or on Windows dev: default backend in config/config.yaml is
      `http://localhost:8000` already, so no override needed in `capture-and-upload`)

  4.  Poll the job you just created:
      ```
      curl http://localhost:8000/api/v1/jobs/<job_id>
      ```

  5.  Once completed, retrieve the outputs:
      ```
      # redirects to MinIO presigned URLs
      curl -L http://localhost:8000/api/v1/assets/<job_id>/ply  -o scan.ply
      curl -L http://localhost:8000/api/v1/assets/<job_id>/glb  -o scan.glb
      ```

  6.  View assets in the browser viewer (P4 — frontend repo comes next):
      Job response includes a `viewer_url` field: `/viewer?job_id=<job_id>`

Directory layout:
```
backend/
├─ app/
│  ├─ api/               FastAPI v1 router (bundle init/finalize, jobs, assets)
│  ├─ storage/           MinIO object-store wrapper + in-Redis/FS job registry
│  ├─ workers/           Celery tasks + COLMAP/OpenMVS reconstruction pipeline
│  ├─ config.py          Pydantic BaseSettings (reads .env)
│  ├─ schemas.py         Shared Pydantic response models
│  └─ main.py            FastAPI app factory + CORS + startup health
├─ Dockerfile
├─ docker-compose.yml    Redis / MinIO / API / Worker
├─ requirements.txt
└─ .env.example
```
