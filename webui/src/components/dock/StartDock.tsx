// 标签式右侧坞（批次 home-dock-pty-terminal-v1.1 Task 9）。
// 单组件双宿主：首页 rail + 会话页覆盖在工作台列上的工具侧边栏（embedded）。
// 承接 v1 TerminalSection 的容器职责：挂载即 ``terminal_list`` 探活并按
// ``terminal_list_result`` 恢复存活终端标签；file/browser 单例 focus-or-open、
// terminal 标签 ≤8（dockStore 强制）；关闭 terminal 标签走 ``terminal_kill`` +
// store.remove，tombstone 语义保证后续 list 不再复活该标签；空态 = v1 三卡启动器。
// 接管条：dockStore.persistedTerminals 返回原始清单（含已开 tab 的 id），
// 渲染方必须自行排除 snapshot 已存在的 tab id（orchestrator 裁定 1）。
// terminal 标签内容直接复用 TerminalSurface——pty===true 走 xterm 直通、
// false/缺省保持行式回退，dock 外层不再分叉（orchestrator 裁定 3）。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Files,
  Globe,
  PanelRightClose,
  Plus,
  SquareTerminal,
  X,
  type LucideIcon,
} from "lucide-react";

import { DockFileTab } from "@/components/dock/DockFileTab";
import { ResizableRail } from "@/components/workbench/ResizableRail";
import { BrowserSection } from "@/components/workbench/BrowserSection";
import { TerminalSurface } from "@/components/workbench/TerminalSurface";
import { defaultGenerateTerminalId } from "@/components/workbench/terminal-shared";
import { useTerminalStore } from "@/hooks/useZhixuStream";
import { fetchTerminalShells, type TerminalShellsPayload } from "@/lib/api";
import {
  dockStore,
  dockTerminalTitle,
  useDockTabs,
  consumeDockTerminalPanelRequest,
  subscribeDockTerminalPanel,
  type DockTab,
  type DockTabKind,
  type PersistedTerminal,
} from "@/lib/dock-store";
import type { TerminalEvent } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";

interface CardSpec {
  id: string;
  icon: LucideIcon;
  titleKey: string;
  titleDefault: string;
  descKey: string;
  descDefault: string;
  shortcut: string | null;
}

// v1 HomeStartPanel 的三卡启动器原文搬入（空态即 launcher）。
const CARD_SPECS: CardSpec[] = [
  {
    id: "files",
    icon: Files,
    titleKey: "startPanel.files",
    titleDefault: "Workspace files",
    descKey: "startPanel.filesDesc",
    descDefault: "Browse files in the session workspace",
    shortcut: "⌘P",
  },
  {
    id: "terminal",
    icon: SquareTerminal,
    titleKey: "startPanel.terminal",
    titleDefault: "New terminal",
    descKey: "startPanel.terminalDesc",
    descDefault: "Run commands in the session workspace",
    shortcut: "⌘J",
  },
  {
    id: "browser",
    icon: Globe,
    titleKey: "startPanel.browser",
    titleDefault: "Browser",
    descKey: "startPanel.browserDesc",
    descDefault: "Browse the web",
    shortcut: "⌘T",
  },
];

function TabKindIcon({ kind }: { kind: DockTabKind }) {
  const Icon = kind === "file" ? Files : kind === "browser" ? Globe : SquareTerminal;
  return <Icon className="h-3 w-3 shrink-0 opacity-70" aria-hidden />;
}

export interface StartDockProps {
  /** 宿主形态：rail = 首页独立右坞；embedded = 会话页工具侧边栏，铺满宿主给的容器（不渲染自身 aside）。 */
  variant?: "rail" | "embedded";
  /** dock scope：首页为 HOME_DOCK_SCOPE，会话页为 `websocket:<chat_id>`（Task 10）。 */
  scope: string;
  sessionKey: string | null;
  getToken: () => string;
  /** 传入则文件预览上抛宿主（SessionWorkbench 外置预览面板语义）。 */
  onOpenFilePreview?: (path: string) => void;
  /**
   * 会话覆盖层宿主传入收起回调；首页由顶栏开关收起，不传以避免两个收起按钮。
   * 收起一律由宿主卸载整条坞，组件内部不留 40px 竖条。
   */
  onCollapse?: () => void;
}

