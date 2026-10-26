import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/evaluation-api", () => ({
  evaluationApi: {
    overview: vi.fn(),
    listRuns: vi.fn(),
    getRun: vi.fn(),
    listAttempts: vi.fn(),
    getTrace: vi.fn(),
    getRunMetrics: vi.fn(),
    addReview: vi.fn(),
    listSuites: vi.fn(),
    listCases: vi.fn(),
    getCase: vi.fn(),
    updateCase: vi.fn(),
    listProfiles: vi.fn(),
    createRun: vi.fn(),
    cancelRun: vi.fn(),
    rerunRun: vi.fn(),
    downloadRunExport: vi.fn(),
    downloadDatasetTemplate: vi.fn(),
    importDataset: vi.fn(),
    exportDatasetXlsx: vi.fn(),
    downloadSummaryReport: vi.fn(),
    listGoldSamples: vi.fn(),
    confirmGoldSample: vi.fn(),
    createCalibration: vi.fn(),
    createComparison: vi.fn(),
    getComparison: vi.fn(),
  },
  EvaluationApiError: class extends Error {
    constructor(
      readonly code: string,
      message: string,
      readonly status: number,
    ) {
      super(message || code);
    }
  },
}));

import { EvaluationSettings } from "@/components/settings/evaluation/EvaluationSettings";
import { evaluationApi } from "@/lib/evaluation-api";

const overviewMock = vi.mocked(evaluationApi.overview);
const listRunsMock = vi.mocked(evaluationApi.listRuns);
const getRunMock = vi.mocked(evaluationApi.getRun);
const listAttemptsMock = vi.mocked(evaluationApi.listAttempts);
const getRunMetricsMock = vi.mocked(evaluationApi.getRunMetrics);
const listSuitesMock = vi.mocked(evaluationApi.listSuites);
const listCasesMock = vi.mocked(evaluationApi.listCases);
const getCaseMock = vi.mocked(evaluationApi.getCase);
const updateCaseMock = vi.mocked(evaluationApi.updateCase);
const listGoldSamplesMock = vi.mocked(evaluationApi.listGoldSamples);
const createComparisonMock = vi.mocked(evaluationApi.createComparison);

const runRow = {
  id: "run-1",
  suite_id: "suite-1",
  suite_name: "Dataset A",
  status: "completed" as const,
  created_at: "2026-10-26T08:00:00Z",
  started_at: "2026-10-26T08:00:01Z",
  finished_at: "2026-10-26T08:00:09Z",
  parent_run_id: null,
  planned_count: 1,
  terminal_count: 1,
};

const attemptRow = {
  id: "attempt-1",
  run_id: "run-1",
  position: 1,
  case_id: "case-1",
  case_revision: 1,
  case_name: "Case 1",
  execution_status: "succeeded" as const,
  grading_status: "graded" as const,
  verdict: "passed" as const,
  started_at: "2026-10-26T08:00:01Z",
  finished_at: "2026-10-26T08:00:09Z",
  final_answer: "ok",
  stop_reason: null,
  errors: [],
  assertions: [],
  judge: null,
  usage: null,
  timing: null,
  trace_complete: false,
  artifacts: [],
  reviews: [],
};

const metricsWithMissingEvidence = {
  metrics_version: 2,
  metrics: {
    planned: 1,
    passed: 1,
    failed: 0,
    review_required: 0,
    not_evaluated: 0,
    overall_pass_rate: 1,
    evaluated_pass_rate: 1,
    evaluation_coverage: 1,
    tool_case_pass_rate: null,
    tool_eligible_cases: 0,
    tool_valid_cases: 0,
    quality: {},
    agent_duration: {},
    usage: {},
    task_outcomes: {
      planned: 1,
      completed: 1,
      partial: 0,
      failed: 0,
      unknown: 0,
      task_success_rate: 1,
      partial_completion_rate: 0,
      task_assessment_coverage: 1,
    },
    qualification: {
      qualified: 1,
      disqualified: 0,
      review_required: 0,
      not_evaluated: 0,
      qualification_rate: 1,
    },
    reliability: { total_cases: 1, groups: [] },
  },
  coverage: {
    trace: "missing",
    identity: "complete",
    usage: "missing",
    artifacts: "missing",
    required_evidence: "partial",
    reasons: ["TRACE_MISSING", "USAGE_MISSING"],
  },
  scope: {
    run_id: "run-1",
    case_count: 1,
    planned_trials: 1,
    valid_trials: 1,
    repetitions: 1,
    model_mode: "real",
  },
};

