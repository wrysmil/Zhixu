import {
  type ReactNode,
  useCallback,
  useRef,
} from "react";
import {
  Bot,
  CalendarClock,
  Feather,
  MessagesSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plug,
  Settings,
  Sparkles,
} from "lucide-react";
import { useTranslation } from "react-i18next";

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
import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/workspace/BrandMark";
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

/**
 * Global 知序 icon navigation. It owns the sidebar landmark, the selection
 * highlight, the nav entries and the collapse control. The chat-context column
 * and the connection status are injected by the caller through slots so the
 * live shell keeps owning ChatList callbacks while the preview injects
 * fixtures. Non-assistant pages render only the nav column.
 */
export function WorkspaceNavigation({
  activeView,
  collapsed,
  preview,
  onNavigate,
  onToggleCollapsed,
  chatNavigation,
  connectionStatus,
  newChatActive = false,
  activeUtility = null,
  hostChromeInset = false,
}: WorkspaceNavigationProps) {
  const { t } = useTranslation();
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
    const active =
      activeView === item.view ||
      (item.view === "creative" && activeView === "article");
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
          "host-no-drag flex h-10 w-10 cursor-pointer items-center justify-center rounded-xl transition-colors duration-150",
          SIDEBAR_SELECTION_ACTION_ITEM_CLASS,
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          active
            ? "bg-accent font-medium text-sidebar-foreground"
            : "text-sidebar-foreground/85 hover:bg-sidebar-foreground/[0.05] hover:text-sidebar-foreground dark:hover:bg-white/[0.06]",
        )}
      >
        <Icon className={cn("h-[19px] w-[19px] shrink-0", active && "text-accent-foreground")} strokeWidth={1.65} aria-hidden />
        <span className="sr-only">{label}</span>
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
            "workspace-navigation-rail flex w-rail min-h-0 shrink-0 flex-col items-center gap-2 overflow-y-auto overscroll-contain border-r border-sidebar-border pb-3",
            topInset,
          )}
        >
          <div
            className={cn(
              "flex shrink-0 items-center justify-center pb-4",
            )}
          >
            <BrandMark className="h-10 w-10" />
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
          <div className="flex shrink-0 flex-col gap-2">{PRIMARY_ITEMS.map(renderItem)}</div>
          <div className="my-1 h-px w-7 bg-sidebar-border" />
          <div className="flex shrink-0 flex-col gap-2">
            {CAPABILITY_ITEMS.map(renderItem)}
          </div>
          <div className="min-h-8 flex-1" />
          <div className="flex shrink-0 flex-col items-center gap-2">
            {renderItem(SETTINGS_ITEM)}
          </div>
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
