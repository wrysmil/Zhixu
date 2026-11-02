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
      listAttemptEvents: vi.fn(),
      listAttemptProbes: vi.fn(),
      addReview: vi.fn(),
    },
  };
});

import {
  MemoryReplay,
  memoryReplayMissingStages,
  memoryReplayRows,
  memoryReplayStageOf,
} from "@/components/settings/evaluation/memory/MemoryReplay";
import i18n from "@/i18n";
import { EvaluationApiError } from "@/lib/evaluation-api";
import {
  memoryEvaluationApi,
  type MemoryProbeSummary,
} from "@/lib/memory-evaluation-api";

const listAttemptEvents = vi.mocked(memoryEvaluationApi.listAttemptEvents);
const listAttemptProbes = vi.mocked(memoryEvaluationApi.listAttemptProbes);
const addReview = vi.mocked(memoryEvaluationApi.addReview);

function event(seq: number, payload: Record<string, unknown>) {
  return {
    schema_version: 1,
    attempt_id: "a1",
    seq,
    event_id: `e${seq}`,
    type: "memory.event",
    payload,
  };
}

const eventPage = {
  data: [
    event(1, { kind: "session_closed", session_id: "sess-1" }),
    event(2, { kind: "extraction_completed", session_id: "sess-1", detail: "state_v=4" }),
    event(3, { kind: "extraction_failed", session_id: "sess-2", detail: "timeout" }),
    event(4, { kind: "extraction_skipped", session_id: "sess-3", detail: "policy" }),
    event(5, { kind: "recall_candidates", probe_id: "p1", detail: "CANDIDATES=3" }),
    event(6, { kind: "injected", probe_id: "p1", origin_refs: ["fact-1", "fact-2"] }),
    event(7, { kind: "probe_answered", probe_id: "p1", body: "你以前喝美式。" }),
    event(8, { kind: "mystery_kind", detail: "unexpected" }),
  ],
  next_after_seq: 8,
  has_more: false,
  sealed: true,
};

function probeFixture(overrides: Partial<MemoryProbeSummary> = {}): MemoryProbeSummary {
  return {
    scenario_id: "s-1",
    probe_id: "p1",
    trial_index: 0,
    category: "事实记忆",
    qa_correct: true,
    qa_status: "scored",
    fact_coverage: 1,
    grounded_memory_success: false,
    grading_revision: 3,
    review_digest: "abcd1234ef",
    reasons: [],
    metrics: [
      {
        id: "qa_accuracy",
        version: 1,
        unit: "ratio",
        value: 1,
        numerator: 1,
        denominator: 1,
        status: "scored",
        scope: "probe",
        reasons: [],
        coverage: { scored: 1, planned: 1, reasons: [] },
      },
    ],
    ...overrides,
  };
}

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage("zh-CN");
  listAttemptEvents.mockResolvedValue(eventPage);
  listAttemptProbes.mockResolvedValue({ data: [probeFixture()], next_after_key: null });
});

afterEach(() => cleanup());

