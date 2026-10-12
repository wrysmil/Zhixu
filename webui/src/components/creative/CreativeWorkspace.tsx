import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, type WebUIMutationTransport } from "@/lib/api";
import {
  createArticle,
  deleteArticle,
  duplicateArticle,
  fetchArticle,
  fetchArticleList,
  saveArticle,
  toArticle,
  toSnapshotPayload,
  type CreateArticleRequest,
} from "@/lib/writing-api";
import type { Article, ArticleStatus, ArticleSummaryView } from "./types";
import {
  createDraftInput,
  duplicateTitle,
  filterByStatus,
  searchArticles,
} from "./article-model";
import { demoArticles } from "./demo-data";
import { ArticleList, type ArticleListState } from "./ArticleList";
import { ArticleEditor } from "./ArticleEditor";
import { useArticleSaveFlow } from "./use-article-save-flow";

export interface CreativeWorkspaceProps {
  getToken: () => string;
  transport: WebUIMutationTransport;
  /** 仅用于本地恢复稿命名空间隔离；服务端不接收该值（契约 §2）。 */
  workspacePath: string;
  /** 只有预览入口为 true：允许装载演示数据，且不发起任何请求（A-01/A-T11）。 */
  preview?: boolean;
}

type View = "list" | "editor";

const LIST_LIMIT = 50;
/** 搜索由服务端负责（契约 §4），逐键一请求会打满接口。 */
const SEARCH_DEBOUNCE_MS = 250;

function newLocalId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function freePreviewId(articles: Article[]): string {
  let index = articles.length + 1;
  while (articles.some((item) => item.id === `demo-${index}`)) index += 1;
  return `demo-${index}`;
}

function toSummaryRow(article: Article): ArticleSummaryView {
  return {
    id: article.id,
    title: article.title,
    summary: article.summary,
    status: article.status,
    writingType: article.writingType,
    tags: [...article.tags],
    revision: article.revision,
    updatedAt: article.updatedAt,
  };
}

/** 只比对会持久化的内容字段，用来回答 `no_change`。 */
function contentFields(article: Article) {
  return {
    title: article.title,
    writingType: article.writingType,
    templateId: article.templateId ?? "",
    status: article.status,
    summary: article.summary,
    brief: article.brief,
    body: article.body,
    outline: article.outline,
    materials: article.materials,
    tags: article.tags,
  };
}

/** 预览响应用契约 §5 的 snake_case 线格式，方便 `toArticle()` 原样解析。 */
function articleToWire(article: Article): Record<string, unknown> {
  return {
    ...toSnapshotPayload(article),
    id: article.id,
    revision: article.revision,
    created_at: article.createdAt,
    updated_at: article.updatedAt,
  };
}

/**
 * Preview write path (A-T11): the preview entry has no gateway connection, but the
 * editor still has to behave, so `article.{create,save,duplicate,delete}` are
 * simulated in memory against the same snake_case wire shape the real transport
 * speaks (contract §5). Pure function, no network access.
 */
