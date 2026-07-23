/** Configs API (spec §7/§8) — just the list read Ingest needs; the Rules
 * page (#11) will add the rest (get/create/update/preview). */
import { request } from "@/api/http";

export interface ConfigSummary {
  id: string;
  source_type: string;
  title: string;
}

export function listConfigs() {
  return request<{ configs: ConfigSummary[] }>("/configs");
}
