---
artifact: spec
route: brainstorming -> api-and-interface-design
skills: [brainstorming, api-and-interface-design]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/brainstorming/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/api-and-interface-design/SKILL.md
source:
  - .ai-runtime-artifacts/specs/2026-11-13-trajectory-tracing-spec.md
created_at: 2026-11-13
status: draft
approved: false
revision: 2
self_reviewed_at: 2026-11-13
---

# 轨迹 V1 数据和读取契约

本合同为设计草案，端点尚未实现。Python 内部 snake_case，WebUI DTO camelCase。错误沿用现有 WebUISettingsError／SettingsRouteResult 的 `{"error":"..."}` 封装，字符串以稳定 trajectory code 开头，例如 `{"error":"TRAJECTORY_INVALID_CURSOR: invalid cursor"}`；客户端按冒号前 code 判断，不解析翻译文案，也不假定现有框架已有独立 error.code 字段。

## 1. 上下文与事件信封

```typescript
interface TraceContext {
  workspaceId: string;
  sessionId: string;
  traceId: string;
  runId: string;
  turnId: string | null;
  executionId: string | null;
  parentSpanId: string | null;
  source: 'user' | 'api' | 'cron' | 'dream' | 'system';
  ephemeral: boolean;
}

interface TrajectoryEvent {
  schemaVersion: 1;
  eventId: string;
  sessionId: string;
  seq: number; // session 内从 1 开始，仅提交后分配
  traceId: string;
  runId: string;
  turnId: string | null;
  step: number | null;
  spanId: string | null;
  parentSpanId: string | null;
  executionId: string | null;
  logicalRequestId: string | null;
  modelCallId: string | null;
  toolCallId: string | null;
  type: TrajectoryEventType;
  ignorable: boolean;
  timeMs: number;
  payload: Record<string, unknown>; // 实际使用按 type 判别的闭合 schema
}
```

producer 提交不含 seq 的 immutable candidate，单 writer 按 session 序列化；eventId 重复不分配新 seq。时间是 epoch ms，duration 来自同一进程的 monotonic 时钟，不能用跨进程 wall clock 相减伪造耗时。子进程事件携带自身 duration 与 clockOrigin，需因果排序时用父子关系和 ingestion seq。

轨迹 UI 稳定记录 ID 是 type/domain ID 的组合，不使用数组下标；eventId/seq/recordId/spanId 不混用。session 内连续 seq 只代表保存顺序，表格组装根据 turn/run/step/modelOrder，不按并发完成先后改写模型语义顺序。

普通子 Agent 与父 session 共用归属，childRunId/executionId 区分执行；`execution.linked.childSessionId` 可为 null，不能假定存在独立子聊天。parentSpanId 可以引用同 trace 下其他 run 的 span，导航必须重新校验目标归属。

## 2. V1 事件词汇

| type | 最小 payload 与说明 |
| --- | --- |
| `run.started` | source、purpose、parentRunId、agent 身份快照、coverage |
| `run.finished` | status、stopReason、durationMs、coverage |
| `turn.started` / `turn.finished` | visible turn、source；终态带 outcome |
| `input.recorded` | bodyRef、mediaRefs、source；用户输入事实 |
| `context.injected` | kind、来源、bodyRef、logicalRequestId；实际模型注入 |
| `step.started` / `step.finished` | step 索引、归属 run、终态 |
| `model.requested` | logicalRequestId、requestSnapshotRef、purpose、requestCompleteness |
| `model.attempt.started` | modelCallId、attempt、retryOfCallId、provider/model、effectiveInputRef、attemptVisibility |
| `model.output.delta` | attempt、blockIndex、blockKind、deltaRange、firstAt/lastAt、contentRef；可精确文本合并 |
| `model.attempt.finished` | status、finishReason、outputRef、usage、timing、errorCode、providerResponseId |
| `model.retry.scheduled` | logicalRequestId、previousCallId、attempt、delayMs、errorKind |
| `model.route.changed` | logicalRequestId、from/to provider/model、reason；不再计一次模型请求 |
| `tool.requested` | toolCallId、providerCallId、name、origin、argumentsRef、schemaRef、modelOrder |
| `tool.started` | effectiveArgumentsRef、startedAt、queueMs；真正进入执行 |
| `tool.finished` | status、durationMs、resultRef、errorRef、modelObservationRef、sideEffectOutcome |
| `execution.linked` | invocationId、childRunId、childSessionId（可空）、parentToolCallId、agentId |
| `compaction.started` / `compaction.finished` | 输入范围、输出摘要引用、关联模型请求、status |
| `session.forked` | parentSessionId、source boundary；只记录谱系 |
| `trace.gap` | reason、已知事件／正文缺失范围、coverage；不制造猜测结果 |

