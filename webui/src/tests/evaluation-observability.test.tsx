import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RunDetailSheet } from "@/components/settings/evaluation/RunDetailSheet";

vi.mock("@/lib/evaluation-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/evaluation-api")>();
  return {
    ...original,
    evaluationApi: {
      getRun: vi.fn(),
      listAttempts: vi.fn(),
      getTrace: vi.fn(),
      getExecution: vi.fn(),
      listToolCalls: vi.fn(),
      getRunUsage: vi.fn(),
      downloadRunExport: vi.fn(),
      downloadRunReport: vi.fn(),
      cancelRun: vi.fn(),
      rerunRun: vi.fn(),
    },
  };
});

import { evaluationApi, type ExecutionNode, type RunSummary } from "@/lib/evaluation-api";

const run: RunSummary = {
  id: "run-1",
  suite_id: "suite-1",
  suite_name: "Observability suite",
  status: "completed",
  created_at: "2026-10-25T00:00:00Z",
  started_at: "2026-10-25T00:00:01Z",
  finished_at: "2026-10-25T00:00:10Z",
  parent_run_id: null,
  planned_count: 2,
  terminal_count: 2,
};

const attempts = [
  {
    id: "attempt-1",
    run_id: "run-1",
    position: 1,
    case_id: "case-1",
    case_revision: 1,
    case_name: "Research answer",
    execution_status: "succeeded" as const,
    grading_status: "graded",
    verdict: "passed" as const,
    started_at: "2026-10-25T00:00:01Z",
    finished_at: "2026-10-25T00:00:05Z",
    final_answer: "answer",
    stop_reason: null,
    errors: [],
    assertions: [],
    judge: null,
    usage: null,
    timing: null,
    trace_complete: true,
    artifacts: [],
    reviews: [],
  },
  {
    id: "attempt-2",
    run_id: "run-1",
    position: 2,
    case_id: "case-2",
    case_revision: 1,
    case_name: "Citation failure",
    execution_status: "failed" as const,
    grading_status: "graded",
    verdict: "failed" as const,
    started_at: "2026-10-25T00:00:06Z",
    finished_at: "2026-10-25T00:00:10Z",
    final_answer: null,
    stop_reason: "tool failed",
    errors: [{ phase: "execution" as const, code: "TOOL_FAILED", message: "fetch failed" }],
    assertions: [],
    judge: null,
    usage: null,
    timing: null,
    trace_complete: false,
    artifacts: [],
    reviews: [],
  },
];

const agentNode: ExecutionNode = {
  id: "agent-main-plan",
  kind: "agent_activity",
  agent_id: "agent-main",
  agent_name: "Main agent",
  execution_id: "exec-main",
  parent_execution_id: null,
  call_id: "model-call-1",
  model: "demo-fast",
  status: "succeeded",
  started_at: "2026-10-25T00:00:01Z",
  duration_ms: 1200,
  input_tokens: 0,
  output_tokens: 320,
  total_tokens: null,
  usage_source: "unavailable",
  evidence_refs: ["event-1"],
  label: "Plan request",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(evaluationApi.getRun).mockResolvedValue({
    ...run,
    snapshot: {},
    counts: { passed: 1, failed: 1 },
    metrics: {
      planned: 2,
      passed: 1,
      failed: 1,
      review_required: 0,
      not_evaluated: 0,
      overall_pass_rate: 0.5,
      evaluated_pass_rate: 0.5,
      evaluation_coverage: 1,
      tool_case_pass_rate: null,
      tool_eligible_cases: 0,
      tool_valid_cases: 0,
      quality: {},
      agent_duration: {},
      usage: {},
    },
    attempts_url: "",
  });
  vi.mocked(evaluationApi.listAttempts).mockResolvedValue({ data: attempts });
  vi.mocked(evaluationApi.getExecution).mockResolvedValue({
    data: { nodes: [agentNode], edges: [] },
    next_after_seq: 10,
    has_more: false,
    sealed: true,
    coverage: {
      trace: "complete",
      identity: "complete",
      usage_attribution: "partial",
      reasons: ["one_usage_missing"],
    },
  });
  vi.mocked(evaluationApi.getRunUsage).mockResolvedValue({
    data: [{
      key: "agent-main",
      agent_id: "agent-main",
      agent_name: "Main agent",
      role: "agent",
      provider: "openai",
      model: "demo-fast",
      input_tokens: 0,
      output_tokens: 320,
      total_tokens: null,
      physical_requests: 1,
      missing_usage_calls: 1,
      source: "unavailable",
      coverage: "partial",
    }],
    totals: {
      input_tokens: 0,
      output_tokens: 320,
      total_tokens: null,
      physical_requests: 1,
      missing_usage_calls: 1,
    },
    next_after_key: null,
    coverage: "partial",
  });
  vi.mocked(evaluationApi.listToolCalls).mockResolvedValue({
    data: [{
      call_id: "tool-call-19",
      attempt_id: "attempt-1",
      execution_id: "exec-main",
      phase_id: "research",
      tool_name: "web_fetch",
      agent_id: "agent-main",
      agent_name: "Main agent",
      status: "failed",
      started_at: "2026-10-25T00:00:02Z",
      duration_ms: 663,
      input_summary: "example.com/source",
      output_summary: null,
      error: "timeout",
    }],
    next_after_key: "next-tools",
    total_count: 160,
    coverage: "complete",
  });
});

