---
artifact: contract
route: api-and-interface-design
skills: [api-and-interface-design]
skills_evidence: [.claude/skills/api-and-interface-design/SKILL.md]
source:
  - .ai-runtime-artifacts/research/2026-10-15-agent-team-research.md
  - .ai-runtime-artifacts/contracts/2026-09-29-agents-contract.md
  - nanobot/webui/settings_contracts.py
  - nanobot/agent/runner.py
created_at: 2026-10-15
status: frozen
approved: true
frozen_for: Agent Team 实施（G0 起 WU 的 Python/TS 共同依据；边界签名变更由 Leader 同步两边）
---

# Agent Team 接口契约草案

本契约为计划的实施输入；确认后再冻结。以下均是新增设计，不代表已存在 API。Python 使用 snake_case；Team 领域 JSON 使用 camelCase；既有 WS 外层命令的 `chat_id/turn_id/workspace_scope` 保持原风格。

## 1. 配置、绑定与运行对象

```typescript
type TeamRunStatus = "running" | "cancelling" | "completed" |
  "partial" | "failed" | "cancelled" | "interrupted";
type TaskStatus = "pending" | "queued" | "running" | "cancelling" | "succeeded" |
  "failed" | "cancelled" | "blocked" | "interrupted";

interface TeamMember {
  memberId: string;             // 同一团队内稳定；不同于 Agent 档案 ID
  agentId: string;
  role: string;                 // 本团队职责，最多 120 字
}
interface TeamLimits {
  maxConcurrency: number;       // 默认 3，范围 1..8
  maxTasks: number;             // 默认 16，范围 1..64
  maxDispatchRounds: number;    // 默认 4，范围 1..8
  taskTimeoutSeconds: number;   // 默认 600，范围 30..1800
  runTimeoutSeconds: number;    // 默认 1800，范围 60..7200
}
interface TeamDefinition {
  id: string;
  revision: number;             // 服务端递增，从 1 开始
  name: string;                 // 1..80 字；允许同名，以 ID 区分
  description: string;
  instructions: string;         // 团队目标/分工，最多 8000 字
  leaderAgentId: string;
  members: TeamMember[];        // 1..8 个；档案不能重复或等于 Leader
  limits: TeamLimits;
  archived: boolean;
  createdAt: string;            // UTC ISO 8601
  updatedAt: string;
}
interface TeamBinding {
  teamId: string;
  teamRevision: number;
  teamName: string;
}
interface AgentProfileSnapshot {
  id: string;
  updatedAt: string;
  prompt: string;
  modelId: string | null;       // 已配置的模型 preset 名，非裸模型 ID
  tools: { mode: "all" | "include" | "exclude"; entries: string[] };
  skills: { mode: "all" | "include" | "exclude"; entries: string[] };
  subAgents: { mode: "all" | "include" | "exclude"; entries: string[] };
  name: string;
}
interface TeamRevisionSnapshot {
  definition: TeamDefinition;
  leader: AgentProfileSnapshot;
  members: Array<{ member: TeamMember; profile: AgentProfileSnapshot }>;
}
interface TeamTaskInput {
  key: string;                 // 本批次唯一短标识，用于声明依赖
  memberId: string;
  title: string;               // 1..120 字
  instruction: string;         // 1..32000 字
  acceptance: string;          // 1..2000 字，Leader 验收条件
  dependsOn: string[];         // 仅引用本批次 key，运行时检查 DAG
  resourceKeys: string[];      // 可选串行资源，如 file:webui/src/App.tsx
}
interface TeamTaskResult {
  taskId: string;
  attemptId: string;
  memberId: string;
  agentId: string;
  status: TaskStatus;
  stopReason: string;
  summary: string;             // 最多 8000 字；完整结果另存，上限 64000 字
  artifacts: string[];         // 仅允许已通过工作区路径校验的相对引用
  errorCode: string | null;
  usage: { inputTokens: number; outputTokens: number } | null;
}
interface TeamTaskSnapshot {
  taskId: string;
  memberId: string;
  agentId: string;
  title: string;
  acceptance: string;
  dependsOn: string[];         // 持久化后的 taskId，不再是批次短 key
  status: TaskStatus;
  attemptId: string | null;
  lastAction: string | null;   // 经脱敏的公开活动摘要，最多 240 字
  startedAt: string | null;
  finishedAt: string | null;
  result: TeamTaskResult | null;
}
interface TeamRunSnapshot {
  runId: string;
  chatId: string;
  turnId: string;              // WebUI 可见轮次 ID；内部 ctx.turn_id 单独存
  binding: TeamBinding;
  status: TeamRunStatus;
  lastSeq: number;
  tasks: TeamTaskSnapshot[];
  leaderAction: "planning" | "waiting" | "reviewing" | "summarizing" | "idle";
  startedAt: string;
  finishedAt: string | null;
  parentRunId: string | null;  // 显式重试形成新运行
}
```

