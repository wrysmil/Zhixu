import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Download, PanelRightClose, PanelRightOpen } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { MarkdownText } from "@/components/MarkdownText";
import type { Article, Suggestion } from "./types";
import { ARTICLE_STATUS_LABEL_KEY } from "./types";
import { buildExportFilename, generateMarkdown } from "./article-model";
import { MaterialPanel } from "./MaterialPanel";
import { OutlinePanel } from "./OutlinePanel";
import { SuggestionPanel } from "./SuggestionPanel";

type EditorTab = "edit" | "preview";
type LeftTab = "materials" | "outline";

interface Props {
  article: Article;
  suggestions: Suggestion[];
  onUpdate: (patch: Partial<Article>) => void;
  onBack: () => void;
  onAcceptSuggestion: (s: Suggestion) => boolean;
  onUndoSuggestion: (s: Suggestion) => boolean;
}

export function ArticleEditor({
  article,
  suggestions,
  onUpdate,
  onBack,
  onAcceptSuggestion,
  onUndoSuggestion,
}: Props) {
  const { t } = useTranslation();
  const [editorTab, setEditorTab] = useState<EditorTab>("edit");
  const [leftTab, setLeftTab] = useState<LeftTab>("materials");
  const [rightOpen, setRightOpen] = useState(true);

  const handleExport = () => {
    const md = generateMarkdown(article);
    const filename = buildExportFilename(article.title);
    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex h-full flex-col">
      {/* Editor header */}
      <div className="flex h-[52px] shrink-0 items-center justify-between border-b border-border bg-settings-surface px-4">
        <div className="flex items-center gap-3 min-w-0">
          <Button variant="ghost" size="sm" onClick={onBack} aria-label="Back" className="shrink-0">
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <span className="truncate text-[14px] font-medium text-foreground">
            {article.title}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Button variant="outline" size="sm" onClick={handleExport}>
            <Download className="mr-1 h-3.5 w-3.5" />
            {t("creative.export_md", { defaultValue: "导出 Markdown" })}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setRightOpen((v) => !v)}
            aria-label={rightOpen ? "关闭建议面板" : "打开建议面板"}
          >
            {rightOpen ? (
              <PanelRightClose className="h-4 w-4" />
            ) : (
              <PanelRightOpen className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>

      {/* Three-column body */}
      <div className="flex min-h-0 flex-1">
        {/* Left panel: materials/outline */}
        <div className="w-[220px] shrink-0 overflow-y-auto border-r border-border bg-settings-surface/60 p-3">
          <div className="mb-3 flex gap-1">
            <button
              type="button"
              className={cn(
                "rounded-control px-2.5 py-1 text-[12px]",
                leftTab === "materials"
                  ? "bg-primary/10 font-medium text-primary"
                  : "text-muted-foreground",
              )}
              onClick={() => setLeftTab("materials")}
            >
              {t("creative.tab_materials", { defaultValue: "素材" })}
            </button>
            <button
              type="button"
              className={cn(
                "rounded-control px-2.5 py-1 text-[12px]",
                leftTab === "outline"
                  ? "bg-primary/10 font-medium text-primary"
                  : "text-muted-foreground",
              )}
              onClick={() => setLeftTab("outline")}
            >
              {t("creative.tab_outline", { defaultValue: "提纲" })}
            </button>
          </div>
          {leftTab === "materials" ? (
            <MaterialPanel
              materials={article.materials}
              onChange={(materials) => onUpdate({ materials })}
            />
          ) : (
            <OutlinePanel
              outline={article.outline}
              onChange={(outline) => onUpdate({ outline })}
            />
          )}
        </div>

        {/* Center: editor/preview */}
        <div className="flex min-w-0 flex-1 flex-col bg-background">
          <div className="flex h-9 shrink-0 items-center gap-1 border-b border-border/60 px-4">
            <button
              type="button"
              className={cn(
                "rounded px-2 py-0.5 text-[12px]",
                editorTab === "edit"
                  ? "bg-primary/10 font-medium text-primary"
                  : "text-muted-foreground",
              )}
              onClick={() => setEditorTab("edit")}
            >
              {t("creative.tab_edit", { defaultValue: "编辑" })}
            </button>
            <button
              type="button"
              className={cn(
                "rounded px-2 py-0.5 text-[12px]",
                editorTab === "preview"
                  ? "bg-primary/10 font-medium text-primary"
                  : "text-muted-foreground",
              )}
              onClick={() => setEditorTab("preview")}
            >
              {t("creative.tab_preview", { defaultValue: "预览" })}
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-6">
            {editorTab === "edit" ? (
              <textarea
                className="min-h-[300px] w-full resize-none border-none bg-transparent text-[15px] leading-relaxed text-foreground outline-none"
                value={article.body}
                onChange={(e) => onUpdate({ body: e.target.value })}
                placeholder={t("creative.body_placeholder", {
                  defaultValue: "开始写作…",
                })}
                aria-label="正文编辑"
              />
            ) : (
              <article className="prose prose-sm max-w-none text-[15px] leading-relaxed dark:prose-invert">
                <MarkdownText>{article.body || "*暂无内容*"}</MarkdownText>
              </article>
            )}
          </div>
        </div>

        {/* Right panel: suggestions */}
        {rightOpen ? (
          <div className="w-[260px] shrink-0 overflow-y-auto border-l border-border bg-settings-surface/60 p-3">
            <SuggestionPanel
              article={article}
              suggestions={suggestions}
              onAccept={onAcceptSuggestion}
              onUndo={onUndoSuggestion}
            />
          </div>
        ) : null}
      </div>

      {/* Status bar */}
      <div className="flex h-8 shrink-0 items-center justify-between border-t border-border bg-settings-surface px-4 text-[11px] text-muted-foreground">
        <span>{t(ARTICLE_STATUS_LABEL_KEY[article.status])}</span>
        <span>
          {t("creative.storage_note", {
            defaultValue: "内容仅在本次访问内保留",
          })}
        </span>
      </div>
    </div>
  );
}
