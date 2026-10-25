import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

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
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import {
  evaluationApi,
  type Attempt,
  type Run,
  type RunSummary,
} from "@/lib/evaluation-api";

export function RunDetailSheet({ run, onClose }: { run: RunSummary; onClose: () => void }) {
  const { t } = useTranslation();
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
  }, [run.id, t]);

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

  const isActive = run.status === "queued" || run.status === "running" || run.status === "cancelling";
  const failedAttemptIds = attempts
    .filter((attempt) => attempt.verdict === "failed")
    .map((attempt) => attempt.id);

  return (
    <Sheet
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <SheetContent className="overflow-y-auto sm:max-w-4xl">
        <div className="space-y-4 pr-8">
          <div className="space-y-1">
            <SheetTitle className="text-base font-semibold">{run.suite_name}</SheetTitle>
            <SheetDescription className="text-[12px]">
              {formatDateTime(run.created_at)}
            </SheetDescription>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <EvaluationStatusBadge status={run.status} />
            <span className="text-[12px] text-muted-foreground">
              {t("settings.evaluation.runs.progress")}: {run.terminal_count}/{run.planned_count}
            </span>
          </div>

          <div className="flex flex-wrap gap-2">
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

          <ObservabilityTabs runId={run.id} attempts={attempts} />
        </div>
      </SheetContent>
    </Sheet>
  );
}
