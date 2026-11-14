import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { BookMarked, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { KnowledgeSourcePreview } from "@/components/workspace/knowledge/KnowledgeSourcePreview";
import { fetchKnowledgeDocContent, type KnowledgeDocSourcePayload } from "@/lib/api";
import type { KnowledgeSourceHit } from "@/lib/knowledge-sources";

type SourceState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; payload: KnowledgeDocSourcePayload };

/** 知识来源原文面板：占用右侧栏，关闭后回到工作台。 */
export function KnowledgeSourcePanel({
  source,
  token,
  onClose,
}: {
  source: KnowledgeSourceHit;
  token: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [state, setState] = useState<SourceState>({ status: "loading" });
  const [reload, setReload] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    fetchKnowledgeDocContent(token, { id: source.docId })
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
  }, [source.docId, token, reload]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [source.chunkId]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const meta = [source.kb, source.heading, source.page ? `p.${source.page}` : ""]
    .filter(Boolean)
    .join(" · ");

  return (
    <aside
      data-testid="knowledge-source-panel"
      aria-label={t("message.knowledgeSources.title", { defaultValue: "来源文档" })}
      className="relative flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden border-l border-border/60 bg-surface-raised text-foreground"
    >
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border/50 px-3">
        <BookMarked className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <span className="shrink-0 text-xs font-semibold">
          {t("message.knowledgeSources.title", { defaultValue: "来源文档" })}
        </span>
        <span className="min-w-0 flex-1 truncate text-[12px] text-muted-foreground" title={source.title}>
          {source.title}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={t("message.knowledgeSources.close", { defaultValue: "关闭来源文档" })}
          title={t("message.knowledgeSources.close", { defaultValue: "关闭来源文档" })}
          onClick={onClose}
          className="h-7 w-7 shrink-0 rounded-full text-muted-foreground hover:bg-accent/40 hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </Button>
      </header>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {meta ? <p className="mb-2 truncate text-[11px] text-muted-foreground">{meta}</p> : null}

        {source.content.trim() ? (
          <section className="mb-4">
            <h3 className="text-[11px] font-medium text-muted-foreground">
              {t("message.knowledgeSources.excerpt", { defaultValue: "本次引用" })}
            </h3>
            <blockquote className="mt-1 whitespace-pre-wrap break-words rounded-control border border-border/60 bg-muted/25 px-3 py-2 text-[12px] leading-relaxed text-foreground">
              {source.content}
            </blockquote>
          </section>
        ) : null}

        <section>
          <h3 className="mb-1.5 text-[11px] font-medium text-muted-foreground">
            {t("message.knowledgeSources.source", { defaultValue: "原文" })}
          </h3>
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
            <KnowledgeSourcePreview payload={state.payload} />
          )}
        </section>
      </div>
    </aside>
  );
}
