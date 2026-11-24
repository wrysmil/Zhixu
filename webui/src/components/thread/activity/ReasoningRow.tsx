import { useEffect, useRef, useState } from "react";
import { Check, Ellipsis } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

import { ActivityStep } from "./ActivityStep";
import { compactReasoningPreview } from "./reasoning-preview";

export function ReasoningRow({
  text,
  streaming,
  className,
}: {
  text: string;
  streaming: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const fallback = streaming
    ? t("message.reasoningStreaming", { defaultValue: "Thinking…" })
    : t("message.reasoning", { defaultValue: "Thinking" });
  const kicker = streaming
    ? t("message.reasoningActive", { defaultValue: "Reasoning" })
    : t("message.reasoningDone", { defaultValue: "Thought" });
  const preview = compactReasoningPreview(text) || fallback;
  const collapsedPreview = preview.length > 200 ? `${preview.slice(0, 200)}…` : preview;
  const detail = text.trim() ? text : undefined;
  const [expanded, setExpanded] = useState(streaming);
  const userToggledRef = useRef(false);

  useEffect(() => {
    if (userToggledRef.current) return;
    setExpanded(streaming);
  }, [streaming]);

  return (
    <ActivityStep
      marker={<ReasoningMarker streaming={streaming} />}
      label={expanded || !detail ? fallback : collapsedPreview}
      ariaLabel={kicker}
      labelSingleLine
      labelClassName="text-[13px] font-normal leading-6 text-muted-foreground"
      detail={detail}
      detailClassName="mt-1 max-h-none overflow-visible rounded-none border-0 border-l border-border bg-transparent py-0 pl-3 pr-0 text-[13px] leading-[1.65] text-foreground/75 [overflow-wrap:anywhere]"
      detailExpanded={detail ? expanded : undefined}
      detailFollowBottom={streaming}
      onDetailExpandedChange={
        detail
          ? (open) => {
            userToggledRef.current = true;
            setExpanded(open);
          }
          : undefined
      }
      className={className}
    />
  );
}

function ReasoningMarker({ streaming }: { streaming: boolean }) {
  const wasStreamingRef = useRef(streaming);
  const [justCompleted, setJustCompleted] = useState(false);

  useEffect(() => {
    if (wasStreamingRef.current && !streaming) {
      setJustCompleted(true);
      const timeout = window.setTimeout(() => setJustCompleted(false), 300);
      wasStreamingRef.current = streaming;
      return () => window.clearTimeout(timeout);
    }
    wasStreamingRef.current = streaming;
    return undefined;
  }, [streaming]);

  if (streaming) {
    return (
      <Ellipsis
        data-testid="activity-reasoning-marker"
        data-state="thinking"
        className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
        strokeWidth={1.8}
        aria-hidden
      />
    );
  }
  return (
    <span
      data-testid="activity-reasoning-marker"
      data-state="done"
      className={cn(
        "grid h-3.5 w-3.5 shrink-0 place-items-center text-muted-foreground",
        justCompleted && "animate-in fade-in-0 duration-200 motion-reduce:animate-none",
      )}
      aria-hidden
    >
      <Check className="h-3.5 w-3.5" strokeWidth={1.8} />
    </span>
  );
}
