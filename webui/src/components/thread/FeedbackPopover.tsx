import { useState } from "react";
import { Anchor as PopoverAnchorPrimitive } from "@radix-ui/react-popover";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

interface FeedbackPopoverProps {
  /** Controlled open state (the plus menu opens it after closing itself). */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Receives the raw multiline draft; the caller serializes and runs the command. */
  onSubmit: (text: string) => void;
}

export function FeedbackPopover({ open, onOpenChange, onSubmit }: FeedbackPopoverProps) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    onSubmit(text);
  };

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) setDraft("");
      }}
    >
      <PopoverAnchorPrimitive asChild>
        <span aria-hidden="true" />
      </PopoverAnchorPrimitive>
      <PopoverContent
        side="top"
        align="start"
        sideOffset={8}
        className="w-[21rem] max-w-[calc(100vw-2rem)] rounded-prominent border-border/70 bg-popover p-3 shadow-[0_12px_36px_rgba(15,23,42,0.14)]"
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <label
            htmlFor="composer-feedback-input"
            className="block text-[12px] font-semibold text-foreground"
          >
            {t("plusMenu.feedback")}
          </label>
          <p className="mt-0.5 text-[11.5px] leading-snug text-muted-foreground">
            {t("plusMenu.feedbackDesc")}
          </p>
          <textarea
            id="composer-feedback-input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            rows={4}
            placeholder={t("plusMenu.feedbackPlaceholder")}
            className={cn(
              "mt-2 w-full resize-none rounded-lg border border-border/70 bg-background px-2.5 py-2",
              "text-[13px] text-foreground placeholder:text-muted-foreground/70",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
            )}
          />
          <div className="mt-2 flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 rounded-full px-3 text-[12px]"
              onClick={() => onOpenChange(false)}
            >
              {t("common.cancel", { defaultValue: "Cancel" })}
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={!draft.trim()}
              className="h-7 rounded-full px-3 text-[12px]"
            >
              {t("plusMenu.feedbackSend")}
            </Button>
          </div>
        </form>
      </PopoverContent>
    </Popover>
  );
}
