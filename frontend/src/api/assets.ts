import { apiClient } from "./client";
import type { AssetInfo, HealthResponse } from "../types";

export async function listAssets(jobId?: string): Promise<AssetInfo[]> {
  const params = jobId ? { job_id: jobId } : {};
  const res = await apiClient.get<AssetInfo[]>("/assets", { params });
  return res.data;
}

export async function getHealth(): Promise<HealthResponse> {
  const res = await apiClient.get<HealthResponse>("/health");
  return res.data;
}

export function getPlyUrl(jobId: string): string {
  return `${apiClient.defaults.baseURL}/assets/${jobId}/ply`;
}

export function getGlbUrl(jobId: string): string {
  return `${apiClient.defaults.baseURL}/assets/${jobId}/glb`;
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
}
