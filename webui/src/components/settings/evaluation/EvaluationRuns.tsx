import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";

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

export function EvaluationRuns({ refreshKey = 0 }: { refreshKey?: number }) {
  const { t } = useTranslation();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [statusFilter, setStatusFilter] = useState<"" | RunStatus>("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [activeRun, setActiveRun] = useState<RunSummary | null>(null);

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
        <Button type="button" size="sm" onClick={() => setCreateOpen(true)}>
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
        <div className="overflow-x-auto">
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
                <tr key={run.id} className="border-b border-border/40">
                  <td className="py-2 pr-3">
                    <button
                      type="button"
                      onClick={() => setActiveRun(run)}
                      className="text-left font-medium text-foreground hover:underline"
                    >
                      {run.suite_name}
                    </button>
                  </td>
                  <td className="py-2 pr-3">
                    <EvaluationStatusBadge status={run.status} />
                  </td>
                  <td className="py-2 pr-3 text-muted-foreground">
                    {run.terminal_count}/{run.planned_count}
                  </td>
                  <td className="py-2 text-muted-foreground">{formatDateTime(run.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-[13px] text-muted-foreground">{t("settings.evaluation.runs.empty")}</p>
      )}

      <NewRunDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={() => {
          setCreateOpen(false);
          void loadRuns(statusFilter);
        }}
      />
      {activeRun ? <RunDetailSheet run={activeRun} onClose={() => setActiveRun(null)} /> : null}
    </section>
  );
}
