import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, BookOpen, Loader2, RefreshCcw } from "lucide-react";

import "./knowledge-shared/experience.css";
import { KnowledgeStatus } from "./knowledge-shared/KnowledgeStatus";
import { KnowledgeEvalCaseSheet } from "./knowledge-eval/KnowledgeEvalCaseSheet";
import type { KnowledgeEvalCaseResult } from "@/lib/knowledge-eval-api";
import { KnowledgeSheet, KnowledgeSheetContent, KnowledgeSheetTitle, KnowledgeSheetDescription } from "./knowledge-shared/KnowledgeSurfaces";
import { Button } from "@/components/ui/button";
import { PageHeading } from "@/components/workspace/PageHeading";
import {
  knowledgeEvalApi,
  type KnowledgeEvalComparison,
  type KnowledgeEvalDataset,
  type KnowledgeEvalReport,
  type KnowledgeEvalRun,
  type KnowledgeMetricSpec,
} from "@/lib/knowledge-eval-api";

import { KnowledgeEvalCaseResults } from "./knowledge-eval/KnowledgeEvalCaseResults";
import { KnowledgeEvalCompare } from "./knowledge-eval/KnowledgeEvalCompare";
import { KnowledgeEvalDatasets } from "./knowledge-eval/KnowledgeEvalDatasets";
import { KnowledgeEvalManual } from "./knowledge-eval/KnowledgeEvalManual";
import { KnowledgeEvalMetricGuide } from "./knowledge-eval/KnowledgeEvalMetricGuide";
import { KnowledgeEvalOverview } from "./knowledge-eval/KnowledgeEvalOverview";
import { KnowledgeEvalRuns } from "./knowledge-eval/KnowledgeEvalRuns";
import {
  downloadDatasetTemplate,
  parseDatasetTemplateCsv,
} from "./knowledge-eval/dataset-template";

type Section = "datasets" | "runs" | "compare";

const POLL_INTERVAL_MS = 3000;
const RUNNING_STATUSES = new Set(["queued", "running"]);

/** 运行按「新的在前」排列：后端按创建时间升序返回，默认选中的应该是最新一次。 */
function newestFirst(runs: KnowledgeEvalRun[]): KnowledgeEvalRun[] {
  return [...runs].sort((left, right) =>
    (right.createdAt ?? "").localeCompare(left.createdAt ?? ""),
  );
}

interface PendingRun {
  dataset_id: string;
  configs: Array<Record<string, unknown>>;
}

/**
 * 知识检索评测台（`#/knowledge-eval`）。
 *
 * 三条硬约束：
 *
 * 1. **概览（聚合）与逐题表同时可见**——只有聚合数字视为实现缺失。
 * 2. **公式只来自后端** `GET /knowledge/metrics`，页面与 i18n 都不内置公式串。
 * 3. **缺证据是「未评分」**，不补 0——「算不出来」与「0 分」必须能区分。
 */
