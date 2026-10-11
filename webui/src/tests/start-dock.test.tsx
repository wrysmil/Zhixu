// StartDock 标签式右侧坞（批次 home-dock-pty-terminal-v1.1 Task 9）。
// 覆盖：启动器空态三卡片、+ 菜单（file/browser 单例 focus-or-open、terminal shell 子菜单、
// ≤8 上限）、terminal tab 启动/关闭（kill + tombstone 不复活）、terminal_list_result 恢复、
// 接管条（含「已开 tab 的 id 不进横幅」「persisted 候选不自动恢复」两条裁定）、
// TerminalSurface 复用（pty 行式回退 / 直通切换由 surface 内部负责）。
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StartDock } from "@/components/dock/StartDock";
import { HOME_DOCK_SCOPE, dockStore } from "@/lib/dock-store";
import type { TerminalEvent } from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";

const mocks = vi.hoisted(() => ({
  fetchTerminalShells: vi.fn(),
  fetchWorkspaceFiles: vi.fn(),
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

const directoryPayload = {
  path: "",
  display_path: "workspace",
  project_path: "/tmp/project",
  parent_path: null,
  truncated: false,
  entries: [
    { name: "README.md", path: "README.md", display_path: "README.md", is_dir: false, size: 12 },
    { name: "src", path: "src", display_path: "src", is_dir: true, size: 0 },
  ],
};

const previewPayload = {
  path: "/tmp/project/README.md",
  display_path: "README.md",
  project_path: "/tmp/project",
  language: "markdown",
  content: "# Hello from preview",
  size: 21,
  truncated: false,
};

const shellsPayload = {
  shells: [
    { id: "zsh", label: "Zsh", path: "/bin/zsh" },
    { id: "bash", label: "Bash", path: "/bin/bash" },
  ],
  default: "zsh",
};

const TAKEOVER_KEY = `nanobot.dock.terminals.${HOME_DOCK_SCOPE}`;

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

function renderDock(props: Partial<React.ComponentProps<typeof StartDock>> = {}) {
  const client = makeFakeClient();
  render(
    <ClientProvider client={client as never} token="tok">
      <StartDock
        scope={HOME_DOCK_SCOPE}
        sessionKey={null}
        getToken={() => "tok"}
        {...props}
      />
    </ClientProvider>,
  );
  return client;
}

async function waitForShells() {
  await waitFor(() => expect(screen.getByTestId("dock-shell-select")).toHaveValue("zsh"));
}

function startedEvent(
  id: string,
  status: "ok" | "already_running" = "ok",
  pty?: boolean,
): TerminalEvent {
  return {
    event: "terminal_started",
    type: "terminal_started",
    terminal_id: id,
    shell: "/bin/bash",
    cwd: "/tmp/project",
    status,
    ...(pty === undefined ? {} : { pty }),
  } as unknown as TerminalEvent;
}

function listResultEvent(
  terminals: Array<{ terminal_id: string; status: "running" | "exited"; shell: string }>,
): TerminalEvent {
  return {
    event: "terminal_list_result",
    terminals: terminals.map((entry) => ({ ...entry, cwd: "/tmp/project" })),
  } as unknown as TerminalEvent;
}

beforeEach(() => {
  sessionStorage.clear();
  dockStore.resetForTests();
  mocks.fetchTerminalShells.mockReset().mockResolvedValue(shellsPayload);
  mocks.fetchWorkspaceFiles.mockReset().mockResolvedValue(directoryPayload);
  mocks.fetchFilePreview.mockReset().mockResolvedValue(previewPayload);
});

describe("StartDock launcher empty state", () => {
  it("shows the three cards, the shell select and probes shells + terminal_list on mount", async () => {
    const client = renderDock();
    expect(screen.getByTestId("start-dock")).toBeInTheDocument();
    expect(screen.getByTestId("dock-launcher")).toBeInTheDocument();
    expect(screen.getByTestId("dock-card-files")).toBeInTheDocument();
    expect(screen.getByTestId("dock-card-browser")).toBeInTheDocument();
    expect(screen.getByTestId("dock-card-terminal")).toBeInTheDocument();
    expect(screen.getByTestId("dock-shell-select")).toBeInTheDocument();
    expect(client.terminalList).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(mocks.fetchTerminalShells).toHaveBeenCalledWith("tok"));
    await waitFor(() => expect(screen.getByTestId("dock-shell-select")).toHaveValue("zsh"));
  });

  it("clicking a launcher card opens its tab and drops the launcher", () => {
    renderDock();
    fireEvent.click(screen.getByTestId("dock-card-browser"));
    expect(screen.getByTestId("dock-tab-browser")).toBeInTheDocument();
    expect(screen.queryByTestId("dock-launcher")).toBeNull();
    expect(screen.getByTestId("browser-section")).toBeInTheDocument();
  });

  it("terminal card + shell select sends terminal_start with the chosen shell and titled tab", async () => {
    const client = renderDock();
    await waitForShells();
    fireEvent.change(screen.getByTestId("dock-shell-select"), { target: { value: "bash" } });
    fireEvent.click(screen.getByTestId("dock-card-terminal"));
    const id = client.terminalStart.mock.calls[0][0] as string;
    expect(client.terminalStart).toHaveBeenCalledWith(id, null, "bash");
    expect(screen.getByTestId(`dock-tab-${id}`)).toHaveTextContent(">. bash");
    expect(screen.getByTestId(`terminal-surface-${id}`)).toBeInTheDocument();
  });

  it("files card opens the workspace tree tab and previews through the scoped endpoint", async () => {
    renderDock();
    fireEvent.click(screen.getByTestId("dock-card-files"));
    expect(await screen.findByText("README.md")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /README\.md/ }));
    await waitFor(() => expect(mocks.fetchFilePreview).toHaveBeenCalledWith(
      "tok", "websocket:home", "README.md", "", "default",
    ));
    expect(await screen.findByText("# Hello from preview", { exact: false })).toBeInTheDocument();
  });

  it("falls back to the platform default when the shells fetch fails", async () => {
    mocks.fetchTerminalShells.mockRejectedValue(new Error("offline"));
    const client = renderDock();
    await waitFor(() => expect(mocks.fetchTerminalShells).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("dock-card-terminal"));
    const id = client.terminalStart.mock.calls[0][0] as string;
    expect(client.terminalStart).toHaveBeenCalledWith(id, null, null);
    expect(screen.getByTestId(`dock-tab-${id}`)).toHaveTextContent(">. terminal");
  });
});

