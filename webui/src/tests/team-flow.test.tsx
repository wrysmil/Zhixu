// 团队整链路（plan Task 11 / 契约 §5/§6/§7）。
//
// 这一份只测**通路**：网关发出的帧 → `parseTeamFrame` → seq reducer → `useTeamProgress`
// → `TeamProgressPanel` 的 DOM。fixture 的字段名照抄 `nanobot/webui/outbound_wire.py`
// 的 `encode_team_event` / `encode_team_snapshot`，所以帧一旦改形状，这里先红。
//
// 四条必须成立的口径：
// 1. 重连只靠一张 `team_snapshot` 帧就能把运行画出来，后续增量按 seq 接续，不再多打一次快照请求。
// 2. 断号（含服务端剪掉增量后的 `resetRequired` 路径）先亮「同步中」，快照到位后自己收敛。
// 3. 终态不可回退：迟到的活动事件不改已完成的面板。
// 4. 看不懂的帧是空气：既不生成面板，也不打断普通聊天。
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TeamProgressPanel } from "@/components/thread/TeamProgressPanel";
import { useNanobotStream } from "@/hooks/useNanobotStream";
import { useTeamProgress, type TeamFrameClient } from "@/hooks/useTeamProgress";
import * as teamsApi from "@/lib/teams/api";
import type { TeamRunSnapshot, TeamTaskSnapshot } from "@/lib/teams/types";
import { ClientProvider } from "@/providers/ClientProvider";
import type { InboundEvent, UIMessage } from "@/lib/types";

vi.mock("@/lib/teams/api", () => ({
  listTeamRuns: vi.fn(),
  loadTeamRun: vi.fn(),
  loadTeamRunEvents: vi.fn(),
  loadTeamDetail: vi.fn(),
  cancelTeamRun: vi.fn(),
  cancelTeamTask: vi.fn(),
  retryTeamRun: vi.fn(),
}));

const CHAT_ID = "chat-1";
const TURN_ID = "turn-1";

function task(overrides: Partial<TeamTaskSnapshot> = {}): TeamTaskSnapshot {
  return {
    taskId: "task-a",
    memberId: "m-researcher",
    agentId: "researcher",
    title: "查阅资料",
    acceptance: "给出来源",
    dependsOn: [],
    status: "pending",
    attemptId: "att-a",
    lastAction: null,
    startedAt: null,
    finishedAt: null,
    result: null,
    ...overrides,
  };
}

function run(overrides: Partial<TeamRunSnapshot> = {}): TeamRunSnapshot {
  return {
    runId: "run-1",
    chatId: CHAT_ID,
    turnId: TURN_ID,
    binding: { teamId: "content-team", teamRevision: 2, teamName: "内容团队" },
    status: "running",
    lastSeq: 1,
    tasks: [],
    leaderAction: "planning",
    startedAt: "2026-10-16T08:00:00+00:00",
    finishedAt: null,
    parentRunId: null,
    ...overrides,
  };
}

/** `project_team_event` 的形状：信封 + 每种 type 唯一的白名单 data 键。 */
function eventFrame(
  seq: number,
  type: string,
  data: Record<string, unknown>,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    event: "team_event",
    type: "team_event",
    chat_id: CHAT_ID,
    turn_id: TURN_ID,
    data: {
      schemaVersion: 1,
      runId: "run-1",
      chatId: CHAT_ID,
      turnId: TURN_ID,
      seq,
      type,
      taskId: null,
      attemptId: null,
      occurredAt: "2026-10-16T08:00:02+00:00",
      data,
      ...extra,
    },
  };
}

function taskEvent(seq: number, type: string, snapshot: TeamTaskSnapshot): Record<string, unknown> {
  return eventFrame(seq, type, { task: snapshot }, { taskId: snapshot.taskId, attemptId: snapshot.attemptId });
}

