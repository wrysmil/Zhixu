// 创作空间右栏：真实团队会话。
//
// 会话身份由服务端 `bind_writing_session` 发放（文章 ↔ chat_id/session_key 一一对应），
// 面板不自己拼 chatId。消息通过既有 `useNanobotStream` 发送，附带服务端校验的
// `writing` 元数据（版本 + digest + 可选成员/任务/选区）；选择菜单只填输入框，不直接运行。
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { useTranslation } from "react-i18next";
import {
  AlertCircle,
  AtSign,
  BookOpen,
  ListTree,
  Loader2,
  MessageSquare,
  MoreHorizontal,
  PanelRightClose,
  Send,
  Square,
  X,
} from "lucide-react";
import type { TFunction } from "i18next";

import { ModelPresetBadge, type ModelPresetOption } from "@/components/thread/ModelPresetBadge";
import { ThreadMessages } from "@/components/thread/ThreadMessages";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useOptionalClient } from "@/providers/ClientProvider";
import { useNanobotStream } from "@/hooks/useNanobotStream";
import { useSessionHistory } from "@/hooks/useSessions";
import { loadTeamDetail } from "@/lib/teams/api";
import { workspaceRouteHash } from "@/workspace/routes";
import type { ArticleSnapshotPayload } from "@/lib/writing-api";
import type { StreamError } from "@/lib/nanobot-client";
import type {
  UIMessage,
  WritingSelectionContext,
  WritingSessionInfo,
  WritingTask,
} from "@/lib/types";
import { cn } from "@/lib/utils";

export interface WritingChatSeed {
  id: number;
  text: string;
  task?: WritingTask | null;
  memberId?: string | null;
  selection?: WritingSelectionContext | null;
}

export interface WritingChatPanelProps {
  articleId: string;
  articleSnapshot: ArticleSnapshotPayload;
  flushForWriting: () => Promise<{ revision: number; digest: string } | null>;
  seed?: WritingChatSeed | null;
  onSeedConsumed?: () => void;
  onSessionChange?: (session: WritingSessionInfo | null) => void;
  /** 正文每次编辑递增；用于清掉已经和正文对不上的未发送选区。 */
  articleEditSeq?: number;
  modelPresets: ModelPresetOption[];
  modelPreset: string | null;
  modelDetail: string | null;
  modelProvider: string | null;
  modelProviderLabel: string | null;
  modelPresetFromFallback: boolean;
  modelNeedsSetup: boolean;
  onManageModels: () => void;
  onModelRefresh: () => Promise<string | null>;
  onCollapse?: () => void;
}

type ChatMember = {
  id: string;
  name: string;
  role: string;
  leader: boolean;
};

type ContextScope = "full" | "selection" | "outline" | "materials";

type MenuState = {
  kind: "slash" | "mention";
  start: number;
  cursor: number;
  query: string;
  active: number;
};

type MenuItem =
  | { kind: "slash"; command: SlashCommand }
  | { kind: "member"; member: ChatMember }
  | { kind: "context"; scope: ContextScope; label: string; enabled: boolean; hint?: string };

type MentionItem = Extract<MenuItem, { kind: "member" | "context" }>;

interface SlashCommand {
  name: string;
  aliases: string[];
  label: string;
  handler: string;
  task: WritingTask;
  memberId: string | null;
  template: string;
}

const SLASH_COMMANDS: SlashCommand[] = [
  {
    name: "构思",
    aliases: ["idea", "ideate"],
    label: "找选题角度",
    handler: "主编",
    task: "ask",
    memberId: null,
    template: "先帮我找三个值得展开的写作角度。",
  },
  {
    name: "素材",
    aliases: ["research", "material"],
    label: "查资料并整理素材",
    handler: "研究员",
    task: "material",
    memberId: "writing-researcher",
    template: "补充三个可靠的资料，并整理成文章素材。",
  },
  {
    name: "提纲",
    aliases: ["outline"],
    label: "创建或调整提纲",
    handler: "结构编辑",
    task: "outline",
    memberId: "writing-architect",
    template: "根据当前文章创建一份结构清晰的提纲。",
  },
  {
    name: "写作",
    aliases: ["draft", "write"],
    label: "写正文或续写",
    handler: "写手",
    task: "draft",
    memberId: "writing-author",
    template: "按照当前提纲写一个初稿，表达自然，保留资料出处，不确定的地方标出来。",
  },
  {
    name: "改写",
    aliases: ["rewrite"],
    label: "修改选中内容",
    handler: "写手",
    task: "rewrite",
    memberId: "writing-author",
    template: "把选中的内容改得更清楚，保留原意和术语。",
  },
  {
    name: "审校",
    aliases: ["review"],
    label: "检查事实与表达",
    handler: "审校",
    task: "ask",
    memberId: "writing-reviewer",
    template: "帮我审阅这篇文章，重点看逻辑、重复表达和缺少证据的地方，先给修改建议。",
  },
];

const TASK_LABELS: Record<WritingTask, string> = {
  ask: "问答",
  rewrite: "改写",
  expand: "扩写",
  condense: "精简",
  outline: "提纲",
  material: "素材",
  draft: "写作",
};

const CONTEXT_LABELS: Record<ContextScope, string> = {
  full: "全文",
  selection: "当前选区",
  outline: "提纲",
  materials: "已选素材",
};

function placeholderPresent(text: string): boolean {
  return text.includes("【主题】") || text.includes("【读者】");
}

function memberRole(member: ChatMember): string {
  const role = member.role.split(/[：:]/)[0]?.trim();
  return role || (member.leader ? "主编" : "成员");
}

