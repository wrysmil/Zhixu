import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { KnowledgeSheet, KnowledgeSheetContent, KnowledgeSheetTitle, KnowledgeSheetDescription } from "../knowledge-shared/KnowledgeSurfaces";
import type {
  KnowledgeEvalDataset,
  KnowledgeEvalSearchMode,
} from "@/lib/knowledge-eval-api";


interface Props { error: string | null; open: boolean; onClose: () => void; datasets: KnowledgeEvalDataset[]; busy: boolean; onStartRun: (input: {dataset_id: string; configs: Array<Record<string,unknown>>}) => void; }
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


export function KnowledgeEvalRunSheet({open,onClose,datasets,busy,onStartRun,error}: Props) {
 const {t}=useTranslation();
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


 return <KnowledgeSheet open={open} onOpenChange={v=>{if(!v && !busy)onClose();}}><KnowledgeSheetContent onCloseAutoFocus={e=>{e.preventDefault();(document.querySelector("[data-knowledge-new-eval]") as HTMLElement|null)?.focus();}} className="p-6 sm:p-8"><KnowledgeSheetTitle>{t("knowledge.experience.newEval",{defaultValue:"新建评测"})}</KnowledgeSheetTitle><KnowledgeSheetDescription>{t("knowledge.eval.run.hint")}</KnowledgeSheetDescription>
      {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
      <div className="mt-3 flex flex-col gap-3 rounded-control border border-border/60 bg-muted/20 p-3">
        <label className="flex flex-col gap-1 text-sm font-medium text-muted-foreground">
          {t("knowledge.eval.run.dataset")}
          <select
            aria-label={t("knowledge.eval.run.dataset")}
            value={datasetId}
            onChange={(event) => setDatasetId(event.target.value)}
            className="h-8 rounded-control border border-input bg-background px-2 text-sm"
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
            <label className="flex flex-col gap-1 text-sm font-medium text-muted-foreground">
              {t("knowledge.eval.run.configId")}
              <Input
                aria-label={t("knowledge.eval.run.configId")}
                value={draft.id}
                onChange={(event) => updateDraft(index, { id: event.target.value })}
                className="h-8 font-mono text-sm"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm font-medium text-muted-foreground">
              {t("knowledge.eval.run.configName")}
              <Input
                aria-label={t("knowledge.eval.run.configName")}
                value={draft.name}
                onChange={(event) => updateDraft(index, { name: event.target.value })}
                className="h-8 text-sm"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm font-medium text-muted-foreground">
              {t("knowledge.eval.run.mode")}
              <select
                aria-label={t("knowledge.eval.run.mode")}
                value={draft.mode}
                onChange={(event) =>
                  updateDraft(index, { mode: event.target.value as KnowledgeEvalSearchMode })
                }
                className="h-8 rounded-control border border-input bg-background px-2 text-sm"
              >
                {SEARCH_MODES.map((mode) => (
                  <option key={mode} value={mode}>
                    {mode}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm font-medium text-muted-foreground">
              {t("knowledge.eval.run.limit")}
              <Input
                aria-label={t("knowledge.eval.run.limit")}
                value={draft.limit}
                onChange={(event) => updateDraft(index, { limit: event.target.value })}
                className="h-8 text-sm"
              />
            </label>
<details className="col-span-full"><summary>{t("knowledge.experience.advanced",{defaultValue:"高级参数"})}</summary><div className="grid gap-3 sm:grid-cols-2">            <label className="flex flex-col gap-1 text-sm font-medium text-muted-foreground">
              rrf_k
              <Input
                aria-label="rrf_k"
                value={draft.rrfK}
                onChange={(event) => updateDraft(index, { rrfK: event.target.value })}
                className="h-8 text-sm"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm font-medium text-muted-foreground">
              vector_weight
              <Input
                aria-label="vector_weight"
                value={draft.vectorWeight}
                onChange={(event) => updateDraft(index, { vectorWeight: event.target.value })}
                className="h-8 text-sm"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm font-medium text-muted-foreground">
              keyword_weight
              <Input
                aria-label="keyword_weight"
                value={draft.keywordWeight}
                onChange={(event) => updateDraft(index, { keywordWeight: event.target.value })}
                className="h-8 text-sm"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm font-medium text-muted-foreground">
              mmr_lambda
              <Input
                aria-label="mmr_lambda"
                value={draft.mmrLambda}
                onChange={(event) => updateDraft(index, { mmrLambda: event.target.value })}
                className="h-8 text-sm"
              />
            </label>
</div></details>          </div>
        ))}

        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setDrafts((current) => [...current, emptyDraft(current.length)])}
            className="h-8 rounded-full px-3 text-sm"
          >
            {t("knowledge.eval.action.addConfig")}
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={busy || !datasetId}
            onClick={start}
            className="h-8 gap-1.5 rounded-full px-3.5 text-sm font-semibold"
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <Play className="h-3.5 w-3.5" aria-hidden />}
            {t("knowledge.eval.action.run")}
          </Button>
        </div>
      </div>

</KnowledgeSheetContent></KnowledgeSheet>;
}
