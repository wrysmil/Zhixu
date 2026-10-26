import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  CaseEvaluationEditor,
  type CaseEvaluationDraft,
} from "@/components/settings/evaluation/CaseEvaluationEditor";
import {
  EvaluationCalibration,
  type CalibrationState,
} from "@/components/settings/evaluation/EvaluationCalibration";
import { EvaluationComparison } from "@/components/settings/evaluation/EvaluationComparison";
import {
  EvaluationReviews,
  type ReviewItem,
} from "@/components/settings/evaluation/EvaluationReviews";
import { EvaluationScorecard } from "@/components/settings/evaluation/EvaluationScorecard";
import type {
  Comparison,
  EvaluationCoverage,
  RunMetricsView,
} from "@/lib/evaluation-api";

const metricsView: RunMetricsView = {
  metrics_version: 2,
  metrics: {
    planned: 4,
    passed: 3,
    failed: 1,
    review_required: 0,
    not_evaluated: 0,
    overall_pass_rate: 0.75,
    evaluated_pass_rate: 0.75,
    evaluation_coverage: 1,
    tool_case_pass_rate: null,
    tool_eligible_cases: 0,
    tool_valid_cases: 0,
    quality: {},
    agent_duration: {},
    usage: {},
    task_outcomes: {
      planned: 4,
      completed: 2,
      partial: 1,
      failed: 1,
      unknown: 0,
      task_success_rate: 0.5,
      partial_completion_rate: 0.25,
      task_assessment_coverage: 1,
    },
    qualification: {
      qualified: 3,
      disqualified: 1,
      review_required: 0,
      not_evaluated: 0,
      qualification_rate: 0.75,
    },
    reliability: { total_cases: 2, groups: [] },
  },
  coverage: {},
  scope: {
    run_id: "run-1",
    case_count: 2,
    planned_trials: 4,
    valid_trials: 4,
    repetitions: 2,
    model_mode: "real",
  },
};

const coverageComplete: EvaluationCoverage = {
  trace: "complete",
  identity: "complete",
  usage: "complete",
  artifacts: "not_applicable",
  required_evidence: "complete",
  reasons: [],
};

const comparisonFixture: Comparison = {
  id: "cmp-1",
  baseline_run_id: "run-base",
  candidate_run_id: "run-cand",
  mode: "strict",
  compatibility: "strict",
  pairs: [
    {
      case_id: "case-a",
      case_revision: 2,
      status: "improved",
      reason: "candidate completed more trials",
      baseline: { success_rate: 0.5, trials: 2, status: "completed" },
      candidate: { success_rate: 1, trials: 2, status: "completed" },
      delta_success_rate: 0.5,
    },
    {
      case_id: "case-b",
      case_revision: 1,
      status: "regressed",
      reason: "candidate lost one completed trial",
      baseline: { success_rate: 1, trials: 2, status: "completed" },
      candidate: { success_rate: 0.5, trials: 2, status: "completed" },
      delta_success_rate: -0.5,
    },
    {
      case_id: "case-c",
      case_revision: 1,
      status: "unchanged",
      reason: "same outcome on both sides",
      baseline: { success_rate: 1, trials: 2, status: "completed" },
      candidate: { success_rate: 1, trials: 2, status: "completed" },
      delta_success_rate: 0,
    },
    {
      case_id: "case-d",
      case_revision: 1,
      status: "incomparable",
      reason: "missing trace on the candidate side",
      baseline: { success_rate: 1, trials: 2, status: "completed" },
      candidate: { success_rate: null, trials: 1, status: "unknown" },
      delta_success_rate: null,
    },
  ],
  metrics: {},
  intervals: null,
  excluded: [],
  reasons: ["grading fingerprint differs between the two runs"],
};

