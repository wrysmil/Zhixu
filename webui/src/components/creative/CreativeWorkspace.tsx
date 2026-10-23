import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, fetchSettings, listSessions, type WebUIMutationTransport } from "@/lib/api";
import type { SettingsPayload } from "@/lib/types";
import { providerDisplayLabel } from "@/lib/provider-brand";
import { workspaceRouteHash } from "@/workspace/routes";
import type { ModelPresetOption } from "@/components/thread/ModelPresetBadge";
import { Button } from "@/components/ui/button";
import {
  createArticle,
  deleteArticle,
  duplicateArticle,
  fetchArticle,
  fetchArticleList,
  saveArticle,
  toArticle,
  toSnapshotPayload,
  undoArticleChange,
  type ArticleSnapshotPayload,
  type CreateArticleRequest,
} from "@/lib/writing-api";
import { clearDraft, listDrafts, readDraft, type LocalArticleDraft } from "@/lib/writing-draft-cache";
import type { Article, ArticleStatus, ArticleSummaryView } from "./types";
import {
  createDraftInput,
  duplicateTitle,
  filterByStatus,
  searchArticles,
} from "./article-model";
import { demoArticles } from "./demo-data";
import { ArticleList, type ArticleListState } from "./ArticleList";
import {
  ArticleEditor,
  type WritingSelectionAction,
} from "./ArticleEditor";
import {
  WritingChatPanel,
  type WritingChatSeed,
} from "./WritingChatPanel";
import { useArticleSaveFlow } from "./use-article-save-flow";
import { ArticleRecoveryBanner } from "./ArticleRecoveryBanner";
import type {
  WritingChangedEvent,
  WritingSelectionContext,
  WritingSessionInfo,
  WritingTask,
} from "@/lib/types";

export interface CreativeWorkspaceProps {
  getToken: () => string;
  transport: WebUIMutationTransport;
  /** 仅用于本地恢复稿命名空间隔离；服务端不接收该值（契约 §2）。 */
  workspacePath: string;
  /** 只有预览入口为 true：允许装载演示数据，且不发起任何请求（A-01/A-T11）。 */
  preview?: boolean;
}

type View = "list" | "editor";

const LIST_LIMIT = 50;
/** 搜索由服务端负责（契约 §4），逐键一请求会打满接口。 */
const SEARCH_DEBOUNCE_MS = 250;

function newLocalId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function freePreviewId(articles: Article[]): string {
  let index = articles.length + 1;
  while (articles.some((item) => item.id === `demo-${index}`)) index += 1;
  return `demo-${index}`;
}

function toSummaryRow(article: Article): ArticleSummaryView {
  return {
    id: article.id,
    title: article.title,
    summary: article.summary,
    status: article.status,
    writingType: article.writingType,
    tags: [...article.tags],
    revision: article.revision,
    updatedAt: article.updatedAt,
  };
}

/** 团队写入的三种 kind 对应的可读文本，用于改动前后对比。 */
function writingFieldText(snapshot: ArticleSnapshotPayload, kind: string): string {
  if (kind === "material") {
    return snapshot.materials
      .map((material) => `${material.title}\n${material.content}`.trim())
      .join("\n\n");
  }
  if (kind === "outline") {
    return snapshot.outline
      .slice()
      .sort((a, b) => a.order - b.order)
      .map((item, index) => `${index + 1}. ${item.text}`)
      .join("\n");
  }
  return snapshot.body;
}

interface RemoteWritingDiff {
  changeId: string;
  kind: string;
  actor: string | null;
  revision: number;
  digest: string | null;
  before: string;
  after: string;
}

/** 只比对会持久化的内容字段，用来回答 `no_change`。 */
function contentFields(article: Article) {
  return {
    title: article.title,
    writingType: article.writingType,
    templateId: article.templateId ?? "",
    status: article.status,
    summary: article.summary,
    brief: article.brief,
    body: article.body,
    outline: article.outline,
    materials: article.materials,
    tags: article.tags,
  };
}

// —— 创作搭档模型 chip 的数据来源（spec 2026-10-17-writing-partner-model-selection §4.3）——
//
// `settings.model_presets` 与会话 `model_preset` 都在这一层映射成面板 props：面板本身
// 不读 settings、不读会话列表。选项口径与 `ThreadShell.modelPresetOptionsFromSettings`
// 逐条对齐（同名过滤 `is_default`、按 `model_call_order` 排序）；它是那个模块的私有函数，
// 不能 import，故在此保持同口径实现而不是改动画像侧代码。

/** 宽松但明确的结构校验：拿不到 settings 就不要假装有可切换的 preset。 */
function isSettingsPayload(value: unknown): value is SettingsPayload {
  if (!value || typeof value !== "object") return false;
  const candidate = value as { model_presets?: unknown; agent?: unknown };
  return Array.isArray(candidate.model_presets)
    && Boolean(candidate.agent)
    && typeof candidate.agent === "object";
}

