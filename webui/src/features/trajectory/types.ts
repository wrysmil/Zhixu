export type TrajectorySpanStatus =
  | "requested"
  | "running"
  | "succeeded"
  | "failed"
  | "denied"
  | "cancelled"
  | "interrupted"
  | "skipped";

export type TrajectoryRecordKind =
  | "system"
  | "user"
  | "context"
  | "assistant"
  | "tool"
  | "subtool"
  | "compaction";

export type ContentUnavailableReason =
  | "not_recorded"
  | "provider_managed"
  | "expired"
  | "budget_exceeded"
  | "capture_failed"
  | null;

export interface ContentRef {
  bodyId: string | null;
  preview: string;
  contentType: string;
  storedBytes: number;
  originalBytes: number | null;
  redacted: boolean;
  truncated: boolean;
  unavailableReason: ContentUnavailableReason;
}

export interface BlockSummary {
  index: number;
  kind: string;
  content: ContentRef;
}

export interface TrajectoryRecord {
  recordId: string;
  kind: TrajectoryRecordKind;
  runId: string;
  turnId: string | null;
  step: number | null;
  spanId: string | null;
  parentSpanId: string | null;
  modelOrder: number | null;
  status: TrajectorySpanStatus;
  startedAtMs: number | null;
  finishedAtMs: number | null;
  durationMs: number | null;
  anchorSeq: number;
  recordRevision: number;
  blockSummaries: BlockSummary[];
  requestRef: ContentRef | null;
  schemaRef: ContentRef | null;
  extra?: Record<string, unknown>;
}

export interface TrajectoryCoverage {
  status: "complete" | "partial" | "unknown";
  reasons: string[];
  contentMode: "off" | "metadata" | "standard" | "raw-chunks";
  attemptVisibility: string;
}

export interface RecordPage {
  schemaVersion: number;
  records: TrajectoryRecord[];
  ancestorStubs: Array<{ spanId: string; parentSpanId: string | null; label: string }>;
  snapshotSeq: number;
  historyGeneration: number;
  nextBeforeCursor: string | null;
  hasOlder: boolean;
  retainedFromSeq: number;
  coverage: TrajectoryCoverage;
}

export interface TrajectoryEvent {
  schemaVersion: number;
  eventId: string;
  sessionId: string;
  seq: number;
  traceId: string;
  runId: string;
  turnId: string | null;
  step: number | null;
  spanId: string | null;
  parentSpanId: string | null;
  executionId: string | null;
  logicalRequestId: string | null;
  modelCallId: string | null;
  toolCallId: string | null;
  type: string;
  ignorable: boolean;
  timeMs: number;
  payload: Record<string, unknown>;
}

export interface EventPage {
  schemaVersion: number;
  events: TrajectoryEvent[];
  recordUpdates: TrajectoryRecord[];
  snapshotSeq: number;
  retainedFromSeq: number;
  durableSeq: number;
  historyGeneration: number;
  nextBeforeSeq: number | null;
  nextAfterSeq: number;
  hasOlder: boolean;
  hasMore: boolean;
  coverage: TrajectoryCoverage;
}

export interface TrajectoryRun {
  runId: string;
  traceId: string;
  turnId: string | null;
  source: string;
  status: string;
  startedAtMs: number | null;
  finishedAtMs: number | null;
  durationMs: number | null;
  coverage: TrajectoryCoverage;
}

export interface TrajectorySummary {
  schemaVersion: number;
  sessionId: string;
  mode: "off" | "metadata" | "standard" | "raw-chunks";
  available: boolean;
  persistence: "sqlite" | "memory";
  retainedFromSeq: number;
  durableSeq: number;
  historyGeneration: number;
  coverage: TrajectoryCoverage;
  runs: TrajectoryRun[];
  capabilities: { events: boolean; bodies: boolean; memory: boolean };
}

export interface TrajectoryBodyPage {
  bodyId: string;
  contentType: string;
  text: string;
  offsetBytes: number;
  nextOffsetBytes: number;
  hasMore: boolean;
  storedBytes: number;
  originalBytes: number;
  redacted: boolean;
  truncated: boolean;
}
