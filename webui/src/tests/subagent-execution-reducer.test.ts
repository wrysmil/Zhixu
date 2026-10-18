import { describe, expect, it } from "vitest";

import { parseExecutionEvent, parseExecutionSnapshot } from "@/features/subagent-execution/parser";
import {
  applyExecutionEvent,
  applyExecutionEvents,
  applyExecutionSnapshot,
} from "@/features/subagent-execution/reducer";
import type { ExecutionActivity, ExecutionEvent, ExecutionSnapshot } from "@/features/subagent-execution/types";

const CHAT_ID = "chat-1";
const TURN_ID = "turn-1";
const INVOCATION_ID = "11111111-1111-4111-8111-111111111111";
const ACTIVITY_ID = "22222222-2222-4222-8222-222222222222";

function snapshot(overrides: Partial<ExecutionSnapshot> = {}): ExecutionSnapshot {
  return {
    invocationId: INVOCATION_ID,
    chatId: CHAT_ID,
    parentTurnId: TURN_ID,
    parentToolCallId: "tool-call-1",
    agentId: "researcher",
    agentName: "调研助手",
    title: "查询近期金价",
    instructionSummary: "给出近期金价与来源",
    mode: "background",
    status: "running",
    phase: "preparing",
    createdAt: "2026-10-18T08:00:00Z",
    startedAt: null,
    finishedAt: null,
    queueDurationMs: null,
    durationMs: null,
    lastAction: null,
    completedToolCount: 0,
    lastSeq: 0,
    result: null,
    activities: [],
    ...overrides,
  };
}

function activity(overrides: Partial<ExecutionActivity> = {}): ExecutionActivity {
  return {
    activityId: ACTIVITY_ID,
    kind: "tool",
    toolName: "web_search",
    status: "running",
    label: "搜索网页",
    startedAt: "2026-10-18T08:00:02Z",
    finishedAt: null,
    ...overrides,
  };
}

function rawEvent(seq: number, type: string, executionOverrides: Record<string, unknown> = {}, activityValue?: Record<string, unknown>): ExecutionEvent {
  const parsed = parseExecutionEvent({
    schemaVersion: 1,
    invocationId: INVOCATION_ID,
    chatId: CHAT_ID,
    parentTurnId: TURN_ID,
    parentToolCallId: "tool-call-1",
    seq,
    occurredAt: "2026-10-18T08:00:02Z",
    type,
    data: {
      execution: {
        invocationId: INVOCATION_ID,
        chatId: CHAT_ID,
        parentTurnId: TURN_ID,
        parentToolCallId: "tool-call-1",
        agentId: "researcher",
        agentName: "调研助手",
        title: "查询近期金价",
        mode: "background",
        status: "running",
        phase: "tools",
        createdAt: "2026-10-18T08:00:00Z",
        startedAt: "2026-10-18T08:00:01Z",
        finishedAt: null,
        queueDurationMs: 1000,
        durationMs: null,
        lastAction: null,
        completedToolCount: 0,
        lastSeq: seq,
        ...executionOverrides,
      },
      ...(activityValue ? { activity: activityValue } : {}),
    },
  });
  if (!parsed) throw new Error("fixture failed to parse");
  return parsed;
}

