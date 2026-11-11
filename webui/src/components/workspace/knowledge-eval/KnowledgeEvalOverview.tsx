import { useTranslation } from "react-i18next";
import { AlertTriangle } from "lucide-react";

import type {
  KnowledgeEvalMetricGroup,
  KnowledgeMetricSpec,
} from "@/lib/knowledge-eval-api";
import { cn } from "@/lib/utils";

import {
  formatMetricValue,
  metricValue,
  OVERVIEW_METRICS,
} from "./metrics";

interface KnowledgeEvalOverviewProps {
  groups: KnowledgeEvalMetricGroup[];
  /** 降级原因：非空即「降级运行」，与正常运行的指标不得直接对比。 */
  degraded: string[];
  specs: KnowledgeMetricSpec[];
  loading: boolean;
}

/**
 * 概览卡区（聚合段）。
 *
 * 每组保留五项指标，公式在展开帮助中呈现——公式只来自后端 `GET /knowledge/metrics`，
 * 组件里不内置任何公式文本。缺证据的指标显示「—」，不补 0。
 */
export function KnowledgeEvalOverview({
  groups,
  degraded,
  specs,
  loading,
}: KnowledgeEvalOverviewProps) {
  const { t } = useTranslation();

  if (loading) {
    return (
      <section data-testid="knowledge-eval-overview" className="mt-4">
        <p className="text-sm text-muted-foreground">{t("knowledge.eval.loading")}</p>
      </section>
    );
  }

  return (
    <section data-testid="knowledge-eval-overview" className="mt-4 flex flex-col gap-3">
      {degraded.length > 0 ? (
        <div
          data-testid="knowledge-eval-degraded-badge"
          role="status"
          className="flex items-start gap-2 rounded-control border border-amber-300/70 bg-amber-50/80 px-3 py-2 text-sm leading-relaxed text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-100"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-300" aria-hidden />
          <span>
            <span className="font-semibold">{t("knowledge.eval.badge.degraded")}</span>
            {" · "}
            {degraded.join(", ")}
            <span className="mt-0.5 block opacity-80">
              {t("knowledge.eval.hint.degradedNotComparable")}
            </span>
          </span>
        </div>
      ) : null}

      {groups.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("knowledge.eval.empty.caseResults")}</p>
      ) : null}

      {groups.map((group) => (
        <div key={group.labelSource} className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <span className="rounded-full border border-border/70 bg-background/70 px-2 py-0.5 font-medium text-foreground">
              {group.labelSource === "expect"
                ? t("knowledge.eval.badge.labelSource.expect")
                : group.labelSource === "judge"
                  ? t("knowledge.eval.badge.labelSource.judge")
                  : group.labelSource}
            </span>
            <span className="tabular-nums">
              {t("knowledge.eval.result.scoredCount", { count: group.scoredQueries })}
            </span>
            <span className="tabular-nums">
              {t("knowledge.eval.result.unscoredCount", {
                count: Math.max(0, group.totalQueries - group.scoredQueries),
              })}
            </span>
            {group.metrics.unscoredRate !== null ? (
              <span className="rounded-full bg-muted/60 px-2 py-0.5 tabular-nums">
                {t("knowledge.eval.metric.unscoredRate")}:{" "}
                {formatMetricValue(group.metrics.unscoredRate)}
              </span>
            ) : null}
          </div>

          <div className="knowledge-panel grid grid-cols-2 gap-5 sm:grid-cols-3 xl:grid-cols-5">
            {OVERVIEW_METRICS.map((descriptor) => {
              const value = metricValue(group.metrics, descriptor.id);
              const spec = specs.find((item) => item.metricId === descriptor.id);
              return (
                <div
                  key={descriptor.id}
                  data-testid={`knowledge-eval-metric-${descriptor.id}`}
                  className="flex min-w-0 flex-col gap-2"
                >
                  <span className="text-sm text-muted-foreground">
                    {t(descriptor.labelKey)}
                  </span>
                  <span
                    className={cn(
                      "text-2xl font-semibold tabular-nums text-foreground",
                      value === null && "text-muted-foreground",
                    )}
                  >
                    {formatMetricValue(value)}
                  </span>
                  {spec?.formula ? (<details className="text-sm text-muted-foreground"><summary className="cursor-pointer">{t("knowledge.experience.formula",{defaultValue:"公式与解释"})}</summary>
                    <span
                      data-testid={`knowledge-eval-formula-${descriptor.id}`}
                      title={spec.description}
                      className="mt-2 block text-sm leading-relaxed text-muted-foreground"
                    >
                      {spec.formula}<br/>{spec.description}
                    </span></details>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </section>
  );
}
