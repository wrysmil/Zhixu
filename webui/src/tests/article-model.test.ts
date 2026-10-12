import { describe, expect, it } from "vitest";
import {
  buildExportFilename,
  buildSnapshot,
  canAcceptSuggestion,
  countBodyChars,
  createDraftInput,
  duplicateTitle,
  filterByStatus,
  generateMarkdown,
  isDirty,
  isSafeUrl,
  nextLocalEditSeq,
  searchArticles,
} from "@/components/creative/article-model";
import type {
  Article,
  ArticleSummaryView,
} from "@/components/creative/types";

function fakeArticle(overrides: Partial<Article> = {}): Article {
  return {
    id: "art-1",
    title: "Test",
    writingType: "",
    status: "draft",
    summary: "",
    brief: {},
    body: "",
    outline: [],
    materials: [],
    tags: [],
    revision: 1,
    createdAt: "2026-01-01T00:00:00+00:00",
    updatedAt: "2026-01-01T00:00:00+00:00",
    ...overrides,
  };
}

function summary(overrides: Partial<ArticleSummaryView> = {}): ArticleSummaryView {
  return {
    id: "s1",
    title: "Alpha",
    summary: "about cats",
    status: "draft",
    writingType: "",
    tags: [],
    revision: 1,
    updatedAt: "2026-01-01T00:00:00+00:00",
    ...overrides,
  };
}

