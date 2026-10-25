import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft } from "lucide-react";

import { ObservabilityTabs } from "@/components/settings/evaluation/ObservabilityTabs";
import {
  EvaluationStatusBadge,
} from "@/components/settings/evaluation/evaluation-status";
import {
  evaluationErrorText,
  formatDateTime,
  formatPercent,
} from "@/components/settings/evaluation/evaluation-utils";
import { Button } from "@/components/ui/button";
import {
  evaluationApi,
  type Attempt,
  type Run,
  type RunSummary,
} from "@/lib/evaluation-api";

export function RunDetailSheet({ run, onClose, refreshKey = 0 }: { run: RunSummary; onClose: () => void; refreshKey?: number }) {
  const { t } = useTranslation();
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { headingRef.current?.focus(); }, [run.id]);
  const [full, setFull] = useState<Run | null>(null);
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setFull(null);
    setAttempts([]);
    setLoadError(null);
    setActionError(null);
    void (async () => {
      try {
        const [runPage, attemptPage] = await Promise.all([
          evaluationApi.getRun(run.id),
          evaluationApi.listAttempts(run.id),
        ]);
        if (cancelled) return;
        setFull(runPage);
        setAttempts(attemptPage.data);
      } catch (error) {
        if (!cancelled) setLoadError(evaluationErrorText(error, t));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [run.id, t, refreshKey]);

  const runAction = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setActionError(null);
    try {
      await action();
      const [runPage, attemptPage] = await Promise.all([
        evaluationApi.getRun(run.id),
        evaluationApi.listAttempts(run.id),
      ]);
      setFull(runPage);
      setAttempts(attemptPage.data);
    } catch (error) {
      setActionError(evaluationErrorText(error, t));
    } finally {
      setBusy(false);
    }
  };

  const currentRun = full ?? run;
  const isActive = currentRun.status === "queued" || currentRun.status === "running" || currentRun.status === "cancelling";
  const failedAttemptIds = attempts
    .filter((attempt) => attempt.verdict === "failed")
    .map((attempt) => attempt.id);

  return (
    <section aria-labelledby="evaluation-detail-title" className="min-w-0 space-y-6">
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            <ArrowLeft className="h-4 w-4" aria-hidden />
            {t("settings.evaluation.tab.runs")}
          </Button>
        <div className="space-y-5 rounded-panel border border-border/60 bg-settings-surface p-4 sm:p-6">
          <div className="space-y-1">
            <h2 id="evaluation-detail-title" tabIndex={-1} ref={headingRef} className="break-words text-xl font-semibold tracking-tight outline-none">{run.suite_name}</h2>
            <p className="text-[12px] text-muted-foreground">
              {formatDateTime(run.created_at)}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <EvaluationStatusBadge status={currentRun.status} />
            <span className="text-[12px] text-muted-foreground">
              {t("settings.evaluation.runs.progress")}: {currentRun.terminal_count}/{currentRun.planned_count}
            </span>
          </div>

          <div className="flex flex-wrap gap-2 border-t border-border/60 pt-4">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => void runAction(() => evaluationApi.downloadRunExport(run.id, "json"))}
            >
              {t("settings.evaluation.detail.exportJson")}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => void runAction(() => evaluationApi.downloadRunExport(run.id, "csv"))}
            >
              {t("settings.evaluation.detail.exportCsv")}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              title={t("settings.evaluation.observability.exportReportHint")}
              onClick={() => void runAction(() => evaluationApi.downloadRunReport(run.id))}
            >
              {t("settings.evaluation.observability.exportReport")}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => void runAction(() => evaluationApi.downloadSummaryReport(run.suite_id))}
            >
              {t("settings.evaluation.detail.summaryReport")}
            </Button>
            {isActive ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => void runAction(() => evaluationApi.cancelRun(run.id))}
              >
                {t("settings.evaluation.detail.cancelRun")}
              </Button>
            ) : null}
            {failedAttemptIds.length > 0 ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => void runAction(() => evaluationApi.rerunRun(run.id, failedAttemptIds))}
              >
                {t("settings.evaluation.detail.rerunFailed")}
              </Button>
            ) : null}
          </div>

          {actionError ? (
            <p role="alert" className="text-[12px] text-destructive">
              {actionError}
            </p>
          ) : null}
          {loadError ? (
            <p role="alert" className="text-[12px] text-destructive">
              {loadError}
            </p>
          ) : null}

          {full?.metrics ? (
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-muted-foreground">
              <span>
                {t("settings.evaluation.metrics.passRate")}:{" "}
                {formatPercent(full.metrics.overall_pass_rate)}
              </span>
              <span>
                {t("settings.evaluation.metrics.passed")}: {full.metrics.passed}
              </span>
              <span>
                {t("settings.evaluation.metrics.failed")}: {full.metrics.failed}
              </span>
            </div>
          ) : null}

        </div>
        <div className="min-w-0 rounded-panel border border-border/60 bg-settings-surface p-4 sm:p-6">
          <ObservabilityTabs key={`${run.id}:${refreshKey}`} runId={run.id} attempts={attempts} />
        </div>
    </section>
  );
}
