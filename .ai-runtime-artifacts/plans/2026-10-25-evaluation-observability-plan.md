---
artifact: implementation-plan
route: source-driven-development -> brainstorming -> api-and-interface-design -> superpowers:writing-plans
skills: [source-driven-development, brainstorming, api-and-interface-design, writing-plans]
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
  - /Users/mima0000/.agents/skills/brainstorming/SKILL.md
  - .claude/skills/api-and-interface-design/SKILL.md
  - /Users/mima0000/.agents/skills/writing-plans/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-10-25-evaluation-observability-dispatch.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - harness-kit/project.profile.md
  - harness-kit/context-map.md
  - .ai-runtime-artifacts/specs/2026-10-15-agent-evaluation-spec.md
  - .ai-runtime-artifacts/plans/2026-10-23-evaluation-webui-plan.md
  - .ai-runtime-artifacts/contracts/2026-10-15-contract-agent-evaluation.md
  - .ai-runtime-artifacts/contracts/2026-10-25-contract-evaluation-observability.md
  - .ai-runtime-artifacts/stack/2026-10-25-evaluation-observability-stack.md
  - 用户两轮要求：整理计划，纳入 Token；清楚展示工具与智能体，用画布节点呈现，系统和 HTML 均可看，交付 Demo
  - 用户补充：工具过多时的处理办法
created_at: 2026-10-25
status: completed
approved: true
approval_evidence: "用户在 2026-10-25 单独要求：集成到项目中，后端也得考虑；并明确要求直接在当前分支修改。"
tier: 2
---

# 评估观测台、Token 账本与离线 HTML 报告实施计划

> **For agentic workers:** 实施时按本仓库 Harness 的 orchestration 执行本计划及同 stem dispatch；不因本文件存在而立即编码。每项任务遵循先测试、确认失败、最小实现、确认通过、Leader 审核提交的顺序。独立工作单元使用 Codex 原生子 Agent，不在 Codex App 中假设存在 OMX tmux 运行时。

**Goal:** 让用户清楚看到一次评估由哪些智能体执行、调用了哪些工具、消耗多少 Token、结果如何，并在系统内交互追溯、在离线 HTML 中完整阅读。

**Architecture:** 保留现有评估服务、Trace 与 UsageLedger，以统一规范化读模型连接系统页面和 HTML。先修复聚合与范围口径，再补实际执行身份和父子关系采集，最后接入分层画布、资源表及静态 SVG 报告。大量调用按执行身份和阶段分组，明细分页读取，不把缩小整个大图当作可扩展方案。

**Tech Stack:** Python >=3.11 / aiohttp / Pydantic；React 18.3.1、TypeScript 5.9.3、Vite 5.4.21、Tailwind 3.4.19、Vitest 2.1.9（本机安装版本）。Demo 为原生 HTML/CSS/SVG/JavaScript。正式实现不新增依赖。

**TDD Required:** YES，所有生产代码任务 RED → GREEN；纯视觉数值不写镜像断言，布局用浏览器验证。

## 0. 本轮交付与执行边界

- 用户前一轮已单独要求“整理成一个计划”，后续补充画布、工具/智能体透明度与 Demo。本文件合并这些要求，作为待批准计划。
- 本轮仅创建计划、接口草案、技术栈记录和可交互设计 Demo，不修改业务代码、不运行真实评估、不付费调用模型、不启用新的工具权限。
- Demo：`.ai-runtime-artifacts/research/2026-10-25-evaluation-observability-demo.html`。包含 20 用例运行、完整单用例流程、失败/待复核关系缺失，以及独立的 160 调用压力场景，全部标明模拟数据。
- 已交付两份可单独打开的离线报告样例：`research/2026-10-25-evaluation-run-report-demo.html`（本轮20用例）与 `research/2026-10-25-evaluation-160-calls-report-demo.html`（独立压力场景，全部160条明细）。两者均位于 `.ai-runtime-artifacts/`，由 Demo 的实际下载结果保存。
- 当前工作树存在 `server.py`、`globals.css` 等在途改动。用户明确要求直接在当前分支修改；执行时保存清单、划分文件所有权，不回滚、不混入其他任务。
- 既有评估方案要求 Token 来源、Agent/Judge 分账和工具证据；本次补足可视呈现与数据接线，不改变已批准工具隔离边界。Demo 中 exec 等名称只是说明，不意味着自动授权真实执行。

