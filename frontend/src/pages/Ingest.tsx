import * as React from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/http";
import { createInstance, listInstances, type Instance } from "@/api/instances";
import { cancelJob, createJob, getJob, listJobs, type DocOutcome, type Job } from "@/api/jobs";
import { listConfigs } from "@/api/configs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useConfirm } from "@/components/confirm-dialog";
import { toast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";

// Only "graph" (neo4j) is wired to a real sink adapter today
// (app/pipeline/registry.py's SINKS) — a "vector" checkbox with nothing
// behind it would just be a dishonest control, so it's left out entirely
// until the vector-sink ticket registers "chroma" there.
const SOURCE_TYPES: { value: string; label: string }[] = [
  { value: "obsidian", label: "Obsidian vault" },
  { value: "docx", label: "Documents folder" },
];
const SINK_OPTIONS: { value: string; label: string }[] = [{ value: "neo4j", label: "Knowledge graph (Neo4j)" }];

const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);

const OUTCOME_STYLES: Record<DocOutcome, string> = {
  updated: "bg-emerald-100 text-emerald-800",
  skipped: "bg-neutral-100 text-neutral-600",
  removed: "bg-neutral-100 text-neutral-600",
  failed: "bg-red-100 text-red-800",
};

interface Draft {
  name: string;
  sourceType: string;
  sourcePath: string;
  sinks: string[];
}

