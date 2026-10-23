import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CapabilityPicker, type CapabilityItem } from "@/components/settings/agents/parts/CapabilityPicker";
import { ModelPicker } from "@/components/settings/agents/parts/ModelPicker";
import type { AgentSelection } from "@/lib/agents/types";

const items: CapabilityItem[] = [
  { id: "read_file", label: "Read files", description: "Read local files", group: "files", locked: true },
  { id: "write_file", label: "Write files", description: "Update local files", group: "files" },
  { id: "run_task", label: "Run task", description: "Unavailable to subagents", group: "files", locked: true, blocked: true, blockedReason: "Not available here" },
];

function Picker({ initial, onChange }: { initial: AgentSelection; onChange: (next: AgentSelection) => void }) {
  const [selection, setSelection] = useState(initial);
  return <CapabilityPicker items={items} selection={selection} onChange={(next) => { setSelection(next); onChange(next); }} modeLabel="Search tools" groupLabelFor={() => "File tools"} />;
}

describe("智能体能力与模型边界", () => {
  it("全部模式只统计可用工具，锁定项和不可用项不能操作", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Picker initial={{ mode: "all", entries: [] }} onChange={onChange} />);

    expect(screen.getByText("2 enabled", { exact: false })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /File tools/ }));
    expect(screen.getByRole("checkbox", { name: /Read files/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Read files/ })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /Run task/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Run task/ })).toBeDisabled();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("排除模式批选按实际启用状态切换，并保留不可用条目的原始配置", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Picker initial={{ mode: "exclude", entries: ["write_file", "run_task"] }} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Select all" }));
    expect(onChange).toHaveBeenLastCalledWith({ mode: "exclude", entries: ["run_task"] });
    await user.click(screen.getByRole("button", { name: /File tools/ }));
    expect(screen.getByRole("checkbox", { name: /Write files/ })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(onChange).toHaveBeenLastCalledWith({ mode: "exclude", entries: ["run_task", "write_file"] });
    expect(screen.getByRole("checkbox", { name: /Read files/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Write files/ })).not.toBeChecked();
  });

  it("目录缺少已保存模型时明确提示，只在主动选跟随全局时清除原值", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ModelPicker models={[]} value="retired-model" onChange={onChange} globalModelLabel="global-model" />);

    const trigger = screen.getByRole("combobox", { name: "Model" });
    expect(trigger).toHaveTextContent("Saved model is currently unavailable");
    expect(trigger).toHaveTextContent("retired-model");
    expect(trigger).not.toHaveTextContent("global-model");
    expect(onChange).not.toHaveBeenCalled();
    await user.click(trigger);
    await user.click(screen.getByRole("option", { name: /Follow global model/ }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(null);
  });
});
