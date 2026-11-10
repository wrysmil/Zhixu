import type { KnowledgeEvalMetrics } from "@/lib/knowledge-eval-api";

/** 指标元数据：ID ↔ i18n 键的对应关系。公式串不在这里——它随后端下发。 */
export interface MetricDescriptor {
  id: string;
  /** 展示名（i18n）。 */
  labelKey: string;
}

/** 概览卡的五个主指标（与 plan 的「5 指标」一致）。 */
export const OVERVIEW_METRICS: MetricDescriptor[] = [
  { id: "precision_at_k", labelKey: "knowledge.eval.metric.precision" },
  { id: "recall_at_k", labelKey: "knowledge.eval.metric.recall" },
  { id: "hit_at_k", labelKey: "knowledge.eval.metric.hit" },
  { id: "mrr", labelKey: "knowledge.eval.metric.mrr" },
  { id: "ndcg_at_k", labelKey: "knowledge.eval.metric.ndcg" },
];

/** 逐题表展示的五个指标。 */
export const CASE_METRICS: MetricDescriptor[] = OVERVIEW_METRICS;

/**
 * 指标值 → 展示串。
 *
 * `null` 显示为「—」而不是 0：缺证据与 0 分在报告里必须能区分。
 */
export function formatMetricValue(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toFixed(3);
}

/** 从指标集合里取一个指标值。 */
export function metricValue(
  metrics: KnowledgeEvalMetrics,
  metricId: string,
): number | null {
  switch (metricId) {
    case "precision_at_k":
      return metrics.precisionAtK;
    case "recall_at_k":
      return metrics.recallAtK;
    case "hit_at_k":
      return metrics.hitAtK;
    case "mrr":
      return metrics.mrr;
    case "ndcg_at_k":
      return metrics.ndcgAtK;
    case "pool_coverage":
      return metrics.poolCoverage;
    case "unscored_rate":
      return metrics.unscoredRate;
    default:
      return null;
  }
}

/**
 * 逐题结果的 ID 是 `case_id::config_id` 复合形式——一页里一行是
 * 「一条查询 × 一个配置」，拆开才能分别显示。
 */
export function splitCaseId(caseId: string): { caseId: string; configId: string } {
  const index = caseId.indexOf("::");
  if (index < 0) return { caseId, configId: "" };
  return { caseId: caseId.slice(0, index), configId: caseId.slice(index + 2) };
}
