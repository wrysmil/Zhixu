---
artifact: verification
route: orchestration:collective-closeout -> superpowers:verification-before-completion
skills:
  - verification-before-completion
  - orchestration
skills_evidence:
  - /Users/mima0000/.agents/skills/verification-before-completion/SKILL.md
  - .claude/skills/orchestration/SKILL.md
source:
  - .ai-runtime-artifacts/plans/2026-10-17-writing-team-sidebar-plan.md
  - .ai-runtime-artifacts/plans/2026-10-17-writing-team-sidebar-dispatch.md
  - .ai-runtime-artifacts/contracts/2026-10-17-contract-writing-team-collab.md
  - harness-kit/project.verification.md
created_at: 2026-10-17
status: passed
---

# 创作空间侧栏内置创作团队 · 集体测试（WU-15a）

**执行者**：Leader（收口轮。原 WU-15a 未派发；本批 5 个子 Agent 全部异常中断，由 Leader 亲自执行并收口）
**日期**：2026-10-17
**分支**：`harness/wt-writing-team-sidebar`（19 commits；相对 base `81b4937`：84 files, +24077/−56）
**worktree**：`.harness-worktrees/zhixu/wt-writing-team-sidebar`

---

## 1. 门槛口径

本仓库 base 本身就有既有失败（`pytest tests/ -q` → **126 failed / 7444 passed / 8 skipped**），因此本批门槛为：

1. **相对 base 不得新增失败**（失败集合做 diff，不看总数）；
2. 本批目标集全绿：`tests/writing`、`tests/webui`、`tests/teams`；
3. 前端全量 Vitest 绿 + `tsc -p tsconfig.build.json` exit 0 + `npm run lint` exit 0；
4. `ruff check nanobot/` 相对 base 零新增。

## 2. 目标集结果（全部实跑）

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 写作域 | `.venv/bin/python -m pytest tests/writing -q` | **192 passed** |
| 协作运行器 + 配方 | `pytest tests/writing/test_collab_runner.py tests/writing/test_collab_recipe.py -q` | **36 passed** |
| 传输层 4 文件 | `pytest tests/webui/test_writing_collab_{api,routes,wiring}.py tests/webui/test_writing_wiring.py -q` | **159 passed** |
| 传输层 8 文件目标集 | 追加 `test_writing_{api,routes}.py`、`test_settings_routes.py`、`test_outbound_wire.py` | **247 passed** |
| webui + teams + writing | `pytest tests/writing tests/webui tests/teams -q` | **1578 passed, 1 failed, 1 skipped**（唯一失败为既有 flaky，见 §4） |
| 导入纪律哨兵 | `pytest tests/webui/test_mcp_presets_api.py -q` | **27 passed**（证明协作栈的延迟导入没破坏按名 patch） |
| 内置保护 | `pytest tests/webui/test_builtin_protection.py -q` | **16 passed / 1 skipped**（skip 理由：首批无内置 MCP 预设） |
| 前端全量 | `cd webui && npx vitest run` | **128 files / 1683 passed / 2 skipped** |
| 前端类型 | `cd webui && npx tsc -p tsconfig.build.json` | **exit 0** |
| 前端风格 | `cd webui && npm run lint` | **exit 0（`--max-warnings 0`）** |
| Python 风格 | `.venv/bin/python -m ruff check nanobot/` | **10 errors，全部 `nanobot/memory/*` 既有**，本批零新增 |
| Python 全量 | `.venv/bin/python -m pytest tests/ -q` | **126 failed / 7763 passed / 9 skipped**（225.65s） |

## 3. 全量基线对照

| 运行 | 命令 | 结果 |
| --- | --- | --- |
| **base（主 checkout `81b4937`）** | `.venv/bin/python -m pytest tests/ -q` | **126 failed / 7444 passed / 8 skipped** |
| **worktree（本批 HEAD）** | 同上 | **126 failed / 7763 passed / 9 skipped** |
| 差值 | — | 失败 **±0**，通过 **+319**，跳过 +1 |

失败集合 diff（按 `FAILED <nodeid>` 精确比对，两侧各导出到 `/tmp/base-failures.txt` 与 `/tmp/wt-failures.txt`）：

