---
artifact: dispatch
route: leader-direct + native-subagents
spec: .ai-runtime-artifacts/specs/2026-10-27-branch-red-lights-repair-spec.md
created_at: 2026-10-27
tier: 2
---

# 分支既有红灯修复 — 调度单

## 背景

分支 `harness/agent-evaluation-calibration` 在拉取前即为红：`pytest` 全量 40 failed / 10080 passed。
已在拉取前提交 `2fb9e5d` 的临时 worktree 复跑同批用例，证明先验存在、与本次拉取文件级不相交。

## 分簇与写域（互不重叠）

| 簇 | 用例数 | 写域 | 执行者 |
| --- | --- | --- | --- |
| A | 16 | `nanobot/channels/websocket/tests/**` | Leader 直做 |
| B | 7 | 根 `conftest.py`（代理来源隔离） | Leader 直做 |
| C | 13 | `tests/cli/**`（必要时 `nanobot/cli/**`、`nanobot/gateway/**`） | 子 Agent Laplace |
| D | 3 | `tests/agent/tools/test_subagent_tools.py`、`tests/channels/test_websocket_application_boundary.py`、`tests/webui/test_teams_e2e.py`（必要时对应产品模块） | 子 Agent Singer |
| E | 2 | `tests/agent/test_history_replay.py`、`tests/agent/test_mcp_reconnect_crash.py`（必要时 `nanobot/agent/**`） | 子 Agent Rawls |

## 模型路由事故与处置

首次分派用 `agent_type=executor`（其 prompt 锁定 `gpt-5.2-codex`），两个 Agent 均以
`HTTP 400 model_not_found`（ChatGPT 账户不支持该模型）立即失败。处置：改用 `agent_type=default`
（继承当前模型）重新分派，任务描述自带全部必要上下文，不依赖 fork 的历史。

> Directive: 后续在本机分派子 Agent 时不要用 executor/analyst 等锁死 `gpt-5.2-codex` 的角色，
> 除非显式覆盖到可用模型（如 `gpt-6.1-sol`）。

## 统一判定规则（见 spec）

1. 产品行为符合已写契约 → 测试过期，更新测试并保留断言意图；
2. 产品行为与契约冲突或构成回归 → 修产品，测试原样留作回归锁；
3. 平台/环境差异 → 显式平台判定收窄，目标平台断言仍须成立。

禁止：删断言、改恒真、裸 skip、mock 掉被测行为、放宽安全类断言。

## 验收

- 目标用例全绿；全量 `pytest` 相对基线零新增失败。
- 每处改动能说清归属的判定规则与契约依据。
- 证据落 `verifications/`，执行记录落 `execution-logs/`。
