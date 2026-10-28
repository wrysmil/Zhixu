import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import i18n from "@/i18n";
import { WorkbenchMemories } from "@/components/workbench/workbench-context";
import type { SessionWorkbenchSnapshot } from "@/components/workbench/session-workbench-api";

const WORKSPACE_ID = "52b68c894e474e6be9b5004e9157a99e";

function snapshot(memories: SessionWorkbenchSnapshot["memories"]): SessionWorkbenchSnapshot {
  return {
    schema_version: 1, session_key: "websocket:a", updated_at: null, goal: null,
    tasks: [], plan: null, agents: [], memories, skills: [], mcp: [], limitations: [],
  };
}

beforeEach(async () => {
  await i18n.changeLanguage("zh-CN");
});
afterEach(cleanup);

describe("工作台记忆条目", () => {
  it("长期记忆显示归属工作区，全局记忆单独标注", () => {
    render(<WorkbenchMemories snapshot={snapshot([
      { id: "m1", kind: "long_term", title: "部署顺序约定", content: "先 pnpm install 再 pnpm test",
        source: "session:websocket:a", updated_at: null, persisted: true,
        workspace_id: WORKSPACE_ID, is_global: false },
      { id: "m2", kind: "long_term", title: "回复语言偏好", content: "回复用中文",
        source: "session:websocket:a", updated_at: null, persisted: true,
        workspace_id: "default", is_global: true },
    ])} />);

    expect(screen.getByText(`归属：工作区 ${WORKSPACE_ID.slice(0, 8)}`)).toBeInTheDocument();
    expect(screen.getByText("归属：全局记忆")).toBeInTheDocument();
  });

  it("按会话反查到的记忆标为「本会话抽取」，情节关联仍标「会话经历」", () => {
    render(<WorkbenchMemories snapshot={snapshot([
      { id: "m1", kind: "long_term", title: "会话反查", content: "c1",
        source: "session:websocket:a", updated_at: null, persisted: true },
      { id: "m2", kind: "long_term", title: "情节关联", content: "c2",
        source: "episode:ep-1", updated_at: null, persisted: true },
    ])} />);

    expect(screen.getByText(/本会话抽取/)).toBeInTheDocument();
    expect(screen.getByText(/会话经历 ep-1/)).toBeInTheDocument();
  });
});
