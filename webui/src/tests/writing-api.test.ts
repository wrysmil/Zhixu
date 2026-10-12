import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createArticle,
  deleteArticle,
  duplicateArticle,
  fetchArticle,
  fetchArticleList,
  parseArticleConflict,
  saveArticle,
  toArticle,
  toSnapshotPayload,
} from "@/lib/writing-api";

describe("writing-api", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("builds the contract list URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ articles: [], total: 0, limit: 50, offset: 0 }),
      { status: 200, headers: { "content-type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    await fetchArticleList("tok", { q: "记忆", status: "draft", limit: 20, offset: 40 });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/api/writing/articles?");
    expect(url).toContain("q=%E8%AE%B0%E5%BF%86");
    expect(url).toContain("status=draft");
    expect(url).toContain("limit=20");
    expect(url).toContain("offset=40");
  });

  it("omits undefined params and the all-status sentinel", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ articles: [], total: 0, limit: 50, offset: 0 }),
      { status: 200, headers: { "content-type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    await fetchArticleList("tok", { status: "all" });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/api/writing/articles");
    expect(url).not.toContain("status=all");
    expect(url).not.toContain("status=");
    expect(url).not.toContain("limit=");
    expect(url).not.toContain("offset=");
  });

  it("maps the summary rows to camelCase views", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({
        articles: [
          {
            id: "a1",
            title: "T",
            summary: "S",
            status: "draft",
            writing_type: "技术",
            tags: ["x"],
            revision: 3,
            created_at: "2026-01-01T00:00:00+00:00",
            updated_at: "2026-01-02T00:00:00+00:00",
          },
        ],
        total: 1,
        limit: 50,
        offset: 0,
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    const page = await fetchArticleList("tok", {});
    expect(page.articles[0]).toEqual({
      id: "a1",
      title: "T",
      summary: "S",
      status: "draft",
      writingType: "技术",
      tags: ["x"],
      revision: 3,
      updatedAt: "2026-01-02T00:00:00+00:00",
    });
    expect(page.total).toBe(1);
  });

  it("fetches one article and maps snake_case to camelCase", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({
        article: {
          id: "a1",
          workspace_key: "deadbeefdeadbeef",
          title: "T",
          writing_type: "",
          template_id: "tech-explainer",
          status: "idea",
          summary: "",
          brief: {},
          body: "b",
          outline: [{ id: "o1", text: "x", order: 0 }],
          materials: [
            {
              id: "m1",
              type: "link",
              title: "l",
              content: "c",
              url: "https://e.com",
              included: true,
              order: 0,
              created_at: "2026-01-01T00:00:00+00:00",
              updated_at: "2026-01-01T00:00:00+00:00",
            },
          ],
          tags: [],
          revision: 2,
          created_at: "2026-01-01T00:00:00+00:00",
          updated_at: "2026-01-03T00:00:00+00:00",
        },
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    const article = await fetchArticle("tok", "a1");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/api/writing/articles/get?id=a1");
    expect(article.templateId).toBe("tech-explainer");
    expect(article.revision).toBe(2);
    expect(article.outline[0]).toEqual({ id: "o1", text: "x", order: 0 });
    expect(article.materials[0].createdAt).toBe("2026-01-01T00:00:00+00:00");
    expect(article.materials[0].url).toBe("https://e.com");
    expect(article.updatedAt).toBe("2026-01-03T00:00:00+00:00");
  });

  it("parses the contract conflict message literally", () => {
    expect(parseArticleConflict("article_conflict current_revision=13 article_id=a1"))
      .toEqual({ currentRevision: 13, articleId: "a1" });
    expect(parseArticleConflict("article deleted")).toBeNull();
    expect(parseArticleConflict("")).toBeNull();
  });

  it("sends snake_case payload with base_revision and client_request_id", async () => {
    const requestMutation = vi.fn().mockResolvedValue({
      article_id: "a1", revision: 2, updated_at: "t", digest: "d", no_change: false,
    });
    await saveArticle({ requestMutation } as never, {
      articleId: "a1", baseRevision: 1, clientRequestId: "r-1",
      snapshot: {
        title: "标题", writing_type: "", status: "draft", summary: "",
        brief: {}, body: "正文", outline: [], materials: [], tags: [],
      },
    });
    expect(requestMutation.mock.calls[0][0]).toBe("article.save");
    expect(requestMutation.mock.calls[0][1]).toMatchObject({
      article_id: "a1", base_revision: 1, client_request_id: "r-1",
    });
    expect(requestMutation.mock.calls[0][1].snapshot).toMatchObject({
      title: "标题", status: "draft", body: "正文",
    });
  });

  it("serializes snapshot materials to snake_case wire fields", async () => {
    const requestMutation = vi.fn().mockResolvedValue({
      article_id: "a1", revision: 2, updated_at: "t", digest: "d", no_change: false,
    });
    await saveArticle({ requestMutation } as never, {
      articleId: "a1", baseRevision: 1, clientRequestId: "r-1",
      snapshot: {
        title: "T", writing_type: "wt", template_id: "tech-explainer", status: "draft",
        summary: "", brief: {}, body: "", outline: [], tags: [],
        materials: [
          {
            id: "m1", type: "note", title: "n", content: "c",
            included: false, order: 1,
            createdAt: "2026-01-01T00:00:00+00:00",
            updatedAt: "2026-01-01T00:00:00+00:00",
          },
        ],
      },
    });
    const wire = requestMutation.mock.calls[0][1].snapshot.materials[0];
    expect(wire.created_at).toBe("2026-01-01T00:00:00+00:00");
    expect(wire.updated_at).toBe("2026-01-01T00:00:00+00:00");
    expect(wire.createdAt).toBeUndefined();
    expect(wire.updatedAt).toBeUndefined();
    expect(requestMutation.mock.calls[0][1].snapshot.template_id).toBe("tech-explainer");
  });

  it("creates an article with the contract action and returns the mapped article", async () => {
    const requestMutation = vi.fn().mockResolvedValue({
      article: {
        id: "new1", workspace_key: "k", title: "T", writing_type: "", template_id: "",
        status: "idea", summary: "", brief: {}, body: "", outline: [], materials: [],
        tags: [], revision: 1,
        created_at: "2026-01-01T00:00:00+00:00", updated_at: "2026-01-01T00:00:00+00:00",
      },
    });
    const article = await createArticle({ requestMutation } as never, {
      clientRequestId: "r-9",
      title: "T",
      templateId: "practice-retro",
      outline: [{ id: "o1", text: "x", order: 0 }],
    });
    expect(requestMutation.mock.calls[0][0]).toBe("article.create");
    expect(requestMutation.mock.calls[0][1]).toMatchObject({
      client_request_id: "r-9",
      title: "T",
      template_id: "practice-retro",
      outline: [{ id: "o1", text: "x", order: 0 }],
    });
    expect(article.id).toBe("new1");
    expect(article.templateId).toBeUndefined();
  });

  it("duplicates with base_revision and maps the returned article", async () => {
    const requestMutation = vi.fn().mockResolvedValue({
      article: {
        id: "copy1", workspace_key: "k", title: "T", writing_type: "", template_id: "",
        status: "idea", summary: "", brief: {}, body: "", outline: [], materials: [],
        tags: [], revision: 1,
        created_at: "t", updated_at: "t",
      },
    });
    const copy = await duplicateArticle(
      { requestMutation } as never,
      { articleId: "a1", baseRevision: 4, clientRequestId: "r-2" },
    );
    expect(requestMutation.mock.calls[0][0]).toBe("article.duplicate");
    expect(requestMutation.mock.calls[0][1]).toEqual({
      client_request_id: "r-2", article_id: "a1", base_revision: 4,
    });
    expect(copy.id).toBe("copy1");
  });

  it("deletes and maps the snake_case result", async () => {
    const requestMutation = vi.fn().mockResolvedValue({
      deleted_article_id: "a1", revision: 6,
    });
    const result = await deleteArticle(
      { requestMutation } as never,
      { articleId: "a1", baseRevision: 5, clientRequestId: "r-3" },
    );
    expect(requestMutation.mock.calls[0][0]).toBe("article.delete");
    expect(requestMutation.mock.calls[0][1]).toEqual({
      client_request_id: "r-3", article_id: "a1", base_revision: 5,
    });
    expect(result).toEqual({ deletedArticleId: "a1", revision: 6 });
  });

  it("toSnapshotPayload keeps component-side camelCase articles out of wire names", () => {
    const article = toArticle({
      id: "a1", workspace_key: "k", title: "T", writing_type: "wt", template_id: "tech-explainer",
      status: "review", summary: "s", brief: { goal: "g" }, body: "b",
      outline: [{ id: "o1", text: "x", order: 0 }], materials: [], tags: ["t1"], revision: 7,
      created_at: "c", updated_at: "u",
    });
    const snapshot = toSnapshotPayload(article);
    expect(snapshot).toEqual({
      title: "T",
      writing_type: "wt",
      template_id: "tech-explainer",
      status: "review",
      summary: "s",
      brief: { goal: "g" },
      body: "b",
      outline: [{ id: "o1", text: "x", order: 0 }],
      materials: [],
      tags: ["t1"],
    });
  });
});
