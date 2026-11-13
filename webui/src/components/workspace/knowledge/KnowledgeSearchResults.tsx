import { useTranslation } from "react-i18next";

import { KnowledgeCitation } from "./KnowledgeCitation";
import { knowledgeModeLabel, knowledgeRouteLabel } from "./knowledge-labels";
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
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-muted-foreground">
        <span>
          {t("knowledge.modeUsed", { mode: knowledgeModeLabel(t, result.modeUsed) })}
        </span>
        {result.routes.length > 0 ? (
          <span>
            {t("knowledge.routes", {
              routes: result.routes.map((route) => knowledgeRouteLabel(t, route)).join(" · "),
            })}
          </span>
        ) : null}
        {result.degraded.length > 0 ? (
          <span className="text-amber-700 dark:text-amber-300">
            {t("knowledge.degraded", { reasons: result.degraded.join(", ") })}
          </span>
        ) : null}
      </div>
      {result.hits.length === 0 ? (
        <p className="text-sm text-muted-foreground">{emptyLabel}</p>
      ) : (
        <ul className="flex flex-col divide-y divide-border/60">
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
