import { useEffect, useRef, useState } from "react";
import type { ZhixuClient } from "@/lib/zhixu-client";
import { mergeWorkbenchActivity } from "./workbench-live-model";
import type { Observation, Phase } from "./workbench-live-types";
import { fetchSessionWorkbench, type SessionWorkbenchSnapshot } from "./session-workbench-api";

export type WorkbenchEventClient = Pick<ZhixuClient, "onChat" | "onSessionUpdate" | "onStatus" | "status">;
interface Options { sessionKey?: string | null; token?: string; client?: WorkbenchEventClient; projectIdentity?: string; modelTurnId?: string | null; modelEvents?: Observation[]; }
interface State { identity: string; snapshot: SessionWorkbenchSnapshot | null; loading: boolean; error: string | null; }

export function useSessionWorkbench({ sessionKey, token, client, projectIdentity, modelTurnId, modelEvents }: Options) {
  const identity = JSON.stringify([sessionKey, token, projectIdentity]);
  const [state, setState] = useState<State>({ identity, snapshot: null, loading: false, error: null });
  const [live, setLive] = useState<{ identity: string; turnId: string | null; events: Observation[]; phase: Phase; stale: boolean }>({ identity, turnId: null, events: [], phase: "unknown", stale: false });
  const refreshRef = useRef<() => void>(() => {});

  // Cleanup cancels requests, subscriptions and timers, including Strict Mode remounts.
  // https://react.dev/reference/react/useEffect#fetching-data-with-effects
  useEffect(() => {
    let disposed = false;
    let request: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let dirty = false;
    let turnId: string | null = null;
    let sequence = 0;
    let liveActive = false;
    setLive({ identity, turnId: null, events: [], phase: "unknown", stale: !!client && client.status !== "open" });
    const chatId = sessionKey?.replace(/^websocket:/, "");
    const canFetch = () => !!sessionKey && !!token && !document.hidden && (!client || client.status === "open");
    setState({ identity, snapshot: null, loading: !!sessionKey && !!token, error: null });

    const load = async () => {
      if (disposed || !canFetch()) return;
      if (request) { dirty = true; return; }
      if (timer) clearTimeout(timer);
      const controller = new AbortController();
      request = controller;
      setState((current) => ({ ...current, loading: !current.snapshot, error: null }));
      let active = false;
      try {
        const snapshot = await fetchSessionWorkbench(token!, sessionKey!, controller.signal);
        if (disposed || controller.signal.aborted) return;
        active = snapshot.tasks.some((task) => task.status === "in_progress")
          || snapshot.agents.some((agent) => ["running", "pending", "queued"].includes(agent.status))
          || ["processing", "tool_running", "answering", "waiting_user"].includes(snapshot.activity?.phase ?? "") || liveActive;
        setState({ identity, snapshot, loading: false, error: null });
        setLive((old) => old.identity === identity ? { ...old, stale: false } : old);
      } catch (reason) {
        if (disposed || controller.signal.aborted) return;
        setState((current) => ({ ...current, loading: false, error: reason instanceof Error ? reason.message : "无法读取工作台" }));
        setLive((old) => old.identity === identity ? { ...old, stale: true } : old);
      } finally {
        request = null;
        if (!disposed && canFetch()) {
          if (dirty) { dirty = false; timer = setTimeout(() => void load(), 350); }
          else timer = setTimeout(() => void load(), active ? 10_000 : 30_000);
        }
      }
    };
    const refresh = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void load(), 200);
    };
    refreshRef.current = refresh;
    const onVisibility = () => {
      if (document.hidden) { if (timer) clearTimeout(timer); }
      else refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    const unsubChat = chatId ? client?.onChat(chatId, (event) => {
      if ((event.event === "message_accepted" && event.starts_turn !== false) || (event.event === "user_message" && event.starts_turn)) {
        turnId = event.turn_id ?? null;
        sequence = 0;
        liveActive = true;
        setLive({ identity, turnId, events: [], phase: "processing", stale: false });
      } else {
        const incomingTurn = "turn_id" in event ? event.turn_id ?? null : null;
        if (incomingTurn && turnId && incomingTurn !== turnId) return;
        if (!turnId && incomingTurn) turnId = incomingTurn;
        const currentTurn = turnId;
        if (event.event === "turn_end" || event.event === "error") liveActive = false;
        if (event.event === "message" && event.tool_events?.length) {
          liveActive = true;
          const additions: Observation[] = event.tool_events.flatMap((tool) => {
            if (!tool.call_id || !["start", "end", "error"].includes(tool.phase ?? "")) return [];
            return [{ id: tool.call_id, sequence: ++sequence, turn_id: currentTurn, call_id: tool.call_id, at: null, kind: "tool" as const,
              status: tool.phase === "start" ? "running" as const : tool.phase === "error" ? "failed" as const : "succeeded" as const,
              label: tool.name ?? "tool", resource: null, extent: null, write_kind: null, source: "runtime" as const, coverage: currentTurn ? "turn" as const : "session" as const }];
          });
          setLive((old) => {
            const activity = mergeWorkbenchActivity({ phase: old.phase, current_action: null, started_at: null, events: old.events }, additions, currentTurn);
            const running = activity?.events.some((entry) => entry.status === "running");
            return { ...old, turnId: currentTurn, events: activity?.events ?? [], phase: running ? "tool_running" : "processing" };
          });
        } else if (event.event === "delta" || event.event === "stream_end") setLive((old) => ({ ...old, phase: "answering" }));
        else if (event.event === "turn_end") setLive((old) => ({ ...old, phase: event.outcome === "failed" ? "failed" : event.outcome === "interrupted" || event.outcome === "cancelled" ? "interrupted" : "completed" }));
        else if (event.event === "error") setLive((old) => ({ ...old, phase: "failed" }));
        else if (event.event === "question_requested") setLive((old) => ({ ...old, phase: "waiting_user" }));
      }
      if (!["delta", "reasoning_delta"].includes(event.event)) refresh();
    }) : undefined;
    const unsubSession = client?.onSessionUpdate((id) => {
      if (id === chatId || id === sessionKey) refresh();
    });
    const unsubStatus = client?.onStatus((status) => {
      if (status === "open") refresh();
      else { if (timer) clearTimeout(timer); request?.abort(); setLive((old) => ({ ...old, stale: true })); }
    });
    void load();
    return () => {
      disposed = true;
      request?.abort();
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      unsubChat?.(); unsubSession?.(); unsubStatus?.();
      refreshRef.current = () => {};
    };
  }, [identity, sessionKey, token, client]);

  const visible = state.identity === identity ? state : { snapshot: null, loading: !!sessionKey && !!token, error: null };
  const currentLive = live.identity === identity ? live : null;
  const activeTurn = currentLive?.turnId ?? modelTurnId ?? visible.snapshot?.scope?.turn_id ?? null;
  const snapshotActivity = currentLive?.turnId && visible.snapshot?.scope?.turn_id !== currentLive.turnId ? null : visible.snapshot?.activity ?? null;
  const fallback = currentLive?.phase !== "unknown" ? { phase: currentLive?.phase ?? "unknown" as const, current_action: null, started_at: null, events: [] } : null;
  const withMessages = mergeWorkbenchActivity(snapshotActivity ?? fallback, modelTurnId === activeTurn ? modelEvents ?? [] : [], activeTurn);
  const merged = mergeWorkbenchActivity(withMessages, currentLive?.events ?? [], activeTurn);
  const activity = merged && currentLive && (currentLive.turnId || currentLive.events.length || currentLive.stale || modelEvents?.length)
    ? { ...merged, phase: currentLive.stale ? "unknown" as const : currentLive.phase === "unknown" ? (merged.events.some(event => event.status === "running") ? "tool_running" : merged.phase) : currentLive.phase,
      current_action: currentLive.stale ? null : [...merged.events].reverse().find((item) => item.status === "running")?.label ?? null,
      events: currentLive.stale || ["completed", "failed", "interrupted"].includes(currentLive.phase) ? merged.events.filter((item) => item.status !== "running") : merged.events } : merged;
  return { ...visible, activity, stale: currentLive?.stale ?? false, refresh: () => refreshRef.current() };
}
