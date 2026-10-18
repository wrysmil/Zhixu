---
artifact: plan
route: writing-plans
skills: [writing-plans, source-driven-development, test-driven-development]
skills_evidence:
  - /Users/mima0000/.agents/skills/writing-plans/SKILL.md
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
  - /Users/mima0000/.agents/skills/test-driven-development/SKILL.md
source:
  - .ai-runtime-artifacts/specs/2026-10-18-subagent-observability-spec.md
  - .ai-runtime-artifacts/contracts/2026-10-18-contract-subagent-observability.md
  - .ai-runtime-artifacts/verifications/2026-10-18-subagent-observability-test-spec.md
created_at: 2026-10-18
status: complete
approved: true
approval_evidence: 用户于本会话明确要求「开始执行任务吧」。
dispatch: 2026-10-18-subagent-observability-dispatch.md
tier: 2
---

# 子 Agent 执行详情实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `subagent-driven-development` 或 `executing-plans` 逐任务执行；每项以 RED-GREEN-REFACTOR 推进。

**Goal:** 让 WebUI 中每一次普通子 Agent 调用都有可点击入口，用户可在右侧详情中看到实时状态、公开工具活动与最终结果，并在刷新或重连后恢复。

**Architecture:** 新增独立的普通子 Agent execution ledger，运行时通过现有 `SubagentManager` 生命周期和工具 hook 写入快照/事件；WebUI gateway 复用既有 settings 路由和 MessageBus 投递模式提供只读 HTTP 与实时帧。前端按调用 UUID 合并快照和 seq 事件，详情与文件预览共享右侧 surface 槽位；旧 gateway 继续只显示原有通用工具项。

**Tech Stack:** Python 3.11+、asyncio、SQLite/filelock（仓库既有模式）、React 18.3、TypeScript 5.7、Vite 5、Vitest 2、Radix DropdownMenu；不新增依赖。

**TDD Required:** YES（每个生产代码任务先写失败测试，再做最小实现）。

---

## 文件与职责

- 新增 `nanobot/subagent_executions/`：数据模型、SQLite repository、事件投影与运行时观察器；与 Teams 存储隔离。
- 修改 `nanobot/agent/subagent.py`、`nanobot/agent/tools/spawn.py`、`nanobot/agent/tools/execution.py`：创建 invocation、关联可见 WebUI turn、生命周期与安全工具活动上报。
- 修改 `nanobot/webui/settings_routes.py`、新增 `nanobot/webui/subagent_execution_routes.py`、修改 `nanobot/webui/outbound_projection.py` 与 `outbound_wire.py`：只读 API、能力标志与 WS 帧。
- 新增 `webui/src/features/subagent-execution/`：wire parser、seq reducer、请求客户端、hook 与详情组件。
- 修改 `generic-tool-model.ts`、`ThreadShell.tsx`：从 `spawn` 工具调用关联 invocation；将详情接入唯一右侧 surface 槽位。
- 新增 Python 与 Vitest 测试；最后做真实 gateway/browser 冒烟。

## Task 1：execution 领域模型与持久化（WU-01）

**Files:**
- Create: `nanobot/subagent_executions/models.py`, `repository.py`, `events.py`, `__init__.py`
- Test: `tests/subagent_executions/test_repository.py`, `tests/subagent_executions/test_events.py`

- [ ] **Step 1: Write failing repository and projection tests**

覆盖同名并发 UUID、创建→运行→完成、同一 `activityId` 的开始/结束合并、writer 锁不可用、跨 chat 查询隔离、活动项不被终态历史 limit 截断、重启把旧非终态改为 `interrupted`，并在序列化中放入 token/私有提示哨兵后断言它们不出现。

- [ ] **Step 2: Run tests to verify RED**

Run: `.venv/bin/python -m pytest tests/subagent_executions/test_repository.py tests/subagent_executions/test_events.py -q`

Expected: FAIL，因为 `nanobot.subagent_executions` 尚不存在。

- [ ] **Step 3: Implement minimal ledger**

