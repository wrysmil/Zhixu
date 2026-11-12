import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  File as FileIcon,
  FileSpreadsheet,
  FileText,
  FileType2,
  Presentation,
  UploadCloud,
  X,
} from "lucide-react";

import { KnowledgeSheet, KnowledgeSheetContent, KnowledgeSheetTitle, KnowledgeSheetDescription } from "../knowledge-shared/KnowledgeSurfaces";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { Stepper } from "@/components/ui/progress";
import { KnowledgeStatus } from "../knowledge-shared/KnowledgeStatus";
import { cn } from "@/lib/utils";

const ACCEPT = ".pdf,.docx,.xlsx,.pptx,.md,.txt";

const FORMAT_CHIPS: Array<{ label: string; hint: string }> = [
  { label: "PDF", hint: "pdf" },
  { label: "DOCX", hint: "docx" },
  { label: "XLSX", hint: "xlsx" },
  { label: "PPTX", hint: "pptx" },
  { label: "MD", hint: "md" },
  { label: "TXT", hint: "txt" },
];

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** index;
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

function FileTypeIcon({ name }: { name: string }) {
  const suffix = name.split(".").pop()?.toLowerCase() ?? "";
  if (suffix === "xlsx") return <FileSpreadsheet className="h-4 w-4" aria-hidden />;
  if (suffix === "pptx") return <Presentation className="h-4 w-4" aria-hidden />;
  if (suffix === "md" || suffix === "txt") return <FileType2 className="h-4 w-4" aria-hidden />;
  if (suffix === "pdf" || suffix === "docx") return <FileText className="h-4 w-4" aria-hidden />;
  return <FileIcon className="h-4 w-4" aria-hidden />;
}

/**
 * Import sheet (rebuilt): drag-and-drop intake, an explicit format contract,
 * a per-file card and a visible stage pipeline. The previous version handed
 * the user a raw file input with the browser's default button chrome.
 */
