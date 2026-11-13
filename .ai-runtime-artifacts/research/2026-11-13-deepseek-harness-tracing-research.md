---
artifact: research-report
route: source-driven-development -> brainstorming
skills: [source-driven-development, brainstorming, api-and-interface-design, observability-and-instrumentation]
skills_evidence:
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/source-driven-development/SKILL.md
  - C:/Users/Huangqh/.codex/skills/brainstorming/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/api-and-interface-design/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/observability-and-instrumentation/SKILL.md
source:
  - 用户指定的 deepseek-harness 本地源码及轨迹页截图
  - AGENTS.md
  - harness-kit/core/routing.md
created_at: 2026-11-13
status: complete
---

# DeepSeek Harness 轨迹机制与知序接入调研

## 1. 调研范围与结论

用户明确选择“排查调试 + 聊天中可展开查看”，随后提供截图并要求“就做的和他一样就可以了”。因此目标是复现截图中的 Trajectory 轨迹体验：对话／轨迹标签、三行时间带、轮次与调用折叠、紧凑明细表、搜索、完整内容检查器。不是泛化的监控仪表盘。

主证据来自 `D:/studyspace/源码学习/deepseek-harness-master/deepseek-harness-master`，根包和轨迹包均声明 `0.1.0-rc.5`。该目录没有可读 `.git/HEAD`，不能认定其对应某个上游 commit。知序当前分支 `feature/desktop-app`，调研时 HEAD 为 `611c3ab2`；工作区包含知识库及其他任务改动，本次不修改这些文件。

上游在线官方仓库用于交叉核对和提供入口，线上 master 与本地快照存在词汇差异，具体实现事实以本地源码为准。研究采用静态代码与测试源码阅读，没有启动 DeepSeek Harness、没有执行其测试、没有付费调用模型，不能把测试文件存在表述为测试通过。

核心结论：DeepSeek Harness 用类型化、仅追加的 Session 事件记录驱动轨迹；知序已有可复用的运行入口和活动组件，但当前普通聊天数据不足以准确恢复逐次请求与真实工具时序。建议借鉴原版的事件事实与独立视图，新增轻量轨迹域，不把整个 Python 会话系统重写为它的 Cordis 插件架构。

## 2. 截图与真实源码对应

| 截图区域 | 本地实现 | 查证结果 |
| --- | --- | --- |
| 对话／轨迹标签 | `packages/client/ui-trajectory/src/client/index.ts`、README | 作为 `conversation.view` 的视图注册；独立 target，不从 Chat 气泡反向解析 |
| 时长／轮次／调用／搜索 | `TrajectoryToolbar.tsx` | 时长切换真实耗时和等宽操作；后两项为整体折叠开关，不是统计卡片 |
| 输入／模型／工具色带 | `TrajectoryTimeline.tsx`、`timeline.ts` | 等宽与按时长两种主模式；时长默认压缩无活动间隔，支持区间选择、缩放、平移、悬停详情 |
| 第 N 轮、内部步骤 | `layout.ts`、`TrajectoryTurn.tsx` | 用户轮次和模型 step 分开；输入、上下文、模型、工具保持语义顺序 |
| 工具名 参数 → 结果 | `trajectory-tool-definition.ts`、`TrajectoryTable.tsx` | 用 callId 关联开始和结果，支持嵌套 subtool |
| 助手／仅工具调用 | `trajectory-assistant-definition.ts` | 组装文字、reasoning、工具参数 delta；仅工具响应仍有助手记录 |
| 选中明细 | `TrajectoryTable.tsx::detailTabs` | 输入、输出、思考、schema、系统提示变化、Token、计时等按类型展示 |
| 长历史 | `TrajectoryTable.tsx`、`trajectory-virtual-rows.ts` | 本地阈值 100 行；虚拟列表 overscan 12；向上补页保持稳定行身份 |
| 底部输入框 | README、`TrajectoryView.tsx` | 轨迹仍位于同一会话壳，输入框悬浮，列表预留实际输入框高度 |

原版有明确限制：搜索、折叠、选择和汇总覆盖当前已加载窗口；运行中只有开始标记，不伪造完成耗时；更早历史通过“加载更早”提示补齐。截图是一种已运行状态，不构成所有功能和精确尺寸的证明。

2026-11-13 自检补查：`TrajectoryView.tsx:129,284` 默认 actualTime=false，打开 Duration 对应 timelineMode=duration；`timeline.ts::deriveTimedTimeline` 此时 compressIdle=true。完整 wall-clock 时间轴是独立模式，原 Toolbar 将该开关隐藏。因此不能把截图“时长”直接解释成保留空闲间隔的绝对时间轴。

