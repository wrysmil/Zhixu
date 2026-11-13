import { useState } from "react";
import { ChevronDown } from "lucide-react";
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
        "group -mx-2 grid grid-cols-[1.25rem_minmax(0,1fr)_3.25rem] items-start gap-x-3 rounded-control px-2 py-3 transition-colors hover:bg-muted/30",
        className,
      )}
    >
      <span className="pt-0.5 text-right text-[11px] tabular-nums text-muted-foreground">
        {typeof rank === "number" ? rank : ""}
      </span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[12px]">
          <span className="max-w-[22rem] truncate font-medium text-foreground">{title}</span>
          {heading ? (
            <span className="min-w-0 truncate text-muted-foreground">{heading}</span>
          ) : null}
          {pageLabel ? <span className="text-muted-foreground">{pageLabel}</span> : null}
        </div>
        <p
          className={cn(
            "mt-1 whitespace-pre-wrap break-words text-[13px] leading-relaxed text-foreground",
            expanded ? "max-h-72 overflow-y-auto" : "line-clamp-3",
          )}
        >
          {content}
        </p>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
          <button
            type="button"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
            className="inline-flex items-center gap-0.5 rounded-control transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {expanded
              ? t("knowledge.experience.citationCollapse", { defaultValue: "收起" })
              : t("knowledge.experience.citationExpand", { defaultValue: "展开" })}
            <ChevronDown
              className={cn("h-3 w-3 transition-transform duration-150", expanded && "rotate-180")}
              aria-hidden
            />
          </button>
          {onLocate ? (
            <button
              type="button"
              onClick={onLocate}
              className="rounded-control transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {t("knowledge.experience.citationLocate", { defaultValue: "定位到文档" })}
            </button>
          ) : null}
          {locator && locator !== pageLabel ? (
            <span className="font-mono text-[10px] text-muted-foreground">{locator}</span>
          ) : null}
          <details className="ml-auto text-[10px]">
            <summary className="cursor-pointer list-none text-muted-foreground transition-colors hover:text-foreground">
              {t("knowledge.experience.citationIds", { defaultValue: "定位信息" })}
            </summary>
            <div className="mt-1 flex flex-wrap gap-1.5 font-mono">
              {chunkId ? <span className="rounded bg-muted/60 px-1.5 py-0.5">{chunkId}</span> : null}
              <span className="rounded bg-muted/60 px-1.5 py-0.5">{docId}</span>
            </div>
          </details>
        </div>
      </div>
      <span className="pt-0.5 text-right text-[12px] tabular-nums text-muted-foreground">
        {typeof score === "number" ? score.toFixed(3) : ""}
      </span>
    </li>
  );
}
