import React, { useEffect, useRef, useState } from "react";
import { getLiveFrame, getLiveFrameImageUrl } from "../api/live";
import type { LiveFrameResponse } from "../types";

const POLL_MS = 1000;

export const LiveStream: React.FC = () => {
  const [frame, setFrame] = useState<LiveFrameResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imageTs, setImageTs] = useState<number>(0);
  const [connected, setConnected] = useState<boolean>(false);
  const imgRef = useRef<HTMLImageElement>(null);
  const frameCountRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      try {
        const data = await getLiveFrame();
        if (cancelled) return;
        setFrame(data);
        setError(null);
        setConnected(true);
        setImageTs(Date.now());
        frameCountRef.current += 1;
      } catch (err: any) {
        if (cancelled) return;
        const msg =
          err?.response?.status === 404
            ? "Waiting for edge node to start streaming..."
            : err?.message ?? "Connection error";
        setError(msg);
        setConnected(false);
      } finally {
        if (!cancelled) {
          timer = setTimeout(poll, POLL_MS);
        }
      }
    };

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  const detections = frame?.detections ?? [];

  return (
    <div className="dashboard">
      <div className="page-header">
        <div>
          <h1 className="page-title">Live Feed</h1>
          <p className="page-subtitle">
            Real-time artifact detection from the edge node (digital twin).
          </p>
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.5rem",
            padding: "0.5rem 1rem",
            borderRadius: "0.5rem",
            backgroundColor: connected ? "#ecfdf5" : "#fef2f2",
            border: `1px solid ${connected ? "#10b98144" : "#ef444444"}`,
          }}
        >
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              backgroundColor: connected ? "#10b981" : "#ef4444",
              animation: connected ? "pulse-dot 1.5s infinite" : "none",
            }}
          />
          <span
            style={{
              fontSize: "0.875rem",
              fontWeight: 600,
              color: connected ? "#065f46" : "#991b1b",
            }}
          >
            {connected ? "LIVE" : "DISCONNECTED"}
          </span>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h2 className="card-title">Camera Feed</h2>
          {frame && (
            <span style={{ fontSize: "0.8rem", color: "#64748b" }}>
              {frame.width}×{frame.height} · {new Date(frame.timestamp).toLocaleTimeString()}
            </span>
          )}
        </div>
        <div className="card-body">
          {error && !connected ? (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                aspectRatio: "16/9",
                backgroundColor: "#0f172a",
                borderRadius: "0.5rem",
                color: "#94a3b8",
                flexDirection: "column",
                gap: "0.5rem",
              }}
            >
              <div style={{ fontSize: "2rem" }}>📡</div>
              <div>{error}</div>
              <div style={{ fontSize: "0.8rem", color: "#64748b" }}>
                Run <code>python -m edge_node live-stream</code> on the edge node
              </div>
            </div>
          ) : (
            <div
              style={{
                position: "relative",
                width: "100%",
                aspectRatio: "16/9",
                backgroundColor: "#0f172a",
                borderRadius: "0.5rem",
                overflow: "hidden",
              }}
            >
              {frame && (
                <img
                  ref={imgRef}
                  src={getLiveFrameImageUrl(String(imageTs))}
                  alt="Live camera feed"
                  style={{ width: "100%", height: "100%", objectFit: "contain" }}
                />
              )}
              {/* Detection overlays */}
              {detections.map((det, i) => {
                const [x, y, w, h] = det.bbox;
                return (
                  <div
                    key={i}
                    style={{
                      position: "absolute",
                      left: `${x * 100}%`,
                      top: `${y * 100}%`,
                      width: `${w * 100}%`,
                      height: `${h * 100}%`,
                      border: "2px solid #10b981",
                      borderRadius: "4px",
                      boxShadow: "0 0 0 1px rgba(16,185,129,0.3)",
                      pointerEvents: "none",
                    }}
                  >
                    <span
                      style={{
                        position: "absolute",
                        top: "-1.4rem",
                        left: 0,
                        backgroundColor: "#10b981",
                        color: "white",
                        fontSize: "0.7rem",
                        padding: "2px 6px",
                        borderRadius: "3px",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {det.label} {(det.confidence * 100).toFixed(0)}%
                    </span>
                  </div>
                );
              })}
              {/* "LIVE" badge */}
              <div
                style={{
                  position: "absolute",
                  top: "0.75rem",
                  left: "0.75rem",
                  backgroundColor: "#ef4444",
                  color: "white",
                  fontSize: "0.7rem",
                  fontWeight: 700,
                  padding: "3px 8px",
                  borderRadius: "3px",
                  letterSpacing: "0.05em",
                }}
              >
                ● LIVE
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Stats + detections */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem", marginTop: "1rem" }}>
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">Depth Analysis</h2>
          </div>
          <div className="card-body">
            {frame ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                <StatRow label="Min depth" value={frame.depth_min.toFixed(3)} />
                <StatRow label="Max depth" value={frame.depth_max.toFixed(3)} />
                <StatRow label="Mean depth" value={frame.depth_mean.toFixed(3)} />
                <div
                  style={{
                    height: "24px",
                    borderRadius: "4px",
                    background: "linear-gradient(90deg, #1e40af 0%, #06b6d4 50%, #f59e0b 100%)",
                    marginTop: "0.5rem",
                  }}
                />
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.7rem", color: "#64748b" }}>
                  <span>shallow</span>
                  <span>deep</span>
                </div>
              </div>
            ) : (
              <p style={{ color: "#94a3b8" }}>No depth data yet.</p>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <h2 className="card-title">Detections</h2>
            <span style={{ fontSize: "0.8rem", color: "#64748b" }}>{detections.length} found</span>
          </div>
          <div className="card-body">
            {detections.length === 0 ? (
              <p style={{ color: "#94a3b8" }}>No artifacts detected in this frame.</p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                {detections.map((det, i) => (
                  <div
                    key={i}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      padding: "0.5rem 0.75rem",
                      backgroundColor: "#f8fafc",
                      borderRadius: "6px",
                      border: "1px solid #e2e8f0",
                    }}
                  >
                    <span style={{ fontWeight: 600 }}>{det.label}</span>
                    <span
                      style={{
                        color: det.confidence > 0.7 ? "#059669" : "#d97706",
                        fontWeight: 600,
                      }}
                    >
                      {(det.confidence * 100).toFixed(1)}%
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

const StatRow: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div style={{ display: "flex", justifyContent: "space-between" }}>
    <span style={{ color: "#64748b" }}>{label}</span>
    <span style={{ fontWeight: 600, fontFamily: "monospace" }}>{value}</span>
  </div>
);
