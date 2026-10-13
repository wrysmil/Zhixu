import type { ReactNode, RefObject } from "react";

import type { SettingsSectionKey } from "@/components/settings/contracts";

export type WorkspaceView =
  | "home"
  | "chat"
  | "creative"
  | "article"
  | "agents"
  | "skills"
  | "automations"
  | "apps"
  | "settings";

export interface WorkspaceRoute {
  view: WorkspaceView;
  articleId?: string;
  chatKey?: string | null;
  settingsSection?: SettingsSectionKey;
  temporary?: boolean;
}

export interface WorkspaceNavigationSlotContext {
  activeActionRef: RefObject<HTMLButtonElement>;
}

export interface WorkspaceNavigationProps {
  activeView: WorkspaceView;
  collapsed: boolean;
  preview: boolean;
  onNavigate: (route: WorkspaceRoute) => void;
  onToggleCollapsed: () => void;
  /**
   * Chat-context column. The live shell passes a render function so it can
   * attach the selection-highlight ref to its "New chat" button; the preview
   * passes a plain node of fixture rows.
   */
  chatNavigation?:
    | ReactNode
    | ((ctx: WorkspaceNavigationSlotContext) => ReactNode);
  connectionStatus?: ReactNode;
  /** Marks the "New chat" affordance as the active selection. */
  newChatActive?: boolean;
  /** Highlights the matching capability item as the active page. */
  activeUtility?: "apps" | "skills" | "automations" | "agents" | null;
  hostChromeInset?: boolean;
}

export interface WorkspacePageProps {
  preview: boolean;
  onNavigate: (route: WorkspaceRoute) => void;
}
