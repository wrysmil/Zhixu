import { useTranslation } from "react-i18next";

import type { DimensionStatus } from "@/lib/evaluation-api";
import { cn } from "@/lib/utils";

export type ReviewVerdict = "passed" | "failed" | "review_required" | "not_evaluated";

export type ReviewItem = {
  attempt_id: string;
  case_id: string;
  case_name?: string | null;
  /** 自动判分结论：始终保留，人工结论只做追加。 */
  automatic_verdict: ReviewVerdict;
  automatic_reason?: string | null;
  dimensions?: Record<string, DimensionStatus>;
  manual_verdict?: ReviewVerdict | null;
  manual_note?: string | null;
  manual_reviewer?: string | null;
  manual_reviewed_at?: string | null;
};

export type EvaluationReviewsProps = {
  reviews: ReviewItem[];
  busy?: boolean;
  /** 提交人工结论；自动结论不会被这条路径改写。 */
  onSubmitReview?: (attemptId: string, verdict: ReviewVerdict) => void;
  className?: string;
};

const VERDICT_KEY: Record<ReviewVerdict, string> = {
  passed: "settings.evaluation.reliability.verdicts.passed",
  failed: "settings.evaluation.reliability.verdicts.failed",
  review_required: "settings.evaluation.reliability.verdicts.reviewRequired",
  not_evaluated: "settings.evaluation.reliability.verdicts.notEvaluated",
};

export function EvaluationReviews({
  reviews,
  busy = false,
  onSubmitReview,
  className,
}: EvaluationReviewsProps) {
  const { t } = useTranslation();
  const base = "settings.evaluation.reliability.reviews.";

  return (
    <section
      aria-label={t(base + "title")}
      className={cn("space-y-3 rounded-floating border border-border/60 bg-settings-surface p-3", className)}
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-[13px] font-medium text-foreground">{t(base + "title")}</h3>
      </header>

      {reviews.length === 0 ? (
        <p data-testid="reviews-empty" className="text-[12px] text-muted-foreground">
          {t(base + "empty")}
        </p>
      ) : (
        <ul className="space-y-2">
          {reviews.map((item) => (
            <li
              key={item.attempt_id}
              className="space-y-1.5 rounded-floating border border-border/60 px-2 py-2"
            >
              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                <span className="font-medium text-foreground">
                  {item.case_name || item.case_id}
                </span>
                <span className="text-[11px] text-muted-foreground">{item.attempt_id}</span>
              </div>

              <p data-testid={"automatic-" + item.attempt_id} className="text-[12px] text-foreground">
                {t(base + "automatic") + ": " + t(VERDICT_KEY[item.automatic_verdict])}
                {item.automatic_reason ? (
                  <span className="ml-2 text-[11px] text-muted-foreground">
                    {t(base + "reason") + ": " + item.automatic_reason}
                  </span>
                ) : null}
              </p>

              <p data-testid={"manual-" + item.attempt_id} className="text-[12px] text-foreground">
                {t(base + "manual") + ": "}
                {item.manual_verdict
                  ? t(VERDICT_KEY[item.manual_verdict])
                  : t(base + "manualNone")}
                {item.manual_note ? (
                  <span className="ml-2 text-[11px] text-muted-foreground">
                    {t(base + "reason") + ": " + item.manual_note}
                  </span>
                ) : null}
              </p>
              {item.manual_reviewer ? (
                <p className="text-[11px] text-muted-foreground">
                  {t(base + "reviewer") + ": " + item.manual_reviewer}
                </p>
              ) : null}

              <p data-testid={"keep-automatic-" + item.attempt_id} className="text-[11px] text-muted-foreground">
                {t(base + "keepAutomatic")}
              </p>

              {!item.manual_verdict && onSubmitReview ? (
                <div className="flex flex-wrap items-center gap-1.5">
                  <button
                    type="button"
                    disabled={busy}
                    className="rounded border border-border px-2 py-0.5 text-[11px] text-foreground disabled:opacity-50"
                    onClick={() => onSubmitReview(item.attempt_id, "passed")}
                  >
                    {t(base + "submit")}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    className="rounded border border-border px-2 py-0.5 text-[11px] text-foreground disabled:opacity-50"
                    onClick={() => onSubmitReview(item.attempt_id, "failed")}
                  >
                    {t(base + "failed")}
                  </button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
