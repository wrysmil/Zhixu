import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ConnectionStatus } from "@/lib/types";
import {
  listSubagentExecutions,
  loadSubagentExecution,
  loadSubagentExecutionEvents,
} from "./client";
import { parseExecutionFrame } from "./parser";
import {
  applyExecutionEvent,
  applyExecutionEvents,
  applyExecutionSnapshot,
  isTerminalExecutionStatus,
  markExecutionConnection,
  markExecutionHistoryLimited,
  resumeExecutionTimeline,
} from "./reducer";
import type {
  ExecutionListResponse,
  SubagentExecutionFrameClient,
  SubagentExecutionState,
  SubagentExecutions,
  UseSubagentExecutionsOptions,
} from "./types";

export const SUBAGENT_EXECUTION_EVENTS_CAPABILITY = "subagentExecutionEventsV1";

export type {
  SubagentExecutionFrameClient,
  SubagentExecutions,
  UseSubagentExecutionsOptions,
} from "./types";

const MAX_BACKFILL_PAGES = 20;
type StateMap = Record<string, SubagentExecutionState>;

interface ActiveContext {
  generation: number;
  chatId: string | null;
  token: string;
  capability: boolean;
}

function isDisconnectedStatus(status: ConnectionStatus): boolean {
  return status === "reconnecting" || status === "closed" || status === "error";
}

function clearStale(states: StateMap): StateMap {
  let changed = false;
  const next: StateMap = {};
  for (const [id, state] of Object.entries(states)) {
    if (state.connectionStale) changed = true;
    next[id] = state.connectionStale ? markExecutionConnection(state, false) : state;
  }
  return changed ? next : states;
}

