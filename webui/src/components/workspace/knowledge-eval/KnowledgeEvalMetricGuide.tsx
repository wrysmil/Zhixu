import { useTranslation } from "react-i18next";
import { Info } from "lucide-react";

import type { KnowledgeMetricSpec } from "@/lib/knowledge-eval-api";

import { OVERVIEW_METRICS } from "./metrics";

interface KnowledgeEvalMetricGuideProps {
  specs: KnowledgeMetricSpec[];
}

/**
 * 指标口径区（首页必备）。
 *
 * 表格里的公式、分子、分母**一律来自后端** `GET /knowledge/metrics`，
 * 组件只渲染；i18n 只提供区块标题与表头，公式串不进语言文件。
 */
export function KnowledgeEvalMetricGuide({ specs }: KnowledgeEvalMetricGuideProps) {
  const { t } = useTranslation();

  const displayName = (metricId: string, fallback: string): string => {
    const descriptor = OVERVIEW_METRICS.find((item) => item.id === metricId);
    return descriptor ? t(descriptor.labelKey) : fallback;
  };

  return (
    <section data-testid="knowledge-eval-metric-guide" className="px-4 pb-4">
      <p className="text-[11.5px] leading-relaxed text-muted-foreground">
        {t("knowledge.eval.guide.subtitle")}
      </p>

      <div className="mt-3 overflow-x-auto">
        <table className="w-full border-collapse text-left text-[12px]">
          <thead>
            <tr className="border-b border-border/60 text-[10.5px] uppercase tracking-[0.06em] text-muted-foreground">
              <th className="py-2 pr-2 font-semibold">{t("knowledge.eval.guide.column.name")}</th>
              <th className="py-2 pr-2 font-semibold">
                <span className="inline-flex items-center gap-1">
                  {t("knowledge.eval.guide.column.formula")}
                  <span title={t("knowledge.eval.guide.formulaTooltip")}>
                    <Info className="h-3 w-3" aria-hidden />
                  </span>
                </span>
              </th>
              <th className="py-2 pr-2 font-semibold">{t("knowledge.eval.guide.column.numerator")}</th>
              <th className="py-2 pr-2 font-semibold">{t("knowledge.eval.guide.column.denominator")}</th>
              <th className="py-2 font-semibold">{t("knowledge.eval.guide.column.direction")}</th>
            </tr>
          </thead>
          <tbody>
            {specs.map((spec) => (
              <tr key={spec.metricId} className="border-t border-border/40 align-top">
                <td className="py-2 pr-2 font-medium text-foreground">
                  {displayName(spec.metricId, spec.label)}
                </td>
                <td className="py-2 pr-2 text-muted-foreground">{spec.formula}</td>
                <td className="py-2 pr-2 text-muted-foreground">{spec.numerator}</td>
                <td className="py-2 pr-2 text-muted-foreground">{spec.denominator}</td>
                <td className="py-2 text-muted-foreground">
                  {spec.direction === "lower_is_better"
                    ? t("knowledge.eval.guide.direction.lowerIsBetter")
                    : t("knowledge.eval.guide.direction.higherIsBetter")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="mt-3 flex items-start gap-1.5 text-[11px] leading-relaxed text-muted-foreground">
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        {t("knowledge.eval.hint.recallIsPoolRelative")}
      </p>
    </section>
  );
}
