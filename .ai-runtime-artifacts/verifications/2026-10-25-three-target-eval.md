---
artifact: verification
route: verification-before-completion
batch: agent-evaluation-three-targets
created_at: 2026-10-25
status: pass
scope: 三类目标（Agent / Skill / Team）真实模型评估 + HTML 报告导出
---

# 三类目标的真实模型评估（Agent / Skill / Team）

## 0. 环境与前置

- 分支：`harness/agent-evaluation-calibration`
- gateway：`.venv/bin/nanobot gateway`（WebSocket 8765、健康 18790、内嵌评估 8790，前缀 `/api/eval/v1`）
- 模型：用户自定义 `custom-minmax · MiniMax-M3.1-Flash`（评估 profile 为 gateway 启动时快照，重启才生效）
- 造数方式：评估 HTTP API（登录 → `X-CSRF-Token` + `Idempotency-Key`），未使用浏览器手工点击
- 评估数据集：`冒烟-普通对话`、`冒烟-技能`、`冒烟-团队`，均为 1 次运行、小样本（Agent 2 例，其余各 1 例）

## 1. 结果总览

| 目标类型 | 数据集 | suite id | 最新运行 | 用例 | 结论 |
| --- | --- | --- | --- | --- | --- |
| Agent（普通对话） | 冒烟-普通对话 | `40a9f87f-675b-4fba-9de9-b533851e2862` | `3136610f` | 2 | 2/2 passed |
| Skill | 冒烟-技能 | `af939825-9346-42c3-88cb-89ed4cd616f5` | `5129b18b` | 1 | 1/1 passed |
| Team | 冒烟-团队 | `c1317940-f179-4d73-81fa-74bdf179a147` | `fe7246d6` | 1 | 1/1 passed |

### 1.1 逐用例

| 数据集 | 用例 | 断言 | 实测 |
| --- | --- | --- | --- |
| 冒烟-普通对话 | 算术与排除 | `answer_contains "2"` | passed（final answer `2`） |
| 冒烟-普通对话 | 固定口令回复 | `answer_contains "收到"` | passed（final answer `收到`） |
| 冒烟-技能 | eval-smoke 口令 | `answer_contains "EVAL-SMOKE-OK-42"` | passed（final answer `EVAL-SMOKE-OK-42`，技能文件按目标快照冻结注入） |
| 冒烟-团队 | 秋日落叶一句话 | `answer_contains "金黄"` | passed（约 124s，final answer 含「…在午后的阳光里翻出金黄的颜色。」，经 Leader → 成员 → Leader 汇总） |

### 1.2 团队数据集的全部运行（保留失败历史）

| 运行 | 状态 | 结论 | 说明 |
| --- | --- | --- | --- |
| `fe7246d6` | completed | passed | 修复后成功（约 124s） |
| `85f450f6` | completed | failed | `timed_out`（eval 侧 180s 上限，Leader 因 memberId 缺失反复试错） |
| `506d0cca` | completed | failed | `SCHEDULER_STAGE_FAILED` / `ATTEMPT_EXECUTION_NOT_FINISHED status=running`（团队 runner 从未启动 Leader turn） |

汇总报告「用例最新结论」取 `created_at DESC` 的首条，团队表现为 `passed / fe7246d6`，不会被历史失败覆盖（见 §2-3）。

## 2. 本轮发现并修复的缺陷

### 2.1 团队评估从未真正执行 Leader turn（`nanobot/webui/gateway_services.py`）

