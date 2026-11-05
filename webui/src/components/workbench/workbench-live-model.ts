import type { Observation, WorkbenchActivity } from "./workbench-live-types";

const terminal = new Set<Observation["status"]>(["succeeded", "failed", "cancelled"]);

export function mergeWorkbenchActivity(snapshot: WorkbenchActivity | null, events: Observation[], turnId: string | null): WorkbenchActivity | null {
  const merged = new Map<string, Observation>();
  const accept = (event: Observation) => {
    if (event.turn_id !== turnId) return;
    const normalized = !event.turn_id && event.coverage === "turn" ? { ...event, coverage: "session" as const } : event;
    const key = normalized.kind === "tool" && normalized.call_id ? `tool:${normalized.turn_id}:${normalized.call_id}` : `event:${normalized.id}`;
    const previous = merged.get(key);
    if (previous && terminal.has(previous.status) && normalized.status === "running") return;
    if (!previous || normalized.sequence >= previous.sequence || (previous.status === "running" && terminal.has(normalized.status))) merged.set(key, normalized);
  };
  snapshot?.events.forEach(accept);
  events.forEach(accept);
  if (!snapshot && !merged.size) return null;
  return { phase: snapshot?.phase ?? "unknown", current_action: snapshot?.current_action ?? null, started_at: snapshot?.started_at ?? null, events: [...merged.values()].sort((a, b) => a.sequence - b.sequence) };
}
