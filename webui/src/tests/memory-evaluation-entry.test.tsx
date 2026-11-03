import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MemoryEvaluation } from "@/components/settings/evaluation/memory/MemoryEvaluation";
import i18n from "@/i18n";
import { memoryEvaluationApi } from "@/lib/memory-evaluation-api";

beforeEach(async () => {
  await i18n.changeLanguage("zh-CN");
  vi.spyOn(memoryEvaluationApi, "listExperiments").mockResolvedValue({ data: [], next_after_key: null });
  vi.spyOn(memoryEvaluationApi, "listDatasets").mockResolvedValue({ data: [], next_after_key: null });
  vi.spyOn(memoryEvaluationApi, "capabilities").mockResolvedValue({
    tracks: [{ id: "end_to_end", supported: true, missing: [], primary_metric: "qa_accuracy_scenario_macro", required_metrics: ["qa_accuracy"] }],
    settings: [], profiles: [], capabilities: {}, missing: [], retrieval_channels: [],
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("MemoryEvaluation creation readiness", () => {
  it("explains a failed capability request and lets the user retry", async () => {
    vi.mocked(memoryEvaluationApi.capabilities).mockRejectedValueOnce(new Error("offline"));
    render(<MemoryEvaluation />);
    await userEvent.setup().click(screen.getByRole("tab", { name: "创建实验" }));
    expect(await screen.findByText(/无法读取记忆评测能力/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "开始测试" })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "重试加载" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "高级设置" })).toBeInTheDocument());
    expect(vi.mocked(memoryEvaluationApi.capabilities)).toHaveBeenCalledTimes(2);
  });
});
