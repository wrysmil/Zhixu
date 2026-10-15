import {
  type ReactNode,
  useCallback,
  useRef,
} from "react";
import {
  Bot,
  BookOpen,
  CalendarClock,
  Feather,
  MessagesSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plug,
  Settings,
  Sparkles,
  UserRound,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  SIDEBAR_SELECTION_ACTION_ITEM_CLASS,
  SidebarSelectionHighlight,
} from "@/components/SidebarSelectionHighlight";
import type {
  WorkspaceNavigationProps,
  WorkspaceNavigationSlotContext,
  WorkspaceRoute,
  WorkspaceView,
} from "@/components/workspace/contracts";
import { BrandMark } from "@/components/workspace/BrandMark";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

interface NavItem {
  view: WorkspaceView;
  /** Reuse the legacy `sidebar.*` keys so capability pages keep their tested
   *  English accessible names (Apps/Skills/Automations/Agents/Settings). */
  labelKey: string;
  labelDefault: string;
  icon: typeof MessagesSquare;
}

const PRIMARY_ITEMS: NavItem[] = [
  { view: "chat", labelKey: "workspace.nav.assistant", labelDefault: "Assistant", icon: MessagesSquare },
  { view: "creative", labelKey: "workspace.nav.creative", labelDefault: "Creative", icon: Feather },
];

const CAPABILITY_ITEMS: NavItem[] = [
  { view: "apps", labelKey: "sidebar.apps", labelDefault: "Apps", icon: Plug },
  { view: "skills", labelKey: "sidebar.skills.title", labelDefault: "Skills", icon: Sparkles },
  { view: "automations", labelKey: "sidebar.automations", labelDefault: "Automations", icon: CalendarClock },
  { view: "agents", labelKey: "sidebar.agents", labelDefault: "Agents", icon: Bot },
];

const SETTINGS_ITEM: NavItem = {
  view: "settings",
  labelKey: "sidebar.settings",
  labelDefault: "Settings",
  icon: Settings,
};

const MEMORY_ITEM: NavItem = {
  view: "memory", labelKey: "sidebar.memory.title", labelDefault: "Memory",
  icon: BookOpen,
};

const IDENTITY_ITEM: NavItem = {
  view: "identity", labelKey: "sidebar.identity.title", labelDefault: "Identity",
  icon: UserRound,
};

/**
 * Global 知序 icon navigation. It owns the sidebar landmark, the selection
 * highlight, the nav entries and the collapse control. The chat-context column
 * and the connection status are injected by the caller through slots so the
 * live shell keeps owning ChatList callbacks while the preview injects
 * fixtures. Non-assistant pages render only the nav column.
 */
