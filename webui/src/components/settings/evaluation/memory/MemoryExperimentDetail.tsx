import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, RefreshCw } from "lucide-react";

import { EvaluationStatusBadge } from "@/components/settings/evaluation/evaluation-status";
import {
  EvaluationCalibration,
  type CalibrationState,
} from "@/components/settings/evaluation/EvaluationCalibration";
import { evaluationErrorText } from "@/components/settings/evaluation/evaluation-utils";
import { MemoryMetricValue } from "@/components/settings/evaluation/memory/MemoryMetricValue";
import { memoryReasonText, memoryTrackOfMetric } from "@/components/settings/evaluation/memory/MemoryComparison";
import { Button } from "@/components/ui/button";
import {
  evaluationApi,
  type Attempt,
} from "@/lib/evaluation-api";
import {
  memoryEvaluationApi,
  memoryExperimentIsTerminal,
  type MemoryChildRun,
  type MemoryExperimentDetail as MemoryExperimentDetailData,
  type MemoryMetricsView,
} from "@/lib/memory-evaluation-api";
import { cn } from "@/lib/utils";

const BASE = "settings.evaluation.memory.detail.";

const BUDGET_LABEL: Record<string, string> = {
  max_trials: "maxTrials",
  max_model_requests: "maxModelRequests",
  max_input_tokens: "maxInputTokens",
  max_output_tokens: "maxOutputTokens",
  max_total_cost: "maxTotalCost",
  per_trial_timeout_s: "perTrialTimeout",
  group_timeout_s: "groupTimeout",
  concurrency: "concurrency",
};

const TRACK_ORDER = ["extraction", "retrieval", "end_to_end", "agent", "other"] as const;

export type MemoryExperimentDetailProps = {
  experimentId: string;
  /** 复用既有校准状态：只展示，不因样本数量足够就当成已校准。 */
  calibration?: CalibrationState | null;
  onBack?: () => void;
  /** 进入场景回放：按真实 event seq 对照证据。 */
  onOpenReplay?: (attemptId: string, scenarioId: string | null) => void;
  className?: string;
};

