import { useTranslation } from "react-i18next";

import type {
  EvaluationCoverage,
  RunMetricsView,
} from "@/lib/evaluation-api";
import { cn } from "@/lib/utils";

/** 评估能力等级：smoke / legacy 都不允许当作综合质量门禁。 */
export type EvaluationCapability = "full" | "smoke" | "legacy";

export type EvaluationScorecardProps = {
  metrics: RunMetricsView | null;
  evaluationCapability?: EvaluationCapability | null;
  coverage?: EvaluationCoverage | null;
  className?: string;
};

const CAPABILITY_KEY: Record<EvaluationCapability | "unknown", string> = {
  full: "settings.evaluation.reliability.scorecard.capabilityFull",
  smoke: "settings.evaluation.reliability.scorecard.capabilitySmoke",
  legacy: "settings.evaluation.reliability.scorecard.capabilityLegacy",
  unknown: "settings.evaluation.reliability.scorecard.capabilityUnknown",
};

function rateText(rate: number | null | undefined, unknownLabel: string): string {
  if (rate == null) return unknownLabel;
  return String(Math.round(rate * 100)) + "%";
}

/** 证据是否齐全：缺任何一项都不能给出综合质量结论。 */
function evidenceComplete(coverage: EvaluationCoverage | null | undefined): boolean {
  if (!coverage) return false;
  return (
    coverage.trace === "complete" &&
    coverage.identity === "complete" &&
    coverage.usage === "complete" &&
    coverage.required_evidence === "complete"
  );
}

export function EvaluationScorecard({
  metrics,
  evaluationCapability,
  coverage,
  className,
}: EvaluationScorecardProps) {
  const { t } = useTranslation();
  const base = "settings.evaluation.reliability.scorecard.";
  const unknownLabel = t(base + "unknown");

  const capability: EvaluationCapability | "unknown" = evaluationCapability ?? "unknown";
  const capabilityLabel = t(CAPABILITY_KEY[capability]);
  const task = metrics?.metrics.task_outcomes ?? null;
  const qualification = metrics?.metrics.qualification ?? null;
  const complete = evidenceComplete(coverage);

  const traceNotice =
    !coverage || coverage.trace === "complete"
      ? null
      : coverage.trace === "missing"
        ? t(base + "traceMissing")
        : t(base + "tracePartial");
  const usageNotice =
    !coverage || coverage.usage === "complete"
      ? null
      : coverage.usage === "missing"
        ? t(base + "usageMissing")
        : t(base + "usagePartial");
  const evidenceNotice =
    coverage && coverage.required_evidence === "partial"
      ? t(base + "requiredEvidencePartial")
      : null;

  return (
    <section
      aria-label={t(base + "title")}
      className={cn("space-y-3 rounded-floating border border-border/60 bg-settings-surface p-3", className)}
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-[13px] font-medium text-foreground">{t(base + "title")}</h3>
        <span
          data-testid="evaluation-capability"
          className="rounded-full border border-border/60 bg-muted/30 px-2 py-0.5 text-[11px] text-foreground"
        >
          {t(base + "capability") + ": " + capabilityLabel}
        </span>
      </header>

      {capability !== "full" ? (
        <p data-testid="capability-hint" className="text-[12px] text-muted-foreground">
          {t(base + "capabilityHint")}
        </p>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2">
        <section
          aria-label={t(base + "taskCompletionTitle")}
          className="space-y-1.5 rounded-floating border border-border/60 p-2"
        >
          <h4 className="text-[13px] font-medium text-foreground">
            {t(base + "taskCompletionTitle")}
          </h4>
          <p className="text-[11px] text-muted-foreground">{t(base + "taskCompletionHint")}</p>
          <dl className="space-y-0.5 text-[12px]">
            <div className="flex flex-wrap items-center gap-2">
              <dt className="text-muted-foreground">{t(base + "taskSuccessRate")}</dt>
              <dd data-testid="task-success-rate" className="font-medium text-foreground">
                {rateText(task?.task_success_rate, unknownLabel)}
              </dd>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <dt className="text-muted-foreground">{t(base + "partialCompletionRate")}</dt>
              <dd data-testid="partial-completion-rate" className="font-medium text-foreground">
                {rateText(task?.partial_completion_rate, unknownLabel)}
              </dd>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <dt className="text-muted-foreground">{t(base + "assessmentCoverage")}</dt>
              <dd data-testid="task-assessment-coverage" className="font-medium text-foreground">
                {rateText(task?.task_assessment_coverage, unknownLabel)}
              </dd>
            </div>
          </dl>
          <p data-testid="task-outcome-summary" className="text-[11px] text-muted-foreground">
            {t(base + "outcomesSummary", {
              completed: task?.completed ?? 0,
              partial: task?.partial ?? 0,
              failed: task?.failed ?? 0,
              unknown: task?.unknown ?? 0,
            })}
          </p>
        </section>

        <section
          aria-label={t(base + "qualificationTitle")}
          className="space-y-1.5 rounded-floating border border-border/60 p-2"
        >
          <h4 className="text-[13px] font-medium text-foreground">
            {t(base + "qualificationTitle")}
          </h4>
          <p className="text-[11px] text-muted-foreground">{t(base + "qualificationHint")}</p>
          <dl className="space-y-0.5 text-[12px]">
            <div className="flex flex-wrap items-center gap-2">
              <dt className="text-muted-foreground">{t(base + "qualificationRate")}</dt>
              <dd data-testid="qualification-rate" className="font-medium text-foreground">
                {rateText(qualification?.qualification_rate, unknownLabel)}
              </dd>
            </div>
          </dl>
          <p data-testid="qualification-summary" className="text-[11px] text-muted-foreground">
            {t(base + "qualificationSummary", {
              qualified: qualification?.qualified ?? 0,
              disqualified: qualification?.disqualified ?? 0,
              reviewRequired: qualification?.review_required ?? 0,
              notEvaluated: qualification?.not_evaluated ?? 0,
            })}
          </p>
        </section>
      </div>

      <p data-testid="separate-metrics" className="text-[12px] text-foreground">
        {t(base + "separateMetrics")}
      </p>

      <ul aria-label={t(base + "evidenceCoverage")} className="space-y-1 text-[12px]">
        {traceNotice ? (
          <li data-testid="trace-notice" className="text-amber-700 dark:text-amber-300">
            {traceNotice}
          </li>
        ) : null}
        {usageNotice ? (
          <li data-testid="usage-notice" className="text-amber-700 dark:text-amber-300">
            {usageNotice}
          </li>
        ) : null}
        {evidenceNotice ? (
          <li data-testid="required-evidence-notice" className="text-amber-700 dark:text-amber-300">
            {evidenceNotice}
          </li>
        ) : null}
        {traceNotice || usageNotice || evidenceNotice ? (
          <li data-testid="pending-review" className="text-[12px] font-medium text-foreground">
            {t(base + "pendingReview")}
          </li>
        ) : null}
      </ul>

      {complete && capability === "full" ? (
        <p data-testid="quality-conclusion" className="text-[12px] font-medium text-foreground">
          {t(base + "qualityConclusion")}
        </p>
      ) : (
        <p data-testid="no-quality-conclusion" className="text-[12px] text-muted-foreground">
          {t(base + "noConclusion")}
        </p>
      )}
    </section>
  );
}
