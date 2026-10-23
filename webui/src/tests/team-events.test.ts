// 团队事件解析（契约 §6）。
//
// 跨语言锚点是 `tests/fixtures/teams/event.json`，与 Python 侧 `EVENT_DATA_FIELDS`
// 同一份数据：`describe("共享 fixture")` 那一组逐条吃这份文件，解析器看不懂就返回 null，
// 绝不半懂半猜。前面几组手写常量是补充，不替代这份锚点。
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  isTerminalRunStatus,
  isTerminalTaskStatus,
  parseRunSnapshot,
  parseTaskSnapshot,
  parseTeamEvent,
  parseTeamFrame,
} from "@/lib/teams/events";

/** 契约 §6 点名的同一份 fixture：Python 侧也读它，两端不许各写各的形状。 */
const SHARED_FIXTURE = JSON.parse(
  readFileSync(resolve(process.cwd(), "..", "tests/fixtures/teams/event.json"), "utf8"),
) as Array<Record<string, unknown>>;

const RUN = {
  runId: "run-0001",
  chatId: "chat-0001",
  turnId: "turn-0001",
  binding: { teamId: "content-team", teamRevision: 1, teamName: "内容团队" },
  status: "running",
  lastSeq: 3,
  tasks: [
    {
      taskId: "task-0001",
      memberId: "m-researcher",
      agentId: "researcher",
      title: "查阅资料",
      acceptance: "给出来源",
      dependsOn: [],
      status: "running",
      attemptId: "att-0001",
      lastAction: "读取文档",
      startedAt: "2026-10-15T08:00:01+00:00",
      finishedAt: null,
      result: null,
    },
  ],
  leaderAction: "waiting",
  startedAt: "2026-10-15T08:00:00+00:00",
  finishedAt: null,
  parentRunId: null,
};

const TASK = RUN.tasks[0];

function envelope(overrides: Record<string, unknown> = {}) {
  return {
    schemaVersion: 1,
    runId: "run-0001",
    chatId: "chat-0001",
    turnId: "turn-0001",
    seq: 2,
    type: "task.started",
    taskId: TASK.taskId,
    attemptId: TASK.attemptId,
    occurredAt: "2026-10-15T08:00:01+00:00",
    ...overrides,
  };
}

describe("parseTeamEvent", () => {
  it("解析 task.* 事件里的完整任务快照", () => {
    const parsed = parseTeamEvent(envelope({ data: { task: TASK } }));
    expect(parsed).not.toBeNull();
    expect(parsed?.type).toBe("task.started");
    expect(parsed && "task" in parsed && parsed.task.title).toBe("查阅资料");
  });

  it("解析 run.finished 里的整份运行快照", () => {
    const finished = { ...RUN, status: "completed", lastSeq: 9, finishedAt: "2026-10-15T08:05:00+00:00" };
    const parsed = parseTeamEvent(
      envelope({ type: "run.finished", seq: 9, data: { run: finished } }),
    );
    expect(parsed?.type).toBe("run.finished");
    expect(parsed && "run" in parsed && parsed.run.status).toBe("completed");
  });

  it("解析 leader.updated 与 run.cancelling 的标量载荷", () => {
    const leader = parseTeamEvent(
      envelope({ type: "leader.updated", data: { leaderAction: "summarizing" } }),
    );
    expect(leader && "leaderAction" in leader && leader.leaderAction).toBe("summarizing");

    const cancelling = parseTeamEvent(envelope({ type: "run.cancelling", data: { status: "cancelling" } }));
    expect(cancelling?.type).toBe("run.cancelling");
  });

  it("未知 schema 版本与未知 type 一律 null", () => {
    expect(parseTeamEvent(envelope({ schemaVersion: 2, data: { task: TASK } }))).toBeNull();
    expect(parseTeamEvent(envelope({ type: "task.exploded", data: { task: TASK } }))).toBeNull();
  });

  it("缺关键字段的快照不解析成半个对象", () => {
    expect(parseRunSnapshot({ ...RUN, runId: undefined })).toBeNull();
    expect(parseRunSnapshot({ ...RUN, status: "weird" })).toBeNull();
    expect(parseRunSnapshot({ ...RUN, binding: { teamId: "t" } })).toBeNull();
    expect(parseTaskSnapshot({ ...TASK, status: "unknown" })).toBeNull();
    expect(parseRunSnapshot(null)).toBeNull();
    expect(parseTaskSnapshot("nope")).toBeNull();
  });

  it("结果里缺失的用量显示为未知而不是 0", () => {
    const parsed = parseTaskSnapshot({
      ...TASK,
      status: "succeeded",
      result: {
        taskId: TASK.taskId,
        attemptId: "att-1",
        memberId: TASK.memberId,
        agentId: TASK.agentId,
        status: "succeeded",
        stopReason: "completed",
        summary: "结论",
        artifacts: [],
        errorCode: null,
        usage: null,
      },
    });
    expect(parsed?.result?.usage).toBeNull();
  });
});

