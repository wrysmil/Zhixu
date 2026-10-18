import * as DialogPrimitive from "@radix-ui/react-dialog";
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, CircleAlert, RefreshCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { MarkdownText } from "@/components/MarkdownText";
import {
  SubagentExecutionActivity,
  statusLabel,
} from "@/components/thread/SubagentExecutionActivity";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type {
  ExecutionPhase,
  ExecutionStatus,
  SubagentExecutionState,
} from "@/features/subagent-execution/types";
import { isTerminalExecutionStatus } from "@/features/subagent-execution/reducer";
import { cn } from "@/lib/utils";

export {
  SubagentExecutionActivity,
  SubagentExecutionEntry,
  SubagentExecutionEntryProvider,
  type SubagentExecutionEntryValue,
} from "@/components/thread/SubagentExecutionActivity";

export type RightSurface =
  | { kind: "workbench" }
  | { kind: "tools" }
  | { kind: "file"; path: string }
  | { kind: "execution"; invocationId: string };

export const WORKBENCH_SURFACE: RightSurface = { kind: "workbench" };

function sameRightSurface(left: RightSurface, right: RightSurface): boolean {
  if (left.kind !== right.kind) return false;
  if (left.kind === "file" && right.kind === "file") return left.path === right.path;
  if (left.kind === "execution" && right.kind === "execution") {
    return left.invocationId === right.invocationId;
  }
  return true;
}

/** Push a replacement right surface while retaining the exact prior surface for back navigation. */
export function pushRightSurface(
  stack: readonly RightSurface[],
  next: RightSurface,
): RightSurface[] {
  const base = stack.length > 0 ? stack : [WORKBENCH_SURFACE];
  const top = base[base.length - 1];
  if (sameRightSurface(top, next)) return [...base];
  return [...base, next];
}

/** Pop one surface; the workbench is the non-removable base slot. */
export function popRightSurface(stack: readonly RightSurface[]): RightSurface[] {
  if (stack.length <= 1) return [WORKBENCH_SURFACE];
  return stack.slice(0, -1);
}

function phaseLabel(
  phase: ExecutionPhase,
  t: (key: string, options?: { defaultValue?: string }) => string,
): string {
  return t(`thread.subagentExecution.phase.${phase}`, { defaultValue: phase });
}

function statusTone(status: ExecutionStatus): string {
  if (status === "succeeded") return "text-emerald-600/85";
  if (status === "failed") return "text-destructive";
  if (status === "cancelled") return "text-muted-foreground";
  if (status === "interrupted") return "text-amber-600/85";
  if (status === "queued") return "text-amber-600/80";
  return "text-muted-foreground/75";
}

function formatUnit(value: number, unit: "hour" | "minute" | "second", locale: string): string {
  try {
    return new Intl.NumberFormat(locale, {
      style: "unit",
      unit,
      unitDisplay: "short",
    }).format(value);
  } catch {
    const suffix = unit === "hour" ? "h" : unit === "minute" ? "m" : "s";
    return `${value}${suffix}`;
  }
}

function durationText(
  ms: number | null | undefined,
  locale: string,
): string | null {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return null;
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) return formatUnit(seconds, "second", locale);
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  if (minutes < 60) {
    return `${formatUnit(minutes, "minute", locale)} ${formatUnit(remaining, "second", locale)}`;
  }
  return `${formatUnit(Math.floor(minutes / 60), "hour", locale)} ${formatUnit(minutes % 60, "minute", locale)}`;
}

function panelDuration(
  state: SubagentExecutionState,
  nowMs: number,
  locale: string,
): string | null {
  const snapshot = state.snapshot;
  if (snapshot.status === "queued") return durationText(snapshot.queueDurationMs, locale);
  if (snapshot.durationMs !== null) return durationText(snapshot.durationMs, locale);
  if (!snapshot.startedAt) return null;
  return durationText(nowMs - Date.parse(snapshot.startedAt), locale);
}

interface PanelContentProps {
  executions: readonly SubagentExecutionState[];
  selectedInvocationId: string | null;
  onSelect: (invocationId: string) => void;
  onReload: () => void;
  onClose: () => void;
  onOpenFilePreview?: (path: string) => void;
  initialScrollTop?: number;
  onScrollTopChange?: (scrollTop: number) => void;
  headerTitleId?: string;
}

