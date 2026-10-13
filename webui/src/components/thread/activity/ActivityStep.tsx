import { useState, type CSSProperties, type ReactNode } from "react";
import { ChevronRight, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StreamingLabelSheen } from "@/components/MessageBubble";
import { cn } from "@/lib/utils";

export type ActivityStepTone = "neutral" | "active" | "success" | "error";

export interface ActivityStepProps {
  icon?: LucideIcon;
  marker?: ReactNode;
  label: ReactNode;
  ariaLabel?: string;
  active?: boolean;
  tone?: ActivityStepTone;
  detail?: ReactNode;
  className?: string;
  contentClassName?: string;
  labelClassName?: string;
  markerClassName?: string;
  style?: CSSProperties;
}

export function ActivityStep({
  icon: Icon,
  marker,
  label,
  ariaLabel,
  active = false,
  tone = active ? "active" : "neutral",
  detail,
  className,
  contentClassName,
  labelClassName,
  markerClassName,
  style,
}: ActivityStepProps) {
  const { t } = useTranslation();
  const [detailOpen, setDetailOpen] = useState(false);
  return (
    <div
      data-testid="activity-step"
      aria-label={ariaLabel}
      className={cn(
        "relative grid min-w-0 grid-cols-[1.125rem_minmax(0,1fr)] gap-2 py-0.5 text-[13px] leading-5",
        className,
      )}
      style={style}
    >
      <span
        className={cn(
          "flex h-5 w-[1.125rem] shrink-0 items-start justify-center pt-[3px]",
        )}
        aria-hidden
      >
        {marker ?? (
          <span
            className={cn(
              "grid h-3.5 w-3.5 place-items-center rounded-full border bg-background transition-colors",
              tone === "active" && "border-muted-foreground/28 text-muted-foreground/72",
              tone === "success" && "border-emerald-500/28 text-emerald-500/78",
              tone === "error" && "border-transparent bg-transparent text-destructive",
              tone === "neutral" && "border-muted-foreground/18 text-muted-foreground/50",
              markerClassName,
            )}
          >
            {tone === "error" && !Icon ? (
              <svg viewBox="0 0 12 12" className="h-3 w-3" aria-hidden>
                <path
                  d="M3 3 L9 9 M9 3 L3 9"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  fill="none"
                />
              </svg>
            ) : Icon ? (
              <Icon className="h-2.5 w-2.5" strokeWidth={2.15} />
            ) : null}
          </span>
        )}
      </span>
      <div className={cn("min-w-0", contentClassName)}>
        <div
          data-testid="activity-line"
          title={typeof label === "string" ? label : undefined}
          className="flex min-w-0 items-center gap-1.5 overflow-hidden whitespace-nowrap"
        >
          <StreamingLabelSheen
            active={active}
            className={cn(
              "min-w-0 flex-1 truncate font-medium",
              tone === "error" ? "text-destructive/78" : "text-muted-foreground/85",
              labelClassName,
            )}
          >
            {label}
          </StreamingLabelSheen>
          {detail ? (
            <button
              type="button"
              aria-expanded={detailOpen}
              data-testid="activity-step-details-toggle"
              onClick={() => setDetailOpen((open) => !open)}
              className={cn(
                "ml-auto inline-flex shrink-0 cursor-pointer items-center gap-0.5 appearance-none border-0 bg-transparent p-0 text-[12px] font-normal",
                "text-muted-foreground/65 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/45",
              )}
            >
              <ChevronRight
                className={cn("h-3 w-3 transition-transform duration-150", detailOpen && "rotate-90")}
                aria-hidden
              />
              {detailOpen
                ? t("thread.activity.hideDetails", { defaultValue: "Hide" })
                : t("thread.activity.viewDetails", { defaultValue: "Details" })}
            </button>
          ) : null}
        </div>
        {detail && detailOpen ? (
          <div
            data-testid="activity-step-detail"
            className={cn(
              "mt-1 max-h-64 min-w-0 overflow-y-auto whitespace-pre-wrap break-words rounded-control",
              "border border-border/50 bg-muted/35 px-3 py-2 text-[12.5px] leading-5 text-muted-foreground",
              "scrollbar-thin scrollbar-track-transparent",
            )}
          >
            {detail}
          </div>
        ) : null}
      </div>
    </div>
  );
}