## 3. Session 事件是基础

证据：`packages/core/session/src/types.ts`、`index.ts:425,629`、`known-event-types.ts`。

- `Session.append` 分配 `seq = log.length`，本地从 0 开始，并记录 time/type/data。
- `SessionEventMap` 定义类型化事件，插件可扩展；已知事件词汇由脚本生成。
- 存在 `turn/start/end`、`step/start/end`、`user/message`、`assistant/chunk/message`、`tool/call/result`、`request/header/context`、retry、compaction、hook、subagent 等事实。
- 可进入模型上下文的事件另外带 `surfaceOp`、`sourceEventSeqs`。`foldSurface` 投影当前模型历史，旧事实可以被压缩替代，但日志没有被气泡覆盖。
- `assistant/message` 引用产生它的 chunk seq，因此原始分片和最终消息并存时不会重复生成答案。
- 事件追加后的广播是观察通知，不等同于磁盘事务提交。外部监听器异常被隔离。

对知序的启发：显示序号、调用身份、模型上下文、聊天渲染序号需要分开；不能把现有 `turnSeq` 当持久事件 seq，也不能把一条最终答案冒充整轮所有模型输出。

## 4. 模型采集位置

证据：`packages/core/agent-loop/src/agent.ts:332,349,382,466`。

1. 每个 step 准备请求，保存初始／恢复／变更的 `request/header`。
2. header 中保留有效系统提示与工具 schema；request/context 提供模型与上下文窗口等事实。
3. 流式 chunk 经 assembler 组装，同时记录 `assistant/chunk`。
4. 完成时写 `assistant/message` 与 usage，保存内容块顺序。
5. 解析结果中的 tool-call 并交给工具调度。
6. 失败重试通过事件和状态体现；不能据此声称该轨迹拥有原始 HTTP 字节包。

原版详情中的“输入”主要是规范化、可重建的模型请求视图。Provider 内部转换、服务器端续接历史以及远端未回传的思考，不会因为有轨迹页就自动可见。

## 5. 工具开始、结果与并发

证据：`packages/core/agent-loop/src/tool-calls.ts:133,262,268`。

- 工具计划、准入、dispatch、最终结果是不同阶段。开始前追加 `tool/call`，结果引用其 seq。
- 并行工具使用有界滚动池；实际 dispatch 可重叠，policy 与结果提交保持模型顺序。
- 取消会处理尚未启动与已启动调用；调度器内部失败不会随意伪造成功结果。
- 因此结果日志的到达／提交时间不一定等于工具实际完成时刻。知序若要准确画耗时，必须在实际执行边界独立计时。
- 工具树关联依赖 parentCallId/subCallId，前端对自环、重复父节点、深度做保护；同名调用不是同一次调用。

## 6. 持久化并非“每个 token 同步写文件”

证据：`packages/session/session-persistence/src/write-behind.ts`、`coordinator.ts:30,1123,1342`，JSONL 与 SQLite 后端 README。

- 实时 Session 事件进入每会话 controller；默认固定批处理窗口 200ms，后续事件不重置截止时间。
- 后端写入串行化，失败保留有序前缀；`session/flush` 是显式持久屏障。
- JSONL 可无压缩或 Zstandard，完整分片仍保留；连续同 block delta 可打包为无损存储行，恢复 seq/time。
- JSONL 校验 header、序号、frame 和尾部；只修复可确认的未完成尾部，不掩盖已提交区损坏。
- 恢复区分 TOOL_NOT_STARTED 和 TOOL_OUTCOME_UNKNOWN，后者不意味着可安全重放副作用工具。
- SQLite 是替代存储后端；本地实现使用同步 DatabaseSync，README 明确其阻塞及写争用限制。

对知序：可采用 SQLite 事务和异步批写，不复制其 Node 同步 DB 实现；界面需知道 durable watermark，不能把内存预览当可靠历史。

## 7. 前后端投影和性能

证据：`packages/client/ui-trajectory/src/client/trajectory-contract.ts`、`trajectory-snapshot-builder.ts`、`trajectory-assistant-definition.ts`、`TrajectoryView.tsx`、`TrajectoryTable.tsx`、`packages/client/runtime/src/client/sessions/`。

