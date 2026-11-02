import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";

import { MemoryComparison } from "@/components/settings/evaluation/memory/MemoryComparison";
import type { CalibrationState } from "@/components/settings/evaluation/EvaluationCalibration";
import {
  MemoryDatasets,
  type MemoryDatasetImportMeta,
  type MemoryDatasetImportOutcome,
  type MemoryDatasetRevisionView,
} from "@/components/settings/evaluation/memory/MemoryDatasets";
import { MemoryExperimentDetail } from "@/components/settings/evaluation/memory/MemoryExperimentDetail";
import {
  MEMORY_DEFAULT_CHANGED_FIELDS,
  MemoryExperimentForm,
  type MemoryDatasetOption,
  type MemoryExperimentCapabilitiesView,
  type MemoryExperimentDraft,
  type MemoryMetricOption,
  type MemoryProfileOption,
  type MemoryTrack,
} from "@/components/settings/evaluation/memory/MemoryExperimentForm";
import { MemoryReplay } from "@/components/settings/evaluation/memory/MemoryReplay";
import { evaluationErrorText } from "@/components/settings/evaluation/evaluation-utils";
import { Button } from "@/components/ui/button";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { evaluationApi } from "@/lib/evaluation-api";
import {
  memoryEvaluationApi,
  memoryExperimentIsTerminal,
  type MemoryCapabilitiesView,
  type MemoryDatasetRevision,
  type MemoryExperimentCreate,
  type MemoryExperimentSummary,
} from "@/lib/memory-evaluation-api";
import { cn } from "@/lib/utils";

const BASE = "settings.evaluation.memory.";

/**
 * 契约 §8：判分算法版本进 `grading_digest`，创建实验时必须显式声明。
 * 版本号与后端 `nanobot/evaluation/memory/grading.py` 的常量一一对应。
 */
export const MEMORY_GRADING_POLICY = {
  fact_matching_version: "fact-matching/v1",
  metric_aggregation_version: "metric-aggregation/v1",
  bootstrap_version: "paired-cluster-percentile/v1",
} as const;

const FALLBACK_METRICS: MemoryMetricOption[] = [
  { id: "qa_accuracy", label: "qa_accuracy", unit: "ratio" },
  { id: "extraction_f1", label: "extraction_f1", unit: "ratio" },
  { id: "evidence_recall_at_k", label: "evidence_recall_at_k", unit: "ratio" },
];

type MemoryView = "experiments" | "datasets" | "create" | "detail" | "comparison" | "replay";

function splitOf(revision: MemoryDatasetRevision): "development" | "calibration" | "regression" {
  const keys = Object.keys(revision.split_counts ?? {});
  const known = keys.find(
    (key) => key === "development" || key === "calibration" || key === "regression",
  );
  return (known as "development" | "calibration" | "regression" | undefined) ?? "development";
}

function scenarioCountOf(revision: MemoryDatasetRevision): number {
  return Object.values(revision.split_counts ?? {}).reduce(
    (total, value) => total + (typeof value === "number" ? value : 0),
    0,
  );
}

export function toDatasetView(revision: MemoryDatasetRevision): MemoryDatasetRevisionView {
  return {
    id: revision.id,
    datasetVersion: revision.dataset_version,
    status: revision.status,
    splitCounts: revision.split_counts as Record<string, number>,
    scenarioCount: scenarioCountOf(revision),
    coverage: [],
    source: {
      repoUrl: revision.source.repo_url,
      revision: revision.source.revision,
      license: revision.source.license,
      sha256: revision.source.sha256,
      loaderVersion: revision.source.loader_version,
      gradingProtocol: revision.source.grading_protocol,
    },
    definitionDigest: revision.definition_digest,
    createdAt: revision.created_at,
  };
}

function toDatasetOption(revision: MemoryDatasetRevision): MemoryDatasetOption {
  return {
    id: revision.id,
    label: `${revision.dataset_version}`,
    split: splitOf(revision),
    status: revision.status,
    scenarioCount: scenarioCountOf(revision),
    splitCounts: revision.split_counts as Record<string, number>,
  };
}

