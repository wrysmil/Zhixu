import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { ChevronRight, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StreamingLabelSheen } from "@/components/MessageBubble";
import { cn } from "@/lib/utils";

export type ActivityStepTone = "neutral" | "active" | "success" | "error";

/** 状态色只落在过程徽标上：绿=已完成，红=失败；进行中保持石墨中性，避免整块界面发蓝。 */
function kickerToneClassName(tone: ActivityStepTone): string {
  if (tone === "success") {
    return "border-emerald-500/30 bg-emerald-500/[0.08] text-emerald-700 dark:border-emerald-400/30 dark:bg-emerald-400/[0.12] dark:text-emerald-300";
  }
  if (tone === "active") {
    return "border-border/70 bg-muted/55 text-foreground/75";
  }
  if (tone === "error") {
    return "border-destructive/30 bg-destructive/[0.06] text-destructive";
  }
  return "border-border/60 bg-muted/45 text-muted-foreground";
}

export interface ActivityStepProps {
  icon?: LucideIcon;
  marker?: ReactNode;
  /** Short status word shown before the label, e.g. "已思考" or "终端命令". */
  kicker?: ReactNode;
  label: ReactNode;
  ariaLabel?: string;
  active?: boolean;
  tone?: ActivityStepTone;
  detail?: ReactNode;
  /** Provide to drive the disclosure from outside (e.g. auto-expand while streaming). */
  detailExpanded?: boolean;
  onDetailExpandedChange?: (expanded: boolean) => void;
  /** Keep the detail box pinned to its newest content while streaming. */
  detailFollowBottom?: boolean;
  detailClassName?: string;
  className?: string;
  contentClassName?: string;
  labelClassName?: string;
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
  const detailBoxRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const box = detailBoxRef.current;
    if (detailFollowBottom && detailOpen && box) {
      box.scrollTop = box.scrollHeight;
    }
  }, [detailFollowBottom, detailOpen, detail, label]);
  return (
    <div
      data-testid="activity-step"
      aria-label={ariaLabel}
      className={cn(
        "relative grid min-w-0 grid-cols-[1.125rem_minmax(0,1fr)] gap-2 py-1 text-[14px] leading-6",
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
              "grid h-4 w-4 place-items-center rounded-full border bg-background transition-colors",
              tone === "active" && "border-muted-foreground/28 text-muted-foreground",
              tone === "success" && "border-emerald-500/28 text-emerald-500/78",
              tone === "error" && "border-transparent bg-transparent text-destructive",
              tone === "neutral" && "border-muted-foreground/18 text-muted-foreground",
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
              <Icon className="h-3 w-3" strokeWidth={2.15} />
            ) : null}
          </span>
        )}
      </span>
      <div className={cn("min-w-0", contentClassName)}>
        <div
          data-testid="activity-line"
          title={typeof label === "string" ? label : undefined}
          className="flex min-w-0 items-start gap-2"
        >
          {kicker ? (
            <span
              data-testid="activity-kicker"
              data-tone={tone}
              className={cn(
                "mt-[3px] shrink-0 rounded-[5px] border px-1.5 text-[11px] font-medium leading-4",
                kickerToneClassName(tone),
              )}
            >
              {kicker}
            </span>
          ) : null}
          {active ? (
            <StreamingLabelSheen
              active
              className={cn("min-w-0 flex-1 font-medium text-foreground/90", labelClassName)}
            >
              {label}
            </StreamingLabelSheen>
          ) : (
            <span className={cn(
              "min-w-0 flex-1 break-words font-medium",
              tone === "error" ? "text-destructive" : "text-foreground/80",
              labelClassName,
            )}>
              {label}
            </span>
          )}
          {detail ? (
            <button
              type="button"
              aria-expanded={detailOpen}
              data-testid="activity-step-details-toggle"
              onClick={() => setDetailOpen(!detailOpen)}
              className={cn(
                "ml-auto inline-flex min-h-6 shrink-0 cursor-pointer items-center gap-0.5 appearance-none border-0 bg-transparent p-0 text-[12px] font-normal",
                "text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/45",
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
            ref={detailBoxRef}
            className={cn(
              "mt-2 max-h-96 min-w-0 overflow-y-auto whitespace-pre-wrap break-words rounded-control",
              "border border-border/60 bg-background/60 px-3 py-3 text-[14px] leading-6 text-foreground/85",
              "scrollbar-thin scrollbar-track-transparent",
              detailClassName,
            )}
          >
            {detail}
          </div>
        ) : null}
      </div>
    </div>
  );
}
