import type {
  BlockSummary,
  ContentRef,
  EventPage,
  RecordPage,
  TrajectoryBodyPage,
  TrajectoryCoverage,
  TrajectoryRecord,
  TrajectoryRecordKind,
  TrajectorySpanStatus,
  TrajectorySummary,
} from "./types";

const KINDS = new Set<TrajectoryRecordKind>([
  "system", "user", "context", "assistant", "tool", "subtool", "compaction",
]);
const STATUSES = new Set<TrajectorySpanStatus>([
  "requested", "running", "succeeded", "failed", "denied", "cancelled",
  "interrupted", "skipped",
]);
const UNAVAILABLE = new Set([
  "not_recorded", "provider_managed", "expired", "budget_exceeded", "capture_failed",
]);

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function parseContentRef(value: unknown): ContentRef | null {
  const raw = asRecord(value);
  if (!raw) return null;
  const reason = asString(raw.unavailableReason);
  return {
    bodyId: asString(raw.bodyId) || null,
    preview: asString(raw.preview),
    contentType: asString(raw.contentType) || "text/plain",
    storedBytes: asNumber(raw.storedBytes) ?? 0,
    originalBytes: asNumber(raw.originalBytes),
    redacted: Boolean(raw.redacted),
    truncated: Boolean(raw.truncated),
    unavailableReason: UNAVAILABLE.has(reason) ? (reason as ContentRef["unavailableReason"]) : null,
  };
}

function parseBlock(value: unknown): BlockSummary | null {
  const raw = asRecord(value);
  if (!raw) return null;
  const content = parseContentRef(raw.content);
  if (!content) return null;
  return {
    index: asNumber(raw.index) ?? 0,
    kind: asString(raw.kind) || "text",
    content,
  };
}

export function parseRecord(value: unknown): TrajectoryRecord | null {
  const raw = asRecord(value);
  if (!raw) return null;
  const recordId = asString(raw.recordId);
  const kind = asString(raw.kind) as TrajectoryRecordKind;
  const status = asString(raw.status) as TrajectorySpanStatus;
  if (!recordId || !KINDS.has(kind) || !STATUSES.has(status)) return null;
  const blocks = Array.isArray(raw.blockSummaries)
    ? raw.blockSummaries.map(parseBlock).filter((item): item is BlockSummary => item !== null)
    : [];
  return {
    recordId,
    kind,
    runId: asString(raw.runId),
    turnId: asString(raw.turnId) || null,
    step: asNumber(raw.step),
    spanId: asString(raw.spanId) || null,
    parentSpanId: asString(raw.parentSpanId) || null,
    modelOrder: asNumber(raw.modelOrder),
    status,
    startedAtMs: asNumber(raw.startedAtMs),
    finishedAtMs: asNumber(raw.finishedAtMs),
    durationMs: asNumber(raw.durationMs),
    anchorSeq: asNumber(raw.anchorSeq) ?? 0,
    recordRevision: asNumber(raw.recordRevision) ?? 0,
    blockSummaries: blocks,
    requestRef: parseContentRef(raw.requestRef),
    schemaRef: parseContentRef(raw.schemaRef),
    extra: asRecord(raw.extra) ?? {},
  };
}

export function parseCoverage(value: unknown): TrajectoryCoverage {
  const raw = asRecord(value);
  const status = asString(raw?.status);
  const mode = asString(raw?.contentMode);
  return {
    status: status === "partial" || status === "unknown" ? status : "complete",
    reasons: Array.isArray(raw?.reasons)
      ? raw.reasons.filter((item): item is string => typeof item === "string")
      : [],
    contentMode: mode === "off" || mode === "metadata" || mode === "raw-chunks"
      ? mode
      : "standard",
    attemptVisibility: asString(raw?.attemptVisibility) || "provider_call",
  };
}

export function parseRecordList(value: unknown): TrajectoryRecord[] {
  if (!Array.isArray(value)) return [];
  return value
    .map(parseRecord)
    .filter((item): item is TrajectoryRecord => item !== null);
}

