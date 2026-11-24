import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Check, ChevronRight, CircleAlert, LoaderCircle, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

export type ActivityStepTone = "neutral" | "active" | "success" | "error";

const toolStates = {
  active: { icon: LoaderCircle, key: "running", style: "animate-spin motion-reduce:animate-none text-muted-foreground" },
  success: { icon: Check, key: "succeeded", style: "text-muted-foreground" },
  error: { icon: CircleAlert, key: "failed", style: "text-destructive" },
} as const;

export interface ActivityStepProps {
  icon?: LucideIcon;
  marker?: ReactNode;
  kicker?: ReactNode;
  label: ReactNode;
  ariaLabel?: string;
  active?: boolean;
  tone?: ActivityStepTone;
  detail?: ReactNode;
  detailExpanded?: boolean;
  onDetailExpandedChange?: (expanded: boolean) => void;
  detailFollowBottom?: boolean;
  detailClassName?: string;
  className?: string;
  contentClassName?: string;
  labelClassName?: string;
  labelSingleLine?: boolean;
  markerClassName?: string;
  style?: CSSProperties;
}

export function ActivityStep({
  icon: Icon,
  marker,
  kicker,
  label,
  ariaLabel,
  active = false,
  tone = active ? "active" : "neutral",
  detail,
  detailExpanded,
  onDetailExpandedChange,
  detailFollowBottom = false,
  detailClassName,
  className,
  contentClassName,
  labelClassName,
  labelSingleLine = false,
  markerClassName,
  style,
}: ActivityStepProps) {
  const { t } = useTranslation();
  const [internalDetailOpen, setInternalDetailOpen] = useState(false);
  const detailOpen = detailExpanded ?? internalDetailOpen;
  const setDetailOpen = (open: boolean) => {
    setInternalDetailOpen(open);
    onDetailExpandedChange?.(open);
  };
  const toggleLabel = detailOpen ? t("thread.activity.hideDetails") : t("thread.activity.viewDetails");
  const state = tone === "neutral" ? null : toolStates[tone];
  const statusLabel = state ? t(`thread.subagentExecution.activityStatus.${state.key}`) : undefined;
  const detailBoxRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const box = detailBoxRef.current;
    if (detailFollowBottom && detailOpen && box) box.scrollTop = box.scrollHeight;
  }, [detailFollowBottom, detailOpen, detail, label]);

  return (
    <div
      data-testid="activity-step"
      aria-label={ariaLabel}
      className={cn("grid min-w-0 grid-cols-[1.125rem_minmax(0,1fr)] gap-2 py-1 text-[13px] leading-6", className)}
      style={style}
    >
      <span className="flex h-6 items-center justify-center text-muted-foreground" aria-hidden>
        {marker ?? (Icon && <Icon className={cn("h-3.5 w-3.5", markerClassName)} strokeWidth={1.8} />)}
      </span>
      <div className={cn("min-w-0", contentClassName)}>
        <div data-testid="activity-line" title={typeof label === "string" ? label : undefined} className="flex min-w-0 items-start gap-1.5">
          {kicker && (
            <span data-testid="activity-kicker" data-tone={tone} className="shrink-0 text-xs leading-6 text-muted-foreground">
              {kicker}
            </span>
          )}
          <span className={cn("min-w-0 flex-1 break-words text-foreground/85", labelSingleLine && "truncate", labelClassName)}>
            {label}
          </span>
          {state && (
            <span role="img" aria-label={statusLabel} title={statusLabel} className="inline-flex h-6 shrink-0 items-center">
              <state.icon className={cn("h-3.5 w-3.5", state.style)} aria-hidden />
            </span>
          )}
          {detail && (
            <button
              type="button"
              aria-label={toggleLabel}
              aria-expanded={detailOpen}
              data-testid="activity-step-details-toggle"
              onClick={() => setDetailOpen(!detailOpen)}
              className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-control text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ChevronRight className={cn("h-3.5 w-3.5 transition-transform motion-reduce:transition-none", detailOpen && "rotate-90")} aria-hidden />
            </button>
          )}
        </div>
        {detail && detailOpen ? (
          <div
            data-testid="activity-step-detail"
            ref={detailBoxRef}
            className={cn("mt-1 max-h-96 min-w-0 overflow-y-auto whitespace-pre-wrap border-l border-border pl-3 text-[13px] leading-relaxed text-muted-foreground [overflow-wrap:anywhere] scrollbar-thin scrollbar-track-transparent", detailClassName)}
          >
            {detail}
          </div>
        ) : null}
      </div>
    </div>
  );
}
