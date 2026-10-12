import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { FileText, Plus, Search, Copy, Trash2, ArrowRight, Sparkles, Lightbulb, Code2, Feather, BookOpen, Check } from "lucide-react";

import writingIllustration from "@/assets/zhixu-writing-illustration.webp";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { cn } from "@/lib/utils";
import type { Article, ArticleStatus } from "./types";
import { ARTICLE_STATUSES, ARTICLE_STATUS_LABEL_KEY } from "./types";
import { WRITING_TEMPLATES, findTemplate } from "./templates";

interface Props {
  articles: Article[];
  totalCount: number;
  query: string;
  statusFilter: ArticleStatus | "all";
  onQueryChange: (q: string) => void;
  onStatusFilterChange: (s: ArticleStatus | "all") => void;
  onOpen: (id: string) => void;
  onCreate: (title: string, templateId?: string) => { error: string | null };
  onDuplicate: (id: string) => void;
  onDelete: (id: string) => void;
  onRestoreDemo: () => void;
}

const TEMPLATE_ICONS = [Lightbulb, Code2, Feather, BookOpen];

export function ArticleList({
  articles, totalCount, query, statusFilter, onQueryChange, onStatusFilterChange,
  onOpen, onCreate, onDuplicate, onDelete, onRestoreDemo,
}: Props) {
  const { t } = useTranslation();
  const [newTitle, setNewTitle] = useState("");
  const [selectedTemplate, setSelectedTemplate] = useState<string | undefined>();
  const [createError, setCreateError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Article | null>(null);
  const titleRef = useRef<HTMLTextAreaElement>(null);

  const handleCreate = () => {
    const { error } = onCreate(newTitle, selectedTemplate);
    if (error) {
      setCreateError(error);
      titleRef.current?.focus();
      return;
    }
    setNewTitle("");
    setSelectedTemplate(undefined);
    setCreateError(null);
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

  return (
    <section className="mx-auto w-full max-w-[1120px] px-5 pb-10 pt-8 sm:px-8 lg:px-12 lg:pt-10">
      <div className="relative mb-6 flex min-h-[124px] items-center justify-between gap-6">
        <div className="relative z-10 min-w-0 flex-1">
          <p className="mb-3 flex items-center gap-2 text-[11px] font-medium text-muted-foreground">
            <span className="h-1.5 w-1.5 rounded-sm bg-accent-foreground" aria-hidden />
            {t("creative.title", { defaultValue: "创作空间" })}
          </p>
          <h1 className="text-[28px] font-semibold leading-[1.4] tracking-tight text-foreground sm:text-[32px]">
            {t("creative.headline", { defaultValue: "今天，想写点什么？" })}
          </h1>
          <p className="mt-3 max-w-[560px] text-[13px] leading-7 text-muted-foreground">
            {t("creative.hero_description", { defaultValue: "一段代码、一次实践、一个新理解，都可以是一篇文章的开始。" })}
          </p>
        </div>
        <img src={writingIllustration} alt="" aria-hidden="true" width={640} height={640}
          decoding="async" className="hidden h-[160px] w-[190px] shrink-0 object-contain sm:block dark:brightness-90" />
      </div>

      <form onSubmit={(event) => { event.preventDefault(); handleCreate(); }}
        className="overflow-hidden rounded-xl border border-border bg-card shadow-[0_2px_4px_hsl(var(--foreground)/0.02),0_8px_28px_hsl(var(--foreground)/0.025)] transition-colors focus-within:border-accent-foreground/50">
        <Textarea ref={titleRef} value={newTitle}
          onChange={(event) => { setNewTitle(event.target.value); setCreateError(null); }}
          aria-label={t("creative.topic_label", { defaultValue: "文章主题" })}
          aria-invalid={!!createError} aria-describedby={createError ? "create-article-error" : undefined}
          placeholder={t("creative.new_placeholder", { defaultValue: "想讲清楚什么问题？" })}
          className="min-h-[104px] resize-none rounded-none border-0 bg-transparent px-5 py-5 text-[14px] leading-7 shadow-none focus-visible:ring-0 focus-visible:ring-offset-0 sm:px-6" />
        {createError ? <p id="create-article-error" role="alert" className="px-6 pb-3 text-xs font-medium text-destructive">{createError}</p> : null}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/70 px-4 py-3 sm:px-6">
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <Sparkles className="h-3.5 w-3.5 text-accent-foreground" aria-hidden />
            {selectedTemplate
              ? templateLabel(selectedTemplate)
              : t("creative.input_hint", { defaultValue: "以你的素材与观点为起点" })}
          </p>
          <Button type="submit" size="sm" className="h-9 cursor-pointer gap-3 rounded-control px-4 text-xs">
            {t("creative.create_btn", { defaultValue: "新建" })}
            <ArrowRight className="h-3.5 w-3.5" aria-hidden />
          </Button>
        </div>
      </form>

      <section className="mt-8 sm:mt-9" aria-labelledby="creative-templates-heading">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h2 id="creative-templates-heading" className="text-[14px] font-semibold">
            {t("creative.template_heading", { defaultValue: "从熟悉的方式开始" })}
          </h2>
          <span className="hidden text-[11px] text-muted-foreground sm:inline">
            {t("creative.template_hint", { defaultValue: "模板只是起点" })}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {WRITING_TEMPLATES.map((template, index) => {
            const Icon = TEMPLATE_ICONS[index];
            const selected = selectedTemplate === template.id;
            return (
              <button key={template.id} type="button" aria-pressed={selected}
                onClick={() => { setSelectedTemplate(selected ? undefined : template.id); titleRef.current?.focus(); }}
                className={cn(
                  "group relative min-w-0 cursor-pointer rounded-panel border bg-card p-4 text-left transition-colors duration-150 motion-reduce:transition-none sm:p-5",
                  selected ? "border-accent-foreground/50 bg-accent/50" : "border-border hover:border-accent-foreground/40",
                )}>
                <Icon className="mb-5 h-5 w-5 text-accent-foreground" strokeWidth={1.6} aria-hidden />
                {selected ? <Check className="absolute right-4 top-4 h-3.5 w-3.5 text-accent-foreground" aria-hidden /> : null}
                <span className="block text-[13px] font-semibold text-foreground">{t(template.nameKey)}</span>
                <span className="mt-2 block text-[11px] leading-6 text-muted-foreground">{t(template.descKey)}</span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="mt-9 sm:mt-10" aria-labelledby="creative-library-heading">
        <div className="mb-5 flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <h2 id="creative-library-heading" className="text-[15px] font-semibold">
              {t("creative.library_heading", { defaultValue: "我的文章" })}
            </h2>
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{totalCount}</span>
          </div>
          <button type="button" onClick={() => titleRef.current?.focus()}
            className="flex cursor-pointer items-center gap-1.5 rounded-control px-2 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
            <Plus className="h-3.5 w-3.5" aria-hidden />
            {t("creative.new_article", { defaultValue: "写一篇新文章" })}
          </button>
        </div>

        <div className="mb-5 flex flex-wrap items-center justify-between gap-4">
          <div className="flex flex-wrap items-center gap-1" role="group" aria-label="状态筛选">
            {(["all", ...ARTICLE_STATUSES] as (ArticleStatus | "all")[]).map((status) => (
              <button key={status} type="button" aria-pressed={statusFilter === status}
                className={cn(
                  "cursor-pointer rounded-control px-3 py-2 text-xs transition-colors",
                  statusFilter === status ? "bg-accent font-medium text-accent-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )} onClick={() => onStatusFilterChange(status)}>{status === "all" ? t("creative.filter_all", { defaultValue: "全部" }) : t(ARTICLE_STATUS_LABEL_KEY[status])}</button>
            ))}
          </div>
          <div className="relative w-full sm:w-[220px]">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input value={query} onChange={(event) => onQueryChange(event.target.value)}
              aria-label={t("creative.search_placeholder", { defaultValue: "搜索标题或摘要" })}
              placeholder={t("creative.search_placeholder", { defaultValue: "搜索标题或摘要" })}
              className="h-9 bg-card pl-9 text-xs" />
          </div>
        </div>

        {articles.length === 0 ? (
          <div className="rounded-panel border border-dashed border-border bg-card py-14 text-center text-[13px] text-muted-foreground">
            {totalCount === 0 ? <>
              <FileText className="mx-auto mb-4 h-6 w-6" aria-hidden />
              <p>{t("creative.empty", { defaultValue: "还没有文章" })}</p>
              <Button variant="outline" size="sm" className="mt-3" onClick={onRestoreDemo}>
                {t("creative.restore_demo", { defaultValue: "恢复示例" })}
              </Button>
            </> : <p>{t("creative.no_match", { defaultValue: "没有符合条件的文章" })}</p>}
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {articles.map((article) => (
              <article key={article.id}
                className="group flex min-w-0 flex-col rounded-panel border border-border bg-card p-5 transition-colors hover:border-accent-foreground/35">
                <button type="button" onClick={() => onOpen(article.id)}
                  className="min-w-0 flex-1 cursor-pointer text-left">
                  <div className="mb-5 flex items-center justify-between gap-3">
                    {statusTag(article.status)}
                    <span className="text-[10px] text-muted-foreground">
                      {templateLabel(article.templateId)}
                    </span>
                  </div>
                  <h3 className="mb-3 min-h-[48px] break-words text-[15px] font-semibold leading-6 text-foreground">
                    {article.title}
                  </h3>
                  <p className="line-clamp-2 min-h-[44px] text-xs leading-[22px] text-muted-foreground">
                    {article.summary || t("creative.no_summary", { defaultValue: "暂无摘要" })}
                  </p>
                </button>
                <div className="mt-5 flex items-center justify-between gap-2 border-t border-border/70 pt-3 text-[11px] text-muted-foreground">
                  <span className="flex items-center gap-1.5">
                    <BookOpen className="h-3 w-3" aria-hidden />
                    {t("creative.material_count", { defaultValue: "{{count}} 份素材", count: article.materials.length })}
                  </span>
                  <span>{article.updatedAt}</span>
                </div>
                <div className="mt-3 flex items-center justify-between">
                  <div className="flex items-center gap-1">
                    <button type="button" aria-label="复制" title="复制"
                      onClick={() => onDuplicate(article.id)}
                      className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-control text-muted-foreground hover:bg-muted hover:text-foreground">
                      <Copy className="h-3.5 w-3.5" aria-hidden />
                    </button>
                    <button type="button" aria-label="删除" title="删除"
                      onClick={() => setDeleteTarget(article)}
                      className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-control text-muted-foreground hover:bg-destructive/10 hover:text-destructive">
                      <Trash2 className="h-3.5 w-3.5" aria-hidden />
                    </button>
                  </div>
                  <button type="button" aria-label="编辑" onClick={() => onOpen(article.id)}
                    className="flex cursor-pointer items-center gap-2 rounded-control px-2 py-1.5 text-[11px] font-medium text-foreground hover:bg-muted">
                    {t("creative.continue_writing", { defaultValue: "继续写作" })}
                    <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("creative.confirm_delete", { defaultValue: "确认删除？" })}</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget ? `「${deleteTarget.title}」将被移除，此操作无法撤销。` : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => { if (deleteTarget) onDelete(deleteTarget.id); setDeleteTarget(null); }}>删除</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <p className="mt-6 text-[11px] leading-6 text-muted-foreground">
        {t("creative.note_storage", { defaultValue: "当前内容仅在本次访问内保留，刷新前请导出草稿。" })}
      </p>
    </section>
  );
}
