import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

/**
 * 契约 §4：指标值展示。`value === null` 或非 `scored` 状态一律显示「未评分」/
 * 「不适用」，**不能**降级成 0 或 0%。零分只表示确实测得 0。
 */
export type MemoryMetricValueProps = {
  value: number | null;
  unit: string;
  status: string;
  className?: string;
};

function trimFraction(text: string): string {
  return text.includes(".") ? text.replace(/\.?0+$/, "") : text;
}

export function formatMemoryMetricValue(value: number, unit: string): string {
  switch (unit) {
    case "ratio":
      return `${trimFraction((value * 100).toFixed(1))}%`;
    case "ms":
      return `${trimFraction(value.toFixed(value < 10 ? 1 : 0))} ms`;
    case "token":
      return `${Math.round(value)} tokens`;
    case "bytes":
      return `${Math.round(value)} B`;
    case "currency":
      return value.toFixed(2);
    case "count":
      return String(Math.round(value));
    default:
      return unit ? `${trimFraction(String(value))} ${unit}` : trimFraction(String(value));
  }
}

export function MemoryMetricValue({ value, unit, status, className }: MemoryMetricValueProps) {
  const { t } = useTranslation();
  const assessable = status === "scored" && value !== null && Number.isFinite(value);
  if (!assessable) {
    const label =
      status === "not_applicable"
        ? t("settings.evaluation.memory.metric.notApplicable", { defaultValue: "不适用" })
        : t("settings.evaluation.memory.metric.notScored", { defaultValue: "未评分" });
    return (
      <span
        data-metric-status={status || "not_assessable"}
        className={cn("text-muted-foreground", className)}
      >
        {label}
      </span>
    );
  }
  return (
    <span
      data-metric-status="scored"
      title={`${formatMemoryMetricValue(value, unit)} (${unit})`}
      className={cn("tabular-nums text-foreground", className)}
    >
      {formatMemoryMetricValue(value, unit)}
    </span>
  );
}