## 1. 已核对的事实与优先修复点

| 事实 | 来源 | 本次处理 |
| --- | --- | --- |
| 总览指标只来自某一次运行，但未显示来源 | services.py::overview | 显示来源 run、范围和更新时间；优先活跃运行集合，不把取消/超时误标为活跃 |
| 部分聚合读取顶层结果，仓储却把结果放 result | repository.py::list_run_attempts、reports.py::aggregate_run | 内部聚合适配并验证真实持久化结果；不扩大公开列表 payload |
| run_summary 的 terminal_count 取 pending 数 | services.py::run_summary | 用计划数减 pending，验证 queued/running/completed 三态进度 |
| HTML 按 suite 导出；抽屉看起来像当前 run 导出 | RunDetailSheet.tsx、services.py::summary_report | 分开“导出本轮报告”和“数据集历史汇总” |
| 最新用例结论允许旧失败覆盖新通过，按名字合并 | services.py::summary_report | 按 case_id + 确定顺序取最新，保留 revision；历史失败单列 |
| Trace 有工具与模型事件，缺少完整执行父子标识 | schemas.py::TraceEvent、worker.py::make_model_call_observer | 可选增量字段、显式生命周期事件；旧记录显示缺口 |
| 现有 Token 账本按物理调用、role、scope 分开 | usage.py::UsageLedger | 复用口径，补 call ID 与智能体归属，不重复叠加各层汇总 |

## 2. 设计选择与系统页面

| 选择 | 优点 | 局限 | 结论 |
| --- | --- | --- | --- |
| 表格为主，保留文本 Trace | 最轻量、易归档 | 难看清委派与并行关系 | 作为明细基础 |
| 交互画布 + 结构化表格 + 统一读模型 | 一眼看路径，逐次查证据，可离线输出 | 要补身份采集与分组 | 推荐，Demo 采用 |
| 引入通用流程编辑器与外部观测平台 | 可获得编辑器生态 | 增加依赖、部署和模型概念；本需求是观察执行 | 本次不采用 |

运行详情使用宽内容区域或可扩展画布，不继续把全部结果塞入窄抽屉。

| 区域 | 内容与交互 |
| --- | --- |
| 页头 | 数据集、run ID、状态、模型/模式、起止时间、范围；预览与导出分开 |
| 重点指标 | 全集通过率、失败/待复核、唯一执行智能体数、工具调用数、Token；Agent 和 Judge 单列 |
| 执行画布 | 用例切换；节点类型区分智能体活动、工具、模型、Judge、输出；拖动平移、缩放、适应、展开、键盘选择 |
| 节点检查器 | 真实 agent/execution/parent、模型、调用 ID、参数、结果、状态、耗时、Token 和证据引用 |
| 用例结果 | 执行/判分/结论分列；搜索与筛选；点击定位流程或展开完整结果 |
| Token 账本 | 本轮角色分账；按智能体/模型切换；单用例和物理请求下钻；未归属组可见 |
| 数据集页 | 名称、版本、用例数、标签、更新时间及操作表；历史报告明确 suite 范围 |

运行 completed 使用中性“已结束”，通过使用绿色；状态不能仅靠颜色。未知值显示“—”，真实零值显示 0。局部读取尚未完成时，不将已加载部分的数量当作全量指标。

## 3. Token 的验收口径

| 维度 | 必须呈现 / 规则 |
| --- | --- |
| 角色 | Agent、Judge、合计；合计来自唯一物理请求账本 |
| 数量 | input/output/total、physical_requests、missing_usage_calls |
| 来源与完整度 | reported/estimated/mixed/unavailable 与 complete/partial/unavailable 分开 |
| 执行归属 | agent_id、execution_id、模型、阶段；未知进入未归属组 |
| 主/辅助 | 沿用 main/auxiliary/unknown；无法覆盖辅助请求时明确缺口 |
| 重试 | 每次真实 provider 请求单独记账；事件重复回放不重复计费 |
| 工具 | 工具返回文本不额外计 Token；工具内部真实调用模型时，以独立模型请求记录 |
| 金额 | 本次不估价；未取得可靠价格和计费项目时不推算费用 |
| 范围 | run → case → execution → call 可追溯；祖先与子项不能相加成第二份总量 |