afterEach(() => cleanup());

describe("evaluation observability", () => {
  it("loads the canvas, supports keyboard node selection and preserves unknown token totals", async () => {
    render(<RunDetailSheet run={run} onClose={vi.fn()} />);
    const sheet = await screen.findByRole("dialog", { name: "Observability suite" });

    await userEvent.setup().click(within(sheet).getByRole("tab", { name: "Execution canvas" }));
    await waitFor(() => expect(evaluationApi.getExecution).toHaveBeenCalledWith("attempt-1", 0, 200));

    const node = within(sheet).getByRole("button", { name: /Plan request/ });
    node.focus();
    await userEvent.setup().keyboard("{Enter}");

    const inspector = within(sheet).getByRole("region", { name: "Node inspector" });
    expect(within(inspector).getByText("Main agent")).toBeInTheDocument();
    expect(within(inspector).getByText("0")).toBeInTheDocument();
    expect(within(inspector).getByText("Unavailable")).toBeInTheDocument();
    expect(within(sheet).getByText(/Usage attribution.*Partial/i)).toBeInTheDocument();
  });

  it("filters paged tool calls, changes cases without retaining the old graph, and exports run HTML", async () => {
    render(<RunDetailSheet run={run} onClose={vi.fn()} />);
    const sheet = await screen.findByRole("dialog", { name: "Observability suite" });

    await userEvent.setup().click(within(sheet).getByRole("tab", { name: "Tool calls" }));
    await userEvent.setup().type(within(sheet).getByRole("searchbox", { name: "Search tool calls" }), "call-19");
    await userEvent.setup().selectOptions(within(sheet).getByLabelText("Tool status"), "failed");
    await userEvent.setup().click(within(sheet).getByRole("button", { name: "Apply filters" }));

    await waitFor(() => expect(evaluationApi.listToolCalls).toHaveBeenLastCalledWith(
      "attempt-1",
      expect.objectContaining({ q: "call-19", status: "failed", limit: 50 }),
    ));
    expect(within(sheet).getByText("tool-call-19")).toBeInTheDocument();
    expect(within(sheet).getByText(/1 loaded.*160 total/i)).toBeInTheDocument();

    await userEvent.setup().click(within(sheet).getByRole("tab", { name: "Cases" }));
    await userEvent.setup().click(within(sheet).getByRole("button", { name: /Citation failure/ }));
    await userEvent.setup().click(within(sheet).getByRole("tab", { name: "Execution canvas" }));
    await waitFor(() => expect(evaluationApi.getExecution).toHaveBeenLastCalledWith("attempt-2", 0, 200));

    await userEvent.setup().click(within(sheet).getByRole("button", { name: "Export run HTML" }));
    expect(evaluationApi.downloadRunReport).toHaveBeenCalledWith("run-1");
  });
});
