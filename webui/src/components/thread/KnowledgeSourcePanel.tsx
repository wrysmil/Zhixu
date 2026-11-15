import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { BookMarked, ChevronLeft, ChevronRight, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { MarkdownText } from "@/components/MarkdownText";
import { KnowledgeSourcePreview } from "@/components/workspace/knowledge/KnowledgeSourcePreview";
import {
  fetchKnowledgeChunk,
  fetchKnowledgeDocContent,
  type KnowledgeDocSourcePayload,
} from "@/lib/api";
import {
  knowledgeHitMeta,
  type KnowledgeSourceHit,
} from "@/lib/knowledge-sources";

type SourceState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; payload: KnowledgeDocSourcePayload };

/** 知识来源原文面板：占用右侧栏，关闭后回到工作台。 */
export function KnowledgeSourcePanel({
  source,
  hits,
  token,
  onClose,
}: {
  source: KnowledgeSourceHit;
  /** 同一文档在本轮的全部命中，用于在面板内切换片段。 */
  hits: KnowledgeSourceHit[];
  token: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [activeChunkId, setActiveChunkId] = useState(source.chunkId);
  const [state, setState] = useState<SourceState>({ status: "loading" });
  const [chunkContent, setChunkContent] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setActiveChunkId(source.chunkId);
  }, [source.chunkId]);

  const index = Math.max(0, hits.findIndex((hit) => hit.chunkId === activeChunkId));
  const active = hits[index] ?? source;

  useEffect(() => {
    let cancelled = false;
    setChunkContent(null);
    fetchKnowledgeChunk(token, active.chunkId)
      .then((payload) => {
        if (!cancelled) setChunkContent(payload.chunk?.content.trim() || null);
      })
      .catch(() => {
        // 拿不到完整块时退回检索结果里的片段，仍可定位高亮。
        if (!cancelled) setChunkContent(null);
      });
    return () => {
      cancelled = true;
    };
  }, [active.chunkId, token]);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    fetchKnowledgeDocContent(token, { id: active.docId })
      .then((payload) => {
        if (!cancelled) setState({ status: "ready", payload });
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setState({
            status: "error",
            message: reason instanceof Error ? reason.message : String(reason),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [active.docId, token, reload]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [active.chunkId]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const meta = [active.kb, knowledgeHitMeta(active)].filter(Boolean).join(" · ");

  return (
    <aside
      data-testid="knowledge-source-panel"
      aria-label={t("message.knowledgeSources.title", { defaultValue: "来源文档" })}
      className="relative flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden border-l border-border/60 bg-surface-raised text-foreground"
    >
      <header className="shrink-0 border-b border-border/50 px-3 pb-2.5 pt-2">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1 text-[10px] font-medium tracking-wide text-muted-foreground">
              <BookMarked className="h-3 w-3 shrink-0" aria-hidden />
              {t("message.knowledgeSources.title", { defaultValue: "来源文档" })}
            </div>
            <h2
              className="mt-0.5 break-words text-[13px] font-semibold leading-5"
              title={active.title}
            >
              {active.title}
            </h2>
            {meta ? (
              <p className="mt-0.5 truncate text-[11px] text-muted-foreground" title={meta}>
                {meta}
              </p>
            ) : null}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={t("message.knowledgeSources.close", { defaultValue: "关闭来源文档" })}
            title={t("message.knowledgeSources.close", { defaultValue: "关闭来源文档" })}
            onClick={onClose}
            className="mt-0.5 h-7 w-7 shrink-0 rounded-full text-muted-foreground hover:bg-accent/40 hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </header>

      {hits.length > 1 ? (
        <div
          data-testid="knowledge-source-hit-nav"
          className="flex shrink-0 items-center gap-1 border-b border-border/45 px-2 py-1"
        >
          <Button
            type="button"
            variant="ghost"
            size="icon"
            disabled={index <= 0}
            aria-label={t("knowledge.experience.previous", { defaultValue: "上一个" })}
            onClick={() => setActiveChunkId(hits[index - 1].chunkId)}
            className="h-6 w-6 shrink-0 rounded-full text-muted-foreground hover:bg-accent/40 hover:text-foreground"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </Button>
          <span className="min-w-0 flex-1 truncate text-center text-[11px] text-muted-foreground">
            {knowledgeHitMeta(active) || active.title}
          </span>
          <span className="shrink-0 tabular-nums text-[11px] text-muted-foreground">
            {index + 1}/{hits.length}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            disabled={index >= hits.length - 1}
            aria-label={t("knowledge.experience.next", { defaultValue: "下一个" })}
            onClick={() => setActiveChunkId(hits[index + 1].chunkId)}
            className="h-6 w-6 shrink-0 rounded-full text-muted-foreground hover:bg-accent/40 hover:text-foreground"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col">
        {active.content.trim() ? (
          <section className="shrink-0 border-b border-border/45 px-3 pb-2 pt-2">
            <h3 className="text-[10px] font-medium tracking-wide text-muted-foreground">
              {t("message.knowledgeSources.excerpt", { defaultValue: "本次引用" })}
            </h3>
            <div
              data-testid="knowledge-source-excerpt"
              className="mt-1 max-h-32 overflow-y-auto rounded-control border border-border/55 border-l-2 border-l-primary/40 bg-muted/20 px-3 py-2 scrollbar-thin scrollbar-track-transparent"
            >
              <MarkdownText className="max-w-none text-[12px] leading-relaxed text-foreground [&_h1]:my-1 [&_h1]:text-[12px] [&_h2]:my-1 [&_h2]:text-[12px] [&_h3]:my-1 [&_h3]:text-[12px] [&_li]:my-0.5 [&_p]:my-1 [&_ul]:my-1">
                {active.content}
              </MarkdownText>
            </div>
          </section>
        ) : null}

        <section className="flex min-h-0 flex-1 flex-col">
          <h3 className="shrink-0 px-3 pb-1 pt-2 text-[10px] font-medium tracking-wide text-muted-foreground">
            {t("message.knowledgeSources.source", { defaultValue: "原文" })}
          </h3>
          <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
            {state.status === "loading" ? (
              <p className="flex items-center gap-2 text-[12px] text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden />
                {t("knowledge.loading", { defaultValue: "加载中…" })}
              </p>
            ) : state.status === "error" ? (
              <div
                role="alert"
                className="rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-[12px] text-destructive"
              >
                {t("message.knowledgeSources.loadError", { defaultValue: "原文加载失败" })}：{state.message}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setReload((value) => value + 1)}
                  className="mt-2 h-7 rounded-full px-2.5 text-[11px]"
                >
                  {t("knowledge.experience.retry", { defaultValue: "重试" })}
                </Button>
              </div>
            ) : (
              <KnowledgeSourcePreview
                payload={state.payload}
                highlight={chunkContent ?? active.content}
                hideName
              />
            )}
          </div>
        </section>
      </div>
    </aside>
  );
}
