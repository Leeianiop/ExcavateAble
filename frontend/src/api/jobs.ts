import { apiClient } from "./client";
import type {
  JobResponse,
  BundleInitRequest,
  BundleInitResponse,
  BundleFinalizeResponse,
} from "../types";

export async function getJob(jobId: string): Promise<JobResponse> {
  const res = await apiClient.get<JobResponse>(`/jobs/${jobId}`);
  return res.data;
}

export async function pollJob(
  jobId: string,
  onUpdate: (job: JobResponse) => void,
  shouldStop: (job: JobResponse) => boolean,
  intervalMs = 2000
): Promise<void> {
  const tick = async () => {
    try {
      const job = await getJob(jobId);
      onUpdate(job);
      if (!shouldStop(job)) {
        setTimeout(tick, intervalMs);
      }
    } catch (err) {
      console.error("Poll error:", err);
      setTimeout(tick, intervalMs * 2);
    }
  };
  tick();
}

export async function initBundle(
  req: BundleInitRequest
): Promise<BundleInitResponse> {
  const res = await apiClient.post<BundleInitResponse>("/bundles/init", req);
  return res.data;
}

export async function finalizeBundle(
  bundleId: string,
  manifest: ArrayBuffer,
  manifestSha256: string
): Promise<BundleFinalizeResponse> {
  const res = await apiClient.post<BundleFinalizeResponse>(
    `/bundles/${bundleId}/finalize`,
    manifest,
    {
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Manifest-SHA256": manifestSha256,
      },
    }
  );
  return res.data;
}