export function useSubagentExecutions(
  client: SubagentExecutionFrameClient,
  options: UseSubagentExecutionsOptions,
): SubagentExecutions {
  const { chatId, token, capability } = options;
  const [byInvocationId, setByInvocationId] = useState<StateMap>({});
  const statesRef = useRef<StateMap>({});
  const activeRef = useRef<ActiveContext>({ generation: 0, chatId: null, token: "", capability: false });
  const backfillRef = useRef(new Map<string, Promise<void>>());
  const detailRef = useRef(new Map<string, Promise<SubagentExecutionState | null>>());
  const reloadRef = useRef<() => void>(() => {});

  const commit = useCallback((updater: (current: StateMap) => StateMap) => {
    const next = updater(statesRef.current);
    statesRef.current = next;
    setByInvocationId(next);
  }, []);

  const isCurrent = useCallback((context: ActiveContext) => {
    const active = activeRef.current;
    return active.generation === context.generation
      && active.chatId === context.chatId
      && active.token === context.token
      && active.capability === context.capability;
  }, []);

  const scheduleBackfill = useCallback((context: ActiveContext, invocationId: string) => {
    const key = `${context.generation}:${invocationId}`;
    if (backfillRef.current.has(key) || !isCurrent(context)) return;

    const task = (async () => {
      for (let pageCount = 0; pageCount < MAX_BACKFILL_PAGES; pageCount += 1) {
        if (!isCurrent(context)) return;
        const state = statesRef.current[invocationId];
        if (!state || !state.needsReplay) return;
        const afterSeq = state.timelineCursor;
        const page = await loadSubagentExecutionEvents(context.token, invocationId, { afterSeq });
        if (!isCurrent(context)) return;

        if (page.resetRequired) {
          const detail = await loadSubagentExecution(context.token, invocationId);
          if (!isCurrent(context)) return;
          commit((current) => {
            const existing = current[invocationId];
            if (!existing) return current;
            const recovered = applyExecutionSnapshot(existing, detail.execution);
            return {
              ...current,
              [invocationId]: {
                ...markExecutionHistoryLimited(recovered),
                timelineCursor: Math.max(recovered.timelineCursor, detail.execution.lastSeq),
              },
            };
          });
          return;
        }

        let exhausted = false;
        commit((current) => {
          const existing = current[invocationId];
          if (!existing) return current;
          let next = applyExecutionEvents(existing, page.events) ?? existing;
          if (page.events.length === 0) {
            next = { ...next, timelineCursor: Math.max(next.timelineCursor, page.lastSeq) };
          }
          if (!page.hasMore && next.needsReplay) {
            next = markExecutionHistoryLimited(next);
            exhausted = true;
          }
          return { ...current, [invocationId]: next };
        });

        if (!page.hasMore || exhausted) return;
        if (page.lastSeq <= afterSeq) {
          commit((current) => {
            const existing = current[invocationId];
            return existing
              ? { ...current, [invocationId]: markExecutionHistoryLimited(existing) }
              : current;
          });
          return;
        }
      }
      commit((current) => {
        const existing = current[invocationId];
        return existing
          ? { ...current, [invocationId]: markExecutionHistoryLimited(existing) }
          : current;
      });
    })()
      .catch((error: unknown) => {
        if (!isCurrent(context)) return;
        const message = error instanceof Error ? error.message : "执行详情同步失败";
        commit((current) => {
          const existing = current[invocationId];
          return existing
            ? { ...current, [invocationId]: { ...existing, error: message } }
            : current;
        });
      })
      .finally(() => {
        backfillRef.current.delete(key);
      });

    backfillRef.current.set(key, task);
  }, [commit, isCurrent]);

  const loadDetail = useCallback((invocationId: string): Promise<SubagentExecutionState | null> => {
    const context = activeRef.current;
    if (!context.chatId || !context.capability || !context.token) return Promise.resolve(null);
    const key = `${context.generation}:${invocationId}`;
    const existing = detailRef.current.get(key);
    if (existing) return existing;

    const task = (async (): Promise<SubagentExecutionState | null> => {
      try {
        const background = backfillRef.current.get(key);
        if (background) await background;
        if (!isCurrent(context)) return null;

        const detail = await loadSubagentExecution(context.token, invocationId);
        if (!isCurrent(context)) return null;
        commit((current) => {
          const previous = current[invocationId] ?? null;
          return {
            ...current,
            [invocationId]: applyExecutionSnapshot(previous, detail.execution),
          };
        });

        let afterSeq = 0;
        const throughSeq = detail.execution.lastSeq;
        for (let pageCount = 0; pageCount < MAX_BACKFILL_PAGES; pageCount += 1) {
          const page = await loadSubagentExecutionEvents(context.token, invocationId, {
            afterSeq,
            throughSeq,
          });
          if (!isCurrent(context)) return null;

          if (page.resetRequired) {
            commit((current) => {
              const existing = current[invocationId];
              if (!existing) return current;
              const limited = markExecutionHistoryLimited(existing);
              return {
                ...current,
                [invocationId]: {
                  ...limited,
                  timelineCursor: Math.max(limited.timelineCursor, throughSeq),
                },
              };
            });
            break;
          }

          commit((current) => {
            const existing = current[invocationId];
            if (!existing) return current;
            let next = applyExecutionEvents(existing, page.events) ?? existing;
            if (page.events.length === 0) {
              next = { ...next, timelineCursor: Math.max(next.timelineCursor, page.lastSeq) };
            }
            if (!page.hasMore && next.pendingEvents.some((event) => event.seq <= throughSeq)) {
              next = markExecutionHistoryLimited(next);
            }
            return { ...current, [invocationId]: next };
          });

          if (!page.hasMore) {
            commit((current) => {
              const existing = current[invocationId];
              if (!existing) return current;
              const next: StateMap = {
                ...current,
                [invocationId]: {
                  ...existing,
                  timelineCursor: Math.max(
                    existing.timelineCursor,
                    Math.min(throughSeq, Math.max(afterSeq, page.lastSeq)),
                  ),
                },
              };
              next[invocationId] = resumeExecutionTimeline(next[invocationId]);
              return next;
            });
            break;
          }
          if (page.lastSeq <= afterSeq) break;
          afterSeq = page.lastSeq;
        }
        return statesRef.current[invocationId] ?? null;
      } catch (error: unknown) {
        if (isCurrent(context)) {
          const message = error instanceof Error ? error.message : "执行详情同步失败";
          commit((current) => {
            const existing = current[invocationId];
            return existing
              ? { ...current, [invocationId]: { ...existing, error: message } }
              : current;
          });
        }
        return null;
      }
    })().finally(() => {
      detailRef.current.delete(key);
    });

    detailRef.current.set(key, task);
    return task;
  }, [commit, isCurrent]);

  useEffect(() => {
    const generation = activeRef.current.generation + 1;
    const context: ActiveContext = { generation, chatId, token, capability };
    activeRef.current = context;

    if (!chatId || !capability) {
      commit(() => ({}));
      return () => {
        if (activeRef.current.generation === generation) {
          activeRef.current = { generation, chatId: null, token: "", capability: false };
        }
      };
    }

    let disposed = false;
    const isLive = () => !disposed && isCurrent(context);

    const applySnapshots = (response: ExecutionListResponse): void => {
      const ids: string[] = [];
      commit((current) => {
        let next = { ...current };
        for (const snapshot of response.executions) {
          if (snapshot.chatId !== chatId) continue;
          next[snapshot.invocationId] = applyExecutionSnapshot(
            next[snapshot.invocationId] ?? null,
            snapshot,
          );
          ids.push(snapshot.invocationId);
        }
        next = clearStale(next);
        return next;
      });
      for (const invocationId of ids) {
        if (statesRef.current[invocationId]?.needsReplay) {
          scheduleBackfill(context, invocationId);
        }
      }
    };

    const loadList = async (): Promise<void> => {
      try {
        const response = await listSubagentExecutions(token, chatId, { limit: 20 });
        if (!isLive()) return;
        applySnapshots(response);
      } catch {
        // 列表是恢复路径，不是任务真值；失败保留最后可信快照。
      }
    };

    // 契约 §4：必须先订阅，再 list；list 返回前的实时事件已由 chat handler 缓存/投影。
    const unsubscribeChat = client.onChat(chatId, (raw) => {
      if (!isLive()) return;
      const frame = parseExecutionFrame(raw);
      if (!frame || frame.chatId !== chatId) return;

      if (frame.kind === "snapshot") {
        applySnapshots({
          executions: frame.executions,
          nextCursor: frame.nextCursor,
          serverNow: frame.serverNow,
        });
        return;
      }

      const invocationId = frame.event.invocationId;
      commit((current) => {
        const previous = current[invocationId] ?? null;
        return {
          ...current,
          [invocationId]: applyExecutionEvent(previous, frame.event),
        };
      });
      if (statesRef.current[invocationId]?.needsReplay) {
        scheduleBackfill(context, invocationId);
      }
    });

    const unsubscribeStatus = client.onStatus?.((status) => {
      if (!isLive()) return;
      if (isDisconnectedStatus(status)) {
        commit((current) => {
          let changed = false;
          const next: StateMap = {};
          for (const [id, state] of Object.entries(current)) {
            const updated = markExecutionConnection(state, true);
            if (updated !== state) changed = true;
            next[id] = updated;
          }
          return changed ? next : current;
        });
        return;
      }
      if (status === "open") void loadList();
    });

    reloadRef.current = () => {
      void loadList();
    };
    void loadList();

    return () => {
      disposed = true;
      unsubscribeChat();
      unsubscribeStatus?.();
      if (activeRef.current.generation === generation) {
        activeRef.current = { generation, chatId: null, token: "", capability: false };
      }
    };
  }, [chatId, capability, client, commit, isCurrent, scheduleBackfill, token]);

  const executions = useMemo(
    () => Object.values(byInvocationId).sort((left, right) => (
      left.snapshot.createdAt === right.snapshot.createdAt
        ? left.snapshot.invocationId.localeCompare(right.snapshot.invocationId)
        : left.snapshot.createdAt < right.snapshot.createdAt
          ? 1
          : -1
    )),
    [byInvocationId],
  );

  const activeCount = executions.filter(
    (state) => !isTerminalExecutionStatus(state.snapshot.status),
  ).length;

  const reload = useCallback(() => reloadRef.current(), []);

  return {
    executions,
    byInvocationId,
    activeCount,
    syncing: executions.some((state) => state.needsReplay),
    connectionStale: executions.some((state) => state.connectionStale),
    historyLimited: executions.some((state) => state.historyLimited),
    loadDetail,
    reload,
  };
}
