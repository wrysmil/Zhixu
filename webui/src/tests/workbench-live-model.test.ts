import { deriveSessionWorkbench } from "@/components/workbench/session-workbench-model";
import { describe, expect, it } from "vitest";
import { mergeWorkbenchActivity } from "@/components/workbench/workbench-live-model";
import type { Observation } from "@/components/workbench/workbench-live-types";

const event = (status: Observation["status"], sequence: number, turn_id = "turn-1"): Observation => ({
  id: "tool-1", sequence, turn_id, call_id: "call-1", at: null, kind: "tool", status,
  label: "read", resource: null, extent: null, write_kind: null, source: "runtime", coverage: "turn",
});

describe("workbench activity merge", () => {
  it("does not let an old running snapshot replace a completed live call", () => {
    const snapshot = { phase: "tool_running" as const, current_action: "read", started_at: null, events: [event("running", 1)] };
    expect(mergeWorkbenchActivity(snapshot, [event("succeeded", 2)], "turn-1")?.events[0].status).toBe("succeeded");
  });
  it("does not merge events from another turn", () => {
    expect(mergeWorkbenchActivity(null, [event("running", 1, "old")], "new")).toBeNull();
  });
  it("does not regress a terminal event when a later running event arrives", () => {
    const snapshot = { phase: "completed" as const, current_action: null, started_at: null, events: [event("succeeded", 2)] };
    expect(mergeWorkbenchActivity(snapshot, [event("running", 3)], "turn-1")?.events[0].status).toBe("succeeded");
  });
});

it("从结构化消息投影当前轮次工具状态，不推测自然语言", () => {
  const messages = [
    { id: "old", role: "assistant" as const, content: "", createdAt: 1, turnId: "old", toolEvents: [{ call_id: "old", name: "read", phase: "start" }] },
    { id: "new", role: "assistant" as const, content: "正在读取", createdAt: 2, turnId: "new", toolEvents: [{ call_id: "new", name: "read", phase: "end" }] },
  ];
  const result = deriveSessionWorkbench(messages, null);
  expect(result.liveTurnId).toBe("new");
  expect(result.liveEvents?.map(event => event.status)).toEqual(["succeeded"]);
});
it("同一工具调用的不同记忆观测不会互相覆盖", () => {
  const tool = event("succeeded", 1);
  const read: Observation = { ...event("succeeded", 2), id: "read-1", kind: "memory_read" };
  expect(mergeWorkbenchActivity(null, [tool, read], "turn-1")?.events).toHaveLength(2);
});
