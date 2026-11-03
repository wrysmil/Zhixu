---
artifact: verification
route: Harness:leader-direct
source:
  - .ai-runtime-artifacts/specs/2026-10-27-branch-red-lights-repair-spec.md
  - .ai-runtime-artifacts/contracts/2026-10-18-contract-subagent-observability.md
  - .ai-runtime-artifacts/contracts/2026-10-15-agent-team-contract.md
  - .ai-runtime-artifacts/verifications/2026-09-15-memory-extraction-error-and-idle-timer-collective-test.md
created_at: 2026-10-27
target_branch: harness/agent-evaluation-calibration
status: completed
---

# Cluster D 红灯修复验证（2026-10-27）

## 1. 范围与环境

- 分支：`harness/agent-evaluation-calibration`（HEAD `21ddf78`）
- Python：`/Users/mima0000/Documents/学习-001/do-project/zhixu/.venv`（3.13）
- 目标用例文件：`tests/agent/tools/test_subagent_tools.py`、
  `tests/channels/test_websocket_application_boundary.py`、`tests/webui/test_teams_e2e.py`
- 环境注记：本机有一个 gateway 进程（PID 87577）监听 8765 / 8790 / 18790。
  本簇 4 条失败**全部不是端口或全局状态导致**（3 条是纯逻辑 / 架构断言，第 4 条是同文件内的
  既有竞态），复现与修复全程未停该进程。

## 2. 数字

| 口径 | 修复前 | 修复后 |
| --- | --- | --- |
| 目标 3 个文件 | `4 failed, 23 passed` | `28 passed`（连续 5 次全绿） |
| 全量 `pytest -q` | 基线 `40 failed / 10080 passed / 21 skipped`（spec 记录，含 A/B/C 三簇他人工作） | `2 failed, 10119 passed, 21 skipped` |
| 剩余 2 条失败 | — | `tests/agent/test_history_replay.py::test_runner_checkpoint_keeps_current_user_as_replay_boundary`、<br>`tests/agent/test_mcp_reconnect_crash.py::test_mcp_reconnect_during_shutdown_does_not_crash` |

剩余 2 条与本簇无关：把本簇 5 个改动文件全部 `git checkout` 还原后，这 2 条**仍然失败**
（实测 `2 failed in 9.04s`），属分支既有红灯。

目标文件「修复前 4 条」= 指定的 3 条 + `test_cancel_from_a_foreign_session_is_refused`
（同文件内既有竞态，本轮顺带收敛，见 §3.4）。

## 3. 逐条根因与判定

### 3.1 `test_run_inline_returns_structured_error` → 判定规则 1（测试过期）

- 根因：产品 `nanobot/agent/subagent.py:1055` 自 `774cca1` 起读取 `result.failure_error_kind`，
  该字段是 `AgentRunResult`（`nanobot/agent/runner.py:133`）的既有字段；测试用
  `types.SimpleNamespace` 假冒 `AgentRunResult`，缺该字段 → 抛 `AttributeError`，
  被 `subagent.py:1091` 的兜底 `except Exception` 收成 `Error: 'types.SimpleNamespace' ...`。
- 依据：`.ai-runtime-artifacts/specs/2026-10-18-subagent-observability-spec.md:78`
  「运行超时为 failed，stopReason=timeout」——超时必须走结构化错误类型，产品读该字段是对的。
- 产品健壮性核实：`SubagentManager.runner` 固定为 `AgentRunner()`（`subagent.py:401`），
  `AgentRunner.run` 永远返回带该字段的 `AgentRunResult`，生产路径不存在缺字段的可能 →
  裸 `AttributeError` 不可达，**不需要**把产品改成 `getattr` 兜底。
- 处置：测试桩改为直接用真 dataclass `AgentRunResult`（12 处），从结构上消除「桩漂移」这一类失败；
  原断言 `result == "subagent failed"` / `is_tool_error_result(result)` **一字未动**。
- 追加：新增 `test_timeout_failure_is_classified_as_structured_timeout`，把 774cca1 引入却
  一直没有测试覆盖的「超时 → stopReason/errorCode=timeout」钉住。

