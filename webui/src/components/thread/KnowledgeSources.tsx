import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { BookMarked, ChevronDown, ChevronRight, FileText } from "lucide-react";

import { cn } from "@/lib/utils";
import type { KnowledgeSourceHit } from "@/lib/knowledge-sources";

const COLLAPSED_DOC_COUNT = 3;

interface SourceGroup {
  docId: string;
  title: string;
  hits: KnowledgeSourceHit[];
}

function groupByDocument(sources: KnowledgeSourceHit[]): SourceGroup[] {
  const groups = new Map<string, SourceGroup>();
  for (const hit of sources) {
    const group = groups.get(hit.docId);
    if (group) group.hits.push(hit);
    else groups.set(hit.docId, { docId: hit.docId, title: hit.title, hits: [hit] });
  }
  return [...groups.values()];
}

/** 引用来源条：文档级总览，点开右侧面板后再切换具体命中片段。 */
export function KnowledgeSources({
  sources,
  onOpen,
}: {
  sources: KnowledgeSourceHit[];
  onOpen: (hit: KnowledgeSourceHit, related: KnowledgeSourceHit[]) => void;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const signature = sources.map((hit) => hit.chunkId).join("|");

  useEffect(() => {
    setExpanded(false);
  }, [signature]);

  if (sources.length === 0) return null;
  const groups = groupByDocument(sources);
  const collapsible = groups.length > COLLAPSED_DOC_COUNT;
  const visible = expanded || !collapsible ? groups : groups.slice(0, COLLAPSED_DOC_COUNT);
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
      <ul className="mt-1.5 overflow-hidden rounded-control border border-border/60 bg-background/60">
        {visible.map((group, index) => (
          <li key={group.docId} className={index > 0 ? "border-t border-border/45" : undefined}>
            <button
              type="button"
              data-testid="knowledge-source-item"
              title={group.title}
              onClick={() => onOpen(group.hits[0], group.hits)}
              className="group flex w-full min-w-0 items-center gap-2 px-2.5 py-1.5 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            >
              <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
              <span className="min-w-0 flex-1 truncate text-[12px] text-foreground">
                {group.title}
              </span>
              {group.hits.length > 1 ? (
                <span className="shrink-0 tabular-nums text-[11px] text-muted-foreground">
                  {group.hits.length}
                </span>
              ) : null}
              <ChevronRight
                className="h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-45 transition-opacity group-hover:opacity-90"
                aria-hidden
              />
            </button>
          </li>
        ))}
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
                count: groups.length,
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
