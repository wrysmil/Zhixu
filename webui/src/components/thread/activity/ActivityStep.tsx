import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { ChevronRight, LoaderCircle, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

export type ActivityStepTone = "neutral" | "active" | "success" | "error";

/** 仅错误使用语义色，运行与完成采用中性文字。 */
function kickerToneClassName(tone: ActivityStepTone): string {
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
  previewMeta?: ReactNode;
  previewTitle?: string;
  /** Visible failure summary, kept outside truncated previews. */
  errorSummary?: ReactNode;
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
  previewMeta,
  previewTitle,
  errorSummary,
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
  const detailId = useId();
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
        "relative grid min-w-0 grid-cols-[1.125rem_minmax(0,1fr)] gap-2 py-1 text-[13px] font-normal leading-5",
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
        {marker ?? (active ? <LoaderCircle className="h-3.5 w-3.5 animate-spin text-muted-foreground motion-reduce:animate-none" aria-hidden="true" /> : (
          <span
            className={cn(
              "grid h-3.5 w-3.5 place-items-center rounded-full border bg-background transition-colors",
              tone === "active" && "border-muted-foreground/28 text-muted-foreground/72",
              tone === "success" && "border-border text-muted-foreground",
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
        ))}
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
          <div className="min-w-0 flex-1">
            <span className={cn("block break-words [overflow-wrap:anywhere] font-normal", detail && !detailOpen && "line-clamp-2", active && "font-medium", tone === "error" ? "text-destructive" : "text-foreground/80", labelClassName)}>{label}</span>
            {previewMeta ? <span title={previewTitle} className="block line-clamp-2 break-words [overflow-wrap:anywhere] font-mono text-[12px] leading-[18px] text-muted-foreground">{previewMeta}</span> : null}
            {errorSummary ? (
              <p className="break-words [overflow-wrap:anywhere] text-[12px] font-normal leading-[18px] text-destructive">
                {errorSummary}
              </p>
            ) : null}
          </div>
          {detail ? (
            <button
              type="button"
              aria-expanded={detailOpen}
              aria-controls={detailId}
              data-testid="activity-step-details-toggle"
              onClick={(event) => {
                const open = !detailOpen;
                setDetailOpen(open);
                if (open) event.currentTarget.dispatchEvent(new Event("activity-detail-open", { bubbles: true }));
              }}
              className={cn(
                "ml-auto inline-flex min-h-11 sm:min-h-8 shrink-0 cursor-pointer items-center gap-0.5 appearance-none border-0 bg-transparent p-0 text-[12px] font-normal",
                "text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/45",
              )}
            >
              <ChevronRight
                className={cn("h-3 w-3 transition-transform duration-200 motion-reduce:transition-none", detailOpen && "rotate-90")}
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
            id={detailId}
            data-testid="activity-step-detail"
            ref={detailBoxRef}
            className={cn(
              "mt-2 min-w-0 whitespace-pre-wrap break-words [overflow-wrap:anywhere]",
              "border-l border-border pl-3 py-1 text-[13px] font-normal leading-5 text-foreground/85 [&_code]:text-[12px] [&_code]:leading-[18px] [&_pre]:text-[12px] [&_pre]:leading-[18px]",
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
