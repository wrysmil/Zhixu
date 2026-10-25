import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";

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
import { evaluationApi, type Overview } from "@/lib/evaluation-api";
import { cn } from "@/lib/utils";

type EvalTab = "overview" | "runs" | "datasets";

export function EvaluationSettings() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<EvalTab>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

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
    <div className="space-y-5">
      {loadError ? (
        <div
          role="alert"
          className="rounded-floating border border-destructive/20 bg-destructive/5 px-4 py-3 text-[13px] text-destructive"
        >
          {loadError}
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <SegmentedControl
          mode="tabs"
          value={tab}
          onChange={(next) => setTab(next)}
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
        <section className="space-y-5">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <MetricTile
              label={t("settings.evaluation.metrics.planned")}
              value={metrics ? String(metrics.planned) : "—"}
            />
            <MetricTile
              label={t("settings.evaluation.metrics.passed")}
              value={metrics ? String(metrics.passed) : "—"}
            />
            <MetricTile
              label={t("settings.evaluation.metrics.failed")}
              value={metrics ? String(metrics.failed) : "—"}
            />
            <MetricTile
              label={t("settings.evaluation.metrics.reviewRequired")}
              value={metrics ? String(metrics.review_required) : "—"}
            />
            <MetricTile
              label={t("settings.evaluation.metrics.notEvaluated")}
              value={metrics ? String(metrics.not_evaluated) : "—"}
            />
            <MetricTile
              label={t("settings.evaluation.metrics.passRate")}
              value={formatPercent(metrics?.overall_pass_rate)}
            />
            <MetricTile
              label={t("settings.evaluation.metrics.coverage")}
              value={formatPercent(metrics?.evaluation_coverage)}
            />
          </div>

          <p className="text-[12px] text-muted-foreground">
            {overview?.metrics_run_id
              ? t("settings.evaluation.metrics.sourceRun", {
                  run: overview.metrics_run_id.slice(0, 8),
                  scope: t("settings.evaluation.metrics.scopeSingleRun"),
                })
              : t("settings.evaluation.metrics.sourceUnknown")}
          </p>

          <div className="space-y-2">
            <h3 className="text-[13px] font-medium text-foreground">
              {t("settings.evaluation.metrics.recentRuns")}
            </h3>
            {overview && overview.recent_runs.length > 0 ? (
              <ul className="space-y-1.5">
                {overview.recent_runs.map((run) => (
                  <li
                    key={run.id}
                    className="flex flex-wrap items-center gap-2 rounded-floating border border-border/60 bg-settings-surface px-3 py-2 text-[13px]"
                  >
                    <span className="font-medium text-foreground">{run.suite_name}</span>
                    <EvaluationStatusBadge status={run.status} />
                    <span className="text-muted-foreground">{formatDateTime(run.created_at)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-muted-foreground">
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

      {tab === "runs" ? <EvaluationRuns refreshKey={refreshKey} /> : null}
      {tab === "datasets" ? <EvaluationDatasets /> : null}
    </div>
  );
}

function MetricTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-floating border border-border/60 bg-settings-surface px-3 py-2.5">
      <p className="text-[12px] text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-lg font-medium text-foreground">{value}</p>
    </div>
  );
}
