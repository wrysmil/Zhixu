---
artifact: execution-log
route: orchestration.dispatch
plan: .ai-runtime-artifacts/plans/2026-10-25-evaluation-observability-plan.md
dispatch: .ai-runtime-artifacts/plans/2026-10-25-evaluation-observability-dispatch.md
created_at: 2026-10-25
status: active
branch: harness/agent-evaluation-calibration
worktree: current-checkout
---

# 评估可观测性集成执行日志

## 授权与执行边界

- 用户授权：“集成到项目中，后端也得考虑”。
- 用户指定：“直接到当前分支来改”。因此不创建隔离 worktree。
- 当前分支存在其他会话的未提交改动。各 WU 使用互斥文件所有权；所有执行 Agent 均须保留他人改动，不得 reset、checkout 或重写无关文件。

## 执行单元

| WU | 责任 | 状态 | Agent | 验证 |
| --- | --- | --- | --- | --- |
| WU-02A | Trace、物理模型调用 ID、执行身份与 Token 归属 | done（前序会话已落代码） | 前序 executor | `tests/evaluation/test_worker_identity.py` 通过 |
| WU-01 | 聚合、进度、来源与历史最新口径 | done（前序会话已落代码） | 前序 executor | `tests/evaluation/test_services.py` / `test_reports.py` 通过 |
| WU-03 | 统一读模型与 execution / tool-calls / usage 分页 API | done | Leader（Codex 原生子 Agent 不可用时的 `gpt-5.2-codex` 角色失效，改由 Leader 直做） | `tests/evaluation/test_observability.py`（9 通过）、`test_observability_routes.py`（20 通过） |
| WU-05 | 离线 HTML 报告渲染器 `report_view.py` | done | 子 Agent `Godel`（默认角色，继承当前模型） | `tests/evaluation/test_report_view.py`（27 通过）+ Chrome headless 渲染与打印核对 |
| WU-04 | 评估详情画布、工具表、Token 账本与前端 API | done | 子 Agent `Curie`（默认角色，继承当前模型） | `evaluation-observability.test.tsx` 2 通过；全量 vitest 1893 通过；tsc 通过；build 通过 |
| WU-06 | 集成、浏览器验证、集体测试与审查 | done | Leader | `pytest tests/evaluation` 810 通过（6 个既有失败）；ruff 通过；浏览器验证见 verification 产物 |

## 工具与 Agent 透明记录

- Leader 工具：`exec_command` 用于只读检查与运行验证；`apply_patch` 用于文本修改；Codex 原生子 Agent 用于互斥 WU。
- 不调用真实外部模型、不新增生产工具权限、不执行网络工具调用。

## 进度

- 2026-10-25：计划由 draft 更新为 approved；按用户要求采用当前 checkout。
- 2026-10-25：首次使用固定 `executor` 角色启动 WU-02A 与 WU-01/03/05，平台返回 `gpt-5.2-codex model_not_found`，未产生文件写入；改为继承当前可用模型的 Codex 原生 Agent 后重新派发。
- 2026-10-25（续）：`executor` 角色仍绑定不可用模型，两个子 Agent 派发再次失败；改用默认角色（继承当前模型）分别派发 WU-05（报告）与 WU-04（前端），Leader 直做 WU-03（后端读模型与 API）。
- 2026-10-25（续）：接口形状先行落到 `contracts/2026-10-25-contract-evaluation-observability.md` 附录 A（execution / tool-calls / usage / report 的字段名与快照形状），前后端据此对齐，服务端字段名与 `webui/src/lib/evaluation-api.ts` 既有类型一一对应。
- 2026-10-25（续）：后端读模型重写 `nanobot/evaluation/observability.py`——工具节点按 `call_id` 折叠、模型节点按 `model_call_id` 去重、旧 Trace 缺身份时 coverage 降级而非虚构父子关系；服务端不再产出 `tool_group`，归并统一由前端 `execution-graph.ts` 承担，避免两套口径。
- 2026-10-25（续）：新增 `GET /attempts/{id}/execution`、`GET /attempts/{id}/tool-calls`、`GET /runs/{id}/usage`、`GET /runs/{id}/report`；报告走 `Content-Disposition` 固定文件名，文件名只由 run id 派生。
- 2026-10-25（续）：Token 分组改为按 agent 归组且 Judge 单独成组（`judge:<agent_id>`），否则同一执行智能体兼做评分时两路 Token 会被并成一行。
- 2026-10-25（续）：报告补「参与智能体」区块（计划 §5 要求），并把缺失值口径统一为 `—`、真实零显示 `0`。
- 2026-10-25（续）：WU-05 交付 `report_view.py`（纯函数渲染器）与 27 个用例；Leader 补「参与智能体」区块与
  「按 agent 归组、Judge 单独成组」口径，并把报告路由接到 `server.py`。
- 2026-10-25（续）：WU-04 交付 `ObservabilityTabs.tsx` / `ToolCallsTable.tsx` / `CoverageBadges.tsx` 与
  `RunDetailSheet.tsx` 改造，10 语言新增 67 个 i18n 键；Leader 修掉两处浏览器暴露的问题：
  路由解析漏掉 `eval` 导致评估分区不可达、Token 账本按角色分组时首列文案自相矛盾。
- 2026-10-25（续）：浏览器验证用本地伪造运行（2 用例 / 63 次工具调用 / 42 次物理请求），
  未调用真实模型；验证完把 `EVAL_PORT` 临时改到 8791 的改动已还原（与 HEAD 一致）。
- 2026-10-25（续）：验证结论落盘 `verifications/2026-10-25-evaluation-observability-verification.md`。
