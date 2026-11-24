import { Check, ChevronDown, LoaderCircle } from "lucide-react";
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
  const rowClass = "inline-flex min-h-7 max-w-full items-center gap-1.5 text-[13px] font-medium leading-5 tabular-nums text-muted-foreground";
  const caption = (
    <>
      {active ? (
        <LoaderCircle
          data-testid="activity-running-indicator"
          className="h-3.5 w-3.5 shrink-0 animate-spin motion-reduce:animate-none"
          aria-hidden="true"
        />
      ) : !hasDetails ? <Check className="h-3.5 w-3.5 shrink-0" aria-hidden /> : null}
      <span className="min-w-0 truncate">{label}</span>
    </>
  );

  return (
    <div className="flex min-w-0 flex-col items-start" data-state={active ? "thinking" : "done"}>
      {!hasDetails ? (
        <div className={rowClass} role="status" aria-label={label}>
          {caption}
        </div>
      ) : (
        <>
          <button
            type="button"
            data-thread-disclosure=""
            className={cn(rowClass, "group rounded-control px-1 -mx-1 text-left transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring")}
            onClick={onToggle}
            aria-expanded={expanded}
            aria-label={label}
            aria-live={active ? "polite" : undefined}
          >
            {caption}
            <span className={cn("inline-flex shrink-0 transition-transform [transition-duration:220ms] ease-out motion-reduce:transition-none", expanded && "rotate-180")}>
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground transition-colors duration-200 group-hover:text-foreground motion-reduce:transition-none" aria-hidden />
            </span>
          </button>
          <div
            {...(!expanded ? { inert: "" } : {})}
            aria-hidden={!expanded}
            className={cn(
              "grid w-full min-w-0 transition-[grid-template-rows,opacity] [transition-duration:220ms] ease-out motion-reduce:transition-none",
              expanded ? "grid-rows-[1fr] opacity-100" : "pointer-events-none grid-rows-[0fr] opacity-0",
            )}
          >
            <div className="relative min-h-0 overflow-hidden">
              <div
                ref={viewportRef}
                data-testid={expanded ? "agent-activity-scroll" : undefined}
                data-fade-top={fadeTop}
                data-fade-bottom={fadeBottom}
                onScroll={onScroll}
                className="my-1 max-h-[min(55vh,28rem)] overflow-y-auto scrollbar-thin scrollbar-track-transparent"
              >
                <div ref={contentRef} className="flex min-w-0 flex-col gap-1 pr-1">
                  {children}
                </div>
              </div>
              {fadeTop && <span data-testid="activity-scroll-fade-top" className="pointer-events-none absolute inset-x-0 top-0 h-3 bg-gradient-to-b from-background to-transparent" aria-hidden />}
              {fadeBottom && <span data-testid="activity-scroll-fade-bottom" className="pointer-events-none absolute inset-x-0 bottom-0 h-3 bg-gradient-to-t from-background to-transparent" aria-hidden />}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
