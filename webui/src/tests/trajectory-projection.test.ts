import { describe, expect, it } from "vitest";

import { parseRecordPage, parseEventPage, parseSummary } from "@/features/trajectory/parser";
import {
  buildTimeline,
  formatDuration,
  groupByTurn,
  recordMatches,
  recordSummary,
  recordsInRange,
  summarize,
} from "@/features/trajectory/projection";
import type { ContentRef, TrajectoryRecord } from "@/features/trajectory/types";

const text = (preview: string): ContentRef => ({
  bodyId: `body-${preview}`,
  preview,
  contentType: "text/plain",
  storedBytes: preview.length,
  originalBytes: preview.length,
  redacted: false,
  truncated: false,
  unavailableReason: null,
});

function record(overrides: Partial<TrajectoryRecord>): TrajectoryRecord {
  return {
    recordId: "record-1",
    kind: "assistant",
    runId: "run-1",
    turnId: "turn-1",
    step: 0,
    spanId: null,
    parentSpanId: null,
    modelOrder: 0,
    status: "succeeded",
    startedAtMs: 1000,
    finishedAtMs: 1500,
    durationMs: 500,
    anchorSeq: 1,
    recordRevision: 1,
    blockSummaries: [],
    requestRef: null,
    schemaRef: null,
    extra: {},
    ...overrides,
  };
}

const t = (key: string, options?: Record<string, unknown>) =>
  key === "thread.trajectory.onlyToolCalls" ? "only tool calls" : String(options?.defaultValue ?? key);

describe("trajectory projection", () => {
  it("groups consecutive records by visible turn", () => {
    const groups = groupByTurn([
      record({ recordId: "u1", kind: "user", turnId: "turn-1", step: -1 }),
      record({ recordId: "a1", turnId: "turn-1" }),
      record({ recordId: "t1", kind: "tool", turnId: "turn-1", modelOrder: 1 }),
      record({ recordId: "a2", turnId: "turn-2", step: 0 }),
    ]);
    expect(groups.map((group) => group.records.length)).toEqual([3, 1]);
    expect(groups.map((group) => group.index)).toEqual([1, 2]);
  });

  it("builds three lanes with compacted positions", () => {
    const records = [
      record({ recordId: "u1", kind: "user", turnId: "turn-1", startedAtMs: 0, finishedAtMs: 100 }),
      record({ recordId: "a1", startedAtMs: 10_000, finishedAtMs: 12_000 }),
      record({ recordId: "t1", kind: "tool", startedAtMs: 11_000, finishedAtMs: 11_500 }),
    ];
    const bars = buildTimeline(records, "duration", t);
    expect(bars.map((bar) => bar.lane)).toEqual(["input", "model", "tool"]);
    for (const bar of bars) {
      expect(bar.startPct).toBeGreaterThanOrEqual(0);
      expect(bar.startPct + bar.widthPct).toBeLessThanOrEqual(100.0001);
    }
    const even = buildTimeline(records, "even", t);
    expect(even[0].startPct).toBe(0);
    expect(even[2].startPct).toBeGreaterThan(even[1].startPct);
  });

  it("summarizes turns, tool calls and duration", () => {
    const stats = summarize([
      record({ recordId: "u1", kind: "user", turnId: "turn-1", startedAtMs: 100, finishedAtMs: 100 }),
      record({ recordId: "t1", kind: "tool", turnId: "turn-1", startedAtMs: 200, finishedAtMs: 600 }),
      record({ recordId: "a2", turnId: "turn-2", startedAtMs: 900, finishedAtMs: 1200 }),
    ]);
    expect(stats.turns).toBe(2);
    expect(stats.calls).toBe(1);
    expect(stats.durationMs).toBe(1100);
  });

  it("formats tool rows as name, arguments and result", () => {
    const summary = recordSummary(record({
      recordId: "t1",
      kind: "tool",
      extra: { name: "exec", argumentsPreview: "{\"command\":\"npm test\"}" },
      blockSummaries: [{ index: 0, kind: "result", content: text("exit 0") }],
    }), t);
    expect(summary).toContain("exec");
    expect(summary).toContain("npm test");
    expect(summary).toContain("exit 0");
  });

  it("marks assistant records that only issued tool calls", () => {
    const summary = recordSummary(record({
      blockSummaries: [{ index: 0, kind: "tool-call", content: text("[{\"name\":\"exec\"}]") }],
    }), t);
    expect(summary).toBe("only tool calls");
  });

  it("searches names, ids and previews; ranges use intersection", () => {
    const records = [
      record({ recordId: "tool:1", kind: "tool", startedAtMs: 100, finishedAtMs: 300,
        extra: { name: "web_search" } }),
      record({ recordId: "a1", startedAtMs: 400, finishedAtMs: 800 }),
    ];
    expect(recordMatches(records[0], "web_search")).toBe(true);
    expect(recordMatches(records[1], "missing")).toBe(false);
    expect(recordsInRange(records, 250, 500).map((item) => item.recordId)).toEqual([
      "tool:1", "a1",
    ]);
  });

  it("formats durations", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(120)).toBe("120ms");
    expect(formatDuration(1500)).toBe("1.5s");
    expect(formatDuration(65_000)).toBe("1m05s");
  });
});

describe("trajectory parser", () => {
  it("parses record pages and drops invalid records", () => {
    const page = parseRecordPage({
      records: [
        {
          recordId: "a1", kind: "assistant", runId: "r1", status: "succeeded",
          blockSummaries: [{ index: 0, kind: "text", content: text("hello") }],
          anchorSeq: 3, recordRevision: 4,
        },
        { recordId: "bad", kind: "unknown", runId: "r1", status: "succeeded" },
      ],
      snapshotSeq: 5, historyGeneration: 1, retainedFromSeq: 1, hasOlder: false,
      coverage: { status: "partial", reasons: ["budget"], contentMode: "standard" },
    });
    expect(page?.records).toHaveLength(1);
    expect(page?.records[0].blockSummaries[0].content.preview).toBe("hello");
    expect(page?.coverage.status).toBe("partial");
  });

  it("parses event pages and summary", () => {
    const events = parseEventPage({
      events: [{ eventId: "e1", seq: 1, type: "run.started", payload: {} }],
      recordUpdates: [], snapshotSeq: 1, retainedFromSeq: 1, durableSeq: 1,
      historyGeneration: 1, nextAfterSeq: 1, hasMore: false, hasOlder: false,
    });
    expect(events?.events).toHaveLength(1);
    expect(events?.nextAfterSeq).toBe(1);

    const summary = parseSummary({
      sessionId: "websocket:chat-1", mode: "standard", available: true,
      durableSeq: 3, coverage: { status: "complete", reasons: [], contentMode: "standard" },
      runs: [],
    });
    expect(summary?.sessionId).toBe("websocket:chat-1");
    expect(summary?.durableSeq).toBe(3);
  });
});
