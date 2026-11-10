import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2, RefreshCcw } from "lucide-react";

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
import { KnowledgeEvalMetricGuide } from "./knowledge-eval/KnowledgeEvalMetricGuide";
import { KnowledgeEvalOverview } from "./knowledge-eval/KnowledgeEvalOverview";
import { KnowledgeEvalRuns } from "./knowledge-eval/KnowledgeEvalRuns";

type Section = "datasets" | "runs" | "compare";

const POLL_INTERVAL_MS = 3000;
const RUNNING_STATUSES = new Set(["queued", "running"]);

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

  const [metricSpecs, setMetricSpecs] = useState<KnowledgeMetricSpec[]>([]);
  const [datasets, setDatasets] = useState<KnowledgeEvalDataset[]>([]);
  const [runs, setRuns] = useState<KnowledgeEvalRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [report, setReport] = useState<KnowledgeEvalReport | null>(null);
  const [comparison, setComparison] = useState<KnowledgeEvalComparison | null>(null);
  const [baseRunId, setBaseRunId] = useState("");
  const [candidateRunId, setCandidateRunId] = useState("");
  const [section, setSection] = useState<Section>("datasets");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingRun, setPendingRun] = useState<PendingRun | null>(null);

  const loadRuns = useCallback(async () => {
    const payload = await knowledgeEvalApi.listRuns();
    setRuns(payload);
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
      setRuns(runPayload);
      setSelectedRunId((current) =>
        current && runPayload.some((run) => run.id === current)
          ? current
          : runPayload[0]?.id ?? null,
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

  // 选中运行 → 拉报告（聚合段 + 逐题段）。
  useEffect(() => {
    if (!selectedRunId) {
      setReport(null);
      return;
    }
    let cancelled = false;
    void knowledgeEvalApi
      .getRunReport(selectedRunId)
      .then((payload) => {
        if (!cancelled) setReport(payload);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setReport(null);
        setLoadError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [selectedRunId]);

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

  const runAction = useCallback(
    async (action: () => Promise<unknown>) => {
      setBusy(true);
      setActionError(null);
      try {
        await action();
      } catch (reason: unknown) {
        setActionError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const onCreateDataset = useCallback(
    async (input: { name: string; notes?: string; cases?: Array<Record<string, unknown>> }) => {
      await runAction(async () => {
        await knowledgeEvalApi.createDataset(input);
        await loadAll();
      });
    },
    [loadAll, runAction],
  );

  const onDeleteDataset = useCallback(
    async (id: string) => {
      await runAction(async () => {
        await knowledgeEvalApi.deleteDataset(id);
        await loadAll();
      });
    },
    [loadAll, runAction],
  );

  const onUpdateCases = useCallback(
    async (dataset: KnowledgeEvalDataset, caseId: string, expectAny: string[]) => {
      await runAction(async () => {
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
      await runAction(async () => {
        const created = await knowledgeEvalApi.createRun(input);
        await loadAll();
        setSelectedRunId(created.id);
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
      await runAction(async () => {
        await knowledgeEvalApi.cancelRun(runId);
        await loadRuns();
      });
    },
    [loadRuns, runAction],
  );

  const onCompare = useCallback(async () => {
    if (!baseRunId || !candidateRunId) return;
    await runAction(async () => {
      setComparison(await knowledgeEvalApi.compare(baseRunId, candidateRunId));
    });
  }, [baseRunId, candidateRunId, runAction]);

  const sections: Array<{ key: Section; label: string }> = [
    { key: "datasets", label: t("knowledge.eval.tab.datasets") },
    { key: "runs", label: t("knowledge.eval.tab.runs") },
    { key: "compare", label: t("knowledge.eval.tab.compare") },
  ];

  return (
    <div className="assistant-page flex h-full flex-col" data-testid="knowledge-eval-page">
      <PageHeading
        kicker={t("knowledge.eval.kicker")}
        title={t("knowledge.eval.title")}
        description={t("knowledge.eval.subtitle")}
        actions={
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={loading}
            onClick={() => void loadAll()}
            className="h-8 gap-1.5 rounded-full px-3 text-[12px] font-medium"
          >
            {loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <RefreshCcw className="h-3.5 w-3.5" aria-hidden />
            )}
            {t("knowledge.eval.action.refresh")}
          </Button>
        }
      />

      {loadError ? (
        <div
          role="alert"
          className="mt-4 rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-[12px] text-destructive"
        >
          {t("knowledge.eval.error.load")}: {loadError}
        </div>
      ) : null}

      {actionError ? (
        <div
          role="alert"
          className="mt-4 rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-[12px] text-destructive"
        >
          {t("knowledge.eval.error.action")}: {actionError}
        </div>
      ) : null}

      <KnowledgeEvalOverview
        groups={report?.groups ?? []}
        degraded={selectedRun?.degraded ?? []}
        specs={metricSpecs}
        loading={loading}
      />

      <KnowledgeEvalMetricGuide specs={metricSpecs} />

      <KnowledgeEvalCaseResults report={report} loading={loading} />

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
                ? "-mb-px inline-flex cursor-pointer items-center border-b-2 border-primary px-3 py-2 text-[12px] font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                : "-mb-px inline-flex cursor-pointer items-center border-b-2 border-transparent px-3 py-2 text-[12px] font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
          onCreate={onCreateDataset}
          onDelete={onDeleteDataset}
          onUpdateCases={onUpdateCases}
        />
      </div>

      <div hidden={section !== "runs"}>
        <KnowledgeEvalRuns
          runs={runs}
          datasets={datasets}
          selectedRunId={selectedRunId}
          busy={busy}
          onSelectRun={setSelectedRunId}
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
          onChangeBase={setBaseRunId}
          onChangeCandidate={setCandidateRunId}
          onCompare={() => void onCompare()}
        />
      </div>

      {pendingRun ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="knowledge-eval-privacy-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
        >
          <div className="w-full max-w-[440px] rounded-panel border border-border bg-background p-5 shadow-2xl">
            <h2
              id="knowledge-eval-privacy-title"
              className="flex items-center gap-2 text-[14px] font-semibold text-foreground"
            >
              <AlertTriangle className="h-4 w-4 text-amber-600" aria-hidden />
              {t("knowledge.eval.hint.judgePrivacyTitle")}
            </h2>
            <p className="mt-2 text-[12.5px] leading-relaxed text-muted-foreground">
              {t("knowledge.eval.hint.judgePrivacy")}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setPendingRun(null)}
                className="h-8 rounded-full px-3 text-[12px]"
              >
                {t("knowledge.eval.action.cancel")}
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={() => {
                  const input = pendingRun;
                  setPendingRun(null);
                  void startRun(input);
                }}
                className="h-8 rounded-full px-3.5 text-[12px] font-semibold"
              >
                {t("knowledge.eval.action.confirm")}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