function membersFromSnapshot(
  snapshot: {
    leader: { id: string; name: string };
    members: Array<{
      member: { memberId: string; role: string };
      profile: { name: string };
    }>;
  },
): ChatMember[] {
  const leader: ChatMember = {
    id: snapshot.leader.id,
    name: snapshot.leader.name || "主编",
    role: "主编",
    leader: true,
  };
  const members = snapshot.members.map((entry) => ({
    id: entry.member.memberId,
    name: entry.profile.name || entry.member.memberId,
    role: entry.member.role || "成员",
    leader: false,
  }));
  return [leader, ...members];
}

function detectTrigger(value: string, cursor: number): Omit<MenuState, "active"> | null {
  const before = value.slice(0, cursor);
  const slash = /^\/([^\s/]*)$/.exec(before);
  if (slash) {
    return { kind: "slash", start: 0, cursor, query: slash[1] ?? "" };
  }
  const mention = /(^|\s)@([^\s@]*)$/.exec(before);
  if (mention) {
    const query = mention[2] ?? "";
    return {
      kind: "mention",
      start: cursor - query.length - 1,
      cursor,
      query,
    };
  }
  return null;
}

function contextPreamble(
  scope: ContextScope | null,
  snapshot: ArticleSnapshotPayload,
): string {
  if (scope === "outline") {
    const lines = snapshot.outline
      .slice()
      .sort((a, b) => a.order - b.order)
      .map((item, index) => `${index + 1}. ${item.text}`);
    if (lines.length > 0) return `【引用当前提纲】\n${lines.join("\n")}\n\n`;
  }
  if (scope === "materials") {
    const lines = snapshot.materials
      .filter((item) => item.included)
      .map((item) => `- ${item.title}：${item.content}`.trim());
    if (lines.length > 0) return `【引用已选素材】\n${lines.join("\n")}\n\n`;
  }
  return "";
}

function streamErrorMessage(error: StreamError): string {
  switch (error.kind) {
    case "message_too_big":
      return "消息过长，服务端已拒绝。请缩短后重试。";
    case "workspace_scope_rejected":
      return error.reason
        ? `工作区范围不允许：${error.reason}`
        : "工作区范围不允许这次请求。";
    case "turn_rejected":
      return error.detail || error.reason || "本轮请求被服务端拒绝。";
    case "model_request_failed":
      return "模型请求失败，请检查模型配置后重试。";
    default:
      return "会话连接出现问题，请重试。";
  }
}

export function WritingChatPanel({
  articleId,
  articleSnapshot,
  flushForWriting,
  seed = null,
  onSeedConsumed,
  onSessionChange,
  articleEditSeq = 0,
  modelPresets,
  modelPreset,
  modelDetail,
  modelProvider,
  modelProviderLabel,
  modelPresetFromFallback,
  modelNeedsSetup,
  onManageModels,
  onModelRefresh,
  onCollapse,
}: WritingChatPanelProps) {
  const { t } = useTranslation();
  const context = useOptionalClient();
  const client = context?.client ?? null;
  const getToken = context?.getToken ?? (() => "");
  const [session, setSession] = useState<WritingSessionInfo | null>(null);
  const [bindError, setBindError] = useState<string | null>(null);
  const [roster, setRoster] = useState<ChatMember[] | null>(null);
  const [rosterError, setRosterError] = useState(false);
  const onSessionChangeRef = useRef(onSessionChange);
  onSessionChangeRef.current = onSessionChange;

  useEffect(() => {
    let active = true;
    setSession(null);
    setBindError(null);
    if (!client) {
      setBindError("未连接网关");
      return undefined;
    }
    client
      .bindWritingSession(articleId)
      .then((info) => {
        if (!active) return;
        setSession(info);
        onSessionChangeRef.current?.(info);
      })
      .catch((error: unknown) => {
        if (!active) return;
        const message = error instanceof Error ? error.message : "writing_session_unavailable";
        setBindError(message);
      });
    return () => {
      active = false;
      client.unbindWritingSession(articleId);
      onSessionChangeRef.current?.(null);
    };
  }, [articleId, client]);

  useEffect(() => {
    if (!session) return undefined;
    let active = true;
    setRoster(null);
    setRosterError(false);
    loadTeamDetail(getToken(), session.team_id, { revision: session.team_revision })
      .then((detail) => {
        if (!active) return;
        setRoster(membersFromSnapshot(detail.snapshot));
      })
      .catch(() => {
        if (!active) return;
        setRoster(null);
        setRosterError(true);
      });
    return () => {
      active = false;
    };
  }, [getToken, session]);

  if (bindError) {
    return (
      <aside
        data-testid="writing-chat-panel"
        aria-label={t("creative.chat.title", { defaultValue: "创作团队" })}
        className="flex h-full min-h-0 w-full flex-col bg-background"
      >
        <header className="flex h-[52px] shrink-0 items-center border-b border-border px-3">
          <h2 className="text-[13px] font-semibold text-foreground">
            {t("creative.chat.title", { defaultValue: "创作团队" })}
          </h2>
        </header>
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
          <AlertCircle className="h-5 w-5 text-destructive" aria-hidden />
          <p role="alert" className="text-[12px] leading-relaxed text-muted-foreground">
            {t("creative.chat.bindFailed", {
              defaultValue: "这篇文章的创作会话暂时不可用。",
            })}
          </p>
          <p className="text-[10.5px] text-muted-foreground/80">{bindError}</p>
          <button
            type="button"
            className="rounded-control border border-border px-2.5 py-1 text-[11.5px] hover:bg-accent"
            onClick={() => {
              if (!client) return;
              setBindError(null);
              // 重新触发绑定 effect：先置空再下一帧恢复。
              setSession(null);
              client
                .bindWritingSession(articleId)
                .then((info) => {
                  setSession(info);
                  onSessionChangeRef.current?.(info);
                })
                .catch((error: unknown) => {
                  setBindError(error instanceof Error ? error.message : "writing_session_unavailable");
                });
            }}
          >
            {t("creative.chat.retry", { defaultValue: "重试" })}
          </button>
        </div>
      </aside>
    );
  }

  if (!session) {
    return (
      <aside
        data-testid="writing-chat-panel"
        aria-label={t("creative.chat.title", { defaultValue: "创作团队" })}
        className="flex h-full min-h-0 w-full flex-col bg-background"
      >
        <div className="flex min-h-0 flex-1 items-center justify-center gap-2 text-[12px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          {t("creative.chat.connecting", { defaultValue: "正在连接创作团队…" })}
        </div>
      </aside>
    );
  }

  return (
    <WritingConversation
      session={session}
      articleSnapshot={articleSnapshot}
      flushForWriting={flushForWriting}
      seed={seed}
      onSeedConsumed={onSeedConsumed}
      roster={roster}
      rosterError={rosterError}
      articleEditSeq={articleEditSeq}
      modelPresets={modelPresets}
      modelPreset={modelPreset}
      modelDetail={modelDetail}
      modelProvider={modelProvider}
      modelProviderLabel={modelProviderLabel}
      modelPresetFromFallback={modelPresetFromFallback}
      modelNeedsSetup={modelNeedsSetup}
      onManageModels={onManageModels}
      onModelRefresh={onModelRefresh}
      onCollapse={onCollapse}
    />
  );
}

