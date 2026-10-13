import {
  buildExportFilename,
  countBodyChars,
  generateMarkdown,
} from "@/components/creative/article-model";
import type { Article } from "@/components/creative/types";
import type { ArticleSnapshotPayload } from "./writing-api";

/**
 * Article Markdown export (plan Task A3.7, spec A-09).
 *
 * The export reflects the *current draft* on screen, not the latest server
 * snapshot (A-09: "A-P0 支持导出当前编辑稿的 Markdown，即使服务端保存失败也
 * 可导出；明确提示其中可能包含未保存修改"). All text helpers live here so
 * the editor doesn't grow ad-hoc blob wiring.
 *
 * `generateMarkdown`/`buildExportFilename` are the single source of truth for
 * content & filename shape and live in `article-model.ts` — this module only
 * bridges from the in-memory `ArticleSnapshotPayload` to the `Article` shape
 * those helpers expect, plus the browser-side download plumbing.
 */
export interface ExportOptions {
  /** Reserved for stage B where materials/dialogue may opt-in; A-P0 must be false. */
  includeMaterials?: false;
  /** Test seam: replace the Blob → object URL pipeline. */
  createObjectUrl?: (blob: Blob) => string;
  /** Test seam: replace `<a>` click + revoke. */
  triggerAnchor?: (anchor: HTMLAnchorElement) => void;
  /** Test seam: replace DOM anchor creation. */
  createAnchor?: () => HTMLAnchorElement;
}

export type ExportOutcome =
  | { status: "exported"; filename: string; content: string }
  | { status: "empty"; reason: "no-title-or-body" }
  | { status: "failed"; reason: string };

/**
 * Build an `Article` view of the current draft. Server-only fields (`id`,
 * `revision`, timestamps) are not used by `generateMarkdown`, but we forward
 * the caller's article when supplied for future export hooks.
 */
export function buildMarkdownArticle(
  snapshot: ArticleSnapshotPayload,
  server?: Pick<Article, "id" | "revision" | "createdAt" | "updatedAt">,
): Article {
  const base: Article = {
    id: server?.id ?? "",
    title: snapshot.title,
    writingType: snapshot.writing_type,
    status: snapshot.status,
    summary: snapshot.summary,
    brief: snapshot.brief,
    body: snapshot.body,
    outline: snapshot.outline.map((item) => ({ ...item })),
    materials: snapshot.materials.map((material) => ({ ...material })),
    tags: [...snapshot.tags],
    revision: server?.revision ?? 0,
    createdAt: server?.createdAt ?? "",
    updatedAt: server?.updatedAt ?? "",
  };
  if (snapshot.template_id) base.templateId = snapshot.template_id;
  return base;
}

/** Pure renderer — used by tests and by the live download. */
export function buildMarkdownContent(snapshot: ArticleSnapshotPayload): string {
  return generateMarkdown(buildMarkdownArticle(snapshot));
}

/** Pure filename — mirrors `article-model.buildExportFilename`. */
export function buildMarkdownFilename(snapshot: ArticleSnapshotPayload): string {
  return buildExportFilename(buildMarkdownArticle(snapshot).title);
}

/**
 * A draft with no title and only whitespace body would produce a Markdown file
 * with just a heading line. That's misleading: refuse silently and let the UI
 * show its own hint instead (A-09 + plan step 1: "无正文时不允许静默产出空文
 * 件误导用户").
 */
export function hasMeaningfulContent(snapshot: ArticleSnapshotPayload): boolean {
  if (snapshot.title.trim().length > 0) return true;
  return countBodyChars(snapshot.body) > 0;
}

/**
 * Browser-side download. Returns an outcome object instead of throwing so the
 * editor can decide how to surface failures (e.g. read-only / iOS WebKit
 * blocks programmatic downloads).
 */
export function exportArticleMarkdown(
  snapshot: ArticleSnapshotPayload,
  options: ExportOptions = {},
): ExportOutcome {
  if (!hasMeaningfulContent(snapshot)) {
    return { status: "empty", reason: "no-title-or-body" };
  }
  const content = buildMarkdownContent(snapshot);
  const filename = buildMarkdownFilename(snapshot);
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });

  const createObjectUrl = options.createObjectUrl ?? ((b: Blob) => URL.createObjectURL(b));
  const createAnchor = options.createAnchor ?? (() => document.createElement("a"));
  const url = createObjectUrl(blob);
  let triggered = false;
  try {
    const anchor = createAnchor();
    anchor.href = url;
    anchor.download = filename;
    const trigger = options.triggerAnchor ?? ((node: HTMLAnchorElement) => node.click());
    trigger(anchor);
    triggered = true;
    return { status: "exported", filename, content };
  } catch (reason) {
    return { status: "failed", reason: reason instanceof Error ? reason.message : String(reason) };
  } finally {
    if (!options.createObjectUrl && triggered) URL.revokeObjectURL(url);
  }
}