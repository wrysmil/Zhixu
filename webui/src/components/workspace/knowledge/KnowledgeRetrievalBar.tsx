import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { History, Loader2, Search, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  KnowledgeDialog,
  KnowledgeDialogContent,
  KnowledgeDialogDescription,
  KnowledgeDialogTitle,
} from "../knowledge-shared/KnowledgeSurfaces";
import { KnowledgeSearchResults } from "./KnowledgeSearchResults";
import { RetrievalTraceCanvas, retrievalStageTitle } from "./RetrievalTraceCanvas";
import {
  clearKnowledgeQueryHistory,
  fetchKnowledgeQueryHistory,
  searchKnowledge,
  type KnowledgeDoc,
  type KnowledgeKb,
  type KnowledgeQueryHistoryItem,
  type KnowledgeSearchMode,
  type KnowledgeSearchResult,
  type KnowledgeTraceStage,
  type WebUIMutationTransport,
} from "@/lib/api";

const SEARCH_LIMITS = [5, 8, 12, 20];
const SEARCH_MODES: KnowledgeSearchMode[] = ["hybrid", "semantic", "keyword"];

function normalizeMode(value: string): KnowledgeSearchMode {
  return (SEARCH_MODES as string[]).includes(value)
    ? (value as KnowledgeSearchMode)
    : "hybrid";
}

