import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronRight,
  Files,
  Globe,
  PanelRightClose,
  PanelRightOpen,
  RotateCw,
  SquareTerminal,
  X,
  type LucideIcon,
} from "lucide-react";

import { CodeBlock } from "@/components/CodeBlock";
import { ApiError, fetchFilePreview, fetchWorkspaceFiles } from "@/lib/api";
import type { FilePreviewPayload, SessionDirectoryPayload } from "@/lib/types";
import { WorkspaceFileTree } from "@/components/workbench/WorkspaceFileTree";
import { cn } from "@/lib/utils";

export type StartPanelSection = "files" | "terminal" | "browser";

/**
 * Imperative "open this card" request from the App keymap (⌘P / ⌘T). The
 * nonce makes repeated requests for the same section re-fire.
 */
export interface StartPanelFocus {
  section: StartPanelSection;
  nonce: number;
}

/** Pure shortcut resolver so the ⌘P/⌘T mapping stays testable outside App. */
export function resolveStartPanelShortcut(
  event: globalThis.KeyboardEvent,
  isHomeView: boolean,
): StartPanelSection | null {
  if (!isHomeView) return null;
  if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return null;
  switch (event.key.toLowerCase()) {
    case "p":
      return "files";
    case "t":
      return "browser";
    default:
      return null;
  }
}

interface CardSpec {
  id: StartPanelSection;
  icon: LucideIcon;
  titleKey: string;
  titleDefault: string;
  descKey: string;
  descDefault: string;
  shortcut: string | null;
}

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
    shortcut: null,
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

type FilesState = "idle" | "loading" | "ready" | "noWorkspace" | "error";

/**
 * The home panel has no session, but the file-preview route lives under
 * ``/api/sessions/{key}/…``. With ``scope=default`` the gateway anchors the
 * path at the default workspace and the key only needs to pass the WebUI
 * namespace format check, so a stable placeholder key is enough here.
 */
const HOME_PREVIEW_SESSION_KEY = "websocket:home";

type PreviewState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; payload: FilePreviewPayload };

