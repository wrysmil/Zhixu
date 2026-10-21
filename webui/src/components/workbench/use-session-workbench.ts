import { useEffect, useRef, useState } from "react";
import type { ZhixuClient } from "@/lib/zhixu-client";
import { fetchSessionWorkbench, type SessionWorkbenchSnapshot } from "./session-workbench-api";

export type WorkbenchEventClient = Pick<ZhixuClient, "onChat" | "onSessionUpdate" | "onStatus" | "status">;
interface Options { sessionKey?: string | null; token?: string; client?: WorkbenchEventClient; }
interface State { identity: string; snapshot: SessionWorkbenchSnapshot | null; loading: boolean; error: string | null; }

export function useSessionWorkbench({ sessionKey, token, client }: Options) {
  const identity = JSON.stringify([sessionKey, token]);
  const [state, setState] = useState<State>({ identity, snapshot: null, loading: false, error: null });
  const refreshRef = useRef<() => void>(() => {});

  // Cleanup cancels requests, subscriptions and timers, including Strict Mode remounts.
  // https://react.dev/reference/react/useEffect#fetching-data-with-effects
  useEffect(() => {
    let disposed = false;
    let request: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let dirty = false;
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
          || snapshot.agents.some((agent) => ["running", "pending", "queued"].includes(agent.status));
        setState({ identity, snapshot, loading: false, error: null });
      } catch (reason) {
        if (disposed || controller.signal.aborted) return;
        setState((current) => ({ ...current, loading: false, error: reason instanceof Error ? reason.message : "无法读取工作台" }));
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
      if (!["delta", "reasoning_delta"].includes(event.event)) refresh();
    }) : undefined;
    const unsubSession = client?.onSessionUpdate((id) => {
      if (id === chatId || id === sessionKey) refresh();
    });
    const unsubStatus = client?.onStatus((status) => {
      if (status === "open") refresh();
      else if (timer) clearTimeout(timer);
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
  return { ...visible, refresh: () => refreshRef.current() };
}
