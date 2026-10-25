import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowUpRight, RefreshCw } from "lucide-react";

import { EvaluationDatasets } from "@/components/settings/evaluation/EvaluationDatasets";
import { EvaluationRuns } from "@/components/settings/evaluation/EvaluationRuns";
import { EvaluationStatusBadge } from "@/components/settings/evaluation/evaluation-status";
import {
  evaluationErrorText,
  formatDateTime,
  formatPercent,
} from "@/components/settings/evaluation/evaluation-utils";
import { Button } from "@/components/ui/button";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { evaluationApi, type Overview, type RunSummary } from "@/lib/evaluation-api";
import { cn } from "@/lib/utils";

type EvalTab = "overview" | "runs" | "datasets";

export function EvaluationSettings() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<EvalTab>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [selectedRun, setSelectedRun] = useState<RunSummary | null>(null);

  const loadOverview = useCallback(async () => {
    try {
      const data = await evaluationApi.overview();
      setOverview(data);
      setOnline(true);
      setLoadError(null);
    } catch (error) {
      setOnline(false);
      setLoadError(evaluationErrorText(error, t));
    }
  }, [t]);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview, refreshKey]);

  const metrics = overview?.metrics ?? null;
  const statusEntries = Object.entries(overview?.status_counts ?? {}).filter(([, count]) => count);

  return (
    <div className="min-w-0 space-y-6">
      <header>
        <h2 className="text-2xl font-semibold tracking-tight">{t("settings.evaluation.title")}</h2>
        <p className="mt-2 text-sm text-muted-foreground">{t("settings.evaluation.subtitle")}</p>
      </header>
      {loadError ? (
        <div
          role="alert"
          className="rounded-floating border border-destructive/20 bg-destructive/5 px-4 py-3 text-[13px] text-destructive"
        >
          {loadError}
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 pb-4">
        <SegmentedControl
          mode="tabs"
          value={tab}
          onChange={(next) => { setSelectedRun(null); setTab(next); }}
          className="rounded-control bg-muted/40 p-1"
          itemClassName="rounded-control px-4 py-2"
          ariaLabel={t("settings.evaluation.title")}
          options={[
            { value: "overview", label: t("settings.evaluation.tab.overview") },
            { value: "runs", label: t("settings.evaluation.tab.runs") },
            { value: "datasets", label: t("settings.evaluation.tab.datasets") },
          ]}
        />
        <div className="flex items-center gap-2">
          {online !== null ? (
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium",
                online
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                  : "border-destructive/30 bg-destructive/10 text-destructive",
              )}
            >
              <span
                className={cn("h-1.5 w-1.5 rounded-full", online ? "bg-emerald-500" : "bg-destructive")}
                aria-hidden
              />
              {online ? t("settings.evaluation.online") : t("settings.evaluation.offline")}
            </span>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setRefreshKey((key) => key + 1)}
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            {t("settings.evaluation.refresh")}
          </Button>
        </div>
      </div>

      {tab === "overview" ? (
        <section className="space-y-6">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <MetricTile
              label={t("settings.evaluation.metrics.passRate")}
              value={formatPercent(metrics?.overall_pass_rate)}
              prominent
            />
            <MetricTile
              label={t("settings.evaluation.metrics.coverage")}
              value={formatPercent(metrics?.evaluation_coverage)}
            />
            <MetricTile
              label={t("settings.evaluation.metrics.planned")}
              value={metrics ? String(metrics.planned) : "—"}
            />
            <MetricTile
              label={t("settings.evaluation.metrics.failed")}
              value={metrics ? String(metrics.failed) : "—"}
            />
          </div>

          <div className="flex flex-wrap gap-x-6 gap-y-2 rounded-control bg-muted/30 px-4 py-3 text-xs text-muted-foreground">
            <span>{t("settings.evaluation.metrics.passed")} <strong className="ml-2 font-medium tabular-nums text-foreground">{metrics?.passed ?? "—"}</strong></span>
            <span>{t("settings.evaluation.metrics.reviewRequired")} <strong className="ml-2 font-medium tabular-nums text-foreground">{metrics?.review_required ?? "—"}</strong></span>
            <span>{t("settings.evaluation.metrics.notEvaluated")} <strong className="ml-2 font-medium tabular-nums text-foreground">{metrics?.not_evaluated ?? "—"}</strong></span>
          </div>

          <p className="text-[12px] text-muted-foreground">
            {overview?.metrics_run_id
              ? t("settings.evaluation.metrics.sourceRun", {
                  run: overview.metrics_run_id.slice(0, 8),
                  scope: t("settings.evaluation.metrics.scopeSingleRun"),
                })
              : t("settings.evaluation.metrics.sourceUnknown")}
          </p>

          <div className="overflow-hidden rounded-panel border border-border/60 bg-settings-surface">
            <h3 className="border-b border-border/60 px-5 py-4 text-sm font-semibold text-foreground">
              {t("settings.evaluation.metrics.recentRuns")}
            </h3>
            {overview && overview.recent_runs.length > 0 ? (
              <ul className="divide-y divide-border/50">
                {overview.recent_runs.map((run) => (
                  <li
                    key={run.id}
                    className="text-[13px]"
                  >
                    <button type="button" onClick={() => { setSelectedRun(run); setTab("runs"); }} className="flex w-full flex-wrap items-center gap-3 px-5 py-4 text-left transition-colors hover:bg-muted/30 focus-visible:bg-muted/40">
                    <span className="min-w-0 flex-1 break-words font-medium text-foreground">{run.suite_name}</span>
                    <EvaluationStatusBadge status={run.status} />
                    <span className="text-muted-foreground">{formatDateTime(run.created_at)}</span>
                    <ArrowUpRight className="h-4 w-4 text-muted-foreground" aria-hidden />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-5 py-12 text-center text-[13px] text-muted-foreground">
                {t("settings.evaluation.runs.empty")}
              </p>
            )}
          </div>

          {statusEntries.length > 0 ? (
            <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
              {statusEntries.map(([status, count]) => (
                <span key={status} className="inline-flex items-center gap-1">
                  <EvaluationStatusBadge status={status} />
                  <span>×{count}</span>
                </span>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}

      {tab === "runs" ? <EvaluationRuns refreshKey={refreshKey} initialRun={selectedRun} /> : null}
      {tab === "datasets" ? <EvaluationDatasets /> : null}
    </div>
  );
}

function MetricTile({ label, value, prominent = false }: { label: string; value: string; prominent?: boolean }) {
  return (
    <div className={cn("rounded-panel border px-5 py-5", prominent ? "border-primary/20 bg-primary/5" : "border-border/60 bg-settings-surface")}>
      <p className="text-[12px] text-muted-foreground">{label}</p>
      <p className={cn("mt-3 text-3xl font-semibold tabular-nums tracking-tight", prominent ? "text-primary" : "text-foreground")}>{value}</p>
    </div>
  );
}