function WorkspaceFilePreview({
  getToken,
  path,
  onClose,
}: {
  getToken: () => string;
  path: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [state, setState] = useState<PreviewState>({ status: "loading" });
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    fetchFilePreview(getTokenRef.current(), HOME_PREVIEW_SESSION_KEY, path, "", "default")
      .then((payload) => {
        if (!cancelled) setState({ status: "ready", payload });
      })
      .catch(() => {
        if (!cancelled) setState({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  return (
    <div
      data-testid="home-file-preview"
      aria-label={t("filePreview.aria", { defaultValue: "File preview" })}
      className="overflow-hidden rounded-md border border-border/50 bg-background/70"
    >
      <div className="flex h-8 items-center gap-2 border-b border-border/40 px-2" title={path}>
        <span
          data-testid="home-file-preview-title"
          className="min-w-0 flex-1 truncate text-[12px] font-medium text-foreground/90"
        >
          {state.status === "ready" ? state.payload.display_path : path}
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("filePreview.close", { defaultValue: "Close file preview" })}
          className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
      <div className="max-h-64 overflow-auto">
        {state.status === "loading" ? (
          <p className="py-1 text-[12px] text-muted-foreground/60">
            {t("filePreview.loading", { defaultValue: "Loading preview..." })}
          </p>
        ) : state.status === "error" ? (
          <p role="alert" className="py-1 text-[12px] text-destructive">
            {t("filePreview.failed", { defaultValue: "Could not preview this file." })}
          </p>
        ) : (
          <>
            {state.payload.truncated ? (
              <p className="mx-2 mt-2 rounded-md border border-amber-500/25 bg-amber-500/10 px-2 py-1 text-[11.5px] text-amber-700 dark:text-amber-200">
                {t("filePreview.truncated", {
                  defaultValue: "Preview is truncated because this file is large.",
                })}
              </p>
            ) : null}
            <CodeBlock
              language={state.payload.language}
              code={state.payload.content}
              chrome="none"
              highlight
              showLineNumbers={false}
              wrapLongLines={false}
            />
          </>
        )}
      </div>
    </div>
  );
}

interface HomeStartPanelProps {
  getToken: () => string;
  focus?: StartPanelFocus | null;
  /** WU-D wires the live terminal section here. */
  terminalSlot?: ReactNode;
  /** WU-E wires the embedded browser section here. */
  browserSlot?: ReactNode;
}

export function HomeStartPanel({
  getToken,
  focus = null,
  terminalSlot,
  browserSlot,
}: HomeStartPanelProps) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);
  const [expandedCard, setExpandedCard] = useState<StartPanelSection | null>(null);
  const [filesState, setFilesState] = useState<FilesState>("idle");
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;
  // Root listing from the panel-level probe; consumed by the tree's first load
  // so a 404 (no default workspace) can be told apart from a generic error.
  const rootPayloadRef = useRef<SessionDirectoryPayload | null>(null);

  const probeWorkspaceRoot = useCallback(async () => {
    setFilesState("loading");
    try {
      rootPayloadRef.current = await fetchWorkspaceFiles(getTokenRef.current(), null);
      setFilesState("ready");
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setFilesState("noWorkspace");
      else setFilesState("error");
    }
  }, []);

  useEffect(() => {
    if (!focus) return;
    setCollapsed(false);
    setExpandedCard(focus.section);
  }, [focus]);

  useEffect(() => {
    if (!collapsed && expandedCard === "files" && filesState === "idle") {
      void probeWorkspaceRoot();
    }
  }, [collapsed, expandedCard, filesState, probeWorkspaceRoot]);

  // Switching cards (or collapsing the rail) drops the inline preview so the
  // files section always reopens on the tree itself.
  useEffect(() => {
    setPreviewPath(null);
  }, [expandedCard, collapsed]);

  const fetchDirectory = useCallback(async (token: string, path: string | null) => {
    if (path === null && rootPayloadRef.current) {
      const payload = rootPayloadRef.current;
      rootPayloadRef.current = null;
      return payload;
    }
    return fetchWorkspaceFiles(token, path);
  }, []);

  const renderFilesSection = () => {
    if (filesState === "loading" || filesState === "idle") {
      return (
        <p className="py-1 text-[12px] text-muted-foreground/60">
          {t("workbench.treeLoading", { defaultValue: "Loading…" })}
        </p>
      );
    }
    if (filesState === "noWorkspace") {
      return (
        <p role="status" className="py-1 text-[12px] text-muted-foreground">
          {t("startPanel.openProjectFirst", {
            defaultValue: "Available after you pick a project",
          })}
        </p>
      );
    }
    if (filesState === "error") {
      return (
        <button
          type="button"
          onClick={() => void probeWorkspaceRoot()}
          className="flex items-center gap-1.5 py-1 text-[12px] text-muted-foreground hover:text-foreground"
        >
          <RotateCw className="h-3 w-3" aria-hidden />
          {t("startPanel.filesRetry", {
            defaultValue: "Could not load the workspace — retry",
          })}
        </button>
      );
    }
    return (
      <div className="flex flex-col gap-2">
        {previewPath ? (
          <WorkspaceFilePreview
            getToken={getToken}
            path={previewPath}
            onClose={() => setPreviewPath(null)}
          />
        ) : null}
        <WorkspaceFileTree
          getToken={getToken}
          fetchDirectory={fetchDirectory}
          onOpenFilePreview={setPreviewPath}
        />
      </div>
    );
  };

  const renderExpandedArea = (id: StartPanelSection) => {
    if (id === "terminal" && terminalSlot !== undefined) return terminalSlot;
    if (id === "browser" && browserSlot !== undefined) return browserSlot;
    if (id === "terminal") {
      return (
        <p role="status" className="py-1 text-[12px] text-muted-foreground/70">
          {t("startPanel.terminalPlaceholder", {
            defaultValue: "The command console will appear here.",
          })}
        </p>
      );
    }
    if (id === "browser") {
      return (
        <p role="status" className="py-1 text-[12px] text-muted-foreground/70">
          {t("startPanel.browserPlaceholder", {
            defaultValue: "The embedded browser will appear here.",
          })}
        </p>
      );
    }
    return renderFilesSection();
  };

  if (collapsed) {
    return (
      <aside
        data-testid="home-start-panel"
        aria-label={t("startPanel.title", { defaultValue: "Start" })}
        className="hidden w-10 shrink-0 flex-col items-center border-l border-border/60 bg-muted/20 py-2 md:flex"
      >
        <button
          type="button"
          aria-label={t("startPanel.expand", { defaultValue: "Expand the start panel" })}
          onClick={() => setCollapsed(false)}
          className="grid h-7 w-7 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground"
        >
          <PanelRightOpen className="h-4 w-4" aria-hidden />
        </button>
      </aside>
    );
  }

  return (
    <aside
      data-testid="home-start-panel"
      aria-label={t("startPanel.title", { defaultValue: "Start" })}
      className="hidden w-[22rem] shrink-0 flex-col overflow-hidden border-l border-border/60 bg-muted/20 md:flex"
    >
      <header className="flex h-9 shrink-0 items-center gap-2 border-b border-border/40 px-3">
        <span className="text-[12.5px] font-semibold text-foreground/85">
          {t("startPanel.title", { defaultValue: "Start" })}
        </span>
        <button
          type="button"
          aria-label={t("startPanel.collapse", { defaultValue: "Collapse the start panel" })}
          onClick={() => setCollapsed(true)}
          className="ml-auto grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground"
        >
          <PanelRightClose className="h-3.5 w-3.5" aria-hidden />
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3 scrollbar-thin">
        <ul className="space-y-2" role="list">
          {CARD_SPECS.map((card) => {
            const open = expandedCard === card.id;
            const Icon = card.icon;
            return (
              <li key={card.id}>
                <button
                  type="button"
                  aria-expanded={open}
                  aria-controls={`start-section-${card.id}`}
                  onClick={() => setExpandedCard(open ? null : card.id)}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-lg border px-3 py-2.5 text-left transition-colors",
                    open
                      ? "border-border/70 bg-background"
                      : "border-border/50 bg-background/60 hover:bg-background hover:border-border/70",
                  )}
                >
                  <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md border border-border/50 bg-muted/30">
                    <Icon className="h-4 w-4 text-muted-foreground" aria-hidden />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium text-foreground/90">
                      {t(card.titleKey, { defaultValue: card.titleDefault })}
                    </span>
                    <span className="block truncate text-[11.5px] text-muted-foreground">
                      {t(card.descKey, { defaultValue: card.descDefault })}
                    </span>
                  </span>
                  {card.shortcut ? (
                    <kbd className="shrink-0 rounded border border-border/60 bg-muted/40 px-1.5 py-px text-[11px] tabular-nums text-muted-foreground/80">
                      {card.shortcut}
                    </kbd>
                  ) : (
                    <ChevronRight
                      className={cn(
                        "h-3.5 w-3.5 shrink-0 text-muted-foreground/60 transition-transform",
                        open && "rotate-90",
                      )}
                      aria-hidden
                    />
                  )}
                </button>
                {open ? (
                  <div
                    id={`start-section-${card.id}`}
                    role="region"
                    aria-label={t(card.titleKey, { defaultValue: card.titleDefault })}
                    className="mt-1 rounded-lg border border-border/50 bg-background/60 px-3 py-2"
                  >
                    {renderExpandedArea(card.id)}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      </div>
    </aside>
  );
}
