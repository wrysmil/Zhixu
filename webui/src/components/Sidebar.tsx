import { useState } from "react";
import {
  Archive,
  Search,
  SquarePen,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  ChatList,
  type SidebarDeleteItem,
  type SidebarPaneGroup,
} from "@/components/ChatList";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import {
  SIDEBAR_SELECTION_ACTION_ITEM_CLASS,
} from "@/components/SidebarSelectionHighlight";
import { WorkspaceNavigation } from "@/components/workspace/WorkspaceNavigation";
import { BrandMark } from "@/components/workspace/BrandMark";
import type { WorkspaceView } from "@/components/workspace/contracts";
import type { SettingsSectionKey } from "@/components/settings/contracts";
import { Button } from "@/components/ui/button";
import type {
  ChatSummary,
  SidebarProjectEntry,
  SidebarViewState,
} from "@/lib/types";
import { cn } from "@/lib/utils";

interface SidebarProps {
  sessions: ChatSummary[];
  temporarySessions?: ChatSummary[];
  activeKey: string | null;
  loading: boolean;
  newChatActive: boolean;
  onNewChat: () => void;
  onSelect: (key: string) => void;
  onCloseTemporaryChat?: (key: string) => void;
  onRequestDelete: (key: string, label: string) => void;
  onRequestDeleteMany?: (items: SidebarDeleteItem[]) => void;
  onTogglePin: (key: string) => void;
  onRequestRename: (key: string, label: string) => void;
  onRequestRenameTab?: (key: string, label: string) => void;
  onToggleArchive: (key: string) => void;
  paneGroups?: Record<string, SidebarPaneGroup>;
  onSelectPane?: (tabKey: string, paneKey: string) => void;
  onCreateTab?: (paneKey: string) => void;
  onDetachPane?: (tabKey: string, paneKey: string) => void;
  onDissolveTab?: (tabKey: string) => void;
  onAttachPane?: (paneKey: string, tabKey: string) => void;
  onToggleGroup: (groupId: string) => void;
  onRequestRenameProject: (projectKey: string, label: string) => void;
  onNewChatInProject: (projectPath: string, projectName: string) => void;
  onTogglePinProject?: (projectKey: string) => void;
  onRemoveProject?: (projectKey: string, label: string) => void;
  activeView: WorkspaceView;
  activeSettingsSection?: SettingsSectionKey;
  onOpenHome: () => void;
  onOpenAssistant: () => void;
  onOpenCreative: () => void;
  onOpenSettings: (section?: SettingsSectionKey) => void;
  onOpenApps: () => void;
  onOpenSkills: () => void;
  onOpenAutomations: () => void;
  onOpenAgents: () => void;
  onOpenTeams: () => void;
  onOpenMemory: () => void;
  onOpenIdentity: () => void;
  onOpenSearch: () => void;
  onSettingsIntent?: () => void;
  activeUtility?: "apps" | "skills" | "automations" | "agents" | null;
  onToggleArchived: () => void;
  onCollapse: () => void;
  onExpand?: () => void;
  containActionMenus?: boolean;
  collapsed?: boolean;
  pinnedKeys?: string[];
  archivedKeys?: string[];
  pinnedPaneKeys?: string[];
  archivedPaneKeys?: string[];
  sessionOrder?: string[];
  titleOverrides?: Record<string, string>;
  projectNameOverrides?: Record<string, string>;
  pinnedProjectKeys?: string[];
  hiddenProjectKeys?: string[];
  projectEntries?: SidebarProjectEntry[];
  collapsedGroups?: Record<string, boolean>;
  runningChatIds?: string[];
  updatedChatIds?: string[];
  recoveryChatIds?: string[];
  viewState?: SidebarViewState;
  showArchived?: boolean;
  archivedCount?: number;
  defaultWorkspacePath?: string | null;
  hostChromeInset?: boolean;
}

type NavigatorWithUserAgentData = Navigator & {
  userAgentData?: { platform?: string };
};

function isApplePlatform(): boolean {
  if (typeof navigator === "undefined") return false;
  const platform = navigator.platform || "";
  const userAgentPlatform =
    (navigator as NavigatorWithUserAgentData).userAgentData?.platform || "";
  return /mac|iphone|ipad|ipod/i.test(`${platform} ${userAgentPlatform}`);
}

function newChatShortcutLabel(): string {
  return isApplePlatform() ? "⌘⇧O" : "Ctrl+Shift+O";
}

