import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  BookOpen,
  Download,
  ListTree,
  MessagesSquare,
  Save,
  X,
} from "lucide-react";

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
import { utf16RangeToCodepoints } from "@/lib/writing-collab-api";
import type { WritingSelectionContext } from "@/lib/types";

/** 正文视图模式：A-03 要求三种模式之间切换都不丢内容。 */
export type EditorMode = "edit" | "preview" | "split";
type LeftTab = "materials" | "outline";

export type WritingSelectionAction =
  | "ask"
  | "rewrite"
  | "expand"
  | "condense"
  | "explain"
  | "verify"
  | "tone";

type TextSelection = { start: number; end: number; text: string };

const CHAT_WIDTH_KEY = "zhixu.creative.chat-width";
const CHAT_WIDTH_MIN = 340;
const CHAT_WIDTH_MAX = 480;
const CHAT_WIDTH_DEFAULT = 380;

function clampChatWidth(value: number): number {
  return Math.min(CHAT_WIDTH_MAX, Math.max(CHAT_WIDTH_MIN, Math.round(value)));
}

function readChatWidth(): number {
  if (typeof window === "undefined") return CHAT_WIDTH_DEFAULT;
  try {
    const raw = window.localStorage.getItem(CHAT_WIDTH_KEY);
    const parsed = raw ? Number(raw) : NaN;
    return Number.isFinite(parsed) ? clampChatWidth(parsed) : CHAT_WIDTH_DEFAULT;
  } catch {
    return CHAT_WIDTH_DEFAULT;
  }
}

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
  /**
   * 右侧真实团队会话：由 `CreativeWorkspace` 构造并注入，编辑器不自己取数。
   * 传函数时由编辑器注入收起动作，让窄屏浮层里也有显式关闭入口。
   */
  writingChat?: ReactNode | ((controls: { onCollapse: () => void }) => ReactNode);
  /** 划词操作条；只有真实创作入口注入，预览入口不产生写请求。 */
  onSelectionAction?: (selection: WritingSelectionContext, action: WritingSelectionAction) => void;
  /** 团队写入后的对比/撤销条，由容器按文章版本构造。 */
  remoteBanner?: ReactNode;
}

