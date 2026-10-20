// 标签式 dock 的标签表 store（批次 home-dock-pty-terminal-v1.1 Task 6）。
// 设计：按 scope（`websocket:home` 或 `websocket:<chat_id>`）隔离标签表；
// file/browser 为天然单例（再开 = 聚焦），terminal 标签上限 8、越限返回 null。
// sessionStorage 持久化各 terminal 标签的「原始 shell 路径」，供刷新后的接管条使用。
import { useSyncExternalStore } from "react";

export type DockTabKind = "file" | "browser" | "terminal";

export interface DockTab {
  id: string;
  kind: DockTabKind;
  title: string;
  customTitle?: string;
}

export interface DockScopeSnapshot {
  tabs: DockTab[];
  activeId: string | null;
}

export const HOME_DOCK_SCOPE = "websocket:home";
export const MAX_DOCK_TERMINAL_TABS = 8;
const PERSIST_PREFIX = "nanobot.dock.terminals.";
const PERSIST_TABS_PREFIX = "nanobot.dock.tabs.";

export interface PersistedTerminal {
  id: string;
  shell: string;
}

// 标题形态 `>. <shell 短名>`；无 shell 信息时回退 `>. terminal`。
export function dockTerminalTitle(shell: string | null): string {
  const name = shell ? shell.split(/[\\/]/).pop() : "";
  return `>. ${name || "terminal"}`;
}

function loadPersisted(scope: string): PersistedTerminal[] {
  try {
    const raw = sessionStorage.getItem(PERSIST_PREFIX + scope);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is PersistedTerminal =>
        !!item && typeof item === "object" &&
        typeof (item as { id?: unknown }).id === "string" &&
        typeof (item as { shell?: unknown }).shell === "string",
    );
  } catch {
    return [];
  }
}

function savePersisted(scope: string, entries: PersistedTerminal[]): void {
  try {
    sessionStorage.setItem(PERSIST_PREFIX + scope, JSON.stringify(entries));
  } catch {
    /* 隐私模式等场景：接管清单仅存活于本页面生命周期 */
  }
}

interface PersistedTab {
  id: string;
  kind: "file" | "browser";
  title: string;
  customTitle?: string;
}

function loadPersistedTabs(scope: string): DockScopeSnapshot | null {
  try {
    const raw = sessionStorage.getItem(PERSIST_TABS_PREFIX + scope);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { tabs?: unknown; activeId?: unknown };
    if (!Array.isArray(parsed.tabs)) return null;

    const tabs: DockTab[] = [];
    for (const item of parsed.tabs) {
      if (!item || typeof item !== "object") continue;
      const entry = item as Partial<PersistedTab>;
      if (entry.kind !== "file" && entry.kind !== "browser") continue;
      if (entry.id !== entry.kind) continue;
      if (tabs.some((tab) => tab.id === entry.id)) continue;
      const customTitle =
        typeof entry.customTitle === "string" && entry.customTitle.trim()
          ? entry.customTitle.trim().slice(0, 80)
          : undefined;
      tabs.push({
        id: entry.id,
        kind: entry.kind,
        title: typeof entry.title === "string" ? entry.title : "",
        customTitle,
      });
    }
    if (tabs.length === 0) return null;

    const activeId =
      typeof parsed.activeId === "string" &&
      tabs.some((tab) => tab.id === parsed.activeId)
        ? parsed.activeId
        : tabs[0]?.id ?? null;
    return { tabs, activeId };
  } catch {
    return null;
  }
}

function savePersistedTabs(scope: string, snapshot: DockScopeSnapshot): void {
  const tabs: PersistedTab[] = snapshot.tabs
    .filter((tab) => tab.kind !== "terminal")
    .map((tab) => ({
      id: tab.id,
      kind: tab.kind as "file" | "browser",
      title: tab.title,
      ...(tab.customTitle ? { customTitle: tab.customTitle } : {}),
    }));
  const activeId =
    snapshot.activeId && tabs.some((tab) => tab.id === snapshot.activeId)
      ? snapshot.activeId
      : null;
  try {
    if (tabs.length === 0) {
      sessionStorage.removeItem(PERSIST_TABS_PREFIX + scope);
    } else {
      sessionStorage.setItem(
        PERSIST_TABS_PREFIX + scope,
        JSON.stringify({ tabs, activeId }),
      );
    }
  } catch {
    /* private mode */
  }
}

const EMPTY_SNAPSHOT: DockScopeSnapshot = { tabs: [], activeId: null };

type Listener = () => void;

class DockStore {
  private snapshots = new Map<string, DockScopeSnapshot>();
  private hydratedScopes = new Set<string>();
  private listeners = new Set<Listener>();
  // terminalId -> 原始 shell 路径（openTerminal/setTerminalTitle 收到的路径，非标题形态）。
  // terminal id 为 uuid 形态、全局唯一，故表无需按 scope 分片。
  private terminalShells = new Map<string, string>();

