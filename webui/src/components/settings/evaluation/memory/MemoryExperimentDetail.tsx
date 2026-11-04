import { useCallback, useEffect, useMemo, useState } from "react";
import type { TFunction } from "i18next";
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

/** 指标 id → 中文短名：其余语言走 defaultValue，与仓库里中文兜底的惯例一致。 */
export const METRIC_LABELS: Record<string, string> = {
  qa_accuracy: "记忆题正确率",
  qa_accuracy_scenario_macro: "记忆题正确率（按场景）",
  qa_accuracy_probe_micro: "记忆题正确率（按题）",
  qa_accuracy_category_macro: "记忆题正确率（按类型）",
  strict_scenario_pass_rate: "场景全通过率",
  grounded_memory_success: "有据可依的回答",
  fact_coverage: "关键事实覆盖",
  abstention_accuracy: "正确拒答",
  false_abstention_rate: "误拒答",
  evidence_recall_at_k: "证据召回",
  injection_recall: "注入召回",
  retrieval_precision_returned_at_k: "检索准确率",
  mrr: "首个命中排名",
  extraction_precision: "抽取准确率",
  extraction_recall: "抽取召回率",
  extraction_f1: "抽取综合",
  unsupported_memory_rate: "不该记却记了",
  negative_write_success: "负例不写入",
  duplicate_memory_rate: "重复记忆率",
  forget_behavior_success: "删除后不复用",
  forget_state_success: "删除后状态干净",
  stale_use_rate: "仍在用过期记忆",
  update_accuracy: "更新正确率",
  restart_accuracy: "重启后正确率",
  cross_session_accuracy: "跨会话正确率",
  repeat_stability: "重复稳定性",
  preference_compliance: "偏好遵循",
  annotation_coverage: "标注覆盖",
  scope_leak_rate: "跨账号串味",
  model_requests: "模型请求数",
  reader_tokens: "读取注入 token",
};

function metricLabel(metricId: string, t: TFunction): string {
  return t(`settings.evaluation.memory.metric.${metricId}`, {
    defaultValue: METRIC_LABELS[metricId] ?? metricId,
  });
}

function percentOf(value: number): string {
  return `${Math.round(value * 100)}%`;
}

/** 越低越好的指标：条形用琥珀色，一眼区分「涨了是坏事」。 */
const LOWER_IS_BETTER = new Set([
  "false_abstention_rate",
  "scope_leak_rate",
  "stale_use_rate",
  "duplicate_memory_rate",
  "unsupported_memory_rate",
]);

/** 每个轨道一个颜色，对比页和详情页共用同一套语义。 */
const TRACK_COLORS: Record<string, string> = {
  end_to_end: "bg-sky-500/75",
  retrieval: "bg-violet-500/75",
  extraction: "bg-emerald-500/75",
  agent: "bg-cyan-500/75",
};

/**
 * 「结果速读」：只说人看得懂的几件事，且只挑真正评分过的指标。
 * 顺序即展示优先级，最多 6 条，避免又变成一堵墙。
 */