describe("子 Agent 执行 reducer", () => {
  it("乱序 seq 1,3,3,2,4 缓存后按连续序各应用一次", () => {
    let state = applyExecutionEvent(null, rawEvent(1, "execution.created", { status: "queued", phase: "preparing" }));
    state = applyExecutionEvent(state, rawEvent(3, "execution.activity", { completedToolCount: 0 }, activity()));
    expect(state.needsReplay).toBe(true);
    expect(state.appliedSeqs).toEqual([1]);

    state = applyExecutionEvent(state, rawEvent(3, "execution.activity", { completedToolCount: 0 }, activity()));
    state = applyExecutionEvent(state, rawEvent(2, "execution.started", { phase: "model" }));
    state = applyExecutionEvent(state, rawEvent(4, "execution.phase_changed", { phase: "tools", lastAction: "搜索网页" }));

    expect(state.appliedSeqs).toEqual([1, 2, 3, 4]);
    expect(state.timelineCursor).toBe(4);
    expect(state.needsReplay).toBe(false);
    expect(state.activities).toHaveLength(1);
  });

  it("同一 activityId 的开始/结束只保留一行，completedToolCount 只记一次", () => {
    const started = applyExecutionEvent(null, rawEvent(1, "execution.activity", {}, activity()));
    const finished = applyExecutionEvent(started, rawEvent(2, "execution.activity", {
      completedToolCount: 1,
      lastAction: "搜索网页",
    }, activity({ status: "succeeded", finishedAt: "2026-10-18T08:00:03Z" })));

    expect(finished.activities).toHaveLength(1);
    expect(finished.activities[0]).toMatchObject({ activityId: ACTIVITY_ID, status: "succeeded" });
    expect(finished.snapshot.completedToolCount).toBe(1);
  });

  it("终态和固定耗时不被迟到 running 活动或旧快照回退，结果仍保留", () => {
    const completed = parseExecutionSnapshot(snapshot({
      status: "succeeded",
      phase: "finalizing",
      finishedAt: "2026-10-18T08:00:05Z",
      durationMs: 4000,
      completedToolCount: 1,
      lastSeq: 4,
      result: {
        summary: "金价已查到",
        truncated: false,
        stopReason: "completed",
        errorCode: null,
        errorMessage: null,
      },
    }));
    if (!completed) throw new Error("fixture failed to parse");

    let state = applyExecutionSnapshot(null, completed);
    state = applyExecutionEvent(state, rawEvent(5, "execution.activity", {
      status: "running",
      phase: "model",
      finishedAt: null,
      durationMs: null,
    }, activity({ status: "running" })));
    state = applyExecutionSnapshot(state, snapshot({
      status: "running",
      phase: "model",
      finishedAt: null,
      durationMs: null,
      lastSeq: 3,
      result: null,
    }));

    expect(state.snapshot.status).toBe("succeeded");
    expect(state.snapshot.durationMs).toBe(4000);
    expect(state.snapshot.finishedAt).toBe("2026-10-18T08:00:05Z");
    expect(state.snapshot.result?.summary).toBe("金价已查到");
  });

  it("批量补拉按页顺序应用并可跨 pending 续上", () => {
    let state = applyExecutionEvent(null, rawEvent(1, "execution.created"));
    state = applyExecutionEvent(state, rawEvent(4, "execution.phase_changed", { phase: "finalizing" }));
    state = applyExecutionEvents(state, [
      rawEvent(2, "execution.started", { phase: "model" }),
      rawEvent(3, "execution.phase_changed", { phase: "tools" }),
    ]);
    expect(state.appliedSeqs).toEqual([1, 2, 3, 4]);
    expect(state.needsReplay).toBe(false);
  });
});

describe("终态补读详情快照", () => {
  const detailResult = {
    summary: "公开答案",
    truncated: false,
    stopReason: "completed",
    errorCode: null,
    errorMessage: null,
  };

  it("finished 之后到达的 detail 快照补齐 instructionSummary 与 result，不改终态", () => {
    let state = applyExecutionEvent(null, rawEvent(1, "execution.created", { status: "queued", phase: "preparing" }));
    state = applyExecutionEvent(state, rawEvent(2, "execution.finished", {
      status: "succeeded",
      phase: "finalizing",
      finishedAt: "2026-10-18T08:00:09Z",
      durationMs: 9000,
    }));
    expect(state.snapshot.result).toBeNull();

    state = applyExecutionSnapshot(state, snapshot({
      status: "succeeded",
      phase: "finalizing",
      finishedAt: "2026-10-18T08:00:09Z",
      durationMs: 9000,
      lastSeq: 2,
      instructionSummary: "执行当前回合委派的任务。",
      result: detailResult,
    }));

    expect(state.snapshot.status).toBe("succeeded");
    expect(state.snapshot.instructionSummary).toBe("执行当前回合委派的任务。");
    expect(state.snapshot.result?.summary).toBe("公开答案");
  });

  it("终态后迟到的 running 快照不覆盖终态、耗时与结果", () => {
    let state = applyExecutionSnapshot(null, snapshot({
      status: "succeeded",
      phase: "finalizing",
      finishedAt: "2026-10-18T08:00:09Z",
      durationMs: 9000,
      lastSeq: 3,
      result: detailResult,
    }));
    state = applyExecutionSnapshot(state, snapshot({
      status: "running",
      phase: "model",
      finishedAt: null,
      durationMs: null,
      lastSeq: 3,
    }));

    expect(state.snapshot.status).toBe("succeeded");
    expect(state.snapshot.durationMs).toBe(9000);
    expect(state.snapshot.result?.summary).toBe("公开答案");
  });
});
