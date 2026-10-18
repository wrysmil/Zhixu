---
artifact: contract
route: api-and-interface-design
skills: [api-and-interface-design]
skills_evidence: [harness-kit/.agents/skills/api-and-interface-design/SKILL.md]
source:
  - .ai-runtime-artifacts/specs/2026-10-18-subagent-observability-spec.md
  - nanobot/webui/teams_routes.py
  - nanobot/teams/events.py
created_at: 2026-10-18
status: draft
approved: false
---

# 普通子 Agent 执行观察契约（拟新增，尚未实现）

## 1. 身份与快照

生产数据路径由服务端 workspace 决定；客户端不能传 dbPath 或任意 sessionKey。所有查询沿用现有认证和 workspace/session 访问检查。JSON 领域字段 camelCase，WS 外层 chat_id/turn_id 沿用项目命名。

```typescript
type ExecutionStatus = "queued" | "running" | "succeeded" | "failed" |
  "cancelled" | "interrupted";
type ExecutionPhase = "preparing" | "model" | "tools" | "finalizing";
interface ExecutionSnapshot {
  invocationId: string;           // 完整 UUID，每次派发唯一
  chatId: string;
  parentTurnId: string;           // 可见 WebUI turn；不等于 loop 内部 turn
  parentToolCallId: string;
  agentId: string | null;         // 未指定档案为 null
  agentName: string;              // 派发时的展示名快照
  title: string;                  // ≤120 字，经过公开投影
  instructionSummary: string;     // ≤2000 字，不能直接复制含私密上下文的 task
  mode: "background" | "wait";
  status: ExecutionStatus;
  phase: ExecutionPhase;
  createdAt: string;              // UTC ISO 8601
  startedAt: string | null;
  finishedAt: string | null;
  queueDurationMs: number | null;
  durationMs: number | null;      // 终态由服务端 monotonic 计算，前端不改
  lastAction: string | null;      // ≤240 字
  completedToolCount: number;
  lastSeq: number;
  result: null | {
    summary: string;              // ≤8000 字，公开最终回复，非原始执行转录
    truncated: boolean;
    stopReason: string;
    errorCode: string | null;
    errorMessage: string | null;  // 公开错误说明，不含堆栈和凭据
  };
}
interface ExecutionActivity {
  activityId: string;             // 每个工具调用唯一；开始和结束共享
  kind: "tool" | "progress";
  toolName: string | null;
  status: "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
  label: string;                 // 白名单工具映射文案或公开说明，≤240 字
  startedAt: string;
  finishedAt: string | null;
}
```

status 控制终态，phase 只描述运行阶段；queued 阶段仍可保留 preparing。终态不能被旧快照或活动事件回退。父回合恢复调用时不能因 toolCallId 重复展示同一 invocation；相同工具调用的执行幂等只沿用现有 Runner 恢复边界，不在 UI 层重跑工具。

## 2. 事件

新增能力标志 `subagentExecutionEventsV1`。WS 帧 event/type=`subagent_execution_event`，外层 chat_id/turn_id；data 为下列信封。新连接在 chat hydrate 时发 `subagent_execution_snapshot`，data 是列表查询结果的同形快照。

```typescript
interface ExecutionEvent {
  schemaVersion: 1;
  invocationId: string;
  chatId: string;
  parentTurnId: string;
  parentToolCallId: string;
  seq: number;                   // 每 invocation 从 1 严格递增
  occurredAt: string;
  type: "execution.created" | "execution.started" | "execution.phase_changed" |
    "execution.activity" | "execution.finished";
  data: {
    execution: Omit<ExecutionSnapshot, "instructionSummary" | "result">;
                                                // 对应 seq 的公开状态
    activity?: ExecutionActivity;               // 仅 activity 事件
  };
}
```

活动更新同一 activityId 的行，不能把工具开始和结束渲染成两次工具调用。任务说明、结果不塞进每条事件，打开详情／finished 后可补读 detail；列表/详情快照携带 result。agentName≤80字、toolName≤120字，活动事件data序列化后≤4KiB，超出时先缩短可选展示文案，保留身份、状态、seq；公开说明合并更新最多每调用每秒4次。工具开始、工具终态和 execution.finished 不被节流删除，不逐 token 产生事件。