Demo 数字校验：本轮 Agent 40,680 + Judge 7,640 = 48,320；44 次物理模型请求；用例 #01 Agent 5,480 + Judge 760 = 6,240，其 5 条请求明细同和。160 调用场景是独立压力演示，不计入这轮用例、Token 或调用总量。

## 4. 工具过多时的明确规则

| 规模 / 操作 | 系统行为 | HTML 行为 |
| --- | --- | --- |
| <=40 工具节点 | 可展示完整单用例图 | 完整静态 SVG + 明细表 |
| >40 工具节点 | 按 execution → 阶段 → 工具类型聚合；默认最多 80 可见节点 | 分组 SVG + 每组数量、成功、失败、耗时统计 |
| 展开一组 | 只读取该组；超过可见预算进入局部图，保留面包屑 | 对应明细分节，200 行一表，目录定位 |
| 失败/超时 | 分组上始终显示异常计数；按状态直接定位真实调用 | 摘要后优先列异常表；重试和原失败并存 |
| 搜索 | 工具名、调用 ID、智能体；搜索在全量可用记录范围执行 | 静态表格保留可搜索的文本，支持浏览器查找 |
| 10,000 调用 | 验证分页与节点上限；不一次挂载全部节点/结果文本 | 分组总览，分段调用表；若达到导出预算，明确总量、导出量和缺失范围 |

聚合依据不是“名字相同就合并”：不同智能体、不同 execution、不同阶段或远隔的顺序块不能跨界合并。计数来自 call_id，不把 started/completed 事件算成两次调用。Trace 不完整时，异常数/耗时的未知部分明确标记。

## 5. HTML 报告放置与内容

系统内“HTML 报告”Tab 展示同一数据快照的静态阅读版；点击“导出本轮 HTML”下载相同内容。数据集页面另放“导出历史汇总”，两个入口不复用模糊名称。

| 顺序 | 单运行报告内容 | 大规模处理 |
| --- | --- | --- |
| 1 | 标题、生成时间、范围、配置与结果摘要 | 标明运行结束和用例通过的区别 |
| 2 | 用例执行流程图 | 每个用例单独标范围；默认分组，不缩成无法阅读的大图 |
| 3 | 参与智能体表 | 身份、父执行、职责、模型、调用数、Token、覆盖 |
| 4 | 工具分组统计与异常明细 | 展示各组总数和异常数，异常优先 |
| 5 | Agent/Judge Token 账本 | 来源、样本覆盖、未归属情况和分母 |
| 6 | 用例结果及逐次调用表 | 明确每表范围，完整记录分段 |
| 7 | 断言、评分与脱敏证据 | 原生 details 可辅助展开；关键证据不能仅藏于其中 |
| 8 | 指标口径、范围和缺口 | 未采集、预算截断、历史数量限制都可见 |

视觉：浅灰底、白色版心、克制的绿色品牌色、紫色 Judge、蓝色工具；浅色表头、数字右对齐/等宽数字、合适行高。所有结构在无脚本、离线场景可读。打印采用重复 thead、避免表行拆页、流程分节与长文本换行；不依赖远程字体或 CDN。

## 6. 文件责任图

| 工作单元 | 修改 / 新增文件 | 责任 |
| --- | --- | --- |
| WU-01 | `nanobot/evaluation/{repository,reports,services}.py`；`tests/evaluation/{test_reports,test_services,test_repository_reads}.py` | 聚合适配、进度/来源/历史最新口径 |
| WU-02 | `nanobot/evaluation/{schemas,worker,runtime_factory,usage,ipc}.py`；`nanobot/agent/{hook,subagent}.py`；`nanobot/teams/{execution,events}.py`；`nanobot/webui/gateway_services.py`；必要的 `nanobot/llm_usage/{context,models}.py` | 真实执行身份、父子关系、请求 ID 和 observer 传递 |
| WU-03 | 新增 `nanobot/evaluation/observability.py`；`nanobot/evaluation/{evidence,schemas,server,services}.py`；新增 `tests/evaluation/test_observability.py`，修改 `test_routes.py` | 统一图/调用/Token 读模型与分页 API |
| WU-04 | `webui/src/lib/evaluation-api.ts`；新增 `evaluation/{ExecutionCanvas,ExecutionInspector,UsageLedgerTable}.tsx`、`evaluation/execution-graph.ts`；修改既有评估组件及 SettingsPage；十语言 common.json；相关 tests | 页面、画布分组与联动、Token/数据集表 |
| WU-05 | 新增 `nanobot/evaluation/report_view.py`；`reports.py`、`server.py` 的 HTML 路由；`tests/evaluation/test_reports.py`、`test_routes.py` | 自包含报告、静态 SVG、打印与预算 |
| WU-06 | `tests/evaluation/test_end_to_end.py`；新增 `webui/src/tests/evaluation-observability.test.tsx`；verifications/reviews/execution-logs | 真实受控链路、回归、视觉与安全尾盘 |

