import { useTranslation } from "react-i18next";

import type { Comparison, ComparisonPair } from "@/lib/evaluation-api";
import { cn } from "@/lib/utils";

export type EvaluationComparisonProps = {
  comparison: Comparison | null;
  loading?: boolean;
  /** 跳到指定运行的证据视图。 */
  onOpenEvidence?: (runId: string, caseId?: string) => void;
  className?: string;
};

const STATUS_KEY: Record<ComparisonPair["status"], string> = {
  improved: "settings.evaluation.reliability.comparison.statusImproved",
  regressed: "settings.evaluation.reliability.comparison.statusRegressed",
  unchanged: "settings.evaluation.reliability.comparison.statusUnchanged",
  incomparable: "settings.evaluation.reliability.comparison.statusIncomparable",
};

function rateText(rate: number | null | undefined, unknownLabel: string): string {
  if (rate == null) return unknownLabel;
  return String(Math.round(rate * 100)) + "%";
}

export function EvaluationComparison({
  comparison,
  loading = false,
  onOpenEvidence,
  className,
}: EvaluationComparisonProps) {
  const { t } = useTranslation();
  const base = "settings.evaluation.reliability.comparison.";
  const unknownLabel = t(base + "unknown");

  return (
    <section
      aria-label={t(base + "title")}
      className={cn("space-y-3 rounded-floating border border-border/60 bg-settings-surface p-3", className)}
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-[13px] font-medium text-foreground">{t(base + "title")}</h3>
        {comparison ? (
          <span
            data-testid="comparison-compatibility"
            className="rounded-full border border-border/60 bg-muted/30 px-2 py-0.5 text-[11px] text-foreground"
          >
            {t(base + "compatibility") + ": " +
              t(
                comparison.compatibility === "strict"
                  ? base + "compatibilityStrict"
                  : base + "compatibilityIncompatible",
              )}
          </span>
        ) : null}
      </header>

      {!comparison ? (
        <p data-testid="comparison-empty" className="text-[12px] text-muted-foreground">
          {loading ? t(base + "loading") : t(base + "noPairs")}
        </p>
      ) : (
        <>
          <dl className="flex flex-wrap gap-4 text-[12px]">
            <div className="flex items-center gap-2">
              <dt className="text-muted-foreground">{t(base + "baseline")}</dt>
              <dd data-testid="baseline-run" className="font-medium text-foreground">
                {comparison.baseline_run_id}
              </dd>
            </div>
            <div className="flex items-center gap-2">
              <dt className="text-muted-foreground">{t(base + "candidate")}</dt>
              <dd data-testid="candidate-run" className="font-medium text-foreground">
                {comparison.candidate_run_id}
              </dd>
            </div>
          </dl>

          {comparison.reasons.length > 0 ? (
            <div data-testid="comparison-reasons" className="space-y-0.5">
              <p className="text-[12px] font-medium text-foreground">{t(base + "comparisonReasons")}</p>
              <ul className="list-disc space-y-0.5 pl-4 text-[12px] text-muted-foreground">
                {comparison.reasons.map((reason, index) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {comparison.pairs.length === 0 ? (
            <p data-testid="no-pairs" className="text-[12px] text-muted-foreground">
              {t(base + "noPairs")}
            </p>
          ) : (
            <ul className="space-y-2">
              {comparison.pairs.map((pair) => (
                <li
                  key={pair.case_id + ":" + pair.case_revision}
                  data-testid={"pair-" + pair.case_id}
                  className="space-y-1 rounded-floating border border-border/60 px-2 py-2"
                >
                  <div className="flex flex-wrap items-center gap-2 text-[13px]">
                    <span className="font-medium text-foreground">{pair.case_id}</span>
                    <span className="text-[11px] text-muted-foreground">
                      {"#" + pair.case_revision}
                    </span>
                    <span
                      data-testid={"pair-status-" + pair.case_id}
                      className="rounded-full border border-border/60 bg-muted/30 px-2 py-0.5 text-[11px] text-foreground"
                    >
                      {t(STATUS_KEY[pair.status])}
                    </span>
                  </div>
                  <p data-testid={"pair-reason-" + pair.case_id} className="text-[12px] text-foreground">
                    {t(base + "reason") + ": " + pair.reason}
                  </p>
                  <p className="text-[12px] text-muted-foreground">
                    {t(base + "baseline") + ": " +
                      rateText(pair.baseline.success_rate, unknownLabel) +
                      " (" +
                      t(base + "trials") +
                      " " +
                      pair.baseline.trials +
                      ")"}
                    {" · "}
                    {t(base + "candidate") + ": " +
                      rateText(pair.candidate.success_rate, unknownLabel) +
                      " (" +
                      t(base + "trials") +
                      " " +
                      pair.candidate.trials +
                      ")"}
                    {" · "}
                    {t(base + "delta") + ": " + rateText(pair.delta_success_rate, unknownLabel)}
                  </p>
                  {onOpenEvidence ? (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <button
                        type="button"
                        className="rounded border border-border px-2 py-0.5 text-[11px] text-foreground hover:bg-muted/40"
                        onClick={() =>
                          onOpenEvidence(comparison.baseline_run_id, pair.case_id)
                        }
                      >
                        {t(base + "viewBaselineEvidence")}
                      </button>
                      <button
                        type="button"
                        className="rounded border border-border px-2 py-0.5 text-[11px] text-foreground hover:bg-muted/40"
                        onClick={() =>
                          onOpenEvidence(comparison.candidate_run_id, pair.case_id)
                        }
                      >
                        {t(base + "viewCandidateEvidence")}
                      </button>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
