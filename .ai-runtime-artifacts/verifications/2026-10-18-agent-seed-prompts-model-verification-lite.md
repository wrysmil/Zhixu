---
artifact: verification-lite
route: leader-direct（spec 已批准后按用户「直接执行」进入实现）
skills:
  - source-driven-development
  - test-driven-development
  - verification-before-completion
skills_evidence:
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
  - harness-kit/.agents/skills/test-driven-development/SKILL.md
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户原话：「目前子Agent的那些能力…都能在执行中体现吗，不越界吗」「已存在的预设种子子Agent都没有默认的提示词…你得设计一下」「直接执行」「同意提交」
  - .ai-runtime-artifacts/specs/2026-10-18-agent-seed-prompts-and-model-override-spec.md
  - harness-kit/project.verification.md
  - harness-kit/core/routing.md § 阶段门禁 / § WU 编排硬触发
created_at: 2026-10-18
tier: 1
approved: true
commits:
  - b064190 fix(agents)
  - ffcc6de docs(artifacts)
---

# 出厂 Agent 种子提示词与子 Agent 模型覆盖 — 轻量验证（Tier 1）

## 路由偏离记录（按 routing.md § 总原则要求）

本批 5 个写文件，命中 § WU 编排硬触发的「预计 ≥3 个写文件且非纯 docs/chore」，
默认应升 Tier 2 编排。未走编排的实际原因，以及用户对该口径的明确选择：

1. 用户在 spec 的 `## Next` 三选项中选了「直接执行」，该选项正文即写明
   「A/B/C 约 5 个写文件走 Tier 1 直做 + verification-lite」。
2. B（身份行语义）与 C（模型 fail-closed）改的是**同一对文件**
   `nanobot/agent/subagent.py` + `nanobot/agent/loop.py`，且 C 依赖 B 落地的
   `runtime.py` 构造器。拆 WU 并行会产生物理冲突，属假并行。
3. 实现期间发现本 worktree 有并发会话正在写同一批文件（见 § 事故记录），
   此时派 worker 到 worktree 再合流，冲突面远大于主线程直做。

## 范围

改动文件（commit `b064190`，9 个）：

- `nanobot/agents/catalog.py` — 4 个出厂档案补 prompt、`general-assistant` 描述修正、5 个创作角色补 `# 交付`
- `nanobot/agents/runtime.py` — 新增 `profile_identity_line` / `profile_role_section`
- `nanobot/agent/loop.py` — 注入 `model_resolver=self._subagent_model_resolver`、新增该方法、`turn_role_section` 改用 `profile_role_section`
- `tests/webui/test_agents_catalog.py`、`test_agents_runtime.py`、`test_agents_runtime_e2e.py`、`test_subagent_agent_profile.py`、`test_subagent_spawn_e2e.py`、`tests/agent/test_turn_agent_profile.py`

已入库但不在本 commit 的：`nanobot/agent/subagent.py`（同批改动被并发会话的 `093c00e` 整份带走，见 § 事故记录）。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `pytest tests/webui/test_agents_catalog.py tests/webui/test_agents_runtime.py tests/webui/test_agents_runtime_e2e.py tests/webui/test_agents_store.py tests/webui/test_subagent_agent_profile.py tests/webui/test_subagent_spawn_e2e.py tests/agent/test_turn_agent_profile.py tests/teams/test_execution.py tests/writing/test_writing_builtin.py -q` | **130 passed** |
| 同上（去掉 store/writing 两组） | 102 passed；再含 `tests/teams/test_execution.py` 的一组 39 passed |
| 翻转后的两条 e2e 单独复跑 | 11 passed |
| 并发批次自有面（`tests/agent/test_subagent_activity_projection.py tests/subagent_executions/ tests/webui/test_subagent_execution_{projection,runtime}.py`） | **73 passed** |
| `ruff check nanobot/agents/catalog.py nanobot/agents/runtime.py nanobot/agent/subagent.py nanobot/agent/loop.py` | All checks passed |
| `ruff check nanobot/` | 10 errors，全在 `nanobot/memory/`（8×F401 + 2×N806），项目记忆已登记的基线 |
| `basedpyright` 本批 4 个源文件 | 3 errors，逐行核对**均不在本批 hunk**：`subagent.py:75 @contextmanager`、`loop.py:638 for_workspace`、`loop.py:1233 isinstance` |
| `pytest tests/ -q`（已提交状态） | **26 failed / 8047 passed / 9 skipped**（见 § 全量失败归因） |
| `python -c "import nanobot.agent.subagent, nanobot.agent.loop, nanobot.agents.runtime"` | import ok |
| HEAD 干净副本 `git worktree add --detach /tmp/... HEAD` + `PYTHONPATH=<副本> python -c "import ..."` | **HEAD import ok**（修复前该副本 ImportError，见 § 事故记录） |
| 同一干净副本跑本批 8 个测试文件 | **122 passed**（证明两笔 commit 自洽，不依赖工作区残留） |
| `git push origin feature/desktop-app` | `4c3f88e..87aed2d`（**fast-forward，未用 force**）；`git rev-list --count origin/feature/desktop-app..HEAD` = 0 |
| `git worktree add --detach /tmp/... origin/feature/desktop-app` + 该副本 import + 本批 7 个测试文件 | 副本 HEAD = `87aed2d`，**remote HEAD import ok**、**102 passed** —— 远端已脱离 `4c3f88e` 的 import 崩状态 |
| `git worktree add --detach /tmp/... b9f720f` 跑 HEAD 那 26 项所属的 11 个文件 | **25 failed / 359 passed** — 与 HEAD 的失败集逐名相同 |

