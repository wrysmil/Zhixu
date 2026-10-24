import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  EvaluationStatusBadge,
  evaluationStatusKey,
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
  type TraceEvent,
} from "@/lib/evaluation-api";

export function RunDetailSheet({ run, onClose }: { run: RunSummary; onClose: () => void }) {
  const { t } = useTranslation();
  const [full, setFull] = useState<Run | null>(null);
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [traces, setTraces] = useState<Record<string, TraceEvent[]>>({});
  const [traceMore, setTraceMore] = useState<Record<string, number>>({});

  useEffect(() => {
    let cancelled = false;
    setFull(null);
    setAttempts([]);
    setTraces({});
    setTraceMore({});
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

  const loadTrace = async (attemptId: string, afterSeq = 0) => {
    try {
      const page = await evaluationApi.getTrace(attemptId, afterSeq, 200);
      setTraces((prev) => ({
        ...prev,
        [attemptId]:
          afterSeq === 0 ? page.data : [...(prev[attemptId] ?? []), ...page.data],
      }));
      setTraceMore((prev) => ({
        ...prev,
        [attemptId]: page.has_more ? page.next_after_seq : 0,
      }));
      setActionError(null);
    } catch (error) {
      setActionError(evaluationErrorText(error, t));
    }
  };

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
      <SheetContent className="overflow-y-auto sm:max-w-md">
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

          <div className="space-y-3">
            <h3 className="text-[13px] font-medium text-foreground">
              {t("settings.evaluation.detail.attempts")}
            </h3>
            {attempts.length === 0 && !loadError ? (
              <p className="text-[12px] text-muted-foreground">
                {t("settings.evaluation.detail.noAttempts")}
              </p>
            ) : null}
            {attempts.map((attempt) => (
              <div
                key={attempt.id}
                className="space-y-2 rounded-floating border border-border/60 bg-settings-surface p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[13px] font-medium text-foreground">{attempt.case_name}</span>
                  <EvaluationStatusBadge status={attempt.verdict} kind="verdict" />
                  <span className="text-[11px] text-muted-foreground">
                    {t(`settings.evaluation.status.${evaluationStatusKey(attempt.execution_status)}`, {
                      defaultValue: attempt.execution_status,
                    })}
                  </span>
                  {attempt.errors.length > 0 ? (
                    <span className="text-[11px] text-destructive">
                      {t("settings.evaluation.detail.errors")}: {attempt.errors.length}
                    </span>
                  ) : null}
                </div>
                {attempt.final_answer ? (
                  <p className="line-clamp-2 text-[12px] text-muted-foreground">
                    {attempt.final_answer}
                  </p>
                ) : null}
                {traces[attempt.id] ? (
                  <ol className="space-y-1 rounded-control bg-muted/40 p-2 font-mono text-[11px] text-muted-foreground">
                    {traces[attempt.id].map((event) => (
                      <li key={event.event_id} className="flex gap-2">
                        <span>{event.seq}</span>
                        <span className="text-foreground">{event.type}</span>
                        {event.payload ? (
                          <span className="truncate">{JSON.stringify(event.payload)}</span>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                ) : null}
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => void loadTrace(attempt.id)}
                  >
                    {t("settings.evaluation.detail.loadTrace")}
                  </Button>
                  {traceMore[attempt.id] ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => void loadTrace(attempt.id, traceMore[attempt.id])}
                    >
                      {t("settings.evaluation.detail.loadMore")}
                    </Button>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