describe("memory replay stages", () => {
  it("maps every contract kind to the real step and keeps unknown kinds visible", () => {
    expect(memoryReplayStageOf("session_closed")).toBe("history");
    expect(memoryReplayStageOf("extraction_skipped")).toBe("extraction");
    expect(memoryReplayStageOf("memory_changed")).toBe("state");
    expect(memoryReplayStageOf("recall_candidates")).toBe("candidates");
    expect(memoryReplayStageOf("reranked")).toBe("rerank");
    expect(memoryReplayStageOf("injected")).toBe("injected");
    expect(memoryReplayStageOf("probe_answered")).toBe("answer");
    expect(memoryReplayStageOf("deletion_checked")).toBe("deletion");
    expect(memoryReplayStageOf("brand_new_kind")).toBe("unknown");
  });

  it("orders events by seq and reports the missing steps", () => {
    const rows = memoryReplayRows(eventPage.data);
    expect(rows.map((row) => row.seq)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(memoryReplayMissingStages(rows)).toEqual(["state", "rerank"]);
  });
});

describe("MemoryReplay", () => {
  it("renders the real event sequence with failed, skipped and unknown markers", async () => {
    render(<MemoryReplay attemptId="a1" scenarioId="s-1" />);

    const first = await screen.findByTestId("replay-event-1");
    expect(first.textContent).toContain("历史");
    expect(first.textContent).toContain("session_closed");
    expect(screen.getByTestId("replay-event-2").textContent).toContain("已记录");
    expect(screen.getByTestId("replay-event-3").textContent).toContain("失败");
    expect(screen.getByTestId("replay-event-4").textContent).toContain("已跳过");
    expect(screen.getByTestId("replay-event-7").textContent).toContain("你以前喝美式。");
    expect(screen.getByTestId("replay-event-8").textContent).toContain("未知");

    const order = Array.from(
      document.querySelectorAll("[data-testid^='replay-event-']"),
    ).map((node) => node.getAttribute("data-testid"));
    expect(order).toEqual([
      "replay-event-1",
      "replay-event-2",
      "replay-event-3",
      "replay-event-4",
      "replay-event-5",
      "replay-event-6",
      "replay-event-7",
      "replay-event-8",
    ]);

    // 没有记录的阶段标缺证：不伪造重排或状态变化。
    expect(screen.getByTestId("replay-stage-rerank").textContent).toContain("证据缺失");
    expect(screen.getByTestId("replay-missing").textContent).toContain("证据缺失");
  });

  it("states missing evidence when the attempt has no memory event at all", async () => {
    listAttemptEvents.mockResolvedValue({
      data: [],
      next_after_seq: 0,
      has_more: false,
      sealed: true,
    });
    render(<MemoryReplay attemptId="a1" />);

    expect(await screen.findByTestId("replay-events-empty")).toBeInTheDocument();
    expect(screen.getByTestId("replay-missing")).toBeInTheDocument();
  });

  it("does not count a correct answer without evidence as grounded memory use", async () => {
    render(<MemoryReplay attemptId="a1" scenarioId="s-1" />);

    const probe = await screen.findByTestId("probe-p1");
    expect(probe.textContent).toContain("答对");
    expect(probe.textContent).toContain("答对但无证据，不算记忆使用成功");
    expect(screen.getByTestId("probe-qa-p1").textContent).toBe("答对");
  });

  it("submits a probe review with the memory reference and the grading revision", async () => {
    addReview.mockResolvedValue({});
    render(<MemoryReplay attemptId="a1" scenarioId="s-1" />);

    const probe = await screen.findByTestId("probe-p1");
    expect(probe.textContent).toContain("指纹：评分 v3 · abcd1234");
    await userEvent.setup().click(screen.getByRole("button", { name: "通过" }));

    await waitFor(() => expect(addReview).toHaveBeenCalledTimes(1));
    expect(addReview).toHaveBeenCalledWith("a1", {
      verdict: "passed",
      memory_ref: {
        scenario_id: "s-1",
        probe_id: "p1",
        metric_id: "qa_accuracy",
        grading_revision: 3,
      },
    });
    expect(await screen.findByTestId("probe-review-p1")).toBeInTheDocument();
    // 旧评分与指纹仍在页面上：复核是追加，不是覆盖。
    expect(screen.getByTestId("probe-qa-p1")).toBeInTheDocument();
  });

  it("asks for a refresh when the grading revision is outdated", async () => {
    addReview.mockRejectedValue(
      new EvaluationApiError("MEMORY_GRADING_REVISION_CONFLICT", "stale", 409),
    );
    listAttemptEvents.mockResolvedValue(eventPage);
    render(<MemoryReplay attemptId="a1" scenarioId="s-1" />);

    await screen.findByTestId("probe-p1");
    await userEvent.setup().click(screen.getByRole("button", { name: "不通过" }));

    const conflict = await screen.findByTestId("probe-conflict-p1");
    expect(conflict.textContent).toContain("评分版本已过期");
    await userEvent.setup().click(within(conflict).getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(listAttemptEvents).toHaveBeenCalledTimes(2));
  });
});
