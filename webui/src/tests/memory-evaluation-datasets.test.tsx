import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MemoryDatasets,
  type MemoryDatasetImportOutcome,
  type MemoryDatasetRevisionView,
} from "@/components/settings/evaluation/memory/MemoryDatasets";
import { MemoryMetricValue } from "@/components/settings/evaluation/memory/MemoryMetricValue";
import {
  MemoryScenarioEditor,
  type MemoryScenarioEdit,
  type MemoryScenarioView,
} from "@/components/settings/evaluation/memory/MemoryScenarioEditor";
import i18n from "@/i18n";

const readyDataset: MemoryDatasetRevisionView = {
  id: "rev-smoke",
  datasetVersion: "memory-smoke-v1",
  revision: 3,
  status: "ready",
  splitCounts: { development: 20 },
  scenarioCount: 20,
  probeCount: 24,
  coverage: ["事实记忆", "遗忘"],
  source: {
    repoUrl: "local://nanobot/evaluation/samples",
    revision: "sha-abc",
    license: "internal-test-only",
    loaderVersion: "memory-dataset-loader/v1",
  },
  definitionDigest: "sha256:9f2a",
  exportArtifactRef: "artifact://memory/datasets/rev-smoke.json",
};

const stagingDataset: MemoryDatasetRevisionView = {
  id: "rev-import",
  datasetVersion: "imported-v1",
  revision: 1,
  status: "staging",
  splitCounts: { development: 12 },
  scenarioCount: 12,
  coverage: [],
  source: {
    repoUrl: "https://example.com/memory-set",
    revision: "v1.0.0",
    license: "CC-BY-4.0",
  },
  exportArtifactRef: null,
};

const scenario: MemoryScenarioView = {
  scenarioId: "mem-core-001",
  revision: 3,
  split: "regression",
  clusterId: "cluster-001",
  tags: ["事实记忆"],
  events: [
    {
      type: "session",
      id: "s1",
      at: "2026-03-01T09:00:00+08:00",
      messages: [
        { id: "m1", role: "user", at: "2026-03-01T09:00:00+08:00", text: "我平时喝美式。" },
        { id: "m2", role: "assistant", at: "2026-03-01T09:00:01+08:00", text: "好的。" },
      ],
    },
    { type: "settle", summary: "settle.after_session=s1" },
  ],
  probes: [
    {
      id: "p1",
      question: "我平时喝什么？",
      at: "2026-03-02T09:00:00+08:00",
      answerability: "answerable",
      requiredFacts: [{ key: "drink", value: "美式", evidenceMessageIds: ["m1"] }],
      allowedEvidenceMessageIds: ["m1"],
    },
  ],
  gold: { annotationScope: "targeted", extractionFactCount: 1 },
};

beforeEach(async () => {
  await i18n.changeLanguage("zh-CN");
});

afterEach(() => cleanup());

describe("MemoryDatasets", () => {
  it("shows builtin 20/120/calibration collections and imports them by sample id", async () => {
    const onImportBuiltin = vi.fn().mockResolvedValue({
      status: "ready",
      datasetRevisionId: "rev-new",
      counts: { scenarios: 20 },
      errors: [],
    } satisfies MemoryDatasetImportOutcome);
    render(<MemoryDatasets datasets={[]} onImportBuiltin={onImportBuiltin} />);

    expect(screen.getByText("memory-smoke-v1")).toBeInTheDocument();
    expect(screen.getByText("memory-core-v1")).toBeInTheDocument();
    expect(screen.getByText("memory-calibration-v1")).toBeInTheDocument();
    expect(screen.getByText("120 个场景")).toBeInTheDocument();

    const buttons = screen.getAllByRole("button", { name: "导入内置集合" });
    expect(buttons).toHaveLength(3);
    await userEvent.setup().click(buttons[0]);
    expect(onImportBuiltin).toHaveBeenCalledWith("memory-smoke-v1");
    expect(await screen.findByText("导入结果")).toBeInTheDocument();
    expect(screen.getAllByText(/20 个场景/).length).toBeGreaterThan(0);
  });

  it("shows source, license, split, coverage and revision, and keeps staging unrunnable", async () => {
    const onRunExperiment = vi.fn();
    render(
      <MemoryDatasets
        datasets={[readyDataset, stagingDataset]}
        onRunExperiment={onRunExperiment}
      />,
    );

    expect(screen.getByText("local://nanobot/evaluation/samples")).toBeInTheDocument();
    expect(screen.getByText("internal-test-only")).toBeInTheDocument();
    expect(screen.getByText("开发 20")).toBeInTheDocument();
    expect(screen.getByText("事实记忆")).toBeInTheDocument();
    expect(screen.getAllByText(/版本/).length).toBeGreaterThan(0);
    expect(screen.getByText("artifact://memory/datasets/rev-smoke.json")).toBeInTheDocument();

    const runButtons = screen.getAllByRole("button", { name: "用于新实验" });
    expect(runButtons[0]).toBeEnabled();
    expect(runButtons[1]).toBeDisabled();
    expect(
      screen.getByText("暂存集不可运行，校验通过后才能运行"),
    ).toBeInTheDocument();

    await userEvent.setup().click(runButtons[0]);
    expect(onRunExperiment).toHaveBeenCalledWith(readyDataset);

    const exportButtons = screen.getAllByRole("button", { name: "导出" });
    expect(exportButtons[1]).toBeDisabled();
  });

  it("requires source metadata and locates JSONL import errors by line and field", async () => {
    const onImportDataset = vi.fn().mockResolvedValue({
      status: "staging",
      counts: { scenarios: 1, probes: 1 },
      errors: [
        {
          line: 2,
          field: "events[0].messages[1].role",
          scenarioId: "mem-import-002",
          message: "role must be user or assistant",
        },
      ],
    } satisfies MemoryDatasetImportOutcome);
    render(<MemoryDatasets datasets={[]} onImportDataset={onImportDataset} />);

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(fileInput, new File(['{"scenario_id":"a"}'], "memory.jsonl"));
    expect(await screen.findByText("memory.jsonl")).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "导入" }));
    expect(
      await screen.findByText("导入前必须填写来源仓库、revision 与许可"),
    ).toBeInTheDocument();
    expect(onImportDataset).not.toHaveBeenCalled();

    await userEvent.setup().type(screen.getByLabelText("来源仓库"), "https://example.com/set");
    await userEvent.setup().type(screen.getByLabelText("来源 revision"), "sha-1");
    await userEvent.setup().type(screen.getByLabelText("许可"), "CC-BY-4.0");
    await userEvent.setup().click(screen.getByRole("button", { name: "导入" }));

    await waitFor(() => expect(onImportDataset).toHaveBeenCalledTimes(1));
    expect(onImportDataset).toHaveBeenCalledWith(expect.any(File), {
      format: "jsonl",
      sourceRepo: "https://example.com/set",
      sourceRevision: "sha-1",
      license: "CC-BY-4.0",
    });
    expect(await screen.findByText("第 2 行")).toBeInTheDocument();
    expect(screen.getByText(/字段 events\[0\]\.messages\[1\]\.role/)).toBeInTheDocument();
    expect(screen.getByText(/mem-import-002/)).toBeInTheDocument();
    expect(screen.getByText("当前为暂存版本，校验通过前不可运行。")).toBeInTheDocument();
  });
});

