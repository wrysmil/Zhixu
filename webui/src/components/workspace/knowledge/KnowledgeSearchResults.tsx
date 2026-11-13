import { useTranslation } from "react-i18next";

import { KnowledgeCitation } from "./KnowledgeCitation";
import type { KnowledgeDoc, KnowledgeSearchResult } from "@/lib/api";

export function KnowledgeSearchResults({
  result,
  emptyLabel,
  docsById,
  onLocate,
}: {
  result: KnowledgeSearchResult;
  emptyLabel: string;
  docsById?: Map<string, KnowledgeDoc>;
  onLocate?: (docId: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="rounded-full border border-border/70 px-2 py-0.5 font-medium text-foreground">
          {t("knowledge.modeUsed", { mode: result.modeUsed })}
        </span>
        {result.routes.length > 0 ? (
          <span className="rounded-full border border-border/70 px-2 py-0.5 text-muted-foreground">
            {t("knowledge.routes", { routes: result.routes.join(", ") })}
          </span>
        ) : null}
        {result.degraded.length > 0 ? (
          <span className="rounded-full border border-amber-300/70 bg-amber-50 px-2 py-0.5 text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-100">
            {t("knowledge.degraded", { reasons: result.degraded.join(", ") })}
          </span>
        ) : null}
        <span className="text-muted-foreground">
          {t("knowledge.hitCount", { count: result.hits.length })}
        </span>
      </div>
      {result.hits.length === 0 ? (
        <p className="text-sm text-muted-foreground">{emptyLabel}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {result.hits.map((hit, index) => (
            <KnowledgeCitation
              key={hit.chunkId}
              rank={index + 1}
              docId={hit.docId}
              chunkId={hit.chunkId}
              docTitle={docsById?.get(hit.docId)?.title}
              heading={hit.heading}
              page={hit.page}
              locator={hit.locator}
              content={hit.content}
              score={hit.score}
              onLocate={onLocate ? () => onLocate(hit.docId) : undefined}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
