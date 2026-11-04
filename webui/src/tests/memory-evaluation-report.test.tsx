import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/memory-evaluation-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/memory-evaluation-api")>(
    "@/lib/memory-evaluation-api",
  );
  return {
    ...actual,
    memoryEvaluationApi: {
      listExperiments: vi.fn(),
      listDatasets: vi.fn(),
      capabilities: vi.fn(),
      getExperiment: vi.fn(),
      getExperimentMetrics: vi.fn(),
      cancelExperiment: vi.fn(),
      createExperiment: vi.fn(),
      createComparison: vi.fn(),
      getComparison: vi.fn(),
      downloadComparisonReport: vi.fn(),
      importSample: vi.fn(),
      importDataset: vi.fn(),
      listAttemptEvents: vi.fn(),
      listAttemptProbes: vi.fn(),
      addReview: vi.fn(),
    },
  };
});

vi.mock("@/lib/evaluation-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/evaluation-api")>(
    "@/lib/evaluation-api",
  );
  return {
    ...actual,
    evaluationApi: {
      overview: vi.fn(),
      listGoldSamples: vi.fn(),
      listAttempts: vi.fn(),
      confirmGoldSample: vi.fn(),
      createCalibration: vi.fn(),
      listProfiles: vi.fn(),
      listRuns: vi.fn(),
      listSuites: vi.fn(),
      listCases: vi.fn(),
    },
  };
});

import { EvaluationSettings } from "@/components/settings/evaluation/EvaluationSettings";
import {
  MemoryComparison,
  memoryDeltaLabel,
} from "@/components/settings/evaluation/memory/MemoryComparison";
import { MemoryExperimentDetail } from "@/components/settings/evaluation/memory/MemoryExperimentDetail";
import i18n from "@/i18n";
import { evaluationApi } from "@/lib/evaluation-api";
import {
  memoryEvaluationApi,
  type MemoryComparisonDetail,
  type MemoryExperimentDetail as ExperimentDetail,
} from "@/lib/memory-evaluation-api";

const getComparison = vi.mocked(memoryEvaluationApi.getComparison);
const getExperiment = vi.mocked(memoryEvaluationApi.getExperiment);
const getExperimentMetrics = vi.mocked(memoryEvaluationApi.getExperimentMetrics);
const cancelExperiment = vi.mocked(memoryEvaluationApi.cancelExperiment);
const listExperiments = vi.mocked(memoryEvaluationApi.listExperiments);
const listDatasets = vi.mocked(memoryEvaluationApi.listDatasets);
const capabilities = vi.mocked(memoryEvaluationApi.capabilities);
const overview = vi.mocked(evaluationApi.overview);
const listGoldSamples = vi.mocked(evaluationApi.listGoldSamples);

function comparisonFixture(overrides: Partial<MemoryComparisonDetail> = {}): MemoryComparisonDetail {
  return {
    id: "cmp-12345678",
    baseline_experiment_id: "exp-base-1",
    candidate_experiment_id: "exp-cand-1",
    baseline_candidate_id: "cand-base",
    candidate_candidate_id: "cand-new",
    changed_fields: ["memory_settings.recall_limit"],
    policy: {},
    status: "completed",
    created_at: "2026-11-02T08:00:00Z",
    data: [
      {
        scenario_id: "s-1",
        revision: 1,
        status: "regressed",
        baseline: 0.9,
        candidate: 0.5,
        delta: -0.4,
        delta_pp: -40,
        baseline_trials: 1,
        candidate_trials: 1,
        probe_changes: [{ probe_id: "p1", baseline: 1, candidate: 0 }],
        reasons: [],
      },
    ],
    next_after_key: "s-1",
    statistics: {
      status: "completed",
      measurement_status: "complete",
      coverage: {
        scenes: { paired: 30, planned: 30 },
        trials: {
          baseline: { graded: 30, planned: 30 },
          candidate: { graded: 30, planned: 30 },
        },
      },
      scenario_macro: { baseline: 0.8, candidate: 0.9, delta: 0.1, delta_pp: 10 },
      probe_micro: { baseline: 0.8, candidate: 0.9, delta_pp: 10 },
      primary: {
        metric_id: "qa_accuracy",
        baseline: 0.8,
        candidate: 0.9,
        delta: 0.1,
        delta_pp: 10,
        statistical_direction: "improved",
      },
      interval: null,
      statistical_direction: "improved",
      adoption_decision: { decision: "blocked", reasons: ["CRITICAL:scope_leak"] },
      gates: {
        blocked: true,
        critical_violations: ["scope_leak"],
        scope_leak: { candidate_failed: true },
      },
      secondary: [
        {
          metric_id: "extraction_precision",
          direction: "higher_is_better",
          baseline: null,
          candidate: null,
          delta_pp: null,
          improvement_pp: null,
          interval_pp: null,
          statistical_direction: "incomparable",
          pairs: 0,
          missing: 30,
          clusters: null,
        },
        {
          metric_id: "evidence_recall_at_k",
          direction: "higher_is_better",
          baseline: 0.6,
          candidate: 0.5,
          delta_pp: -10,
          improvement_pp: -10,
          interval_pp: null,
          statistical_direction: "inconclusive",
          pairs: 30,
          missing: 0,
          clusters: null,
        },
      ],
      category_macro: [{ category: "事实记忆", scenes: 30, baseline: 0.8, candidate: 0.9, delta_pp: 10 }],
      cost: { summary: { cost_ratio: null, p95_latency_ratio: null } },
      reasons: ["INTERVALS_SKIPPED:CLUSTERS_LT_20"],
    },
    intervals: null,
    excluded: [],
    reasons: ["INTERVALS_SKIPPED:CLUSTERS_LT_20"],
    coverage: null,
    ...overrides,
  };
}

