import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

/** 评估状态 → 展示口径。运行状态与用例结论共用一套颜色语义。 */
export type EvaluationTone = "pass" | "fail" | "warn" | "info" | "muted";

const RUN_TONE: Record<string, EvaluationTone> = {
  queued: "muted",
  running: "info",
  cancelling: "warn",
  completed: "pass",
  cancelled: "muted",
  interrupted: "warn",
  timed_out: "warn",
};

const VERDICT_TONE: Record<string, EvaluationTone> = {
  passed: "pass",
  failed: "fail",
  review_required: "warn",
  not_evaluated: "muted",
};

const TONE_CLASS: Record<EvaluationTone, string> = {
  pass: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  fail: "border-destructive/30 bg-destructive/10 text-destructive",
  warn: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  info: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  muted: "border-border/50 bg-muted/40 text-muted-foreground",
};

export function evaluationTone(status: string): EvaluationTone {
  return RUN_TONE[status] ?? "muted";
}

/** 后端状态为 snake_case，i18n 词条为 camelCase（timedOut / reviewRequired / notEvaluated）。 */
export function evaluationStatusKey(status: string): string {
  return status.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase());
}

export function evaluationStatusLabel(status: string): string {
  const { t } = useTranslation();
  const key = `settings.evaluation.status.${evaluationStatusKey(status)}`;
  const fallback: Record<string, string> = {
    queued: "Queued",
    running: "Running",
    cancelling: "Cancelling",
    completed: "Completed",
    cancelled: "Cancelled",
    interrupted: "Interrupted",
    timed_out: "Timed out",
  };
  return t(key, { defaultValue: fallback[status] ?? status });
}

export function evaluationVerdictLabel(verdict: string): string {
  const { t } = useTranslation();
  const key = `settings.evaluation.status.${evaluationStatusKey(verdict)}`;
  const fallback: Record<string, string> = {
    passed: "Passed",
    failed: "Failed",
    review_required: "Needs review",
    not_evaluated: "Not evaluated",
  };
  return t(key, { defaultValue: fallback[verdict] ?? verdict });
}

export function EvaluationStatusBadge({
  status,
  kind = "run",
}: {
  status: string;
  kind?: "run" | "verdict";
}) {
  const tone = kind === "verdict" ? VERDICT_TONE[status] ?? "muted" : evaluationTone(status);
  const label = kind === "verdict" ? evaluationVerdictLabel(status) : evaluationStatusLabel(status);
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[11px] font-medium",
        TONE_CLASS[tone],
      )}
    >
      {label}
    </span>
  );
}