function PanelContent({
  executions,
  selectedInvocationId,
  onSelect,
  onReload,
  onClose,
  onOpenFilePreview,
  initialScrollTop = 0,
  onScrollTopChange,
  headerTitleId,
}: PanelContentProps) {
  const { t, i18n } = useTranslation();
  const selected = useMemo(
    () => executions.find((state) => state.snapshot.invocationId === selectedInvocationId)
      ?? executions[0]
      ?? null,
    [executions, selectedInvocationId],
  );
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const lastInvocationRef = useRef<string | null>(null);
  const nowMs = useNow(selected?.snapshot.status === "running");

  useEffect(() => {
    const invocationId = selected?.snapshot.invocationId ?? null;
    if (!bodyRef.current || invocationId === lastInvocationRef.current) return;
    lastInvocationRef.current = invocationId;
    bodyRef.current.scrollTop = initialScrollTop;
  }, [initialScrollTop, selected?.snapshot.invocationId]);

  if (!selected) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-muted-foreground">
        {t("thread.subagentExecution.unavailable", { defaultValue: "No execution details" })}
      </div>
    );
  }

  const snapshot = selected.snapshot;
  const status = statusLabel(snapshot.status, t);
  const phase = phaseLabel(snapshot.phase, t);
  // 终态后阶段已无意义，继续展示会出现「Done · Finalizing」这种自相矛盾的进度。
  const terminal = isTerminalExecutionStatus(snapshot.status);
  const elapsed = panelDuration(
    selected,
    nowMs,
    i18n.resolvedLanguage ?? i18n.language,
  );
  const result = snapshot.result;
  const sortedActivities = [...selected.activities].sort((left, right) => {
    const started = left.startedAt.localeCompare(right.startedAt);
    return started !== 0 ? started : left.activityId.localeCompare(right.activityId);
  });

  const picker = executions.length > 1 ? (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`${t("thread.subagentExecution.taskSwitcher", {
            defaultValue: "Switch sub-agent task",
          })}: ${snapshot.agentName} · ${snapshot.title}`}
          className="flex w-full items-center gap-2 rounded-control border border-border/70 bg-background px-2.5 py-2 text-left transition-colors hover:bg-accent/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/55"
        >
          <span className="min-w-0 flex-1">
            <span className="flex min-w-0 items-center gap-2">
              <strong className="truncate text-[12px] font-semibold text-foreground/90">
                {snapshot.agentName}
              </strong>
              <span className={cn("shrink-0 text-[11px]", statusTone(snapshot.status))}>{status}</span>
            </span>
            <span className="block truncate text-[12px] text-muted-foreground/80">
              {snapshot.title}
            </span>
          </span>
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground/65" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-[min(24rem,var(--radix-dropdown-menu-content-available-width))]">
        <DropdownMenuRadioGroup
          value={snapshot.invocationId}
          onValueChange={onSelect}
        >
          {executions.map((execution) => {
            const item = execution.snapshot;
            const itemStatus = statusLabel(item.status, t);
            return (
              <DropdownMenuRadioItem
                key={item.invocationId}
                value={item.invocationId}
                className="items-start py-2"
              >
                <span className="min-w-0">
                  <span className="flex min-w-0 items-center gap-2">
                    <strong className="truncate text-[12px]">{item.agentName}</strong>
                    <span className={cn("shrink-0 text-[11px]", statusTone(item.status))}>
                      {itemStatus}
                    </span>
                  </span>
                  <span className="block truncate text-[12px] text-muted-foreground/75">
                    {item.title}
                  </span>
                </span>
                {item.invocationId === snapshot.invocationId ? (
                  <Check className="ml-auto h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
                ) : null}
              </DropdownMenuRadioItem>
            );
          })}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  ) : (
    <div className="rounded-control border border-border/60 bg-background px-2.5 py-2">
      <span className="flex min-w-0 items-center gap-2">
        <strong className="truncate text-[12px] font-semibold text-foreground/90">
          {snapshot.agentName}
        </strong>
        <span className={cn("shrink-0 text-[11px]", statusTone(snapshot.status))}>{status}</span>
      </span>
      <span className="block truncate text-[12px] text-muted-foreground/80">{snapshot.title}</span>
    </div>
  );

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header className="flex min-h-0 shrink-0 items-start gap-3 border-b border-border/60 px-4 py-3">
        <div className="min-w-0 flex-1">
          <span className="text-[11px] font-medium text-muted-foreground/70">
            {t("thread.subagentExecution.title", { defaultValue: "Sub-agent execution" })}
          </span>
          <h2
            id={headerTitleId}
            className="truncate text-[15px] font-semibold leading-6 text-foreground"
            title={snapshot.title}
          >
            {snapshot.title}
          </h2>
          <p className="mt-0.5 flex min-w-0 items-center gap-2 text-[12px] text-muted-foreground/80">
            <span className={cn("font-medium", statusTone(snapshot.status))}>{status}</span>
            {terminal ? null : (
              <>
                <span aria-hidden>·</span>
                <span>{phase}</span>
              </>
            )}
            {elapsed ? (
              <>
                <span aria-hidden>·</span>
                <span className="tabular-nums" aria-hidden>{elapsed}</span>
              </>
            ) : null}
          </p>
          <span
            id="subagent-execution-status-live"
            data-testid="subagent-execution-status-live"
            role="status"
            aria-live="polite"
            className="sr-only"
          >
            {status}
          </span>
        </div>
        <button
          type="button"
          aria-label={t("thread.subagentExecution.reload", { defaultValue: "Reload" })}
          onClick={onReload}
          className="grid h-8 w-8 shrink-0 place-items-center rounded-control text-muted-foreground hover:bg-accent/45 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/55"
        >
          <RefreshCw className="h-4 w-4" aria-hidden />
        </button>
        <button
          type="button"
          aria-label={t("thread.subagentExecution.close", { defaultValue: "Close execution details" })}
          onClick={onClose}
          className="grid h-8 w-8 shrink-0 place-items-center rounded-control text-muted-foreground hover:bg-accent/45 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/55"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </header>

      <div
        ref={bodyRef}
        data-testid="subagent-execution-scroll"
        className="min-h-0 flex-1 overflow-y-auto px-4 py-3"
        onScroll={(event) => onScrollTopChange?.(event.currentTarget.scrollTop)}
      >
        {picker}
        {selected.connectionStale ? (
          <div className="mt-3 flex items-start gap-2 rounded-control bg-amber-500/10 px-2.5 py-2 text-[12px] text-amber-800 dark:text-amber-200" role="status">
            <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            {t("thread.subagentExecution.connectionStale", {
              defaultValue: "Connection lost, state is waiting to sync",
            })}
          </div>
        ) : null}
        {selected.historyLimited ? (
          <p className="mt-3 rounded-control bg-muted/55 px-2.5 py-2 text-[12px] text-muted-foreground">
            {t("thread.subagentExecution.historyLimited", {
              defaultValue: "Some earlier activity has expired",
            })}
          </p>
        ) : null}
        {selected.error ? (
          <p className="mt-3 rounded-control bg-destructive/10 px-2.5 py-2 text-[12px] text-destructive">
            {selected.error}
          </p>
        ) : null}

        <section className="mt-4">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/65">
            {t("thread.subagentExecution.instruction", { defaultValue: "Task" })}
          </h3>
          <div className="mt-1.5 text-[13px] leading-5 text-foreground/80">
            <MarkdownText onOpenFilePreview={onOpenFilePreview}>
              {snapshot.instructionSummary}
            </MarkdownText>
          </div>
        </section>

        <section className="mt-5">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/65">
            {t("thread.subagentExecution.process", { defaultValue: "Execution" })}
          </h3>
          {sortedActivities.length > 0 ? (
            <ol className="mt-1" data-testid="subagent-execution-timeline">
              {sortedActivities.map((activity) => (
                <SubagentExecutionActivity key={activity.activityId} activity={activity} nowMs={nowMs} />
              ))}
            </ol>
          ) : (
            <p className="mt-1.5 text-[12px] text-muted-foreground/70">
              {t("thread.subagentExecution.noActivities", { defaultValue: "No public activity yet." })}
            </p>
          )}
        </section>

        {result ? (
          <section className="mt-5 border-t border-border/55 pt-4">
            <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/65">
              {t("thread.subagentExecution.result", { defaultValue: "Sub-agent result" })}
            </h3>
            <div className="mt-1.5 text-[13px] leading-5 text-foreground/80">
              <MarkdownText onOpenFilePreview={onOpenFilePreview}>{result.summary}</MarkdownText>
            </div>
            {result.truncated ? (
              <p className="mt-2 text-[12px] text-muted-foreground/75">
                {t("thread.subagentExecution.truncated", { defaultValue: "Content truncated" })}
              </p>
            ) : null}
            {result.errorMessage ? (
              <p className="mt-2 text-[12px] text-destructive">{result.errorMessage}</p>
            ) : null}
          </section>
        ) : null}

        <p className="mt-5 text-[11px] text-muted-foreground/55">
          {t("thread.subagentExecution.call", {
            id: snapshot.invocationId,
            defaultValue: "Call {{id}}",
          })}
        </p>
      </div>
    </div>
  );
}