const caseDetail = {
  id: "case-1",
  revision: 2,
  suite_id: "suite-1",
  name: "Case 1",
  description: "读取 input.txt",
  tags: ["basic"],
  enabled: true,
  created_at: "2026-10-26T08:00:00Z",
  updated_at: "2026-10-26T08:00:00Z",
  is_deleted: false,
  turns: [{ content: "读取 input.txt" }],
  execution: {
    tool_mode: "fixture",
    allowed_tools: ["read_file"],
    max_iterations: 6,
    timeout_s: 120,
  },
  assertions: [{ id: "a1", kind: "answer_contains", required: true }],
  rubric: null,
  evaluation: {
    version: 2 as const,
    required_dimensions: ["task"],
    required_evidence: ["final_answer"],
    milestones: [{ id: "m1", weight: 1, assertion_ids: ["a1"] }],
    reference_answer: null,
    judge_artifact_paths: [],
    budgets: {},
  },
  dependencies: [],
  required_inputs: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  overviewMock.mockResolvedValue({
    recent_runs: [runRow],
    metrics: null,
    status_counts: { completed: 1 },
  } as never);
  listRunsMock.mockResolvedValue({
    data: [runRow, { ...runRow, id: "run-2", suite_name: "Dataset B" }],
  } as never);
  getRunMock.mockResolvedValue({
    ...runRow,
    counts: { passed: 1 },
    metrics: null,
    attempts_url: "/runs/run-1/attempts",
    evaluation_capability: "smoke",
  } as never);
  listAttemptsMock.mockResolvedValue({ data: [attemptRow] } as never);
  getRunMetricsMock.mockResolvedValue(metricsWithMissingEvidence as never);
  listSuitesMock.mockResolvedValue({
    data: [
      {
        id: "suite-1",
        revision: 1,
        name: "Dataset A",
        description: "",
        tags: [],
        created_at: "2026-10-26T08:00:00Z",
        updated_at: "2026-10-26T08:00:00Z",
        case_count: 1,
      },
    ],
  } as never);
  listCasesMock.mockResolvedValue({
    data: [
      {
        id: "case-1",
        revision: 2,
        suite_id: "suite-1",
        name: "Case 1",
        description: "",
        tags: [],
        enabled: true,
        created_at: "2026-10-26T08:00:00Z",
        updated_at: "2026-10-26T08:00:00Z",
        is_deleted: false,
      },
    ],
  } as never);
  getCaseMock.mockResolvedValue(caseDetail as never);
  updateCaseMock.mockResolvedValue({ ...caseDetail, revision: 3 } as never);
  listGoldSamplesMock.mockResolvedValue({
    gold_revision: 1,
    data: [
      {
        id: "C01",
        label_status: "proposed",
        reviewer: null,
        comment: "",
        updated_at: "2026-10-26T08:00:00Z",
        task: "计算 6+7",
        expected_verdict: "passed",
        expected_scores: [{ dimension_id: "task_completion", score: 4 }],
      },
    ],
  } as never);
  createComparisonMock.mockResolvedValue({
    id: "cmp-1",
    baseline_run_id: "run-1",
    candidate_run_id: "run-1",
    mode: "strict",
    compatibility: "strict",
    pairs: [
      {
        case_id: "case-1",
        case_revision: 1,
        status: "improved",
        reason: "candidate completed more trials",
        baseline: { success_rate: 0.5, trials: 2, status: "completed" },
        candidate: { success_rate: 1, trials: 2, status: "completed" },
        delta_success_rate: 0.5,
      },
    ],
    metrics: {},
    intervals: null,
    excluded: [],
    reasons: [],
  } as never);
});

describe("evaluation reliability integration", () => {
  it("shows missing evidence in the run detail and refuses a quality conclusion", async () => {
    render(<EvaluationSettings />);

    await userEvent.setup().click(await screen.findByRole("tab", { name: "Runs" }));
    await waitFor(() => expect(listRunsMock).toHaveBeenCalled());
    await userEvent.setup().click(await screen.findByRole("button", { name: /Dataset A/ }));

    const sheet = await screen.findByRole("region", { name: "Dataset A" });
    await waitFor(() =>
      expect(within(sheet).getByTestId("trace-notice")).toBeVisible()
    );
    expect(within(sheet).getByTestId("usage-notice")).toBeVisible();
    expect(within(sheet).getByTestId("no-quality-conclusion")).toBeVisible();
    expect(within(sheet).getByTestId("evaluation-capability")).toHaveTextContent(
      "Smoke"
    );
  });

  it("edits case criteria and saves them as a new revision", async () => {
    render(<EvaluationSettings />);

    await userEvent.setup().click(await screen.findByRole("tab", { name: "Datasets" }));
    await waitFor(() => expect(listSuitesMock).toHaveBeenCalled());
    await userEvent.setup().click(
      await screen.findByRole("button", { name: "View cases" })
    );
    await waitFor(() => expect(listCasesMock).toHaveBeenCalled());
    await userEvent.setup().click(
      await screen.findByRole("button", { name: "Edit criteria" })
    );
    await waitFor(() => expect(getCaseMock).toHaveBeenCalledWith("case-1"));

    await userEvent.setup().click(screen.getByRole("button", { name: "Save criteria" }));
    await waitFor(() => expect(updateCaseMock).toHaveBeenCalled());
    const payload = updateCaseMock.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(payload.base_revision).toBe(2);
    expect((payload.evaluation as Record<string, unknown>).version).toBe(2);
  });

  it("keeps the calibration panel on the human confirmation path", async () => {
    render(<EvaluationSettings />);

    await waitFor(() => expect(listGoldSamplesMock).toHaveBeenCalled());
    expect(await screen.findByTestId("gold-progress")).toBeVisible();
    expect(screen.getByTestId("gold-pending")).toBeVisible();
    // gold 未达人工确认门槛时，校准按钮保持禁用：不允许自动代签。
    expect(screen.getByTestId("calibration-blocked")).toBeVisible();
  });

  it("creates a baseline/candidate comparison from the runs tab", async () => {
    render(<EvaluationSettings />);

    await userEvent.setup().click(await screen.findByRole("tab", { name: "Runs" }));
    await waitFor(() => expect(listRunsMock).toHaveBeenCalled());
    await userEvent.setup().selectOptions(
      await screen.findByRole("combobox", { name: "Baseline" }),
      "run-1"
    );
    await userEvent.setup().selectOptions(
      screen.getByRole("combobox", { name: "Candidate" }),
      "run-2"
    );
    await userEvent.setup().click(
      await screen.findByRole("button", { name: "Create comparison" })
    );
    await waitFor(() => expect(createComparisonMock).toHaveBeenCalled());
    expect(await screen.findByTestId("comparison-compatibility")).toBeVisible();
  });
});