export function KnowledgeRetrievalBar({
  kb,
  token,
  transport,
  scope,
  docs,
  onLocateDoc,
}: {
  kb: KnowledgeKb;
  token: string;
  transport: WebUIMutationTransport;
  scope?: string[];
  docs: KnowledgeDoc[];
  onLocateDoc: (docId: string) => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<KnowledgeSearchMode>("hybrid");
  const [limit, setLimit] = useState(8);
  const [searching, setSearching] = useState(false);
  const [result, setResult] = useState<KnowledgeSearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<KnowledgeQueryHistoryItem[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [canvasCollapsed, setCanvasCollapsed] = useState(false);
  const [activeStage, setActiveStage] = useState<KnowledgeTraceStage | null>(null);
  const seq = useRef(0);
  const busy = useRef(false);

  const docsById = useMemo(() => {
    const map = new Map<string, KnowledgeDoc>();
    for (const doc of docs) map.set(doc.id, doc);
    return map;
  }, [docs]);

  const loadHistory = useCallback(async () => {
    try {
      const payload = await fetchKnowledgeQueryHistory(token, { kbId: kb.id, limit: 20 });
      setHistory(payload.items);
      setHistoryError(null);
    } catch (reason: unknown) {
      setHistoryError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [kb.id, token]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  const runSearch = useCallback(
    async (next: { query: string; mode: KnowledgeSearchMode; limit: number }) => {
      const text = next.query.trim();
      if (!text || busy.current) return;
      const current = ++seq.current;
      busy.current = true;
      setSearching(true);
      setError(null);
      try {
        const payload = await searchKnowledge(token, {
          query: text,
          mode: next.mode,
          limit: next.limit,
          kbId: kb.id,
          workspaceIds: scope,
          trace: true,
          record: true,
        });
        if (current !== seq.current) return;
        setResult(payload);
        setHistoryOpen(false);
        void loadHistory();
      } catch (reason: unknown) {
        if (current !== seq.current) return;
        setResult(null);
        setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (current === seq.current) {
          setSearching(false);
          busy.current = false;
        }
      }
    },
    [kb.id, loadHistory, scope, token],
  );

  const runFromHistory = useCallback(
    (item: KnowledgeQueryHistoryItem) => {
      const nextMode = normalizeMode(item.mode);
      setQuery(item.query);
      setMode(nextMode);
      setLimit(item.limit);
      void runSearch({ query: item.query, mode: nextMode, limit: item.limit });
    },
    [runSearch],
  );

  const clearHistory = useCallback(async () => {
    setHistoryError(null);
    try {
      await clearKnowledgeQueryHistory(transport, kb.id);
      setHistory([]);
    } catch (reason: unknown) {
      setHistoryError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [kb.id, transport]);

  const panelOpen = searching || !!result || !!error;

  return (
    <section
      className="sticky top-0 z-20 rounded-panel border border-border/60 bg-background/95 p-3 shadow-sm backdrop-blur"
      data-testid="knowledge-retrieval-bar"
    >
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void runSearch({ query, mode, limit });
        }}
      >
        <label className="flex min-w-[200px] flex-1 flex-col gap-1 text-[11px] font-medium text-muted-foreground">
          {t("knowledge.query")}
          <Input
            aria-label={t("knowledge.query")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("knowledge.queryPlaceholder")}
            className="h-9 text-sm"
          />
        </label>
        <label className="flex w-28 shrink-0 flex-col gap-1 text-[11px] font-medium text-muted-foreground">
          {t("knowledge.mode")}
          <select
            aria-label={t("knowledge.mode")}
            value={mode}
            onChange={(event) => setMode(event.target.value as KnowledgeSearchMode)}
            className="h-9 rounded-control border border-input bg-background px-2 text-sm"
          >
            {SEARCH_MODES.map((value) => (
              <option key={value} value={value}>
                {t(`knowledge.mode${value.charAt(0).toUpperCase()}${value.slice(1)}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex w-20 shrink-0 flex-col gap-1 text-[11px] font-medium text-muted-foreground">
          {t("knowledge.limit")}
          <select
            aria-label={t("knowledge.limit")}
            value={String(limit)}
            onChange={(event) => setLimit(Number(event.target.value))}
            className="h-9 rounded-control border border-input bg-background px-2 text-sm"
          >
            {SEARCH_LIMITS.map((value) => (
              <option key={value} value={String(value)}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <Button
          type="submit"
          size="sm"
          disabled={searching || !query.trim()}
          className="h-9 gap-1.5 rounded-full px-4 text-sm font-semibold"
        >
          {searching ? (
            <Loader2 className="h-3.5 w-3.5 knowledge-motion animate-spin" aria-hidden />
          ) : (
            <Search className="h-3.5 w-3.5" aria-hidden />
          )}
          {t("knowledge.search")}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-label={t("knowledge.retrieval.history")}
          aria-expanded={historyOpen}
          onClick={() => setHistoryOpen((value) => !value)}
          className="h-9 gap-1.5 rounded-full px-3 text-sm"
        >
          <History className="h-3.5 w-3.5" aria-hidden />
          {history.length > 0 ? (
            <span className="tabular-nums">{history.length}</span>
          ) : null}
        </Button>
      </form>

      {historyOpen ? (
        <div className="mt-2 rounded-control border border-border/60 bg-muted/10 p-2">
          <div className="flex items-center gap-2 px-1 pb-1">
            <span className="text-[11px] font-medium text-muted-foreground">
              {t("knowledge.retrieval.history")}
            </span>
            {history.length > 0 ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={t("knowledge.retrieval.historyClear")}
                onClick={() => void clearHistory()}
                className="ml-auto h-6 rounded-full px-2 text-[11px] text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
              >
                <Trash2 className="h-3 w-3" aria-hidden />
              </Button>
            ) : null}
          </div>
          {historyError ? (
            <p role="alert" className="px-1 py-2 text-[12px] text-destructive">
              {historyError}
            </p>
          ) : history.length === 0 ? (
            <p className="px-1 py-2 text-[12px] text-muted-foreground">
              {t("knowledge.retrieval.historyEmpty")}
            </p>
          ) : (
            <ul className="flex max-h-44 flex-col gap-0.5 overflow-y-auto">
              {history.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => runFromHistory(item)}
                    className="w-full rounded-control px-2 py-1.5 text-left transition-colors hover:bg-muted/50"
                  >
                    <span className="block truncate text-[12.5px] text-foreground">
                      {item.query}
                    </span>
                    <span className="mt-0.5 flex flex-wrap gap-1 text-[10px] tabular-nums text-muted-foreground">
                      <span>
                        {t("knowledge.retrieval.historyMeta", {
                          count: item.hitCount,
                          ms: item.durationMs,
                        })}
                      </span>
                      <span>{item.mode}</span>
                      <span>{item.createdAt.slice(5, 16).replace("T", " ")}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      {panelOpen ? (
        <div className="mt-3 flex flex-col gap-3">
          <RetrievalTraceCanvas
            trace={result?.trace ?? null}
            loading={searching}
            collapsed={canvasCollapsed}
            onToggleCollapsed={() => setCanvasCollapsed((value) => !value)}
            onSelectStage={(stage) => setActiveStage(stage)}
          />
          <div className="rounded-panel border border-border/60 bg-background p-4">
            <h3 className="pb-2 text-sm font-semibold text-foreground">
              {t("knowledge.retrieval.results")}
            </h3>
            {error ? (
              <div
                role="alert"
                className="rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
              >
                {t("knowledge.searchError")}: {error}
              </div>
            ) : searching ? (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 knowledge-motion animate-spin" aria-hidden />
                {t("knowledge.searching")}
              </p>
            ) : result ? (
              <KnowledgeSearchResults
                result={result}
                docsById={docsById}
                onLocate={onLocateDoc}
                emptyLabel={
                  result.reason
                    ? t("knowledge.searchFiltered", { reason: result.reason })
                    : t("knowledge.searchEmpty")
                }
              />
            ) : null}
          </div>
        </div>
      ) : null}

      <KnowledgeDialog open={!!activeStage} onOpenChange={(open) => { if (!open) setActiveStage(null); }}>
        <KnowledgeDialogContent className="max-h-[80vh] overflow-y-auto">
          {activeStage ? (
            <>
              <KnowledgeDialogTitle>{retrievalStageTitle(t, activeStage.kind)}</KnowledgeDialogTitle>
              <KnowledgeDialogDescription className="mt-1">
                {t("knowledge.retrieval.detailSubtitle", {
                  status: t(
                    `knowledge.retrieval.status${activeStage.status.charAt(0).toUpperCase()}${activeStage.status.slice(1)}`,
                    { defaultValue: activeStage.status },
                  ),
                  ms: activeStage.durationMs,
                })}
              </KnowledgeDialogDescription>
              <div className="mt-4 flex flex-col gap-3 text-sm">
                <StageMetrics stage={activeStage} />
                <StageChips stage={activeStage} />
                <StageScoreTables stage={activeStage} />
                <StageScalars stage={activeStage} />
              </div>
            </>
          ) : null}
        </KnowledgeDialogContent>
      </KnowledgeDialog>
    </section>
  );
}

const CHIP_KEYS: Array<{ key: "tokens" | "variants" | "entities" | "labels" | "degraded" | "routes" }> = [
  { key: "tokens" },
  { key: "variants" },
  { key: "entities" },
  { key: "labels" },
  { key: "degraded" },
  { key: "routes" },
];

function StageMetrics({ stage }: { stage: KnowledgeTraceStage }) {
  const { t } = useTranslation();
  const entries = Object.entries(stage.metrics);
  if (entries.length === 0) return null;
  return (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {entries.map(([key, value]) => (
        <div key={key} className="rounded-control border border-border/60 bg-muted/10 px-3 py-2">
          <dt className="text-[10px] text-muted-foreground">
            {t(`knowledge.retrieval.metric${key.charAt(0).toUpperCase()}${key.slice(1)}`, {
              defaultValue: key,
            })}
          </dt>
          <dd className="mt-0.5 text-sm tabular-nums text-foreground">
            {value === null ? "—" : typeof value === "boolean" ? (value ? "on" : "off") : String(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function StageChips({ stage }: { stage: KnowledgeTraceStage }) {
  const { t } = useTranslation();
  const detail = stage.detail ?? {};
  const rows = CHIP_KEYS.flatMap(({ key }) => {
    const values = detail[key];
    if (!Array.isArray(values) || values.length === 0) return [];
    return [{ key, values: values.map(String) }];
  });
  if (rows.length === 0) return null;
  return (
    <div className="flex flex-col gap-2">
      {rows.map(({ key, values }) => (
        <div key={key}>
          <p className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">{key}</p>
          <div className="flex flex-wrap gap-1">
            {values.map((value, index) => (
              <span
                key={`${key}-${index}-${value}`}
                className="rounded-full border border-border/60 px-2 py-0.5 text-[11px] text-foreground"
              >
                {value}
              </span>
            ))}
          </div>
        </div>
      ))}
      {typeof detail.fallback === "boolean" ? (
        <p className="text-[11px] text-muted-foreground">
          {t("knowledge.retrieval.detailFallback", {
            value: detail.fallback ? t("knowledge.retrieval.fallbackYes") : t("knowledge.retrieval.fallbackNo"),
          })}
        </p>
      ) : null}
    </div>
  );
}

function StageScoreTables({ stage }: { stage: KnowledgeTraceStage }) {
  const { t } = useTranslation();
  const detail = stage.detail ?? {};
  const top = Array.isArray(detail.top) ? detail.top : [];
  const scores = Array.isArray(detail.scores) ? detail.scores : [];
  return (
    <>
      {top.length > 0 ? (
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
            {t("knowledge.retrieval.detailTop")}
          </p>
          <table className="w-full border-collapse text-[11px]">
            <thead>
              <tr className="text-muted-foreground">
                <th className="border-b border-border/60 py-1 text-left font-medium">
                  {t("knowledge.retrieval.detailChunk")}
                </th>
                <th className="border-b border-border/60 py-1 text-right font-medium">
                  {t("knowledge.retrieval.detailScore")}
                </th>
              </tr>
            </thead>
            <tbody>
              {top.map((row, index) => (
                <tr key={`${row.chunkId}-${index}`}>
                  <td className="border-b border-border/30 py-1 font-mono text-[10.5px] text-foreground">
                    {row.chunkId}
                  </td>
                  <td className="border-b border-border/30 py-1 text-right tabular-nums text-foreground">
                    {row.score}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {scores.length > 0 ? (
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
            {t("knowledge.retrieval.detailRerank")}
          </p>
          <table className="w-full border-collapse text-[11px]">
            <thead>
              <tr className="text-muted-foreground">
                <th className="border-b border-border/60 py-1 text-left font-medium">
                  {t("knowledge.retrieval.detailChunk")}
                </th>
                <th className="border-b border-border/60 py-1 text-right font-medium">
                  {t("knowledge.retrieval.detailBefore")}
                </th>
                <th className="border-b border-border/60 py-1 text-right font-medium">
                  {t("knowledge.retrieval.detailAfter")}
                </th>
                <th className="border-b border-border/60 py-1 text-right font-medium">
                  {t("knowledge.retrieval.detailDelta")}
                </th>
              </tr>
            </thead>
            <tbody>
              {scores.map((row, index) => (
                <tr key={`${row.chunkId}-${index}`}>
                  <td className="border-b border-border/30 py-1 font-mono text-[10.5px] text-foreground">
                    {row.chunkId}
                  </td>
                  <td className="border-b border-border/30 py-1 text-right tabular-nums text-muted-foreground">
                    {row.before ?? "—"}
                  </td>
                  <td className="border-b border-border/30 py-1 text-right tabular-nums text-foreground">
                    {row.after}
                  </td>
                  <td className="border-b border-border/30 py-1 text-right tabular-nums text-muted-foreground">
                    {row.delta ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </>
  );
}

function StageScalars({ stage }: { stage: KnowledgeTraceStage }) {
  const detail = stage.detail ?? {};
  const scalars = [
    ["modeUsed", detail.modeUsed],
    ["outcome", detail.outcome],
    ["model", detail.model],
    ["reason", detail.reason],
  ].filter(([, value]) => typeof value === "string" && value);
  if (scalars.length === 0) return null;
  return (
    <dl className="flex flex-col gap-1">
      {scalars.map(([key, value]) => (
        <div key={String(key)} className="flex items-baseline gap-2 text-[11.5px]">
          <dt className="text-muted-foreground">{String(key)}</dt>
          <dd className="font-mono text-foreground">{String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}
