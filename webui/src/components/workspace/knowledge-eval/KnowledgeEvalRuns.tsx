import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Play, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/workspace/EmptyState";
import type {
  KnowledgeEvalDataset,
  KnowledgeEvalRun,
  KnowledgeEvalSearchMode,
} from "@/lib/knowledge-eval-api";
import { cn } from "@/lib/utils";

interface KnowledgeEvalRunsProps {
  runs: KnowledgeEvalRun[];
  datasets: KnowledgeEvalDataset[];
  busy: boolean;
  onOpenRun: (runId: string) => void;
  onCancelRun: (runId: string) => void;
  onStartRun: (input: {
    dataset_id: string;
    configs: Array<Record<string, unknown>>;
  }) => void;
}

interface ConfigDraft {
  id: string;
  name: string;
  mode: KnowledgeEvalSearchMode;
  limit: string;
  rrfK: string;
  vectorWeight: string;
  keywordWeight: string;
  mmrLambda: string;
}

const SEARCH_MODES: KnowledgeEvalSearchMode[] = ["hybrid", "semantic", "keyword"];

function emptyDraft(index: number): ConfigDraft {
  return {
    id: index === 0 ? "base" : `config-${index + 1}`,
    name: index === 0 ? "Base" : "",
    mode: "hybrid",
    limit: "8",
    rrfK: "",
    vectorWeight: "",
    keywordWeight: "",
    mmrLambda: "",
  };
}

