import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import type { TrajectoryBatchFrame } from "@/lib/zhixu-client";

import { TrajectoryClient } from "./client";
import { parseRecordList } from "./parser";
import { groupByTurn } from "./projection";
import type {
  TrajectoryBodyPage,
  TrajectoryCoverage,
  TrajectoryRecord,
  TrajectorySummary,
} from "./types";

const WS_POLL_INTERVAL_MS = 5000;
const FALLBACK_POLL_INTERVAL_MS = 1200;
const PAGE_LIMIT = 200;
const BODY_PAGE_BYTES = 65_536;

export interface TrajectoryWsClient {
  subscribeTrajectory(chatId: string, afterSeq: number): void;
  unsubscribeTrajectory(chatId: string): void;
  updateTrajectoryCursor(chatId: string, seq: number): void;
  onTrajectoryBatch(
    chatId: string,
    handler: (batch: TrajectoryBatchFrame) => void,
  ): () => void;
}

interface UseTrajectoryOptions {
  sessionKey: string | null;
  active: boolean;
  getToken: () => string;
  wsClient?: TrajectoryWsClient | null;
}

function chatIdForSession(sessionKey: string | null): string | null {
  if (!sessionKey) return null;
  return sessionKey.startsWith("websocket:") ? sessionKey.slice("websocket:".length) : null;
}

export interface TrajectoryState {
  records: TrajectoryRecord[];
  groups: ReturnType<typeof groupByTurn>;
  summary: TrajectorySummary | null;
  coverage: TrajectoryCoverage | null;
  loading: boolean;
  loadingOlder: boolean;
  error: string | null;
  notice: string | null;
  hasOlder: boolean;
  appliedSeq: number;
  loadEarlier: () => Promise<void>;
  reload: () => Promise<void>;
  bodies: Record<string, TrajectoryBodyPage>;
  bodyLoading: Set<string>;
  loadBody: (bodyId: string, offset?: number) => Promise<void>;
  clearNotice: () => void;
}

function mergeRecords(
  current: TrajectoryRecord[],
  updates: TrajectoryRecord[],
): TrajectoryRecord[] {
  if (updates.length === 0) return current;
  const byId = new Map(current.map((record) => [record.recordId, record]));
  const order = current.map((record) => record.recordId);
  for (const update of updates) {
    const existing = byId.get(update.recordId);
    if (!existing) {
      byId.set(update.recordId, update);
      order.push(update.recordId);
      continue;
    }
    if (update.recordRevision >= existing.recordRevision) {
      byId.set(update.recordId, update);
    }
  }
  return order
    .map((recordId) => byId.get(recordId))
    .filter((record): record is TrajectoryRecord => record !== undefined);
}