function partnerModelPresetOptions(settings: SettingsPayload | null): ModelPresetOption[] {
  if (!settings) return [];
  const order = new Map(
    (settings.model_call_order ?? []).map((name, index) => [name.trim(), index]),
  );
  return settings.model_presets
    .filter((preset) => !preset.is_default && preset.name.trim())
    .sort((a, b) => (
      (order.get(a.name.trim()) ?? Number.POSITIVE_INFINITY)
      - (order.get(b.name.trim()) ?? Number.POSITIVE_INFINITY)
    ))
    .map((preset) => {
      const name = preset.name.trim();
      return {
        name,
        model: preset.model,
        provider: preset.resolved_provider || preset.provider,
        reasoningEffort: preset.reasoning_effort ?? null,
      };
    });
}

/** 当前 preset 对应的模型与供应商（与 `ThreadShell.toModelBadgeInfo` 同口径）。 */
function partnerModelDisplay(
  settings: SettingsPayload | null,
  currentPreset: string,
): { model: string | null; provider: string | null; providerLabel: string | null } {
  const preset = settings?.model_presets.find((entry) => entry.name === currentPreset) ?? null;
  const model = (preset?.model || settings?.agent.model || "").trim() || null;
  const rawProvider = preset?.resolved_provider
    || preset?.provider
    || settings?.agent.resolved_provider
    || settings?.agent.provider
    || null;
  const provider = rawProvider === "auto"
    ? preset?.resolved_provider || settings?.agent.resolved_provider || null
    : rawProvider;
  const providerLabel = provider
    ? providerDisplayLabel(settings?.providers ?? [], provider)
    : null;
  return { model, provider, providerLabel };
}

/** 没有可用 preset 或当前 preset 没配好 → 走 `ModelPresetBadge` 既有 needsSetup 行为。 */
function partnerModelNeedsSetup(
  settings: SettingsPayload | null,
  currentPreset: string,
): boolean {
  if (!settings) return true;
  const preset = settings.model_presets.find((entry) => entry.name === currentPreset) ?? null;
  const model = (preset?.model || settings.agent.model || "").trim();
  const provider = preset?.resolved_provider
    || preset?.provider
    || settings.agent.resolved_provider
    || settings.agent.provider
    || null;
  const providerRow = provider
    ? settings.providers.find((item) => item.name === provider) ?? null
    : null;
  return Boolean(!model || !provider || !providerRow || !providerRow.configured);
}

/**
 * needsSetup 时的既有入口：写既有 settings 路由（App 与 PreviewApp 都监听 `hashchange`），
 * 不新增导航机制、不自己造跳转。
 */
function openModelSettings(): void {
  if (typeof window === "undefined") return;
  window.location.hash = workspaceRouteHash({ view: "settings", settingsSection: "models" });
}

/** 预览响应用契约 §5 的 snake_case 线格式，方便 `toArticle()` 原样解析。 */
function articleToWire(article: Article): Record<string, unknown> {
  return {
    ...toSnapshotPayload(article),
    id: article.id,
    revision: article.revision,
    created_at: article.createdAt,
    updated_at: article.updatedAt,
  };
}

/**
 * Preview write path (A-T11): the preview entry has no gateway connection, but the
 * editor still has to behave, so `article.{create,save,duplicate,delete}` are
 * simulated in memory against the same snake_case wire shape the real transport
 * speaks (contract §5). Pure function, no network access.
 */
export function applyPreviewMutation(
  articles: Article[],
  action: string,
  payload: Record<string, unknown>,
): { articles: Article[]; response: Record<string, unknown> } {
  const now = new Date().toISOString();
  const text = (key: string): string => {
    const value = payload[key];
    return typeof value === "string" ? value : "";
  };
  const next = [...articles];

  if (action === "article.create") {
    const created = toArticle({
      ...payload,
      id: freePreviewId(articles),
      revision: 1,
      created_at: now,
      updated_at: now,
    });
    return { articles: [created, ...next], response: { article: articleToWire(created) } };
  }

  if (action === "article.save") {
    const index = next.findIndex((item) => item.id === text("article_id"));
    if (index < 0) throw new ApiError(409, "article deleted");
    const current = next[index];
    if (Number(payload.base_revision ?? 0) !== current.revision) {
      throw new ApiError(
        409,
        `article_conflict current_revision=${current.revision} article_id=${current.id}`,
      );
    }
    const incoming = toArticle({
      ...(payload.snapshot as Record<string, unknown>),
      id: current.id,
      revision: current.revision,
      created_at: current.createdAt,
      updated_at: current.updatedAt,
    });
    if (JSON.stringify(contentFields(current)) === JSON.stringify(contentFields(incoming))) {
      return {
        articles: next,
        response: {
          article_id: current.id,
          revision: current.revision,
          updated_at: current.updatedAt,
          digest: `unchanged-${current.revision}`,
          no_change: true,
        },
      };
    }
    const saved: Article = {
      ...incoming,
      revision: current.revision + 1,
      createdAt: current.createdAt,
      updatedAt: now,
    };
    next[index] = saved;
    return {
      articles: next,
      response: {
        article_id: saved.id,
        revision: saved.revision,
        updated_at: saved.updatedAt,
        digest: newLocalId(),
        no_change: false,
      },
    };
  }

  if (action === "article.duplicate") {
    const source = next.find((item) => item.id === text("article_id"));
    if (!source) throw new ApiError(404, "article not found");
    const copy: Article = {
      ...source,
      id: freePreviewId(articles),
      title: duplicateTitle(source.title),
      revision: 1,
      createdAt: now,
      updatedAt: now,
      outline: source.outline.map((item, index) => ({ ...item, id: newLocalId(), order: index })),
      materials: source.materials.map((item) => ({ ...item, id: newLocalId() })),
    };
    return { articles: [copy, ...next], response: { article: articleToWire(copy) } };
  }

  if (action === "article.delete") {
    const target = next.find((item) => item.id === text("article_id"));
    if (!target) throw new ApiError(404, "article not found");
    return {
      articles: next.filter((item) => item.id !== target.id),
      response: { deleted_article_id: target.id, revision: target.revision },
    };
  }

  throw new ApiError(400, `preview workspace does not support ${action}`);
}

