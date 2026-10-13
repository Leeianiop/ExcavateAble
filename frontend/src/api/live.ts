import { apiClient } from "./client";
import type { LiveFrameResponse } from "../types";

export async function getLiveFrame(): Promise<LiveFrameResponse> {
  const res = await apiClient.get<LiveFrameResponse>("/live/frame");
  return res.data;
}

export function getLiveFrameImageUrl(timestamp?: string): string {
  const base = `${apiClient.defaults.baseURL}/live/frame/image`;
  return timestamp ? `${base}?t=${timestamp}` : base;
}