/** 契约 §3：能力缺失必须显式传递，表单据此禁用对应轨道并给出原因。 */
export function toFormCapabilities(
  view: MemoryCapabilitiesView | null,
  calibration: CalibrationState | null = null,
): MemoryExperimentCapabilitiesView {
  if (!view) return { tracks: {}, settings: [] };
  const tracks: MemoryExperimentCapabilitiesView["tracks"] = {};
  for (const track of view.tracks) {
    tracks[track.id as MemoryTrack] = {
      status: track.supported ? "supported" : "unsupported",
      detail: track.missing.length > 0 ? track.missing.join(", ") : undefined,
    };
  }
  const calibrated =
    calibration !== null &&
    calibration.status === "ready" &&
    calibration.agreement_rate !== null &&
    calibration.agreement_rate >= calibration.agreement_threshold;
  return {
    tracks,
    settings: view.settings,
    judge_calibration: calibration === null ? "unknown" : calibrated ? "calibrated" : "not_calibrated",
    delete: view.capabilities.delete,
    clock_coverage: view.capabilities.clock_coverage,
    scope_coverage: view.capabilities.scope_coverage,
  };
}

function toProfileOption(raw: Record<string, unknown>): MemoryProfileOption | null {
  const id = typeof raw.id === "string" ? raw.id : "";
  if (!id) return null;
  const kind = raw.kind === "judge" ? "judge" : raw.kind === "agent" ? "agent" : "both";
  return {
    id,
    label: String(raw.label ?? raw.model ?? id),
    kind,
    model: typeof raw.model === "string" ? raw.model : undefined,
  };
}

function metricOptions(view: MemoryCapabilitiesView | null): MemoryMetricOption[] {
  if (!view) return FALLBACK_METRICS;
  const ids = new Set<string>();
  for (const track of view.tracks) {
    if (track.primary_metric) ids.add(track.primary_metric);
    for (const metric of track.required_metrics) ids.add(metric);
  }
  if (ids.size === 0) return FALLBACK_METRICS;
  return Array.from(ids).map((id) => ({
    id,
    label: id,
    unit: id.includes("latency") ? "ms" : id.includes("token") ? "token" : "ratio",
  }));
}

async function sha256Of(file: File): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new Error("SHA-256 unavailable");
  const digest = await subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export type MemoryEvaluationProps = {
  /** 复用既有校准状态：样本够了不等于 Judge 已校准，所以原样透传、不自己判绿。 */
  calibration?: CalibrationState | null;
  className?: string;
};