function experimentFixture(overrides: Partial<ExperimentDetail> = {}): ExperimentDetail {
  return {
    id: "exp-run-1",
    suite_id: "suite-1",
    dataset_revision_id: "rev-1",
    split: "regression",
    track: "end_to_end",
    status: "running",
    measurement_status: "incomplete",
    planned_trials: 120,
    repetitions: 1,
    model_mode: "real",
    primary_metric: "qa_accuracy",
    created_at: "2026-11-02T08:00:00Z",
    updated_at: "2026-11-02T08:10:00Z",
    candidates: [
      {
        id: "cand-new",
        name: "候选 A",
        mode: "zhixu",
        agent_profile_id: "model:fast",
        memory_settings: {},
      },
    ],
    judge_profile_id: null,
    policy: {},
    frozen_manifest: {},
    budget: { max_trials: 200, max_total_cost: null },
    child_runs: [
      {
        id: "child-1",
        experiment_id: "exp-run-1",
        candidate_id: "cand-new",
        batch_index: 0,
        run_id: "run-batch-0",
        scenario_ids: ["s-1"],
        status: "running",
        created_at: "2026-11-02T08:01:00Z",
      },
    ],
    progress: {
      planned_trials: 120,
      child_runs: 1,
      child_status_counts: { running: 1 },
      attempts: 40,
      terminal_attempts: 12,
    },
    coverage: { measurement_status: "incomplete", planned: 120, scored: 12 },
    ...overrides,
  };
}

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage("zh-CN");
  getComparison.mockResolvedValue(comparisonFixture());
  listExperiments.mockResolvedValue({ data: [], next_after_key: null });
  listDatasets.mockResolvedValue({ data: [], next_after_key: null });
  capabilities.mockResolvedValue({
    tracks: [
      {
        id: "end_to_end",
        supported: true,
        missing: [],
        required_metrics: ["qa_accuracy"],
        primary_metric: "qa_accuracy",
      },
    ],
    settings: [],
    profiles: [],
    capabilities: {},
    missing: [],
    retrieval_channels: [],
  });
  overview.mockResolvedValue({
    recent_runs: [],
    metrics: {
      planned: 0,
      passed: 0,
      failed: 0,
      review_required: 0,
      not_evaluated: 0,
      overall_pass_rate: null,
      evaluated_pass_rate: null,
      evaluation_coverage: null,
      tool_case_pass_rate: null,
      tool_eligible_cases: 0,
      tool_valid_cases: 0,
      quality: {},
      agent_duration: {},
      usage: {},
    },
    status_counts: {},
  } as unknown as Awaited<ReturnType<typeof evaluationApi.overview>>);
  listGoldSamples.mockResolvedValue({ gold_revision: 1, data: [] });
});

afterEach(() => cleanup());

