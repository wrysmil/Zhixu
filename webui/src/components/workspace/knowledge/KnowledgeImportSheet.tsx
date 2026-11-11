import { useRef, useState } from "react";
import type { DragEvent } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, FileText, UploadCloud, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  KnowledgeDialog,
  KnowledgeDialogContent,
  KnowledgeDialogDescription,
  KnowledgeDialogTitle,
} from "../knowledge-shared/KnowledgeSurfaces";
import { KnowledgeStatus } from "../knowledge-shared/KnowledgeStatus";

const ACCEPTED_TYPES = ".pdf,.docx,.xlsx,.pptx,.md,.txt";

function formatFileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${bytes} B`;
}

export function KnowledgeImportSheet({ open, onClose, busy, error, stage, retryable, file, path, onPath, onUpload, onAddPath, onClearFile }: {
  open: boolean; onClose: () => void; busy: boolean; error: string | null; stage: string; retryable: boolean;
  file: File | null; path: string; onPath: (path: string) => void;
  onUpload: (file: File) => void; onAddPath: () => void; onClearFile: () => void;
}) {
  const { t } = useTranslation();
  const [method, setMethod] = useState<"local" | "path">("local");
  const [dragging, setDragging] = useState(false);
  const returnFocus = useRef<HTMLElement | null>(null);

  const acceptDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    setDragging(false);
    if (busy) return;
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) onUpload(dropped);
  };

  return <KnowledgeDialog open={open} onOpenChange={value => { if (!value && !busy) onClose(); }}>
    <KnowledgeDialogContent className="knowledge-dialog-compact gap-0 overflow-hidden p-0" showCloseButton={!busy}
      onOpenAutoFocus={() => { returnFocus.current = document.activeElement as HTMLElement; }}
      onCloseAutoFocus={event => { event.preventDefault();
        const target = returnFocus.current?.isConnected ? returnFocus.current : document.querySelector<HTMLElement>('[data-testid="knowledge-page"] button');
        target?.focus();
      }}>
      <header className="flex items-start gap-3 border-b border-border/60 px-6 pb-5 pr-12 pt-6">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
          <UploadCloud className="h-5 w-5" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <KnowledgeDialogTitle className="text-base">{t("knowledge.experience.addMaterial", { defaultValue: "添加资料" })}</KnowledgeDialogTitle>
          <KnowledgeDialogDescription className="mt-1 text-xs leading-relaxed">{t("knowledge.uploadHint")}</KnowledgeDialogDescription>
        </div>
      </header>

      <div className="px-6 py-5">
        <div role="tablist" className="mb-4 inline-flex rounded-control bg-muted/60 p-1">
          {(["local", "path"] as const).map(value => <button key={value} type="button" role="tab" aria-selected={method === value}
            disabled={busy} onClick={() => setMethod(value)}
            className={cn(
              "cursor-pointer rounded-[6px] px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60",
              method === value ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
            )}>
            {value === "local" ? t("knowledge.experience.localFile", { defaultValue: "本地文件" }) : t("knowledge.experience.serverPath", { defaultValue: "服务器路径" })}
          </button>)}
        </div>

        {method === "local" ? <div className="space-y-3">
          <label
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-panel border border-dashed px-4 py-7 text-center transition-colors",
              dragging ? "border-primary bg-primary/5" : "border-border hover:border-ring/60 hover:bg-muted/30",
              busy && "pointer-events-none opacity-60",
            )}
            onDragEnter={event => { event.preventDefault(); if (!busy) setDragging(true); }}
            onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true); }}
            onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }}
            onDrop={acceptDrop}
          >
            <input type="file" aria-label={t("knowledge.upload")} disabled={busy} accept={ACCEPTED_TYPES}
              className="sr-only"
              onChange={event => { const picked = event.target.files?.[0]; event.target.value = ""; if (picked) onUpload(picked); }} />
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-background shadow-sm ring-1 ring-border/70">
              <UploadCloud className="h-4 w-4 text-muted-foreground" aria-hidden />
            </span>
            <span className="text-sm font-medium text-foreground">
              {t("knowledge.experience.dropHint", { defaultValue: "拖拽文件到此处，或点击选择" })}
            </span>
          </label>

          {file ? <div className="flex items-center gap-3 rounded-panel border border-border/60 bg-muted/20 px-3 py-2.5">
            <FileText className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-foreground" title={file.name}>{file.name}</p>
              <p className="text-xs tabular-nums text-muted-foreground">{formatFileSize(file.size)}</p>
            </div>
            {error && retryable && <Button size="sm" variant="outline" disabled={busy} onClick={() => onUpload(file)}
              className="h-7 shrink-0 rounded-full px-2.5 text-xs">
              {t("knowledge.experience.retryUpload", { defaultValue: "重试上传" })}
            </Button>}
            <button type="button" aria-label={t("knowledge.experience.removeFile", { defaultValue: "移除文件" })}
              title={t("knowledge.experience.removeFile", { defaultValue: "移除文件" })} disabled={busy} onClick={onClearFile}
              className="shrink-0 cursor-pointer rounded-full p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50">
              <X className="h-3.5 w-3.5" aria-hidden />
            </button>
          </div> : null}

          {busy ? <KnowledgeStatus label={stage} tone="pending" busy /> : null}

          {error ? <p role="alert" className="flex items-start gap-2 rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            <span className="min-w-0 flex-1 break-words">{error}</span>
          </p> : null}

          {!file && !error ? <p className="text-xs leading-relaxed text-muted-foreground">
            {t("knowledge.experience.importStages", { defaultValue: "读取文件 → 提交 → 排队处理。处理结果将在文档列表中更新。" })}
          </p> : null}
        </div> : <form className="space-y-3" onSubmit={event => { event.preventDefault(); onAddPath(); }}>
          <label className="block space-y-2 text-sm font-medium text-foreground">
            {t("knowledge.addPath")}
            <Input aria-label={t("knowledge.addPath")} value={path} disabled={busy} onChange={event => onPath(event.target.value)} placeholder={t("knowledge.addPathPlaceholder")} />
          </label>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("knowledge.experience.importStages", { defaultValue: "读取文件 → 提交 → 排队处理。处理结果将在文档列表中更新。" })}
          </p>
          <Button type="submit" disabled={busy || !path.trim()} className="w-full">
            {t("knowledge.add")}
          </Button>
        </form>}
      </div>
    </KnowledgeDialogContent>
  </KnowledgeDialog>;
}
