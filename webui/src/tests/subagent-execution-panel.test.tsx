import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { AgentActivityCluster } from "@/components/thread/AgentActivityCluster";
import {
  SubagentExecutionEntry,
  SubagentExecutionEntryProvider,
  SubagentExecutionPanel,
  type SubagentExecutionEntryValue,
} from "@/components/thread/SubagentExecutionPanel";
import { parseGenericToolTrace } from "@/components/thread/activity/generic-tool-model";
import type {
  ExecutionActivity,
  ExecutionSnapshot,
  SubagentExecutionState,
} from "@/features/subagent-execution/types";
import { executionStateFromSnapshot } from "@/features/subagent-execution/reducer";
import i18n from "@/i18n";

function snapshot(overrides: Partial<ExecutionSnapshot> = {}): ExecutionSnapshot {
  return {
    invocationId: "inv-a",
    chatId: "chat-1",
    parentTurnId: "turn-1",
    parentToolCallId: "call-a",
    agentId: "researcher",
    agentName: "调研助手",
    title: "查询近期金价",
    instructionSummary: "查询公开报价资料，并保留日期、单位和来源。",
    mode: "background",
    status: "running",
    phase: "tools",
    createdAt: "2026-10-18T08:00:00Z",
    startedAt: "2026-10-18T08:00:01Z",
    finishedAt: null,
    queueDurationMs: 1000,
    durationMs: null,
    lastAction: "搜索网页",
    completedToolCount: 2,
    lastSeq: 3,
    result: null,
    activities: [],
    ...overrides,
  };
}

function activity(overrides: Partial<ExecutionActivity> = {}): ExecutionActivity {
  return {
    activityId: "activity-a",
    kind: "tool",
    toolName: "web_search",
    status: "running",
    label: "搜索网页",
    startedAt: "2026-10-18T08:00:05Z",
    finishedAt: null,
    detail: null,
    output: null,
    ...overrides,
  };
}

function state(overrides: Partial<ExecutionSnapshot> = {}): SubagentExecutionState {
  const value = snapshot(overrides);
  return {
    ...executionStateFromSnapshot(value),
    activities: value.activities,
  };
}

function entryValue(
  executions: SubagentExecutionState[],
  onOpen = vi.fn(),
  agentIconById: Record<string, string> = {},
): SubagentExecutionEntryValue {
  return {
    capability: true,
    byInvocationId: Object.fromEntries(
      executions.map((execution) => [execution.snapshot.invocationId, execution]),
    ),
    invocationIdByToolCallId: Object.fromEntries(
      executions.map((execution) => [
        execution.snapshot.parentToolCallId,
        execution.snapshot.invocationId,
      ]),
    ),
    agentIconById,
    onOpen,
  };
}

