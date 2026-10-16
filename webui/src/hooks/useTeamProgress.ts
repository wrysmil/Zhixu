import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { parseTeamEvent, parseTeamFrame } from "@/lib/teams/events";
import {
  applyTeamEvent,
  applyTeamSnapshot,
  type TeamRunState,
} from "@/lib/teams/reducer";
import {
  cancelTeamRun,
  cancelTeamTask,
  listTeamRuns,
  loadTeamDetail,
  loadTeamRun,
  loadTeamRunEvents,
  retryTeamRun,
  type TeamRunEventsResponse,
} from "@/lib/teams/api";
import type { WebUIMutationTransport } from "@/lib/api";
import type { TeamRunSnapshot } from "@/lib/teams/types";

/** Minimal client surface: the hook only needs the per-chat frame subscription. */
export interface TeamFrameClient {
  onChat(chatId: string, handler: (event: unknown) => void): () => void;
}

export interface TeamProgress {
  /** Newest first; a chat can hold several runs (retries open new ones). */
  runs: TeamRunState[];
  activeRun: TeamRunState | null;
  /** memberId → display name, from the frozen revision this run was bound to. */
  memberNames: Record<string, string>;
  needsReplay: boolean;
  cancelRun(runId: string): Promise<void>;
  cancelTask(runId: string, taskId: string): Promise<void>;
  retryRun(runId: string): Promise<void>;
}

const HISTORY_LIMIT = 20;

/** 缺口补拉的两种结局：能按 seq 接就接，服务端说增量没了才整份回快照。 */
type BackfillOutcome =
  | { kind: "page"; page: TeamRunEventsResponse }
  | { kind: "snapshot"; run: TeamRunSnapshot };

function newRequestId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * Live team progress for one chat.
 *
 * The panel is a *projection*, never a second source of truth: frames arrive over the
 * chat subscription, gaps and pruned increments are answered by re-reading the run
 * snapshot, and history comes from the run list. A frame we cannot parse is ignored so
 * an unknown team event can never disturb an ordinary chat.
 */
