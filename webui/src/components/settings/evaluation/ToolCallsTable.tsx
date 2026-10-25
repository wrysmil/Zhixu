import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { evaluationErrorText } from "@/components/settings/evaluation/evaluation-utils";
import { Button } from "@/components/ui/button";
import {
  evaluationApi,
  type ToolCall,
  type ToolCallPage,
  type ToolCallStatus,
} from "@/lib/evaluation-api";

export type ToolCallFilter = {
  q: string;
  status: ToolCallStatus | null;
};

const STATUS_OPTIONS: ToolCallStatus[] = [
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "unknown",
];

const STATUS_LABEL_KEY: Record<ToolCallStatus, string> = {
  running: "settings.evaluation.observability.statusRunning",
  succeeded: "settings.evaluation.observability.statusSucceeded",
  failed: "settings.evaluation.observability.statusFailed",
  cancelled: "settings.evaluation.observability.statusCancelled",
  unknown: "settings.evaluation.observability.statusUnknown",
};

function statusLabelKey(status: ToolCallStatus): string {
  return STATUS_LABEL_KEY[status] ?? STATUS_LABEL_KEY.unknown;
}

export function ToolCallsTable({
  attemptId,
  query,
  status,
  applied,
  onQueryChange,
  onStatusChange,
  onApply,
  onReset,
}: {
  attemptId: string | null;
  query: string;
  status: ToolCallStatus | "all";
  applied: ToolCallFilter;
  onQueryChange: (value: string) => void;
  onStatusChange: (value: ToolCallStatus | "all") => void;
  onApply: () => void;
  onReset: () => void;
}) {
  const { t } = useTranslation();
  const [page, setPage] = useState<ToolCallPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!attemptId) {
      setPage(null);
      return;
    }
    if (typeof evaluationApi.listToolCalls !== "function") return;
    let cancelled = false;
    setLoading(true);
    void evaluationApi
      .listToolCalls(attemptId, {
        q: applied.q || undefined,
        status: applied.status ?? undefined,
        limit: 50,
      })
      .then((next) => {
        if (cancelled) return;
        setPage(next);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setPage(null);
        setError(cause);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [attemptId, applied]);

  const rows: ToolCall[] = page?.data ?? [];
  const total = page?.total_count ?? 0;
  const errorText = error ? evaluationErrorText(error, t) : null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex min-w-[14rem] flex-1 flex-col gap-1">
          <label
            className="text-[11px] font-medium text-muted-foreground"
            htmlFor="evaluation-tool-call-search"
          >
            {t("settings.evaluation.observability.searchToolCalls")}
          </label>
          <input
            id="evaluation-tool-call-search"
            type="search"
            value={query}
            aria-label={t("settings.evaluation.observability.searchToolCalls")}
            placeholder={t("settings.evaluation.observability.searchToolCallsPlaceholder")}
            onChange={(event) => onQueryChange(event.target.value)}
            className="h-9 w-full rounded-control border border-input bg-background px-3 py-1 text-[13px]"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label
            className="text-[11px] font-medium text-muted-foreground"
            htmlFor="evaluation-tool-call-status"
          >
            {t("settings.evaluation.observability.toolStatus")}
          </label>
          <select
            id="evaluation-tool-call-status"
            value={status}
            aria-label={t("settings.evaluation.observability.toolStatus")}
            onChange={(event) => onStatusChange(event.target.value as ToolCallStatus | "all")}
            className="h-9 rounded-control border border-input bg-background px-2 text-[13px]"
          >
            <option value="all">{t("settings.evaluation.observability.statusAll")}</option>
            {STATUS_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {t(statusLabelKey(option))}
              </option>
            ))}
          </select>
        </div>
        <Button type="button" size="sm" onClick={onApply}>
          {t("settings.evaluation.observability.applyFilters")}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onReset}>
          {t("settings.evaluation.observability.resetFilters")}
        </Button>
      </div>

      {error ? (
        <p role="alert" className="text-[12px] text-destructive">
          {errorText}
        </p>
      ) : null}

      {attemptId ? (
        <p role="status" className="text-[12px] text-muted-foreground">
          {t("settings.evaluation.observability.loadedTotal", {
            loaded: rows.length,
            total,
            defaultValue: `${rows.length} loaded of ${total} total`,
          })}
        </p>
      ) : null}

      {loading && rows.length === 0 ? (
        <p className="text-[12px] text-muted-foreground">
          {t("settings.evaluation.observability.loadingToolCalls")}
        </p>
      ) : null}

      {!loading && attemptId && rows.length === 0 ? (
        <p className="rounded-floating border border-dashed border-border/70 p-6 text-center text-[13px] text-muted-foreground">
          {t("settings.evaluation.observability.noToolCalls")}
        </p>
      ) : null}

      {rows.length > 0 ? (
        <div className="max-w-full overflow-x-auto rounded-floating border border-border/70">
          <table
            className="w-full min-w-[820px] text-left text-[12px]"
            aria-label={t("settings.evaluation.observability.toolCallsTable")}
          >
            <thead className="bg-muted/30 text-muted-foreground">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">
                  {t("settings.evaluation.observability.toolColumnCall")}
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  {t("settings.evaluation.observability.toolColumnTool")}
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  {t("settings.evaluation.observability.toolColumnAgent")}
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  {t("settings.evaluation.observability.toolColumnPhase")}
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  {t("settings.evaluation.observability.toolColumnStatus")}
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  {t("settings.evaluation.observability.toolColumnDuration")}
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  {t("settings.evaluation.observability.toolColumnInput")}
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  {t("settings.evaluation.observability.toolColumnError")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {rows.map((row) => (
                <tr key={row.call_id}>
                  <td className="px-3 py-2 font-mono text-foreground">{row.call_id}</td>
                  <td className="px-3 py-2 text-foreground">{row.tool_name}</td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {row.agent_name ?? t("settings.evaluation.observability.unavailable")}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {row.phase_id ?? t("settings.evaluation.observability.notRecorded")}
                  </td>
                  <td className="px-3 py-2 text-foreground">
                    {t(statusLabelKey(row.status))}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                    {row.duration_ms == null
                      ? t("settings.evaluation.observability.unavailable")
                      : `${row.duration_ms} ms`}
                  </td>
                  <td className="max-w-[16rem] truncate px-3 py-2 text-muted-foreground">
                    {row.input_summary ?? t("settings.evaluation.observability.notRecorded")}
                  </td>
                  <td className="max-w-[16rem] truncate px-3 py-2 text-destructive">
                    {row.error ?? t("settings.evaluation.observability.notRecorded")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