describe("SubagentExecutionPanel", () => {
  it("renders only public execution detail and keeps reload read-only", async () => {
    const user = userEvent.setup();
    const onReload = vi.fn();
    const onClose = vi.fn();
    const current = state({ activities: [activity()] });

    render(
      <SubagentExecutionPanel
        executions={[current]}
        selectedInvocationId={current.snapshot.invocationId}
        onSelect={() => {}}
        onReload={onReload}
        onClose={onClose}
      />,
    );

    expect(screen.getByText("调研助手")).toBeInTheDocument();
    expect(screen.getAllByText("查询近期金价").length).toBeGreaterThan(0);
    expect(screen.getByText(/查询公开报价资料，并保留日期、单位和来源。/)).toBeInTheDocument();
    expect(screen.getByText("web_search")).toBeInTheDocument();
    expect(screen.getByTestId("subagent-execution-status-live")).toHaveTextContent(
      i18n.t("thread.subagentExecution.status.running"),
    );

    await user.click(screen.getByRole("button", { name: i18n.t("thread.subagentExecution.reload") }));
    await user.click(screen.getByRole("button", { name: i18n.t("thread.subagentExecution.close") }));

    expect(onReload).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("uses a Radix radio task menu for multiple executions and no menu for one", async () => {
    const user = userEvent.setup();
    const first = state();
    const second = state({
      invocationId: "inv-b",
      parentToolCallId: "call-b",
      agentName: "校对助手",
      title: "核对日期与单位",
      status: "queued",
      startedAt: null,
      queueDurationMs: 8000,
    });
    const onSelect = vi.fn();

    const { rerender } = render(
      <SubagentExecutionPanel
        executions={[first, second]}
        selectedInvocationId={first.snapshot.invocationId}
        onSelect={onSelect}
        onReload={() => {}}
        onClose={() => {}}
      />,
    );

    const switcherLabel = i18n.t("thread.subagentExecution.taskSwitcher");
    const trigger = screen.getByRole("button", { name: new RegExp(switcherLabel) });
    await user.click(trigger);
    const firstOption = await screen.findByRole("menuitemradio", { name: /调研助手.*查询近期金价/ });
    expect(firstOption).toHaveAttribute("aria-checked", "true");
    const secondOption = screen.getByRole("menuitemradio", { name: /校对助手.*核对日期与单位/ });

    await user.click(secondOption);
    expect(onSelect).toHaveBeenCalledWith("inv-b");
    await waitFor(() => expect(trigger).toHaveFocus());

    rerender(
      <SubagentExecutionPanel
        executions={[first]}
        selectedInvocationId={first.snapshot.invocationId}
        onSelect={() => {}}
        onReload={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: new RegExp(switcherLabel) })).toBeNull();
  });

  it("shows terminal result, truncation, history and connection notices", () => {
    const finished = state({
      status: "succeeded",
      phase: "finalizing",
      finishedAt: "2026-10-18T08:00:20Z",
      durationMs: 19000,
      result: {
        summary: "已整理公开资料和来源。",
        truncated: true,
        stopReason: "completed",
        errorCode: null,
        errorMessage: null,
      },
      activities: [
        activity({ status: "succeeded", finishedAt: "2026-10-18T08:00:08Z" }),
      ],
    });
    finished.historyLimited = true;
    finished.connectionStale = true;

    render(
      <SubagentExecutionPanel
        executions={[finished]}
        selectedInvocationId={finished.snapshot.invocationId}
        onSelect={() => {}}
        onReload={() => {}}
        onClose={() => {}}
      />,
    );

    expect(screen.getByText("已整理公开资料和来源。")).toBeInTheDocument();
    // 终态后不应再显示阶段文案，否则出现「Done · Finalizing」这类自相矛盾的进度（真机页面验证发现）。
    expect(screen.queryByText(i18n.t("thread.subagentExecution.phase.finalizing"))).toBeNull();
    expect(screen.getByText(i18n.t("thread.subagentExecution.truncated"))).toBeInTheDocument();
    expect(screen.getByText(i18n.t("thread.subagentExecution.historyLimited"))).toBeInTheDocument();
    expect(screen.getByText(i18n.t("thread.subagentExecution.connectionStale"))).toBeInTheDocument();
  });

  it("renders sub-second tool activity durations in milliseconds, not 0 sec", () => {
    // 真机发现：read_file 等本地工具单次仅 ~1-2ms，旧的 formatDuration 取整到整秒，
    // 导致 EXECUTION 时间线每一行都显示 "0 sec"，看起来像坏了。
    const lng = i18n.resolvedLanguage ?? i18n.language;
    const ms = (value: number) =>
      new Intl.NumberFormat(lng, { style: "unit", unit: "millisecond", unitDisplay: "short" }).format(value);
    const fast = state({
      status: "succeeded",
      phase: "finalizing",
      finishedAt: "2026-10-18T08:00:09Z",
      durationMs: 8000,
      activities: [
        activity({
          activityId: "act-fast",
          status: "succeeded",
          startedAt: "2026-10-18T08:00:05.000Z",
          finishedAt: "2026-10-18T08:00:05.420Z",
        }),
        activity({
          activityId: "act-tiny",
          status: "failed",
          startedAt: "2026-10-18T08:00:06.000Z",
          finishedAt: "2026-10-18T08:00:06.002Z",
        }),
      ],
    });

    render(
      <SubagentExecutionPanel
        executions={[fast]}
        selectedInvocationId={fast.snapshot.invocationId}
        onSelect={() => {}}
        onReload={() => {}}
        onClose={() => {}}
      />,
    );

    const fastRow = screen.getByTestId("subagent-execution-activity-act-fast");
    const tinyRow = screen.getByTestId("subagent-execution-activity-act-tiny");
    expect(within(fastRow).getByText(ms(420))).toBeInTheDocument();
    expect(within(tinyRow).getByText(ms(2))).toBeInTheDocument();
    expect(fastRow.textContent).not.toMatch(/0\s*(sec|s|秒)/);
    expect(tinyRow.textContent).not.toMatch(/0\s*(sec|s|秒)/);
  });

  it("工具活动行以工具名作标题，不重复通用动作文案", () => {
    // 真机发现：后端对每次工具调用都传同一个 label「正在使用工具」，
    // 时间线每行标题都一模一样，看不出究竟调用了什么工具。
    const current = state({
      activities: [
        activity({
          activityId: "act-tool",
          toolName: "read_file",
          label: "正在使用工具",
          status: "succeeded",
          finishedAt: "2026-10-18T08:00:07Z",
        }),
        activity({
          activityId: "act-progress",
          kind: "progress",
          toolName: null,
          label: "正在整理结果",
          status: "running",
        }),
      ],
    });

    render(
      <SubagentExecutionPanel
        executions={[current]}
        selectedInvocationId={current.snapshot.invocationId}
        onSelect={() => {}}
        onReload={() => {}}
        onClose={() => {}}
      />,
    );

    const toolRow = screen.getByTestId("subagent-execution-activity-act-tool");
    expect(toolRow).toHaveTextContent("read_file");
    expect(toolRow.textContent?.match(/read_file/g)).toHaveLength(1);
    expect(toolRow).not.toHaveTextContent("正在使用工具");

    // 无工具名的进度活动仍用服务端文案兜底。
    const progressRow = screen.getByTestId("subagent-execution-activity-act-progress");
    expect(progressRow).toHaveTextContent("正在整理结果");
  });

  it("有参数/输出摘要的活动行默认折叠，展开后才显示", async () => {
    const user = userEvent.setup();
    const current = state({
      activities: [
        activity({
          activityId: "act-detail",
          toolName: "read_file",
          status: "succeeded",
          finishedAt: "2026-10-18T08:00:07Z",
          detail: "path=src/app.py",
          output: "lines=128",
        }),
      ],
    });

    render(
      <SubagentExecutionPanel
        executions={[current]}
        selectedInvocationId={current.snapshot.invocationId}
        onSelect={() => {}}
        onReload={() => {}}
        onClose={() => {}}
      />,
    );

    const row = screen.getByTestId("subagent-execution-activity-act-detail");
    // 默认折叠：摘要不进 DOM，展开前也查不到。
    expect(row).not.toHaveTextContent("path=src/app.py");
    const toggle = within(row).getByRole("button", {
      name: i18n.t("thread.subagentExecution.detailToggle"),
    });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(within(row).getByText(i18n.t("thread.subagentExecution.parameters"))).toBeInTheDocument();
    expect(within(row).getByText(i18n.t("thread.subagentExecution.toolOutput"))).toBeInTheDocument();
    expect(within(row).getByText("path=src/app.py")).toBeInTheDocument();
    expect(within(row).getByText("lines=128")).toBeInTheDocument();
  });

  it("没有摘要的活动行不显示展开控件", () => {
    const current = state({
      activities: [
        activity({
          activityId: "act-plain",
          status: "succeeded",
          finishedAt: "2026-10-18T08:00:07Z",
        }),
      ],
    });

    render(
      <SubagentExecutionPanel
        executions={[current]}
        selectedInvocationId={current.snapshot.invocationId}
        onSelect={() => {}}
        onReload={() => {}}
        onClose={() => {}}
      />,
    );

    const row = screen.getByTestId("subagent-execution-activity-act-plain");
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("摘要被服务端截断时提示内容已截断", async () => {
    const user = userEvent.setup();
    const current = state({
      activities: [
        activity({
          activityId: "act-cut",
          toolName: "grep",
          status: "succeeded",
          finishedAt: "2026-10-18T08:00:07Z",
          detail: "pattern=很长很长很长…",
        }),
      ],
    });

    render(
      <SubagentExecutionPanel
        executions={[current]}
        selectedInvocationId={current.snapshot.invocationId}
        onSelect={() => {}}
        onReload={() => {}}
        onClose={() => {}}
      />,
    );

    await user.click(
      within(screen.getByTestId("subagent-execution-activity-act-cut")).getByRole("button", {
        name: i18n.t("thread.subagentExecution.detailToggle"),
      }),
    );
    expect(
      screen.getAllByText(i18n.t("thread.subagentExecution.truncated")).length,
    ).toBeGreaterThan(0);
  });

  it("uses a focus-managed dialog on narrow surfaces and restores the trigger", async () => {
    const user = userEvent.setup();
    const current = state();
    const returnFocus = document.createElement("button");
    returnFocus.textContent = "入口";
    document.body.appendChild(returnFocus);
    returnFocus.focus();
    const onClose = vi.fn();

    function DialogHarness() {
      const [open, setOpen] = useState(true);
      if (!open) return null;
      return (
        <SubagentExecutionPanel
          executions={[current]}
          selectedInvocationId={current.snapshot.invocationId}
          onSelect={() => {}}
          onReload={() => {}}
          onClose={() => {
            onClose();
            setOpen(false);
          }}
          presentation="dialog"
          returnFocus={returnFocus}
        />
      );
    }

    render(<DialogHarness />);

    const dialog = screen.getByRole("dialog", { name: /查询近期金价/ });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(returnFocus).toHaveFocus());
  });
});

describe("SubagentExecutionEntry", () => {
  it("opens the matched invocation from spawn call_id without title guessing", async () => {
    const user = userEvent.setup();
    const trace = parseGenericToolTrace(
      'spawn({"label":"查询近期金价","task":"private task that must not render"})',
    )!;
    const onOpen = vi.fn();

    render(
      <SubagentExecutionEntryProvider value={entryValue([state()], onOpen)}>
        <SubagentExecutionEntry
          item={{ trace, status: "running", toolCallId: "call-a" }}
        />
      </SubagentExecutionEntryProvider>,
    );

    const entry = screen.getByTestId("subagent-execution-entry");
    expect(entry).toHaveAccessibleName(/调研助手.*查询近期金价.*Running/);
    await user.click(entry);
    expect(onOpen).toHaveBeenCalledWith("inv-a", entry);
    expect(screen.queryByText(/private task/)).toBeNull();
  });

  it("joins a spawn trace through call_id in the real activity cluster", async () => {
    const user = userEvent.setup();
    const trace = 'spawn({"label":"查询近期金价","task":"private task"})';
    const message = {
      id: "trace-1",
      role: "tool" as const,
      kind: "trace" as const,
      content: trace,
      traces: [trace],
      toolEvents: [{
        phase: "start",
        call_id: "call-a",
        name: "spawn",
        arguments: { label: "查询近期金价", task: "private task" },
      }],
      createdAt: 1,
    };
    const onOpen = vi.fn();

    render(
      <SubagentExecutionEntryProvider value={entryValue([state()], onOpen)}>
        <AgentActivityCluster
          messages={[message]}
          isTurnStreaming
          hasBodyBelow={false}
        />
      </SubagentExecutionEntryProvider>,
    );

    const entry = screen.getByTestId("subagent-execution-entry");
    expect(entry).toHaveAttribute("data-invocation-id", "inv-a");
    await user.click(entry);
    expect(onOpen).toHaveBeenCalledWith("inv-a", entry);
    expect(screen.queryByText(/private task/)).toBeNull();
  });

  it("keeps an old gateway spawn row visually unchanged without a no-detail affordance", () => {
    const trace = parseGenericToolTrace(
      'spawn({"label":"旧网关任务","task":"private task"})',
    )!;

    render(
      <SubagentExecutionEntryProvider value={{ ...entryValue([]), capability: false }}>
        <SubagentExecutionEntry item={{ trace, status: "done", toolCallId: "old-call" }} />
      </SubagentExecutionEntryProvider>,
    );

    expect(screen.queryByText(i18n.t("thread.subagentExecution.unavailable"))).toBeNull();
    expect(screen.getByText(/Delegated task/)).toBeInTheDocument();
  });

  it("keeps legacy spawn rows unchanged and marks unavailable details", () => {
    const trace = parseGenericToolTrace(
      'spawn({"label":"历史任务","task":"private task"})',
    )!;

    render(
      <SubagentExecutionEntryProvider value={entryValue([])}>
        <SubagentExecutionEntry item={{ trace, status: "done", toolCallId: "legacy-call" }} />
      </SubagentExecutionEntryProvider>,
    );

    expect(screen.getByText(i18n.t("thread.subagentExecution.unavailable"))).toBeInTheDocument();
    expect(screen.queryByTestId("subagent-execution-entry")).toBeNull();
  });
});

describe("SubagentExecutionEntry 终态文案", () => {
  it("终态入口不再显示过时的进行中文案，只保留已完成工具数", () => {
    const finished = state({
      status: "succeeded",
      phase: "finalizing",
      lastAction: "正在整理结果",
      completedToolCount: 1,
      finishedAt: "2026-10-18T08:00:09Z",
      durationMs: 9000,
    });
    const trace = parseGenericToolTrace('spawn({"label":"查询近期金价"})')!;

    render(
      <SubagentExecutionEntryProvider value={entryValue([finished], () => {})}>
        <SubagentExecutionEntry item={{ trace, status: "done", toolCallId: "call-a" }} />
      </SubagentExecutionEntryProvider>,
    );

    expect(screen.queryByText(/正在整理结果/)).toBeNull();
    const row = screen.getByTestId("subagent-execution-entry");
    expect(row).toHaveAttribute("data-status", "succeeded");
    expect(row).toHaveTextContent(i18n.t("thread.subagentExecution.status.succeeded"));
  });
});

describe("子 Agent 活动行", () => {
  const spawnMessage = {
    id: "trace-1",
    role: "tool" as const,
    kind: "trace" as const,
    content: 'spawn({"label":"查询近期金价","task":"private task"})',
    traces: ['spawn({"label":"查询近期金价","task":"private task"})'],
    toolEvents: [{
      phase: "start",
      call_id: "call-a",
      name: "spawn",
      arguments: { label: "查询近期金价", task: "private task" },
    }],
    createdAt: 1,
  };

  it("活动簇折叠时主聊天仍保留可点击的 Agent 活动行", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();

    render(
      <SubagentExecutionEntryProvider value={entryValue([state()], onOpen)}>
        <AgentActivityCluster
          messages={[spawnMessage]}
          isTurnStreaming={false}
          hasBodyBelow={false}
        />
      </SubagentExecutionEntryProvider>,
    );

    // 活动簇折叠时不渲染活动内容，Agent 活动行仍然要在主聊天里可见。
    expect(screen.queryByTestId("agent-activity-scroll")).toBeNull();
    expect(screen.getByTestId("subagent-execution-entries")).toBeInTheDocument();

    const row = screen.getByTestId("subagent-execution-entry");
    expect(row).toHaveAttribute("data-invocation-id", "inv-a");
    expect(row).toHaveAttribute("data-status", "running");
    expect(row).toHaveTextContent("调研助手");
    expect(row).toHaveTextContent("查询近期金价");
    expect(row).toHaveTextContent(i18n.t("thread.subagentExecution.status.running"));
    // 没有配置图标时回退到内置默认 Agent 图标，且没有卡片容器
    expect(row.querySelector('img[src="/icons/agent-bot.svg"]')).not.toBeNull();

    await user.click(row);
    expect(onOpen).toHaveBeenCalledWith("inv-a", row);
  });

  it("用档案配置的图标，并标出是哪个 Agent", () => {
    const profiled = state({
      invocationId: "inv-a",
      parentToolCallId: "call-a",
      agentId: "researcher",
      agentName: "资料研究",
    });

    render(
      <SubagentExecutionEntryProvider
        value={entryValue([profiled], vi.fn(), { researcher: "🔬" })}
      >
        <AgentActivityCluster
          messages={[spawnMessage]}
          isTurnStreaming={false}
          hasBodyBelow={false}
        />
      </SubagentExecutionEntryProvider>,
    );

    const row = screen.getByTestId("subagent-execution-entry");
    expect(row).toHaveTextContent("资料研究");
    expect(row).toHaveTextContent("🔬");
    expect(row.querySelector('img[src="/icons/agent-bot.svg"]')).toBeNull();
  });

  it("没有档案的调用显示通用 Agent 与默认图标", () => {
    const generic = state({
      invocationId: "inv-a",
      parentToolCallId: "call-a",
      agentId: null,
      agentName: "子 Agent",
    });

    render(
      <SubagentExecutionEntryProvider value={entryValue([generic])}>
        <AgentActivityCluster
          messages={[spawnMessage]}
          isTurnStreaming={false}
          hasBodyBelow={false}
        />
      </SubagentExecutionEntryProvider>,
    );

    const row = screen.getByTestId("subagent-execution-entry");
    expect(row).toHaveTextContent(i18n.t("thread.subagentExecution.genericAgent"));
    expect(row).not.toHaveTextContent("子 Agent ·");
    expect(row.querySelector('img[src="/icons/agent-bot.svg"]')).not.toBeNull();
  });

  it("运行中／运行错误／运行完成三种状态都在活动行里可读", () => {
    const executions = [
      state({ invocationId: "inv-run", parentToolCallId: "call-a", status: "running" }),
      state({
        invocationId: "inv-fail",
        parentToolCallId: "call-b",
        status: "failed",
        errorCode: "MODEL_ERROR",
        errorMessage: "model unavailable",
      }),
      state({
        invocationId: "inv-done",
        parentToolCallId: "call-c",
        status: "succeeded",
        phase: "finalizing",
        finishedAt: "2026-10-18T08:00:09Z",
        durationMs: 8000,
      }),
    ];
    const messages = ["call-a", "call-b", "call-c"].map((callId, index) => {
      const trace = `spawn({"label":"任务${index + 1}","task":"private task"})`;
      return {
        id: `trace-${index + 1}`,
        role: "tool" as const,
        kind: "trace" as const,
        content: trace,
        traces: [trace],
        toolEvents: [{
          phase: "start",
          call_id: callId,
          name: "spawn",
          arguments: { label: `任务${index + 1}`, task: "private task" },
        }],
        createdAt: index + 1,
      };
    });

    render(
      <SubagentExecutionEntryProvider value={entryValue(executions)}>
        <AgentActivityCluster
          messages={messages}
          isTurnStreaming={false}
          hasBodyBelow={false}
        />
      </SubagentExecutionEntryProvider>,
    );

    const rows = screen.getAllByTestId("subagent-execution-entry");
    expect(rows).toHaveLength(3);
    const textByStatus = Object.fromEntries(
      rows.map((row) => [row.getAttribute("data-status"), row.textContent ?? ""]),
    );
    expect(textByStatus.running).toContain(
      i18n.t("thread.subagentExecution.status.running"),
    );
    expect(textByStatus.failed).toContain(
      i18n.t("thread.subagentExecution.status.failed"),
    );
    expect(textByStatus.succeeded).toContain(
      i18n.t("thread.subagentExecution.status.succeeded"),
    );
  });

  it("旧 gateway 未声明能力时保留原有 generic 行，不渲染卡片控件", () => {
    render(
      <SubagentExecutionEntryProvider value={{ ...entryValue([]), capability: false }}>
        <AgentActivityCluster
          messages={[spawnMessage]}
          isTurnStreaming={false}
          hasBodyBelow={false}
        />
      </SubagentExecutionEntryProvider>,
    );

    expect(screen.queryByTestId("subagent-execution-entry")).toBeNull();
    expect(screen.getAllByTestId("activity-step").length).toBeGreaterThan(0);
  });

  it("旧历史没有可关联调用时标注暂无执行详情，不渲染卡片控件", () => {
    render(
      <SubagentExecutionEntryProvider value={entryValue([])}>
        <AgentActivityCluster
          messages={[spawnMessage]}
          isTurnStreaming={false}
          hasBodyBelow={false}
        />
      </SubagentExecutionEntryProvider>,
    );

    expect(screen.queryByTestId("subagent-execution-entry")).toBeNull();
    expect(screen.getByTestId("subagent-execution-unavailable")).toBeInTheDocument();
  });
});
