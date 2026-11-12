import { useTranslation } from "react-i18next";

import type { KnowledgeEvalReport, KnowledgeEvalCaseResult } from "@/lib/knowledge-eval-api";
import { cn } from "@/lib/utils";

import { CASE_METRICS, formatMetricValue, metricValue, splitCaseId } from "./metrics";

interface KnowledgeEvalCaseResultsProps {
  report: KnowledgeEvalReport | null;
  onOpenCase?: (result: KnowledgeEvalCaseResult) => void;
  loading: boolean;
  /** 用例 id → 查询原文：逐题表要显示问的是什么，而不是一串 UUID。 */
  queries?: Record<string, string>;
}

/**
 * 逐题结果表（每次测评都必须呈现）。
 *
 * 一行 = 一条查询 × 一个配置；未评的题显示「未评分」而不是 0 分——
 * 「算不出来」与「0 分」在报告里必须能区分。
 */
export function KnowledgeEvalCaseResults({
  report,
  loading,
  queries = {},
  onOpenCase,
}: KnowledgeEvalCaseResultsProps) {
  const { t } = useTranslation();

  return (
    <section
      data-testid="knowledge-eval-case-results"
      className="knowledge-panel mt-5"
    >
      <h2 className="text-base font-semibold text-foreground">
        {t("knowledge.eval.tab.caseResults")}
      </h2>

      {loading ? (
        <p className="py-4 text-sm text-muted-foreground">{t("knowledge.eval.loading")}</p>
      ) : !report || report.results.length === 0 ? (
        <p className="py-4 text-sm text-muted-foreground">
          {t("knowledge.eval.empty.caseResults")}
        </p>
      ) : (
        <div className="knowledge-table-scroll mt-4">
          <table className="knowledge-table min-w-[760px]">
            <thead>
              <tr className="border-b border-border/60 text-sm uppercase tracking-[0.06em] text-muted-foreground">
                <th className="py-2 pr-2 font-semibold">{t("knowledge.eval.result.query")}</th>
                <th className="py-2 pr-2 font-semibold">{t("knowledge.eval.result.config")}</th>
                {CASE_METRICS.map((metric) => (
                  <th key={metric.id} className="py-2 pr-2 font-semibold">
                    {t(metric.labelKey)}
                  </th>
                ))}
                <th className="py-2 font-semibold">{t("knowledge.eval.result.status")}</th>
              </tr>
            </thead>
            <tbody>
              {report.results.map((result) => {
                const { caseId, configId } = splitCaseId(result.caseId);
                const scored = result.status === "scored";
                return (
                  <tr
                    key={result.caseId}
                    data-testid={`knowledge-eval-case-${result.caseId}`}
                    className="border-t border-border/40 align-top transition-colors hover:bg-muted/30"
                  >
                    <td className="py-2 pr-2">
                      <button type="button" onClick={()=>onOpenCase?.(result)} className="block max-w-[320px] whitespace-normal text-left text-sm font-medium text-foreground underline-offset-4 hover:underline">
                        {queries[caseId] ?? caseId}
                      </button>
                      <span className="mt-0.5 inline-block rounded-full border border-border/70 px-1.5 py-0.5 text-sm text-muted-foreground">
                        {result.labelSource === "expect"
                          ? t("knowledge.eval.badge.labelSource.expect")
                          : t("knowledge.eval.badge.labelSource.judge")}
                      </span>
                    </td>
                    <td className="py-2 pr-2 font-mono text-sm text-muted-foreground">
                      {configId || "—"}
                    </td>
                    {scored ? (
                      CASE_METRICS.map((metric) => (
                        <td
                          key={metric.id}
                          className="py-2 pr-2 tabular-nums text-foreground"
                        >
                          {metricValue(result.metrics, metric.id)===null?t("knowledge.eval.result.notScored"):formatMetricValue(metricValue(result.metrics, metric.id))}
                        </td>
                      ))
                    ) : (
                      <td colSpan={CASE_METRICS.length} className="py-2 pr-2 text-muted-foreground">
                        {t("knowledge.eval.result.notScored")}
                      </td>
                    )}
                    <td className="py-2">
                      <span
                        className={cn(
                          "inline-flex items-center rounded-full border px-2 py-0.5 text-sm font-medium",
                          scored
                            ? "border-emerald-300/70 bg-emerald-50 text-emerald-900 dark:border-emerald-700/40 dark:bg-emerald-950/30 dark:text-emerald-200"
                            : result.status === "failed"
                              ? "border-destructive/40 bg-destructive/5 text-destructive"
                              : "border-amber-300/70 bg-amber-50 text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-100",
                        )}
                      >
                        {result.status === "scored"
                          ? t("knowledge.eval.status.scored")
                          : result.status === "failed"
                            ? t("knowledge.eval.status.failed")
                            : t("knowledge.eval.badge.notAssessable")}
                      </span>
                      {result.error ? (
                        <span className="mt-1 block max-w-[220px] break-words text-sm text-destructive">
                          {result.error}
                        </span>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