存在共享文件的 WU 串行整合；WU-04 与 WU-05 在 WU-03 契约稳定后并行，后端路由由 WU-05 单独负责。不得并行争写 tests/evaluation/conftest.py；扩展 fixture 在各自测试文件局部定义。

## 7. 工作单元与测试步骤

### WU-01：先让已有数字与报告范围可信

- [ ] **Step 1：写失败测试。** 在 test_reports.py 加 `test_normalize_nested_attempt_metrics`，用嵌套 result 的 usage/timing/judge 和冻结 case_snapshot 验证非零 Token、耗时、维度评分；补 null、不合法 verdict 和未授权 snapshot 暴露测试。在 test_services.py 增加 queued/running/completed 的 terminal_count，以及 overview 返回 metrics_run_id；在历史报告测试覆盖“新通过旧失败”“同名不同 ID”“同 ID 改名/新 revision”“超过100轮范围”。

```python
def test_normalize_nested_attempt_metrics():
    from nanobot.evaluation.reports import aggregate_run, normalize_attempt_for_metrics
    row = {"verdict": "passed", "result": {
        "timing": {"agent_ms": 125},
        "usage": {"agent": {"input_tokens": 10, "output_tokens": 5,
            "total_tokens": 15, "physical_requests": 1, "iterations": 1,
            "missing_usage_calls": 0, "source": "reported", "coverage": "complete"}},
    }}
    metrics = aggregate_run([normalize_attempt_for_metrics(row)], 1)
    assert metrics["usage"]["agent"]["total_tokens"] == 15
    assert metrics["agent_duration"]["median_ms"] == 125
```

- [ ] **Step 2：运行并确认失败。** `.venv/bin/python -m pytest tests/evaluation/test_reports.py tests/evaluation/test_services.py tests/evaluation/test_repository_reads.py -q`。新增测试应因缺适配、错误进度或错误历史口径失败，先隔离环境/依赖失败。
- [ ] **Step 3：最小实现。** reports.py 新增 `normalize_attempt_for_metrics(row)`，只提取既有断言、评分、时间和 usage 字段，保留 case_snapshot 供指标计算。repository.list_run_attempts 增加默认 False 的内部 include_snapshot 开关，API 默认不泄露快照；services 的聚合入口统一使用内部适配。terminal_count=planned-pending；overview 返回 metrics_run_id 和范围。suite 最新按 case_id 关联，按确定时间顺序保留第一条，revision/name 展示冻结值；旧失败另列，不覆盖最新。
- [ ] **Step 4：运行并确认通过。** 重跑 Step 2，增加 `tests/evaluation/test_routes.py` 检查 API 形状、快照未暴露、JSON/CSV 兼容。确认原有零分母/缺用量测试仍通过。
- [ ] **Step 5：Leader 审核提交。** 仅提交本 WU；Lore intent 为“让评估展示对应真实持久化结果”，Tested 填实测命令与结果。

### WU-02：补真实智能体和物理调用的关联证据

- [ ] **Step 1：写失败测试。** 在 test_worker.py / test_usage.py 验证工具前后事件共享 call_id、模型重试有不同 model_call_id、分页重复事件不重复统计；在子智能体和团队既有测试中验证真实 parent_execution_id。加入并行两个 agent 使用相同局部工具 ID、不存在 agent 档案、旧事件无字段、observer 异常、取消中缺 completed 的用例。

