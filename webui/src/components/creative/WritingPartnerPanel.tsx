// 创作搭档侧栏（plan Task 12 / spec §3、§12.1）。
//
// 结构：顶部「创作搭档 · 内置团队」+ 本篇阶段；中部当前产物与主操作；底部固定输入框；
// 快捷任务可折叠；成员活动收进「查看协作过程」。
// **不出现**团队选择器、节点编辑、工作流配置——团队/角色/Skill 一律服务端解析（契约 §3）。
// （模型 preset chip 不是团队选择器：它只切这个协作会话的模型，走既有 `/model` 系统命令，
//   团队/角色/Skill 仍然完全由服务端解析。）
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, Loader2, Send, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ModelPresetBadge, type ModelPresetOption } from "@/components/thread/ModelPresetBadge";
import type {
  CollabAction,
  CollabApi,
  CollabArtifact,
  CollabArtifactSummary,
  CollabSuggestion,
  SelectionRange,
} from "@/lib/writing-collab-api";
import { ArtifactCard, type OutlinePoint, type ReviewIssue } from "./ArtifactCard";
import {
  PARTNER_BUILTIN_BADGE_KEY,
  PARTNER_DEFAULTS,
  PARTNER_INPUT_PLACEHOLDER_KEY,
  PARTNER_MODEL_APPLIED_KEY,
  PARTNER_MODEL_FAILED_KEY,
  PARTNER_MODEL_FALLBACK_KEY,
  PARTNER_MODEL_PENDING_KEY,
  PARTNER_MODEL_REQUESTED_KEY,
  PARTNER_MODEL_SETUP_KEY,
  PARTNER_MODEL_STALE_KEY,
  PARTNER_MODEL_UNKNOWN_KEY,
  PARTNER_MODEL_UNSUPPORTED_KEY,
  PARTNER_PROCESS_TOGGLE_KEY,
  PARTNER_TITLE_KEY,
  actionLabelKey,
  nextLabelKey,
  progressDescriptor,
  statusLabelKey,
} from "./collab-labels";
import {
  useWritingCollab,
  type CollabFrameClient,
  type ConfirmOutlineInput,
} from "./use-writing-collab";

export interface WritingPartnerPanelProps {
  articleId: string;
  articleTitle?: string;
  articleBody: string;
  articleRevision: number;
  snapshotDigest: string;
  /** 协作会话 chatId；缺省时按文章派生，集成层（WU-13）应显式传入。 */
  chatId?: string;
  client: CollabFrameClient;
  /** 集成层在拿到 transport 后注入；单测里换成 mock。 */
  api?: CollabApi;
  /** 编辑器追踪的选区（UTF-16）；面板不自己读 DOM 选区。 */
  selection?: SelectionRange | null;
  onSelectionChange?(range: SelectionRange | null): void;
  /** 显式作者写动作（spec §12.3）：面板只回调，不自己写文章。 */
  onAddMaterial?(material: { id: string; title: string }): void;
  onInsertDraft?(payload: { body: string; replaceWhole: boolean }): void;
  onApplyRewrite?(suggestion: CollabSuggestion): void;
  onUseAngle?(angle: { title: string; rationale?: string }): void;
  /**
   * 模型 chip（spec 2026-10-17-writing-partner-model-selection §4.3）。
   * 选项与会话当前值都由集成层注入：面板不读 settings、不读会话列表，
   * 只负责展示 + 发既有系统命令 `/model <preset>`。
   */
  modelPresets?: ModelPresetOption[];
  /** 协作会话 metadata 的真值；会话还没建立时是全局默认 preset。 */
  modelPreset?: string | null;
  /** 当前 preset 的模型名（会话列表查不到 preset 时的兜底展示）。 */
  modelDetail?: string | null;
  modelProvider?: string | null;
  modelProviderLabel?: string | null;
  /** 当前值只是全局默认（会话尚未建立）：如实说明，不假装已选会话 preset。 */
  modelPresetFromFallback?: boolean;
  /** 没有可用 preset / 未配置：走 `ModelPresetBadge` 既有 needsSetup 行为。 */
  modelNeedsSetup?: boolean;
  /** needsSetup 时的既有入口（跳模型设置）。 */
  onManageModels?(): void;
  /** 切换后读回服务端真值；返回读回的 preset 名，读不到时返回 null。 */
  onModelRefresh?(): Promise<string | null>;
}

