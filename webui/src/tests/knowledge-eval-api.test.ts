import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import {
  EvaluationApiError,
  evaluationTransport,
  resetEvaluationSession,
} from "@/lib/evaluation-api";
import { knowledgeEvalApi } from "@/lib/knowledge-eval-api";

function jsonResponse(
  body: unknown,
  status = 200,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

/** 用 vi.fn() 替换传输层：只验证客户端自己的路径/方法/归一化。 */
function mockTransport(...responses: unknown[]) {
  const request = vi.fn();
  for (const response of responses) {
    request.mockResolvedValueOnce(response);
  }
  const spy = vi.spyOn(evaluationTransport, "request").mockImplementation(
    request as never,
  );
  return { request, spy };
}

describe("knowledgeEvalApi", () => {
  beforeEach(() => {
    resetEvaluationSession();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("listMetrics 返回后端公式原文（客户端不内置公式）", async () => {
    const { request } = mockTransport({
      metrics: [
        {
          metricId: "precision_at_k",
          label: "准确率",
          formula: "相关块数(top-k) ÷ k",
          numerator: "top-k 中 grade≥2 的块数",
          denominator: "k",
          direction: "higher_is_better",
          description: "返回结果里有多少块真的回答了问题。",
        },
      ],
    });

    const metrics = await knowledgeEvalApi.listMetrics();

    expect(request).toHaveBeenCalledTimes(1);
    expect(request.mock.calls[0][0]).toBe("/knowledge/metrics");
    expect(metrics).toHaveLength(1);
    expect(metrics[0].formula).toBe("相关块数(top-k) ÷ k");
    expect(metrics[0].metricId).toBe("precision_at_k");
    expect(metrics[0].direction).toBe("higher_is_better");
  });

  it("指标公式串不出现在客户端源码里（公式只随端下发）", () => {
    const candidates = [
      path.resolve(process.cwd(), "src/lib/knowledge-eval-api.ts"),
      path.resolve(process.cwd(), "webui/src/lib/knowledge-eval-api.ts"),
    ];
    const target = candidates.find((item) => existsSync(item));
    expect(target).toBeTruthy();
    const source = readFileSync(String(target), "utf8");
    expect(source).not.toContain("相关块数");
    expect(source).not.toContain("命中查询数");
    expect(source).not.toContain("÷");
  });

  it("listMetrics 同时接受 snake_case 的 metric_id", async () => {
    mockTransport({
      metrics: [
        {
          metric_id: "recall_at_k",
          label: "召回率",
          formula: "相关块数(top-k) ÷ 相关块数(pool)",
          numerator: "top-k 中 grade≥2 的块数",
          denominator: "候选池中 grade≥2 的块数",
          direction: "higher_is_better",
          description: "池相对召回。",
        },
      ],
    });

    const metrics = await knowledgeEvalApi.listMetrics();
    expect(metrics[0].metricId).toBe("recall_at_k");
  });

  it("getRunReport 把 snake_case 指标键映射成 camelCase", async () => {
    mockTransport({
      run_id: "run-1",
      status: "completed",
      metrics: {
        hit_at_k: 1,
        precision_at_k: 0.5,
        recall_at_k: 0.25,
        mrr: 1,
        ndcg_at_k: 0.9,
        pool_coverage: 0.4,
        unscored_rate: 0,
      },
      results: [],
    });

    const report = await knowledgeEvalApi.getRunReport("run-1");

    expect(report.metrics.hitAtK).toBe(1);
    expect(report.metrics.precisionAtK).toBe(0.5);
    expect(report.metrics.recallAtK).toBe(0.25);
    expect(report.metrics.mrr).toBe(1);
    expect(report.metrics.ndcgAtK).toBe(0.9);
    expect(report.metrics.poolCoverage).toBe(0.4);
    expect(report.metrics.unscoredRate).toBe(0);
    expect(report.runId).toBe("run-1");
  });

  it("null 指标不被兜底成 0", async () => {
    mockTransport({
      metrics: {
        hit_at_k: 1,
        precision_at_k: 0.5,
        recall_at_k: null,
        mrr: null,
        ndcg_at_k: null,
        pool_coverage: null,
        unscored_rate: null,
      },
      results: [],
    });

    const report = await knowledgeEvalApi.getRunReport("run-1");

    expect(report.metrics.recallAtK).toBeNull();
    expect(report.metrics.mrr).toBeNull();
    expect(report.metrics.ndcgAtK).toBeNull();
    expect(report.metrics.poolCoverage).toBeNull();
    expect(report.metrics.unscoredRate).toBeNull();
    expect(report.metrics.hitAtK).toBe(1);
  });

  it("缺失的指标键同样保持 null 而不是 0", async () => {
    mockTransport({
      metrics: { hit_at_k: 1 },
      results: [],
    });

    const report = await knowledgeEvalApi.getRunReport("run-1");
    expect(report.metrics.recallAtK).toBeNull();
    expect(report.metrics.precisionAtK).toBeNull();
  });

  it("报告同时含聚合段与逐题段，逐题段做键名映射", async () => {
    mockTransport({
      run_id: "run-1",
      status: "completed",
      metrics: { hit_at_k: 1 },
      results: [
        {
          run_id: "run-1",
          case_id: "case-1",
          status: "scored",
          label_source: "expect",
          metrics: { hit_at_k: 1, precision_at_k: 0.5, recall_at_k: null },
          hits: [
            {
              chunk_id: "chunk-1",
              rank: 1,
              score: 0.87,
              content_hash: "sha256-abc",
              grade: 3,
              reason: "命中期望关键词",
            },
          ],
          error: null,
        },
      ],
    });

    const report = await knowledgeEvalApi.getRunReport("run-1");

    expect(report.results).toHaveLength(1);
    const result = report.results[0];
    expect(result.caseId).toBe("case-1");
    expect(result.status).toBe("scored");
    expect(result.labelSource).toBe("expect");
    expect(result.metrics.hitAtK).toBe(1);
    expect(result.metrics.precisionAtK).toBe(0.5);
    expect(result.metrics.recallAtK).toBeNull();

    // 命中项：chunk_id / content_hash 正确映射。
    expect(result.hits).toHaveLength(1);
    expect(result.hits[0].chunkId).toBe("chunk-1");
    expect(result.hits[0].contentHash).toBe("sha256-abc");
    expect(result.hits[0].rank).toBe(1);
    expect(result.hits[0].score).toBe(0.87);
    expect(result.hits[0].grade).toBe(3);
    expect(result.hits[0].reason).toBe("命中期望关键词");
  });

  it("按 label_source 分组的聚合段也能读出", async () => {
    mockTransport({
      metrics: {
        expect: {
          total_queries: 4,
          scored_queries: 3,
          metrics: { hit_at_k: 1, recall_at_k: null },
        },
        judge: {
          total_queries: 2,
          scored_queries: 0,
          metrics: { hit_at_k: null, recall_at_k: null },
        },
      },
      results: [],
    });

    const report = await knowledgeEvalApi.getRunReport("run-1");

    expect(report.groups).toHaveLength(2);
    const expectGroup = report.groups.find((group) => group.labelSource === "expect");
    expect(expectGroup?.totalQueries).toBe(4);
    expect(expectGroup?.scoredQueries).toBe(3);
    expect(expectGroup?.metrics.hitAtK).toBe(1);
    expect(expectGroup?.metrics.recallAtK).toBeNull();
  });

  it("createRun 发出的请求路径与 method 正确", async () => {
    const { request } = mockTransport({
      id: "run-1",
      dataset_id: "ds-1",
      status: "queued",
      configs: [{ id: "base", name: "基线", overrides: { mode: "keyword", limit: 5 } }],
      created_at: "2026-11-10T00:00:00Z",
      finished_at: null,
    });

    const run = await knowledgeEvalApi.createRun({
      dataset_id: "ds-1",
      configs: [{ id: "base", overrides: { mode: "keyword", limit: 5 } }],
    });

    expect(request).toHaveBeenCalledTimes(1);
    const [path, init] = request.mock.calls[0];
    expect(path).toBe("/knowledge/runs");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();
    expect(JSON.parse(String(init.body))).toEqual({
      dataset_id: "ds-1",
      configs: [{ id: "base", overrides: { mode: "keyword", limit: 5 } }],
    });

    expect(run.id).toBe("run-1");
    expect(run.datasetId).toBe("ds-1");
    expect(run.status).toBe("queued");
    expect(run.createdAt).toBe("2026-11-10T00:00:00Z");
    expect(run.finishedAt).toBeNull();
  });

  it("listRuns 带 dataset_id 查询，getRun 走 /knowledge/runs/{id}", async () => {
    const { request } = mockTransport(
      [{ id: "run-1", dataset_id: "ds-1", status: "completed" }],
      { id: "run-2", dataset_id: "ds-1", status: "running" },
    );

    await knowledgeEvalApi.listRuns("ds-1");
    await knowledgeEvalApi.getRun("run-2");

    expect(request.mock.calls[0][0]).toBe("/knowledge/runs?dataset_id=ds-1");
    expect(request.mock.calls[1][0]).toBe("/knowledge/runs/run-2");
  });

  it("compare 走 /knowledge/compare 并归一化逐指标差", async () => {
    const { request } = mockTransport({
      base_run_id: "run-1",
      candidate_run_id: "run-2",
      metrics: {
        hit_at_k: { base: 0.8, candidate: 0.6, delta: -0.2 },
        recall_at_k: { base: null, candidate: null, delta: null },
      },
      regressed: [{ case_id: "case-1", reason: "recall 变差" }],
    });

    const comparison = await knowledgeEvalApi.compare("run-1", "run-2");

    expect(request.mock.calls[0][0]).toBe(
      "/knowledge/compare?base=run-1&candidate=run-2",
    );
    expect(comparison.baseRunId).toBe("run-1");
    expect(comparison.candidateRunId).toBe("run-2");
    const hit = comparison.metrics.find((item) => item.metricId === "hit_at_k");
    expect(hit?.base).toBe(0.8);
    expect(hit?.candidate).toBe(0.6);
    expect(hit?.delta).toBe(-0.2);
    const recall = comparison.metrics.find((item) => item.metricId === "recall_at_k");
    expect(recall?.delta).toBeNull();
    expect(comparison.regressedCases[0].caseId).toBe("case-1");
  });

  it("数据集 CRUD 走 /knowledge/datasets 前缀", async () => {
    const { request } = mockTransport(
      { id: "ds-1", name: "基线集", notes: "", revision: 1, cases: [] },
      { id: "ds-1", name: "改名", notes: "", revision: 2, cases: [] },
      undefined,
    );

    const created = await knowledgeEvalApi.createDataset({ name: "基线集" });
    expect(request.mock.calls[0][0]).toBe("/knowledge/datasets");
    expect(request.mock.calls[0][1].method).toBe("POST");
    expect(created.revision).toBe("1");

    await knowledgeEvalApi.updateDataset("ds-1", { base_revision: "1", name: "改名" });
    expect(request.mock.calls[1][0]).toBe("/knowledge/datasets/ds-1");
    expect(request.mock.calls[1][1].method).toBe("PATCH");

    await knowledgeEvalApi.deleteDataset("ds-1");
    expect(request.mock.calls[2][0]).toBe("/knowledge/datasets/ds-1");
    expect(request.mock.calls[2][1].method).toBe("DELETE");
  });

  it("错误响应映射成既有的 EvaluationApiError", async () => {
    const fetchMock = vi.fn();
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        { error: { code: "KNOWLEDGE_RUN_NOT_FOUND", message: "运行不存在" } },
        404,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const error = await knowledgeEvalApi.getRun("missing").then(
      () => null,
      (err: unknown) => err,
    );

    expect(error).toBeInstanceOf(EvaluationApiError);
    expect(error).toMatchObject({
      code: "KNOWLEDGE_RUN_NOT_FOUND",
      status: 404,
    });
  });
});