describe("StartDock plus menu", () => {
  it("files/browser are focus-or-open and the terminal item lists the shell submenu", async () => {
    const client = renderDock();
    await waitForShells();

    fireEvent.click(screen.getByTestId("dock-plus-button"));
    fireEvent.click(screen.getByTestId("dock-plus-files"));
    fireEvent.click(screen.getByTestId("dock-plus-button"));
    fireEvent.click(screen.getByTestId("dock-plus-files"));
    expect(screen.getAllByTestId("dock-tab-file")).toHaveLength(1);

    fireEvent.click(screen.getByTestId("dock-plus-button"));
    fireEvent.click(screen.getByTestId("dock-plus-browser"));
    fireEvent.click(screen.getByTestId("dock-plus-button"));
    fireEvent.click(screen.getByTestId("dock-plus-browser"));
    expect(screen.getAllByTestId("dock-tab-browser")).toHaveLength(1);

    fireEvent.click(screen.getByTestId("dock-plus-button"));
    fireEvent.click(screen.getByTestId("dock-plus-terminal"));
    fireEvent.click(await screen.findByTestId("dock-shell-zsh"));
    expect(client.terminalStart).toHaveBeenLastCalledWith(
      client.terminalStart.mock.calls.at(-1)![0], null, "zsh",
    );
  });

  it("caps terminal tabs at eight", async () => {
    const client = renderDock();
    await waitForShells();
    for (let index = 0; index < 9; index += 1) {
      fireEvent.click(screen.getByTestId("dock-plus-button"));
      fireEvent.click(screen.getByTestId("dock-plus-terminal"));
      const menuItem = await screen.findByTestId("dock-shell-bash");
      fireEvent.click(menuItem);
    }
    const ids = new Set(client.terminalStart.mock.calls.map((call) => call[0]));
    expect(ids.size).toBe(8);
    const terminalTabs = screen.getAllByRole("tab", { name: /^>\./ });
    expect(terminalTabs).toHaveLength(8);
  });
});

describe("StartDock tab close (terminal_kill + tombstone)", () => {
  it("closing a terminal tab sends terminal_kill, removes the tab and clears persistence", async () => {
    const client = renderDock();
    await waitForShells();
    fireEvent.click(screen.getByTestId("dock-card-terminal"));
    const id = client.terminalStart.mock.calls[0][0] as string;
    fireEvent.click(screen.getByTestId(`dock-close-${id}`));
    expect(client.terminalKill).toHaveBeenCalledWith(id);
    expect(screen.queryByTestId(`dock-tab-${id}`)).toBeNull();
    expect(screen.getByTestId("dock-launcher")).toBeInTheDocument();
    expect(dockStore.persistedTerminals(HOME_DOCK_SCOPE)).toEqual([]);
  });

  it("a killed tab is not resurrected by a later terminal_list_result", async () => {
    const client = renderDock();
    await waitForShells();
    fireEvent.click(screen.getByTestId("dock-card-terminal"));
    const id = client.terminalStart.mock.calls[0][0] as string;
    fireEvent.click(screen.getByTestId(`dock-close-${id}`));
    client.emit(listResultEvent([{ terminal_id: id, status: "running", shell: "/bin/bash" }]));
    expect(screen.queryByTestId(`dock-tab-${id}`)).toBeNull();
  });
});