临时副本 `/tmp/zhixu-head-ct`、`/tmp/zhixu-verify-head`、`/tmp/zhixu-base-b9f`、`/tmp/zhixu-base2` 均已
`git worktree remove --force` 清理；`/tmp/zhixu_check_model.py`（实跑取证脚本）已删。
共享 worktree **未使用** `git stash`（记忆 line 28/32 的陷阱 + 并发会话会互扫）。

## 全量失败归因（26 项，0 项属本批）

方法：取可构建基线 `b9f720f`（本批之前、也早于并发会话的两笔提交）在**同样的 11 个文件**上重跑，
与 HEAD 的失败名集做 `comm` 差集。

| 归属 | 数量 | 证据 |
| --- | --- | --- |
| 基线逐名相同 | 25 | `comm -13` 差集为空，`comm -23` 亦为空 |
| 抖动 | 1 | `test_teams_e2e.py::test_end_to_end_team_flow`：HEAD 单跑 3 次 = passed / failed / passed；基线单跑同样 failed。项目记忆已登记该用例为并跑抖动 |

`tests/agent/` 4 例（`test_auto_compact` / `test_history_replay` / `test_subagent_tools` /
`test_mcp_reconnect_crash`）与 4 个 `test_serve_*` 与记忆里的既有清单一致；
其中唯一落在我改动 blast radius 的 `test_run_inline_returns_structured_error` 失败原因逐字为
`'types.SimpleNamespace' object has no attribute 'failure_error_kind'`，即登记过的基线成因，非本批引入。
`mcp_probe` / `mcp_tool` / `web_fetch_security` / `cli gateway_*` 为真实 DNS 与端口依赖
（报错里解析到 `93.184.216.34`、绑定 `:18791`），在基线同样红。
`tests/webui/test_agents_catalog.py::test_tool_category_keys_are_all_real_tool_names`
只在单跑该文件时红（`ToolLoader()` 脱离全量收集的循环导入），合跑即绿。

## TDD Compliance

```text
TDD Compliance: 部分达标，两处偏差如实记录
- 逐单元 RED→GREEN 已执行并留档（每条失败原因都核对过）：
  · general-assistant 的出厂 prompt 不能为空 → AssertionError: assert ''
  · writing-editor 缺少小节 # 交付
  · assert '对齐全局' not in '什么都能接的默认助手，能力对齐全局配置。'
  · ImportError: cannot import name 'profile_identity_line'（函数未落地）
  · 空 prompt 不拼身份行的旧断言 → 翻转为「你是空白档案，没写提示词的自建档案。」
  · test_unresolvable_profile_model_fails_without_calling_the_runner → 红（当时静默降级并跑了 runner）
  · test_loop_wires_the_subagent_model_resolver → 红（resolver 未接）
- Test commit / Code commit: **同一笔 b064190**。git log 无法证明 test-first 顺序 ——
  这是本批唯一未达标的 gate 项，RED 证据只存在于本文件的命令记录里。
- Happy path: YES | Edge cases: YES（preset 不可解析、档案 id 非法、hidden 档案、空白自建档案）
  | Error cases: YES（model_preset_unavailable、runner 未被调用）
- All tests pass: **NO** —— 全量 26 failed，但 0 项属本批（逐名对照基线，见上表）。
  gate 要求的「0 failures」在本仓当前分支上不可达，改以「失败集与基线逐名相同」为等价证据。
```

按 spec §6 验收口径逐条核对：

