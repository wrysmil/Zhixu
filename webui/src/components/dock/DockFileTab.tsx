// dock 的「文件」标签页（批次 home-dock-pty-terminal-v1.1 Task 9）。
// 由 v1 HomeStartPanel 的工作区文件状态机（probe / noWorkspace / error / retry +
// root 缓存）与 WorkspaceFilePreview 整体迁移而来，占满 dock 全高。
// 双锚点：sessionKey 非空 = session 锚点（WorkspaceFileTree 默认 fetchSessionDirectory，
// 预览走 fetchFilePreview(token, sessionKey, path)）；为空 = home/工作区锚点
// （fetchWorkspaceFiles + HOME_PREVIEW_SESSION_KEY + scope "default"，与 v1 逐一对应）。
// onOpenFilePreview 传入时预览上抛宿主（SessionWorkbench 保留外置预览面板语义）。
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { RotateCw, X } from "lucide-react";

import { CodeBlock } from "@/components/CodeBlock";
import { WorkspaceFileTree } from "@/components/workbench/WorkspaceFileTree";
import { ApiError, fetchFilePreview, fetchWorkspaceFiles } from "@/lib/api";
import type { FilePreviewPayload, SessionDirectoryPayload } from "@/lib/types";

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
      <div className="flex h-8 shrink-0 items-center gap-2 border-b border-border/40 px-2" title={path}>
        <span
          data-testid="dock-file-preview-title"
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
      <div className="min-h-0 flex-1 overflow-auto">
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
              wrapLongLines
            />
          </>
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
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;
  // Root listing from the tab-level probe; consumed by the tree's first load
  // so a 404 (no default workspace) can be told apart from a generic error.
  const rootPayloadRef = useRef<SessionDirectoryPayload | null>(null);

  const homeAnchored = sessionKey === null;

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
    if (homeAnchored && filesState === "idle") {
      void probeWorkspaceRoot();
    }
  }, [homeAnchored, filesState, probeWorkspaceRoot]);

  const fetchDirectory = useCallback(async (token: string, path: string | null) => {
    if (path === null && rootPayloadRef.current) {
      const payload = rootPayloadRef.current;
      rootPayloadRef.current = null;
      return payload;
    }
    return fetchWorkspaceFiles(token, path);
  }, []);

  const openPreview = onOpenFilePreview ?? setPreviewPath;
  // 宿主自带外置预览面板时（会话工作台）不内嵌预览列，文件树占满整列。
  const inlinePreview = !onOpenFilePreview;

  const renderTree = () => (
    <div className="flex min-h-0 flex-1 gap-2">
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
      <div
        data-testid="dock-file-tree-column"
        className={
          inlinePreview
            ? "flex min-h-0 w-[46%] shrink-0 flex-col gap-2"
            : "flex min-h-0 flex-1 flex-col gap-2"
        }
      >
        {homeAnchored ? (
          <WorkspaceFileTree
            getToken={getToken}
            fetchDirectory={fetchDirectory}
            onOpenFilePreview={openPreview}
          />
        ) : (
          <WorkspaceFileTree
            sessionKey={sessionKey ?? ""}
            getToken={getToken}
            onOpenFilePreview={openPreview}
          />
        )}
      </div>
    </div>
  );

  if (!homeAnchored) {
    return (
      <div
        data-scope={scope}
        className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto px-3 py-2 scrollbar-thin"
      >
        {renderTree()}
      </div>
    );
  }

  if (filesState === "loading" || filesState === "idle") {
    return (
      <div
        data-scope={scope}
        className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto px-3 py-2 scrollbar-thin"
      >
        <p className="py-1 text-[12px] text-muted-foreground/60">
          {t("workbench.treeLoading", { defaultValue: "Loading…" })}
        </p>
      </div>
    );
  }
  if (filesState === "noWorkspace") {
    return (
      <div
        data-scope={scope}
        className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto px-3 py-2 scrollbar-thin"
      >
        <p role="status" className="py-1 text-[12px] text-muted-foreground">
          {t("startPanel.openProjectFirst", {
            defaultValue: "Available after you pick a project",
          })}
        </p>
      </div>
    );
  }
  if (filesState === "error") {
    return (
      <div
        data-scope={scope}
        className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto px-3 py-2 scrollbar-thin"
      >
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
      </div>
    );
  }

  return (
    <div
      data-scope={scope}
      className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto px-3 py-2 scrollbar-thin"
    >
      {renderTree()}
    </div>
  );
}
