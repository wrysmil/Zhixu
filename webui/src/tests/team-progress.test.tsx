// 实时成员面板（plan Task 10 / 契约 §5/§6）。
//
// 覆盖三条口径：
// 1. 面板只显示服务端说过的东西：任务标题、成员、最近活动、结果摘要，没有伪造进度。
// 2. 停止按钮按服务端状态机走：cancelling 就显示「正在停止」，不是「已停止」。
// 3. 帧 → hook → 面板的整条链路：补拉快照、缺口补拉、取消/重试 mutation。
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TeamProgressPanel } from "@/components/thread/TeamProgressPanel";
import { useTeamProgress, type TeamFrameClient } from "@/hooks/useTeamProgress";
import * as teamsApi from "@/lib/teams/api";
import { teamRunStateFromSnapshot } from "@/lib/teams/reducer";
import type { TeamRunSnapshot, TeamTaskSnapshot } from "@/lib/teams/types";

vi.mock("@/lib/teams/api", () => ({
  listTeamRuns: vi.fn(),
  loadTeamRun: vi.fn(),
  loadTeamRunEvents: vi.fn(),
  loadTeamDetail: vi.fn(),
  cancelTeamRun: vi.fn(),
  cancelTeamTask: vi.fn(),
  retryTeamRun: vi.fn(),
}));

function task(overrides: Partial<TeamTaskSnapshot> = {}): TeamTaskSnapshot {
  return {
    taskId: "task-1",
    memberId: "m-researcher",
    agentId: "researcher",
    title: "查阅资料",
    acceptance: "给出来源",
    dependsOn: [],
    status: "running",
    attemptId: "att-1",
    lastAction: "正在读取文档",
    startedAt: "2026-10-15T08:00:01+00:00",
    finishedAt: null,
    result: null,
    ...overrides,
  };
}

function run(overrides: Partial<TeamRunSnapshot> = {}): TeamRunSnapshot {
  return {
    runId: "run-1",
    chatId: "chat-1",
    turnId: "turn-1",
    binding: { teamId: "content-team", teamRevision: 2, teamName: "内容团队" },
    status: "running",
    lastSeq: 2,
    tasks: [task()],
    leaderAction: "waiting",
    startedAt: "2026-10-15T08:00:00+00:00",
    finishedAt: null,
    parentRunId: null,
    ...overrides,
  };
}

class FakeClient implements TeamFrameClient {
  private handlers = new Map<string, (event: unknown) => void>();

  onChat(chatId: string, handler: (event: unknown) => void): () => void {
    this.handlers.set(chatId, handler);
    return () => this.handlers.delete(chatId);
  }

  emit(chatId: string, event: unknown): void {
    this.handlers.get(chatId)?.(event);
  }
}

function taskFrame(chatId: string, data: Record<string, unknown>) {
  return {
    event: "team_event",
    chat_id: chatId,
    turn_id: "turn-1",
    data: {
      schemaVersion: 1,
      runId: "run-1",
      chatId,
      turnId: "turn-1",
      seq: 3,
      type: "task.activity",
      taskId: "task-1",
      attemptId: "att-1",
      occurredAt: "2026-10-15T08:00:02+00:00",
      data: { task: task({ lastAction: "正在整理引用" }) },
      ...data,
    },
  };
}

beforeEach(() => {
  vi.mocked(teamsApi.listTeamRuns).mockReset().mockResolvedValue({ runs: [], nextCursor: null });
  vi.mocked(teamsApi.loadTeamRun).mockReset().mockResolvedValue({ run: run() });
  vi.mocked(teamsApi.loadTeamRunEvents)
    .mockReset()
    .mockResolvedValue({ events: [], lastSeq: 0, hasMore: false, resetRequired: false });
  vi.mocked(teamsApi.loadTeamDetail).mockReset().mockResolvedValue({
    team: {} as never,
    snapshot: { definition: {} as never, leader: {} as never, members: [] },
  });
  vi.mocked(teamsApi.cancelTeamRun).mockReset();
  vi.mocked(teamsApi.cancelTeamTask).mockReset();
  vi.mocked(teamsApi.retryTeamRun).mockReset();
});

