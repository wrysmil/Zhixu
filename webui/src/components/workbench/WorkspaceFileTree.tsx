import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronRight,
  File,
  Folder,
  FolderOpen,
  RotateCw,
} from "lucide-react";

import { fetchSessionDirectory } from "@/lib/api";
import type { SessionDirectoryEntry, SessionDirectoryPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

const ROOT_KEY = "<root>";

type NodeState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; entries: SessionDirectoryEntry[]; truncated: boolean };

interface WorkspaceFileTreeProps {
  sessionKey?: string;
  getToken: () => string;
  onOpenFilePreview?: (path: string) => void;
  /**
   * Optional data source override (contract §2: the home Start panel swaps the
   * fetcher to the workspace endpoint). Defaults to the session-anchored
   * listing when omitted.
   */
  fetchDirectory?: (
    token: string,
    path: string | null,
    showHidden: boolean,
  ) => Promise<SessionDirectoryPayload>;
  showHidden?: boolean;
  refreshToken?: number;
  collapseToken?: number;
}

export function WorkspaceFileTree({
  sessionKey,
  getToken,
  onOpenFilePreview,
  fetchDirectory,
  showHidden = false,
  refreshToken = 0,
  collapseToken = 0,
}: WorkspaceFileTreeProps) {
  const { t } = useTranslation();
  const [nodes, setNodes] = useState<Record<string, NodeState>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  const load = useCallback(
    async (path: string | null, nodeKey: string) => {
      setNodes((current) => ({ ...current, [nodeKey]: { status: "loading" } }));
      try {
        const payload = fetchDirectory
          ? await fetchDirectory(getTokenRef.current(), path, showHidden)
          : await fetchSessionDirectory(
              getTokenRef.current(),
              sessionKey ?? "",
              path,
              "",
              showHidden,
            );
        setNodes((current) => ({
          ...current,
          [nodeKey]: {
            status: "ready",
            entries: payload.entries,
            truncated: payload.truncated,
          },
        }));
      } catch {
        setNodes((current) => ({ ...current, [nodeKey]: { status: "error" } }));
      }
    },
    [sessionKey, fetchDirectory, showHidden],
  );

  useEffect(() => {
    setNodes({});
    setExpanded({});
    void load(null, ROOT_KEY);
  }, [load, refreshToken]);

  useEffect(() => {
    if (collapseToken > 0) setExpanded({});
  }, [collapseToken]);

  const onToggleDir = (entry: SessionDirectoryEntry) => {
    const willOpen = !expanded[entry.path];
    setExpanded((current) => ({ ...current, [entry.path]: willOpen }));
    if (willOpen && !nodes[entry.path]) void load(entry.path, entry.path);
  };

  const retry = (nodeKey: string) => {
    if (nodeKey === ROOT_KEY) {
      void load(null, ROOT_KEY);
      return;
    }
    void load(nodeKey, nodeKey);
  };

  const renderLevel = (nodeKey: string, depth: number): ReactNode => {
    const state = nodes[nodeKey];
    if (!state || state.status === "loading") {
      return (
        <p className="py-1 text-[12px] text-muted-foreground/60">
          {t("workbench.treeLoading", { defaultValue: "Loading…" })}
        </p>
      );
    }
    if (state.status === "error") {
      return (
        <button
          type="button"
          onClick={() => retry(nodeKey)}
          className="flex items-center gap-1.5 py-1 text-[12px] text-muted-foreground hover:text-foreground"
        >
          <RotateCw className="h-3 w-3" aria-hidden />
          {t("workbench.treeRetry", { defaultValue: "Could not read this folder — retry" })}
        </button>
      );
    }
    if (!state.entries.length) {
      if (nodeKey === ROOT_KEY) {
        return (
          <p className="py-1 text-[12px] text-muted-foreground/60">
            {t("workbench.treeEmpty", { defaultValue: "This workspace is empty" })}
          </p>
        );
      }
      return (
        <p className="py-0.5 text-[11.5px] text-muted-foreground/50">
          {t("workbench.treeEmptyFolder", { defaultValue: "Empty" })}
        </p>
      );
    }
    return (
      <ul className={cn("space-y-px", depth > 0 && "ml-3 border-l border-border/40 pl-1.5")}>
        {state.entries.map((entry) => (
          <li key={entry.path}>
            {entry.is_dir ? (
              <>
                <button
                  type="button"
                  aria-expanded={Boolean(expanded[entry.path])}
                  onClick={() => onToggleDir(entry)}
                  title={entry.display_path}
                  data-hidden={entry.hidden ? "true" : undefined}
                  className={cn(
                    "flex w-full min-w-0 items-center gap-1.5 rounded px-1 py-0.5 text-left text-[12px] text-muted-foreground hover:bg-muted/45 hover:text-foreground",
                    entry.hidden && "opacity-60",
                  )}
                >
                  <ChevronRight
                    className={cn(
                      "h-3 w-3 shrink-0 opacity-60 transition-transform",
                      expanded[entry.path] && "rotate-90",
                    )}
                    aria-hidden
                  />
                  {expanded[entry.path] ? (
                    <FolderOpen className="h-3 w-3 shrink-0 opacity-60" aria-hidden />
                  ) : (
                    <Folder className="h-3 w-3 shrink-0 opacity-60" aria-hidden />
                  )}
                  <span className="min-w-0 truncate">{entry.name}</span>
                </button>
                {expanded[entry.path] ? (
                  <div className="mt-px">
                    {renderLevel(entry.path, depth + 1)}
                  </div>
                ) : null}
              </>
            ) : (
              <button
                type="button"
                onClick={() => onOpenFilePreview?.(entry.path)}
                disabled={!onOpenFilePreview}
                title={entry.display_path}
                data-hidden={entry.hidden ? "true" : undefined}
                className={cn(
                  "flex w-full min-w-0 items-center gap-1.5 rounded px-1 py-0.5 text-left text-[12px] text-muted-foreground enabled:hover:bg-muted/45 enabled:hover:text-foreground disabled:cursor-default",
                  entry.hidden && "opacity-60",
                )}
              >
                <ChevronRight className="h-3 w-3 shrink-0 opacity-0" aria-hidden />
                <File className="h-3 w-3 shrink-0 opacity-50" aria-hidden />
                <span className="min-w-0 truncate">{entry.name}</span>
              </button>
            )}
          </li>
        ))}
        {state.truncated ? (
          <li className="px-1 py-1 text-[11.5px] text-muted-foreground/60">
            {t("workbench.treeTruncated", { defaultValue: "Showing the first items only" })}
          </li>
        ) : null}
      </ul>
    );
  };

  return <div className="min-w-0">{renderLevel(ROOT_KEY, 0)}</div>;
}