Team 修订保存时解析并冻结档案内容，禁止将凭据、provider 配置、完整父聊天写入快照。`TeamBinding` 绑定一个修订，已有聊天不会因修改团队或档案而静默换行为。修改档案后通过团队编辑页「同步档案并保存」产生新修订。

新建/新运行均检查档案是否仍存在；隐藏档案不能作为新团队选择项，已有绑定新运行也要求修复后再执行。档案隐藏不是撤销正在运行任务的机制；运行期间执行全局安全策略及工作区限制。

`subAgents` 在 Team Leader 下取「团队成员 ∩ Leader 档案 resolve_selection 结果」。不强行越过已有档案的 include/exclude；保存团队时若所选成员被 Leader 排除，返回校验错误并指出档案。成员 scope 不包含调度工具，仍保持一层委派。

## 2. 持久化与服务所有权

建议 `<agent-workspace>/teams/state.db`，路径由 `get_teams_dir` 统一推导。这里的 agent workspace 是配置所属空间，与用户选择的项目 cwd/WorkspaceScope 分开，不允许客户端指定任意 DB 路径。

| 表 | 键/内容 | 不变量 |
| --- | --- | --- |
| teams | workspaceKey + teamId，当前 revision、归档位 | 保存使用 expectedRevision 比较并交换 |
| team_revisions | workspaceKey + teamId + revision，TeamRevisionSnapshot JSON | 只追加，不原地修改 |
| team_runs | runId，sessionKey、可见 turnId、internalTurnId、snapshot、status、lastSeq | 同 sessionKey + 可见 turnId 最多一个运行 |
| team_tasks | taskId，runId、owner、依赖、resourceKeys、status、result | 归属创建后不可改 |
| team_attempts | attemptId，taskId、stopReason、timing、usage | 不把 profile ID 当作运行 ID |
| team_events | runId + seq，event JSON | 状态改变与事件追加同一事务 |
| team_dispatches | runId + batchId，payloadHash、taskIds、结果 | 相同 key/相同 payload 幂等；不同 payload 为 409 |

DB 层复用 WritingDatabase 的连接/事务习惯，增加 `foreign_keys`、`busy_timeout`、schema version 与必要索引。同步 sqlite 工作通过 `asyncio.to_thread` 的短事务入口执行，不在事件循环里等待磁盘锁；事务内不 await 模型或网络。

TeamService 是 gateway 注入、按配置 workspace 绑定的服务；不通过模块全局读取 Loop。Session.metadata 保存 `team_binding`，不是把 SQLite 执行记录复制进 session 文件。历史通过 run 的 turnId 关联。

## 3. 读取与 mutation

沿用项目 settings 风格：读取经认证 HTTP；写经认证 WS `requestMutation` 白名单，不能新增免认证写 HTTP。沿用 `{error: string}` 传输错误格式；失败码放固定前缀，避免本功能单独改全站 error envelope。

