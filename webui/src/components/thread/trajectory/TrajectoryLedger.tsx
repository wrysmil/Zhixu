import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  Loader2,
  XCircle,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

import {
  formatClock,
  formatDuration,
  recordName,
  recordSummary,
  type TrajectoryText,
  type TurnGroup,
} from "@/features/trajectory/projection";
import type { TrajectoryRecord, TrajectoryRecordKind } from "@/features/trajectory/types";

const KIND_TONE: Record<TrajectoryRecordKind, string> = {
  system: "bg-slate-400",
  user: "bg-sky-500",
  context: "bg-emerald-500",
  assistant: "bg-violet-500",
  tool: "bg-amber-500",
  subtool: "bg-amber-400",
  compaction: "bg-rose-400",
};

interface TrajectoryLedgerProps {
  groups: TurnGroup[];
  query: string;
  collapsedTurns: Set<string>;
  onToggleTurn: (key: string) => void;
  callsCollapsed: boolean;
  selectedId: string | null;
  onSelect: (recordId: string) => void;
  hasOlder: boolean;
  loadingOlder: boolean;
  onLoadEarlier: () => void;
  loading: boolean;
}

export function TrajectoryLedger({
  groups,
  query,
  collapsedTurns,
  onToggleTurn,
  callsCollapsed,
  selectedId,
  onSelect,
  hasOlder,
  loadingOlder,
  onLoadEarlier,
  loading,
}: TrajectoryLedgerProps) {
  const { t } = useTranslation();
  const text = t as unknown as TrajectoryText;

  if (loading && groups.length === 0) {
    return (
      <div className="flex items-center gap-2 px-4 py-6 text-[12px] text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        {t("thread.trajectory.loading")}
      </div>
    );
  }

  if (groups.length === 0) {
    return (
      <div className="px-4 py-10 text-center text-[12px] text-muted-foreground">
        {query
          ? t("thread.trajectory.searchEmpty")
          : t("thread.trajectory.empty.description")}
      </div>
    );
  }

  return (
    <div data-testid="trajectory-ledger" className="pb-24">
      {hasOlder ? (
        <div className="px-3 py-2">
          <button
            type="button"
            onClick={onLoadEarlier}
            disabled={loadingOlder}
            className="inline-flex items-center gap-1 rounded-full border border-border/60 px-2.5 py-1 text-[11px] text-muted-foreground hover:bg-accent/40 hover:text-foreground disabled:opacity-50"
          >
            {loadingOlder ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> : null}
            {t("thread.trajectory.loadEarlier")}
          </button>
        </div>
      ) : null}

      {groups.map((group) => {
        const turnKey = group.key;
        const collapsed = collapsedTurns.has(turnKey);
        const visible = callsCollapsed
          ? group.records.filter(
              (record) => record.kind !== "tool" && record.kind !== "subtool",
            )
          : group.records;
        const toolCount = group.records.filter(
          (record) => record.kind === "tool" || record.kind === "subtool",
        ).length;
        return (
          <section key={group.key} data-testid="trajectory-turn">
            <button
              type="button"
              aria-expanded={!collapsed}
              onClick={() => onToggleTurn(turnKey)}
              className="sticky top-0 z-10 flex w-full items-center gap-2 border-b border-border/50 bg-background/95 px-3 py-1.5 text-left text-[11px] font-medium text-muted-foreground backdrop-blur"
            >
              {collapsed ? (
                <ChevronRight className="h-3.5 w-3.5" aria-hidden />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" aria-hidden />
              )}
              {group.index != null
                ? t("thread.trajectory.turnLabel", { index: group.index })
                : t("thread.trajectory.backgroundLabel")}
              <span className="text-muted-foreground/70">{formatClock(group.startedAtMs)}</span>
              {toolCount > 0 ? (
                <span className="ml-auto rounded-full bg-muted/70 px-2 py-0.5 text-[10px]">
                  {t("thread.trajectory.toolCount", { count: toolCount })}
                </span>
              ) : null}
            </button>
            {!collapsed ? (
              <div className="py-0.5">
                {visible.map((record) => (
                  <TrajectoryRow
                    key={record.recordId}
                    record={record}
                    selected={record.recordId === selectedId}
                    onSelect={onSelect}
                    text={text}
                  />
                ))}
              </div>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}

function TrajectoryRow({
  record,
  selected,
  onSelect,
  text,
}: {
  record: TrajectoryRecord;
  selected: boolean;
  onSelect: (recordId: string) => void;
  text: TrajectoryText;
}) {
  const { t } = useTranslation();
  const nested = record.kind === "tool" || record.kind === "subtool";
  const name = recordName(record);
  const fullSummary = recordSummary(record, text);
  const summary = nested && name && fullSummary.startsWith(name)
    ? fullSummary.slice(name.length).trim()
    : fullSummary;
  return (
    <button
      type="button"
      data-testid="trajectory-record"
      data-record-id={record.recordId}
      aria-current={selected ? "true" : undefined}
      onClick={() => onSelect(record.recordId)}
      className={cn(
        "flex w-full items-center gap-2 px-3 py-1 text-left text-[12px] transition-colors",
        selected ? "bg-primary/10" : "hover:bg-accent/30",
      )}
      style={{ contentVisibility: "auto", containIntrinsicSize: "28px" }}
    >
      <span
        aria-hidden
        className={cn("h-1.5 w-1.5 shrink-0 rounded-full", KIND_TONE[record.kind])}
      />
      <span className="w-[4.75rem] shrink-0 truncate text-[10px] uppercase tracking-wide text-muted-foreground/90">
        {t(`thread.trajectory.kind.${record.kind}`)}
      </span>
      <span className={cn("min-w-0 flex-1 truncate", nested && "pl-2 text-foreground/90")}>
        {nested ? (
          <span className="font-medium text-foreground/90">{name} </span>
        ) : null}
        <span className="text-muted-foreground">{summary}</span>
      </span>
      <span className="w-14 shrink-0 text-right text-[10px] tabular-nums text-muted-foreground/80">
        {formatDuration(record.durationMs)}
      </span>
      <StatusMark status={record.status} />
    </button>
  );
}

function StatusMark({ status }: { status: TrajectoryRecord["status"] }) {
  const props = { className: "h-3.5 w-3.5 shrink-0", "aria-hidden": true } as const;
  if (status === "running" || status === "requested") {
    return <Loader2 {...props} className="h-3.5 w-3.5 shrink-0 animate-spin text-sky-500" />;
  }
  if (status === "succeeded") return <Check {...props} className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />;
  if (status === "failed" || status === "interrupted") {
    return <AlertTriangle {...props} className="h-3.5 w-3.5 shrink-0 text-rose-500" />;
  }
  if (status === "cancelled" || status === "denied") {
    return <XCircle {...props} className="h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />;
  }
  return <CircleDashed {...props} className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60" />;
}