export function applyPreviewMutation(
  articles: Article[],
  action: string,
  payload: Record<string, unknown>,
): { articles: Article[]; response: Record<string, unknown> } {
  const now = new Date().toISOString();
  const text = (key: string): string => {
    const value = payload[key];
    return typeof value === "string" ? value : "";
  };
  const next = [...articles];

  if (action === "article.create") {
    const created = toArticle({
      ...payload,
      id: freePreviewId(articles),
      revision: 1,
      created_at: now,
      updated_at: now,
    });
    return { articles: [created, ...next], response: { article: articleToWire(created) } };
  }

  if (action === "article.save") {
    const index = next.findIndex((item) => item.id === text("article_id"));
    if (index < 0) throw new ApiError(409, "article deleted");
    const current = next[index];
    if (Number(payload.base_revision ?? 0) !== current.revision) {
      throw new ApiError(
        409,
        `article_conflict current_revision=${current.revision} article_id=${current.id}`,
      );
    }
    const incoming = toArticle({
      ...(payload.snapshot as Record<string, unknown>),
      id: current.id,
      revision: current.revision,
      created_at: current.createdAt,
      updated_at: current.updatedAt,
    });
    if (JSON.stringify(contentFields(current)) === JSON.stringify(contentFields(incoming))) {
      return {
        articles: next,
        response: {
          article_id: current.id,
          revision: current.revision,
          updated_at: current.updatedAt,
          digest: `unchanged-${current.revision}`,
          no_change: true,
        },
      };
    }
    const saved: Article = {
      ...incoming,
      revision: current.revision + 1,
      createdAt: current.createdAt,
      updatedAt: now,
    };
    next[index] = saved;
    return {
      articles: next,
      response: {
        article_id: saved.id,
        revision: saved.revision,
        updated_at: saved.updatedAt,
        digest: newLocalId(),
        no_change: false,
      },
    };
  }

  if (action === "article.duplicate") {
    const source = next.find((item) => item.id === text("article_id"));
    if (!source) throw new ApiError(404, "article not found");
    const copy: Article = {
      ...source,
      id: freePreviewId(articles),
      title: duplicateTitle(source.title),
      revision: 1,
      createdAt: now,
      updatedAt: now,
      outline: source.outline.map((item, index) => ({ ...item, id: newLocalId(), order: index })),
      materials: source.materials.map((item) => ({ ...item, id: newLocalId() })),
    };
    return { articles: [copy, ...next], response: { article: articleToWire(copy) } };
  }

  if (action === "article.delete") {
    const target = next.find((item) => item.id === text("article_id"));
    if (!target) throw new ApiError(404, "article not found");
    return {
      articles: next.filter((item) => item.id !== target.id),
      response: { deleted_article_id: target.id, revision: target.revision },
    };
  }

  throw new ApiError(400, `preview workspace does not support ${action}`);
}

/**
 * `useArticleSaveFlow` needs a non-null article, so the hook lives in this child
 * instead of the container (hooks cannot be conditional).
 */
function EditorHost({
  getToken,
  transport,
  workspacePath,
  article,
  readLatest,
  onArticleReplaced,
  onBack,
}: {
  getToken: () => string;
  transport: WebUIMutationTransport;
  workspacePath: string;
  article: Article;
  /** 读取服务器最新版本；预览入口走内存副本，不发网络请求。 */
  readLatest: (articleId: string) => Promise<Article>;
  onArticleReplaced: (article: Article) => void;
  onBack: () => void;
}) {
  const flow = useArticleSaveFlow({ getToken, transport, article, workspacePath });

  /** 冲突对话框要展示服务器那一版，读取入口由容器提供（契约 §4）。 */
  const handleFetchLatest = useCallback(
    () => readLatest(article.id),
    [readLatest, article.id],
  );

  /**
   * 「另存副本」保住的是**本地稿**，不是服务端那一行：所以走 `article.create`
   * 新建（`duplicateArticle` 复制的是服务端当前内容，语义不同），紧接着把本地快照
   * 落一次盘，避免新文章停在「有标题没正文」的半保存状态（A-06 原子性）。
   */
  const handleSaveCopy = useCallback(async () => {
    const snapshot = flow.draft;
    const request: CreateArticleRequest = {
      clientRequestId: newLocalId(),
      title: duplicateTitle(snapshot.title),
      writingType: snapshot.writing_type,
      summary: snapshot.summary,
      brief: snapshot.brief,
      outline: snapshot.outline,
      materials: snapshot.materials,
      tags: snapshot.tags,
    };
    if (snapshot.template_id) request.templateId = snapshot.template_id;
    const created = await createArticle(transport, request);
    const saved = await saveArticle(transport, {
      clientRequestId: newLocalId(),
      articleId: created.id,
      baseRevision: created.revision,
      snapshot,
    });
    onArticleReplaced({
      ...created,
      writingType: snapshot.writing_type,
      status: snapshot.status,
      summary: snapshot.summary,
      brief: snapshot.brief,
      body: snapshot.body,
      outline: snapshot.outline,
      materials: snapshot.materials,
      tags: snapshot.tags,
      revision: saved.revision,
      updatedAt: saved.updated_at,
    });
  }, [flow.draft, onArticleReplaced, transport]);

  return (
    <ArticleEditor
      article={article}
      draft={flow.draft}
      dirty={flow.dirty}
      saveState={flow.state}
      setField={flow.setField}
      onSaveNow={flow.saveNow}
      onAcknowledgeConflict={flow.acknowledgeConflict}
      onFetchLatest={handleFetchLatest}
      onSaveCopy={handleSaveCopy}
      onBack={onBack}
    />
  );
}