/** 主操作随状态推进：空闲「帮我完成这篇文章」，完成「继续写下一节」。 */
const ACTION_BY_STATUS: Partial<Record<string, CollabAction>> = {
  idle: "start_full",
  queued: "start_full",
  running: "reply",
  waiting_author: "start_full",
  completed: "start_full",
  failed: "start_full",
  cancelled: "start_full",
  interrupted: "start_full",
};

/** 任务收口后的明确下一步：动作 → `next.*` 文案（spec §12.2）。 */
const NEXT_STEP_BY_ACTION: Partial<Record<CollabAction, string>> = {
  draft: "writeFromOutline",
  research: "addMaterials",
  outline: "writeFromOutline",
  rewrite_selection: "viewChanges",
  review_all: "viewChanges",
};

const QUICK_ACTION_FALLBACK: CollabAction[] = [
  "ideate",
  "research",
  "outline",
  "draft",
  "rewrite_selection",
  "review_all",
];

function isRunning(status: string): boolean {
  return status === "running" || status === "queued";
}

export function WritingPartnerPanel({
  articleId,
  articleTitle = "",
  articleBody,
  articleRevision,
  snapshotDigest,
  chatId,
  client,
  api,
  selection = null,
  onSelectionChange,
  onAddMaterial,
  onInsertDraft,
  onApplyRewrite,
  onUseAngle,
  modelPresets = [],
  modelPreset = null,
  modelDetail = null,
  modelProvider = null,
  modelProviderLabel = null,
  modelPresetFromFallback = false,
  modelNeedsSetup = false,
  onManageModels,
  onModelRefresh,
}: WritingPartnerPanelProps) {
  const { t } = useTranslation();
  const collab = useWritingCollab({
    articleId,
    articleBody,
    articleRevision,
    snapshotDigest,
    chatId: chatId ?? `${articleId}:partner`,
    client,
    api,
    selection,
  });

  const [draftText, setDraftText] = useState("");
  const [processOpen, setProcessOpen] = useState(false);
  // 快捷任务默认展开：作者最常用的入口不该藏在一次点击后面（面板仍是可折叠的）。
  const [quickOpen, setQuickOpen] = useState(true);
  const [busyArtifactId, setBusyArtifactId] = useState<string | null>(null);
  /**
   * 模型 chip 的本地状态：`modelSelection` 是「已经提交/读回确认的目标 preset」，
   * 用来在集成层把真值读回来之前不至于闪回旧值；集成层的真值一到就把它丢掉。
   */
  const [modelSelection, setModelSelection] = useState<string | null>(null);
  const [modelInFlight, setModelInFlight] = useState(false);
  const [modelNotice, setModelNotice] = useState<
    { key: string; tone: "ok" | "error"; preset?: string } | null
  >(null);

  const { snapshot, status, stage, busy, busyAction, nextActions, blockedReason, error } = collab;
  const collaboration = snapshot.collaboration;
  const running = isRunning(status);

  // 面板只回报选区，不改组件的输入状态：切产物/流式更新都不清空已输入反馈。
  useEffect(() => {
    onSelectionChange?.(selection);
  }, [onSelectionChange, selection]);

  const progress = progressDescriptor(status, stage);
  const progressText = t(progress.key, {
    defaultValue: PARTNER_DEFAULTS[progress.key] ?? progress.key,
  });
  const statusText = t(statusLabelKey(status), {
    defaultValue: PARTNER_DEFAULTS[statusLabelKey(status)] ?? status,
  });
  const stageText = stage
    ? t(`creative.partner.artifact.${stage}`, { defaultValue: stage })
    : t("creative.partner.stage.idle", {
      defaultValue: PARTNER_DEFAULTS["creative.partner.stage.idle"],
    });

  const primaryAction: CollabAction = useMemo(() => {
    if (ACTION_BY_STATUS[status] !== "start_full") return "start_full";
    const first = nextActions.find((action) => action !== "reply");
    return first ?? "start_full";
  }, [nextActions, status]);

  const primaryLabel = t(actionLabelKey(primaryAction), {
    defaultValue: PARTNER_DEFAULTS[actionLabelKey(primaryAction)] ?? primaryAction,
  });

  const nextStepLabel = useMemo(() => {
    const candidate = nextActions[0];
    if (candidate) {
      const id = NEXT_STEP_BY_ACTION[candidate];
      if (id) {
        return t(nextLabelKey(id), {
          defaultValue: PARTNER_DEFAULTS[nextLabelKey(id)] ?? id,
        });
      }
      return t(actionLabelKey(candidate), {
        defaultValue: PARTNER_DEFAULTS[actionLabelKey(candidate)] ?? candidate,
      });
    }
    if (collab.artifacts.length) {
      return t(nextLabelKey("viewChanges"), {
        defaultValue: PARTNER_DEFAULTS[nextLabelKey("viewChanges")],
      });
    }
    return primaryLabel;
  }, [collab.artifacts.length, nextActions, primaryLabel, t]);

  /**
   * 快捷任务是「作者可直接发起」的固定入口（spec §3 表格），不随服务端 nextActions 收缩：
   * `nextActions` 只决定主操作与下一步文案。「改这段」常驻但在无选区时不可发起。
   */
  const quickActions: CollabAction[] = QUICK_ACTION_FALLBACK;

  const plan = useMemo(() => {
    switch (stage) {
      case "brief":
        return "creative.partner.next.writeFromOutline";
      case "research":
        return "creative.partner.next.addMaterials";
      case "outline":
        return "creative.partner.next.writeFromOutline";
      case "draft":
      case "review":
      case "rewrite":
        return "creative.partner.next.viewChanges";
      default:
        return null;
    }
  }, [stage]);

  const handlePrimary = useCallback(async () => {
    if (running) {
      const text = draftText.trim();
      if (!text) return;
      await collab.reply(text, "primary");
      return;
    }
    await collab.start(primaryAction, draftText.trim() || undefined);
  }, [collab, draftText, primaryAction, running]);

  const handleQuickAction = useCallback(
    async (action: CollabAction) => {
      await collab.start(action, draftText.trim() || undefined);
    },
    [collab, draftText],
  );

  /**
   * 「以此角度继续」：只更新协作目标，**不写文章**（spec §12.3 第一行）。
   * 集成层想自己发 `writing.reply` 就接管回调；否则面板用 ideate 带上作者选的角度。
   */
  const handleUseAngle = useCallback(
    async (angle: { title: string; rationale?: string }) => {
      if (onUseAngle) {
        onUseAngle(angle);
        return;
      }
      const instruction = angle.rationale ? `${angle.title} — ${angle.rationale}` : angle.title;
      await collab.start("ideate", instruction);
    },
    [collab, onUseAngle],
  );

  const handleConfirmOutline = useCallback(
    async (outline: OutlinePoint[], artifactId?: string) => {
      if (!collab.pendingHandoff) {
        // 没有待确认交接就不假装确认：先让服务端给出交接。
        await collab.start("outline");
        return;
      }
      const input: ConfirmOutlineInput = {
        handoffId: collab.pendingHandoff.id,
        outline: outline.map((point, index) => ({
          id: point.id,
          text: point.text,
          order: index,
        })),
      };
      if (artifactId) setBusyArtifactId(artifactId);
      try {
        await collab.confirm(input);
      } finally {
        setBusyArtifactId(null);
      }
    },
    [collab],
  );

  const handleApplySuggestion = useCallback(
    async (suggestion: CollabSuggestion) => {
      setBusyArtifactId(suggestion.artifactId);
      try {
        const applied = await collab.apply(suggestion.id);
        if (applied) onApplyRewrite?.(suggestion);
      } finally {
        setBusyArtifactId(null);
      }
    },
    [collab, onApplyRewrite],
  );

  const handleApplyIssue = useCallback(
    async (issue: ReviewIssue) => {
      const suggestion = snapshot.suggestions.find(
        (entry) => entry.status === "pending" && (entry.id === issue.id || entry.summary === issue.title),
      );
      if (suggestion) {
        await handleApplySuggestion(suggestion);
        return;
      }
      // 审稿问题主要以「回复主编」推进：不发隐式写请求。
      await collab.reply(`Please address: ${issue.title}`, `issue:${issue.id || issue.title}`);
    },
    [collab, handleApplySuggestion, snapshot.suggestions],
  );

  const artifacts: Array<CollabArtifact | CollabArtifactSummary> = collab.artifacts;

  // —— 模型 chip（spec 2026-10-17-writing-partner-model-selection §4.3/§4.4）——
  // 显示值是集成层注入的会话真值；`modelSelection` 只在「已提交/已确认、父级真值还没到」
  // 这段窗口里顶一下，绝不长期覆盖服务端真值。
  const effectiveChatId = chatId ?? `${articleId}:partner`;
  const sessionPresetName = modelPreset?.trim() ?? "";
  const activeModelPreset = modelSelection ?? sessionPresetName;
  const modelChipNeedsSetup = modelNeedsSetup || !activeModelPreset;

  useEffect(() => {
    if (modelSelection && sessionPresetName === modelSelection) setModelSelection(null);
  }, [modelSelection, sessionPresetName]);

  const modelPresetNames = useMemo(
    () => new Set(modelPresets.map((preset) => preset.name)),
    [modelPresets],
  );

  const handleModelPresetChange = useCallback(
    async (name: string) => {
      const next = name.trim();
      // preset 名虽然来自 settings，仍然做非空 + 白名单校验：不信任任何外部字符串。
      if (!next || next === activeModelPreset) return;
      if (!modelPresetNames.has(next)) {
        setModelNotice({ key: PARTNER_MODEL_UNKNOWN_KEY, tone: "error" });
        return;
      }
      const sendSystemCommand = client.sendSystemCommand?.bind(client);
      if (!sendSystemCommand) {
        setModelNotice({ key: PARTNER_MODEL_UNSUPPORTED_KEY, tone: "error" });
        return;
      }
      setModelSelection(next);
      setModelInFlight(true);
      setModelNotice(null);
      try {
        await sendSystemCommand(effectiveChatId, `/model ${next}`);
        const confirmed = (await onModelRefresh?.()) ?? null;
        if (confirmed === null) {
          // 没有读回通道时只说「已提交」，绝不宣称已生效。
          setModelNotice({ key: PARTNER_MODEL_REQUESTED_KEY, tone: "ok" });
        } else if (confirmed === next) {
          setModelNotice({ key: PARTNER_MODEL_APPLIED_KEY, tone: "ok", preset: next });
        } else {
          setModelSelection(null);
          setModelNotice({ key: PARTNER_MODEL_STALE_KEY, tone: "error", preset: confirmed });
        }
      } catch {
        setModelSelection(null);
        setModelNotice({ key: PARTNER_MODEL_FAILED_KEY, tone: "error" });
      } finally {
        setModelInFlight(false);
      }
    },
    [
      activeModelPreset,
      client,
      effectiveChatId,
      modelPresetNames,
      onModelRefresh,
    ],
  );

  const modelChipLabel = modelChipNeedsSetup
    ? t(PARTNER_MODEL_SETUP_KEY, { defaultValue: PARTNER_DEFAULTS[PARTNER_MODEL_SETUP_KEY] })
    : activeModelPreset;

  // 稳定的回调身份：ModelPresetBadge 内部会把它挂在按钮上，没必要每次渲染换一个。
  const handlePresetChange = useCallback(
    (name: string) => {
      void handleModelPresetChange(name);
    },
    [handleModelPresetChange],
  );
  const modelNoticeIsError = !modelInFlight && modelNotice?.tone === "error";

  return (
    <aside
      data-writing-partner
      aria-label={t(PARTNER_TITLE_KEY, { defaultValue: PARTNER_DEFAULTS[PARTNER_TITLE_KEY] })}
      className="flex h-full min-h-0 w-full flex-col border-l border-border bg-background"
    >
      <header className="shrink-0 border-b border-border px-3 py-2.5">
        <div className="flex items-center gap-2">
          <h2 className="min-w-0 flex-1 truncate text-[13px] font-semibold text-foreground">
            {t(PARTNER_TITLE_KEY, { defaultValue: PARTNER_DEFAULTS[PARTNER_TITLE_KEY] })}
          </h2>
          {/* 模型 chip：只切本篇协作会话的模型，团队/角色/Skill 仍由服务端解析。 */}
          <div
            className="flex min-w-0 max-w-[11rem] shrink-0 justify-end"
            data-partner-model-chip
            aria-busy={modelInFlight || undefined}
          >
            <ModelPresetBadge
              isHero={false}
              label={modelChipLabel}
              modelDetail={modelChipNeedsSetup ? null : modelDetail}
              modelPreset={modelChipNeedsSetup ? "" : activeModelPreset}
              modelPresets={modelPresets}
              provider={modelProvider}
              providerLabel={modelProviderLabel}
              needsSetup={modelChipNeedsSetup}
              onPresetChange={handlePresetChange}
              onClick={modelChipNeedsSetup ? onManageModels : undefined}
              onManageModels={onManageModels}
            />
          </div>
          <span
            data-partner-builtin
            className="shrink-0 rounded-full border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground"
          >
            {t(PARTNER_BUILTIN_BADGE_KEY, {
              defaultValue: PARTNER_DEFAULTS[PARTNER_BUILTIN_BADGE_KEY],
            })}
          </span>
        </div>
        {modelPresetFromFallback && !modelChipNeedsSetup ? (
          <p data-partner-model-fallback className="mt-1 text-[10.5px] text-muted-foreground">
            {t(PARTNER_MODEL_FALLBACK_KEY, {
              defaultValue: PARTNER_DEFAULTS[PARTNER_MODEL_FALLBACK_KEY],
              preset: activeModelPreset,
            })}
          </p>
        ) : null}
        {modelInFlight || modelNotice ? (
          <p
            data-partner-model-notice={modelInFlight ? "pending" : modelNotice?.tone}
            role={modelNoticeIsError ? "alert" : undefined}
            aria-live={modelNoticeIsError ? "assertive" : "polite"}
            className={
              modelNoticeIsError
                ? "mt-1 text-[10.5px] text-destructive"
                : "mt-1 text-[10.5px] text-muted-foreground"
            }
          >
            {modelInFlight
              ? t(PARTNER_MODEL_PENDING_KEY, {
                defaultValue: PARTNER_DEFAULTS[PARTNER_MODEL_PENDING_KEY],
              })
              : t(modelNotice?.key ?? PARTNER_MODEL_REQUESTED_KEY, {
                defaultValue: modelNotice
                  ? PARTNER_DEFAULTS[modelNotice.key] ?? modelNotice.key
                  : PARTNER_DEFAULTS[PARTNER_MODEL_REQUESTED_KEY],
                preset: modelNotice?.preset ?? "",
              })}
          </p>
        ) : null}
        <p className="mt-1 truncate text-[11.5px] text-muted-foreground" data-partner-article>
          {articleTitle || t("creative.partner.ui.untitled", {
            defaultValue: PARTNER_DEFAULTS["creative.partner.ui.untitled"],
          })}
          {" · "}
          <span data-partner-stage={stage || "idle"}>{stageText}</span>
        </p>
        {plan ? (
          <p data-partner-plan className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {t(nextLabelKey("viewChanges"), {
              defaultValue: PARTNER_DEFAULTS["creative.partner.next.viewChanges"],
            })}
            {": "}
            {t("creative.partner.ui.plan", { defaultValue: PARTNER_DEFAULTS["creative.partner.ui.plan"] })}
          </p>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2.5">
        {/* 适度 live region：只在阶段/状态跃迁时播报一次，不逐 token 宣读。 */}
        <p
          role="status"
          aria-live="polite"
          data-partner-status={status}
          className="sr-only"
        >
          {progressText}
        </p>

        {blockedReason ? (
          <p data-partner-blocked className="mb-2 rounded-control bg-amber-500/10 px-2 py-1 text-[11px] text-amber-700 dark:text-amber-300">
            {blockedReason}
          </p>
        ) : null}

        {error ? (
          <p
            role="alert"
            data-partner-error
            className="mb-2 rounded-control bg-destructive/10 px-2 py-1 text-[11px] text-destructive"
          >
            {t(error, { defaultValue: PARTNER_DEFAULTS[error] ?? error })}
          </p>
        ) : null}

        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            className="h-8 text-[12px]"
            data-partner-primary
            disabled={busy || (running && !draftText.trim())}
            onClick={() => void handlePrimary()}
          >
            {busy && busyAction === primaryAction ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : null}
            {running ? t("creative.partner.ui.send", {
              defaultValue: PARTNER_DEFAULTS["creative.partner.ui.send"],
            }) : primaryLabel}
          </Button>
          {collab.canCancel ? (
            <Button
              size="sm"
              variant="outline"
              className="h-8 text-[11.5px]"
              data-partner-stop
              disabled={busy}
              onClick={() => void collab.cancel()}
            >
              <Square className="mr-1 h-3 w-3" aria-hidden="true" />
              {t("creative.partner.ui.stop", { defaultValue: PARTNER_DEFAULTS["creative.partner.ui.stop"] })}
            </Button>
          ) : null}
          {collab.failedBatch ? (
            <Button
              size="sm"
              variant="outline"
              className="h-8 text-[11.5px]"
              data-partner-retry
              disabled={busy}
              onClick={() => void collab.retry()}
            >
              {t("creative.partner.ui.retry", { defaultValue: PARTNER_DEFAULTS["creative.partner.ui.retry"] })}
            </Button>
          ) : null}
        </div>

        <p className="mt-2 text-[11.5px] text-muted-foreground" data-partner-progress>
          {collab.canCancel
            ? progressText
            : t("creative.partner.ui.noProgress", {
              defaultValue: PARTNER_DEFAULTS["creative.partner.ui.noProgress"],
            })}
          {" · "}
          <span data-partner-status-label>{statusText}</span>
        </p>

        {/* 无选区时明确提示要先划范围，而不是静默什么都不做。 */}
        {!selection && status !== "running" && status !== "queued" ? (
          <p data-partner-selection-hint className="mt-1.5 text-[10.5px] text-muted-foreground">
            {t("creative.partner.error.selectionRequired", {
              defaultValue: PARTNER_DEFAULTS["creative.partner.error.selectionRequired"],
            })}
          </p>
        ) : null}

        {/* 任务收口后总有一个明确下一步，不用泛问结尾（spec §12.2）。 */}
        <div data-partner-next className="mt-2">
          <Button
            size="sm"
            variant="secondary"
            className="h-7 text-[11.5px]"
            disabled={busy}
            onClick={() => void handleQuickAction(primaryAction)}
          >
            {nextStepLabel}
          </Button>
        </div>

        <section className="mt-3" aria-label={t("creative.partner.ui.quickTasks", {
          defaultValue: PARTNER_DEFAULTS["creative.partner.ui.quickTasks"],
        })}>
          <button
            type="button"
            data-partner-quick-toggle
            aria-expanded={quickOpen}
            onClick={() => setQuickOpen((open) => !open)}
            className="flex w-full items-center gap-1 text-left text-[11.5px] font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            {quickOpen ? (
              <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {t("creative.partner.ui.quickTasks", {
              defaultValue: PARTNER_DEFAULTS["creative.partner.ui.quickTasks"],
            })}
          </button>
          {quickOpen ? (
            <div className="mt-1.5 flex flex-wrap gap-1.5" data-partner-quick>
              {quickActions.map((action) => (
                <Button
                  key={action}
                  size="sm"
                  variant="outline"
                  className="h-7 text-[11px]"
                  disabled={busy || (action === "rewrite_selection" && !selection)}
                  onClick={() => {
                    if (action === "rewrite_selection" && !selection) {
                      collab.start("rewrite_selection");
                      return;
                    }
                    void handleQuickAction(action);
                  }}
                >
                  {t(actionLabelKey(action), {
                    defaultValue: PARTNER_DEFAULTS[actionLabelKey(action)] ?? action,
                  })}
                </Button>
              ))}
            </div>
          ) : null}
        </section>

        <div data-partner-artifacts className="mt-3 space-y-2">
          {artifacts.length ? (
            artifacts.map((artifact) => (
              <ArtifactCard
                key={`${artifact.id}:${artifact.version}`}
                artifact={artifact}
                locale={document.documentElement.lang || "en"}
                busy={busy || busyArtifactId === artifact.id}
                onUseAngle={(angle) => void handleUseAngle(angle)}
                onAddMaterial={(material) => onAddMaterial?.({ id: material.id, title: material.title })}
                onConfirmOutline={(outline) => void handleConfirmOutline(outline, artifact.id)}
                onInsertDraft={(payload) =>
                  onInsertDraft?.({
                    body: payload.sections.map((section) => section.body).join("\n\n"),
                    replaceWhole: payload.replaceWhole,
                  })
                }
                onApplyRewrite={() => {
                  const suggestion = snapshot.suggestions.find(
                    (entry) => entry.artifactId === artifact.id && entry.status === "pending",
                  );
                  if (suggestion) void handleApplySuggestion(suggestion);
                }}
                onApplyIssue={(issue) => void handleApplyIssue(issue)}
              />
            ))
          ) : (
            <p data-partner-empty className="text-[11px] text-muted-foreground">
              {t("creative.partner.ui.empty", { defaultValue: PARTNER_DEFAULTS["creative.partner.ui.empty"] })}
            </p>
          )}
        </div>

        <section className="mt-3">
          <button
            type="button"
            data-partner-process-toggle
            aria-expanded={processOpen}
            onClick={() => setProcessOpen((open) => !open)}
            className="flex w-full items-center gap-1 text-left text-[11.5px] font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            {processOpen ? (
              <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {t(PARTNER_PROCESS_TOGGLE_KEY, {
              defaultValue: PARTNER_DEFAULTS[PARTNER_PROCESS_TOGGLE_KEY],
            })}
          </button>
          {processOpen ? (
            <div data-partner-process className="mt-1.5 space-y-1">
              {collaboration ? (
                <p className="text-[11px] text-muted-foreground">
                  {statusText}
                  {" · "}
                  {progressText}
                  {" · "}
                  seq {snapshot.lastSeq}
                </p>
              ) : (
                <p className="text-[11px] text-muted-foreground">
                  {t("creative.partner.ui.empty", { defaultValue: PARTNER_DEFAULTS["creative.partner.ui.empty"] })}
                </p>
              )}
              {snapshot.handoffs.map((handoff) => (
                <p key={handoff.id} className="text-[11px] text-muted-foreground">
                  {handoff.kind}
                  {" · "}
                  {handoff.status}
                </p>
              ))}
            </div>
          ) : null}
        </section>
      </div>

      {/* 底部固定输入框：不随流式消息跳动，也不因切换产物而清空。 */}
      <footer className="shrink-0 border-t border-border px-3 py-2.5">
        <Textarea
          data-partner-input
          aria-label={t("creative.partner.ui.inputLabel", {
            defaultValue: PARTNER_DEFAULTS["creative.partner.ui.inputLabel"],
          })}
          placeholder={t(PARTNER_INPUT_PLACEHOLDER_KEY, {
            defaultValue: PARTNER_DEFAULTS[PARTNER_INPUT_PLACEHOLDER_KEY],
          })}
          value={draftText}
          rows={2}
          onChange={(event) => setDraftText(event.target.value)}
          className="min-h-[38px] resize-none text-[12px]"
        />
        <div className="mt-1.5 flex items-center justify-between gap-2">
          <span className="truncate text-[10.5px] text-muted-foreground" data-partner-selection>
            {selection ? `${selection.start}–${selection.end}` : ""}
          </span>
          <Button
            size="sm"
            className="h-7 text-[11.5px]"
            data-partner-send
            disabled={busy || !draftText.trim()}
            onClick={() => void collab.reply(draftText, "footer")}
          >
            <Send className="mr-1 h-3 w-3" aria-hidden="true" />
            {t("creative.partner.ui.send", { defaultValue: PARTNER_DEFAULTS["creative.partner.ui.send"] })}
          </Button>
        </div>
      </footer>
    </aside>
  );
}
