import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { KnowledgeSheet, KnowledgeSheetContent, KnowledgeSheetTitle, KnowledgeSheetDescription } from "../knowledge-shared/KnowledgeSurfaces";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { KnowledgeStatus } from "../knowledge-shared/KnowledgeStatus";

export function KnowledgeImportSheet({ open, onClose, busy, error, stage, file, path, onPath, onUpload, onAddPath }: {
  open: boolean; onClose: () => void; busy: boolean; error: string | null; stage: string;
  file: File | null; path: string; onPath: (path: string) => void;
  onUpload: (file: File) => void; onAddPath: () => void;
}) {
  const { t } = useTranslation();
  const [method, setMethod] = useState<"local" | "path">("local");
  const returnFocus = useRef<HTMLElement | null>(null);
  return <KnowledgeSheet open={open} onOpenChange={value => { if (!value && !busy) onClose(); }}>
    <KnowledgeSheetContent className="p-6 sm:p-8"
      onOpenAutoFocus={() => { returnFocus.current = document.activeElement as HTMLElement; }}
      onCloseAutoFocus={event => { event.preventDefault();
        const target = returnFocus.current?.isConnected ? returnFocus.current : document.querySelector<HTMLElement>('[data-testid="knowledge-page"] button');
        target?.focus();
      }}>
      <KnowledgeSheetTitle>{t("knowledge.experience.addMaterial", { defaultValue: "添加资料" })}</KnowledgeSheetTitle>
      <KnowledgeSheetDescription>{t("knowledge.uploadHint")}</KnowledgeSheetDescription>
      <div role="tablist" className="flex gap-2 border-b pb-3">
        {(["local", "path"] as const).map(value => <Button key={value} role="tab" aria-selected={method === value}
          variant={method === value ? "secondary" : "ghost"} disabled={busy} onClick={() => setMethod(value)}>
          {value === "local" ? t("knowledge.experience.localFile", { defaultValue: "本地文件" }) : t("knowledge.experience.serverPath", { defaultValue: "服务器路径" })}
        </Button>)}
      </div>
      {method === "local" ? <div className="space-y-4 rounded-lg border border-dashed p-5">
        <label className="block space-y-3 text-sm font-medium">{t("knowledge.upload")}
          <input type="file" aria-label={t("knowledge.upload")} disabled={busy} accept=".pdf,.docx,.xlsx,.pptx,.md,.txt"
            className="block w-full text-sm file:mr-3 file:rounded file:border file:bg-muted file:px-3 file:py-2"
            onChange={event => { const picked = event.target.files?.[0]; event.target.value = ""; if (picked) onUpload(picked); }} />
        </label>
        {file && <p className="break-words text-sm">{file.name}</p>}
        {file && error && <Button disabled={busy} onClick={() => onUpload(file)}>{t("knowledge.experience.retryUpload", { defaultValue: "重试上传" })}</Button>}
      </div> : <form className="space-y-4" onSubmit={event => { event.preventDefault(); onAddPath(); }}>
        <label className="block space-y-2 text-sm">{t("knowledge.addPath")}
          <Input aria-label={t("knowledge.addPath")} value={path} disabled={busy} onChange={event => onPath(event.target.value)} placeholder={t("knowledge.addPathPlaceholder")} />
        </label>
        <Button type="submit" disabled={busy || !path.trim()}>{t("knowledge.add")}</Button>
      </form>}
      {busy && <KnowledgeStatus label={stage} tone="pending" busy />}
      {error && <p role="alert" className="break-words rounded border border-destructive/30 p-3 text-sm text-destructive">{error}</p>}
      <p className="text-sm leading-6 text-muted-foreground">{t("knowledge.experience.importStages", { defaultValue: "读取文件 → 提交 → 排队处理。处理结果将在文档列表中更新。" })}</p>
      <Button variant="outline" className="mt-auto" disabled={busy} onClick={onClose}>{t("knowledge.close")}</Button>
    </KnowledgeSheetContent>
  </KnowledgeSheet>;
}