```python
def test_physical_model_call_identity_is_not_round_identity():
    from dataclasses import replace
    from nanobot.evaluation.worker import make_model_call_observer
    from nanobot.llm_usage.models import LLMCallRecord
    captured = []

    class Recorder:
        def emit(self, event_type, **kwargs):
            captured.append(kwargs["payload"]["model_call_id"])

    observer = make_model_call_observer(Recorder(), role="agent", scope="main")
    first = LLMCallRecord(started_at_ms=0, duration_ms=2, provider="demo", model="demo",
        source="user", stream=False, finish_reason="stop", model_call_id="m1")
    observer(first)
    observer(replace(first, model_call_id="m2"))
    assert captured == ["m1", "m2"]
```

- [ ] **Step 2：运行并确认失败。** `.venv/bin/python -m pytest tests/evaluation/test_worker.py tests/evaluation/test_usage.py tests/evaluation/test_worker_orchestration.py -q`；新增执行关联测试必须指向当前缺失的身份或调用标识。
- [ ] **Step 3：最小实现。** 由每次真实执行创建 execution_id，将真实父执行上下文传给子 Agent / team runner；observer 从该上下文获得身份。call ID 在真实 provider request 边界生成，不能在完成回调时按 Token 或时间拼凑。旧事件字段可选，未归属保留 null；新增生命周期事件同步 schema/IPC。若共用 provider，按 execution 上下文分流 observer，禁止全局覆盖和重复挂监听器。只增加观测，不开启原来禁止的子智能体/工具能力。
- [ ] **Step 4：运行并确认通过。** 重跑 Step 2，加现有子智能体/团队目标测试和 `tests/evaluation/test_targets.py`。覆盖 observer 解绑、异常 cleanup、取消、并发与 Judge 零工具权限。保留事件来源与用量来源两个概念。
- [ ] **Step 5：Leader 审核提交。** Lore intent 为“保留真实执行归属以支持可追溯评估”，Scope-risk=moderate；列明未覆盖的执行目标，不能写“全部完整”掩盖缺口。

### WU-03：统一读模型、分组和分页接口

- [ ] **Step 1：写失败测试。** 新建 test_observability.py，覆盖事件重放、乱序终态、缺起始/结束、父节点缺失、不合法关联、自环、同名工具不同执行、重试、多页更新。数据规模至少 160 与 10,000 次调用；验证分组统计全量，首屏只读分页，Token 总量不随分页/分组改变。

```python
def test_unknown_parent_does_not_invent_a_delegation():
    from nanobot.evaluation.observability import build_execution_view
    view = build_execution_view([{
        "event_id": "e1", "seq": 1, "type": "tool.started", "call_id": "t1",
        "payload": {"name": "read_file", "arguments": {"path": "input.txt"}},
    }])
    assert view["coverage"]["identity"] == "unavailable"
    assert not any(edge["kind"] == "delegation" for edge in view["edges"])
```

- [ ] **Step 2：运行并确认失败。** `.venv/bin/python -m pytest tests/evaluation/test_observability.py tests/evaluation/test_routes.py -q`，确认缺新模块/接口或旧数据归属逻辑导致新断言失败。
- [ ] **Step 3：最小实现。** observability.py 提供 `unique_model_calls(events)`、`build_execution_view(events)`、分组与角色/智能体/模型聚合；复用 evidence.py 工具开始/结束归并语义。按契约接入 execution、tool-calls、usage 三个 GET API。错误沿用服务中间件；Trace budget、sealed 与 has_more 保持真实。group ID 不是 agent ID，祖先聚合不加入 totals。禁止从相邻时间推断跨执行的委派边。
- [ ] **Step 4：运行并确认通过。** 重跑 Step 2，加 `tests/evaluation/test_trace.py tests/evaluation/test_auth.py`。验证非法分页、跨 attempt ID、无鉴权、空数据、部分 Trace、超大参数截断标记以及任意工具文本不被执行。
- [ ] **Step 5：Leader 审核提交。** Lore intent 为“让页面和报告共享同一套执行证据”，附接口草案实际变更差异；此后固定 WU-04/05 消费字段。

### WU-04：系统中的画布、表格与 Token 下钻

- [ ] **Step 1：写失败测试。** evaluation-api.test.ts 测新接口分页与错误；execution-graph.test.ts 验证 stable node IDs、合并更新、40/41 个节点分组边界、80 可见预算、执行阶段隔离；evaluation-observability.test.tsx 测节点选中/键盘、搜索失败工具、翻页定位、用例切换清空旧数据、缺轨迹提示、null 和 0 区分及 Token 不重复。

