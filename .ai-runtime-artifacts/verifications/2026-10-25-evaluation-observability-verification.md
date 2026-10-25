---
artifact: verification
route: verification-before-completion
plan: .ai-runtime-artifacts/plans/2026-10-25-evaluation-observability-plan.md
contract: .ai-runtime-artifacts/contracts/2026-10-25-contract-evaluation-observability.md
created_at: 2026-10-25
status: complete
scope: WU-01..WU-06
---

# 评估观测台集成验证

环境：分支 `harness/agent-evaluation-calibration`（用户要求直接在当前分支改，未建 worktree）。
验证数据：本地伪造的评估运行（2 用例 / 63 次工具调用 / 42 次物理模型请求），
**未调用任何真实模型、未产生任何外部费用**。

## 1. 自动化验证

| 命令 | 结果 | 判定 |
| --- | --- | --- |
| `.venv/bin/python -m pytest tests/evaluation -q` | `6 failed, 810 passed` | 通过（6 个失败是 HEAD 既有基线，见 §4） |
| `.venv/bin/python -m ruff check nanobot/evaluation nanobot/agent/hook.py nanobot/agent/subagent.py nanobot/teams nanobot/webui/gateway_services.py nanobot/llm_usage` | `All checks passed!` | 通过 |
| `webui: ./node_modules/.bin/vitest run` | `149 passed \| 2 skipped`（1893 passed） | 通过（基线为 `1 failed \| 148 passed`） |
| `webui: ./node_modules/.bin/tsc -p tsconfig.build.json --noEmit` | 无错误 | 通过 |
| `webui: npm run build` | `✓ built in 5.92s` | 通过 |
| `webui: npm run lint` | 3 errors | 既有基线（`evaluation-status.tsx` ×2、`browser-url.ts` ×1，均非本次改动文件） |
| `.venv/bin/python -m basedpyright` | 2385 errors（全仓） | 既有基线：仓库在 strict 模式下本就不干净；本次新增文件 `observability.py` 68 条、`report_view.py` 50 条，与既有模块（如 `services.py` 101 条）同一量级，错误全部是 `Any` 造成的 partially-unknown 噪声 |

新增测试文件：
- `tests/evaluation/test_observability.py`（9 个用例，读模型口径）
- `tests/evaluation/test_observability_routes.py`（20 个用例，四个新路由的鉴权/校验/形状）
- `tests/evaluation/test_report_view.py`（27 个用例，离线报告渲染）
- `webui/src/tests/evaluation-observability.test.tsx`（2 个用例，画布/工具表/导出）
- `webui/src/tests/execution-graph.test.ts`（4 个用例，分组与可见节点预算）

## 2. 接口联调（真实 HTTP，非 mock）

以真实 aiohttp 应用 + 真实 SQLite 仓储 + 真实 Trace 落盘数据验证：

| 接口 | 实测结果 |
| --- | --- |
| `GET /attempts/{id}/execution` | 85 节点 / 168 边；`coverage = {trace: complete, identity: complete, usage_attribution: complete}` |
| `GET /attempts/{id}/tool-calls?limit=5` | `total_count=63`，当前页 5 条，`next_after_key` 可翻页 |
| `GET /runs/{id}/usage` | 42 次物理请求，input 54,600 / output 21,840 / total 76,440，coverage complete |
| `GET /runs/{id}/report` | 201,742 字节 HTML；无 `<script>`；`Content-Disposition: attachment; filename="<run_id>-report.html"` |

未登录访问四个新路由均返回 401 `AUTH_REQUIRED`；非法 `after_seq`/`limit`/`group_by`/`status` 返回 422 `VALIDATION_ERROR`；
不存在的运行返回 404。

## 3. 浏览器验证（Chrome / in-app browser，真实页面）

1. **评估分区可达**：发现 `#/settings?section=eval` 被路由解析丢弃（`webui/src/workspace/routes.ts` 的
   `SETTINGS_SECTION_KEYS` 漏了 `eval`），导致刷新后回到概览、评估页根本进不去。
   已补 `eval` 并加回归用例 `workspace-routes.test.ts`。
2. **总览页**：显示计划 2 / 通过 1 / 失败 1 / 通过率 50%，并新增来源行
   「指标来源：run 9a75bf26 · single run」——总览数字不再是无来源的孤立指标。
3. **运行详情面板**：宽面板（`sm:max-w-4xl`），三个 Tab（Execution canvas / Tool calls / Cases）
   均可用，`Export run HTML` 按钮在位。
4. **执行画布**：覆盖度徽标三项均为 Complete；63 次工具调用按阶段与工具分组，
   画布节点上限 80 生效并显式提示「5 nodes are outside the overview」；
   选中节点后检查器显示真实身份（agent `主执行智能体` / `agent-main` / `exec-main`）、
   `Call ID: m-0`、`Model: demo-fast`、耗时 1100 ms、Token 900/320/1220、`usage source: reported`、
   证据事件 ID。
5. **工具调用表**：搜索框、状态下拉、Apply/Reset 全部可用；计数「50 loaded of 63 total」；
   表格展示 call_id、工具名、智能体、阶段、状态、耗时、参数摘要（失败为 `—`）。
6. **Token 账本**：总量 54,600 / 21,840 / 76,440、42 次请求；按角色分组时首列曾显示
   「Unavailable agent · agent」这种自相矛盾文案，已改为按角色退化显示（`UsageLedgerTable.tsx`）。
7. **离线报告**：用同一份真实数据渲染，Chrome headless 截图确认标题、报告范围、摘要指标、
   **参与智能体**、Token 分账、用例结果、流程图、工具分组、异常调用、全量调用分段、口径说明
   十个区块齐全；页面无脚本、无外链。

## 4. 既有基线失败（非本次引入，已用 `git stash` 在 HEAD 上复现）

- `tests/evaluation/test_calibration_jobs.py`：5 个
- `tests/evaluation/test_processes.py::test_grandchild_is_recycled_with_the_job_tree`：1 个

## 5. 已知缺口

- `basedpyright` 全仓 strict 模式本就不干净（2385 条），本次未新增依赖、未扩大类型面。
- `npm run lint` 的 3 个错误位于本次未改动的文件（`evaluation-status.tsx`、`browser-url.ts`），属既有基线。
- 报告里没有「参与智能体表」之外的单列「模型成本」区：本轮不估价，避免用不可靠价格推算费用。
- 真实评估系统尚未接入：目前跑通的是本地伪造运行的数据链路，真实评估运行时仍需人工触发一次运行来核对
  真实 Trace 里是否带齐 `agent_id` / `execution_id` / `parent_execution_id`（采集侧代码已就位，
  但没有真实运行证据）。
