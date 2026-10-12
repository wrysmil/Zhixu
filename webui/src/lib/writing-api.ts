import {
  ARTICLE_STATUSES,
  MATERIAL_TYPES,
  type Article,
  type ArticleBrief,
  type ArticleStatus,
  type ArticleSummaryView,
  type Material,
  type MaterialType,
  type OutlineItem,
} from "@/components/creative/types";
import { mutation, request, type WebUIMutationTransport } from "./api";

const WRITING_BASE = "/api/writing";
const WRITING_READ_TIMEOUT_MS = 20_000;

export interface ArticleListParams {
  q?: string;
  status?: ArticleStatus | "all";
  limit?: number;
  offset?: number;
}

export interface ArticleListPage {
  articles: ArticleSummaryView[];
  total: number;
  limit: number;
  offset: number;
}

/** Wire snapshot (contract §5): snake_case protocol field names, camelCase nested items. */
export interface ArticleSnapshotPayload {
  title: string;
  writing_type: string;
  template_id?: string;
  status: ArticleStatus;
  summary: string;
  brief: ArticleBrief;
  body: string;
  outline: OutlineItem[];
  materials: Material[];
  tags: string[];
}

export interface SaveArticleResult {
  article_id: string;
  revision: number;
  updated_at: string;
  digest: string;
  no_change: boolean;
}

export interface ArticleConflict {
  currentRevision: number;
  articleId: string;
}

export interface CreateArticleRequest {
  clientRequestId: string;
  title: string;
  templateId?: string;
  writingType?: string;
  summary?: string;
  brief?: ArticleBrief;
  outline?: OutlineItem[];
  materials?: Material[];
  tags?: string[];
}

export interface SaveArticleRequest {
  clientRequestId: string;
  articleId: string;
  baseRevision: number;
  snapshot: ArticleSnapshotPayload;
}

export interface ArticleRefRequest {
  articleId: string;
  baseRevision: number;
  clientRequestId: string;
}

export interface DeleteArticleResult {
  deletedArticleId: string;
  revision: number;
}

const CONFLICT_RE = /^article_conflict current_revision=(\d+) article_id=(\S+)$/;