```typescript
it("does not merge the same tool across executions", () => {
  const groups = groupToolNodes([
    { id: "t1", execution_id: "a", phase_id: "p1", tool_name: "web_fetch" },
    { id: "t2", execution_id: "b", phase_id: "p1", tool_name: "web_fetch" },
  ]);
  expect(groups).toHaveLength(2);
});
```

- [ ] **Step 2：运行并确认失败。** `cd webui && ./node_modules/.bin/vitest run src/tests/evaluation-api.test.ts src/tests/execution-graph.test.ts src/tests/evaluation-observability.test.tsx`，失败应来自未实现的 UI/归并行为。
- [ ] **Step 3：最小实现。** 按 Demo 视觉复用主题 token 和现有组件；ExecutionCanvas 负责视口/选择，execution-graph.ts 负责稳定图与分组，ExecutionInspector 负责调用明细，UsageLedgerTable 负责资源分账。run/case/execution 范围独立显示。大分组分页，响应迟到不能覆盖新选择；未加载详情不提前复制大 payload。窄屏将 inspector 放到图下并允许局部图缩放，表格在自身容器滚动。升级数据集列表为表格，保留导入错误/下载反馈。文案进入现有十语言，`quality` 按实际数组契约修正 TypeScript 类型。
- [ ] **Step 4：运行并确认通过。** 重跑 Step 2，加 evaluation-settings/i18n 测试、tsc 和 lint。浏览器验证当前默认宽度、390px、宽桌面布局；真实受控数据与模拟 Demo 分别标记。画布交互不能修改运行数据。
- [ ] **Step 5：Leader 审核提交。** Lore intent 为“让评估执行过程可直接定位和比较”，附截图与未验证清单。

### WU-05：单运行报告和数据集历史报告

- [ ] **Step 1：写失败测试。** test_reports.py 覆盖 run/suite 范围不同、Token 各层同和、无 JavaScript 可读、SVG 文本/属性转义、参数脱敏、缺数据不补零、160 工具分组摘要、明细总数/截断说明、空报告；test_routes.py 覆盖单运行 HTML 路由和下载 MIME、无权限/不存在/非法 ID。

```python
def test_report_never_embeds_untrusted_markup():
    from nanobot.evaluation.report_view import render_execution_report
    html = render_execution_report({
        "scope": {"kind": "run", "run_id": "r1"},
        "title": '<img src=x onerror="alert(1)">',
        "generated_at": "2026-10-25T06:35:00Z",
        "metrics": None, "attempts": [], "executions": [],
    })
    assert "<img" not in html and "&lt;img" in html
    assert "<script" not in html
    assert '<meta name="viewport"' in html
```

- [ ] **Step 2：运行并确认失败。** `.venv/bin/python -m pytest tests/evaluation/test_reports.py tests/evaluation/test_routes.py -q`。新路由、结构化报告或离线图缺失应造成新测试失败。
- [ ] **Step 3：最小实现。** report_view.py 消费 WU-03 读模型生成报告，reports.py 保留兼容入口；server.py 增加单运行 report 路由。报告采用静态 SVG、语义 thead/tbody、scope、右对齐数字、报告版心和 print CSS。大工具量生成分组概览＋异常表＋分段明细；输出预算到达时显示已导出/总量，不谎称完整。suite 报告保留历史范围与最新结论修复。下载结构与系统预览来自相同快照，关键数据无需执行脚本。
- [ ] **Step 4：运行并确认通过。** 重跑 Step 2，加已有 JSON/CSV 测试；用浏览器下载并重开，检查无外部资源、关键文本可读和报告标题。打印预览核对表头重复、长文本、空态和大图分页；仅有 CSS 不算已经证明打印效果。
- [ ] **Step 5：Leader 审核提交。** Lore intent 为“让评估报告可离线复核和归档”，列出真正执行过的打印/浏览器检查。

### WU-06：集成与交付验证