export function useTeamProgress(
  client: TeamFrameClient,
  options: {
    chatId: string | null;
    token: string;
    transport?: WebUIMutationTransport | null;
  },
): TeamProgress {
  const { chatId, token, transport } = options;
  const [runs, setRuns] = useState<Record<string, TeamRunState>>({});
  const [memberNames, setMemberNames] = useState<Record<string, string>>({});
  const replaying = useRef<Set<string>>(new Set());

  useEffect(() => {
    setRuns({});
    setMemberNames({});
  }, [chatId]);

  const resync = useCallback(
    (runId: string, afterSeq?: number) => {
      if (replaying.current.has(runId)) return;
      replaying.current.add(runId);
      const backfill = async (): Promise<BackfillOutcome> => {
        // 缺口优先按 seq 补拉（契约 §6）；服务端说增量已被剪掉（resetRequired）才回快照。
        const page = await loadTeamRunEvents(token, runId, afterSeq ? { afterSeq } : {});
        if (page.resetRequired) return { kind: "snapshot", run: (await loadTeamRun(token, runId)).run };
        return { kind: "page", page };
      };
      void backfill()
        .catch(() => loadTeamRun(token, runId).then((detail) => ({
          kind: "snapshot" as const,
          run: detail.run,
        })))
        .then((outcome) => {
          setRuns((current) => {
            if (outcome.kind === "page") {
              let next: TeamRunState | null = current[runId] ?? null;
              for (const raw of outcome.page.events) {
                const event = parseTeamEvent(raw);
                if (event) next = applyTeamEvent(next, event);
              }
              // 页与页之间仍可能有缺口，剩下的交给下一轮补拉，不硬拼。
              return next ? { ...current, [runId]: next } : current;
            }
            return { ...current, [runId]: applyTeamSnapshot(current[runId] ?? null, outcome.run) };
          });
        })
        .catch(() => {
          // A failed resync keeps the last known state; the next frame may close the gap.
        })
        .finally(() => {
          replaying.current.delete(runId);
        });
    },
    [token],
  );

  useEffect(() => {
    if (!chatId) return;
    let disposed = false;
    void listTeamRuns(token, chatId, { limit: HISTORY_LIMIT })
      .then((page) => {
        if (disposed) return;
        setRuns((current) => {
          const next = { ...current };
          for (const run of page.runs) {
            next[run.runId] = applyTeamSnapshot(current[run.runId] ?? null, run);
          }
          return next;
        });
      })
      .catch(() => {
        // History is best effort: the live stream is the primary source.
      });
    return () => {
      disposed = true;
    };
  }, [chatId, token]);

  useEffect(() => {
    if (!chatId) return;
    return client.onChat(chatId, (raw) => {
      const frame = parseTeamFrame(raw);
      if (!frame) return;
      if (frame.kind === "snapshot") {
        setRuns((current) => ({
          ...current,
          [frame.run.runId]: applyTeamSnapshot(current[frame.run.runId] ?? null, frame.run),
        }));
        return;
      }
      setRuns((current) => {
        const existing = current[frame.event.runId];
        if (!existing) {
          // run.started 自带绑定，够把面板立起来；其余增量只知道 runId，去问整份。
          const seeded = applyTeamEvent(null, frame.event);
          if (seeded) return { ...current, [seeded.runId]: seeded };
          resync(frame.event.runId);
          return current;
        }
        const next = applyTeamEvent(existing, frame.event);
        if (!next) return current;
        if (next.needsReplay) resync(next.runId, next.lastSeq);
        return { ...current, [next.runId]: next };
      });
    });
  }, [chatId, client, resync]);

  const ordered = useMemo(
    () =>
      Object.values(runs).sort((left, right) =>
        left.startedAt === right.startedAt
          ? left.runId.localeCompare(right.runId)
          : left.startedAt < right.startedAt
            ? 1
            : -1,
      ),
    [runs],
  );

  const activeRun = useMemo(() => {
    if (!ordered.length) return null;
    const live = ordered.filter((run) => run.status === "running" || run.status === "cancelling");
    return live[0] ?? ordered[0];
  }, [ordered]);

  const boundTeam = activeRun?.teamId ?? "";
  const boundRevision = activeRun?.teamRevision ?? 0;
  useEffect(() => {
    if (!boundTeam || !boundRevision) return;
    let disposed = false;
    void loadTeamDetail(token, boundTeam, { revision: boundRevision })
      .then((detail) => {
        if (disposed) return;
        const names: Record<string, string> = {};
        for (const member of detail.snapshot.members) {
          names[member.member.memberId] = member.profile.name || member.member.agentId;
        }
        setMemberNames(names);
      })
      .catch(() => {
        // 名字拿不到就退回显示 memberId：面板不能因为取不到名字就不显示任务。
      });
    return () => {
      disposed = true;
    };
  }, [boundTeam, boundRevision, token]);

  const cancelRun = useCallback(
    async (runId: string) => {
      if (!transport) return;
      const response = await cancelTeamRun(transport, runId, newRequestId());
      setRuns((current) => {
        const existing = current[runId];
        if (!existing) return current;
        // 服务端说还在 cancelling 就显示 cancelling：不能假装已经停干净了。
        return {
          ...current,
          [runId]: { ...existing, status: response.status as TeamRunState["status"] },
        };
      });
    },
    [transport],
  );

  const cancelTask = useCallback(
    async (runId: string, taskId: string) => {
      if (!transport) return;
      await cancelTeamTask(transport, runId, taskId, newRequestId());
    },
    [transport],
  );

  const retryRun = useCallback(
    async (runId: string) => {
      if (!transport) return;
      await retryTeamRun(transport, runId, newRequestId());
    },
    [transport],
  );

  return {
    runs: ordered,
    activeRun,
    memberNames,
    needsReplay: ordered.some((run) => run.needsReplay),
    cancelRun,
    cancelTask,
    retryRun,
  };
}
