---
artifact: spec
route: leader-direct | 分支既有红灯修复
created_at: 2026-10-27
tier: 2
status: approved
---

# 分支既有红灯修复 spec（2026-10-27）

## 背景

分支 `harness/agent-evaluation-calibration` 在本次拉取（`2fb9e5d` → `21ddf78`）之前就是红的：
全量 `pytest` 有 40 条失败（`10080 passed / 40 failed / 21 skipped`）。已用拉取前提交的临时
worktree 复跑同批用例，证明失败先于本次拉取存在，与本次拉取内容（仅 `nanobot/cli/eval_commands.py`、
`nanobot/cli/gateway_runtime.py`、`nanobot/evaluation/*`、webui 评估相关）文件级不相交。

本轮目标：把这批红灯收敛到 0，且**不牺牲任何行为断言**。

## 失败分类（诊断结论）

| 簇 | 用例数 | 位置 | 根因初判 |
| --- | --- | --- | --- |
| A | 16 | `nanobot/channels/websocket/tests/*` | 协议新增 `subagent_execution_snapshot` 帧（hydrate 时下发），测试 helper 与单测仍假设严格事件序列 → **测试落后于契约** |
| B | 7 | `tests/tools/test_{mcp_probe,mcp_tool,web_fetch_security}.py` | 待定性：疑似 SSRF/重定向拦截与 DNS rebind 防护**未按预期触发**，需区分「产品回归」与「桩失效」 |
| C | 15 | `tests/cli/test_commands.py`（14，CI 矩阵已 `--ignore`）、`tests/cli/test_tui_launcher.py`（1） | 待定性 |
| D | 3 | `tests/agent/tools/test_subagent_tools.py`、`tests/channels/test_websocket_application_boundary.py`、`tests/webui/test_teams_e2e.py` | 待定性（已知：`SimpleNamespace` 桩缺 `failure_error_kind` → 产品新增字段，桩过期） |

## 判定规则（每个失败必须落到其中一条）

1. **产品行为符合已写契约**（`.ai-runtime-artifacts/contracts/`、`docs/`、`specs/`）→ **测试过期**：
   更新测试去断言契约行为，保留原断言的**意图**（同一性质、同一分母、同一拦截点）。
2. **产品行为与契约冲突，或构成安全/正确性回归** → **产品修复**：改产品，测试原样保留作回归锁。
3. **平台差异**（路径语义、`/proc`、时钟等）→ 用显式平台判定收窄，前提是目标平台上断言仍然成立。

## 禁止项（硬约束）

- 不得删除断言、不得把断言改成恒真、不得裸 `skip`、不得把被测行为 mock 掉。
- 不得为了让用例变绿而放宽安全类断言（如 SSRF/重定向拦截）。
- 不得跨簇改动他人文件；不得回退他人改动。
- 不得新增运行时依赖。

## 验收口径

- 目标用例文件全绿；全量 `pytest` 相对基线 **零新增失败**。
- 每个改动都能说清：属于哪条判定规则、依据哪份契约、为什么不是另一种可能。
- 证据落 `.ai-runtime-artifacts/verifications/`，执行记录落 `.ai-runtime-artifacts/execution-logs/`。

## 未验证项（初始）

- 浏览器端不重跑（本次只改 Python 测试与可能的 Python 产品代码，不动 webui）。
- CI 矩阵未实跑（无 runner），以本地全量 pytest 近似。