function WritingConversation({
  session,
  articleSnapshot,
  flushForWriting,
  seed,
  onSeedConsumed,
  roster,
  rosterError,
  articleEditSeq,
  modelPresets,
  modelPreset,
  modelDetail,
  modelProvider,
  modelProviderLabel,
  modelPresetFromFallback,
  modelNeedsSetup,
  onManageModels,
  onModelRefresh,
  onCollapse,
}: {
  session: WritingSessionInfo;
  articleSnapshot: ArticleSnapshotPayload;
  flushForWriting: () => Promise<{ revision: number; digest: string } | null>;
  seed: WritingChatSeed | null;
  onSeedConsumed?: () => void;
  roster: ChatMember[] | null;
  rosterError: boolean;
  articleEditSeq: number;
  modelPresets: ModelPresetOption[];
  modelPreset: string | null;
  modelDetail: string | null;
  modelProvider: string | null;
  modelProviderLabel: string | null;
  modelPresetFromFallback: boolean;
  modelNeedsSetup: boolean;
  onManageModels: () => void;
  onModelRefresh: () => Promise<string | null>;
  onCollapse?: () => void;
}) {
  const history = useSessionHistory(session.session_key);
  const historyReady = !history.loading && (history.lineage > 0 || history.error !== null);

  if (history.error) {
    return (
      <aside className="flex h-full min-h-0 w-full flex-col bg-background">
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
          <AlertCircle className="h-5 w-5 text-destructive" aria-hidden />
          <p role="alert" className="text-[12px] text-muted-foreground">
            {history.error}
          </p>
          <button
            type="button"
            className="rounded-control border border-border px-2.5 py-1 text-[11.5px] hover:bg-accent"
            onClick={history.refresh}
          >
            重新加载
          </button>
        </div>
      </aside>
    );
  }

  if (!historyReady) {
    return (
      <aside className="flex h-full min-h-0 w-full flex-col bg-background">
        <div className="flex min-h-0 flex-1 items-center justify-center gap-2 text-[12px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          正在加载会话…
        </div>
      </aside>
    );
  }

  return (
    <StreamedConversation
      key={session.session_key}
      session={session}
      initialMessages={history.messages}
      hasPendingToolCalls={history.hasPendingToolCalls}
      articleSnapshot={articleSnapshot}
      flushForWriting={flushForWriting}
      seed={seed}
      onSeedConsumed={onSeedConsumed}
      roster={roster}
      rosterError={rosterError}
      articleEditSeq={articleEditSeq}
      modelPresets={modelPresets}
      modelPreset={modelPreset}
      modelDetail={modelDetail}
      modelProvider={modelProvider}
      modelProviderLabel={modelProviderLabel}
      modelPresetFromFallback={modelPresetFromFallback}
      modelNeedsSetup={modelNeedsSetup}
      onManageModels={onManageModels}
      onModelRefresh={onModelRefresh}
      onCollapse={onCollapse}
    />
  );
}

