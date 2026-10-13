import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, type WebUIMutationTransport } from "@/lib/api";
import {
  fetchArticle,
  parseArticleConflict,
  saveArticle,
  type ArticleConflict,
  type ArticleSnapshotPayload,
} from "@/lib/writing-api";
import { clearDraft, writeDraft } from "@/lib/writing-draft-cache";
import { buildSnapshot, nextLocalEditSeq } from "./article-model";
import type { Article } from "./types";

/**
 * Article save state machine (plan Task A3.2, spec A-06/A-07/A-08).
 *
 * ```text
 * saved ──setField──▶ dirty ──flush──▶ saving ──┬─ 2xx ─────────▶ saved
 *                                               ├─ 4xx/5xx ─────▶ failed
 *                                               ├─ 409/404 ─────▶ conflict (autosave paused)
 *                                               └─ 503/offline ─▶ offline
 * ```
 *
 * The server is the only source of truth for `revision`: the phase reaches
 * `saved` only once `article.save` resolves. The local draft cache is a recovery
 * copy, never evidence of a successful save (spec A-06, last bullet).
 */
export type SavePhase = "saved" | "dirty" | "saving" | "failed" | "conflict" | "offline";

/** spec A-06 recommended default; kept in exactly one place. */
export const AUTOSAVE_DEBOUNCE_MS = 1500;

/** Contract §6 carries no revision for a deleted article: -1 means "no server row". */
export const ARTICLE_DELETED_REVISION = -1;

/**
 * Editor working copy. It is the protocol snapshot itself, so the panels and the
 * save pipeline share one shape (contract §5).
 */
export type Draft = ArticleSnapshotPayload;

export interface SaveFlowState {
  phase: SavePhase;
  /** Revision confirmed by the server; every send uses this as `base_revision`. */
  baseRevision: number;
  /** Local edit counter (spec A-06 dual counting); never minted server-side. */
  localEditSeq: number;
  lastSavedAt: string | null;
  lastError: string | null;
  conflict: ArticleConflict | null;
}

export interface ArticleSaveFlow {
  draft: Draft;
  dirty: boolean;
  state: SaveFlowState;
  /**
   * Local-storage health (spec A-07). `true` while recent `writeDraft` calls
   * succeed; flipped to `false` when a write fails (e.g. quota exceeded) so the
   * editor can render a static "recovery unavailable" hint without silently
   * losing the next session of edits.
   */
  storageHealthy: boolean;
  setField: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
  saveNow: () => Promise<void>;
  acknowledgeConflict: (resolution: "reload" | "savedCopy" | "discardLocal") => Promise<void>;
  /** Replace the editor working copy with a draft recovered from local cache. */
  applyRecovery: (snapshot: ArticleSnapshotPayload) => void;
  /** Discard the cached recovery draft (keeps the editor on the server view). */
  discardRecovery: () => void;
  /** Force the flow into a conflict state without issuing a save attempt. */
  forceConflict: (conflict: ArticleConflict) => void;
  /**
   * Try to leave the editor safely (A-07). Runs an outstanding save, then
   * either resolves to `ok` or reports `blocked` so the caller can show the
   * three-option leave guard.
   */
  tryLeave: () => Promise<LeaveDecision>;
  /**
   * Tell the flow that the caller has decided to leave regardless of pending
   * work. Subsequent in-flight save responses will be discarded so they don't
   * resurrect an article the user already left (A-02 late-arriving responses).
   */
  abandon: () => void;
}

export type LeaveDecision =
  | { status: "ok" }
  | { status: "blocked"; phase: SavePhase; lastError: string | null };

export interface ArticleSaveFlowOptions {
  getToken: () => string;
  transport: WebUIMutationTransport;
  article: Article;
  /** Draft-cache namespace only; never sent to the server (contract §2). */
  workspacePath: string;
}

/** One frozen submission: content, the revision it was based on, its dedup id. */
interface Attempt {
  snapshot: Draft;
  baseRevision: number;
  clientRequestId: string;
  seq: number;
}

function newClientRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function errorStatus(reason: unknown): number {
  return reason instanceof ApiError ? reason.status : 500;
}

function errorMessage(reason: unknown): string {
  if (reason instanceof ApiError) return reason.message;
  return reason instanceof Error ? reason.message : "article save failed";
}

function isBrowserOffline(): boolean {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}

