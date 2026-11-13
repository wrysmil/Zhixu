import type {
  BlockSummary,
  TrajectoryRecord,
  TrajectoryRecordKind,
} from "./types";

export type TrajectoryText = (key: string, options?: Record<string, unknown>) => string;

export type TrajectoryLane = "input" | "model" | "tool";

export interface TurnGroup {
  key: string;
  turnId: string | null;
  index: number | null;
  records: TrajectoryRecord[];
  startedAtMs: number | null;
}

export interface TimelineBar {
  recordId: string;
  lane: TrajectoryLane;
  startPct: number;
  widthPct: number;
  status: TrajectoryRecord["status"];
  label: string;
  kind: TrajectoryRecordKind;
}

export interface TrajectorySummaryStats {
  turns: number;
  calls: number;
  durationMs: number;
}

const LANE_BY_KIND: Record<TrajectoryRecordKind, TrajectoryLane> = {
  system: "model",
  user: "input",
  context: "input",
  assistant: "model",
  tool: "tool",
  subtool: "tool",
  compaction: "model",
};

const MAX_GAP_MS = 800;

export function recordLane(record: TrajectoryRecord): TrajectoryLane {
  return LANE_BY_KIND[record.kind] ?? "model";
}

export function groupByTurn(records: TrajectoryRecord[]): TurnGroup[] {
  const groups: TurnGroup[] = [];
  const seenTurns = new Map<string, number>();
  let turnCounter = 0;
  for (const record of records) {
    const key = record.turnId ? `turn:${record.turnId}` : `run:${record.runId}`;
    let group = groups[groups.length - 1];
    if (!group || group.key !== key) {
      if (record.turnId && !seenTurns.has(record.turnId)) {
        seenTurns.set(record.turnId, ++turnCounter);
      }
      group = {
        key,
        turnId: record.turnId,
        index: record.turnId ? (seenTurns.get(record.turnId) ?? null) : null,
        records: [],
        startedAtMs: record.startedAtMs,
      };
      groups.push(group);
    }
    group.records.push(record);
    if (record.startedAtMs != null) {
      group.startedAtMs = Math.min(group.startedAtMs ?? record.startedAtMs, record.startedAtMs);
    }
  }
  return groups;
}

export function recordText(record: TrajectoryRecord): string {
  return record.blockSummaries
    .filter((block) => block.kind === "text")
    .map((block) => block.content.preview)
    .filter(Boolean)
    .join(" ")
    .trim();
}

export function recordReasoning(record: TrajectoryRecord): string {
  return record.blockSummaries
    .filter((block) => block.kind === "reasoning")
    .map((block) => block.content.preview)
    .filter(Boolean)
    .join("\n")
    .trim();
}

function compactValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") {
    return value.length > 160 ? `${value.slice(0, 160)}…` : value;
  }
  try {
    const encoded = JSON.stringify(value);
    return encoded.length > 160 ? `${encoded.slice(0, 160)}…` : encoded;
  } catch {
    return String(value);
  }
}

export function recordName(record: TrajectoryRecord): string {
  const extra = record.extra ?? {};
  const name = typeof extra.name === "string" ? extra.name : "";
  if (name) return name;
  if (record.kind === "tool" || record.kind === "subtool") return "tool";
  return record.kind;
}

export function recordSummary(record: TrajectoryRecord, t: TrajectoryText): string {
  const extra = record.extra ?? {};
  if (record.kind === "tool" || record.kind === "subtool") {
    const args = compactValue(extra.argumentsPreview);
    const result = record.blockSummaries.find((block) => block.kind === "result");
    const error = record.blockSummaries.find((block) => block.kind === "error");
    const arrow = result?.content.preview || error?.content.preview || "";
    return [recordName(record), args, arrow ? `→ ${arrow}` : ""]
      .filter(Boolean)
      .join(" ");
  }
  if (record.kind === "compaction") {
    const reason = typeof extra.reason === "string" ? extra.reason : "";
    const summary = record.blockSummaries[0]?.content.preview ?? "";
    return [reason, summary].filter(Boolean).join(" · ");
  }
  const text = recordText(record);
  if (text) return text;
  const toolCall = record.blockSummaries.find((block) => block.kind === "tool-call");
  if (record.kind === "assistant" && toolCall) {
    return t("thread.trajectory.onlyToolCalls", { defaultValue: "Tool calls only" });
  }
  return record.blockSummaries[0]?.content.preview ?? "";
}

