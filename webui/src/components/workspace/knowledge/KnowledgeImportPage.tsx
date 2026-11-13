import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  File as FileIcon,
  FileSpreadsheet,
  FileText,
  FileType2,
  GitMerge,
  Loader2,
  Presentation,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { cn } from "@/lib/utils";
import {
  addKnowledgeDoc,
  addKnowledgeDocFromPath,
  previewKnowledgeChunks,
  type KnowledgeChunkPreview,
  type KnowledgeChunkPreviewItem,
  type KnowledgeKb,
  type WebUIMutationTransport,
} from "@/lib/api";

const ACCEPT = ".pdf,.docx,.xlsx,.pptx,.md,.txt";
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
const MIN_CHUNK_SIZE = 200;
const MAX_CHUNK_SIZE = 4000;
const MAX_SEPARATORS = 12;
const DEFAULT_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，"];

const UPLOAD_MIME_BY_SUFFIX: Record<string, string> = {
  ".pdf": "application/pdf",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".md": "text/markdown",
  ".txt": "text/plain",
};

function fileSuffix(name: string): string {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index).toLowerCase() : "";
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? "");
      const comma = text.indexOf(",");
      resolve(comma >= 0 ? text.slice(comma + 1) : "");
    };
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

function FileTypeIcon({ name }: { name: string }) {
  const suffix = fileSuffix(name);
  if (suffix === ".xlsx") return <FileSpreadsheet className="h-4 w-4" aria-hidden />;
  if (suffix === ".pptx") return <Presentation className="h-4 w-4" aria-hidden />;
  if (suffix === ".md" || suffix === ".txt") return <FileType2 className="h-4 w-4" aria-hidden />;
  if (suffix === ".pdf" || suffix === ".docx") return <FileText className="h-4 w-4" aria-hidden />;
  return <FileIcon className="h-4 w-4" aria-hidden />;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** index;
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

/** 分隔符展示：换行/制表符转成可见转义。 */
function displaySeparator(separator: string): string {
  return separator.replace(/\n/g, "\\n").replace(/\t/g, "\\t");
}

function parseSeparatorInput(value: string): string {
  return value.replace(/\\n/g, "\n").replace(/\\t/g, "\t");
}

function autoRows(content: string): number {
  return Math.min(10, Math.max(2, Math.ceil(content.length / 52)));
}

/**
 * 导入分块独立页：左原文、右可编辑分块。
 *
 * 分块参数只影响预览；确认导入时把当前分块原样提交（后端按自定义分块入库，
 * 重索引时复用），因此文档过大不可编辑时回退到知识库默认分块。
 */
export function KnowledgeImportPage({
  transport,
  kb,
  workspaceId,
  onBack,
  onImported,
}: {
  transport: WebUIMutationTransport;
  kb: KnowledgeKb;
  workspaceId?: string;
  onBack: () => void;
  onImported: () => Promise<void> | void;
}) {
  const { t } = useTranslation();
  const [method, setMethod] = useState<"local" | "path">("local");
  const [file, setFile] = useState<File | null>(null);
  const [dataUrl, setDataUrl] = useState("");
  const [path, setPath] = useState("");
  const [dragging, setDragging] = useState(false);
  const [mode, setMode] = useState<"smart" | "separator">("smart");
  const [separators, setSeparators] = useState<string[]>(DEFAULT_SEPARATORS);
  const [separatorDraft, setSeparatorDraft] = useState("");
  const [chunkSize, setChunkSize] = useState(String(kb.chunkSize ?? 800));
  const [chunkOverlap, setChunkOverlap] = useState(String(kb.chunkOverlap ?? 120));
  const [preview, setPreview] = useState<KnowledgeChunkPreview | null>(null);
  const [chunks, setChunks] = useState<KnowledgeChunkPreviewItem[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const seq = useRef(0);

  const size = Number(chunkSize);
  const overlap = Number(chunkOverlap);
  const settingsError = !Number.isInteger(size) || size < MIN_CHUNK_SIZE || size > MAX_CHUNK_SIZE
    ? t("knowledge.import.sizeInvalid", { defaultValue: "分块大小需在 200–4000 之间" })
    : !Number.isInteger(overlap) || overlap < 0 || overlap >= size
      ? t("knowledge.import.overlapInvalid", { defaultValue: "重叠需小于分块大小" })
      : null;

  const sourcePath = path.trim();
  const hasSource = method === "local" ? Boolean(file) : Boolean(sourcePath);

  const settingsDirty = useMemo(() => {
    if (!preview) return false;
    return (
      preview.chunkMode !== mode
      || preview.chunkSize !== size
      || preview.chunkOverlap !== overlap
      || JSON.stringify(preview.separators) !== JSON.stringify(separators)
    );
  }, [mode, overlap, preview, separators, size]);

  const runPreview = useCallback(
    async (source: { file: File; dataUrl: string } | { path: string }) => {
      if (settingsError) {
        setPreviewError(settingsError);
        return;
      }
      const current = ++seq.current;
      setPreviewing(true);
      setPreviewError(null);
      setSubmitError(null);
      try {
        const result = await previewKnowledgeChunks(transport, {
          kbId: kb.id,
          chunkMode: mode,
          separators: mode === "separator" ? separators : undefined,
          chunkSize: size,
          chunkOverlap: overlap,
          ...("file" in source
            ? { name: source.file.name, dataUrl: source.dataUrl }
            : { path: source.path }),
        });
        if (current !== seq.current) return;
        setPreview(result);
        setChunks(result.chunks.map((chunk) => ({ ...chunk })));
        setActiveIndex(0);
        setDirty(false);
      } catch (reason: unknown) {
        if (current !== seq.current) return;
        setPreview(null);
        setChunks([]);
        setPreviewError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (current === seq.current) setPreviewing(false);
      }
    },
    [kb.id, mode, overlap, separators, settingsError, size, transport],
  );

  const selectFile = useCallback(
    async (next: File | null) => {
      setPreviewError(null);
      setSubmitError(null);
      setPreview(null);
      setChunks([]);
      setDirty(false);
      setFile(next);
      setDataUrl("");
      if (!next) {
        if (inputRef.current) inputRef.current.value = "";
        return;
      }
      const mime = UPLOAD_MIME_BY_SUFFIX[fileSuffix(next.name)];
      if (!mime) {
        setFile(null);
        setPreviewError(t("knowledge.uploadBadType"));
        return;
      }
      if (next.size > MAX_UPLOAD_BYTES) {
        setFile(null);
        setPreviewError(t("knowledge.uploadTooLarge"));
        return;
      }
      try {
        const base64 = await readFileAsBase64(next);
        const nextDataUrl = `data:${mime};base64,${base64}`;
        setDataUrl(nextDataUrl);
        await runPreview({ file: next, dataUrl: nextDataUrl });
      } catch {
        setPreviewError(
          t("knowledge.experience.readFailed", {
            defaultValue: "无法读取文件，请重新选择后重试",
          }),
        );
      }
    },
    [runPreview, t],
  );

  const applySettings = () => {
    if (method === "local" && file && dataUrl) {
      void runPreview({ file, dataUrl });
    } else if (method === "path" && sourcePath) {
      void runPreview({ path: sourcePath });
    }
  };

  const updateChunk = (index: number, content: string) => {
    setChunks((prev) =>
      prev.map((chunk, position) =>
        position === index ? { ...chunk, content, chars: content.length } : chunk,
      ),
    );
    setDirty(true);
  };

  const removeChunk = (index: number) => {
    setChunks((prev) => prev.filter((_, position) => position !== index));
    setActiveIndex((current) => Math.max(0, current >= index ? current - 1 : current));
    setDirty(true);
  };

  const mergeNext = (index: number) => {
    setChunks((prev) => {
      if (index >= prev.length - 1) return prev;
      const current = prev[index];
      const next = prev[index + 1];
      const content = `${current.content.trimEnd()}\n\n${next.content.trimStart()}`;
      const merged = {
        ...current,
        content,
        chars: content.length,
        end: next.end,
      };
      return [...prev.slice(0, index), merged, ...prev.slice(index + 2)];
    });
    setDirty(true);
  };

  const addSeparator = () => {
    const parsed = parseSeparatorInput(separatorDraft);
    if (!parsed || separators.includes(parsed) || separators.length >= MAX_SEPARATORS) return;
    setSeparators((prev) => [...prev, parsed]);
    setSeparatorDraft("");
  };

  const confirm = async () => {
    if (!preview || previewing || submitting || !hasSource) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const customChunks = preview.editable
        ? chunks
            .filter((chunk) => chunk.content.trim())
            .map((chunk) => ({
              content: chunk.content.trim(),
              heading: chunk.heading,
              page: chunk.page,
              locator: chunk.locator,
            }))
        : undefined;
      if (method === "local" && file) {
        await addKnowledgeDoc(transport, {
          kbId: kb.id,
          name: file.name,
          dataUrl,
          workspaceId,
          chunkSize: size,
          chunkOverlap: overlap,
          ...(customChunks ? { chunks: customChunks } : {}),
        });
      } else {
        await addKnowledgeDocFromPath(transport, {
          kbId: kb.id,
          path: sourcePath,
          workspaceId,
          chunkSize: size,
          chunkOverlap: overlap,
          ...(customChunks ? { chunks: customChunks } : {}),
        });
      }
      setSubmitted(true);
      await onImported();
    } catch (reason: unknown) {
      setSubmitError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    return () => {
      seq.current += 1;
    };
  }, []);

  const activeChunk = chunks[activeIndex] ?? null;
  const sourceText = preview?.source.text ?? "";
  const canHighlight = Boolean(
    activeChunk
    && preview
    && !preview.source.truncated
    && activeChunk.end <= sourceText.length,
  );

  useEffect(() => {
    if (!canHighlight) return;
    document
      .getElementById("knowledge-import-highlight")
      ?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, canHighlight]);

  const confirmDisabled = !preview || previewing || submitting || Boolean(previewError)
    || Boolean(settingsError) || (preview.editable && chunks.length === 0);

  return (
    <div className="flex min-w-0 flex-col gap-3" data-testid="knowledge-import-page">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onBack}
          aria-label={t("knowledge.import.back", { defaultValue: "返回文档" })}
          className="inline-flex w-fit items-center gap-1 rounded-control text-[12px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          {t("knowledge.import.back", { defaultValue: "返回文档" })}
        </button>
        <h1 className="text-base font-semibold text-foreground">
          {t("knowledge.experience.addMaterial", { defaultValue: "添加资料" })}
        </h1>
        <span className="text-[12px] text-muted-foreground">{kb.name}</span>
        <div className="ml-auto flex items-center gap-2">
          {submitted ? (
            <Button size="sm" onClick={onBack} className="h-8">
              {t("knowledge.import.backToDocs", { defaultValue: "返回文档列表" })}
            </Button>
          ) : (
            <Button size="sm" onClick={() => void confirm()} disabled={confirmDisabled} className="h-8 gap-1.5">
              {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
              {t("knowledge.import.confirm", { defaultValue: "确认添加" })}
            </Button>
          )}
        </div>
      </div>

      {submitted ? (
        <section className="rounded-panel border border-border/70 bg-card p-6" data-testid="knowledge-import-submitted">
          <p className="text-sm font-medium text-foreground">
            {t("knowledge.import.submittedTitle", { defaultValue: "已提交" })}
          </p>
          <p className="mt-2 max-w-xl text-[12.5px] leading-relaxed text-muted-foreground">
            {t("knowledge.import.submittedBody", {
              name: file?.name || sourcePath.split(/[\\/]/).pop() || "",
              defaultValue: "「{{name}}」已进入解析队列；完成后可在文档列表查看分块。",
            })}
          </p>
          <p className="mt-1 text-[11.5px] text-muted-foreground">
            {t("knowledge.import.submittedHint", {
              defaultValue: "解析完成后，文档列表会更新分块与索引状态。",
            })}
          </p>
        </section>
      ) : (
        <div className="grid min-w-0 items-start gap-3 lg:grid-cols-2">
          <section className="flex min-w-0 flex-col rounded-panel border border-border/70 bg-card">
            <header className="flex items-center gap-2 border-b border-border/60 px-4 py-2.5">
              <h2 className="text-[13px] font-semibold text-foreground">
                {t("knowledge.import.sourceTitle", { defaultValue: "原文" })}
              </h2>
              {preview ? (
                <span className="text-[11px] text-muted-foreground">
                  {t("knowledge.import.sourceChars", {
                    chars: preview.source.chars,
                    defaultValue: "{{chars}} 字符",
                  })}
                </span>
              ) : null}
              {preview?.source.truncated ? (
                <span className="text-[11px] text-amber-600 dark:text-amber-300">
                  {t("knowledge.import.sourceTruncated", { defaultValue: "原文过长，仅展示前段" })}
                </span>
              ) : null}
              <span className="ml-auto text-[11px] text-muted-foreground">
                {t("knowledge.import.highlightHint", { defaultValue: "点击分块定位原文" })}
              </span>
            </header>
            <div className="max-h-[64vh] min-h-[16rem] overflow-y-auto px-4 py-3">
              {preview ? (
                <pre className="whitespace-pre-wrap break-words font-sans text-[12.5px] leading-relaxed text-foreground">
                  {canHighlight && activeChunk ? (
                    <>
                      {sourceText.slice(0, activeChunk.start)}
                      <mark id="knowledge-import-highlight" className="rounded bg-amber-200/70 px-0.5 text-foreground dark:bg-amber-500/30">
                        {sourceText.slice(activeChunk.start, activeChunk.end)}
                      </mark>
                      {sourceText.slice(activeChunk.end)}
                    </>
                  ) : (
                    sourceText
                  )}
                </pre>
              ) : (
                <p className="text-[12.5px] text-muted-foreground">
                  {t("knowledge.import.chooseFile", { defaultValue: "选择文件后可预览分块。" })}
                </p>
              )}
            </div>
          </section>

          <section className="flex min-w-0 flex-col rounded-panel border border-border/70 bg-card">
            <div className="flex flex-col gap-3 border-b border-border/60 px-4 py-3">
              <SegmentedControl
                mode="tabs"
                ariaLabel={t("knowledge.experience.source", { defaultValue: "资料来源" })}
                value={method}
                onChange={(value) => setMethod(value)}
                options={[
                  { value: "local", label: t("knowledge.experience.localFile", { defaultValue: "本地文件" }) },
                  { value: "path", label: t("knowledge.experience.serverPath", { defaultValue: "服务器路径" }) },
                ]}
              />

              {method === "local" ? (
                file ? (
                  <div className="flex items-center gap-3 rounded-control border border-border/70 bg-background px-3 py-2">
                    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-primary/10 text-primary">
                      <FileTypeIcon name={file.name} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-foreground">{file.name}</span>
                      <span className="text-[11px] text-muted-foreground">{formatBytes(file.size)}</span>
                    </span>
                    <Button
                      size="icon"
                      variant={null}
                      aria-label={t("knowledge.experience.removeFile", { defaultValue: "移除文件" })}
                      className="h-7 w-7 rounded-control text-muted-foreground hover:bg-muted hover:text-foreground"
                      onClick={() => void selectFile(null)}
                    >
                      <X className="h-3.5 w-3.5" aria-hidden />
                    </Button>
                  </div>
                ) : (
                  <div
                    data-testid="knowledge-dropzone"
                    onDragOver={(event) => {
                      event.preventDefault();
                      setDragging(true);
                    }}
                    onDragLeave={() => setDragging(false)}
                    onDrop={(event) => {
                      event.preventDefault();
                      setDragging(false);
                      void selectFile(event.dataTransfer?.files?.[0] ?? null);
                    }}
                    onClick={(event) => {
                      if ((event.target as HTMLElement).tagName === "INPUT") return;
                      inputRef.current?.click();
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        inputRef.current?.click();
                      }
                    }}
                    role="button"
                    tabIndex={0}
                    aria-label={t("knowledge.experience.dropHint", { defaultValue: "拖拽文件到此处，或点击选择文件" })}
                    className={cn(
                      "flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-panel border border-dashed px-6 py-6 text-center transition-colors",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      dragging ? "border-primary bg-primary/[0.06]" : "border-border bg-background hover:border-primary/60 hover:bg-muted/40",
                    )}
                  >
                    <UploadCloud className="h-5 w-5 text-muted-foreground" aria-hidden />
                    <span className="text-[13px] font-medium text-foreground">
                      {t("knowledge.experience.dropTitle", { defaultValue: "拖拽文件到此处" })}
                    </span>
                    <span className="text-[11px] text-muted-foreground">{t("knowledge.uploadHint")}</span>
                    <input
                      ref={inputRef}
                      type="file"
                      aria-label={t("knowledge.upload")}
                      accept={ACCEPT}
                      className="sr-only"
                      onClick={(event) => event.stopPropagation()}
                      onChange={(event) => {
                        const picked = event.target.files?.[0] ?? null;
                        event.target.value = "";
                        void selectFile(picked);
                      }}
                    />
                  </div>
                )
              ) : (
                <div className="flex items-end gap-2">
                  <label className="flex min-w-0 flex-1 flex-col gap-1 text-[12px] text-muted-foreground">
                    {t("knowledge.addPath")}
                    <Input
                      aria-label={t("knowledge.addPath")}
                      value={path}
                      onChange={(event) => setPath(event.target.value)}
                      placeholder={t("knowledge.addPathPlaceholder")}
                      className="h-9 text-sm"
                    />
                  </label>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-9 shrink-0"
                    disabled={!sourcePath || previewing}
                    onClick={() => void runPreview({ path: sourcePath })}
                  >
                    {t("knowledge.import.loadPreview", { defaultValue: "预览" })}
                  </Button>
                </div>
              )}

              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <SegmentedControl
                  mode="tabs"
                  ariaLabel={t("knowledge.import.mode", { defaultValue: "分块模式" })}
                  value={mode}
                  onChange={setMode}
                  options={[
                    { value: "smart", label: t("knowledge.import.modeSmart", { defaultValue: "智能分段" }) },
                    { value: "separator", label: t("knowledge.import.modeSeparator", { defaultValue: "自定义分隔符" }) },
                  ]}
                />
                <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
                  {t("knowledge.import.chunkSize", { defaultValue: "分块大小" })}
                  <Input
                    type="number"
                    aria-label={t("knowledge.import.chunkSize", { defaultValue: "分块大小" })}
                    min={MIN_CHUNK_SIZE}
                    max={MAX_CHUNK_SIZE}
                    step={50}
                    value={chunkSize}
                    onChange={(event) => setChunkSize(event.target.value)}
                    className="h-8 w-24 text-sm tabular-nums"
                  />
                </label>
                <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
                  {t("knowledge.import.chunkOverlap", { defaultValue: "重叠" })}
                  <Input
                    type="number"
                    aria-label={t("knowledge.import.chunkOverlap", { defaultValue: "重叠" })}
                    min={0}
                    max={MAX_CHUNK_SIZE}
                    step={10}
                    value={chunkOverlap}
                    onChange={(event) => setChunkOverlap(event.target.value)}
                    className="h-8 w-20 text-sm tabular-nums"
                  />
                </label>
                <Button
                  size="sm"
                  variant={settingsDirty || dirty ? "default" : "outline"}
                  className="h-8 self-end"
                  disabled={!hasSource || previewing || Boolean(settingsError)}
                  onClick={applySettings}
                >
                  {dirty
                    ? t("knowledge.import.reChunkDirty", { defaultValue: "重新分块（丢弃修改）" })
                    : t("knowledge.import.applySettings", { defaultValue: "应用分块设置" })}
                </Button>
              </div>

              {mode === "separator" ? (
                <div className="flex flex-col gap-1.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-[11px] text-muted-foreground">
                      {t("knowledge.import.separators", { defaultValue: "分隔符" })}
                    </span>
                    {separators.map((separator, index) => (
                      <span
                        key={`${separator}-${index}`}
                        className="inline-flex items-center gap-1 rounded-control border border-border/70 bg-background px-1.5 py-0.5 font-mono text-[11px] text-foreground"
                      >
                        {displaySeparator(separator)}
                        <button
                          type="button"
                          aria-label={t("knowledge.import.removeSeparator", { defaultValue: "移除分隔符" })}
                          onClick={() => setSeparators((prev) => prev.filter((_, position) => position !== index))}
                          className="text-muted-foreground hover:text-foreground"
                        >
                          <X className="h-3 w-3" aria-hidden />
                        </button>
                      </span>
                    ))}
                    {separators.length < MAX_SEPARATORS ? (
                      <span className="inline-flex items-center gap-1">
                        <Input
                          aria-label={t("knowledge.import.addSeparator", { defaultValue: "添加分隔符" })}
                          value={separatorDraft}
                          placeholder="\\n\\n"
                          onChange={(event) => setSeparatorDraft(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              event.preventDefault();
                              addSeparator();
                            }
                          }}
                          className="h-7 w-24 px-2 font-mono text-[11px]"
                        />
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 px-2 text-[11px]"
                          disabled={!separatorDraft.trim()}
                          onClick={addSeparator}
                        >
                          {t("knowledge.import.addSeparatorButton", { defaultValue: "添加" })}
                        </Button>
                      </span>
                    ) : null}
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    {t("knowledge.import.separatorHint", {
                      defaultValue: "按列表顺序优先尝试；支持 \\n、\\t 写法。",
                    })}
                  </p>
                </div>
              ) : null}

              {settingsError ? (
                <p className="text-[11.5px] text-destructive">{settingsError}</p>
              ) : settingsDirty ? (
                <p className="text-[11.5px] text-amber-700 dark:text-amber-300">
                  {t("knowledge.import.settingsPending", { defaultValue: "分块设置已修改，点击「应用分块设置」重新预览。" })}
                </p>
              ) : null}
            </div>

            <div className="flex items-center gap-2 border-b border-border/60 px-4 py-2">
              <h2 className="text-[13px] font-semibold text-foreground">
                {t("knowledge.import.chunksTitle", { defaultValue: "分块" })}
              </h2>
              {preview ? (
                <span className="text-[11px] tabular-nums text-muted-foreground">
                  {t("knowledge.import.summary", {
                    total: preview.total,
                    chars: preview.avgChars,
                    defaultValue: "共 {{total}} 块 · 平均 {{chars}} 字",
                  })}
                </span>
              ) : null}
              {dirty ? (
                <span className="text-[11px] text-foreground">
                  {t("knowledge.import.dirtyCount", { defaultValue: "已修改" })}
                </span>
              ) : null}
              {preview && !preview.editable ? (
                <span className="ml-auto text-[11px] text-amber-700 dark:text-amber-300">
                  {t("knowledge.import.notEditable", { defaultValue: "文档过大，仅展示前部分分块，导入时按自动分块处理。" })}
                </span>
              ) : null}
            </div>

            <div className="max-h-[52vh] min-h-[16rem] overflow-y-auto px-2 py-2">
              {previewing ? (
                <p className="flex items-center gap-2 px-2 py-3 text-[12px] text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  {t("knowledge.import.previewing", { defaultValue: "正在解析分块" })}
                </p>
              ) : previewError ? (
                <p role="alert" className="mx-2 rounded-control border border-destructive/30 bg-destructive/[0.06] px-3 py-2 text-[12.5px] text-destructive">
                  {previewError}
                </p>
              ) : preview && chunks.length > 0 ? (
                <ol className="flex flex-col gap-1">
                  {chunks.map((chunk, index) => (
                    <li
                      key={`${chunk.index}-${index}`}
                      onClick={() => setActiveIndex(index)}
                      className={cn(
                        "rounded-control border px-3 py-2 transition-colors",
                        index === activeIndex
                          ? "border-primary/50 bg-primary/[0.04]"
                          : "border-transparent hover:bg-muted/30",
                      )}
                    >
                      <div className="flex flex-wrap items-center gap-x-2 text-[11px] text-muted-foreground">
                        <span className="tabular-nums text-foreground">{index + 1}</span>
                        <span className="tabular-nums">
                          {t("knowledge.import.chunkChars", {
                            chars: chunk.content.length,
                            defaultValue: "{{chars}} 字",
                          })}
                        </span>
                        {chunk.heading ? <span className="truncate">{chunk.heading}</span> : null}
                        {typeof chunk.page === "number" ? (
                          <span>{t("knowledge.pageLabel", { page: chunk.page })}</span>
                        ) : null}
                        {preview.editable ? (
                          <span className="ml-auto flex items-center gap-1">
                            <button
                              type="button"
                              disabled={index >= chunks.length - 1}
                              aria-label={t("knowledge.import.mergeNext", { defaultValue: "合并下一块" })}
                              title={t("knowledge.import.mergeNext", { defaultValue: "合并下一块" })}
                              onClick={(event) => {
                                event.stopPropagation();
                                mergeNext(index);
                              }}
                              className="rounded-control p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
                            >
                              <GitMerge className="h-3.5 w-3.5" aria-hidden />
                            </button>
                            <button
                              type="button"
                              aria-label={t("knowledge.import.deleteChunk", { defaultValue: "删除分块" })}
                              title={t("knowledge.import.deleteChunk", { defaultValue: "删除分块" })}
                              onClick={(event) => {
                                event.stopPropagation();
                                removeChunk(index);
                              }}
                              className="rounded-control p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                            >
                              <Trash2 className="h-3.5 w-3.5" aria-hidden />
                            </button>
                          </span>
                        ) : null}
                      </div>
                      {preview.editable ? (
                        <textarea
                          aria-label={t("knowledge.import.chunkContent", { defaultValue: "分块内容" })}
                          value={chunk.content}
                          rows={autoRows(chunk.content)}
                          onClick={(event) => event.stopPropagation()}
                          onFocus={() => setActiveIndex(index)}
                          onChange={(event) => updateChunk(index, event.target.value)}
                          className="mt-1 w-full resize-y rounded-control border border-transparent bg-transparent px-0 py-0 text-[12.5px] leading-relaxed text-foreground outline-none focus:border-input focus:bg-background focus:px-2 focus:py-1"
                        />
                      ) : (
                        <p className="mt-1 whitespace-pre-wrap break-words text-[12.5px] leading-relaxed text-foreground">
                          {chunk.content}
                        </p>
                      )}
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="px-2 py-3 text-[12px] text-muted-foreground">
                  {t("knowledge.import.chooseFile", { defaultValue: "选择文件后可预览分块。" })}
                </p>
              )}
            </div>
          </section>
        </div>
      )}

      {submitError ? (
        <p role="alert" className="rounded-control border border-destructive/30 bg-destructive/[0.06] px-3 py-2 text-sm text-destructive">
          {submitError}
        </p>
      ) : null}
    </div>
  );
}
