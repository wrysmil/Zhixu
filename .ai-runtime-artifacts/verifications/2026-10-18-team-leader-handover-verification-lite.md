---
artifact: verification-lite
route: leader-direct | 小改动直做
skills:
  - source-driven-development
  - superpowers:test-driven-development
  - verification-before-completion
skills_evidence:
  - harness-kit/skills/source-driven-development/SKILL.md
  - /Users/mima0000/.qoder-cn/plugins/cache/qoder-marketplace/superpowers/6.3.0/skills/test-driven-development/SKILL.md
  - verification-before-completion（本轮先跑命令再给结论，命令与输出见下）
source:
  - 用户原话「agentteam 只要选择了，然后 leader 将由那个团队的 teamleader 来主管，而不是那个默认的那个聊天 leader」
  - 用户口径确认：保留全局人格，只加「主管层」；范围选 A（只改提示词文本），效果不够再升级 B
  - .ai-runtime-artifacts/research/2026-09-30-openakita-subagent-integration-research.md §6.2（协作优先原则原文）
  - .ai-runtime-artifacts/specs/2026-10-17-writing-conversation-team-spec.md §7（主编 Loop + team_dispatch 主路径）
created_at: 2026-10-18
tier: 1
---

# 团队 Leader 主管层提示词 — 轻量验证（Tier 1）

## 范围

用户已确认口径后由 Leader 直做，不拆 WU、不派 worker。改动全部是提示词文本与渲染参数，
**不碰**工具白名单、模型解析、绑定生命周期与调度器。

| 文件 | 改动 |
| --- | --- |
| `nanobot/teams/leader.py` | `render_role_prompt` 新增 `leader_name` 参数与主管交接行；固定文本 `_DELEGATION_DISCIPLINE`（6 条委派纪律）注入每个团队回合 |
| `nanobot/teams/turn.py` | 回合装配把冻结快照里的 `leader.name` 传给 `render_role_prompt` |
| `nanobot/agents/catalog.py` | `commander`（普通聊天路径）出厂 prompt 增加「派活纪律」段；`writing-editor` 只留团队特有的一句派活口径，纪律由主管层统一提供 |
| `nanobot/writing/team_seed.py` | 成员 `role` 去掉与档案名重复的前缀，`WRITING_TEAM_TEMPLATE_VERSION` bump 到 `2026-10-18-1` |
| `tests/teams/test_leader_context.py` | 3 条新用例（主管交接、委派纪律、无名字时不拼空壳） |
| `tests/teams/test_leader_turn.py` | 断言回合装配真的带上 Leader 名字与成员角色 |
| `tests/webui/test_agents_catalog.py` | 默认主管含交接纪律；团队 Leader 档案**不得**重复渲染段纪律；派活入口不写错（指挥官 `spawn` / 主编 `team_dispatch`） |
| `tests/writing/test_writing_builtin.py` | 内置团队成员 `role` 不得以档案名开头（名册前缀重复的回归闸） |

未改动并刻意排除：`nanobot/agent/context.py` 的注入顺序（属 B 方案，等 A 的实际效果再决定）、
前端团队会话的显示层（用户本轮未选「UI 标明回答者身份」）。

## 命令与结果

先红后绿，均按 TDD 顺序执行。

| 命令 | 结果 |
| --- | --- |
| `pytest tests/teams/test_leader_context.py -k role_prompt`（实现前） | 4 failed（`TypeError: unexpected keyword 'leader_name'`）——红灯原因正确 |
| `pytest tests/teams/` | 397 passed |
| `pytest tests/webui/test_agents_catalog.py`（出厂 prompt 实现前） | 2 条新用例 failed |
| `pytest tests/webui/test_agents_catalog.py`（实现后） | 23 passed, 1 failed（基线，见下） |
| `python -` 渲染内置创作团队（走 `turn.py:195-202` 同一段装配） | **抓到两个真实缺陷**：主管纪律被档案与渲染段写了两遍（1586 字符）；名册读作「资料研究员：研究员：…」 |
| 去重与名册修好后重跑同一渲染 | 1403 字符，纪律只剩主管层一份，名册读作「资料研究员：按请求检索、核对资料并记录疑点。」 |
| `pytest tests/webui/test_agents_catalog.py -k "handoff or defers or dispatch_tool"` | 先 1 failed（档案与渲染段重复），修后 3 passed |
| `pytest tests/writing/test_writing_builtin.py` | 先 1 failed（role 前缀），修后 8 passed |
| `pytest tests/writing tests/teams` | 615 passed |
| `pytest tests/teams tests/writing tests/webui tests/agent`（末次） | **3351 passed**, 1 skipped, 5 failed（全部为已登记基线） |
| `ruff check`（含 `team_seed.py` 与全部触及测试） | All checks passed |
| `basedpyright`（4 个源文件） | 0 errors 0 warnings 0 notes |
| `npm run test`（webui，`vitest run`） | 139 files / **1778 passed**, 2 skipped；其中一次出现 1 例 `waitFor` 超时，连跑三次仅一次，同命令重跑两次均全绿 |
| `npx tsc -p tsconfig.build.json --noEmit` | exit 0，无输出 |
| 前端源码/测试 grep 本次改动的后端字面量（`研究员：`、`team_dispatch` 等） | 0 命中——那次 flake 与本批改动无因果路径 |