function snapshotFrame(snapshot: TeamRunSnapshot): Record<string, unknown> {
  return {
    event: "team_snapshot",
    type: "team_snapshot",
    chat_id: CHAT_ID,
    turn_id: TURN_ID,
    data: snapshot,
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

describe("团队帧整链路", () => {
  it("第一条 run.started 就把面板立起来，不用先补拉快照", async () => {
    const client = new FakeClient();
    const { result } = renderHook(() =>
      useTeamProgress(client, { chatId: CHAT_ID, token: "t", transport: null }),
    );

    await act(async () => {
      client.emit(CHAT_ID, eventFrame(1, "run.started", {
        binding: { teamId: "content-team", teamRevision: 2, teamName: "内容团队" },
        startedAt: "2026-10-16T08:00:00+00:00",
      }));
    });

    // run.started 只带绑定与起点（契约 §6），凭它就能显示团队与 Leader。
    expect(result.current.activeRun?.runId).toBe("run-1");
    expect(result.current.activeRun?.teamName).toBe("内容团队");
    expect(result.current.activeRun?.lastSeq).toBe(1);
    expect(teamsApi.loadTeamRun).not.toHaveBeenCalled();

    render(<TeamProgressPanel run={result.current.activeRun} memberNames={{}} />);
    expect(screen.getByText("内容团队")).toBeInTheDocument();
    // 还没派活、运行也没收口：不能先说「这回合 Leader 自己答了」。
    expect(screen.queryByText("The Leader handled this turn directly.")).toBeNull();
  });

  it("重连只靠一张快照帧开局，后续增量按 seq 接上", async () => {
    const client = new FakeClient();
    const started = task({ status: "running", lastAction: "read_file ok #1", startedAt: "2026-10-16T08:00:01+00:00" });
    vi.mocked(teamsApi.loadTeamRun).mockResolvedValue({
      run: run({ lastSeq: 3, tasks: [started], leaderAction: "waiting" }),
    });

    const { result } = renderHook(() =>
      useTeamProgress(client, { chatId: CHAT_ID, token: "t", transport: null }),
    );
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      client.emit(CHAT_ID, snapshotFrame(run({ lastSeq: 3, tasks: [started], leaderAction: "waiting" })));
    });

    // 快照自带全量：面板已经有内容，并且不需要为它再拉一次运行详情。
    expect(result.current.activeRun?.lastSeq).toBe(3);
    expect(teamsApi.loadTeamRun).not.toHaveBeenCalled();

    await act(async () => {
      client.emit(CHAT_ID, taskEvent(4, "task.activity", { ...started, lastAction: "read_file ok #2" }));
      client.emit(CHAT_ID, eventFrame(5, "leader.updated", { leaderAction: "reviewing" }));
    });

    expect(result.current.activeRun?.lastSeq).toBe(5);
    expect(result.current.activeRun?.leaderAction).toBe("reviewing");
    expect(result.current.activeRun?.tasks["task-a"].lastAction).toBe("read_file ok #2");

    render(
      <TeamProgressPanel
        run={result.current.activeRun}
        memberNames={{ "m-researcher": "研究员" }}
      />,
    );
    expect(screen.getByText("内容团队")).toBeInTheDocument();
    expect(screen.getByText("read_file ok #2")).toBeInTheDocument();
    expect(screen.getByText("Leader · reviewing")).toBeInTheDocument();
  });

  it("收口的 run.finished 带整份快照，之后迟到的活动不改已完成的面板", async () => {
    const client = new FakeClient();
    const running = task({ status: "running", lastAction: "read_file ok #1" });
    const { result } = renderHook(() =>
      useTeamProgress(client, { chatId: CHAT_ID, token: "t", transport: null }),
    );
    await act(async () => {
      client.emit(CHAT_ID, eventFrame(1, "run.started", {
        binding: { teamId: "content-team", teamRevision: 2, teamName: "内容团队" },
        startedAt: "2026-10-16T08:00:00+00:00",
      }));
      client.emit(CHAT_ID, taskEvent(2, "task.started", running));
    });
    expect(result.current.activeRun?.status).toBe("running");
    expect(teamsApi.loadTeamRun).not.toHaveBeenCalled();

    const finished = task({
      status: "succeeded",
      lastAction: "read_file ok #3",
      finishedAt: "2026-10-16T08:01:00+00:00",
      result: {
        taskId: "task-a",
        attemptId: "att-a",
        memberId: "m-researcher",
        agentId: "researcher",
        status: "succeeded",
        stopReason: "completed",
        summary: "三份可引用来源已给出来源",
        artifacts: ["file:notes.md"],
        errorCode: null,
        usage: null,
      },
    });
    const completed = run({
      status: "completed",
      lastSeq: 4,
      tasks: [finished],
      leaderAction: "summarizing",
      finishedAt: "2026-10-16T08:01:01+00:00",
    });

    await act(async () => {
      client.emit(CHAT_ID, eventFrame(4, "run.finished", { run: completed }));
      // 传输层乱序：完成之后才到的活动帧。
      client.emit(CHAT_ID, taskEvent(5, "task.activity", { ...running, lastAction: "read_file ok #4" }));
    });

    expect(result.current.activeRun?.status).toBe("completed");
    expect(result.current.activeRun?.tasks["task-a"].lastAction).toBe("read_file ok #3");

    render(
      <TeamProgressPanel run={result.current.activeRun} memberNames={{}} />,
    );
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("read_file ok #3")).toBeInTheDocument();
    expect(screen.getByText("三份可引用来源已给出来源")).toBeInTheDocument();
    expect(screen.getByText("Artifacts: file:notes.md")).toBeInTheDocument();
    expect(screen.queryByText("read_file ok #4")).toBeNull();
  });

  it("断号时先要补齐，能按 seq 接上就接（契约 §6 的补拉协议）", async () => {
    const running = task({ status: "running", lastAction: "read_file ok #1" });
    vi.mocked(teamsApi.listTeamRuns).mockResolvedValue({
      runs: [run({ lastSeq: 3, tasks: [running] })],
      nextCursor: null,
    });
    // 补拉期间面板必须停在「要补齐」，不能拿缺口里的半条事件凑合。
    let deliverPage: ((value: { events: unknown[]; lastSeq: number; hasMore: boolean; resetRequired: boolean }) => void) | null = null;
    vi.mocked(teamsApi.loadTeamRunEvents).mockReturnValue(
      new Promise((resolve) => {
        deliverPage = resolve;
      }),
    );

    const client = new FakeClient();
    const { result } = renderHook(() =>
      useTeamProgress(client, { chatId: CHAT_ID, token: "t", transport: null }),
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.activeRun?.lastSeq).toBe(3);

    await act(async () => {
      client.emit(CHAT_ID, taskEvent(6, "task.activity", running));
    });
    expect(result.current.activeRun?.needsReplay).toBe(true);
    expect(result.current.activeRun?.tasks["task-a"].lastAction).toBe("read_file ok #1");
    // 缺口要带上「已经到哪」，否则服务端会把整段历史重发一遍。
    expect(teamsApi.loadTeamRunEvents).toHaveBeenCalledWith("t", "run-1", { afterSeq: 3 });
    expect(teamsApi.loadTeamRun).not.toHaveBeenCalled();

    const healed = task({ status: "running", lastAction: "web_search ok #2" });
    await act(async () => {
      deliverPage?.({
        events: [
          {
            schemaVersion: 1,
            runId: "run-1",
            chatId: CHAT_ID,
            turnId: TURN_ID,
            seq: 4,
            type: "task.activity",
            taskId: "task-a",
            attemptId: "att-a",
            occurredAt: "2026-10-16T08:00:03+00:00",
            data: { task: { ...healed, lastAction: "read_file ok #3" } },
          },
          {
            schemaVersion: 1,
            runId: "run-1",
            chatId: CHAT_ID,
            turnId: TURN_ID,
            seq: 5,
            type: "task.activity",
            taskId: "task-a",
            attemptId: "att-a",
            occurredAt: "2026-10-16T08:00:04+00:00",
            data: { task: healed },
          },
        ],
        lastSeq: 5,
        hasMore: false,
        resetRequired: false,
      });
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.activeRun?.needsReplay).toBe(false));
    expect(result.current.activeRun?.lastSeq).toBe(5);
    expect(result.current.activeRun?.tasks["task-a"].lastAction).toBe("web_search ok #2");
    // 能接上就不该多打一次快照请求。
    expect(teamsApi.loadTeamRun).not.toHaveBeenCalled();
  });

  it("增量被剪掉（resetRequired）时整份回快照", async () => {
    const running = task({ status: "running", lastAction: "read_file ok #1" });
    vi.mocked(teamsApi.listTeamRuns).mockResolvedValue({
      runs: [run({ lastSeq: 3, tasks: [running] })],
      nextCursor: null,
    });
    vi.mocked(teamsApi.loadTeamRunEvents).mockResolvedValue({
      events: [],
      lastSeq: 0,
      hasMore: false,
      resetRequired: true,
    });
    const healed = task({ status: "succeeded", lastAction: "web_search ok #2" });
    vi.mocked(teamsApi.loadTeamRun).mockResolvedValue({
      run: run({ lastSeq: 6, tasks: [healed] }),
    });

    const client = new FakeClient();
    const { result } = renderHook(() =>
      useTeamProgress(client, { chatId: CHAT_ID, token: "t", transport: null }),
    );
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      client.emit(CHAT_ID, taskEvent(6, "task.activity", running));
    });

    await waitFor(() => expect(result.current.activeRun?.lastSeq).toBe(6));
    expect(result.current.activeRun?.needsReplay).toBe(false);
    expect(result.current.activeRun?.tasks["task-a"].lastAction).toBe("web_search ok #2");
    expect(teamsApi.loadTeamRun).toHaveBeenCalledWith("t", "run-1");
  });

  it("首个增量先于快照到达时，只知道 runId 就去补拉", async () => {
    const running = task({ status: "queued", lastAction: null });
    vi.mocked(teamsApi.loadTeamRunEvents).mockResolvedValue({
      events: [],
      lastSeq: 0,
      hasMore: false,
      resetRequired: true,
    });
    vi.mocked(teamsApi.loadTeamRun).mockResolvedValue({
      run: run({ lastSeq: 2, tasks: [running], leaderAction: "waiting" }),
    });
    const client = new FakeClient();
    const { result } = renderHook(() =>
      useTeamProgress(client, { chatId: CHAT_ID, token: "t", transport: null }),
    );
    await act(async () => {
      client.emit(CHAT_ID, taskEvent(2, "task.queued", running));
    });

    await waitFor(() => expect(result.current.activeRun?.runId).toBe("run-1"));
    expect(result.current.activeRun?.tasks["task-a"].status).toBe("queued");
    expect(teamsApi.loadTeamRunEvents).toHaveBeenCalledWith("t", "run-1", {});
  });

  it("旧快照不会把面板拽回去", async () => {
    const client = new FakeClient();
    const { result } = renderHook(() =>
      useTeamProgress(client, { chatId: CHAT_ID, token: "t", transport: null }),
    );
    await act(async () => {
      client.emit(CHAT_ID, snapshotFrame(run({ lastSeq: 9, leaderAction: "summarizing" })));
      client.emit(CHAT_ID, snapshotFrame(run({ lastSeq: 4, leaderAction: "planning" })));
    });
    expect(result.current.activeRun?.lastSeq).toBe(9);
    expect(result.current.activeRun?.leaderAction).toBe("summarizing");
  });
});