export function MemoryEvaluation({ calibration = null, className }: MemoryEvaluationProps) {
  const { t } = useTranslation();
  const [view, setView] = useState<MemoryView>("experiments");
  const [experiments, setExperiments] = useState<MemoryExperimentSummary[]>([]);
  const [datasets, setDatasets] = useState<MemoryDatasetRevision[]>([]);
  const [capabilities, setCapabilities] = useState<MemoryCapabilitiesView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [baselineId, setBaselineId] = useState<string | null>(null);
  const [selectedExperimentId, setSelectedExperimentId] = useState<string | null>(null);
  const [comparisonId, setComparisonId] = useState<string | null>(null);
  const [replay, setReplay] = useState<{ attemptId: string; scenarioId: string | null } | null>(null);
  const [backTarget, setBackTarget] = useState<MemoryView>("experiments");
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [createDatasetId, setCreateDatasetId] = useState<string | undefined>(undefined);
  const [compareBusy, setCompareBusy] = useState<string | null>(null);
  const [compareError, setCompareError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [experimentPage, datasetPage, capability] = await Promise.all([
        memoryEvaluationApi.listExperiments({ limit: 50 }),
        memoryEvaluationApi.listDatasets({ limit: 50 }),
        memoryEvaluationApi.capabilities().catch(() => null),
      ]);
      setExperiments(experimentPage.data);
      setDatasets(datasetPage.data);
      setCapabilities(capability);
      setError(null);
    } catch (failure) {
      setError(evaluationErrorText(failure, t));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const datasetViews = useMemo(() => datasets.map(toDatasetView), [datasets]);
  const datasetOptions = useMemo(() => datasets.map(toDatasetOption), [datasets]);
  const formCapabilities = useMemo(
    () => toFormCapabilities(capabilities, calibration),
    [capabilities, calibration],
  );
  const profiles = useMemo(
    () =>
      (capabilities?.profiles ?? [])
        .map(toProfileOption)
        .filter((profile): profile is MemoryProfileOption => profile !== null),
    [capabilities],
  );
  const baselines = useMemo(
    () =>
      experiments.map((experiment) => ({
        id: experiment.id,
        label: `${experiment.id.slice(0, 8)} · ${experiment.track}`,
        datasetRevisionId: experiment.dataset_revision_id,
        track: experiment.track as MemoryTrack,
        repetitions: experiment.repetitions,
        modelMode: experiment.model_mode,
        primaryMetric: experiment.primary_metric,
      })),
    [experiments],
  );

  const openExperiment = (experimentId: string) => {
    setSelectedExperimentId(experimentId);
    setView("detail");
  };

  const submitExperiment = async (draft: MemoryExperimentDraft) => {
    setFormBusy(true);
    setFormError(null);
    try {
      const payload: MemoryExperimentCreate = {
        dataset_revision_id: draft.dataset_revision_id,
        split: draft.split,
        track: draft.track,
        candidates: draft.candidates.map((candidate) => ({
          id: candidate.id,
          name: candidate.name,
          mode: candidate.mode,
          agent_profile_id: candidate.agent_profile_id,
          extractor_profile_id: candidate.extractor_profile_id,
          memory_settings: candidate.memory_settings,
        })),
        repetitions: draft.repetitions,
        model_mode: draft.model_mode,
        script_fixture_id: draft.script_fixture_id ?? null,
        judge_profile_id: draft.judge_profile_id,
        budgets: { ...draft.budgets },
        primary_metric: draft.primary_metric,
        policy: { ...MEMORY_GRADING_POLICY },
      };
      const created = await memoryEvaluationApi.createExperiment(payload);
      if (draft.baseline_experiment_id) setBaselineId(draft.baseline_experiment_id);
      await load();
      openExperiment(created.id);
    } catch (failure) {
      setFormError(evaluationErrorText(failure, t));
    } finally {
      setFormBusy(false);
    }
  };

  const importDataset = async (
    file: File,
    meta: MemoryDatasetImportMeta,
  ): Promise<MemoryDatasetImportOutcome> => {
    const sha256 = await sha256Of(file);
    const result = await memoryEvaluationApi.importDataset({
      file,
      fileName: file.name,
      format: meta.format,
      dataset_version: file.name.replace(/\.[^.]+$/, "") || "uploaded",
      source: {
        repo_url: meta.sourceRepo,
        revision: meta.sourceRevision,
        sha256,
        license: meta.license,
        original_ids: [],
        loader_version: "webui-upload/v1",
        grading_protocol: "memory-dataset/v1",
      },
    });
    await load();
    return {
      status: result.status === "ready" ? "ready" : result.status === "invalid" ? "invalid" : "staging",
      datasetRevisionId: result.dataset_revision_id,
      counts: { scenarios: result.scenario_count },
      errors: result.errors.map((row) => ({
        line: typeof row.line === "number" ? row.line : null,
        field: typeof row.field === "string" ? row.field : null,
        scenarioId: typeof row.scenario_id === "string" ? row.scenario_id : null,
        message: String(row.message ?? ""),
      })),
    };
  };

  const importBuiltin = async (sampleId: string): Promise<MemoryDatasetImportOutcome> => {
    const result = await memoryEvaluationApi.importSample(sampleId);
    await load();
    return {
      status: result.status === "invalid" ? "invalid" : result.status === "ready" ? "ready" : "staging",
      datasetRevisionId: result.memory_dataset_revision_id ?? result.dataset_revision_id ?? null,
      counts: { scenarios: result.scenario_count ?? 0 },
      errors: [],
    };
  };

  const compare = async (candidateExperimentId: string) => {
    if (!baselineId || baselineId === candidateExperimentId) return;
    setCompareBusy(candidateExperimentId);
    setCompareError(null);
    try {
      const [baseline, candidate] = await Promise.all([
        memoryEvaluationApi.getExperiment(baselineId),
        memoryEvaluationApi.getExperiment(candidateExperimentId),
      ]);
      const baselineCandidate = baseline.candidates[0];
      const candidateCandidate = candidate.candidates[0];
      if (!baselineCandidate || !candidateCandidate) {
        throw new Error(t(BASE + "comparison.notComparable"));
      }
      const summary = await memoryEvaluationApi.createComparison({
        baseline_experiment_id: baselineId,
        candidate_experiment_id: candidateExperimentId,
        baseline_candidate_id: baselineCandidate.id,
        candidate_candidate_id: candidateCandidate.id,
        changed_fields: [...MEMORY_DEFAULT_CHANGED_FIELDS],
        policy: {},
      });
      setComparisonId(summary.id);
      setView("comparison");
    } catch (failure) {
      setCompareError(
        t(BASE + "list.compareFailed", {
          message: failure instanceof Error ? failure.message : "",
        }),
      );
    } finally {
      setCompareBusy(null);
    }
  };

  /**
   * 比较页的逐场景入口：在候选实验的分批里找到包含该场景的 attempt。
   * 只探测前 5 条记录，找不到就明确说明，不假装存在回放。
   */
  const openScenarioReplay = async (scenarioId: string) => {
    setBackTarget("comparison");
    if (!comparisonId) return;
    try {
      const detail = await memoryEvaluationApi.getExperiment(
        (await memoryEvaluationApi.getComparison(comparisonId, { limit: 1 }))
          .candidate_experiment_id,
      );
      const batches = detail.child_runs.filter((child) =>
        child.scenario_ids.includes(scenarioId),
      );
      let probed = 0;
      for (const batch of batches) {
        const page = await evaluationApi.listAttempts(batch.run_id);
        for (const attempt of page.data) {
          if (probed >= 5) break;
          probed += 1;
          const probes = await memoryEvaluationApi.listAttemptProbes(attempt.id, { limit: 1 });
          if (probes.data.some((probe) => probe.scenario_id === scenarioId)) {
            setReplay({ attemptId: attempt.id, scenarioId });
            setView("replay");
            return;
          }
        }
      }
      setCompareError(t(BASE + "comparison.casesEmpty"));
    } catch (failure) {
      setCompareError(evaluationErrorText(failure, t));
    }
  };

  const openReplay = (attemptId: string, scenarioId: string | null) => {
    setBackTarget("detail");
    setReplay({ attemptId, scenarioId });
    setView("replay");
  };

  const tabValue = view === "datasets" ? "datasets" : view === "create" ? "create" : "experiments";

  return (
    <div className={cn("min-w-0 space-y-5", className)}>
      <header className="space-y-1">
        <h2 className="text-xl font-semibold tracking-tight text-foreground">
          {t(BASE + "list.title")}
        </h2>
        <p className="text-[13px] text-muted-foreground">{t(BASE + "list.subtitle")}</p>
      </header>

      {error ? (
        <div
          role="alert"
          className="rounded-floating border border-destructive/20 bg-destructive/5 px-4 py-3 text-[13px] text-destructive"
        >
          {error}
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 pb-3">
        <SegmentedControl
          mode="tabs"
          value={tabValue}
          onChange={(next) => {
            setView(next);
            setCompareError(null);
          }}
          ariaLabel={t(BASE + "list.title")}
          className="rounded-control bg-muted/40 p-1"
          itemClassName="rounded-control px-4 py-2"
          options={[
            { value: "experiments", label: t(BASE + "nav.experiments") },
            { value: "datasets", label: t(BASE + "nav.datasets") },
            { value: "create", label: t(BASE + "nav.create") },
          ]}
        />
        <Button type="button" variant="ghost" size="sm" onClick={() => void load()}>
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          {t(BASE + "common.refresh")}
        </Button>
      </div>

      {view === "experiments" ? (
        <section className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
            <span>
              {t(BASE + "list.baseline")}:{" "}
              <strong className="font-medium text-foreground">
                {baselineId ? baselineId.slice(0, 8) : t(BASE + "form.baselineNone")}
              </strong>
            </span>
            <span>{t(BASE + "list.compareHint")}</span>
          </div>
          {compareError ? (
            <p role="alert" className="text-[12px] text-destructive">
              {compareError}
            </p>
          ) : null}
          {loading && experiments.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">{t(BASE + "common.loading")}</p>
          ) : null}
          {!loading && experiments.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">{t(BASE + "list.empty")}</p>
          ) : null}
          {experiments.length > 0 ? (
            <div className="overflow-x-auto rounded-panel border border-border/60 bg-settings-surface">
              <table className="w-full min-w-[760px] text-left text-[13px]">
                <thead className="border-b border-border/60 text-[11px] text-muted-foreground">
                  <tr>
                    <th className="px-4 py-2 font-medium">{t(BASE + "list.column.experiment")}</th>
                    <th className="px-2 py-2 font-medium">{t(BASE + "list.column.track")}</th>
                    <th className="px-2 py-2 font-medium">{t(BASE + "list.column.status")}</th>
                    <th className="px-2 py-2 font-medium">{t(BASE + "list.column.primary")}</th>
                    <th className="px-2 py-2 font-medium">{t(BASE + "list.column.created")}</th>
                    <th className="px-4 py-2 text-right font-medium">
                      {t(BASE + "list.column.actions")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {experiments.map((experiment) => (
                    <tr
                      key={experiment.id}
                      data-testid={`experiment-${experiment.id}`}
                      className="border-b border-border/40 last:border-b-0"
                    >
                      <td className="px-4 py-2">
                        <button
                          type="button"
                          className="text-left font-medium text-foreground hover:underline"
                          onClick={() => openExperiment(experiment.id)}
                        >
                          {experiment.id.slice(0, 8)}
                        </button>
                        <span className="ml-2 text-[11px] text-muted-foreground">
                          {experiment.dataset_revision_id.slice(0, 8)}
                        </span>
                      </td>
                      <td className="px-2 py-2 text-muted-foreground">
                        {t(BASE + `detail.track.${experiment.track}`, {
                          defaultValue: experiment.track,
                        })}
                      </td>
                      <td className="px-2 py-2">
                        <span
                          className={cn(
                            "inline-flex rounded-full border px-2 py-0.5 text-[11px]",
                            memoryExperimentIsTerminal(experiment.status)
                              ? "border-border/60 bg-muted/30 text-muted-foreground"
                              : "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
                          )}
                        >
                          {experiment.status}
                        </span>
                        <span className="ml-1 text-[11px] text-muted-foreground">
                          {experiment.measurement_status === "complete"
                            ? t(BASE + "list.measurementComplete")
                            : t(BASE + "list.measurementIncomplete")}
                        </span>
                      </td>
                      <td className="px-2 py-2 text-muted-foreground">{experiment.primary_metric}</td>
                      <td className="px-2 py-2 text-muted-foreground">
                        {experiment.created_at ? experiment.created_at.slice(0, 19).replace("T", " ") : "—"}
                      </td>
                      <td className="px-4 py-2">
                        <span className="flex flex-wrap items-center justify-end gap-1">
                          <button
                            type="button"
                            className="rounded border border-border px-2 py-0.5 text-[11px] text-foreground hover:bg-muted/40"
                            onClick={() => openExperiment(experiment.id)}
                          >
                            {t(BASE + "list.open")}
                          </button>
                          <button
                            type="button"
                            className="rounded border border-border px-2 py-0.5 text-[11px] text-foreground hover:bg-muted/40"
                            onClick={() => setBaselineId(experiment.id)}
                          >
                            {t(BASE + "list.setBaseline")}
                          </button>
                          <button
                            type="button"
                            disabled={!baselineId || baselineId === experiment.id || compareBusy === experiment.id}
                            className="rounded border border-border px-2 py-0.5 text-[11px] text-foreground hover:bg-muted/40 disabled:opacity-50"
                            onClick={() => void compare(experiment.id)}
                          >
                            {t(BASE + "list.compare")}
                          </button>
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      ) : null}

      {view === "datasets" ? (
        <MemoryDatasets
          datasets={datasetViews}
          loading={loading}
          error={error}
          onImportDataset={importDataset}
          onImportBuiltin={importBuiltin}
          onRunExperiment={(dataset) => {
            setCreateDatasetId(dataset.id);
            setView("create");
          }}
        />
      ) : null}

      {view === "create" ? (
        <MemoryExperimentForm
          capabilities={formCapabilities}
          datasets={datasetOptions}
          baselines={baselines}
          profiles={profiles}
          metrics={metricOptions(capabilities)}
          defaultDatasetId={createDatasetId}
          busy={formBusy}
          error={formError}
          onSubmit={submitExperiment}
          onCancel={() => setView("experiments")}
        />
      ) : null}

      {view === "detail" && selectedExperimentId ? (
        <MemoryExperimentDetail
          experimentId={selectedExperimentId}
          calibration={calibration}
          onBack={() => setView("experiments")}
          onOpenReplay={openReplay}
        />
      ) : null}

      {view === "comparison" && comparisonId ? (
        <MemoryComparison
          comparisonId={comparisonId}
          onBack={() => setView("experiments")}
          onOpenReplay={(scenarioId) => void openScenarioReplay(scenarioId)}
        />
      ) : null}

      {view === "replay" && replay ? (
        <MemoryReplay
          attemptId={replay.attemptId}
          scenarioId={replay.scenarioId}
          onBack={() => setView(backTarget)}
        />
      ) : null}
    </div>
  );
}
