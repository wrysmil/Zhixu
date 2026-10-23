import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";

import { fetchSessionSearch, fetchWorkspaceSearch } from "@/lib/api";
import type { WorkspaceSearchPayload } from "@/lib/types";

const SEARCH_DEBOUNCE_MS = 250;
const MAX_QUERY_CHARS = 200;

type SearchState =
  | { status: "idle" }
  | { status: "loading"; query: string }
  | { status: "ready"; query: string; payload: WorkspaceSearchPayload }
  | { status: "error"; query: string };

interface DockFileSearchProps {
  sessionKey: string | null;
  getToken: () => string;
  showHidden: boolean;
  onOpen: (path: string) => void;
  onClose: () => void;
}

export function DockFileSearch({
  sessionKey,
  getToken,
  showHidden,
  onOpen,
  onClose,
}: DockFileSearchProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [state, setState] = useState<SearchState>({ status: "idle" });
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setState({ status: "idle" });
      return;
    }
    let cancelled = false;
    setState({ status: "loading", query: trimmed });
    const timer = window.setTimeout(() => {
      const pending = sessionKey
        ? fetchSessionSearch(getTokenRef.current(), sessionKey, trimmed, "", showHidden)
        : fetchWorkspaceSearch(getTokenRef.current(), trimmed, "", showHidden);
      pending
        .then((payload) => {
          if (!cancelled) setState({ status: "ready", query: trimmed, payload });
        })
        .catch(() => {
          if (!cancelled) setState({ status: "error", query: trimmed });
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query, sessionKey, showHidden]);

  const results = state.status === "ready" ? state.payload.results : [];

  return (
    <div
      data-testid="dock-file-search"
      className="flex shrink-0 flex-col gap-1 rounded-md border border-border/50 bg-background/70 p-1.5"
    >
      <div className="flex items-center gap-1">
        <input
          autoFocus
          data-testid="dock-file-search-input"
          type="search"
          value={query}
          maxLength={MAX_QUERY_CHARS}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") onClose();
          }}
          placeholder={t("dock.searchPlaceholder", {
            defaultValue: "Search file names and contents",
          })}
          spellCheck={false}
          autoComplete="off"
          className="h-6 min-w-0 flex-1 rounded border border-border/50 bg-background px-1.5 text-[11.5px] text-foreground outline-none focus-visible:ring-1 focus-visible:ring-ring/60"
        />
        <button
          type="button"
          aria-label={t("filePreview.close", { defaultValue: "Close file preview" })}
          onClick={onClose}
          className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/70 hover:bg-muted/60 hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>

      {state.status === "loading" ? (
        <p className="px-1 text-[11.5px] text-muted-foreground/70">
          {t("dock.searching", { defaultValue: "Searching…" })}
        </p>
      ) : null}

      {state.status === "error" ? (
        <p role="alert" className="px-1 text-[11.5px] text-destructive">
          {t("dock.searchFailed", { defaultValue: "Search failed" })}
        </p>
      ) : null}

      {state.status === "ready" && results.length === 0 ? (
        <p className="px-1 text-[11.5px] text-muted-foreground/70">
          {t("dock.noMatches", { defaultValue: "No matches" })}
        </p>
      ) : null}

      {results.length > 0 ? (
        <ul className="max-h-48 overflow-y-auto scrollbar-thin">
          {results.map((hit) => (
            <li key={`${hit.path}:${hit.line}:${hit.column}`}>
              <button
                type="button"
                data-testid={`dock-file-search-hit-${hit.path}`}
                onClick={() => onOpen(hit.path)}
                className="flex w-full min-w-0 flex-col gap-0.5 rounded px-1 py-1 text-left hover:bg-muted/50"
              >
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className="min-w-0 flex-1 truncate text-[11.5px] font-medium text-foreground/90">
                    {hit.name}
                  </span>
                  {hit.line > 0 ? (
                    <span className="shrink-0 text-[10.5px] tabular-nums text-muted-foreground/70">
                      {hit.line}:{hit.column}
                    </span>
                  ) : null}
                </span>
                <span className="min-w-0 truncate font-mono text-[10.5px] text-muted-foreground">
                  {hit.display_path}
                </span>
                <code className="line-clamp-2 break-all text-[11px] text-foreground/70">
                  {hit.snippet}
                </code>
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {state.status === "ready" && state.payload.truncated ? (
        <p className="px-1 text-[10.5px] text-amber-700 dark:text-amber-200">
          {t("dock.searchTruncated", { defaultValue: "Showing partial results" })}
        </p>
      ) : null}
    </div>
  );
}
