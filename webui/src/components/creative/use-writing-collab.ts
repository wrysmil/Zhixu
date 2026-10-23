// 创作搭档的状态机（plan Task 12 / 契约 §5、§6）。
//
// 职责边界：
// - 读：`collabApi.getSnapshot(articleId)` 开场，产物全文按需 `getArtifact`。
// - 写：start / reply / confirm / cancel / retry / apply / undo 一律走 `collabApi`
//   （传输层已在 L3 固定为「HTTP 读 + 认证 WS mutation 写」）。
// - 实时：复用既有 `onChat(chatId)` 通道，帧经 `applyCollabEvent` 幂等归并
//   （旧 `version/seq` 忽略、重复事件不重复插入）。
// - 失败：保留作者输入与选区（本 hook 不持有输入框内容，因此也绝不代为清空）。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  applyCollabEvent,
  applyCollabSnapshot,
  createEmptyCollabSnapshot,
  createRequestIdFactory,
  utf16RangeToCodepoints,
  type CollabAction,
  type CollabApi,
  type CollabArtifact,
  type CollabArtifactSummary,
  type CollabBatch,
  type CollabHandoff,
  type CollabSnapshot,
  type CollabStage,
  type CollabStatus,
  type CollabSuggestion,
  type SelectionRange,
} from "@/lib/writing-collab-api";
import type { WebUIMutationTransport } from "@/lib/api";
import { collabApi as defaultCollabApi } from "@/lib/writing-collab-api";
import { errorLabelKey } from "./collab-labels";

/** 只依赖每聊天的帧订阅，便于测试注入假 transport。 */
export interface CollabFrameClient extends WebUIMutationTransport {
  onChat?(chatId: string, handler: (event: unknown) => void): () => void;
  /**
   * 既有系统命令通道（`/model <preset>` 等）。集成层传入真实 `ZhixuClient`；
   * 能力保持可选，单测替身与预览 transport 不必实现它（面板会如实报「不支持」）。
   * Source: webui/src/lib/zhixu-client.ts:1079（`sendSystemCommand`）。
   */
  sendSystemCommand?(chatId: string, command: string, timeoutMs?: number): Promise<void>;
  /**
   * 既有会话更新订阅。`/model` 命令的 turn end 会广播 `session_updated(scope="metadata")`，
   * 模型 chip 靠它把「会话当前 preset」读回真值，而不是乐观改 UI。
   * Source: nanobot/webui/outbound_projection.py:279-293（TurnEndEvent → scope metadata）。
   */
  onSessionUpdate?(handler: (chatId: string, scope?: string) => void): () => void;
}

export interface UseWritingCollabOptions {
  articleId: string;
  /** 文章正文：把 JS 的 UTF-16 选区换算成服务端认的 Python 码点。 */
  articleBody: string;
  articleRevision: number;
  snapshotDigest: string;
  chatId: string;
  client: CollabFrameClient;
  /** 集成层注入的默认实例；单测里换成 mock。 */
  api?: CollabApi;
  /** 父级（编辑器）传入的选区；缺省时 hook 自持，仍然通过 `setSelection` 回报。 */
  selection?: SelectionRange | null;
}

export interface ConfirmOutlineInput {
  handoffId: string;
  outline: Array<{ id: string; text: string; order: number }>;
}

export interface WritingCollabController {
  snapshot: CollabSnapshot;
  loading: boolean;
  status: CollabStatus;
  stage: CollabStage;
  /** 最近一次失败的 i18n key；null 表示没有未处理错误。 */
  error: string | null;
  busy: boolean;
  /** 当前忙的是哪个写动作，供面板按卡片显示等待态。 */
  busyAction: CollabAction | null;
  nextActions: CollabAction[];
  blockedReason: string | null;
  selection: SelectionRange | null;
  setSelection(range: SelectionRange | null): void;
  /** 有选区时返回当前选中的正文（按码点切片）。 */
  selectedText(): string;
  start(action: CollabAction, instruction?: string): Promise<void>;
  reply(text: string, key?: string): Promise<void>;
  confirm(input: ConfirmOutlineInput): Promise<void>;
  cancel(): Promise<void>;
  retry(): Promise<void>;
  apply(suggestionId: string): Promise<{ revision: number; digest: string; undoId: string } | null>;
  undo(undoId: string): Promise<void>;
  reload(): Promise<void>;
  clearError(): void;
  collaborationId: string;
  activeBatchId: string | null;
  artifacts: Array<CollabArtifact | CollabArtifactSummary>;
  suggestions: CollabSuggestion[];
  /** 最近一个待作者确认的交接（提纲确认等）。 */
  pendingHandoff: CollabHandoff | null;
  failedBatch: CollabBatch | null;
  canCancel: boolean;
}

