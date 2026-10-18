import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { StatusBadge } from "../components/StatusBadge";
import { getHealth } from "../api/assets";
import { getJob } from "../api/jobs";
import type { HealthResponse, JobResponse, JobStatus } from "../types";
import { JOB_STATUS_LABELS, JOB_STATUS_COLORS } from "../types";

interface JobSummary {
  jobId: string;
  status: JobStatus;
  created_at: string;
  num_shots: number;
  progress_percent: number;
}

const MOCK_JOBS: JobSummary[] = [];

export const Dashboard: React.FC = () => {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [recentJobs, setRecentJobs] = useState<JobResponse[]>([]);
  const [jobIdInput, setJobIdInput] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    (async () => {
      try {
        const h = await getHealth();
        setHealth(h);
      } catch (err: any) {
        setHealthError(err?.message ?? "Cannot reach backend");
      }
    })();
  }, []);

  useEffect(() => {
    const stored = localStorage.getItem("excavatable_seen_jobs");
    const seenIds: string[] = stored ? JSON.parse(stored) : [];
    (async () => {
      const jobs: JobResponse[] = [];
      for (const id of seenIds.slice(-10).reverse()) {
        try {
          const job = await getJob(id);
          jobs.push(job);
        } catch {
          /* skip stale */
        }
      }
      setRecentJobs(jobs);
    })();
  }, []);

  const addSeenJob = (jobId: string) => {
    const stored = localStorage.getItem("excavatable_seen_jobs");
    const seenIds: string[] = stored ? JSON.parse(stored) : [];
    if (!seenIds.includes(jobId)) {
      seenIds.push(jobId);
      localStorage.setItem("excavatable_seen_jobs", JSON.stringify(seenIds));
    }
  };

  const handleLookupJob = (e: React.FormEvent) => {
    e.preventDefault();
    const id = jobIdInput.trim();
    if (!id) return;
    addSeenJob(id);
    navigate(`/jobs/${id}`);
  };

  const statusCounts = React.useMemo(() => {
    const counts: Record<JobStatus, number> = {
      pending: 0,
      queued: 0,
      running: 0,
      uploading_assets: 0,
      completed: 0,
      failed: 0,
      cancelled: 0,
    };
    recentJobs.forEach((j) => {
      counts[j.status] = (counts[j.status] ?? 0) + 1;
    });
    return counts;
  }, [recentJobs]);

  return (
    <div className="dashboard">
      <div className="page-header">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <p className="page-subtitle">
            Monitor 3D scan reconstruction jobs and assets.
          </p>
        </div>
        <div
          className="health-indicator"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.5rem",
            padding: "0.5rem 1rem",
            borderRadius: "0.5rem",
            backgroundColor:
              health?.status === "ok" ? "#ecfdf5" : healthError ? "#fef2f2" : "#fef3c7",
            border: `1px solid ${
              health?.status === "ok"
                ? "#10b98144"
                : healthError
                ? "#ef444444"
                : "#f59e0b44"
            }`,
          }}
        >
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              backgroundColor:
                health?.status === "ok" ? "#10b981" : healthError ? "#ef4444" : "#f59e0b",
            }}
          />
          <span
            style={{
              fontSize: "0.875rem",
              fontWeight: 600,
              color:
                health?.status === "ok" ? "#065f46" : healthError ? "#991b1b" : "#92400e",
            }}
          >
            {health?.status === "ok"
              ? "Backend Connected"
              : healthError
              ? `Backend Offline: ${healthError}`
              : "Checking Backend..."}
          </span>
        </div>
      </div>

      <div className="stats-grid">
        {(Object.keys(statusCounts) as JobStatus[]).map((status) => (
          <div
            key={status}
            className="stat-card"
            style={{
              borderTop: `3px solid ${JOB_STATUS_COLORS[status]}`,
            }}
          >
            <div className="stat-label" style={{ color: JOB_STATUS_COLORS[status] }}>
              {JOB_STATUS_LABELS[status]}
            </div>
            <div className="stat-value">{statusCounts[status]}</div>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="card-header">
          <h2 className="card-title">Look Up a Job</h2>
        </div>
        <div className="card-body">
          <form onSubmit={handleLookupJob} className="lookup-form">
            <input
              type="text"
              placeholder="Paste job_id (e.g. job_xxx-xxx-xxx)"
              value={jobIdInput}
              onChange={(e) => setJobIdInput(e.target.value)}
              className="input"
              style={{ flex: 1 }}
            />
            <button type="submit" className="btn btn-primary">
              View Job
            </button>
          </form>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h2 className="card-title">Recent Jobs</h2>
          {recentJobs.length > 0 && (
            <button
              className="btn btn-ghost"
              onClick={() => {
                localStorage.removeItem("excavatable_seen_jobs");
                setRecentJobs([]);
              }}
            >
              Clear History
            </button>
          )}
        </div>
        <div className="card-body">
          {recentJobs.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">📷</div>
              <h3 className="empty-title">No jobs tracked yet</h3>
              <p className="empty-subtitle">
                Look up a Job ID above, or run <code>capture-and-upload</code>{" "}
                from the edge node to create one.
              </p>
              <Link to="/viewer/demo?format=demo" className="btn btn-primary">
                Open Demo Map
              </Link>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Job ID</th>
                    <th>Status</th>
                    <th>Shots</th>
                    <th>Progress</th>
                    <th>Created</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {recentJobs.map((job) => (
                    <tr key={job.job_id}>
                      <td>
                        <code className="mono-small">{job.job_id.slice(0, 24)}…</code>
                      </td>
                      <td>
                        <StatusBadge status={job.status} size="sm" />
                      </td>
                      <td>{job.num_shots}</td>
                      <td style={{ width: 200 }}>
                        <div className="mini-progress">
                          <div
                            className="mini-progress-bar"
                            style={{
                              width: `${job.progress_percent}%`,
                              backgroundColor: JOB_STATUS_COLORS[job.status],
                            }}
                          />
                        </div>
                        <span className="mini-progress-label">
                          {job.progress_percent.toFixed(0)}%
                        </span>
                      </td>
                      <td>
                        <span className="mono-small">
                          {new Date(job.created_at).toLocaleString()}
                        </span>
                      </td>
                      <td>
                        <Link to={`/jobs/${job.job_id}`} className="link-btn">
                          Details →
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
