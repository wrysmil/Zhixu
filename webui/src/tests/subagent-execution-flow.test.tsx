import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as executionApi from "@/features/subagent-execution/client";
import type {
  ExecutionEvent,
  ExecutionEventsResponse,
  ExecutionListResponse,
  ExecutionSnapshot,
  SubagentExecutionFrameClient,
} from "@/features/subagent-execution/types";
import {
  useSubagentExecutions,
  type SubagentExecutionFrameClient as HookFrameClient,
} from "@/features/subagent-execution/useSubagentExecutions";

vi.mock("@/features/subagent-execution/client", () => ({
  listSubagentExecutions: vi.fn(),
  loadSubagentExecution: vi.fn(),
  loadSubagentExecutionEvents: vi.fn(),
  subagentExecutionErrorCode: vi.fn(() => null),
}));

const CHAT_A = "chat-a";
const CHAT_B = "chat-b";
const TURN_ID = "turn-1";
const INVOCATION_ID = "11111111-1111-4111-8111-111111111111";
const ACTIVITY_ID = "22222222-2222-4222-8222-222222222222";

function snapshot(overrides: Partial<ExecutionSnapshot> = {}): ExecutionSnapshot {
  return {
    invocationId: INVOCATION_ID,
    chatId: CHAT_A,
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
    lastAction: "搜索网页",
    completedToolCount: 0,
    lastSeq: 1,
    result: null,
    activities: [],
    ...overrides,
  };
}

function event(seq: number, type: string, executionOverrides: Partial<ExecutionSnapshot> = {}, activity?: Record<string, unknown>): ExecutionEvent {
  return {
    schemaVersion: 1,
    invocationId: INVOCATION_ID,
    chatId: CHAT_A,
    parentTurnId: TURN_ID,
    parentToolCallId: "tool-call-1",
    seq,
    occurredAt: "2026-10-18T08:00:02Z",
    type: type as ExecutionEvent["type"],
    data: {
      execution: {
        ...snapshot({ ...executionOverrides, lastSeq: seq }),
        instructionSummary: undefined,
        result: undefined,
      },
      ...(activity ? { activity } : {}),
    },
  } as ExecutionEvent;
}

function eventFrame(sequence: ExecutionEvent): Record<string, unknown> {
  return {
    event: "subagent_execution_event",
    type: "subagent_execution_event",
    chat_id: sequence.chatId,
    turn_id: sequence.parentTurnId,
    data: sequence,
  };
}