describe("StartDock live terminal restore", () => {
  it("restores running terminals from terminal_list_result as titled tabs", () => {
    const client = renderDock();
    expect(client.terminalList).toHaveBeenCalled();
    client.emit(listResultEvent([
      { terminal_id: "remote-1", status: "running", shell: "/bin/bash" },
      { terminal_id: "remote-2", status: "exited", shell: "/bin/zsh" },
    ]));
    expect(screen.getByTestId("dock-tab-remote-1")).toHaveTextContent(">. bash");
    expect(screen.queryByTestId("dock-tab-remote-2")).toBeNull();
    // 恢复只挂标签，不重建会话（不发 terminal_start）。
    expect(client.terminalStart).not.toHaveBeenCalled();
  });

  it("leaves persisted takeover candidates to the banner instead of auto-restoring them", () => {
    sessionStorage.setItem(TAKEOVER_KEY, JSON.stringify([{ id: "term-old", shell: "/bin/zsh" }]));
    const client = renderDock();
    client.emit(listResultEvent([{ terminal_id: "term-old", status: "running", shell: "/bin/zsh" }]));
    expect(screen.queryByTestId("dock-tab-term-old")).toBeNull();
    expect(screen.getByTestId("dock-takeover-term-old")).toBeInTheDocument();
    expect(client.terminalStart).not.toHaveBeenCalled();
  });
});

describe("StartDock takeover banner", () => {
  it("re-opens a persisted terminal by its original id and drops the banner", () => {
    sessionStorage.setItem(TAKEOVER_KEY, JSON.stringify([{ id: "term-old", shell: "/bin/zsh" }]));
    const client = renderDock();
    const banner = screen.getByTestId("dock-takeover-term-old");
    expect(banner).toHaveTextContent(">. zsh");
    fireEvent.click(banner);
    expect(client.terminalStart).toHaveBeenCalledWith("term-old", null, null);
    expect(screen.getByTestId("dock-tab-term-old")).toBeInTheDocument();
    expect(screen.queryByTestId("dock-takeover-term-old")).toBeNull();
  });

  it("excludes ids already open as tabs from the banner", () => {
    sessionStorage.setItem(TAKEOVER_KEY, JSON.stringify([{ id: "term-open", shell: "/bin/zsh" }]));
    dockStore.openTerminal(HOME_DOCK_SCOPE, "term-open", "/bin/zsh");
    renderDock();
    expect(screen.getByTestId("dock-tab-term-open")).toBeInTheDocument();
    expect(screen.queryByTestId("dock-takeover-term-open")).toBeNull();
  });
});

describe("StartDock reuses TerminalSurface for the pty flag", () => {
  it("keeps the line-mode composer while pty is false and drops it once pty is true", async () => {
    const client = renderDock();
    await waitForShells();
    fireEvent.click(screen.getByTestId("dock-card-terminal"));
    const id = client.terminalStart.mock.calls[0][0] as string;
    expect(screen.getByText(/Starting terminal/)).toBeInTheDocument();

    client.emit(startedEvent(id, "ok", false));
    expect(screen.getByTestId(`terminal-output-${id}`)).toBeInTheDocument();
    expect(screen.getByTestId("terminal-input")).toBeInTheDocument();

    client.emit(startedEvent(id, "already_running", true));
    expect(screen.queryByTestId("terminal-input")).toBeNull();
    // 标题跟随 terminal_started 回报的 shell 路径。
    expect(screen.getByTestId(`dock-tab-${id}`)).toHaveTextContent(">. bash");
  });
});

describe("StartDock collapse rail", () => {
  it("collapses to a rail and re-expands keeping the start-dock testid", () => {
    renderDock();
    fireEvent.click(screen.getByRole("button", { name: /Collapse/i }));
    expect(screen.queryByTestId("dock-launcher")).toBeNull();
    expect(screen.getByTestId("start-dock")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Expand/i }));
    expect(screen.getByTestId("dock-launcher")).toBeInTheDocument();
  });
});