- **现象**：团队目标运行后 attempt 一直停在 `running`，只有 `run.started` 事件，最终报 `SCHEDULER_STAGE_FAILED`。
- **根因**：`build_team_eval_runner` 只调了 `scheduler.start_run` 建 run，没有驱动任何一轮对话；`_execute_team` 又把非终态 `running` 直接交给 `finish_attempt`。
- **修复**：
  - runner 生成 `session_key = eval:<token>`，构造 `TeamBinding`，`start_run` 后以 `host.process_direct` 跑一轮 Leader，并注入 `SESSION_TEAM_BINDING_KEY` 与 `VISIBLE_TURN_ID_METADATA_KEY`（turn_id 与 scheduler 一致）；
  - 无 host 时返回 `TEAM_HOST_UNAVAILABLE`；超时返回 `timed_out`；结束取 `team_service.repository.get_run` 的真实终态；
  - `build_gateway_services` 注入 `scheduler` / `session_manager` / `host`。

### 2.2 Leader 名册缺 memberId，`team_dispatch` 反复被拒（`nanobot/teams/leader.py`、`nanobot/teams/turn.py`）

- **现象**：180s 上限内 Leader 做了约 30 次 LLM 调用，反复在 waiting/planning 间切换并试探派活，最终超时。
- **根因**：`render_role_prompt` 只渲染「名字：角色」，而 `team_dispatch` 要求 `memberId` 必须与冻结名册完全一致；Leader 只能瞎猜，未知成员会让整批被拒。
- **修复**：名册改为「- 名字（memberId: xxx）：角色」，并在段末注明 memberId 只能取括号内值；`turn.py` 传入 `(name, role, member_id)` 三元组。修复后同一用例约 124s 通过。

### 2.3 汇总报告用旧失败覆盖最新结论（`nanobot/evaluation/services.py`）

- **现象**：团队用例修复后最新运行 passed，HTML 汇总却仍显示最初的 failed 与旧 run id。
- **根因**：`summary_report` 遍历按 `created_at DESC` 排序的运行，但每命中一次就覆写用例结论，最后剩下的是最旧一条。
- **修复**：改为 first-wins —— 第一次（即最新）命中的 verdict/run id 生效。

## 3. 回归证据

```text
.venv/bin/python -m pytest tests/evaluation/test_targets.py tests/evaluation/test_services.py \
    tests/webui/test_eval_team_runner.py tests/teams -q
→ 438 passed in 13.02s

.venv/bin/ruff check <8 个改动文件>
→ All checks passed!
```

新增/更新的测试：

- `tests/webui/test_eval_team_runner.py`（新）：Leader turn 注入 team_binding / webui_turn_id、final answer 透传、host 缺失、partial 状态。
- `tests/evaluation/test_targets.py`：`partial` 记为 succeeded（stop_reason=partial）、非终态记为 failed（`TEAM_RUN_STATUS_UNEXPECTED`）、内置技能冻结回归。
- `tests/evaluation/test_services.py`：汇总报告取最新 verdict。
- `tests/teams/test_leader_context.py`：名册含 memberId。

## 4. HTML 报告

由 `GET /api/eval/v1/reports/summary` 导出，产物目录：

- `.ai-runtime-artifacts/verifications/2026-10-25-eval-reports/冒烟-普通对话.html`
- `.ai-runtime-artifacts/verifications/2026-10-25-eval-reports/冒烟-技能.html`
- `.ai-runtime-artifacts/verifications/2026-10-25-eval-reports/冒烟-团队.html`

## 5. 观察与注意（非阻塞）

1. 团队目标只有「冻结的团队引用」，执行发生在 gateway 进程内，因此耗时与稳定性直接受 Leader 调度质量影响；当前小样本 124s，建议在生产用例上显式提高 `timeout_s`（默认 120，上限 900）。
2. `list_runs` 的 `ORDER BY created_at DESC` 是「最新结论」聚合前提，改动排序会同时影响汇总口径。
3. 团队数据集保留了两次历史失败运行（`85f450f6`、`506d0cca`），属缺陷取证，未删除。
4. 本批修复尚未提交；涉及 `nanobot/webui/gateway_services.py`、`nanobot/evaluation/services.py`、`nanobot/teams/leader.py`、`nanobot/teams/turn.py` 及对应测试。
