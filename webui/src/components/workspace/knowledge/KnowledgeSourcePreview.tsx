import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { FileText } from "lucide-react";

import { MarkdownText } from "@/components/MarkdownText";
import {
  blockMatchesChunk,
  findChunkRange,
  injectMarkdownMarks,
  splitHighlighted,
} from "@/lib/knowledge-highlight";
import { cn } from "@/lib/utils";
import type { KnowledgeDocSourcePayload } from "@/lib/api";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const HEADING_CLASS: Record<number, string> = {
  1: "text-lg font-semibold",
  2: "text-base font-semibold",
  3: "text-sm font-semibold",
};

function SplitText({ text, highlight }: { text: string; highlight?: string }) {
  const segments = useMemo(
    () => splitHighlighted(text, highlight ? findChunkRange(text, highlight) : null),
    [highlight, text],
  );
  if (!highlight || segments.every((segment) => !segment.hit)) return <>{text}</>;
  return (
    <>
      {segments.map((segment, index) => (
        segment.hit ? <mark key={index}>{segment.text}</mark> : segment.text
      ))}
    </>
  );
}

export function KnowledgeSourcePreview({
  payload,
  highlight,
  hideName = false,
}: {
  payload: KnowledgeDocSourcePayload;
  /** 命中片段正文：传入后在原文里高亮并滚动到首个命中位置。 */
  highlight?: string;
  /** 外层已展示文档名时只保留体积/截断信息。 */
  hideName?: boolean;
}) {
  const { t } = useTranslation();
  const rootRef = useRef<HTMLDivElement>(null);
  const emptyPageCount = payload.pages.filter((page) => !page.text.trim()).length;
  const needle = highlight?.trim() ? highlight : undefined;

  const markdown = useMemo(
    () => (needle ? injectMarkdownMarks(payload.content, findChunkRange(payload.content, needle)) : null),
    [needle, payload.content],
  );

  const docxBlocks = useMemo(() => {
    if (!needle) return null;
    return payload.blocks.map((block) => (
      block.type === "table"
        ? block.rows.some((row) => row.some((cell) => blockMatchesChunk(cell, needle)))
        : blockMatchesChunk(block.text, needle)
    ));
  }, [needle, payload.blocks]);

  useEffect(() => {
    if (!needle) return undefined;
    const frame = window.requestAnimationFrame(() => {
      rootRef.current
        ?.querySelector("mark, [data-hit='true']")
        ?.scrollIntoView({ block: "center" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [docxBlocks, markdown, needle, payload]);

  return (
    <div
      ref={rootRef}
      data-testid="knowledge-source-preview"
      className={cn("flex flex-col gap-3", needle && "knowledge-source-highlight")}
    >
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
        <FileText className="h-3.5 w-3.5 shrink-0" aria-hidden />
        {hideName ? null : (
          <span className="max-w-[320px] truncate text-foreground">{payload.name}</span>
        )}
        <span className="tabular-nums">{formatBytes(payload.size)}</span>
        {payload.truncated ? (
          <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-amber-700 dark:text-amber-300">
            {t("knowledge.sourceTruncated")}
          </span>
        ) : null}
      </div>

      {payload.kind === "unsupported" ? (
        <p className="py-6 text-center text-[12px] text-muted-foreground">
          {t("knowledge.sourceUnsupported")}
        </p>
      ) : null}

      {payload.kind === "markdown" ? (
        payload.content.trim() ? (
          <MarkdownText className="max-w-none text-sm leading-relaxed text-foreground">
            {markdown ?? payload.content}
          </MarkdownText>
        ) : (
          <p className="py-6 text-center text-[12px] text-muted-foreground">
            {t("knowledge.sourceEmpty")}
          </p>
        )
      ) : null}

      {payload.kind === "text" ? (
        payload.content.trim() ? (
          <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-foreground">
            <SplitText text={payload.content} highlight={needle} />
          </pre>
        ) : (
          <p className="py-6 text-center text-[12px] text-muted-foreground">
            {t("knowledge.sourceEmpty")}
          </p>
        )
      ) : null}

      {payload.kind === "pdf" ? (
        <>
          {emptyPageCount === payload.pages.length && payload.pages.length > 0 ? (
            <p className="rounded-control border border-border/60 bg-muted/20 px-3 py-2 text-[12px] text-muted-foreground">
              {t("knowledge.sourcePdfNoText")}
            </p>
          ) : null}
          {payload.pages.map((page) => {
            const hit = needle ? findChunkRange(page.text, needle) !== null : false;
            return (
              <section
                key={page.page}
                data-testid="knowledge-source-page"
                data-hit={hit ? "true" : undefined}
                className={cn(
                  "rounded-control border border-border/60 bg-muted/15 p-3",
                  hit && "border-primary/45 bg-primary/[0.04]",
                )}
              >
                <div className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span className="rounded-full bg-muted px-2 py-0.5 font-semibold tabular-nums text-foreground">
                    {t("knowledge.sourcePage", { page: page.page })}
                  </span>
                  {page.page === 1 && payload.totalPages > 0 ? (
                    <span>{t("knowledge.sourcePageTotal", { total: payload.totalPages })}</span>
                  ) : null}
                </div>
                {page.text.trim() ? (
                  <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground">
                    <SplitText text={page.text} highlight={needle} />
                  </p>
                ) : (
                  <p className="text-[12px] text-muted-foreground">{t("knowledge.sourcePageEmpty")}</p>
                )}
              </section>
            );
          })}
        </>
      ) : null}

      {payload.kind === "docx" ? (
        payload.blocks.length === 0 ? (
          <p className="py-6 text-center text-[12px] text-muted-foreground">
            {t("knowledge.sourceEmpty")}
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {payload.blocks.map((block, index) => {
              const hit = docxBlocks?.[index] === true;
              if (block.type === "table") {
                return (
                  <div
                    key={index}
                    data-hit={hit ? "true" : undefined}
                    className={cn(
                      "overflow-x-auto rounded-control border border-border/60",
                      hit && "border-primary/45 bg-primary/[0.04]",
                    )}
                  >
                    <table className="w-full border-collapse text-sm">
                      <tbody>
                        {block.rows.map((row, rowIndex) => (
                          <tr key={rowIndex} className={rowIndex === 0 ? "bg-muted/40 font-medium" : ""}>
                            {row.map((cell, cellIndex) => (
                              <td
                                key={cellIndex}
                                className="border border-border/40 px-2 py-1 align-top text-foreground"
                              >
                                {hit ? <mark>{cell}</mark> : cell}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                );
              }
              if (block.type === "heading") {
                return (
                  <h3
                    key={index}
                    className={cn(
                      "text-foreground",
                      HEADING_CLASS[block.level ?? 3] ?? HEADING_CLASS[3],
                      index > 0 ? "mt-1" : undefined,
                    )}
                  >
                    {hit ? <mark>{block.text}</mark> : block.text}
                  </h3>
                );
              }
              return (
                <p key={index} className="whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground">
                  {hit ? <mark>{block.text}</mark> : block.text}
                </p>
              );
            })}
          </div>
        )
      ) : null}
    </div>
  );
}
