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
    async (action: CollabAction, task: () => Promise<unknown>): Promise<boolean> => {
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
        setBusy(false);
        setBusyAction(null);
      }
    },
    [],
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
      await run(action, async () => {
        const result = await collab.start({
          clientRequestId: requestIds.for(`start:${action}`),
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
        requestIds.release(`start:${action}`);
      });
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
      await run("reply", async () => {
        await collab.reply({
          clientRequestId: requestIds.for(`reply:${key}`),
          collaborationId: collaboration.id,
          feedback,
          expectVersion: Math.max(collaboration.version, snapshot.lastVersion),
        });
        requestIds.release(`reply:${key}`);
      });
    },
    [collab, requestIds, run, snapshot.collaboration, snapshot.lastVersion],
  );

  const confirm = useCallback(
    async (input: ConfirmOutlineInput) => {
      const collaboration = snapshot.collaboration;
      if (!collaboration) return;
      await run("outline", async () => {
        const result = await collab.confirm({
          clientRequestId: requestIds.for(`confirm:${input.handoffId}`),
          collaborationId: collaboration.id,
          handoffId: input.handoffId,
          expectedHandoffVersion: handoffVersion(snapshot, input.handoffId),
          articleRevision: Math.max(articleRevision, appliedRevision),
          articleDigest: snapshotDigest,
          outline: input.outline,
        });
        setAppliedRevision(result.articleRevision);
        requestIds.release(`confirm:${input.handoffId}`);
      });
    },
    [
      appliedRevision,
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
    // 停止作用于当前自动批次；服务端回什么状态就显示什么（不假装已停干净）。
    await run("start_full", async () => {
      const result = await collab.cancel({
        clientRequestId: requestIds.for(`cancel:${collaboration.activeBatchId}`),
        collaborationId: collaboration.id,
        activeBatchId: collaboration.activeBatchId!,
      });
      setSnapshot((current) => ({
        ...current,
        collaboration: current.collaboration
          ? { ...current.collaboration, status: result.status }
          : current.collaboration,
      }));
      requestIds.release(`cancel:${collaboration.activeBatchId}`);
    });
  }, [collab, requestIds, run, snapshot.collaboration]);

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
    await run("start_full", async () => {
      await collab.retry({
        clientRequestId: requestIds.for(`retry:${failedBatch.id}`),
        collaborationId: collaboration.id,
        batchId: failedBatch.id,
        expectVersion: Math.max(collaboration.version, snapshot.lastVersion),
      });
      requestIds.release(`retry:${failedBatch.id}`);
    });
  }, [collab, failedBatch, requestIds, run, snapshot.collaboration, snapshot.lastVersion]);

  const apply = useCallback(
    async (suggestionId: string) => {
      const suggestion = snapshot.suggestions.find((entry) => entry.id === suggestionId);
      if (!suggestion) return null;
      const applied: { value: { revision: number; digest: string; undoId: string } | null } = {
        value: null,
      };
      await run("start_full", async () => {
        const result = await collab.apply({
          clientRequestId: requestIds.for(`apply:${suggestionId}`),
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
        requestIds.release(`apply:${suggestionId}`);
        applied.value = { revision: result.revision, digest: result.digest, undoId: result.undoId };
      });
      return applied.value;
    },
    [appliedRevision, articleId, articleRevision, collab, requestIds, run, snapshot.suggestions],
  );

  const undo = useCallback(
    async (undoId: string) => {
      await run("start_full", async () => {
        const result = await collab.undo({
          clientRequestId: requestIds.for(`undo:${undoId}`),
          undoId,
          baseRevision: Math.max(articleRevision, appliedRevision),
        });
        setAppliedRevision(result.revision);
        requestIds.release(`undo:${undoId}`);
      });
    },
    [appliedRevision, articleRevision, collab, requestIds, run],
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

function handoffVersion(snapshot: CollabSnapshot, handoffId: string): number {
  const handoff = snapshot.handoffs.find((entry) => entry.id === handoffId);
  // 交接自身没有版本列；用协作版本 + 产物版本作为 CAS 依据（契约 §3）。
  if (!handoff) return Math.max(snapshot.lastVersion, 1);
  return Math.max(snapshot.lastVersion, handoff.artifactVersion, 1);
}