本机没有 `bun`，AGENTS.md 里的 `bun run test` 换成了 `npm run test`（同一脚本：`vitest run`）。

## 基线失败归因（非本批引入）

| 用例 | 证据 |
| --- | --- |
| `tests/webui/test_teams_e2e.py::test_end_to_end_team_flow` | 仅并跑失败、单跑通过。用 `git stash push` 收起本批 `teams/leader.py`+`teams/turn.py` 及两个团队测试后，`pytest tests/webui` 仍是 `1 failed, 1076 passed`，同一用例同一原因 |
| `tests/agent/test_auto_compact.py::...throttles_checks` | `ContextWindowExceededError: 3103/2880`，与提示词文本无关；该文件 `grep teams\|catalog\|render_role_prompt\|FACTORY_PROFILES` 命中 0 次 |
| `tests/agent/tools/test_subagent_tools.py::test_run_inline_returns_structured_error` | `'SimpleNamespace' object has no attribute 'failure_error_kind'`，属子 Agent 执行观察批次的替身缺口（同 0 命中） |
| `tests/agent/test_history_replay.py::test_runner_checkpoint_...` | `StopIteration`，替身缺字段，同上 |
| `tests/agent/test_mcp_reconnect_crash.py::...` | 已在项目记忆 `project-known-baseline-failures` 中登记为既有失败 |

另记一条**新的**基线失败：`tests/webui/test_agents_catalog.py::test_tool_category_keys_are_all_real_tool_names`
单独运行该文件时 `ImportError: cannot import name 'agent' from 'nanobot'`（`ToolLoader()` 的包内导入在脱离
全量收集时触发循环导入）。已用 `git stash push` 收起本批对该测试文件的改动复现，属既有问题。

## 已知残留（如实记录，未顺手改）

- **本轮已修掉两项**：主管纪律重复（档案段与渲染段各写一遍）与名册前缀重复（`team_seed._MEMBER_ROLES`
  带「研究员：」这类前缀）。后者连带 bump 了 `WRITING_TEAM_TEMPLATE_VERSION`，播种幂等路径由
  `tests/writing/` 615 例覆盖通过。
- 档案 prompt 用 `# 输入 / # 你要做 / # 边界`，而主管层用 `##`，渲染后层级看着像平级。属既存形状，
  改它要同时影响普通 `@agent` 路径（那里 `#` 是合理的顶层），本批不动。
- **没有跑真实模型**。证据止于装配层与渲染文本；Leader 是否真的从「自己答」转为「先派活再汇总」，
  要起 gateway 新开一个绑定创作团队的聊天才能看出。这是 A 方案约定的验收动作，尚未执行。
- 前端团队会话的回答者显示仍是默认助手身份（本轮口径未选「UI 标明回答者身份」）。若实际使用后仍觉得
  「看不出换人了」，这是下一个该补的面。
- `webui` 全量并跑有一次 `waitFor` 超时 flake，重跑消失，且已排除与本批的因果。

## Next

- 想看真效果 → 起 gateway 新开一个绑定团队的聊天，观察回答是否转为「派了谁 + 等回执 + 汇总核对」。
- 觉得主管感仍不足 → 走 B：把 `role_section` 移到 bootstrap（人格规则）之后。
- 想让用户一眼看出换人了 → 在团队会话里显示 Leader 名字/头像（前端显示层，独立一批）。
