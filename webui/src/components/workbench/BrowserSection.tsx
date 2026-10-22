import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertCircle,
  AppWindow,
  ArrowLeft,
  ArrowRight,
  ExternalLink,
  Globe,
  RotateCw,
} from "lucide-react";

import { normalizeBrowserUrl, type BrowserUrlRejectReason } from "@/lib/browser-url";
import { cn } from "@/lib/utils";

// Most embed-refusing sites fire no load event inside the iframe; waiting a
// bounded time before offering the external-open fallback is the spec §3 heuristic.
const EMBED_TIMEOUT_MS = 4000;

type FrameStatus = "empty" | "loading" | "loaded" | "blocked";

const BROWSER_STATE_PREFIX = "zhixu.dock.browser.";
const MAX_STORED_HISTORY = 50;

interface StoredBrowserState {
  history: string[];
  index: number;
}

function loadBrowserState(scope?: string): StoredBrowserState {
  if (!scope) return { history: [], index: -1 };
  try {
    const raw = sessionStorage.getItem(BROWSER_STATE_PREFIX + scope);
    if (!raw) return { history: [], index: -1 };
    const parsed = JSON.parse(raw) as { history?: unknown; index?: unknown };
    if (!Array.isArray(parsed.history)) return { history: [], index: -1 };
    const history: string[] = [];
    for (const value of parsed.history) {
      if (typeof value !== "string") continue;
      const result = normalizeBrowserUrl(value);
      if (result.ok) history.push(result.url);
      if (history.length >= MAX_STORED_HISTORY) break;
    }
    const requested = typeof parsed.index === "number" ? Math.trunc(parsed.index) : -1;
    const index = Math.min(Math.max(requested, -1), history.length - 1);
    return { history, index };
  } catch {
    return { history: [], index: -1 };
  }
}

