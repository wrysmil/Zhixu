import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Network, RefreshCcw, Search, ShieldAlert, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  fetchKnowledgeGraphEntities,
  fetchKnowledgeGraphEntity,
  fetchKnowledgeGraphStats,
  fetchKnowledgeGraphSubgraph,
  knowledgeGraphExtract,
  knowledgeGraphRebuild,
  type KnowledgeGraphEdge,
  type KnowledgeGraphEntity,
  type KnowledgeGraphEntityDetail,
  type KnowledgeGraphNode,
  type KnowledgeGraphStats,
  type WebUIMutationTransport,
} from "@/lib/api";
import { cn } from "@/lib/utils";

import { GraphView } from "./GraphView";

type ConfirmKind = "build" | "rebuild";

interface GraphSubgraph {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
}

interface GraphPanelApi {
  stats(kbId: string): Promise<KnowledgeGraphStats>;
  entities(params: {
    kbId: string;
    q?: string;
    type?: string;
  }): Promise<{ entities: KnowledgeGraphEntity[]; total: number }>;
  entity(id: string): Promise<KnowledgeGraphEntityDetail>;
  subgraph(params: {
    entityId: string;
    hops?: number;
    limit?: number;
  }): Promise<GraphSubgraph>;
  extract(payload: {
    kbId?: string;
    docId?: string;
    force?: boolean;
    confirm?: boolean;
  }): Promise<{ queued: number; docs: string[] }>;
  rebuild(payload: {
    kbId: string;
    purge?: boolean;
    confirm?: boolean;
  }): Promise<{ queued: number; docs?: string[] }>;
}

export interface GraphPanelProps {
  kbId: string;
  token: string;
  client: WebUIMutationTransport;
  api?: GraphPanelApi;
  onOpenChunk?: (docId: string, chunkId: string) => void;
}

function createGraphApi(
  token: string,
  transport: WebUIMutationTransport,
): GraphPanelApi {
  return {
    stats: (kbId) => fetchKnowledgeGraphStats(token, kbId),
    entities: (params) => fetchKnowledgeGraphEntities(token, params),
    entity: (id) => fetchKnowledgeGraphEntity(token, id),
    subgraph: (params) => fetchKnowledgeGraphSubgraph(token, params),
    extract: (payload) => knowledgeGraphExtract(transport, payload),
    rebuild: (payload) => knowledgeGraphRebuild(transport, payload),
  };
}

