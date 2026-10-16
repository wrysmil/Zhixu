import { useEffect, useId, useRef, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Maximize2, Minimize2 } from "lucide-react";

import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import type { AgentProfile } from "@/lib/agents/types";
import { PROMPT_MAX_LENGTH } from "@/lib/agents/types";
import { cn } from "@/lib/utils";

type PromptProps = {
  profile: AgentProfile;
  onChange: (prompt: string) => void;
};

const PILL_CLASS =
  "inline-flex h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-control bg-muted/65 px-3 text-[12px] font-medium text-muted-foreground transition-colors duration-150 [transition-timing-function:cubic-bezier(.2,.8,.2,1)] hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none";

/**
 * 提示词输入框。高度随内容自动增长（把 `scrollHeight` 反写回 `style.height`），
 * 到 `maxHeightPx` 封顶后转为内部滚动 —— 长提示词不必顶着一块小框看。
 *
 * 刻意是纯 markdown 文本域：变量直接手写 `{{name}}` 即可，预览那侧会替换。
 * 之前那套「插入变量」下拉把简单的一件事做成了两步，反而挡手。
 */
function PromptTextarea({
  profile,
  onChange,
  textareaRef,
  maxHeightPx,
  expanded = false,
  descriptionId,
}: PromptProps & {
  textareaRef: RefObject<HTMLTextAreaElement>;
  maxHeightPx: number;
  expanded?: boolean;
  descriptionId: string;
}) {
  const { t } = useTranslation();

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    // 先归零再量，否则删字后高度只增不减。
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [profile.prompt, textareaRef]);

  return (
    <Textarea
      ref={textareaRef}
      value={profile.prompt}
      onChange={(event) => onChange(event.target.value)}
      placeholder={t(
        "settings.agents.prompt.placeholder",
        "用 markdown 写这个 Agent 的职责、边界与输出风格。",
      )}
      aria-label={t("settings.agents.prompt.label", "系统提示词")}
      aria-describedby={descriptionId}
      aria-invalid={profile.prompt.length > PROMPT_MAX_LENGTH}
      spellCheck={false}
      style={{ maxHeight: `${maxHeightPx}px` }}
      className={cn(
        "resize-none overflow-y-auto rounded-xl border-border/75 bg-background/80 p-4 font-mono text-[13px] leading-7 shadow-none scrollbar-thin focus-visible:border-emerald-700/60 focus-visible:ring-emerald-700/20",
        expanded ? "min-h-[60dvh]" : "min-h-[320px]",
      )}
    />
  );
}

function LengthCounter({ profile }: { profile: AgentProfile }) {
  const over = profile.prompt.length > PROMPT_MAX_LENGTH;
  return (
    <span
      className={cn(
        "shrink-0 text-[12px] tabular-nums",
        over ? "font-medium text-destructive" : "text-muted-foreground",
      )}
    >
      {profile.prompt.length} / {PROMPT_MAX_LENGTH}
    </span>
  );
}

export function PromptSection({ profile, onChange }: PromptProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const expandedRef = useRef<HTMLTextAreaElement>(null);
  const hintId = useId();
  const expandedHintId = useId();
  const overLimit = profile.prompt.length > PROMPT_MAX_LENGTH;
  const hint = overLimit
    ? t("settings.agents.workspace.promptLimit", {
      count: PROMPT_MAX_LENGTH,
      defaultValue: "Instructions must be within {{count}} characters.",
    })
    : t(
      "settings.agents.workspace.promptHint",
      "Describe its responsibilities, how it should work, and what it should deliver.",
    );

  return (
    <Dialog open={expanded} onOpenChange={setExpanded}>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <p
            id={hintId}
            className={cn("min-w-0 flex-1 text-[12px] leading-5", overLimit ? "text-destructive" : "text-muted-foreground")}
          >
            {hint}
          </p>
          <DialogPrimitive.Trigger asChild>
            <button type="button" className={PILL_CLASS}>
              <Maximize2 className="h-3.5 w-3.5" aria-hidden />
              {t("settings.agents.prompt.expand", "展开编辑")}
            </button>
          </DialogPrimitive.Trigger>
        </div>
        {!expanded ? (
          <PromptTextarea
            profile={profile}
            onChange={onChange}
            textareaRef={textareaRef}
            maxHeightPx={520}
            descriptionId={hintId}
          />
        ) : null}
        <div className="flex justify-end"><LengthCounter profile={profile} /></div>
      </div>
      <DialogContent
        showCloseButton={false}
        className="flex max-h-[92dvh] w-full max-w-[1000px] flex-col gap-0 overflow-hidden p-0 text-left"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          expandedRef.current?.focus();
        }}
        onEscapeKeyDown={(event) => {
          event.preventDefault();
          event.stopPropagation();
          setExpanded(false);
        }}
      >
        <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border/60 px-5 py-4">
          <DialogTitle className="text-base">
            {t("settings.agents.prompt.expandedTitle", "编辑提示词")}
          </DialogTitle>
          <div className="ml-auto"><LengthCounter profile={profile} /></div>
          <DialogPrimitive.Close asChild>
            <button type="button" className={PILL_CLASS}>
              <Minimize2 className="h-3.5 w-3.5" aria-hidden />
              {t("settings.agents.prompt.collapse", "完成")}
            </button>
          </DialogPrimitive.Close>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <DialogDescription
            className={cn("mb-3 text-xs leading-5", overLimit && "text-destructive")}
          >
            <span id={expandedHintId}>{hint}</span>
          </DialogDescription>
          <PromptTextarea
            profile={profile}
            onChange={onChange}
            textareaRef={expandedRef}
            maxHeightPx={700}
            expanded
            descriptionId={expandedHintId}
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}
