import { ChevronDown, LoaderCircle } from "lucide-react";
import type { ReactNode, Ref } from "react";

import { cn } from "@/lib/utils";

interface ThinkingReasoningShellProps {
  active: boolean;
  expanded: boolean;
  label: string;
  children: ReactNode;
  viewportRef: Ref<HTMLDivElement>;
  contentRef: Ref<HTMLDivElement>;
  fadeTop: boolean;
  fadeBottom: boolean;
  hasDetails?: boolean;
  onToggle: () => void;
  onScroll: () => void;
}

export function ThinkingReasoningShell({
  active,
  expanded,
  label,
  children,
  viewportRef,
  contentRef,
  fadeTop,
  fadeBottom,
  hasDetails = true,
  onToggle,
  onScroll,
}: ThinkingReasoningShellProps) {
  const statusLabel = (
    <>
      {active ? (
        <LoaderCircle
          data-testid="activity-running-indicator"
          className="h-4 w-4 shrink-0 animate-spin text-[hsl(var(--workbench-accent))] motion-reduce:animate-none"
          strokeWidth={2}
          aria-hidden="true"
        />
      ) : null}
      <span
        className={cn(
          "min-w-0 truncate",
          active ? "text-[15px] font-semibold text-foreground" : "text-[14px] font-medium text-muted-foreground",
        )}
      >
        {label}
      </span>
    </>
  );

  if (!hasDetails) {
    // No activity yet: a compact pill reads as "work in progress" instead of an
    // empty framed box, and keeps the running state impossible to miss.
    return (
      <div
        className="flex w-full flex-col"
        data-state={active ? "thinking" : "done"}
      >
        <div
          className={cn(
            "inline-flex w-fit max-w-full items-center gap-2 self-start rounded-full px-3 py-1.5",
            active
              ? "bg-[hsl(var(--workbench-accent)/0.1)] text-foreground ring-1 ring-inset ring-[hsl(var(--workbench-accent)/0.28)]"
              : "bg-muted/60 text-muted-foreground ring-1 ring-inset ring-border/50",
          )}
          role="status"
          aria-label={label}
          aria-live={active ? "polite" : undefined}
        >
          {statusLabel}
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "relative flex w-full flex-col",
        expanded && "overflow-hidden rounded-2xl bg-muted/30 px-3 pb-3 pt-2.5 ring-1 ring-inset ring-border/45 sm:px-4 sm:pb-4",
      )}
      data-state={active ? "thinking" : "done"}
    >
      {active && expanded ? (
        <span
          data-testid="activity-running-sweep"
          className="pointer-events-none absolute inset-x-0 top-0 h-[2px] overflow-hidden"
          aria-hidden
        >
          <span className="activity-running-sweep block h-full w-1/3 bg-gradient-to-r from-transparent via-[hsl(var(--workbench-accent))] to-transparent" />
        </span>
      ) : null}
      <button
        type="button"
        data-thread-disclosure=""
        className={cn(
          "group inline-flex min-h-7 w-full items-center gap-2 rounded-lg bg-transparent p-0 text-left",
          "transition-colors hover:bg-foreground/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/45",
          expanded && "-mx-2 px-2",
        )}
        onClick={onToggle}
        aria-expanded={expanded}
        aria-label={label}
        aria-live={active ? "polite" : undefined}
      >
        {statusLabel}
        <span
          className={cn(
            "ml-auto inline-flex shrink-0 transition-transform [transition-duration:220ms] ease-out",
            "motion-reduce:transition-none",
            expanded && "rotate-180",
          )}
        >
          <ChevronDown
            className={cn(
              "h-4 w-4 text-muted-foreground/70 transition-colors duration-200",
              "group-hover:text-foreground motion-reduce:transition-none",
            )}
            strokeWidth={1.8}
            aria-hidden
          />
        </span>
      </button>

      <div
        {...(!expanded ? { inert: "" } : {})}
        aria-hidden={!expanded}
        className={cn(
          "grid transition-[grid-template-rows,opacity] [transition-duration:220ms] ease-out motion-reduce:transition-none",
          expanded
            ? "grid-rows-[1fr] opacity-100"
            : "pointer-events-none grid-rows-[0fr] opacity-0",
        )}
      >
        <div className="relative min-h-0 overflow-hidden">
          <div
            ref={viewportRef}
            data-testid={expanded ? "agent-activity-scroll" : undefined}
            data-fade-top={fadeTop}
            data-fade-bottom={fadeBottom}
            onScroll={onScroll}
            className="mt-2.5 max-h-[min(55vh,28rem)] overflow-y-auto pr-1 scrollbar-thin scrollbar-track-transparent"
          >
            <div ref={contentRef} className="flex flex-col gap-1">
              {children}
            </div>
          </div>
          {fadeTop ? (
            <span
              data-testid="activity-scroll-fade-top"
              className="pointer-events-none absolute inset-x-0 top-1.5 z-10 h-3.5 bg-gradient-to-b from-muted/60 to-transparent"
              aria-hidden
            />
          ) : null}
          {fadeBottom ? (
            <span
              data-testid="activity-scroll-fade-bottom"
              className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-3.5 bg-gradient-to-t from-muted/60 to-transparent"
              aria-hidden
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}
