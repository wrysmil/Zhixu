import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/workspace/EmptyState";
import type {
  KnowledgeEvalComparison,
  KnowledgeEvalRun,
} from "@/lib/knowledge-eval-api";
import { cn } from "@/lib/utils";

import { formatMetricValue, OVERVIEW_METRICS, splitCaseId } from "./metrics";

interface KnowledgeEvalCompareProps {
  runs: KnowledgeEvalRun[];
  baseRunId: string;
  candidateRunId: string;
  comparison: KnowledgeEvalComparison | null;
  busy: boolean;
  onChangeBase: (runId: string) => void;
  onChangeCandidate: (runId: string) => void;
  onCompare: () => void;
  /** 用例 id → 查询原文：退化清单显示问的是什么。 */
  queries?: Record<string, string>;
  runNames?: Record<string,string>;
  onOpenRegression?: (caseId:string) => void;
}

/**
 * 对比分区：逐指标差 + **退化查询清单**。
 *
 * 退化清单是这一页的重点——只看聚合数字会掩盖「哪条查询在候选配置下变差了」。
 */
export function KnowledgeEvalCompare({
  runs,
  baseRunId,
  candidateRunId,
  comparison,
  busy,
  onChangeBase,
  onChangeCandidate,
  onCompare,
  queries = {},
  runNames = {},
  onOpenRegression,
}: KnowledgeEvalCompareProps) {
  const { t } = useTranslation();

  const metricLabel = (metricId: string, fallback: string): string => {
    const descriptor = OVERVIEW_METRICS.find((item) => item.id === metricId);
    return descriptor ? t(descriptor.labelKey) : fallback;
  };

  return (
    <section data-testid="knowledge-eval-compare" className="knowledge-panel mt-5">
      <h2 className="text-base font-semibold text-foreground">
        {t("knowledge.eval.tab.compare")}
      </h2>

      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label className="flex min-w-[180px] flex-1 flex-col gap-1 text-sm font-medium text-muted-foreground">
          {t("knowledge.eval.compare.base")}
          <select
            aria-label={t("knowledge.eval.compare.base")}
            value={baseRunId}
            onChange={(event) => onChangeBase(event.target.value)}
            className="h-8 rounded-control border border-input bg-background px-2 text-sm"
          >
            <option value="">{t("knowledge.eval.compare.none")}</option>
            {runs.map((run) => (
              <option key={run.id} value={run.id}>
                {runNames[run.id] ?? run.id}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-w-[180px] flex-1 flex-col gap-1 text-sm font-medium text-muted-foreground">
          {t("knowledge.eval.compare.candidate")}
          <select
            aria-label={t("knowledge.eval.compare.candidate")}
            value={candidateRunId}
            onChange={(event) => onChangeCandidate(event.target.value)}
            className="h-8 rounded-control border border-input bg-background px-2 text-sm"
          >
            <option value="">{t("knowledge.eval.compare.none")}</option>
            {runs.map((run) => (
              <option key={run.id} value={run.id}>
                {runNames[run.id] ?? run.id}
              </option>
            ))}
          </select>
        </label>
        <Button
          type="button"
          size="sm"
          disabled={busy || !baseRunId || !candidateRunId || baseRunId===candidateRunId}
          onClick={onCompare}
          className="h-8 gap-1.5 rounded-full px-3.5 text-sm font-semibold"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
          {t("knowledge.eval.action.compare")}
        </Button>
      </div>

      {!comparison ? (
        <div className="mt-3">
          <EmptyState
            title={t("knowledge.eval.empty.compare")}
            description={t("knowledge.eval.compare.hint")}
          />
        </div>
      ) : (
        <div className="mt-3 flex flex-col gap-3">
          <div className="knowledge-table-scroll">
            <table className="knowledge-table">
              <thead>
                <tr className="border-b border-border/60 text-sm uppercase tracking-[0.06em] text-muted-foreground">
                  <th className="py-2 pr-2 font-semibold">{t("knowledge.eval.guide.column.name")}</th>
                  <th className="py-2 pr-2 font-semibold">{t("knowledge.eval.compare.base")}</th>
                  <th className="py-2 pr-2 font-semibold">{t("knowledge.eval.compare.candidate")}</th>
                  <th className="py-2 font-semibold">{t("knowledge.eval.compare.delta")}</th>
                </tr>
              </thead>
              <tbody>
                {comparison.metrics.map((row) => (
                  <tr key={`${row.labelSource ?? "all"}:${row.metricId}`} className="border-t border-border/40">
                    <td className="py-2 pr-2 font-medium text-foreground">
                      {metricLabel(row.metricId, row.label ?? row.metricId)}
                      {row.labelSource ? (
                        <span className="ml-1.5 rounded-full border border-border/70 px-1.5 py-0.5 text-sm text-muted-foreground">
                          {row.labelSource}
                        </span>
                      ) : null}
                    </td>
                    <td className="py-2 pr-2 tabular-nums text-muted-foreground">
                      {formatMetricValue(row.base)}
                    </td>
                    <td className="py-2 pr-2 tabular-nums text-muted-foreground">
                      {formatMetricValue(row.candidate)}
                    </td>
                    <td
                      className={cn(
                        "py-2 tabular-nums",
                        row.delta === null
                          ? "text-muted-foreground"
                          : (row.direction === "lower_is_better" ? row.delta > 0 : row.direction === "higher_is_better" ? row.delta < 0 : false)
                            ? "text-destructive"
                            : "text-emerald-600 dark:text-emerald-400",
                      )}
                    >
                      {formatMetricValue(row.delta)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div>
            <h3 className="pb-1.5 text-sm font-semibold text-foreground">
              {t("knowledge.eval.compare.regressions")}
              <span className="ml-1.5 rounded-full bg-muted/60 px-1.5 py-0.5 text-sm font-medium tabular-nums text-muted-foreground">
                {comparison.regressedCases.length}
              </span>
            </h3>
            {comparison.regressedCases.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {t("knowledge.eval.compare.noRegressions")}
              </p>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {comparison.regressedCases.map((item, index) => (
                  <li
                    key={`${item.caseId}:${item.configId ?? ""}:${item.metricId ?? ""}:${index}`}
                    className="flex flex-wrap items-center gap-2 rounded-control border border-destructive/25 bg-destructive/5 px-2.5 py-1.5 text-sm"
                  >
                    <span className="font-medium text-foreground">
                      {queries[splitCaseId(item.caseId).caseId] ?? item.caseId}
                    </span>
                    {item.configId ? (
                      <span className="font-mono text-sm text-muted-foreground">
                        {item.configId}
                      </span>
                    ) : null}
                    {item.metricId ? (
                      <span className="rounded-full bg-muted/60 px-1.5 py-0.5 text-sm text-muted-foreground">
                        {metricLabel(item.metricId, item.metricId)}
                      </span>
                    ) : null}
                    <p className="w-full text-sm">{item.reason}</p>
                    <Button variant="outline" disabled={!item.caseId.includes("::")&&!item.configId} onClick={()=>onOpenRegression?.(item.caseId.includes("::") ? item.caseId : item.caseId+"::"+item.configId)}>{t("knowledge.experience.viewRegression",{defaultValue:"查看退步题目"})}</Button>
                    {item.delta !== null && item.delta !== undefined ? (
                      <span className="tabular-nums text-destructive">
                        {formatMetricValue(item.delta)}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
