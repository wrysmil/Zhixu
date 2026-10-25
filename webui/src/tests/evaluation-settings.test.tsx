import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EvaluationSettings } from "@/components/settings/evaluation/EvaluationSettings";

vi.mock("@/lib/evaluation-api", () => ({
  evaluationApi: {
    overview: vi.fn(),
    listRuns: vi.fn(),
    getRun: vi.fn(),
    listAttempts: vi.fn(),
    getTrace: vi.fn(),
    listSuites: vi.fn(),
    listCases: vi.fn(),
    listProfiles: vi.fn(),
    createRun: vi.fn(),
    cancelRun: vi.fn(),
    rerunRun: vi.fn(),
    downloadRunExport: vi.fn(),
    downloadDatasetTemplate: vi.fn(),
    importDataset: vi.fn(),
    exportDatasetXlsx: vi.fn(),
    downloadSummaryReport: vi.fn(),
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

import { evaluationApi } from "@/lib/evaluation-api";

const overviewMock = vi.mocked(evaluationApi.overview);
const listRunsMock = vi.mocked(evaluationApi.listRuns);
const getRunMock = vi.mocked(evaluationApi.getRun);
const listAttemptsMock = vi.mocked(evaluationApi.listAttempts);
const getTraceMock = vi.mocked(evaluationApi.getTrace);
const listSuitesMock = vi.mocked(evaluationApi.listSuites);
const listCasesMock = vi.mocked(evaluationApi.listCases);
const listProfilesMock = vi.mocked(evaluationApi.listProfiles);
const createRunMock = vi.mocked(evaluationApi.createRun);
const importDatasetMock = vi.mocked(evaluationApi.importDataset);
const downloadDatasetTemplateMock = vi.mocked(evaluationApi.downloadDatasetTemplate);
const exportDatasetXlsxMock = vi.mocked(evaluationApi.exportDatasetXlsx);
const downloadSummaryReportMock = vi.mocked(evaluationApi.downloadSummaryReport);

function overviewPayload() {
  return {
    recent_runs: [
      {
        id: "r1",
        suite_id: "s1",
        suite_name: "Dataset A",
        status: "running" as const,
        created_at: "2026-10-24T08:00:00Z",
        started_at: "2026-10-24T08:00:01Z",
        finished_at: null,
        parent_run_id: null,
        planned_count: 4,
        terminal_count: 1,
      },
    ],
    metrics: {
      planned: 4,
      passed: 1,
      failed: 0,
      review_required: 0,
      not_evaluated: 0,
      overall_pass_rate: 0.25,
      evaluated_pass_rate: 1,
      evaluation_coverage: 0.25,
      tool_case_pass_rate: null,
      tool_eligible_cases: 0,
      tool_valid_cases: 0,
      quality: {},
      agent_duration: {},
      usage: {},
    },
    status_counts: { running: 1 },
  };
}

const runRow = {
  id: "r1",
  suite_id: "s1",
  suite_name: "Dataset A",
  status: "running" as const,
  created_at: "2026-10-24T08:00:00Z",
  started_at: "2026-10-24T08:00:01Z",
  finished_at: null,
  parent_run_id: null,
  planned_count: 2,
  terminal_count: 1,
};

const attemptRow = {
  id: "a1",
  run_id: "r1",
  position: 1,
  case_id: "c1",
  case_revision: 3,
  case_name: "Case 1",
  execution_status: "succeeded" as const,
  grading_status: "graded" as const,
  verdict: "passed" as const,
  started_at: "2026-10-24T08:00:01Z",
  finished_at: "2026-10-24T08:00:09Z",
  final_answer: "ok",
  stop_reason: null,
  errors: [],
  assertions: [],
  judge: null,
  usage: null,
  timing: null,
  trace_complete: true,
  artifacts: [],
  reviews: [],
};

const tracePage = {
  data: [
    {
      schema_version: 1 as const,
      attempt_id: "a1",
      seq: 1,
      event_id: "e1",
      type: "attempt.started",
    },
    {
      schema_version: 1 as const,
      attempt_id: "a1",
      seq: 2,
      event_id: "e2",
      type: "tool.started",
      payload: { tool: "web_search", summary: "query=zhixu" },
    },
  ],
  next_after_seq: 2,
  has_more: false,
  sealed: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  overviewMock.mockResolvedValue(overviewPayload());
  listRunsMock.mockResolvedValue({ data: [runRow] });
  getRunMock.mockResolvedValue({ ...runRow, snapshot: {}, counts: {}, metrics: null, attempts_url: "" });
  listAttemptsMock.mockResolvedValue({ data: [attemptRow] });
  getTraceMock.mockResolvedValue(tracePage);
  listSuitesMock.mockResolvedValue({
    data: [{ id: "s1", revision: 1, case_count: 2, name: "Dataset A", description: "", tags: [], created_at: "", updated_at: "", is_deleted: false }],
  });
  listCasesMock.mockResolvedValue({
    data: [
      { id: "c1", revision: 1, suite_id: "s1", name: "Case 1", description: "", tags: [], enabled: true, created_at: "", updated_at: "", is_deleted: false },
      { id: "c2", revision: 1, suite_id: "s1", name: "Case 2", description: "", tags: [], enabled: false, created_at: "", updated_at: "", is_deleted: false },
    ],
  });
  listProfilesMock.mockResolvedValue({
    data: [
      { id: "model:fast", label: "fast", provider: "openai", model: "gpt-4o-mini", kind: "both" as const },
      { id: "judge:strict", label: "strict", provider: "openai", model: "gpt-4o", kind: "judge" as const },
    ],
  });
  createRunMock.mockResolvedValue({
    ...runRow,
    id: "r9",
    snapshot: {},
    counts: {},
    metrics: null,
    attempts_url: "",
  });
});

afterEach(() => cleanup());

describe("EvaluationSettings", () => {
  it("renders overview metrics and recent runs, then opens the runs table", async () => {
    render(<EvaluationSettings />);

    await waitFor(() => expect(screen.getByText("Pass rate")).toBeInTheDocument());
    expect(screen.getByText("Recent runs")).toBeInTheDocument();
    expect(screen.getByText("Dataset A")).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("tab", { name: "Runs" }));
    await waitFor(() => expect(listRunsMock).toHaveBeenCalled());
    const table = screen.getByRole("table", { name: "Runs" });
    expect(within(table).getAllByText("Dataset A").length).toBeGreaterThan(0);
  });

  it("submits a new run carrying target and run timeout", async () => {
    render(<EvaluationSettings />);

    await userEvent.setup().click(await screen.findByRole("tab", { name: "Runs" }));
    await userEvent.setup().click(await screen.findByRole("button", { name: "New run" }));

    const dialog = await screen.findByRole("region", { name: "New run" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const targetInput = within(dialog).getByLabelText("Target ID");
    await userEvent.setup().clear(targetInput);
    await userEvent.setup().type(targetInput, "my-agent");
    await waitFor(() => {
      expect(within(dialog).getByRole("combobox", { name: "Model" })).toBeEnabled();
    });
    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Start run" }));

    await waitFor(() => expect(createRunMock).toHaveBeenCalledTimes(1));
    expect(createRunMock).toHaveBeenCalledWith({
      suite_id: "s1",
      case_ids: ["c1"],
      agent_profile_id: "model:fast",
      judge_profile_id: null,
      target: { kind: "agent", id: "my-agent" },
      run_timeout_s: 600,
    });
    expect(await screen.findByRole("region", { name: "Dataset A" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "New run" })).not.toBeInTheDocument();
  });

  it("opens run details in the page and returns to the filtered list", async () => {
    render(<EvaluationSettings />);

    await userEvent.setup().click(await screen.findByRole("tab", { name: "Runs" }));
    await waitFor(() => expect(listRunsMock).toHaveBeenCalled());
    await userEvent.setup().selectOptions(screen.getByRole("combobox"), "running");
    await userEvent.setup().click(await screen.findByRole("button", { name: /Dataset A/ }));

    const sheet = await screen.findByRole("region", { name: "Dataset A" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Runs" })).not.toBeInTheDocument();
    await waitFor(() => expect(within(sheet).getByText("Case 1")).toBeInTheDocument());
    await userEvent.setup().click(within(sheet).getByRole("button", { name: "Load trace" }));

    await waitFor(() => expect(getTraceMock).toHaveBeenCalledWith("a1", 0, 200));
    expect(within(sheet).getByText("attempt.started")).toBeInTheDocument();
    expect(within(sheet).getByText("tool.started")).toBeInTheDocument();
    await userEvent.setup().click(within(sheet).getByRole("button", { name: "Runs" }));
    expect(screen.getByRole("table", { name: "Runs" })).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveValue("running");
    expect(screen.getByRole("button", { name: "Dataset A" })).toHaveFocus();
  });

  it("opens a recent run directly from the overview", async () => {
    render(<EvaluationSettings />);
    await userEvent.setup().click(await screen.findByRole("button", { name: /Dataset A/ }));
    expect(await screen.findByRole("region", { name: "Dataset A" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("cancels page creation without starting a run", async () => {
    render(<EvaluationSettings />);
    await userEvent.setup().click(await screen.findByRole("tab", { name: "Runs" }));
    await userEvent.setup().click(await screen.findByRole("button", { name: "New run" }));
    const form = await screen.findByRole("region", { name: "New run" });
    await userEvent.setup().click(within(form).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("region", { name: "New run" })).not.toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Runs" })).toBeInTheDocument();
    expect(createRunMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "New run" })).toHaveFocus();
  });

  it("refreshes the visible run detail and uses its latest status", async () => {
    render(<EvaluationSettings />);
    await userEvent.setup().click(await screen.findByRole("button", { name: /Dataset A/ }));
    const detail = await screen.findByRole("region", { name: "Dataset A" });
    await waitFor(() => expect(listAttemptsMock).toHaveBeenCalledWith("r1"));
    const beforeRefresh = getRunMock.mock.calls.length;
    getRunMock.mockResolvedValue({ ...runRow, status: "completed", terminal_count: 2, snapshot: {}, counts: {}, metrics: null, attempts_url: "" });
    await userEvent.setup().click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(getRunMock.mock.calls.length).toBeGreaterThan(beforeRefresh));
    expect(await within(detail).findByText("Completed")).toBeInTheDocument();
    expect(within(detail).queryByRole("button", { name: "Cancel run" })).not.toBeInTheDocument();
  });

  it("manages datasets: template, export, summary and case list", async () => {
    render(<EvaluationSettings />);

    await userEvent.setup().click(await screen.findByRole("tab", { name: "Datasets" }));
    await waitFor(() => expect(listSuitesMock).toHaveBeenCalled());
    expect(screen.getByText("Dataset A")).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "Download template" }));
    expect(downloadDatasetTemplateMock).toHaveBeenCalledTimes(1);

    await userEvent.setup().click(screen.getByRole("button", { name: "Export xlsx" }));
    expect(exportDatasetXlsxMock).toHaveBeenCalledWith("s1");

    await userEvent.setup().click(screen.getByRole("button", { name: "HTML summary" }));
    expect(downloadSummaryReportMock).toHaveBeenCalledWith("s1");

    await userEvent.setup().click(screen.getByRole("button", { name: "View cases" }));
    await waitFor(() => expect(listCasesMock).toHaveBeenCalledWith("s1"));
    expect(await screen.findByText("Case 1")).toBeInTheDocument();
    expect(screen.getByText("Case 2")).toBeInTheDocument();
  });

  it("imports an xlsx dataset and surfaces row errors", async () => {
    importDatasetMock.mockResolvedValue({
      suite: {
        id: "s2",
        revision: 1,
        case_count: 3,
        name: "Dataset B",
        description: "",
        tags: [],
        created_at: "",
        updated_at: "",
        is_deleted: false,
      },
      case_count: 3,
      errors: [{ row: 2, message: "missing question" }],
    });

    render(<EvaluationSettings />);

    await userEvent.setup().click(await screen.findByRole("tab", { name: "Datasets" }));
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).not.toBeNull();
    await userEvent.upload(input, new File(["x"], "dataset.xlsx"));

    await waitFor(() => expect(importDatasetMock).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Dataset B")).toBeInTheDocument();
    expect(screen.getByText("Imported")).toBeInTheDocument();
    expect(screen.getByText("Row errors")).toBeInTheDocument();
  });
});
