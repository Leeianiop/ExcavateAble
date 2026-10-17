import React, { useEffect, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { StatusBadge } from "../components/StatusBadge";
import { ProgressTimeline } from "../components/ProgressTimeline";
import { getJob, pollJob } from "../api/jobs";
import { getGlbUrl, getPlyUrl, formatBytes } from "../api/assets";
import type { JobResponse } from "../types";

export const JobDetail: React.FC = () => {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const [job, setJob] = useState<JobResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPolling, setIsPolling] = useState(false);

  const fetchJob = async () => {
    if (!jobId) return;
    try {
      setError(null);
      const data = await getJob(jobId);
      setJob(data);
      const stored = localStorage.getItem("excavatable_seen_jobs");
      const seenIds: string[] = stored ? JSON.parse(stored) : [];
      if (!seenIds.includes(jobId)) {
        seenIds.push(jobId);
        localStorage.setItem("excavatable_seen_jobs", JSON.stringify(seenIds));
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? err?.message ?? "Failed to load job");
    }
  };

  useEffect(() => {
    fetchJob();
  }, [jobId]);

  useEffect(() => {
    if (
      !job ||
      job.status === "completed" ||
      job.status === "failed" ||
      job.status === "cancelled"
    ) {
      setIsPolling(false);
      return;
    }
    if (isPolling) return;
    setIsPolling(true);

    pollJob(
      job.job_id,
      (updated) => setJob(updated),
      (j) =>
        j.status === "completed" ||
        j.status === "failed" ||
        j.status === "cancelled"
    );
  }, [job, isPolling]);

  if (error && !job) {
    return (
      <div className="job-detail">
        <div className="card error-card">
          <h2>Error loading job</h2>
          <p style={{ color: "#ef4444" }}>{error}</p>
          <Link to="/" className="btn btn-primary">
            ← Back to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="job-detail">
        <div className="card skeleton">
          <div className="skeleton-line w-1/3" />
          <div className="skeleton-line w-2/3" />
          <div className="skeleton-line w-1/2" />
        </div>
      </div>
    );
  }

  const hasAssets = !!job.glb_url || !!job.ply_url;

  return (
    <div className="job-detail">
      <div className="page-header">
        <div>
          <Link to="/" className="back-link">
            ← Back to Dashboard
          </Link>
          <h1 className="page-title">Job Details</h1>
          <p className="page-subtitle">
            Bundle: <code className="mono">{job.bundle_id}</code>
          </p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "0.5rem" }}>
          <StatusBadge status={job.status} size="lg" />
          {isPolling && (
            <span style={{ fontSize: "0.75rem", color: "#64748b" }}>
              Live polling...
            </span>
          )}
        </div>
      </div>

      <div className="two-col-grid">
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">Reconstruction Progress</h2>
          </div>
          <div className="card-body">
            <ProgressTimeline
              progress={job.progress}
              currentStage={job.current_stage}
              overallPercent={job.progress_percent}
            />
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div className="card">
            <div className="card-header">
              <h2 className="card-title">Job Info</h2>
            </div>
            <div className="card-body">
              <dl className="info-list">
                <div>
                  <dt>Job ID</dt>
                  <dd><code className="mono-small">{job.job_id}</code></dd>
                </div>
                <div>
                  <dt>Shots</dt>
                  <dd>{job.num_shots} photos</dd>
                </div>
                <div>
                  <dt>Created</dt>
                  <dd>{new Date(job.created_at).toLocaleString()}</dd>
                </div>
                {job.started_at && (
                  <div>
                    <dt>Started</dt>
                    <dd>{new Date(job.started_at).toLocaleString()}</dd>
                  </div>
                )}
                {job.finished_at && (
                  <div>
                    <dt>Finished</dt>
                    <dd>{new Date(job.finished_at).toLocaleString()}</dd>
                  </div>
                )}
              </dl>
            </div>
          </div>

          {job.status === "completed" && (
            <div className="card success-card">
              <div className="card-header">
                <h2 className="card-title">Scan Outputs</h2>
              </div>
              <div className="card-body">
                <div className="mesh-stats">
                  <div>
                    <span className="stat-label">Vertices</span>
                    <span className="stat-value-lg">{job.num_vertices.toLocaleString()}</span>
                  </div>
                  <div>
                    <span className="stat-label">Faces</span>
                    <span className="stat-value-lg">{job.num_faces.toLocaleString()}</span>
                  </div>
                </div>

                <div className="asset-buttons">
                  {job.glb_url && (
                    <>
                      <button
                        className="btn btn-primary"
                        onClick={() => navigate(`/viewer/${job.job_id}?format=glb`)}
                      >
                        🎯 Open 3D Viewer
                      </button>
                      <a
                        href={getGlbUrl(job.job_id)}
                        className="btn btn-outline"
                        download
                        target="_blank"
                        rel="noreferrer"
                      >
                        ⬇ GLB ({formatBytes(job.glb_size_bytes)})
                      </a>
                    </>
                  )}
                  {job.ply_url && (
                    <a
                      href={getPlyUrl(job.job_id)}
                      className="btn btn-outline"
                      download
                      target="_blank"
                      rel="noreferrer"
                    >
                      ⬇ PLY ({formatBytes(job.ply_size_bytes)})
                    </a>
                  )}
                </div>
              </div>
            </div>
          )}

          {job.status === "failed" && job.error_message && (
            <div className="card error-card">
              <div className="card-header">
                <h2 className="card-title" style={{ color: "#ef4444" }}>
                  ⚠️ Error
                </h2>
              </div>
              <div className="card-body">
                <pre className="error-pre">{job.error_message}</pre>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