  getSnapshot(scope: string): DockScopeSnapshot {
    if (!this.hydratedScopes.has(scope)) {
      this.hydratedScopes.add(scope);
      if (!this.snapshots.has(scope)) {
        const restored = loadPersistedTabs(scope);
        if (restored) this.snapshots.set(scope, restored);
      }
    }
    return this.snapshots.get(scope) ?? EMPTY_SNAPSHOT;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  openFile(scope: string): string {
    return this.openSingleton(scope, "file", "file");
  }

  openBrowser(scope: string): string {
    return this.openSingleton(scope, "browser", "browser");
  }

  openTerminal(scope: string, terminalId: string, shell: string | null): string | null {
    const current = this.getSnapshot(scope);
    const existing = current.tabs.find((tab) => tab.id === terminalId);
    if (existing) {
      // 已存在 = 聚焦，不新增重复标签。
      this.setSnapshot(scope, { tabs: current.tabs, activeId: terminalId });
      return terminalId;
    }
    const liveCount = current.tabs.filter((tab) => tab.kind === "terminal").length;
    if (liveCount >= MAX_DOCK_TERMINAL_TABS) return null;
    this.terminalShells.set(terminalId, shell ?? "");
    const tabs = [...current.tabs, {
      id: terminalId,
      kind: "terminal" as const,
      title: dockTerminalTitle(shell),
    }];
    this.setSnapshot(scope, { tabs, activeId: terminalId });
    this.writePersisted(scope, tabs);
    return terminalId;
  }

  focusTab(scope: string, tabId: string): void {
    const current = this.getSnapshot(scope);
    if (!current.tabs.some((tab) => tab.id === tabId)) return;
    this.setSnapshot(scope, { tabs: current.tabs, activeId: tabId });
  }

  closeTab(scope: string, tabId: string): void {
    const current = this.getSnapshot(scope);
    const tabs = current.tabs.filter((tab) => tab.id !== tabId);
    // activeId 回退：关闭活动标签后落到剩余最后一个（无剩余则 null）。
    const activeId = current.activeId === tabId ? tabs[tabs.length - 1]?.id ?? null : current.activeId;
    this.setSnapshot(scope, { tabs, activeId });
    this.terminalShells.delete(tabId);
    this.writePersisted(scope, tabs);
  }

  renameTab(scope: string, tabId: string, title: string): void {
    const current = this.getSnapshot(scope);
    if (!current.tabs.some((tab) => tab.id === tabId)) return;
    const customTitle = title.trim().slice(0, 80);
    const tabs = current.tabs.map((tab) =>
      tab.id === tabId
        ? { ...tab, customTitle: customTitle || undefined }
        : tab,
    );
    this.setSnapshot(scope, { tabs, activeId: current.activeId });
  }

  setTerminalTitle(scope: string, terminalId: string, shellPath: string): void {
    const current = this.getSnapshot(scope);
    if (!current.tabs.some((tab) => tab.id === terminalId)) return;
    this.terminalShells.set(terminalId, shellPath);
    const tabs = current.tabs.map((tab) =>
      tab.id === terminalId ? { ...tab, title: dockTerminalTitle(shellPath) } : tab,
    );
    this.setSnapshot(scope, { tabs, activeId: current.activeId });
    this.writePersisted(scope, tabs);
  }

  // 接管清单：sessionStorage 中该 scope 的 terminal 条目。
  // 注意：本页生命周期内新开的标签也会出现在这里（测试契约如此），
  // 接管条渲染方（Task 9 StartDock）应过滤掉当前 tabs 已存在的 id。
  persistedTerminals(scope: string): PersistedTerminal[] {
    return loadPersisted(scope);
  }

  forgetPersisted(scope: string, terminalId: string): void {
    savePersisted(scope, loadPersisted(scope).filter((entry) => entry.id !== terminalId));
  }

  // 仅供测试重置内存快照。
  resetForTests(): void {
    this.snapshots.clear();
    this.hydratedScopes.clear();
    this.terminalShells.clear();
  }

  private openSingleton(scope: string, kind: "file" | "browser", id: string): string {
    const current = this.getSnapshot(scope);
    if (!current.tabs.some((tab) => tab.id === id)) {
      this.setSnapshot(scope, {
        tabs: [...current.tabs, { id, kind, title: "" }],
        activeId: id,
      });
    } else {
      this.setSnapshot(scope, { tabs: current.tabs, activeId: id });
    }
    return id;
  }

  // persisted entry 的 shell 存「原始 shell 路径」（terminalShells），不存 `>. zsh` 标题。
  private writePersisted(scope: string, tabs: DockTab[]): void {
    savePersisted(
      scope,
      tabs
        .filter((tab) => tab.kind === "terminal")
        .map((tab) => ({ id: tab.id, shell: this.terminalShells.get(tab.id) ?? "" })),
    );
  }

  private setSnapshot(scope: string, next: DockScopeSnapshot): void {
    this.snapshots.set(scope, next);
    savePersistedTabs(scope, next);
    for (const listener of [...this.listeners]) listener();
  }
}

export const dockStore = new DockStore();

export function useDockTabs(scope: string): DockScopeSnapshot {
  return useSyncExternalStore(
    (listener) => dockStore.subscribe(listener),
    () => dockStore.getSnapshot(scope),
    () => dockStore.getSnapshot(scope),
  );
}