每条快照的 lastSeq 与同事务事件一致。推送仅是提示，数据库是长期调用真值；网络投递失败不会重跑任务。存储失败通过结构化可观察性降级通知给当前会话，不按字符串猜测。

## 3. 只读 HTTP（拟新增）

| 路由 | 参数 | 成功响应 |
| --- | --- | --- |
| `GET /api/subagent-executions` | chatId 必填，parentTurnId 可选，limit 默认20/最大100，cursor 可选 | `{executions, nextCursor, serverNow}` |
| `GET /api/subagent-executions/{invocationId}` | 无 | `{execution, serverNow}` |
| `GET /api/subagent-executions/{invocationId}/events` | afterSeq 默认0，throughSeq 可选，limit 默认100/最大200 | `{events, lastSeq, hasMore, resetRequired}` |

列表包含所有非终态任务及分页终态历史：首屏活动项不受20条历史限制；上限仍遵守服务端现有运行容量。cursor 只分页终态历史，按 (createdAt, invocationId) 降序；不可因较早启动而隐藏仍活动的任务。

detail 的 seq=S 是状态边界；详情首次打开时，从0分页读取 events 至 throughSeq=S 来恢复时间线，之后合并缓存的 seq>S 实时事件。只取一张快照不代表已经恢复了时间线。通过 activityId 合并、按首次 startedAt 和 ID 稳定排序。

错误沿用现有 `{error: string}`，不改全站信封：401 未认证，403 无作用域访问权，404 该作用域内不可见／不存在，400 非法参数，503 存储或观察服务不可用。固定消息前缀 `EXECUTION_NOT_FOUND`、`EXECUTION_STORAGE_UNAVAILABLE`、`EXECUTION_WRITER_BUSY`。未知/过期 cursor 返回400；不泄露其他 workspace 是否存在同名 ID。无库列表返回空，不建库。

不新增取消／重试 mutation；“重新加载”仅调用这些 GET。观察服务不可用时返回降级状态，不将其翻译成任务执行失败。

## 4. 恢复和竞态

1. 按 chat 订阅实时事件，再获取列表；缓存列表返回前收到的事件。快照与缓存按 invocationId/seq 合并，旧快照不得覆盖新终态。
2. 正常连续事件直接应用；重复或旧 seq 忽略。断号时标记 syncing，先缓存后续事件，按最后连续 seq 补拉；同 invocation 只允许一条补拉链。
3. detail 返回的 lastSeq 较新时，状态可按快照更新，仍按上一条连续 seq 补齐时间线；状态 revision 与 timeline cursor 分开记录。
4. 终态快照保留30天、终态事件保留7天；活动调用不清理。resetRequired=true 时取最新 detail，展示“部分较早活动已过期”；保留已有可信时间线，清理冲突尾段，再接续最新快照后的事件，不无限请求过期页。
5. chat/workspace 切换时取消旧请求、解除订阅，generation guard 拒绝晚到响应。重新连接补快照，不触发新 spawn。
6. 服务端 writer 取得独占权后才做重启恢复：旧非终态记录变 interrupted；所有未结束工具活动同样收口；已完成任务不动。临时聊天只做内存恢复，不落此库。
7. 详情打开时不修改原任务，关闭不发送取消。终态事件到达后所有仍 running 的工具活动按该任务的取消／中断原因收口；任务成功时不凭空修复缺失的工具结束事件，而触发补拉。

## 5. 内容与兼容

不传系统提示词、私有推理、原始工具参数/输出。phase 与工具状态由 runtime 事件确定，不能从 assistant 文本识别。Agent 无工具的任务仍展示 model 阶段与最终结果。

旧 gateway 无能力标志：不订阅/补拉新接口，保持原工具显示。旧历史无关联：不可点击伪详情。新前端和旧后端仍能正常聊天。schemaVersion 未知帧安全忽略，必要时重新读受支持快照，不能打断主流。
