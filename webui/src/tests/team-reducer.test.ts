// 团队运行的客户端 reducer（契约 §6 的恢复协议）。
//
// 钉住三件事：只按 seq 顺序应用、缺口要显式要补拉、终态永不回退。
import { describe, expect, it } from "vitest";

import type { TeamEvent, TeamEventWithRun, TeamTaskEvent } from "@/lib/teams/events";
import {
  applyTeamEvent,
  applyTeamSnapshot,
  hasActiveTasks,
  teamRunStateFromSnapshot,
  teamTaskRows,
} from "@/lib/teams/reducer";
import type { TeamRunSnapshot, TeamTaskSnapshot } from "@/lib/teams/types";

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
    lastAction: "读取文档",
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
    binding: { teamId: "content-team", teamRevision: 1, teamName: "内容团队" },
    status: "running",
    lastSeq: 1,
    tasks: [],
    leaderAction: "waiting",
    startedAt: "2026-10-15T08:00:00+00:00",
    finishedAt: null,
    parentRunId: null,
    ...overrides,
  };
}

function taskEvent(seq: number, task: TeamTaskSnapshot, type = "task.activity"): TeamTaskEvent {
  return {
    schemaVersion: 1,
    runId: "run-1",
    chatId: "chat-1",
    turnId: "turn-1",
    seq,
    type: type as TeamTaskEvent["type"],
    taskId: task.taskId,
    attemptId: task.attemptId,
    occurredAt: "2026-10-15T08:00:00+00:00",
    task,
  };
}

function finishedEvent(seq: number, status = "completed"): TeamEventWithRun {
  return {
    schemaVersion: 1,
    runId: "run-1",
    chatId: "chat-1",
    turnId: "turn-1",
    seq,
    type: "run.finished",
    taskId: null,
    attemptId: null,
    occurredAt: "2026-10-15T08:05:00+00:00",
    run: run({
      status: status as TeamRunSnapshot["status"],
      lastSeq: seq,
      finishedAt: "2026-10-15T08:05:00+00:00",
      tasks: [task({ status: "succeeded" })],
    }),
  };
}

describe("applyTeamEvent", () => {
  it("顺序事件按 seq 推进并归并到同一张任务卡", () => {
    const state = teamRunStateFromSnapshot(run({ tasks: [task()] }));
    const next = applyTeamEvent(state, taskEvent(2, task({ lastAction: "整理引用" })));
    expect(next?.lastSeq).toBe(2);
    expect(next?.tasks["task-1"].lastAction).toBe("整理引用");
    expect(next?.taskOrder).toEqual(["task-1"]);
  });

  it("终态不会被重复或旧事件回退", () => {
    const done = applyTeamEvent(teamRunStateFromSnapshot(run()), finishedEvent(7));
    const next = applyTeamEvent(done, finishedEvent(6));
    expect(next).toEqual(done);

    const late = applyTeamEvent(done, taskEvent(8, task({ status: "running" })));
    expect(late?.status).toBe("completed");
    expect(late?.lastSeq).toBe(8);
  });

  it("已终态的任务不再被迟到的活动改写", () => {
    const state = teamRunStateFromSnapshot(
      run({ tasks: [task({ status: "succeeded", lastAction: "完成" })] }),
    );
    const next = applyTeamEvent(state, taskEvent(2, task({ status: "running", lastAction: "还在跑" })));
    expect(next?.tasks["task-1"].lastAction).toBe("完成");
  });

  it("重放与重复事件被忽略，引用不变", () => {
    const state = applyTeamEvent(
      teamRunStateFromSnapshot(run({ tasks: [task()] })),
      taskEvent(2, task({ lastAction: "整理引用" })),
    );
    expect(applyTeamEvent(state, taskEvent(2, task({ lastAction: "别的" })))).toBe(state);
    expect(applyTeamEvent(state, taskEvent(1, task()))).toBe(state);
  });

  it("seq 断号时不猜测，标记需要补拉", () => {
    const state = teamRunStateFromSnapshot(run({ tasks: [task()] }));
    const next = applyTeamEvent(state, taskEvent(5, task()));
    expect(next?.needsReplay).toBe(true);
    expect(next?.tasks["task-1"].lastAction).toBe("读取文档");
  });

  it("同一 Agent 的不同运行不会串卡", () => {
    const state = teamRunStateFromSnapshot(run());
    const foreign: TeamTaskEvent = { ...taskEvent(2, task()), runId: "run-2" };
    expect(applyTeamEvent(state, foreign)).toBe(state);
  });

  it("重试开出的新运行是独立卡片，不复用上一轮的终态", () => {
    const origin = teamRunStateFromSnapshot(
      run({ runId: "run-1", tasks: [task({ status: "failed", attemptId: "att-1" })] }),
    );
    // 同一条运行里终态任务不会被重新拉起：服务端把重试做成新运行。
    const reopened = applyTeamEvent(
      origin,
      taskEvent(2, task({ status: "running", attemptId: "att-2" }), "task.started"),
    );
    expect(reopened?.tasks["task-1"].attemptId).toBe("att-1");

    const retried = applyTeamSnapshot(null, run({ runId: "run-2", tasks: [task({ status: "running" })] }));
    expect(retried.runId).toBe("run-2");
    expect(retried.tasks["task-1"].status).toBe("running");
  });

  it("没有运行状态时不凭空造一条", () => {
    expect(applyTeamEvent(null, taskEvent(1, task()))).toBeNull();
  });
});

