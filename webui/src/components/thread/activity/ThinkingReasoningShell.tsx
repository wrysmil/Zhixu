import { Check, CircleAlert, ChevronDown, LoaderCircle } from "lucide-react";
import { useId, type ReactNode, type Ref } from "react";

import { cn } from "@/lib/utils";

interface ThinkingReasoningShellProps {
  active: boolean;
  expanded: boolean;
  label: string;
  accessibleLabel?: string;
  duration?: string;
  announcement?: string;
  error?: boolean;
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
  accessibleLabel,
  duration,
  announcement,
  error = false,
  children,
  viewportRef,
  contentRef,
  fadeTop,
  fadeBottom,
  hasDetails = true,
  onToggle,
  onScroll,
}: ThinkingReasoningShellProps) {
  const detailsId = useId();
  const statusLabel = (
    <>
      {active ? (
        <LoaderCircle data-testid="activity-running-indicator" className={cn("h-3.5 w-3.5 shrink-0 text-muted-foreground", (!expanded || !hasDetails) && "animate-spin motion-reduce:animate-none")} aria-hidden="true" />
      ) : error ? <CircleAlert className="h-3.5 w-3.5 shrink-0 text-destructive" aria-hidden="true" /> : <Check className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
      <span className={cn("min-w-0 break-words text-[13px] font-medium leading-5", error ? "text-destructive" : "text-foreground/80")}>{label}</span>
      {duration ? <span className="shrink-0 text-[12px] font-normal leading-[18px] tabular-nums text-muted-foreground">· {duration}</span> : null}
    </>
  );
  const status = <span role="status" className="sr-only">{announcement ?? ""}</span>;
  if (!hasDetails) {
    return <div className="flex w-full flex-col" data-state={active ? "thinking" : "done"}>
      {status}
      <div className="inline-flex max-w-full flex-wrap items-center gap-2 py-1">{statusLabel}</div>
    </div>;
  }
  return (
    <div className="relative flex w-full min-w-0 flex-col" data-state={active ? "thinking" : "done"}>
      {status}
      <button
        type="button"
        data-thread-disclosure=""
        className={cn(
          "group inline-flex min-h-11 sm:min-h-8 w-full cursor-pointer flex-wrap items-center gap-2 rounded-lg bg-transparent p-0 text-left",
          "transition-colors hover:bg-foreground/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/45",

        )}
        onClick={onToggle}
        aria-expanded={expanded}
        aria-label={accessibleLabel ?? label}
        aria-controls={detailsId}
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
        id={detailsId}
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
            className="mt-2 max-h-[min(55vh,28rem)] overflow-y-auto pr-1 scrollbar-thin scrollbar-track-transparent"
          >
            <div ref={contentRef} className="flex flex-col gap-1 [&_[data-testid=activity-step]]:before:absolute [&_[data-testid=activity-step]]:before:bottom-0 [&_[data-testid=activity-step]]:before:left-[8px] [&_[data-testid=activity-step]]:before:top-5 [&_[data-testid=activity-step]]:before:border-l [&_[data-testid=activity-step]]:before:border-border">
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