export function Ingest() {
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const confirm = useConfirm();

  const [mode, setMode] = React.useState<"existing" | "new">("new");
  const isExisting = mode === "existing";
  const [selectedInstanceId, setSelectedInstanceId] = React.useState<string>("");
  const [draft, setDraft] = React.useState<Draft>({
    name: "",
    sourceType: searchParams.get("source_type") ?? "",
    sourcePath: searchParams.get("source_path") ?? "",
    sinks: searchParams.get("sinks")?.split(",").filter(Boolean) ?? [],
  });
  const [configId, setConfigId] = React.useState("");
  const [jobId, setJobId] = React.useState<string | null>(null);

  const instancesQuery = useQuery({ queryKey: ["instances"], queryFn: listInstances });
  const configsQuery = useQuery({ queryKey: ["configs"], queryFn: listConfigs });

  const selectedInstance: Instance | undefined = instancesQuery.data?.instances.find(
    (i) => i.id === selectedInstanceId,
  );

  // An existing instance's source/path/sinks (its identity, ADR-0025) are
  // locked once chosen; its config is deliberately left editable — ADR-0025
  // keeps config out of instance identity precisely so a re-run can use a
  // different one, and the Instance itself carries no config_id at all
  // (only a run of it does).
  const lastJobQuery = useQuery({
    queryKey: ["jobs", "lastForInstance", selectedInstanceId],
    queryFn: () => listJobs({ instanceId: selectedInstanceId, limit: 1 }),
    enabled: isExisting && !!selectedInstanceId,
  });

  React.useEffect(() => {
    if (!isExisting || !selectedInstance) return;
    setDraft((prev) => ({
      ...prev,
      sourceType: selectedInstance.source_type,
      sourcePath: selectedInstance.source_path,
      sinks: selectedInstance.sinks,
    }));
  }, [isExisting, selectedInstance]);

  React.useEffect(() => {
    const lastJob = lastJobQuery.data?.jobs[0];
    if (isExisting && lastJob) setConfigId(lastJob.config_id);
  }, [isExisting, lastJobQuery.data]);

  const jobQuery = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const job = query.state.data as Job | undefined;
      return job && TERMINAL_STATUSES.has(job.status) ? false : 1000;
    },
  });
  const job = jobQuery.data;

  // Toast once per job the moment polling observes it land in a terminal,
  // unsuccessful state — the inline banner below covers the same case for
  // anyone still looking at the page, this covers the "walked away" case
  // ADR-0016 has in mind for "job failed" feedback.
  const lastToastedStatus = React.useRef<Job["status"] | undefined>(undefined);
  React.useEffect(() => {
    if (!job || job.status === lastToastedStatus.current) return;
    lastToastedStatus.current = job.status;
    if (job.status === "failed") {
      toast({ variant: "destructive", title: "Job failed", description: job.error ?? undefined });
    } else if (job.status === "cancelled") {
      toast({ title: "Job cancelled" });
    }
  }, [job]);

  const runMutation = useMutation({
    mutationFn: async () => {
      const instanceId = isExisting
        ? selectedInstanceId
        : (
            await createInstance({
              name: draft.name.trim() || undefined,
              source_type: draft.sourceType,
              source_path: draft.sourcePath,
              sinks: draft.sinks,
            })
          ).instance_id;
      return createJob({ instance_id: instanceId, config_id: configId });
    },
    onSuccess: (data) => {
      setJobId(data.job_id);
      queryClient.invalidateQueries({ queryKey: ["instances"] });
    },
    onError: (error: unknown) => {
      const message =
        error instanceof ApiError && error.status === 409
          ? "An instance with this source and sink(s) already exists."
          : "Failed to start the job.";
      toast({ variant: "destructive", title: "Run failed", description: message });
    },
  });

  const cancelMutation = useMutation({
    mutationFn: () => cancelJob(jobId!),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["job", jobId] }),
    onError: (error: unknown) => {
      const message =
        error instanceof ApiError && error.status === 409
          ? "That job already finished."
          : "Failed to cancel the job.";
      toast({ variant: "destructive", title: "Cancel failed", description: message });
    },
  });

  const availableConfigs = (configsQuery.data?.configs ?? []).filter((c) => c.source_type === draft.sourceType);
  const identityLocked = isExisting;
  const canRun =
    draft.sourceType !== "" &&
    draft.sourcePath.trim() !== "" &&
    draft.sinks.length > 0 &&
    configId !== "" &&
    (!isExisting || selectedInstanceId !== "") &&
    !runMutation.isPending;

  const handleCancel = async () => {
    const confirmed = await confirm({
      title: "Cancel this job?",
      description: "The job will stop at its next document boundary.",
      confirmLabel: "Cancel job",
    });
    if (confirmed) cancelMutation.mutate();
  };

  return (
    <div className="mx-auto max-w-3xl p-8">
      <h1 className="text-lg font-semibold text-neutral-900">Ingest</h1>

      <section className="mt-6 space-y-3">
        <Label>Instance</Label>
        <div className="flex gap-2">
          <Button
            type="button"
            variant={!isExisting ? "default" : "outline"}
            onClick={() => {
              setMode("new");
              setSelectedInstanceId("");
            }}
          >
            New instance
          </Button>
          <Button
            type="button"
            variant={isExisting ? "default" : "outline"}
            onClick={() => setMode("existing")}
            disabled={(instancesQuery.data?.instances.length ?? 0) === 0}
          >
            Existing instance
          </Button>
        </div>

        {isExisting && (
          <Select value={selectedInstanceId} onValueChange={setSelectedInstanceId}>
            <SelectTrigger>
              <SelectValue placeholder="Choose an instance…" />
            </SelectTrigger>
            <SelectContent>
              {instancesQuery.data?.instances.map((instance) => (
                <SelectItem key={instance.id} value={instance.id}>
                  {instance.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        {!isExisting && (
          <div>
            <Label htmlFor="instance-name">Name (optional)</Label>
            <Input
              id="instance-name"
              value={draft.name}
              onChange={(e) => setDraft((prev) => ({ ...prev, name: e.target.value }))}
              placeholder="Auto-named from the path if left blank"
            />
          </div>
        )}
      </section>

      <section className="mt-6 space-y-4">
        <div>
          <Label htmlFor="source-type">Source type</Label>
          <Select
            value={draft.sourceType}
            onValueChange={(value) => setDraft((prev) => ({ ...prev, sourceType: value }))}
            disabled={identityLocked}
          >
            <SelectTrigger id="source-type">
              <SelectValue placeholder="Choose a source…" />
            </SelectTrigger>
            <SelectContent>
              {SOURCE_TYPES.map((s) => (
                <SelectItem key={s.value} value={s.value}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div>
          <Label htmlFor="source-path">Path</Label>
          <Input
            id="source-path"
            value={draft.sourcePath}
            onChange={(e) => setDraft((prev) => ({ ...prev, sourcePath: e.target.value }))}
            disabled={identityLocked}
            placeholder="/path/to/vault-or-folder"
          />
        </div>

        <div>
          <Label>Sinks</Label>
          <div className="mt-1 flex flex-col gap-2">
            {SINK_OPTIONS.map((sink) => (
              <label key={sink.value} className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={draft.sinks.includes(sink.value)}
                  disabled={identityLocked}
                  onCheckedChange={(checked) =>
                    setDraft((prev) => ({
                      ...prev,
                      sinks: checked ? [...prev.sinks, sink.value] : prev.sinks.filter((s) => s !== sink.value),
                    }))
                  }
                />
                {sink.label}
              </label>
            ))}
          </div>
        </div>

        <div>
          <Label htmlFor="config">Config</Label>
          <Select value={configId} onValueChange={setConfigId} disabled={draft.sourceType === ""}>
            <SelectTrigger id="config">
              <SelectValue placeholder="Choose a rule config…" />
            </SelectTrigger>
            <SelectContent>
              {availableConfigs.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <Button onClick={() => runMutation.mutate()} disabled={!canRun}>
          {runMutation.isPending ? "Starting…" : "Run"}
        </Button>
      </section>

      {job && (
        <section className="mt-8 rounded-lg border border-neutral-200 p-4">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium capitalize">{job.status}</span>
            {(job.status === "pending" || job.status === "running") && (
              <Button variant="outline" size="sm" onClick={handleCancel}>
                Cancel job
              </Button>
            )}
          </div>

          <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-neutral-100">
            <div
              className="h-full bg-neutral-900 transition-all"
              style={{ width: `${Math.round(job.progress * 100)}%` }}
            />
          </div>

          {job.status === "failed" && job.error && (
            <p className="mt-3 rounded-md bg-red-50 p-2 text-sm text-red-800">{job.error}</p>
          )}

          {job.doc_statuses.length > 0 && (
            <table className="mt-4 w-full text-left text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-neutral-500">
                  <th className="py-1 font-medium">Document</th>
                  <th className="py-1 font-medium">Outcome</th>
                  <th className="py-1 font-medium">Warning</th>
                </tr>
              </thead>
              <tbody>
                {job.doc_statuses.map((doc) => (
                  <tr key={doc.doc_id} className="border-b border-neutral-100 align-top">
                    <td className="py-1.5">{doc.doc_id}</td>
                    <td className="py-1.5">
                      <span className={cn("rounded px-1.5 py-0.5 text-xs font-medium", OUTCOME_STYLES[doc.outcome])}>
                        {doc.outcome}
                      </span>
                    </td>
                    <td className="py-1.5">
                      {doc.warning && <span className="text-amber-700">{doc.warning}</span>}
                      {doc.error && (
                        <details>
                          <summary className="cursor-pointer text-red-700">Error</summary>
                          <p className="mt-1 whitespace-pre-wrap text-neutral-600">{doc.error}</p>
                        </details>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {job.orphans.length > 0 && (
            <div className="mt-4">
              <h2 className="text-sm font-medium">Orphaned edges flagged</h2>
              <ul className="mt-1 space-y-1 text-sm text-neutral-600">
                {job.orphans.map((orphan) => (
                  <li key={orphan.edge_id}>
                    {orphan.edge_id} — {orphan.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