describe("MemoryScenarioEditor", () => {
  it("shows events, gold and evidence, then saves only whitelisted fields as a new revision", async () => {
    const onSaveRevision = vi.fn().mockResolvedValue({ revision: 4 });
    render(<MemoryScenarioEditor scenario={scenario} onSaveRevision={onSaveRevision} />);

    expect(screen.getByLabelText("测试问题")).toHaveValue("我平时喝什么？");
    expect(screen.getByText("session")).toBeInTheDocument();
    expect(screen.getByText("我平时喝美式。")).toBeInTheDocument();
    expect(screen.getByText("drink")).toBeInTheDocument();
    expect(
      screen.getByText("会话事件与发生时间为只读；不提供脚本或任意代码编辑。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("gold 只用于评分，不会进入被测 Agent 的执行输入。"),
    ).toBeInTheDocument();
    expect(document.querySelector("textarea")).toBeNull();

    const questionInput = screen.getByLabelText("测试问题");
    await userEvent.setup().clear(questionInput);
    await userEvent.setup().type(questionInput, "我平时喝什么咖啡？");
    const factInput = screen.getByLabelText("事实值 drink");
    await userEvent.setup().clear(factInput);
    await userEvent.setup().type(factInput, "拿铁");

    const saveButton = screen.getByRole("button", { name: "保存为新版本" });
    expect(saveButton).toBeEnabled();
    await userEvent.setup().click(saveButton);

    await waitFor(() => expect(onSaveRevision).toHaveBeenCalledTimes(1));
    const payload = onSaveRevision.mock.calls[0][0] as MemoryScenarioEdit;
    expect(payload).toEqual({
      scenario_id: "mem-core-001",
      base_revision: 3,
      probes: [
        {
          id: "p1",
          question: "我平时喝什么咖啡？",
          required_facts: [{ key: "drink", value: "拿铁" }],
        },
      ],
    });
    expect(payload).not.toHaveProperty("events");
    expect(payload).not.toHaveProperty("script");
    expect(await screen.findByText("已生成 revision 4")).toBeInTheDocument();
  });

  it("keeps the save button disabled until a whitelisted field actually changes", () => {
    render(<MemoryScenarioEditor scenario={scenario} onSaveRevision={vi.fn()} />);
    expect(screen.getByRole("button", { name: "保存为新版本" })).toBeDisabled();
    expect(screen.getByText(/没有修改/)).toBeInTheDocument();
  });
});

describe("MemoryMetricValue", () => {
  it("does not display missing evidence as zero accuracy", () => {
    render(<MemoryMetricValue value={null} unit="ratio" status="not_assessable" />);
    expect(screen.getByText("未评分")).toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });

  it("renders a real zero and scored ratios without inventing values", () => {
    const { rerender } = render(<MemoryMetricValue value={0} unit="ratio" status="scored" />);
    expect(screen.getByText("0%")).toBeInTheDocument();

    rerender(<MemoryMetricValue value={0.856} unit="ratio" status="scored" />);
    expect(screen.getByText("85.6%")).toBeInTheDocument();

    rerender(<MemoryMetricValue value={null} unit="count" status="not_applicable" />);
    expect(screen.getByText("不适用")).toBeInTheDocument();
  });

  it("never treats an unknown status with a value as scored", () => {
    render(<MemoryMetricValue value={12} unit="count" status="unknown" />);
    expect(screen.getByText("未评分")).toBeInTheDocument();
    expect(screen.queryByText("12")).not.toBeInTheDocument();
  });
});
