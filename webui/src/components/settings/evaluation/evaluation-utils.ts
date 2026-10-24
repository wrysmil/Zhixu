import type { TFunction } from "i18next";

import { EvaluationApiError } from "@/lib/evaluation-api";

export function evaluationErrorText(error: unknown, t: TFunction): string {
  if (error instanceof EvaluationApiError) {
    if (error.code === "EVAL_OFFLINE") return t("settings.evaluation.error.offline");
    if (error.code === "EVAL_AUTH_FAILED") return t("settings.evaluation.error.auth");
  }
  return t("settings.evaluation.error.request");
}

export function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function formatPercent(rate: number | null | undefined): string {
  if (rate == null) return "—";
  return `${Math.round(rate * 100)}%`;
}