export function StartDock({
  variant = "rail",
  scope,
  sessionKey,
  getToken,
  onOpenFilePreview,
  onCollapse,
}: StartDockProps) {
  const { t } = useTranslation();
  const isEmbedded = variant === "embedded";
  const { client } = useClient();
  const snapshot = useDockTabs(scope);
  const { store: terminalStore, terminals } = useTerminalStore();
  const [plusOpen, setPlusOpen] = useState(false);
  const [shellSubOpen, setShellSubOpen] = useState(false);
  const [shells, setShells] = useState<TerminalShellsPayload | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  // 启动器不再做 shell 选择（规格 §6）：终端卡片直接以 platforms 默认 shell 开 tab；
  // + 菜单里的 shell 子菜单仍可指定 shell。
  // 本 dock 生命周期内被用户关闭过的 terminal id（tombstone）：
  // 后续 terminal_list_result 不得复活它们；terminal id 为 uuid，不会跨页复用。
  const closedTerminalIdsRef = useRef(new Set<string>());

  useEffect(() => {
    let cancelled = false;
    fetchTerminalShells(getToken())
      .then((payload) => {
        if (!cancelled) setShells(payload);
      })
      .catch(() => {
        if (!cancelled) setShells(null);
      });
    return () => {
      cancelled = true;
    };
  }, [getToken]);

  // 容器职责（v1 TerminalSection 移交）：挂载即探活。
  useEffect(() => {
    client.terminalList();
  }, [client]);

  // 按 terminal_list_result 恢复存活终端标签。接管候选（sessionStorage 清单里
  // 的 id）留给接管条走显式点击重绑，不自动开标签，避免抢掉横幅语义。
  useEffect(() => {
    return client.onTerminalEvent((event: TerminalEvent) => {
      if (event.event !== "terminal_list_result") return;
      for (const entry of event.terminals) {
        if (entry.status !== "running") continue;
        if (closedTerminalIdsRef.current.has(entry.terminal_id)) continue;
        const current = dockStore.getSnapshot(scope);
        if (current.tabs.some((tab) => tab.id === entry.terminal_id)) continue;
        if (dockStore.persistedTerminals(scope).some((item) => item.id === entry.terminal_id)) {
          continue;
        }
        dockStore.openTerminal(scope, entry.terminal_id, entry.shell);
      }
    });
  }, [client, scope]);

  // 标题跟随：活动终端 terminal_started 回报 shell 后刷新 tab 标题（spec §3.1）。
  const activeTab = snapshot.tabs.find((tab) => tab.id === snapshot.activeId)
    ?? (snapshot.tabs.length > 0 ? snapshot.tabs[snapshot.tabs.length - 1] : undefined);
  const activeTerminal = activeTab && activeTab.kind === "terminal" ? activeTab : undefined;
  const activeRecord = activeTerminal ? terminals.byId[activeTerminal.id] ?? null : null;
  useEffect(() => {
    if (activeTerminal && activeRecord?.shell) {
      dockStore.setTerminalTitle(scope, activeTerminal.id, activeRecord.shell);
    }
  }, [scope, activeTerminal?.id, activeRecord?.shell]);

  const takeovers = useMemo(
    () =>
      dockStore
        .persistedTerminals(scope)
        .filter((entry) => !snapshot.tabs.some((tab) => tab.id === entry.id)),
    // persisted 清单在 store 变更时随之重算（tabs 变化即重渲）。
    [scope, snapshot],
  );

  const openTerminalTab = useCallback((shell: string | null) => {
    setPlusOpen(false);
    setShellSubOpen(false);
    const id = defaultGenerateTerminalId();
    if (dockStore.openTerminal(scope, id, shell) === null) return;
    terminalStore.beginCreate(id, sessionKey);
    client.terminalStart(id, sessionKey, shell);
  }, [client, scope, sessionKey, terminalStore]);

  // 顶栏「打开终端面板」与 ⌘J 共用同一意图：挂载后取用一次，
  // 已挂载时由订阅即时消费，避免首页懒加载期间丢请求。
  useEffect(() => {
    const consumeRequest = () => {
      if (consumeDockTerminalPanelRequest()) openTerminalTab(null);
    };
    consumeRequest();
    return subscribeDockTerminalPanel(consumeRequest);
  }, [openTerminalTab]);

  const takeOver = (entry: PersistedTerminal) => {
    if (dockStore.openTerminal(scope, entry.id, entry.shell) === null) return;
    terminalStore.beginCreate(entry.id, sessionKey);
    dockStore.forgetPersisted(scope, entry.id);
    // 同 id = 接管重绑（契约 v2），不带 shell。
    client.terminalStart(entry.id, sessionKey, null);
  };

  const closeTab = (tab: DockTab) => {
    if (tab.kind === "terminal") {
      closedTerminalIdsRef.current.add(tab.id);
      client.terminalKill(tab.id);
      terminalStore.remove(tab.id);
    }
    dockStore.closeTab(scope, tab.id);
  };

  const tabLabel = (tab: DockTab) => {
    if (tab.customTitle) return tab.customTitle;
    if (tab.kind === "file") return t("dock.tabFile", { defaultValue: "Files" });
    if (tab.kind === "browser") return t("dock.tabBrowser", { defaultValue: "Browser" });
    return tab.title;
  };

  const beginRename = (tab: DockTab) => {
    setRenamingId(tab.id);
    setRenameDraft(tabLabel(tab));
  };

  const commitRename = () => {
    if (!renamingId) return;
    dockStore.renameTab(scope, renamingId, renameDraft);
    setRenamingId(null);
  };

  const openCard = (cardId: string) => {
    if (cardId === "files") dockStore.openFile(scope);
    else if (cardId === "browser") dockStore.openBrowser(scope);
    else openTerminalTab(shells?.default ?? null);
  };

  const expandShellSubmenu = () => {
    if (shells && shells.shells.length > 0) {
      setShellSubOpen((value) => !value);
      return;
    }
    // 无 shell 数据（拉取失败/旧网关）：走平台默认 shell。
    openTerminalTab(null);
  };

  const shellInner = (
    <>
      <header className="flex h-dockHeader shrink-0 items-center gap-1 border-b border-border/40 px-2">
        <div role="tablist" className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
          {snapshot.tabs.map((tab) => {
            const isActive = tab.id === snapshot.activeId;
            return (
              <span
                key={tab.id}
                className={cn(
                  "flex shrink-0 items-center gap-0.5 rounded px-1",
                  isActive && "bg-muted/70",
                )}
              >
                {renamingId === tab.id ? (
                  <input
                    autoFocus
                    data-testid={`dock-tab-rename-${tab.id}`}
                    aria-label={t("dock.renameTab", { defaultValue: "Rename tab" })}
                    value={renameDraft}
                    maxLength={80}
                    onChange={(event) => setRenameDraft(event.target.value)}
                    onBlur={commitRename}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        commitRename();
                      } else if (event.key === "Escape") {
                        event.preventDefault();
                        setRenamingId(null);
                      }
                    }}
                    className="w-28 rounded border border-border/60 bg-background px-1 py-0.5 text-[11.5px] text-foreground outline-none focus-visible:ring-1 focus-visible:ring-ring/60"
                  />
                ) : (
                  <button
                    type="button"
                    role="tab"
                    aria-selected={isActive}
                    data-testid={`dock-tab-${tab.id}`}
                    onClick={() => dockStore.focusTab(scope, tab.id)}
                    onDoubleClick={() => beginRename(tab)}
                    onAuxClick={(event) => {
                      if (event.button !== 1) return;
                      event.preventDefault();
                      closeTab(tab);
                    }}
                    className={cn(
                      "flex max-w-[10rem] items-center gap-1 truncate rounded px-1.5 py-0.5 text-[11.5px] text-muted-foreground/80 hover:bg-muted/60 hover:text-foreground",
                      isActive && "font-medium text-foreground",
                    )}
                  >
                    <TabKindIcon kind={tab.kind} />
                    <span className="truncate">{tabLabel(tab)}</span>
                  </button>
                )}
                <button
                  type="button"
                  aria-label={t("dock.closeTab", { defaultValue: "Close tab" })}
                  data-testid={`dock-close-${tab.id}`}
                  onClick={() => closeTab(tab)}
                  className="grid h-4 w-4 shrink-0 place-items-center rounded text-muted-foreground/60 hover:bg-muted/60 hover:text-foreground"
                >
                  <X className="h-3 w-3" aria-hidden />
                </button>
              </span>
            );
          })}
        </div>
        <div className="relative shrink-0">
          <button
            type="button"
            aria-label={t("dock.plusAria", { defaultValue: "New tab" })}
            data-testid="dock-plus-button"
            onClick={() => setPlusOpen((value) => !value)}
            className="grid h-6 w-6 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground"
          >
            <Plus className="h-3.5 w-3.5" aria-hidden />
          </button>
          {plusOpen ? (
            <div
              role="menu"
              className="absolute right-0 top-7 z-20 flex w-40 flex-col rounded-lg border border-border/60 bg-background p-1 shadow-lg"
            >
              <button
                type="button"
                role="menuitem"
                data-testid="dock-plus-files"
                onClick={() => {
                  dockStore.openFile(scope);
                  setPlusOpen(false);
                }}
                className="flex items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-foreground/90 hover:bg-muted/60"
              >
                <Files className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                {t("dock.tabFile", { defaultValue: "Files" })}
              </button>
              <button
                type="button"
                role="menuitem"
                data-testid="dock-plus-browser"
                onClick={() => {
                  dockStore.openBrowser(scope);
                  setPlusOpen(false);
                }}
                className="flex items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-foreground/90 hover:bg-muted/60"
              >
                <Globe className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                {t("dock.tabBrowser", { defaultValue: "Browser" })}
              </button>
              <button
                type="button"
                role="menuitem"
                data-testid="dock-plus-terminal"
                onClick={expandShellSubmenu}
                className="flex items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-foreground/90 hover:bg-muted/60"
              >
                <SquareTerminal className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                {t("dock.newTerminal", { defaultValue: "New terminal" })}
              </button>
              {shellSubOpen && shells ? (
                <div data-testid="dock-shell-submenu" className="mt-1 border-t border-border/40 pt-1">
                  {shells.shells.map((shell) => (
                    <button
                      key={shell.id}
                      type="button"
                      role="menuitem"
                      data-testid={`dock-shell-${shell.id}`}
                      onClick={() => openTerminalTab(shell.id)}
                      className="flex w-full items-center rounded px-2 py-1 text-left text-[11.5px] text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                    >
                      {shell.label}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
        {onCollapse ? (
          <button
            type="button"
            aria-label={t("startPanel.collapse", { defaultValue: "Collapse the start panel" })}
            onClick={onCollapse}
            className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground"
          >
            <PanelRightClose className="h-3.5 w-3.5" aria-hidden />
          </button>
        ) : null}
      </header>

      {takeovers.length > 0 ? (
        <div className="shrink-0 border-b border-border/40 bg-muted/30 px-3 py-1.5">
          {takeovers.map((entry) => (
            <div key={entry.id} className="flex items-center gap-2">
              <button
                type="button"
                data-testid={`dock-takeover-${entry.id}`}
                onClick={() => takeOver(entry)}
                className="min-w-0 flex-1 truncate rounded px-1 py-0.5 text-left text-[11.5px] text-foreground/90 hover:bg-muted/60"
              >
                {t("dock.takeoverRunning", { defaultValue: "Running terminal" })}:{" "}
                {dockTerminalTitle(entry.shell)} —{" "}
                {t("dock.takeoverAction", { defaultValue: "Take over" })}
              </button>
              <p className="shrink-0 text-[10.5px] text-muted-foreground/70">
                {t("dock.takeoverHint", { defaultValue: "Press Enter to redraw" })}
              </p>
            </div>
          ))}
        </div>
      ) : null}

      {snapshot.tabs.length === 0 ? (
        <div
          data-testid="dock-launcher"
          className="dock-launcher-surface flex min-h-0 flex-1 flex-col overflow-y-auto px-3 py-4 scrollbar-thin"
        >
          {/* m-auto：空间足够时垂直居中，内容超高时自动让位给滚动。 */}
          <ul className="m-auto w-full space-y-2.5" role="list">
            {CARD_SPECS.map((card) => {
              const Icon = card.icon;
              return (
                <li key={card.id}>
                  <button
                    type="button"
                    data-testid={`dock-card-${card.id}`}
                    onClick={() => openCard(card.id)}
                    className={cn(
                      "group flex min-h-[76px] w-full cursor-pointer items-center gap-3 rounded-xl border border-border/60 bg-background/85 px-3.5 py-3 text-left",
                      "shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-[border-color,background-color,box-shadow] duration-200",
                      "hover:border-primary/30 hover:bg-background hover:shadow-[0_8px_22px_-14px_rgba(15,23,42,0.45)]",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/70",
                      "dark:bg-card/80 dark:hover:border-primary/35 dark:hover:bg-card",
                    )}
                  >
                    <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-border/50 bg-muted/40 text-foreground/75 transition-colors duration-200 group-hover:bg-primary/[0.08] group-hover:text-primary">
                      <Icon className="h-5 w-5" aria-hidden />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13.5px] font-semibold text-foreground/90">
                        {t(card.titleKey, { defaultValue: card.titleDefault })}
                      </span>
                      <span className="mt-0.5 block truncate text-[11.5px] text-muted-foreground">
                        {t(card.descKey, { defaultValue: card.descDefault })}
                      </span>
                    </span>
                    {card.shortcut ? (
                      <kbd className="shrink-0 rounded-md border border-border/60 bg-muted/40 px-2 py-0.5 text-[10.5px] tabular-nums text-muted-foreground/80">
                        {card.shortcut}
                      </kbd>
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ) : (
        <div className="relative flex min-h-0 flex-1 flex-col">
          {snapshot.tabs.map((tab) => {
            const isActive = tab.id === snapshot.activeId;
            return (
              <div
                key={tab.id}
                data-testid={`dock-panel-${tab.id}`}
                aria-hidden={!isActive}
                className={cn(
                  "absolute inset-0 flex min-h-0 flex-col",
                  !isActive && "invisible pointer-events-none",
                )}
              >
                {tab.kind === "file" ? (
                  <DockFileTab
                    scope={scope}
                    sessionKey={sessionKey}
                    getToken={getToken}
                    onOpenFilePreview={onOpenFilePreview}
                  />
                ) : null}
                {tab.kind === "browser" ? (
                  <BrowserSection key={scope} fullHeight scope={scope} />
                ) : null}
                {tab.kind === "terminal" ? (
                  <TerminalSurface
                    terminalId={tab.id}
                    sessionKey={sessionKey}
                    active={isActive}
                  />
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </>
  );

  if (isEmbedded) {
    return (
      <div
        data-testid="start-dock"
        aria-label={t("startPanel.title", { defaultValue: "Start" })}
        className="flex h-full min-h-0 w-full flex-1 flex-col overflow-hidden border-l border-border/40 bg-muted/20"
      >
        {shellInner}
      </div>
    );
  }

  return (
    <ResizableRail label={t("workbench.resizeTools", { defaultValue: "Resize tools sidebar" })}
      defaultWidth={480} minWidth={320} className="hidden md:flex">
    <aside
      data-testid="start-dock"
      aria-label={t("startPanel.title", { defaultValue: "Start" })}
      className="flex h-full min-h-0 w-full flex-col overflow-hidden border-l border-border/60 bg-muted/20"
    >
      {shellInner}
    </aside>
    </ResizableRail>
  );
}