| spec 条目 | 状态 | 落点 |
| --- | --- | --- |
| 1 出厂 prompt 非空 + 按组分节 | 完成 | `test_subagent_factory_prompts_are_written` / `test_writing_factory_prompts_carry_a_delivery_section` / `test_commander_factory_prompt_structure_is_pinned` |
| 2 tools 白名单与承诺不矛盾 | 完成 | `test_subagent_factory_tool_sets_match_their_prompts` / `test_subagent_prompts_do_not_claim_capabilities_the_profile_lacks` |
| 3 空 prompt 也拼身份行（两处路径） | 完成 | `test_build_prompt_adds_identity_when_prompt_empty` + `profile_identity_line` / `profile_role_section` 四条 |
| 4 装配级回归锁 | 完成 | `test_loop_wires_the_subagent_model_resolver`（含 `resolver("no-such-preset") is None` 收敛） |
| 5 模型覆盖两条路径 | 完成 | `test_profile_model_replaces_the_runtime_before_the_runner`、`test_unresolvable_profile_model_fails_without_calling_the_runner`、`test_frozen_profile_model_fails_the_same_way` |
| 6 快照 `model` / `model_source`（spec §4.6） | **未做，按 spec 标注整包切出** | 见 § 未验证项 |

## 事故记录：并发会话把我的改动提交并推送了

实现期间 `git status` 从会话开始快照的 clean 变成 37 个文件被改，其中
`nanobot/subagent_executions/*`、`webui/src/features/subagent-execution/*`、10 个 locale、
`IdentityPage.tsx` 均非我所写。随后并发会话产出 `093c00e` / `4c3f88e` 两笔提交并推送，
`093c00e` 整份带走了我对 `nanobot/agent/subagent.py` 的改动，却没带它依赖的
`nanobot/agents/runtime.py`。后果：

```
HEAD:nanobot/agent/subagent.py:31  ImportError: cannot import name 'profile_identity_line'
HEAD:nanobot/agents/runtime.py 中 profile_identity_line 出现次数 = 0
```

即 **`origin/feature/desktop-app` 的 `4c3f88e` 处于 import 即崩的状态**（任何新克隆与 CI 都会红），
正是项目记忆 line 30 警告过的「中间 commit 不可独立构建」，只是这次发生在远端。
核对方式：`git worktree add --detach` 到 HEAD 的只读副本，`PYTHONPATH` 指向副本再用主 venv 解释器 import
（共享 worktree 不能 stash）。

`b064190` 补齐 `runtime.py` 与 `loop.py` 接线，使 HEAD 恢复可构建，已在该干净副本上验证
`HEAD import ok` + `122 passed`。归属披露写进了 commit body。
用户批准后三笔已 forward push（`4c3f88e..87aed2d`，未动历史），远端状态另在
`origin/feature/desktop-app` 的只读副本上复验为 `import ok` + `102 passed`。

## 未验证项

- spec §4.6（快照补 `model` / `model_source` + 前端显示实际模型）**未实施**：
  `nanobot/subagent_executions/models.py` 与前端 `parser.ts` / `types.ts` / 面板组件刚被
  `093c00e` 改过入库，现在动同一批文件会与并发会话继续互相覆盖。执行面板目前仍核对不了
  「这次跑的是哪个模型」，即便 §4.5 已修好模型覆盖 —— 这是本批遗留的可见性缺口。
- 未做真实 LLM 冒烟：`spawn agent=code-reviewer` 带 `modelId` 端到端跑一次推理未验证，
  本批证据止于 runtime 装配层（`spec.runtime.model == "resolved-model"`）。
  渠道/gateway 侧未启动态验证（`project.verification.md` 第 4 条未跑）。
- 出厂提示词的**文本质量**只有结构断言（小节名、白名单一致性），没有效果评测；
  模型是否真的按 `# 回报格式` 回报需人工观察。
- WebUI 前端未跑（本批零前端改动）。若并发会话的 locale 改动仍在途，
  `npm run test` 的结果不属本批可归因范围。

## Next

- 已推送：`4c3f88e..87aed2d` fast-forward，未动已推送历史
- 要补执行面板模型可见性 → 等并发会话的 `subagent_executions` 批次落地后单独立项（spec §4.6 已写好口径）
- 要动提示词文本 → 直接给修改意见，改 `catalog.py` 后重跑 `test_agents_catalog.py`
- 想做真实端到端冒烟 → 启 `nanobot gateway`，给某档案选一个与全局不同的 `modelId` 后
  `spawn agent=<id>` 观察回报模型；再把该 preset 删掉复跑，应得 `model_preset_unavailable`
  而不是静默换成父会话模型