export function parseRecordPage(value: unknown): RecordPage | null {
  const raw = asRecord(value);
  if (!raw) return null;
  const records = parseRecordList(raw.records);
  return {
    schemaVersion: asNumber(raw.schemaVersion) ?? 1,
    records,
    ancestorStubs: Array.isArray(raw.ancestorStubs)
      ? raw.ancestorStubs.map(asRecord).filter(Boolean) as RecordPage["ancestorStubs"]
      : [],
    snapshotSeq: asNumber(raw.snapshotSeq) ?? 0,
    historyGeneration: asNumber(raw.historyGeneration) ?? 1,
    nextBeforeCursor: asString(raw.nextBeforeCursor) || null,
    hasOlder: Boolean(raw.hasOlder),
    retainedFromSeq: asNumber(raw.retainedFromSeq) ?? 1,
    coverage: parseCoverage(raw.coverage),
  };
}

export function parseEventPage(value: unknown): EventPage | null {
  const raw = asRecord(value);
  if (!raw) return null;
  const events = Array.isArray(raw.events)
    ? raw.events.filter((item): item is EventPage["events"][number] => asRecord(item) !== null)
        .map((item) => item as EventPage["events"][number])
    : [];
  const updates = parseRecordList(raw.recordUpdates);
  return {
    schemaVersion: asNumber(raw.schemaVersion) ?? 1,
    events,
    recordUpdates: updates,
    snapshotSeq: asNumber(raw.snapshotSeq) ?? 0,
    retainedFromSeq: asNumber(raw.retainedFromSeq) ?? 1,
    durableSeq: asNumber(raw.durableSeq) ?? 0,
    historyGeneration: asNumber(raw.historyGeneration) ?? 1,
    nextBeforeSeq: asNumber(raw.nextBeforeSeq),
    nextAfterSeq: asNumber(raw.nextAfterSeq) ?? 0,
    hasOlder: Boolean(raw.hasOlder),
    hasMore: Boolean(raw.hasMore),
    coverage: parseCoverage(raw.coverage),
  };
}

export function parseSummary(value: unknown): TrajectorySummary | null {
  const raw = asRecord(value);
  if (!raw) return null;
  const sessionId = asString(raw.sessionId);
  if (!sessionId) return null;
  const mode = asString(raw.mode);
  return {
    schemaVersion: asNumber(raw.schemaVersion) ?? 1,
    sessionId,
    mode: mode === "off" || mode === "metadata" || mode === "raw-chunks" ? mode : "standard",
    available: Boolean(raw.available),
    persistence: asString(raw.persistence) === "memory" ? "memory" : "sqlite",
    retainedFromSeq: asNumber(raw.retainedFromSeq) ?? 1,
    durableSeq: asNumber(raw.durableSeq) ?? 0,
    historyGeneration: asNumber(raw.historyGeneration) ?? 1,
    coverage: parseCoverage(raw.coverage),
    runs: Array.isArray(raw.runs)
      ? raw.runs.map((item) => {
          const run = asRecord(item) ?? {};
          return {
            runId: asString(run.runId),
            traceId: asString(run.traceId),
            turnId: asString(run.turnId) || null,
            source: asString(run.source),
            status: asString(run.status),
            startedAtMs: asNumber(run.startedAtMs),
            finishedAtMs: asNumber(run.finishedAtMs),
            durationMs: asNumber(run.durationMs),
            coverage: parseCoverage(run.coverage),
          };
        })
      : [],
    capabilities: {
      events: true,
      bodies: true,
      memory: Boolean(asRecord(raw.capabilities)?.memory),
    },
  };
}

export function parseBodyPage(value: unknown): TrajectoryBodyPage | null {
  const raw = asRecord(value);
  if (!raw) return null;
  return {
    bodyId: asString(raw.bodyId),
    contentType: asString(raw.contentType) || "text/plain",
    text: asString(raw.text),
    offsetBytes: asNumber(raw.offsetBytes) ?? 0,
    nextOffsetBytes: asNumber(raw.nextOffsetBytes) ?? 0,
    hasMore: Boolean(raw.hasMore),
    storedBytes: asNumber(raw.storedBytes) ?? 0,
    originalBytes: asNumber(raw.originalBytes) ?? 0,
    redacted: Boolean(raw.redacted),
    truncated: Boolean(raw.truncated),
  };
}