定义 `ExecutionSnapshot`、`ExecutionActivity`、`ExecutionEvent` dataclasses 和 `ExecutionRepository`。所有事件与对应快照在同一 SQLite transaction 提交，`seq` 每 invocation 单调递增；只投影白名单字段，活动数据限制 4KiB，终态/生命周期不因节流丢失。使用 workspace runtime 的独立库、filelock writer；只有获得 writer 后才执行 interrupted recovery。临时 WebUI chat 使用内存 repository，绝不创建 ledger。

- [ ] **Step 4: Run tests to verify GREEN**

Run: `.venv/bin/python -m pytest tests/subagent_executions/test_repository.py tests/subagent_executions/test_events.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

在 WU 合并后由 Leader 按 Lore commit 规范提交；不要提交 `memory/state.db` 或别的会话产物。

## Task 2：运行时 observation 与 spawn 关联（WU-02，依赖 Task 1）

**Files:**
- Modify: `nanobot/agent/subagent.py`, `nanobot/agent/tools/spawn.py`, `nanobot/agent/tools/execution.py`
- Test: `tests/agent/test_subagent_lifecycle.py`, `tests/webui/test_subagent_execution_runtime.py`

- [ ] **Step 1: Write failing lifecycle tests**

测试 background spawn 返回后 invocation 仍为 queued/running，`wait=true` 期间仍产生 phase/activity，工具失败后恢复完成，模型/空答/max iterations/取消分别产生正确终态；并测试并发工具执行时每个 ContextVar 的 `parentToolCallId` 不串线、profile 预检失败也保存可读 failed execution。

- [ ] **Step 2: Run tests to verify RED**

Run: `.venv/bin/python -m pytest tests/agent/test_subagent_lifecycle.py tests/webui/test_subagent_execution_runtime.py -q`

Expected: FAIL，因为 spawn 尚未创建 invocation 或发布观察事件。

- [ ] **Step 3: Implement runtime bridge**

在 `spawn` 实际派发前创建完整 UUID，使用 RequestContext metadata 的 `webui_turn_id` 作为 `parentTurnId`，并以 ContextVar 捕获每次 tool call 的 `parentToolCallId`。给 `SubagentManager` 注入可选 observer，不改变非 WebUI、旧调用者及 `spawn`/`run_inline` 的字符串语义。`SubagentStatus` phase、runner hook 与 finally 路径统一驱动 terminal result；取消、异常与空答不归类为成功。

- [ ] **Step 4: Run tests to verify GREEN**

Run: `.venv/bin/python -m pytest tests/agent/test_subagent_lifecycle.py tests/webui/test_subagent_execution_runtime.py -q`

Expected: PASS，并保留现有 `tests/teams/test_execution.py` 回归通过。

- [ ] **Step 5: Commit**

由 Leader 用 Lore 格式提交运行时和测试的完整原子变更。

## Task 3：gateway 只读查询与实时帧（WU-03，依赖 Tasks 1–2）

**Files:**
- Create: `nanobot/webui/subagent_execution_routes.py`, `tests/webui/test_subagent_execution_routes.py`, `tests/webui/test_subagent_execution_projection.py`
- Modify: `nanobot/webui/settings_routes.py`, `nanobot/webui/outbound_projection.py`, `nanobot/webui/outbound_wire.py`

- [ ] **Step 1: Write failing API and wire tests**

测试 `/api/subagent-executions`、detail、events 的参数验证、401/403/404/503 错误前缀、cursor 分页、`resetRequired`、无库空列表和权限隔离；测试 `subagentExecutionEventsV1` 只输出安全投影、未知 schema 忽略、聊天/turn 外层字段与 invocation seq 正确。

- [ ] **Step 2: Run tests to verify RED**

Run: `.venv/bin/python -m pytest tests/webui/test_subagent_execution_routes.py tests/webui/test_subagent_execution_projection.py -q`

Expected: FAIL，因为路由和 wire encoder 尚未注册。

- [ ] **Step 3: Implement HTTP/WS adapter**

仿照 Teams 的 settings operation 注入方式接入 repository，只新增认证 GET，不新增 cancel/retry mutation。初次 hydrate 和重连推 snapshot；实时帧以 `subagent_execution_event` 发送。存储不可用只返回 execution 详情不可用，绝不改变实际子 Agent 执行结果；旧 gateway 不声明 capability。

- [ ] **Step 4: Run tests to verify GREEN**

Run: `.venv/bin/python -m pytest tests/webui/test_subagent_execution_routes.py tests/webui/test_subagent_execution_projection.py tests/webui/test_teams_routes.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