function StreamedConversation({
  session,
  initialMessages,
  hasPendingToolCalls,
  articleSnapshot,
  flushForWriting,
  seed,
  onSeedConsumed,
  roster,
  rosterError,
  articleEditSeq,
  modelPresets,
  modelPreset,
  modelDetail,
  modelProvider,
  modelProviderLabel,
  modelPresetFromFallback,
  modelNeedsSetup,
  onManageModels,
  onModelRefresh,
  onCollapse,
}: {
  session: WritingSessionInfo;
  initialMessages: UIMessage[];
  hasPendingToolCalls: boolean;
  articleSnapshot: ArticleSnapshotPayload;
  flushForWriting: () => Promise<{ revision: number; digest: string } | null>;
  seed: WritingChatSeed | null;
  onSeedConsumed?: () => void;
  roster: ChatMember[] | null;
  rosterError: boolean;
  articleEditSeq: number;
  modelPresets: ModelPresetOption[];
  modelPreset: string | null;
  modelDetail: string | null;
  modelProvider: string | null;
  modelProviderLabel: string | null;
  modelPresetFromFallback: boolean;
  modelNeedsSetup: boolean;
  onManageModels: () => void;
  onModelRefresh: () => Promise<string | null>;
  onCollapse?: () => void;
}) {
  const { t, i18n } = useTranslation();
  const { client } = useOptionalClient() ?? {};
  const stream = useNanobotStream(
    session.chat_id,
    initialMessages,
    hasPendingToolCalls,
  );
  const [input, setInput] = useState("");
  const [memberId, setMemberId] = useState<string | null>(null);
  const [task, setTask] = useState<WritingTask | null>(null);
  const [selection, setSelection] = useState<WritingSelectionContext | null>(null);
  const [contextScope, setContextScope] = useState<ContextScope | null>(null);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [optimisticPreset, setOptimisticPreset] = useState<string | null>(null);
  const [modelNotice, setModelNotice] = useState<string | null>(null);
  const [modelError, setModelError] = useState(false);
  const [modelBusy, setModelBusy] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);
  const composingRef = useRef(false);
  const selectionSeedSeqRef = useRef<number | null>(null);
  const articleEditSeqRef = useRef(articleEditSeq);
  articleEditSeqRef.current = articleEditSeq;

  const activePreset = optimisticPreset ?? modelPreset ?? "";
  const modelChipNeedsSetup = modelNeedsSetup || !activePreset;

  useEffect(() => {
    if (optimisticPreset && modelPreset === optimisticPreset) setOptimisticPreset(null);
  }, [modelPreset, optimisticPreset]);

  useEffect(() => {
    if (!seed) return;
    if (seed.text) setInput(seed.text);
    if (seed.task !== undefined) setTask(seed.task);
    if (seed.memberId !== undefined) setMemberId(seed.memberId);
    if (seed.selection !== undefined) {
      setSelection(seed.selection);
      selectionSeedSeqRef.current = seed.selection ? articleEditSeqRef.current : null;
    }
    setMenu(null);
    setSendError(null);
    onSeedConsumed?.();
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    });
    // `articleEditSeq` 只用来记录种子对应的正文版本，不驱动重新应用种子。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed, onSeedConsumed]);

  useEffect(() => {
    if (selection && selectionSeedSeqRef.current !== null
      && selectionSeedSeqRef.current !== articleEditSeq) {
      setSelection(null);
      selectionSeedSeqRef.current = null;
    }
  }, [articleEditSeq, selection]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [stream.messages, stream.isStreaming]);

  const commandItems = useMemo(() => {
    const query = menu?.kind === "slash" ? menu.query.trim().toLowerCase() : "";
    if (!query) return SLASH_COMMANDS;
    return SLASH_COMMANDS.filter((command) => (
      command.name.includes(query)
      || command.label.includes(query)
      || command.aliases.some((alias) => alias.includes(query))
    ));
  }, [menu]);

  const mentionItems = useMemo<MentionItem[]>(() => {
    const query = menu?.kind === "mention" ? menu.query.trim().toLowerCase() : "";
    const members = (roster ?? []).filter((member) => (
      !query
      || member.name.toLowerCase().includes(query)
      || member.id.toLowerCase().includes(query)
      || member.role.toLowerCase().includes(query)
    ));
    const contexts: MentionItem[] = query ? [] : [
      {
        kind: "context",
        scope: "full",
        label: CONTEXT_LABELS.full,
        enabled: true,
      },
      {
        kind: "context",
        scope: "selection",
        label: CONTEXT_LABELS.selection,
        enabled: selection !== null,
        hint: selection ? undefined : "先在正文中选中一段文字",
      },
      {
        kind: "context",
        scope: "outline",
        label: CONTEXT_LABELS.outline,
        enabled: articleSnapshot.outline.length > 0,
        hint: articleSnapshot.outline.length > 0 ? undefined : "还没有提纲",
      },
      {
        kind: "context",
        scope: "materials",
        label: CONTEXT_LABELS.materials,
        enabled: articleSnapshot.materials.some((item) => item.included),
        hint: articleSnapshot.materials.some((item) => item.included)
          ? undefined
          : "还没有已选素材",
      },
    ];
    return [
      ...members.map<MentionItem>((member) => ({ kind: "member", member })),
      ...contexts,
    ];
  }, [articleSnapshot, menu, roster, selection]);

  const menuItems: MenuItem[] = menu
    ? menu.kind === "slash"
      ? commandItems.map((command) => ({ kind: "slash", command }))
      : mentionItems
    : [];
  const activeIndex = menu ? Math.min(menu.active, Math.max(0, menuItems.length - 1)) : 0;

  const resetComposer = useCallback(() => {
    setInput("");
    setMemberId(null);
    setTask(null);
    setSelection(null);
    setContextScope(null);
    setMenu(null);
    setSendError(null);
    selectionSeedSeqRef.current = null;
  }, []);

  const applySlashCommand = useCallback((command: SlashCommand) => {
    const state = menu;
    if (!state) return;
    const next = `${input.slice(0, state.start)}${command.template}${input.slice(state.cursor)}`;
    setInput(next);
    setTask(command.task);
    setMemberId(command.memberId);
    setMenu(null);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      const position = state.start + command.template.length;
      el.setSelectionRange(position, position);
    });
  }, [input, menu]);

  const applyMention = useCallback((item: MenuItem) => {
    const state = menu;
    if (!state) return;
    const next = `${input.slice(0, state.start)}${input.slice(state.cursor)}`;
    if (item.kind === "member") {
      setInput(next);
      setMemberId(item.member.id);
    } else if (item.kind === "context") {
      if (!item.enabled) return;
      setInput(next);
      setContextScope(item.scope);
      if (item.scope === "selection" && selection) {
        selectionSeedSeqRef.current = articleEditSeq;
      }
    }
    setMenu(null);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(state.start, state.start);
    });
  }, [articleEditSeq, input, menu, selection]);

  const selectMenuIndex = useCallback((index: number) => {
    const item = menuItems[index];
    if (!item) return;
    if (item.kind === "slash") applySlashCommand(item.command);
    else applyMention(item);
  }, [applyMention, applySlashCommand, menuItems]);

  const updateTrigger = useCallback((value: string, cursor: number) => {
    if (composingRef.current) return;
    const trigger = detectTrigger(value, cursor);
    setMenu((previous) => {
      if (!trigger) return null;
      const same = previous
        && previous.kind === trigger.kind
        && previous.start === trigger.start
        && previous.query === trigger.query;
      return same ? previous : { ...trigger, active: 0 };
    });
  }, []);

  const handleInputChange = (value: string) => {
    setInput(value);
    setSendError(null);
    const el = textareaRef.current;
    updateTrigger(value, el?.selectionStart ?? value.length);
  };

  const handleSend = useCallback(async () => {
    if (sending) return;
    const text = input.trim();
    const preamble = contextPreamble(contextScope, articleSnapshot);
    const content = `${preamble}${text}`.trim();
    if (!content) return;
    if (placeholderPresent(content)) {
      setSendError(t("creative.chat.fillPlaceholder", {
        defaultValue: "请先把【主题】和【读者】替换成实际内容。",
      }));
      return;
    }
    setSending(true);
    setSendError(null);
    try {
      const snapshot = await flushForWriting();
      if (!snapshot) {
        setSendError(t("creative.chat.saveFirst", {
          defaultValue: "文章还有未保存、冲突或离线的修改，先处理保存后再发送。",
        }));
        return;
      }
      const sent = stream.send(content, undefined, {
        writing: {
          article_id: session.article_id,
          revision: snapshot.revision,
          digest: snapshot.digest,
          ...(memberId ? { member_id: memberId } : {}),
          ...(task ? { task } : {}),
          ...(selection ? { selection } : {}),
        },
      });
      if (!sent) {
        setSendError(t("creative.chat.sendFailed", {
          defaultValue: "消息没有发出，请稍后重试。",
        }));
        return;
      }
      resetComposer();
      stickToBottomRef.current = true;
      requestAnimationFrame(() => textareaRef.current?.focus());
    } catch (error: unknown) {
      setSendError(error instanceof Error ? error.message : "发送失败，请重试。");
    } finally {
      setSending(false);
    }
  }, [
    articleSnapshot,
    contextScope,
    flushForWriting,
    input,
    memberId,
    resetComposer,
    selection,
    sending,
    session.article_id,
    stream,
    t,
    task,
  ]);

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (composingRef.current || event.nativeEvent.isComposing) return;
    if (menu) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setMenu((previous) => previous
          ? { ...previous, active: (activeIndex + 1) % Math.max(1, menuItems.length) }
          : previous);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setMenu((previous) => previous
          ? {
              ...previous,
              active: (activeIndex - 1 + Math.max(1, menuItems.length))
                % Math.max(1, menuItems.length),
            }
          : previous);
        return;
      }
      if ((event.key === "Enter" || event.key === "Tab") && menuItems.length > 0) {
        event.preventDefault();
        selectMenuIndex(activeIndex);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setMenu(null);
        return;
      }
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSend();
    }
  };

  const handlePreset = (text: string, presetTask: WritingTask | null) => {
    setInput(text);
    setTask(presetTask);
    setMenu(null);
    setSendError(null);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    });
  };

  const handleModelPresetChange = useCallback(async (name: string) => {
    const next = name.trim();
    if (!next || next === activePreset || modelBusy) return;
    if (!client) {
      setModelError(true);
      setModelNotice("未连接网关，暂时不能切换模型");
      return;
    }
    if (!modelPresets.some((preset) => preset.name === next)) {
      setModelError(true);
      setModelNotice("未知的模型预设");
      return;
    }
    setOptimisticPreset(next);
    setModelBusy(true);
    setModelError(false);
    setModelNotice(t("creative.chat.modelPending", { defaultValue: "正在切换模型…" }));
    try {
      await client.sendSystemCommand(session.chat_id, `/model ${next}`);
      const confirmed = await onModelRefresh();
      if (confirmed === null) {
        setModelNotice(t("creative.chat.modelRequested", { defaultValue: "已提交切换，等待生效" }));
      } else if (confirmed === next) {
        setModelNotice(t("creative.chat.modelApplied", { defaultValue: "已切换模型" }));
      } else {
        setOptimisticPreset(null);
        setModelError(true);
        setModelNotice(t("creative.chat.modelStale", {
          defaultValue: "模型尚未切换，仍在使用 {{preset}}",
          preset: confirmed,
        }));
      }
    } catch {
      setOptimisticPreset(null);
      setModelError(true);
      setModelNotice(t("creative.chat.modelFailed", { defaultValue: "模型切换失败" }));
    } finally {
      setModelBusy(false);
    }
  }, [activePreset, client, modelBusy, modelPresets, onModelRefresh, session.chat_id, t]);

  const member = memberId
    ? roster?.find((entry) => entry.id === memberId) ?? null
    : null;
  const selectedMemberName = member?.name
    ?? (memberId
      ? t("creative.chat.memberFallback", { defaultValue: "已选成员" })
      : null);

  const showEmpty = stream.messages.length === 0 && !stream.isStreaming;
  const presets = emptyStatePresets(articleSnapshot, t);

  return (
    <aside
      data-testid="writing-chat-panel"
      aria-label={t("creative.chat.title", { defaultValue: "创作团队" })}
      className="flex h-full min-h-0 w-full flex-col bg-background"
    >
      <header className="shrink-0 border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
          <h2 className="min-w-0 flex-1 truncate text-[13px] font-semibold text-foreground">
            {session.team_name || t("creative.chat.title", { defaultValue: "创作团队" })}
          </h2>
          <span className="shrink-0 rounded-full border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
            {t("creative.chat.builtin", { defaultValue: "内置" })}
          </span>
          <Popover>
            <PopoverTrigger asChild>
              <button
                type="button"
                aria-label={t("creative.chat.more", { defaultValue: "更多" })}
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded-control text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <MoreHorizontal className="h-4 w-4" aria-hidden />
              </button>
            </PopoverTrigger>
            <PopoverContent side="bottom" align="end" className="w-64 p-2">
              <p className="px-1.5 pb-1 text-[10.5px] font-medium uppercase tracking-wide text-muted-foreground">
                {t("creative.chat.members", { defaultValue: "团队成员" })}
              </p>
              {roster ? (
                <ul className="mb-1.5 max-h-48 overflow-y-auto">
                  {roster.map((entry) => (
                    <li key={entry.id} className="flex items-center gap-2 rounded-control px-1.5 py-1">
                      <span
                        className={cn(
                          "flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded-full text-[9px]",
                          entry.leader
                            ? "bg-primary/15 text-primary"
                            : "bg-muted text-muted-foreground",
                        )}
                        aria-hidden
                      >
                        {entry.name.slice(0, 1)}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[11.5px] text-foreground">
                        {entry.name}
                      </span>
                      <span className="shrink-0 text-[10px] text-muted-foreground">
                        {memberRole(entry)}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="px-1.5 pb-1.5 text-[11px] text-muted-foreground">
                  {rosterError
                    ? t("creative.chat.rosterFailed", { defaultValue: "成员列表读取失败" })
                    : t("creative.chat.rosterLoading", { defaultValue: "正在读取成员…" })}
                </p>
              )}
              <div className="my-1 h-px bg-border" />
              <button
                type="button"
                className="flex w-full items-center rounded-control px-1.5 py-1.5 text-left text-[11.5px] hover:bg-accent"
                onClick={() => {
                  window.location.hash = workspaceRouteHash({ view: "teams" });
                }}
              >
                {t("creative.chat.manageTeam", { defaultValue: "团队管理" })}
              </button>
              <button
                type="button"
                className="flex w-full items-center rounded-control px-1.5 py-1.5 text-left text-[11.5px] hover:bg-accent"
                onClick={() => {
                  window.location.hash = workspaceRouteHash({
                    view: "chat",
                    chatKey: session.session_key,
                  });
                }}
              >
                {t("creative.chat.openSession", { defaultValue: "打开完整会话" })}
              </button>
            </PopoverContent>
          </Popover>
          {onCollapse ? (
            <button
              type="button"
              onClick={onCollapse}
              aria-label={t("creative.chat.collapse", { defaultValue: "收起创作团队" })}
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-control text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <PanelRightClose className="h-4 w-4" aria-hidden />
            </button>
          ) : null}
        </div>
        <div className="mt-1 flex min-w-0 items-center gap-2">
          <div className="flex min-w-0 max-w-[12rem] items-center" data-writing-model-chip>
            <ModelPresetBadge
              isHero={false}
              label={
                modelChipNeedsSetup
                  ? t("creative.chat.modelSetup", { defaultValue: "设置模型" })
                  : activePreset
              }
              modelDetail={modelChipNeedsSetup ? null : modelDetail}
              modelPreset={modelChipNeedsSetup ? "" : activePreset}
              modelPresets={modelPresets}
              provider={modelProvider}
              providerLabel={modelProviderLabel}
              needsSetup={modelChipNeedsSetup}
              onPresetChange={(name) => void handleModelPresetChange(name)}
              onClick={modelChipNeedsSetup ? onManageModels : undefined}
              onManageModels={onManageModels}
            />
          </div>
          {modelPresetFromFallback && !modelChipNeedsSetup ? (
            <span className="min-w-0 truncate text-[10px] text-muted-foreground">
              {t("creative.chat.globalModel", { defaultValue: "全局默认" })}
            </span>
          ) : null}
          {modelNotice ? (
            <span
              role={modelError ? "alert" : "status"}
              className={cn(
                "min-w-0 truncate text-[10px]",
                modelError ? "text-destructive" : "text-muted-foreground",
              )}
            >
              {modelNotice}
            </span>
          ) : null}
        </div>
      </header>

      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overflow-y-auto px-3 py-3"
        onScroll={(event) => {
          const el = event.currentTarget;
          stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
      >
        {showEmpty ? (
          <div data-testid="writing-chat-empty" className="flex flex-col gap-3">
            <p className="text-[12.5px] leading-relaxed text-foreground">
              {t("creative.chat.welcome", {
                defaultValue: "我是你的创作主编，可以帮你查资料、搭结构、写正文和改表达。",
              })}
            </p>
            <div className="grid grid-cols-1 gap-2">
              {presets.map((preset) => (
                <button
                  key={preset.key}
                  type="button"
                  data-testid={`writing-chat-preset-${preset.key}`}
                  onClick={() => handlePreset(preset.text, preset.task)}
                  className="rounded-control border border-border bg-settings-surface/50 px-2.5 py-2 text-left transition-colors hover:border-primary/40 hover:bg-accent"
                >
                  <span className="mb-0.5 block text-[10.5px] font-medium text-muted-foreground">
                    {preset.label}
                  </span>
                  <span className="line-clamp-2 text-[11.5px] leading-relaxed text-foreground">
                    {preset.text}
                  </span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <ThreadMessages
            messages={stream.messages}
            isStreaming={stream.isStreaming}
            runStartedAt={stream.runStartedAt}
            retryStatus={stream.retryStatus}
            onQuoteSelection={(text) => {
              setInput((current) => (current.trim() ? `${current}\n${text}` : text));
              textareaRef.current?.focus();
            }}
          />
        )}
      </div>

      <footer className="shrink-0 border-t border-border px-3 pb-3 pt-2">
        {stream.streamError ? (
          <div className="mb-2 flex items-start gap-2 rounded-control bg-destructive/10 px-2 py-1.5 text-[11px] text-destructive">
            <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
            <span className="min-w-0 flex-1">{streamErrorMessage(stream.streamError)}</span>
            <button
              type="button"
              onClick={stream.dismissStreamError}
              aria-label="关闭错误提示"
              className="shrink-0 hover:text-foreground"
            >
              <X className="h-3 w-3" aria-hidden />
            </button>
          </div>
        ) : null}

        {sendError ? (
          <p role="alert" className="mb-2 text-[11px] text-destructive">
            {sendError}
          </p>
        ) : null}

        <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
          {memberId ? (
            <button
              type="button"
              data-testid="writing-member-chip"
              onClick={() => setMemberId(null)}
              className="inline-flex max-w-full items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-[10.5px] text-primary"
              aria-label={`移除成员 ${selectedMemberName}`}
            >
              <AtSign className="h-3 w-3 shrink-0" aria-hidden />
              <span className="truncate">{selectedMemberName}</span>
              <X className="h-3 w-3 shrink-0" aria-hidden />
            </button>
          ) : null}
          {task ? (
            <button
              type="button"
              data-testid="writing-task-chip"
              onClick={() => setTask(null)}
              className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-[10.5px] text-muted-foreground"
              aria-label={`移除任务 ${TASK_LABELS[task]}`}
            >
              {TASK_LABELS[task]}
              <X className="h-3 w-3" aria-hidden />
            </button>
          ) : null}
          {contextScope && contextScope !== "selection" ? (
            <button
              type="button"
              data-testid="writing-context-chip"
              onClick={() => setContextScope(null)}
              className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-[10.5px] text-muted-foreground"
            >
              {contextScope === "outline"
                ? <ListTree className="h-3 w-3" aria-hidden />
                : <BookOpen className="h-3 w-3" aria-hidden />}
              {CONTEXT_LABELS[contextScope]}
              <X className="h-3 w-3" aria-hidden />
            </button>
          ) : null}
          {selection ? (
            <button
              type="button"
              data-testid="writing-selection-chip"
              onClick={() => setSelection(null)}
              className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-[10.5px] text-muted-foreground"
              title={selection.text}
              aria-label="移除选区引用"
            >
              <MessageSquare className="h-3 w-3 shrink-0" aria-hidden />
              <span className="max-w-[12rem] truncate">已引用：{selection.text}</span>
              <X className="h-3 w-3 shrink-0" aria-hidden />
            </button>
          ) : null}
        </div>

        <div className="relative">
          {menu ? (
            <div
              role="listbox"
              data-testid={`writing-${menu.kind}-menu`}
              aria-label={menu.kind === "slash" ? "写作任务" : "成员与上下文"}
              className="absolute bottom-full left-0 z-20 mb-1.5 max-h-[min(22rem,60vh)] w-full overflow-y-auto rounded-panel border border-border bg-popover p-1 shadow-xl"
            >
              {menu.kind === "slash" ? (
                menuItems.length > 0 ? (
                  (menuItems as Array<Extract<MenuItem, { kind: "slash" }>>).map((item, index) => (
                    <button
                      key={item.command.name}
                      type="button"
                      role="option"
                      aria-selected={index === activeIndex}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => applySlashCommand(item.command)}
                      className={cn(
                        "flex w-full items-center gap-2 rounded-control px-2 py-1.5 text-left",
                        index === activeIndex ? "bg-accent" : "hover:bg-accent/60",
                      )}
                    >
                      <span className="w-12 shrink-0 text-[11.5px] font-medium text-foreground">
                        /{item.command.name}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">
                        {item.command.label}
                      </span>
                      <span className="shrink-0 text-[10px] text-muted-foreground">
                        {item.command.handler}
                      </span>
                    </button>
                  ))
                ) : (
                  <p className="px-2 py-2 text-[11px] text-muted-foreground">没有匹配的任务</p>
                )
              ) : rosterError ? (
                <p className="px-2 py-2 text-[11px] text-muted-foreground">
                  成员列表读取失败，暂时不能用 @ 点名。仍可直接描述请求交给主编。
                </p>
              ) : mentionItems.length > 0 ? (
                mentionItems.map((item, index) => (
                  <div key={item.kind === "member" ? item.member.id : item.scope}>
                    {item.kind === "context" && index === (roster?.length ?? 0) ? (
                      <p className="px-2 pb-0.5 pt-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                        文章内容
                      </p>
                    ) : null}
                    <button
                      type="button"
                      role="option"
                      aria-selected={index === activeIndex}
                      disabled={item.kind === "context" && !item.enabled}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => applyMention(item)}
                      className={cn(
                        "flex w-full items-center gap-2 rounded-control px-2 py-1.5 text-left",
                        index === activeIndex ? "bg-accent" : "hover:bg-accent/60",
                        item.kind === "context" && !item.enabled && "opacity-45",
                      )}
                    >
                      {item.kind === "member" ? (
                        <>
                          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-primary/15 text-[9px] text-primary" aria-hidden>
                            {item.member.name.slice(0, 1)}
                          </span>
                          <span className="min-w-0 flex-1 truncate text-[11.5px] text-foreground">
                            {item.member.name}
                          </span>
                          <span className="shrink-0 text-[10px] text-muted-foreground">
                            {memberRole(item.member)}
                          </span>
                        </>
                      ) : (
                        <>
                          {item.scope === "selection"
                            ? <MessageSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                            : item.scope === "outline"
                              ? <ListTree className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                              : <BookOpen className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />}
                          <span className="min-w-0 flex-1 truncate text-[11.5px] text-foreground">
                            {item.label}
                          </span>
                          {item.hint ? (
                            <span className="shrink-0 text-[10px] text-muted-foreground">
                              {item.hint}
                            </span>
                          ) : null}
                        </>
                      )}
                    </button>
                  </div>
                ))
              ) : (
                <p className="px-2 py-2 text-[11px] text-muted-foreground">
                  {roster === null ? "正在读取成员…" : "没有匹配的成员"}
                </p>
              )}
            </div>
          ) : null}

          <div className="rounded-control border border-border bg-background focus-within:border-primary/50">
            <textarea
              ref={textareaRef}
              data-testid="writing-chat-input"
              value={input}
              rows={3}
              onChange={(event) => handleInputChange(event.target.value)}
              onKeyDown={handleKeyDown}
              onCompositionStart={() => {
                composingRef.current = true;
              }}
              onCompositionEnd={(event) => {
                composingRef.current = false;
                const el = event.currentTarget;
                updateTrigger(el.value, el.selectionStart ?? 0);
              }}
              placeholder={t("creative.chat.placeholder", {
                defaultValue: "输入需求，/ 选任务，@ 选成员",
              })}
              aria-label={t("creative.chat.inputLabel", { defaultValue: "创作对话输入框" })}
              className="block min-h-[64px] w-full resize-none border-none bg-transparent px-2.5 pt-2 text-[12.5px] leading-relaxed text-foreground outline-none placeholder:text-muted-foreground"
            />
            <div className="flex items-center justify-between gap-2 px-2 pb-1.5">
              <div className="flex min-w-0 items-center gap-1.5 text-[10px] text-muted-foreground">
                <button
                  type="button"
                  aria-label="写作任务"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => {
                    const el = textareaRef.current;
                    const cursor = el?.selectionStart ?? input.length;
                    setMenu({
                      kind: "slash",
                      start: cursor,
                      cursor,
                      query: "",
                      active: 0,
                    });
                    el?.focus();
                  }}
                  className="flex h-5 w-5 items-center justify-center rounded-control hover:bg-accent hover:text-foreground"
                >
                  /
                </button>
                <button
                  type="button"
                  aria-label="选择成员"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => {
                    const el = textareaRef.current;
                    setMenu({
                      kind: "mention",
                      start: el?.selectionStart ?? input.length,
                      cursor: el?.selectionStart ?? input.length,
                      query: "",
                      active: 0,
                    });
                    el?.focus();
                  }}
                  className="flex h-5 w-5 items-center justify-center rounded-control hover:bg-accent hover:text-foreground"
                >
                  @
                </button>
                <span className="truncate">
                  {i18n.resolvedLanguage?.startsWith("zh") ? "Enter 发送" : "Enter to send"}
                </span>
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                {stream.isStreaming ? (
                  <button
                    type="button"
                    data-testid="writing-chat-stop"
                    onClick={stream.stop}
                    aria-label={t("creative.chat.stop", { defaultValue: "停止" })}
                    className="flex h-7 items-center gap-1 rounded-control border border-border px-2 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
                  >
                    <Square className="h-3 w-3" aria-hidden />
                    {t("creative.chat.stop", { defaultValue: "停止" })}
                  </button>
                ) : null}
                <button
                  type="button"
                  data-testid="writing-chat-send"
                  onClick={() => void handleSend()}
                  disabled={sending || !input.trim()}
                  aria-label={t("creative.chat.send", { defaultValue: "发送" })}
                  className="flex h-7 w-7 items-center justify-center rounded-control bg-primary text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {sending
                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                    : <Send className="h-3.5 w-3.5" aria-hidden />}
                </button>
              </div>
            </div>
          </div>
        </div>
      </footer>
    </aside>
  );
}

function emptyStatePresets(
  snapshot: ArticleSnapshotPayload,
  t: TFunction,
): Array<{ key: string; label: string; text: string; task: WritingTask }> {
  const title = snapshot.title.trim();
  const hasTitle = Boolean(title) && !/^(未命名|未命名文章|untitled)$/i.test(title);
  const hasBody = snapshot.body.trim().length > 0;
  const hasOutline = snapshot.outline.length > 0;
  const hasMaterials = snapshot.materials.length > 0;

  const angles = {
    key: "angles",
    label: t("creative.chat.preset.angles.label", { defaultValue: "找选题角度" }),
    text: hasTitle
      ? `我想写一篇关于《${title}》的文章，先帮我找三个值得展开的角度。`
      : "我想写一篇关于【主题】的文章，面向【读者】。先帮我找三个值得展开的角度。",
    task: "ask" as const,
  };
  const materials = {
    key: "materials",
    label: t("creative.chat.preset.materials.label", { defaultValue: "整理素材" }),
    text: hasMaterials
      ? "根据我选中的素材，帮我整理核心观点，并创建一份文章提纲。"
      : "帮我找一些可靠的资料，整理成素材，并说明每份资料的来源。",
    task: hasMaterials ? ("outline" as const) : ("material" as const),
  };
  const outline = {
    key: "outline",
    label: t("creative.chat.preset.outline.label", { defaultValue: "写作初稿" }),
    text: hasOutline
      ? "按照当前提纲写一个初稿，表达自然，保留资料出处，不确定的地方标出来。"
      : "帮我梳理文章结构，先给一份提纲。",
    task: hasOutline ? ("draft" as const) : ("outline" as const),
  };
  const review = {
    key: "review",
    label: t("creative.chat.preset.review.label", { defaultValue: "审阅文章" }),
    text: "帮我审阅这篇文章，重点看逻辑、重复表达和缺少证据的地方，先给修改建议。",
    task: "ask" as const,
  };

  if (hasBody) return [review, angles, materials, outline];
  if (hasOutline) return [outline, review, materials, angles];
  if (hasMaterials) return [materials, angles, outline, review];
  return [angles, materials, outline, review];
}