describe("memoryDeltaLabel", () => {
  it("states missing evidence instead of zero", () => {
    expect(memoryDeltaLabel(null)).toBe("证据不足");
  });

  it("keeps the percentage-point unit and sign", () => {
    expect(memoryDeltaLabel(0.123)).toBe("+12.3 个百分点");
    expect(memoryDeltaLabel(-0.0345)).toBe("-3.5 个百分点");
    expect(memoryDeltaLabel(0)).toBe("0.0 个百分点");
  });
});

describe("MemoryComparison", () => {
  it("separates statistical direction from adoption advice and lets a critical failure win", async () => {
    render(<MemoryComparison comparisonId="cmp-12345678" />);

    const direction = await screen.findByTestId("comparison-direction");
    expect(within(direction).getByText("统计方向")).toBeInTheDocument();
    expect(within(direction).getByText("有证据支持提升")).toBeInTheDocument();
    expect(within(direction).getByText("+10.0 个百分点")).toBeInTheDocument();

    const adoption = screen.getByTestId("comparison-adoption");
    expect(within(adoption).getByText("采用建议")).toBeInTheDocument();
    expect(within(adoption).getByText("关键项阻止采用")).toBeInTheDocument();
    // 采用建议里的关键原因不会被平均提升掩盖。
    expect(
      screen.getByTestId("comparison-critical").textContent,
    ).toContain("关键门禁未通过");

    expect(screen.getByText("固定条件")).toBeInTheDocument();
    expect(screen.getByText("memory_settings.recall_limit")).toBeInTheDocument();
    expect(screen.getByTestId("comparison-snapshot").textContent).toContain("统计来自该快照");
  });

  it("hides the interval below 20 clusters and explains the targeted precision gap", async () => {
    render(<MemoryComparison comparisonId="cmp-12345678" />);

    await screen.findByTestId("comparison-direction");
    expect(screen.getByTestId("comparison-interval").textContent).toContain(
      "独立 cluster 少于 20 个",
    );
    expect(screen.queryByText(/95%/)).not.toBeInTheDocument();
    expect(
      screen.getByText("targeted 标注不输出抽取精确率：未标注的非事实不在分母里。"),
    ).toBeInTheDocument();
  });

  it("pages cases with the opaque cursor and never recomputes the statistics", async () => {
    render(<MemoryComparison comparisonId="cmp-12345678" />);
    await screen.findByTestId("comparison-pair-s-1");

    getComparison.mockResolvedValueOnce(
      comparisonFixture({
        data: [
          {
            scenario_id: "s-3",
            revision: 1,
            status: "improved",
            baseline: 0.4,
            candidate: 0.8,
            delta: 0.4,
            delta_pp: 40,
            baseline_trials: 1,
            candidate_trials: 1,
            probe_changes: [],
            reasons: [],
          },
        ],
        next_after_key: null,
      }),
    );

    await userEvent.setup().click(screen.getByRole("button", { name: "加载更多" }));
    await screen.findByTestId("comparison-pair-s-3");
    expect(getComparison).toHaveBeenLastCalledWith("cmp-12345678", {
      limit: 50,
      status: undefined,
      after_key: "s-1",
    });
    expect(screen.getByTestId("comparison-pair-s-1")).toBeInTheDocument();
    expect(screen.getByText("固定条件")).toBeInTheDocument();
  });

  it("links each scenario to the probe review in replay", async () => {
    const onOpenReplay = vi.fn();
    render(<MemoryComparison comparisonId="cmp-12345678" onOpenReplay={onOpenReplay} />);

    const row = await screen.findByTestId("comparison-pair-s-1");
    await userEvent.setup().click(within(row).getByRole("button", { name: "打开回放复核" }));
    expect(onOpenReplay).toHaveBeenCalledWith("s-1");
  });
});

