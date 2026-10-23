import React, { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Viewer3D } from "../components/Viewer3D";
import { StatusBadge } from "../components/StatusBadge";
import { getJob } from "../api/jobs";
import { getGlbUrl, getPlyUrl } from "../api/assets";
import type { JobResponse } from "../types";

const ARTIFACT_DEMO_PLY_URL = "/demo/artifact-demo.ply?v=horse-side-profile-2";
const ARTIFACT_VIEW_SRC = "demo:horse-head";

export const Viewer: React.FC = () => {
  const { jobId } = useParams<{ jobId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const isDemoViewer = jobId === "demo";
  const isArtifactViewer = jobId === "artifact-demo";
  const format = (searchParams.get("format") as "glb" | "ply" | "demo") ??
    (isDemoViewer ? "demo" : isArtifactViewer ? "ply" : "glb");

  const [job, setJob] = useState<JobResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoRotate, setAutoRotate] = useState(false);
  const [showGrid, setShowGrid] = useState(true);

  useEffect(() => {
    if (isDemoViewer || isArtifactViewer) return;
    if (!jobId) return;
    (async () => {
      try {
        const data = await getJob(jobId);
        setJob(data);
      } catch (err: any) {
        setError(err?.message ?? "Failed to load job");
      }
    })();
  }, [isDemoViewer, isArtifactViewer, jobId]);

  const switchFormat = (f: "glb" | "ply" | "demo") => {
    setSearchParams({ format: f });
  };

  if (isDemoViewer) {
    return (
      <div className="viewer-page">
        <div className="viewer-header">
          <div>
            <Link to="/assets" className="back-link">
              ← Back to Assets
            </Link>
            <div style={{ display: "flex", alignItems: "center", gap: "1rem", marginTop: "0.5rem" }}>
              <h1 className="viewer-title">Excavation Demo Map</h1>
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  padding: "0.25rem 0.6rem",
                  borderRadius: "999px",
                  background: "#ecfdf5",
                  color: "#047857",
                  fontSize: "0.8rem",
                  fontWeight: 700,
                }}
              >
                Demo
              </span>
            </div>
            <p style={{ margin: "0.25rem 0 0 0", color: "#64748b", fontSize: "0.875rem" }}>
              Built-in fallback scene based on your excavation layout photos.
            </p>
          </div>
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            <button
              className={`tool-btn ${autoRotate ? "active" : ""}`}
              onClick={() => setAutoRotate((v) => !v)}
            >
              ⟳ Auto-Rotate
            </button>
            <button
              className={`tool-btn ${showGrid ? "active" : ""}`}
              onClick={() => setShowGrid((v) => !v)}
            >
              ⊞ Grid
            </button>
          </div>
        </div>

        <div className="viewer-body">
          <Viewer3D
            src="demo:excavation-layout"
            format="demo"
            autoRotate={autoRotate}
            showGrid={showGrid}
          />
        </div>
      </div>
    );
  }

  if (isArtifactViewer) {
    return (
      <div className="viewer-page">
        <div className="viewer-header">
          <div>
            <Link to="/assets" className="back-link">
              ← Back to Assets
            </Link>
            <div style={{ display: "flex", alignItems: "center", gap: "1rem", marginTop: "0.5rem" }}>
              <h1 className="viewer-title">Horse Head Demo Scan</h1>
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  padding: "0.25rem 0.6rem",
                  borderRadius: "999px",
                  background: "#fef3c7",
                  color: "#92400e",
                  fontSize: "0.8rem",
                  fontWeight: 700,
                }}
              >
                PLY
              </span>
            </div>
            <p style={{ margin: "0.25rem 0 0 0", color: "#64748b", fontSize: "0.875rem" }}>
              Low-poly horse-head artifact based on your uploaded photo.
            </p>
          </div>
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            <button
              className={`tool-btn ${autoRotate ? "active" : ""}`}
              onClick={() => setAutoRotate((v) => !v)}
            >
              ⟳ Auto-Rotate
            </button>
            <button
              className={`tool-btn ${showGrid ? "active" : ""}`}
              onClick={() => setShowGrid((v) => !v)}
            >
              ⊞ Grid
            </button>
            <a
              href={ARTIFACT_DEMO_PLY_URL}
              className="btn btn-primary"
              download
              target="_blank"
              rel="noreferrer"
            >
              ⬇ Download PLY
            </a>
          </div>
        </div>

        <div className="viewer-body">
          <Viewer3D
            src={ARTIFACT_VIEW_SRC}
            format="ply"
            autoRotate={autoRotate}
            showGrid={showGrid}
          />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="viewer-page">
        <div className="card error-card">
          <h2>Error</h2>
          <p style={{ color: "#ef4444" }}>{error}</p>
          <Link to={jobId ? `/jobs/${jobId}` : "/"} className="btn btn-primary">
            ← Go Back
          </Link>
        </div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="viewer-page">
        <div className="card skeleton">
          <div className="skeleton-line w-1/2" />
          <div className="skeleton-line w-full" />
        </div>
      </div>
    );
  }

  const viewerFormat: "glb" | "ply" = format === "ply" ? "ply" : "glb";
  const assetUrl = viewerFormat === "ply" ? getPlyUrl(job.job_id) : getGlbUrl(job.job_id);
  const hasBoth = !!job.glb_url && !!job.ply_url;

  return (
    <div className="viewer-page">
      <div className="viewer-header">
        <div>
          <Link to={jobId ? `/jobs/${jobId}` : "/"} className="back-link">
            ← Back to Job
          </Link>
          <div style={{ display: "flex", alignItems: "center", gap: "1rem", marginTop: "0.5rem" }}>
            <h1 className="viewer-title">3D Viewer</h1>
            <StatusBadge status={job.status} size="sm" />
          </div>
          <p style={{ margin: "0.25rem 0 0 0", color: "#64748b", fontSize: "0.875rem" }}>
            Job: <code className="mono">{job.job_id.slice(0, 24)}…</code>
            {" · "}
            {job.num_shots} shots
            {job.num_vertices > 0 && ` · ${job.num_vertices.toLocaleString()} vertices`}
            {job.num_faces > 0 && ` · ${job.num_faces.toLocaleString()} faces`}
          </p>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          {hasBoth && (
            <div className="segmented-control" role="tablist">
              <button
                className={`seg-btn ${viewerFormat === "glb" ? "active" : ""}`}
                onClick={() => switchFormat("glb")}
                disabled={!job.glb_url}
              >
                GLB
              </button>
              <button
                className={`seg-btn ${viewerFormat === "ply" ? "active" : ""}`}
                onClick={() => switchFormat("ply")}
                disabled={!job.ply_url}
              >
                PLY
              </button>
            </div>
          )}
          <button
            className={`tool-btn ${autoRotate ? "active" : ""}`}
            onClick={() => setAutoRotate((v) => !v)}
          >
            ⟳ Auto-Rotate
          </button>
          <button
            className={`tool-btn ${showGrid ? "active" : ""}`}
            onClick={() => setShowGrid((v) => !v)}
          >
            ⊞ Grid
          </button>
          <a
            href={assetUrl}
            className="btn btn-primary"
            download
            target="_blank"
            rel="noreferrer"
          >
            ⬇ Download {format.toUpperCase()}
          </a>
        </div>
      </div>

      <div className="viewer-body">
        {job.status !== "completed" || !job.glb_url || !job.ply_url ? (
          <div className="viewer-placeholder">
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: "3rem", marginBottom: "1rem" }}>⏳</div>
              <h2>Assets not ready yet</h2>
              <p style={{ color: "#64748b", maxWidth: 500 }}>
                This job is currently{" "}
                <strong style={{ color: "#1e293b" }}>{job.status}</strong>.
                3D assets will appear here once reconstruction completes.
                Check the Job Detail page for live progress.
              </p>
              <Link to={`/jobs/${job.job_id}`} className="btn btn-primary" style={{ marginTop: "1.5rem" }}>
                View Job Details
              </Link>
            </div>
          </div>
        ) : (
          <Viewer3D
            src={assetUrl}
            format={viewerFormat}
            autoRotate={autoRotate}
            showGrid={showGrid}
          />
        )}
      </div>
    </div>
  );
};