| 接口 | 参数/返回 |
| --- | --- |
| `GET /api/settings/teams?cursor=&limit=20` | `{teams: TeamDefinition[], nextCursor: string|null}`；limit 1..100，按 updatedAt/id 稳定游标 |
| `GET /api/settings/teams/detail?id=...&revision=...` | `{team: TeamDefinition, snapshot: TeamRevisionSnapshot}`；省略 revision 读当前 |
| mutation `teams.save` | `{requestId, team, expectedRevision: number|null}` → `{team}`；创建 expectedRevision=null |
| mutation `teams.archive` | `{requestId,id,expectedRevision}` → `{id,archived:true,revision}`；重复归档幂等 |
| mutation `teams.clone` | `{requestId,id,name}` → `{team}`；新 ID，从 revision=1 开始 |
| `GET /api/team-runs?chatId=...&cursor=&limit=20` | `{runs: TeamRunSnapshot[], nextCursor}`，会话访问检查 |
| `GET /api/team-runs/detail?runId=...` | `{run: TeamRunSnapshot}` |
| `GET /api/team-runs/events?runId=...&afterSeq=...&limit=200` | `{events:TeamEvent[],lastSeq,hasMore,resetRequired}`；limit 1..500 |
| mutation `teamRuns.cancel` | `{requestId,runId}` → `{runId,status}`；可能返回 cancelling，不能提前宣称全部停止 |
| mutation `teamTasks.cancel` | `{requestId,runId,taskId}` → `{taskId,status}`；禁止跨会话取消 |
| mutation `teamRuns.retry` | `{requestId,runId}` → `{newRunId,turnId}`；仅终态且含 failed/interrupted 任务的运行；重新执行前明确在 UI 提示可能重复已有副作用 |

`requestId` 是领域幂等键，不只依赖连接内请求缓存；重连重放不能重复建团队或重试运行。run 查询与 mutation 从认证会话推导 workspaceKey，忽略客户端的 workspaceKey。

错误映射：400 `TEAM_INVALID`（空名、重复成员、无效参数、非 DAG）；404 `TEAM_NOT_FOUND`/`RUN_NOT_FOUND`；409 `TEAM_REVISION_CONFLICT`、`TEAM_BUSY`、`TEAM_MEMBER_UNAVAILABLE`、`DISPATCH_KEY_CONFLICT`；403 跨 workspace/session 或禁止访问；503 `TEAMS_DISABLED`、存储不可用；模型 preset 无效用 400 `MODEL_PRESET_UNAVAILABLE`。失败都不可退回全权限/普通聊天执行。

Agent 删除动作：当前未归档团队引用该档案时 409 `AGENT_IN_USE_BY_TEAM`，返回团队 ID 列表；归档团队与历史快照不阻止删除。现有无 Team 的删除行为保持一致。

`teamRuns.retry` 不重复执行成功任务：复制失败/中断任务及必要的下游 blocked 子图到新 run，已成功前置结果保存为只读输入引用；新尝试仍使用原固定修订。若原 Team/档案/preset 已不可用，拒绝重试并要求先修复或新建聊天。新 turn 先持久化并进入既有会话队列，再返回确认；不能绕过单会话串行调度。错误包括 retry 本身的 requestId 重放。

## 4. 聊天创建与版本规则

`new_chat` 扩展为 `{type:"new_chat",workspace_scope?,team_id?:string,team_revision?:number}`。未传 team_id 为普通聊天；revision 省略时服务端解析最新修订后返回明确版本。

保留现有懒创建：TeamBinding 与 workspace scope 一起服务端暂存，并绑定已认证连接/聊天；首条消息前完成持久化，才能启动 Leader。首次消息不得携带/覆盖可信 TeamSnapshot。空聊天重连若暂存已失效，返回 `TEAM_BINDING_EXPIRED` 并要求重选，不能静默退回普通模式。

`attached` 和 `session_updated` 新增 `team_binding: TeamBinding|null`；聊天列表 `ChatSummary` 增加同语义可空字段。已有会话没有该字段一律按普通聊天解释。

