import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createMemory,
  deleteEpisode,
  deleteMemory,
  fetchEpisode,
  fetchMemory,
  fetchMemoryStats,
  fetchScratchpad,
  listEpisodes,
  listMemories,
  saveScratchpad,
  searchMemories,
  updateMemory,
  type WebUIMutationTransport,
} from "@/lib/api";

const requestMutation = vi.fn();
const mutationTransport: WebUIMutationTransport = {
  requestMutation: <T>(
    action: string,
    payload?: Record<string, unknown>,
    timeoutMs?: number,
  ) => requestMutation(action, payload, timeoutMs) as Promise<T>,
};

describe("memory api helpers", () => {
  beforeEach(() => {
    requestMutation.mockReset();
    requestMutation.mockResolvedValue({});
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ items: [], query: "x" }),
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists memories with typed query params", async () => {
    await listMemories("tok", { type: "preference", order: "importance", limit: 5 });

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/memory/memories?type=preference&order=importance&limit=5",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
        credentials: "same-origin",
      }),
    );
  });

  it("omits query params that are left unset", async () => {
    await listMemories("tok");

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/memory/memories",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("combines memory provenance, workspace, query and priority filters", async () => {
    await listMemories("tok", { origin: "manual", workspace: "ws-1", q: "边界", priority: "long_term", order: "updated" });
    const url = new URL(vi.mocked(fetch).mock.calls[0][0] as string, "http://localhost");
    expect(Object.fromEntries(url.searchParams)).toEqual({ origin: "manual", workspace: "ws-1", q: "边界", priority: "long_term", order: "updated" });
  });

  it("searches memories with q and type filters", async () => {
    await searchMemories("tok", "Python", { type: "fact", limit: 20 });

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/memory/memories/search?q=Python&type=fact&limit=20",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("percent-encodes memory and episode ids", async () => {
    await fetchMemory("tok", "mem+/=");
    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/memory/memories/get?id=mem%2B%2F%3D",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );

    await fetchEpisode("tok", "ep 1");
    expect(fetch).toHaveBeenLastCalledWith(
      "/api/settings/memory/episodes/get?id=ep%201",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("lists episodes with an optional session filter", async () => {
    await listEpisodes("tok", { session_id: "websocket:chat-1" });

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/memory/episodes?session_id=websocket%3Achat-1",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("fetches the scratchpad and memory stats", async () => {
    await fetchScratchpad("tok");
    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/memory/scratchpad",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );

    await fetchMemoryStats("tok");
    expect(fetch).toHaveBeenLastCalledWith(
      "/api/settings/memory/stats",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("creates a memory over the WebSocket", async () => {
    await createMemory(mutationTransport, {
      content: "用户偏好繁体中文",
      type: "preference",
      priority: "long_term",
      importanceScore: 0.8,
      tags: ["中文"],
    });

    expect(requestMutation).toHaveBeenCalledWith(
      "memory.create",
      {
        content: "用户偏好繁体中文",
        type: "preference",
        priority: "long_term",
        importance_score: 0.8,
        tags: ["中文"],
      },
      20_000,
    );
    expect(fetch).not.toHaveBeenCalled();
  });

  it("omits unset memory fields from create/update payloads", async () => {
    await createMemory(mutationTransport, { content: "hello", type: "fact" });
    expect(requestMutation).toHaveBeenLastCalledWith(
      "memory.create",
      { content: "hello", type: "fact" },
      20_000,
    );

    await updateMemory(mutationTransport, "mem_1", { content: "updated" });
    expect(requestMutation).toHaveBeenLastCalledWith(
      "memory.update",
      { id: "mem_1", content: "updated" },
      20_000,
    );
  });

  it("deletes memories and episodes over the WebSocket", async () => {
    await deleteMemory(mutationTransport, "mem_1");
    expect(requestMutation).toHaveBeenLastCalledWith("memory.delete", { id: "mem_1" }, 20_000);

    await deleteEpisode(mutationTransport, "ep_1");
    expect(requestMutation).toHaveBeenLastCalledWith("episode.delete", { id: "ep_1" }, 20_000);
  });

  it("stores the scratchpad with camelCase keys converted to snake_case", async () => {
    await saveScratchpad(mutationTransport, {
      content: "focus notes",
      activeProjects: ["webui"],
      currentFocus: "memory section",
      openQuestions: ["qa"],
      nextSteps: ["run tests"],
    });

    expect(requestMutation).toHaveBeenCalledWith(
      "scratchpad.save",
      {
        content: "focus notes",
        active_projects: ["webui"],
        current_focus: "memory section",
        open_questions: ["qa"],
        next_steps: ["run tests"],
      },
      20_000,
    );
  });
});
