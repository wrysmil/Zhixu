# nanobot/teams — Agent Team 领域包

Agent Team 的实现：团队定义与修订快照、会话绑定、运行/任务调度、事件投影，以及 Leader 回合的装配。
真值源是 `.ai-runtime-artifacts/contracts/2026-10-15-agent-team-contract.md`（252 行，下称「契约」）：
本目录只实现契约，不重新定义它。领域类型不依赖任何运行时对象（`models.py` 的 import 只有标准库）。

建议阅读顺序：`models.py` → `database.py` / `repository.py` → `service.py` → `scheduler.py` / `execution.py`
→ `events.py` → `leader.py` / `turn.py` → `binding.py` / `runs.py`。

## 外部接缝

| 方向 | 位置 | 内容 |
| --- | --- | --- |
| loop → teams（窄口） | `nanobot/agent/loop.py:116-117` | 只 import 4 个符号：`persist_message_binding`、`LeaderTurnContext`、`build_leader_registry`、`resolve_leader_runtime` |
| teams → agent（反向依赖） | `leader.py:22-25`、`execution.py:21-28`、`turn.py:26,40` | `agent.model_runtime` / `tools.base` / `tools.registry`、`agent.subagent`、`agent.tools.team` |
| 依赖倒置 | `turn.py:46-66` | `_AdmittingService`、`AgentLike`、`TeamHost` 三个 Protocol 对接 loop 表面，`turn.py` **不 import loop** |
| gateway 装配 | `nanobot/cli/gateway_runtime.py:44,799`、`nanobot/webui/gateway_services.py:604,618` | `bind_team_turns`；`build_team_turns` / `TeamLeaderTurns` / `TeamRunController` |

## 逐文件职责