首版：普通新聊天/项目新聊天可选团队；临时聊天不显示团队选择；有消息的聊天不切换团队，提供「用此团队新建聊天」。fork 若已有入口则继承团队修订和对话历史，不继承运行中任务及 runId。团队归档后活动运行可结束，后续新运行报 TEAM_NOT_FOUND 并保留历史；不自动换团队。

Team 运行期间的新普通用户消息进入同一会话的下一轮队列，不注入正在运行的成员，也不修改当前运行的输入快照；UI 显示「已排队，当前团队完成后处理」。取消等控制 mutation 走即时控制通道，不等新聊天轮次。普通无 Team 聊天沿用既有消息注入策略。run.turnId 使用服务端确认的 WebUI 可见轮次 ID，ctx.turn_id 作为单独内部关联保存，禁止二者随意互换。

## 5. Leader 工具与执行边界

```python
# 计划接口，具体 TypedDict/dataclass 均在 teams/models.py、execution.py 定义。
async def team_dispatch(*, batch_id: str, tasks: list[TeamTaskInput]) -> list[TeamTaskResult]: ...

async def execute_member(
    request: TeamExecutionRequest,  # run/task/attempt、冻结 ResolvedAgent、runtime、scope
    *,
    events: EventSink,
) -> SubagentExecutionResult: ...
```

`batchId` 和任务声明进入模型可见 schema；run/session/snapshot/权限由宿主注入，模型不能传入覆盖。第一版一个工具等待该批次全部任务进入终态后返回结构化结果；期间后台 EventSink 持续推送进度。声明依赖必须全部在本批，跨批结果由 Leader 写进下一批任务 instruction。DAG 校验整批通过后才持久化与启动。

新 typed `SubagentExecutionResult` 必含 executionId、outcome、stopReason、finalContent、usage、errorCode；outcome 是 succeeded/failed/cancelled/interrupted。只对正常最终答案标 succeeded，max_iterations/invalid_profile 都是失败。旧 spawn/run_inline 字符串接口保留适配，不变更普通聊天输出。

Team 工具复用共享 Runner 和子执行装配，不复制执行循环。冻结 profile 通过可信 preparation 参数传入；不能按 agentId 临执行再读盘覆盖快照。`announce=False`，不触发普通 spawn 的 system 结果回注，避免一份结果引起两个 Leader 回合。

Leader 除 team_dispatch 外采用本回合 core scope 的白名单工具；在 Team 模式移除普通 spawn，避免绕过团队成员限制。调度工具由 Team 服务注入，保存时校验 Leader 档案允许的团队成员；不把子 Agent 的 subagent scope 当作 Leader scope。

## 6. 事件、状态和重连

```typescript
interface TeamEvent {
  schemaVersion: 1;
  runId: string;
  chatId: string;
  turnId: string;
  seq: number;
  type: "run.started" | "leader.updated" | "task.created" |
    "task.queued" | "task.started" | "task.activity" | "task.finished" |
    "run.cancelling" | "run.finished";
  taskId: string | null;
  attemptId: string | null;
  occurredAt: string;
  data: Record<string, unknown>; // 入口按 type 做 discriminated parser，不原样相信该字典
}
```

新增 typed `TeamRunUpdateEvent(AgentEvent)`，WebUI 边缘投影到 `{type:"team_event",chat_id,turn_id,data:TeamEvent}`；hydrate 使用 `{type:"team_snapshot",chat_id,data:TeamRunSnapshot}`。不复用普通 progress/tool trace，不依赖当前未接线的 agent_ui。

事件 data 固定为以下判别形状：`run.started={binding,startedAt}`；`leader.updated={leaderAction}`；task.created/queued/started/activity/finished 全部为 `{task:TeamTaskSnapshot}`；`run.cancelling={status:"cancelling"}`；`run.finished={run:TeamRunSnapshot}`。任务进入 cancelling 时复用 task.activity 带更新快照。边缘仅投影白名单字段。Python/TS 用同一组 JSON fixture 测边界一致性，未知 schema 版本降级到查询快照，不中断普通聊天。

