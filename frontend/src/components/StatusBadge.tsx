import React from "react";
import {
  JobStatus,
  JOB_STATUS_COLORS,
  JOB_STATUS_LABELS,
} from "../types";

interface StatusBadgeProps {
  status: JobStatus;
  size?: "sm" | "md" | "lg";
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({
  status,
  size = "md",
}) => {
  const color = JOB_STATUS_COLORS[status];
  const label = JOB_STATUS_LABELS[status];
  const sizeClasses = {
    sm: "px-2 py-0.5 text-xs",
    md: "px-3 py-1 text-sm",
    lg: "px-4 py-1.5 text-base",
  }[size];

  return (
    <span
      className={`status-badge ${sizeClasses}`}
      style={{
        backgroundColor: `${color}22`,
        color: color,
        border: `1px solid ${color}44`,
        borderRadius: "9999px",
        fontWeight: 600,
        display: "inline-flex",
        alignItems: "center",
        gap: "0.5rem",
      }}
    >
      <span
        style={{
          width: size === "sm" ? 6 : size === "md" ? 8 : 10,
          height: size === "sm" ? 6 : size === "md" ? 8 : 10,
          borderRadius: "50%",
          backgroundColor: color,
          display: "inline-block",
          ...(status === "running" || status === "queued"
            ? { animation: "pulse-dot 1.5s ease-in-out infinite" }
            : {}),
        }}
      />
      {label}
    </span>
  );
};