export default function CreativeWorkspace({
  getToken,
  transport,
  workspacePath,
  preview = false,
}: CreativeWorkspaceProps) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<ArticleSummaryView[]>([]);
  const [total, setTotal] = useState(0);
  const [listState, setListState] = useState<ArticleListState>(
    preview ? "ready" : "loading",
  );
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<ArticleStatus | "all">("all");
  const [view, setView] = useState<View>("list");
  const [activeArticle, setActiveArticle] = useState<Article | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [previewArticles, setPreviewArticles] = useState<Article[]>(() =>
    preview ? demoArticles() : [],
  );

  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;
  const previewRef = useRef(previewArticles);
  previewRef.current = previewArticles;
  /** 最新请求胜出：`fetchArticleList` 不接收 AbortSignal，迟到的旧响应必须丢弃。 */
  const listSeq = useRef(0);

  const writePreview = useCallback((next: Article[]) => {
    previewRef.current = next;
    setPreviewArticles(next);
  }, []);

  const previewTransport = useMemo<WebUIMutationTransport>(
    () => ({
      requestMutation: async <T,>(
        action: string,
        payload?: Record<string, unknown>,
      ): Promise<T> => {
        const outcome = applyPreviewMutation(previewRef.current, action, payload ?? {});
        writePreview(outcome.articles);
        return outcome.response as T;
      },
    }),
    [writePreview],
  );

  // 预览模式连写入都是本地的：真实 transport 在预览里一次都不该被用到。
  const mutationTransport = preview ? previewTransport : transport;

  useEffect(() => {
    if (preview) return undefined;
    const seq = listSeq.current + 1;
    listSeq.current = seq;
    setListState("loading");
    let settled = false;
    fetchArticleList(getTokenRef.current(), {
      q: debouncedQuery,
      status: statusFilter,
      limit: LIST_LIMIT,
      offset: 0,
    })
      .then((page) => {
        settled = true;
        if (listSeq.current !== seq) return;
        setRows(page.articles);
        setTotal(page.total);
        setListState(page.articles.length === 0 ? "empty" : "ready");
      })
      .catch(() => {
        settled = true;
        if (listSeq.current !== seq) return;
        setListState("error");
      });
    return () => {
      // 依赖变化时让在飞请求作废，避免旧结果覆盖新筛选。
      if (!settled) listSeq.current += 1000;
    };
  }, [preview, debouncedQuery, statusFilter, reloadKey]);

  useEffect(() => {
    if (preview) return undefined;
    const timer = setTimeout(() => setDebouncedQuery(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [preview, query]);

  const localRows = useMemo(
    () => searchArticles(filterByStatus(previewArticles, statusFilter), query).map(toSummaryRow),
    [previewArticles, statusFilter, query],
  );
  const visibleRows = preview ? localRows : rows;
  const visibleTotal = preview ? visibleRows.length : total;
  const visibleListState: ArticleListState = preview
    ? visibleRows.length === 0 ? "empty" : "ready"
    : listState;

  const refreshList = useCallback(() => setReloadKey((key) => key + 1), []);

  /**
   * 冲突对话框读取「服务器那一版」的入口：预览入口一律走内存副本（A-T11），
   * 否则才是真实的 `article.get`（契约 §4）。
   */
  const readLatestArticle = useCallback(
    async (id: string): Promise<Article> => {
      if (preview) {
        const found = previewRef.current.find((item) => item.id === id) ?? null;
        if (!found) throw new ApiError(404, "article not found");
        return found;
      }
      return fetchArticle(getTokenRef.current(), id);
    },
    [preview],
  );

  const failureText = useCallback(
    (reason: unknown, fallbackKey: string, fallback: string) => {
      const message = reason instanceof Error ? reason.message.trim() : "";
      return message || t(fallbackKey, { defaultValue: fallback });
    },
    [t],
  );

  const openEditor = useCallback(
    async (id: string) => {
      setOpenError(null);
      if (preview) {
        const found = previewRef.current.find((item) => item.id === id) ?? null;
        setActiveArticle(found);
        setOpenError(found ? null : t("creative.articleMissing", { defaultValue: "文章已不存在" }));
        setView("editor");
        return;
      }
      try {
        const article = await fetchArticle(getTokenRef.current(), id);
        setActiveArticle(article);
        setView("editor");
      } catch (reason) {
        const status = reason instanceof ApiError ? reason.status : 0;
        const message = reason instanceof Error ? reason.message : "";
        let text = t("creative.articleOpenFailed", { defaultValue: "文章打开失败" });
        if (status === 404) {
          text = t("creative.articleMissing", { defaultValue: "文章已不存在" });
        } else if (status === 409 && message.includes("article deleted")) {
          text = t("creative.articleDeleted", { defaultValue: "文章已被删除" });
        } else if (status === 409) {
          text = t("creative.articleConflict", { defaultValue: "文章存在版本冲突，请重新打开" });
        }
        // spec A-07：读取失败不回退演示数据，只显示失败原因。
        setActiveArticle(null);
        setOpenError(text);
        setView("editor");
      }
    },
    [preview, t],
  );

  const backToList = useCallback(() => {
    setView("list");
    setActiveArticle(null);
    setOpenError(null);
    if (!preview) refreshList();
  }, [preview, refreshList]);

  const handleCreate = useCallback(
    async (title: string, templateId?: string) => {
      if (!title.trim()) {
        return { error: t("creative.title_required", { defaultValue: "请输入文章主题" }) };
      }
      try {
        const article = await createArticle(
          mutationTransport,
          createDraftInput(title, templateId, t),
        );
        if (preview) writePreview([article, ...previewRef.current]);
        else refreshList();
        setActiveArticle(article);
        setOpenError(null);
        setView("editor");
        return { error: null };
      } catch (reason) {
        // A-01：创建失败要保留已填标题与模板选择，所以错误只回给表单，不清空它。
        return { error: failureText(reason, "creative.createFailed", "新建文章失败") };
      }
    },
    [failureText, mutationTransport, preview, refreshList, t, writePreview],
  );

  const handleDuplicate = useCallback(
    async (row: ArticleSummaryView) => {
      try {
        const copy = await duplicateArticle(mutationTransport, {
          articleId: row.id,
          baseRevision: row.revision,
          clientRequestId: newLocalId(),
        });
        if (preview) {
          writePreview([copy, ...previewRef.current]);
          return;
        }
        // 服务端把副本整条返回了，直接插到列表头部，省一次往返。
        setRows((prev) => [toSummaryRow(copy), ...prev]);
        setTotal((prev) => prev + 1);
        setListState("ready");
      } catch (reason) {
        setActionError(failureText(reason, "creative.duplicateFailed", "复制文章失败"));
      }
    },
    [failureText, mutationTransport, preview, writePreview],
  );

  const handleDelete = useCallback(
    async (row: ArticleSummaryView) => {
      try {
        await deleteArticle(mutationTransport, {
          articleId: row.id,
          baseRevision: row.revision,
          clientRequestId: newLocalId(),
        });
        if (activeArticle?.id === row.id) backToList();
        if (preview) {
          writePreview(previewRef.current.filter((item) => item.id !== row.id));
          return;
        }
        refreshList();
      } catch (reason) {
        setActionError(failureText(reason, "creative.deleteFailed", "删除文章失败"));
      }
    },
    [
      activeArticle,
      backToList,
      failureText,
      mutationTransport,
      preview,
      refreshList,
      writePreview,
    ],
  );

  if (view === "editor" && activeArticle) {
    return (
      <EditorHost
        key={activeArticle.id}
        getToken={getToken}
        transport={mutationTransport}
        workspacePath={workspacePath}
        article={activeArticle}
        readLatest={readLatestArticle}
        onArticleReplaced={setActiveArticle}
        onBack={backToList}
      />
    );
  }

  if (view === "editor" && openError) {
    return (
      <section
        data-testid="article-editor-error"
        className="mx-auto w-full max-w-[560px] px-6 py-16 text-center"
      >
        <p role="alert" className="text-[13px] font-medium text-foreground">
          {openError}
        </p>
        <button
          type="button"
          onClick={backToList}
          className="mt-4 cursor-pointer rounded-control border border-border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          {t("creative.back_to_list", { defaultValue: "返回文章列表" })}
        </button>
      </section>
    );
  }

  return (
    <ArticleList
      articles={visibleRows}
      totalCount={visibleTotal}
      listState={visibleListState}
      preview={preview}
      actionError={actionError}
      query={query}
      statusFilter={statusFilter}
      onQueryChange={setQuery}
      onStatusFilterChange={setStatusFilter}
      onOpen={openEditor}
      onCreate={handleCreate}
      onDuplicate={handleDuplicate}
      onDelete={handleDelete}
      onRetry={refreshList}
      onDismissActionError={() => setActionError(null)}
    />
  );
}
