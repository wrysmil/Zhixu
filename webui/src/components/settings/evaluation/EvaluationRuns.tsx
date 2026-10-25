import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight, Plus } from "lucide-react";

import { NewRunDialog } from "@/components/settings/evaluation/NewRunDialog";
import { RunDetailSheet } from "@/components/settings/evaluation/RunDetailSheet";
import {
  evaluationStatusKey,
  EvaluationStatusBadge,
} from "@/components/settings/evaluation/evaluation-status";
import {
  evaluationErrorText,
  formatDateTime,
} from "@/components/settings/evaluation/evaluation-utils";
import { Button } from "@/components/ui/button";
import { evaluationApi, type RunStatus, type RunSummary } from "@/lib/evaluation-api";

const FILTER_STATUSES: RunStatus[] = [
  "queued",
  "running",
  "completed",
  "cancelling",
  "cancelled",
  "interrupted",
  "timed_out",
];

export function EvaluationRuns({ refreshKey = 0, initialRun = null }: { refreshKey?: number; initialRun?: RunSummary | null }) {
  const { t } = useTranslation();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [statusFilter, setStatusFilter] = useState<"" | RunStatus>("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [activeRun, setActiveRun] = useState<RunSummary | null>(initialRun);
  const returnFocus = useRef<string | null>(null);
  const newRunRef = useRef<HTMLButtonElement>(null);
  const runRefs = useRef(new Map<string, HTMLButtonElement>());

  useEffect(() => {
    if (createOpen || activeRun || !returnFocus.current) return;
    (runRefs.current.get(returnFocus.current) ?? newRunRef.current)?.focus();
    returnFocus.current = null;
  }, [createOpen, activeRun]);

  const loadRuns = useCallback(
    async (status: "" | RunStatus) => {
      try {
        const page = await evaluationApi.listRuns(status ? { status } : {});
        setRuns(page.data);
        setLoadError(null);
      } catch (error) {
        setLoadError(evaluationErrorText(error, t));
      }
    },
    [t],
  );

  useEffect(() => {
    void loadRuns(statusFilter);
  }, [loadRuns, statusFilter, refreshKey]);

  if (createOpen) return (
    <NewRunDialog
      open
      onOpenChange={setCreateOpen}
      onCreated={(run) => {
        setActiveRun(run);
        void loadRuns(statusFilter);
      }}
    />
  );

  if (activeRun) return <RunDetailSheet key={activeRun.id} run={activeRun} refreshKey={refreshKey} onClose={() => {
    returnFocus.current = activeRun.id;
    setActiveRun(null);
    void loadRuns(statusFilter);
  }} />;

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
          <span>{t("settings.evaluation.runs.filter")}</span>
          <select
            aria-label={t("settings.evaluation.runs.filter")}
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value as "" | RunStatus)}
            className="h-8 rounded-control border border-border/70 bg-background px-2 text-[12px] text-foreground"
          >
            <option value="">{t("settings.evaluation.runs.filterAll")}</option>
            {FILTER_STATUSES.map((status) => (
              <option key={status} value={status}>
                {t(`settings.evaluation.status.${evaluationStatusKey(status)}`, {
                  defaultValue: status,
                })}
              </option>
            ))}
          </select>
        </div>
        <Button ref={newRunRef} type="button" size="sm" onClick={() => { returnFocus.current = "new"; setCreateOpen(true); }}>
          <Plus className="h-3.5 w-3.5" aria-hidden />
          {t("settings.evaluation.runs.new")}
        </Button>
      </div>

      {loadError ? (
        <p role="alert" className="text-[13px] text-destructive">
          {loadError}
        </p>
      ) : null}

      {runs.length > 0 ? (
        <div className="overflow-x-auto rounded-panel border border-border/60 bg-settings-surface p-2 sm:p-4">
          <table aria-label={t("settings.evaluation.tab.runs")} className="w-full text-left text-[13px]">
            <thead>
              <tr className="border-b border-border/60 text-[12px] text-muted-foreground">
                <th scope="col" className="py-2 pr-3 font-medium">
                  {t("settings.evaluation.runs.suite")}
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  {t("settings.evaluation.runs.status")}
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  {t("settings.evaluation.runs.progress")}
                </th>
                <th scope="col" className="py-2 font-medium">
                  {t("settings.evaluation.runs.created")}
                </th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id} className="border-b border-border/40 transition-colors last:border-0 hover:bg-muted/30">
                  <td className="py-4 pr-3">
                    <button
                      type="button"
                      ref={(node) => { if (node) runRefs.current.set(run.id, node); else runRefs.current.delete(run.id); }}
                      onClick={() => setActiveRun(run)}
                      className="flex items-center gap-2 text-left font-medium text-foreground hover:text-primary focus-visible:underline"
                    >
                      {run.suite_name}
                      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                    </button>
                  </td>
                  <td className="py-2 pr-3">
                    <EvaluationStatusBadge status={run.status} />
                  </td>
                  <td className="py-2 pr-3 text-muted-foreground">
                    <div className="min-w-20 space-y-1.5">
                      <span className="tabular-nums">{run.terminal_count}/{run.planned_count}</span>
                      <div className="h-1 w-20 overflow-hidden rounded-full bg-muted" aria-hidden>
                        <div className="h-full rounded-full bg-primary/70" style={{ width: `${run.planned_count > 0 ? Math.min(100, Math.max(0, run.terminal_count / run.planned_count * 100)) : 0}%` }} />
                      </div>
                    </div>
                  </td>
                  <td className="py-2 text-muted-foreground">{formatDateTime(run.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="rounded-panel border border-dashed border-border/70 px-6 py-16 text-center text-sm text-muted-foreground">{t("settings.evaluation.runs.empty")}</p>
      )}

    </section>
  );
}
