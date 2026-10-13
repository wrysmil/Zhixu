import { describe, expect, it } from "vitest";

import {
  buildMarkdownArticle,
  buildMarkdownContent,
  buildMarkdownFilename,
  exportArticleMarkdown,
  hasMeaningfulContent,
} from "@/lib/writing-export";
import type { ArticleSnapshotPayload } from "@/lib/writing-api";

function snapshot(overrides: Partial<ArticleSnapshotPayload> = {}): ArticleSnapshotPayload {
  return {
    title: "一篇示例文章",
    writing_type: "tech-explainer",
    template_id: "tech-explainer",
    status: "draft",
    summary: "示例摘要",
    brief: {},
    body: "这是正文段落。\n\n还有第二段。",
    outline: [],
    materials: [],
    tags: ["写作"],
    ...overrides,
  };
}

describe("writing-export (A3.7/A-09 pure helpers)", () => {
  it("builds the markdown content with title, summary and body only", () => {
    const md = buildMarkdownContent(snapshot());
    expect(md.startsWith("# 一篇示例文章\n\n")).toBe(true);
    expect(md).toContain("> 示例摘要");
    expect(md).toContain("这是正文段落。");
    expect(md).toContain("还有第二段。");
    // 不混入素材、提纲、对话、工具日志
    expect(md).not.toContain("素材");
    expect(md).not.toContain("提纲");
    expect(md).not.toContain("AI");
  });

  it("skips the summary block when summary is empty", () => {
    const md = buildMarkdownContent(snapshot({ summary: "" }));
    expect(md).toBe("# 一篇示例文章\n\n这是正文段落。\n\n还有第二段。");
  });

  it("cleans illegal filename characters and falls back for empty titles", () => {
    // ASCII illegal chars per Windows / POSIX filename rules; full-width
    // variants are kept (they're valid on modern filesystems and common in
    // Chinese titles).
    expect(buildMarkdownFilename(snapshot({ title: "好/题\\名|name" }))).toBe("好题名name.md");
    // All-ASCII-illegal title still resolves to a safe fallback filename.
    expect(buildMarkdownFilename(snapshot({ title: '<>:"|?*' }))).toBe("未命名文章.md");
    expect(buildMarkdownFilename(snapshot({ title: "" }))).toBe("未命名文章.md");
    expect(buildMarkdownFilename(snapshot({ title: "   " }))).toBe("未命名文章.md");
    expect(buildMarkdownFilename(snapshot({ title: "name<>with|bars" }))).toBe("namewithbars.md");
  });

  it("strips ASCII control characters that some filesystems reject", () => {
    expect(buildMarkdownFilename(snapshot({ title: "题\u0000\u0007名" }))).toBe("题名.md");
  });

  it("preserves Chinese characters and collapses internal whitespace", () => {
    expect(buildMarkdownFilename(snapshot({ title: "写作   实战" }))).toBe("写作-实战.md");
  });

  it("treats a draft without title or body as not meaningful", () => {
    expect(hasMeaningfulContent(snapshot({ title: "", body: "   \n\t  " }))).toBe(false);
    expect(hasMeaningfulContent(snapshot({ title: "只有标题", body: "" }))).toBe(true);
    expect(hasMeaningfulContent(snapshot({ title: "", body: "只有正文" }))).toBe(true);
    expect(hasMeaningfulContent(snapshot({ title: "只有标题", body: "  " }))).toBe(true);
  });

  it("builds an Article shape so generateMarkdown stays the single source of truth", () => {
    const article = buildMarkdownArticle(snapshot({ body: "草稿正文" }));
    expect(article.title).toBe("一篇示例文章");
    expect(article.body).toBe("草稿正文");
    expect(article.summary).toBe("示例摘要");
    expect(article.status).toBe("draft");
  });
});

describe("exportArticleMarkdown (download path)", () => {
  function snapshot(overrides: Partial<ArticleSnapshotPayload> = {}): ArticleSnapshotPayload {
    return {
      title: "下载示例",
      writing_type: "tech-explainer",
      status: "draft",
      summary: "",
      brief: {},
      body: "正文片段",
      outline: [],
      materials: [],
      tags: [],
      ...overrides,
    };
  }

  it("emits a UTF-8 markdown Blob and a synthesized click on the download anchor", () => {
    const captured: { url?: string; blobs: Blob[]; clicks: HTMLAnchorElement[] } = {
      blobs: [],
      clicks: [],
    };
    const outcome = exportArticleMarkdown(snapshot({ body: "草稿正文" }), {
      createObjectUrl: (blob) => {
        captured.blobs.push(blob);
        return "blob:fake";
      },
      triggerAnchor: (anchor) => {
        captured.clicks.push(anchor);
      },
      createAnchor: () => {
        const a = document.createElement("a");
        return a;
      },
    });

    expect(outcome.status).toBe("exported");
    if (outcome.status !== "exported") throw new Error("unreachable");
    expect(outcome.filename).toBe("下载示例.md");
    expect(captured.blobs).toHaveLength(1);
    expect(captured.blobs[0].type).toBe("text/markdown;charset=utf-8");
    expect(captured.clicks).toHaveLength(1);
    expect(captured.clicks[0].download).toBe("下载示例.md");
    expect(captured.clicks[0].href).toBe("blob:fake");
    expect(outcome.content.startsWith("# 文章")).toBe(false);
    expect(outcome.content.startsWith("# 下载示例")).toBe(true);
  });

  it("refuses to produce an empty file when the draft has no title and no body", () => {
    const captured = { blobs: 0, clicks: 0 };
    const outcome = exportArticleMarkdown(snapshot({ title: "", body: "   \n\t " }), {
      createObjectUrl: () => { captured.blobs += 1; return "blob:never"; },
      triggerAnchor: () => { captured.clicks += 1; },
      createAnchor: () => document.createElement("a"),
    });

    expect(outcome).toEqual({ status: "empty", reason: "no-title-or-body" });
    expect(captured.blobs).toBe(0);
    expect(captured.clicks).toBe(0);
  });

  it("returns a failed outcome instead of throwing when click is unavailable", () => {
    const outcome = exportArticleMarkdown(snapshot({ body: "内容" }), {
      createObjectUrl: () => "blob:fake",
      triggerAnchor: () => { throw new Error("not allowed"); },
      createAnchor: () => document.createElement("a"),
    });

    expect(outcome.status).toBe("failed");
    if (outcome.status !== "failed") throw new Error("unreachable");
    expect(outcome.reason).toContain("not allowed");
  });
});