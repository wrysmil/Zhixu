import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Download, Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { cn } from "@/lib/utils";
import { MarkdownText } from "@/components/MarkdownText";
import type { Article } from "./types";
import { ARTICLE_STATUS_LABEL_KEY } from "./types";
import type { ArticleSnapshotPayload } from "@/lib/writing-api";
import { exportArticleMarkdown } from "@/lib/writing-export";
import type { SaveFlowState } from "./use-article-save-flow";
import { ARTICLE_DELETED_REVISION, mergeImeValue } from "./use-article-save-flow";
import { countBodyChars } from "./article-model";
import { ConflictDialog } from "./ConflictDialog";
import { MaterialPanel } from "./MaterialPanel";
import { OutlinePanel } from "./OutlinePanel";

/** 正文视图模式：A-03 要求三种模式之间切换都不丢内容。 */
export type EditorMode = "edit" | "preview" | "split";
type LeftTab = "materials" | "outline";

const MODE_TABS: Array<{ mode: EditorMode; key: string; fallback: string }> = [
  { mode: "edit", key: "creative.tab_edit", fallback: "编辑" },
  { mode: "preview", key: "creative.tab_preview", fallback: "预览" },
  { mode: "split", key: "creative.tab_split", fallback: "分屏" },
];

interface Props {
  /** 服务端返回的文章身份（id/revision/createdAt）；内容以 `draft` 为准。 */
  article: Article;
  draft: ArticleSnapshotPayload;
  dirty: boolean;
  saveState: SaveFlowState;
  setField: <K extends keyof ArticleSnapshotPayload>(key: K, value: ArticleSnapshotPayload[K]) => void;
  onSaveNow: () => Promise<void>;
  onAcknowledgeConflict: (resolution: "reload" | "savedCopy" | "discardLocal") => Promise<void>;
  /** 冲突期间读取服务器最新版本（A-08 第 3 条：给出读取最新内容的入口）。 */
  onFetchLatest: () => Promise<Article>;
  /** 用本地草稿另存一篇新文章（A-08 第 5 条），成功后编辑器切到新文章。 */
  onSaveCopy: () => Promise<void>;
  onBack: () => void;
  /** A-07 重开恢复横幅，null 表示无可恢复内容。 */
  recoveryBanner?: ReactNode;
  /** A-07 本地恢复保障不可用时的静态提示开关。 */
  storageUnhealthy?: boolean;
  /** A-07 离开守卫对话框开关。 */
  leaveGuardOpen?: boolean;
  onLeaveRetry?: () => void;
  onLeaveStay?: () => void;
  onLeaveForce?: () => void;
}

