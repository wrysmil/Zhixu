import { useCallback, useEffect, useState, Suspense, lazy } from "react";
import { useTranslation } from "react-i18next";
import { MessageSquareMore, Moon, Plus, Search, Sun } from "lucide-react";

import { WorkspaceNavigation } from "@/components/workspace/WorkspaceNavigation";
import { ChatList } from "@/components/ChatList";
import type {
  WorkspaceRoute,
  WorkspaceView,
} from "@/components/workspace/contracts";
import { parseWorkspaceHash, workspaceRouteHash } from "@/workspace/routes";
import { PreviewThread } from "@/preview/PreviewThread";
import { PreviewSettings } from "@/preview/PreviewSettings";
import {
  PreviewCapabilities,
  type CapabilityKind,
} from "@/preview/PreviewCapabilities";
import { PREVIEW_DEFAULT_SCOPE, previewCapabilityCopy, previewSessions } from "@/preview/fixtures";
import type { WebUIMutationTransport } from "@/lib/api";
import type { ChatSummary, SidebarProjectEntry, WorkspaceScopePayload } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ThemeProvider, useTheme } from "@/hooks/useTheme";

const CreativeWorkspace = lazy(() => import("@/components/creative/CreativeWorkspace"));

/**
 * 预览入口没有网关连接：创作空间在 preview 下自带内存写路径，这个替身一旦被调用
 * 就说明有真实 mutation 漏进了预览（A-T11），直接抛错而不是静默发请求。
 */
const PREVIEW_NO_TRANSPORT: WebUIMutationTransport = {
  requestMutation: async <T,>(action: string): Promise<T> => {
    throw new Error(`preview must not issue business mutation: ${action}`);
  },
};

/**
 * Fully offline preview shell mounted only when the entry query carries
 * `?preview=1`. It owns no runtime, WebSocket or business fetch: navigation is
 * pure hash state, the chat column and every non-home page render fixture
 * copy, and an explicit "interface preview" badge is always visible.
 */
