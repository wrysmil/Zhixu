import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { FileText, Plus, Search, Copy, Trash2, ArrowRight, Sparkles, Lightbulb, Code2, Feather, BookOpen, Check, Loader2 } from "lucide-react";

import writingIllustration from "@/assets/creative-writing-desk.png";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { cn } from "@/lib/utils";
import { fmtDateTime, relativeTime } from "@/lib/format";
import type { ArticleStatus, ArticleSummaryView } from "./types";
import { ARTICLE_STATUSES, ARTICLE_STATUS_LABEL_KEY } from "./types";
import { WRITING_TEMPLATES, findTemplate } from "./templates";

export type ArticleListState = "loading" | "empty" | "error" | "ready";

interface Props {
  articles: ArticleSummaryView[];
  totalCount: number;
  listState: ArticleListState;
  /** 预览入口专用：演示数据标记与说明文案只在 preview 下出现（A-T11）。 */
  preview: boolean;
  actionError: string | null;
  query: string;
  statusFilter: ArticleStatus | "all";
  /** 文章 ID 集合：本地还有未保存草稿，删除时追加明确提示（A-02）。 */
  draftIds?: Set<string>;
  onQueryChange: (q: string) => void;
  onStatusFilterChange: (s: ArticleStatus | "all") => void;
  onOpen: (id: string) => void;
  onCreate: (title: string, templateId?: string) => Promise<{ error: string | null }>;
  onDuplicate: (article: ArticleSummaryView) => void;
  onDelete: (article: ArticleSummaryView) => void;
  onRetry: () => void;
  onDismissActionError: () => void;
}

const TEMPLATE_ICONS = [Lightbulb, Code2, Feather, BookOpen];
const FOCUS_STYLE = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background";

function filteredOutOfServer(query: string, statusFilter: ArticleStatus | "all"): boolean {
  return query.trim().length > 0 || statusFilter !== "all";
}