export function ArticleEditor({
  article: _article,
  draft,
  dirty,
  saveState,
  setField,
  onSaveNow,
  onAcknowledgeConflict,
  onFetchLatest,
  onSaveCopy,
  onBack,
  recoveryBanner,
  storageUnhealthy = false,
  leaveGuardOpen = false,
  onLeaveRetry,
  onLeaveStay,
  onLeaveForce,
}: Props) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<EditorMode>("edit");
  const [leftTab, setLeftTab] = useState<LeftTab>("materials");
  const [bodyMergeHint, setBodyMergeHint] = useState(false);
  const [conflictOpen, setConflictOpen] = useState(false);
  const [latest, setLatest] = useState<Article | null>(null);
  const [latestLoading, setLatestLoading] = useState(false);
  const [exportHint, setExportHint] = useState<null | "empty" | "failed" | "dirty" | "saved">(null);

  const conflict = saveState.conflict;
  const inConflict = saveState.phase === "conflict";

  /**
   * 进入冲突态就弹出对话框并读取服务器最新版本；服务端已删除（`ARTICLE_DELETED_REVISION`）
   * 时不再去读，直接给出「另存副本」这条出路（A-02/A-08）。
   */
  useEffect(() => {
    if (!inConflict) {
      setConflictOpen(false);
      setLatest(null);
      setLatestLoading(false);
      return undefined;
    }
    setConflictOpen(true);
    if (!conflict || conflict.currentRevision === ARTICLE_DELETED_REVISION) {
      setLatest(null);
      setLatestLoading(false);
      return undefined;
    }
    let active = true;
    setLatest(null);
    setLatestLoading(true);
    onFetchLatest()
      .then((remote) => {
        if (!active) return;
        setLatest(remote);
        setLatestLoading(false);
      })
      .catch(() => {
        if (!active) return;
        setLatest(null);
        setLatestLoading(false);
      });
    return () => {
      active = false;
    };
  }, [inConflict, conflict, onFetchLatest]);

  /**
   * 正文 textarea 是非受控的：保存响应、恢复稿等异步来源都晚于输入，
   * 用 `value={draft.body}` 强制回写会把用户刚敲进去的字（尤其中文组合串）冲掉。
   * 因此 DOM 内容只由输入自己维护，`renderedBodyRef` 记录「上一次交给 textarea 的正文」
   * 供 `mergeImeValue` 三方比对。
   */
  const bodyRef = useRef<HTMLTextAreaElement | null>(null);
  const renderedBodyRef = useRef(draft.body);
  const composingRef = useRef(false);

  useEffect(() => {
    const el = bodyRef.current;
    // 组合期间正文归输入法，任何异步来源都不许插手。
    if (!el || composingRef.current) return;
    // 走到这里说明正文是被第三方改的（冲突重载/恢复稿合并），显式同步一次 DOM。
    if (el.value !== draft.body) el.value = draft.body;
    renderedBodyRef.current = draft.body;
  }, [draft.body]);

  const commitBody = (value: string) => {
    setBodyMergeHint(false);
    setField("body", value);
  };

  const handleBodyChange = (value: string) => {
    if (composingRef.current) return;
    renderedBodyRef.current = value;
    commitBody(value);
  };

  const handleCompositionEnd = () => {
    composingRef.current = false;
    const el = bodyRef.current;
    if (!el) return;
    const merged = mergeImeValue(draft.body, renderedBodyRef.current, el.value);
    if (merged === null) {
      // 组合期间正文被异步来源改过：两份内容都保留，绝不覆盖（合并策略归 A3.5）。
      setBodyMergeHint(true);
      return;
    }
    renderedBodyRef.current = merged;
    commitBody(merged);
  };

  const handleExport = () => {
    // A-09 / 计划 A3.7：导出的是当前草稿，不是仅服务端版本。
    const outcome = exportArticleMarkdown(draft);
    if (outcome.status === "empty") {
      setExportHint("empty");
      return;
    }
    if (outcome.status === "failed") {
      setExportHint("failed");
      return;
    }
    setExportHint(dirty ? "dirty" : "saved");
  };

  const saveLabel = () => {
    switch (saveState.phase) {
      case "saved":
        return saveState.lastSavedAt
          ? t("creative.save.saved_at", { defaultValue: "已保存 · {{time}}", time: saveState.lastSavedAt })
          : t("creative.save.saved", { defaultValue: "已保存" });
      case "dirty":
        return t("creative.save.dirty", { defaultValue: "有未保存的修改" });
      case "saving":
        return t("creative.save.saving", { defaultValue: "保存中…" });
      case "failed":
        return t("creative.save.failed", { defaultValue: "保存失败，本地稿已保留" });
      case "conflict":
        return t("creative.save.conflict", { defaultValue: "版本冲突，已暂停自动保存" });
      case "offline":
        return t("creative.save.offline", { defaultValue: "离线，已暂停保存" });
      default:
        return "";
    }
  };

  const showEditor = mode === "edit" || mode === "split";
  const showPreview = mode === "preview" || mode === "split";

  const bodyInput = (
    <textarea
      id="article-body-input"
      data-testid="article-body-input"
      ref={bodyRef}
      defaultValue={draft.body}
      onChange={(e) => handleBodyChange(e.target.value)}
      onCompositionStart={() => {
        composingRef.current = true;
      }}
      onCompositionEnd={handleCompositionEnd}
      className="min-h-[300px] h-full w-full resize-none border-none bg-transparent text-[15px] leading-relaxed text-foreground outline-none"
      placeholder={t("creative.body_placeholder", { defaultValue: "开始写作…" })}
      aria-label="正文编辑"
    />
  );

  const previewPane = (
    <article
      data-testid="article-preview-pane"
      className="prose prose-sm max-w-none text-[15px] leading-relaxed dark:prose-invert"
    >
      <MarkdownText>{draft.body || t("creative.body_empty", { defaultValue: "*暂无内容*" })}</MarkdownText>
    </article>
  );

  return (
    <div data-testid="article-editor" className="flex h-full flex-col">
      {/* Editor header */}
      <div className="flex h-[52px] shrink-0 items-center justify-between border-b border-border bg-settings-surface px-4">
        <div className="flex items-center gap-3 min-w-0">
          <Button variant="ghost" size="sm" onClick={onBack} aria-label="Back" className="shrink-0">
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <span data-testid="article-editor-title" className="truncate text-[14px] font-medium text-foreground">
            {draft.title}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Button variant="outline" size="sm" onClick={() => void onSaveNow()}
            disabled={saveState.phase === "saving" || !dirty}>
            <Save className="mr-1 h-3.5 w-3.5" />
            {t("creative.save_now", { defaultValue: "保存" })}
          </Button>
          <Button variant="outline" size="sm" onClick={handleExport}>
            <Download className="mr-1 h-3.5 w-3.5" />
            {t("creative.export_md", { defaultValue: "导出 Markdown" })}
          </Button>
        </div>
      </div>

      {inConflict ? (
        <div role="alert" className="flex items-center justify-between gap-3 border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-[11px] text-amber-800 dark:text-amber-200">
          <span className="min-w-0 truncate">
            {saveState.lastError || t("creative.save.conflict", { defaultValue: "版本冲突，已暂停自动保存" })}
          </span>
          <Button
            variant="outline"
            size="sm"
            data-testid="conflict-reopen"
            className="h-6 shrink-0 text-[11px] cursor-pointer"
            onClick={() => setConflictOpen(true)}
          >
            {t("creative.conflict.reopen", { defaultValue: "处理冲突" })}
          </Button>
        </div>
      ) : null}

      {recoveryBanner}

      {storageUnhealthy ? (
        <div
          data-testid="recovery-storage-hint"
          role="status"
          className="flex items-center gap-2 border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-[11px] text-amber-800 dark:text-amber-200"
        >
          <span className="min-w-0 truncate">
            {t("creative.recovery.storageUnavailable", {
              defaultValue: "本地恢复保障不可用，保存与导出仍可继续。",
            })}
          </span>
        </div>
      ) : null}

      {exportHint === "empty" || exportHint === "failed" || exportHint === "dirty" ? (
        <div
          data-testid="export-hint"
          role="status"
          className={cn(
            "flex items-center justify-between gap-3 border-b px-4 py-2 text-[11px]",
            exportHint === "dirty"
              ? "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-200"
              : "border-destructive/40 bg-destructive/5 text-destructive",
          )}
        >
          <span className="min-w-0 truncate">
            {exportHint === "empty"
              ? t("creative.export.empty", { defaultValue: "正文为空，未生成文件。" })
              : exportHint === "failed"
                ? t("creative.export.failed", { defaultValue: "导出失败，请重试。" })
                : t("creative.export.dirty", {
                    defaultValue: "已导出含未保存修改的 Markdown，仅供本地参考。",
                  })}
          </span>
          <button
            type="button"
            onClick={() => setExportHint(null)}
            className="shrink-0 text-muted-foreground hover:text-foreground"
            aria-label={t("creative.export.dismiss", { defaultValue: "知道了" })}
          >
            {t("creative.export.dismiss", { defaultValue: "知道了" })}
          </button>
        </div>
      ) : null}

      {/* Three-column body */}
      <div className="flex min-h-0 flex-1">
        {/* Left panel: materials/outline */}
        <div className="w-[220px] shrink-0 overflow-y-auto border-r border-border bg-settings-surface/60 p-3">
          <div className="mb-3 flex gap-1">
            <button
              type="button"
              className={cn(
                "rounded-control px-2.5 py-1 text-[12px] cursor-pointer",
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
                "rounded-control px-2.5 py-1 text-[12px] cursor-pointer",
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
              materials={draft.materials}
              onChange={(materials) => setField("materials", materials)}
            />
          ) : (
            <OutlinePanel
              outline={draft.outline}
              onChange={(outline) => setField("outline", outline)}
            />
          )}
        </div>

        {/* Center: editor/preview/split */}
        <div className="flex min-w-0 flex-1 flex-col bg-background">
          <div className="flex h-9 shrink-0 items-center gap-1 border-b border-border/60 px-4">
            {MODE_TABS.map((tab) => (
              <button
                key={tab.mode}
                type="button"
                className={cn(
                  "rounded px-2 py-0.5 text-[12px] cursor-pointer",
                  mode === tab.mode
                    ? "bg-primary/10 font-medium text-primary"
                    : "text-muted-foreground",
                )}
                onClick={() => setMode(tab.mode)}
              >
                {t(tab.key, { defaultValue: tab.fallback })}
              </button>
            ))}
          </div>

          {bodyMergeHint ? (
            <div
              role="alert"
              data-testid="body-merge-hint"
              className="border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-[11px] text-amber-800 dark:text-amber-200"
            >
              {t("creative.editor.imeMergeHint", {
                defaultValue: "组合输入期间正文有其它更新，本地内容已保留，未自动覆盖任一方。",
              })}
            </div>
          ) : null}

          <div className="min-h-0 flex-1 overflow-hidden">
            <div
              className={cn(
                "grid h-full min-h-0",
                mode === "split" ? "grid-cols-2 divide-x divide-border/60" : "grid-cols-1",
              )}
            >
              {showEditor ? <div className="min-h-0 overflow-y-auto p-6">{bodyInput}</div> : null}
              {showPreview ? <div className="min-h-0 overflow-y-auto p-6">{previewPane}</div> : null}
            </div>
          </div>
        </div>

        {/* Right panel: 阶段 A 建议为只读空态，阶段 B 才接入建议流 */}
        <div className="w-[260px] shrink-0 overflow-y-auto border-l border-border bg-settings-surface/60 p-3">
          <p className="mb-3 text-[12px] font-medium text-foreground">
            {t("creative.suggestions_title", { defaultValue: "AI 建议" })}
          </p>
          <p
            data-testid="suggestions-placeholder"
            className="rounded-control border border-dashed border-border px-2.5 py-3 text-[11px] leading-relaxed text-muted-foreground"
          >
            {t("creative.suggestions_pending", {
              defaultValue: "建议功能将在阶段 B 接入，现在写作不受影响。",
            })}
          </p>
        </div>
      </div>

      {/* Status bar */}
      <div className="flex h-8 shrink-0 items-center justify-between border-t border-border bg-settings-surface px-4 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-3">
          <span>{t(ARTICLE_STATUS_LABEL_KEY[draft.status])}</span>
          {/* spec A-03：只报字符数（去空白），不做「词数」口径。 */}
          <span data-testid="body-stats">
            {t("creative.editor.charCount", {
              count: countBodyChars(draft.body),
              defaultValue: "{{count}} 字",
            })}
          </span>
        </span>
        <span className="flex items-center gap-3">
          <span data-testid="save-phase-label" aria-live="polite">{saveLabel()}</span>
        </span>
      </div>

      {inConflict && conflictOpen ? (
        <ConflictDialog
          conflict={conflict}
          latest={latest}
          latestLoading={latestLoading}
          localBody={draft.body}
          onReloadLatest={() => {
            void onAcknowledgeConflict("reload");
          }}
          onSaveCopy={onSaveCopy}
          onDiscardLocal={() => onAcknowledgeConflict("discardLocal")}
          onCancel={() => setConflictOpen(false)}
        />
      ) : null}

      {leaveGuardOpen ? (
        <AlertDialog open onOpenChange={(open) => (!open ? onLeaveStay?.() : undefined)}>
          <AlertDialogContent
            data-testid="leave-guard"
            className="w-[min(calc(100vw-2rem),28rem)] gap-0 p-5"
          >
            <AlertDialogHeader>
              <AlertDialogTitle className="text-left text-[15px] font-semibold leading-snug text-foreground">
                {t("creative.leaveGuard.title", {
                  defaultValue: "这篇文章还有未保存的修改",
                })}
              </AlertDialogTitle>
              <AlertDialogDescription className="text-left text-[12px] leading-relaxed">
                {t("creative.leaveGuard.body", {
                  defaultValue: "自动保存未成功完成。可以再试一次、留在当前页继续编辑，或确认离开。离开后本地恢复稿仍保留，下次回来还能再恢复。",
                })}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter className="mt-5 grid grid-cols-1 gap-2 sm:flex sm:flex-row sm:justify-end">
              <AlertDialogCancel
                onClick={onLeaveStay}
                data-testid="leave-guard-stay"
                className="mt-0 h-7 w-auto min-w-0 text-[11px]"
              >
                {t("creative.leaveGuard.stay", { defaultValue: "留在当前页" })}
              </AlertDialogCancel>
              <Button
                variant="outline"
                size="sm"
                data-testid="leave-guard-retry"
                onClick={onLeaveRetry}
                className="h-7 cursor-pointer text-[11px]"
              >
                {t("creative.leaveGuard.retry", { defaultValue: "重试保存" })}
              </Button>
              <Button
                size="sm"
                data-testid="leave-guard-force"
                onClick={onLeaveForce}
                className="h-7 text-[11px]"
              >
                {t("creative.leaveGuard.force", { defaultValue: "确认离开" })}
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      ) : null}
    </div>
  );
}
