import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MemoryExperimentForm,
  type MemoryExperimentCapabilitiesView,
  type MemoryExperimentDraft,
  type MemoryExperimentFormProps,
} from "@/components/settings/evaluation/memory/MemoryExperimentForm";
import i18n from "@/i18n";

const capabilities: MemoryExperimentCapabilitiesView = {
  tracks: {
    extraction: { status: "supported" },
    retrieval: { status: "supported" },
    end_to_end: { status: "supported" },
  },
  settings: ["retrieval_threshold", "injection_budget_tokens"],
  judge_calibration: "not_calibrated",
  delete: { status: "unsupported" },
  clock_coverage: { status: "partial", detail: "wall clock only" },
  scope_coverage: { status: "supported" },
};

const baseProps: MemoryExperimentFormProps = {
  capabilities,
  datasets: [
    {
      id: "rev-core",
      label: "核心回归集 v1",
      split: "regression",
      status: "ready",
      scenarioCount: 120,
      splitCounts: { regression: 120 },
      forgetScenarios: 2,
    },
    {
      id: "rev-staging",
      label: "导入暂存集",
      split: "development",
      status: "staging",
      scenarioCount: 12,
    },
  ],
  baselines: [
    {
      id: "exp-base",
      label: "上周基线",
      datasetRevisionId: "rev-core",
      track: "end_to_end",
      repetitions: 3,
      modelMode: "real",
      primaryMetric: "qa_accuracy_scenario_macro",
    },
  ],
  profiles: [
    { id: "model:fast", label: "fast", kind: "both", model: "gpt-4o-mini" },
    { id: "judge:strict", label: "strict", kind: "judge" },
  ],
  metrics: [{ id: "qa_accuracy_scenario_macro", label: "记忆回答正确率", unit: "ratio" }],
  scriptFixtures: [{ id: "fixture-default", label: "默认脚本" }],
};

beforeEach(async () => {
  await i18n.changeLanguage("zh-CN");
});

afterEach(() => cleanup());