export function KnowledgeImportSheet({
  open,
  onClose,
  busy,
  error,
  stage,
  stageIndex = -1,
  file,
  path,
  onPath,
  onUpload,
  onAddPath,
}: {
  open: boolean;
  onClose: () => void;
  busy: boolean;
  error: string | null;
  stage: string;
  /** -1 = idle, 0 = reading, 1 = submitting, 2 = queued. */
  stageIndex?: number;
  file: File | null;
  path: string;
  onPath: (path: string) => void;
  onUpload: (file: File) => void;
  onAddPath: () => void;
}) {
  const { t } = useTranslation();
  const [method, setMethod] = useState<"local" | "path">("local");
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  const acceptFile = useCallback(
    (candidate: File | undefined | null) => {
      if (!candidate || busy) return;
      onUpload(candidate);
    },
    [busy, onUpload],
  );

  return (
    <KnowledgeSheet open={open} onOpenChange={(value) => { if (!value && !busy) onClose(); }}>
      <KnowledgeSheetContent
        className="gap-0 overflow-y-auto p-0 sm:p-0"
        onOpenAutoFocus={() => { returnFocus.current = document.activeElement as HTMLElement; }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          const target = returnFocus.current?.isConnected
            ? returnFocus.current
            : document.querySelector<HTMLElement>('[data-testid="knowledge-page"] button');
          target?.focus();
        }}
      >
        <div className="border-b border-border/70 bg-card px-6 py-5">
          <div className="mb-1 flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.16em] text-primary/80">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-primary motion-pulse" aria-hidden />
            {t("knowledge.experience.eyebrow", { defaultValue: "Knowledge intake" })}
          </div>
          <KnowledgeSheetTitle>{t("knowledge.experience.addMaterial", { defaultValue: "添加资料" })}</KnowledgeSheetTitle>
          <KnowledgeSheetDescription>
            {t("knowledge.uploadHint")}
          </KnowledgeSheetDescription>
        </div>

        <div className="px-6 py-5">
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
            <div className="mt-4 flex flex-col gap-4">
              <div
                data-testid="knowledge-dropzone"
                onDragOver={(event) => {
                  event.preventDefault();
                  if (!busy) setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragging(false);
                  acceptFile(event.dataTransfer?.files?.[0]);
                }}
                onClick={(event) => {
                  // The hidden input lives inside the dropzone: without this guard
                  // its own click bubbles back and re-enters this handler.
                  if ((event.target as HTMLElement).tagName === "INPUT") return;
                  if (!busy) inputRef.current?.click();
                }}
                onKeyDown={(event) => {
                  if (busy) return;
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    inputRef.current?.click();
                  }
                }}
                role="button"
                tabIndex={0}
                aria-label={t("knowledge.experience.dropHint", { defaultValue: "拖拽文件到此处，或点击选择文件" })}
                aria-disabled={busy}
                className={cn(
                  "group flex cursor-pointer flex-col items-center justify-center gap-2 rounded-panel border border-dashed px-6 py-8 text-center transition-colors duration-150",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  dragging
                    ? "border-primary bg-primary/[0.06] shadow-[0_0_0_3px_hsl(var(--primary)/0.12)]"
                    : "border-border bg-background hover:border-primary/60 hover:bg-muted/40",
                  busy && "pointer-events-none opacity-60",
                )}
              >
                <span
                  className={cn(
                    "grid h-11 w-11 place-items-center rounded-full border transition-colors duration-150",
                    dragging
                      ? "border-primary/40 bg-primary/10 text-primary"
                      : "border-border bg-card text-muted-foreground group-hover:text-primary",
                  )}
                >
                  <UploadCloud className="h-5 w-5" aria-hidden />
                </span>
                <span className="text-sm font-medium text-foreground">
                  {t("knowledge.experience.dropTitle", { defaultValue: "拖拽文件到此处" })}
                </span>
                <span className="text-xs text-muted-foreground">
                  {t("knowledge.experience.dropHint", { defaultValue: "或点击选择文件 · 单个文件最大 6 MB" })}
                </span>
                <input
                  ref={inputRef}
                  type="file"
                  aria-label={t("knowledge.upload")}
                  disabled={busy}
                  accept={ACCEPT}
                  className="sr-only"
                  onClick={(event) => event.stopPropagation()}
                  onChange={(event) => {
                    const picked = event.target.files?.[0];
                    event.target.value = "";
                    acceptFile(picked);
                  }}
                />
              </div>

              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-[11px] text-muted-foreground">
                  {t("knowledge.experience.supported", { defaultValue: "支持格式" })}
                </span>
                {FORMAT_CHIPS.map((chip) => (
                  <span
                    key={chip.hint}
                    className="rounded-full border border-border/70 bg-muted/50 px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
                  >
                    {chip.label}
                  </span>
                ))}
              </div>

              {file ? (
                <div className="motion-fade-in-up flex items-center gap-3 rounded-control border border-border/70 bg-card px-3 py-2.5 shadow-sm">
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-control bg-primary/10 text-primary">
                    <FileTypeIcon name={file.name} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-foreground">{file.name}</span>
                    <span className="block text-[11px] text-muted-foreground">{formatBytes(file.size)}</span>
                  </span>
                  {busy ? (
                    <span className="motion-pulse text-[11px] text-primary">
                      {stage || t("knowledge.experience.working", { defaultValue: "处理中" })}
                    </span>
                  ) : error ? (
                    <Button size="sm" variant="secondary" onClick={() => onUpload(file)}>
                      {t("knowledge.experience.retryUpload", { defaultValue: "重试上传" })}
                    </Button>
                  ) : (
                    <Button
                      size="icon"
                      variant={null}
                      aria-label={t("knowledge.experience.removeFile", { defaultValue: "移除文件" })}
                      className="h-7 w-7 rounded-control text-muted-foreground hover:bg-muted hover:text-foreground"
                      onClick={() => {
                        if (inputRef.current) inputRef.current.value = "";
                      }}
                    >
                      <X className="h-3.5 w-3.5" aria-hidden />
                    </Button>
                  )}
                </div>
              ) : null}

              {busy || stageIndex >= 0 ? (
                <Stepper
                  className="rounded-control border border-border/70 bg-muted/30 px-3 py-3"
                  activeIndex={stageIndex}
                  steps={[
                    { label: t("knowledge.experience.stageRead", { defaultValue: "读取文件" }) },
                    { label: t("knowledge.experience.stageSubmit", { defaultValue: "提交资料" }) },
                    { label: t("knowledge.experience.stageQueue", { defaultValue: "排队解析" }) },
                  ]}
                />
              ) : null}
            </div>
          ) : (
            <form
              className="mt-4 flex flex-col gap-4"
              onSubmit={(event) => { event.preventDefault(); onAddPath(); }}
            >
              <label className="block space-y-2 text-sm">
                <span className="font-medium text-foreground">{t("knowledge.addPath")}</span>
                <Input
                  aria-label={t("knowledge.addPath")}
                  value={path}
                  disabled={busy}
                  onChange={(event) => onPath(event.target.value)}
                  placeholder={t("knowledge.addPathPlaceholder")}
                />
              </label>
              <p className="text-xs leading-5 text-muted-foreground">
                {t("knowledge.experience.pathHint", { defaultValue: "路径指向网关所在机器上的可读文件，读取权限与网关进程一致。" })}
              </p>
              <Button type="submit" disabled={busy || !path.trim()}>
                {t("knowledge.add")}
              </Button>
            </form>
          )}

          {busy && !file ? <KnowledgeStatus label={stage} tone="pending" busy /> : null}
          {error ? (
            <p
              role="alert"
              className="mt-4 break-words rounded-control border border-destructive/30 bg-destructive/[0.06] p-3 text-sm text-destructive"
            >
              {error}
            </p>
          ) : null}
        </div>

        <div className="mt-auto flex items-center justify-between gap-3 border-t border-border/70 bg-muted/30 px-6 py-4">
          <p className="text-[11px] leading-5 text-muted-foreground">
            {t("knowledge.experience.importStages", { defaultValue: "读取文件 → 提交 → 排队处理。处理结果将在文档列表中更新。" })}
          </p>
          <Button variant="outline" disabled={busy} onClick={onClose}>
            {t("knowledge.close")}
          </Button>
        </div>
      </KnowledgeSheetContent>
    </KnowledgeSheet>
  );
}