export function useTrajectory({
  sessionKey,
  active,
  getToken,
  wsClient = null,
}: UseTrajectoryOptions): TrajectoryState {
  const chatId = useMemo(() => chatIdForSession(sessionKey), [sessionKey]);
  const ws = useMemo(() => {
    if (!wsClient) return null;
    const candidate = wsClient as unknown as Record<string, unknown>;
    const required = [
      "subscribeTrajectory", "unsubscribeTrajectory", "updateTrajectoryCursor",
      "onTrajectoryBatch",
    ];
    return required.every((name) => typeof candidate[name] === "function")
      ? wsClient
      : null;
  }, [wsClient]);
  const clientRef = useRef<TrajectoryClient | null>(null);
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;
  if (clientRef.current === null) {
    clientRef.current = new TrajectoryClient({ getToken: () => getTokenRef.current() });
  }
  const [records, setRecords] = useState<TrajectoryRecord[]>([]);
  const [summary, setSummary] = useState<TrajectorySummary | null>(null);
  const [coverage, setCoverage] = useState<TrajectoryCoverage | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [hasOlder, setHasOlder] = useState(false);
  const [appliedSeq, setAppliedSeq] = useState(0);
  const [bodies, setBodies] = useState<Record<string, TrajectoryBodyPage>>({});
  const [bodyLoading, setBodyLoading] = useState<Set<string>>(new Set());

  const sessionRef = useRef<string | null>(sessionKey);
  sessionRef.current = sessionKey;
  const appliedRef = useRef(0);
  const cursorRef = useRef<string | null>(null);
  const busyRef = useRef(false);
  const bodyBusyRef = useRef(new Set<string>());

  const loadTail = useCallback(async (signal?: AbortSignal) => {
    const key = sessionRef.current;
    if (!key) return;
    setLoading(true);
    setError(null);
    try {
      const client = clientRef.current;
      if (!client) return;
      const page = await client.records(key, { limit: PAGE_LIMIT }, signal);
      if (sessionRef.current !== key) return;
      const detail = await client.summary(key, signal).catch(() => null);
      if (sessionRef.current !== key) return;
      setRecords(page.records);
      setSummary(detail);
      setCoverage(page.coverage);
      setHasOlder(page.hasOlder);
      cursorRef.current = page.nextBeforeCursor;
      appliedRef.current = page.snapshotSeq;
      setAppliedSeq(page.snapshotSeq);
      if (page.snapshotSeq === 0) {
        setNotice("empty");
      }
      if (ws && chatId) {
        ws.subscribeTrajectory(chatId, page.snapshotSeq);
      }
    } catch (err) {
      if (signal?.aborted) return;
      setError(err instanceof Error ? err.message : "failed to load trajectory");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [chatId, ws]);

  useEffect(() => {
    appliedRef.current = 0;
    cursorRef.current = null;
    setRecords([]);
    setSummary(null);
    setCoverage(null);
    setHasOlder(false);
    setError(null);
    setNotice(null);
    setAppliedSeq(0);
    setBodies({});
    return () => {
      if (ws && chatId) ws.unsubscribeTrajectory(chatId);
    };
  }, [sessionKey, chatId, ws]);

  useEffect(() => {
    if (!active || !sessionKey) return;
    const controller = new AbortController();
    void loadTail(controller.signal);
    return () => controller.abort();
  }, [active, sessionKey, loadTail]);

  const drain = useCallback(async (signal: AbortSignal) => {
    const key = sessionRef.current;
    const client = clientRef.current;
    if (!key || !client || busyRef.current) return;
    busyRef.current = true;
    try {
      let after = appliedRef.current;
      for (let round = 0; round < 6; round += 1) {
        const page = await client.events(key, { afterSeq: after, limit: PAGE_LIMIT }, signal);
        if (sessionRef.current !== key) return;
        if (page.recordUpdates.length > 0) {
          setRecords((current) => mergeRecords(current, page.recordUpdates));
        }
        setCoverage(page.coverage);
        after = page.nextAfterSeq;
        if (after > appliedRef.current) {
          appliedRef.current = after;
          setAppliedSeq(after);
          if (ws && chatId) ws.updateTrajectoryCursor(chatId, after);
        }
        if (!page.hasMore) break;
      }
    } catch (err) {
      if (signal.aborted) return;
      if (err instanceof ApiError && err.status === 410) {
        setNotice("cursorExpired");
        await loadTail(signal);
        return;
      }
      setError(err instanceof Error ? err.message : "failed to refresh trajectory");
    } finally {
      busyRef.current = false;
    }
  }, [chatId, loadTail, ws]);

  useEffect(() => {
    if (!active || !sessionKey) return;
    const controller = new AbortController();
    void drain(controller.signal);
    const intervalMs = ws && chatId ? WS_POLL_INTERVAL_MS : FALLBACK_POLL_INTERVAL_MS;
    const timer = window.setInterval(() => {
      void drain(controller.signal);
    }, intervalMs);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [active, sessionKey, chatId, drain, ws]);

  useEffect(() => {
    if (!ws || !chatId) return;
    const unsubscribe = ws.onTrajectoryBatch(chatId, (batch) => {
      if (batch.session_id !== sessionRef.current) return;
      const applied = appliedRef.current;
      if (applied > 0 && batch.from_seq > applied + 1) {
        void drain(new AbortController().signal);
        return;
      }
      const updates = parseRecordList(batch.record_updates);
      if (updates.length > 0) {
        setRecords((current) => mergeRecords(current, updates));
      }
      if (batch.to_seq > applied) {
        appliedRef.current = batch.to_seq;
        setAppliedSeq(batch.to_seq);
        ws.updateTrajectoryCursor(chatId, batch.to_seq);
      }
    });
    return unsubscribe;
  }, [chatId, drain, ws]);

  const loadEarlier = useCallback(async () => {
    const key = sessionRef.current;
    const client = clientRef.current;
    if (!key || !client || loadingOlder || !cursorRef.current) return;
    setLoadingOlder(true);
    try {
      const page = await client.records(
        key, { beforeCursor: cursorRef.current, limit: PAGE_LIMIT },
      );
      if (sessionRef.current !== key) return;
      setRecords((current) => mergeRecords(page.records, current));
      cursorRef.current = page.nextBeforeCursor;
      setHasOlder(page.hasOlder);
    } catch (err) {
      if (err instanceof ApiError && err.status === 410) {
        setNotice("cursorExpired");
        await loadTail();
      } else {
        setError(err instanceof Error ? err.message : "failed to load older records");
      }
    } finally {
      setLoadingOlder(false);
    }
  }, [loadingOlder, loadTail]);

  const loadBody = useCallback(async (bodyId: string, offset = 0) => {
    const key = sessionRef.current;
    const client = clientRef.current;
    if (!key || !client || bodyBusyRef.current.has(bodyId)) return;
    bodyBusyRef.current.add(bodyId);
    setBodyLoading((current) => new Set(current).add(bodyId));
    try {
      const page = await client.body(key, bodyId, {
        offsetBytes: offset,
        limitBytes: BODY_PAGE_BYTES,
      });
      setBodies((current) => {
        const previous = current[bodyId];
        if (offset > 0 && previous) {
          return {
            ...current,
            [bodyId]: { ...page, text: `${previous.text}${page.text}` },
          };
        }
        return { ...current, [bodyId]: page };
      });
    } catch (err) {
      setBodies((current) => ({
        ...current,
        [bodyId]: {
          bodyId,
          contentType: "text/plain",
          text: err instanceof Error ? err.message : "failed to load body",
          offsetBytes: 0,
          nextOffsetBytes: 0,
          hasMore: false,
          storedBytes: 0,
          originalBytes: 0,
          redacted: false,
          truncated: false,
        },
      }));
    } finally {
      bodyBusyRef.current.delete(bodyId);
      setBodyLoading((current) => {
        const next = new Set(current);
        next.delete(bodyId);
        return next;
      });
    }
  }, []);

  const reload = useCallback(async () => {
    await loadTail();
    await drain(new AbortController().signal);
  }, [drain, loadTail]);

  const groups = useMemo(() => groupByTurn(records), [records]);

  return {
    records,
    groups,
    summary,
    coverage,
    loading,
    loadingOlder,
    error,
    notice,
    hasOlder,
    appliedSeq,
    loadEarlier,
    reload,
    bodies,
    bodyLoading,
    loadBody,
    clearNotice: () => setNotice(null),
  };
}
