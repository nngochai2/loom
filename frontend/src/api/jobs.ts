/** Jobs API (spec §8). Polling only — no SSE (see `pages/Ingest.tsx`). */
import { request } from "@/api/http";

export type JobStatus = "pending" | "running" | "completed" | "failed" | "cancelled";
export type DocOutcome = "skipped" | "updated" | "failed" | "removed";

export interface DocStatus {
  doc_id: string;
  outcome: DocOutcome;
  error: string | null;
  warning: string | null;
}

export interface OrphanFlag {
  edge_id: string;
  reason: string;
}

export interface Job {
  id: string;
  instance_id: string;
  source_type: string;
  source_path: string;
  sinks: string[];
  config_id: string;
  status: JobStatus;
  progress: number;
  doc_statuses: DocStatus[];
  orphans: OrphanFlag[];
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface JobListResponse {
  jobs: Job[];
  total: number;
  limit: number;
  offset: number;
}

export function createJob(payload: { instance_id: string; config_id: string }) {
  return request<{ job_id: string }>("/jobs", { method: "POST", body: JSON.stringify(payload) });
}

export function getJob(jobId: string) {
  return request<Job>(`/jobs/${jobId}`);
}

export function listJobs(params: { instanceId?: string; limit?: number; offset?: number } = {}) {
  const search = new URLSearchParams();
  if (params.instanceId) search.set("instance_id", params.instanceId);
  if (params.limit) search.set("limit", String(params.limit));
  if (params.offset) search.set("offset", String(params.offset));
  const qs = search.toString();
  return request<JobListResponse>(`/jobs${qs ? `?${qs}` : ""}`);
}

export function cancelJob(jobId: string) {
  return request<Job>(`/jobs/${jobId}/cancel`, { method: "POST" });
}
