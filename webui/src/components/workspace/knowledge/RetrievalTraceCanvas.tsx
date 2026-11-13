import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import {
  ArrowUpDown,
  Boxes,
  ChevronsDownUp,
  ChevronsUpDown,
  Copy,
  GitBranch,
  GitMerge,
  Hash,
  Loader2,
  Network,
  Scissors,
  Search,
  Sparkles,
  Type,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { KnowledgeSearchTrace, KnowledgeTraceStage } from "@/lib/api";

const STAGE_ICONS: Record<string, LucideIcon> = {
  scope: Boxes,
  query: Search,
  rewrite: Sparkles,
  vector: Hash,
  keyword: Type,
  expansion: GitBranch,
  graph: Network,
  fusion: GitMerge,
  dedup: Copy,
  rerank: ArrowUpDown,
  mmr: Scissors,
};

const STAGE_TITLE_KEYS: Record<string, string> = {
  scope: "knowledge.retrieval.stageScope",
  query: "knowledge.retrieval.stageQuery",
  rewrite: "knowledge.retrieval.stageRewrite",
  vector: "knowledge.retrieval.stageVector",
  keyword: "knowledge.retrieval.stageKeyword",
  expansion: "knowledge.retrieval.stageExpansion",
  graph: "knowledge.retrieval.stageGraph",
  fusion: "knowledge.retrieval.stageFusion",
  dedup: "knowledge.retrieval.stageDedup",
  rerank: "knowledge.retrieval.stageRerank",
  mmr: "knowledge.retrieval.stageMmr",
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

const STATUS_DOT: Record<string, string> = {
  ok: "bg-emerald-500",
  skipped: "bg-muted-foreground/50",
  degraded: "bg-amber-500",
  error: "bg-destructive",
};

function formatMetric(key: string, value: number | string | boolean | null): string {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "on" : "off";
  if (typeof value === "number") {
    if (["vectorWeight", "keywordWeight", "threshold", "lambda"].includes(key)) {
      return value.toFixed(2);
    }
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  return String(value);
}

export function retrievalStageTitle(t: TFunction, kind: string): string {
  const key = STAGE_TITLE_KEYS[kind];
  return key ? t(key, { defaultValue: kind }) : kind;
}

export function RetrievalTraceCanvas({
  trace,
  loading,
  collapsed,
  onToggleCollapsed,
  onSelectStage,
}: {
  trace: KnowledgeSearchTrace | null;
  loading: boolean;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onSelectStage: (stage: KnowledgeTraceStage, index: number) => void;
}) {
  const { t } = useTranslation();
  const stages = trace?.stages ?? [];

  return (
    <section className="rounded-panel border border-border/60 bg-background" data-testid="retrieval-trace-canvas">
      <header className="flex items-center gap-2 border-b border-border/60 px-4 py-2.5">
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-primary/10 text-primary">
          <GitMerge className="h-3.5 w-3.5" aria-hidden />
        </span>
        <h3 className="text-sm font-semibold text-foreground">{t("knowledge.retrieval.canvas")}</h3>
        {trace ? (
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {t("knowledge.retrieval.canvasTotal", { ms: trace.totalMs })}
          </span>
        ) : null}
        <button
          type="button"
          onClick={onToggleCollapsed}
          aria-label={
            collapsed
              ? t("knowledge.retrieval.canvasExpand")
              : t("knowledge.retrieval.canvasCollapse")
          }
          className="ml-auto flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          {collapsed ? (
            <ChevronsUpDown className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <ChevronsDownUp className="h-3.5 w-3.5" aria-hidden />
          )}
        </button>
      </header>

      {collapsed ? null : (
        <div className="px-4 py-3">
          {loading ? (
            <p className="flex items-center gap-2 py-4 text-[12px] text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 knowledge-motion animate-spin" aria-hidden />
              {t("knowledge.retrieval.canvasRunning")}
            </p>
          ) : stages.length === 0 ? (
            <p className="py-4 text-[12px] text-muted-foreground">
              {t("knowledge.retrieval.canvasHint")}
            </p>
          ) : (
            <ol className="relative flex flex-col gap-1.5 ps-1">
              {stages.map((stage, index) => {
                const Icon = STAGE_ICONS[stage.kind] ?? Boxes;
                const statusDot = STATUS_DOT[stage.status] ?? STATUS_DOT.skipped;
                const metricEntries = Object.entries(stage.metrics).slice(0, 5);
                return (
                  <li key={`${stage.kind}-${index}`} className="relative flex gap-2">
                    {index < stages.length - 1 ? (
                      <span
                        className="absolute left-[13px] top-7 h-[calc(100%-4px)] w-px bg-border"
                        aria-hidden
                      />
                    ) : null}
                    <span className="knowledge-trace-node relative z-[1] flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-border bg-background text-muted-foreground" style={{ animationDelay: `${index * 70}ms` }}>
                      <Icon className="h-3.5 w-3.5" aria-hidden />
                    </span>
                    <button
                      type="button"
                      onClick={() => onSelectStage(stage, index)}
                      className="knowledge-trace-node min-w-0 flex-1 rounded-control border border-border/60 bg-muted/10 px-3 py-2 text-left transition-colors hover:border-border hover:bg-muted/30"
                      style={{ animationDelay: `${index * 70 + 40}ms` }}
                    >
                      <span className="flex flex-wrap items-center gap-1.5 text-xs">
                        <span className={cn("h-1.5 w-1.5 rounded-full", statusDot)} aria-hidden />
                        <span className="font-medium text-foreground">
                          {retrievalStageTitle(t, stage.kind)}
                        </span>
                        {stage.durationMs > 0 ? (
                          <span className="tabular-nums text-muted-foreground">
                            {stage.durationMs}ms
                          </span>
                        ) : null}
                        {stage.status !== "ok" ? (
                          <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                            {t(`knowledge.retrieval.status${stage.status.charAt(0).toUpperCase()}${stage.status.slice(1)}`, {
                              defaultValue: stage.status,
                            })}
                          </span>
                        ) : null}
                      </span>
                      {metricEntries.length > 0 ? (
                        <span className="mt-1 flex flex-wrap gap-1">
                          {metricEntries.map(([key, value]) => (
                            <span
                              key={key}
                              className="rounded-full border border-border/60 px-1.5 py-0.5 text-[10px] tabular-nums text-muted-foreground"
                            >
                              {METRIC_LABEL_KEYS[key]
                                ? t(METRIC_LABEL_KEYS[key], { defaultValue: key })
                                : key}
                              {": "}
                              {formatMetric(key, value)}
                            </span>
                          ))}
                        </span>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}