- 共享 Session 窗口提供事件，Trajectory 自己注册定义、组装快照，Chat 的视图状态独立。
- 轨迹有 eventNodes、eventLocations、requests、callSchemas、partial、runningCalls，不只有字符串日志。
- 使用稳定 recordId，向前补页不重新编号身份；选中记录和折叠状态可保留。
- 内容 delta 更新复用行结构、测量与滚动位置；不能每个 token 重建全表和搜索索引。
- 时间带与表格共享记录身份和筛选集合；区间判断是活动跨度与所选闭区间相交。
- 前端搜索索引有节流；最终状态替换 partial，不把最终文本再拼一遍。
- `session-query/tracing.ts` 是另一项能力：分析会话谱系、事件来源和替代关系，不等于截图组件，也不等于 OTel。

## 8. 轨迹、遥测与外部平台是三层

证据：`packages/session/session-telemetry/README.zh.md`、`session-telemetry-otel/README.zh.md`。

| 层 | 作用 | 可靠性与限制 |
| --- | --- | --- |
| Session 本地事实 | 历史、续接、轨迹来源 | 本地持久化契约负责 |
| Trajectory UI | 查询和检查这些事实 | 不向模型加提示，不负责上报 |
| Telemetry / OTel backend | 导出副本给外部 collector | SDK 队列／批处理／重试，handoff 不等于 delivery |

本地 OTel 后端默认 DISABLED，可选 FULL／FEEDBACK_ONLY；映射为 OTel **日志记录**，不是简单地为每个步骤创建分布式 Span。seam 不自带脱敏规则；未挂规则的导出副本可能含命令输出和文件内嵌凭据。遥测投影只保留每 step 第一个 assistant/chunk，缺 seq 不能直接判断丢包。崩溃后外部队列可能丢记录，没有持久 outbox 保证。

因此，“复制它的轨迹页”不要求安装 OTel、Langfuse 或 collector。知序第一版应本地闭环，后续导出作为独立适配层。

## 9. 知序当前能力与明确缺口

| 当前拥有者 | 已有能力 | 对本需求的不足／接入点 |
| --- | --- | --- |
| `agent/hook.py`、`turn_hooks.py` | 生命周期、逐工具钩子、每轮组合 | 缺最终请求准备后的标准内容快照；ephemeral 默认跳过额外 hooks，需单独接内存轨迹 |
| `agent/runner.py::_request_model` | 上下文治理、流式内容／reasoning、hosted tool、retry | 在 governor 完成后采集 logical request；不能只读 before_iteration 的旧 messages |
| `agent/progress_hook.py:132,161` | 一批工具开始，一轮后统一结束事件 | 一批工具不等于真实开始，结束延后不适合实际时间带 |
| `agent/tools/execution.py:130–178` | ContextVar 隔离工具身份、并行执行 | repeated lookup 与参数失败发生在 before_execute_tool 之前；CancelledError 不走普通错误钩子 |
| `providers/base.py:713,764,1262,1357` | 逐次 retry-managed call observer、usage、时延 | 仅完成通知；没有标准 started、内容和稳定 call ID；具体 leaf/SDK 覆盖还需适配测试 |
| `llm_usage/models.py` | 内容无关的物理调用统计 | 合同明确排除 messages、response、reasoning、tool payload；不得硬塞全文 |
| `cli/gateway_runtime.py:506` | 已设置全局 record_llm_call | 单槽 setter；再设置 trace observer 会覆盖用量采集 |
| `providers/fallback_provider.py` | observer 传给主叶和新 fallback 叶 | 要防 wrapper 与 leaf 重复记账；logical request 和物理 attempts 分开 |
| `session/manager.py` | 聊天与 checkpoint 持久化、fork／删除 | 不是逐调用 append-only 轨迹；旧历史缺失的信息无法反推 |
| `subagent_executions/` | SQLite 状态与事件、invocation 及父工具关联、补拉 | 以执行摘要为主，不是主模型和全部工具内容的统一记录 |
| `evaluation/trace.py` | seq、JSONL 分段、大正文外移、incomplete | attempt 专用、闭集事件、16KiB／1MiB／20MiB 预算，不能直接当在线会话存储 |
| `evaluation/worker.py` | hook + model observer、物理 ID、Agent/Judge 分账 | 评测特有语义与 fail 行为需保留；普通聊天不能照搬“采集失败即执行失败” |
| `webui/src/lib/tool-traces.ts` | call_id 与 phase 合并 | 部分 fallback 仍解析字符串；不适合作为新轨迹的真相源 |
| `activity-timeline.ts`、`AgentActivityCluster` | 聊天活动折叠、可见 turnId、顺序 | 可保留现状并接轨迹定位入口；需要独立轨迹投影 |
| `webui/src/lib/types.ts` | ToolProgressEvent、turnSeq、reasoning、usage | 缺逐物理请求、精确逐工具时间和持久轨迹序号 |