export function WorkspaceNavigation({
  activeView,
  activeSettingsSection,
  collapsed,
  preview,
  onNavigate,
  onToggleCollapsed,
  chatNavigation,
  connectionStatus,
  footerAction,
  newChatActive = false,
  activeUtility = null,
  hostChromeInset = false,
}: WorkspaceNavigationProps) {
  const { t, i18n } = useTranslation();
  const activeActionRef = useRef<HTMLButtonElement>(null);
  const activeRailRef = useRef<HTMLButtonElement>(null);

  const selectionActiveId = newChatActive
    ? "new-chat"
    : activeUtility
      ? `utility:${activeUtility}`
      : null;

  const showChatColumn = activeView === "chat" && !collapsed;
  const chatSlot: ReactNode =
    typeof chatNavigation === "function"
      ? (chatNavigation as (ctx: WorkspaceNavigationSlotContext) => ReactNode)({
          activeActionRef,
        })
      : chatNavigation;

  const topInset = hostChromeInset ? "pt-12" : "pt-4";

  const navigateTo = useCallback(
    (view: WorkspaceView) => onNavigate({ view } satisfies WorkspaceRoute),
    [onNavigate],
  );

  const renderItem = (item: NavItem) => {
    const label = t(item.labelKey, { defaultValue: item.labelDefault });
    const matchesView = (
      activeView === item.view ||
      (item.view === "creative" && activeView === "article")
    );
    // activeSettingsSection is no longer used for highlighting Memory/Identity
    // because both are now standalone views, but it stays in the prop shape
    // so existing callers do not need to change.
    void activeSettingsSection;
    const active = matchesView;
    const Icon = item.icon;
    return (
      <Tooltip key={item.view}>
      <TooltipTrigger asChild>
      <button
        type="button"
        aria-label={label}
        aria-current={active ? "page" : undefined}
        title={label}
        ref={active ? activeRailRef : undefined}
        onClick={() => navigateTo(item.view)}
        className={cn(
          "workspace-nav-item host-no-drag flex h-[48px] w-[46px] cursor-pointer flex-col items-center justify-center gap-1 rounded-control transition-colors duration-150",
          SIDEBAR_SELECTION_ACTION_ITEM_CLASS,
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          active
            ? "bg-accent font-medium text-sidebar-foreground"
            : "text-sidebar-foreground/85 hover:bg-sidebar-foreground/[0.05] hover:text-sidebar-foreground dark:hover:bg-white/[0.06]",
        )}
      >
        <Icon className={cn("h-[19px] w-[19px] shrink-0", active && "text-accent-foreground")} strokeWidth={1.65} aria-hidden />
        <span className="workspace-nav-label" aria-hidden>{item.view === "automations"
          ? (i18n.resolvedLanguage?.startsWith("zh") ? "定时" : "Tasks")
          : item.view === "memory"
            ? (i18n.resolvedLanguage?.startsWith("zh") ? "记忆" : "Memory")
            : item.view === "identity"
              ? (i18n.resolvedLanguage?.startsWith("zh") ? "身份" : "Identity")
              : label}</span>
      </button>
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
      </Tooltip>
    );
  };

  return (
    <nav
      aria-label={t("sidebar.navigation")}
      className={cn(
        "workspace-navigation h-full w-full min-w-0 text-sidebar-foreground",
        hostChromeInset ? "bg-transparent" : "bg-sidebar",
      )}
    >
      <TooltipProvider delayDuration={150}>
      <SidebarSelectionHighlight
        targetRef={newChatActive ? activeActionRef : activeRailRef}
        activeId={selectionActiveId}
        scope="actions"
        className="relative flex h-full w-full min-w-0 flex-row"
      >
        <div
          data-testid="sidebar-rail"
          className={cn(
            "workspace-navigation-rail flex w-rail min-h-0 shrink-0 flex-col items-center gap-1 overflow-y-auto overscroll-contain border-r border-sidebar-border pb-3",
            topInset,
          )}
        >
          <div
            className={cn(
              "flex shrink-0 items-center justify-center pb-3",
            )}
          >
            <BrandMark className="h-9 w-9" />
            <span className="sr-only">{t("workspace.brand", { defaultValue: "知序" })}</span>
          </div>
          {preview ? (
            <span
              data-preview-badge
              className="sr-only"
            >
              {t("workspace.preview.badge", { defaultValue: "界面预览 · 示例内容" })}
            </span>
          ) : null}
          <div className="flex shrink-0 flex-col gap-1">{PRIMARY_ITEMS.map(renderItem)}</div>
          <div className="my-1 h-px w-7 bg-sidebar-border" />
          <div className="flex shrink-0 flex-col gap-1">
            {CAPABILITY_ITEMS.map(renderItem)}
            {renderItem(MEMORY_ITEM)}
            {renderItem(IDENTITY_ITEM)}
          </div>
          <div className="min-h-8 flex-1" />
          <div className="flex shrink-0 flex-col items-center gap-2">
            {renderItem(SETTINGS_ITEM)}
          </div>
          <div className="workspace-language-switcher"><LanguageSwitcher /></div>
          {footerAction ? <div className="flex shrink-0 items-center justify-center">{footerAction}</div> : null}
          {activeView === "chat" ? <div className="shrink-0">
            <RailToggleButton
              collapsed={collapsed}
              label={
                collapsed
                  ? t("thread.header.toggleSidebar")
                  : t("sidebar.collapse")
              }
              onClick={onToggleCollapsed}
            />
          </div> : null}

          {!collapsed && !showChatColumn && connectionStatus ? (
            <div className="sr-only">
              {connectionStatus}
            </div>
          ) : null}
        </div>
        {showChatColumn ? (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-background/50">
            {chatSlot}
          </div>
        ) : null}
      </SidebarSelectionHighlight>
      </TooltipProvider>
    </nav>
  );
}

function RailToggleButton({
  collapsed,
  label,
  onClick,
}: {
  collapsed: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <Button
      type="button"
      variant={null}
      size="icon"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cn(
        "host-no-drag flex cursor-pointer items-center justify-center gap-2 rounded-control text-muted-foreground hover:bg-sidebar-foreground/[0.06] hover:text-sidebar-foreground",
        "h-10 w-10",
      )}
    >
      {collapsed ? (
        <PanelLeftOpen className="h-4 w-4" aria-hidden />
      ) : (
        <>
          <PanelLeftClose className="h-4 w-4 shrink-0" aria-hidden />
          <span className="sr-only">{label}</span>
        </>
      )}
    </Button>
  );
}
