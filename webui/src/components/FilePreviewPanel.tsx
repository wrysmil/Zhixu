import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { AlertCircle, Check, ChevronRight, Loader2, Undo2, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { CodeBlock } from "@/components/CodeBlock";
import { ResizeHandle } from "@/components/workbench/ResizeHandle";
import { splitFilePath } from "@/components/FileReferenceChip";
import { useFileSaver } from "@/hooks/useFileSaver";
import { ApiError, fetchFilePreview } from "@/lib/api";
import type { FilePreviewPayload } from "@/lib/types";
import { useOptionalClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";

interface FilePreviewPanelProps {
  sessionKey: string;
  path: string;
  token: string;
  desktopWidth?: number;
  isClosing?: boolean;
  maxDesktopWidth?: number;
  onWidthChange?: (width: number) => void;
  onClose: () => void;
}

type PreviewState =
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | { status: "ready"; payload: FilePreviewPayload };

export function FilePreviewPanel({
  sessionKey,
  path,
  token,
  desktopWidth = 544,
  isClosing = false,
  maxDesktopWidth = 860,
  onWidthChange,
  onClose,
}: FilePreviewPanelProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<PreviewState>({ status: "loading" });
  const [entered, setEntered] = useState(false);
  const [resizing, setResizing] = useState(false);
  const panelRef = useRef<HTMLElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const clientContext = useOptionalClient();
  const saver = useFileSaver({
    transport: clientContext?.client ?? null,
    sessionKey,
    scope: null,
    payload: state.status === "ready" ? state.payload : null,
    onSaved: (next) => setState({ status: "ready", payload: next }),
  });

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setEntered(true));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    fetchFilePreview(tokenRef.current, sessionKey, path)
      .then((payload) => {
        if (!cancelled) setState({ status: "ready", payload });
      })
      .catch((error: unknown) => {
        if (!cancelled) setState({ status: "error", error });
      });
    return () => {
      cancelled = true;
    };
  }, [path, sessionKey]);

  useEffect(() => {
    const container = contentRef.current;
    if (!container) return;
    // A resize changes the available width; start long lines at their real
    // left edge instead of retaining an old horizontal offset from the slimmer panel.
    container.scrollLeft = 0;
    container.querySelectorAll("pre").forEach((pre) => {
      pre.scrollLeft = 0;
    });
  }, [desktopWidth, path]);

  const displayPath = state.status === "ready" ? state.payload.display_path : path;
  const previewPath = state.status === "ready" ? state.payload.path : displayPath;
  const normalizedPreviewPath = previewPath.replace(/\\/g, "/");
  const hasRootPrefix = normalizedPreviewPath.startsWith("/");
  const { name } = splitFilePath(displayPath);
  const fileName = name || displayPath;
  const pathParts = useMemo(
    () => normalizedPreviewPath.split("/").filter(Boolean),
    [normalizedPreviewPath],
  );
  const directoryParts = useMemo(
    () => (pathParts.length > 1 ? pathParts.slice(0, -1) : []),
    [pathParts],
  );
  const breadcrumbParts = useMemo(
    () => (directoryParts.length > 0 ? [...directoryParts, fileName] : [fileName]),
    [directoryParts, fileName],
  );
  const compactBreadcrumbParts = useMemo(
    () => (breadcrumbParts.length > 3 ? breadcrumbParts.slice(-3) : breadcrumbParts),
    [breadcrumbParts],
  );
  const hasCompactPrefix = breadcrumbParts.length > compactBreadcrumbParts.length;
  const breadcrumbTitle = `${hasRootPrefix ? "/" : ""}${[
    ...directoryParts,
    fileName,
  ].join("/")}`;
  const errorMessage = state.status === "error"
    ? (state.error instanceof ApiError
      ? (state.error.status === 404 && /API route not found/i.test(state.error.message)
        ? t("filePreview.routeMissing", {
          defaultValue: "File preview needs the latest gateway. Restart nanobot gateway and try again.",
        })
        : state.error.message)
      : t("filePreview.failed", { defaultValue: "Could not preview this file." }))
    : null;

  return (
    <aside
      ref={panelRef}
      aria-label={t("filePreview.aria", { defaultValue: "File preview" })}
      style={{
        "--file-preview-width": `${desktopWidth}px`,
        "--file-preview-slot-width": !entered || isClosing ? "0px" : `${desktopWidth}px`,
      } as CSSProperties}
      className={cn(
        "absolute inset-y-0 right-0 z-30 w-[min(100vw,var(--file-preview-slot-width))] overflow-hidden",
        !resizing && "transition-[width] duration-300 ease-out will-change-[width]",
        "md:relative md:z-auto md:w-[var(--file-preview-slot-width)] md:min-w-0 md:shrink-0",
        isClosing && "pointer-events-none",
      )}
      data-testid="file-preview-panel"
      data-file-preview-panel
    >
      <div
        className={cn(
          "absolute inset-y-0 right-0 flex w-[min(100vw,var(--file-preview-width))] flex-col overflow-hidden pb-[env(safe-area-inset-bottom)] md:w-[var(--file-preview-width)] md:pb-0",
          "border-l border-border/70 bg-background shadow-2xl md:shadow-none",
          "transition-[opacity,transform] duration-300 ease-out will-change-transform",
          !entered || isClosing ? "translate-x-full opacity-0" : "translate-x-0 opacity-100",
          "motion-reduce:translate-x-0",
        )}
      >
        {onWidthChange ? (
          <ResizeHandle label={t("filePreview.resize", { defaultValue: "Resize file preview" })}
            value={desktopWidth} min={Math.min(360, maxDesktopWidth)} max={maxDesktopWidth}
            className="absolute inset-y-0 left-0 hidden md:flex"
            valueAtPointer={(x) => (panelRef.current?.getBoundingClientRect().right ?? 0) - x}
            onChange={onWidthChange} onDraggingChange={setResizing}
          />
        ) : null}
        <div className="flex min-h-0 flex-1 flex-col">
          <div
            className="flex h-11 shrink-0 items-center gap-2 border-b border-border/60 px-3"
            title={previewPath}
          >
            <nav
              aria-label={t("filePreview.breadcrumb", { defaultValue: "File path" })}
              className="flex min-w-0 flex-1 items-center overflow-hidden text-sm leading-5"
              title={breadcrumbTitle}
              data-testid="file-preview-breadcrumb"
            >
              {hasCompactPrefix ? (
                <>
                  <span className="shrink-0 text-muted-foreground/55">...</span>
                  <ChevronRight
                    className="mx-1 h-3.5 w-3.5 shrink-0 text-muted-foreground/35"
                    aria-hidden
                  />
                </>
              ) : hasRootPrefix ? (
                <>
                  <span className="shrink-0 text-muted-foreground/55">/</span>
                  <ChevronRight
                    className="mx-1 h-3.5 w-3.5 shrink-0 text-muted-foreground/35"
                    aria-hidden
                  />
                </>
              ) : null}
              {compactBreadcrumbParts.map((part, index) => {
                const isLast = index === compactBreadcrumbParts.length - 1;
                return (
                  <span
                    key={`${part}-${index}`}
                    className="flex min-w-0 items-center overflow-hidden"
                  >
                    {index > 0 ? (
                      <ChevronRight
                        className="mx-1 h-3.5 w-3.5 shrink-0 text-muted-foreground/35"
                        aria-hidden
                      />
                    ) : null}
                    <span
                      className={cn(
                        "min-w-0 truncate rounded-mark px-1 py-0.5",
                        isLast
                          ? "font-medium text-foreground"
                          : "max-w-[26vw] shrink text-muted-foreground/78",
                      )}
                      data-testid={isLast ? "file-preview-title" : undefined}
                    >
                      {part}
                    </span>
                  </span>
                );
              })}
            </nav>
            {saver.editable ? (
              <>
                {saver.dirty ? (
                  <button
                    type="button"
                    data-testid="file-preview-revert"
                    onClick={saver.revert}
                    className={cn(
                      "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
                      "text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    )}
                    title={t("filePreview.cancel", { defaultValue: "Cancel editing" })}
                    aria-label={t("filePreview.cancel", { defaultValue: "Cancel editing" })}
                  >
                    <Undo2 className="h-4 w-4" aria-hidden />
                  </button>
                ) : null}
                <button
                  type="button"
                  data-testid="file-preview-save"
                  onClick={() => void saver.save()}
                  disabled={saver.saving || !saver.dirty}
                  className={cn(
                    "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
                    "text-muted-foreground transition-colors enabled:hover:bg-muted enabled:hover:text-foreground",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-35",
                  )}
                  title={`${t("filePreview.save", { defaultValue: "Save file" })} (Ctrl+S)`}
                  aria-label={t("filePreview.save", { defaultValue: "Save file" })}
                >
                  {saver.saving ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : (
                    <Check className="h-4 w-4" aria-hidden />
                  )}
                </button>
              </>
            ) : null}
            <button
              type="button"
              onClick={onClose}
              className={cn(
                "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
                "text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
              title={t("filePreview.close", { defaultValue: "Close file preview" })}
              aria-label={t("filePreview.close", { defaultValue: "Close file preview" })}
              data-testid="file-preview-close"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </div>

          {saver.error ? (
            <p role="alert" className="shrink-0 border-b border-destructive/15 bg-destructive/5 px-3 py-1.5 text-[11.5px] text-destructive">
              {t("filePreview.saveFailed", { defaultValue: "Could not save this file." })}
            </p>
          ) : saver.saved ? (
            <p role="status" className="shrink-0 border-b border-border/40 bg-muted/30 px-3 py-1.5 text-[11.5px] text-muted-foreground">
              {t("filePreview.saved", { defaultValue: "Saved" })}
            </p>
          ) : null}

          <div ref={contentRef} className="flex min-h-0 flex-1 flex-col overflow-hidden">
            {state.status === "loading" ? (
              <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                {t("filePreview.loading", { defaultValue: "Loading preview..." })}
              </div>
            ) : state.status === "error" ? (
              <div className="flex flex-1 items-center justify-center px-8 text-center text-sm text-muted-foreground">
                <div className="max-w-sm">
                  <AlertCircle
                    className="mx-auto mb-3 h-5 w-5 text-muted-foreground/70"
                    aria-hidden
                  />
                  <p>{errorMessage}</p>
                </div>
              </div>
            ) : saver.editable ? (
              <textarea
                data-testid="file-preview-editor"
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
                className="min-h-0 w-full flex-1 resize-none border-0 bg-background px-4 py-3 font-mono text-[12.5px] leading-[1.65] text-foreground outline-none"
              />
            ) : (
              <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
                {state.payload.truncated ? (
                  <div className="mx-4 mt-3 rounded-md border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-200">
                    {t("filePreview.truncated", {
                      defaultValue: "Preview is truncated because this file is large.",
                    })}
                  </div>
                ) : null}
                <CodeBlock
                  language={state.payload.language}
                  code={state.payload.content}
                  chrome="none"
                  highlight
                  showLineNumbers
                  wrapLongLines={false}
                  className="min-h-full"
                />
              </div>
            )}
          </div>
        </div>
      </div>
    </aside>
  );
}
