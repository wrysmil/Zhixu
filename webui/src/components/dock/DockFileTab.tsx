// dock 的「文件」标签页（批次 home-dock-pty-terminal-v1.1 Task 9）。
// 由 v1 HomeStartPanel 的工作区文件状态机（probe / noWorkspace / error / retry）
// 与 WorkspaceFilePreview 整体迁移而来，占满 dock 全高。
// 双锚点：sessionKey 非空 = session 锚点（WorkspaceFileTree 默认 fetchSessionDirectory，
// 预览走 fetchFilePreview(token, sessionKey, path)）；为空 = home/工作区锚点
// （fetchWorkspaceFiles + HOME_PREVIEW_SESSION_KEY + scope "default"，与 v1 逐一对应）。
// onOpenFilePreview 传入时预览上抛宿主（SessionWorkbench 保留外置预览面板语义）。
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Check,
  ChevronsDownUp,
  Eye,
  EyeOff,
  Loader2,
  RefreshCw,
  RotateCw,
  Search,
  Undo2,
  X,
} from "lucide-react";

import { CodeBlock } from "@/components/CodeBlock";
import { ResizeHandle } from "@/components/workbench/ResizeHandle";
import { DockFileSearch } from "@/components/dock/DockFileSearch";
import { WorkspaceFileTree } from "@/components/workbench/WorkspaceFileTree";
import { useFileSaver } from "@/hooks/useFileSaver";
import { ApiError, fetchFilePreview, fetchWorkspaceFiles } from "@/lib/api";
import type { FilePreviewPayload } from "@/lib/types";
import { useOptionalClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";

/**
 * The home dock has no session, but the file-preview route lives under
 * ``/api/sessions/{key}/…``. With ``scope=default`` the gateway anchors the
 * path at the default workspace and the key only needs to pass the WebUI
 * namespace format check, so a stable placeholder key is enough here.
 */
const HOME_PREVIEW_SESSION_KEY = "websocket:home";

type FilesState = "idle" | "loading" | "ready" | "noWorkspace" | "error";

type PreviewState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; payload: FilePreviewPayload };