export function Sidebar(props: SidebarProps) {
  const { t } = useTranslation();
  const [menuPortalContainer, setMenuPortalContainer] =
    useState<HTMLElement | null>(null);
  const newChatShortcut = newChatShortcutLabel();

  const activeView = props.activeView;

  return (
    <WorkspaceNavigation
      activeView={activeView}
      activeSettingsSection={props.activeSettingsSection}
      collapsed={Boolean(props.collapsed)}
      preview={false}
      newChatActive={props.newChatActive}
      activeUtility={props.activeUtility ?? null}
      hostChromeInset={props.hostChromeInset}
      onToggleCollapsed={() => {
        if (props.collapsed) props.onExpand?.();
        else props.onCollapse();
      }}
      onNavigate={(route) => {
        switch (route.view) {
          case "home":
            props.onOpenHome();
            break;
          case "chat":
            props.onOpenAssistant();
            break;
          case "creative":
            props.onOpenCreative();
            break;
          case "apps":
            props.onOpenApps();
            break;
          case "skills":
            props.onOpenSkills();
            break;
          case "automations":
            props.onOpenAutomations();
            break;
          case "agents":
            props.onOpenAgents();
            break;
          case "teams":
            props.onOpenTeams();
            break;
          case "settings":
            props.onOpenSettings(route.settingsSection);
            break;
          case "memory":
            props.onOpenMemory();
            break;
          case "identity":
            props.onOpenIdentity();
            break;
          default:
            break;
        }
      }}
      connectionStatus={
        <div className="flex items-center gap-1 text-xs">
          <ConnectionBadge />
        </div>
      }
      chatNavigation={({ activeActionRef }) => (
        <div
          ref={props.containActionMenus ? setMenuPortalContainer : undefined}
          className="flex min-h-0 min-w-0 flex-1 flex-col"
        >
          <div
            data-testid="sidebar-brand-row"
            className={cn(
              "flex items-center gap-2 px-3 pb-1",
              props.hostChromeInset ? "pt-8" : "pt-3",
            )}
          >
            <BrandMark className="shrink-0 text-[18px]" />
          </div>
          <div className="space-y-0.5 px-2 pb-2 pt-1">
            <SidebarActionButton
              label={t("sidebar.newChat")}
              onClick={props.onNewChat}
              active={props.newChatActive}
              selectionRef={activeActionRef}
              icon={<SquarePen className="h-4 w-4" />}
              shortcut={newChatShortcut}
              ariaKeyShortcuts="Meta+Shift+O Control+Shift+O"
            />
            <SidebarActionButton
              label={t("sidebar.searchAria")}
              onClick={props.onOpenSearch}
              icon={<Search className="h-4 w-4" />}
            />
            {props.archivedCount ? (
              <SidebarActionButton
                label={props.showArchived ? t("chat.hideArchived") : t("chat.showArchived")}
                onClick={props.onToggleArchived}
                icon={<Archive className="h-4 w-4" />}
              />
            ) : null}
          </div>
          <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            <ChatList
              sessions={props.sessions}
              temporarySessions={props.temporarySessions}
              activeKey={props.activeKey}
              loading={props.loading}
              emptyLabel={t("chat.noSessions")}
              onSelect={props.onSelect}
              onCloseTemporaryChat={props.onCloseTemporaryChat}
              onRequestDelete={props.onRequestDelete}
              onRequestDeleteMany={props.onRequestDeleteMany}
              onTogglePin={props.onTogglePin}
              onRequestRename={props.onRequestRename}
              onRequestRenameTab={props.onRequestRenameTab}
              onToggleArchive={props.onToggleArchive}
              paneGroups={props.paneGroups}
              onSelectPane={props.onSelectPane}
              onCreateTab={props.onCreateTab}
              onDetachPane={props.onDetachPane}
              onDissolveTab={props.onDissolveTab}
              onAttachPane={props.onAttachPane}
              onToggleGroup={props.onToggleGroup}
              onRequestRenameProject={props.onRequestRenameProject}
              onNewChatInProject={props.onNewChatInProject}
              onTogglePinProject={props.onTogglePinProject}
              onRemoveProject={props.onRemoveProject}
              pinnedKeys={props.pinnedKeys}
              archivedKeys={props.archivedKeys}
              pinnedPaneKeys={props.pinnedPaneKeys}
              archivedPaneKeys={props.archivedPaneKeys}
              sessionOrder={props.sessionOrder}
              titleOverrides={props.titleOverrides}
              projectNameOverrides={props.projectNameOverrides}
              pinnedProjectKeys={props.pinnedProjectKeys}
              hiddenProjectKeys={props.hiddenProjectKeys}
              projectEntries={props.projectEntries}
              collapsedGroups={props.collapsedGroups}
              runningChatIds={props.runningChatIds}
              updatedChatIds={props.updatedChatIds}
              recoveryChatIds={props.recoveryChatIds}
              density={props.viewState?.density}
              showPreviews={props.viewState?.show_previews}
              showTimestamps={props.viewState?.show_timestamps}
              sort={props.viewState?.sort}
              showArchived={props.showArchived}
              defaultWorkspacePath={props.defaultWorkspacePath}
              actionMenuPortalContainer={
                props.containActionMenus ? menuPortalContainer : undefined
              }
            />
          </div>
        </div>
      )}
    />
  );
}

function SidebarActionButton({
  label,
  icon,
  onClick,
  active = false,
  className,
  shortcut,
  ariaKeyShortcuts,
  selectionRef,
}: {
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  active?: boolean;
  className?: string;
  shortcut?: string;
  ariaKeyShortcuts?: string;
  selectionRef?: React.RefObject<HTMLButtonElement>;
}) {
  const title = shortcut ? `${label} (${shortcut})` : undefined;

  return (
    <Button
      ref={active ? selectionRef : undefined}
      type="button"
      variant={null}
      aria-label={label}
      aria-current={active ? "page" : undefined}
      aria-keyshortcuts={ariaKeyShortcuts}
      title={title}
      onClick={() => onClick()}
      className={cn(
        "touch-target group h-8 w-full min-w-0 justify-start gap-2 overflow-hidden rounded-xl px-3 text-left font-medium text-[13px]",
        SIDEBAR_SELECTION_ACTION_ITEM_CLASS,
        active
          ? "text-sidebar-accent-foreground"
          : "text-sidebar-foreground/85 hover:bg-sidebar-foreground/[0.035] hover:text-sidebar-foreground dark:hover:bg-white/[0.05]",
        className,
      )}
    >
      <span className="flex shrink-0 items-center justify-center" aria-hidden>
        {icon}
      </span>
      <span className="min-w-0 flex-1 truncate whitespace-nowrap">
        {label}
      </span>
    </Button>
  );
}