schema 按 type 冻结字段；事件信封增加 `ignorable: boolean`，V1 默认 false。未知 schemaVersion 不解释 payload，客户端显示版本不可用。未知 type 仅在 ignorable=true 时跳过显示并推进已验证游标；否则停止应用并显示需升级，不反复 resync 同一批不兼容数据。不能让客户端自行猜测未知事件是否影响分组。

`TrajectoryEventType` 即上表 type 字面量的联合；带 `/` 的表格项各展开成两个独立字面量，实施时 Python 和 TypeScript 的运行时 parser 使用同一组 fixture 验证。本文类型片段为契约说明，不作为可直接编译的完整 SDK。

## 3. Span、请求和正文

```typescript
type SpanStatus = 'requested' | 'running' | 'succeeded' | 'failed'
  | 'denied' | 'cancelled' | 'interrupted' | 'skipped';

interface ContentRef {
  bodyId: string | null;
  preview: string;
  contentType: string;
  storedBytes: number;
  originalBytes: number | null;
  redacted: boolean;
  truncated: boolean;
  unavailableReason: 'not_recorded' | 'provider_managed' | 'expired'
    | 'budget_exceeded' | 'capture_failed' | null;
}

interface UsageDetail {
  inputTokens: number | null;
  outputTokens: number | null;
  totalTokens: number | null;
  cacheReadTokens: number | null;
  cacheWriteTokens: number | null;
  source: 'reported' | 'estimated' | 'mixed' | 'missing';
}
```

正文引用始终受 owner/workspace/session 校验；bodyId 不映射任意本机路径。原参数非法 JSON 时保留脱敏 argsRaw，不强制解析成空对象后丢失证据。requestSnapshot 包含 system、顺序 messages、tools、generation、continuationKind 和 completeness，不含凭据或函数回调。

输出块保留 type/order，媒体仅受控引用；不支持的块保留安全类型说明和缺失原因。body 不以裸 SHA256 标识秘密原文，防止通过 hash 判断已知敏感内容。

工具终态第一次有效闭合后不被普通迟到事件回退；若收到冲突终态，记录诊断缺口并保留最早可信事实。模型 request 是组，attempt 是独立 span；成功重试不改写上一次失败。

## 4. 读取接口

所有端点位于现有认证 WebUI 路由层；`sessionId` 为服务器已解析的会话身份，不允许以 URL 参数构建 DB 文件路径。权限失败遵循现有封装；跨 workspace/session 的对象统一不暴露其存在性。

管理身份与聊天绑定权限必须显式区分：本机／gateway 管理凭据可在服务器授予的 workspace 范围读取；聊天连接只读已绑定 session 及可证明归属的子执行；模型 API 凭据不授予轨迹读取。`caller_chats=()` 是无归属证据，不得直接解释为全局放行。当前项目不假定多租户 owner 系统已经存在。

| 接口 | 输入与响应 |
| --- | --- |
| `GET /api/trajectory/sessions/{sessionId}` | 返回 mode、available、retainedFromSeq、durableSeq、coverage、runs 和能力；不返回所有正文 |
| `GET /api/trajectory/sessions/{sessionId}/records` | UI 初始化与历史补页；`beforeCursor` 可空、`limit=200`（1–500），响应 RecordPage；初次取尾页 |
| `GET /api/trajectory/sessions/{sessionId}/events` | `beforeSeq` 与 `afterSeq` 二选一，`limit=200`（1–500）；无 cursor 取尾页，响应事件均升序 |
| `GET /api/trajectory/sessions/{sessionId}/spans/{spanId}` | 返回已提交快照、关联请求和 ContentRef；不得默默读取任意 child session |
| `GET /api/trajectory/sessions/{sessionId}/bodies/{bodyId}` | `offsetBytes=0&limitBytes=65536`，最大单页 256KiB；返回安全文本片段、nextOffsetBytes、hasMore |