describe("共享 fixture（与 Python 侧同一份）", () => {
  it("逐条事件都能解析，没有一条被静默丢掉", () => {
    const unparsed = SHARED_FIXTURE.map(
      (raw) => `${String(raw.type)}#${String(raw.seq)}`,
    ).filter((_, index) => parseTeamEvent(SHARED_FIXTURE[index]) === null);
    expect(unparsed).toEqual([]);
  });

  it("run.started 带的是绑定与起点，不是整份运行快照", () => {
    const parsed = parseTeamEvent(SHARED_FIXTURE[0]);
    expect(parsed?.type).toBe("run.started");
    expect(parsed && "binding" in parsed && parsed.binding.teamId).toBe("content-team");
    expect(parsed && "binding" in parsed && parsed.binding.teamRevision).toBe(1);
    // 拿 run.started 当 run.finished 解析必须失败：判别形状不能混。
    expect(SHARED_FIXTURE[0].data).not.toHaveProperty("run");
  });

  it("run.finished 带整份运行快照", () => {
    const finished = SHARED_FIXTURE.find((raw) => raw.type === "run.finished");
    expect(finished).toBeDefined();
    const parsed = parseTeamEvent(finished!);
    expect(parsed?.type).toBe("run.finished");
    expect(parsed && "run" in parsed && parsed.run.status).toBeTruthy();
  });

  it("每条运行的 seq 从 1 起严格连续，前端可以直接按序归并", () => {
    // seq 是**按运行**单调的：fixture 里有第二条运行，seq 会重新从 1 开始。
    const byRun = new Map<string, unknown[]>();
    for (const raw of SHARED_FIXTURE) {
      const runId = String(raw.runId);
      byRun.set(runId, [...(byRun.get(runId) ?? []), raw.seq]);
    }
    expect(byRun.size).toBeGreaterThan(1);
    for (const seqs of byRun.values()) {
      expect(seqs).toEqual(seqs.map((_, index) => index + 1));
    }
  });
});

describe("parseTeamFrame", () => {
  it("包住 team_event 与 team_snapshot 两种帧", () => {
    const event = parseTeamFrame({
      event: "team_event",
      chat_id: "chat-0001",
      turn_id: "turn-0001",
      data: envelope({ data: { task: TASK } }),
    });
    expect(event?.kind).toBe("event");
    expect(event?.chatId).toBe("chat-0001");

    const snapshot = parseTeamFrame({
      event: "team_snapshot",
      chat_id: "chat-0001",
      turn_id: "turn-0001",
      data: RUN,
    });
    expect(snapshot?.kind).toBe("snapshot");
    expect(snapshot && snapshot.kind === "snapshot" && snapshot.run.lastSeq).toBe(3);
  });

  it("普通聊天帧与坏帧都不会被当成团队状态", () => {
    expect(parseTeamFrame({ event: "message", chat_id: "c", text: "hi" })).toBeNull();
    expect(parseTeamFrame({ event: "team_event" })).toBeNull();
    expect(parseTeamFrame({ event: "team_event", chat_id: "c", data: 42 })).toBeNull();
    expect(parseTeamFrame(null)).toBeNull();
    expect(parseTeamFrame("team_event")).toBeNull();
  });
});

describe("终态判定", () => {
  it("终态集合与契约一致", () => {
    expect(isTerminalRunStatus("completed")).toBe(true);
    expect(isTerminalRunStatus("interrupted")).toBe(true);
    expect(isTerminalRunStatus("cancelling")).toBe(false);
    expect(isTerminalTaskStatus("blocked")).toBe(true);
    expect(isTerminalTaskStatus("queued")).toBe(false);
  });
});