const SUMMARY_SPECS: ReadonlyArray<{
  id: string;
  key: string;
  defaultValue: string;
  invert?: boolean;
}> = [
  {
    id: "qa_accuracy_scenario_macro",
    key: "summaryQa",
    defaultValue: "答对了 {{percent}} 的记忆题（{{scored}} 题参与评分）",
  },
  {
    id: "qa_accuracy",
    key: "summaryQa",
    defaultValue: "答对了 {{percent}} 的记忆题（{{scored}} 题参与评分）",
  },
  {
    id: "grounded_memory_success",
    key: "summaryGrounded",
    defaultValue: "{{percent}} 的回答确实基于已记录的记忆",
  },
  {
    id: "abstention_accuracy",
    key: "summaryAbstention",
    defaultValue: "问到没记过的事，{{percent}} 能正确说不知道",
  },
  {
    id: "false_abstention_rate",
    key: "summaryFalseAbstention",
    defaultValue: "{{percent}} 明明有答案却谎称不知道（越低越好）",
    invert: true,
  },
  {
    id: "evidence_recall_at_k",
    key: "summaryEvidenceRecall",
    defaultValue: "该引用的记忆里，{{percent}} 被检索找回",
  },
  {
    id: "forget_behavior_success",
    key: "summaryForget",
    defaultValue: "删掉的记忆在 {{percent}} 的场景里确实不再被复用",
  },
  {
    id: "stale_use_rate",
    key: "summaryStale",
    defaultValue: "{{percent}} 的回答仍在用已过期记忆（越低越好）",
    invert: true,
  },
  {
    id: "scope_leak_rate",
    key: "summaryLeak",
    defaultValue: "{{percent}} 出现跨账号串味（应为 0）",
    invert: true,
  },
];

