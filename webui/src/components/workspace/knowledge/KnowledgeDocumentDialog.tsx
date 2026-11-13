import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronsDownUp, ChevronsUpDown, Loader2, Search } from "lucide-react";
import {
  KnowledgeDialog,
  KnowledgeDialogContent,
  KnowledgeDialogDescription,
  KnowledgeDialogTitle,
} from "../knowledge-shared/KnowledgeSurfaces";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { KnowledgeSourcePreview } from "./KnowledgeSourcePreview";
import {
  fetchKnowledgeDocContent,
  type KnowledgeChunk,
  type KnowledgeDoc,
  type KnowledgeDocSourcePayload,
} from "@/lib/api";

type DocTab = "chunks" | "source";

type SourceState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; payload: KnowledgeDocSourcePayload };

export function KnowledgeDocumentDialog({
  doc,
  token,
  previewChunks,
  previewTotal,
  previewDocTotal,
  previewOffset,
  previewQuery,
  previewPageSize,
  previewLoading,
  previewError,
  onClose,
  onPageChange,
  onRetry,
}: {
  doc: KnowledgeDoc | null;
  token: string;
  previewChunks: KnowledgeChunk[];
  previewTotal: number;
  previewDocTotal: number;
  previewOffset: number;
  previewQuery: string;
  previewPageSize: number;
  previewLoading: boolean;
  previewError: string | null;
  onClose: () => void;
  onPageChange: (offset: number, query: string) => void;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  const returnFocus = useRef<HTMLElement | null>(null);
  const [tab, setTab] = useState<DocTab>("chunks");
  const [collapsed, setCollapsed] = useState(false);
  const [searchValue, setSearchValue] = useState("");
  const [sourceState, setSourceState] = useState<SourceState | null>(null);
  const [sourceReload, setSourceReload] = useState(0);
  const docId = doc?.id ?? null;

  useEffect(() => {
    if (docId === null) {
      setTab("chunks");
      setCollapsed(false);
      setSearchValue("");
      setSourceState(null);
    }
  }, [docId]);

  useEffect(() => {
    if (docId === null) return;
    const next = searchValue.trim();
    if (next === previewQuery) return;
    const timer = window.setTimeout(() => onPageChange(0, next), 300);
    return () => window.clearTimeout(timer);
  }, [docId, onPageChange, previewQuery, searchValue]);

  useEffect(() => {
    if (!docId || tab !== "source") return;
    let cancelled = false;
    setSourceState({ status: "loading" });
    fetchKnowledgeDocContent(token, { id: docId })
      .then((payload) => {
        if (!cancelled) setSourceState({ status: "ready", payload });
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setSourceState({
            status: "error",
            message: reason instanceof Error ? reason.message : String(reason),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [docId, tab, token, sourceReload]);

  const pageStart = previewTotal === 0 ? 0 : previewOffset + 1;
  const pageEnd = previewOffset + previewChunks.length;
  const canPrev = previewOffset > 0 && !previewLoading;
  const canNext = previewOffset + previewPageSize < previewTotal && !previewLoading;

  return (
    <KnowledgeDialog open={!!doc} onOpenChange={(open) => { if (!open) onClose(); }}>
      <KnowledgeDialogContent
        data-testid="knowledge-chunk-preview"
        showCloseButton={false}
        className="knowledge-doc-dialog"
        onOpenAutoFocus={() => {
          returnFocus.current = document.activeElement as HTMLElement;
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          const target = returnFocus.current?.isConnected
            ? returnFocus.current
            : document.querySelector<HTMLElement>('[data-testid="knowledge-page"] button');
          target?.focus();
        }}
      >
        <header className="shrink-0 border-b border-border/60 px-6 py-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <KnowledgeDialogTitle>{t("knowledge.chunkTitle")}</KnowledgeDialogTitle>
              <KnowledgeDialogDescription className="mt-1 break-words">
                {doc?.title}
                {previewDocTotal > 0 ? (
                  <span className="ml-2 text-[11px] tabular-nums">
                    {t("knowledge.chunksSummary", { count: previewDocTotal })}
                  </span>
                ) : null}
              </KnowledgeDialogDescription>
            </div>
            <SegmentedControl<DocTab>
              value={tab}
              mode="tabs"
              ariaLabel={t("knowledge.docTabAria")}
              onChange={setTab}
              options={[
                { value: "chunks", label: t("knowledge.docTabChunks") },
                { value: "source", label: t("knowledge.docTabSource") },
              ]}
            />
          </div>
        </header>

        {doc?.error ? (
          <p
            role="alert"
            className="mx-6 mt-4 shrink-0 whitespace-pre-wrap break-words rounded-control border border-destructive/30 p-3 text-sm text-destructive"
          >
            {doc.error}
          </p>
        ) : null}

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
          <div hidden={tab !== "chunks"}>
            <div className="sticky top-0 z-10 -mx-1 mb-3 flex items-center gap-2 bg-background/95 px-1 py-1 backdrop-blur">
              <div className="relative min-w-0 flex-1">
                <Search
                  className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
                  aria-hidden
                />
                <Input
                  value={searchValue}
                  onChange={(event) => setSearchValue(event.target.value)}
                  placeholder={t("knowledge.chunkSearchPlaceholder")}
                  aria-label={t("knowledge.chunkSearchPlaceholder")}
                  className="h-8 rounded-full pl-8 text-[12px]"
                />
              </div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={collapsed ? t("knowledge.chunksExpand") : t("knowledge.chunksCollapse")}
                title={collapsed ? t("knowledge.chunksExpand") : t("knowledge.chunksCollapse")}
                aria-pressed={collapsed}
                onClick={() => setCollapsed((value) => !value)}
                className="h-8 w-8 shrink-0 rounded-full p-0 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                {collapsed ? (
                  <ChevronsUpDown className="h-3.5 w-3.5" aria-hidden />
                ) : (
                  <ChevronsDownUp className="h-3.5 w-3.5" aria-hidden />
                )}
              </Button>
            </div>

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
                {previewQuery ? t("knowledge.chunksSearchEmpty") : t("knowledge.chunksEmpty")}
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
                    {collapsed ? null : (
                      <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground">
                        {chunk.content}
                      </p>
                    )}
                  </article>
                ))}
              </div>
            )}
          </div>

          <div hidden={tab !== "source"}>
            {sourceState === null || sourceState.status === "loading" ? (
              <p className="flex items-center gap-2 text-[12px] text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 knowledge-motion animate-spin" aria-hidden />
                {t("knowledge.loading")}
              </p>
            ) : sourceState.status === "error" ? (
              <div
                role="alert"
                className="rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-[12px] text-destructive"
              >
                {t("knowledge.sourceLoadError")}: {sourceState.message}
              </div>
            ) : (
              <KnowledgeSourcePreview payload={sourceState.payload} />
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2 border-t border-border/60 px-6 py-3">
          {tab === "chunks" && previewTotal > 0 ? (
            <span className="text-[11px] tabular-nums text-muted-foreground">
              {t("knowledge.chunksPageRange", {
                start: pageStart,
                end: pageEnd,
                total: previewTotal,
              })}
            </span>
          ) : null}
          {tab === "chunks" ? (
            <div className="flex items-center gap-1">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!canPrev}
                onClick={() => onPageChange(Math.max(0, previewOffset - previewPageSize), previewQuery)}
                className="h-7 rounded-full px-2.5 text-[11px]"
              >
                {t("knowledge.experience.previous", { defaultValue: "上一页" })}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!canNext}
                onClick={() => onPageChange(previewOffset + previewPageSize, previewQuery)}
                className="h-7 rounded-full px-2.5 text-[11px]"
              >
                {t("knowledge.experience.next", { defaultValue: "下一页" })}
              </Button>
            </div>
          ) : null}
          {tab === "source" && sourceState?.status === "error" ? (
            <Button
              type="button"
              variant="outline"
              className="h-8 rounded-full px-3 text-[12px]"
              onClick={() => setSourceReload((value) => value + 1)}
            >
              {t("knowledge.experience.retry", { defaultValue: "重试" })}
            </Button>
          ) : null}
          {tab === "chunks" && previewError ? (
            <Button
              type="button"
              variant="outline"
              className="h-8 rounded-full px-3 text-[12px]"
              onClick={onRetry}
            >
              {t("knowledge.experience.retry", { defaultValue: "重试" })}
            </Button>
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
      </KnowledgeDialogContent>
    </KnowledgeDialog>
  );
}