export function ArticleEditor({
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
  writingChat,
  onSelectionAction,
  remoteBanner,
}: Props) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<EditorMode>("edit");
  const [drawer, setDrawer] = useState<LeftTab | null>(null);
  const [chatWidth, setChatWidth] = useState(readChatWidth);
  const [selection, setSelection] = useState<TextSelection | null>(null);
  const [moreActionsOpen, setMoreActionsOpen] = useState(false);
  const resizeRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const [bodyMergeHint, setBodyMergeHint] = useState(false);
  const [conflictOpen, setConflictOpen] = useState(false);
  const [latest, setLatest] = useState<Article | null>(null);
  const [latestLoading, setLatestLoading] = useState(false);
  const [exportHint, setExportHint] = useState<null | "empty" | "failed" | "dirty" | "saved">(null);
  // 宽屏默认展开；窄屏（<1024px）默认收起，面板在窄屏是浮层，不挤压正文。
  const [chatOpen, setChatOpen] = useState(() => (
    typeof window === "undefined" || typeof window.matchMedia !== "function"
      ? true
      : window.matchMedia("(min-width: 1024px)").matches
  ));

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

  const syncSelection = () => {
    const el = bodyRef.current;
    if (!el) return;
    const start = el.selectionStart ?? 0;
    const end = el.selectionEnd ?? 0;
    if (end <= start || composingRef.current) {
      setSelection(null);
      setMoreActionsOpen(false);
      return;
    }
    const text = el.value.slice(start, end);
    setSelection((prev) => (
      prev && prev.start === start && prev.end === end && prev.text === text
        ? prev
        : { start, end, text }
    ));
  };

  useEffect(() => {
    if (mode === "preview") {
      setSelection(null);
      setMoreActionsOpen(false);
    }
  }, [mode]);

  const buildSelectionContext = (current: TextSelection): WritingSelectionContext | null => {
    const range = utf16RangeToCodepoints(draft.body, current.start, current.end);
    if (range.end <= range.start) return null;
    const chars = Array.from(draft.body);
    const prefix = chars.slice(Math.max(0, range.start - 40), range.start).join("");
    const suffix = chars.slice(range.end, range.end + 40).join("");
    return {
      text: current.text,
      start: range.start,
      end: range.end,
      ...(prefix ? { prefix } : {}),
      ...(suffix ? { suffix } : {}),
    };
  };

  const dispatchSelectionAction = (
    action: WritingSelectionAction,
    current: TextSelection | null = selection,
  ) => {
    if (!current || !onSelectionAction) return;
    const context = buildSelectionContext(current);
    if (!context) return;
    setMoreActionsOpen(false);
    setChatOpen(true);
    onSelectionAction(context, action);
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
  const writingChatNode = typeof writingChat === "function"
    ? writingChat({ onCollapse: () => setChatOpen(false) })
    : writingChat;

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
      onSelect={syncSelection}
      onKeyUp={(event) => {
        if (event.key.startsWith("Arrow") || event.key === "Shift") syncSelection();
        if (
          (event.metaKey || event.ctrlKey)
          && event.key.toLowerCase() === "i"
        ) {
          const el = bodyRef.current;
          if (!el) return;
          const start = el.selectionStart ?? 0;
          const end = el.selectionEnd ?? 0;
          if (end <= start) return;
          event.preventDefault();
          const current = { start, end, text: el.value.slice(start, end) };
          dispatchSelectionAction("ask", current);
        }
      }}
      className="min-h-[300px] h-full w-full resize-none border-none bg-transparent text-[13px] leading-relaxed text-foreground outline-none"
      placeholder={t("creative.body_placeholder", { defaultValue: "开始写作…" })}
      aria-label="正文编辑"
    />
  );

  const previewPane = (
    <article
      data-testid="article-preview-pane"
      className="prose prose-sm max-w-none text-[13px] leading-relaxed prose-h1:text-base prose-h2:text-sm prose-h3:text-[13px] prose-h4:text-[12.5px] dark:prose-invert"
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
          <Button
            variant="outline"
            size="sm"
            onClick={() => setDrawer((open) => (open === "materials" ? null : "materials"))}
            aria-label={t("creative.tab_materials", { defaultValue: "素材" })}
            aria-expanded={drawer === "materials"}
            aria-controls="creative-side-drawer"
            data-testid="materials-toggle"
          >
            <BookOpen className="h-3.5 w-3.5 md:mr-1" />
            <span className="hidden md:inline">
              {t("creative.tab_materials", { defaultValue: "素材" })}
              {draft.materials.length > 0 ? ` ${draft.materials.length}` : ""}
            </span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setDrawer((open) => (open === "outline" ? null : "outline"))}
            aria-label={t("creative.tab_outline", { defaultValue: "提纲" })}
            aria-expanded={drawer === "outline"}
            aria-controls="creative-side-drawer"
            data-testid="outline-toggle"
          >
            <ListTree className="h-3.5 w-3.5 md:mr-1" />
            <span className="hidden md:inline">
              {t("creative.tab_outline", { defaultValue: "提纲" })}
              {draft.outline.length > 0 ? ` ${draft.outline.length}` : ""}
            </span>
          </Button>
          {writingChatNode ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setChatOpen((open) => !open)}
              aria-label={t("creative.chat.title", { defaultValue: "创作团队" })}
              aria-expanded={chatOpen}
              aria-controls="creative-partner-column"
              data-testid="partner-toggle"
            >
              <MessagesSquare className="h-3.5 w-3.5 md:mr-1" />
              <span className="hidden md:inline">
                {t("creative.chat.title", { defaultValue: "创作团队" })}
              </span>
            </Button>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            onClick={() => void onSaveNow()}
            aria-label={t("creative.save_now", { defaultValue: "保存" })}
            disabled={saveState.phase === "saving" || !dirty}
          >
            <Save className="h-3.5 w-3.5 md:mr-1" />
            <span className="hidden md:inline">
              {t("creative.save_now", { defaultValue: "保存" })}
            </span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleExport}
            aria-label={t("creative.export_md", { defaultValue: "导出 Markdown" })}
          >
            <Download className="h-3.5 w-3.5 md:mr-1" />
            <span className="hidden md:inline">
              {t("creative.export_md", { defaultValue: "导出 Markdown" })}
            </span>
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

      <div className="relative flex min-h-0 flex-1">
        {drawer ? (
          <div
            id="creative-side-drawer"
            data-testid="creative-side-drawer"
            className="absolute inset-y-0 left-0 z-20 flex w-[300px] flex-col border-r border-border bg-background shadow-xl"
          >
            <div className="flex h-9 shrink-0 items-center gap-1 border-b border-border px-3">
              <button
                type="button"
                className={cn(
                  "rounded-control px-2 py-0.5 text-[12px] cursor-pointer",
                  drawer === "materials"
                    ? "bg-primary/10 font-medium text-primary"
                    : "text-muted-foreground",
                )}
                onClick={() => setDrawer("materials")}
              >
                {t("creative.tab_materials", { defaultValue: "素材" })}
              </button>
              <button
                type="button"
                className={cn(
                  "rounded-control px-2 py-0.5 text-[12px] cursor-pointer",
                  drawer === "outline"
                    ? "bg-primary/10 font-medium text-primary"
                    : "text-muted-foreground",
                )}
                onClick={() => setDrawer("outline")}
              >
                {t("creative.tab_outline", { defaultValue: "提纲" })}
              </button>
              <span className="flex-1" />
              <button
                type="button"
                onClick={() => setDrawer(null)}
                aria-label={t("creative.drawer.close", { defaultValue: "关闭面板" })}
                className="flex h-6 w-6 items-center justify-center rounded-control text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" aria-hidden />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              {drawer === "materials" ? (
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
          </div>
        ) : null}

        {/* Center: editor/preview/split */}
        <div className="relative flex min-w-0 flex-1 flex-col bg-background">
          {remoteBanner}

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

          {showEditor && selection && onSelectionAction ? (
            <div
              data-testid="selection-toolbar"
              role="toolbar"
              aria-label={t("creative.selection.toolbar", { defaultValue: "选区操作" })}
              className="absolute bottom-4 left-1/2 z-10 flex -translate-x-1/2 items-center gap-0.5 rounded-full border border-border bg-popover px-1 py-1 shadow-lg"
            >
              {([
                { action: "ask", label: "问 AI" },
                { action: "rewrite", label: "改写" },
                { action: "expand", label: "扩写" },
                { action: "condense", label: "精简" },
              ] as const).map((item) => (
                <button
                  key={item.action}
                  type="button"
                  data-testid={`selection-action-${item.action}`}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => dispatchSelectionAction(item.action)}
                  className="rounded-full px-2.5 py-1 text-[11.5px] text-foreground hover:bg-accent"
                >
                  {item.label}
                </button>
              ))}
              <span className="mx-0.5 h-4 w-px bg-border" aria-hidden />
              <div className="relative">
                <button
                  type="button"
                  aria-expanded={moreActionsOpen}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => setMoreActionsOpen((open) => !open)}
                  className="rounded-full px-2 py-1 text-[11.5px] text-muted-foreground hover:bg-accent hover:text-foreground"
                >
                  {t("creative.selection.more", { defaultValue: "更多" })}
                </button>
                {moreActionsOpen ? (
                  <div className="absolute bottom-full right-0 mb-1.5 w-32 rounded-panel border border-border bg-popover p-1 shadow-xl">
                    {([
                      { action: "explain", label: "解释这段" },
                      { action: "verify", label: "核实说法" },
                      { action: "tone", label: "换语气" },
                    ] as const).map((item) => (
                      <button
                        key={item.action}
                        type="button"
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => dispatchSelectionAction(item.action)}
                        className="flex w-full rounded-control px-2 py-1.5 text-left text-[11.5px] hover:bg-accent"
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>

        {writingChatNode && chatOpen ? (
          <div
            id="creative-partner-column"
            data-testid="creative-partner-column"
            aria-label={t("creative.chat.title", { defaultValue: "创作团队" })}
            style={{ "--writing-chat-width": `${chatWidth}px` } as CSSProperties}
            className={cn(
              "relative flex min-h-0 shrink-0 flex-col border-l border-border bg-background",
              // 窄屏浮层：不挤压正文，开关仍然可用。
              "max-lg:absolute max-lg:inset-y-0 max-lg:right-0 max-lg:z-20 max-lg:w-[min(420px,100vw)]",
              "max-lg:shadow-xl lg:w-[var(--writing-chat-width)]",
            )}
          >
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label={t("creative.chat.resize", { defaultValue: "调整创作团队宽度" })}
              data-testid="writing-chat-resize"
              onPointerDown={(event: ReactPointerEvent<HTMLDivElement>) => {
                if (event.button !== 0) return;
                resizeRef.current = { startX: event.clientX, startWidth: chatWidth };
                event.currentTarget.setPointerCapture(event.pointerId);
              }}
              onPointerMove={(event: ReactPointerEvent<HTMLDivElement>) => {
                const state = resizeRef.current;
                if (!state) return;
                const next = clampChatWidth(
                  state.startWidth - (event.clientX - state.startX),
                );
                setChatWidth(next);
              }}
              onPointerUp={(event: ReactPointerEvent<HTMLDivElement>) => {
                const state = resizeRef.current;
                if (!state) return;
                resizeRef.current = null;
                const next = clampChatWidth(
                  state.startWidth - (event.clientX - state.startX),
                );
                setChatWidth(next);
                try {
                  window.localStorage.setItem(CHAT_WIDTH_KEY, String(next));
                } catch {
                  // 本地存储不可用只影响记忆宽度，不影响会话。
                }
                event.currentTarget.releasePointerCapture(event.pointerId);
              }}
              onPointerCancel={() => {
                resizeRef.current = null;
              }}
              className="absolute inset-y-0 -left-1 hidden w-2 cursor-col-resize lg:block"
            />
            <div className="min-h-0 flex-1 overflow-hidden">{writingChatNode}</div>
          </div>
        ) : null}
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
