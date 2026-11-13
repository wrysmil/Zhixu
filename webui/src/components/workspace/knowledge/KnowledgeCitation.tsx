import { useState } from "react";
import { ChevronDown, FileText, MapPin } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

export interface KnowledgeCitationProps {
  /** 1-based hit rank. */
  rank?: number;
  /** Human readable document title; falls back to the raw id when unresolved. */
  docTitle?: string;
  docId: string;
  /** Chunk id — kept for traceability even when the doc title is known. */
  chunkId?: string;
  /** Section heading inside the document. */
  heading?: string;
  page?: number | null;
  /** Free-form locator such as `p.12 / §3` or a chunk offset. */
  locator?: string;
  /** The cited passage. */
  content: string;
  score?: number;
  /** Opens the source document with its chunk list. */
  onLocate?: () => void;
  defaultExpanded?: boolean;
  className?: string;
}

/**
 * Citation card: answers "which document, and which part of it" in one glance.
 *
 * The old hit list only exposed raw `chunkId` / `docId` hashes, so a reader
 * could not tell what had actually been cited. This surfaces the document
 * title, the section heading, the page/locator and the passage itself.
 */
export function KnowledgeCitation({
  rank,
  docTitle,
  docId,
  chunkId,
  heading,
  page,
  locator,
  content,
  score,
  onLocate,
  defaultExpanded = false,
  className,
}: KnowledgeCitationProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(defaultExpanded);
  const title = docTitle?.trim() || docId;
  const pageLabel = typeof page === "number" ? t("knowledge.pageLabel", { page }) : null;


  return (
    <li
      className={cn(
        "motion-fade-in-up group rounded-control border border-border/60 border-l-2 border-l-primary/50 bg-card px-3 py-2.5 shadow-sm transition-colors hover:border-primary/40",
        className,
      )}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        {typeof rank === "number" ? (
          <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-muted px-1.5 font-semibold tabular-nums text-foreground">
            {rank}
          </span>
        ) : null}
        {typeof score === "number" ? (
          <span className="rounded-full bg-primary/10 px-2 py-0.5 font-semibold tabular-nums text-primary">
            {score.toFixed(3)}
          </span>
        ) : null}
        <span className="inline-flex min-w-0 items-center gap-1.5 font-medium text-foreground">
          <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <span className="truncate">{title}</span>
        </span>
        {heading ? (
          <span className="min-w-0 truncate text-muted-foreground">{heading}</span>
        ) : null}
        {pageLabel ? (
          <span className="inline-flex items-center gap-1 text-muted-foreground">
            <MapPin className="h-3 w-3 shrink-0" aria-hidden />
            {pageLabel}
          </span>
        ) : null}
        {locator && locator !== pageLabel ? (
          <span className="font-mono text-[10px] text-muted-foreground">{locator}</span>
        ) : null}
        <span className="ml-auto flex items-center gap-1">
          {onLocate ? (
            <button
              type="button"
              onClick={onLocate}
              className="rounded-full border border-border/70 px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:border-primary/50 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {t("knowledge.experience.citationLocate", { defaultValue: "定位到文档" })}
            </button>
          ) : null}
          <button
            type="button"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
            className="inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {expanded
              ? t("knowledge.experience.citationCollapse", { defaultValue: "收起" })
              : t("knowledge.experience.citationExpand", { defaultValue: "展开" })}
            <ChevronDown
              className={cn("h-3 w-3 transition-transform duration-150", expanded && "rotate-180")}
              aria-hidden
            />
          </button>
        </span>
      </div>
      <p
        className={cn(
          "mt-1.5 whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground",
          expanded ? "max-h-72 overflow-y-auto" : "line-clamp-3",
        )}
      >
        {content}
      </p>
      <details className="mt-1 text-[10px] text-muted-foreground">
        <summary className="cursor-pointer list-none opacity-70 transition-opacity hover:opacity-100">
          {t("knowledge.experience.citationIds", { defaultValue: "定位信息" })}
        </summary>
        <div className="mt-1 flex flex-wrap gap-1.5 font-mono">
          {chunkId ? <span className="rounded bg-muted/60 px-1.5 py-0.5">{chunkId}</span> : null}
          <span className="rounded bg-muted/60 px-1.5 py-0.5">{docId}</span>
        </div>
      </details>
    </li>
  );
}