export default function PreviewApp() {
  const { t, i18n } = useTranslation();
  const appearance = useTheme({ persist: false });
  const darkPreview = appearance.theme === "dark";
  const themeLabel = i18n.resolvedLanguage?.startsWith("zh")
    ? darkPreview ? "切换为浅色主题" : "切换为深色主题"
    : darkPreview ? "Switch to light theme" : "Switch to dark theme";
  const [route, setRoute] = useState<WorkspaceRoute>(() =>
    parseWorkspaceHash(window.location.hash),
  );
  const narrowQuery = "(max-width: 767px)";
  const [collapsed, setCollapsed] = useState(() => window.matchMedia(narrowQuery).matches);
  const [narrow, setNarrow] = useState(() => window.matchMedia(narrowQuery).matches);
  const [workspaceScope, setWorkspaceScope] = useState(PREVIEW_DEFAULT_SCOPE);
  const [projects, setProjects] = useState<SidebarProjectEntry[]>([]);

  useEffect(() => {
    const media = window.matchMedia(narrowQuery);
    const sync = () => setNarrow(media.matches);
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    if (narrow) setCollapsed(true);
  }, [narrow]);

  const selectWorkspace = useCallback((scope: WorkspaceScopePayload) => {
    setWorkspaceScope(scope);
    if (scope.project_path !== PREVIEW_DEFAULT_SCOPE.project_path) {
      setProjects((current) => current.some((project) => project.path === scope.project_path)
        ? current
        : [...current, { key: scope.project_path, path: scope.project_path, name: scope.project_name || scope.project_path.split(/[\\/]/).at(-1) || scope.project_path }]);
    }
  }, []);

  useEffect(() => {
    const sync = () => setRoute(parseWorkspaceHash(window.location.hash));
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  const navigate = useCallback((next: WorkspaceRoute) => {
    const hash = workspaceRouteHash(next);
    setRoute(next);
    if (narrow) setCollapsed(true);
    // Preserve the `?preview=1` query by assigning only the hash fragment.
    if (window.location.hash !== hash) {
      window.location.hash = hash;
    }
  }, []);

  return (
    <ThemeProvider theme={appearance.theme} appearance={appearance}>
    <div className="preview-app-shell relative flex h-[100dvh] w-full overflow-hidden bg-background text-foreground">
      <div
        className={cn(
          "shrink-0 border-r border-sidebar-border",
          route.view === "chat" && !collapsed ? "w-[274px]" : "w-rail",
          narrow && route.view === "chat" && !collapsed &&
            "absolute inset-y-0 left-0 z-30 shadow-[0_0_0_100vmax_rgb(0_0_0/0.28)]",
        )}
      >
        <WorkspaceNavigation
          activeView={route.view}
          activeSettingsSection={route.settingsSection}
          collapsed={collapsed}
          preview
          onNavigate={navigate}
          onToggleCollapsed={() => setCollapsed((value) => !value)}
          footerAction={
            <button type="button" aria-label={themeLabel} title={themeLabel}
              onClick={appearance.toggle}
              className="host-no-drag flex h-9 w-9 items-center justify-center rounded-full border border-border bg-card text-muted-foreground shadow-sm transition-colors duration-150 hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {darkPreview ? <Sun size={16} aria-hidden /> : <Moon size={16} aria-hidden />}
            </button>
          }
          connectionStatus={
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <span
                aria-hidden
                className="h-2 w-2 rounded-full bg-amber-500"
              />
              {t("workspace.preview.badge", {
                defaultValue: "Interface preview · sample content",
              })}
            </span>
          }
          chatNavigation={<PreviewChatColumn activeKey={route.chatKey} onNavigate={navigate}
            projects={projects} onSelectWorkspace={selectWorkspace} />}
        />
      </div>

      {narrow && route.view === "chat" && !collapsed ? (
        <button
          type="button"
          aria-label={t("sidebar.close", { defaultValue: "Close sidebar" })}
          onClick={() => setCollapsed(true)}
          className="absolute inset-0 z-20 cursor-default"
        />
      ) : null}

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {route.view === "chat" ? (
          <PreviewThread key={route.chatKey ?? "new"} chatKey={route.chatKey}
            workspaceScope={workspaceScope} onWorkspaceScopeChange={selectWorkspace}
            onManageModels={() => navigate({ view: "settings", settingsSection: "models" })} />
        ) : route.view === "settings" ? (
          <PreviewSettings key={route.settingsSection ?? "overview"} initialSection={
            route.settingsSection === "apps" || route.settingsSection === "agents"
              || route.settingsSection === "skills" || route.settingsSection === "automations" || route.settingsSection === "teams"
              ? "overview" : route.settingsSection
          } onSectionChange={(settingsSection) => navigate({ view: "settings", settingsSection })} />
        ) : route.view === "apps" ||
          route.view === "agents" ||
          route.view === "skills" ||
          route.view === "automations" ? (
          <PreviewCapabilities kind={route.view as CapabilityKind} />
        ) : route.view === "creative" || route.view === "article" ? (
          <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="flex h-14 items-center justify-end border-b border-border px-6">
            <span className="text-[11px] text-muted-foreground">{t("workspace.preview.badge")}</span>
          </div>
          <Suspense fallback={null}>
            <CreativeWorkspace
              preview
              getToken={() => ""}
              transport={PREVIEW_NO_TRANSPORT}
              workspacePath={workspaceScope.project_path}
            />
          </Suspense>
          </div>
        ) : (
          <PlaceholderPage view={route.view} />
        )}
      </main>
    </div>
    </ThemeProvider>
  );
}

function PreviewChatColumn({ activeKey, onNavigate, projects, onSelectWorkspace }: {
  activeKey?: string | null;
  onNavigate: (route: WorkspaceRoute) => void;
  projects: SidebarProjectEntry[];
  onSelectWorkspace: (scope: WorkspaceScopePayload) => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [pinnedKeys, setPinnedKeys] = useState<string[]>([]);
  const [archivedKeys, setArchivedKeys] = useState<string[]>([]);
  const [titleOverrides, setTitleOverrides] = useState<Record<string, string>>({});
  const [sessions, setSessions] = useState<ChatSummary[]>(() => previewSessions.map((row) => ({
    ...row, channel: "websocket", chatId: row.key, createdAt: null, updatedAt: null,
  })));
  const toggleKey = (key: string, setter: (update: (current: string[]) => string[]) => void) =>
    setter((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key]);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-1 px-3 pt-3 pb-1">
        <span className="min-w-0 flex-1 truncate text-[15px] font-semibold text-sidebar-foreground">
          {t("sidebar.recent")}
        </span>
        <button type="button" onClick={() => onNavigate({ view: "chat", chatKey: null })}
          aria-label={t("sidebar.newChat")} title={t("sidebar.newChat")}
          className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-control text-muted-foreground hover:bg-muted">
          <Plus className="h-4 w-4" aria-hidden />
        </button>
      </div>
      <div className="relative mx-3 my-3">
        <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" aria-hidden />
        <input value={query} onChange={(event) => setQuery(event.target.value)} aria-label={t("sidebar.searchAria")}
          placeholder={t("sidebar.searchPlaceholder")} className="h-9 w-full rounded-control border border-border/70 bg-card pl-8 pr-3 text-xs" />
      </div>
      <div className="flex min-h-0 flex-1 flex-col">
        <ChatList sessions={sessions.filter((row) => (titleOverrides[row.key] || row.title || "").includes(query))}
          activeKey={activeKey ?? null} projectEntries={projects}
          defaultWorkspacePath={PREVIEW_DEFAULT_SCOPE.project_path}
          onSelect={(key) => {
            const scope = sessions.find((session) => session.key === key)?.workspaceScope;
            onSelectWorkspace(scope ?? PREVIEW_DEFAULT_SCOPE);
            onNavigate({ view: "chat", chatKey: key });
          }}
          onNewChatInProject={(path, name) => {
            const scope = { ...PREVIEW_DEFAULT_SCOPE, project_path: path, project_name: name };
            onSelectWorkspace(scope);
            onNavigate({ view: "chat", chatKey: null });
          }}
          onRequestDelete={(key) => setSessions((current) => current.filter((row) => row.key !== key))}
          onTogglePin={(key) => toggleKey(key, setPinnedKeys)}
          onToggleArchive={(key) => toggleKey(key, setArchivedKeys)}
          onRequestRename={(key, label) => setTitleOverrides((current) => ({ ...current, [key]: label }))}
          pinnedKeys={pinnedKeys} archivedKeys={archivedKeys} titleOverrides={titleOverrides}
          emptyLabel={t("sidebar.noSearchResults")} />
      </div>
      <p className="border-t border-border px-4 py-3 text-[11px] text-muted-foreground">{t("workspace.preview.badge")}</p>
    </div>
  );
}

function PlaceholderPage({ view }: { view: WorkspaceView }) {
  const { t } = useTranslation();
  const copyKey =
    view === "chat"
      ? "chat"
      : view === "creative" || view === "article"
        ? "creative"
        : view === "agents" || view === "skills" || view === "automations" || view === "apps"
          ? view
          : "settings";
  const copy = previewCapabilityCopy[copyKey];
  return (
    <div className="flex h-full w-full items-center justify-center overflow-y-auto bg-background px-6 py-10">
      <div className="flex w-full max-w-[560px] flex-col items-center gap-3 rounded-prominent border border-border/70 bg-settings-surface p-8 text-center shadow-sm">
        <span className="flex h-11 w-11 items-center justify-center rounded-mark bg-primary/10 text-primary">
          <MessageSquareMore className="h-5 w-5" aria-hidden />
        </span>
        <h2 className="text-[17px] font-semibold text-foreground">{copy.title}</h2>
        <p className="text-sm leading-relaxed text-muted-foreground">{copy.body}</p>
        <p className="mt-1 rounded-mark bg-amber-500/15 px-2.5 py-1 text-xs text-amber-700 dark:text-amber-300">
          {t("workspace.preview.badge", {
            defaultValue: "Interface preview · sample content",
          })}
        </p>
      </div>
    </div>
  );
}
