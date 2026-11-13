import { Clock, Hash, Layers, Search, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

import { formatDuration, type TrajectorySummaryStats } from "@/features/trajectory/projection";
import type { TrajectoryCoverage } from "@/features/trajectory/types";

interface TrajectoryToolbarProps {
  stats: TrajectorySummaryStats;
  loadedCount: number;
  mode: "duration" | "even";
  onModeChange: (mode: "duration" | "even") => void;
  turnsCollapsed: boolean;
  onToggleTurns: () => void;
  callsCollapsed: boolean;
  onToggleCalls: () => void;
  query: string;
  onQueryChange: (value: string) => void;
  coverage: TrajectoryCoverage | null;
}

export function TrajectoryToolbar({
  stats,
  loadedCount,
  mode,
  onModeChange,
  turnsCollapsed,
  onToggleTurns,
  callsCollapsed,
  onToggleCalls,
  query,
  onQueryChange,
  coverage,
}: TrajectoryToolbarProps) {
  const { t } = useTranslation();
  return (
    <div
      data-testid="trajectory-toolbar"
      className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border/60 px-3 py-2 text-[12px] text-muted-foreground"
    >
      <div className="flex items-center gap-3 font-medium text-foreground/85">
        <span className="inline-flex items-center gap-1" title={t("thread.trajectory.stats.duration")}>
          <Clock className="h-3.5 w-3.5" aria-hidden />
          {formatDuration(stats.durationMs)}
        </span>
        <span className="inline-flex items-center gap-1" title={t("thread.trajectory.stats.turns")}>
          <Layers className="h-3.5 w-3.5" aria-hidden />
          {stats.turns}
        </span>
        <span className="inline-flex items-center gap-1" title={t("thread.trajectory.stats.calls")}>
          <Wrench className="h-3.5 w-3.5" aria-hidden />
          {stats.calls}
        </span>
        {coverage && coverage.status !== "complete" ? (
          <span
            className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-700 dark:text-amber-300"
            title={coverage.reasons.join(", ") || t("thread.trajectory.coverage.partial")}
          >
            {t("thread.trajectory.coverage.partial")}
          </span>
        ) : null}
      </div>

      <div className="ml-auto flex flex-wrap items-center gap-1">
        <ToolbarToggle
          active={mode === "duration"}
          onClick={() => onModeChange(mode === "duration" ? "even" : "duration")}
          label={mode === "duration"
            ? t("thread.trajectory.toolbar.compactDuration")
            : t("thread.trajectory.toolbar.evenDuration")}
          icon={<Clock className="h-3.5 w-3.5" aria-hidden />}
        />
        <ToolbarToggle
          active={turnsCollapsed}
          onClick={onToggleTurns}
          label={t("thread.trajectory.toolbar.turns")}
          icon={<Layers className="h-3.5 w-3.5" aria-hidden />}
        />
        <ToolbarToggle
          active={callsCollapsed}
          onClick={onToggleCalls}
          label={t("thread.trajectory.toolbar.calls")}
          icon={<Wrench className="h-3.5 w-3.5" aria-hidden />}
        />
        <label className="relative flex items-center">
          <Search className="pointer-events-none absolute left-2 h-3.5 w-3.5 text-muted-foreground/70" aria-hidden />
          <input
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder={t("thread.trajectory.toolbar.search")}
            aria-label={t("thread.trajectory.toolbar.search")}
            className="h-7 w-40 rounded-full border border-border/60 bg-background/70 pl-7 pr-2 text-[12px] text-foreground outline-none focus:border-primary/60 focus:ring-1 focus:ring-primary/30 sm:w-52"
          />
        </label>
        <span className="inline-flex items-center gap-1 rounded-full bg-muted/60 px-2 py-0.5 text-[11px]">
          <Hash className="h-3 w-3" aria-hidden />
          {t("thread.trajectory.toolbar.loaded", { count: loadedCount })}
        </span>
      </div>
    </div>
  );
}

function ToolbarToggle({
  active,
  onClick,
  label,
  icon,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  icon: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex h-7 items-center gap-1 rounded-full border px-2.5 text-[11px] transition-colors",
        active
          ? "border-primary/40 bg-primary/10 text-foreground"
          : "border-border/60 text-muted-foreground hover:bg-accent/40 hover:text-foreground",
      )}
    >
      {icon}
      {label}
    </button>
  );
}