export function summarize(records: TrajectoryRecord[]): TrajectorySummaryStats {
  const turns = groupByTurn(records).filter((group) => group.turnId != null).length;
  const calls = records.filter(
    (record) => record.kind === "tool" || record.kind === "subtool",
  ).length;
  let min = Number.POSITIVE_INFINITY;
  let max = 0;
  for (const record of records) {
    if (record.startedAtMs != null) min = Math.min(min, record.startedAtMs);
    const end = record.finishedAtMs ?? record.startedAtMs;
    if (end != null) max = Math.max(max, end);
  }
  return {
    turns,
    calls,
    durationMs: Number.isFinite(min) && max > min ? max - min : 0,
  };
}

function compactScale(records: TrajectoryRecord[]): {
  positions: Map<number, number>;
  total: number;
  startMs: number;
} {
  const points = new Set<number>();
  for (const record of records) {
    if (record.startedAtMs == null) continue;
    points.add(record.startedAtMs);
    points.add(record.finishedAtMs ?? record.startedAtMs + 1);
  }
  const ordered = [...points].sort((a, b) => a - b);
  const positions = new Map<number, number>();
  let total = 0;
  let previous = ordered[0] ?? 0;
  for (const point of ordered) {
    if (point !== previous) {
      total += Math.min(point - previous, MAX_GAP_MS);
      previous = point;
    }
    positions.set(point, total);
  }
  return { positions, total: Math.max(total, 1), startMs: ordered[0] ?? 0 };
}

export function buildTimeline(
  records: TrajectoryRecord[],
  mode: "duration" | "even",
  t: TrajectoryText,
): TimelineBar[] {
  const timed = records.filter((record) => record.startedAtMs != null);
  if (timed.length === 0) return [];
  if (mode === "even") {
    const width = 100 / timed.length;
    return timed.map((record, index) => ({
      recordId: record.recordId,
      lane: recordLane(record),
      startPct: index * width,
      widthPct: Math.max(width * 0.86, 0.6),
      status: record.status,
      label: recordSummary(record, t),
      kind: record.kind,
    }));
  }
  const { positions, total } = compactScale(timed);
  return timed.map((record) => {
    const start = positions.get(record.startedAtMs ?? 0) ?? 0;
    const end = positions.get(record.finishedAtMs ?? (record.startedAtMs ?? 0) + 1) ?? start + 1;
    return {
      recordId: record.recordId,
      lane: recordLane(record),
      startPct: (start / total) * 100,
      widthPct: Math.max(((end - start) / total) * 100, 0.6),
      status: record.status,
      label: recordSummary(record, t),
      kind: record.kind,
    };
  });
}

export function recordsInRange(
  records: TrajectoryRecord[],
  startMs: number,
  endMs: number,
): TrajectoryRecord[] {
  if (endMs < startMs) [startMs, endMs] = [endMs, startMs];
  return records.filter((record) => {
    if (record.startedAtMs == null) return false;
    const end = record.finishedAtMs ?? record.startedAtMs;
    return record.startedAtMs <= endMs && end >= startMs;
  });
}

export function recordMatches(record: TrajectoryRecord, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  const haystack = [
    record.recordId,
    record.kind,
    recordName(record),
    record.status,
    record.spanId ?? "",
    record.extra?.provider,
    record.extra?.model,
    record.requestRef?.preview,
    ...record.blockSummaries.map((block: BlockSummary) => block.content.preview),
  ]
    .filter((value): value is string => typeof value === "string")
    .join(" ")
    .toLowerCase();
  return haystack.includes(needle);
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m${seconds.toString().padStart(2, "0")}s`;
}

export function formatClock(ms: number | null | undefined): string {
  if (ms == null) return "—";
  const date = new Date(ms);
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
