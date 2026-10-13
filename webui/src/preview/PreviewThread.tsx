import { useCallback, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { MessageBubble } from "@/components/MessageBubble";
import { ThreadFrame } from "@/components/thread/ThreadFrame";
import { ThreadComposer } from "@/components/thread/ThreadComposer";
import type { ModelPresetOption } from "@/components/thread/ModelPresetBadge";
import type { SendAttachment } from "@/hooks/useNanobotStream";
import type { UIMessage, WorkspaceScopePayload } from "@/lib/types";
import { PREVIEW_DEFAULT_SCOPE } from "@/preview/fixtures";

const DEMO_MESSAGES: UIMessage[] = [
  { id: "demo-user-1", role: "user", content: "帮我把这周的记忆整理成一篇周回顾。", createdAt: 1 },
  {
    id: "demo-assistant-1", role: "assistant",
    content: "好的，我先按主题聚合本周记忆，再给出正文结构：\n\n## 本周回顾\n\n- **进展**：完成导航与创作页调整\n- **待办**：整理本周的阅读笔记\n\n（界面预览：以上为固定示例内容，不会连接模型或后端。）",
    createdAt: 2,
  },
];

const DEMO_REPLY = "（演示回复）已根据示例素材生成内容。界面预览模式下不会真正调用模型，也不会发送任何业务请求。";
const PREVIEW_MODELS: ModelPresetOption[] = [
  { name: "MiniMax M2.7", model: "MiniMax-M2.7", provider: "minimax" },
  { name: "GPT-5.5", model: "gpt-5.5", provider: "openai" },
];
const PREVIEW_SKILLS = [
  { name: "github", description: "GitHub repositories and pull requests", source: "builtin", available: true },
  { name: "cron", description: "Scheduled tasks", source: "builtin", available: true },
];

/** Uses the production composer and message renderer with local-only fixtures.
 * Models, workspace scope, attachments and menus match the real UI; send never
 * creates a runtime or calls a model. */
export function PreviewThread({
  chatKey,
  workspaceScope = PREVIEW_DEFAULT_SCOPE,
  onWorkspaceScopeChange,
  onManageModels,
}: {
  chatKey?: string | null;
  workspaceScope?: WorkspaceScopePayload;
  onWorkspaceScopeChange?: (scope: WorkspaceScopePayload) => void;
  onManageModels?: () => void;
}) {
  const { t } = useTranslation();
  const [messages, setMessages] = useState<UIMessage[]>(() => chatKey ? DEMO_MESSAGES : []);
  const [modelPreset, setModelPreset] = useState(PREVIEW_MODELS[0].name);
  const [reasoningEffort, setReasoningEffort] = useState("medium");
  const [localScope, setLocalScope] = useState(workspaceScope);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const activeModel = PREVIEW_MODELS.find((model) => model.name === modelPreset) ?? PREVIEW_MODELS[0];

  const send = useCallback((content: string, attachments?: SendAttachment[]) => {
    const trimmed = content.trim();
    if (!trimmed && !attachments?.length) return false;
    const now = Date.now();
    setMessages((current) => [
      ...current,
      { id: `demo-user-${now}`, role: "user", content: trimmed, media: attachments?.map((attachment) => attachment.preview), createdAt: now },
      { id: `demo-assistant-${now}`, role: "assistant", content: DEMO_REPLY, createdAt: now + 1, turnPhase: "answer" },
    ]);
    requestAnimationFrame(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }));
    return true;
  }, []);

  const header = useMemo(() => (
    <div className="flex h-14 items-center gap-2 px-4">
      <h1 className="min-w-0 flex-1 truncate text-[15px] font-semibold text-foreground">
        {t("workspace.nav.assistant", { defaultValue: "助手" })}
      </h1>
      <span className="rounded-mark bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
        {t("workspace.preview.badge")}
      </span>
    </div>
  ), [t]);

  const composer = (
    <div className="w-full px-4 py-4 sm:px-6">
      <ThreadComposer
        onSend={send}
        inputAriaLabel={t("thread.composer.inputAria")}
        placeholder={t(messages.length ? "thread.composer.placeholderThread" : "thread.composer.placeholderHero")}
        variant={messages.length ? "thread" : "hero"}
        modelLabel={activeModel.name}
        modelDetail={activeModel.model}
        modelPreset={modelPreset}
        modelPresets={PREVIEW_MODELS}
        modelProvider={activeModel.provider}
        onModelPresetChange={setModelPreset}
        modelReasoningEffort={activeModel.provider === "openai" ? reasoningEffort : null}
        modelReasoningEffortValues={activeModel.provider === "openai" ? ["low", "medium", "high"] : null}
        onReasoningEffortChange={setReasoningEffort}
        onManageModels={onManageModels}
        skills={PREVIEW_SKILLS}
        workspaceScope={onWorkspaceScopeChange ? workspaceScope : localScope}
        workspaceDefaultScope={PREVIEW_DEFAULT_SCOPE}
        workspaceControls={{ can_change_project: true, can_use_full_access: true, can_pick_folder: false }}
        onWorkspaceScopeChange={onWorkspaceScopeChange ?? setLocalScope}
      />
    </div>
  );

  if (messages.length === 0) {
    return (
      <div className="flex h-full min-w-0 flex-col bg-background">
        <header className="shrink-0 border-b border-border/70">{header}</header>
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto pb-16">
          <h2 className="mb-5 px-6 text-center text-[26px] font-semibold tracking-tight text-foreground">
            {t("thread.empty.greetings.workOn")}
          </h2>
          {composer}
        </div>
      </div>
    );
  }

  return (
    <ThreadFrame header={header} composer={composer}>
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-6">
        <div className="mx-auto flex w-full max-w-[800px] flex-col gap-5">
          {messages.map((message) => <MessageBubble key={message.id} message={message} showCopyAction={false} />)}
        </div>
      </div>
    </ThreadFrame>
  );
}