export function MemoryExperimentDetail({
  experimentId,
  calibration = null,
  onBack,
  onOpenReplay,
  className,
}: MemoryExperimentDetailProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<MemoryExperimentDetailData | null>(null);
  const [metrics, setMetrics] = useState<MemoryMetricsView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [showBatches, setShowBatches] = useState(false);
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [attempts, setAttempts] = useState<Record<string, Attempt[]>>({});
  const [attemptErrors, setAttemptErrors] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [experiment, metricView] = await Promise.all([
        memoryEvaluationApi.getExperiment(experimentId),
        memoryEvaluationApi.getExperimentMetrics(experimentId).catch(() => null),
      ]);
      setDetail(experiment);
      setMetrics(metricView);
      setError(null);
    } catch (failure) {
      setError(evaluationErrorText(failure, t));
    } finally {
      setLoading(false);
    }
  }, [experimentId, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const cancel = async () => {
    setCancelling(true);
    setCancelError(null);
    try {
      const updated = await memoryEvaluationApi.cancelExperiment(experimentId);
      setDetail(updated);
    } catch (failure) {
      setCancelError(evaluationErrorText(failure, t));
    } finally {
      setCancelling(false);
    }
  };

  const loadAttempts = async (runId: string) => {
    setAttemptErrors((current) => {
      const next = { ...current };
      delete next[runId];
      return next;
    });
    try {
      const page = await evaluationApi.listAttempts(runId);
      setAttempts((current) => ({ ...current, [runId]: page.data }));
    } catch (failure) {
      setAttemptErrors((current) => ({ ...current, [runId]: evaluationErrorText(failure, t) }));
    }
  };

  const groupedMetrics = useMemo(() => {
    const groups: Record<string, MemoryMetricsView["metrics"]> = {};
    for (const metric of metrics?.metrics ?? []) {
      const track = memoryTrackOfMetric(metric.id);
      (groups[track] ??= []).push(metric);
    }
    return groups;
  }, [metrics]);

  const terminal = detail ? memoryExperimentIsTerminal(detail.status) : false;
  const budget = detail?.budget ?? {};
  const budgetEntries = Object.entries(budget);
  const childRuns = detail?.child_runs ?? [];
  const candidateNames = new Map(
    (detail?.candidates ?? []).map((candidate) => [candidate.id, candidate.name]),
  );

  return (
    <section aria-label={t(BASE + "title")} className={cn("min-w-0 space-y-5", className)}>
      <header className="flex flex-wrap items-center gap-2">
        {onBack ? (
          <Button type="button" variant="ghost" size="sm" onClick={onBack}>
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
            {t("settings.evaluation.memory.common.back")}
          </Button>
        ) : null}
        <h3 className="text-base font-semibold text-foreground">{t(BASE + "title")}</h3>
        {detail ? <EvaluationStatusBadge status={detail.status} /> : null}
        <span className="text-[12px] text-muted-foreground">{experimentId.slice(0, 8)}</span>
        <div className="ml-auto flex items-center gap-1.5">
          <Button type="button" variant="ghost" size="sm" onClick={() => void load()}>
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            {t("settings.evaluation.memory.common.refresh")}
          </Button>
          {detail && !terminal ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={cancelling}
              onClick={() => void cancel()}
            >
              {cancelling ? t(BASE + "cancelling") : t(BASE + "cancel")}
            </Button>
          ) : null}
        </div>
      </header>

      {error ? (
        <p role="alert" className="rounded-floating border border-destructive/20 bg-destructive/5 px-3 py-2 text-[12px] text-destructive">
          {error}
        </p>
      ) : null}
      {cancelError ? (
        <p role="alert" className="text-[12px] text-destructive">
          {t(BASE + "cancelFailed", { message: cancelError })}
        </p>
      ) : null}
      {loading && !detail ? (
        <p className="text-[13px] text-muted-foreground">
          {t("settings.evaluation.memory.common.loading")}
        </p>
      ) : null}

      {detail ? (
        <>
          {!terminal ? (
            <p data-testid="detail-provisional" className="rounded-floating border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-700 dark:text-amber-300">
              {t(BASE + "provisional")}
            </p>
          ) : null}
          {detail.measurement_status !== "complete" ? (
            <p data-testid="detail-incomplete" className="rounded-floating border border-destructive/20 bg-destructive/5 px-3 py-2 text-[12px] text-destructive">
              {t(BASE + "incomplete")}
            </p>
          ) : null}

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Tile label={t(BASE + "plannedTrials")} value={String(detail.progress.planned_trials)} />
            <Tile
              label={t(BASE + "terminalTrials")}
              value={`${detail.progress.terminal_attempts} / ${detail.progress.attempts}`}
            />
            <Tile
              label={t(BASE + "measurement")}
              value={
                detail.measurement_status === "complete"
                  ? t("settings.evaluation.memory.list.measurementComplete")
                  : t("settings.evaluation.memory.list.measurementIncomplete")
              }
            />
            <Tile
              label={t("settings.evaluation.memory.comparison.coverage")}
              value={`${detail.coverage.scored} / ${detail.coverage.planned}`}
            />
          </div>

          <section className="space-y-2 rounded-panel border border-border/60 bg-settings-surface px-4 py-3">
            <h4 className="text-[13px] font-medium text-foreground">{t(BASE + "budget")}</h4>
            {budgetEntries.length === 0 ? (
              <p className="text-[12px] text-muted-foreground">{t(BASE + "budgetUnset")}</p>
            ) : (
              <dl className="grid gap-1 text-[12px] text-muted-foreground sm:grid-cols-3">
                {budgetEntries.map(([key, value]) => (
                  <div key={key}>
                    <dt className="inline">
                      {BUDGET_LABEL[key]
                        ? t(`settings.evaluation.memory.form.${BUDGET_LABEL[key]}`)
                        : key}
                      :{" "}
                    </dt>
                    <dd className="inline tabular-nums text-foreground">
                      {value === null || value === undefined
                        ? t(BASE + "budgetUnset")
                        : String(value)}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </section>

          <section className="space-y-2 rounded-panel border border-border/60 bg-settings-surface px-4 py-3">
            <header className="flex flex-wrap items-center gap-2">
              <h4 className="text-[13px] font-medium text-foreground">{t(BASE + "batches")}</h4>
              <span className="text-[11px] text-muted-foreground">
                {childRuns.length} ·{" "}
                {Object.entries(detail.progress.child_status_counts)
                  .map(([status, count]) => `${status} ×${count}`)
                  .join(" · ")}
              </span>
              <button
                type="button"
                className="ml-auto rounded border border-border px-2 py-0.5 text-[11px] text-foreground hover:bg-muted/40"
                aria-expanded={showBatches}
                onClick={() => setShowBatches((current) => !current)}
              >
                {showBatches ? t(BASE + "collapseBatches") : t(BASE + "expandBatches")}
              </button>
            </header>
            {childRuns.length === 0 ? (
              <p className="text-[12px] text-muted-foreground">{t(BASE + "attemptsEmpty")}</p>
            ) : null}
            {showBatches ? (
              <ul className="space-y-2">
                {childRuns.map((child: MemoryChildRun) => (
                  <li
                    key={child.id}
                    data-testid={`batch-${child.candidate_id}-${child.batch_index}`}
                    className="space-y-1.5 rounded-floating border border-border/60 px-2 py-2 text-[12px]"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-foreground">
                        {candidateNames.get(child.candidate_id) ?? child.candidate_id}
                      </span>
                      <span className="text-muted-foreground">
                        #{child.batch_index} · {child.scenario_ids.length}
                      </span>
                      <EvaluationStatusBadge status={child.status} />
                      <button
                        type="button"
                        className="ml-auto rounded border border-border px-2 py-0.5 text-[11px] text-foreground hover:bg-muted/40"
                        onClick={() => void loadAttempts(child.run_id)}
                      >
                        {t(BASE + "attempts")}
                      </button>
                    </div>
                    {attemptErrors[child.run_id] ? (
                      <p className="text-[11px] text-destructive">{attemptErrors[child.run_id]}</p>
                    ) : null}
                    {attempts[child.run_id] ? (
                      <ul className="space-y-1">
                        {attempts[child.run_id]
                          .filter(
                            (attempt) =>
                              !onlyFailed ||
                              attempt.verdict === "failed" ||
                              attempt.verdict === "review_required" ||
                              attempt.execution_status !== "succeeded",
                          )
                          .map((attempt) => (
                            <li key={attempt.id} className="flex flex-wrap items-center gap-2">
                              <span className="min-w-0 flex-1 break-all text-foreground">
                                {attempt.case_name || attempt.case_id}
                              </span>
                              <EvaluationStatusBadge status={attempt.verdict} kind="verdict" />
                              {onOpenReplay ? (
                                <button
                                  type="button"
                                  className="rounded border border-border px-2 py-0.5 text-[11px] text-foreground hover:bg-muted/40"
                                  onClick={() => onOpenReplay(attempt.id, null)}
                                >
                                  {t(BASE + "openReplay")}
                                </button>
                              ) : null}
                            </li>
                          ))}
                      </ul>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
            <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <input
                type="checkbox"
                checked={onlyFailed}
                onChange={(event) => setOnlyFailed(event.target.checked)}
              />
              {t(BASE + "onlyFailed")}
            </label>
          </section>

          <section className="space-y-3 rounded-panel border border-border/60 bg-settings-surface px-4 py-3">
            <h4 className="text-[13px] font-medium text-foreground">{t(BASE + "metrics")}</h4>
            {TRACK_ORDER.filter((track) => groupedMetrics[track]?.length).length === 0 ? (
              <p className="text-[12px] text-muted-foreground">{t(BASE + "metricsEmpty")}</p>
            ) : (
              TRACK_ORDER.map((track) => {
                const rows = groupedMetrics[track] ?? [];
                if (rows.length === 0) return null;
                return (
                  <div key={track} className="space-y-1">
                    <h5 className="text-[12px] font-medium text-muted-foreground">
                      {t(BASE + `track.${track}`)}
                    </h5>
                    <ul className="space-y-1">
                      {rows.map((metric) => (
                        <li
                          key={metric.id}
                          data-testid={`metric-${metric.id}`}
                          className="flex flex-wrap items-center gap-2 text-[12px]"
                        >
                          <span className="min-w-0 flex-1 break-all text-foreground">
                            {metric.id}
                          </span>
                          <MemoryMetricValue value={metric.value} unit={metric.unit} status={metric.status} />
                          <span className="text-[11px] text-muted-foreground">
                            {metric.coverage.scored}/{metric.coverage.planned}
                          </span>
                          {metric.reasons.length > 0 ? (
                            <span className="w-full text-[11px] text-amber-700 dark:text-amber-300">
                              {metric.reasons.map((reason) => memoryReasonText(reason, t)).join("、")}
                            </span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })
            )}
          </section>

          <section className="space-y-2 rounded-panel border border-border/60 bg-settings-surface px-4 py-3">
            <h4 className="text-[13px] font-medium text-foreground">{t(BASE + "calibration")}</h4>
            <EvaluationCalibration
              calibration={calibration}
              agreementUnavailableHint={t(BASE + "calibrationHint")}
            />
          </section>
        </>
      ) : null}
    </section>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-panel border border-border/60 bg-settings-surface px-4 py-3">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums tracking-tight text-foreground">{value}</p>
    </div>
  );
}
