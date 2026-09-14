# 3D Scanning Web App — Full Setup Guide

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Hardware Requirements](#3-hardware-requirements)
4. [Software & Package Requirements](#4-software--package-requirements)
   - [4.1 Backend Host — Container Runtime](#41-backend-host--container-runtime)
   - [4.2 Backend — System Packages (apt / image layer)](#42-backend--system-packages-apt--image-layer)
   - [4.3 Backend — Python Packages (pip)](#43-backend--python-packages-pip)
   - [4.4 Backend — 3D Reconstruction Binaries (production, optional)](#44-backend--3d-reconstruction-binaries-production-optional)
   - [4.5 Edge Node — Raspberry Pi OS + System Apt Packages](#45-edge-node--raspberry-pi-os--system-apt-packages)
   - [4.6 Edge Node — Hailo-8L SDK Packages](#46-edge-node--hailo-8l-sdk-packages)
   - [4.7 Edge Node — Python Packages (pip)](#47-edge-node--python-packages-pip)
   - [4.8 Local Dev Mock Mode (no hardware)](#48-local-dev-mock-mode-no-hardware)
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

## 4. Software & Package Requirements

Every package below is listed **verbatim** from the project's manifest files:
- Backend pip: [backend/requirements.txt](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/requirements.txt)
- Backend system apt layer: [backend/Dockerfile](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/Dockerfile#L19-L23)
- Edge node pip: [edge_node/requirements.txt](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/edge_node/requirements.txt)
- Edge node system: Raspberry Pi OS Bookworm apt packages listed inline with install commands

Where a package is marked **(optional)**, the subsystem has a graceful mock/fallback — you can skip it and the pipeline still runs end-to-end.

---

### 4.0 — INSTALL LOCATION DECISION MATRIX (read this first!)

The project has **two code folders → two physical machines**. This table answers, for *every* package group, **which machine you run the install commands on**:

| Package group / folder                 | Install on 🖥️ **Machine A — Backend Host** (your Windows/macOS/Linux laptop, desktop, or server, x86_64 or ARM64, 16+ GB RAM preferred) | Install on 🍓 **Machine B — Raspberry Pi 5** (the Pi 5 8GB with camera + Hailo-8L HAT) |
| -------------------------------------- | :-------: | :-------: |
| `SnakeBot/backend/` folder + Docker    | ✅ **YES** — `cd backend` lives here, `docker compose up` runs here | ❌ NO — (only backend mock mode *can* run here for CI, never real COLMAP/OpenMVS) |
| `SnakeBot/edge_node/` folder           | ✅ *Optional* — for local mock dev on laptop (§4.8) | ✅ **YES** — this is where the camera/Hailo physically live |
| Docker Desktop / Docker Engine         | ✅ **YES** — on the backend host                     | ❌ NO — (technically installable, but COLMAP image will OOM on 8 GB) |
| Docker compose stack (redis, minio, api, worker) | ✅ **YES** — `docker compose up --build -d` runs here | ❌ NO                                  |
| **COLMAP** (§4.4 — dense recon)       | ✅ **YES** — only on this machine. See 3 install paths in §4.4. | ❌ **DO NOT INSTALL ON PI 5.** §4.4 warning explains: ≥14 GB RAM usage + OOM + 10–30× slowdown + thermal throttle. |
| **OpenMVS** (§4.4 — texturing)        | ✅ **YES** — only on this machine. Build-from-source script in §4.4. | ❌ **DO NOT INSTALL ON PI 5.** Same OOM/thermal issues, takes hours vs minutes on x86/CUDA. |
| `trimesh` / `open3d` (§4.3, optional 3D pip) | ✅ **YES** — backend worker pip install, used in decimation + GLB fallback | ❌ NO — not used on the edge; edge only *captures* and *uploads*, never decimates meshes. |
| Backend Python pip deps (§4.3: fastapi / celery / redis / minio / …) | ✅ **YES** — if running §A2 local venv (no Docker). Auto-installed inside container if using Docker path. | ❌ NO — backend doesn't run on Pi (unless mock smoke-test only). |
| Backend apt layer (§4.2: curl/ca-certificates/libgomp1) | ✅ **YES** — if §A2 no-Docker. Auto-in Dockerfile. | ❌ NO                                  |
| **Picamera2** (apt: `python3-picamera2`) | ❌ NO — laptop cameras aren't wired to libcamera HAL. | ✅ **YES** — this is the Pi camera driver, lives only here. |
| **HailoRT SDK** (§4.6, apt: `hailort` + `hailort-py`) | ❌ NO — no Hailo hardware inside a normal laptop/desktop. | ✅ **YES** — only on the Pi 5 with the Hailo-8L HAT physically installed. |
| Edge Node Python pip deps (§4.7: PyYAML / numpy / Pillow / httpx / opencv-python-headless) | ✅ *Optional* — for §B3 mock dev on laptop. | ✅ **YES** — install into the `.venv` on the Pi 5. |

**TL;DR folder → machine rule of thumb:**
- **`backend/` directory + COLMAP + OpenMVS + trimesh/open3d** → live on **your big computer (laptop/desktop/server)**. This is CPU/RAM/GPU-heavy work.
- **`edge_node/` directory + Picamera2 apt + HailoRT apt** → live on **the Raspberry Pi 5**. This is hardware-capture + Hailo-accelerated AI, then upload. That's it.
- You **can** also `git clone` the entire `SnakeBot/` repo onto *both* machines if you want; just follow the install commands for *that machine's* section.

---

### 4.0b — PER-MACHINE INSTALL CHEAT SHEET (copy-paste in order)

Pick the sheet that matches the machine you are physically sitting at.

**🖥️ Machine A — Backend Host (your laptop / desktop / server):**
```bash
# ------------------------------------------------------------------
# Step 0 (if Windows): enable WSL2 first, then open an Ubuntu shell
#   wsl --install -d Ubuntu-24.04
# ------------------------------------------------------------------

# 1. Install Docker (only if you don't already have Docker Desktop)
#    -> https://docs.docker.com/engine/install/ubuntu/   (Linux)
#    -> https://www.docker.com/products/docker-desktop/   (Windows/macOS)
docker --version     # confirm >= 24.0
docker compose version

# 2. Clone the repo (if not cloned yet)
git clone <your-repo-url> SnakeBot
cd SnakeBot

# 3. Bring up backend (Redis + MinIO + API + Worker — 4 containers)
cd backend
cp .env.example .env
docker compose up --build -d
# wait ~30 s; all 4 services should be "healthy"
docker compose ps

# 4. OPTIONAL PRODUCTION — install COLMAP 3.9.1 + OpenMVS 2.2.0 with CUDA:
#    Save the §4.4 "Option 3" block as backend/Dockerfile.cuda, then:
#    docker compose down -v
#    # edit docker-compose.yml: build.dockerfile -> Dockerfile.cuda; worker.runtime: nvidia
#    docker compose up --build -d
#    OR install directly on metal via §4.4 Option 1 apt / Option 2 conda + §4.4 openmvs build script

# 5. OPTIONAL: extra 3D pip deps inside existing venv (not needed if you use Docker, worker installs them)
#    cd backend
#    pip install -r requirements.txt
#    pip install "trimesh>=4.0" "open3d>=0.18" --default-timeout=300

# Verify: http://localhost:8000/api/v1/health  returns {"status":"ok"}
curl http://localhost:8000/api/v1/health
```

**🍓 Machine B — Raspberry Pi 5 (ssh into it, Hailo-8L + camera physically installed):**
```bash
# 1. OS flash + boot: Raspberry Pi Imager → Bookworm Lite 64-bit, SSH on, Wi-Fi on
#    Then from your laptop:
#    ssh pi@scan-pi-01.local

# 2. System update + Picamera2 + libcamera + python venv + git
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y \
  python3-picamera2 python3-libcamera \
  python3-pip python3-venv git
sudo reboot

# 3. After reboot, log back in. Verify camera.
libcamera-hello --list-cameras
# -> should see "Available cameras" with 1 entry

# 4. Install Hailo-8L SDK (hailort + hailort-py via Hailo apt repo)
curl -fsSL https://hailo.ai/apt/KEY.gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/hailo-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hailo-archive-keyring.gpg] \
  https://hailo.ai/apt/ bookworm main" \
  | sudo tee /etc/apt/sources.list.d/hailo.list
sudo apt update
sudo apt install -y hailort hailort-py
hailortcli scan     # -> 1 Hailo-8L on PCIe

# 5. Project + Python deps into an isolated venv (Bookworm PEP-668 requires this)
cd ~
git clone <your-repo-url> SnakeBot
cd SnakeBot/edge_node
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# -> installs PyYAML, numpy, Pillow, httpx, + opencv-python-headless if listed

# 6. Drop Hailo .hef model files into SnakeBot/edge_node/models/ (§B2)
#    scp yolov8n-seg.hef dpt_lite.hef pi@scan-pi-01.local:~/SnakeBot/edge_node/models/

# 7. Point upload at Machine A's LAN IP/hostname (NOT localhost:8000 from Pi)
#    edit SnakeBot/edge_node/config/config.yaml line 30:
#      upload.backend_url: "http://192.168.1.42:8000"    # <- Machine A's LAN IP
python -m edge_node health-check
# -> all [OK], last line confirms backend /health returned 200
```

That's the full two-machine install flow. Once both check out, run the §7 E2E smoke test from the Pi 5:
```bash
cd ~/SnakeBot/edge_node
source .venv/bin/activate
python -m edge_node capture-and-upload --shots 20 --interval 1.5 --wait
```

---

### 4.1 Backend Host — Container Runtime

These are the **only** packages you need to install manually on the host if you use the Docker path (recommended). Python, Redis, MinIO, FastAPI, and the worker run entirely inside containers.

| Tool                 | Min Version | Where / how to install                              | Required? |
| -------------------- | ----------- | ---------------------------------------------------- | --------- |
| Docker Engine        | 24.0        | https://docs.docker.com/engine/install/              | ✅ Yes    |
| Docker Compose (v2)  | plugin v2   | Included with Docker Desktop / Engine 24+            | ✅ Yes    |

---

### 4.2 Backend — System Packages (apt / image layer)

Installed automatically inside the [backend/Dockerfile](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/Dockerfile#L19-L23) `apt-get install` step. Listed here if you are running backend locally without Docker (§A2):

| Apt package       | Pulled into image for —                                 | Required? |
| ----------------- | ------------------------------------------------------- | --------- |
| `curl`            | Healthcheck probes, CLI downloads                      | ✅ Yes    |
| `ca-certificates` | TLS root store (for Hailo/MinIO HTTPS endpoints)       | ✅ Yes    |
| `libgomp1`        | OpenMP runtime for numpy / OpenCV SIMD in worker       | ✅ Yes    |

Install copy-paste (Debian/Ubuntu / Linux Mint / any deb-based backend host, outside Docker):
```bash
sudo apt-get update && sudo apt-get install -y --no-install-recommends \
  curl \
  ca-certificates \
  libgomp1
```
macOS / Windows host users: skip this section entirely — use the Docker path (§A1) or a WSL2 Ubuntu VM.

Base image the Dockerfile uses: **`python:3.11-slim-bookworm`** (Debian 12, Python 3.11.x). If running backend outside Docker, use a matching **Python 3.11** interpreter.

---

### 4.3 Backend — Python Packages (pip)

All pinned minimums are from [backend/requirements.txt](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/requirements.txt). Install inside container (automatic) or in a venv for §A2 local dev with `pip install -r backend/requirements.txt`.

| Group                   | PyPI package               | Min pin         | Used in file(s)                                                                 |
| ----------------------- | -------------------------- | --------------- | ------------------------------------------------------------------------------- |
| **API / web framework** | `fastapi`                  | `>=0.105`       | [main.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/app/main.py) |
|                         | `uvicorn[standard]`        | `>=0.24`        | Dockerfile CMD, compose `command:` blocks                                        |
|                         | `pydantic`                 | `>=2.5`         | [schemas.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/app/schemas.py) response models |
|                         | `pydantic-settings`        | `>=2.1`         | [config.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/app/config.py) `.env` loader |
| **Queue / storage**     | `celery`                   | `>=5.3`         | [tasks.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/app/workers/tasks.py) async recon jobs |
|                         | `redis`                    | `>=5.0`         | Celery broker + result backend, job cache                                       |
|                         | `minio`                    | `>=7.2`         | [object_store.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/app/storage/object_store.py) S3-compatible PUT/presign |
|                         | `httpx`                    | `>=0.25`        | Object-store S3 presign fallback calls, health probes                          |
| **Data / image**        | `numpy`                    | `>=1.24,<2`     | [reconstruction.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/app/workers/reconstruction.py) mock PLY vertex math |
|                         | `Pillow`                   | `>=10.0`        | Texture atlas generation, image re-encoding during background-strip            |
|                         | `PyYAML`                   | `>=6.0`         | Debug config dump, manifest validation                                          |
| **(Optional) 3D libs**  | `trimesh`                  | `>=4.0`         | ⬜ PLY quadric decimation + GLB export fallback if no OpenMVS TextureMesh      |
|                         | `open3d`                   | `>=0.18`        | ⬜ Mesh cleaning / poisson / watertight post-processing (CPU or CUDA build)     |
| **(Optional) dev**      | `pytest`                   | `>=7.0`         | ⬜ Unit / E2E test runner                                                        |
|                         | `ruff`                     | `>=0.1`         | ⬜ Lint + import ordering + style                                                |
| *(implicit, FastAPI)*   | `python-multipart`         | `>=0.0.6`       | UploadFile body parser for `/bundles/{bid}/shots/{sid}/{role}` PUT route         |

Install **only required** packages (the default `requirements.txt` is already trimmed this way — 3D + dev lines are commented out).

One-line installs:
```bash
# --- Required packages (already in requirements.txt) ---
cd backend
pip install -r requirements.txt

# --- Optional: 3D post-processing libs (decimation + GLB fallback) ---
# Uncommented below lines install trimesh + open3d. open3d wheel ~500 MB.
pip install "trimesh>=4.0"
# open3d has platform-specific wheels; if this fails check pypi.org/project/open3d for your Python/arch combo
pip install "open3d>=0.18" --default-timeout=300

# --- Optional: dev tools ---
pip install "pytest>=7.0" "ruff>=0.1"
```

---

### 4.4 Backend — 3D Reconstruction Binaries (production, optional)

> ⚠️ **DO NOT install COLMAP or OpenMVS on the Raspberry Pi 5.**
>
> The Pi 5's role in this architecture is **edge capture only** — it shoots photos, runs background segmentation + depth on the Hailo-8L, and **uploads the bundle to the backend host**. Photogrammetry reconstruction runs on a **separate backend machine** (your x86/ARM64 laptop/desktop/server) via §A1 Docker:
>
> - COLMAP dense (patch_match_stereo + stereo_fusion) on 20 × 12 MP photos typically uses **≥14 GB RAM** and **4–8 CPU threads for 30–120 min**; a Pi 5 8GB will **OOM-kill**, **thermal-throttle to 600 MHz** (5W cap), and finish **10–30× slower** than a mid-range laptop.
> - OpenMVS `DensifyPointCloud + TextureMesh` on CUDA is 40× faster again vs Pi 5 CPU.
> - Backend Docker images run fine on **Pi 5 ARM64 in mock mode** (CI, smoke tests); only the COLMAP/OpenMVS stages are unsuitable there.
>
> If you only have a Pi 5 and no other host, skip real COLMAP entirely — the backend's pure-Python mock PLY/GLB pipeline (Stage 9 fallback) will still produce valid downloadable meshes so you can validate the upload → job → viewer roundtrip end-to-end.

The default Docker image runs a pure-Python mock PLY/GLB writer so you can exercise the upload → job → asset pipeline today. For **real photogrammetry**, add the system binaries below and point `COLMAP_BIN` / `OPENMVS_DIR` at them via [.env](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/.env.example#L30-L33):

| Binary / suite    | Min version | Where to get it                                                    | Used in stage(s) — [reconstruction.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/app/workers/reconstruction.py) |
| ----------------- | ----------- | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| **COLMAP**        | 3.8+        | https://colmap.github.io/install.html (apt for Debian: `colmap` or CUDA build from `nvidia/cuda:11.8.0-devel-ubuntu22.04`) | Stage 3 — feature_extractor, exhaustive_matcher, mapper; Stage 4 — image_undistorter, patch_match_stereo, stereo_fusion |
| **OpenMVS**       | 2.2+        | Build from source with CUDA: https://github.com/cdcseacave/openMVS  (binary dir typically `/usr/local/bin/OpenMVS`) | Stage 5 — InterfaceCOLMAP, DensifyPointCloud, ReconstructMesh, RefineMesh, TextureMesh |
| trimesh / open3d  | §4.3 above  | `pip install trimesh open3d` (see §4.3 install block)             | Stage 6 — decimation + cleaning fallback if OpenMVS RefineMesh skipped; Stage 7/8 — alternative PLY/GLB writer            |

---

#### Installing COLMAP (3 options, pick 1)

Run **on the backend host** (x86_64 laptop/server), NOT on the Pi 5.

**Option 1 — Debian/Ubuntu apt (fastest, CPU-only, colmap 3.8 in testing/sid or bookworm-backports):**
```bash
# Debian 12 Bookworm: enable bookworm-backports first
echo "deb http://deb.debian.org/debian bookworm-backports main contrib non-free non-free-firmware" \
  | sudo tee /etc/apt/sources.list.d/backports.list
sudo apt update
sudo apt install -y -t bookworm-backports colmap

# Ubuntu 24.04 / 23.10: direct from universe
# sudo apt install -y colmap

# Verify
colmap -h          # prints usage + version (expect 3.8+)
which colmap       # -> /usr/bin/colmap (set COLMAP_BIN=colmap in .env)
```

**Option 2 — conda-forge (cross-platform, macOS/Linux/Windows x64, latest):**
```bash
conda install -c conda-forge colmap
# or
mamba install -c conda-forge colmap
conda run colmap -h
# COLMAP_BIN=$(conda run which colmap)   -> put this in .env
```

**Option 3 — CUDA-accelerated Docker build (recommended for ≥ 100 photo sessions, NVIDIA GPU only):**
Save as `backend/Dockerfile.cuda` next to the existing non-CUDA Dockerfile:
```dockerfile
# backend/Dockerfile.cuda  —  real photogrammetry build (~8 GB image)
FROM nvidia/cuda:11.8.0-devel-ubuntu22.04 AS base
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common curl ca-certificates libgomp1 git cmake ninja-build \
        build-essential libboost-all-dev libeigen3-dev libfreeimage-dev \
        libgoogle-glog-dev libgtest-dev libsqlite3-dev libglew-dev qtbase5-dev \
        libcgal-dev libcgal-qt5-dev libmetis-dev libceres-dev \
        python3.11 python3.11-venv python3-pip \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && rm -rf /var/lib/apt/lists/*

# Colmap 3.9 from source with CUDA
WORKDIR /opt/colmap
RUN git clone --depth 1 --branch 3.9.1 https://github.com/colmap/colmap.git src \
 && cmake -S src -B build -GNinja -DCMAKE_BUILD_TYPE=Release -DCUDA_ENABLED=ON \
 && cmake --build build --parallel $(nproc) && cmake --install build

# OpenMVS 2.2 from source with CUDA
WORKDIR /opt/openmvs
RUN git clone --depth 1 --branch v2.2.0 https://github.com/cdcseacave/openMVS.git src \
 && cmake -S src -B build -GNinja -DCMAKE_BUILD_TYPE=Release \
         -DOpenMVS_USE_CUDA=ON -DOpenMVS_BUILD_VIEWER=OFF -DVCG_DIR=/opt/openmvs/vcglib \
 && cmake --build build --parallel $(nproc) && cmake --install build

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt && pip3 install --no-cache-dir "trimesh>=4.0"
COPY app /app/app
ENV COLMAP_BIN=colmap OPENMVS_DIR=/usr/local/bin/OpenMVS
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```
Then edit `backend/docker-compose.yml`: change `api.build.dockerfile` and `worker.build.dockerfile` from `Dockerfile` to `Dockerfile.cuda`, and add `runtime: nvidia` + device request under `worker`. Verify GPU with `docker run --rm --gpus all <image> colmap -h`.

---

#### Installing OpenMVS (production builds only)

Skip if you only need the mock pipeline or the trimesh fallback. **OpenMVS has no universal apt/conda package** — build it from source. Run on the backend x86 host:

```bash
# 1. Build-time deps (Debian/Ubuntu)
sudo apt-get update && sudo apt-get install -y --no-install-recommends \
  build-essential cmake git libboost-all-dev libeigen3-dev \
  libopencv-dev libcgal-dev libceres-dev libfreeimage-dev \
  libglew-dev libglfw3-dev libcgal-qt5-dev qtbase5-dev \
  nvidia-cuda-toolkit   # omit if no GPU

# 2. VCG (header-only, OpenMVS dep)
sudo mkdir -p /opt/openmvs && cd /opt/openmvs
sudo git clone --depth 1 https://github.com/cdcseacave/VCG.git vcglib

# 3. Clone + build OpenMVS
sudo git clone --depth 1 --branch v2.2.0 https://github.com/cdcseacave/openMVS.git src
sudo cmake -S src -B build -GNinja \
    -DCMAKE_BUILD_TYPE=Release \
    -DOpenMVS_USE_CUDA=ON \
    -DOpenMVS_BUILD_VIEWER=OFF \
    -DVCG_DIR=/opt/openmvs/vcglib
sudo cmake --build build --parallel $(nproc)
sudo cmake --install build   # -> installs binaries to /usr/local/bin/OpenMVS

# 4. Verify
ls /usr/local/bin/OpenMVS
# -> DensifyPointCloud, InterfaceCOLMAP, ReconstructMesh, RefineMesh, TextureMesh
# Set OPENMVS_DIR=/usr/local/bin/OpenMVS in backend/.env
```

Fallback chain (what runs if each level is missing):
- COLMAP missing → Stage 3/4 no-op, pass through image list → Stage 5 no-op
- OpenMVS missing → Stage 5 no-op
- Both missing → **Stage 9 mock pipeline**: pure-Python/numpy cylinder mesh + ASCII PLY writer + PIL texture atlas → valid `scan.ply` + `scan.glb`


---

### 4.5 Edge Node — Raspberry Pi OS + System Apt Packages

**Base OS requirement**: **Raspberry Pi OS Bookworm 64-bit** (Lite or Desktop). This is non-negotiable — Bullseye ships Python 3.9, the Hailo apt repo is built against Bookworm Python 3.11, and Picamera2/libcamera APIs changed across the release.

Flash tool: **Raspberry Pi Imager** (https://www.raspberrypi.com/software/) → choose "Raspberry Pi OS Lite (64-bit)".

After first boot, install these **Raspberry Pi OS apt packages** (commands also reproduced in §B1):

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y \
  python3-picamera2 \
  python3-libcamera \
  python3-pip \
  python3-venv \
  git
sudo reboot
```

| Apt package             | Purpose                                                                      | Required? |
| ----------------------- | ---------------------------------------------------------------------------- | --------- |
| `python3-picamera2`     | Official libcamera Python bindings for the Pi Camera Module (not on PyPI)    | ✅ Yes    |
| `python3-libcamera`    | Low-level libcamera + sensor tuning shared libs                              | ✅ Yes (dep of picamera2) |
| `python3-pip`           | Pulls in pip for `python3 -m pip install …` in the venv                      | ✅ Yes    |
| `python3-venv`          | `python3 -m venv .venv` isolated env (needed because Bookworm PEP-668)       | ✅ Yes    |
| `git`                   | Clone the project (or use `scp` / USB transfer instead)                      | ✅ Yes    |

---

### 4.6 Edge Node — Hailo-8L SDK Packages

HailoRT packages are **distributed via Hailo's own apt repository**, **not via PyPI**, so `pip install hailort` will not work. Install per §B1 step 3, reproduced here for the package list:

```bash
curl -fsSL https://hailo.ai/apt/KEY.gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/hailo-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hailo-archive-keyring.gpg] \
  https://hailo.ai/apt/ bookworm main" \
  | sudo tee /etc/apt/sources.list.d/hailo.list
sudo apt update
sudo apt install -y hailort hailort-py
```

| Hailo apt package    | Files it installs                                                                      | Used in module: [edge_node/ai/hailo_engine.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/edge_node/ai/hailo_engine.py) |
| -------------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `hailort`            | `/usr/bin/hailortcli`, `libhailort.so*`, Hailo PCIe kernel driver + udev rules         | Device probe, InferVStreams runtime, .hef loader                                            |
| `hailort-py`         | `hailo_platform` Python 3.11 wheel → `from hailo_platform import HEF, HAILO, VDevice`  | Direct import used at top of `hailo_engine.py`; missing → engine falls back to MOCK mode    |

Verification binary (installed as part of `hailort`):
```bash
hailortcli scan     # confirms Hailo-8L on PCIe bus
hailortcli fw-control identify   # prints FW version, serial, architecture (hailo8l)
```

---

### 4.7 Edge Node — Python Packages (pip)

All pinned minimums from [edge_node/requirements.txt](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/edge_node/requirements.txt). Install in a venv on the Pi (§B1 step 4) with:
```bash
cd edge_node
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

| Group                        | PyPI package               | Min pin         | Used in module                                                               |
| ---------------------------- | -------------------------- | --------------- | ---------------------------------------------------------------------------- |
| **Always required**          | `PyYAML`                   | `>=6.0`         | [config/\_\_init\_\_.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/edge_node/config/__init__.py) YAML loader |
|                              | `numpy`                    | `>=1.24,<2`     | [hailo_engine.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/edge_node/ai/hailo_engine.py) postprocess, mock depth math, mask resize |
|                              | `Pillow`                   | `>=10.0`        | [camera.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/edge_node/capture/camera.py) mock frame gen + EXIF injection; JPEG re-encode |
|                              | `httpx`                    | `>=0.25`        | [upload_client.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/edge_node/pipeline/upload_client.py) HTTP session, 3-phase upload, retry |
| *(Optional, image)*          | `opencv-python-headless`   | `>=4.8`         | ⬜ Bilinear mask/depth resizing with HDR-aware `INTER_CUBIC`; Pillow fallback exists if missing |
| *(System only, not pip)*     | `Picamera2`                | —               | Installed via apt `python3-picamera2` in §4.5 — not a wheel. `import picamera2` fails on non-Pi → camera mock kicks in |
| *(System only, not pip)*     | `hailo_platform`           | —               | Installed via apt `hailort-py` in §4.6 — not a PyPI wheel. Import fails → AI mock kicks in |
| *(Optional, dev)*            | `pytest` / `ruff`          | `>=7.0` / `>=0.1` | ⬜ Commented lines in requirements.txt; unit tests + lint                      |

**Mock mode behavior summary** (automatic, no flags):
| Hardware / dep missing         | Fallback used                                                                               |
| ------------------------------ | ------------------------------------------------------------------------------------------- |
| `picamera2` not importable     | `CameraCapture` → synthetic gradient + pattern frames, valid EXIF (focal-length / F-number) |
| `hailo_platform` not importable| `HailoInferenceEngine` → ellipse foreground mask + radial depth map                         |
| `opencv-python-headless` missing | Mask/depth resize via `Pillow.Image.resize` Lanczos 3                                     |

---

### 4.8 Local Dev Mock Mode (no hardware)

On a Windows/Mac/Linux laptop that is **not** a Raspberry Pi and has no Hailo hardware, the minimum install set collapses to **just 4 PyPI packages** per subsystem. Docker is optional.

| Subsystem     | What you install (minimal)                                                                   | Notes                                                                         |
| ------------- | -------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Backend       | `cd backend && pip install -r requirements.txt`                                              | Runs MinIO→FS fallback, Redis→JSON fallback, Celery→inline thread fallback   |
| Edge Node     | `cd edge_node && pip install -r requirements.txt`                                            | Camera→mock RGB, Hailo→mock mask/depth. Uploads to real backend URL.         |
| *(shortcut)*  | Install both venvs side-by-side and run §C E2E smoke test directly on your laptop            | Full pipeline runs in <10 s with mock outputs.                                |


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

> 🔁 **Architecture reminder for the Pi 5**:
>
> Install only **camera, Hailo SDK, Python deps, and upload client** on the Pi 5. **Do not install COLMAP / OpenMVS / the reconstruction worker here.** After `capture-and-upload` the Pi 5's job is done — the backend host (§A1) pulls the bundle from MinIO and runs the 9-stage reconstruction pipeline. If you have no other host, run the backend Docker compose stack directly on the Pi 5 in **mock mode** — you'll still get valid PLY/GLB outputs for the viewer.

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
