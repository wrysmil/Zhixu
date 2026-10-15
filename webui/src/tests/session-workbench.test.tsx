// SessionWorkbench 接入统一 StartDock（批次 home-dock-pty-terminal-v1.1 Task 10）。
// 覆盖：embedded dock 顶替三个内联 Section（fileTree/terminal/browser）、
// terminal_start 携带会话 sessionKey、embedded 无 rail 折叠按钮、
// 标签状态与接管清单按 scope（sessionKey）隔离。
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionWorkbench } from "@/components/workbench/SessionWorkbench";
import type { SessionWorkbenchModel } from "@/components/workbench/session-workbench-model";
import { dockStore } from "@/lib/dock-store";
import type { TerminalEvent } from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";

const mocks = vi.hoisted(() => ({
  fetchTerminalShells: vi.fn(),
  fetchWorkspaceFiles: vi.fn(),
  fetchSessionDirectory: vi.fn(),
  fetchFilePreview: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, ...mocks };
});

// xterm 在 happy-dom 下不做真实渲染：桩掉构造器即可验证 dock→surface 的接线。
vi.mock("@xterm/xterm", () => ({
  Terminal: class FakeXterm {
    cols = 80;
    rows = 24;
    open() {}
    write() {}
    scrollToBottom() {}
    dispose() {}
    loadAddon() {}
    onData() {
      return { dispose() {} };
    }
    onResize() {
      return { dispose() {} };
    }
  },
}));
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit() {}
  },
}));

const shellsPayload = {
  shells: [
    { id: "zsh", label: "Zsh", path: "/bin/zsh" },
    { id: "bash", label: "Bash", path: "/bin/bash" },
  ],
  default: "zsh",
};

const directoryPayload = {
  path: "",
  display_path: "workspace",
  project_path: "/tmp/project",
  parent_path: null,
  truncated: false,
  entries: [
    { name: "README.md", path: "README.md", display_path: "README.md", is_dir: false, size: 12 },
  ],
};

const emptyModel: SessionWorkbenchModel = {
  updatedAtMs: null,
  summary: "",
  diffFiles: 0,
  diffAdded: 0,
  diffDeleted: 0,
  projectName: "demo",
  projectPath: "/tmp/project",
  accessMode: "full",
  mcpUsed: [],
  cliUsed: [],
  outputs: [],
  sources: [],
};

interface FakeClient {
  terminalStart: ReturnType<typeof vi.fn>;
  terminalWrite: ReturnType<typeof vi.fn>;
  terminalResize: ReturnType<typeof vi.fn>;
  terminalKill: ReturnType<typeof vi.fn>;
  terminalList: ReturnType<typeof vi.fn>;
  onTerminalEvent: ReturnType<typeof vi.fn>;
  emit: (event: TerminalEvent) => void;
}

function makeFakeClient(): FakeClient {
  const handlers = new Set<(event: TerminalEvent) => void>();
  return {
    terminalStart: vi.fn(),
    terminalWrite: vi.fn(),
    terminalResize: vi.fn(),
    terminalKill: vi.fn(),
    terminalList: vi.fn(),
    onTerminalEvent: vi.fn((handler: (event: TerminalEvent) => void) => {
      handlers.add(handler);
      return () => {
        handlers.delete(handler);
      };
    }),
    emit(event) {
      act(() => {
        for (const handler of [...handlers]) handler(event);
      });
    },
  };
}

type WorkbenchProps = React.ComponentProps<typeof SessionWorkbench>;

function renderWorkbench(props: Partial<WorkbenchProps> = {}) {
  const client = makeFakeClient();
  const view = render(
    <ClientProvider client={client as never} token="tok">
      <SessionWorkbench
        model={emptyModel}
        onCollapse={() => {}}
        sessionKey="websocket:chat-7"
        getToken={() => "tok"}
        {...props}
      />
    </ClientProvider>,
  );
  return { ...view, client };
}

