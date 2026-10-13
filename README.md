# SnakeBot — 3D Scanning System

Photogrammetry-based 3D scanning pipeline. A Raspberry Pi captures photos + AI masks, a backend reconstructs a 3D mesh, and a web viewer renders it in the browser.

```
Edge Node (Pi 5 + Camera + Hailo-8L)  →  Backend (FastAPI + COLMAP/OpenMVS)  →  Frontend (React + WebGL viewer)
```

Every part has a **mock fallback**, so you can run the whole pipeline on a plain laptop with **no hardware** and **no 3D binaries**.

---

## Project Layout

```
SnakeBot/
├── backend/        FastAPI API + Celery worker + MinIO/Redis (3D reconstruction)
├── edge_node/      Raspberry Pi capture code (camera + Hailo AI + upload)
├── frontend/       React + Vite + Three.js web viewer
└── hardware/       CAD/PCB files (not needed for software)
```

- **Backend docs:** [backend/README.txt](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/README.txt)
- **Frontend docs:** [frontend/README.md](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/frontend/README.md)

---

## Prerequisites

| Tool        | Minimum | Why |
|-------------|---------|-----|
| Python      | 3.11    | Backend + edge node |
| Node.js     | 18      | Frontend (Vite) |
| Docker      | 24      | Backend (easiest path) |

> Don't have Docker? You can run the backend locally too (see below).

---

## 1. Backend Setup

The backend runs on port **8000**. It needs Redis + MinIO, but has built-in fallbacks so you can skip them for local dev.

### Option A — Docker (recommended, one command)

```bash
cd backend
cp .env.example .env
docker compose up --build -d
```

That starts 4 services: Redis, MinIO, the FastAPI API, and the Celery worker.

Wait ~30 seconds, then verify:

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok",...}
```

### Option B — Local (no Docker)

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

When you see these two warnings in the log, it's **working correctly**:

- `MinIO unreachable ... falling back to FS storage`
- `Redis unavailable ... using in-memory job store`

The backend automatically falls back to local files and an in-memory store. No Redis or MinIO needed.

Verify:

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok",...}
```

> **Windows note:** if `pip install` or `python -m venv` fails with a sandbox/permission error, run the commands in your own PowerShell window (not inside an IDE terminal).

---

## 2. Frontend Setup

The frontend runs on port **5173** and automatically proxies API calls to `localhost:8000`.

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173** in your browser. You should see the Dashboard with a green **"API Healthy"** pill (if the backend is running).

To build for production:

```bash
npm run build
```

---

## 3. End-to-End Test (no hardware needed)

Once both backend and frontend are running, you can run a full mock scan.

> **Run this from the repo root** (`SnakeBot/`), **not** from inside `edge_node/`. Running from inside `edge_node/` gives `No module named edge_node`.

```bash
# From the repo root (SnakeBot/)
# (use backend's venv if you don't have a global Python with the deps)
backend\.venv\Scripts\python -m edge_node capture-and-upload --shots 20 --interval 0.1 --wait
```

This:
1. Generates 20 synthetic photos + masks + depth maps
2. Uploads them to the backend (3-phase protocol with SHA-256 checksums)
3. The backend runs a 9-stage reconstruction pipeline (mock mode → synthetic PLY/GLB)
4. Waits for the job to complete

When it finishes, a big banner prints the `job_id`. Copy it, then paste it into the **Dashboard → Job Lookup** field on the frontend. You'll see the 9-stage progress timeline, then **Open 3D Viewer** to see the rendered mesh.

---

## 4. Edge Node (real hardware — Raspberry Pi 5)

This section connects a real Raspberry Pi 5 (with Camera Module v3 + Hailo-8L) to your backend.

### Step 1 — Find your backend's LAN IP

The Pi cannot reach `localhost` (that points to the Pi itself). You need your Windows/Mac machine's LAN IP.

**Windows PowerShell:**
```powershell
ipconfig | Select-String "IPv4"
```
**macOS/Linux:**
```bash
ipconfig getifaddr en0   # or: hostname -I
```
You'll get something like `192.168.1.50`. **Write this down.**

### Step 2 — Start the backend so the Pi can reach it

The backend must bind to `0.0.0.0` (not `127.0.0.1`) so other devices on the network can connect:

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Step 3 — Set up the Pi

SSH into the Pi (Raspberry Pi OS Bookworm 64-bit):

```bash
sudo apt install -y python3-picamera2 python3-libcamera python3-pip python3-venv
sudo apt install -y hailort hailort-py   # Hailo-8L SDK

cd ~/SnakeBot/edge_node
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Step 4 — Point the Pi at your backend

Edit `edge_node/config/config.yaml` on the Pi and set `backend_url` to your machine's LAN IP from Step 1:

```yaml
upload:
  backend_url: "http://192.168.1.50:8000"   # ← your LAN IP here
```

### Step 5 — Run a real scan

```bash
cd ~/SnakeBot
python -m edge_node health-check            # verifies camera + Hailo + backend reachability
python -m edge_node capture-and-upload --shots 20 --interval 1.5 --wait
```

The big banner prints the `job_id`. Paste it into **http://localhost:5173** → Job Lookup → Open 3D Viewer.

> You also need Hailo `.hef` model files in `edge_node/models/` (download `yolov8n-seg.hef` + `dpt_lite.hef` from the [Hailo Model Zoo](https://github.com/hailo-ai/hailo_model_zoo)). Without them, the AI inference falls back to mock masks/depth.

---

## Default URLs & Credentials

| Service    | URL | Credentials |
|------------|-----|-------------|
| Frontend   | http://localhost:5173 | — |
| Backend API | http://localhost:8000 | — |
| API Docs   | http://localhost:8000/docs | — |
| MinIO (Docker only) | http://localhost:9001 | minioadmin / minioadmin |
| Redis (Docker only) | localhost:6379 | — |

---

## How It Works (30-second version)

1. **Edge node** captures RGB photos, runs Hailo-8L segmentation + depth, builds a manifest.
2. **Upload** is 3-phase: `POST /bundles/init` → `PUT` each shot's 5 files → `POST /bundles/finalize` (with SHA-256 manifest check).
3. **Backend** queues a 9-stage Celery job: download → background-strip → COLMAP sparse → COLMAP dense → OpenMVS fusion → mesh post-process → export PLY → export GLB → upload assets.
4. **Frontend** polls `GET /jobs/{id}` every 2s, shows a progress timeline, and streams the GLB/PLY into a WebGL viewer (OrbitControls, auto-rotate, format toggle).

---

## What's Missing / Next Steps

For a full production scan (not mock), you still need:

- **COLMAP + OpenMVS binaries** installed on the backend host (or use the CUDA Dockerfile). Without them, the worker outputs a synthetic mesh.
- **Hailo `.hef` model files** (`yolov8n-seg.hef`, `dpt_lite.hef`) in `edge_node/models/` for real AI inference.
- **Raspberry Pi 5 + Camera Module v3 + Hailo-8L HAT** for real captures.

For software completeness, everything else is in place and verified: backend runs, frontend builds, types match across layers, and the mock pipeline runs end-to-end.