`seq` 在单个 run 内严格递增。状态变更和事件先同事务落盘，再排入 MessageBus 的网络投影；广播失败不回滚执行，不在事务锁内 await。终态事件不被节流丢弃，task.activity 最多每任务每秒 4 条、data 最多 4KiB，不逐 token 写库。

快照与 `lastSeq` 从同一数据库读事务获得。客户端先注册事件缓存，再拉快照；丢弃 seq<=lastSeq 的缓存、顺序应用其余；出现缺号从 events 补拉。已删除增量返回 resetRequired=true，再拉快照。跨运行无全局 seq，不按时间戳排序。

任务转移：pending→queued/running/blocked/cancelled；queued→running/cancelled/interrupted；running→cancelling/succeeded/failed/interrupted；cancelling→cancelled/failed/interrupted。前置失败/取消时依赖任务为 blocked。取消与成功竞争时以服务端首个持久化状态转移为准；已经进入 cancelling 不再被晚到 succeeded 改写。终态不可回退，重试用新运行/attempt，不复写旧终态。

运行完成要求所有已声明任务终态且 Leader 已交付最终消息：全部成功为 completed；成功/失败混合且 Leader 交付部分结果为 partial；无可用结果或 Leader 失败为 failed；用户停止为 cancelled；进程异常重启遗留为 interrupted。无派单的普通回答允许 completed，UI 显示「本轮由 Leader 直接处理」。

取消：先持久化 cancelling→停止排队/依赖调度→取消和 await 子任务与工具清理→写 cancelled。用户停止整个聊天也走同一服务；单任务取消不取消成功兄弟任务，但其依赖为 blocked。外部工具不支持停止时保留 cancelling，按既有 shell/provider 取消能力处理，不伪造「已停止」。

重启：在拥有该 workspace 的单 gateway writer 启动时将 running/cancelling、未结束尝试置 interrupted；不能由任意第二个读连接标中断。暂不支持多 gateway 同 workspace 调度，第二 writer 拒绝启动团队调度。阻断既有主回合恢复重放 team_dispatch，显示手动重试入口。

## 7. UI 展示与资源边界

面板按 runId→memberId→taskId/attemptId 归属；同一成员多任务排队执行，不把 Agent 档案 ID 当唯一状态键。Leader 为独立行；未参与成员显示「待命」。卡片显示任务、状态、最近公开动作、耗时、结果摘要/错误和停止按钮。

展示执行状态和公开摘要，不展示私有推理；不读取/保存 API key、完整工具参数、原始 shell 输出。未知总工作量不生成百分比；用「已完成 2 / 当前 4 项」和阶段表达进度。历史分页加载 run 快照，在对应 turn 下呈现，实时和历史走同一组件与 reducer。

并发取团队配额、同成员 1、既有 SubagentManager 全局配额的共同约束。resourceKeys 排序后获取锁以防死锁；默认所有写任务使用 workspace-write 共享资源锁，只有显式声明互不相交写集才放宽；未声明写集一律串行。该锁是协作约束，不能拦截恶意 shell；工作区边界和既有沙箱仍必须执行。成员实际工具权限为子 scope 基线、冻结档案选择、本回合 session policy 和全局配置共同允许的交集；Team 不能因引用某档案而重新放开父会话禁用工具。

终态快照保留以支持聊天历史。普通活动事件保留 30 天，每 run 最多保留 5000 条 task.activity，超出时停止追加中间活动并继续更新 task 最新快照；任务/run 生命周期事件不因该阈值丢弃。清理由持 writer 锁的 TeamService 在启动和每日执行，剪除旧增量后 events 接口通过 resetRequired 让客户端回快照。删除聊天沿既有删除入口清理对应 run/events，团队配置和其他聊天不受影响。

预算先限制任务数、dispatch 次数、运行/任务时长及既有 max_iterations；记录各模型用量。用量缺失显示未知，不按 0 计；首版不承诺精确金额或强 token 计费上限。默认不自动重跑有副作用的整个子任务。
