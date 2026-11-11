import {useState} from "react";
import {useTranslation} from "react-i18next";
import {Square} from "lucide-react";
import {Button} from "@/components/ui/button";
import {Input} from "@/components/ui/input";
import {EmptyState} from "@/components/workspace/EmptyState";
import type {KnowledgeEvalRun,KnowledgeEvalDataset} from "@/lib/knowledge-eval-api";
import {cn} from "@/lib/utils";
import {KnowledgeEvalRunSheet} from "./KnowledgeEvalRunSheet";
interface KnowledgeEvalRunsProps {
  error: string | null;
  filter: string;
  onFilter: (value:string)=>void;
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


export function KnowledgeEvalRuns({runs,datasets,busy,onOpenRun,onCancelRun,onStartRun,filter,onFilter,error}: KnowledgeEvalRunsProps) {
 const {t}=useTranslation(); const [open,setOpen]=useState(false);
 const filtered=runs.filter(r=>(r.id+" "+(datasets.find(d=>d.id===r.datasetId)?.name??"")).toLowerCase().includes(filter.toLowerCase()));
 return <section data-testid="knowledge-eval-runs" className="knowledge-panel mt-5">
 <div className="knowledge-toolbar justify-between"><h2 className="text-base font-semibold">{t("knowledge.eval.tab.runs")}</h2><Button data-knowledge-new-eval onClick={()=>setOpen(true)}>{t("knowledge.experience.newEval",{defaultValue:"新建评测"})}</Button></div>
 <Input className="my-4 max-w-sm" aria-label={t("knowledge.experience.filterRuns",{defaultValue:"筛选评测记录"})} placeholder={t("knowledge.experience.filterRuns",{defaultValue:"筛选评测记录"})} value={filter} onChange={e=>onFilter(e.target.value)}/>
      {runs.length === 0 ? (
        <div className="mt-3">
          <EmptyState
            title={t("knowledge.eval.empty.runs")}
            description={t("knowledge.eval.run.hint")}
          />
        </div>
      ) : (
        <ul className="mt-3 flex flex-col gap-2">
          {filtered.map((run) => (
            <li
              key={run.id}
              data-testid={`knowledge-eval-run-${run.id}`}
              className="flex flex-wrap items-center gap-2 rounded-control border border-border/60 bg-background px-3 py-2 transition-colors hover:bg-muted/30"
            >
              <div className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-foreground">
                  {datasets.find(d=>d.id===run.datasetId)?.name ?? run.datasetId} · {run.createdAt?.slice(0,19) ?? "—"}
                </span><span className="text-sm text-muted-foreground">{run.id}</span>
                <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
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
                className="h-7 shrink-0 rounded-full px-3 text-sm"
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
                  className="h-7 gap-1 rounded-full px-2.5 text-sm"
                >
                  <Square className="h-3 w-3" aria-hidden />
                  {t("knowledge.eval.action.cancel")}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
<KnowledgeEvalRunSheet open={open} onClose={()=>setOpen(false)} datasets={datasets} busy={busy} error={error} onStartRun={onStartRun}/></section>;
}