```text
comm -13 /tmp/base-failures.txt /tmp/wt-failures.txt   # 本批新增失败 → 空
comm -23 /tmp/base-failures.txt /tmp/wt-failures.txt   # 本批修好的既有失败 → 空
```

**结论：新增 0 条、消失 0 条**，两侧失败集合完全一致；+319 条通过即本批交付的测试。

## 4. 已知的非本批失败（登记，不计入本批）

| 失败 | 性质 | 证据 |
| --- | --- | --- |
| `tests/webui/test_teams_e2e.py::test_end_to_end_team_flow` | **既有 flaky**：断言两个并行任务的完成顺序（`['任务 b','任务 a','任务 c']` vs `['任务 a','任务 b','任务 c']`） | base 连跑 5 次：**2 passed / 3 failed**；worktree 连跑 3 次：3 failed。两侧都抖，非本批引入 |
| `tests/webui/test_teams_e2e.py::test_cancel_from_a_foreign_session_is_refused` | **既有失败** | base `pytest tests/webui/test_teams_e2e.py -q` → 1 failed / 5 passed |
| `tests/cli/test_commands.py`（15 项）、`tests/agent/test_history_replay.py`、`tests/providers/*`、`tests/tools/test_web_search_tool.py` 等 | **既有失败** | 失败集合 diff 中两侧一致（§3） |

## 5. 本批交付的验证面（要点）

| 面 | 证据 |
| --- | --- |
| 冻结准备默认路径 | `test_default_preparer_runs_real_prepare_frozen`：真实 `prepare_frozen` + 真实 `SkillsLoader` + 真实 `RECIPES` 走通 `start()`（审查 blocker B-1 的回归） |
| 幂等写路径 | `test_same_request_id_different_payload_is_replay_conflict`、`test_record_request_refuses_to_replace_a_foreign_{op,article}`、`test_{apply,undo}_keeps_the_save_snapshot_idempotency_record` |
| 代数闸门 / 取消窗口 | 变异探针实跑：`if not ref.live` → 2 failed；`cancel_generation` 校验 → 1 failed（两条新用例分别复现取消窗口与换代后旧 run 事件） |
| 生产装配 | `test_collab_runner_assembly_binds_ports_and_event_sink`（端口 + envelope 出口 + 收口 sink + 调度器订阅）、`test_collab_operations_without_runner_stay_503` |
| 前端挂载 | `creative-partner-integration.test.tsx` 3 项：注入面板渲染 + 开关收起/展开、无面板保留阶段 A 占位、真实工作区打开文章挂上真面板 |
| 交接版本号 | `writing-partner-panel.test.tsx` 断言 `expectedHandoffVersion == artifactVersion`（此前取 `max` 会稳定 409） |
| 内置保护 | `test_builtin_protection.py` 16 项（含 PoC 口径：内置团队写入口 409 `BUILTIN_RESOURCE_PROTECTED`） |
| 导入纪律 | `test_mcp_presets_api.py` 27 项（协作栈全部延迟导入，按名 patch 仍可达） |

## 6. 未覆盖 / 未验证（如实登记）

- **浏览器验收（390 / 768 / 1280 / 1440）未执行**：环境没有可用的浏览器驱动；窄屏行为只用 `max-lg` 浮层类与单测锁定，未做真实视口截图。
- **真实模型端到端（plan W15）未执行**：需要可用模型配额与真实 Team 运行；`TeamPort` 的成员派发路径用假 host / 假执行器验证（`test_collab_team_port.py` 12 项），未跑真实 LLM 回合。
- **`writing.confirm` 重试幂等未修**（契约 §10 #19/#20/#24）：首次确认正常，重复 confirm 仍会 200 并改写 `resolved_at`。
- **`ArticleRepository._record_request` 仍是裸 `INSERT OR REPLACE`**（契约 §10 #21）。
- **未跑 basedpyright**（CI 的严格类型检查）；Python 侧只跑了 ruff + pytest。
- 协作级 `cancel_generation` 的进程内计数器重启后从 0 起算（列只增，见契约 §10 #8 残留）。