function useNow(active: boolean): number {
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    setNowMs(Date.now());
    const interval = window.setInterval(() => setNowMs(Date.now()), 1_000);
    return () => window.clearInterval(interval);
  }, [active]);
  return nowMs;
}

export interface SubagentExecutionPanelProps {
  executions: readonly SubagentExecutionState[];
  selectedInvocationId: string | null;
  onSelect: (invocationId: string) => void;
  onReload: () => void;
  onClose: () => void;
  onOpenFilePreview?: (path: string) => void;
  initialScrollTop?: number;
  onScrollTopChange?: (scrollTop: number) => void;
  presentation?: "sidebar" | "dialog";
  returnFocus?: HTMLElement | null;
}

export function SubagentExecutionPanel({
  executions,
  selectedInvocationId,
  onSelect,
  onReload,
  onClose,
  onOpenFilePreview,
  initialScrollTop = 0,
  onScrollTopChange,
  presentation = "sidebar",
  returnFocus = null,
}: SubagentExecutionPanelProps) {
  const { t } = useTranslation();
  const selected = executions.find(
    (state) => state.snapshot.invocationId === selectedInvocationId,
  ) ?? executions[0] ?? null;
  const title = selected?.snapshot.title
    ?? t("thread.subagentExecution.title", { defaultValue: "Sub-agent execution" });

  if (presentation === "dialog") {
    return (
      <DialogPrimitive.Root open onOpenChange={(open) => {
        if (!open) onClose();
      }}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/25 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
          <DialogPrimitive.Content
            data-testid="subagent-execution-dialog"
            aria-modal="true"
            className="fixed inset-y-0 right-0 z-50 w-[min(100vw,440px)] border-l border-border bg-background shadow-2xl outline-none data-[state=open]:animate-in data-[state=open]:slide-in-from-right"
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              window.requestAnimationFrame(() => returnFocus?.focus());
            }}
          >
            <DialogPrimitive.Title className="sr-only">{title}</DialogPrimitive.Title>
            <DialogPrimitive.Description className="sr-only">
              {selected?.snapshot.instructionSummary ?? ""}
            </DialogPrimitive.Description>
            <PanelContent
              executions={executions}
              selectedInvocationId={selectedInvocationId}
              onSelect={onSelect}
              onReload={onReload}
              onClose={onClose}
              onOpenFilePreview={onOpenFilePreview}
              initialScrollTop={initialScrollTop}
              onScrollTopChange={onScrollTopChange}
            />
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
    );
  }

  return (
    <aside
      aria-label={title}
      data-testid="subagent-execution-panel"
      data-presentation="sidebar"
      className="relative block w-[440px] min-w-[440px] shrink-0 border-l border-border/70 bg-background"
    >
      <PanelContent
        executions={executions}
        selectedInvocationId={selectedInvocationId}
        onSelect={onSelect}
        onReload={onReload}
        onClose={onClose}
        onOpenFilePreview={onOpenFilePreview}
        initialScrollTop={initialScrollTop}
        onScrollTopChange={onScrollTopChange}
      />
    </aside>
  );
}
