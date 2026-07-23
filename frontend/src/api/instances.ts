/** Instances API (spec §8, ADR-0025/0026). */
import { request } from "@/api/http";

export interface Instance {
  id: string;
  name: string;
  source_type: string;
  source_path: string;
  sinks: string[];
  created_at: string;
  updated_at: string;
  job_count: number;
  last_status: string | null;
  last_run_at: string | null;
}

export interface CreateInstanceRequest {
  name?: string;
  source_type: string;
  source_path: string;
  sinks: string[];
}

export function listInstances() {
  return request<{ instances: Instance[] }>("/instances");
}

export function getInstance(instanceId: string) {
  return request<Instance>(`/instances/${instanceId}`);
}

export function createInstance(payload: CreateInstanceRequest) {
  return request<{ instance_id: string }>("/instances", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function renameInstance(instanceId: string, name: string) {
  return request<Instance>(`/instances/${instanceId}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export function deleteInstance(instanceId: string) {
  return request<void>(`/instances/${instanceId}`, { method: "DELETE" });
}