export function GraphPanel({
  kbId,
  token,
  client,
  api,
  onOpenChunk,
}: GraphPanelProps) {
  const { t } = useTranslation();
  const graphApi = useMemo(
    () => api ?? createGraphApi(token, client),
    [api, token, client],
  );

  const [stats, setStats] = useState<KnowledgeGraphStats | null>(null);
  const [entities, setEntities] = useState<KnowledgeGraphEntity[]>([]);
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [detail, setDetail] = useState<KnowledgeGraphEntityDetail | null>(null);
  const [subgraph, setSubgraph] = useState<GraphSubgraph | null>(null);
  const [hops, setHops] = useState(1);
  const [confirm, setConfirm] = useState<ConfirmKind | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [detailError, setDetailError] = useState("");
  const [graphError, setGraphError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphTarget, setGraphTarget] = useState({ entityId: "", hops: 1 });
  const detailRequest = useRef(0);
  const graphRequest = useRef(0);
  const statsRequest = useRef(0);
  const panelRef = useRef<HTMLDivElement>(null);
  const entityTrigger = useRef<HTMLButtonElement | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setSelectedId("");
    setDetail(null);
    setSubgraph(null);
    setDetailError("");
    setGraphError("");
    setDetailLoading(false);
    setGraphLoading(false);
    setHops(1);
    setLoading(true);
    setStats(null);
    setEntities([]);
    setConfirm(null);
    return () => {
      detailRequest.current += 1;
      graphRequest.current += 1;
      statsRequest.current += 1;
    };
  }, [graphApi, kbId]);

  const loadStats = useCallback(async () => {
    const request = ++statsRequest.current;
    try {
      const next = await graphApi.stats(kbId);
      if (request !== statsRequest.current) return;
      setStats(next);
      setError("");
    } catch (reason) {
      if (request !== statsRequest.current) return;
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (request === statsRequest.current) setLoading(false);
    }
  }, [graphApi, kbId]);

  useEffect(() => {
    void loadStats();
  }, [loadStats]);

  useEffect(() => {
    let cancelled = false;
    void graphApi
      .entities({ kbId, q: query, type })
      .then((page) => {
        if (!cancelled) setEntities(page.entities);
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [graphApi, kbId, query, type]);

  const types = useMemo(
    () => Array.from(new Set(entities.map((entity) => entity.type))).sort(),
    [entities],
  );

  const expandEntity = useCallback(
    async (entityId: string, nextHops = hops) => {
      const request = ++graphRequest.current;
      setGraphTarget({ entityId, hops: nextHops });
      setGraphError("");
      setGraphLoading(true);
      try {
        const next = await graphApi.subgraph({ entityId, hops: nextHops, limit: 50 });
        if (request === graphRequest.current) setSubgraph(next);
      } catch (reason) {
        if (request === graphRequest.current) setGraphError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (request === graphRequest.current) setGraphLoading(false);
      }
    },
    [graphApi, hops],
  );

  const openEntity = useCallback(
    async (entityId: string, loadGraph = true) => {
      const request = ++detailRequest.current;
      setSelectedId(entityId);
      setDetail(null);
      setDetailError("");
      setDetailLoading(true);
      if (loadGraph) {
        setSubgraph(null);
        void expandEntity(entityId);
      }
      try {
        const next = await graphApi.entity(entityId);
        if (request === detailRequest.current) setDetail(next);
      } catch (reason) {
        if (request === detailRequest.current) setDetailError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (request === detailRequest.current) setDetailLoading(false);
      }
    },
    [graphApi, expandEntity],
  );

  const changeHops = useCallback(
    (next: number) => {
      setHops(next);
      if (selectedId) void expandEntity(selectedId, next);
    },
    [selectedId, expandEntity],
  );

  const runAction = useCallback(async () => {
    if (!confirm) return;
    setBusy(true);
    try {
      if (confirm === "rebuild") {
        await graphApi.rebuild({ kbId, purge: false, confirm: true });
      } else {
        await graphApi.extract({ kbId, confirm: true });
      }
      setConfirm(null);
      await loadStats();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }, [confirm, graphApi, kbId, loadStats]);

  if (loading) {
    return (
      <p role="status" className="flex items-center gap-2 p-4 text-[12px] text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden />
        {t("knowledge.loading")}
      </p>
    );
  }

  const disabled = !stats?.enabled || stats?.status === "disabled";
  const degraded = stats?.status === "degraded" && stats.reason !== "disabled";

  return (
    <div ref={panelRef} className="flex min-w-0 flex-col gap-3">
      <GraphStatsCard
        stats={stats}
        disabled={disabled}
        onBuild={() => setConfirm("build")}
        onRebuild={() => setConfirm("rebuild")}
      />

      {degraded ? (
        <div
          role="status"
          className="flex items-center justify-between gap-3 rounded-control border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[12px] text-amber-700 dark:text-amber-300"
        >
          <span>
            {t("knowledge.graph.statusDegraded")}
            {stats?.reason ? `: ${stats.reason}` : ""}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 gap-1 rounded-full px-2 text-[12px]"
            onClick={() => void loadStats()}
          >
            <RefreshCcw className="h-3.5 w-3.5" aria-hidden />
            {t("knowledge.graph.retry")}
          </Button>
        </div>
      ) : null}

      {disabled ? (
        <div className="rounded-panel border border-dashed border-border/70 bg-background px-4 py-8 text-center">
          <Network className="mx-auto h-5 w-5 text-muted-foreground" aria-hidden />
          <p className="mt-2 text-[14px] font-medium text-foreground">
            {t("knowledge.graph.statusDisabled")}
          </p>
          <p className="mt-1 break-words text-[14px] text-muted-foreground">
            {t("knowledge.graph.disabledHint")}
          </p>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-end gap-2 rounded-panel border border-border/60 bg-background p-3">
            <label className="flex min-w-[180px] flex-1 flex-col gap-1 text-[12px] font-medium text-muted-foreground">
              {t("knowledge.graph.searchPlaceholder")}
              <Input
                ref={searchRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("knowledge.graph.searchPlaceholder")}
                className="h-8 text-[14px]"
              />
            </label>
            <label className="flex w-40 flex-col gap-1 text-[12px] font-medium text-muted-foreground">
              {t("knowledge.graph.filterType")}
              <select
                value={type}
                onChange={(event) => setType(event.target.value)}
                className="h-8 rounded-control border border-input bg-background px-2 text-[12px]"
              >
                <option value="">{t("knowledge.graph.allTypes")}</option>
                {types.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-8 gap-1.5 rounded-full px-3 text-[12px]"
              onClick={() => void loadStats()}
            >
              <Search className="h-3.5 w-3.5" aria-hidden />
              {t("knowledge.graph.retry")}
            </Button>
          </div>

          {entities.length === 0 ? (
            <div className="rounded-panel border border-dashed border-border/70 bg-background px-4 py-8 text-center">
              <p className="text-[14px] font-medium text-foreground">
                {t("knowledge.graph.empty")}
              </p>
              <p className="mt-1 text-[12px] text-muted-foreground">
                {t("knowledge.graph.emptyHint")}
              </p>
            </div>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {entities.map((entity) => (
                <button
                  key={entity.id}
                  type="button"
                  data-testid="graph-entity-card"
                  data-entity-id={entity.id}
                  aria-pressed={selectedId === entity.id}
                  onClick={(event) => {
                    entityTrigger.current = event.currentTarget;
                    void openEntity(entity.id);
                  }}
                  className={cn(
                    "flex flex-col gap-1 rounded-control border border-border/60 bg-background px-3 py-2 text-left transition-colors duration-[120ms] motion-reduce:transition-none",
                    "hover:border-primary/40 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    selectedId === entity.id && "border-foreground/40 bg-muted",
                  )}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="truncate text-[14px] font-medium text-foreground">
                      {entity.name}
                    </span>
                    <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[12px] text-muted-foreground">
                      {entity.type}
                    </span>
                  </span>
                  <span className="truncate text-[12px] text-muted-foreground">
                    {entity.description || t("knowledge.graph.otherType")}
                  </span>
                  <span className="text-[12px] text-muted-foreground">
                    {t("knowledge.graph.mentions")}: {entity.mentionCount}
                  </span>
                </button>
              ))}
            </div>
          )}

          {selectedId ? (
            <div className="rounded-panel border border-border/60 bg-background p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[12px] font-medium uppercase tracking-wide text-muted-foreground">
                    {t("knowledge.graph.entityDetail")}
                  </p>
                  <h3 className="mt-0.5 break-words text-[16px] font-semibold text-foreground">
                    {detail?.entity.name ?? entities.find((entity) => entity.id === selectedId)?.name ?? selectedId}
                  </h3>
                  <p className="mt-1 break-words text-[14px] text-muted-foreground">
                    {detail?.entity.description}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={t("knowledge.cancel")}
                  className="h-7 w-7 rounded-full p-0"
                  onClick={() => {
                    detailRequest.current += 1;
                    graphRequest.current += 1;
                    setSelectedId("");
                    setDetail(null);
                    setSubgraph(null);
                    const selectedTrigger = panelRef.current?.querySelector<HTMLButtonElement>(`[data-entity-id="${CSS.escape(selectedId)}"]`);
                    const fallback = entityTrigger.current?.isConnected ? entityTrigger.current : searchRef.current;
                    (selectedTrigger ?? fallback)?.focus();
                  }}
                >
                  <X className="h-3.5 w-3.5" aria-hidden />
                </Button>
              </div>

              {detailLoading ? <p role="status" className="mt-3 text-[12px] text-muted-foreground">{t('knowledge.experience.graphDetailLoading', { defaultValue: '正在读取实体详情…' })}</p> : null}
              {detailError ? <div role="alert" className="mt-3 flex flex-wrap items-center gap-2 text-[12px] text-destructive">
                <span>{detailError}</span>
                <Button variant="outline" size="sm" onClick={() => void openEntity(selectedId, false)}>{t('knowledge.experience.graphRetryDetail', { defaultValue: '重试实体详情' })}</Button>
              </div> : null}
              {graphLoading ? <p role="status" className="mt-3 text-[12px] text-muted-foreground">{t('knowledge.experience.graphLoading', { defaultValue: '正在加载关联图谱…' })}</p> : null}
              {graphError ? <div role="alert" className="mt-3 flex flex-wrap items-center gap-2 text-[12px] text-destructive">
                <span>{graphError}</span>
                <Button variant="outline" size="sm" onClick={() => void expandEntity(graphTarget.entityId, graphTarget.hops)}>{t('knowledge.experience.graphRetryGraph', { defaultValue: '重试关联图谱' })}</Button>
              </div> : null}
              {subgraph && !graphLoading && !graphError && subgraph.nodes.length === 0 ? <p role="status" className="mt-3 text-[12px] text-muted-foreground">{t('knowledge.experience.graphNoRelations', { defaultValue: '暂无关联节点，仍可查看下方文档证据' })}</p> : null}

              {subgraph && subgraph.nodes.length > 0 ? (
                <div className="mt-3">
                  <div className="mb-1.5 flex items-center justify-between gap-2">
                    <p className="text-[12px] font-medium text-muted-foreground">
                      {t("knowledge.graph.subgraph")}
                    </p>
                    <select
                      aria-label={t("knowledge.graph.subgraph")}
                      value={String(hops)}
                      onChange={(event) => void changeHops(Number(event.target.value))}
                      className="h-7 rounded-control border border-input bg-background px-2 text-[12px]"
                    >
                      <option value="1">
                        {t("knowledge.graph.hopCount", { count: 1 })}
                      </option>
                      <option value="2">
                        {t("knowledge.graph.hopCount", { count: 2 })}
                      </option>
                    </select>
                  </div>
                  <GraphView
                    nodes={subgraph.nodes}
                    edges={subgraph.edges}
                    centerId={graphTarget.entityId}
                    selectedId={selectedId}
                    onSelect={(entityId) => void openEntity(entityId, false)}
                    onExpand={(entityId) => void expandEntity(entityId)}
                  />
                </div>
              ) : null}

              {detail ? <div className="mt-3 grid gap-3 md:grid-cols-2">
                <div>
                  <p className="text-[12px] font-medium text-muted-foreground">
                    {t("knowledge.graph.neighbors")}
                  </p>
                  <ul className="mt-1 flex flex-col gap-1">
                    {detail.neighbors.length === 0 ? (
                      <li className="text-[12px] text-muted-foreground">—</li>
                    ) : (
                      detail.neighbors.map((neighbor) => (
                        <li
                          key={`${neighbor.entity.id}-${neighbor.relation}`}
                          className="flex items-center justify-between gap-2 rounded-control bg-muted/40 px-2 py-1 text-[12px]"
                        >
                          <span className="min-w-0 break-words text-[14px] text-foreground">
                            {neighbor.direction === "in"
                              ? t("knowledge.graph.directionIn")
                              : t("knowledge.graph.directionOut")}{" "}
                            {neighbor.entity.name}
                          </span>
                          <span className="shrink-0 text-muted-foreground">
                            {neighbor.relation} · {neighbor.weight.toFixed(1)}
                          </span>
                        </li>
                      ))
                    )}
                  </ul>
                </div>
                <div>
                  <p className="text-[12px] font-medium text-muted-foreground">
                    {t("knowledge.graph.mentions")}
                  </p>
                  <ul className="mt-1 flex flex-col gap-1">
                    {detail.mentions.length === 0 ? (
                      <li className="text-[12px] text-muted-foreground">—</li>
                    ) : (
                      detail.mentions.map((mention) => (
                        <li key={mention.chunkId}>
                          <button
                            type="button"
                            disabled={!onOpenChunk}
                            onClick={() => onOpenChunk?.(mention.docId, mention.chunkId)}
                            className="w-full break-words rounded-control bg-muted/40 px-2 py-1 text-left text-[14px] text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                          >
                            {mention.docTitle} · #{mention.chunkIndex + 1}
                          </button>
                        </li>
                      ))
                    )}
                  </ul>
                </div>
              </div> : null}
            </div>
          ) : null}
        </>
      )}

      {error ? (
        <p role="alert" className="text-[12px] text-destructive">
          {error}
        </p>
      ) : null}

      {confirm ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="knowledge-graph-confirm-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => (busy ? undefined : setConfirm(null))}
        >
          <div
            className="w-full max-w-[440px] rounded-panel border border-border bg-background p-5 shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center gap-2 text-foreground">
              <ShieldAlert className="h-4 w-4 text-amber-600" aria-hidden />
              <h2
                id="knowledge-graph-confirm-title"
                className="text-[14px] font-semibold"
              >
                {t("knowledge.graph.confirmTitle")}
              </h2>
            </div>
            <p className="mt-2 text-[12px] leading-relaxed text-muted-foreground">
              {t("knowledge.graph.confirmBody")}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => setConfirm(null)}
              >
                {t("knowledge.cancel")}
              </Button>
              <Button
                type="button"
                size="sm"
                data-testid="graph-confirm"
                disabled={busy}
                onClick={() => void runAction()}
              >
                {busy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden />
                ) : null}
                {t("knowledge.graph.confirmAction")}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function GraphStatsCard({
  stats,
  disabled,
  onBuild,
  onRebuild,
}: {
  stats: KnowledgeGraphStats | null;
  disabled: boolean;
  onBuild: () => void;
  onRebuild: () => void;
}) {
  const { t } = useTranslation();
  const statusKey = disabled
    ? "statusDisabled"
    : stats?.status === "degraded"
      ? "statusDegraded"
      : "statusReady";
  return (
    <div className="rounded-panel border border-border/60 bg-background p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[12px] font-medium uppercase tracking-wide text-muted-foreground">
            {t("knowledge.graph.title")}
          </p>
          <p className="mt-0.5 flex items-center gap-2 text-[14px] font-semibold text-foreground">
            <span
              className={cn(
                "inline-block h-2 w-2 rounded-full",
                disabled
                  ? "bg-muted-foreground/50"
                  : stats?.status === "degraded"
                    ? "bg-amber-500"
                    : "bg-emerald-500",
              )}
              aria-hidden
            />
            {t(`knowledge.graph.${statusKey}`)}
            <span className="text-[12px] font-normal text-muted-foreground">
              {stats?.backend ?? "neo4j"}
            </span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            data-testid="graph-build"
            disabled={disabled}
            className="h-8 gap-1.5 rounded-full px-3 text-[12px]"
            onClick={onBuild}
          >
            <RefreshCcw className="h-3.5 w-3.5" aria-hidden />
            {t("knowledge.graph.build")}
          </Button>
          <Button
            type="button"
            size="sm"
            data-testid="graph-rebuild"
            disabled={disabled}
            className="h-8 gap-1.5 rounded-full px-3 text-[12px]"
            onClick={onRebuild}
          >
            <Network className="h-3.5 w-3.5" aria-hidden />
            {t("knowledge.graph.rebuild")}
          </Button>
        </div>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-2 text-[12px] sm:grid-cols-4">
        <Stat label={t("knowledge.graph.entities")} value={stats?.entities ?? 0} />
        <Stat label={t("knowledge.graph.relations")} value={stats?.relations ?? 0} />
        <Stat label={t("knowledge.graph.docsReady")} value={stats?.docsReady ?? 0} />
        <Stat
          label={t("knowledge.graph.docsPending")}
          value={(stats?.docsPending ?? 0) + (stats?.docsFailed ?? 0)}
        />
      </dl>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-control bg-muted/40 px-2.5 py-1.5">
      <dt className="text-[12px] text-muted-foreground">{label}</dt>
      <dd className="text-[14px] font-semibold text-foreground">{value}</dd>
    </div>
  );
}
