import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ArrowLeft, Loader2, Search, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  KnowledgeDialog,
  KnowledgeDialogContent,
  KnowledgeDialogDescription,
  KnowledgeDialogTitle,
} from "../knowledge-shared/KnowledgeSurfaces";
import { KnowledgeSearchResults } from "./KnowledgeSearchResults";
import { knowledgeModeLabel } from "./knowledge-labels";
import {
  RetrievalTraceCanvas,
  retrievalStageStatus,
  retrievalStageTitle,
} from "./RetrievalTraceCanvas";
import { formatCompactDuration, relativeTime } from "@/lib/format";
import {
  clearKnowledgeQueryHistory,
  fetchKnowledgeDocs,
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

export function KnowledgeRetrievalPage({
  token,
  transport,
  scope,
  kbs,
  initialKbId,
  onBack,
  onOpenDoc,
}: {
  token: string;
  transport: WebUIMutationTransport;
  scope?: string[];
  kbs: KnowledgeKb[];
  initialKbId?: string;
  onBack: () => void;
  onOpenDoc: (doc: KnowledgeDoc) => void;
}) {
  const { t } = useTranslation();
  const [selectedKbId, setSelectedKbId] = useState(initialKbId ?? kbs[0]?.id ?? "");
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [selectedDocId, setSelectedDocId] = useState("");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<KnowledgeSearchMode>("hybrid");
  const [limit, setLimit] = useState(8);
  const [searching, setSearching] = useState(false);
  const [result, setResult] = useState<KnowledgeSearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<KnowledgeQueryHistoryItem[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [canvasCollapsed, setCanvasCollapsed] = useState(
    () => typeof window !== "undefined" && window.innerWidth < 1024,
  );
  const [activeStage, setActiveStage] = useState<KnowledgeTraceStage | null>(null);
  const seq = useRef(0);
  const busy = useRef(false);

  const docsById = useMemo(() => {
    const map = new Map<string, KnowledgeDoc>();
    for (const doc of docs) map.set(doc.id, doc);
    return map;
  }, [docs]);

  const loadHistory = useCallback(async () => {
    if (!selectedKbId) {
      setHistory([]);
      return;
    }
    try {
      const payload = await fetchKnowledgeQueryHistory(token, { kbId: selectedKbId, limit: 20 });
      setHistory(payload.items);
      setHistoryError(null);
    } catch (reason: unknown) {
      setHistoryError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [selectedKbId, token]);

  useEffect(() => {
    let cancelled = false;
    if (!selectedKbId) {
      setDocs([]);
      setSelectedDocId("");
      return;
    }
    setDocsLoading(true);
    fetchKnowledgeDocs(token, { kbId: selectedKbId, workspaceIds: scope, limit: 200 })
      .then((payload) => {
        if (cancelled) return;
        setDocs(payload.documents);
        setSelectedDocId("");
      })
      .catch(() => {
        if (!cancelled) setDocs([]);
      })
      .finally(() => {
        if (!cancelled) setDocsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scope, selectedKbId, token]);

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
          kbId: selectedKbId || undefined,
          docIds: selectedDocId ? [selectedDocId] : undefined,
          workspaceIds: scope,
          trace: true,
          record: Boolean(selectedKbId),
        });
        if (current !== seq.current) return;
        setResult(payload);
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
    [loadHistory, scope, selectedDocId, selectedKbId, token],
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
    if (!selectedKbId) return;
    setHistoryError(null);
    try {
      await clearKnowledgeQueryHistory(transport, selectedKbId);
      setHistory([]);
    } catch (reason: unknown) {
      setHistoryError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [selectedKbId, transport]);

  const locateDoc = useCallback(
    (docId: string) => {
      const doc = docsById.get(docId);
      if (doc) {
        onOpenDoc(doc);
        return;
      }
      const hit = result?.hits.find((item) => item.docId === docId);
      onOpenDoc({
        id: docId,
        kbId: hit?.kbId ?? selectedKbId,
        title: docId,
        originalName: "",
        status: "ready",
        chunkCount: 0,
        charCount: 0,
      });
    },
    [docsById, onOpenDoc, result, selectedKbId],
  );

  const scopeChanged = useCallback(() => {
    seq.current += 1;
    busy.current = false;
    setResult(null);
    setError(null);
  }, []);

  const hitDocIds = useMemo(() => {
    const map = new Map<string, string>();
    for (const hit of result?.hits ?? []) map.set(hit.chunkId, hit.docId);
    return map;
  }, [result]);

  const resultMeta = result
    ? [
        t("knowledge.hitCount", { count: result.hits.length }),
        result.durationMs ? formatCompactDuration(result.durationMs) : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : null;

  return (
    <div className="flex min-w-0 flex-col gap-3" data-testid="knowledge-retrieval-page">
      <div className="flex min-w-0 flex-col gap-1">
        <button
          type="button"
          onClick={onBack}
          aria-label={t("knowledge.experience.backToLibraries", { defaultValue: "返回知识库" })}
          className="inline-flex w-fit items-center gap-1 rounded-control text-[12px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          {t("knowledge.experience.backToLibraries", { defaultValue: "返回知识库" })}
        </button>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-base font-semibold text-foreground">{t("knowledge.searchTitle")}</h1>
          <p className="text-[12px] text-muted-foreground">{t("knowledge.retrieval.subtitle")}</p>
        </div>
      </div>

      <div className="flex flex-col gap-3 lg:grid lg:grid-cols-[320px_minmax(0,1fr)] lg:items-start">
        <div className="contents lg:flex lg:min-w-0 lg:flex-col lg:gap-3">
        <aside className="order-1 flex min-w-0 flex-col rounded-panel border border-border/70 bg-card lg:order-none">
          <form
            className="flex flex-col gap-3 border-b border-border/60 p-4"
            onSubmit={(event) => {
              event.preventDefault();
              void runSearch({ query, mode, limit });
            }}
          >
            <label className="flex flex-col gap-1 text-[12px] text-muted-foreground">
              {t("knowledge.query")}
              <Input
                aria-label={t("knowledge.query")}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("knowledge.queryPlaceholder")}
                className="h-9 text-sm"
              />
            </label>
            <div className="flex items-end gap-2">
              <label className="flex min-w-0 flex-1 flex-col gap-1 text-[12px] text-muted-foreground">
                {t("knowledge.mode")}
                <select
                  aria-label={t("knowledge.mode")}
                  value={mode}
                  onChange={(event) => setMode(event.target.value as KnowledgeSearchMode)}
                  className="h-9 min-w-0 rounded-control border border-input bg-background px-2 text-sm"
                >
                  {SEARCH_MODES.map((value) => (
                    <option key={value} value={value}>
                      {t(`knowledge.mode${value.charAt(0).toUpperCase()}${value.slice(1)}`)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex w-[4.75rem] shrink-0 flex-col gap-1 text-[12px] text-muted-foreground">
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
                className="h-9 shrink-0 gap-1.5 px-3.5 text-sm font-medium disabled:bg-muted disabled:text-muted-foreground"
              >
                {searching ? (
                  <Loader2 className="h-3.5 w-3.5 knowledge-motion animate-spin" aria-hidden />
                ) : (
                  <Search className="h-3.5 w-3.5" aria-hidden />
                )}
                {t("knowledge.search")}
              </Button>
            </div>
          </form>

          <section className="flex flex-col gap-2 border-b border-border/60 p-4">
            <h2 className="text-[12px] font-medium text-foreground">
              {t("knowledge.retrieval.scope")}
            </h2>
            <label className="flex flex-col gap-1 text-[12px] text-muted-foreground">
              {t("knowledge.retrieval.scopeKb")}
              <select
                aria-label={t("knowledge.retrieval.scopeKb")}
                value={selectedKbId}
                onChange={(event) => {
                  setSelectedKbId(event.target.value);
                  scopeChanged();
                }}
                className="h-9 rounded-control border border-input bg-background px-2 text-sm"
              >
                <option value="">{t("knowledge.retrieval.allKbs")}</option>
                {kbs.map((kb) => (
                  <option key={kb.id} value={kb.id}>
                    {kb.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[12px] text-muted-foreground">
              {t("knowledge.retrieval.scopeDoc")}
              <select
                aria-label={t("knowledge.retrieval.scopeDoc")}
                value={selectedDocId}
                onChange={(event) => {
                  setSelectedDocId(event.target.value);
                  scopeChanged();
                }}
                disabled={!selectedKbId || docsLoading}
                className="h-9 rounded-control border border-input bg-background px-2 text-sm disabled:opacity-60"
              >
                <option value="">{t("knowledge.retrieval.allDocs")}</option>
                {docs.map((doc) => (
                  <option key={doc.id} value={doc.id}>
                    {doc.title || doc.originalName || doc.id}
                  </option>
                ))}
              </select>
            </label>
          </section>

        </aside>

        <section className="order-3 flex min-w-0 flex-col rounded-panel border border-border/70 bg-card p-4 lg:order-none">
          <header className="flex items-center gap-2 pb-2">
            <h2 className="text-[12px] font-medium text-foreground">
              {t("knowledge.retrieval.history")}
            </h2>
            {history.length > 0 ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={t("knowledge.retrieval.historyClear")}
                title={t("knowledge.retrieval.historyClear")}
                onClick={() => void clearHistory()}
                className="ml-auto h-6 w-6 p-0 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
              </Button>
            ) : null}
          </header>
          <div className="max-h-56 overflow-y-auto">
            {!selectedKbId ? (
              <p className="px-1 py-2 text-[12px] text-muted-foreground">
                {t("knowledge.retrieval.historyKbHint")}
              </p>
            ) : historyError ? (
              <p role="alert" className="px-1 py-2 text-[12px] text-destructive">
                {historyError}
              </p>
            ) : history.length === 0 ? (
              <p className="px-1 py-2 text-[12px] text-muted-foreground">
                {t("knowledge.retrieval.historyEmpty")}
              </p>
            ) : (
              <ul className="flex flex-col">
                {history.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => runFromHistory(item)}
                      className="w-full rounded-control px-1.5 py-1.5 text-left transition-colors hover:bg-muted/40"
                    >
                      <span className="block truncate text-[12.5px] text-foreground">
                        {item.query}
                      </span>
                      <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[10.5px] text-muted-foreground">
                        <span>{knowledgeModeLabel(t, item.mode)}</span>
                        <span className="tabular-nums">
                          {t("knowledge.retrieval.historyMeta", {
                            count: item.hitCount,
                            duration: formatCompactDuration(item.durationMs),
                          })}
                        </span>
                        <span>{relativeTime(item.createdAt)}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
        </div>

        <div className="order-2 flex min-w-0 flex-col gap-3 lg:order-none">
          <RetrievalTraceCanvas
            trace={result?.trace ?? null}
            loading={searching}
            collapsed={canvasCollapsed}
            onToggleCollapsed={() => setCanvasCollapsed((value) => !value)}
            onSelectStage={(stage) => setActiveStage(stage)}
          />

          <section className="flex min-h-[260px] flex-col rounded-panel border border-border/60 bg-card p-4">
            <header className="flex flex-wrap items-baseline gap-x-2 gap-y-1 pb-3">
              <h2 className="text-[13px] font-semibold text-foreground">
                {t("knowledge.retrieval.results")}
              </h2>
              {resultMeta ? (
                <span className="text-[11.5px] tabular-nums text-muted-foreground">
                  {resultMeta}
                </span>
              ) : null}
            </header>
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
            ) : !result ? (
              <div className="flex flex-1 flex-col items-start justify-center gap-1 py-10">
                <p className="text-sm text-foreground">{t("knowledge.searchIdle")}</p>
                <p className="max-w-md text-[12px] leading-relaxed text-muted-foreground">
                  {t("knowledge.retrieval.idleHint")}
                </p>
              </div>
            ) : (
              <KnowledgeSearchResults
                result={result}
                docsById={docsById}
                onLocate={locateDoc}
                emptyLabel={
                  result.reason
                    ? t("knowledge.searchFiltered", { reason: result.reason })
                    : t("knowledge.searchEmpty")
                }
              />
            )}
          </section>
        </div>

      </div>

      <KnowledgeDialog open={!!activeStage} onOpenChange={(open) => { if (!open) setActiveStage(null); }}>
        <KnowledgeDialogContent className="knowledge-stage-dialog">
          {activeStage ? (
            <>
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <KnowledgeDialogTitle className="text-[17px]">
                  {retrievalStageTitle(t, activeStage.kind)}
                </KnowledgeDialogTitle>
                <span
                  className={
                    activeStage.status === "error"
                      ? "text-[11.5px] text-destructive"
                      : activeStage.status === "degraded"
                        ? "text-[11.5px] text-amber-600 dark:text-amber-300"
                        : "text-[11.5px] text-muted-foreground"
                  }
                >
                  {retrievalStageStatus(t, activeStage.status)}
                </span>
                {activeStage.durationMs > 0 ? (
                  <span className="text-[11.5px] tabular-nums text-muted-foreground">
                    {formatCompactDuration(activeStage.durationMs)}
                  </span>
                ) : null}
              </div>
              <KnowledgeDialogDescription className="mt-1 text-[12px] leading-relaxed">
                {stageDescription(t, activeStage.kind)}
              </KnowledgeDialogDescription>
              <div className="mt-4 flex flex-col gap-4 pb-2">
                <StageMetrics stage={activeStage} />
                <StageValues stage={activeStage} />
                <StageScoreTables
                  stage={activeStage}
                  docsById={docsById}
                  hitDocIds={hitDocIds}
                />
                <StageScalars stage={activeStage} />
              </div>
            </>
          ) : null}
        </KnowledgeDialogContent>
      </KnowledgeDialog>
    </div>
  );
}

const STAGE_DESCRIPTION_KEYS: Record<string, string> = {
  scope: "knowledge.retrieval.descScope",
  query: "knowledge.retrieval.descQuery",
  rewrite: "knowledge.retrieval.descRewrite",
  vector: "knowledge.retrieval.descVector",
  keyword: "knowledge.retrieval.descKeyword",
  expansion: "knowledge.retrieval.descExpansion",
  graph: "knowledge.retrieval.descGraph",
  docFilter: "knowledge.retrieval.descDocFilter",
  fusion: "knowledge.retrieval.descFusion",
  dedup: "knowledge.retrieval.descDedup",
  rerank: "knowledge.retrieval.descRerank",
  mmr: "knowledge.retrieval.descMmr",
};

const METRIC_LABEL_KEYS: Record<string, string> = {
  tokens: "knowledge.retrieval.metricTokens",
  variants: "knowledge.retrieval.metricVariants",
  groups: "knowledge.retrieval.metricGroups",
  candidates: "knowledge.retrieval.metricCandidates",
  raw: "knowledge.retrieval.metricRaw",
  entities: "knowledge.retrieval.metricEntities",
  seeds: "knowledge.retrieval.metricSeeds",
  kbs: "knowledge.retrieval.metricKbs",
  merged: "knowledge.retrieval.metricMerged",
  before: "knowledge.retrieval.metricBefore",
  after: "knowledge.retrieval.metricAfter",
  removed: "knowledge.retrieval.metricRemoved",
  pool: "knowledge.retrieval.metricPool",
  kept: "knowledge.retrieval.metricKept",
  added: "knowledge.retrieval.metricAdded",
  vectorWeight: "knowledge.retrieval.metricVectorWeight",
  keywordWeight: "knowledge.retrieval.metricKeywordWeight",
  rrfK: "knowledge.retrieval.metricRrfK",
  vectorCandidates: "knowledge.retrieval.metricVectorCandidates",
  keywordCandidates: "knowledge.retrieval.metricKeywordCandidates",
  graphCandidates: "knowledge.retrieval.metricGraphCandidates",
  candidatesIn: "knowledge.retrieval.metricCandidatesIn",
  candidatesKept: "knowledge.retrieval.metricCandidatesKept",
  threshold: "knowledge.retrieval.metricThreshold",
  lambda: "knowledge.retrieval.metricLambda",
  visibleKbs: "knowledge.retrieval.metricVisibleKbs",
  enabled: "knowledge.retrieval.metricEnabled",
};

const CHIP_KEYS: Array<{ key: string; labelKey: string }> = [
  { key: "tokens", labelKey: "knowledge.retrieval.metricTokens" },
  { key: "variants", labelKey: "knowledge.retrieval.metricVariants" },
  { key: "entities", labelKey: "knowledge.retrieval.metricEntities" },
  { key: "labels", labelKey: "knowledge.retrieval.chipGraphLabels" },
  { key: "degraded", labelKey: "knowledge.retrieval.statusDegraded" },
  { key: "routes", labelKey: "knowledge.retrieval.chipRoutes" },
];

function stageDescription(t: TFunction, kind: string): string {
  const key = STAGE_DESCRIPTION_KEYS[kind];
  return key ? t(key, { defaultValue: "" }) : "";
}

function formatMetric(
  t: TFunction,
  key: string,
  value: number | string | boolean | null,
): string {
  if (value === null) return "—";
  if (typeof value === "boolean") {
    return value
      ? t("knowledge.retrieval.fallbackYes")
      : t("knowledge.retrieval.fallbackNo");
  }
  if (typeof value === "number") {
    if (["vectorWeight", "keywordWeight", "threshold", "lambda"].includes(key)) {
      return value.toFixed(2);
    }
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  return String(value);
}

function shortChunkId(chunkId: string): string {
  return chunkId.length > 16 ? `…${chunkId.slice(-8)}` : chunkId;
}

function StageMetrics({ stage }: { stage: KnowledgeTraceStage }) {
  const { t } = useTranslation();
  const entries = Object.entries(stage.metrics);
  if (entries.length === 0) return null;
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-2.5 sm:grid-cols-3">
      {entries.map(([key, value]) => (
        <div key={key} className="min-w-0">
          <dt className="text-[11px] text-muted-foreground">
            {METRIC_LABEL_KEYS[key]
              ? t(METRIC_LABEL_KEYS[key], { defaultValue: key })
              : key}
          </dt>
          <dd className="mt-0.5 text-[14px] tabular-nums text-foreground">
            {formatMetric(t, key, value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function StageValues({ stage }: { stage: KnowledgeTraceStage }) {
  const { t } = useTranslation();
  const detail = stage.detail ?? {};
  const rows = CHIP_KEYS.flatMap(({ key, labelKey }) => {
    const values = detail[key];
    if (!Array.isArray(values) || values.length === 0) return [];
    return [{ key, labelKey, values: values.map(String) }];
  });
  if (rows.length === 0 && typeof detail.fallback !== "boolean") return null;
  return (
    <div className="flex flex-col gap-2.5">
      {rows.map(({ key, labelKey, values }) => (
        <div key={key}>
          <p className="mb-1 text-[11px] text-muted-foreground">{t(labelKey)}</p>
          <div className="flex flex-wrap gap-1">
            {values.map((value, index) => (
              <span
                key={`${key}-${index}-${value}`}
                className="rounded bg-muted px-1.5 py-0.5 text-[11.5px] text-foreground"
              >
                {value}
              </span>
            ))}
          </div>
        </div>
      ))}
      {typeof detail.fallback === "boolean" ? (
        <p className="text-[11.5px] text-muted-foreground">
          {t("knowledge.retrieval.detailFallback", {
            value: detail.fallback ? t("knowledge.retrieval.fallbackYes") : t("knowledge.retrieval.fallbackNo"),
          })}
        </p>
      ) : null}
    </div>
  );
}

function StageScoreTables({
  stage,
  docsById,
  hitDocIds,
}: {
  stage: KnowledgeTraceStage;
  docsById: Map<string, KnowledgeDoc>;
  hitDocIds: Map<string, string>;
}) {
  const { t } = useTranslation();
  const detail = stage.detail ?? {};
  const top = Array.isArray(detail.top) ? detail.top : [];
  const scores = Array.isArray(detail.scores) ? detail.scores : [];
  const docTitle = (docId: string | undefined): string | null =>
    docId ? docsById.get(docId)?.title ?? docId : null;

  return (
    <>
      {top.length > 0 ? (
        <div>
          <p className="mb-1.5 text-[11px] text-muted-foreground">
            {t("knowledge.retrieval.detailTop")}
          </p>
          <table className="w-full border-collapse text-[11.5px]">
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
                  <td className="border-b border-border/30 py-1.5">
                    {docTitle(row.docId ?? hitDocIds.get(row.chunkId)) ? (
                      <span className="block max-w-[20rem] truncate text-foreground">
                        {docTitle(row.docId ?? hitDocIds.get(row.chunkId))}
                      </span>
                    ) : null}
                    <span
                      className="font-mono text-[10.5px] text-muted-foreground"
                      title={row.chunkId}
                    >
                      {shortChunkId(row.chunkId)}
                    </span>
                  </td>
                  <td className="border-b border-border/30 py-1.5 text-right tabular-nums text-foreground">
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
          <p className="mb-1.5 text-[11px] text-muted-foreground">
            {t("knowledge.retrieval.detailRerank")}
          </p>
          <table className="w-full border-collapse text-[11.5px]">
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
              {scores.map((row, index) => {
                const title = docTitle(hitDocIds.get(row.chunkId));
                return (
                  <tr key={`${row.chunkId}-${index}`}>
                    <td className="border-b border-border/30 py-1.5">
                      {title ? (
                        <span className="block max-w-[20rem] truncate text-foreground">
                          {title}
                        </span>
                      ) : null}
                      <span
                        className="font-mono text-[10.5px] text-muted-foreground"
                        title={row.chunkId}
                      >
                        {shortChunkId(row.chunkId)}
                      </span>
                    </td>
                    <td className="border-b border-border/30 py-1.5 text-right tabular-nums text-muted-foreground">
                      {row.before ?? "—"}
                    </td>
                    <td className="border-b border-border/30 py-1.5 text-right tabular-nums text-foreground">
                      {row.after}
                    </td>
                    <td className="border-b border-border/30 py-1.5 text-right tabular-nums text-muted-foreground">
                      {row.delta ?? "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </>
  );
}

function StageScalars({ stage }: { stage: KnowledgeTraceStage }) {
  const { t } = useTranslation();
  const detail = stage.detail ?? {};
  const scalars = [
    ["knowledge.retrieval.detailMode", detail.modeUsed],
    ["knowledge.retrieval.detailOutcome", detail.outcome],
    ["knowledge.retrieval.detailModel", detail.model],
    ["knowledge.retrieval.detailReason", detail.reason],
  ].filter(([, value]) => typeof value === "string" && value);
  if (scalars.length === 0) return null;
  return (
    <dl className="flex flex-col gap-1.5 border-t border-border/60 pt-3">
      {scalars.map(([labelKey, value]) => (
        <div key={String(labelKey)} className="flex items-baseline gap-2 text-[11.5px]">
          <dt className="w-20 shrink-0 text-muted-foreground">{t(String(labelKey))}</dt>
          <dd className="min-w-0 break-all font-mono text-foreground">{String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}