记录页和增量事件页：

```typescript
interface TrajectoryRecord {
  recordId: string;
  anchorSeq: number; // 第一次建立记录时固定，向前补页不改变身份
  recordRevision: number; // 更新本记录的最后已提交 seq
  kind: 'system' | 'user' | 'context' | 'assistant' | 'tool' | 'subtool' | 'compaction';
  runId: string;
  turnId: string | null;
  step: number | null;
  spanId: string | null;
  parentSpanId: string | null;
  modelOrder: number | null;
  status: SpanStatus;
  startedAtMs: number | null;
  finishedAtMs: number | null;
  durationMs: number | null;
  blockSummaries: { index: number; kind: string; content: ContentRef }[];
  requestRef: ContentRef | null;
  schemaRef: ContentRef | null;
}

interface RecordPage {
  schemaVersion: 1;
  sessionId: string;
  records: TrajectoryRecord[]; // 按固定的 turn/run/step/modelOrder 和 anchorSeq 展示键排序
  ancestorStubs: { spanId: string; parentSpanId: string | null; label: string }[];
  snapshotSeq: number; // 记录投影和这个 H 来自同一次 SQLite 读快照
  historyGeneration: number;
  nextBeforeCursor: string | null; // 服务器签发，绑定 session、generation 与固定展示键
  hasOlder: boolean;
  retainedFromSeq: number;
  coverage: Coverage;
}

interface EventPage {
  schemaVersion: 1;
  sessionId: string;
  events: TrajectoryEvent[];
  snapshotSeq: number;
  retainedFromSeq: number;
  durableSeq: number;
  historyGeneration: number;
  recordUpdates: TrajectoryRecord[];
  nextBeforeSeq: number | null;
  nextAfterSeq: number;
  hasOlder: boolean;
  hasMore: boolean;
  coverage: Coverage;
}

interface Coverage {
    status: 'complete' | 'partial' | 'unknown';
    reasons: string[];
    contentMode: 'off' | 'metadata' | 'standard' | 'raw-chunks';
    attemptVisibility: 'provider_call' | 'transport_attempt' | 'mixed';
}
```

`beforeSeq` exclusive，`afterSeq` exclusive。事件页本次请求 snapshotSeq 固定为查询起始时的提交头；后续产生的事件靠订阅或新的 after 请求读取。nextAfterSeq 只能推进至本页实际交付的最后 seq，不能直接跳 durableSeq；空页保持原 after 或已确定尾页水位。nextBeforeSeq 为本页最小 seq；hasMore 表示向后仍有事件，hasOlder 表示向前仍有事件。

UI 初始化先应用 RecordPage，设置 `appliedSeq=snapshotSeq`；基线包含窗口内已提交 partial／终态，不能再把基线之前的 delta 拼一遍。历史记录分页每次可有新的 snapshotSeq，合并时只接受更大 recordRevision，且不推进 live appliedSeq。记录正文仍以 ContentRef 懒加载；预览只做绝对替换，不做完整文本拼接。模型最终输出更新原 recordId，不创建第二份答案。

增量 EventPage 的 recordUpdates 可为该查询读快照下的较新绝对记录状态，并不证明中间事件均已接收，因此 appliedSeq 仍只跟随实际连续 events；较晚收到的旧 recordRevision 不回退状态。UI 不提供任意历史时刻回放，不能将这个接口当时光快照 API。

`retainedFromSeq` 初始为 1，空 session 的 durableSeq=0；后端只清理连续前缀，historyGeneration 变化使旧历史分页 cursor 返回 410。补拉 afterSeq < retainedFromSeq-1 返回 410，afterSeq > durableSeq 返回 400。已清理前缀和捕获时从未产生事件是不同缺口，不用 seq 跳号暗示正文采集完整。