| 文件 | 负责 | 不负责（边界） | 主要导出 |
| --- | --- | --- | --- |
| `__init__.py` | 包 docstring 声明真相源与「本包只承载类型与边界校验」；把 `models.py` 的公开符号集中再导出 | 不 re-export `database`/`repository`/`service`/`scheduler`/`leader`/`turn`/`events`/`binding`/`runs`（见下节） | `__all__`（49 项）与 `models` 的常量/类型同名 |
| `models.py` | 契约 §1/§5/§6 的全部 dataclass 与 Literal：定义、修订快照、任务输入/结果/快照、运行快照、事件与判别 data；camelCase 线格式；`__post_init__` 统一做拒绝式边界校验 | 不读写库、不做 IO、不认识 sqlite 与 agent 运行时；不决定业务状态流转（只校验状态值合法） | `TeamDefinition`/`TeamRevisionSnapshot`/`TeamRunSnapshot`/`TeamTaskInput`/`TeamTaskResult`/`TeamEvent`、各 `*Data`、上限常量、`TeamValidationError`、`check_full_result_length` |
| `database.py` | SQLite 入口：`get_teams_dir` 唯一路径、连接与 PRAGMA（`foreign_keys`/`busy_timeout`）、幂等 DDL、workspace 独占 writer 锁 | 不含任何业务 SQL 语义；`db_path` 只服务测试；读路径不建库（`DatabaseAbsent`） | `TeamDatabase`、`compute_workspace_key`、`WriterBusy`/`StorageUnavailable`/`DatabaseAbsent`、`SCHEMA_VERSION` |
| `repository.py` | 单事务内的修订 CAS（`UPDATE ... WHERE revision=` 判 rowcount）、事件 seq 与 `last_seq` 同事务递增、`requestId` 幂等、运行/任务/attempt/dispatch 落盘、`events_page` 的 `resetRequired`、活动事件剪枝 | 不做引用校验（档案是否存在）、不碰事件循环、不决定终态（只写调用方给的 status） | `TeamRepository`、`TeamNotFound`/`RunNotFound`/`TeamRevisionConflict`/`IllegalTaskTransition`、`payload_digest`、`new_id`、`utc_now_iso`、`RUN_TERMINAL_STATUSES` |
| `binding.py` | 契约 §2/§4 的懒创建：按 chatId 暂存绑定（TTL、一次性 claim、容量上限），首条消息落 `session.metadata["team_binding"]` | 不落盘（保存由回合路径负责）、不启动运行、不做准入判断（交给 `prepare_new_run`） | `TeamBindingStore`、`binding_from_metadata`、`persist_message_binding`、`TeamBindingExpired`、`SESSION_TEAM_BINDING_KEY` |
| `service.py` | gateway 注入口面：保存前把引用校验干净（Leader/成员档案存在且未隐藏、成员 ∈ Leader `subAgents` 交集）、盖章服务端字段、冻结快照、内置团队保护与播种、`prepare_new_run` 准入、重启恢复、`a_*` 的 `to_thread` 入口 | 不写 SQL（委托 repository）、不执行任务、不判定运行终态；不读凭据、不解析模型 preset | `TeamService`、`ProfileRecord`/`ProfileDirectory`、`TeamDisabled`/`TeamArchived`/`TeamReferenceError`/`TeamProfileUnavailable`/`CrossSessionForbidden` |
| `scheduler.py` | 调度：整批校验 + DAG 拓扑、团队并发/同成员串行/资源锁、批次幂等、子任务执行与超时、取消与收口（`finalize` 唯一入口）、事件按 seq 增量投递 | 不拼 LLM 请求、不复制 Runner（成员执行一律走 `MemberExecutor`）、不自己造 seq（只读 `last_seq`） | `TeamScheduler`、`TeamRunHandle`、`SnapshotMemberCatalog`、`MemberExecutor`/`MemberCatalog` Protocol、`UNDECLARED_WRITE_RESOURCE` |
| `execution.py` | 契约 §5 的 `execute_member`：把 `TeamExecutionRequest` 的可信上下文转成一次 `SubagentManager.execute_frozen`；`announce=False`；活动转发给 `EventSink` | 不做第二套 LLM 循环、不按 `agentId` 读盘、不聚合结果、不落库 | `TeamMemberExecutor`、`TeamExecutionRequest`、`EventSink`、`NO_EVENTS` |
| `events.py` | 契约 §6/§7 的出口：`task.activity` 节流（0.25s/任务）、按 type 的 data 白名单投影、4KiB 预算裁剪、总线投递（失败只记日志） | 不写库、不分配 seq、不决定任务状态；未知 schema/类型返回 `None`（前端回查快照） | `project_team_event`、`project_run_snapshot`、`TeamRunEventSink`、`TeamRunUpdateEvent`、`bus_publisher`、`EVENT_DATA_FIELDS` |
| `leader.py` | 纯函数装配：`render_role_prompt` 拼团队段、`build_leader_registry` 造新 registry、`resolve_leader_runtime` 解析 preset | 不读 session、不写库、不碰 `AgentLoop` 私有状态（见文件 docstring 第 3-5 行） | `LeaderTurnContext`、`build_leader_registry`、`resolve_leader_runtime`、`render_role_prompt`、`ModelPresetUnavailable`、`TEAM_HIDDEN_TOOLS` |
| `turn.py` | 回合装配本体：`TeamLeaderTurns`（loop 注入点）、`bind_team_turns`、`build_team_turns`（把 service + host 装成完整栈）、`resolve_session_runtime` | 不碰 WebSocket/前端、不实现 `TeamService` 的准入逻辑、不 import loop | `TeamLeaderTurns`、`bind_team_turns`、`build_team_turns`、`resolve_session_runtime`、`AgentLike`/`TeamHost` Protocol、`VISIBLE_TURN_ID_METADATA_KEY` |
| `runs.py` | 三条运行控制 mutation（取消运行/取消单任务/重试）与会话归属检查；重试在落盘后把新运行 `adopt_run` 接回调度器并在后台驱动 | 不放 `TeamService`（这三条要碰事件循环，不能包 `to_thread`）；不复制成功任务、不改旧终态 | `TeamRunController`、`CrossSessionForbidden`（再导出） |

### `__init__.py` 的再导出策略

