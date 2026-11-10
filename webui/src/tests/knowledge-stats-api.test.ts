import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchKnowledgeStats } from "@/lib/api";

const BASE = "http://127.0.0.1:8765";

function stubStats(payload: Record<string, unknown>) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("fetchKnowledgeStats vector 归一化", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("后端对象口径取 ok_docs", async () => {
    stubStats({
      enabled: true,
      kbs: 1,
      documents: 1,
      chunks: 4,
      pending: 0,
      vector: {
        status: "ready",
        backend: "chromadb",
        model: "bge-m3",
        dimensions: 1024,
        ok_docs: 1,
        failed_docs: 0,
        pending_docs: 0,
      },
    });
    const stats = await fetchKnowledgeStats("tok", {}, BASE);
    expect(stats.vector).toBe(1);
  });

  it("兼容裸数字并保留 0", async () => {
    stubStats({ enabled: true, vector: 0 });
    const stats = await fetchKnowledgeStats("tok", {}, BASE);
    expect(stats.vector).toBe(0);
  });

  it("缺失或形状不认识时给 null，不伪造 0", async () => {
    stubStats({ enabled: true, vector: { status: "disabled" } });
    const stats = await fetchKnowledgeStats("tok", {}, BASE);
    expect(stats.vector).toBeNull();
  });
});