详情里的 record/span 状态返回 recordRevision；读取某个 ContentRef.bodyId 的字节内容在其存续期内不可变。流式记录更新指向新正文引用或不可变 chunk 列表，不能原地改写已有 bodyId 后仍让历史缓存认为有效。

正文 offset 是 UTF-8 字节偏移；服务端按字符边界切片并返回真实 next offset，客户端只用返回值续页。截断与脱敏发生后才计算 storedBytes，不能按原文偏移套到脱敏正文。

## 5. WebSocket 与补拉

- 握手 capability：`trajectoryEventsV1`。订阅消息 `trajectory.subscribe` 带 requestId/sessionId/afterSeq，unsubscribe 在切聊天或关闭订阅时发出。
- 服务端在一致的提交订阅边界注册，再补放 `(afterSeq,H]`，并缓冲其后的 live commits；或用 outbox replay 实现同样语义。不能先读 HTTP 再无水位地订阅。
- 帧 `trajectory.batch` 包含 sessionId、historyGeneration、events、recordUpdates、fromSeq、toSeq、durableSeq。这里的 seq 区间必须连续；一次完整应用后客户端才推进 appliedSeq=toSeq。durableSeq 只是服务器提交头，不作为客户端确认水位。例如客户端只应用到 100 而服务器已提交到 150，重连必须 afterSeq=100，不能用 150。
- `trajectory.resync_required`：保留范围已清理、客户端太慢超缓冲或不兼容版本。客户端重新取尾页，明确历史缺口；不能凭一次断线就把运行标为失败。
- `trajectory.health`：available、reason、capture state；存储失败可经内存健康帧通知，该帧不带持久 seq，也不冒充落盘事件。
- `trajectory.head`：活跃订阅每 5s 发送 sessionId、historyGeneration、durableSeq；客户端发现头高于 appliedSeq 就按已应用水位补拉。用于发现 commit 成功但最后一批通知丢失，不将 head 当确认或采集完整证据。
- 已知 seq 同 eventId 重复可忽略；同 seq 不同 eventId 视为冲突，需要 resync。跨 chat/workspace frame 丢弃。
- 慢消费者不阻塞模型执行；服务端关闭慢订阅并要求补拉，已提交事实留在 DB。
- 公共配置设为 off 时停止新事件／正文采集并 flush 已接受队列，既有持久历史按权限仍可读取；metadata 保留身份／状态／计时而不保留正文；standard 合并 chunk，raw-chunks 保留原分片。切模式以事件边界生效，覆盖信息记录生效区间，不用新模式替过去的正文完整度背书。

临时聊天使用连接期内存会话和内存 seq，能力明确 `persistence=memory`，不复用持久 session endpoint 悄悄创建 DB。

## 6. 错误与覆盖

稳定 code 与 HTTP 状态：`TRAJECTORY_NOT_FOUND=404`、`TRAJECTORY_UNAVAILABLE=503`、`TRAJECTORY_WRITER_BUSY=503`、`TRAJECTORY_CURSOR_EXPIRED=410`、`TRAJECTORY_VERSION_UNSUPPORTED=409`、`TRAJECTORY_BODY_UNAVAILABLE=410`、`TRAJECTORY_INVALID_CURSOR=400`。未获授权或不存在的 body 一律按现有权限封装返回 404；只有已授权且明确过期／未采集的正文返回 BODY_UNAVAILABLE 和安全原因。

`coverage.status=complete` 仅指声明 contentMode 和 attemptVisibility 下已支持边界的记录完整，不代表掌握远端隐藏 reasoning、SDK 隐式重试或 MCP 内部链路。UI 同时显示 coverage 范围和不可见项，不能只展示绿色 complete。

模式 metadata 的正文应明确 not_recorded，即使其事件完整；standard 的 body 被预算截断则 status=partial。策略性 chunk 合并不等于正文丢失，但不能声称具备 raw token timing。

## 7. 并行边界

后续可按采集、持久查询、前端纯投影、界面四个边界拆分，但先固定本合同样例与身份。生产点修改只由一个任务持有；前端可用相同 fixture 开发，最终必须接真实后端验证。此文不构成子 Agent 调度授权或已批准实施计划。
