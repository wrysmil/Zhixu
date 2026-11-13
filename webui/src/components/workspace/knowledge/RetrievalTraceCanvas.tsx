import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import {
  ArrowUpDown,
  Boxes,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Copy,
  Filter,
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
import { formatCompactDuration } from "@/lib/format";
import type { KnowledgeSearchTrace, KnowledgeTraceStage } from "@/lib/api";

const STAGE_ICONS: Record<string, LucideIcon> = {
  scope: Boxes,
  query: Search,
  rewrite: Sparkles,
  vector: Hash,
  keyword: Type,
  expansion: GitBranch,
  graph: Network,
  docFilter: Filter,
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

const STATUS_DOT: Record<string, string> = {
  ok: "bg-emerald-500",
  skipped: "bg-muted-foreground/30",
  degraded: "bg-amber-500",
  error: "bg-destructive",
};

export function retrievalStageTitle(t: TFunction, kind: string): string {
  const key = STAGE_TITLE_KEYS[kind];
  return key ? t(key, { defaultValue: kind }) : kind;
}

export function retrievalStageStatus(t: TFunction, status: string): string {
  return t(
    `knowledge.retrieval.status${status.charAt(0).toUpperCase()}${status.slice(1)}`,
    { defaultValue: status },
  );
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

  if (!trace && !loading) return null;

  const slowest = stages.reduce<KnowledgeTraceStage | null>(
    (worst, stage) => (!worst || stage.durationMs > worst.durationMs ? stage : worst),
    null,
  );

  return (
    <section
      className="rounded-panel border border-border/70 bg-card"
      data-testid="retrieval-trace-canvas"
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border/60 px-4 py-2.5">
        <h3 className="text-[13px] font-semibold text-foreground">
          {t("knowledge.retrieval.canvas")}
        </h3>
        {trace ? (
          <span className="text-[11.5px] tabular-nums text-muted-foreground">
            {t("knowledge.retrieval.canvasTotal", {
              duration: formatCompactDuration(trace.totalMs),
            })}
          </span>
        ) : null}
        {slowest && slowest.durationMs > 0 ? (
          <span className="text-[11.5px] text-muted-foreground">
            {t("knowledge.retrieval.canvasSlowest", {
              stage: retrievalStageTitle(t, slowest.kind),
              duration: formatCompactDuration(slowest.durationMs),
            })}
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
          className="ml-auto inline-flex h-7 items-center gap-1 rounded-control px-2 text-[11.5px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          {collapsed
            ? t("knowledge.retrieval.canvasExpand")
            : t("knowledge.retrieval.canvasCollapse")}
          {collapsed ? (
            <ChevronDown className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <ChevronUp className="h-3.5 w-3.5" aria-hidden />
          )}
        </button>
      </header>

      {collapsed ? null : (
        <div className="px-4 py-3">
          {loading ? (
            <p className="flex items-center gap-2 py-1.5 text-[12px] text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 knowledge-motion animate-spin" aria-hidden />
              {t("knowledge.retrieval.canvasRunning")}
            </p>
          ) : (
            <ol className="flex flex-wrap items-center gap-x-1 gap-y-2">
              {stages.map((stage, index) => {
                const Icon = STAGE_ICONS[stage.kind] ?? Boxes;
                return (
                  <li key={`${stage.kind}-${index}`} className="flex items-center gap-1">
                    {index > 0 ? (
                      <ChevronRight
                        className="h-3 w-3 shrink-0 text-muted-foreground/40"
                        aria-hidden
                      />
                    ) : null}
                    <button
                      type="button"
                      onClick={() => onSelectStage(stage, index)}
                      style={{ animationDelay: `${index * 45}ms` }}
                      className="knowledge-trace-node inline-flex items-center gap-1.5 rounded-full border border-border/70 bg-background px-2.5 py-1 text-[12px] transition-colors hover:border-foreground/20 hover:bg-muted/40"
                    >
                      <span
                        className={cn(
                          "h-1.5 w-1.5 shrink-0 rounded-full",
                          STATUS_DOT[stage.status] ?? STATUS_DOT.skipped,
                        )}
                        aria-hidden
                      />
                      <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                      <span className="text-foreground">{retrievalStageTitle(t, stage.kind)}</span>
                      {stage.durationMs > 0 ? (
                        <span className="tabular-nums text-muted-foreground">
                          {formatCompactDuration(stage.durationMs)}
                        </span>
                      ) : null}
                      {stage.status !== "ok" ? (
                        <span className="text-[10.5px] text-muted-foreground">
                          {retrievalStageStatus(t, stage.status)}
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
