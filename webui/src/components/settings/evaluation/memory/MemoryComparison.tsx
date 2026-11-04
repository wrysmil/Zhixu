import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ArrowLeft, Download, RefreshCw } from "lucide-react";

import { MemoryMetricValue } from "@/components/settings/evaluation/memory/MemoryMetricValue";
import {
  evaluationErrorText,
  formatDateTime,
} from "@/components/settings/evaluation/evaluation-utils";
import { Button } from "@/components/ui/button";
import { SegmentedControl } from "@/components/ui/segmented-control";
import {
  memoryEvaluationApi,
  type MemoryComparisonDetail,
} from "@/lib/memory-evaluation-api";
import { cn } from "@/lib/utils";

const BASE = "settings.evaluation.memory.comparison.";

/** 契约 §8.5 第 4 条：独立 cluster 少于 20 个不显示区间。 */
export const MEMORY_MIN_CLUSTERS_FOR_INTERVAL = 20;

const PAIR_STATUSES = ["all", "regressed", "improved", "unchanged", "incomparable"] as const;
type PairFilter = (typeof PAIR_STATUSES)[number];

/**
 * 契约 §8.5 第 2 条：差值一律以百分点展示。
 *
 * 无证据（null）时说明「证据不足」，**不能**降级成 0 个百分点。
 */
export function memoryDeltaLabel(delta: number | null): string {
  if (delta === null) return "证据不足";
  const value = delta * 100;
  return `${value > 0 ? "+" : ""}${value.toFixed(1)} 个百分点`;
}

