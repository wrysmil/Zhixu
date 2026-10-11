import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, ArrowRight, ExternalLink, RotateCw } from "lucide-react";

import { normalizeBrowserUrl, type BrowserUrlRejectReason } from "@/lib/browser-url";
import { cn } from "@/lib/utils";

// Most embed-refusing sites fire no load event inside the iframe; waiting a
// bounded time before offering the external-open fallback is the spec §3 heuristic.
const EMBED_TIMEOUT_MS = 4000;

type FrameStatus = "empty" | "loading" | "loaded" | "blocked";

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
}

export function BrowserSection({ fullHeight = false }: BrowserSectionProps = {}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");
  const [historyStack, setHistoryStack] = useState<string[]>([]);
  const [index, setIndex] = useState(-1);
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
    "grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/75 enabled:hover:bg-muted/60 enabled:hover:text-foreground disabled:opacity-40";

  return (
    <div data-testid="browser-section" className={cn("flex flex-col gap-1.5", fullHeight && "h-full min-h-0")}>
      <form onSubmit={handleSubmit} className="flex items-center gap-1">
        <button
          type="button"
          aria-label={t("startPanel.browserBack", { defaultValue: "Back" })}
          disabled={index <= 0}
          onClick={() => setIndex((value) => Math.max(0, value - 1))}
          className={buttonClass}
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
        </button>
        <button
          type="button"
          aria-label={t("startPanel.browserForward", { defaultValue: "Forward" })}
          disabled={index < 0 || index >= historyStack.length - 1}
          onClick={() => setIndex((value) => Math.min(historyStack.length - 1, value + 1))}
          className={buttonClass}
        >
          <ArrowRight className="h-3.5 w-3.5" aria-hidden />
        </button>
        <button
          type="button"
          aria-label={t("startPanel.browserRefresh", { defaultValue: "Refresh" })}
          disabled={!current}
          onClick={() => setReloadKey((value) => value + 1)}
          className={buttonClass}
        >
          <RotateCw className="h-3.5 w-3.5" aria-hidden />
        </button>
        <label className="sr-only" htmlFor="browser-address">
          {t("startPanel.browserAddress", { defaultValue: "Address" })}
        </label>
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
          className="h-6 min-w-0 flex-1 rounded border border-border/50 bg-background px-1.5 text-[11.5px] text-foreground outline-none focus-visible:ring-1 focus-visible:ring-ring/60"
        />
        <button
          type="submit"
          aria-label={t("startPanel.browserGo", { defaultValue: "Go" })}
          className={buttonClass}
        >
          <ArrowRight className="h-3.5 w-3.5" aria-hidden />
        </button>
        {current && status === "loaded" ? (
          <button
            type="button"
            aria-label={t("startPanel.externalOpen", { defaultValue: "Open externally" })}
            onClick={() => openExternally(current)}
            className={buttonClass}
          >
            <ExternalLink className="h-3.5 w-3.5" aria-hidden />
          </button>
        ) : null}
      </form>

      {errorText ? (
        <p role="alert" className="text-[11.5px] text-destructive">
          {errorText}
        </p>
      ) : null}

      {status === "empty" ? (
        <p className="text-[12px] text-muted-foreground/60">
          {t("startPanel.browserEmptyHint", {
            defaultValue: "Enter an address to start browsing.",
          })}
        </p>
      ) : null}

      {current && status !== "empty" ? (
        status === "blocked" ? (
          <div
            role="status"
            className={cn(
              "flex flex-col items-center justify-center gap-2 rounded border border-dashed border-border/60 bg-muted/30 px-3 text-center",
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
              className="rounded border border-border/50 bg-background px-2 py-1 text-[11.5px] text-foreground hover:bg-muted/45"
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
              "w-full rounded border border-border/50 bg-background",
              fullHeight ? "min-h-0 flex-1" : "h-72",
              status === "loading" && "animate-pulse",
            )}
          />
        )
      ) : null}
    </div>
  );
}
