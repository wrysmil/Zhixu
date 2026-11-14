import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { BookMarked, ChevronDown, ChevronRight, FileText } from "lucide-react";

import { cn } from "@/lib/utils";
import type { KnowledgeSourceHit } from "@/lib/knowledge-sources";

const COLLAPSED_SOURCE_COUNT = 4;

/** 引用来源条：挂在知识库回答下方，点击在右侧打开原文。 */
export function KnowledgeSources({
  sources,
  onOpen,
}: {
  sources: KnowledgeSourceHit[];
  onOpen: (hit: KnowledgeSourceHit) => void;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const signature = sources.map((hit) => hit.chunkId).join("|");

  useEffect(() => {
    setExpanded(false);
  }, [signature]);

  if (sources.length === 0) return null;
  const collapsible = sources.length > COLLAPSED_SOURCE_COUNT;
  const visible = expanded || !collapsible ? sources : sources.slice(0, COLLAPSED_SOURCE_COUNT);
  return (
    <section
      data-testid="knowledge-sources"
      aria-label={t("message.knowledgeSources.title", { defaultValue: "来源文档" })}
      className="mt-3"
    >
      <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
        <BookMarked className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span>{t("message.knowledgeSources.title", { defaultValue: "来源文档" })}</span>
        <span className="tabular-nums">· {sources.length}</span>
      </div>
      <ul className="mt-1.5 flex flex-col gap-1">
        {visible.map((hit) => {
          const meta = [hit.kb, hit.heading, hit.page ? `p.${hit.page}` : ""]
            .filter(Boolean)
            .join(" · ");
          return (
            <li key={hit.chunkId}>
              <button
                type="button"
                data-testid="knowledge-source-item"
                title={hit.title}
                onClick={() => onOpen(hit)}
                className="group flex w-full min-w-0 items-center gap-2 rounded-control border border-border/60 bg-background/60 px-2.5 py-1.5 text-left transition-colors hover:border-border hover:bg-muted/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12px] text-foreground">{hit.title}</span>
                  {meta ? (
                    <span className="block truncate text-[11px] text-muted-foreground">{meta}</span>
                  ) : null}
                </span>
                <ChevronRight
                  className="h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100"
                  aria-hidden
                />
              </button>
            </li>
          );
        })}
      </ul>
      {collapsible ? (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
          className="mt-1 inline-flex items-center gap-0.5 rounded-control text-[11px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {expanded
            ? t("message.knowledgeSources.less", { defaultValue: "收起" })
            : t("message.knowledgeSources.more", {
                defaultValue: "展开全部 {{count}} 个来源",
                count: sources.length,
              })}
          <ChevronDown
            className={cn("h-3 w-3 transition-transform", expanded && "rotate-180")}
            aria-hidden
          />
        </button>
      ) : null}
    </section>
  );
}
