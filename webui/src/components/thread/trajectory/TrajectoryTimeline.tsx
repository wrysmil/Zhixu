import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

import {
  buildTimeline,
  type TimelineBar,
  type TrajectoryLane,
  type TrajectoryText,
} from "@/features/trajectory/projection";
import type { TrajectoryRecord } from "@/features/trajectory/types";

const LANES: TrajectoryLane[] = ["input", "model", "tool"];

const LANE_TONE: Record<TrajectoryLane, string> = {
  input: "bg-sky-500/70 dark:bg-sky-400/70",
  model: "bg-violet-500/70 dark:bg-violet-400/70",
  tool: "bg-amber-500/75 dark:bg-amber-400/75",
};

const LANE_TONE_ERROR: Record<TrajectoryLane, string> = {
  input: "bg-rose-500/80",
  model: "bg-rose-500/80",
  tool: "bg-rose-500/80",
};

interface TrajectoryTimelineProps {
  records: TrajectoryRecord[];
  mode: "duration" | "even";
  selectedId: string | null;
  onSelect: (recordId: string) => void;
  onRangeSelect: (recordIds: string[]) => void;
}

export function TrajectoryTimeline({
  records,
  mode,
  selectedId,
  onSelect,
  onRangeSelect,
}: TrajectoryTimelineProps) {
  const { t } = useTranslation();
  const timelineText = t as unknown as TrajectoryText;
  const bars = useMemo(() => buildTimeline(records, mode, timelineText), [records, mode, timelineText]);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [drag, setDrag] = useState<{ start: number; end: number } | null>(null);

  const pctFromEvent = (clientX: number): number => {
    const element = containerRef.current;
    if (!element) return 0;
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0) return 0;
    return Math.min(100, Math.max(0, ((clientX - rect.left) / rect.width) * 100));
  };

  const finishDrag = (clientX: number) => {
    if (!drag) return;
    const end = pctFromEvent(clientX);
    const start = Math.min(drag.start, end);
    const stop = Math.max(drag.start, end);
    setDrag(null);
    if (stop - start < 0.8) return;
    const ids = bars
      .filter((bar) => {
        const barStart = bar.startPct;
        const barEnd = bar.startPct + bar.widthPct;
        return barStart <= stop && barEnd >= start;
      })
      .map((bar) => bar.recordId);
    onRangeSelect(ids);
  };

  if (bars.length === 0) return null;

  return (
    <div
      data-testid="trajectory-timeline"
      className="shrink-0 border-b border-border/60 px-3 py-2"
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        setDrag({ start: pctFromEvent(event.clientX), end: pctFromEvent(event.clientX) });
        (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
      }}
      onPointerMove={(event) => {
        if (!drag) return;
        setDrag({ ...drag, end: pctFromEvent(event.clientX) });
      }}
      onPointerUp={(event) => finishDrag(event.clientX)}
      onPointerCancel={() => setDrag(null)}
    >
      <div className="flex gap-2">
        <div className="flex w-10 shrink-0 flex-col justify-around py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground/80">
          {LANES.map((lane) => (
            <span key={lane} className="leading-none">
              {t(`thread.trajectory.lanes.${lane}`)}
            </span>
          ))}
        </div>
        <div ref={containerRef} className="relative flex-1 select-none">
          {LANES.map((lane) => (
            <div
              key={lane}
              data-testid={`trajectory-lane-${lane}`}
              className="relative h-5 rounded bg-muted/35"
            >
              {bars.filter((bar) => bar.lane === lane).map((bar) => (
                <TimelineBar
                  key={bar.recordId}
                  bar={bar}
                  lane={lane}
                  selected={bar.recordId === selectedId}
                  onSelect={onSelect}
                />
              ))}
            </div>
          ))}
          {drag && Math.abs(drag.end - drag.start) >= 0.8 ? (
            <div
              aria-hidden
              className="pointer-events-none absolute inset-y-0 rounded bg-primary/15 ring-1 ring-primary/40"
              style={{
                left: `${Math.min(drag.start, drag.end)}%`,
                width: `${Math.abs(drag.end - drag.start)}%`,
              }}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function TimelineBar({
  bar,
  lane,
  selected,
  onSelect,
}: {
  bar: TimelineBar;
  lane: TrajectoryLane;
  selected: boolean;
  onSelect: (recordId: string) => void;
}) {
  const failed = bar.status === "failed" || bar.status === "cancelled" || bar.status === "interrupted";
  return (
    <button
      type="button"
      title={bar.label}
      aria-label={bar.label}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(bar.recordId);
      }}
      onPointerDown={(event) => event.stopPropagation()}
      className={cn(
        "absolute top-1 h-3 min-w-[3px] rounded-sm transition-shadow",
        failed ? LANE_TONE_ERROR[lane] : LANE_TONE[lane],
        selected ? "ring-2 ring-foreground/70" : "hover:ring-1 hover:ring-foreground/40",
      )}
      style={{ left: `${bar.startPct}%`, width: `${bar.widthPct}%` }}
    />
  );
}