function snapshotFrame(executions: ExecutionSnapshot[], chatId = CHAT_A): Record<string, unknown> {
  return {
    event: "subagent_execution_snapshot",
    type: "subagent_execution_snapshot",
    chat_id: chatId,
    turn_id: TURN_ID,
    data: { executions, nextCursor: null, serverNow: "2026-10-18T08:00:03Z" },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

class FakeClient implements SubagentExecutionFrameClient, HookFrameClient {
  private chatHandlers = new Map<string, Set<(value: unknown) => void>>();
  private statusHandlers = new Set<(status: "idle" | "connecting" | "open" | "reconnecting" | "closed" | "error") => void>();
  onChatCalls = 0;
  unsubscribed = 0;

  onChat(chatId: string, handler: (value: unknown) => void): () => void {
    this.onChatCalls += 1;
    let handlers = this.chatHandlers.get(chatId);
    if (!handlers) {
      handlers = new Set();
      this.chatHandlers.set(chatId, handlers);
    }
    handlers.add(handler);
    return () => {
      this.unsubscribed += 1;
      handlers?.delete(handler);
    };
  }

  onStatus(handler: (status: "idle" | "connecting" | "open" | "reconnecting" | "closed" | "error") => void): () => void {
    this.statusHandlers.add(handler);
    return () => this.statusHandlers.delete(handler);
  }

  emit(chatId: string, frame: unknown): void {
    for (const handler of [...(this.chatHandlers.get(chatId) ?? [])]) handler(frame);
  }

  emitStatus(status: "idle" | "connecting" | "open" | "reconnecting" | "closed" | "error"): void {
    for (const handler of [...this.statusHandlers]) handler(status);
  }
}

const emptyList: ExecutionListResponse = { executions: [], nextCursor: null, serverNow: "2026-10-18T08:00:03Z" };
const emptyEvents: ExecutionEventsResponse = { events: [], lastSeq: 0, hasMore: false, resetRequired: false };

beforeEach(() => {
  vi.mocked(executionApi.listSubagentExecutions).mockReset().mockResolvedValue(emptyList);
  vi.mocked(executionApi.loadSubagentExecution).mockReset().mockResolvedValue({
    execution: snapshot({ status: "succeeded", lastSeq: 3 }),
    serverNow: "2026-10-18T08:00:03Z",
  });
  vi.mocked(executionApi.loadSubagentExecutionEvents).mockReset().mockResolvedValue(emptyEvents);
});

describe("useSubagentExecutions 恢复与竞态", () => {
  it("先订阅再 list，finished 早到后晚到列表不覆盖终态", async () => {
    const list = deferred<ExecutionListResponse>();
    const order: string[] = [];
    const client = new FakeClient();
    const originalOnChat = client.onChat.bind(client);
    client.onChat = (chatId, handler) => {
      order.push("subscribe");
      return originalOnChat(chatId, handler);
    };
    vi.mocked(executionApi.listSubagentExecutions).mockImplementation(() => {
      order.push("list");
      return list.promise;
    });

    const { result } = renderHook(() => useSubagentExecutions(client, {
      chatId: CHAT_A,
      token: "token",
      capability: true,
    }));

    expect(order.slice(0, 2)).toEqual(["subscribe", "list"]);
    await act(async () => {
      client.emit(CHAT_A, eventFrame(event(2, "execution.finished", {
        status: "succeeded",
        phase: "finalizing",
        finishedAt: "2026-10-18T08:00:05Z",
        durationMs: 4000,
      })));
    });
    await act(async () => {
      list.resolve({ executions: [snapshot({ status: "running", lastSeq: 1 })], nextCursor: null, serverNow: "now" });
      await list.promise;
    });

    expect(result.current.byInvocationId[INVOCATION_ID].snapshot.status).toBe("succeeded");
    expect(result.current.byInvocationId[INVOCATION_ID].snapshot.durationMs).toBe(4000);
  });

  it("chat 切换时解除旧订阅并用 generation guard 拒绝晚到响应", async () => {
    const chatAList = deferred<ExecutionListResponse>();
    const chatBList = deferred<ExecutionListResponse>();
    vi.mocked(executionApi.listSubagentExecutions).mockImplementation((_token, chatId) => (
      chatId === CHAT_A ? chatAList.promise : chatBList.promise
    ));
    const client = new FakeClient();
    const { result, rerender } = renderHook(
      ({ chatId }) => useSubagentExecutions(client, { chatId, token: "token", capability: true }),
      { initialProps: { chatId: CHAT_A } },
    );

    rerender({ chatId: CHAT_B });
    expect(client.unsubscribed).toBeGreaterThanOrEqual(1);

    await act(async () => {
      chatBList.resolve(emptyList);
      chatAList.resolve({ executions: [snapshot()], nextCursor: null, serverNow: "now" });
      await Promise.all([chatBList.promise, chatAList.promise]);
    });

    expect(result.current.byInvocationId[INVOCATION_ID]).toBeUndefined();
    expect(result.current.executions).toHaveLength(0);
  });

  it("运行中刷新/重连先由 snapshot 恢复，再移除断线标记且活动不重复", async () => {
    const client = new FakeClient();
    const recovered = snapshot({
      lastSeq: 3,
      activities: [{
        activityId: ACTIVITY_ID,
        kind: "tool",
        toolName: "web_search",
        status: "succeeded",
        label: "搜索网页",
        startedAt: "2026-10-18T08:00:02Z",
        finishedAt: "2026-10-18T08:00:03Z",
      }],
    });
    const { result } = renderHook(() => useSubagentExecutions(client, {
      chatId: CHAT_A,
      token: "token",
      capability: true,
    }));

    await act(async () => {
      client.emit(CHAT_A, snapshotFrame([recovered]));
    });
    expect(result.current.byInvocationId[INVOCATION_ID].activities).toHaveLength(1);

    act(() => client.emitStatus("reconnecting"));
    expect(result.current.connectionStale).toBe(true);

    await act(async () => {
      client.emitStatus("open");
      client.emit(CHAT_A, snapshotFrame([recovered]));
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.connectionStale).toBe(false));
    expect(result.current.byInvocationId[INVOCATION_ID].activities).toHaveLength(1);
    expect(result.current.byInvocationId[INVOCATION_ID].snapshot.invocationId).toBe(INVOCATION_ID);
  });

  it("resetRequired 只回最新 detail 一次，标记有限历史并停止重试", async () => {
    vi.mocked(executionApi.loadSubagentExecutionEvents).mockResolvedValue({
      events: [],
      lastSeq: 1,
      hasMore: false,
      resetRequired: true,
    });
    vi.mocked(executionApi.loadSubagentExecution).mockResolvedValue({
      execution: snapshot({ status: "succeeded", phase: "finalizing", lastSeq: 3, durationMs: 3000 }),
      serverNow: "now",
    });
    const client = new FakeClient();
    const { result } = renderHook(() => useSubagentExecutions(client, {
      chatId: CHAT_A,
      token: "token",
      capability: true,
    }));

    await act(async () => {
      client.emit(CHAT_A, eventFrame(event(1, "execution.created", { status: "queued", phase: "preparing" })));
      client.emit(CHAT_A, eventFrame(event(3, "execution.phase_changed", { phase: "tools" })));
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.historyLimited).toBe(true));
    expect(executionApi.loadSubagentExecutionEvents).toHaveBeenCalledTimes(1);
    expect(executionApi.loadSubagentExecution).toHaveBeenCalledTimes(1);
    expect(result.current.byInvocationId[INVOCATION_ID].needsReplay).toBe(false);
  });

  it("断号补拉每 invocation 只跑一条链，分页读完再续", async () => {
    vi.mocked(executionApi.loadSubagentExecutionEvents)
      .mockResolvedValueOnce({
        events: [event(2, "execution.started", { phase: "model" })],
        lastSeq: 2,
        hasMore: true,
        resetRequired: false,
      })
      .mockResolvedValueOnce({
        events: [event(3, "execution.phase_changed", { phase: "tools" })],
        lastSeq: 3,
        hasMore: false,
        resetRequired: false,
      });
    const client = new FakeClient();
    const { result } = renderHook(() => useSubagentExecutions(client, {
      chatId: CHAT_A,
      token: "token",
      capability: true,
    }));

    await act(async () => {
      client.emit(CHAT_A, eventFrame(event(1, "execution.created", { status: "queued", phase: "preparing" })));
      client.emit(CHAT_A, eventFrame(event(4, "execution.phase_changed", { phase: "finalizing" })));
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.byInvocationId[INVOCATION_ID].appliedSeqs).toEqual([1, 2, 3, 4]));
    expect(executionApi.loadSubagentExecutionEvents).toHaveBeenCalledTimes(2);
    expect(vi.mocked(executionApi.loadSubagentExecutionEvents).mock.calls.map((call) => call[2])).toEqual([
      { afterSeq: 1 },
      { afterSeq: 2 },
    ]);
  });

  it("旧 gateway 无 subagentExecutionEventsV1 能力时不订阅也不补拉", () => {
    const client = new FakeClient();
    renderHook(() => useSubagentExecutions(client, {
      chatId: CHAT_A,
      token: "token",
      capability: false,
    }));

    expect(client.onChatCalls).toBe(0);
    expect(executionApi.listSubagentExecutions).not.toHaveBeenCalled();
  });

  it("未知/更高 schemaVersion 不更新状态且不触发补拉", async () => {
    const client = new FakeClient();
    const { result } = renderHook(() => useSubagentExecutions(client, {
      chatId: CHAT_A,
      token: "token",
      capability: true,
    }));

    const unknown = eventFrame(event(1, "execution.created", { status: "queued", phase: "preparing" }));
    (unknown.data as { schemaVersion: number }).schemaVersion = 99;
    await act(async () => {
      client.emit(CHAT_A, unknown);
      await Promise.resolve();
    });

    expect(result.current.executions).toHaveLength(0);
    expect(executionApi.loadSubagentExecutionEvents).not.toHaveBeenCalled();
  });
});
