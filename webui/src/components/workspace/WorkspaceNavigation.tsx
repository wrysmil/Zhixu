import {
  type ReactNode,
  useCallback,
  useLayoutEffect,
  useRef,
} from "react";
import {
  Bot,
  BookOpen,
  CalendarClock,
  MessageCircleMore,
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
import { TeamStructureIcon } from "@/components/settings/teams/TeamVisuals";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

interface NavItem {
  view: WorkspaceView;
  /** Reuse the legacy `sidebar.*` keys so capability pages keep their tested
   *  English accessible names (Apps/Skills/Automations/Agents/Settings). */
  labelKey: string;
  labelDefault: string;
  icon: typeof MessageCircleMore | typeof TeamStructureIcon;
}

const APP_ITEM: NavItem = {
  view: "apps", labelKey: "sidebar.apps", labelDefault: "Apps", icon: Plug,
};
const SKILLS_ITEM: NavItem = {
  view: "skills", labelKey: "sidebar.skills.title", labelDefault: "Skills", icon: Sparkles,
};
const AUTOMATIONS_ITEM: NavItem = {
  view: "automations", labelKey: "sidebar.automations", labelDefault: "Automations", icon: CalendarClock,
};
const AGENTS_ITEM: NavItem = {
  view: "agents", labelKey: "sidebar.agents", labelDefault: "Agents", icon: Bot,
};
const MEMORY_ITEM: NavItem = {
  view: "memory", labelKey: "settings.nav.memory", labelDefault: "Memory",
  icon: BookOpen,
};
const IDENTITY_ITEM: NavItem = {
  view: "identity", labelKey: "settings.nav.identity", labelDefault: "Identity",
  icon: UserRound,
};
const TEAM_ITEM: NavItem = {
  view: "teams", labelKey: "settings.nav.teams", labelDefault: "Teams",
  icon: TeamStructureIcon,
};

// Keep the product's daily loop visible, then the capability pages. Every entry
// lives on the rail so no menu is nested behind an extra click.
const PRIMARY_ITEMS: NavItem[] = [
  { view: "chat", labelKey: "workspace.nav.assistant", labelDefault: "Assistant", icon: MessageCircleMore },
  AGENTS_ITEM,
  MEMORY_ITEM,
];

const CAPABILITY_ITEMS: NavItem[] = [
  APP_ITEM,
  SKILLS_ITEM,
  AUTOMATIONS_ITEM,
  TEAM_ITEM,
  IDENTITY_ITEM,
];

const SETTINGS_ITEM: NavItem = {
  view: "settings",
  labelKey: "sidebar.settings",
  labelDefault: "Settings",
  icon: Settings,
};


/**
 * Global 知序 icon navigation. It owns the sidebar landmark, the selection
 * highlight, the nav entries and the collapse control. The chat-context column
 * and the connection status are injected by the caller through slots so the
 * live shell keeps owning ChatList callbacks while the preview injects
 * fixtures. The rail lists every destination directly; the conversation column
 * stays visible until the user collapses it.
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
  hostChromeInset = false,
}: WorkspaceNavigationProps) {
  const { t, i18n } = useTranslation();
  const activeActionRef = useRef<HTMLButtonElement>(null);
  const activeRailRef = useRef<HTMLButtonElement>(null);
  const chatColumnRef = useRef<HTMLDivElement>(null);

  // The conversation column is part of the persistent left region: navigating to
  // any menu keeps it in place, and only the explicit collapse control hides it.
  const showChatColumn = !collapsed;
  const highlightNewChat = showChatColumn && newChatActive;
  const railItemActive =
    PRIMARY_ITEMS.some((item) => item.view === activeView)
    || CAPABILITY_ITEMS.some((item) => item.view === activeView)
    || SETTINGS_ITEM.view === activeView;
  const selectionActiveId = highlightNewChat
    ? "new-chat"
    : railItemActive
      ? `view:${activeView}`
      : null;

  const chatSlot: ReactNode =
    typeof chatNavigation === "function"
      ? (chatNavigation as (ctx: WorkspaceNavigationSlotContext) => ReactNode)({
          activeActionRef,
        })
      : chatNavigation;

  useLayoutEffect(() => {
    const column = chatColumnRef.current;
    if (!column) return;
    if (!showChatColumn && column.contains(document.activeElement)) {
      activeRailRef.current?.focus({ preventScroll: true });
    }
    // React 18 does not expose inert as a typed JSX boolean attribute.
    // https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Global_attributes/inert
    column.toggleAttribute("inert", !showChatColumn);
  }, [showChatColumn, activeView]);

  const topInset = hostChromeInset ? "pt-12" : "pt-4";

  const navigateTo = useCallback(
    (view: WorkspaceView) => onNavigate({ view } satisfies WorkspaceRoute),
    [onNavigate],
  );

  const renderItem = (item: NavItem) => {
    const label = t(item.labelKey, { defaultValue: item.labelDefault });
    const matchesView = activeView === item.view;
    // activeSettingsSection is no longer used for highlighting the rail items
    // because every page is now its own view (Memory/Identity/Teams included),
    // but it stays in the prop shape so existing callers do not need to change.
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
            ? "font-medium text-[hsl(var(--nav-active-fg))]"
            : "text-sidebar-foreground/85 hover:bg-sidebar-foreground/[0.05] hover:text-sidebar-foreground dark:hover:bg-white/[0.06]",
        )}
      >
        <Icon className="h-[19px] w-[19px] shrink-0" strokeWidth={item.view === "teams" ? undefined : 1.65} aria-hidden />
        <span className="workspace-nav-label" aria-hidden>{item.view === "automations"
          ? (i18n.resolvedLanguage?.startsWith("zh") ? "定时" : "Tasks")
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
        targetRef={highlightNewChat ? activeActionRef : activeRailRef}
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
          <div data-testid="sidebar-rail-brand" className="flex shrink-0 items-center justify-center pb-3">
            <BrandMark className="h-9" />
          </div>
          {preview ? (
            <span
              data-preview-badge
              className="sr-only"
            >
              {t("workspace.preview.badge", { defaultValue: "界面预览 · 示例内容" })}
            </span>
          ) : null}
          <div className="flex shrink-0 flex-col gap-1">
            {[...PRIMARY_ITEMS, ...CAPABILITY_ITEMS].map(renderItem)}
          </div>
          <div className="min-h-8 flex-1" />
          <div className="flex shrink-0 flex-col items-center gap-2 pb-1">
            {renderItem(SETTINGS_ITEM)}
            {footerAction}
            <RailToggleButton
              collapsed={collapsed}
              label={
                collapsed
                  ? t("thread.header.toggleSidebar")
                  : t("sidebar.collapse")
              }
              onClick={onToggleCollapsed}
            />
          </div>

          {collapsed && connectionStatus ? (
            <div className="sr-only">
              {connectionStatus}
            </div>
          ) : null}
        </div>
        {chatNavigation ? (
          <div
            ref={chatColumnRef}
            data-testid="sidebar-chat-column"
            data-expanded={showChatColumn}
            aria-hidden={!showChatColumn || undefined}
            className="workspace-chat-column flex min-h-0 flex-col bg-background/50"
          >
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
      data-testid="sidebar-rail-toggle"
      aria-label={label}
      aria-expanded={!collapsed}
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
