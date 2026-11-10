import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KnowledgeEvalPage } from "@/components/workspace/KnowledgeEvalPage";
import {
  knowledgeEvalApi,
  type KnowledgeEvalCaseResult,
  type KnowledgeEvalDataset,
  type KnowledgeEvalMetricGroup,
  type KnowledgeEvalRun,
  type KnowledgeMetricSpec,
} from "@/lib/knowledge-eval-api";
import i18n from "@/i18n";
import { parseWorkspaceHash, workspaceRouteHash } from "@/workspace/routes";

vi.mock("@/lib/knowledge-eval-api", () => ({
  knowledgeEvalApi: {
    listMetrics: vi.fn(),
    listDatasets: vi.fn(),
    listRuns: vi.fn(),
    getRun: vi.fn(),
    getRunReport: vi.fn(),
    createDataset: vi.fn(),
    updateDataset: vi.fn(),
    deleteDataset: vi.fn(),
    createRun: vi.fn(),
    cancelRun: vi.fn(),
    compare: vi.fn(),
  },
}));

const api = vi.mocked(knowledgeEvalApi);

const SPECS: KnowledgeMetricSpec[] = [
  {
    metricId: "precision_at_k",
    label: "准确率",
    formula: "相关块数(top-k) ÷ k",
    numerator: "top-k 中 grade≥2 的块数",
    denominator: "k",
    direction: "higher_is_better",
    description: "返回结果里有多少块真的回答了问题。",
  },
  {
    metricId: "recall_at_k",
    label: "召回率",
    formula: "相关块数(top-k) ÷ 相关块数(pool)",
    numerator: "top-k 中 grade≥2 的块数",
    denominator: "候选池中 grade≥2 的块数",
    direction: "higher_is_better",
    description: "池相对召回。",
  },
  {
    metricId: "hit_at_k",
    label: "命中率",
    formula: "命中查询数 ÷ 可评查询数",
    numerator: "top-k 含 ≥1 相关块的查询数",
    denominator: "全部可评查询数",
    direction: "higher_is_better",
    description: "有多少条查询至少找到一条相关块。",
  },
  {
    metricId: "mrr",
    label: "首个命中倒数",
    formula: "平均( 1 ÷ 首个相关块名次 )",
    numerator: "—",
    denominator: "—",
    direction: "higher_is_better",
    description: "相关块排得越靠前越高。",
  },
  {
    metricId: "ndcg_at_k",
    label: "归一化折损累计增益",
    formula: "DCG ÷ IDCG，其中 DCG = Σ (2^grade − 1) ÷ log₂(名次+1)",
    numerator: "—",
    denominator: "—",
    direction: "higher_is_better",
    description: "兼顾分级相关性与排名位置。",
  },
  {
    metricId: "pool_coverage",
    label: "候选池相关占比",
    formula: "相关块数(pool) ÷ 池候选总数",
    numerator: "池中 grade≥2 的块数",
    denominator: "池候选总数",
    direction: "higher_is_better",
    description: "池本身的质量。",
  },
  {
    metricId: "unscored_rate",
    label: "未评率",
    formula: "not_assessable 查询数 ÷ 总查询数",
    numerator: "not_assessable 查询数",
    denominator: "全部查询数",
    direction: "lower_is_better",
    description: "缺证据的查询占比。",
  },
];

const GROUPS: KnowledgeEvalMetricGroup[] = [
  {
    labelSource: "expect",
    totalQueries: 3,
    scoredQueries: 2,
    metrics: {
      hitAtK: 1,
      precisionAtK: 0.5,
      mrr: 0.75,
      ndcgAtK: 0.8,
      recallAtK: 0.5,
      poolCoverage: 0.4,
      unscoredRate: 0.333,
    },
  },
];

const DATASETS: KnowledgeEvalDataset[] = [
  {
    id: "ds_1",
    name: "检索基线",
    notes: "",
    revision: "1",
    cases: [
      {
        id: "c1",
        query: "如何回滚部署",
        expectAny: ["回滚"],
        kbIds: [],
        mode: "hybrid",
        limit: 8,
        labelSource: "expect",
        ordinal: 0,
      },
    ],
    createdAt: null,
    updatedAt: null,
  },
];

