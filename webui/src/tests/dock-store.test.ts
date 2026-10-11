// dock store 单元测试（批次 home-dock-pty-terminal-v1.1 Task 6）。
// 覆盖：file/browser 单例聚焦、terminal ≤8 越限、scope 隔离、sessionStorage 接管清单。
import { beforeEach, describe, expect, it } from "vitest";
import {
  HOME_DOCK_SCOPE, MAX_DOCK_TERMINAL_TABS, dockStore, useDockTabs,
} from "@/lib/dock-store";

describe("dock store", () => {
  beforeEach(() => {
    sessionStorage.clear();
    dockStore.resetForTests();
  });

  it("file/browser tabs are singletons that focus instead of duplicating", () => {
    const first = dockStore.openFile(HOME_DOCK_SCOPE);
    const again = dockStore.openFile(HOME_DOCK_SCOPE);
    expect(again).toBe(first);
    expect(dockStore.getSnapshot(HOME_DOCK_SCOPE).tabs.filter((t) => t.kind === "file")).toHaveLength(1);
    dockStore.openTerminal(HOME_DOCK_SCOPE, "term-1", "/bin/zsh");
    dockStore.focusTab(HOME_DOCK_SCOPE, "term-1");
    dockStore.openFile(HOME_DOCK_SCOPE);
    expect(dockStore.getSnapshot(HOME_DOCK_SCOPE).activeId).toBe("file");
    // useDockTabs 与 dockStore.getSnapshot 共享同一快照来源（hook 有导出即类型可用）。
    expect(typeof useDockTabs).toBe("function");
  });

  it("terminal tabs are titled >. <shell> and capped at 8", () => {
    for (let i = 1; i <= MAX_DOCK_TERMINAL_TABS; i += 1) {
      expect(dockStore.openTerminal(HOME_DOCK_SCOPE, `term-${i}`, "/bin/zsh")).toBe(`term-${i}`);
    }
    expect(dockStore.openTerminal(HOME_DOCK_SCOPE, "term-overflow", null)).toBeNull();
    dockStore.setTerminalTitle(HOME_DOCK_SCOPE, "term-1", "/bin/zsh");
    const tab = dockStore.getSnapshot(HOME_DOCK_SCOPE).tabs.find((t) => t.id === "term-1");
    expect(tab?.title).toBe(">. zsh");
  });

  it("persists terminal ids for the refresh takeover banner and forgets on close", () => {
    dockStore.openTerminal(HOME_DOCK_SCOPE, "term-a", "/bin/bash");
    // persisted entry 的 shell 存原始 shell 路径（不是 `>. bash` 标题形态）。
    expect(dockStore.persistedTerminals(HOME_DOCK_SCOPE)).toEqual([{ id: "term-a", shell: "/bin/bash" }]);
    dockStore.closeTab(HOME_DOCK_SCOPE, "term-a");
    expect(dockStore.persistedTerminals(HOME_DOCK_SCOPE)).toEqual([]);
  });

  it("scopes are isolated between home and a session", () => {
    dockStore.openFile(HOME_DOCK_SCOPE);
    dockStore.openBrowser("websocket:chat-9");
    expect(dockStore.getSnapshot(HOME_DOCK_SCOPE).tabs.map((t) => t.kind)).toEqual(["file"]);
    expect(dockStore.getSnapshot("websocket:chat-9").tabs.map((t) => t.kind)).toEqual(["browser"]);
  });
});