function summaryLines(
  metrics: MemoryMetricsView["metrics"],
  t: TFunction,
): Array<{ id: string; text: string }> {
  const byId = new Map(metrics.map((metric) => [metric.id, metric]));
  const lines: Array<{ id: string; text: string }> = [];
  for (const spec of SUMMARY_SPECS) {
    if (lines.some((line) => line.id === spec.key)) continue;
    const metric = byId.get(spec.id);
    if (!metric || metric.status !== "scored" || metric.value === null) continue;
    lines.push({
      id: spec.key,
      text: t(`${BASE}detail.${spec.key}`, {
        defaultValue: spec.defaultValue,
        percent: percentOf(metric.value),
        scored: metric.coverage.scored,
      }),
    });
    if (lines.length >= 6) break;
  }
  return lines;
}

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
  const [showUnscored, setShowUnscored] = useState(false);
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

  const scored = useMemo(
    () => (metrics?.metrics ?? []).filter((metric) => metric.status === "scored" && metric.value !== null),
    [metrics],
  );
  const unscored = useMemo(
    () => (metrics?.metrics ?? []).filter((metric) => metric.status !== "scored" || metric.value === null),
    [metrics],
  );
  const groupByTrack = useCallback(
    (rows: MemoryMetricsView["metrics"]) => {
      const groups: Record<string, MemoryMetricsView["metrics"]> = {};
      for (const metric of rows) {
        const track = memoryTrackOfMetric(metric.id);
        (groups[track] ??= []).push(metric);
      }
      return groups;
    },
    [],
  );
  const groupedMetrics = useMemo(() => groupByTrack(scored), [groupByTrack, scored]);
  const groupedUnscored = useMemo(() => groupByTrack(unscored), [groupByTrack, unscored]);
  const summary = useMemo(() => summaryLines(metrics?.metrics ?? [], t), [metrics, t]);

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

          <section
            data-testid="detail-summary"
            className="space-y-2 rounded-panel border border-border/60 bg-settings-surface px-4 py-3"
          >
            <h4 className="text-[13px] font-medium text-foreground">
              {t(BASE + "summary", { defaultValue: "结果速读" })}
            </h4>
            {loading && !metrics ? (
              <p className="text-[12px] text-muted-foreground">
                {t("settings.evaluation.memory.common.loading")}
              </p>
            ) : summary.length === 0 ? (
              <p className="text-[12px] text-muted-foreground">
                {t(BASE + "summaryEmpty", {
                  defaultValue:
                    "还没有算出可读的结论：可能是测试集缺标注，或这一轨道本就不产出这些指标。展开下方详细指标能看到每项的原因。",
                })}
              </p>
            ) : (
              <ul className="space-y-1.5 text-[13px] text-foreground">
                {summary.map((line) => (
                  <li key={line.id} data-testid={`summary-${line.id}`} className="flex gap-2">
                    <span aria-hidden className="text-muted-foreground">
                      ·
                    </span>
                    <span>{line.text}</span>
                  </li>
                ))}
              </ul>
            )}
            {metrics ? (
              <p className="text-[11px] text-muted-foreground">
                {t(BASE + "summaryHint", {
                  defaultValue:
                    "想知道具体哪道题答错、模型当时看到了什么记忆：在「分批进度」点「展开批次」，再点批次右侧的「试验记录」，最后点具体场景的「查看回放」。",
                })}
              </p>
            ) : null}
          </section>

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
            <header className="flex flex-wrap items-center gap-2">
              <h4 className="text-[13px] font-medium text-foreground">{t(BASE + "metrics")}</h4>
              {unscored.length > 0 ? (
                <button
                  type="button"
                  className="ml-auto rounded border border-border px-2 py-0.5 text-[11px] text-foreground hover:bg-muted/40"
                  aria-expanded={showUnscored}
                  onClick={() => setShowUnscored((current) => !current)}
                >
                  {showUnscored
                    ? t(BASE + "collapseUnscored", {
                        defaultValue: "收起无法评估的 {{count}} 项",
                        count: unscored.length,
                      })
                    : t(BASE + "expandUnscored", {
                        defaultValue: "展开无法评估的 {{count}} 项",
                        count: unscored.length,
                      })}
                </button>
              ) : null}
            </header>
            {TRACK_ORDER.filter((track) => groupedMetrics[track]?.length).length === 0 ? (
              <p className="text-[12px] text-muted-foreground">
                {t(BASE + "metricsEmpty", { defaultValue: "暂无已评分指标" })}
              </p>
            ) : null}
            {TRACK_ORDER.map((track) => {
              const rows = groupedMetrics[track] ?? [];
              const hidden = groupedUnscored[track] ?? [];
              if (rows.length === 0 && (!showUnscored || hidden.length === 0)) return null;
              return (
                <div key={track} className="space-y-1">
                  <h5 className="flex items-center gap-1.5 text-[12px] font-medium text-muted-foreground">
                    <span
                      aria-hidden
                      className={cn("h-2 w-2 rounded-full", TRACK_COLORS[track] ?? "bg-sky-500/75")}
                    />
                    {t(BASE + `track.${track}`)}
                  </h5>
                  <ul className="space-y-1.5">
                    {[...(showUnscored ? hidden : []), ...rows].map((metric) => (
                      <li
                        key={metric.id}
                        data-testid={`metric-${metric.id}`}
                        className="space-y-1 text-[12px]"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="min-w-0 flex-1 text-foreground" title={metric.id}>
                            {metricLabel(metric.id, t)}
                            <span className="ml-1 text-[11px] text-muted-foreground">{metric.id}</span>
                          </span>
                          <MemoryMetricValue value={metric.value} unit={metric.unit} status={metric.status} />
                          <span className="text-[11px] text-muted-foreground">
                            {metric.coverage.scored}/{metric.coverage.planned}
                          </span>
                        </div>
                        {metric.unit === "ratio" && metric.value !== null ? (
                          <span
                            data-testid={`metric-bar-${metric.id}`}
                            className="flex h-2 w-full overflow-hidden rounded-full bg-muted/60"
                            role="img"
                            aria-label={`${metricLabel(metric.id, t)} ${percentOf(metric.value)}`}
                          >
                            <span
                              className={cn(
                                "block h-full rounded-full",
                                LOWER_IS_BETTER.has(metric.id)
                                  ? "bg-amber-500/80"
                                  : TRACK_COLORS[track] ?? "bg-sky-500/75",
                              )}
                              style={{ width: `${Math.min(100, Math.max(0, metric.value * 100))}%` }}
                            />
                          </span>
                        ) : null}
                        {metric.reasons.length > 0 ? (
                          <span className="block text-[11px] text-amber-700 dark:text-amber-300">
                            {metric.reasons.map((reason) => memoryReasonText(reason, t)).join("、")}
                          </span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
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