export function ArticleList({
  articles, totalCount, listState, preview, actionError, query, statusFilter,
  draftIds, onQueryChange, onStatusFilterChange, onOpen, onCreate, onDuplicate, onDelete,
  onRetry, onDismissActionError,
}: Props) {
  const { t, i18n } = useTranslation();
  const [newTitle, setNewTitle] = useState("");
  const [selectedTemplate, setSelectedTemplate] = useState<string | undefined>();
  const [createError, setCreateError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ArticleSummaryView | null>(null);
  const titleRef = useRef<HTMLInputElement>(null);
  const submittingRef = useRef(false);
  const composingRef = useRef(false);
  const hasFilters = filteredOutOfServer(query, statusFilter);

  const handleCreate = async () => {
    // 同一渲染批次也可能收到两次 submit，状态更新前先同步锁住。
    if (submittingRef.current || composingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setCreateError(null);
    try {
      const { error } = await onCreate(newTitle, selectedTemplate);
      if (error) {
        // A-01：失败要留着已填标题与模板选择，用户不必重写。
        setCreateError(error);
        titleRef.current?.focus();
        return;
      }
      setNewTitle("");
      setSelectedTemplate(undefined);
    } catch {
      setCreateError(t("creative.createFailed"));
      titleRef.current?.focus();
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const templateLabel = (templateId?: string) => {
    const template = findTemplate(templateId);
    return template ? t(template.nameKey) : null;
  };

  const statusTag = (status: ArticleStatus) => (
    <span className={cn(
      "inline-flex items-center rounded px-2 py-1 text-[11px] font-medium",
      status === "draft" ? "bg-accent text-accent-foreground"
        : status === "review" ? "bg-amber-500/10 text-amber-700 dark:text-amber-300"
        : "bg-muted text-muted-foreground",
    )}>{t(ARTICLE_STATUS_LABEL_KEY[status])}</span>
  );

  const clearFilters = () => {
    onQueryChange("");
    onStatusFilterChange("all");
  };

  return (
    <section className="mx-auto w-full min-w-0 max-w-[1120px] px-4 pb-10 pt-6 sm:px-8 lg:px-10 lg:pt-8">
      <header className="relative mb-5 flex items-center justify-between gap-5 sm:mb-6">
        <div className="min-w-0 flex-1 py-2">
          <p className="mb-2.5 flex items-center gap-2 text-[11px] font-medium tracking-wide text-accent-foreground">
            <span className="h-1.5 w-1.5 rounded-full bg-accent-foreground" aria-hidden />
            {t("creative.title")}
            {preview ? (
              <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {t("creative.sample_label")}
              </span>
            ) : null}
          </p>
          <h1 className="font-serif text-[28px] font-medium leading-[1.3] tracking-tight text-foreground sm:text-[34px]">
            {t("creative.headline")}
          </h1>
          <p className="mt-2.5 max-w-[590px] text-xs leading-6 text-muted-foreground sm:text-[13px]">
            {t("creative.hero_description")}
          </p>
        </div>
        <img src={writingIllustration} alt="" aria-hidden="true" width={690} height={460}
          decoding="async" className="hidden h-[132px] w-[184px] shrink-0 object-contain sm:block lg:w-[208px] dark:brightness-90" />
      </header>

      <form data-testid="create-article-form" aria-busy={submitting}
        onSubmit={(event) => { event.preventDefault(); void handleCreate(); }}
        className="rounded-xl border border-border bg-card shadow-[0_2px_4px_hsl(var(--foreground)/0.02),0_8px_28px_hsl(var(--foreground)/0.025)]">
        <div className="p-4 sm:p-5">
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <label htmlFor="new-article-title" className="text-xs font-semibold text-foreground">
              {t("creative.topic_label")}
            </label>
            <span id="new-article-title-hint" className="text-[11px] text-muted-foreground">
              {t("creative.title_hint")}
            </span>
          </div>
          <div className="flex flex-col gap-2.5 sm:flex-row">
            <Input id="new-article-title" ref={titleRef} value={newTitle} readOnly={submitting}
              onChange={(event) => { setNewTitle(event.target.value); setCreateError(null); }}
              onCompositionStart={() => { composingRef.current = true; }}
              onCompositionEnd={() => { composingRef.current = false; }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && (composingRef.current || event.nativeEvent.isComposing || event.keyCode === 229)) {
                  event.preventDefault();
                }
              }}
              aria-invalid={!!createError}
              aria-describedby={cn("new-article-title-hint", createError && "create-article-error")}
              placeholder={t("creative.new_placeholder")}
              className="h-11 min-w-0 flex-1 bg-background/50 px-3.5 text-[13px] placeholder:text-muted-foreground/75" />
            <Button type="submit" disabled={submitting}
              className="h-11 shrink-0 cursor-pointer gap-3 rounded-control px-5 text-xs">
              {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden /> : null}
              {t(submitting ? "creative.creating_btn" : "creative.create_btn")}
              {!submitting ? <ArrowRight className="h-3.5 w-3.5" aria-hidden /> : null}
            </Button>
          </div>
          {createError ? <p id="create-article-error" data-testid="create-error" role="alert" className="mt-2 text-xs font-medium text-destructive">{createError}</p> : null}
          <p className="mt-2.5 text-[11px] leading-5 text-muted-foreground">{t("creative.input_hint")}</p>
        </div>
        <section aria-labelledby="creative-templates-heading" className="rounded-b-xl border-t border-border/70 bg-background/35 px-4 pb-4 pt-3.5 sm:px-5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5">
            <h2 id="creative-templates-heading" className="text-[11px] font-medium text-muted-foreground">
              {t("creative.template_heading")}
            </h2>
            <p aria-live="polite" className="flex min-w-0 items-center gap-1.5 text-[11px] text-accent-foreground">
              <Sparkles className="h-3 w-3 shrink-0" aria-hidden />
              {selectedTemplate
                ? t("creative.selected_template", { name: templateLabel(selectedTemplate) })
                : t("creative.empty_template")}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
            {WRITING_TEMPLATES.map((template, index) => {
              const Icon = TEMPLATE_ICONS[index];
              const selected = selectedTemplate === template.id;
              return (
                <button key={template.id} type="button" data-testid={`template-${template.id}`}
                  aria-pressed={selected} disabled={submitting}
                  onClick={() => { setSelectedTemplate(selected ? undefined : template.id); titleRef.current?.focus(); }}
                  className={cn(
                    "group flex min-w-0 cursor-pointer items-start gap-2.5 rounded-lg border p-3 text-left transition-colors duration-150 motion-reduce:transition-none disabled:cursor-wait disabled:opacity-60",
                    FOCUS_STYLE,
                    selected ? "border-accent-foreground/45 bg-accent/65" : "border-border/75 bg-card/70 hover:border-accent-foreground/35 hover:bg-card",
                  )}>
                  <span className={cn("flex h-7 w-7 shrink-0 items-center justify-center rounded-md", selected ? "bg-card/75" : "bg-accent/55")}>
                    {selected ? <Check className="h-3.5 w-3.5 text-accent-foreground" aria-hidden /> : <Icon className="h-4 w-4 text-accent-foreground" strokeWidth={1.6} aria-hidden />}
                  </span>
                  <span className="min-w-0 pt-0.5">
                    <span className="block text-[11px] font-semibold leading-5 text-foreground sm:text-xs">{t(template.nameKey)}</span>
                    <span className="mt-0.5 block text-[10px] leading-[1.6] text-muted-foreground">{t(template.descKey)}</span>
                  </span>
                </button>
              );
            })}
          </div>
          <p className="mt-2.5 text-[10px] leading-5 text-muted-foreground">{t("creative.template_hint")}</p>
        </section>
      </form>

      <section className="mt-7 sm:mt-8" aria-labelledby="creative-library-heading">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 id="creative-library-heading" className="text-[15px] font-semibold">
                {t("creative.library_heading")}
              </h2>
              <span className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] tabular-nums text-muted-foreground">{totalCount}</span>
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">{t("creative.library_hint")}</p>
          </div>
          <button type="button" onClick={() => titleRef.current?.focus()}
            className={cn("flex cursor-pointer items-center gap-1.5 rounded-control px-2 py-2 text-[11px] text-accent-foreground transition-colors hover:bg-accent/60", FOCUS_STYLE)}>
            <Plus className="h-3.5 w-3.5" aria-hidden />
            {t("creative.new_article")}
          </button>
        </div>

        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-1" role="group"
            aria-label={t("creative.status_filter")}>
            {(["all", ...ARTICLE_STATUSES] as (ArticleStatus | "all")[]).map((status) => (
              <button key={status} type="button" data-testid={`status-filter-${status}`}
                aria-pressed={statusFilter === status}
                className={cn(
                  "cursor-pointer rounded-control px-2.5 py-2 text-[11px] transition-colors",
                  FOCUS_STYLE,
                  statusFilter === status ? "bg-accent font-medium text-accent-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )} onClick={() => onStatusFilterChange(status)}>{status === "all" ? t("creative.filter_all") : t(ARTICLE_STATUS_LABEL_KEY[status])}</button>
            ))}
          </div>
          <div className="relative w-full sm:w-[220px]">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input value={query} onChange={(event) => onQueryChange(event.target.value)}
              aria-label={t("creative.search_placeholder")}
              placeholder={t("creative.search_placeholder")}
              className="h-9 bg-card pl-9 text-xs" />
          </div>
        </div>

        {actionError ? (
          <div data-testid="article-action-error" role="alert"
            className="mb-4 flex items-center justify-between gap-3 rounded-control border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            <span>{actionError}</span>
            <button type="button" onClick={onDismissActionError}
              className={cn("shrink-0 cursor-pointer rounded px-1 py-1 text-muted-foreground hover:text-foreground", FOCUS_STYLE)}>
              {t("creative.dismiss")}
            </button>
          </div>
        ) : null}

        {listState === "loading" ? (
          <div data-testid="article-list-loading" aria-busy="true"
            role="status" className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((index) => (
              <div key={index} className="h-[200px] animate-pulse rounded-xl border border-border bg-card motion-reduce:animate-none" />
            ))}
          </div>
        ) : null}

        {listState === "error" ? (
          <div data-testid="article-list-error" role="alert"
            className="rounded-xl border border-dashed border-border bg-card px-5 py-10 text-center">
            <FileText className="mx-auto mb-4 h-6 w-6 text-muted-foreground" aria-hidden />
            <p className="text-[13px] text-muted-foreground">
              {t("creative.loadFailed")}
            </p>
            <Button variant="outline" size="sm" className="mt-3 cursor-pointer" onClick={onRetry}>
              {t("creative.retry")}
            </Button>
          </div>
        ) : null}

        {listState === "empty" ? (
          <div data-testid="article-list-empty"
            className="rounded-xl border border-dashed border-border bg-card/65 px-5 py-9 text-center text-xs text-muted-foreground">
            <span className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-accent/60 text-accent-foreground">
              <FileText className="h-5 w-5" strokeWidth={1.5} aria-hidden />
            </span>
            <p>
              {hasFilters ? t("creative.no_match") : t("creative.empty")}
            </p>
            <Button type="button" variant="outline" size="sm" className="mt-4 cursor-pointer text-xs"
              onClick={hasFilters ? clearFilters : () => titleRef.current?.focus()}>
              {hasFilters ? t("creative.clear_filters") : t("creative.empty_action")}
            </Button>
          </div>
        ) : null}

        {listState === "ready" ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {articles.map((article) => (
              <article key={article.id}
                className="group flex min-w-0 flex-col rounded-xl border border-border bg-card p-4 transition-colors hover:border-accent-foreground/35">
                <button type="button" data-testid={`open-article-${article.id}`}
                  onClick={() => onOpen(article.id)}
                  className={cn("min-w-0 flex-1 cursor-pointer rounded-sm text-left", FOCUS_STYLE)}>
                  <div className="mb-3 flex items-center justify-between gap-2">
                    {statusTag(article.status)}
                    <time dateTime={article.updatedAt}
                      title={fmtDateTime(article.updatedAt, i18n.resolvedLanguage) || article.updatedAt}
                      className="min-w-0 truncate text-[10px] text-muted-foreground">
                      {relativeTime(article.updatedAt, i18n.resolvedLanguage) || article.updatedAt}
                    </time>
                  </div>
                  <h3 className="mb-2 line-clamp-2 break-words text-[14px] font-semibold leading-6 text-foreground transition-colors group-hover:text-accent-foreground">
                    {article.title}
                  </h3>
                  <p className="line-clamp-2 text-[11px] leading-5 text-muted-foreground">
                    {article.summary || t("creative.no_summary")}
                  </p>
                </button>
                <div className="mt-4 flex flex-wrap items-center justify-between gap-x-2 gap-y-1 border-t border-border/70 pt-2.5">
                  <div className="flex min-w-0 items-center gap-0.5">
                    <button type="button" data-testid={`duplicate-article-${article.id}`}
                      aria-label={t("creative.duplicate")}
                      title={t("creative.duplicate")}
                      onClick={() => onDuplicate(article)}
                      className={cn("flex h-8 w-8 cursor-pointer items-center justify-center rounded-control text-muted-foreground hover:bg-muted hover:text-foreground", FOCUS_STYLE)}>
                      <Copy className="h-3.5 w-3.5" aria-hidden />
                    </button>
                    <button type="button" data-testid={`delete-article-${article.id}`}
                      aria-label={t("creative.delete")}
                      title={t("creative.delete")}
                      onClick={() => setDeleteTarget(article)}
                      className={cn("flex h-8 w-8 cursor-pointer items-center justify-center rounded-control text-muted-foreground hover:bg-destructive/10 hover:text-destructive", FOCUS_STYLE)}>
                      <Trash2 className="h-3.5 w-3.5" aria-hidden />
                    </button>
                    <span className="ml-1 text-[10px] text-muted-foreground">
                      {t("creative.revision_label", { revision: article.revision })}
                    </span>
                    {preview ? <span className="ml-1 text-[10px] text-muted-foreground">{t("creative.sample_label")}</span> : null}
                  </div>
                  <button type="button" aria-label={t("creative.edit_article")}
                    onClick={() => onOpen(article.id)}
                    className={cn("flex cursor-pointer items-center gap-1.5 rounded-control px-2 py-2 text-[11px] font-medium text-accent-foreground hover:bg-accent/60", FOCUS_STYLE)}>
                    {t("creative.continue_writing")}
                    <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : null}
      </section>

      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("creative.confirm_delete")}</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget
                ? t("creative.confirm_delete_body", { title: deleteTarget.title })
                : ""}
              {deleteTarget && draftIds?.has(deleteTarget.id) ? (
                <span
                  data-testid="delete-dirty-warning"
                  className="mt-2 block rounded-control border border-amber-500/40 bg-amber-500/10 px-2.5 py-1.5 text-[11px] leading-relaxed text-amber-800 dark:text-amber-200"
                >
                  {t("creative.confirm_delete_dirty")}
                </span>
              ) : null}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("creative.cancel")}</AlertDialogCancel>
            <AlertDialogAction data-testid="confirm-delete-article"
              onClick={() => { if (deleteTarget) onDelete(deleteTarget); setDeleteTarget(null); }}>
              {t("creative.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {preview ? (
        <p className="mt-6 text-[11px] leading-6 text-muted-foreground">
          {t("creative.note_storage")}
        </p>
      ) : null}
    </section>
  );
}
