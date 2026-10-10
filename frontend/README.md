# SnakeBot Frontend

The browser-based web UI for the **SnakeBot 3D Scanning System**. Built with React + Vite + TypeScript and React Three Fiber for in-browser WebGL rendering of reconstructed 3D assets.

---

## Stack

| Layer       | Technology |
|-------------|------------|
| Build       | [Vite 5](https://vitejs.dev/) + TypeScript 5 (strict) |
| UI          | React 18 + React Router 6 |
| 3D Rendering | [@react-three/fiber](https://r3f.docs.pmnd.rs/) (R3F) + [@react-three/drei](https://drei.pmnd.rs/) + three.js 0.169 |
| HTTP Client | axios 1.7 |
| Styling     | Pure CSS with design tokens (no Tailwind / CSS-in-JS) |

---

## Quick Start

### Prerequisites

- Node.js **≥ 18**
- npm (comes with Node.js)
- Backend API running on `http://localhost:8000` (FastAPI + Redis + MinIO) — see the project root README for how to start the backend stack.

### Install

```bash
cd frontend
npm install
```

### Run the Dev Server

```bash
npm run dev
```

Open <http://localhost:5173>. The Vite dev server automatically proxies any `/api/*` request to `http://localhost:8000` so you never hit CORS issues during development.

### Production Build

```bash
npm run build
```

This type-checks (`tsc --noEmit`) then builds with Vite. Output goes to `dist/`.

### Preview the Production Build

```bash
npm run preview
```

Serves the `dist/` folder locally on port 4173.

---

## Environment Variables

Copy [.env.example](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/frontend/.env.example) to `.env.local` (git-ignored) and tweak if needed:

```bash
# Base URL of the SnakeBot backend API (without the /api/v1 suffix).
# Dev default is already proxied via Vite, so you usually don't need to set this.
VITE_API_BASE_URL=http://localhost:8000
```

All `import.meta.env.VITE_*` variables are typed in [vite-env.d.ts](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/frontend/src/vite-env.d.ts).

---

## Project Structure

```
frontend/
├── index.html                      # Vite entry HTML
├── package.json
├── tsconfig.json                   # TS strict mode, JSX react-jsx, ES2020
├── vite.config.ts                  # Vite dev proxy + React plugin
├── .env.example
└── src/
    ├── main.tsx                    # React root, StrictMode
    ├── App.tsx                     # BrowserRouter + route table
    ├── index.css                   # ~600 lines: design tokens + all component styles
    ├── vite-env.d.ts
    ├── types/
    │   └── index.ts                # 1:1 TS interfaces for backend schemas + label/color maps
    ├── api/
    │   ├── client.ts               # axios instance, baseURL, error interceptor
    │   ├── jobs.ts                 # getJob, pollJob, initBundle, finalizeBundle
    │   └── assets.ts               # listAssets, getHealth, getPlyUrl, getGlbUrl
    ├── components/
    │   ├── Layout.tsx              # App shell: sticky nav + max-width content
    │   ├── Navbar.tsx              # Gradient logo + nav links (NavLink)
    │   ├── StatusBadge.tsx         # Animated badge for JobStatus (pulse when running)
    │   ├── ProgressTimeline.tsx    # 9-stage vertical pipeline tracker
    │   └── Viewer3D.tsx            # R3F Canvas: GLB/PLY loader, OrbitControls, camera rig
    └── pages/
        ├── Dashboard.tsx           # `/` — health, stats, job lookup, recent jobs
        ├── JobDetail.tsx           # `/jobs/:jobId` — live polling, stages, downloads
        ├── AssetLibrary.tsx        # `/assets` — responsive card grid of finished scans
        └── Viewer.tsx              # `/viewer/:jobId` — dark WebGL viewer shell
```

---

## Pages & Routes

| Route              | Page             | What it does |
|--------------------|------------------|--------------|
| `/`                | Dashboard        | Backend health indicator, 7 status stat cards, Job ID lookup form, recent-jobs table (persisted in `localStorage`). |
| `/jobs/:jobId`     | Job Detail       | Auto-polls the job every 2s until terminal. 9-stage vertical timeline with per-stage percent and message. On completion shows vertex/face counts + GLB/PLY download CTAs + Viewer deep-link. On failure shows the error stack. |
| `/assets`          | Asset Library    | Merges backend `GET /assets` with locally-seen job IDs into a responsive card grid. Each card has a CSS animated 3D cube thumbnail, size/format chips, and links to Job Detail / Viewer. |
| `/viewer/:jobId`   | 3D Viewer        | Dark full-screen WebGL shell. Segmented control to switch GLB ↔ PLY. Toggle toolbar: Auto-Rotate, Grid, Wireframe, Axes. Download button. Auto-frames the camera on the mesh bounding box so any scan size works. |
| `*`                | 404              | Friendly not-found page with link back to Dashboard. |

---

## Type Contract (Backend ↔ Frontend)

All TypeScript types live in [types/index.ts](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/frontend/src/types/index.ts) and mirror the backend Pydantic models 1:1. Highlights:

### Enums

- **`JobStatus`** (7): `pending` · `queued` · `running` · `uploading_assets` · `completed` · `failed` · `cancelled`
- **`JobStage`** (9, in pipeline order): `download_bundle` → `background_strip` → `colmap_sparse` → `colmap_dense` → `openmvs_fusion` → `mesh_postprocess` → `export_ply` → `export_glb` → `upload_assets`

### Lookup Maps

Used across every page — never hard-code strings:

```ts
JOB_STAGE_ORDER[]    // ordered pipeline array
JOB_STAGE_LABELS{}   // human-readable stage titles
JOB_STATUS_LABELS{}  // human-readable status titles
JOB_STATUS_COLORS{}  // hex colors per status
```

### Main Models

- `JobResponse` — the full job payload (`job_id`, `status`, `progress[]`, `progress_percent`, `glb_url`, `ply_url`, `num_vertices`, `num_faces`, etc.)
- `JobProgress` — one stage's entry (stage, percent, message, timestamps)
- `AssetInfo` — one asset row from `/assets`
- `HealthResponse` — `/health` (status, object_store, job_store)

---

## API Layer

Located in [src/api/](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/frontend/src/api/).

### Base Client ([client.ts](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/frontend/src/api/client.ts))

```ts
api = axios.create({
  baseURL: `${VITE_API_BASE_URL ?? ""}/api/v1`,
  timeout: 30_000,
});
```

Error interceptor logs + re-throws so callers can `try/catch` cleanly.

### Job API ([jobs.ts](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/frontend/src/api/jobs.ts))

| Function | Signature | Notes |
|----------|-----------|-------|
| `getJob` | `(jobId: string) => Promise<JobResponse>` | `GET /jobs/{jobId}` |
| `pollJob` | `(jobId, onUpdate, shouldStop, intervalMs = 2000) => Promise<void>` | Self-recursing `setTimeout` poller. Doubles interval on axios errors. Stops when `shouldStop(job)` returns true. |
| `initBundle` | `(req: BundleInitRequest) => Promise<BundleInitResponse>` | `POST /bundles/init` |
| `finalizeBundle` | `(bid: string) => Promise<BundleFinalizeResponse>` | `POST /bundles/{bid}/finalize` |

### Asset API ([assets.ts](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/frontend/src/api/assets.ts))

| Function | What |
|----------|------|
| `getHealth()` | `GET /health` |
| `listAssets(jobId?)` | `GET /assets?job_id=...` |
| `getPlyUrl(jobId)` | Resolves the signed URL (follows 302 from `/assets/{id}/ply`) |
| `getGlbUrl(jobId)` | Same for GLB |
| `formatBytes(n)` | Humanizes byte counts (kB/MB/GB) |

---

## 3D Viewer Deep Dive

File: [components/Viewer3D.tsx](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/frontend/src/components/Viewer3D.tsx)

### Loader

Rather than using drei's Suspense-based `useGLTF` (which only handles glTF), the viewer uses a **unified custom loader** inside a `useEffect` with three.js `LoadingManager`:

- **GLB** → `GLTFLoader` — walks the scene, finds every `Mesh`, applies `MeshStandardMaterial`
- **PLY** → `PLYLoader` — builds a `BufferGeometry` and wraps it in a Mesh with `vertexColors` enabled

Loaded meshes cast/receive shadows, get `flatShading` (toggleable), and dispatch an `onBoundingBox(Box3)` callback.

### Camera Rig

A separate `CameraRig` component reads the computed `Box3` and:
1. Calculates `maxDim = max(size.x, size.y, size.z)`
2. Positions camera at distance `maxDim / (2 * tan(fov/2)) * 1.6` (fits the object with breathing room)
3. Updates `OrbitControls.target` to the box center
4. Scales `near`/`far` planes proportionally so tiny mock meshes and huge real scans both avoid z-fighting

This is why the viewer can jump between a small synthetic test cylinder and a full 20-shot photogrammetry scan without manual zoom.

### Scene Setup (drei primitives used)

- `<Canvas shadows dpr={[1, 2]} />` with automatic pixel-ratio clamping
- `<OrbitControls enableDamping autoRotate />` (togglable from parent)
- `<Center>` — recenters the mesh regardless of how it was exported
- `<Environment preset="city" />` — realistic HDR PBR lighting
- `<ContactShadows />` — soft ground shadow without bake
- `<Grid infiniteGrid fadeDistance={...} />` — togglable infinite ground grid
- `<axesHelper />` — togglable world axes
- `useProgress()` overlay — shimmering loader screen while assets stream in

### Parent Shell

[pages/Viewer.tsx](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/frontend/src/pages/Viewer.tsx) wraps the Canvas in a dark full-height shell with:
- Sticky top bar: segmented GLB/PLY switch + toggles + download button
- `?format=glb|ply` query param support (defaults to GLB)
- Placeholder screen when job isn't `completed` yet

---

## Live Polling Pattern

Used in [JobDetail.tsx](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/frontend/src/pages/JobDetail.tsx):

```tsx
useEffect(() => {
  if (!job || ["completed", "failed", "cancelled"].includes(job.status)) {
    setIsPolling(false); return;
  }
  if (isPolling) return;          // guard: one poll cycle at a time
  setIsPolling(true);
  pollJob(job.job_id, setJob, (j) =>
    ["completed", "failed", "cancelled"].includes(j.status));
}, [job, isPolling]);
```

Why this pattern:
- Guards against double-mount under StrictMode
- Cleanly cancels by reaching terminal status (the recursive timeout isn't scheduled again)
- Exponential backoff on axios errors (doubles to 4s, 8s…) so a flaky backend won't hammer the API

---

## Client-Side Job History

The backend intentionally omits a `GET /api/v1/jobs` listing endpoint. Instead, the frontend uses `localStorage["snakebot_seen_jobs"]` as a **client-side registry of job IDs the user has visited or looked up**.

- Any call that loads a job writes its id to the set
- Dashboard's "Recent Jobs" table iterates this set (newest first) and re-fetches each one
- Asset Library first seeds its grid from this set, then overlays whatever `GET /assets` returns globally
- Dashboard has a "Clear History" button to wipe the registry

This approach keeps the backend minimal while still giving users a "my recent scans" experience.

---

## Styling

Single-file custom CSS at [src/index.css](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/frontend/src/index.css).

### Design Tokens (CSS Variables)

```css
:root {
  --color-primary:   #2563eb;   /* blue-600 */
  --color-accent:    #6366f1;   /* indigo-500 */
  --color-success:   #10b981;   /* emerald-500 */
  --color-warning:   #f59e0b;   /* amber-500 */
  --color-danger:    #ef4444;   /* red-500 */
  --color-slate-50…900: ...;    /* full neutral ramp */
  --radius: 12px;
  --radius-sm: 8px;
  --shadow-card: 0 1px 3px rgba(0,0,0,.06), 0 4px 12px rgba(0,0,0,.04);
  --nav-height: 64px;
}
```

### Key Utility Classes

| Class | Purpose |
|-------|---------|
| `.navbar` | Sticky top bar with `backdrop-filter: blur()` glass effect |
| `.card` | Rounded shadowed container with hover lift |
| `.btn` / `.btn-primary` / `.btn-ghost` | Button variants |
| `.segmented` / `.seg-btn` | GLB/PLY switcher group (also used for toggles) |
| `.two-col-grid` | Job detail layout — collapses to 1 col @ ≤ 900px |
| `.card-grid` | Asset library — `grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))` |
| `.timeline` | 9-stage progress nodes with connectors |
| `.thumb-cube` | Pure CSS 3D animated cube placeholder for asset cards (`transform-style: preserve-3d` + `cube-float` keyframe) |
| `.skeleton` | Shimmer loading state (gradient sweep keyframe) |
| `.viewer-shell` / `.viewer-toolbar` | Dark viewer layout |

### Responsive Breakpoints

- `@media (max-width: 900px)` — two-col layouts collapse, padding shrinks
- `@media (max-width: 640px)` — font sizes step down, buttons stack, timeline stage cards compact

---

## Build Verification Checklist

```bash
cd frontend

# 1. Type-check only (fast)
npm run lint
#   → Expected: 0 errors, exit 0

# 2. Full production build (type-check + bundle)
npm run build
#   → Expected: 693 modules, ~1.2 MB JS (346 KB gzipped), exit 0

# 3. Preview prod bundle
npm run preview
```

All three commands pass on a clean checkout as of the initial commit.

---

## End-to-End Smoke Test

1. **Start backend** on port 8000 (Docker Compose or `uvicorn backend.app.main:app --reload --port 8000` with Redis + MinIO running).
2. **Start frontend** dev server: `cd frontend && npm run dev`.
3. Run the edge-node smoke test (see project root README): `capture-and-upload --shots 20 --wait` (or use the mock/synthetic path if no hardware is attached).
4. Copy the returned `job_id` from the edge output.
5. Paste it into the Dashboard **Job Lookup** form at <http://localhost:5173> → hit Enter.
6. You should land on `/jobs/<id>` and see the Progress Timeline advance through the 9 stages:
   - `download_bundle` → … → `export_glb` → `upload_assets`
7. When status turns **Completed**:
   - Vertex/face count cards appear
   - Click **Open 3D Viewer** → this jumps to `/viewer/<id>`
   - The viewer loads the GLB, auto-frames, and you can orbit / zoom / toggle wireframe / switch to PLY
8. Navigate to **Asset Library** (`/assets`) — the new scan appears as a card.

---

## Backend Endpoints Consumed

| Method | Path | Consumed by |
|--------|------|-------------|
| `GET`  | `/api/v1/health` | Dashboard hero |
| `POST` | `/api/v1/bundles/init` | jobs.ts (upload flow) |
| `PUT`  | `/api/v1/bundles/{bid}/shots/{sid}/{role}` | Edge node (not frontend) |
| `POST` | `/api/v1/bundles/{bid}/finalize` | jobs.ts (upload flow) |
| `GET`  | `/api/v1/jobs/{job_id}` | `getJob` + `pollJob` → JobDetail |
| `GET`  | `/api/v1/assets` | AssetLibrary |
| `GET`  | `/api/v1/assets/{job_id}/ply` | 302 → signed MinIO URL (Viewer) |
| `GET`  | `/api/v1/assets/{job_id}/glb` | 302 → signed MinIO URL (Viewer) |

Backend contract lives in:
- [backend/app/api/v1.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/app/api/v1.py)
- [backend/app/schemas.py](file:///C:/Users/seeru/OneDrive/Desktop/SnakeBot/backend/app/schemas.py)

---

## Known Caveats

1. **Bundle weight:** The main chunk is ~1.2 MB (346 KB gzipped) because three.js + R3F + drei + both loaders ship together. You can cut Dashboard first-load time by code-splitting the Viewer route with `React.lazy` + `<Suspense>` (no action taken here since build still passes Vite's soft warning).
2. **Thumbnails:** Asset cards use a CSS cube placeholder. Real thumbnails require the backend worker to render a 256×256 PNG at `export_glb` stage and expose it on `JobResponse.thumbnail_url`.
3. **Upload form:** The frontend currently reads jobs but does not yet expose the `init → PUT shots → finalize` flow as a drag-and-drop form. That's a natural follow-up feature.
4. **Dev Proxy Only:** The Vite `/api` proxy only works in `npm run dev`. For a real deployment, set `VITE_API_BASE_URL` to your production backend origin *before* building, or put both frontend and API behind the same reverse proxy.

---

## Scripts Summary

```json
{
  "dev":     "vite",                        // start dev server :5173
  "build":   "tsc && vite build",           // type-check + emit dist/
  "preview": "vite preview",                // serve dist/ :4173
  "lint":    "tsc --noEmit"                 // fast type-only check
}
```