describe("MemoryExperimentDetail", () => {
  it("shows provisional progress, budget gaps and unscored metrics without inventing zero", async () => {
    getExperiment.mockResolvedValue(experimentFixture());
    getExperimentMetrics.mockResolvedValue({
      metrics_version: 1,
      aggregation: "probe_micro_mean",
      metrics: [
        {
          id: "extraction_precision",
          version: 1,
          unit: "ratio",
          value: null,
          numerator: null,
          denominator: null,
          status: "not_assessable",
          scope: "dataset",
          reasons: ["TARGETED_SCOPE_NO_FULL_PRECISION"],
          coverage: { scored: 0, planned: 30, reasons: [] },
        },
        {
          id: "qa_accuracy",
          version: 1,
          unit: "ratio",
          value: 0.75,
          numerator: 30,
          denominator: 40,
          status: "scored",
          scope: "dataset",
          reasons: [],
          coverage: { scored: 30, planned: 40, reasons: [] },
        },
      ],
      scope: {},
      coverage: {},
    });

    render(<MemoryExperimentDetail experimentId="exp-run-1" />);

    expect(await screen.findByTestId("detail-provisional")).toBeInTheDocument();
    expect(screen.getByTestId("detail-incomplete")).toBeInTheDocument();
    expect(screen.getByText("预算")).toBeInTheDocument();
    expect(screen.getByText("未设置")).toBeInTheDocument();
    await userEvent.setup().click(await screen.findByRole("button", { name: /展开无法评估的/ }));
    const precision = await screen.findByTestId("metric-extraction_precision");
    expect(within(precision).getByText("未评分")).toBeInTheDocument();
    expect(within(precision).queryByText(/0%/)).not.toBeInTheDocument();
    expect(
      within(precision).getByText("targeted 标注不输出抽取精确率：未标注的非事实不在分母里。"),
    ).toBeInTheDocument();
  });

  it("keeps unscored metrics collapsed until the reader asks for them", async () => {
    getExperiment.mockResolvedValue(experimentFixture());
    getExperimentMetrics.mockResolvedValue({
      metrics_version: 1,
      aggregation: "probe_micro_mean",
      metrics: [
        {
          id: "extraction_precision",
          version: 1,
          unit: "ratio",
          value: null,
          numerator: null,
          denominator: null,
          status: "not_assessable",
          scope: "dataset",
          reasons: ["TARGETED_SCOPE_NO_FULL_PRECISION"],
          coverage: { scored: 0, planned: 30, reasons: [] },
        },
      ],
      scope: {},
      coverage: {},
    });

    render(<MemoryExperimentDetail experimentId="exp-run-1" />);

    const toggle = await screen.findByRole("button", { name: /展开无法评估的/ });
    expect(screen.queryByTestId("metric-extraction_precision")).not.toBeInTheDocument();
    await userEvent.setup().click(toggle);
    expect(await screen.findByTestId("metric-extraction_precision")).toBeInTheDocument();
  });

  it("cancels a running experiment and keeps the returned status", async () => {
    getExperiment.mockResolvedValue(experimentFixture());
    getExperimentMetrics.mockResolvedValue({
      metrics_version: 1,
      aggregation: "probe_micro_mean",
      metrics: [],
      scope: {},
      coverage: {},
    });
    cancelExperiment.mockResolvedValue(
      experimentFixture({ status: "cancelling", measurement_status: "incomplete" }),
    );

    render(<MemoryExperimentDetail experimentId="exp-run-1" />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "取消实验" }));

    await waitFor(() => expect(cancelExperiment).toHaveBeenCalledWith("exp-run-1"));
    await waitFor(() => expect(screen.getByTestId("detail-provisional")).toBeInTheDocument());
  });
});

describe("EvaluationSettings memory tab", () => {
  it("keeps the existing tabs and adds the memory entry point", async () => {
    listExperiments.mockResolvedValue({
      data: [
        {
          id: "exp-aaaa-1",
          suite_id: "suite-1",
          dataset_revision_id: "rev-1",
          split: "regression",
          track: "end_to_end",
          status: "completed",
          measurement_status: "complete",
          planned_trials: 120,
          repetitions: 1,
          model_mode: "real",
          primary_metric: "qa_accuracy",
          created_at: "2026-11-02T08:00:00Z",
          updated_at: "2026-11-02T09:00:00Z",
        },
      ],
      next_after_key: null,
    });

    render(<EvaluationSettings />);
    await waitFor(() => expect(screen.getByRole("tab", { name: "总览" })).toBeInTheDocument());
    expect(screen.getByRole("tab", { name: "运行" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "数据集" })).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("tab", { name: "记忆专项" }));
    await waitFor(() => expect(listExperiments).toHaveBeenCalled());
    expect(await screen.findByTestId("experiment-exp-aaaa-1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "设为比较基线" })).toBeInTheDocument();
  });
});