由 Leader 提交 gateway adapter、线协议和测试。

## Task 4：前端事件状态与数据恢复（WU-04，可在 Task 3 wire 定稿后并行 UI）

**Files:**
- Create: `webui/src/features/subagent-execution/types.ts`, `parser.ts`, `reducer.ts`, `client.ts`, `useSubagentExecutions.ts`
- Test: `webui/src/tests/subagent-execution-events.test.ts`, `subagent-execution-reducer.test.ts`, `subagent-execution-flow.test.tsx`

- [ ] **Step 1: Write failing parser/reducer/flow tests**

固定 gateway fixtures 测 seq `1,3,3,2,4` 的补拉去重、finished 先于列表返回、终态不能被旧 running 覆盖、同一工具开始/结束只渲染一行、chat 切换拒绝晚到请求、断线后 snapshot 恢复以及旧 gateway capability 回退。

- [ ] **Step 2: Run tests to verify RED**

Run: `cd webui && node node_modules/vitest/vitest.mjs run src/tests/subagent-execution-events.test.ts src/tests/subagent-execution-reducer.test.ts src/tests/subagent-execution-flow.test.tsx`

Expected: FAIL，因为 feature modules 尚不存在。

- [ ] **Step 3: Implement state pipeline**

按「先订阅再 list」加载，缓存早到事件；每调用单独维护连续 seq、补拉链、timeline cursor 与 snapshot revision。detail 先恢复 `throughSeq` 历史再接实时事件；`resetRequired` 显示有限历史说明并停止无限重试。卸载或 chat 切换时取消请求、解除订阅、用 generation guard 拒绝旧响应。

- [ ] **Step 4: Run tests to verify GREEN**

Run: `cd webui && node node_modules/vitest/vitest.mjs run src/tests/subagent-execution-events.test.ts src/tests/subagent-execution-reducer.test.ts src/tests/subagent-execution-flow.test.tsx src/tests/team-flow.test.tsx`

Expected: PASS。

- [ ] **Step 5: Commit**

由 Leader 提交前端数据层和测试。

## Task 5：聊天入口、右侧详情和任务菜单（WU-05，依赖 Task 4）

**Files:**
- Create: `webui/src/components/thread/SubagentExecutionPanel.tsx`, `webui/src/components/thread/SubagentExecutionActivity.tsx`
- Modify: `webui/src/components/thread/activity/generic-tool-model.ts`, `webui/src/components/thread/ThreadShell.tsx`
- Test: `webui/src/tests/subagent-execution-panel.test.tsx`, `webui/src/tests/right-surface.test.tsx`, `webui/src/tests/thread-shell.test.tsx`

- [ ] **Step 1: Write failing interaction and accessibility tests**

测试 background tool return 仍显示 running、点击/关闭/重开不发送 mutation、同名并发入口不串、打开文件后返回 execution、草稿不丢；多任务使用 Radix RadioGroup 时可用 Arrow/Home/End/Enter/Space/Escape/外点，焦点回触发器；单任务没有菜单。再测试桌面 non-modal 右栏不抢焦点，窄屏 dialog trap、Escape 和焦点返回，`role=status` 不重复播报秒表。

- [ ] **Step 2: Run tests to verify RED**

Run: `cd webui && node node_modules/vitest/vitest.mjs run src/tests/subagent-execution-panel.test.tsx src/tests/right-surface.test.tsx src/tests/thread-shell.test.tsx`