const RUN: KnowledgeEvalRun = {
  id: "run_1",
  datasetId: "ds_1",
  status: "completed",
  configs: [{ id: "base", name: "基线", overrides: { mode: "keyword", limit: 5 } }],
  judge: {},
  budgets: {},
  metrics: GROUPS,
  progress: {},
  degraded: [],
  error: null,
  createdAt: null,
  finishedAt: null,
};

function makeResult(overrides: Partial<KnowledgeEvalCaseResult>): KnowledgeEvalCaseResult {
  return {
    caseId: "c1::base",
    status: "scored",
    labelSource: "expect",
    metrics: {
      hitAtK: 1,
      precisionAtK: 0.5,
      mrr: 1,
      ndcgAtK: 0.9,
      recallAtK: 0.5,
      poolCoverage: 0.4,
      unscoredRate: null,
    },
    hits: [],
    error: null,
    ...overrides,
  };
}

describe("knowledge-eval route", () => {
  it("parses #/knowledge-eval", () => {
    expect(parseWorkspaceHash("#/knowledge-eval").view).toBe("knowledge-eval");
  });

  it("round-trips the hash", () => {
    expect(workspaceRouteHash({ view: "knowledge-eval" })).toBe("#/knowledge-eval");
  });
});

describe("KnowledgeEvalPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listMetrics.mockResolvedValue(SPECS);
    api.listDatasets.mockResolvedValue(DATASETS);
    api.listRuns.mockResolvedValue([RUN]);
    api.getRunReport.mockResolvedValue({
      runId: "run_1",
      status: "completed",
      metrics: GROUPS[0].metrics,
      groups: GROUPS,
      results: [makeResult({})],
    });
  });

  it("renders the overview, guide, per-case table and the three sections", async () => {
    render(<KnowledgeEvalPage />);

    await waitFor(() => {
      expect(screen.getByTestId("knowledge-eval-overview")).toBeInTheDocument();
    });
    expect(screen.getByTestId("knowledge-eval-metric-guide")).toBeInTheDocument();
    expect(screen.getByTestId("knowledge-eval-case-results")).toBeInTheDocument();
    expect(screen.getByTestId("knowledge-eval-datasets")).toBeInTheDocument();
    expect(screen.getByTestId("knowledge-eval-runs")).toBeInTheDocument();
    expect(screen.getByTestId("knowledge-eval-compare")).toBeInTheDocument();
  });

  it("shows the metric formulas delivered by the backend", async () => {
    render(<KnowledgeEvalPage />);

    await waitFor(() => {
      expect(screen.getByTestId("knowledge-eval-metric-guide")).toBeInTheDocument();
    });
    // 公式串只能来自 GET /knowledge/metrics——组件里不得内置。
    await waitFor(() => {
      expect(screen.getByText("相关块数(top-k) ÷ k")).toBeInTheDocument();
    });
    expect(screen.getByText("相关块数(top-k) ÷ 相关块数(pool)")).toBeInTheDocument();
  });

  it("marks unscored cases as not scored instead of zero", async () => {
    api.getRunReport.mockResolvedValue({
      runId: "run_1",
      status: "completed",
      metrics: GROUPS[0].metrics,
      groups: GROUPS,
      results: [
        makeResult({
          caseId: "c2::base",
          status: "not_assessable",
          metrics: {
            hitAtK: null,
            precisionAtK: null,
            mrr: null,
            ndcgAtK: null,
            recallAtK: null,
            poolCoverage: null,
            unscoredRate: null,
          },
        }),
      ],
    });

    render(<KnowledgeEvalPage />);

    await waitFor(() => {
      expect(screen.getByTestId("knowledge-eval-case-results")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText(i18n.t("knowledge.eval.result.notScored"))).toBeInTheDocument();
    });
  });

  it("flags a degraded run", async () => {
    api.listRuns.mockResolvedValue([{ ...RUN, degraded: ["vector_store_unavailable"] }]);

    render(<KnowledgeEvalPage />);

    await waitFor(() => {
      expect(screen.getByTestId("knowledge-eval-degraded-badge")).toBeInTheDocument();
    });
  });
});