describe("evaluation reliability surfaces", () => {
  it("keeps the task completion rate and the qualification rate apart", () => {
    render(
      <EvaluationScorecard
        metrics={metricsView}
        evaluationCapability="full"
        coverage={coverageComplete}
      />,
    );

    expect(screen.getByTestId("task-success-rate")).toHaveTextContent("50%");
    expect(screen.getByTestId("qualification-rate")).toHaveTextContent("75%");
    expect(screen.getByTestId("task-success-rate")).not.toHaveTextContent("75%");
    expect(screen.getByTestId("separate-metrics")).toBeVisible();
  });

  it("shows unknown denominators as unknown instead of zero", () => {
    const unknownMetrics: RunMetricsView = {
      ...metricsView,
      metrics: {
        ...metricsView.metrics,
        task_outcomes: {
          planned: 4,
          completed: 0,
          partial: 0,
          failed: 0,
          unknown: 4,
          task_success_rate: null,
          partial_completion_rate: null,
          task_assessment_coverage: null,
        },
      },
    };

    render(
      <EvaluationScorecard
        metrics={unknownMetrics}
        evaluationCapability="full"
        coverage={coverageComplete}
      />,
    );

    expect(screen.getByTestId("task-success-rate")).toHaveTextContent("Unknown");
    expect(screen.getByTestId("task-success-rate")).not.toHaveTextContent("0%");
  });

  it("marks smoke and legacy evaluation capability explicitly", () => {
    const { unmount } = render(
      <EvaluationScorecard
        metrics={metricsView}
        evaluationCapability="smoke"
        coverage={coverageComplete}
      />,
    );
    expect(screen.getByTestId("evaluation-capability")).toHaveTextContent("Smoke");
    expect(screen.getByTestId("capability-hint")).toBeVisible();
    unmount();

    render(
      <EvaluationScorecard
        metrics={metricsView}
        evaluationCapability="legacy"
        coverage={coverageComplete}
      />,
    );
    expect(screen.getByTestId("evaluation-capability")).toHaveTextContent("Legacy");
  });

  it("reports a missing trace and a missing usage source differently", () => {
    const missingTrace: EvaluationCoverage = {
      ...coverageComplete,
      trace: "missing",
    };
    const { unmount } = render(
      <EvaluationScorecard
        metrics={metricsView}
        evaluationCapability="full"
        coverage={missingTrace}
      />,
    );
    const traceNotice = screen.getByTestId("trace-notice").textContent ?? "";
    expect(traceNotice).toContain("Trace");
    expect(screen.queryByTestId("usage-notice")).toBeNull();
    unmount();

    const missingUsage: EvaluationCoverage = {
      ...coverageComplete,
      usage: "missing",
    };
    render(
      <EvaluationScorecard
        metrics={metricsView}
        evaluationCapability="full"
        coverage={missingUsage}
      />,
    );
    const usageNotice = screen.getByTestId("usage-notice").textContent ?? "";
    expect(usageNotice).toContain("Usage");
    expect(usageNotice).not.toBe(traceNotice);
    expect(screen.queryByTestId("trace-notice")).toBeNull();
  });

  it("withholds the overall quality conclusion while evidence is missing", () => {
    render(
      <EvaluationScorecard
        metrics={metricsView}
        evaluationCapability="full"
        coverage={{ ...coverageComplete, trace: "missing" }}
      />,
    );

    expect(screen.queryByTestId("quality-conclusion")).toBeNull();
    expect(screen.queryByText("Overall quality: qualified")).toBeNull();
    expect(screen.getByTestId("no-quality-conclusion")).toBeVisible();
    expect(screen.getByTestId("pending-review")).toBeVisible();
  });

  it("lists every case status with its reason and links to both evidence sides", async () => {
    const onOpenEvidence = vi.fn();
    render(
      <EvaluationComparison comparison={comparisonFixture} onOpenEvidence={onOpenEvidence} />,
    );

    expect(screen.getByTestId("pair-status-case-a")).toHaveTextContent("Improved");
    expect(screen.getByTestId("pair-status-case-b")).toHaveTextContent("Regressed");
    expect(screen.getByTestId("pair-status-case-c")).toHaveTextContent("Unchanged");
    expect(screen.getByTestId("pair-status-case-d")).toHaveTextContent("Incomparable");
    expect(screen.getByTestId("pair-reason-case-d")).toHaveTextContent("missing trace");
    expect(screen.getByTestId("comparison-reasons")).toHaveTextContent(
      "grading fingerprint differs",
    );

    const pair = screen.getByTestId("pair-case-a");
    await userEvent.setup().click(
      within(pair).getByRole("button", { name: "View baseline evidence" }),
    );
    expect(onOpenEvidence).toHaveBeenCalledWith("run-base", "case-a");

    await userEvent.setup().click(
      within(pair).getByRole("button", { name: "View candidate evidence" }),
    );
    expect(onOpenEvidence).toHaveBeenCalledWith("run-cand", "case-a");
  });

  it("keeps the automatic conclusion next to the manual one", () => {
    const reviews: ReviewItem[] = [
      {
        attempt_id: "att-1",
        case_id: "case-a",
        case_name: "Case A",
        automatic_verdict: "failed",
        automatic_reason: "artifact missing",
        manual_verdict: "passed",
        manual_note: "artifact exists in the workspace",
        manual_reviewer: "reviewer-a",
      },
    ];

    render(<EvaluationReviews reviews={reviews} />);

    expect(screen.getByTestId("automatic-att-1")).toHaveTextContent("Failed");
    expect(screen.getByTestId("automatic-att-1")).toHaveTextContent("artifact missing");
    expect(screen.getByTestId("manual-att-1")).toHaveTextContent("Passed");
    expect(screen.getByTestId("keep-automatic-att-1")).toBeVisible();
  });

  it("requires a human confirmation before a calibration job can run", async () => {
    const onRunCalibration = vi.fn();
    const onConfirmGold = vi.fn();
    const calibration: CalibrationState = {
      status: "not_started",
      fingerprint: "fingerprint-1",
      required_confirmed: 10,
      confirmed_count: 2,
      positive_confirmed: 1,
      negative_confirmed: 1,
      agreement_rate: 0.5,
      agreement_threshold: 0.8,
      samples: [
        { id: "sample-1", case_id: "case-a", gold_status: "proposed" },
        { id: "sample-2", case_id: "case-b", gold_status: "confirmed" },
      ],
    };

    render(
      <EvaluationCalibration
        calibration={calibration}
        onRunCalibration={onRunCalibration}
        onConfirmGold={onConfirmGold}
      />,
    );

    expect(screen.getByTestId("gold-pending")).toHaveTextContent("Pending human confirmation");
    expect(screen.getByRole("button", { name: "Run calibration" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /auto/i })).toBeNull();
    expect(screen.getByTestId("agreement-warning")).toBeVisible();

    await userEvent.setup().click(screen.getByRole("button", { name: "Confirm gold" }));
    expect(onConfirmGold).toHaveBeenCalledWith("sample-1");
    expect(onRunCalibration).not.toHaveBeenCalled();
  });

  it("saves case criteria as a new revision and rejects unknown assertions", async () => {
    const onSave = vi.fn();
    const draft: CaseEvaluationDraft = {
      version: 2,
      required_dimensions: ["task", "safety"],
      required_evidence: ["final_answer", "trace"],
      milestones: [{ id: "m1", weight: 1, assertion_ids: ["a1"] }],
      reference_answer: "ok",
      judge_artifact_paths: [],
      budgets: {
        max_input_tokens: null,
        max_output_tokens: null,
        max_total_tokens: null,
        max_duration_ms: null,
        max_tool_calls: null,
        max_iterations: null,
      },
      hard_constraints: ["a1"],
    };

    const { unmount } = render(
      <CaseEvaluationEditor
        caseId="case-a"
        caseName="Case A"
        evaluation={draft}
        assertions={[{ id: "a1", kind: "file_exists", required: false }]}
        baseRevision={2}
        onSave={onSave}
      />,
    );

    await userEvent.setup().click(screen.getByRole("button", { name: "Save criteria" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("case-editor-saved")).toHaveTextContent("revision 3");
    unmount();

    const broken: CaseEvaluationDraft = {
      ...draft,
      milestones: [{ id: "m1", weight: 1, assertion_ids: ["missing-assertion"] }],
    };
    render(
      <CaseEvaluationEditor
        caseId="case-a"
        caseName="Case A"
        evaluation={broken}
        assertions={[{ id: "a1", kind: "file_exists", required: false }]}
        onSave={onSave}
      />,
    );

    await userEvent.setup().click(screen.getByRole("button", { name: "Save criteria" }));
    expect(await screen.findByTestId("case-editor-error")).toBeVisible();
    expect(onSave).toHaveBeenCalledTimes(1);
  });
});
