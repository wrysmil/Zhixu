import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchSessionWorkbench, fetchWorkbenchMemories, fetchWorkbenchResource } from "@/components/workbench/session-workbench-api";
const base = { schema_version: 1, session_key: "a:b", updated_at: null, goal: null, tasks: [], plan: null, agents: [], memories: [], skills: [], mcp: [], limitations: [] };
afterEach(() => vi.unstubAllGlobals());
describe("workbench live API", () => {
  it("accepts a legacy response and preserves a null activity time", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json(base)).mockResolvedValueOnce(Response.json({ ...base, activity: { phase: "unknown", current_action: null, started_at: null, events: [] } })));
    expect((await fetchSessionWorkbench("t", "a:b", new AbortController().signal)).activity).toBeUndefined();
    expect((await fetchSessionWorkbench("t", "a:b", new AbortController().signal)).activity?.started_at).toBeNull();
  });
  it("rejects malformed optional live fields", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ ...base, activity: { phase: "running", events: [] } })));
    await expect(fetchSessionWorkbench("t", "a:b", new AbortController().signal)).rejects.toThrow();
  });
  it("encodes identifiers once and validates pages", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ items: [], total: 0, next_cursor: null, revision: "r" })).mockResolvedValueOnce(Response.json({ resource: { id: "a/b", kind: "note", scope: "global", name: "n", title: "N" }, content: "x", updated_at: null, truncated: false }));
    vi.stubGlobal("fetch", fetcher);
    const signal = new AbortController().signal;
    await fetchWorkbenchMemories("t", "a:b", "global", null, signal);
    await fetchWorkbenchResource("t", "a:b", "a/b", signal);
    expect(fetcher.mock.calls[1][0]).toContain("/a%2Fb");
  });
});
