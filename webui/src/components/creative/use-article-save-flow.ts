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
  setField: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
  saveNow: () => Promise<void>;
  acknowledgeConflict: (resolution: "reload" | "savedCopy" | "discardLocal") => Promise<void>;
}

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

const DELETED_MESSAGE = "article deleted";

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
  const getTokenRef = useRef(getToken);
  const transportRef = useRef(transport);
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
    writeDraft({
      workspaceKey: workspacePath,
      articleId: article.id,
      baseRevision: stateRef.current.baseRevision,
      localEditSeq: stateRef.current.localEditSeq,
      snapshot: draftRef.current,
    });
  }, [article.id, workspacePath]);

  const cancelScheduled = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const runAttempt = useCallback(
    async (attempt: Attempt) => {
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

  return { draft, dirty, state, setField, saveNow, acknowledgeConflict };
}
