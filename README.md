# 3D Scanning Web App — Full Setup Guide

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Hardware Requirements](#3-hardware-requirements)
4. [Software Prerequisites](#4-software-prerequisites)
5. [Part A — Backend Reconstruction Service](#5-part-a--backend-reconstruction-service)
   - [A1 — Quick Start (Docker, recommended)](#a1--quick-start-docker-recommended)
   - [A2 — Local Dev (no Docker)](#a2--local-dev-no-docker)
   - [A3 — Verify the Backend](#a3--verify-the-backend)
6. [Part B — Edge Capture Node](#6-part-b--edge-capture-node)
   - [B1 — Raspberry Pi 5 Production Setup](#b1--raspberry-pi-5-production-setup)
   - [B2 — Hailo-8L AI Models](#b2--hailo-8l-ai-models)
   - [B3 — Local Dev Mock Mode (no hardware)](#b3--local-dev-mock-mode-no-hardware)
7. [Part C — End-to-End Smoke Test](#7-part-c--end-to-end-smoke-test)
8. [Part D — Configuration Reference](#8-part-d--configuration-reference)
9. [Default URLs & Credentials](#9-default-urls--credentials)
10. [Troubleshooting](#10-troubleshooting)
11. [Project Layout](#11-project-layout)
12. [Next Steps (P4–P6)](#12-next-steps-p4p6)

---

## 1. Project Overview

A photogrammetry-based 3D scanning pipeline split across two tiers:

| Tier          | Runs on                         | Job                                                                                  |
| ------------- | ------------------------------- | ------------------------------------------------------------------------------------ |
| **Edge Node** | Raspberry Pi 5 8GB + Hailo-8L   | Captures 20–200 photos, runs realtime background segmentation + depth estimation, uploads a structured bundle. |
| **Backend**   | Any Linux/Windows/Mac host (Docker) | Validates the bundle, runs COLMAP SfM → OpenMVS densification → mesh post-processing, serves `.ply` + `.glb` via a REST API. |
| **Viewer**    | Browser (P4, deliverable next)  | Renders the GLB/PLY in WebGL (Three.js / React Three Fiber) with measurement tools.  |

All four mandated project objectives are wired together:
1. ✅ Edge node photographs targets (real camera or mock)
2. ✅ Hailo-8L runs segmentation + depth (real Hailo or mock)
3. ✅ Backend produces `.PLY` + `.GLB` (real COLMAP/OpenMVS or mock)
4. ⬜ In-browser viewer renders (P4)

Every component has a **graceful mock fallback**, meaning you can exercise the full upload → job → asset pipeline on a plain Windows/Mac laptop without any hardware or 3D binaries installed.

---

## 2. System Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          EDGE NODE (Raspberry Pi 5)                       │
│                                                                           │
│  Pi Camera Module  ──►  CameraCapture (picamera2)  ──►  rgb/jpeg/raw     │
│                                                  │                        │
│                                                  ▼                        │
│                           HailoInferenceEngine (HailoRT PCIe)             │
│                             ├─ yolov8n-seg.hef  →  foreground mask       │
│                             └─ dpt_lite.hef     →  disparity/depth       │
│                                                  │                        │
│                                                  ▼                        │
│                          CapturePipeline → CaptureBundle                  │
│                            (manifest.json + per-shot 5-file dirs)         │
│                                                  │                        │
│                                                  ▼                        │
│                          BackendUploadClient (httpx)                      │
│                            POST /bundles/init → PUT shots → /finalize    │
└─────────────────────────────────────┬────────────────────────────────────┘
                                      │ HTTPS / LAN
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                     BACKEND (Docker compose on host)                      │
│                                                                           │
│  FastAPI API (:8000)  ──►  v1_router  ──►  ObjectStore (MinIO :9000)     │
│        │                      │                                           │
│        │    bundle validated  │                                           │
│        ▼                      ▼                                           │
│  JobStore (FS JSON + Redis) ──► Celery task queue (Redis :6379)          │
│                                      │                                    │
│                                      ▼                                    │
│                         ReconstructionWorker (9 stages)                   │
│                           1. download bundle                               │
│                           2. background-strip (use edge masks)            │
│                           3. COLMAP sparse (feature → match → mapper)     │
│                           4. COLMAP dense  (undistort → patch-match-stereo│
│                           5. OpenMVS densify / reconstruct / texture      │
│                           6. post-process (decimate, clean)               │
│                           7. write .PLY                                    │
│                           8. write .GLB (textured)                        │
│                           9. upload to asset bucket + job_done            │
│                                      │                                    │
│                                      ▼                                    │
│                          GET /api/v1/assets/{id}/ply  → 302 → MinIO URL  │
│                          GET /api/v1/assets/{id}/glb  → 302 → MinIO URL  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Hardware Requirements

### 3.1 Edge Node (Production)

| Component                | Spec / Model                              | Notes                                         |
| ------------------------ | ----------------------------------------- | --------------------------------------------- |
| SBC                      | **Raspberry Pi 5 8GB**                    | 4GB works; 8GB recommended for Hailo DMA bufs |
| Camera                   | **Raspberry Pi Camera Module v3** (12MP)  | Works with Module 2/3/HQ; 4056×3040 default   |
| AI Accelerator           | **Hailo-8L AI HAT** (13 TOPS)             | PCIe HAT form factor                          |
| Storage                  | ≥ 64 GB microSD (A2 rated)                | Store raw captures locally                    |
| Power                    | 5V/5A USB-C PD or official Pi 5 PSU       | Hailo-8L draws ~6–8W peak extra               |
| Network                  | Gigabit Ethernet or 5GHz Wi-Fi            | Uploads ~500 MB–3 GB per session              |

### 3.2 Backend Host (Docker)

Any x86_64 / ARM64 machine with Docker ≥ 24:
- **CPU**: 4+ cores (reconstruction is multi-threaded)
- **RAM**: ≥ 16 GB (32 GB for dense OpenMVS on 200+ photos)
- **Disk**: ≥ 50 GB free (MinIO + intermediate COLMAP files)
- **GPU** (optional): CUDA GPU accelerates COLMAP feature matching — set `COLMAP_BIN` to a CUDA build

---

## 4. Software Prerequisites

### 4.1 Backend Host

| Tool     | Min Version | Install link                                    |
| -------- | ----------- | ----------------------------------------------- |
| Docker   | 24.0        | https://docs.docker.com/engine/install/         |
| Compose  | v2 (plugin) | Included with Docker Desktop / Engine 24+       |

That's it — Python, Redis, MinIO, FastAPI, and the worker all run inside containers.

### 4.2 Raspberry Pi 5 (Edge Node)

| Tool            | Notes                                                        |
| --------------- | ------------------------------------------------------------ |
| Raspberry Pi OS | **Bookworm 64-bit** (desktop or lite; Python 3.11 included)  |
| Picamera2       | `sudo apt install -y python3-picamera2 python3-libcamera`    |
| HailoRT         | Add Hailo apt repo, then `sudo apt install hailort hailort-py` |
| Python pip deps | `pip install -r edge_node/requirements.txt`                  |

### 4.3 Local Dev (Mock Mode, no hardware)

- Python ≥ 3.11
- pip

No Docker required for pure edge-node+backend smoke test if you run them directly (see §A2 and §B3).

---

## 5. Part A — Backend Reconstruction Service

The backend publishes the upload API at `http://localhost:8000`. This is the **same URL** hardcoded as the default in [edge_node/config/config.yaml](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/edge_node/config/config.yaml#L29-L34).

### A1 — Quick Start (Docker, recommended)

Run these commands from a terminal on the backend host:

```bash
# 1. Clone / navigate into the project root
cd SnakeBot

# 2. Copy env template (no edits needed for localdev)
cd backend
cp .env.example .env

# 3. Build + start all 4 services (Redis, MinIO, API, Worker)
#    First build takes ~2–5 min; subsequent runs are instant.
docker compose up --build -d

# 4. Wait for healthchecks (~20 s). Check status with:
docker compose ps
# All 4 services should show "healthy" or "Up (healthy)"
```

Services start in dependency order via compose `depends_on.condition: service_healthy`:
1. Redis (queue + cache)
2. MinIO (object storage)
3. FastAPI API + Celery worker (wait for 1 + 2 healthy)

### A2 — Local Dev (no Docker)

If you don't have Docker (e.g. restricted sandbox), you can still run the backend. It will automatically fall back to:
- **File-based object store** (`./backend/data/tmp/minio_mock/`) instead of MinIO
- **FS JSON job registry** instead of Redis
- **Inline thread worker** instead of Celery

```bash
cd backend
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt

# Launch API (starts an inline reconstruction worker in a daemon thread
# if no Redis broker is reachable on :6379)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### A3 — Verify the Backend

From any terminal on the same machine:

```bash
# FastAPI alive
curl http://localhost:8000/api/v1/health
# -> {"status":"ok","version":"1.0","redis":"ok","minio":"ok"}  (or "fs_fallback")

# Swagger UI — paste this URL in a browser to explore the API interactively
# http://localhost:8000/docs

# MinIO console (if running Docker)
# http://localhost:9001   user: minioadmin   pass: minioadmin
```

---

## 6. Part B — Edge Capture Node

### B1 — Raspberry Pi 5 Production Setup

#### Step 1 — Flash OS & boot

1. Use **Raspberry Pi Imager** → choose **Raspberry Pi OS (64-bit) Bookworm Lite**
2. In Imager advanced options:
   - Set hostname: e.g. `scan-pi-01.local`
   - Enable SSH (password auth or public key)
   - Configure Wi-Fi / locale
   - Default user `pi` + a strong password
3. Boot, SSH in: `ssh pi@scan-pi-01.local`

#### Step 2 — System updates + Picamera2 + libcamera

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y python3-picamera2 python3-libcamera python3-pip python3-venv git
sudo reboot
```

Test camera:

```bash
libcamera-hello --list-cameras
# You should see: "Available cameras" with 1 entry (imx708 / imx477 etc.)
```

#### Step 3 — Install Hailo-8L SDK

HailoRT is **not on PyPI** — use the official Hailo apt repo:

```bash
# Add Hailo GPG key + repo (check Hailo docs if the key URL changes)
curl -fsSL https://hailo.ai/apt/KEY.gpg | sudo gpg --dearmor -o /usr/share/keyrings/hailo-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hailo-archive-keyring.gpg] https://hailo.ai/apt/ bookworm main" | sudo tee /etc/apt/sources.list.d/hailo.list

sudo apt update
sudo apt install -y hailort hailort-py
```

Verify Hailo-8L is detected:

```bash
hailortcli scan
# Should print 1 Hailo-8L device on PCIe with a serial number
```

#### Step 4 — Project + Python deps

```bash
cd ~
git clone <your-repo-url> SnakeBot
cd SnakeBot/edge_node

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### Step 5 — Edge node health check

```bash
# Always run from the edge_node/ directory so ./models and ./captures resolve
cd ~/SnakeBot/edge_node
python -m edge_node health-check
# Expected output:
#   [OK] Config loaded
#   [OK] Camera: libcamera probe succeeded (1 camera)
#   [OK] Hailo: 1 device(s) found (serial: XXXXXXXX)
#   [OK] Backend connectivity: http://<your-backend-ip>:8000/api/v1/health -> 200
```

If the backend is on your LAN (not on the Pi itself), edit the `upload.backend_url` in [edge_node/config/config.yaml](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/edge_node/config/config.yaml#L29-L34), e.g.:

```yaml
upload:
  backend_url: "http://192.168.1.42:8000"
```

### B2 — Hailo-8L AI Models

The Hailo engine in [edge_node/ai/hailo_engine.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/edge_node/ai/hailo_engine.py#L1-L440) expects two **compiled `.hef`** binaries in `edge_node/models/`:

| File            | Purpose                        | Source                                                     |
| --------------- | ------------------------------ | ---------------------------------------------------------- |
| `yolov8n-seg.hef` | Background / object segmentation | Hailo Model Zoo `data/download_hefs.py yolov8n-seg`        |
| `dpt_lite.hef`  | Monocular depth estimation     | Hailo Model Zoo `data/download_hefs.py dpt_lite`           |

Download them (run anywhere that has `git` + Python):

```bash
git clone https://github.com/hailo-ai/hailo_model_zoo
cd hailo_model_zoo
pip install -r requirements.txt
python data/download_hefs.py yolov8n-seg
python data/download_hefs.py dpt_lite
# copy the .hef files into SnakeBot/edge_node/models/
```

Then re-verify:

```bash
cd ~/SnakeBot/edge_node
python -m edge_node verify-hailo
# Expected: both .hef files load, inference on a dummy frame completes in <80 ms
```

### B3 — Local Dev Mock Mode (no hardware)

On a Windows/Mac dev laptop that has no Pi Camera or Hailo-8L, **the edge node falls back automatically** — you don't need to enable anything:

- `CameraCapture` → returns deterministic synthetic RGB frames (gradient + test pattern) with EXIF
- `HailoInferenceEngine` → returns ellipse foreground mask + radial depth map

Install Python deps:

```bash
cd edge_node
python -m venv .venv
# Windows
.venv\Scripts\Activate.ps1
# macOS/Linux
# source .venv/bin/activate
pip install -r requirements.txt
```

Health check confirms fallbacks are active:

```bash
python -m edge_node health-check
# Look for these lines:
#   [OK] Camera: using MOCK driver (Picamera2 not importable)
#   [OK] Hailo: using MOCK inference engine (hailo_platform not importable)
```

Perfect for developing the backend upload pipeline, viewer, or CI.

---

## 7. Part C — End-to-End Smoke Test

This is the single most-important command in the repo. It validates every layer end-to-end **without hardware or 3D binaries** (mock mode).

### Prerequisites

1. Backend is running on `http://localhost:8000` (Docker §A1 or local §A2)
2. Edge node deps are installed (§B3 on laptop, §B1 on Pi)
3. You are in `SnakeBot/edge_node` (or pass `--config` explicitly)

### Run it

```bash
cd edge_node
python -m edge_node capture-and-upload --shots 20 --interval 0.1 --wait
```

What `--wait` does:
1. Runs capture pipeline → 20 mock photos, masks, depths, manifest
2. Calls `/bundles/init` → gets upload_id + job_id
3. PUTs each shot's 5 files to `/bundles/{id}/shots/{sid}/{role}` with `X-Content-SHA256` ETag
4. Calls `/bundles/{id}/finalize` with manifest ETag
5. Polls `GET /api/v1/jobs/{id}` every 2 s until `status == completed`
6. Prints the final job response:

```
========================================================
JOB FINISHED SUCCESSFULLY
========================================================
job_id:        job_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
status:        completed
ply_url:       http://localhost:8000/api/v1/assets/job_xxx/ply
glb_url:       http://localhost:8000/api/v1/assets/job_xxx/glb
ply_size:      2,438,192 bytes
glb_size:        872,410 bytes
num_vertices:     50,000
num_faces:        98,000
viewer_url:    /viewer?job_id=job_xxx
========================================================
```

### Download the outputs

```bash
# 302 → MinIO presigned URL; curl -L follows
curl -L http://localhost:8000/api/v1/assets/job_xxx/ply -o scan.ply
curl -L http://localhost:8000/api/v1/assets/job_xxx/glb -o scan.glb
```

Open `scan.glb` in any 3D viewer (Windows 3D Viewer, macOS Preview, Blender, https://modelviewer.dev/editor/) — you should see a textured cylinder (mock pipeline output) or your actual scan (if you ran with real hardware + COLMAP/OpenMVS).

---

## 8. Part D — Configuration Reference

### 8.1 Edge Node YAML — [edge_node/config/config.yaml](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/edge_node/config/config.yaml#L1-L52)

| Section  | Key                       | Default                 | What it does                                                  |
| -------- | ------------------------- | ----------------------- | ------------------------------------------------------------- |
| capture  | resolution.{width,height} | 4056 / 3040             | Pi Camera resolution (12 MP v3 full frame)                    |
| capture  | num_shots                 | 20                      | Default shots per session (override with `--shots N`)        |
| capture  | shot_interval_sec         | 1.5                     | Seconds between shutter actuations (for camera settle)       |
| capture  | save_raw / save_jpeg      | true / true             | Keep RAW .dng pairs alongside JPEGs                           |
| hailo    | segmentation_model_path   | ./models/yolov8n-seg.hef| Absolute/rel path to segmentation .hef                        |
| hailo    | depth_model_path          | ./models/dpt_lite.hef   | Absolute/rel path to depth .hef                               |
| upload   | backend_url               | http://localhost:8000   | **Change to your backend LAN IP/hostname if Pi ≠ backend.**  |
| upload   | max_retries / retry_delay | 3 / 5s                  | Resilience to transient network blips                         |
| storage  | local_dir                 | ./captures              | Where raw shots are kept before (and after) upload           |
| storage  | keep_local_after_upload   | true                    | Set to false to save SD card space on the Pi                  |

### 8.2 Backend Env — [backend/.env.example](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/.env.example#L1-L34)

Copy to `.env`; only change these if you know you need to:

| Var                         | Default                            | When to change                                              |
| --------------------------- | ---------------------------------- | ----------------------------------------------------------- |
| `DEBUG`                     | `true`                             | Set `false` in prod; disables stack traces                  |
| `CORS_ORIGINS`              | `*`                                | Restrict to your viewer domain(s) in prod                   |
| `REDIS_URL`                 | `redis://redis:6379/0`             | Point at an external Redis if scaling beyond 1 machine      |
| `MINIO_ACCESS_KEY/SECRET`   | `minioadmin / minioadmin`          | Rotate for prod!                                            |
| `WORKER_CONCURRENCY`        | `2`                                | Bump to `num CPU cores` on a dedicated recon server         |
| `MAX_UPLOAD_SIZE_MB`        | `8192`                             | Raise if you shoot huge RAW sessions (>200 × 12 MP DNGs)   |
| `COLMAP_BIN` / `OPENMVS_DIR`| `colmap` / `/usr/local/bin/OpenMVS`| Install real COLMAP/OpenMVS and point these for production  |
| `PLY_DECIMATE_TARGET_RATIO` | `0.5`                              | `0.1` = more aggressive mesh decimation; `1.0` = keep all  |
| `GLB_TEXTURE_SIZE`          | `4096`                             | 2048 on memory-constrained viewers; 8192 for high-quality   |

---

## 9. Default URLs & Credentials

| Service             | URL                                              | User         | Password     |
| ------------------- | ------------------------------------------------ | ------------ | ------------ |
| FastAPI REST        | http://localhost:8000/api/v1                     | —            | —            |
| Swagger / OpenAPI   | http://localhost:8000/docs                       | —            | —            |
| MinIO S3 API        | http://localhost:9000                            | minioadmin   | minioadmin   |
| MinIO Web Console   | http://localhost:9001                            | minioadmin   | minioadmin   |
| Redis CLI           | `redis-cli -h localhost -p 6379`                 | —            | — (no auth)  |
| Backend (default) in edge config | `http://localhost:8000`                | —            | —            |

**Production hardening reminders**:
- Rotate MinIO credentials (env vars + `.env` file)
- Put a TLS-terminating reverse proxy (Nginx / Caddy / Cloudflare) in front of `:8000`
- Enable Redis AUTH or use a managed Redis in cloud deployments
- Add `api_key` to edge config + backend auth middleware

---

## 10. Troubleshooting

### E1 — `docker compose up` fails on build / healthcheck

```bash
docker compose down -v        # wipe volumes (resets corrupt MinIO/Redis state)
docker compose build --no-cache
docker compose up -d
```

### E2 — Edge node `capture-and-upload` → `ConnectionRefused: localhost:8000`

Confirm the backend container is `Up (healthy)`:
```bash
docker compose ps
```
If it's running on another machine, edit `edge_node/config/config.yaml → upload.backend_url` to the backend LAN IP and retry.

### E3 — Reconstruction job stuck in `pending` forever

The Celery worker is dead or Redis was restarted. Check:
```bash
docker compose logs worker --tail 200
```
Most common cause: `MINIO_ACCESS_KEY/SECRET` mismatch between `.env` and the compose env override.

### E4 — Job completes but PLY is empty / zero vertices

COLMAP failed to register enough views (typical: < 8 photos, featureless white wall, or no overlap). Re-shoot with ≥ 20 photos, 60–80% overlap, and feature-rich targets.

### E5 — Hailo `RuntimeError: No Hailo device found`

- Reseat the Hailo-8L HAT on the Pi 5 PCIe header (screw it down firmly — it must be level)
- Verify `hailortcli scan` returns a device
- If you only get a mock engine: Hailo apt packages were installed for `bookworm` but you're running `bullseye` — reinstall Bookworm 64-bit and retry §B1

### E6 — Picamera2 `RuntimeError: failed to allocate buffers`

- Use the **official Pi 5 5V/5A PSU**. Undervolt on 3A supplies causes intermittent camera DMA failures.
- Reduce capture resolution in `config.yaml → capture.resolution` temporarily to rule out bandwidth.

### E7 — Upload ETag mismatch at `/finalize`

Almost never a code bug — means one of the PUTs was silently truncated by a proxy. Raise `upload.upload_timeout_sec` or switch from Wi-Fi to Ethernet on the Pi. The edge node retries 3× by default; if it still fails, run with `--verbose` and check `./logs/edge_node.log` for the failing PUT.

---

## 11. Project Layout

```
SnakeBot/
├─ backend/                           P3 — Reconstruction Backend
│  ├─ app/
│  │  ├─ api/v1.py                    FastAPI v1 router (bundles init/finalize, jobs, assets)
│  │  ├─ storage/
│  │  │  ├─ object_store.py           MinIO wrapper + FS fallback (presigned URLs, ETag PUT)
│  │  │  └─ job_store.py              Bundle upload state + job registry (disk-snapshot + RLock)
│  │  ├─ workers/
│  │  │  ├─ reconstruction.py         9-stage recon pipeline (COLMAP/OpenMVS or pure-Python mock)
│  │  │  └─ tasks.py                  Celery reconstruct_3d task + inline thread fallback
│  │  ├─ config.py                    Pydantic BaseSettings loader
│  │  ├─ schemas.py                   JobStatus/JobStage/JobResponse/BundleInit pydantic models
│  │  └─ main.py                      FastAPI factory + CORS + startup probe
│  ├─ Dockerfile
│  ├─ docker-compose.yml              4 services: redis / minio / api(:8000) / worker
│  ├─ requirements.txt
│  ├─ .env.example
│  └─ README.txt
│
├─ edge_node/                         P1/P2 — Edge Capture Node (Pi 5 + Hailo)
│  ├─ capture/camera.py               Picamera2 driver (RAW+JPEG+EXIF) + deterministic mock
│  ├─ ai/hailo_engine.py              HailoRT YOLOv8-seg + DPT-Lite wrapper + mock fallback
│  ├─ pipeline/
│  │  ├─ capture_pipeline.py          CapturePipeline fuses capture+AI → CaptureBundle + manifest
│  │  └─ upload_client.py             BackendUploadClient (init → PUT shots → finalize → poll job)
│  ├─ config/
│  │  ├─ __init__.py                  Typed dataclass loader + validate()
│  │  └─ config.yaml                  Defaults (4056×3040, 20 shots, backend localhost:8000)
│  ├─ utils/logging_setup.py          Rotating file + console logger
│  ├─ models/README.txt               Where to put yolov8n-seg.hef + dpt_lite.hef
│  ├─ main.py                         4-subcommand CLI: health-check/verify-hailo/capture/capture-and-upload
│  ├─ __main__.py                     `python -m edge_node` entrypoint
│  └─ requirements.txt
│
└─ README.md                          THIS FILE (you are here)
```

Key protocol file pairs to read together if you're hacking on the upload/job flow:
- [upload_client.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/edge_node/pipeline/upload_client.py#L1-L230) and [v1.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/app/api/v1.py#L1-L260) — HTTP call shapes are co-designed; every header and JSON key matches.
- [reconstruction.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/app/workers/reconstruction.py#L1-L500) and [job_store.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/app/storage/job_store.py#L1-L215) — 9 stage-progress events + final asset stats.

---

## 12. Next Steps (P4–P6)

### P4 — Browser 3D Viewer (Recommended next deliverable)
Once you have GLB/PLY URLs from a completed job, the viewer will:
- Render the GLB with React Three Fiber (`/viewer?job_id=…` matches the `viewer_url` in the job response)
- Fall back to a PLY point-cloud renderer for raw COLMAP outputs
- Add mesh distance/angle measurement tools, annotation pins, and share-token URLs
- Validate CORS against `:8000` backend

### P5 — Integration + Benchmarking Harness
- E2E script: spin up backend → run mock edge capture → download PLY → assert vertex count ≥ N
- Accuracy benchmark against reference CAD `.stl`
- Headless browser viewer FPS benchmark (R3F on 100k / 1M / 10M triangles)
- Pi 5 Hailo-8L thermal + power stress loop (1000 consecutive inferences)

### P6 — Optimization / Deploy / Handoff
- Production Docker image (multi-stage, slim, no pip build deps)
- Pre-flashed Pi 5 OS image + Ansible provisioning
- Enclosure BOM + STL files (3D-printable with the camera + Hailo facing forward)
- GitHub Actions CI: py_compile + ruff + mypy + E2E smoke on green-main gate
- Horizontal worker scaling: how to add a CUDA recon farm of N workers against shared Redis/MinIO

---

*Document version 1.0 — 2026-09-14.*