describe("TeamProgressPanel", () => {
  it("显示成员、任务与最近活动，没有运行时不渲染", () => {
    const { rerender } = render(<TeamProgressPanel run={null} memberNames={{}} />);
    expect(screen.queryByLabelText("Team progress")).toBeNull();

    rerender(
      <TeamProgressPanel
        run={teamRunStateFromSnapshot(run())}
        memberNames={{ "m-researcher": "研究员" }}
      />,
    );
    expect(screen.getByText("内容团队")).toBeInTheDocument();
    expect(screen.getByText("查阅资料")).toBeInTheDocument();
    expect(screen.getByText("研究员")).toBeInTheDocument();
    expect(screen.getByText("正在读取文档")).toBeInTheDocument();
  });

  it("cancelling 期间显示正在停止而不是已停止", async () => {
    render(
      <TeamProgressPanel
        run={teamRunStateFromSnapshot(run({ status: "cancelling" }))}
        memberNames={{}}
        canControl
        onCancelRun={vi.fn()}
      />,
    );
    expect(screen.getByText("Stopping…", { selector: "[data-team-run-status]" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stopping…" })).toBeDisabled();
  });

  it("失败终态才给重试入口，并提示可能重复副作用", async () => {
    const onRetryRun = vi.fn();
    const user = userEvent.setup();
    render(
      <TeamProgressPanel
        run={teamRunStateFromSnapshot(
          run({ status: "failed", tasks: [task({ status: "failed" })] }),
        )}
        memberNames={{}}
        canControl
        onRetryRun={onRetryRun}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Retry failed tasks/ }));
    expect(onRetryRun).toHaveBeenCalledWith("run-1");
    expect(screen.getByText(/may repeat existing side effects/)).toBeInTheDocument();
  });

  it("没有派单的回合也能显示完成", async () => {
    render(
      <TeamProgressPanel
        run={teamRunStateFromSnapshot(run({ status: "completed", tasks: [] }))}
        memberNames={{}}
      />,
    );
    expect(screen.getByText("The Leader handled this turn directly.")).toBeInTheDocument();
  });
});

describe("useTeamProgress", () => {
  it("用历史运行开场，再按 seq 归并实时增量", async () => {
    vi.mocked(teamsApi.listTeamRuns).mockResolvedValue({ runs: [run()], nextCursor: null });
    const client = new FakeClient();
    const { result } = renderHook(() =>
      useTeamProgress(client, { chatId: "chat-1", token: "t", transport: null }),
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.activeRun?.runId).toBe("run-1");
    expect(result.current.activeRun?.lastSeq).toBe(2);

    await act(async () => {
      client.emit("chat-1", taskFrame("chat-1", {}));
    });
    expect(result.current.activeRun?.lastSeq).toBe(3);
    expect(result.current.activeRun?.tasks["task-1"].lastAction).toBe("正在整理引用");
  });

  it("看不懂的帧不影响面板，也不会去拉快照", async () => {
    const client = new FakeClient();
    const { result } = renderHook(() =>
      useTeamProgress(client, { chatId: "chat-1", token: "t", transport: null }),
    );
    await act(async () => {
      client.emit("chat-1", { event: "message", chat_id: "chat-1", text: "hi" });
      client.emit("chat-1", { event: "team_event", chat_id: "chat-1", data: { broken: true } });
    });
    expect(result.current.runs).toEqual([]);
    expect(teamsApi.loadTeamRun).not.toHaveBeenCalled();
  });

  it("seq 断号时回到服务端补拉快照", async () => {
    vi.mocked(teamsApi.listTeamRuns).mockResolvedValue({ runs: [run()], nextCursor: null });
    // 增量已被剪掉：客户端必须回快照，而不是拿缺口里的半条事件凑合。
    vi.mocked(teamsApi.loadTeamRunEvents).mockResolvedValue({
      events: [],
      lastSeq: 0,
      hasMore: false,
      resetRequired: true,
    });
    vi.mocked(teamsApi.loadTeamRun).mockResolvedValue({
      run: run({ lastSeq: 9, tasks: [task({ lastAction: "已补齐" })] }),
    });
    const client = new FakeClient();
    const { result } = renderHook(() =>
      useTeamProgress(client, { chatId: "chat-1", token: "t", transport: null }),
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.activeRun?.runId).toBe("run-1");

    await act(async () => {
      client.emit("chat-1", taskFrame("chat-1", { seq: 12 }));
    });

    await waitFor(() => expect(result.current.activeRun?.lastSeq).toBe(9));
    expect(result.current.activeRun?.tasks["task-1"].lastAction).toBe("已补齐");
    expect(teamsApi.loadTeamRun).toHaveBeenCalledWith("t", "run-1");
  });

  it("取消按服务端回的状态更新，不自己猜已停止", async () => {
    vi.mocked(teamsApi.listTeamRuns).mockResolvedValue({ runs: [run()], nextCursor: null });
    vi.mocked(teamsApi.cancelTeamRun).mockResolvedValue({ runId: "run-1", status: "cancelling" });
    const client = new FakeClient();
    const { result } = renderHook(() =>
      useTeamProgress(client, { chatId: "chat-1", token: "t", transport: {} }),
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.activeRun?.runId).toBe("run-1");

    await act(async () => {
      await result.current.cancelRun("run-1");
    });

    expect(result.current.activeRun?.status).toBe("cancelling");
    expect(teamsApi.cancelTeamRun).toHaveBeenCalledWith({}, "run-1", expect.any(String));
  });

  it("切换会话会释放订阅并清空上一段运行", async () => {
    vi.mocked(teamsApi.listTeamRuns).mockResolvedValue({ runs: [run()], nextCursor: null });
    const client = new FakeClient();
    const { result, rerender } = renderHook(
      ({ chatId }: { chatId: string | null }) =>
        useTeamProgress(client, { chatId, token: "t", transport: null }),
      { initialProps: { chatId: "chat-1" as string | null } },
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.activeRun?.runId).toBe("run-1");

    rerender({ chatId: "chat-2" });
    await waitFor(() => expect(result.current.runs).toEqual([]));
    expect(client.handlers.has("chat-1")).toBe(false);
  });
});