function openExternally(url: string): void {
  // ``window.mira`` is typed by lib/runtime.ts (MiraShellApi.openExternal);
  // plain browsers fall back to a new tab.
  const shell = typeof window !== "undefined" ? window.mira : undefined;
  if (typeof shell?.openExternal === "function") {
    void shell.openExternal(url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

interface BrowserSectionProps {
  /** dock 内嵌变体：占满宿主容器全高（B1），默认保持 v1 固定高度用法。 */
  fullHeight?: boolean;
  /** dock scope; enables per-scope history persistence. */
  scope?: string;
}

export function BrowserSection({ fullHeight = false, scope }: BrowserSectionProps = {}) {
  const { t } = useTranslation();
  const [initial] = useState(() => loadBrowserState(scope));
  const [draft, setDraft] = useState(
    initial.index >= 0 ? initial.history[initial.index] ?? "" : "",
  );
  const [historyStack, setHistoryStack] = useState<string[]>(initial.history);
  const [index, setIndex] = useState(initial.index);
  const [reloadKey, setReloadKey] = useState(0);
  const [error, setError] = useState<BrowserUrlRejectReason | null>(null);
  const [status, setStatus] = useState<FrameStatus>("empty");

  const current = index >= 0 ? historyStack[index] ?? null : null;

  useEffect(() => {
    if (!current) {
      setStatus("empty");
      return;
    }
    setStatus("loading");
    const timer = window.setTimeout(
      () => setStatus((value) => (value === "loading" ? "blocked" : value)),
      EMBED_TIMEOUT_MS,
    );
    return () => window.clearTimeout(timer);
  }, [current, reloadKey]);

  useEffect(() => {
    if (!scope) return;
    try {
      if (historyStack.length === 0) {
        sessionStorage.removeItem(BROWSER_STATE_PREFIX + scope);
      } else {
        sessionStorage.setItem(
          BROWSER_STATE_PREFIX + scope,
          JSON.stringify({ history: historyStack, index }),
        );
      }
    } catch {
      /* sessionStorage may be unavailable in private mode */
    }
  }, [scope, historyStack, index]);

  const navigate = (raw: string) => {
    const result = normalizeBrowserUrl(raw);
    if (!result.ok) {
      setError(result.reason);
      return;
    }
    setError(null);
    setDraft(result.url);
    setHistoryStack((stack) => [...stack.slice(0, index + 1), result.url]);
    setIndex((value) => value + 1);
    setReloadKey((value) => value + 1);
  };

  const canDetach =
    typeof window !== "undefined" &&
    typeof window.mira?.openBrowserWindow === "function";

  const openInWindow = async (url: string) => {
    const shell = window.mira;
    if (!shell?.openBrowserWindow) return;
    const opened = await shell.openBrowserWindow(url).catch(() => false);
    if (!opened) openExternally(url);
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    navigate(draft);
  };

  const errorText = error
    ? t(`startPanel.browserError_${error}`, {
        defaultValue:
          error === "scheme"
            ? "Only http/https addresses can be opened here."
            : error === "private"
              ? "Private or local-network addresses are not allowed."
              : "That doesn't look like a valid web address.",
      })
    : null;

  const buttonClass =
    "grid h-8 w-8 shrink-0 place-items-center rounded-full text-muted-foreground/70 transition-colors enabled:hover:bg-muted/70 enabled:hover:text-foreground disabled:opacity-35";

  return (
    <div
      data-testid="browser-section"
      className={cn("flex min-h-0 flex-col", fullHeight ? "h-full" : "gap-1.5")}
    >
      <form
        onSubmit={handleSubmit}
        className="flex shrink-0 items-center gap-1 border-b border-border/40 px-3 py-2"
      >
        <button
          type="button"
          aria-label={t("startPanel.browserBack", { defaultValue: "Back" })}
          disabled={index <= 0}
          onClick={() => setIndex((value) => Math.max(0, value - 1))}
          className={buttonClass}
        >
          <ArrowLeft className="h-4 w-4" aria-hidden />
        </button>
        <button
          type="button"
          aria-label={t("startPanel.browserForward", { defaultValue: "Forward" })}
          disabled={index < 0 || index >= historyStack.length - 1}
          onClick={() => setIndex((value) => Math.min(historyStack.length - 1, value + 1))}
          className={buttonClass}
        >
          <ArrowRight className="h-4 w-4" aria-hidden />
        </button>
        <button
          type="button"
          aria-label={t("startPanel.browserRefresh", { defaultValue: "Refresh" })}
          disabled={!current}
          onClick={() => setReloadKey((value) => value + 1)}
          className={buttonClass}
        >
          <RotateCw className="h-4 w-4" aria-hidden />
        </button>
        <label className="sr-only" htmlFor="browser-address">
          {t("startPanel.browserAddress", { defaultValue: "Address" })}
        </label>
        <div
          className={cn(
            "flex h-9 min-w-0 flex-1 items-center gap-2 rounded-full border bg-background pl-3 pr-0.5 transition-[border-color,box-shadow]",
            error
              ? "border-destructive/70 ring-2 ring-destructive/10"
              : "border-border/70 focus-within:border-primary/45 focus-within:ring-2 focus-within:ring-primary/10",
          )}
        >
          <Globe className="h-3.5 w-3.5 shrink-0 text-muted-foreground/55" aria-hidden />
          <input
            id="browser-address"
            type="text"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={t("startPanel.browserAddressPlaceholder", {
              defaultValue: "https://example.com",
            })}
            spellCheck={false}
            autoComplete="off"
            className="h-full min-w-0 flex-1 border-0 bg-transparent p-0 text-[12.5px] text-foreground outline-none placeholder:text-muted-foreground/50"
          />
          <button
            type="submit"
            aria-label={t("startPanel.browserGo", { defaultValue: "Go" })}
            className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-muted-foreground/70 transition-colors hover:bg-muted/70 hover:text-foreground"
          >
            <ArrowRight className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
        {current && status === "loaded" ? (
          <button
            type="button"
            aria-label={t("startPanel.externalOpen", { defaultValue: "Open externally" })}
            onClick={() => openExternally(current)}
            className={buttonClass}
          >
            <ExternalLink className="h-4 w-4" aria-hidden />
          </button>
        ) : null}
        {current && canDetach ? (
          <button
            type="button"
            data-testid="browser-detached"
            aria-label={t("dock.browserWindow", {
              defaultValue: "Open in browser window",
            })}
            title={t("dock.browserWindow", {
              defaultValue: "Open in browser window",
            })}
            onClick={() => void openInWindow(current)}
            className={buttonClass}
          >
            <AppWindow className="h-4 w-4" aria-hidden />
          </button>
        ) : null}
      </form>

      {errorText ? (
        <div
          role="alert"
          className="flex shrink-0 items-center gap-2 border-b border-destructive/15 bg-destructive/5 px-3 py-2 text-[11.5px] leading-5 text-destructive"
        >
          <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="min-w-0 [overflow-wrap:anywhere]">{errorText}</span>
        </div>
      ) : null}

      {status === "empty" ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="shrink-0 px-3 pt-3">
            <span className="inline-flex items-center gap-1.5 rounded-md bg-muted/60 px-2 py-1 text-[11.5px] text-muted-foreground">
              <Globe className="h-3 w-3" aria-hidden />
              {t("startPanel.browserNewTab", { defaultValue: "New tab" })}
            </span>
          </div>
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 px-6 pb-16 text-center">
            <Globe
              className="h-16 w-16 text-muted-foreground/20"
              strokeWidth={1.2}
              aria-hidden
            />
            <p className="text-[13px] font-medium text-foreground/75">
              {t("startPanel.browserNewTab", { defaultValue: "New tab" })}
            </p>
            <p className="max-w-[22rem] text-[12px] leading-5 text-muted-foreground/60">
              {t("startPanel.browserEmptyHint", {
                defaultValue: "Enter an address to start browsing.",
              })}
            </p>
          </div>
        </div>
      ) : null}

      {current && status !== "empty" ? (
        status === "blocked" ? (
          <div
            role="status"
            className={cn(
              "flex flex-col items-center justify-center gap-2 border-t border-dashed border-border/60 bg-muted/30 px-3 text-center",
              fullHeight ? "min-h-0 flex-1" : "h-40",
            )}
          >
            <p className="text-[12px] text-muted-foreground">
              {t("startPanel.embedBlocked", {
                defaultValue: "This site refuses to be embedded.",
              })}
            </p>
            <button
              type="button"
              onClick={() => openExternally(current)}
              className="rounded-full border border-border/60 bg-background px-3 py-1 text-[11.5px] text-foreground hover:bg-muted/45"
            >
              {t("startPanel.externalOpen", { defaultValue: "Open externally" })}
            </button>
          </div>
        ) : (
          <iframe
            key={`${current}-${reloadKey}`}
            data-testid="browser-iframe"
            title={t("startPanel.browserFrameTitle", {
              defaultValue: "Embedded browser page",
            })}
            src={current}
            onLoad={() => setStatus("loaded")}
            // No ``allow-same-origin``: the URL whitelist is only checked at submit,
            // so a redirect or DNS rebinding can land this frame on the gateway's
            // origin, where scripts could read the parent page's token.
            sandbox="allow-scripts allow-forms allow-popups"
            referrerPolicy="no-referrer"
            className={cn(
              "w-full bg-background",
              fullHeight ? "min-h-0 flex-1" : "h-72",
              status === "loading" && "animate-pulse",
            )}
          />
        )
      ) : null}
    </div>
  );
}