/**
 * `useArticleSaveFlow` needs a non-null article, so the hook lives in this child
 * instead of the container (hooks cannot be conditional).
 */
function EditorHost({
  getToken,
  transport,
  workspacePath,
  article,
  readLatest,
  onArticleReplaced,
  onBack,
  partnerEnabled = true,
  settings = null,
  settingsResolved = false,
}: {
  getToken: () => string;
  transport: WebUIMutationTransport;
  workspacePath: string;
  article: Article;
  /** 读取服务器最新版本；预览入口走内存副本，不发网络请求。 */
  readLatest: (articleId: string) => Promise<Article>;
  onArticleReplaced: (article: Article) => void;
  onBack: () => void;
  /** 预览入口不走真实协作：面板不挂载（预览里 transport 是内存替身）。 */
  partnerEnabled?: boolean;
  /** 容器读到的 settings（模型 chip 的选项来源）；预览入口为 null。 */
  settings?: SettingsPayload | null;
  /** settings 是否已经有结果（成功或失败）：加载中不谎报「未配置」。 */
  settingsResolved?: boolean;
}) {
  const flow = useArticleSaveFlow({ getToken, transport, article, workspacePath });

  // 重开同一篇文章：spec A-07 列出三种情形——干净打开 / 本地恢复稿存在且版本对齐
  // / 本地恢复稿存在但服务端已变。后两种必须在用户主动选择前进入浏览器。
  const [recovery, setRecovery] = useState<LocalArticleDraft | null>(() =>
    readDraft(workspacePath, article.id),
  );
  const [leaveGuard, setLeaveGuard] = useState(false);

  /**
   * On-mount 把恢复稿的选择处理掉：
   * - 服务端版本未变：留给 banner，用户自己点；
   * - 服务端版本已变：直接把冲突态灌进 save flow，让 A3.5 的 ConflictDialog 接管。
   */
  useEffect(() => {
    if (!recovery) return;
    if (recovery.baseRevision === article.revision) return;
    // 服务端比本地稿新：进入 A3.5 冲突合并流程。
    flow.applyRecovery(recovery.snapshot);
    flow.forceConflict({ currentRevision: article.revision, articleId: article.id });
    setRecovery(null);
    // 仅在挂载时触发一次；recovery/baseRevision 的更新不影响这条决策。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleRecover = useCallback(() => {
    if (!recovery) return;
    flow.applyRecovery(recovery.snapshot);
    setRecovery(null);
  }, [recovery, flow]);

  const handleDiscardRecovery = useCallback(() => {
    flow.discardRecovery();
    setRecovery(null);
  }, [flow]);

  const handleBack = useCallback(async () => {
    const decision = await flow.tryLeave();
    if (decision.status === "ok") {
      flow.abandon();
      onBack();
      return;
    }
    setLeaveGuard(true);
  }, [flow, onBack]);

  const handleLeaveRetry = useCallback(async () => {
    const decision = await flow.tryLeave();
    if (decision.status === "ok") {
      setLeaveGuard(false);
      flow.abandon();
      onBack();
    }
  }, [flow, onBack]);

  const handleLeaveStay = useCallback(() => {
    setLeaveGuard(false);
  }, []);

  const handleLeaveForce = useCallback(() => {
    setLeaveGuard(false);
    flow.abandon();
    onBack();
  }, [flow, onBack]);

  // 文章卸载（包括切换到列表 / 切换到另一篇文章）时，告知 save flow 放弃
  // 任何在飞 save，避免晚到的响应写回一个已经不存在或被替换的编辑器。
  // 只在挂载/卸载期间触发，否则每渲染都重新注册会清掉内部定时器。
  useEffect(() => () => {
    flow.abandon();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** 冲突对话框要展示服务器那一版，读取入口由容器提供（契约 §4）。 */
  const handleFetchLatest = useCallback(
    () => readLatest(article.id),
    [readLatest, article.id],
  );

  /**
   * 「另存副本」保住的是**本地稿**，不是服务端那一行：所以走 `article.create`
   * 新建（`duplicateArticle` 复制的是服务端当前内容，语义不同），紧接着把本地快照
   * 落一次盘，避免新文章停在「有标题没正文」的半保存状态（A-06 原子性）。
   */
  const handleSaveCopy = useCallback(async () => {
    const snapshot = flow.draft;
    const request: CreateArticleRequest = {
      clientRequestId: newLocalId(),
      title: duplicateTitle(snapshot.title),
      writingType: snapshot.writing_type,
      summary: snapshot.summary,
      brief: snapshot.brief,
      outline: snapshot.outline,
      materials: snapshot.materials,
      tags: snapshot.tags,
    };
    if (snapshot.template_id) request.templateId = snapshot.template_id;
    const created = await createArticle(transport, request);
    const saved = await saveArticle(transport, {
      clientRequestId: newLocalId(),
      articleId: created.id,
      baseRevision: created.revision,
      snapshot,
    });
    onArticleReplaced({
      ...created,
      writingType: snapshot.writing_type,
      status: snapshot.status,
      summary: snapshot.summary,
      brief: snapshot.brief,
      body: snapshot.body,
      outline: snapshot.outline,
      materials: snapshot.materials,
      tags: snapshot.tags,
      revision: saved.revision,
      updatedAt: saved.updated_at,
    });
  }, [flow.draft, onArticleReplaced, transport]);

  // —— 创作会话：身份由服务端绑定，模型 chip 读绑定后的会话真值 ——
  const [writingSession, setWritingSession] = useState<WritingSessionInfo | null>(null);
  const writingChatId = writingSession?.chat_id ?? null;
  const writingSessionKey = writingSession?.session_key ?? null;
  const [writingSessionPreset, setWritingSessionPreset] = useState<string | null>(null);
  const globalDefaultPreset = settings?.agent.model_preset?.trim() || "default";
  const globalDefaultPresetRef = useRef(globalDefaultPreset);
  globalDefaultPresetRef.current = globalDefaultPreset;
  const writingModelOptions = useMemo(() => partnerModelPresetOptions(settings), [settings]);

  /** 读回该会话的 `model_preset`；读不到（请求失败）返回 null，绝不假装成功。 */
  const readWritingSessionPreset = useCallback(async (): Promise<string | null> => {
    if (!writingSessionKey) return null;
    try {
      const rows = await listSessions(getToken());
      const row = rows.find((entry) => entry.key === writingSessionKey) ?? null;
      const value = row?.modelPreset?.trim() || null;
      setWritingSessionPreset(value);
      return value ?? globalDefaultPresetRef.current;
    } catch {
      return null;
    }
  }, [getToken, writingSessionKey]);

  useEffect(() => {
    if (!partnerEnabled || !writingSessionKey) {
      setWritingSessionPreset(null);
      return undefined;
    }
    void readWritingSessionPreset();
    return undefined;
  }, [partnerEnabled, readWritingSessionPreset, writingSessionKey]);

  /**
   * 会话帧对齐：`/model` 的 turn end 广播 `session_updated(scope="metadata")`，
   * 据此把真值读回来。只订阅本会话的 metadata 帧。
   */
  useEffect(() => {
    if (!partnerEnabled || !writingChatId) return undefined;
    const source = transport as {
      onSessionUpdate?: (
        handler: (chatId: string, scope?: string) => void,
      ) => () => void;
    };
    const subscribe = source.onSessionUpdate?.bind(source);
    if (!subscribe) return undefined;
    return subscribe((updatedChatId, scope) => {
      if (updatedChatId !== writingChatId) return;
      if (scope && scope !== "metadata") return;
      void readWritingSessionPreset();
    });
  }, [
    partnerEnabled,
    readWritingSessionPreset,
    transport,
    writingChatId,
  ]);

  const writingPreset = writingSessionPreset ?? globalDefaultPreset;
  const writingDisplay = partnerModelDisplay(settings, writingPreset);
  const writingNeedsSetup = settingsResolved
    && (writingModelOptions.length === 0
      || partnerModelNeedsSetup(settings, writingPreset));

  // —— 团队写入 → 编辑器：无脏稿自动应用；有脏稿只提示，绝不覆盖 ——
  const flowRef = useRef(flow);
  flowRef.current = flow;
  const dirtyRef = useRef(flow.dirty);
  dirtyRef.current = flow.dirty;
  const phaseRef = useRef(flow.state.phase);
  phaseRef.current = flow.state.phase;
  const [bodyEditSeq, setBodyEditSeq] = useState(0);
  const [composerSeed, setComposerSeed] = useState<WritingChatSeed | null>(null);
  const seedSeqRef = useRef(0);
  const [remoteNotice, setRemoteNotice] = useState<WritingChangedEvent | null>(null);
  const [remoteDiff, setRemoteDiff] = useState<RemoteWritingDiff | null>(null);
  const [remoteDiffOpen, setRemoteDiffOpen] = useState(false);
  const [remoteError, setRemoteError] = useState<string | null>(null);
  const [remoteBusy, setRemoteBusy] = useState(false);
  const remoteTimerRef = useRef<number | null>(null);
  const appliedChangeRef = useRef<string | null>(null);

  const applyTeamUpdate = useCallback(async (
    event: WritingChangedEvent,
    compare: boolean,
  ) => {
    setRemoteBusy(true);
    setRemoteError(null);
    try {
      const latest = await readLatest(article.id);
      const beforeSnapshot = flowRef.current.draft;
      const latestSnapshot = toSnapshotPayload(latest);
      const before = writingFieldText(beforeSnapshot, event.kind);
      const after = writingFieldText(latestSnapshot, event.kind);
      flowRef.current.applyRemote(
        latestSnapshot,
        latest.revision,
        latest.contentDigest ?? null,
        latest.updatedAt,
      );
      onArticleReplaced(latest);
      appliedChangeRef.current = event.change_id;
      setRemoteNotice(null);
      if (compare && before !== after) {
        setRemoteDiff({
          changeId: event.change_id,
          kind: event.kind,
          actor: event.actor_name ?? event.actor ?? null,
          revision: event.revision,
          digest: event.digest,
          before,
          after,
        });
        setRemoteDiffOpen(false);
      } else {
        setRemoteDiff(null);
      }
    } catch (error) {
      setRemoteError(
        error instanceof Error ? error.message : "读取团队修改失败，请稍后重试。",
      );
    } finally {
      setRemoteBusy(false);
    }
  }, [article.id, onArticleReplaced, readLatest]);

  useEffect(() => {
    if (!partnerEnabled) return undefined;
    const source = transport as {
      onWritingChanged?: (
        handler: (event: WritingChangedEvent) => void,
      ) => () => void;
    };
    const subscribe = source.onWritingChanged?.bind(source);
    if (!subscribe) return undefined;
    return subscribe((event) => {
      if (event.article_id !== article.id) return;
      if (appliedChangeRef.current === event.change_id) return;
      const phase = phaseRef.current;
      if (dirtyRef.current || phase === "saving" || phase === "conflict" || phase === "offline") {
        setRemoteNotice(event);
        return;
      }
      if (remoteTimerRef.current !== null) window.clearTimeout(remoteTimerRef.current);
      remoteTimerRef.current = window.setTimeout(() => {
        remoteTimerRef.current = null;
        void applyTeamUpdate(event, true);
      }, 150);
    });
  }, [applyTeamUpdate, article.id, partnerEnabled, transport]);

  useEffect(() => () => {
    if (remoteTimerRef.current !== null) {
      window.clearTimeout(remoteTimerRef.current);
      remoteTimerRef.current = null;
    }
  }, []);

  const handleLoadRemote = useCallback(async () => {
    const notice = remoteNotice;
    if (!notice) return;
    setRemoteBusy(true);
    try {
      const saved = await flowRef.current.flushForWriting();
      if (!saved) {
        setRemoteError("本地稿还没保存成功，先处理保存后再载入团队版本。");
        return;
      }
      await applyTeamUpdate(notice, true);
    } finally {
      setRemoteBusy(false);
    }
  }, [applyTeamUpdate, remoteNotice]);

  const handleUndoRemote = useCallback(async () => {
    if (!remoteDiff) return;
    setRemoteBusy(true);
    setRemoteError(null);
    try {
      const result = await undoArticleChange(transport, {
        clientRequestId: newLocalId(),
        changeId: remoteDiff.changeId,
        baseRevision: remoteDiff.revision,
      });
      if (result.noChange) {
        setRemoteDiff(null);
        return;
      }
      const latest = await readLatest(article.id);
      flowRef.current.applyRemote(
        toSnapshotPayload(latest),
        latest.revision,
        latest.contentDigest ?? null,
        latest.updatedAt,
      );
      onArticleReplaced(latest);
      setRemoteDiff(null);
      setRemoteNotice(null);
    } catch (error) {
      setRemoteError(
        error instanceof Error
          ? error.message
          : "撤销失败：可能有更新的修改，请先查看最新正文。",
      );
    } finally {
      setRemoteBusy(false);
    }
  }, [article.id, onArticleReplaced, readLatest, remoteDiff, transport]);

  const handleSetField = useCallback(
    <K extends keyof ArticleSnapshotPayload,>(
      key: K,
      value: ArticleSnapshotPayload[K],
    ) => {
      if (key === "body") setBodyEditSeq((seq) => seq + 1);
      flowRef.current.setField(key, value);
    },
    [],
  );

  const handleSeedConsumed = useCallback(() => setComposerSeed(null), []);

  const handleSelectionAction = useCallback((
    selection: WritingSelectionContext,
    action: WritingSelectionAction,
  ) => {
    const requests: Record<WritingSelectionAction, {
      text: string;
      task: WritingTask;
    }> = {
      ask: { text: "", task: "ask" },
      rewrite: { text: "把这段改得简洁清楚，保留术语。", task: "rewrite" },
      expand: { text: "把这段扩写得更具体，保持原意。", task: "expand" },
      condense: { text: "把这段精简，保留关键信息。", task: "condense" },
      explain: { text: "这段是什么意思？", task: "ask" },
      verify: { text: "这段说法准确吗？", task: "ask" },
      tone: { text: "把这段换成更自然的语气。", task: "rewrite" },
    };
    const request = requests[action];
    seedSeqRef.current += 1;
    setComposerSeed({
      id: seedSeqRef.current,
      text: request.text,
      task: request.task,
      memberId: null,
      selection,
    });
  }, []);

  const remoteBanner = remoteNotice || remoteDiff || remoteError ? (
    <div
      data-testid="writing-remote-banner"
      className="border-b border-primary/30 bg-primary/5 px-4 py-2 text-[11px] text-foreground"
    >
      {remoteNotice ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="min-w-0 flex-1">
            团队更新了文章；本地有未保存的修改，未覆盖本地稿。
          </span>
          <Button
            variant="outline"
            size="sm"
            className="h-6 cursor-pointer text-[11px]"
            disabled={remoteBusy}
            onClick={() => void handleLoadRemote()}
          >
            保存并载入团队版本
          </Button>
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground"
            onClick={() => setRemoteNotice(null)}
          >
            忽略
          </button>
        </div>
      ) : null}
      {remoteDiff ? (
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 flex-1">
              团队修改了{remoteDiff.kind === "body"
                ? "正文"
                : remoteDiff.kind === "outline"
                  ? "提纲"
                  : "素材"}
              {remoteDiff.actor ? `（${remoteDiff.actor}）` : ""}，已载入最新版本。
            </span>
            <button
              type="button"
              className="rounded border border-border px-1.5 py-0.5 hover:bg-accent"
              aria-expanded={remoteDiffOpen}
              onClick={() => setRemoteDiffOpen((open) => !open)}
            >
              {remoteDiffOpen ? "收起对比" : "查看对比"}
            </button>
            <button
              type="button"
              className="rounded border border-border px-1.5 py-0.5 hover:bg-accent disabled:opacity-50"
              disabled={remoteBusy}
              onClick={() => void handleUndoRemote()}
            >
              {remoteBusy ? "处理中…" : "撤销这次修改"}
            </button>
            <button
              type="button"
              className="text-muted-foreground hover:text-foreground"
              onClick={() => {
                setRemoteDiff(null);
                setRemoteError(null);
              }}
            >
              关闭
            </button>
          </div>
          {remoteDiffOpen ? (
            <div className="mt-2 grid grid-cols-2 gap-2">
              <div>
                <p className="mb-1 text-[10px] font-medium text-muted-foreground">修改前</p>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-control border border-border bg-background p-2 font-mono text-[10.5px] leading-relaxed">
                  {remoteDiff.before || "（空）"}
                </pre>
              </div>
              <div>
                <p className="mb-1 text-[10px] font-medium text-muted-foreground">修改后</p>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-control border border-border bg-background p-2 font-mono text-[10.5px] leading-relaxed">
                  {remoteDiff.after || "（空）"}
                </pre>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
      {remoteError ? (
        <p role="alert" className="mt-1 text-destructive">{remoteError}</p>
      ) : null}
    </div>
  ) : null;

  const writingChat = partnerEnabled
    ? ({ onCollapse }: { onCollapse: () => void }) => (
      <WritingChatPanel
        articleId={article.id}
        articleSnapshot={flow.draft}
        flushForWriting={flow.flushForWriting}
        seed={composerSeed}
        onSeedConsumed={handleSeedConsumed}
        onSessionChange={setWritingSession}
        articleEditSeq={bodyEditSeq}
        modelPresets={writingModelOptions}
        modelPreset={writingPreset}
        modelDetail={writingDisplay.model}
        modelProvider={writingDisplay.provider}
        modelProviderLabel={writingDisplay.providerLabel}
        modelPresetFromFallback={!writingSessionPreset}
        modelNeedsSetup={writingNeedsSetup}
        onManageModels={openModelSettings}
        onModelRefresh={readWritingSessionPreset}
        onCollapse={onCollapse}
      />
    )
    : null;

  return (
    <ArticleEditor
      article={article}
      draft={flow.draft}
      dirty={flow.dirty}
      saveState={flow.state}
      setField={handleSetField}
      onSaveNow={flow.saveNow}
      onAcknowledgeConflict={flow.acknowledgeConflict}
      onFetchLatest={handleFetchLatest}
      onSaveCopy={handleSaveCopy}
      onBack={handleBack}
      recoveryBanner={recovery ? (
        <ArticleRecoveryBanner
          article={article}
          recovery={recovery}
          storageUnhealthy={!flow.storageHealthy}
          onRecover={handleRecover}
          onDiscard={handleDiscardRecovery}
        />
      ) : null}
      storageUnhealthy={!flow.storageHealthy && !recovery}
      leaveGuardOpen={leaveGuard}
      onLeaveRetry={handleLeaveRetry}
      onLeaveStay={handleLeaveStay}
      onLeaveForce={handleLeaveForce}
      writingChat={writingChat}
      onSelectionAction={handleSelectionAction}
      remoteBanner={remoteBanner}
    />
  );
}

export default function CreativeWorkspace({
  getToken,
  transport,
  workspacePath,
  preview = false,
}: CreativeWorkspaceProps) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<ArticleSummaryView[]>([]);
  const [total, setTotal] = useState(0);
  const [listState, setListState] = useState<ArticleListState>(
    preview ? "ready" : "loading",
  );
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<ArticleStatus | "all">("all");
  const [view, setView] = useState<View>("list");
  const [activeArticle, setActiveArticle] = useState<Article | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [previewArticles, setPreviewArticles] = useState<Article[]>(() =>
    preview ? demoArticles() : [],
  );
  /**
   * 模型 chip 的选项来源。容器不再渲染 settings 页，但模型 preset 只能从 settings 取：
   * 这里读一次（每次进入创作视图一次），把 `model_presets` 映射后注入面板。
   */
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [settingsResolved, setSettingsResolved] = useState(preview);

  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;
  const previewRef = useRef(previewArticles);
  previewRef.current = previewArticles;
  /** 最新请求胜出：`fetchArticleList` 不接收 AbortSignal，迟到的旧响应必须丢弃。 */
  const listSeq = useRef(0);

  const writePreview = useCallback((next: Article[]) => {
    previewRef.current = next;
    setPreviewArticles(next);
  }, []);

  const previewTransport = useMemo<WebUIMutationTransport>(
    () => ({
      requestMutation: async <T,>(
        action: string,
        payload?: Record<string, unknown>,
      ): Promise<T> => {
        const outcome = applyPreviewMutation(previewRef.current, action, payload ?? {});
        writePreview(outcome.articles);
        return outcome.response as T;
      },
    }),
    [writePreview],
  );

  // 预览模式连写入都是本地的：真实 transport 在预览里一次都不该被用到。
  const mutationTransport = preview ? previewTransport : transport;

  useEffect(() => {
    if (preview) return undefined;
    const seq = listSeq.current + 1;
    listSeq.current = seq;
    setListState("loading");
    let settled = false;
    fetchArticleList(getTokenRef.current(), {
      q: debouncedQuery,
      status: statusFilter,
      limit: LIST_LIMIT,
      offset: 0,
    })
      .then((page) => {
        settled = true;
        if (listSeq.current !== seq) return;
        setRows(page.articles);
        setTotal(page.total);
        setListState(page.articles.length === 0 ? "empty" : "ready");
      })
      .catch(() => {
        settled = true;
        if (listSeq.current !== seq) return;
        setListState("error");
      });
    return () => {
      // 依赖变化时让在飞请求作废，避免旧结果覆盖新筛选。
      if (!settled) listSeq.current += 1000;
    };
  }, [preview, debouncedQuery, statusFilter, reloadKey]);

  useEffect(() => {
    if (preview) return undefined;
    const timer = setTimeout(() => setDebouncedQuery(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [preview, query]);

  useEffect(() => {
    if (preview) return undefined;
    let cancelled = false;
    setSettingsResolved(false);
    fetchSettings(getTokenRef.current())
      .then((payload) => {
        if (cancelled) return;
        // 载荷形状不对就当没有 settings：chip 走 needsSetup 跳设置，而不是崩掉。
        setSettings(isSettingsPayload(payload) ? payload : null);
        setSettingsResolved(true);
      })
      .catch(() => {
        if (cancelled) return;
        setSettings(null);
        setSettingsResolved(true);
      });
    return () => {
      cancelled = true;
    };
  }, [preview]);

  const localRows = useMemo(
    () => searchArticles(filterByStatus(previewArticles, statusFilter), query).map(toSummaryRow),
    [previewArticles, statusFilter, query],
  );
  const visibleRows = preview ? localRows : rows;
  const visibleTotal = preview ? visibleRows.length : total;
  const visibleListState: ArticleListState = preview
    ? visibleRows.length === 0 ? "empty" : "ready"
    : listState;

  const refreshList = useCallback(() => setReloadKey((key) => key + 1), []);

  /**
   * 冲突对话框读取「服务器那一版」的入口：预览入口一律走内存副本（A-T11），
   * 否则才是真实的 `article.get`（契约 §4）。
   */
  const readLatestArticle = useCallback(
    async (id: string): Promise<Article> => {
      if (preview) {
        const found = previewRef.current.find((item) => item.id === id) ?? null;
        if (!found) throw new ApiError(404, "article not found");
        return found;
      }
      return fetchArticle(getTokenRef.current(), id);
    },
    [preview],
  );

  const failureText = useCallback(
    (reason: unknown, fallbackKey: string, fallback: string) => {
      const message = reason instanceof Error ? reason.message.trim() : "";
      return message || t(fallbackKey, { defaultValue: fallback });
    },
    [t],
  );

  const openEditor = useCallback(
    async (id: string) => {
      setOpenError(null);
      if (preview) {
        const found = previewRef.current.find((item) => item.id === id) ?? null;
        setActiveArticle(found);
        setOpenError(found ? null : t("creative.articleMissing", { defaultValue: "文章已不存在" }));
        setView("editor");
        return;
      }
      try {
        const article = await fetchArticle(getTokenRef.current(), id);
        setActiveArticle(article);
        setView("editor");
      } catch (reason) {
        const status = reason instanceof ApiError ? reason.status : 0;
        const message = reason instanceof Error ? reason.message : "";
        let text = t("creative.articleOpenFailed", { defaultValue: "文章打开失败" });
        if (status === 404) {
          text = t("creative.articleMissing", { defaultValue: "文章已不存在" });
        } else if (status === 409 && message.includes("article deleted")) {
          text = t("creative.articleDeleted", { defaultValue: "文章已被删除" });
        } else if (status === 409) {
          text = t("creative.articleConflict", { defaultValue: "文章存在版本冲突，请重新打开" });
        }
        // spec A-07：读取失败不回退演示数据，只显示失败原因。
        setActiveArticle(null);
        setOpenError(text);
        setView("editor");
      }
    },
    [preview, t],
  );

  const backToList = useCallback(() => {
    setView("list");
    setActiveArticle(null);
    setOpenError(null);
    if (!preview) refreshList();
  }, [preview, refreshList]);

  const handleCreate = useCallback(
    async (title: string, templateId?: string) => {
      if (!title.trim()) {
        return { error: t("creative.title_required", { defaultValue: "请输入文章主题" }) };
      }
      try {
        const article = await createArticle(
          mutationTransport,
          createDraftInput(title, templateId, t),
        );
        // B01：预览列表的更新责任唯一归内存写通道（`previewTransport` 已 writePreview），
        // 这里再 prepend 一次会得到两张同 id 的卡。真实入口仍由服务端列表刷新。
        if (!preview) refreshList();
        setActiveArticle(article);
        setOpenError(null);
        setView("editor");
        return { error: null };
      } catch (reason) {
        // A-01：创建失败要保留已填标题与模板选择，所以错误只回给表单，不清空它。
        return { error: failureText(reason, "creative.createFailed", "新建文章失败") };
      }
    },
    [failureText, mutationTransport, preview, refreshList, t],
  );

  const handleDuplicate = useCallback(
    async (row: ArticleSummaryView) => {
      try {
        const copy = await duplicateArticle(mutationTransport, {
          articleId: row.id,
          baseRevision: row.revision,
          clientRequestId: newLocalId(),
        });
        if (preview) {
          // 同上：预览副本已由内存写通道插入，这里不再重复插。
          return;
        }
        // 服务端把副本整条返回了，直接插到列表头部，省一次往返。
        setRows((prev) => [toSummaryRow(copy), ...prev]);
        setTotal((prev) => prev + 1);
        setListState("ready");
      } catch (reason) {
        setActionError(failureText(reason, "creative.duplicateFailed", "复制文章失败"));
      }
    },
    [failureText, mutationTransport, preview],
  );

  const handleDelete = useCallback(
    async (row: ArticleSummaryView) => {
      try {
        await deleteArticle(mutationTransport, {
          articleId: row.id,
          baseRevision: row.revision,
          clientRequestId: newLocalId(),
        });
        // 服务端确认删除后才清本地恢复稿与本地表，避免删除后晚到的 save 把文章
        // 又写回去（A-02）。
        clearDraft(workspacePath, row.id);
        if (activeArticle?.id === row.id) backToList();
        if (preview) {
          // 内存写通道的 `article.delete` 已经把它从集合里摘掉，这里不再二次过滤。
          return;
        }
        refreshList();
      } catch (reason) {
        setActionError(failureText(reason, "creative.deleteFailed", "删除文章失败"));
      }
    },
    [
      activeArticle,
      backToList,
      failureText,
      mutationTransport,
      preview,
      refreshList,
      workspacePath,
    ],
  );

  // 把当前命名空间下仍缓存着本地恢复稿的文章 id 集合传给 ArticleList，
  // 让删除确认对话框在仍有未保存内容时显示警告（A-02）。
  const draftIds = useMemo(
    () =>
      new Set(listDrafts(workspacePath).map((draft) => draft.articleId)),
    [workspacePath, listDrafts, actionError],
  );

  if (view === "editor" && activeArticle) {
    return (
      <EditorHost
        key={activeArticle.id}
        getToken={getToken}
        transport={mutationTransport}
        workspacePath={workspacePath}
        article={activeArticle}
        readLatest={readLatestArticle}
        onArticleReplaced={setActiveArticle}
        onBack={backToList}
        partnerEnabled={!preview}
        settings={settings}
        settingsResolved={settingsResolved}
      />
    );
  }

  if (view === "editor" && openError) {
    return (
      <section
        data-testid="article-editor-error"
        className="mx-auto w-full max-w-[560px] px-6 py-16 text-center"
      >
        <p role="alert" className="text-[13px] font-medium text-foreground">
          {openError}
        </p>
        <button
          type="button"
          onClick={backToList}
          className="mt-4 cursor-pointer rounded-control border border-border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          {t("creative.back_to_list", { defaultValue: "返回文章列表" })}
        </button>
      </section>
    );
  }

  return (
    <ArticleList
      articles={visibleRows}
      totalCount={visibleTotal}
      listState={visibleListState}
      preview={preview}
      actionError={actionError}
      query={query}
      statusFilter={statusFilter}
      draftIds={draftIds}
      onQueryChange={setQuery}
      onStatusFilterChange={setStatusFilter}
      onOpen={openEditor}
      onCreate={handleCreate}
      onDuplicate={handleDuplicate}
      onDelete={handleDelete}
      onRetry={refreshList}
      onDismissActionError={() => setActionError(null)}
    />
  );
}