describe("MemoryExperimentForm", () => {
  it("starts with just the dataset and model, and submits a smoke test without advanced settings", async () => {
    const onSubmit = vi.fn();
    render(
      <MemoryExperimentForm
        {...baseProps}
        datasets={[
          ...baseProps.datasets,
          { id: "rev-smoke", label: "memory-smoke-v1", split: "development", status: "ready", scenarioCount: 20 },
        ]}
        metrics={[
          { id: "extraction_recall", label: "抽取召回率", unit: "ratio" },
          ...baseProps.metrics,
        ]}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByLabelText("测试集版本")).toHaveValue("rev-smoke");
    expect(screen.getByLabelText("被测模型")).toHaveValue("model:fast");
    expect(screen.getAllByRole("combobox")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "高级设置" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText("检索阈值")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("历史基线")).not.toBeInTheDocument();
    expect(screen.getByText(/20 个场景 × 1 次重复 × 1 个候选/)).toBeInTheDocument();

    await userEvent.setup().click(screen.getByLabelText("我已了解以上评测限制"));
    await userEvent.setup().click(screen.getByRole("button", { name: "开始测试" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      dataset_revision_id: "rev-smoke",
      track: "end_to_end",
      primary_metric: "qa_accuracy_scenario_macro",
      repetitions: 1,
      model_mode: "real",
      baseline_experiment_id: null,
      candidates: [{ agent_profile_id: "model:fast", mode: "zhixu", memory_settings: {} }],
    });
  });

  it("keeps the primary metric within the selected track", async () => {
    render(
      <MemoryExperimentForm
        {...baseProps}
        metrics={[
          { id: "extraction_recall", label: "抽取召回率", unit: "ratio" },
          { id: "evidence_recall_at_k", label: "检索召回率", unit: "ratio" },
          ...baseProps.metrics,
        ]}
      />,
    );
    await userEvent.setup().click(screen.getByRole("button", { name: "高级设置" }));
    expect(screen.getByLabelText("主要指标")).toHaveValue("qa_accuracy_scenario_macro");
    expect(screen.queryByRole("option", { name: /抽取召回率/ })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("radio", { name: /是否记下来/ }));
    expect(screen.getByLabelText("主要指标")).toHaveValue("extraction_recall");
    expect(screen.queryByRole("option", { name: /记忆回答正确率/ })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("radio", { name: /是否找得到/ }));
    expect(screen.getByLabelText("主要指标")).toHaveValue("evidence_recall_at_k");
  });

  it("explains an unavailable runtime before the user opens advanced settings", () => {
    render(<MemoryExperimentForm {...baseProps} capabilities={{ tracks: {}, settings: [] }} profiles={[]} datasets={[]} />);
    expect(screen.getByText(/记忆评测尚未就绪/)).toBeInTheDocument();
    expect(screen.getByText(/记忆测试集.*导入内置/)).toBeInTheDocument();
    expect(screen.getByText(/模型设置/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始测试" })).toBeDisabled();
  });

  it("never selects offline controlled profiles for a real model evaluation", () => {
    render(<MemoryExperimentForm {...baseProps} profiles={[
      { id: "controlled-agent", label: "Controlled", kind: "agent", provider: "controlled" },
      { id: "real-agent", label: "Real model", kind: "agent", provider: "openai" },
    ]} />);
    expect(screen.getByLabelText("被测模型")).toHaveValue("real-agent");
    expect(screen.queryByRole("option", { name: "Controlled" })).not.toBeInTheDocument();
  });

  it("offers the three tracks, disables unsupported capabilities and staging datasets", async () => {
    render(
      <MemoryExperimentForm
        {...baseProps}
        capabilities={{
          ...capabilities,
          tracks: {
            extraction: { status: "supported" },
            retrieval: { status: "unsupported" },
            end_to_end: { status: "supported" },
          },
        }}
      />,
    );

    await userEvent.setup().click(screen.getByRole("button", { name: "高级设置" }));
    expect(screen.getByRole("radio", { name: /是否记下来/ })).toBeEnabled();
    expect(screen.getByRole("radio", { name: /是否找得到/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /完整记忆效果/ })).toBeEnabled();
    expect(screen.getByText("当前版本不支持此项评测")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /导入暂存集/ })).toBeDisabled();
  });

  it("only renders memory settings the backend declares, never unsupported knobs", async () => {
    render(<MemoryExperimentForm {...baseProps} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "高级设置" }));
    expect(screen.getByLabelText("检索阈值")).toBeInTheDocument();
    expect(screen.getByLabelText("注入预算（token）")).toBeInTheDocument();
    expect(screen.queryByLabelText("索引版本")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("嵌入模型")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("抽取策略版本")).not.toBeInTheDocument();
    expect(screen.getByLabelText("抽取模型")).toBeDisabled();
    expect(screen.getByText("当前评测的记忆抽取与回答共用被测模型。")).toBeInTheDocument();
  });

  it("recomputes planned trials with repetitions and candidates, and blocks over-budget submit", async () => {
    const onSubmit = vi.fn();
    render(<MemoryExperimentForm {...baseProps} onSubmit={onSubmit} />);

    await userEvent.setup().click(screen.getByRole("button", { name: "高级设置" }));

    expect(screen.getByText(/计划 120 次试验（120 个场景 × 1 次重复 × 1 个候选）/)).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "添加候选" }));
    await userEvent.setup().click(screen.getByRole("button", { name: "添加候选" }));
    expect(screen.getByText(/计划 360 次试验（120 个场景 × 1 次重复 × 3 个候选）/)).toBeInTheDocument();

    const repetitions = screen.getByLabelText("重复次数");
    await userEvent.setup().clear(repetitions);
    await userEvent.setup().type(repetitions, "3");
    expect(screen.getByText(/计划 1080 次试验（120 个场景 × 3 次重复 × 3 个候选）/)).toBeInTheDocument();
    expect(screen.getAllByText(/1080 次试验超过总预算 200/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "开始测试" })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();

    const maxTrials = screen.getByLabelText("总试验预算");
    await userEvent.setup().clear(maxTrials);
    await userEvent.setup().type(maxTrials, "1200");
    expect(screen.queryAllByText(/1080 次试验超过总预算 200/)).toHaveLength(0);

    await userEvent.setup().selectOptions(screen.getByLabelText("评分 Judge"), "judge:strict");
    await userEvent.setup().click(screen.getByLabelText("我已了解以上评测限制"));
    await userEvent.setup().click(screen.getByRole("button", { name: "高级设置" }));
    expect(screen.queryByLabelText("总试验预算")).not.toBeInTheDocument();
    expect(screen.getByText(/1080 次试验（120 个场景 × 3 次重复 × 3 个候选）/)).toBeInTheDocument();
    const submitButton = screen.getByRole("button", { name: "开始测试" });
    expect(submitButton).toBeEnabled();
    await userEvent.setup().click(submitButton);

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const draft = onSubmit.mock.calls[0][0] as MemoryExperimentDraft;
    expect(draft).toMatchObject({
      dataset_revision_id: "rev-core",
      split: "regression",
      track: "end_to_end",
      repetitions: 3,
      model_mode: "real",
      judge_profile_id: "judge:strict",
      primary_metric: "qa_accuracy_scenario_macro",
      baseline_experiment_id: null,
      changed_fields: [],
      budgets: { max_trials: 1200 },
    });
    expect(draft.candidates).toHaveLength(3);
    expect(draft.candidates[0]).toEqual({
      id: "candidate-1",
      name: "候选 1",
      mode: "zhixu",
      agent_profile_id: "model:fast",
      extractor_profile_id: null,
      memory_settings: {},
    });
    expect(draft).not.toHaveProperty("script_fixture_id");
  });

  it("shows pre-submit limits, uses saved experiments as baseline and requires changed fields", async () => {
    const onSubmit = vi.fn();
    render(<MemoryExperimentForm {...baseProps} onSubmit={onSubmit} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "高级设置" }));

    expect(screen.getByText("未选择 Judge：语义评分项将标记为待复核。")).toBeInTheDocument();
    expect(
      screen.getByText("后端不支持删除：遗忘类场景无法通过，将标记为不支持。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("时钟覆盖不足：时间相关判定将不可评分。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("基线从已保存实验选择；本版本不支持从界面切换 Git 源码。"),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(/git/i)).not.toBeInTheDocument();

    await userEvent.setup().selectOptions(screen.getByLabelText("历史基线"), "exp-base");
    expect(screen.getByText(/基线条件不一致：重复次数/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始测试" })).toBeDisabled();
    expect(
      screen.getByText("与历史基线比较前必须声明本次改动项。"),
    ).toBeInTheDocument();

    await userEvent.setup().click(
      screen.getByLabelText("memory_settings.retrieval_threshold"),
    );
    await userEvent.setup().selectOptions(screen.getByLabelText("评分 Judge"), "judge:strict");
    await userEvent.setup().click(screen.getByLabelText("我已了解以上评测限制"));
    await userEvent.setup().click(screen.getByRole("button", { name: "开始测试" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      baseline_experiment_id: "exp-base",
      changed_fields: ["memory_settings.retrieval_threshold"],
    });
  });

  it("keeps the no-memory reference group limited to the end-to-end track", async () => {
    render(<MemoryExperimentForm {...baseProps} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "高级设置" }));
    const noMemoryOption = screen.getByRole("option", { name: "无持久记忆参考组" });
    expect(noMemoryOption).toBeEnabled();

    await userEvent.setup().click(screen.getByRole("radio", { name: /是否记下来/ }));
    expect(screen.getByRole("option", { name: "无持久记忆参考组" })).toBeDisabled();
  });
});