Expected: FAIL，因为详情组件和 surface 仲裁尚不存在。

- [ ] **Step 3: Implement UI**

从 spawn tool call 的 `invocationId` 建立可点击的轻量入口；桌面在有效内容宽度满足 panel 440px、聊天至少 420px 时显示非 modal 右栏，否则走 Radix Dialog 抽屉。`ThreadShell` 只保留一个 surface slot（workbench/file/execution/tools）和返回栈。用现有 Radix DropdownMenu 做两行 Agent/任务选择；状态、工具活动和结果仅使用公开字段，不渲染推理、工具参数或输出。

- [ ] **Step 4: Run tests to verify GREEN**

Run: `cd webui && node node_modules/vitest/vitest.mjs run src/tests/subagent-execution-panel.test.tsx src/tests/right-surface.test.tsx src/tests/thread-shell.test.tsx src/tests/generic-tool-model.test.ts`

Expected: PASS。

- [ ] **Step 5: Commit**

由 Leader 提交可见 UI、可访问性交互和测试。

## Task 6：集成验收、浏览器验证与收尾（Leader）

**Files:**
- Create: `.ai-runtime-artifacts/verifications/2026-10-18-subagent-observability-collective-test.md`, `.ai-runtime-artifacts/reviews/2026-10-18-subagent-observability-code-review.md`, `.ai-runtime-artifacts/execution-logs/2026-10-18-subagent-observability-execution-log.md`

- [ ] **Step 1: Add deterministic gateway E2E fixture and its failing test**

让慢工具 fixture 覆盖 background、wait、刷新、断线重连、取消、失败与无工具任务；浏览器从真实 gateway 跑到 Runner，禁止用真实付费模型。

- [ ] **Step 2: Run it to verify RED, then implement only missing integration wiring**

Run: `.venv/bin/python -m pytest tests/webui/test_subagent_execution_e2e.py -q`

Expected: 先因未接通而失败，接通后 PASS。

- [ ] **Step 3: Run targeted and full validation**

Run: `.venv/bin/python -m pytest tests/agent/test_subagent_lifecycle.py tests/webui/test_subagent_execution_runtime.py tests/webui/test_subagent_execution_routes.py tests/webui/test_subagent_execution_projection.py tests/webui/test_subagent_execution_e2e.py -q && ruff check nanobot/ && uv run --no-sync basedpyright`

Run: `cd webui && node node_modules/vitest/vitest.mjs run && node node_modules/typescript/bin/tsc -p tsconfig.build.json --noEmit && node node_modules/eslint/bin/eslint.js --config eslint.config.js src "../nanobot/channels/*/webui/**/*.{ts,tsx}" --max-warnings 0 && node node_modules/vite/bin/vite.js build`

Expected: 全部 PASS；记录既有警告与新增 warning 的差异。

- [ ] **Step 4: Perform real-browser P0 checks and review**

按测试规格的 P0-01 至 P0-29 录入每项结果；至少保存 desktop running/finished/failed、窄屏菜单/抽屉、刷新恢复的真实截图。独立代码审查后修复所有确认问题。

- [ ] **Step 5: Commit**

由 Leader 以 Lore trailers 进行最终提交；不自动推送或合并。

## 计划自检

- Spec 关键路径（后台与 wait、进度、存储恢复、实时/HTTP、单槽位、菜单与无障碍）分别由 Tasks 1–5 覆盖；P0/P1 的总体证据由 Task 6 收口。
- 每个生产任务均以失败测试、RED、最小实现、GREEN 为顺序。
- 不新加依赖；不增加取消/重试 mutation；不迁移现有 Team 执行链。
- 任务间接口字段与契约的 `invocationId`、`parentTurnId`、`parentToolCallId`、`seq`、`subagentExecutionEventsV1` 一致。

## Next

用户已明确要求开始执行。本计划将用 dispatch 将五个工作单元按依赖拆开，并从 runtime ledger 的 RED 测试开始；完成后进入集体验证与审查，不自动推送或合并。