export function parseArticleConflict(message: string): ArticleConflict | null {
  const match = CONFLICT_RE.exec(message ?? "");
  if (!match) return null;
  return { currentRevision: Number(match[1]), articleId: match[2] };
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function count(value: unknown, fallback = 0): number {
  const num = typeof value === "number" ? value : Number(text(value));
  return Number.isFinite(num) ? num : fallback;
}

function toStatus(value: unknown): ArticleStatus {
  const raw = text(value);
  return ARTICLE_STATUSES.includes(raw as ArticleStatus)
    ? (raw as ArticleStatus)
    : "idea";
}

function toMaterialType(value: unknown): MaterialType {
  const raw = text(value);
  return MATERIAL_TYPES.includes(raw as MaterialType)
    ? (raw as MaterialType)
    : "note";
}

function toBrief(raw: unknown): ArticleBrief {
  if (!raw || typeof raw !== "object") return {};
  const source = raw as Record<string, unknown>;
  const brief: ArticleBrief = {};
  if (typeof source.goal === "string") brief.goal = source.goal;
  if (typeof source.audience === "string") brief.audience = source.audience;
  if (typeof source.style === "string") brief.style = source.style;
  return brief;
}

function toOutlineItem(raw: unknown): OutlineItem {
  const source = (raw ?? {}) as Record<string, unknown>;
  return {
    id: text(source.id),
    text: text(source.text),
    order: count(source.order),
  };
}

function toMaterial(raw: unknown): Material {
  const source = (raw ?? {}) as Record<string, unknown>;
  const material: Material = {
    id: text(source.id),
    type: toMaterialType(source.type),
    title: text(source.title),
    content: text(source.content),
    included: source.included !== false,
    order: count(source.order),
    createdAt: text(source.created_at),
    updatedAt: text(source.updated_at),
  };
  if (typeof source.url === "string") material.url = source.url;
  if (typeof source.source === "string") material.source = source.source;
  if (typeof source.note === "string") material.note = source.note;
  if (typeof source.language === "string") material.language = source.language;
  return material;
}

export function toArticle(raw: Record<string, unknown>): Article {
  const article: Article = {
    id: text(raw.id),
    title: text(raw.title),
    writingType: text(raw.writing_type),
    status: toStatus(raw.status),
    summary: text(raw.summary),
    brief: toBrief(raw.brief),
    body: text(raw.body),
    outline: Array.isArray(raw.outline) ? raw.outline.map(toOutlineItem) : [],
    materials: Array.isArray(raw.materials) ? raw.materials.map(toMaterial) : [],
    tags: Array.isArray(raw.tags) ? raw.tags.map((tag) => text(tag)) : [],
    revision: count(raw.revision, 1),
    createdAt: text(raw.created_at),
    updatedAt: text(raw.updated_at),
  };
  const templateId = text(raw.template_id);
  if (templateId) article.templateId = templateId;
  return article;
}

function toSummaryView(raw: Record<string, unknown>): ArticleSummaryView {
  return {
    id: text(raw.id),
    title: text(raw.title),
    summary: text(raw.summary),
    status: toStatus(raw.status),
    writingType: text(raw.writing_type),
    tags: Array.isArray(raw.tags) ? raw.tags.map((tag) => text(tag)) : [],
    revision: count(raw.revision, 1),
    updatedAt: text(raw.updated_at),
  };
}

function outlineItemToWire(item: OutlineItem): Record<string, unknown> {
  return { id: item.id, text: item.text, order: item.order };
}

function materialToWire(material: Material): Record<string, unknown> {
  const wire: Record<string, unknown> = {
    id: material.id,
    type: material.type,
    title: material.title,
    content: material.content,
    included: material.included,
    order: material.order,
    created_at: material.createdAt,
    updated_at: material.updatedAt,
  };
  if (material.url !== undefined) wire.url = material.url;
  if (material.source !== undefined) wire.source = material.source;
  if (material.note !== undefined) wire.note = material.note;
  if (material.language !== undefined) wire.language = material.language;
  return wire;
}

function snapshotToWire(snapshot: ArticleSnapshotPayload): Record<string, unknown> {
  const wire: Record<string, unknown> = {
    title: snapshot.title,
    writing_type: snapshot.writing_type,
    status: snapshot.status,
    summary: snapshot.summary,
    brief: snapshot.brief,
    body: snapshot.body,
    outline: snapshot.outline.map(outlineItemToWire),
    materials: snapshot.materials.map(materialToWire),
    tags: snapshot.tags,
  };
  if (snapshot.template_id !== undefined) wire.template_id = snapshot.template_id;
  return wire;
}

export function toSnapshotPayload(article: Article): ArticleSnapshotPayload {
  const snapshot: ArticleSnapshotPayload = {
    title: article.title,
    writing_type: article.writingType,
    status: article.status,
    summary: article.summary,
    brief: article.brief,
    body: article.body,
    outline: article.outline.map((item) => ({ ...item })),
    materials: article.materials.map((material) => ({ ...material })),
    tags: [...article.tags],
  };
  if (article.templateId !== undefined) snapshot.template_id = article.templateId;
  return snapshot;
}

export async function fetchArticleList(
  token: string,
  params: ArticleListParams,
  base = "",
): Promise<ArticleListPage> {
  const query = new URLSearchParams();
  if (params.q) query.set("q", params.q);
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  type Wire = { articles: Record<string, unknown>[]; total: number; limit: number; offset: number };
  const body = await request<Wire>(
    `${base}${WRITING_BASE}/articles${suffix}`,
    token,
    undefined,
    WRITING_READ_TIMEOUT_MS,
  );
  return {
    articles: body.articles.map(toSummaryView),
    total: count(body.total),
    limit: count(body.limit, 50),
    offset: count(body.offset),
  };
}

export async function fetchArticle(token: string, id: string, base = ""): Promise<Article> {
  const query = new URLSearchParams({ id });
  const body = await request<{ article: Record<string, unknown> }>(
    `${base}${WRITING_BASE}/articles/get?${query}`,
    token,
    undefined,
    WRITING_READ_TIMEOUT_MS,
  );
  return toArticle(body.article);
}

export async function createArticle(
  transport: WebUIMutationTransport,
  req: CreateArticleRequest,
): Promise<Article> {
  const payload: Record<string, unknown> = {
    client_request_id: req.clientRequestId,
    title: req.title,
  };
  if (req.templateId !== undefined) payload.template_id = req.templateId;
  if (req.writingType !== undefined) payload.writing_type = req.writingType;
  if (req.summary !== undefined) payload.summary = req.summary;
  if (req.brief !== undefined) payload.brief = req.brief;
  if (req.outline !== undefined) payload.outline = req.outline.map(outlineItemToWire);
  if (req.materials !== undefined) payload.materials = req.materials.map(materialToWire);
  if (req.tags !== undefined) payload.tags = req.tags;
  const body = await mutation<{ article: Record<string, unknown> }>(
    transport,
    "article.create",
    payload,
  );
  return toArticle(body.article);
}

export async function saveArticle(
  transport: WebUIMutationTransport,
  req: SaveArticleRequest,
): Promise<SaveArticleResult> {
  return mutation<SaveArticleResult>(transport, "article.save", {
    client_request_id: req.clientRequestId,
    article_id: req.articleId,
    base_revision: req.baseRevision,
    snapshot: snapshotToWire(req.snapshot),
  });
}

export async function duplicateArticle(
  transport: WebUIMutationTransport,
  req: ArticleRefRequest,
): Promise<Article> {
  const body = await mutation<{ article: Record<string, unknown> }>(
    transport,
    "article.duplicate",
    {
      client_request_id: req.clientRequestId,
      article_id: req.articleId,
      base_revision: req.baseRevision,
    },
  );
  return toArticle(body.article);
}

export async function deleteArticle(
  transport: WebUIMutationTransport,
  req: ArticleRefRequest,
): Promise<DeleteArticleResult> {
  const body = await mutation<{ deleted_article_id: string; revision: number }>(
    transport,
    "article.delete",
    {
      client_request_id: req.clientRequestId,
      article_id: req.articleId,
      base_revision: req.baseRevision,
    },
  );
  return { deletedArticleId: body.deleted_article_id, revision: body.revision };
}