function toConflict(message: string, fallbackId: string): ArticleConflict {
  const parsed = parseArticleConflict(message);
  if (parsed) return parsed;
  // 契约 §6：除冲突格式外，任何 409/404 一律按「不可自动合并」处理。
  return { currentRevision: ARTICLE_DELETED_REVISION, articleId: fallbackId };
}

/**
 * Phase a local edit moves the machine into. An in-flight save keeps showing
 * `saving`; a conflict or an offline link stays paused until the user (or the
 * browser's `online` event) resolves it.
 */
function phaseAfterEdit(phase: SavePhase): SavePhase {
  if (phase === "conflict" || phase === "offline") return phase;
  return phase === "saving" ? "saving" : "dirty";
}

/**
 * IME-safe commit of a composed string (plan Task A3.3, A-T04).
 *
 * `original` is the store value when composition ends, `previousRendered` the
 * value the textarea was rendered with and `current` the textarea value. When a
 * third party changed the body mid-composition, `original` no longer matches
 * what we rendered and merging would drop one side: the function returns `null`
 * so the caller keeps both values and shows a conflict hint instead.
 */
export function mergeImeValue(
  original: string,
  previousRendered: string,
  current: string,
): string | null {
  if (previousRendered === original) return current;
  if (previousRendered === current) return original;
  return null;
}

