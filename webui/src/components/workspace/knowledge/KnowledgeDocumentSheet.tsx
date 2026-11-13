import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { KnowledgeSheet, KnowledgeSheetContent, KnowledgeSheetTitle, KnowledgeSheetDescription } from "../knowledge-shared/KnowledgeSurfaces";
import { Button } from "@/components/ui/button";
import type { KnowledgeDoc, KnowledgeChunk } from "@/lib/api";

export function KnowledgeDocumentSheet({ doc, previewChunks, previewTotal, previewLoading, previewError, onClose, onRetry }: {
 doc: KnowledgeDoc | null; previewChunks: KnowledgeChunk[]; previewTotal: number;
 previewLoading: boolean; previewError: string | null; onClose: () => void; onRetry: () => void;
}) {
 const { t } = useTranslation();
 const returnFocus = useRef<HTMLElement | null>(null);
 return <KnowledgeSheet open={!!doc} onOpenChange={open => { if (!open) onClose(); }}>
   <KnowledgeSheetContent data-testid="knowledge-chunk-preview" showCloseButton={false}
     className="gap-0 p-0"
     onOpenAutoFocus={() => { returnFocus.current = document.activeElement as HTMLElement; }}
      onCloseAutoFocus={event => { event.preventDefault();
        const target = returnFocus.current?.isConnected ? returnFocus.current : document.querySelector<HTMLElement>('[data-testid="knowledge-page"] button');
        target?.focus();
      }}>
     <header className="border-b p-5"><KnowledgeSheetTitle>{t("knowledge.chunkTitle")}</KnowledgeSheetTitle><KnowledgeSheetDescription className="mt-2 break-words">{doc?.title}</KnowledgeSheetDescription></header>
     {doc?.error && <p role="alert" className="mx-5 mt-4 whitespace-pre-wrap break-words rounded border border-destructive/30 p-3 text-sm text-destructive">{doc.error}</p>}
                 <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              {previewLoading ? (
                <p className="flex items-center gap-2 text-[12px] text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 knowledge-motion animate-spin" aria-hidden />
                  {t("knowledge.loading")}
                </p>
              ) : previewError ? (
                <div
                  role="alert"
                  className="rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-[12px] text-destructive"
                >
                  {t("knowledge.chunksLoadError")}: {previewError}
                </div>
              ) : previewChunks.length === 0 ? (
                <p className="py-6 text-center text-[12px] text-muted-foreground">
                  {t("knowledge.chunksEmpty")}
                </p>
              ) : (
                <div className="flex flex-col gap-2">
                  {previewChunks.map((chunk) => (
                    <article
                      key={chunk.id}
                      data-testid="knowledge-chunk"
                      className="rounded-control border border-border/60 bg-muted/15 p-3"
                    >
                      <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                        <span className="rounded-full bg-muted px-2 py-0.5 font-semibold tabular-nums text-foreground">
                          #{chunk.index}
                        </span>
                        {chunk.heading ? (
                          <span className="max-w-[320px] truncate text-foreground">{chunk.heading}</span>
                        ) : null}
                        {chunk.page !== null ? (
                          <span>{t("knowledge.pageLabel", { page: chunk.page })}</span>
                        ) : null}
                        <span>{t("knowledge.sizeValue", { count: chunk.charCount })}</span>
                        <span className="ml-auto font-mono text-[10px]">{chunk.id}</span>
                      </div>
                      <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground">
                        {chunk.content}
                      </p>
                    </article>
                  ))}
                </div>
              )}
            </div>
            <div className="flex items-center gap-2 border-t border-border/60 px-5 py-3">
              {previewChunks.length > 0 ? (
                <span className="text-[11px] text-muted-foreground">
                  {previewChunks.length < previewTotal
                    ? t("knowledge.chunksShowing", { total: previewTotal, shown: previewChunks.length })
                    : t("knowledge.chunksSummary", { count: previewTotal })}
                </span>
              ) : null}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={onClose}
                className="ml-auto h-8 rounded-full px-3 text-[12px]"
              >
                {t("knowledge.close")}
              </Button>
            </div>
     {previewError && <Button className="mx-5 mb-4" variant="outline" onClick={onRetry}>{t("knowledge.experience.retry", { defaultValue: "重试" })}</Button>}
   </KnowledgeSheetContent>
 </KnowledgeSheet>;
}
