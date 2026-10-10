export type JobStatus =
  | "pending"
  | "queued"
  | "running"
  | "uploading_assets"
  | "completed"
  | "failed"
  | "cancelled";

export type JobStage =
  | "download_bundle"
  | "background_strip"
  | "colmap_sparse"
  | "colmap_dense"
  | "openmvs_fusion"
  | "mesh_postprocess"
  | "export_ply"
  | "export_glb"
  | "upload_assets";

export interface ShotUploadItem {
  rgb?: string;
  mask?: string;
  depth?: string;
  metadata?: string;
  raw?: string;
}

export interface BundleInitRequest {
  bundle_id: string;
  session_id: string;
  num_shots: number;
  manifest_size_bytes: number;
  manifest_etag: string;
}

export interface BundleInitResponse {
  upload_id: string;
  job_id: string;
  shot_uploads: Record<string, ShotUploadItem>;
  expires_at?: string;
}

export interface BundleFinalizeResponse {
  bundle_id: string;
  job_id: string;
  status: JobStatus;
  message: string;
  asset_url?: string;
}

export interface JobProgress {
  stage: JobStage;
  percent: number;
  message?: string;
  started_at?: string;
  finished_at?: string;
}

export interface JobResponse {
  job_id: string;
  bundle_id: string;
  status: JobStatus;
  created_at: string;
  started_at?: string;
  finished_at?: string;
  current_stage?: JobStage;
  progress_percent: number;
  progress: JobProgress[];
  error_message?: string;
  num_shots: number;

  ply_url?: string;
  glb_url?: string;
  ply_size_bytes: number;
  glb_size_bytes: number;
  num_vertices: number;
  num_faces: number;

  viewer_url?: string;
}

export interface AssetInfo {
  asset_id: string;
  bundle_id: string;
  job_id: string;
  format: string;
  size_bytes: number;
  url: string;
  created_at: string;
}

export interface HealthResponse {
  status: string;
  object_store: string;
  job_store: string;
}

export const JOB_STAGE_LABELS: Record<JobStage, string> = {
  download_bundle: "Download Bundle",
  background_strip: "Background Strip",
  colmap_sparse: "COLMAP Sparse",
  colmap_dense: "COLMAP Dense",
  openmvs_fusion: "OpenMVS Fusion",
  mesh_postprocess: "Mesh Post-Process",
  export_ply: "Export PLY",
  export_glb: "Export GLB",
  upload_assets: "Upload Assets",
};

export const JOB_STAGE_ORDER: JobStage[] = [
  "download_bundle",
  "background_strip",
  "colmap_sparse",
  "colmap_dense",
  "openmvs_fusion",
  "mesh_postprocess",
  "export_ply",
  "export_glb",
  "upload_assets",
];

export const JOB_STATUS_COLORS: Record<JobStatus, string> = {
  pending: "#94a3b8",
  queued: "#f59e0b",
  running: "#3b82f6",
  uploading_assets: "#8b5cf6",
  completed: "#10b981",
  failed: "#ef4444",
  cancelled: "#6b7280",
};

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  pending: "Pending",
  queued: "Queued",
  running: "Running",
  uploading_assets: "Uploading Assets",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};