只有一条 `from nanobot.teams.models import (...)`（第 9-58 行），`__all__`（60-109 行）是它的逐项镜像，
顺序与取值一致。刻意不导出子模块，原因可从 `models.py` 的 import 清单反推：只要 `import nanobot.teams`
不牵连 `database`（sqlite/filelock/`config.paths`）、`scheduler`（`agents.runtime`、`security`）与
`turn`（`agent.subagent`、`agent.tools.team`），领域类型就能被 WebUI 投影层与测试单独引用，也不会与
`nanobot.agent` 的初始化顺序形成环。需要 `TeamService`/`TeamLeaderTurns` 的调用方按模块路径直接 import。

## Leader 回合数据流（入站消息 → 一次团队运行收口）

1. **装配（进程启动）**：`build_team_stack`（`gateway_services.py:461-485`）建 `TeamService`、抢 writer 锁并做
   重启恢复、建 `TeamBindingStore`；随后 `build_team_turns(service=…, host=team_host, workspace=…, run_extension=…)`
   （`gateway_services.py:604-611`，定义 `turn.py:300-379`）装配出 `TeamScheduler`（`turn.py:361-368`）与
   `TeamLeaderTurns`（`turn.py:369-379`）。输入：workspace + service + host；输出：`GatewayServices.team_turns`。
2. **挂注入点**：`bind_team_turns(agent, channels.webui_gateway.team_turns)`（`gateway_runtime.py:799`，定义
   `turn.py:264-269`）写 `agent.team_context_provider = turns`、`agent.team_run_cancel_hook = turns.cancel_runs`。
   输入：`AgentLoop` 与 turns；输出：loop 的两个字段（声明见 `loop.py:477,483`）。`turns is None` 时直接返回。
3. **入站消息进回合管线**：阶段固定为 restore → compact → command → build → run → save → respond
   （`loop.py:2249-2256`），末尾统一收口（`loop.py:2266-2274`）。团队装配只发生在 restore 阶段。
4. **绑定先落地**：`_restore_turn`（`loop.py:2339`）在 USER 回合调 `persist_message_binding(session, msg)`
   （`loop.py:2370`，定义 `binding.py:157-176`）。输入：`msg.metadata` 里服务端暂存层放入的 `team_binding`；
   输出：`session.metadata`，返回 True 才 `sessions.save(session)`（`loop.py:2373`）。
   **顺序不可颠倒**：绑定必须先落盘再取装配——重连/重启后的后续回合只认会话文件里的这份真值，
   不依赖那条入站消息还活着。
5. **向注入点取装配**：`ctx.team = await self._team_context_for_turn(msg, session, ctx.turn_id)`（`loop.py:2374`，
   方法 1168-1183）。输入：msg、session、loop 内部 turn_id；输出：`LeaderTurnContext | None`（provider 为
   `None` 时恒 None，普通聊天一行团队代码都不跑；provider 返回协程则 await）。
6. **绑定是唯一开关**：`TeamLeaderTurns.__call__`（`turn.py:147`）。恢复续接直接 `None`（148-155，契约 §6 禁止
   重放 `team_dispatch`）；`binding_from_metadata` 为空返回 `None`（156-158）；可见轮次 id 取
   `msg.metadata["webui_turn_id"]`，否则用内部 turn_id（160-162）；然后 `scheduler.open_run(session_key, turn_id)`
   （163，`scheduler.py:898-911`）。输入：msg + session；输出：复用已有 `TeamRunHandle` 或 `None`。
7. **起运行**：`open_run` 为 `None` 时先 `await asyncio.to_thread(service.prepare_new_run, team_id,
   revision=binding.team_revision)`（`turn.py:165-167`，`service.py:404-419`：开关/归档/档案可用，失败抛
   `TeamDisabled`/`TeamArchived`/`StorageUnavailable`），再 `scheduler.start_run(...)`（`turn.py:168-179`，
   `scheduler.py:913-966`）：`repository.create_run` 在同事务插运行行并落 `run.started`（`repository.py:430-458`）
   → `_handle_for` 建 `TeamRunHandle` 放进活动表（953-965）。输出：`TeamRunHandle`。
   **顺序不可颠倒**：`open_run` 先于 `start_run`（先查复用再决定新建，否则内部续接会开出第二条运行）；
   `prepare_new_run` 先于 `start_run`（准入失败不留半个运行）。
