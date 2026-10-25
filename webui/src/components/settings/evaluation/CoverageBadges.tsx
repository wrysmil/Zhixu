import { useTranslation } from "react-i18next";

import type { Coverage, ExecutionCoverage } from "@/lib/evaluation-api";

const COVERAGE_LABEL_KEY: Record<Coverage, string> = {
  complete: "settings.evaluation.observability.coverageComplete",
  partial: "settings.evaluation.observability.coveragePartial",
  unavailable: "settings.evaluation.observability.coverageUnavailable",
};

function coverageLabelKey(coverage: Coverage): string {
  return COVERAGE_LABEL_KEY[coverage] ?? COVERAGE_LABEL_KEY.unavailable;
}

/**
 * 采集覆盖度徽标：轨迹 / 身份 / 用量归属。
 * 状态同时给出文字（不依赖颜色），单条文案形如 "Usage attribution: Partial"。
 */
export function CoverageBadges({ coverage }: { coverage: ExecutionCoverage | null }) {
  const { t } = useTranslation();
  if (!coverage) return null;

  const items: Array<{ label: string; value: Coverage }> = [
    { label: t("settings.evaluation.observability.traceCoverage"), value: coverage.trace },
    { label: t("settings.evaluation.observability.identityCoverage"), value: coverage.identity },
    {
      label: t("settings.evaluation.observability.usageAttribution"),
      value: coverage.usage_attribution,
    },
  ];

  return (
    <ul
      aria-label={t("settings.evaluation.observability.coverageLabel")}
      className="flex flex-wrap items-center gap-2"
    >
      {items.map((item) => (
        <li
          key={item.label}
          className="rounded-full border border-border/60 bg-muted/30 px-2 py-0.5 text-[11px] text-foreground"
        >
          {`${item.label}: ${t(coverageLabelKey(item.value), { defaultValue: item.value })}`}
        </li>
      ))}
      {coverage.reasons.length > 0 ? (
        <li className="text-[11px] text-muted-foreground">
          {t("settings.evaluation.observability.coverageReasons", {
            reasons: coverage.reasons.join(", "),
            defaultValue: `Coverage notes: ${coverage.reasons.join(", ")}`,
          })}
        </li>
      ) : null}
    </ul>
  );
}