function DockFilePreview({
  getToken,
  sessionKey,
  path,
  onClose,
}: {
  getToken: () => string;
  sessionKey: string | null;
  path: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [state, setState] = useState<PreviewState>({ status: "loading" });
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;
  const clientContext = useOptionalClient();
  const previewKey = sessionKey ?? HOME_PREVIEW_SESSION_KEY;
  const previewScope = sessionKey ? null : "default";
  const saver = useFileSaver({
    transport: clientContext?.client ?? null,
    sessionKey: previewKey,
    scope: previewScope,
    payload: state.status === "ready" ? state.payload : null,
    onSaved: (next) => setState({ status: "ready", payload: next }),
  });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    const key = sessionKey ?? HOME_PREVIEW_SESSION_KEY;
    fetchFilePreview(getTokenRef.current(), key, path, "", sessionKey ? null : "default")
      .then((payload) => {
        if (!cancelled) setState({ status: "ready", payload });
      })
      .catch(() => {
        if (!cancelled) setState({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [path, sessionKey]);

  return (
    <div
      data-testid="dock-file-preview"
      aria-label={t("filePreview.aria", { defaultValue: "File preview" })}
      className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-border/50 bg-background/70"
    >
      <div className="flex h-8 shrink-0 items-center gap-1 border-b border-border/40 px-2" title={path}>
        <span
          data-testid="dock-file-preview-title"
          className="min-w-0 flex-1 truncate text-[12px] font-medium text-foreground/90"
        >
          {state.status === "ready" ? state.payload.display_path : path}
        </span>
        {saver.editable ? (
          <>
            {saver.dirty ? (
              <button
                type="button"
                data-testid="dock-file-preview-revert"
                onClick={saver.revert}
                aria-label={t("filePreview.cancel", { defaultValue: "Cancel editing" })}
                title={t("filePreview.cancel", { defaultValue: "Cancel editing" })}
                className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground"
              >
                <Undo2 className="h-3.5 w-3.5" aria-hidden />
              </button>
            ) : null}
            <button
              type="button"
              data-testid="dock-file-preview-save"
              onClick={() => void saver.save()}
              disabled={saver.saving || !saver.dirty}
              aria-label={t("filePreview.save", { defaultValue: "Save file" })}
              title={`${t("filePreview.save", { defaultValue: "Save file" })} (Ctrl+S)`}
              className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground disabled:opacity-35"
            >
              {saver.saving ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <Check className="h-3.5 w-3.5" aria-hidden />
              )}
            </button>
          </>
        ) : null}
        <button
          type="button"
          onClick={onClose}
          aria-label={t("filePreview.close", { defaultValue: "Close file preview" })}
          className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
      {saver.error ? (
        <p role="alert" className="shrink-0 border-b border-destructive/15 bg-destructive/5 px-2 py-1 text-[11.5px] text-destructive">
          {t("filePreview.saveFailed", { defaultValue: "Could not save this file." })}
        </p>
      ) : saver.saved ? (
        <p role="status" className="shrink-0 border-b border-border/40 bg-muted/30 px-2 py-1 text-[11.5px] text-muted-foreground">
          {t("filePreview.saved", { defaultValue: "Saved" })}
        </p>
      ) : null}
      <div className="flex min-h-0 flex-1 flex-col">
        {state.status === "loading" ? (
          <p className="py-1 text-[12px] text-muted-foreground/60">
            {t("filePreview.loading", { defaultValue: "Loading preview..." })}
          </p>
        ) : state.status === "error" ? (
          <p role="alert" className="py-1 text-[12px] text-destructive">
            {t("filePreview.failed", { defaultValue: "Could not preview this file." })}
          </p>
        ) : saver.editable ? (
          <textarea
            data-testid="dock-file-preview-editor"
            aria-label={t("filePreview.edit", { defaultValue: "Edit file" })}
            value={saver.draft}
            onChange={(event) => saver.setDraft(event.target.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
                event.preventDefault();
                void saver.save();
              }
            }}
            spellCheck={false}
            className="min-h-0 w-full flex-1 resize-none border-0 bg-background px-3 py-2 font-mono text-[12.5px] leading-[1.65] text-foreground outline-none"
          />
        ) : (
          <div className="min-h-0 flex-1 overflow-auto">
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
              wrapLongLines
            />
          </div>
        )}
      </div>
    </div>
  );
}

interface DockFileTabProps {
  /** dock scope（`websocket:home` 或 `websocket:<chat_id>`），仅作宿主标识。 */
  scope: string;
  sessionKey: string | null;
  getToken: () => string;
  /** 传入则预览上抛宿主（SessionWorkbench 外置预览面板语义）。 */
  onOpenFilePreview?: (path: string) => void;
}

export function DockFileTab({
  scope,
  sessionKey,
  getToken,
  onOpenFilePreview,
}: DockFileTabProps) {
  const { t } = useTranslation();
  const [filesState, setFilesState] = useState<FilesState>("idle");
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const [collapseToken, setCollapseToken] = useState(0);
  const [searchOpen, setSearchOpen] = useState(false);
  const [treePercent, setTreePercent] = useState(46);
  const columnsRef = useRef<HTMLDivElement>(null);
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  const homeAnchored = sessionKey === null;

  const probeWorkspaceRoot = useCallback(async () => {
    setFilesState("loading");
    try {
      await fetchWorkspaceFiles(getTokenRef.current(), null, "", false);
      setFilesState("ready");
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setFilesState("noWorkspace");
      else setFilesState("error");
    }
  }, []);

  useEffect(() => {
    if (homeAnchored && filesState === "idle") {
      void probeWorkspaceRoot();
    }
  }, [homeAnchored, filesState, probeWorkspaceRoot]);

  const fetchDirectory = useCallback(
    (token: string, path: string | null, hidden: boolean) =>
      fetchWorkspaceFiles(token, path, "", hidden),
    [],
  );

  const openPreview = onOpenFilePreview ?? setPreviewPath;
  // 宿主自带外置预览面板时（会话工作台）不内嵌预览列，文件树占满整列。
  const inlinePreview = !onOpenFilePreview;

  const toolButton = (active = false) =>
    cn(
      "grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground",
      active && "bg-muted/70 text-foreground",
    );

  const toolbar = (
    <div
      data-testid="dock-file-toolbar"
      className="flex shrink-0 items-center gap-0.5"
    >
      <button
        type="button"
        data-testid="dock-file-refresh"
        aria-label={t("dock.refresh", { defaultValue: "Refresh" })}
        title={t("dock.refresh", { defaultValue: "Refresh" })}
        onClick={() => setRefreshToken((value) => value + 1)}
        className={toolButton()}
      >
        <RefreshCw className="h-3.5 w-3.5" aria-hidden />
      </button>
      <button
        type="button"
        data-testid="dock-file-hidden-toggle"
        aria-pressed={showHidden}
        aria-label={
          showHidden
            ? t("dock.hideHidden", { defaultValue: "Hide hidden files" })
            : t("dock.showHidden", { defaultValue: "Show hidden files" })
        }
        title={
          showHidden
            ? t("dock.hideHidden", { defaultValue: "Hide hidden files" })
            : t("dock.showHidden", { defaultValue: "Show hidden files" })
        }
        onClick={() => setShowHidden((value) => !value)}
        className={toolButton(showHidden)}
      >
        {showHidden ? (
          <EyeOff className="h-3.5 w-3.5" aria-hidden />
        ) : (
          <Eye className="h-3.5 w-3.5" aria-hidden />
        )}
      </button>
      <button
        type="button"
        data-testid="dock-file-collapse"
        aria-label={t("dock.collapseAll", { defaultValue: "Collapse all" })}
        title={t("dock.collapseAll", { defaultValue: "Collapse all" })}
        onClick={() => setCollapseToken((value) => value + 1)}
        className={toolButton()}
      >
        <ChevronsDownUp className="h-3.5 w-3.5" aria-hidden />
      </button>
      <button
        type="button"
        data-testid="dock-file-search-toggle"
        aria-pressed={searchOpen}
        aria-label={t("dock.search", { defaultValue: "Search files" })}
        title={t("dock.search", { defaultValue: "Search files" })}
        onClick={() => setSearchOpen((value) => !value)}
        className={toolButton(searchOpen)}
      >
        <Search className="h-3.5 w-3.5" aria-hidden />
      </button>
    </div>
  );

  const renderTree = () => (
    <div ref={columnsRef} className="flex min-h-0 min-w-0 flex-1">
      {inlinePreview ? (
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {previewPath ? (
            <DockFilePreview
              getToken={getToken}
              sessionKey={sessionKey}
              path={previewPath}
              onClose={() => setPreviewPath(null)}
            />
          ) : (
            <p
              data-testid="dock-file-preview-empty"
              className="px-1 py-1 text-[12px] text-muted-foreground/60"
            >
              {t("filePreview.pickFile", {
                defaultValue: "Pick a file to preview it here.",
              })}
            </p>
          )}
        </div>
      ) : null}
      {inlinePreview ? <ResizeHandle
        label={t("filePreview.resizeTree", { defaultValue: "Resize file tree" })}
        value={treePercent} min={20} max={65} step={5}
        valueAtPointer={(x) => {
          const bounds = columnsRef.current?.getBoundingClientRect();
          return bounds?.width ? (x - bounds.left) / bounds.width * 100 : treePercent;
        }}
        onChange={setTreePercent}
      /> : null}
      <div
        data-testid="dock-file-tree-column"
        style={inlinePreview ? { width: `${treePercent}%` } : undefined}
        className={
          inlinePreview
            ? "flex min-h-0 min-w-0 shrink-0 flex-col gap-2"
            : "flex min-h-0 flex-1 flex-col gap-2"
        }
      >
        {homeAnchored ? (
          <WorkspaceFileTree
            getToken={getToken}
            fetchDirectory={fetchDirectory}
            showHidden={showHidden}
            refreshToken={refreshToken}
            collapseToken={collapseToken}
            onOpenFilePreview={openPreview}
          />
        ) : (
          <WorkspaceFileTree
            sessionKey={sessionKey ?? ""}
            getToken={getToken}
            showHidden={showHidden}
            refreshToken={refreshToken}
            collapseToken={collapseToken}
            onOpenFilePreview={openPreview}
          />
        )}
      </div>
    </div>
  );

  const content = () => {
    if (!homeAnchored) return renderTree();
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
    return renderTree();
  };

  return (
    <div
      data-scope={scope}
      className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto px-3 py-2 scrollbar-thin"
    >
      {toolbar}
      {searchOpen ? (
        <DockFileSearch
          sessionKey={sessionKey}
          getToken={getToken}
          showHidden={showHidden}
          onOpen={openPreview}
          onClose={() => setSearchOpen(false)}
        />
      ) : null}
      {content()}
    </div>
  );
}