async function waitForShells() {
  // 启动器不再展示 shell select；测试关心的是 shells 数据被拉回，
  // SessionWorkbench 的 dock + 菜单 shell 子菜单据此可用。
  await waitFor(() => expect(mocks.fetchTerminalShells).toHaveBeenCalled());
}

function openTools() {
  fireEvent.click(screen.getByRole("button", { name: "Expand sidebar tools" }));
}

beforeEach(() => {
  sessionStorage.clear();
  dockStore.resetForTests();
  mocks.fetchTerminalShells.mockReset().mockResolvedValue(shellsPayload);
  mocks.fetchWorkspaceFiles.mockReset().mockResolvedValue(directoryPayload);
  mocks.fetchSessionDirectory.mockReset().mockResolvedValue(directoryPayload);
  mocks.fetchFilePreview.mockReset().mockResolvedValue({
    path: "/tmp/project/README.md",
    display_path: "README.md",
    project_path: "/tmp/project",
    language: "markdown",
    content: "# Hello",
    size: 6,
    truncated: false,
  });
});

describe("SessionWorkbench embedded StartDock", () => {
  it("reports the measured rail width when workspace changes to tools", () => {
    const bounds = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function () {
      return { width: this.dataset.mode === "tools" ? 576 : 304 } as DOMRect;
    });
    const onRailWidthChange = vi.fn();
    try {
      const view = renderWorkbench({ onRailWidthChange });
      expect(onRailWidthChange).toHaveBeenLastCalledWith(304);
      openTools();
      expect(onRailWidthChange).toHaveBeenLastCalledWith(576);
      view.unmount();
      expect(onRailWidthChange).toHaveBeenLastCalledWith(0);
    } finally {
      bounds.mockRestore();
    }
  });

  it("reserves no composer space for a narrow overlay", () => {
    const onRailWidthChange = vi.fn();
    renderWorkbench({ overlay: true, onRailWidthChange });
    expect(onRailWidthChange).toHaveBeenLastCalledWith(0);
    openTools();
    expect(onRailWidthChange).toHaveBeenLastCalledWith(0);
    expect(screen.getByTestId("session-workbench")).toHaveAttribute("data-overlay", "true");
  });

  it("shows workspace information first and opens tools as a separate full-height mode", () => {
    const { client } = renderWorkbench({ model: { ...emptyModel, summary: "Workspace summary" } });
    expect(screen.getByText("Workspace summary")).toBeVisible();
    expect(screen.queryByTestId("start-dock")).not.toBeInTheDocument();

    openTools();
    expect(screen.getByTestId("dock-launcher")).toBeVisible();
    expect(screen.getByText("Workspace summary")).not.toBeVisible();
    fireEvent.click(screen.getByTestId("dock-card-terminal"));
    const terminalId = client.terminalStart.mock.calls[0][0] as string;
    const surface = screen.getByTestId(`terminal-surface-${terminalId}`);

    fireEvent.click(screen.getByRole("button", { name: "Show workspace" }));
    expect(screen.getByText("Workspace summary")).toBeVisible();
    expect(surface).not.toBeVisible();
    expect(client.terminalKill).not.toHaveBeenCalled();
    openTools();
    expect(screen.getByTestId(`terminal-surface-${terminalId}`)).toBe(surface);
    expect(surface).toBeVisible();
    expect(client.terminalStart).toHaveBeenCalledTimes(1);
  });

  it("session dock replaces the three inline sections with one tabbed dock", async () => {
    const { client } = renderWorkbench({ sessionKey: "websocket:chat-7" });
    openTools();
    expect(screen.getByTestId("start-dock")).toBeInTheDocument();
    expect(client.terminalList).toHaveBeenCalled();
    // 三个旧 Section 头（Files / New terminal / Browser）不复存在；
    // dock 卡片/内容的可访问名均长于这些精确文本，不会误配。
    expect(screen.queryByRole("button", { name: "Files", exact: true })).toBeNull();
    expect(screen.queryByRole("button", { name: "New terminal", exact: true })).toBeNull();
    expect(screen.queryByRole("button", { name: "Browser", exact: true })).toBeNull();

    await waitForShells();
    fireEvent.click(screen.getByTestId("dock-card-terminal"));
    expect(client.terminalStart).toHaveBeenCalledWith(
      expect.any(String), "websocket:chat-7", "zsh",
    );
    const id = client.terminalStart.mock.calls[0][0] as string;
    expect(screen.getByTestId(`terminal-surface-${id}`)).toBeInTheDocument();
  });

  it("embedded variant renders inside the workbench without the rail collapse button", () => {
    renderWorkbench();
    openTools();
    expect(screen.getByTestId("session-workbench")).toBeInTheDocument();
    expect(screen.getByTestId("start-dock")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Collapse the start panel/i })).toBeNull();
    // 工作台自身的折叠入口保留。
    expect(screen.getByRole("button", { name: /Collapse workbench/i })).toBeInTheDocument();
  });

  it("dock tabs are isolated per session scope", () => {
    const view = renderWorkbench({ sessionKey: "websocket:chat-a" });
    openTools();
    fireEvent.click(screen.getByTestId("dock-card-browser"));
    expect(screen.getByTestId("dock-tab-browser")).toBeInTheDocument();
    view.unmount();

    renderWorkbench({ sessionKey: "websocket:chat-b" });
    openTools();
    expect(screen.queryByTestId("dock-tab-browser")).toBeNull();
    expect(screen.getByTestId("dock-launcher")).toBeInTheDocument();
  });

  it("persisted takeover terminals are scoped per session key", async () => {
    const first = renderWorkbench({ sessionKey: "websocket:chat-a" });
    openTools();
    await waitForShells();
    fireEvent.click(screen.getByTestId("dock-card-terminal"));
    const id = first.client.terminalStart.mock.calls[0][0] as string;
    expect(
      JSON.parse(sessionStorage.getItem("nanobot.dock.terminals.websocket:chat-a") ?? "[]"),
    ).toEqual([{ id, shell: "zsh" }]);
    first.unmount();
    // 模拟页面刷新：内存标签清空，接管候选仅剩各 scope 的 sessionStorage 清单。
    dockStore.resetForTests();

    const second = renderWorkbench({ sessionKey: "websocket:chat-b" });
    openTools();
    expect(screen.queryByTestId(`dock-takeover-${id}`)).toBeNull();
    // 同 scope 重新挂载：接管横幅按会话 scope 的持久化清单出现（与首页行为一致）。
    second.unmount();
    renderWorkbench({ sessionKey: "websocket:chat-a" });
    openTools();
    const banner = screen.getByTestId(`dock-takeover-${id}`);
    expect(banner).toHaveTextContent(">. zsh");
    // 与首页 rail 行为一致：无标签时接管横幅与启动器空态共存。
    expect(screen.getByTestId("dock-launcher")).toBeInTheDocument();
  });

  it("previews clicked files beside the right-hand tree within the session tools", async () => {
    const onOpenFilePreview = vi.fn();
    renderWorkbench({ sessionKey: "websocket:chat-7", onOpenFilePreview });
    openTools();
    fireEvent.click(screen.getByTestId("dock-card-files"));
    expect(await screen.findByText("README.md")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /README\.md/ }));
    const preview = await screen.findByTestId("dock-file-preview");
    const tree = screen.getByTestId("dock-file-tree");
    expect(preview.parentElement).toBe(tree.parentElement);
    expect(preview.compareDocumentPosition(tree) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(await screen.findByText("# Hello")).toBeVisible();
    expect(mocks.fetchFilePreview).toHaveBeenCalledWith("tok", "websocket:chat-7", "README.md", "", null);
    expect(onOpenFilePreview).not.toHaveBeenCalled();
  });
});
