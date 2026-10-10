import React from "react";
import {
  JobProgress,
  JobStage,
  JOB_STAGE_LABELS,
  JOB_STAGE_ORDER,
} from "../types";

interface ProgressTimelineProps {
  progress: JobProgress[];
  currentStage?: JobStage;
  overallPercent: number;
}

export const ProgressTimeline: React.FC<ProgressTimelineProps> = ({
  progress,
  currentStage,
  overallPercent,
}) => {
  const progressMap = new Map<JobStage, JobProgress>();
  progress.forEach((p) => progressMap.set(p.stage, p));

  return (
    <div className="progress-timeline">
      <div style={{ marginBottom: "1rem" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            marginBottom: "0.5rem",
            fontSize: "0.875rem",
            color: "#64748b",
          }}
        >
          <span>Overall Progress</span>
          <span style={{ fontWeight: 600, color: "#1e293b" }}>
            {overallPercent.toFixed(1)}%
          </span>
        </div>
        <div
          style={{
            height: "8px",
            backgroundColor: "#e2e8f0",
            borderRadius: "9999px",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              width: `${Math.min(100, overallPercent)}%`,
              background:
                "linear-gradient(90deg, #3b82f6 0%, #8b5cf6 100%)",
              borderRadius: "9999px",
              transition: "width 0.3s ease",
            }}
          />
        </div>
      </div>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "0.25rem",
          marginTop: "1.5rem",
        }}
      >
        {JOB_STAGE_ORDER.map((stage, idx) => {
          const stageProgress = progressMap.get(stage);
          const isActive = currentStage === stage;
          const isDone =
            stageProgress?.finished_at !== undefined ||
            (stageProgress && stageProgress.percent >= 100);
          const label = JOB_STAGE_LABELS[stage];

          let statusColor = "#94a3b8";
          if (isActive) statusColor = "#3b82f6";
          if (isDone) statusColor = "#10b981";

          return (
            <div
              key={stage}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "1rem",
                padding: "0.625rem 0.875rem",
                borderRadius: "0.5rem",
                backgroundColor: isActive ? "#eff6ff" : "transparent",
                transition: "all 0.2s",
              }}
            >
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                <div
                  style={{
                    width: "24px",
                    height: "24px",
                    borderRadius: "50%",
                    border: `2px solid ${statusColor}`,
                    backgroundColor: isDone ? statusColor : "white",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: isDone ? "white" : statusColor,
                    fontSize: "0.75rem",
                    fontWeight: 700,
                    flexShrink: 0,
                    ...(isActive
                      ? { boxShadow: `0 0 0 4px ${statusColor}22` }
                      : {}),
                  }}
                >
                  {isDone ? "✓" : idx + 1}
                </div>
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: "0.25rem",
                  }}
                >
                  <span
                    style={{
                      fontWeight: isActive ? 600 : 500,
                      color: isActive ? "#1e40af" : isDone ? "#065f46" : "#475569",
                      fontSize: "0.875rem",
                    }}
                  >
                    {label}
                  </span>
                  <span
                    style={{
                      fontSize: "0.75rem",
                      color: "#64748b",
                      fontWeight: 600,
                    }}
                  >
                    {isDone
                      ? "Done"
                      : isActive
                      ? `${(stageProgress?.percent ?? 0).toFixed(0)}%`
                      : "Waiting"}
                  </span>
                </div>
                {isActive && stageProgress?.message && (
                  <div
                    style={{
                      fontSize: "0.75rem",
                      color: "#64748b",
                      fontStyle: "italic",
                    }}
                  >
                    {stageProgress.message}
                  </div>
                )}
                {stageProgress?.percent !== undefined &&
                  stageProgress.percent > 0 &&
                  stageProgress.percent < 100 && (
                    <div
                      style={{
                        height: "4px",
                        backgroundColor: "#e2e8f0",
                        borderRadius: "9999px",
                        overflow: "hidden",
                        marginTop: "0.375rem",
                      }}
                    >
                      <div
                        style={{
                          height: "100%",
                          width: `${stageProgress.percent}%`,
                          backgroundColor: statusColor,
                          borderRadius: "9999px",
                        }}
                      />
                    </div>
                  )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