8. **渲染回合输入**：`_context_for`（`turn.py:186-222`）：`SnapshotMemberCatalog.resolve`（`scheduler.py:119-144`）
   用冻结 profile 与真实工具/技能目录求交得到 Leader 的 `ResolvedAgent` → `render_role_prompt`（`leader.py:80-133`）
   拼 team 段 → 构造 frozen `LeaderTurnContext`：`tool_allow`、`skill_exclude`、`model_id` 全来自快照，
   `dispatch_tool=TeamDispatchTool(handle)`（217-219），`turn_finalizer=lambda delivered: self._finalize(handle, delivered)`
   （221）。输入：handle + 冻结修订；输出：不可变装配输入，回传 loop。
9. **造 registry**：`loop.py:2375-2377` 用 `build_leader_registry(tools, ctx.team)`（`leader.py:136-153`）新建
   `ToolRegistry`：按 `tool_allow` 排序取 base 中存在的工具、跳过 `TEAM_HIDDEN_TOOLS`（`spawn`）、再挂
   `dispatch_tool` 与 `extra_tools`；`base` 不被修改。
   **顺序不可颠倒**：必须先有 `ctx.team` 才能建 registry；且不能在共享 registry 上 unregister（`leader.py:9-10`）。
10. **解析 runtime 与修订戳**：`_build_turn`（`loop.py:2466`）中 `ctx.runtime is None` 且 `ctx.team is not None` 时
    走 `_leader_runtime`（`loop.py:2480-2481`，定义 1184-1189：无 `model_id` 用 `runtime_for_session`，否则
    `resolve_leader_runtime` → `resolver.resolve_preset`，失败抛 `ModelPresetUnavailable`）；随后 2505-2511：
    签名（`f"{team_id}@{revision}"`，`loop.py:147-148`）与 `session.metadata["teamAppliedSignature"]` 不一致时
    清 `provider_state`，再 `_stamp_team_revision` 打戳。
    **顺序不可颠倒**：先清 provider 续接再打戳。反过来的话，续接仍是上一版装配的产物却被记成「本版跑过」；
    且「选定修订」与「实际执行修订」必须分两个键存（`loop.py:1264-1271`），否则一次未生效的编辑会误删续接。
11. **注入 prompt 与技能裁剪**：`_run_agent_loop(..., team=ctx.team)`（调用点 `loop.py:2645-2657`）在 1510-1512
    取 `turn_role_section = team.role_prompt`、`turn_skill_exclude = set(team.skill_exclude)`；普通回合由
    `agent` 提供同一对输入，团队优先。`_resolve_runtime_context_for_turn`（1279-1280）复用同一排除集。
    输入：role_prompt + 排除集；输出：本轮 system 段、技能概览与 `$skill` 的可解析集合。
12. **回合内派活**：模型调 `team_dispatch` → `TeamDispatchTool.execute`（`nanobot/agent/tools/team.py:139`）→
    `TeamRunHandle.dispatch`（`scheduler.py:261-317`：整批校验/DAG、批次幂等、任务预算、落库后建子任务）
    → `_execute`（484-508：成员锁 → `queued` → 资源锁 → 并发信号量 → 取消/超时二次检查）→ `_invoke`
    （542-589：组 `TeamExecutionRequest`、`TeamMemberExecutor.execute_member` → `SubagentManager.execute_frozen`，
    `execution.py:74-107`）。任务快照经 `TeamRunEventSink`（`events.py:160-197`）投影后投总线。
13. **收口**：`_finish_team_turn`（`loop.py:2269-2278`）在正常路径（2266-2274）与异常路径（`except BaseException`，
    2257-2260）都调 `turn_finalizer(delivered)`，`delivered = not suppress_response and bool(final_content.strip())`
    （2263-2265）；`_finalize`（`turn.py:253-261`）→ `handle.finalize`（`scheduler.py:733-746`）：
    关句柄 → `_terminal_status`（748-759：取消 > 未交付 failed > 全成功 completed > 有成功 partial > failed）
    → `set_leader_action("idle")` → `update_run_status` → `_publish_new` 排空事件 → `scheduler.forget`（894-896）。
    输入：`handle` + `delivered`；输出：终态运行快照 + `run.finished` 事件。
