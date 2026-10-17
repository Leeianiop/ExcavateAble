import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { formatBytes, getGlbUrl, getPlyUrl, getHealth, listAssets } from "../api/assets";
import { getJob } from "../api/jobs";
import type { AssetInfo, JobResponse } from "../types";

interface AssetGroup {
  job_id: string;
  bundle_id: string;
  created_at: string;
  assets: { ply?: AssetInfo; glb?: AssetInfo };
  job?: JobResponse;
}

export const AssetLibrary: React.FC = () => {
  const [groups, setGroups] = useState<AssetGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        setError(null);

        await getHealth();

        const seen = localStorage.getItem("excavatable_seen_jobs");
        const seenIds: string[] = seen ? JSON.parse(seen) : [];

        const assetMap = new Map<string, AssetGroup>();

        for (const id of seenIds) {
          try {
            const [assets, job] = await Promise.all([
              listAssets(id).catch(() => [] as AssetInfo[]),
              getJob(id).catch(() => undefined as JobResponse | undefined),
            ]);

            const jobAssets: AssetGroup["assets"] = {};
            for (const a of assets) {
              if (a.format === "ply") jobAssets.ply = a;
              if (a.format === "glb") jobAssets.glb = a;
            }
            if (job && (job.status === "completed" || assets.length > 0)) {
              assetMap.set(id, {
                job_id: id,
                bundle_id: job?.bundle_id ?? assets[0]?.bundle_id ?? id,
                created_at: job?.created_at ?? assets[0]?.created_at ?? new Date().toISOString(),
                assets: jobAssets,
                job,
              });
            }
          } catch {
            /* skip */
          }
        }

        const allAssets = await listAssets().catch(() => [] as AssetInfo[]);
        for (const a of allAssets) {
          if (!assetMap.has(a.job_id)) {
            assetMap.set(a.job_id, {
              job_id: a.job_id,
              bundle_id: a.bundle_id,
              created_at: a.created_at,
              assets: {},
            });
          }
          const g = assetMap.get(a.job_id)!;
          if (a.format === "ply") g.assets.ply = a;
          if (a.format === "glb") g.assets.glb = a;
        }

        const sorted = Array.from(assetMap.values()).sort(
          (a, b) => +new Date(b.created_at) - +new Date(a.created_at)
        );
        setGroups(sorted);
      } catch (err: any) {
        setError(err?.message ?? "Failed to load assets");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (error && groups.length === 0) {
    return (
      <div className="asset-library">
        <div className="page-header">
          <div>
            <h1 className="page-title">Asset Library</h1>
            <p className="page-subtitle">Browse all reconstructed 3D scans.</p>
          </div>
        </div>
        <div className="card error-card">
          <h2>Error loading library</h2>
          <p style={{ color: "#ef4444" }}>{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="asset-library">
      <div className="page-header">
        <div>
          <h1 className="page-title">Asset Library</h1>
          <p className="page-subtitle">
            {loading
              ? "Loading assets..."
              : `${groups.length} completed scan${groups.length === 1 ? "" : "s"} in library`}
          </p>
        </div>
      </div>

      {loading && groups.length === 0 ? (
        <div className="card-grid">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="card asset-card skeleton">
              <div className="skeleton-thumb" />
              <div className="skeleton-line w-2/3" />
              <div className="skeleton-line w-1/2" />
            </div>
          ))}
        </div>
      ) : groups.length === 0 ? (
        <div className="card">
          <div className="card-body">
            <div className="empty-state">
              <div className="empty-icon">🗂️</div>
              <h3 className="empty-title">No assets yet</h3>
              <p className="empty-subtitle">
                Run <code>capture-and-upload</code> from the edge node to produce
                a 3D scan. Completed jobs appear here automatically.
              </p>
            </div>
          </div>
        </div>
      ) : (
        <div className="card-grid">
          {groups.map((g) => {
            const hasGLB = !!g.assets.glb;
            const hasPLY = !!g.assets.ply;
            const size =
              (g.assets.glb?.size_bytes ?? 0) + (g.assets.ply?.size_bytes ?? 0);
            const vertices = g.job?.num_vertices ?? 0;
            const faces = g.job?.num_faces ?? 0;
            const shots = g.job?.num_shots ?? 0;

            return (
              <div key={g.job_id} className="card asset-card">
                <div
                  className="asset-thumb"
                  onClick={() =>
                    navigate(
                      `/viewer/${g.job_id}${hasGLB ? "?format=glb" : "?format=ply"}`
                    )
                  }
                >
                  <div className="thumb-placeholder">
                    <div className="thumb-cube">
                      <div className="cube-face cube-front" />
                      <div className="cube-face cube-right" />
                      <div className="cube-face cube-top" />
                    </div>
                  </div>
                  <div className="thumb-overlay">
                    <span className="thumb-overlay-text">Open Viewer →</span>
                  </div>
                </div>

                <div className="asset-meta">
                  <div className="asset-title-row">
                    <h3 className="asset-title">
                      {g.bundle_id?.length > 24
                        ? g.bundle_id.slice(0, 24) + "…"
                        : g.bundle_id || "Untitled Scan"}
                    </h3>
                  </div>
                  <div className="asset-sub">
                    <span>{new Date(g.created_at).toLocaleDateString()}</span>
                    <span>·</span>
                    <span>{formatBytes(size)}</span>
                  </div>

                  <div className="asset-stats">
                    {shots > 0 && <span>📷 {shots} shots</span>}
                    {vertices > 0 && <span>⬡ {vertices.toLocaleString()} vtx</span>}
                    {faces > 0 && <span>△ {faces.toLocaleString()} faces</span>}
                  </div>

                  <div className="asset-formats">
                    {hasGLB && (
                      <a
                        href={getGlbUrl(g.job_id)}
                        className="chip-btn"
                        download
                        onClick={(e) => e.stopPropagation()}
                        target="_blank"
                        rel="noreferrer"
                      >
                        GLB · {formatBytes(g.assets.glb!.size_bytes)}
                      </a>
                    )}
                    {hasPLY && (
                      <a
                        href={getPlyUrl(g.job_id)}
                        className="chip-btn chip-alt"
                        download
                        onClick={(e) => e.stopPropagation()}
                        target="_blank"
                        rel="noreferrer"
                      >
                        PLY · {formatBytes(g.assets.ply!.size_bytes)}
                      </a>
                    )}
                  </div>

                  <div className="asset-actions">
                    <Link to={`/jobs/${g.job_id}`} className="link-btn">
                      Job Details
                    </Link>
                    <Link
                      to={`/viewer/${g.job_id}${hasGLB ? "?format=glb" : "?format=ply"}`}
                      className="btn btn-primary btn-sm"
                    >
                      View 3D
                    </Link>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