describe("applyTeamSnapshot", () => {
  it("快照覆盖增量并清掉补拉标记", () => {
    const stale = { ...teamRunStateFromSnapshot(run({ lastSeq: 3 })), needsReplay: true };
    const next = applyTeamSnapshot(stale, run({ lastSeq: 9, tasks: [task({ status: "succeeded" })] }));
    expect(next.needsReplay).toBe(false);
    expect(next.lastSeq).toBe(9);
    expect(next.tasks["task-1"].status).toBe("succeeded");
  });

  it("更旧的快照不会把面板拽回去", () => {
    const fresh = teamRunStateFromSnapshot(run({ lastSeq: 9, status: "completed" }));
    const next = applyTeamSnapshot(fresh, run({ lastSeq: 4 }));
    expect(next).toBe(fresh);
  });

  it("未知运行的快照直接成为新状态", () => {
    const next = applyTeamSnapshot(null, run());
    expect(next.runId).toBe("run-1");
    expect(next.teamName).toBe("内容团队");
  });
});

describe("视图辅助", () => {
  it("没有任务时报告没有在跑的任务", () => {
    const state = teamRunStateFromSnapshot(run({ tasks: [task({ status: "succeeded" })] }));
    expect(hasActiveTasks(state)).toBe(false);
    expect(teamTaskRows(state, { "m-researcher": "研究员" })).toEqual([
      {
        task: expect.objectContaining({ taskId: "task-1" }),
        memberName: "研究员",
        agentId: "researcher",
      },
    ]);
  });

  it("未知成员退回展示原始 memberId 而不是空白", () => {
    const rows = teamTaskRows(teamRunStateFromSnapshot(run({ tasks: [task()] })));
    expect(rows[0].memberName).toBeNull();
  });

  it("run.cancelling 让状态停在 cancelling 而不假装已停", () => {
    const state = teamRunStateFromSnapshot(run());
    const cancelling: TeamEvent = {
      schemaVersion: 1,
      runId: "run-1",
      chatId: "chat-1",
      turnId: "turn-1",
      seq: 2,
      type: "run.cancelling",
      taskId: null,
      attemptId: null,
      occurredAt: "2026-10-15T08:00:00+00:00",
      status: "cancelling",
    };
    expect(applyTeamEvent(state, cancelling)?.status).toBe("cancelling");
  });
});