export function useArticleSaveFlow({
  getToken,
  transport,
  article,
  workspacePath,
}: ArticleSaveFlowOptions): ArticleSaveFlow {
  const [draft, setDraft] = useState<Draft>(() => buildSnapshot(article));
  const [dirty, setDirty] = useState(false);
  const [storageHealthy, setStorageHealthy] = useState(true);
  const [state, setState] = useState<SaveFlowState>(() => ({
    phase: "saved",
    baseRevision: article.revision,
    localEditSeq: 0,
    lastSavedAt: article.updatedAt,
    lastError: null,
    conflict: null,
  }));

  const draftRef = useRef(draft);
  const stateRef = useRef(state);
  const dirtyRef = useRef(dirty);
  const storageHealthyRef = useRef(storageHealthy);
  const getTokenRef = useRef(getToken);
  const transportRef = useRef(transport);
  /**
   * Set when the host has decided to navigate regardless of pending work. Any
   * subsequent save attempt that finally settles will skip its post-response
   * setState so it can't resurrect an article the user already abandoned
   * (A-02 late-arriving responses).
   */
  const abandonedRef = useRef(false);
  /**
   * The business id is bound to the draft *object*: retrying an unchanged draft
   * reuses it (contract §5.2), the first edit that changes the content mints a
   * new one.
   */
  const requestIdRef = useRef<{ draft: Draft; id: string }>({ draft, id: newClientRequestId() });
  const queueRef = useRef<Promise<void>>(Promise.resolve());
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scheduleRef = useRef<(delay: number) => void>(() => {});

  getTokenRef.current = getToken;
  transportRef.current = transport;

  const applyState = useCallback((patch: Partial<SaveFlowState>) => {
    stateRef.current = { ...stateRef.current, ...patch };
    setState(stateRef.current);
  }, []);

  const markDirty = useCallback((value: boolean) => {
    dirtyRef.current = value;
    setDirty(value);
  }, []);

  const cacheDraft = useCallback(() => {
    const ok = writeDraft({
      workspaceKey: workspacePath,
      articleId: article.id,
      baseRevision: stateRef.current.baseRevision,
      localEditSeq: stateRef.current.localEditSeq,
      snapshot: draftRef.current,
    });
    if (!ok) {
      // Spec A-07: local cache unavailable must be announced, not silently fail.
      if (storageHealthyRef.current) {
        storageHealthyRef.current = false;
        setStorageHealthy(false);
      }
    }
  }, [article.id, workspacePath]);

  const cancelScheduled = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const runAttempt = useCallback(
    async (attempt: Attempt) => {
      if (abandonedRef.current) return;
      if (isBrowserOffline()) {
        cacheDraft();
        applyState({ phase: "offline", lastError: null });
        return;
      }
      let result;
      try {
        result = await saveArticle(transportRef.current, {
          clientRequestId: attempt.clientRequestId,
          articleId: article.id,
          baseRevision: attempt.baseRevision,
          snapshot: attempt.snapshot,
        });
      } catch (reason) {
        if (abandonedRef.current) return;
        const status = errorStatus(reason);
        const message = errorMessage(reason);
        cacheDraft();
        markDirty(true);
        if (status === 409 || status === 404) {
          // 自动保存暂停，直到用户处理冲突（A-07/A-08）。
          applyState({
            phase: "conflict",
            lastError: message,
            conflict: toConflict(message, article.id),
          });
          return;
        }
        if (status === 503 || isBrowserOffline()) {
          applyState({ phase: "offline", lastError: message, conflict: null });
          return;
        }
        applyState({ phase: "failed", lastError: message, conflict: null });
        return;
      }

      if (abandonedRef.current) return;
      if (draftRef.current !== attempt.snapshot) {
        // 在飞响应只推进版本，绝不回写编辑器内容（A-T03）。
        cacheDraft();
        markDirty(true);
        applyState({
          phase: "dirty",
          baseRevision: result.no_change ? attempt.baseRevision : result.revision,
          lastSavedAt: result.updated_at,
          lastError: null,
          conflict: null,
        });
        scheduleRef.current(AUTOSAVE_DEBOUNCE_MS);
        return;
      }
      // 服务端确认过的内容与本地一致，恢复稿不再需要保留。
      clearDraft(workspacePath, article.id);
      markDirty(false);
      applyState({
        phase: "saved",
        baseRevision: result.revision,
        lastSavedAt: result.updated_at,
        lastError: null,
        conflict: null,
      });
    },
    [applyState, article.id, cacheDraft, markDirty, workspacePath],
  );

  const flush = useCallback((): Promise<void> => {
    cancelScheduled();
    const snapshot = draftRef.current;
    if (requestIdRef.current.draft !== snapshot) {
      requestIdRef.current = { draft: snapshot, id: newClientRequestId() };
    }
    const attempt: Attempt = {
      snapshot,
      baseRevision: stateRef.current.baseRevision,
      clientRequestId: requestIdRef.current.id,
      seq: stateRef.current.localEditSeq,
    };
    applyState({ phase: "saving", conflict: null, lastError: null });
    // 同一编辑器的提交串成一条链：服务端另有每连接锁，前端不依赖它。
    queueRef.current = queueRef.current.then(() => runAttempt(attempt));
    return queueRef.current;
  }, [applyState, cancelScheduled, runAttempt]);

  const saveNow = useCallback(() => flush(), [flush]);

  const schedule = useCallback((delay: number) => {
    cancelScheduled();
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      const phase = stateRef.current.phase;
      // 冲突与离线期间不自动写回（A-08）。
      if (phase === "conflict" || phase === "offline") return;
      if (!dirtyRef.current) return;
      void flush();
    }, delay);
  }, [cancelScheduled, flush]);

  scheduleRef.current = schedule;

  const setField = useCallback(
    <K extends keyof Draft>(key: K, value: Draft[K]) => {
      const nextDraft = { ...draftRef.current, [key]: value };
      draftRef.current = nextDraft;
      setDraft(nextDraft);
      applyState({
        phase: phaseAfterEdit(stateRef.current.phase),
        localEditSeq: nextLocalEditSeq(stateRef.current.localEditSeq),
      });
      markDirty(true);
      // 每次本地编辑都刷新恢复稿：冲突暂停自动保存后内容也不会只剩半截。
      cacheDraft();
      // 冲突期间保留本地稿，但把写回的时机交还给用户（A-08）。
      if (stateRef.current.phase !== "conflict") scheduleRef.current(AUTOSAVE_DEBOUNCE_MS);
    },
    [applyState, cacheDraft, markDirty],
  );

  const acknowledgeConflict = useCallback(
    async (resolution: "reload" | "savedCopy" | "discardLocal") => {
      cancelScheduled();
      if (resolution === "savedCopy") {
        // 本地内容已由调用方另存为新文章（A3.5），这一篇不再自动写回。
        clearDraft(workspacePath, article.id);
        markDirty(false);
        applyState({ phase: "saved", conflict: null, lastError: null });
        return;
      }
      const latest = await fetchArticle(getTokenRef.current(), article.id);
      if (abandonedRef.current) return;
      const snapshot = buildSnapshot(latest);
      draftRef.current = snapshot;
      setDraft(snapshot);
      requestIdRef.current = { draft: snapshot, id: newClientRequestId() };
      if (resolution === "discardLocal") clearDraft(workspacePath, article.id);
      markDirty(false);
      applyState({
        phase: "saved",
        baseRevision: latest.revision,
        lastSavedAt: latest.updatedAt,
        lastError: null,
        conflict: null,
      });
    },
    [applyState, article.id, cancelScheduled, markDirty, workspacePath],
  );

  const applyRecovery = useCallback(
    (snapshot: ArticleSnapshotPayload) => {
      cancelScheduled();
      draftRef.current = snapshot;
      setDraft(snapshot);
      requestIdRef.current = { draft: snapshot, id: newClientRequestId() };
      // Spec A-06 末条：本地稿只是恢复用，不当保存成功依据；下一发 save 以
      // 当前服务端版本为基线，差异由 A3.5 的 ConflictDialog 兜底。
      applyState({
        phase: "dirty",
        baseRevision: article.revision,
        localEditSeq: nextLocalEditSeq(stateRef.current.localEditSeq),
        lastError: null,
        conflict: null,
      });
      markDirty(true);
      cacheDraft();
      scheduleRef.current(AUTOSAVE_DEBOUNCE_MS);
    },
    [article.revision, applyState, cacheDraft, cancelScheduled, markDirty],
  );

  const discardRecovery = useCallback(() => {
    clearDraft(workspacePath, article.id);
  }, [article.id, workspacePath]);

  const forceConflict = useCallback(
    (conflict: ArticleConflict) => {
      cancelScheduled();
      // 缓存保留：用户还得有机会在冲突对话框里取「另存副本」。
      cacheDraft();
      applyState({
        phase: "conflict",
        lastError: null,
        conflict,
      });
    },
    [applyState, cacheDraft, cancelScheduled],
  );

  const abandon = useCallback(() => {
    abandonedRef.current = true;
    cancelScheduled();
  }, [cancelScheduled]);

  const tryLeave = useCallback(async (): Promise<LeaveDecision> => {
    cancelScheduled();
    const phase = stateRef.current.phase;
    if (!dirtyRef.current && phase !== "saving") {
      return { status: "ok" };
    }
    if (phase === "saving") {
      // 等当前在飞 save 落地，再判断是否能离开。
      try {
        await queueRef.current;
      } catch {
        // ignore
      }
      const afterWait = stateRef.current.phase;
      if (afterWait !== "saving" && (!dirtyRef.current || afterWait === "saved")) {
        return { status: "ok" };
      }
      return { status: "blocked", phase: afterWait, lastError: stateRef.current.lastError };
    }
    try {
      await flush();
    } catch {
      // ignore — flush 不抛，但保险起见
    }
    const afterFlush = stateRef.current.phase;
    if (afterFlush === "saved" && !dirtyRef.current) {
      return { status: "ok" };
    }
    return { status: "blocked", phase: afterFlush, lastError: stateRef.current.lastError };
  }, [cancelScheduled, flush]);

  // 网络恢复后先向服务端确认版本，再决定是否允许继续自动保存（A-07）。
  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const onOnline = () => {
      if (stateRef.current.phase !== "offline") return;
      void (async () => {
        try {
          const latest = await fetchArticle(getTokenRef.current(), article.id);
          if (latest.revision === stateRef.current.baseRevision) {
            applyState({ phase: dirtyRef.current ? "dirty" : "saved", conflict: null });
            scheduleRef.current(AUTOSAVE_DEBOUNCE_MS);
            return;
          }
          applyState({
            phase: "conflict",
            conflict: { currentRevision: latest.revision, articleId: article.id },
          });
        } catch (reason) {
          applyState({ phase: "failed", lastError: errorMessage(reason) });
        }
      })();
    };
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, [applyState, article.id]);

  useEffect(() => cancelScheduled, [cancelScheduled]);

  // Spec A-07：浏览器关闭/刷新时若有未保存内容，要触发平台提示。浏览器自己
  // 决定是否拦截，调用方只能 preventDefault 设置 returnValue 触发系统弹窗。
  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const handler = (event: BeforeUnloadEvent) => {
      const phase = stateRef.current.phase;
      const unsaved = dirtyRef.current || phase === "saving";
      if (!unsaved) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, []);

  // 卸载 = 容器已经把 EditorHost 拆掉。任何在飞的 save 必须认这一事实，否则
  // 晚到的响应会回写到一个已经不存在的 state，引起 React 警告甚至让被删除
  // 文章在编辑器里复活（A-02 晚到保存防护）。
  useEffect(() => () => {
    abandonedRef.current = true;
    cancelScheduled();
  }, [cancelScheduled]);

  return {
    draft,
    dirty,
    state,
    storageHealthy,
    setField,
    saveNow,
    acknowledgeConflict,
    applyRecovery,
    discardRecovery,
    forceConflict,
    tryLeave,
    abandon,
  };
}
