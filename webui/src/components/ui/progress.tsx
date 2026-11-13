import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type ProgressTone = "default" | "primary" | "success" | "warning" | "danger";

const TONE_FILL: Record<ProgressTone, string> = {
  default: "bg-foreground/80",
  primary: "bg-primary",
  success: "bg-emerald-500",
  warning: "bg-amber-500",
  danger: "bg-destructive",
};

/**
 * Determinate progress bar. Width follows `value` (0–100); `indeterminate`
 * turns it into a sliding sweep for work whose length is unknown.
 */
export function Progress({
  value,
  indeterminate = false,
  tone = "primary",
  className,
  label,
}: {
  value?: number;
  indeterminate?: boolean;
  tone?: ProgressTone;
  className?: string;
  label?: string;
}) {
  const clamped = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={indeterminate ? undefined : 0}
      aria-valuemax={indeterminate ? undefined : 100}
      aria-valuenow={indeterminate ? undefined : clamped}
      className={cn(
        "h-1.5 w-full overflow-hidden rounded-full bg-muted",
        className,
      )}
    >
      <div
        className={cn(
          "h-full rounded-full transition-[width] duration-300 ease-out",
          TONE_FILL[tone],
          indeterminate && "motion-progress-indeterminate w-1/3",
        )}
        style={indeterminate ? undefined : { width: `${clamped}%` }}
      />
    </div>
  );
}

/**
 * Stage stepper: the import pipeline reads as "read → submit → queue → done"
 * instead of a single opaque spinner.
 */
export function Stepper({
  steps,
  activeIndex,
  className,
  tone = "primary",
}: {
  steps: Array<{ label: string; hint?: ReactNode }>;
  /** -1 means nothing started yet; a finished step is `steps.length`. */
  activeIndex?: number;
  className?: string;
  tone?: ProgressTone;
}) {
  const active = activeIndex ?? -1;
  return (
    <ol className={cn("flex flex-col gap-1.5", className)}>
      {steps.map((step, index) => {
        const done = active > index;
        const current = active === index;
        return (
          <li
            key={step.label}
            className="flex items-center gap-2 text-xs"
            aria-current={current ? "step" : undefined}
          >
            <span
              className={cn(
                "grid h-4 w-4 shrink-0 place-items-center rounded-full border text-[9px] font-semibold transition-colors",
                done && "border-transparent bg-primary text-primary-foreground",
                current && "border-primary bg-primary/10 text-primary",
                !done && !current && "border-border bg-card text-muted-foreground",
              )}
            >
              {done ? "✓" : index + 1}
            </span>
            <span
              className={cn(
                "truncate",
                current ? "font-medium text-foreground" : done ? "text-foreground/80" : "text-muted-foreground",
              )}
            >
              {step.label}
            </span>
            {step.hint ? (
              <span className="ml-auto truncate text-[11px] text-muted-foreground">
                {step.hint}
              </span>
            ) : null}
          </li>
        );
      })}
      <li aria-hidden className="mt-1">
        <Progress
          value={active <= 0 ? 0 : Math.min(100, (active / steps.length) * 100)}
          tone={tone}
        />
      </li>
    </ol>
  );
}