describe("看不懂的帧是空气", () => {
  it("未知 type、坏 data、非团队帧都不会变成团队状态，也不触发补拉", async () => {
    const client = new FakeClient();
    const { result } = renderHook(() =>
      useTeamProgress(client, { chatId: CHAT_ID, token: "t", transport: null }),
    );
    await act(async () => {
      client.emit(CHAT_ID, eventFrame(2, "task.melted", { task: task() }));
      client.emit(CHAT_ID, { event: "team_event", chat_id: CHAT_ID, turn_id: TURN_ID, data: { schemaVersion: 2, seq: 3 } });
      client.emit(CHAT_ID, snapshotFrame({ nope: true }));
      client.emit(CHAT_ID, { event: "message", chat_id: CHAT_ID, text: "hi" });
    });

    expect(result.current.runs).toEqual([]);
    expect(result.current.activeRun).toBeNull();
    expect(teamsApi.loadTeamRun).not.toHaveBeenCalled();

    render(<TeamProgressPanel run={result.current.activeRun} memberNames={{}} />);
    expect(screen.queryByLabelText("Team progress")).toBeNull();
  });

  it("普通聊天与团队面板共用一个订阅：团队帧不进消息流", async () => {
    const handlers = new Map<string, Set<(event: unknown) => void>>();
    const noopUnsubscribe = () => () => undefined;
    const client = {
      onStatus: (handler: (status: string) => void) => {
        handler("open");
        return noopUnsubscribe();
      },
      onRunStatus: noopUnsubscribe,
      onError: noopUnsubscribe,
      onTerminalEvent: noopUnsubscribe,
      getRunStartedAt: () => null,
      getRunTurnId: () => null,
      getGoalState: () => undefined,
      hasUnsettledRun: () => false,
      onChat(chatId: string, handler: (event: unknown) => void) {
        const set = handlers.get(chatId) ?? new Set();
        set.add(handler);
        handlers.set(chatId, set);
        return () => set.delete(handler);
      },
    };
    const seen: UIMessage[] = [];

    function Harness() {
      const stream = useNanobotStream(CHAT_ID);
      const team = useTeamProgress(client, { chatId: CHAT_ID, token: "t", transport: null });
      seen.length = 0;
      seen.push(...stream.messages);
      return (
        <TeamProgressPanel run={team.activeRun} memberNames={team.memberNames} canControl={false} />
      );
    }

    render(
      <ClientProvider client={client as never} token="tok">
        <Harness />
      </ClientProvider>,
    );

    // 两条坏掉的团队帧与一条普通回答共用这一个订阅。
    await act(async () => {
      handlers.get(CHAT_ID)?.forEach((handler) =>
        handler({ event: "team_event", chat_id: CHAT_ID, turn_id: TURN_ID, data: { nonsense: true } }),
      );
      handlers.get(CHAT_ID)?.forEach((handler) =>
        handler({ event: "team_snapshot", chat_id: CHAT_ID, turn_id: TURN_ID, data: { nonsense: true } }),
      );
      handlers.get(CHAT_ID)?.forEach((handler) =>
        handler({
          event: "delta",
          chat_id: CHAT_ID,
          turn_id: TURN_ID,
          turn_phase: "answer",
          turn_seq: 1,
          text: "普通回答",
        } as InboundEvent),
      );
      await Promise.resolve();
    });

    expect(seen.some((message) => message.content === "普通回答")).toBe(true);
    expect(screen.queryByLabelText("Team progress")).toBeNull();
    expect(teamsApi.loadTeamRun).not.toHaveBeenCalled();
  });
});
