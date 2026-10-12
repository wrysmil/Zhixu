import type {
  Article,
  ArticleStatus,
  ArticleSummaryView,
} from "./types";
import type {
  ArticleSnapshotPayload,
  CreateArticleRequest,
} from "@/lib/writing-api";
import { toSnapshotPayload } from "@/lib/writing-api";
import { buildTemplateOutline, findTemplate } from "./templates";

/**
 * Pure helpers for the writing domain (Phase A, plan A2.4).
 *
 * Server-side identity (article id, revision, timestamps) belongs to the
 * repository behind `writing-api.ts`; nothing here mints article ids or bumps
 * `revision` any more. `nextLocalEditSeq` is the *local* edit counter that
 * coexists with the server revision (dual counting, spec A-06/A-08).
 */
export function nextLocalEditSeq(prev: number): number {
  return prev + 1;
}

export function canAcceptSuggestion(
  article: { id: string; revision: number },
  suggestion: { articleId: string; baseRevision: number },
): boolean {
  return (
    article.id === suggestion.articleId &&
    article.revision === suggestion.baseRevision
  );
}

function newLocalId(): string {
  return (typeof crypto !== "undefined" && "randomUUID" in crypto)
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function createDraftInput(
  title: string,
  templateId?: string,
  translate?: (key: string) => string,
): CreateArticleRequest {
  if (!title.trim()) {
    throw new Error("article title is required");
  }
  const template = findTemplate(templateId);
  const tr = translate ?? ((key: string) => key);
  const outline = template ? buildTemplateOutline(template, tr, newLocalId) : [];
  const input: CreateArticleRequest = {
    clientRequestId: newLocalId(),
    title: title.trim(),
    outline,
  };
  if (template) input.templateId = template.id;
  return input;
}

export function duplicateTitle(title: string): string {
  return `${title}（副本）`;
}

const WHITESPACE_RE = /\s+/gu;

/**
 * Body length in characters after stripping every whitespace run
 * (spec A-03). Deliberately NOT a "word count".
 */
export function countBodyChars(body: string): number {
  return body.replace(WHITESPACE_RE, "").length;
}

export function buildSnapshot(article: Article): ArticleSnapshotPayload {
  return toSnapshotPayload(article);
}

function canonical(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([k, v]) => `${k}:${canonical(v)}`);
    return `{${entries.join(",")}}`;
  }
  return JSON.stringify(value);
}

export function isDirty(
  a: ArticleSnapshotPayload,
  b: ArticleSnapshotPayload,
): boolean {
  return canonical(a) !== canonical(b);
}

export function searchArticles<T extends ArticleSummaryView>(
  articles: T[],
  query: string,
): T[] {
  if (!query.trim()) return articles;
  const lower = query.toLowerCase();
  return articles.filter(
    (a) =>
      a.title.toLowerCase().includes(lower) ||
      a.summary.toLowerCase().includes(lower),
  );
}

export function filterByStatus<T extends ArticleSummaryView>(
  articles: T[],
  status: ArticleStatus | "all",
): T[] {
  if (status === "all") return articles;
  return articles.filter((a) => a.status === status);
}

export function buildExportFilename(title: string): string {
  const cleaned = title
    .split("")
    .filter((ch) => ch.charCodeAt(0) >= 0x20 && !'<>:"/\\|?*'.includes(ch))
    .join("")
    .trim()
    .replace(/\s+/g, "-");
  return `${cleaned || "未命名文章"}.md`;
}

/** Contract §7: export contains title, summary and body only. */
export function generateMarkdown(article: Article): string {
  let out = `# ${article.title}\n\n`;
  if (article.summary) out += `> ${article.summary}\n\n`;
  out += article.body;
  return out;
}

const DANGEROUS_PROTOCOLS = ["javascript:", "data:", "vbscript:"];

export function isSafeUrl(url: string): boolean {
  const lower = url.trim().toLowerCase();
  return !DANGEROUS_PROTOCOLS.some((p) => lower.startsWith(p));
}