export function memoryRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function memoryList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function memoryNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function memoryText(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/** 后端 reason code → 大白话；不认识的码原样展示，不编造解释。 */
const REASON_KEYS: Record<string, string> = {
  BUDGET_EXCEEDED: "BUDGET_EXCEEDED",
  BUDGET_UNKNOWN: "BUDGET_UNKNOWN",
  CRITICAL: "CRITICAL",
  deletion_resurrection: "deletion_resurrection",
  EXPERIMENT_NOT_COMPLETED: "EXPERIMENT_NOT_COMPLETED",
  GROUNDED_UNKNOWN_CAUSE: "GROUNDED_UNKNOWN_CAUSE",
  INCOMPLETE_PAIRING: "INCOMPLETE_PAIRING",
  INSUFFICIENT_EVIDENCE: "INSUFFICIENT_EVIDENCE",
  INTERVAL_UNAVAILABLE: "INTERVAL_UNAVAILABLE",
  INTERVALS_SKIPPED: "INTERVALS_SKIPPED",
  MEANINGFUL_REGRESSION: "MEANINGFUL_REGRESSION",
  MEASUREMENT_INCOMPLETE: "MEASUREMENT_INCOMPLETE",
  MEASUREMENT_STATUS_INCOMPLETE: "MEASUREMENT_STATUS_INCOMPLETE",
  MINIMUM_EFFECT_PP_UNCONFIGURED: "MINIMUM_EFFECT_PP_UNCONFIGURED",
  MODEL_OR_EXTRACTOR_CHANGED: "MODEL_OR_EXTRACTOR_CHANGED",
  PRIMARY_GAIN_EXCEEDS_MINIMUM: "PRIMARY_GAIN_EXCEEDS_MINIMUM",
  RECALL_COVERAGE_INCOMPLETE: "RECALL_COVERAGE_INCOMPLETE",
  REGRESSION_EXCEEDS_TOLERANCE: "REGRESSION_EXCEEDS_TOLERANCE",
  REGRESSION_INTERVAL_UNAVAILABLE: "REGRESSION_INTERVAL_UNAVAILABLE",
  REQUIRED_FACTS_NOT_COVERED: "REQUIRED_FACTS_NOT_COVERED",
  REQUIRED_METRICS_MISSING: "REQUIRED_METRICS_MISSING",
  REQUIRED_METRIC_UNCALIBRATED: "REQUIRED_METRIC_UNCALIBRATED",
  RETRIED_ATTEMPTS_COUNTED: "RETRIED_ATTEMPTS_COUNTED",
  SEMANTIC_EQUIVALENCE_UNKNOWN: "SEMANTIC_EQUIVALENCE_UNKNOWN",
  SCOPE_LEAK: "SCOPE_LEAK",
  scope_leak: "scope_leak",
  TARGETED_SCOPE_NO_FULL_F1: "TARGETED_SCOPE_NO_FULL_F1",
  TARGETED_SCOPE_NO_FULL_PRECISION: "TARGETED_SCOPE_NO_FULL_PRECISION",
  TRIAL_COUNT_MISMATCH: "TRIAL_COUNT_MISMATCH",
  TRIAL_MISSING: "TRIAL_MISSING",
  UNCALIBRATED: "UNCALIBRATED",
  UNGRADED_ATTEMPTS_EXCLUDED: "UNGRADED_ATTEMPTS_EXCLUDED",
};

export function memoryReasonText(reason: string, t: TFunction): string {
  const [head, ...rest] = String(reason).split(":");
  const known = REASON_KEYS[head];
  const label = known
    ? t(`settings.evaluation.memory.reason.${known}`)
    : head;
  return rest.length > 0 ? `${label}（${rest.join(":")}）` : label;
}

/** 契约 §7：指标按轨道分组，不混成一个「万能记忆总分」。 */
export function memoryTrackOfMetric(metricId: string): string {
  if (
    metricId.startsWith("extraction_") ||
    metricId === "unsupported_memory_rate" ||
    metricId === "negative_write_success"
  ) {
    return "extraction";
  }
  if (
    metricId.startsWith("evidence_recall") ||
    metricId.startsWith("retrieval_precision") ||
    metricId === "mrr" ||
    metricId === "injection_recall"
  ) {
    return "retrieval";
  }
  if (
    metricId.startsWith("task_") ||
    metricId.startsWith("tool_") ||
    metricId === "annotation_coverage" ||
    metricId === "repeat_stability"
  ) {
    return "agent";
  }
  if (
    metricId === "qa_accuracy" ||
    metricId.includes("_accuracy") ||
    metricId.includes("_rate") ||
    metricId.includes("_success") ||
    metricId === "memory_lift"
  ) {
    return "end_to_end";
  }
  return "other";
}

const STATUS_CLASS: Record<string, string> = {
  improved: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  regressed: "border-destructive/30 bg-destructive/10 text-destructive",
  unchanged: "border-border/60 bg-muted/30 text-muted-foreground",
  incomparable: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
};

const DIRECTION_CLASS: Record<string, string> = {
  improved: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  regressed: "border-destructive/30 bg-destructive/10 text-destructive",
  inconclusive: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  incomparable: "border-border/60 bg-muted/30 text-muted-foreground",
};

const DECISION_CLASS: Record<string, string> = {
  adoptable: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  blocked: "border-destructive/30 bg-destructive/10 text-destructive font-medium",
  rejected: "border-destructive/30 bg-destructive/10 text-destructive",
  review_required: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  diagnostic_only: "border-border/60 bg-muted/30 text-muted-foreground",
  insufficient: "border-border/60 bg-muted/30 text-muted-foreground",
};

export type MemoryComparisonProps = {
  comparisonId: string;
  onBack?: () => void;
  /** 跳到该场景的回放：逐 probe 复核在那里提交（带 memory_ref）。 */
  onOpenReplay?: (scenarioId: string) => void;
  className?: string;
};

export function MemoryComparison({
  comparisonId,
  onBack,
  onOpenReplay,
  className,
}: MemoryComparisonProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<MemoryComparisonDetail | null>(null);
  const [pairs, setPairs] = useState<Array<Record<string, unknown>>>([]);
  const [nextKey, setNextKey] = useState<string | null>(null);
  const [filter, setFilter] = useState<PairFilter>("all");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (mode: "reset" | "more") => {
      if (mode === "more") setLoadingMore(true);
      else setLoading(true);
      try {
        const page = await memoryEvaluationApi.getComparison(comparisonId, {
          limit: 50,
          status: filter === "all" ? undefined : filter,
          after_key: mode === "more" ? (nextKey ?? undefined) : undefined,
        });
        setDetail(page);
        setPairs((current) => (mode === "more" ? [...current, ...page.data] : page.data));
        setNextKey(page.next_after_key);
        setError(null);
      } catch (failure) {
        setError(evaluationErrorText(failure, t));
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [comparisonId, filter, nextKey, t],
  );

  useEffect(() => {
    setPairs([]);
    setNextKey(null);
    void load("reset");
    // 只在比较对象或筛选变化时重取；翻页走 load("more")。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [comparisonId, filter]);

  const statistics = useMemo(() => memoryRecord(detail?.statistics), [detail]);
  const primary = memoryRecord(statistics.primary);
  const adoption = memoryRecord(statistics.adoption_decision);
  const gates = memoryRecord(statistics.gates);
  const critical = memoryList(gates.critical_violations).map(String);
  const direction = memoryText(statistics.statistical_direction) ?? "incomparable";
  const decision = memoryText(adoption.decision) ?? "";
  const interval = memoryRecord(detail?.intervals);
  const clusters = memoryNumber(interval.clusters);
  const intervalLow = memoryNumber(interval.low_pp);
  const intervalHigh = memoryNumber(interval.high_pp);
  const showInterval =
    clusters !== null && clusters >= MEMORY_MIN_CLUSTERS_FOR_INTERVAL &&
    intervalLow !== null && intervalHigh !== null;
  const reasons = memoryList(detail?.reasons).map(String);
  const coverage = memoryRecord(statistics.coverage);
  const scenesCoverage = memoryRecord(coverage.scenes);
  const trialsCoverage = memoryRecord(coverage.trials);
  const costSummary = memoryRecord(memoryRecord(statistics.cost).summary);
  const secondary = memoryList(statistics.secondary).map(memoryRecord);
  const categories = memoryList(statistics.category_macro).map(memoryRecord);
  const probeMicro = memoryRecord(statistics.probe_micro);
  const changedFields = detail?.changed_fields ?? [];
  const comparable = memoryText(statistics.status) === "completed" && critical.length === 0;

  const sortedPairs = useMemo(() => {
    const rank: Record<string, number> = { regressed: 0, incomparable: 1, improved: 2, unchanged: 3 };
    return [...pairs].sort(
      (left, right) =>
        (rank[String(left.status)] ?? 9) - (rank[String(right.status)] ?? 9),
    );
  }, [pairs]);

  /** 主指标 + 次要指标画成基线/候选双条，直观看差距。 */
  const chartRows = useMemo(() => {
    const rows: Array<{
      key: string;
      metricId: string;
      baseline: number | null;
      candidate: number | null;
    }> = [];
    const primaryId = memoryText(primary.metric_id);
    if (primaryId) {
      rows.push({
        key: `primary:${primaryId}`,
        metricId: primaryId,
        baseline: memoryNumber(primary.baseline),
        candidate: memoryNumber(primary.candidate),
      });
    }
    for (const row of secondary) {
      const metricId = String(row.metric_id);
      rows.push({
        key: metricId,
        metricId,
        baseline: memoryNumber(row.baseline),
        candidate: memoryNumber(row.candidate),
      });
    }
    return rows;
  }, [primary, secondary]);

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
        <span className="text-[12px] text-muted-foreground">{comparisonId.slice(0, 8)}</span>
        <div className="ml-auto flex items-center gap-1.5">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => void memoryEvaluationApi.downloadComparisonReport(comparisonId)}
          >
            <Download className="h-3.5 w-3.5" aria-hidden />
            {t(BASE + "downloadReport")}
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={() => void load("reset")}>
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            {t("settings.evaluation.memory.common.refresh")}
          </Button>
        </div>
      </header>

      {error ? (
        <p role="alert" className="rounded-floating border border-destructive/20 bg-destructive/5 px-3 py-2 text-[12px] text-destructive">
          {error}
        </p>
      ) : null}
      {loading && !detail ? (
        <p className="text-[13px] text-muted-foreground">
          {t("settings.evaluation.memory.common.loading")}
        </p>
      ) : null}

      {detail ? (
        <>
          <p data-testid="comparison-snapshot" className="text-[11px] text-muted-foreground">
            {t(BASE + "snapshot", { created: formatDateTime(detail.created_at) })}
          </p>

          <div className="grid gap-3 lg:grid-cols-2">
            <section className="rounded-panel border border-border/60 bg-settings-surface px-4 py-3">
              <h4 className="text-[13px] font-medium text-foreground">{t(BASE + "fixedConditions")}</h4>
              <dl className="mt-2 grid gap-1 text-[12px] text-muted-foreground sm:grid-cols-2">
                <div>
                  <dt className="inline">{t(BASE + "baseline")}: </dt>
                  <dd className="inline text-foreground">
                    {detail.baseline_experiment_id.slice(0, 8)} · {detail.baseline_candidate_id}
                  </dd>
                </div>
                <div>
                  <dt className="inline">{t(BASE + "candidate")}: </dt>
                  <dd className="inline text-foreground">
                    {detail.candidate_experiment_id.slice(0, 8)} · {detail.candidate_candidate_id}
                  </dd>
                </div>
                <div>
                  <dt className="inline">{t(BASE + "scenarioMacro")}: </dt>
                  <dd className="inline text-foreground">
                    {memoryText(primary.metric_id) ?? t(BASE + "unavailable")}
                  </dd>
                </div>
                <div>
                  <dt className="inline">{t(BASE + "coverage")}: </dt>
                  <dd className="inline text-foreground">
                    {memoryNumber(scenesCoverage.paired) ?? 0} /{" "}
                    {memoryNumber(scenesCoverage.planned) ?? 0}
                  </dd>
                </div>
              </dl>
              <p
                data-testid="comparison-comparable"
                className={cn(
                  "mt-2 inline-flex rounded-full border px-2 py-0.5 text-[11px]",
                  comparable
                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                    : "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
                )}
              >
                {comparable ? t("settings.evaluation.memory.list.measurementComplete") : t(BASE + "notComparable")}
              </p>
            </section>

            <section className="rounded-panel border border-border/60 bg-settings-surface px-4 py-3">
              <h4 className="text-[13px] font-medium text-foreground">{t(BASE + "changedFields")}</h4>
              {changedFields.length === 0 ? (
                <p className="mt-2 text-[12px] text-muted-foreground">{t(BASE + "changedFieldsEmpty")}</p>
              ) : (
                <ul className="mt-2 space-y-0.5 text-[12px] text-foreground">
                  {changedFields.map((field) => (
                    <li key={field} className="break-all">
                      {field}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>

          {chartRows.length > 0 ? (
            <section
              data-testid="comparison-chart"
              className="space-y-2.5 rounded-panel border border-border/60 bg-settings-surface px-4 py-3"
            >
              <header className="flex flex-wrap items-center gap-3">
                <h4 className="text-[13px] font-medium text-foreground">
                  {t(BASE + "metricChart", { defaultValue: "指标对比" })}
                </h4>
                <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <span className="h-2 w-2 rounded-full bg-slate-400/70" aria-hidden />
                  {t(BASE + "baseline")}
                </span>
                <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <span className="h-2 w-2 rounded-full bg-sky-500/80" aria-hidden />
                  {t(BASE + "candidate")}
                </span>
              </header>
              <ul className="space-y-2">
                {chartRows.map((row) => (
                  <li key={row.key} className="space-y-1">
                    <div className="flex items-center gap-2 text-[12px]">
                      <span className="min-w-0 flex-1 truncate text-foreground" title={row.metricId}>
                        {row.metricId}
                      </span>
                      <span className="tabular-nums text-muted-foreground">
                        {row.baseline === null ? "—" : `${Math.round(row.baseline * 100)}%`}
                      </span>
                      <span aria-hidden className="text-muted-foreground">
                        →
                      </span>
                      <span className="tabular-nums font-medium text-foreground">
                        {row.candidate === null ? "—" : `${Math.round(row.candidate * 100)}%`}
                      </span>
                    </div>
                    <div className="space-y-0.5">
                      <ComparisonBar value={row.baseline} className="bg-slate-400/70" />
                      <ComparisonBar value={row.candidate} className="bg-sky-500/80" />
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {critical.length > 0 ? (
            <p
              data-testid="comparison-critical"
              role="alert"
              className="rounded-floating border border-destructive/30 bg-destructive/10 px-3 py-2 text-[12px] font-medium text-destructive"
            >
              {t(BASE + "critical", {
                items: critical.map((item) => memoryReasonText(item, t)).join("、"),
              })}
            </p>
          ) : null}

          <div className="grid gap-3 lg:grid-cols-2">
            <section
              data-testid="comparison-direction"
              className="space-y-2 rounded-panel border border-border/60 bg-settings-surface px-4 py-3"
            >
              <h4 className="text-[13px] font-medium text-foreground">{t(BASE + "direction")}</h4>
              <span
                className={cn(
                  "inline-flex rounded-full border px-2 py-0.5 text-[11px]",
                  DIRECTION_CLASS[direction] ?? DIRECTION_CLASS.incomparable,
                )}
              >
                {t(BASE + `direction.${direction}`, { defaultValue: direction })}
              </span>
              <p className="text-[13px] text-foreground">
                <MemoryMetricValue
                  value={memoryNumber(primary.baseline)}
                  unit="ratio"
                  status={memoryNumber(primary.baseline) === null ? "not_assessable" : "scored"}
                />
                {" → "}
                <MemoryMetricValue
                  value={memoryNumber(primary.candidate)}
                  unit="ratio"
                  status={memoryNumber(primary.candidate) === null ? "not_assessable" : "scored"}
                />
                <span className="ml-2 text-[12px] text-muted-foreground">
                  {memoryDeltaLabel(memoryNumber(primary.delta))}
                </span>
              </p>
              <p data-testid="comparison-interval" className="text-[12px] text-muted-foreground">
                {showInterval
                  ? `${t(BASE + "interval")}: ${memoryDeltaLabel(intervalLow! / 100)} — ${memoryDeltaLabel(intervalHigh! / 100)}`
                  : t(BASE + "clustersInsufficient", { min: MEMORY_MIN_CLUSTERS_FOR_INTERVAL })}
              </p>
              {detail.intervals && memoryText(interval.version) ? (
                <p className="text-[11px] text-muted-foreground">
                  {t(BASE + "bootstrap", {
                    version: memoryText(interval.version),
                    seed: memoryNumber(interval.seed) ?? 42,
                    rounds: memoryNumber(interval.rounds) ?? 0,
                    clusters: clusters ?? 0,
                  })}
                </p>
              ) : null}
            </section>

            <section
              data-testid="comparison-adoption"
              className="space-y-2 rounded-panel border border-border/60 bg-settings-surface px-4 py-3"
            >
              <h4 className="text-[13px] font-medium text-foreground">{t(BASE + "adoption")}</h4>
              <span
                className={cn(
                  "inline-flex rounded-full border px-2 py-0.5 text-[11px]",
                  DECISION_CLASS[decision] ?? DECISION_CLASS.insufficient,
                )}
              >
                {decision
                  ? t(BASE + `adoption.${decision}`, { defaultValue: decision })
                  : t(BASE + "adoption.insufficient")}
              </span>
              {memoryList(adoption.reasons).length > 0 ? (
                <ul className="space-y-0.5 text-[12px] text-muted-foreground">
                  {memoryList(adoption.reasons)
                    .map(String)
                    .map((reason) => (
                      <li key={reason}>{memoryReasonText(reason, t)}</li>
                    ))}
                </ul>
              ) : null}
            </section>
          </div>

          <section className="space-y-2 rounded-panel border border-border/60 bg-settings-surface px-4 py-3">
            <h4 className="text-[13px] font-medium text-foreground">{t(BASE + "overall")}</h4>
            <dl className="grid gap-1 text-[12px] text-muted-foreground sm:grid-cols-3">
              <div>
                <dt className="inline">{t(BASE + "scenarioMacro")}: </dt>
                <dd className="inline text-foreground">
                  {memoryDeltaLabel(memoryNumber(memoryRecord(statistics.scenario_macro).delta))}
                </dd>
              </div>
              <div>
                <dt className="inline">{t(BASE + "probeMicro")}: </dt>
                <dd className="inline text-foreground">
                  {memoryDeltaLabel(memoryNumber(probeMicro.delta_pp) === null ? null : memoryNumber(probeMicro.delta_pp)! / 100)}
                </dd>
              </div>
              <div>
                <dt className="inline">{t("settings.evaluation.memory.list.column.coverage")}: </dt>
                <dd className="inline text-foreground">
                  {memoryNumber(memoryRecord(memoryRecord(trialsCoverage.baseline).graded)) ?? 0}/
                  {memoryNumber(memoryRecord(trialsCoverage.baseline).planned) ?? 0}
                  {" · "}
                  {memoryNumber(memoryRecord(trialsCoverage.candidate).graded) ?? 0}/
                  {memoryNumber(memoryRecord(trialsCoverage.candidate).planned) ?? 0}
                </dd>
              </div>
              <div>
                <dt className="inline">cost_ratio: </dt>
                <dd className="inline text-foreground">
                  {memoryNumber(costSummary.cost_ratio) === null
                    ? t(BASE + "unavailable")
                    : memoryNumber(costSummary.cost_ratio)!.toFixed(2)}
                </dd>
              </div>
              <div>
                <dt className="inline">p95_latency_ratio: </dt>
                <dd className="inline text-foreground">
                  {memoryNumber(costSummary.p95_latency_ratio) === null
                    ? t(BASE + "unavailable")
                    : memoryNumber(costSummary.p95_latency_ratio)!.toFixed(2)}
                </dd>
              </div>
            </dl>
            {reasons.length > 0 ? (
              <ul className="space-y-0.5 text-[11px] text-muted-foreground">
                {reasons.map((reason) => (
                  <li key={reason}>{memoryReasonText(reason, t)}</li>
                ))}
              </ul>
            ) : null}
          </section>

          {categories.length > 0 ? (
            <section className="space-y-2 rounded-panel border border-border/60 bg-settings-surface px-4 py-3">
              <h4 className="text-[13px] font-medium text-foreground">
                {t("settings.evaluation.memory.detail.track.other")}
              </h4>
              <ul className="space-y-1 text-[12px]">
                {categories.map((row) => {
                  const delta = memoryNumber(row.delta_pp);
                  return (
                    <li key={String(row.category)} className="flex flex-wrap items-center gap-2">
                      <span className="min-w-0 flex-1 break-all text-foreground">
                        {String(row.category)}
                      </span>
                      <span className="text-muted-foreground">
                        {memoryNumber(row.scenes) ?? 0} {t("settings.evaluation.memory.detail.attemptCase")}
                      </span>
                      <span className="tabular-nums text-muted-foreground">
                        {memoryDeltaLabel(delta === null ? null : delta / 100)}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </section>
          ) : null}

          <section className="space-y-2 rounded-panel border border-border/60 bg-settings-surface px-4 py-3">
            <h4 className="text-[13px] font-medium text-foreground">{t(BASE + "secondary")}</h4>
            {secondary.length === 0 ? (
              <p className="text-[12px] text-muted-foreground">{t(BASE + "unavailable")}</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-left text-[12px]">
                  <thead className="text-[11px] text-muted-foreground">
                    <tr>
                      <th className="py-1 pr-2 font-medium">{t(BASE + "scenarioMacro")}</th>
                      <th className="py-1 pr-2 font-medium">{t(BASE + "baseline")}</th>
                      <th className="py-1 pr-2 font-medium">{t(BASE + "candidate")}</th>
                      <th className="py-1 pr-2 font-medium">{t(BASE + "interval")}</th>
                      <th className="py-1 font-medium">{t(BASE + "direction")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {secondary.map((row) => {
                      const metricId = String(row.metric_id);
                      const lowerIsBetter = memoryText(row.direction) === "lower_is_better";
                      const improvement = memoryNumber(row.improvement_pp);
                      const missing =
                        memoryNumber(row.baseline) === null && memoryNumber(row.candidate) === null;
                      return (
                        <tr key={metricId} className="border-t border-border/50 align-top">
                          <td className="py-1 pr-2">
                            <span className="text-foreground">{metricId}</span>
                            <span className="ml-2 text-[11px] text-muted-foreground">
                              {lowerIsBetter ? t(BASE + "lowerIsBetter") : t(BASE + "upperIsBetter")}
                            </span>
                            {missing ? (
                              <span className="ml-2 text-[11px] text-amber-700 dark:text-amber-300">
                                {memoryReasonText("TARGETED_SCOPE_NO_FULL_PRECISION", t)}
                              </span>
                            ) : null}
                          </td>
                          <td className="py-1 pr-2 tabular-nums text-muted-foreground">
                            {memoryNumber(row.baseline) === null ? t(BASE + "unavailable") : memoryNumber(row.baseline)!.toFixed(3)}
                          </td>
                          <td className="py-1 pr-2 tabular-nums text-muted-foreground">
                            {memoryNumber(row.candidate) === null ? t(BASE + "unavailable") : memoryNumber(row.candidate)!.toFixed(3)}
                          </td>
                          <td className="py-1 pr-2 tabular-nums text-muted-foreground">
                            {memoryNumber(row.delta_pp) === null
                              ? t(BASE + "intervalMissing")
                              : memoryDeltaLabel(memoryNumber(row.delta_pp)! / 100)}
                          </td>
                          <td className="py-1">
                            <span className="text-foreground">
                              {improvement === null
                                ? t(BASE + "unavailable")
                                : improvement > 0
                                  ? t(BASE + "status.improved")
                                  : improvement < 0
                                    ? t(BASE + "status.regressed")
                                    : t(BASE + "status.unchanged")}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="space-y-2">
            <header className="flex flex-wrap items-center gap-2">
              <h4 className="text-[13px] font-medium text-foreground">{t(BASE + "cases")}</h4>
              <SegmentedControl
                value={filter}
                onChange={(next) => setFilter(next)}
                ariaLabel={t(BASE + "cases")}
                options={PAIR_STATUSES.map((value) => ({
                  value,
                  label: t(BASE + `filter.${value}`),
                }))}
              />
            </header>
            {sortedPairs.length === 0 ? (
              <p className="text-[12px] text-muted-foreground">{t(BASE + "casesEmpty")}</p>
            ) : (
              <ul className="space-y-2">
                {sortedPairs.map((row) => {
                  const scenarioId = String(row.scenario_id ?? "");
                  const status = String(row.status ?? "incomparable");
                  const probes = memoryList(row.probe_changes).map(memoryRecord);
                  const rowReasons = memoryList(row.reasons).map(String);
                  return (
                    <li
                      key={`${scenarioId}-${String(row.revision ?? "")}`}
                      data-testid={`comparison-pair-${scenarioId}`}
                      className="space-y-1.5 rounded-floating border border-border/60 bg-settings-surface px-3 py-2"
                    >
                      <div className="flex flex-wrap items-center gap-2 text-[13px]">
                        <span className="font-medium text-foreground">{scenarioId}</span>
                        <span
                          className={cn(
                            "rounded-full border px-2 py-0.5 text-[11px]",
                            STATUS_CLASS[status] ?? STATUS_CLASS.incomparable,
                          )}
                        >
                          {t(BASE + `status.${status}`, { defaultValue: status })}
                        </span>
                        {memoryText(row.category) ? (
                          <span className="text-[11px] text-muted-foreground">{memoryText(row.category)}</span>
                        ) : null}
                        <span className="ml-auto tabular-nums text-[12px] text-foreground">
                          {memoryDeltaLabel(memoryNumber(row.delta))}
                        </span>
                        {onOpenReplay ? (
                          <button
                            type="button"
                            className="rounded border border-border px-2 py-0.5 text-[11px] text-foreground hover:bg-muted/40"
                            onClick={() => onOpenReplay(scenarioId)}
                          >
                            {t(BASE + "reviewLink")}
                          </button>
                        ) : null}
                      </div>
                      <p className="text-[12px] text-muted-foreground">
                        {t(BASE + "baseline")}:{" "}
                        <MemoryMetricValue
                          value={memoryNumber(row.baseline)}
                          unit="ratio"
                          status={memoryNumber(row.baseline) === null ? "not_assessable" : "scored"}
                        />
                        {" · "}
                        {t(BASE + "candidate")}:{" "}
                        <MemoryMetricValue
                          value={memoryNumber(row.candidate)}
                          unit="ratio"
                          status={memoryNumber(row.candidate) === null ? "not_assessable" : "scored"}
                        />
                        {` · ${t("settings.evaluation.memory.detail.plannedTrials")}: ${memoryNumber(row.baseline_trials) ?? 0}/${memoryNumber(row.candidate_trials) ?? 0}`}
                      </p>
                      {probes.length > 0 ? (
                        <ul className="space-y-0.5 text-[11px] text-muted-foreground">
                          {probes.map((probe) => (
                            <li key={String(probe.probe_id)}>
                              {t(BASE + "probeChange", { probe: String(probe.probe_id) })}:{" "}
                              <MemoryMetricValue
                                value={memoryNumber(probe.baseline)}
                                unit="ratio"
                                status={memoryNumber(probe.baseline) === null ? "not_assessable" : "scored"}
                              />
                              {" → "}
                              <MemoryMetricValue
                                value={memoryNumber(probe.candidate)}
                                unit="ratio"
                                status={memoryNumber(probe.candidate) === null ? "not_assessable" : "scored"}
                              />
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      {rowReasons.length > 0 ? (
                        <p className="text-[11px] text-amber-700 dark:text-amber-300">
                          {t(BASE + "reasons")}: {rowReasons.map((reason) => memoryReasonText(reason, t)).join("、")}
                        </p>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
            <div className="flex flex-wrap items-center gap-2">
              {nextKey ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={loadingMore}
                  onClick={() => void load("more")}
                >
                  {loadingMore ? t("settings.evaluation.memory.common.loading") : t(BASE + "loadMore")}
                </Button>
              ) : null}
              <p className="text-[11px] text-muted-foreground">{t(BASE + "reviewHint")}</p>
            </div>
          </section>
        </>
      ) : null}
    </section>
  );
}

function ComparisonBar({ value, className }: { value: number | null; className: string }) {
  return (
    <span className="flex h-1.5 w-full overflow-hidden rounded-full bg-muted/50">
      {value === null ? null : (
        <span
          className={cn("block h-full rounded-full", className)}
          style={{ width: `${Math.min(100, Math.max(0, value * 100))}%` }}
        />
      )}
    </span>
  );
}
