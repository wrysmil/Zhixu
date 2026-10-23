---
artifact: review
type: security
route: orchestration:dispatcher-workflow
skills: [orchestration, security-and-hardening]
feature: agent-team
phase: G1+G2 尾盘安全审查
baseline: 2700025
head: 03377c4
branch: harness/wt-agent-team
scope: WU-01 / WU-02 / WU-03 / WU-08（nanobot/teams/*、nanobot/agent/subagent.py、webui teams 客户端与管理页）
status: reviewed-with-fixes
reviewer: Leader（自审，见「审查方式与局限」）
created_at: 2026-10-15
---

# Agent Team G1+G2 安全审查

## 审查方式与局限

**未派独立 security-auditor 子代理。** 用户在本会话中明确拒绝了一次 `Agent`（子代理派发）调用，
`orchestration` skill 的禁止项亦含「派发子 Agent 做全项目编排」。尾盘要求的「并行扇出
reviewer + security-auditor」被替换为 **Leader 分离式自审**：安全审查在实现完成之后单独进行，
重新读源码取证，每条结论都对应一次可复现的 `grep` / `pytest` 输出。

局限：自审无法覆盖「作者本人对契约的误读」。本文件结论只作为 G1+G2 阶段性口径，
**WU-11（Task 11）必须补一次独立安全审查**，且要把本文件未触及的 Task 4–7、9 的新增攻击面
（Leader 提示装配、mutation 白名单、调度与取消、事件推送）一并纳入。

严重度：High = 可越权/可损坏数据，必须修；Medium = 有条件可利用或明显加固缺口；
Low/INFO = 知会或后续 GROUP 处理。

## 1. 已发现并修复

| # | 严重度 | 主题 | 处置 |
| --- | --- | --- | --- |
| S1 | **High** | `team_events` / `team_dispatches` 两张子表没有 `workspace_key` 列，而 `list_events`、`events_page`、`get_dispatch`、`record_dispatch` 直接按 `run_id` 查询。两个 `workspace_key` 共用同一份库文件时（备份恢复、显式 `db_path`、迁移），只要知道或猜到 `run_id` 就能读到别的工作空间的完整事件流（含任务指令、成员产出摘要），`record_dispatch` 还能往里写派单记录；`DispatchKeyConflict` 异常本身会泄露「该 batch 存在」。对应验收 **A18**。 | **已修** `03377c4`：新增 `_run_in_scope()` / `_require_run_scope()`，所有子表入口先过守卫；`get_dispatch` 越界时返回 `None`（与「不存在」不可区分，不泄露存在性）；守卫置于幂等预检**之前**。回归测试 `test_run_child_tables_are_scoped_to_the_owning_workspace` 先见红（`DID NOT RAISE RunNotFound`）后见绿，`pytest tests/teams -q` → 284 passed。 |
| S2 | Medium | `prune_activity_events` 按 `type='task.activity'` 全库 `DELETE`，跨工作空间生效 —— 是**写**越权，比 S1 更严重，但当前无任何调用方（dead code）。 | **已收口** `03377c4`：两处 DELETE 加 `run_id IN (SELECT run_id FROM team_runs WHERE workspace_key = ?)`。**无专属测试**（构造活动事件需要 WU-07 的事件语义），WU-07 接线时必须补测。 |

## 2. 逐项核对（未发现问题的部分）

| 面 | 核对方式 | 结论 |
| --- | --- | --- |
| SQL 注入 | `grep -rn 'execute(f"' nanobot/teams/` | 全仓仅一处 f-string SQL：`database.py:262` 的 `PRAGMA busy_timeout = {int(self.busy_timeout_ms)}`，值经 `int()` 强转，非客户端可控。其余 SQL 全部参数化绑定（含游标 `_decode_cursor` 的分页条件）。S2 新增的 `own_runs` 片段是模块内字面量常量，不拼接任何外部值。 |
| 工作空间作用域键 | 读 `compute_workspace_key` | `sha256(Path(workspace).expanduser().resolve())[:16]`，由**服务端路径**推导，客户端无法伪造；`teams` 主键 `(workspace_key, team_id)`，`team_runs` / `team_tasks` / `team_requests` 均带 `workspace_key` 且读路径（`_read_team_row` / `_read_run_row` / `_read_task_row` / `get_request`）都带该条件。子表是唯一缺口，见 S1。 |
| 服务端所有权字段 | 读 `service.py:114-153` | 创建走 `from_create_payload`，携带 `revision`/`createdAt`/`updatedAt` 直接拒绝；更新路径剥离这三个字段后由服务端重新盖戳（`revision = expected + 1`、`createdAt` 沿用旧值）。客户端无法自增修订或伪造时间。前端 `toCreatePayload()` 与之对齐，并有 `team-api.test.ts` 钉住。 |
| 修订 CAS / 幂等 | 读 `save_team`、`record_dispatch`、`get_request` | 修订推进按 rowcount 判定，冲突映射 `TEAM_REVISION_CONFLICT`；创建时 `IntegrityError` 亦归为修订冲突（见遗留 L2 的文案问题）；幂等键 `team_requests` 以 `(workspace_key, request_id)` 为键，跨工作空间不共享重放缓存；事件 `seq` 与业务写在同一事务同一连接内递增，`last_seq` 是唯一来源。 |
| 历史不可改写 | `grep -n "CREATE TRIGGER" nanobot/teams/database.py` | `team_revisions_no_update` / `team_revisions_no_delete`（append-only）与 `team_tasks_ownership_no_update`（`run_id`/`member_id`/`agent_id` 不可改）由 SQLite 触发器强制；`test_task_ownership_is_immutable_after_create` 断言抛 `IntegrityError`。任务不能事后被改挂到别的 run 或别的成员。 |
| 资源上限 / DoS | 读 `_resolve_limit`、`_validate_event_limit` | 列表 limit 必须在 `MIN_LIST_LIMIT..MAX_LIST_LIMIT`，事件 limit 必须在 `1..MAX_EVENT_LIMIT`，`bool` 被显式排除（防 `True == 1` 绕过）；`TeamLimits` 各字段在 models 层做区间校验。超限是**拒绝**而不是静默截断。 |
| 库文件路径 | `grep -rn "db_path=" nanobot/` | 团队域生产代码没有任何调用方传 `db_path`；库路径唯一出口是 `get_teams_dir(workspace)`。`TeamDatabase(..., db_path=...)` 是**仅测试**参数（模块 docstring 已声明）。→ 见遗留 L1。 |
| 读路径不落盘 | `test_reading_an_empty_workspace_creates_no_directory` | 只读调用不在用户磁盘上创建 `teams/` 目录，避免探测型请求污染工作区。 |
| 密钥与配置 | 本批改动面 | 未读取 `~/.nanobot/config.json`、未触碰任何凭据、未新增依赖、测试不发真实网络请求（`FakeProfileProvider` 是内存替身，fixture 是静态 JSON）。未提交 `.env`。 |
| 前端 XSS | 读 `TeamsView.tsx` / `TeamEditorDialog.tsx` | 无 `dangerouslySetInnerHTML`、无 `innerHTML`、无 `eval`；服务端错误文案与团队名/描述/角色全部走 React 文本节点渲染（自动转义）。 |
| 前端凭据处理 | 读 `lib/teams/api.ts` | 读请求经 `getToken()` 取 Bearer，走既有 `request()`；写操作一律走已认证 WS 的 mutation 白名单，不在 URL/query 里带 token；错误只暴露 `{error: string}` 信封与固定错误码前缀，不透传堆栈。 |
| 子执行权限 | `execution.py` + `tests/teams/test_execution.py` | 工具授权为**空即空**：`tool_names is None` 直接拒绝，空集合不会被解释成「全量」（对应 A19）。成员执行使用**冻结档案快照**，运行中改 Agent 档案不影响活动 run（A15）。 |

## 3. 遗留与移交（本批不修，需在后续 GROUP 处置）

| # | 严重度 | 内容 | 移交 |
| --- | --- | --- | --- |
| L1 | Medium | `TeamDatabase(db_path=...)` 是公开 kwarg。**Task 5 的 API/路由层绝不能让请求体、query 或 header 影响 workspace 或 db 路径** —— 否则 S1 的修复会被上层绕过（客户端可直接指定别人的库文件）。 | WU-05 实现约束 + WU-11 独立审查必查项 |
| L2 | Low | 创建团队时撞已存在 id，服务端把主键冲突映射为 `TEAM_REVISION_CONFLICT`，前端显示「已被其他改动更新，请重新加载」。语义可接受但对「新建重名」含糊；需要 `TEAM_ID_TAKEN` 口径或前端预判。 | WU-05 / WU-11 |
| L3 | Medium（设计面） | 团队 `instructions`（≤8000 字符）与成员 `role` 是自由文本，Task 4 会把它们装进 Leader 的系统提示 —— 这是**设计内的信任配置**（等价于 Agent 档案），但由此产生一条硬约束：**成员的产出（任务结果、摘要）绝不能回流去修改团队定义、成员授权或工具集**，否则一个被提示注入的成员就能自我提权。 | WU-04 / WU-06 实现约束；WU-11 审查 |
| L4 | Low | 事件表持久化任务指令与成员产出摘要（用户内容），保留策略靠 `prune_activity_events`，而它目前没有调用方 —— 即数据只增不减。 | WU-07 接线（同时补 S2 的测试） |
| L5 | INFO | 契约 §6 明确「暂不支持多 gateway 同 workspace 调度」，由 writer 独占锁（`WriterBusy` → 503）保证。锁文件与库文件同目录同生命周期；本次未做跨进程并发压测。 | WU-11 端到端验证 |
| L6 | INFO | 基线既有失败（`test_mcp_reconnect_crash`、`tests/agent` 4 处 F401、`test_serve_*`）已在原始代码 `2700025` 上复现，非本批引入；本批未放宽任何既有校验去「修绿」它们。 | WU-11 统一归因 |

## 4. 结论

G1+G2 范围内**无未处置的 High**。唯一的 High（S1，跨工作空间读取事件与派单）已修复并带先红后绿
的回归测试；Medium 的 S2 已在 SQL 层收口但缺专属测试（dead code）。前端本批只落地组件与客户端
通道，**尚未接通服务端**，因此本批的安全结论不覆盖端到端授权链 —— mutation 白名单、HTTP 读路由
鉴权、绑定与取消的真正校验都在 Task 5–7，届时必须重跑一次独立安全审查（见「审查方式与局限」）。
