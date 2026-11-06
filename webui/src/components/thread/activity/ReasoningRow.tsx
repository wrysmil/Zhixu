import { useEffect, useRef, useState } from "react";
import { Check, CircleDashed } from "lucide-react";
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
  const detail = text.trim() ? text : undefined;
  const [expanded, setExpanded] = useState(false);
  const userToggledRef = useRef(false);

  useEffect(() => {
    if (userToggledRef.current) return;
    if (!streaming) setExpanded(false);
  }, [streaming]);

  return (
    <ActivityStep
      marker={<ReasoningMarker streaming={streaming} />}
      kicker={kicker}
      active={streaming}
      tone={streaming ? "active" : "success"}
      label={expanded ? fallback : preview}
      labelClassName="text-foreground/85"
      contentClassName="overflow-hidden"
      detail={detail}
      detailClassName="max-h-none overflow-visible rounded-none border-0 border-l border-border bg-transparent py-1 pl-3 pr-0"
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
      <CircleDashed
        data-testid="activity-reasoning-marker"
        data-state="thinking"
        className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground motion-reduce:animate-none"
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
        "grid h-3.5 w-3.5 shrink-0 place-items-center rounded-full border border-border text-muted-foreground",
        "bg-background transition-[border-color,background-color,box-shadow,transform] duration-300 ease-out",
        justCompleted
          && "animate-in fade-in-0 zoom-in-75 motion-reduce:animate-none",
      )}
      aria-hidden
    >
      <Check
        className={cn(
          "h-2.5 w-2.5 stroke-[2.4]",
          justCompleted && "animate-in fade-in-0 zoom-in-50 duration-300 motion-reduce:animate-none",
        )}
      />
    </span>
  );
}