/**
 * 归并口径：快照按 `lastVersion` 前进，事件按 `version/seq` 幂等。
 * 旧帧、重复帧、看不懂的帧都原样返回，绝不倒退状态。
 */
function mergeFrame(state: CollabSnapshot, frame: unknown): CollabSnapshot {
  return applyCollabEvent(state, frame);
}

/** 产物卡需要全文；摘要也能先渲染，但点开前拿不到分节内容。 */
export function hasArtifactContent(
  artifact: CollabArtifact | CollabArtifactSummary,
): artifact is CollabArtifact {
  return "content" in artifact;
}

/**
 * 幂等键：`client_request_id` 必须带**文章身份**。旧实现只按 `start:${action}`
 * 记键，切文章后同一个键会复用同一个 id，被服务端判 RequestReplayConflict
 * （security review F5/F6）。同一个键在飞行期间仍然只发一个 id。
 */
function writeRequestKey(articleId: string, kind: string, suffix = ""): string {
  return suffix ? `${kind}:${articleId}:${suffix}` : `${kind}:${articleId}`;
}

export function useWritingCollab(options: UseWritingCollabOptions): WritingCollabController {
  const {
    articleId,
    articleBody,
    articleRevision,
    snapshotDigest,
    chatId,
    client,
    api,
  } = options;
  const collab: CollabApi = api ?? defaultCollabApi;
  const requestIds = useRef(createRequestIdFactory()).current;

  const [snapshot, setSnapshot] = useState<CollabSnapshot>(createEmptyCollabSnapshot);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [busyAction, setBusyAction] = useState<CollabAction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [internalSelection, setInternalSelection] = useState<SelectionRange | null>(
    options.selection ?? null,
  );
  const [appliedRevision, setAppliedRevision] = useState(articleRevision);
  const detailsRequested = useRef(new Set<string>());
  const disposed = useRef(false);

  const selection = options.selection !== undefined ? options.selection : internalSelection;
  const setSelection = useCallback(
    (range: SelectionRange | null) => {
      setInternalSelection(range);
    },
    [],
  );

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const incoming = await collab.getSnapshot(articleId);
      setSnapshot((current) =>
        // 快照是数组字段的唯一来源：缺字段时不能把 `undefined` 漏给渲染层。
        applyCollabSnapshot(current, incoming ? normalizeSnapshot(incoming) : current),
      );
      setError(null);
    } catch (reason) {
      setError(errorLabelKey(reason));
    } finally {
      setLoading(false);
    }
  }, [articleId, collab]);

  useEffect(() => {
    disposed.current = false;
    setSnapshot(createEmptyCollabSnapshot());
    detailsRequested.current = new Set();
    setError(null);
    setLoading(true);
    void reload();
    return () => {
      disposed.current = true;
    };
  }, [reload]);

  // 订阅协作帧：只认 `onChat`，认不出的帧交给归并函数丢弃。
  useEffect(() => {
    const subscribe = client.onChat?.bind(client);
    if (!subscribe || !chatId) return;
    return subscribe(chatId, (frame) => {
      setSnapshot((current) => mergeFrame(current, frame));
    });
  }, [chatId, client]);

  // 产物全文按需补拉：摘要先渲染，拿到详情后替换，同一 id 只请求一次。
  useEffect(() => {
    for (const artifact of snapshot.artifacts) {
      if (hasArtifactContent(artifact)) continue;
      if (detailsRequested.current.has(artifact.id)) continue;
      detailsRequested.current.add(artifact.id);
      void collab
        .getArtifact(artifact.id)
        .then((full) => {
          if (disposed.current) return;
          setSnapshot((current) => ({
            ...current,
            artifacts: current.artifacts.map((entry) => (entry.id === full.id ? full : entry)),
          }));
        })
        .catch(() => {
          // 详情拉不到就保留摘要：卡片仍然能显示类型与摘要，不假装有全文。
        });
    }
  }, [snapshot.artifacts, collab]);

  const selectedText = useCallback((): string => {
    if (!selection) return "";
    return articleBody.slice(selection.start, selection.end);
  }, [articleBody, selection]);

  const run = useCallback(
    async (
      action: CollabAction,
      task: () => Promise<unknown>,
      /** 本次写动作的幂等键；成功或失败都要释放（security review F5/F6）。 */
      releaseKey?: string,
    ): Promise<boolean> => {
      setBusy(true);
      setBusyAction(action);
      try {
        await task();
        setError(null);
        return true;
      } catch (reason) {
        // 失败保留作者输入与选区：这里只落错误 key，不碰任何输入状态。
        setError(errorLabelKey(reason));
        return false;
      } finally {
        // 失败也要释放：否则「失败后改过载荷再试」会复用同一 client_request_id，
        // 被服务端按 RequestReplayConflict 挡掉；成功后再发本来就是新的逻辑动作。
        if (releaseKey) requestIds.release(releaseKey);
        setBusy(false);
        setBusyAction(null);
      }
    },
    [requestIds],
  );

  const start = useCallback(
    async (action: CollabAction, instruction?: string) => {
      const trimmed = (instruction ?? "").trim();
      if (action === "rewrite_selection" && !selection) {
        setError("creative.partner.error.selectionRequired");
        return;
      }
      let wireSelection: { start: number; end: number; text: string } | undefined;
      if (selection) {
        const range = utf16RangeToCodepoints(articleBody, selection.start, selection.end);
        wireSelection = {
          start: range.start,
          end: range.end,
          text: articleBody.slice(selection.start, selection.end),
        };
      }
      const baseRevision = Math.max(articleRevision, appliedRevision);
      const releaseKey = writeRequestKey(articleId, "start", action);
      await run(action, async () => {
        const result = await collab.start({
          clientRequestId: requestIds.for(releaseKey),
          articleId,
          chatId,
          action,
          baseRevision,
          snapshotDigest,
          ...(trimmed ? { instruction: trimmed } : {}),
          ...(wireSelection ? { selection: wireSelection } : {}),
        });
        setSnapshot((current) => ({
          ...current,
          collaboration: current.collaboration
            ? {
                ...current.collaboration,
                status: result.status,
                stage: result.stage,
                activeBatchId: result.activeBatchId,
              }
            : current.collaboration,
        }));
      }, releaseKey);
    },
    [
      appliedRevision,
      articleBody,
      articleId,
      articleRevision,
      chatId,
      collab,
      requestIds,
      run,
      selection,
      snapshotDigest,
    ],
  );

  const reply = useCallback(
    async (text: string, key = "reply") => {
      const collaboration = snapshot.collaboration;
      if (!collaboration) return;
      const feedback = text.trim();
      if (!feedback) return;
      const releaseKey = writeRequestKey(articleId, "reply", key);
      await run("reply", async () => {
        await collab.reply({
          clientRequestId: requestIds.for(releaseKey),
          collaborationId: collaboration.id,
          feedback,
          expectVersion: Math.max(collaboration.version, snapshot.lastVersion),
        });
      }, releaseKey);
    },
    [articleId, collab, requestIds, run, snapshot.collaboration, snapshot.lastVersion],
  );

  const confirm = useCallback(
    async (input: ConfirmOutlineInput) => {
      const collaboration = snapshot.collaboration;
      if (!collaboration) return;
      const releaseKey = writeRequestKey(articleId, "confirm", input.handoffId);
      await run("outline", async () => {
        const result = await collab.confirm({
          clientRequestId: requestIds.for(releaseKey),
          collaborationId: collaboration.id,
          handoffId: input.handoffId,
          expectedHandoffVersion: handoffVersion(snapshot, input.handoffId),
          articleRevision: Math.max(articleRevision, appliedRevision),
          articleDigest: snapshotDigest,
          outline: input.outline,
        });
        setAppliedRevision(result.articleRevision);
      }, releaseKey);
    },
    [
      appliedRevision,
      articleId,
      articleRevision,
      collab,
      requestIds,
      run,
      snapshot,
      snapshotDigest,
    ],
  );

  const cancel = useCallback(async () => {
    const collaboration = snapshot.collaboration;
    if (!collaboration?.activeBatchId) return;
    const releaseKey = writeRequestKey(articleId, "cancel", collaboration.activeBatchId);
    // 停止作用于当前自动批次；服务端回什么状态就显示什么（不假装已停干净）。
    await run("start_full", async () => {
      const result = await collab.cancel({
        clientRequestId: requestIds.for(releaseKey),
        collaborationId: collaboration.id,
        activeBatchId: collaboration.activeBatchId!,
      });
      setSnapshot((current) => ({
        ...current,
        collaboration: current.collaboration
          ? { ...current.collaboration, status: result.status }
          : current.collaboration,
      }));
    }, releaseKey);
  }, [articleId, collab, requestIds, run, snapshot.collaboration]);

  const failedBatch = useMemo(
    () =>
      [...snapshot.batches]
        .reverse()
        .find((batch) => batch.status === "failed" || batch.status === "interrupted") ?? null,
    [snapshot.batches],
  );

  const retry = useCallback(async () => {
    const collaboration = snapshot.collaboration;
    if (!collaboration || !failedBatch) return;
    const releaseKey = writeRequestKey(articleId, "retry", failedBatch.id);
    await run("start_full", async () => {
      await collab.retry({
        clientRequestId: requestIds.for(releaseKey),
        collaborationId: collaboration.id,
        batchId: failedBatch.id,
        expectVersion: Math.max(collaboration.version, snapshot.lastVersion),
      });
    }, releaseKey);
  }, [articleId, collab, failedBatch, requestIds, run, snapshot.collaboration, snapshot.lastVersion]);

  const apply = useCallback(
    async (suggestionId: string) => {
      const suggestion = snapshot.suggestions.find((entry) => entry.id === suggestionId);
      if (!suggestion) return null;
      const applied: { value: { revision: number; digest: string; undoId: string } | null } = {
        value: null,
      };
      const releaseKey = writeRequestKey(articleId, "apply", suggestionId);
      await run("start_full", async () => {
        const result = await collab.apply({
          clientRequestId: requestIds.for(releaseKey),
          suggestionId,
          articleId,
          baseRevision: Math.max(articleRevision, appliedRevision),
        });
        setAppliedRevision(result.revision);
        setSnapshot((current) => ({
          ...current,
          suggestions: current.suggestions.map((entry) =>
            entry.id === suggestionId
              ? { ...entry, status: "applied" as const, appliedRevision: result.revision }
              : result.staledSuggestionIds.includes(entry.id)
                ? { ...entry, status: "stale" as const }
                : entry,
          ),
        }));
        applied.value = { revision: result.revision, digest: result.digest, undoId: result.undoId };
      }, releaseKey);
      return applied.value;
    },
    [appliedRevision, articleId, articleRevision, collab, requestIds, run, snapshot.suggestions],
  );

  const undo = useCallback(
    async (undoId: string) => {
      const releaseKey = writeRequestKey(articleId, "undo", undoId);
      await run("start_full", async () => {
        const result = await collab.undo({
          clientRequestId: requestIds.for(releaseKey),
          undoId,
          baseRevision: Math.max(articleRevision, appliedRevision),
        });
        setAppliedRevision(result.revision);
      }, releaseKey);
    },
    [appliedRevision, articleId, articleRevision, collab, requestIds, run],
  );

  const pendingHandoff = useMemo(
    () => snapshot.handoffs.find((handoff) => handoff.status === "pending") ?? null,
    [snapshot.handoffs],
  );

  const collaboration = snapshot.collaboration;
  return {
    snapshot,
    loading,
    status: collaboration?.status ?? "idle",
    stage: collaboration?.stage ?? "",
    error,
    busy,
    busyAction,
    nextActions: snapshot.nextActions,
    blockedReason: snapshot.blockedReason,
    selection,
    setSelection,
    selectedText,
    start,
    reply,
    confirm,
    cancel,
    retry,
    apply,
    undo,
    reload,
    clearError: () => setError(null),
    collaborationId: collaboration?.id ?? "",
    activeBatchId: collaboration?.activeBatchId ?? null,
    artifacts: snapshot.artifacts,
    suggestions: snapshot.suggestions,
    pendingHandoff,
    failedBatch,
    canCancel: !!collaboration?.activeBatchId,
  };
}

/** 服务端加字段/缺字段都不该让面板炸：数组字段一律补成数组。 */
function normalizeSnapshot(snapshot: CollabSnapshot): CollabSnapshot {
  return {
    ...snapshot,
    batches: snapshot.batches ?? [],
    artifacts: snapshot.artifacts ?? [],
    handoffs: snapshot.handoffs ?? [],
    suggestions: snapshot.suggestions ?? [],
    nextActions: snapshot.nextActions ?? [],
  };
}

/**
 * `writing.confirm` 的 `expected_handoff_version`（契约 §10 裁决 3 冻结口径）：
 * 交接自身的整数 `artifact_version` **直接透传**。绝不混入协作版本
 * （`lastVersion` = `collaboration.version` 必然远大于产物版本，混入会让服务端
 * 的交接 CAS 稳定 409，提纲确认→续写主链路直接断）。
 * 交接不在快照里时没有可透传的版本：返回 1，让服务端按既有规则判 409。
 */
function handoffVersion(snapshot: CollabSnapshot, handoffId: string): number {
  const handoff = snapshot.handoffs.find((entry) => entry.id === handoffId);
  return handoff?.artifactVersion ?? 1;
}
