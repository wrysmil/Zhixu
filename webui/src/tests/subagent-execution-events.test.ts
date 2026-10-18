import { describe, expect, it } from "vitest";

import {
  parseExecutionActivity,
  parseExecutionEvent,
  parseExecutionFrame,
  parseExecutionSnapshot,
} from "@/features/subagent-execution/parser";
import type { ExecutionSnapshot } from "@/features/subagent-execution/types";

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
    phase: "tools",
    createdAt: "2026-10-18T08:00:00Z",
    startedAt: "2026-10-18T08:00:01Z",
    finishedAt: null,
    queueDurationMs: 1000,
    durationMs: null,
    lastAction: "正在搜索网页",
    completedToolCount: 0,
    lastSeq: 1,
    result: null,
    activities: [],
    ...overrides,
  };
}

function eventEnvelope(seq: number, type: string, execution: Record<string, unknown>, activity?: Record<string, unknown>) {
  return {
    schemaVersion: 1,
    invocationId: INVOCATION_ID,
    chatId: CHAT_ID,
    parentTurnId: TURN_ID,
    parentToolCallId: "tool-call-1",
    seq,
    occurredAt: "2026-10-18T08:00:02Z",
    type,
    data: {
      execution,
      ...(activity ? { activity } : {}),
    },
  };
}

describe("子 Agent 执行 wire parser", () => {
  it("按契约解析列表/详情快照及其活动", () => {
    const parsed = parseExecutionSnapshot(snapshot({
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
      activities: [{
        activityId: ACTIVITY_ID,
        kind: "tool",
        toolName: "web_search",
        status: "succeeded",
        label: "搜索网页",
        startedAt: "2026-10-18T08:00:02Z",
        finishedAt: "2026-10-18T08:00:03Z",
        detail: "query=近期金价",
        output: "hits=5",
      }],
    }));

    expect(parsed).toMatchObject({
      invocationId: INVOCATION_ID,
      chatId: CHAT_ID,
      parentTurnId: TURN_ID,
      parentToolCallId: "tool-call-1",
      status: "succeeded",
      durationMs: 4000,
      result: { summary: "金价已查到", truncated: false },
      activities: [{ activityId: ACTIVITY_ID, status: "succeeded", detail: "query=近期金价" }],
    });
  });

  it("解析实时 event 帧，并只接受 schemaVersion=1", () => {
    const frame = {
      event: "subagent_execution_event",
      type: "subagent_execution_event",
      chat_id: CHAT_ID,
      turn_id: TURN_ID,
      data: eventEnvelope(2, "execution.activity", {
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
        lastAction: "搜索网页",
        completedToolCount: 0,
        lastSeq: 2,
      }, {
        activityId: ACTIVITY_ID,
        kind: "tool",
        toolName: "web_search",
        status: "running",
        label: "搜索网页",
        startedAt: "2026-10-18T08:00:02Z",
        finishedAt: null,
      }),
    };

    const parsed = parseExecutionFrame(frame);
    expect(parsed).toMatchObject({
      kind: "event",
      chatId: CHAT_ID,
      event: {
        seq: 2,
        type: "execution.activity",
        data: { activity: { activityId: ACTIVITY_ID, status: "running" } },
      },
    });

    for (const schemaVersion of [2, 99]) {
      const higher = structuredClone(frame);
      higher.data.schemaVersion = schemaVersion;
      expect(parseExecutionEvent(higher.data)).toBeNull();
      expect(parseExecutionFrame(higher)).toBeNull();
    }
    expect(parseExecutionFrame({ event: "subagent_execution_event", data: {} })).toBeNull();
  });

  it("解析 hydrate 列表快照帧", () => {
    const frame = {
      event: "subagent_execution_snapshot",
      type: "subagent_execution_snapshot",
      chat_id: CHAT_ID,
      turn_id: TURN_ID,
      data: {
        executions: [snapshot()],
        nextCursor: null,
        serverNow: "2026-10-18T08:00:03Z",
      },
    };
    expect(parseExecutionFrame(frame)).toMatchObject({
      kind: "snapshot",
      chatId: CHAT_ID,
      executions: [{ invocationId: INVOCATION_ID, lastSeq: 1 }],
    });
  });
});

describe("子 Agent 活动的参数/输出投影", () => {
  function rawActivity(overrides: Record<string, unknown> = {}) {
    return {
      activityId: ACTIVITY_ID,
      kind: "tool",
      toolName: "read_file",
      status: "succeeded",
      label: "正在使用工具",
      startedAt: "2026-10-18T08:00:02Z",
      finishedAt: "2026-10-18T08:00:03Z",
      ...overrides,
    };
  }

  it("保留服务端投影出的参数与输出摘要", () => {
    const parsed = parseExecutionActivity(
      rawActivity({ detail: "path=src/app.py", output: "lines=128" }),
    );
    expect(parsed).toMatchObject({ detail: "path=src/app.py", output: "lines=128" });
  });

  it("投影字段缺失或类型不对时归零，整条活动仍然可用", () => {
    for (const value of [undefined, null, 42, true, {}, ["x"]]) {
      const parsed = parseExecutionActivity(rawActivity({ detail: value, output: value }));
      expect(parsed).not.toBeNull();
      expect(parsed?.detail).toBeNull();
      expect(parsed?.output).toBeNull();
    }
  });

  it("空字符串按没有投影处理", () => {
    const parsed = parseExecutionActivity(rawActivity({ detail: "", output: "" }));
    expect(parsed?.detail).toBeNull();
    expect(parsed?.output).toBeNull();
  });

  it("旧网关没有这两个字段时不影响解析", () => {
    const parsed = parseExecutionActivity(rawActivity());
    expect(parsed).toMatchObject({ activityId: ACTIVITY_ID, detail: null, output: null });
  });
});