14. **取消旁路**：`loop.py:1416-1418` 在停聊天时调 `team_run_cancel_hook` → `TeamLeaderTurns.cancel_runs`
    （`turn.py:182-184`）→ `scheduler.cancel_session`（`scheduler.py:1048-1054`）→ `handle.cancel`（685-696）：
    先落 `cancelling` 再 cancel 子任务；终态由第 13 步的收口写。

## 四条不可破坏的约束

来源：`leader.py:1-15`（契约 §5 的四条边界）+ `turn.py:1-14`（三条口径）。

1. **工具白名单只能靠新建 registry 表达。** `build_leader_registry` 每次造新 `ToolRegistry`，绝不在
   `self.tools` 上 `unregister` 再恢复（`leader.py:9-10`）；否则并发的普通聊天回合会看到团队的裁剪结果。
2. **Team 模式下普通 `spawn` 必须消失。** `TEAM_HIDDEN_TOOLS = {"spawn"}`（`leader.py:28`）在装配时被跳过：
   成员授权来自冻结档案，绕过团队自发子 Agent 等于扩权（`leader.py:11`）。
3. **`team_dispatch` 只由宿主挂上。** 它不在档案白名单里（`leader.py:12`），`_plugin_discoverable = False`
   （`agent/tools/team.py:86`），模型无法自行注册；参数只有 `batchId` 与任务声明。
4. **preset 失效就拒绝起跑。** `resolve_leader_runtime` 捕获 `KeyError/ValueError` 后抛
   `ModelPresetUnavailable`（`leader.py:161-166`），不静默退回默认模型（`leader.py:13-14`）；重试路径同样
   在落盘前查 preset（`runs.py:148-158`）。

`turn.py` 的三条口径是同一件事在回合层的投影：**绑定是唯一开关**（无绑定返回 `None`，第 6 步）、
**一个可见轮次一个运行**（`team_runs_turn_idx` 唯一索引 `database.py:129-130` + `open_run`，第 6-7 步）、
**准入失败就失败**（抛出而非降级为普通聊天，第 7 步）。

## 与契约文档不一致的地方（核对后如实记录）

1. `leader.py:31` 注释写「与契约 §4 的 400 错误码同名」，但 `MODEL_PRESET_UNAVAILABLE` 在契约 §3 的
   「错误映射」段（契约第 168 行），§4 讲的是聊天创建与版本规则。仅注释章节号错，行为一致。
2. `runs.py:1` 写「契约 §5 的三条 run mutation」，而 `teamRuns.cancel` / `teamTasks.cancel` / `teamRuns.retry`
   的接口定义在契约 §3（152-164 行）。同样是引错章节。
3. `database.py` 的表清单比契约 §2（134-142 行）多两张：`team_requests`（203-212，落 `requestId` 幂等响应）
   与 `_schema_meta`（213）。是实现补充，但契约未记载。
4. 契约 §6（234 行）给的是逐条转移表；`repository.update_task_status`（844-869）只校验「目标是非终态、
   当前不是终态」，并不比对该条转移是否被允许（例如 `running → queued` 也能写进去）。终态回退确实被
   `IllegalTaskTransition` 挡住，粗粒度不回退成立，但细粒度状态机没有在仓储层强制。
5. `models.py:286` 的 `TeamLimits` docstring 引「契约 §3.2」，契约里没有 §3.2 这个小节，limits 定义在
   §1（34-40 行）。默认值与范围本身逐项一致（`models.py:288-299`）。
6. 契约 §2 说 DB 层「复用 WritingDatabase 的连接/事务习惯」；`database.py:3-5` 是照抄习惯，但
   `compute_workspace_key`（51-58）在团队域独立实现（注释说明为不依赖写作域），两处口径靠人工保持一致，
   没有共享常量或共享测试锚点。