export function KnowledgeEvalPage() {
  const { t } = useTranslation();

  const initialSection = useRef(false);
  const [runFilter,setRunFilter]=useState("");
  const [caseFilter,setCaseFilter]=useState<string | null>(null);
  const compareVersion=useRef(0);
  const actionLock = useRef(false);
  const returnPoint = useRef<{id:string;positions:Array<{element:Element;top:number}>} | null>(null);
  const [caseResult,setCaseResult] = useState<KnowledgeEvalCaseResult | null>(null);
  const [reportLoading,setReportLoading] = useState(false);
  const [reportError,setReportError] = useState<string | null>(null);
  const [reportRetry,setReportRetry] = useState(0);
  const [metricSpecs, setMetricSpecs] = useState<KnowledgeMetricSpec[]>([]);
  const [datasets, setDatasets] = useState<KnowledgeEvalDataset[]>([]);
  const [runs, setRuns] = useState<KnowledgeEvalRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [report, setReport] = useState<KnowledgeEvalReport | null>(null);
  const [comparison, setComparison] = useState<KnowledgeEvalComparison | null>(null);
  const [baseRunId, setBaseRunId] = useState("");
  const [candidateRunId, setCandidateRunId] = useState("");
  const [section, setSection] = useState<Section>("datasets");
  /** 两级结构（对齐知识库页）：列表页管数据与运行，点某个运行才进报告详情。 */
  const [detailOpen, setDetailOpen] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingRun, setPendingRun] = useState<PendingRun | null>(null);

  const loadRuns = useCallback(async () => {
    const payload = await knowledgeEvalApi.listRuns();
    setRuns(newestFirst(payload));
    return payload;
  }, []);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [specs, datasetPayload, runPayload] = await Promise.all([
        knowledgeEvalApi.listMetrics(),
        knowledgeEvalApi.listDatasets(),
        knowledgeEvalApi.listRuns(),
      ]);
      setMetricSpecs(specs);
      setDatasets(datasetPayload);
      const ordered = newestFirst(runPayload);
      setRuns(ordered);
      if (!initialSection.current) { setSection(ordered.length ? "runs" : "datasets"); initialSection.current=true; }
      setSelectedRunId((current) =>
        current && ordered.some((run) => run.id === current) ? current : ordered[0]?.id ?? null,
      );
    } catch (reason: unknown) {
      setLoadError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // 详情页才拉报告（聚合段 + 逐题段）。
  // 依赖里带 `selectedRunStatus`：轮询把 queued/running 翻成终态时要重新拉一次，
  // 否则发起运行后详情页会一直停在「还没有逐题结果」。
  const selectedRunStatus =
    runs.find((run) => run.id === selectedRunId)?.status ?? null;
  useEffect(() => {
    if (!detailOpen || !selectedRunId) {
      setReport(null);
      return;
    }
    let cancelled = false;
    setReport(null); setReportLoading(true); setReportError(null);
    void knowledgeEvalApi
      .getRunReport(selectedRunId)
      .then((payload) => {
        if (!cancelled) setReport(payload);
      })
      .catch((reason: unknown) => {
        if (!cancelled) { setReport(null); setReportError(reason instanceof Error ? reason.message : String(reason)); }
      }).finally(() => { if (!cancelled) setReportLoading(false); });
    return () => {
      cancelled = true;
    };
  }, [detailOpen, selectedRunId, selectedRunStatus, reportRetry]);

  // 有未结束的运行时轮询运行列表；全部终态后停表。
  useEffect(() => {
    if (!runs.some((run) => RUNNING_STATUSES.has(run.status))) return;
    const timer = setTimeout(() => {
      void loadRuns().catch(() => undefined);
    }, POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [loadRuns, runs]);

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? null,
    [runs, selectedRunId],
  );

  /** 用例 id → 查询原文：逐题表显示问的是什么，而不是 UUID。 */
  const queriesByCaseId = useMemo(() => {
    const map: Record<string, string> = {};
    for (const dataset of datasets) {
      for (const item of dataset.cases) {
        map[item.id] = item.query;
      }
    }
    return map;
  }, [datasets]);

  const runAction = useCallback(
    async (action: () => Promise<unknown>) => {
      if(actionLock.current) return false;
      actionLock.current=true;
      setBusy(true);
      setActionError(null);
      try {
        await action();
        return true;
      } catch (reason: unknown) {
        setActionError(reason instanceof Error ? reason.message : String(reason));
        return false;
      } finally {
        actionLock.current=false;
        setBusy(false);
      }
    },
    [],
  );

  const onCreateDataset = useCallback(
    async (input: { name: string; notes?: string; cases?: Array<Record<string, unknown>> }) => {
      return runAction(async () => {
        await knowledgeEvalApi.createDataset(input);
        await loadAll();
      });
    },
    [loadAll, runAction],
  );

  const onDeleteDataset = useCallback(
    async (id: string) => {
      return runAction(async () => {
        await knowledgeEvalApi.deleteDataset(id);
        await loadAll();
      });
    },
    [loadAll, runAction],
  );

  const onUpdateCases = useCallback(
    async (dataset: KnowledgeEvalDataset, caseId: string, expectAny: string[]) => {
      return runAction(async () => {
        // 后端按严格模型收口：字段名必须是 snake_case，且不带 label_source
        // （标注来源由服务端推导，客户端传什么都不作数）。
        const cases = dataset.cases.map((item) =>
          item.id === caseId
            ? {
                id: item.id,
                query: item.query,
                expect_any: expectAny,
                kb_ids: item.kbIds,
                mode: item.mode,
                limit: item.limit,
              }
            : {
                id: item.id,
                query: item.query,
                expect_any: item.expectAny,
                kb_ids: item.kbIds,
                mode: item.mode,
                limit: item.limit,
              },
        );
        await knowledgeEvalApi.updateDataset(dataset.id, {
          base_revision: dataset.revision,
          cases,
        });
        await loadAll();
      });
    },
    [loadAll, runAction],
  );

  const startRun = useCallback(
    async (input: PendingRun) => {
      return runAction(async () => {
        const created = await knowledgeEvalApi.createRun(input);
        await loadAll();
        setRuns(current=>newestFirst([created,...current.filter(run=>run.id!==created.id)]));
        setSelectedRunId(created.id);
        // 发起后直接进报告详情：轮询会把 queued/running 翻成终态并重取报告。
        setDetailOpen(true);
      });
    },
    [loadAll, runAction],
  );

  const onStartRun = useCallback(
    (input: PendingRun) => {
      const dataset = datasets.find((item) => item.id === input.dataset_id);
      const hasJudgeCases = (dataset?.cases ?? []).some(
        (item) => item.labelSource !== "expect",
      );
      // judge 标注会把 chunk 正文发给模型服务（可能是远程）——先确认再发起。
      if (hasJudgeCases) {
        setPendingRun(input);
        return;
      }
      void startRun(input);
    },
    [datasets, startRun],
  );

  const onCancelRun = useCallback(
    async (runId: string) => {
      return runAction(async () => {
        await knowledgeEvalApi.cancelRun(runId);
        await loadRuns();
      });
    },
    [loadRuns, runAction],
  );

  const onCompare = useCallback(async () => {
    if (!baseRunId || !candidateRunId) return;
    await runAction(async () => {
      const version=compareVersion.current;
      const payload=await knowledgeEvalApi.compare(baseRunId, candidateRunId);
      if(version===compareVersion.current)setComparison(payload);
    });
  }, [baseRunId, candidateRunId, runAction]);

  const onDownloadTemplate = useCallback(() => {
    downloadDatasetTemplate("knowledge-eval-dataset-template.csv");
  }, []);

  const onImportTemplate = useCallback(
    async (file: File) => {
      setActionError(null);
      try {
        const parsed = parseDatasetTemplateCsv(await file.text());
        if (parsed.errors.length > 0) {
          setActionError(parsed.errors.join("; "));
          return;
        }
        if (parsed.cases.length === 0) {
          setActionError(t("knowledge.eval.error.csvEmpty"));
          return;
        }
        await onCreateDataset({
          name: file.name.replace(/\.csv$/i, ""),
          cases: parsed.cases.map((item) => ({
            query: item.query,
            expect_any: item.expectAny,
          })),
        });
      } catch (reason: unknown) {
        setActionError(reason instanceof Error ? reason.message : String(reason));
      }
    },
    [onCreateDataset, t],
  );

  const sections: Array<{ key: Section; label: string }> = [
    { key: "datasets", label: t("knowledge.eval.tab.datasets") },
    { key: "runs", label: t("knowledge.eval.tab.runs") },
    { key: "compare", label: t("knowledge.eval.tab.compare") },
  ];

  return (
    <div className="knowledge-experience assistant-page flex h-full min-w-0 flex-col pb-6" data-testid="knowledge-eval-page">
      {detailOpen ? null : (
        <PageHeading
          className="knowledge-page-header"
          title={t("knowledge.eval.title")}
          description={t("knowledge.eval.subtitle")}
          actions={
            <>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setManualOpen(true)}
                className="h-8 gap-1.5 rounded-full px-3 text-sm font-medium"
              >
                <BookOpen className="h-3.5 w-3.5" aria-hidden />
                {t("knowledge.eval.manual.open")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={loading}
                onClick={() => void loadAll()}
                className="h-8 gap-1.5 rounded-full px-3 text-sm font-medium"
              >
                {loading ? (
                  <Loader2 className="h-3.5 w-3.5 knowledge-motion animate-spin" aria-hidden />
                ) : (
                  <RefreshCcw className="h-3.5 w-3.5" aria-hidden />
                )}
                {t("knowledge.eval.action.refresh")}
              </Button>
            </>
          }
        />
      )}

      {loadError ? (
        <div
          role="alert"
          className="mt-4 rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
        >
          {t("knowledge.eval.error.load")}: {loadError}
        </div>
      ) : null}

      {actionError ? (
        <div
          role="alert"
          className="mt-4 rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
        >
          {t("knowledge.eval.error.action")}: {actionError}
        </div>
      ) : null}

      {detailOpen ? (
        <div className="flex min-w-0 items-center gap-2 pt-1 text-sm text-muted-foreground">
          <button
            type="button"
            onClick={() => {
              setDetailOpen(false);
              setReport(null);
              requestAnimationFrame(()=>{const point=returnPoint.current;if(point){const row=Array.from(document.querySelectorAll("[data-testid]")).find(element=>element.getAttribute("data-testid")===`knowledge-eval-run-${point.id}`);(row?.querySelector("button") as HTMLElement | null)?.focus({preventScroll:true});for(const position of point.positions)position.element.scrollTop=position.top;}});
            }}
            className="inline-flex cursor-pointer items-center gap-1 rounded-full px-2 py-1 transition-colors hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
            {t("knowledge.eval.title")}
          </button>
          <span aria-hidden>/</span>
          <span className="truncate text-foreground">
            {selectedRun?.id ?? t("knowledge.eval.detail.title")}
          </span>
        </div>
      ) : null}

      {detailOpen && selectedRun ? (
        <>
          <section
            data-testid="knowledge-eval-run-header"
            className="mt-3 flex flex-wrap items-center gap-2 rounded-panel border border-border/60 bg-background p-4"
          >
            <div className="min-w-0 flex-1">
              <h2 className="truncate text-[15px] font-semibold text-foreground">
                {t("knowledge.eval.detail.title")}
              </h2>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
                <span className="rounded-full bg-muted/60 px-1.5 py-0.5">{selectedRun.status}</span>
                <span className="font-mono">{selectedRun.id}</span>
                <span className="tabular-nums">
                  {t("knowledge.eval.run.configCount", { count: selectedRun.configs.length })}
                </span>
                {selectedRun.finishedAt ? (
                  <span className="tabular-nums">{selectedRun.finishedAt.slice(0, 19)}</span>
                ) : null}
              </div>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={loading}
              onClick={() => void loadAll()}
              className="h-8 gap-1.5 rounded-full px-3 text-sm font-medium"
            >
              {loading ? (
                <Loader2 className="h-3.5 w-3.5 knowledge-motion animate-spin" aria-hidden />
              ) : (
                <RefreshCcw className="h-3.5 w-3.5" aria-hidden />
              )}
              {t("knowledge.eval.action.refresh")}
            </Button>
          </section>

          {reportLoading ? <div className="py-4"><KnowledgeStatus label={t("knowledge.experience.reportLoading",{defaultValue:"加载评测报告"})} tone="pending" busy /></div> : null}
          {reportError ? <div role="alert" className="knowledge-panel mt-4 text-destructive">{reportError}<Button onClick={()=>setReportRetry(n=>n+1)}>{t("knowledge.experience.retry",{defaultValue:"重试"})}</Button></div> : null}
          {selectedRun.error ? <p role="alert" className="text-destructive">{selectedRun.error}</p> : null}
          <KnowledgeEvalOverview
            groups={report?.groups ?? []}
            degraded={selectedRun.degraded}
            specs={metricSpecs}
            loading={reportLoading}
          />

          {caseFilter ? <Button variant="outline" className="mt-4 self-start" onClick={()=>setCaseFilter(null)}>{t("knowledge.experience.allCases",{defaultValue:"显示全部题目"})}</Button> : null}
          <KnowledgeEvalCaseResults report={report && caseFilter ? {...report,results:report.results.filter(r=>r.caseId===caseFilter)} : report} loading={reportLoading} queries={queriesByCaseId} onOpenCase={setCaseResult} />

          <details className="mt-4 rounded-panel border border-border/60 bg-background">
            <summary className="cursor-pointer px-4 py-3 text-sm font-semibold text-foreground">
              {t("knowledge.eval.guide.title")}
            </summary>
            <KnowledgeEvalMetricGuide specs={metricSpecs} />
          </details>
        </>
      ) : (
        <>
          <div role="tablist" className="mt-4 flex gap-1 border-b border-border/60">
            {sections.map((item) => (
              <button
                key={item.key}
                type="button"
                role="tab"
                aria-selected={section === item.key}
                onClick={() => setSection(item.key)}
                className={
                  section === item.key
                    ? "-mb-px inline-flex cursor-pointer items-center border-b-2 border-primary px-3 py-2 text-sm font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    : "-mb-px inline-flex cursor-pointer items-center border-b-2 border-transparent px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                }
              >
                {item.label}
              </button>
            ))}
          </div>

          <div hidden={section !== "datasets"}>
            <KnowledgeEvalDatasets
              datasets={datasets}
              busy={busy}
              error={actionError}
              onCreate={onCreateDataset}
              onDelete={onDeleteDataset}
              onUpdateCases={onUpdateCases}
              onDownloadTemplate={onDownloadTemplate}
              onImportTemplate={(file) => void onImportTemplate(file)}
            />
          </div>

          <div hidden={section !== "runs"}>
            <KnowledgeEvalRuns
              error={actionError}
              filter={runFilter}
              onFilter={setRunFilter}
              runs={runs}
              datasets={datasets}
              busy={busy}
              onOpenRun={(runId) => {
                const positions:Array<{element:Element;top:number}>=[];
                let element:Element|null=document.querySelector("[data-testid=knowledge-eval-page]");
                while(element){positions.push({element,top:element.scrollTop});element=element.parentElement;}
                if(document.scrollingElement)positions.push({element:document.scrollingElement,top:document.scrollingElement.scrollTop});
                returnPoint.current={id:runId,positions};
                setReport(null); setReportLoading(true); setReportError(null);
                setSelectedRunId(runId);
                setCaseFilter(null);
                setDetailOpen(true);
              }}
              onCancelRun={onCancelRun}
              onStartRun={onStartRun}
            />
          </div>

          <div hidden={section !== "compare"}>
            <KnowledgeEvalCompare
              runs={runs}
              baseRunId={baseRunId}
              candidateRunId={candidateRunId}
              comparison={comparison}
              busy={busy}
              onChangeBase={id=>{compareVersion.current++;setComparison(null);setBaseRunId(id);}}
              onChangeCandidate={id=>{compareVersion.current++;setComparison(null);setCandidateRunId(id);}}
              runNames={Object.fromEntries(runs.map(run=>[run.id,`${datasets.find(d=>d.id===run.datasetId)?.name??run.datasetId} · ${run.configs.map(c=>c.name||c.id).join(", ")} · ${run.createdAt?.slice(0,19)??"—"} · ${run.id}`]))}
              onOpenRegression={id=>{setReport(null);setReportLoading(true);setReportError(null);setCaseFilter(id);setSelectedRunId(candidateRunId);setDetailOpen(true);}}
              onCompare={() => void onCompare()}
              queries={queriesByCaseId}
            />
          </div>
        </>
      )}

      <KnowledgeEvalCaseSheet result={caseResult} query={caseResult ? queriesByCaseId[caseResult.caseId.split("::")[0]] : undefined} onClose={()=>setCaseResult(null)} />
      <KnowledgeEvalManual
        open={manualOpen}
        onClose={() => setManualOpen(false)}
        onDownloadTemplate={onDownloadTemplate}
      />

      <KnowledgeSheet open={!!pendingRun} onOpenChange={v=>{if(!v)setPendingRun(null);}}>
      <KnowledgeSheetContent className="p-6 sm:p-8">
      <KnowledgeSheetTitle>{t("knowledge.eval.hint.judgePrivacyTitle")}</KnowledgeSheetTitle>
      <KnowledgeSheetDescription>{t("knowledge.eval.hint.judgePrivacy")}</KnowledgeSheetDescription>
      <div className="flex gap-3"><Button variant="outline" onClick={()=>setPendingRun(null)}>{t("knowledge.eval.action.cancel")}</Button>
      <Button disabled={busy} onClick={()=>{if(pendingRun){const input=pendingRun;setPendingRun(null);void startRun(input);}}}>{t("knowledge.eval.action.confirm")}</Button></div>
      </KnowledgeSheetContent></KnowledgeSheet>
    </div>
  );
}
