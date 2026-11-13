import { Activity, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

import { recordMatches, summarize } from "@/features/trajectory/projection";
import { useTrajectory, type TrajectoryWsClient } from "@/features/trajectory/useTrajectory";

import { TrajectoryInspector } from "./TrajectoryInspector";
import { TrajectoryLedger } from "./TrajectoryLedger";
import { TrajectoryTimeline } from "./TrajectoryTimeline";
import { TrajectoryToolbar } from "./TrajectoryToolbar";

interface TrajectoryPanelProps {
  sessionKey: string | null;
  active: boolean;
  getToken: () => string;
  wsClient?: TrajectoryWsClient | null;
  composer?: ReactNode;
}

export function TrajectoryPanel({
  sessionKey,
  active,
  getToken,
  wsClient = null,
  composer,
}: TrajectoryPanelProps) {
  const { t } = useTranslation();
  const trajectory = useTrajectory({ sessionKey, active, getToken, wsClient });
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"duration" | "even">("duration");
  const [turnsCollapsed, setTurnsCollapsed] = useState(false);
  const [callsCollapsed, setCallsCollapsed] = useState(false);
  const [collapsedTurns, setCollapsedTurns] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rangeIds, setRangeIds] = useState<Set<string> | null>(null);
  const [inspectorWidth, setInspectorWidth] = useState(380);
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined"
    && window.matchMedia("(max-width: 860px)").matches);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 860px)");
    const update = () => setNarrow(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);

  useEffect(() => {
    setSelectedId(null);
    setRangeIds(null);
    setCollapsedTurns(new Set());
  }, [sessionKey]);

  const stats = useMemo(() => summarize(trajectory.records), [trajectory.records]);
  const filtered = useMemo(() => trajectory.records.filter((record) => {
    if (rangeIds && !rangeIds.has(record.recordId)) return false;
    return recordMatches(record, query);
  }), [trajectory.records, rangeIds, query]);
  const groups = useMemo(
    () => trajectory.groups
      .map((group) => ({
        ...group,
        records: group.records.filter((record) => filtered.includes(record)),
      }))
      .filter((group) => group.records.length > 0),
    [trajectory.groups, filtered],
  );
  const selected = useMemo(
    () => trajectory.records.find((record) => record.recordId === selectedId) ?? null,
    [trajectory.records, selectedId],
  );

  useEffect(() => {
    if (turnsCollapsed) {
      setCollapsedTurns(new Set(trajectory.groups.map((group) => group.key)));
    } else {
      setCollapsedTurns(new Set());
    }
  }, [turnsCollapsed, trajectory.groups]);

  const toggleTurn = (key: string) => {
    setCollapsedTurns((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const startResize = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = inspectorWidth;
    const move = (moveEvent: PointerEvent) => {
      setInspectorWidth(Math.min(640, Math.max(320, startWidth + (startX - moveEvent.clientX))));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  if (!sessionKey) {
    return (
      <div data-testid="trajectory-panel" className="flex h-full items-center justify-center px-6 text-center text-[12px] text-muted-foreground">
        {t("thread.trajectory.empty.noSession")}
      </div>
    );
  }

  return (
    <div data-testid="trajectory-panel" className="relative flex min-h-0 flex-1 flex-col">
      <TrajectoryToolbar
        stats={stats}
        loadedCount={trajectory.records.length}
        mode={mode}
        onModeChange={setMode}
        turnsCollapsed={turnsCollapsed}
        onToggleTurns={() => setTurnsCollapsed((value) => !value)}
        callsCollapsed={callsCollapsed}
        onToggleCalls={() => setCallsCollapsed((value) => !value)}
        query={query}
        onQueryChange={setQuery}
        coverage={trajectory.coverage}
      />

      {trajectory.notice === "cursorExpired" ? (
        <Notice
          message={t("thread.trajectory.notice.cursorExpired")}
          action={t("thread.trajectory.notice.reload")}
          onAction={() => {
            trajectory.clearNotice();
            void trajectory.reload();
          }}
        />
      ) : null}
      {trajectory.error ? (
        <Notice
          message={trajectory.error}
          action={t("thread.trajectory.notice.retry")}
          onAction={() => void trajectory.reload()}
        />
      ) : null}
      {rangeIds ? (
        <Notice
          message={t("thread.trajectory.notice.range", { count: rangeIds.size })}
          action={t("thread.trajectory.notice.clearRange")}
          onAction={() => setRangeIds(null)}
        />
      ) : null}

      <TrajectoryTimeline
        records={trajectory.records}
        mode={mode}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onRangeSelect={(ids) => setRangeIds(new Set(ids))}
      />

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-y-auto">
          {trajectory.records.length === 0 && !trajectory.loading ? (
            <EmptyState
              unavailable={trajectory.summary?.available === false}
              mode={trajectory.summary?.mode ?? null}
            />
          ) : (
            <TrajectoryLedger
              groups={groups}
              query={query}
              collapsedTurns={collapsedTurns}
              onToggleTurn={toggleTurn}
              callsCollapsed={callsCollapsed}
              selectedId={selectedId}
              onSelect={setSelectedId}
              hasOlder={trajectory.hasOlder}
              loadingOlder={trajectory.loadingOlder}
              onLoadEarlier={() => void trajectory.loadEarlier()}
              loading={trajectory.loading}
            />
          )}
        </div>

        {selected ? (
          <>
            {!narrow ? (
              <div
                role="separator"
                aria-orientation="vertical"
                aria-label={t("thread.trajectory.inspector.resize")}
                onPointerDown={startResize}
                className="w-1 shrink-0 cursor-col-resize border-l border-border/60 hover:bg-primary/20"
              />
            ) : null}
            <div
              className={cn("flex min-h-0", narrow ? "absolute inset-0" : "")}
              style={narrow ? undefined : { width: inspectorWidth }}
            >
              <TrajectoryInspector
                record={selected}
                onClose={() => setSelectedId(null)}
                bodies={trajectory.bodies}
                bodyLoading={trajectory.bodyLoading}
                loadBody={trajectory.loadBody}
                overlay={narrow}
              />
            </div>
          </>
        ) : null}
      </div>
      {composer ? <div className="shrink-0">{composer}</div> : null}
    </div>
  );
}

function Notice({
  message,
  action,
  onAction,
}: {
  message: string;
  action: string;
  onAction: () => void;
}) {
  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-[11px] text-amber-800 dark:text-amber-200">
      <span className="min-w-0 flex-1 truncate">{message}</span>
      <button
        type="button"
        onClick={onAction}
        className="inline-flex shrink-0 items-center gap-1 rounded-full border border-amber-600/40 px-2 py-0.5 hover:bg-amber-500/20"
      >
        <RefreshCw className="h-3 w-3" aria-hidden />
        {action}
      </button>
    </div>
  );
}

function EmptyState({ unavailable, mode }: { unavailable: boolean; mode: string | null }) {
  const { t } = useTranslation();
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <Activity className="h-6 w-6 text-muted-foreground" aria-hidden />
      <div className="text-[13px] font-medium text-foreground/90">
        {unavailable
          ? t("thread.trajectory.empty.unavailable")
          : t("thread.trajectory.empty.title")}
      </div>
      <p className="max-w-md text-[12px] text-muted-foreground">
        {unavailable
          ? t("thread.trajectory.empty.unavailableDescription")
          : t("thread.trajectory.empty.description")}
      </p>
      {mode ? (
        <div className="rounded-full bg-muted/60 px-2.5 py-0.5 text-[10px] text-muted-foreground">
          {t("thread.trajectory.empty.captureMode", { mode })}
        </div>
      ) : null}
    </div>
  );
}