### 3.2 `test_persisted_webui_session_prefix_has_one_production_owner` → 判定规则 2（产品修复）

- 根因：仓库有架构约束——持久化 WebUI 会话前缀 `websocket:` 的**字面量**全仓只允许出现在
  `nanobot/webui/session_identity.py` 一处（唯一生产属主）。三处违规：
  `nanobot/teams/turn.py:131`（注释）、`nanobot/webui/gateway_services.py:219`（docstring）、
  `nanobot/writing/run_extension.py:151`（真代码硬编码 `startswith("websocket:")`）。
- 依据：`.ai-runtime-artifacts/verifications/2026-09-15-memory-extraction-error-and-idle-timer-collective-test.md:97-102`
  明确写「该架构约束测试要求全仓库（除 `nanobot/webui/session_identity.py`）不得出现字面量
  `"websocket:"`」，且历史处置就是改产品（docstring 改成 `<channel>:<sender_id>`）。
- 处置：改产品，测试**原样保留**作回归锁。
  - `writing/run_extension.py` 改为 `from nanobot.webui.session_identity import WEBUI_SESSION_STORAGE_PREFIX`
    并用常量判定（同包 `writing/collab/runner.py`、`writing/collab/repository.py` 已是同一写法，行为完全等价）；
  - `teams/turn.py` 注释、`gateway_services.py` docstring 去掉字面量，改指唯一属主模块。

### 3.3 `test_end_to_end_team_flow` → 判定规则 1（测试过度约束）

- 根因：断言 `gateway.executor.activities == ["任务 a", "任务 b", "任务 c"]` 固定了两个
  **并行**成员（a、b）的相对先后。同一用例里 `peak == 2` 已证明二者并发；并行任务的
  活动记录顺序由事件循环调度决定，无契约保证 → 实测 8 次跑挂 4 次（50% 概率失败）。
- 依据：`.ai-runtime-artifacts/contracts/2026-10-15-agent-team-contract.md` —— 定序只来自
  「声明依赖」（`depends_on`）；事件流的顺序契约是 `seq` 在单 run 内严格递增，客户端按 seq
  顺序应用，**没有**「并行成员活动必须按派单顺序」这一条。
- 处置：改为断言契约真正保证的两件事——三条活动各出现一次（`sorted(activities) == [...]`）
  与依赖顺序（`activities.index("任务 c") > activities.index("任务 a")`）；
  `a`/`b` 的相对先后不再断言（那属于调度竞态，不是行为契约）。

### 3.4 `test_cancel_from_a_foreign_session_is_refused`（同文件既有竞态，非指定 3 条）→ 判定规则 1

- 根因：该用例没有 `gateway.executor.block()`，成员会在断言前自己跑完，
  `status_of(run_id)` 在 `running` / `completed` 之间摇摆 → 实测 8 次跑挂 5 次。
- 依据：同文件 `test_cancel_run_is_idempotent_and_reports_real_status` 用 `executor.block()`
  把运行钉在非终态再断言状态，本用例漏了这一步。
- 处置：补 `gateway.executor.block()`（与兄弟用例同一手法）+ 取消后 `release.set()`。
  断言 `status_of(run_id) == "running"` **一字未动**。

## 4. 断言削弱声明

**没有任何断言被削弱、删除、改为恒真，也没有裸 skip，没有把被测行为 mock 掉。**

- 3.1：原断言逐字保留；新增 1 条用例（净 +1 条）。
- 3.2：测试文件**零改动**，作为回归锁。
- 3.3 / 3.4：断言仍是同一性质、同一分母、同一拦截点，只是把「并发无保证的相对顺序」
  换成「契约保证的依赖顺序」，并额外锁住「三条活动各一次」。

## 5. 未验证项

- 未跑浏览器端 / WebUI vitest（本次只动 Python 测试与 3 个 Python 产品模块，不动 `webui/`）。
- 未跑 CI 矩阵（无 runner）；以本地全量 `pytest` 近似。
- `basedpyright` 在 `nanobot/webui/gateway_services.py` 仍有 2 条 `reportUnknownMemberType`，
  实测 HEAD 版本同样有这 2 条（行号 387→388，仅因 docstring 多了一行），属既有问题，非本轮引入。