/** 把表单里的空串当成「不覆写」——空值不参与请求。 */
function numericOrUndefined(value: string): number | undefined {
  const text = value.trim();
  if (!text) return undefined;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/**
 * 运行分区：被评配置、发起运行、运行状态。
 *
 * 检索参数只走后端白名单——这里拼出的 overrides 只含白名单字段，
 * 未知字段由后端以 `KNOWLEDGE_CONFIG_UNSUPPORTED` 拒绝。
 */
export function KnowledgeEvalRuns({
  runs,
  datasets,
  busy,
  onOpenRun,
  onCancelRun,
  onStartRun,
}: KnowledgeEvalRunsProps) {
  const { t } = useTranslation();
  const [datasetId, setDatasetId] = useState("");
  const [drafts, setDrafts] = useState<ConfigDraft[]>([emptyDraft(0)]);

  useEffect(() => {
    if (!datasetId && datasets.length > 0) setDatasetId(datasets[0].id);
  }, [datasetId, datasets]);

  const updateDraft = (index: number, patch: Partial<ConfigDraft>) => {
    setDrafts((current) =>
      current.map((item, itemIndex) => (itemIndex === index ? { ...item, ...patch } : item)),
    );
  };

  const start = () => {
    const configs = drafts
      .filter((draft) => draft.id.trim())
      .map((draft) => {
        const overrides: Record<string, unknown> = { mode: draft.mode };
        const limit = numericOrUndefined(draft.limit);
        if (limit !== undefined) overrides.limit = limit;
        const rrfK = numericOrUndefined(draft.rrfK);
        if (rrfK !== undefined) overrides.rrf_k = rrfK;
        const vectorWeight = numericOrUndefined(draft.vectorWeight);
        if (vectorWeight !== undefined) overrides.vector_weight = vectorWeight;
        const keywordWeight = numericOrUndefined(draft.keywordWeight);
        if (keywordWeight !== undefined) overrides.keyword_weight = keywordWeight;
        const mmrLambda = numericOrUndefined(draft.mmrLambda);
        if (mmrLambda !== undefined) overrides.mmr_lambda = mmrLambda;
        return { id: draft.id.trim(), name: draft.name.trim(), overrides };
      });
    if (!datasetId || configs.length === 0) return;
    onStartRun({ dataset_id: datasetId, configs });
  };

  return (
    <section data-testid="knowledge-eval-runs" className="mt-4 rounded-panel border border-border/60 bg-background p-4">
      <h2 className="text-[13px] font-semibold text-foreground">
        {t("knowledge.eval.tab.runs")}
      </h2>

      <div className="mt-3 flex flex-col gap-3 rounded-control border border-border/60 bg-muted/20 p-3">
        <label className="flex flex-col gap-1 text-[11px] font-medium text-muted-foreground">
          {t("knowledge.eval.run.dataset")}
          <select
            aria-label={t("knowledge.eval.run.dataset")}
            value={datasetId}
            onChange={(event) => setDatasetId(event.target.value)}
            className="h-8 rounded-control border border-input bg-background px-2 text-[12px]"
          >
            {datasets.length === 0 ? (
              <option value="">{t("knowledge.eval.empty.datasets")}</option>
            ) : null}
            {datasets.map((dataset) => (
              <option key={dataset.id} value={dataset.id}>
                {dataset.name}
              </option>
            ))}
          </select>
        </label>

        {drafts.map((draft, index) => (
          <div key={index} className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <label className="flex flex-col gap-1 text-[10.5px] font-medium text-muted-foreground">
              {t("knowledge.eval.run.configId")}
              <Input
                aria-label={t("knowledge.eval.run.configId")}
                value={draft.id}
                onChange={(event) => updateDraft(index, { id: event.target.value })}
                className="h-8 font-mono text-[11.5px]"
              />
            </label>
            <label className="flex flex-col gap-1 text-[10.5px] font-medium text-muted-foreground">
              {t("knowledge.eval.run.configName")}
              <Input
                aria-label={t("knowledge.eval.run.configName")}
                value={draft.name}
                onChange={(event) => updateDraft(index, { name: event.target.value })}
                className="h-8 text-[11.5px]"
              />
            </label>
            <label className="flex flex-col gap-1 text-[10.5px] font-medium text-muted-foreground">
              {t("knowledge.eval.run.mode")}
              <select
                aria-label={t("knowledge.eval.run.mode")}
                value={draft.mode}
                onChange={(event) =>
                  updateDraft(index, { mode: event.target.value as KnowledgeEvalSearchMode })
                }
                className="h-8 rounded-control border border-input bg-background px-2 text-[11.5px]"
              >
                {SEARCH_MODES.map((mode) => (
                  <option key={mode} value={mode}>
                    {mode}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[10.5px] font-medium text-muted-foreground">
              {t("knowledge.eval.run.limit")}
              <Input
                aria-label={t("knowledge.eval.run.limit")}
                value={draft.limit}
                onChange={(event) => updateDraft(index, { limit: event.target.value })}
                className="h-8 text-[11.5px]"
              />
            </label>
            <label className="flex flex-col gap-1 text-[10.5px] font-medium text-muted-foreground">
              rrf_k
              <Input
                aria-label="rrf_k"
                value={draft.rrfK}
                onChange={(event) => updateDraft(index, { rrfK: event.target.value })}
                className="h-8 text-[11.5px]"
              />
            </label>
            <label className="flex flex-col gap-1 text-[10.5px] font-medium text-muted-foreground">
              vector_weight
              <Input
                aria-label="vector_weight"
                value={draft.vectorWeight}
                onChange={(event) => updateDraft(index, { vectorWeight: event.target.value })}
                className="h-8 text-[11.5px]"
              />
            </label>
            <label className="flex flex-col gap-1 text-[10.5px] font-medium text-muted-foreground">
              keyword_weight
              <Input
                aria-label="keyword_weight"
                value={draft.keywordWeight}
                onChange={(event) => updateDraft(index, { keywordWeight: event.target.value })}
                className="h-8 text-[11.5px]"
              />
            </label>
            <label className="flex flex-col gap-1 text-[10.5px] font-medium text-muted-foreground">
              mmr_lambda
              <Input
                aria-label="mmr_lambda"
                value={draft.mmrLambda}
                onChange={(event) => updateDraft(index, { mmrLambda: event.target.value })}
                className="h-8 text-[11.5px]"
              />
            </label>
          </div>
        ))}

        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setDrafts((current) => [...current, emptyDraft(current.length)])}
            className="h-8 rounded-full px-3 text-[11.5px]"
          >
            {t("knowledge.eval.action.addConfig")}
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={busy || !datasetId}
            onClick={start}
            className="h-8 gap-1.5 rounded-full px-3.5 text-[12px] font-semibold"
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <Play className="h-3.5 w-3.5" aria-hidden />}
            {t("knowledge.eval.action.run")}
          </Button>
        </div>
      </div>

      {runs.length === 0 ? (
        <div className="mt-3">
          <EmptyState
            title={t("knowledge.eval.empty.runs")}
            description={t("knowledge.eval.run.hint")}
          />
        </div>
      ) : (
        <ul className="mt-3 flex flex-col gap-2">
          {runs.map((run) => (
            <li
              key={run.id}
              data-testid={`knowledge-eval-run-${run.id}`}
              className="flex flex-wrap items-center gap-2 rounded-control border border-border/60 bg-background px-3 py-2 transition-colors hover:bg-muted/30"
            >
              <div className="min-w-0 flex-1">
                <span className="block truncate text-[12.5px] font-medium text-foreground">
                  {run.id}
                </span>
                <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10.5px] text-muted-foreground">
                  <span className={cn(
                    "rounded-full px-1.5 py-0.5",
                    run.status === "completed"
                      ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200"
                      : run.status === "failed"
                        ? "bg-destructive/10 text-destructive"
                        : "bg-muted/60",
                  )}>
                    {run.status}
                  </span>
                  <span className="tabular-nums">
                    {t("knowledge.eval.run.configCount", { count: run.configs.length })}
                  </span>
                  {run.degraded.length > 0 ? (
                    <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
                      {t("knowledge.eval.badge.degraded")}
                    </span>
                  ) : null}
                </span>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => onOpenRun(run.id)}
                className="h-7 shrink-0 rounded-full px-3 text-[11px]"
              >
                {t("knowledge.eval.action.viewReport")}
              </Button>
              {run.status === "queued" || run.status === "running" ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => onCancelRun(run.id)}
                  className="h-7 gap-1 rounded-full px-2.5 text-[11px]"
                >
                  <Square className="h-3 w-3" aria-hidden />
                  {t("knowledge.eval.action.cancel")}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