此外，调用点搜索发现 memory consolidation、extractor、profile extractor、标题生成、judge、fallback 等辅助模型调用。不能只在主聊天 Runner 装一个 hook 就承诺所有模型请求已覆盖。

自检补充：`agent/subagent.py` 普通子运行从 origin 读取 session_key 并传入 RequestContext/AgentRunSpec，不能假定其独立 childSession。`settings_contracts.py` 错误结构为 error 字符串，caller_chats 的注释说明空集合无法证明归属；新轨迹接口不能将空集合当成全权限，或假定已有 error.code／多租户 owner ACL。

## 10. 实现路线比较

| 路线 | 成本 | 判断 |
| --- | --- | --- |
| 给现有 UIMessage 换个表格皮肤 | 低 | 无法补出物理重试、请求快照、被拒工具和真实计时，不满足调试需求 |
| 原版轨迹交互 + 知序本地统一轨迹域 | 中 | 推荐；保留已有会话机制，集中补采集与查询，视觉和交互对齐原版 |
| 整体迁移 Cordis／Session event sourcing 或依赖外部平台 | 高 | 超出本次目标，带来会话迁移及部署成本，原版 UI 也无法直接脱离其 contracts 安装 |

首版不是只有漂亮列表的演示版，应一次具备完整的截图交互与真实链路接线；实施内部可分纵向验收批次。

## 11. 可直接借鉴与需要改造的代码

- 优先借鉴纯投影：`timeline.ts`、`trajectory-virtual-rows.ts`、record 格式化和时间区间算法；适配知序 DTO 并保留回归样例。
- 参考 UI：Toolbar、Timeline、Table／Inspector、轮次分组；拆成知序小组件，不复制超过千行的整张 Table。
- 不直接引入 Cordis、dsh-client-runtime、Slot 插件注册体系和整个 dsh Session 服务。
- 原版 MIT 许可证允许改编；复制实质代码时保留 DeepSeek 版权与 MIT 声明，并登记第三方来源。仅模仿功能／布局与逐行复制是不同的依赖决策。
- `@tanstack/react-virtual` 原版声明 `^3.14.9`，知序目前未安装。建议实施时引入 React Virtual v3 的精确验证版本；方案不伪造已完成安装和兼容测试。

## 12. 证据入口

本地重点阅读路径均相对于前述 DeepSeek Harness 根目录：

1. `packages/core/session/src/{types,index,surface,known-event-types}.ts`
2. `packages/core/agent-loop/src/{agent,tool-calls}.ts`
3. `packages/session/session-persistence/src/{coordinator,write-behind}.ts`
4. `packages/session/session-persistence-{jsonl,sqlite}/README.zh.md`
5. `packages/client/ui-trajectory/README.zh.md` 与 `src/client/` 上述文件
6. `packages/client/runtime/src/client/sessions/` 与 `conversation/event-registry.ts`
7. `packages/host/apiproxy/src/api/{events,sessions,sessions.schema}.ts`
8. `packages/session-query/session-query/src/tracing.ts`
9. `packages/session/session-telemetry{,-otel}/README.zh.md`
10. `packages/client/ui-trajectory/tests/{layout,table,snapshot-builder,virtual-rows}.client.spec.*`（阅读，不代表已执行）

在线官方入口（访问日期 2026-11-13）：

- [DeepSeek Harness Session 说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/session.md)
- [DeepSeek Harness Trajectory](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/client/ui-trajectory/README.md)
- [DeepSeek Harness Web Client 数据流](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/web-client.md)
- [TanStack Virtual Virtualizer API](https://tanstack.com/virtual/latest/docs/api/virtualizer)：稳定 key、测量、overscan 和 scroll margin 的官方依据。

OpenTelemetry 原 gen-ai 文档地址当前返回 Moved，未将该跳转页当作 GenAI 语义契约证据；本方案的 OTel 结论仅来自已读本地实现，不绑定某个外部 Span 规范。

## 13. 尚未被证明的事项

- 原版在本机浏览器／桌面端的实际帧率、真实 collector 投递和模型服务行为。
- 知序所有 provider 内部 SDK 重试次数；现有 safe-call 边界只能直接证明一次包装调用，SDK 内再试不能默认等于独立可见网络请求。
- opaque provider continuation 的服务器端完整上下文；只展示已知请求片段和续接标识。
- 新增存储／虚拟化库的真实性能与兼容性；属于后续实施验收，不能用本次静态研究代替。

完整设计见 [轨迹实现方案](../specs/2026-11-13-trajectory-tracing-spec.md)，字段与读取语义见 [接口契约](../contracts/2026-11-13-trajectory-tracing-contract.md)。
