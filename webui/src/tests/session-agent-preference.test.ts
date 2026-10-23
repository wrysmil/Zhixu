// 会话级 Agent 选择的本地持久化（刷新后仍记得每个会话由谁接管主回合）。
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  readSessionAgentSelection,
  writeSessionAgentSelection,
  SESSION_AGENT_STORAGE_KEY,
} from "@/lib/session-agent-preference";

beforeEach(() => {
  window.localStorage.clear();
});

describe("session agent preference", () => {
  it("按 chat 存取选择", () => {
    writeSessionAgentSelection({ "chat-a": "code-helper", "chat-b": "writer" });

    expect(readSessionAgentSelection()).toEqual({
      "chat-a": "code-helper",
      "chat-b": "writer",
    });
  });

  it("没存过或存的是脏数据时当作空选择", () => {
    expect(readSessionAgentSelection()).toEqual({});

    for (const raw of ["not json", "[]", "null", "\"writer\"", "42"]) {
      window.localStorage.setItem(SESSION_AGENT_STORAGE_KEY, raw);
      expect(readSessionAgentSelection()).toEqual({});
    }
  });

  it("丢掉非字符串与空串条目，保留合法项", () => {
    window.localStorage.setItem(
      SESSION_AGENT_STORAGE_KEY,
      JSON.stringify({ keep: "writer", drop: 42, empty: "", nested: { id: "x" } }),
    );

    expect(readSessionAgentSelection()).toEqual({ keep: "writer" });
  });

  it("本地存储写不进去时静默失败，不阻断聊天", () => {
    const setItem = vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });

    expect(() => writeSessionAgentSelection({ "chat-a": "writer" })).not.toThrow();

    setItem.mockRestore();
  });
});