- [ ] **Step 1：补集成失败场景。** test_end_to_end.py 使用 fixture provider 与文件工具，不访问真实网络；模拟父 Agent、两子执行、一次工具失败重试、一次 Judge 错误、usage 缺失和取消。验证前端/报告 totals、call IDs、case verdict、覆盖说明对应同一持久化快照。
- [ ] **Step 2：运行目标集成测试并确认暴露缺口。** 如果场景在前五个 WU 后已通过，记录“既有行为已满足”；不得人为制造失败。缺口未复现前不改代码。
- [ ] **Step 3：只修复复现缺口。** 回派给原责任 WU，保持 observer 生命周期、分页取消、脱敏和范围不变量；不扩张工具权限、不替换业务执行路径。
- [ ] **Step 4：运行完整验证并保留证据。**

```bash
.venv/bin/python -m pytest tests/evaluation -q
.venv/bin/python -m ruff check nanobot/evaluation nanobot/agent/hook.py nanobot/agent/subagent.py nanobot/teams nanobot/webui/gateway_services.py nanobot/llm_usage
.venv/bin/python -m basedpyright
```

```bash
cd webui
./node_modules/.bin/vitest run
./node_modules/.bin/tsc -p tsconfig.build.json --noEmit
npm run lint
npm run build
```

执行前重新取得当前测试基线。历史记录中的通过/失败数量只作上下文，不当作本轮结果；依赖/环境不可用时明确原因和替代检查，不标绿。涉及 gateway/team observer 的变更补 gateway 启停和 observer 解绑冒烟。

- [ ] **Step 5：Leader 尾盘。** 运行 Harness 检查、集体测试、独立代码/安全审查并落盘；核对 160 / 10,000 调用性能行为、390px/桌面、键盘可达、导出离线与打印；按 Lore 提交，既有未提交工作不混入。未经额外指令不 push、不建 PR。

## 8. 自检与验收清单

- [ ] 每个图中节点可定位到真实事件，真实关系缺失不补画。
- [ ] 主智能体两次活动仍计一个身份，Judge 不混入执行 Agent 数。
- [ ] 工具名、调用者、参数、结果、状态、耗时、调用标识、证据引用均可查看。
- [ ] 分页不丢失、不重复；失败重试保留全部实际调用。
- [ ] 160 调用默认 4 个演示分组，真实实现按 execution/阶段归并；10,000 调用不全挂载。
- [ ] 页面、HTML、角色、用例、智能体和请求的 Token 汇总可对账；未知与零不同。
- [ ] 运行 completed 与评估 passed 不混淆；overview 清楚标记来源。
- [ ] 同名不同 case 不合并；历史失败不覆盖最新通过；版本和统计范围可见。
- [ ] 本轮报告与套件历史报告明确区分；离线 HTML 无脚本仍保留主要证据。
- [ ] 关键路径先测试后实现；视觉变化用截图验证，不写复制 CSS 的测试。
- [ ] 旧数据无执行关系时可用；无新增依赖、无改工具权限、无密钥入报告。

计划自审：范围覆盖了用户的页面/表格/HTML/Token/工具/智能体/节点画布/大量工具问题。接口以增量契约为准；生产实现尚未开始。正式编排前 WU-02 需精读既有 subagent/team 生命周期，若 observer 接线跨越列出的边界，先由 Leader 更新接口和工作单元责任。

## 完成情况（2026-10-25）

- WU-01/02/03/04/05/06 全部落地：后端读模型与四个新接口、离线 HTML 报告渲染器与路由、
  前端执行画布 / 工具明细 / Token 账本 / 报告导出、总览指标来源标注。
- 接口形状以 `contracts/2026-10-25-contract-evaluation-observability.md` 附录 A 为准，前后端一致。
- 验证证据见 `verifications/2026-10-25-evaluation-observability-verification.md`：
  后端 810 个用例通过（6 个既有失败与本次无关）、前端 1893 个用例通过、tsc/build 通过、
  四个新接口真实 HTTP 联调通过、浏览器验证画布与检查器与报告渲染通过。
- 未做：接真实评估系统跑一轮真实评估（需要真实模型调用与人工确认），以及报告里的成本估算
  （缺少可靠价格来源，本轮刻意不推算）。

## Next

- 查看交互 Demo 和本计划，可直接提出样式、流程或统计字段的修改意见。
- 计划确认后，说“开始实现”或“执行”，进入 Harness 编排与分组验证。
- 本轮停在计划与演示交付；不自动将模拟页面接入真实评估系统。