describe("article-model", () => {
  describe("nextLocalEditSeq", () => {
    it("increments the local edit counter without touching revision", () => {
      expect(nextLocalEditSeq(0)).toBe(1);
      expect(nextLocalEditSeq(12)).toBe(13);
    });
  });

  describe("canAcceptSuggestion", () => {
    it("returns false for different articles", () => {
      expect(
        canAcceptSuggestion(
          { id: "art-1", revision: 0 },
          { articleId: "art-2", baseRevision: 0 },
        ),
      ).toBe(false);
    });

    it("returns false for old revision", () => {
      expect(
        canAcceptSuggestion(
          { id: "art-1", revision: 3 },
          { articleId: "art-1", baseRevision: 1 },
        ),
      ).toBe(false);
    });

    it("returns true for matching article and revision", () => {
      expect(
        canAcceptSuggestion(
          { id: "art-1", revision: 2 },
          { articleId: "art-1", baseRevision: 2 },
        ),
      ).toBe(true);
    });
  });

  describe("createDraftInput", () => {
    it("throws on blank title (caller validates first)", () => {
      expect(() => createDraftInput("   ")).toThrow();
    });

    it("produces a create request with a retry-stable client_request_id", () => {
      const input = createDraftInput(" My Post ", "source-reading");
      expect(input.title).toBe("My Post");
      expect(input.templateId).toBe("source-reading");
      expect(input.clientRequestId).toMatch(/^[A-Za-z0-9._:-]{1,128}$/);
      expect(input.outline!.length).toBeGreaterThan(0);
      expect(input.outline![0]).toMatchObject({ order: 0 });
      expect(input.outline![0]).toHaveProperty("id");
      expect(input.outline![0]).toHaveProperty("text");
    });

    it("renders template outline through the translate function", () => {
      const input = createDraftInput("t", "tech-explainer", (key) => `T:${key}`);
      expect(input.outline![0].text).toBe("T:creative.template.tech-explainer.outline.0");
    });

    it("keeps an empty outline without a template", () => {
      const input = createDraftInput("t");
      expect(input.outline).toEqual([]);
      expect(input.templateId).toBeUndefined();
    });
  });

  describe("duplicateTitle", () => {
    it("marks the copy suffix", () => {
      expect(duplicateTitle("原文")).toBe("原文（副本）");
    });
  });

  describe("countBodyChars", () => {
    it("counts unicode characters after removing all whitespace", () => {
      expect(countBodyChars("a 中\nb")).toBe(3);
      expect(countBodyChars("")).toBe(0);
      expect(countBodyChars("   \t\n ")).toBe(0);
      expect(countBodyChars("中文字数统计")).toBe(6);
    });
  });

  describe("buildSnapshot", () => {
    it("maps a camelCase article to the protocol snapshot payload", () => {
      const snapshot = buildSnapshot(fakeArticle({
        writingType: "wt",
        templateId: "tech-explainer",
        status: "review",
        summary: "s",
        body: "b",
        tags: ["t1"],
      }));
      expect(snapshot).toEqual({
        title: "Test",
        writing_type: "wt",
        template_id: "tech-explainer",
        status: "review",
        summary: "s",
        brief: {},
        body: "b",
        outline: [],
        materials: [],
        tags: ["t1"],
      });
    });
  });

  describe("isDirty", () => {
    it("is false for identical snapshots", () => {
      const a = buildSnapshot(fakeArticle({ body: "x", tags: ["t"] }));
      const b = buildSnapshot(fakeArticle({ body: "x", tags: ["t"] }));
      expect(isDirty(a, b)).toBe(false);
    });

    it("is true when the body changed", () => {
      const a = buildSnapshot(fakeArticle({ body: "x" }));
      const b = buildSnapshot(fakeArticle({ body: "y" }));
      expect(isDirty(a, b)).toBe(true);
    });

    it("is insensitive to key insertion order", () => {
      const a = buildSnapshot(fakeArticle({ brief: { goal: "g", style: "s" } }));
      const b = { ...a, brief: { style: "s", goal: "g" } };
      expect(isDirty(a, b)).toBe(false);
    });

    it("is true when a material flipped inclusion", () => {
      const article = fakeArticle({
        materials: [
          {
            id: "m1", type: "note", title: "n", content: "c",
            included: true, order: 0,
            createdAt: "2026-01-01T00:00:00+00:00", updatedAt: "2026-01-01T00:00:00+00:00",
          },
        ],
      });
      const a = buildSnapshot(article);
      const b = buildSnapshot({
        ...article,
        materials: [{ ...article.materials[0], included: false }],
      });
      expect(isDirty(a, b)).toBe(true);
    });
  });

  describe("searchArticles", () => {
    const articles = [
      summary({ id: "1", title: "Alpha", summary: "about cats" }),
      summary({ id: "2", title: "Beta", summary: "about dogs" }),
    ];

    it("filters by title", () => {
      expect(searchArticles(articles, "alpha").length).toBe(1);
    });

    it("filters by summary", () => {
      expect(searchArticles(articles, "dogs").map((a) => a.id)).toEqual(["2"]);
    });

    it("returns empty on no match", () => {
      expect(searchArticles(articles, "xyz").length).toBe(0);
    });

    it("returns all for empty query", () => {
      expect(searchArticles(articles, "").length).toBe(2);
    });
  });

  describe("filterByStatus", () => {
    const articles = [
      summary({ id: "1", status: "draft" }),
      summary({ id: "2", status: "idea" }),
    ];

    it("filters to one status", () => {
      expect(filterByStatus(articles, "draft").length).toBe(1);
    });

    it("returns all for all", () => {
      expect(filterByStatus(articles, "all").length).toBe(2);
    });
  });

  describe("generateMarkdown", () => {
    it("exports title, summary and body only (contract §7)", () => {
      const md = generateMarkdown(fakeArticle({
        title: "T",
        summary: "S",
        body: "B",
        materials: [
          {
            id: "m1", type: "note", title: "secret material", content: "must not leak",
            included: true, order: 0,
            createdAt: "2026-01-01T00:00:00+00:00", updatedAt: "2026-01-01T00:00:00+00:00",
          },
        ],
      }));
      expect(md).toBe("# T\n\n> S\n\nB");
      expect(md).not.toContain("secret material");
      expect(md).not.toContain("must not leak");
    });
  });

  describe("isSafeUrl", () => {
    it("rejects javascript: protocol", () => {
      expect(isSafeUrl("javascript:alert(1)")).toBe(false);
    });

    it("rejects data: protocol", () => {
      expect(isSafeUrl("data:text/html,<script>")).toBe(false);
    });

    it("accepts https", () => {
      expect(isSafeUrl("https://example.com")).toBe(true);
    });
  });

  describe("buildExportFilename", () => {
    it("cleans invalid characters", () => {
      const name = buildExportFilename("Hello: World/<>?");
      expect(name).not.toContain(":");
      expect(name).not.toContain("/");
      expect(name).not.toContain("<");
      expect(name).toMatch(/\.md$/);
    });
  });
});
