import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  async function openReport(user: ReturnType<typeof userEvent.setup>) {
    await waitFor(() => {
      expect(screen.getByTestId("knowledge-eval-runs")).toBeInTheDocument();
    });
    await user.click(screen.getByRole("tab", { name: i18n.t("knowledge.eval.tab.runs") }));
    await user.click(
      await screen.findByRole("button", { name: i18n.t("knowledge.eval.action.viewReport") }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("knowledge-eval-run-header")).toBeInTheDocument();
    });
  }

  it("keeps the list page to datasets / runs / compare", async () => {
    render(<KnowledgeEvalPage />);

    await waitFor(() => {
      expect(screen.getByTestId("knowledge-eval-datasets")).toBeInTheDocument();
    });
    expect(screen.getByTestId("knowledge-eval-runs")).toBeInTheDocument();
    expect(screen.getByTestId("knowledge-eval-compare")).toBeInTheDocument();
    // 两级结构：列表页不再铺概览卡、逐题表与口径区。
    expect(screen.queryByTestId("knowledge-eval-overview")).toBeNull();
    expect(screen.queryByTestId("knowledge-eval-case-results")).toBeNull();
    expect(screen.queryByTestId("knowledge-eval-metric-guide")).toBeNull();
  });

  it("defaults to records when runs exist and keeps the user's tab on refresh", async () => {
    const user = userEvent.setup();
    render(<KnowledgeEvalPage />);
    const runs = await screen.findByRole("tab", { name: i18n.t("knowledge.eval.tab.runs") });
    await waitFor(() => expect(runs).toHaveAttribute("aria-selected", "true"));
    expect(screen.queryByRole("combobox", { name: i18n.t("knowledge.eval.run.dataset") })).toBeNull();
    await user.click(screen.getByRole("tab", { name: i18n.t("knowledge.eval.tab.datasets") }));
    await user.click(screen.getByRole("button", { name: i18n.t("knowledge.eval.action.refresh") }));
    expect(screen.getByRole("tab", { name: i18n.t("knowledge.eval.tab.datasets") })).toHaveAttribute("aria-selected", "true");
  });

  it("submits exactly once while creation is pending", async () => {
    const user = userEvent.setup();
    api.createRun.mockImplementation(() => new Promise(() => {}));
    render(<KnowledgeEvalPage />);
    await user.click(await screen.findByRole("button", { name: i18n.t("knowledge.experience.newEval") }));
    await user.dblClick(screen.getByRole("button", { name: i18n.t("knowledge.eval.action.run") }));
    expect(api.createRun).toHaveBeenCalledTimes(1);
  });

  it("preserves record filtering and focus after returning from a report", async () => {
    const user = userEvent.setup(); render(<KnowledgeEvalPage />);
    const filter = await screen.findByRole("textbox", { name: i18n.t("knowledge.experience.filterRuns") });
    await user.type(filter,"run_1"); await openReport(user);
    await user.click(screen.getByRole("button", {name:new RegExp(i18n.t("knowledge.eval.title"))}));
    expect(screen.getByRole("textbox",{name: i18n.t("knowledge.experience.filterRuns")})).toHaveValue("run_1");
    await waitFor(()=>expect(screen.getByRole("button",{name:i18n.t("knowledge.eval.action.viewReport")})).toHaveFocus());
  });

  it("requires privacy confirmation for judge cases before submitting", async () => {
    api.listDatasets.mockResolvedValue([{...DATASETS[0],cases:[{...DATASETS[0].cases[0],labelSource:"judge",expectAny:[]}]}]);
    api.createRun.mockImplementation(()=>new Promise(()=>{}));
    const user=userEvent.setup();render(<KnowledgeEvalPage />);
    await user.click(await screen.findByRole("button",{name: i18n.t("knowledge.experience.newEval")}));
    await user.click(screen.getByRole("button",{name:i18n.t("knowledge.eval.action.run")}));
    expect(api.createRun).not.toHaveBeenCalled();
    const privacy=screen.getByRole("dialog",{name:i18n.t("knowledge.eval.hint.judgePrivacyTitle")});
    await user.dblClick(within(privacy).getByRole("button",{name:i18n.t("knowledge.eval.action.confirm")}));
    expect(api.createRun).toHaveBeenCalledTimes(1);
  });

  it("cancels a running run and refreshes the open report on terminal status", async () => {
    api.listRuns.mockResolvedValueOnce([{...RUN,status:"running"}]).mockResolvedValue([{...RUN,status:"cancelled"}]);
    api.cancelRun.mockResolvedValue({...RUN,status:"cancelled"});
    const user=userEvent.setup();render(<KnowledgeEvalPage />);
    await user.click(await screen.findByRole("button",{name:i18n.t("knowledge.eval.action.cancel")}));
    expect(api.cancelRun).toHaveBeenCalledWith(RUN.id);
    await openReport(user);
    expect(api.getRunReport).toHaveBeenCalledWith(RUN.id);
  });

  it("fetches the report again when polling changes a run to terminal", async () => {
    api.listRuns.mockResolvedValueOnce([{...RUN,status:"running"}]).mockResolvedValue([RUN]);
    const user=userEvent.setup();render(<KnowledgeEvalPage />);await openReport(user);
    await waitFor(()=>expect(api.getRunReport).toHaveBeenCalledTimes(2),{timeout:4500});
    expect(api.listRuns).toHaveBeenCalledTimes(2);
  });

  it("locates a regression using the complete query and config identity", async () => {
    api.listRuns.mockResolvedValue([RUN,{...RUN,id:"run_2"}]);
    api.compare.mockResolvedValue({baseRunId:RUN.id,candidateRunId:"run_2",metrics:[],regressedCases:[{caseId:"c1",configId:"base",reason:"rank fell",metrics:GROUPS[0].metrics}]});
    const user=userEvent.setup();render(<KnowledgeEvalPage />);
    await user.click(await screen.findByRole("tab",{name:i18n.t("knowledge.eval.tab.compare")}));
    await user.selectOptions(screen.getByRole("combobox",{name:i18n.t("knowledge.eval.compare.base")}),RUN.id);
    await user.selectOptions(screen.getByRole("combobox",{name:i18n.t("knowledge.eval.compare.candidate")}),"run_2");
    await user.click(screen.getByRole("button",{name:i18n.t("knowledge.eval.action.compare")}));
    await user.click(await screen.findByRole("button",{name: i18n.t("knowledge.experience.viewRegression")}));
    const rows=await screen.findAllByTestId("knowledge-eval-case-c1::base");
    expect(rows).toHaveLength(1);
    await user.click(within(rows[0]).getByRole("button",{name:"如何回滚部署"}));
    expect(screen.getByRole("dialog")).toHaveTextContent("c1::base");
  });

  it("shows hit evidence with null grades distinct from zero", async () => {
    api.getRunReport.mockResolvedValue({ runId: RUN.id, status: "completed", metrics: GROUPS[0].metrics, groups: GROUPS,
      results: [makeResult({ hits: [
        { chunkId: "chunk-null", rank: 1, score: 0.8, contentHash: "h", grade: null, reason: "缺少证据" },
        { chunkId: "chunk-zero", rank: 2, score: 0.2, contentHash: "h2", grade: 0, reason: "不相关" },
      ] })] });
    const user = userEvent.setup();
    render(<KnowledgeEvalPage />);
    await openReport(user);
    await user.click(await screen.findByRole("button", { name: "如何回滚部署" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("chunk-null")).toBeInTheDocument();
    expect(within(dialog).getByText(i18n.t("knowledge.eval.result.notScored"))).toBeInTheDocument();
    expect(within(dialog).getByText("0")).toBeInTheDocument();
    expect(within(dialog).getByText("缺少证据")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(()=>expect(screen.getByRole("button",{name:"如何回滚部署"})).toHaveFocus());
  });

  it("ignores an old report failure after opening another run", async () => {
    let rejectOld!: (reason: Error) => void;
    api.listRuns.mockResolvedValue([RUN, { ...RUN, id: "run_2" }]);
    api.getRunReport.mockImplementation((id) => id === "run_1" ? new Promise((_resolve, reject) => { rejectOld = reject; }) : Promise.resolve({ runId: "run_2", status: "completed", metrics: GROUPS[0].metrics, groups: GROUPS, results: [makeResult({})] }));
    const user = userEvent.setup();
    render(<KnowledgeEvalPage />);
    await user.click(within(await screen.findByTestId("knowledge-eval-run-run_1")).getByRole("button", { name: i18n.t("knowledge.eval.action.viewReport") }));
    expect(screen.getByRole("status", { name: i18n.t("knowledge.experience.reportLoading") })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: new RegExp(i18n.t("knowledge.eval.title")) }));
    await user.click(within(screen.getByTestId("knowledge-eval-run-run_2")).getByRole("button", { name: i18n.t("knowledge.eval.action.viewReport") }));
    await screen.findByText("如何回滚部署");
    await act(async () => rejectOld(new Error("obsolete failure")));
    expect(screen.queryByText(/obsolete failure/)).toBeNull();
  });

  it("opens a run report as a second page", async () => {
    const user = userEvent.setup();
    render(<KnowledgeEvalPage />);

    await openReport(user);

    expect(screen.getByTestId("knowledge-eval-overview")).toBeInTheDocument();
    expect(screen.getByTestId("knowledge-eval-case-results")).toBeInTheDocument();
    expect(screen.getByTestId("knowledge-eval-metric-guide")).toBeInTheDocument();
    // 详情页不再显示列表的分区。
    expect(screen.queryByTestId("knowledge-eval-datasets")).toBeNull();

    await user.click(
      screen.getByRole("button", { name: new RegExp(i18n.t("knowledge.eval.title")) }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("knowledge-eval-datasets")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("knowledge-eval-overview")).toBeNull();
  });

  it("shows the metric formulas delivered by the backend", async () => {
    const user = userEvent.setup();
    render(<KnowledgeEvalPage />);

    await openReport(user);

    // 公式串只能来自 GET /knowledge/metrics——组件里不得内置。
    // 同一公式会在概览卡与口径表各出现一次。
    await waitFor(() => {
      expect(screen.getAllByText("相关块数(top-k) ÷ k").length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText("相关块数(top-k) ÷ 相关块数(pool)").length).toBeGreaterThan(0);
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
    const user = userEvent.setup();
    render(<KnowledgeEvalPage />);

    await openReport(user);

    await waitFor(() => {
      expect(screen.getByText(i18n.t("knowledge.eval.result.notScored"))).toBeInTheDocument();
    });
  });

  it("flags a degraded run", async () => {
    api.listRuns.mockResolvedValue([{ ...RUN, degraded: ["vector_store_unavailable"] }]);
    const user = userEvent.setup();
    render(<KnowledgeEvalPage />);

    await openReport(user);

    expect(screen.getByTestId("knowledge-eval-degraded-badge")).toBeInTheDocument();
  });

  it("keeps failed, unassessable, scored-null and actual zero distinct", async () => {
    api.getRunReport.mockResolvedValue({runId:RUN.id,status:"completed",metrics:GROUPS[0].metrics,groups:[...GROUPS,{...GROUPS[0],labelSource:"judge"}],results:[
      makeResult({caseId:"failed::base",status:"failed",error:"judge timed out"}),
      makeResult({caseId:"missing::base",status:"not_assessable"}),
      makeResult({caseId:"null::base",metrics:{...GROUPS[0].metrics,precisionAtK:null}}),
      makeResult({caseId:"zero::base",metrics:{...GROUPS[0].metrics,precisionAtK:0}}),
    ]});
    const user=userEvent.setup();render(<KnowledgeEvalPage />);await openReport(user);
    expect(await screen.findByText("judge timed out")).toBeInTheDocument();
    expect(within(screen.getByTestId("knowledge-eval-case-failed::base")).getByText(i18n.t("knowledge.eval.status.failed"))).toBeInTheDocument();
    expect(within(screen.getByTestId("knowledge-eval-case-null::base")).getByText(i18n.t("knowledge.eval.result.notScored"))).toBeInTheDocument();
    expect(within(screen.getByTestId("knowledge-eval-case-zero::base")).getByText("0.000")).toBeInTheDocument();
    expect(within(screen.getByTestId("knowledge-eval-overview")).getByText(i18n.t("knowledge.eval.badge.labelSource.judge"))).toBeInTheDocument();
  });

  it("opens the operation guide from the header", async () => {
    const user = userEvent.setup();
    render(<KnowledgeEvalPage />);

    await waitFor(() => {
      expect(screen.getByTestId("knowledge-eval-datasets")).toBeInTheDocument();
    });
    await user.click(
      screen.getByRole("button", { name: i18n.t("knowledge.eval.manual.open") }),
    );

    const dialog = screen.getByRole("dialog", {
      name: i18n.t("knowledge.eval.manual.title"),
    });
    expect(dialog).toBeInTheDocument();
    for (let step = 1; step <= 5; step += 1) {
      expect(
        within(dialog).getByText(i18n.t(`knowledge.eval.manual.steps.${step}.title`)),
      ).toBeInTheDocument();
    }
    expect(
      within(dialog).getByRole("button", { name: i18n.t("knowledge.eval.manual.download") }),
    ).toBeInTheDocument();
  });

  it("imports a dataset from the CSV template", async () => {
    const user = userEvent.setup();
    api.createDataset.mockResolvedValue(DATASETS[0]);
    render(<KnowledgeEvalPage />);

    await waitFor(() => {
      expect(screen.getByTestId("knowledge-eval-datasets")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("tab", { name: i18n.t("knowledge.eval.tab.datasets") }));

    const file = new File(
      ["query,expect_any\r\n如何回滚部署,回滚|kubectl\r\n数据库多久备份一次,备份\r\n"],
      "模板导入.csv",
      { type: "text/csv" },
    );
    await user.upload(
      screen.getByLabelText(i18n.t("knowledge.eval.manual.import")),
      file,
    );

    await waitFor(() => {
      expect(api.createDataset).toHaveBeenCalled();
    });
    const payload = api.createDataset.mock.calls[0][0] as {
      name: string;
      cases: Array<Record<string, unknown>>;
    };
    expect(payload.name).toBe("模板导入");
    expect(payload.cases).toEqual([
      { query: "如何回滚部署", expect_any: ["回滚", "kubectl"] },
      { query: "数据库多久备份一次", expect_any: ["备份"] },
    ]);
  });

  it("keeps a dirty dataset draft on conflict and asks before leaving", async () => {
    const user = userEvent.setup();
    api.updateDataset.mockRejectedValue(new Error("409 revision conflict"));
    render(<KnowledgeEvalPage />);
    await user.click(await screen.findByRole("tab", { name: i18n.t("knowledge.eval.tab.datasets") }));
    await user.click(screen.getByRole("button", { name: "检索基线" }));
    const input = screen.getByRole("textbox", { name: i18n.t("knowledge.eval.dataset.expect") });
    await user.clear(input); await user.type(input, "新的答案");
    await user.click(screen.getByRole("button", { name: i18n.t("knowledge.eval.action.save") }));
    await within(screen.getByRole("dialog")).findByText(/409 revision conflict/);
    expect(input).toHaveValue("新的答案");
    await user.keyboard("{Escape}");
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: i18n.t("knowledge.experience.keepEditing") }));
    expect(input).toHaveValue("新的答案");
  });
});
