---
artifact: verification
route: orchestration:dispatcher-workflow
skills: [orchestration, verification-before-completion]
skills_evidence:
  - harness-kit/.agents/skills/orchestration/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - .ai-runtime-artifacts/plans/2026-10-15-agent-team-plan.md
  - .ai-runtime-artifacts/plans/2026-10-15-agent-team-dispatch.md
  - .ai-runtime-artifacts/contracts/2026-10-15-agent-team-contract.md
  - harness-kit/project.verification.md
created_at: 2026-10-15
status: verified
phase: G2
baseline: 2700025
head: 03377c4
---

# Agent Team G1+G2 尾盘集体测试（WU-01 / WU-02 / WU-03 / WU-08）

结论：**G1（后端底座）与 G2（团队管理页）可以收口**。所有命令均为实跑输出；
唯一失败项是基线既有缺陷，已在原始代码上复现归因，不属于本批引入。

产物落位：本文件在**主 checkout**（`project.git.md` §45）；代码在 worktree
`harness/wt-agent-team`（分支同名），HEAD `03377c4`，基线 `2700025`。

§1–§4 记录的是 HEAD `42e89aa` 时的集体测试；尾盘审查又发现两处缺陷，修复与复验见 §5。

## 1. 命令与输出（逐条实跑）

| # | 命令 | 输出 | 判定 |
| --- | --- | --- | --- |
| V1 | `pytest tests/teams tests/agent tests/webui -q` | `1 failed, 2689 passed, 1 warning in 68.86s` | 唯一失败为 V2 |
| V2 | `tests/agent/test_mcp_reconnect_crash.py::test_mcp_reconnect_during_shutdown_does_not_crash` | 在**未含本批改动**的基线 `2700025` 上同样失败 | 基线既有，非本批回归 |
| V3 | `ruff check nanobot/teams nanobot/agent/subagent.py nanobot/agents tests/teams` | `All checks passed!` | 通过 |
| V4 | `python -m basedpyright nanobot/teams nanobot/agent/subagent.py tests/teams` | `0 errors, 0 warnings, 0 notes` | 通过 |
| V5 | `npx vitest run`（webui 全量） | `Test Files 116 passed (116)` / `Tests 1552 passed (1552)` | 通过 |
| V6 | `npm run build`（= `tsc -p tsconfig.build.json && vite build`） | exit 0，`✓ built in 5.71s` | 通过 |
| V7 | `npm run lint`（eslint `--max-warnings 0`） | exit 0，无输出 | 通过 |

工具替代口径（延续 G0 记录）：本机无 `bun`，plan §7 的 `bun run test` 以
`npx vitest run` 执行 —— `package.json` 的 `test` 脚本即 `vitest run`，语义等价；
未安装任何新运行时。类型检查同理走 `npm run build` 内的 `tsc -p tsconfig.build.json`
（直接 `npx tsc -p tsconfig.json` 会因缺少 vite 环境声明报 `import.meta.glob/env` 假错，
不作为判定依据）。

## 2. 本批新增测试面

| 范围 | 文件 | 数量 |
| --- | --- | --- |
| 领域类型与边界校验（拒绝式） | `tests/teams/test_models*.py` + 跨语言 fixture | WU-01 |
| 存储 / 修订 CAS / 追加式历史 / 幂等 | `tests/teams/test_repository.py` | WU-02 |
| 服务层引用校验与冻结快照 | `tests/teams/test_service.py` | WU-02 |
| 子执行结构化结果与取消 | `tests/teams/test_execution.py`、`tests/agent/test_subagent_lifecycle.py` | WU-03 |
| 前端读写通道 | `webui/src/tests/team-api.test.ts` | 8 |
| 前端管理页交互 | `webui/src/tests/teams-view.test.tsx` | 12（§5 后） |

i18n 口径：`webui/src/tests/i18n.test.tsx::keeps every locale aligned with the English resource
shape` 通过 —— 说明 `settings.teams.*`（48 键）与 `settings.nav.teams` 在 10 语言
（en / zh-CN / zh-TW / ja / ko / vi / fr / es / pt-BR / id）**键集与插值占位符完全对齐**，
无缺键、无多余键。

## 3. 验收矩阵覆盖情况（截至 G2）

| ID | 口径 | 状态 | 证据 |
| --- | --- | --- | --- |
| A01 | 创建、刷新、编辑团队 | **部分**（Task 8 前端 + Task 2 存储；Task 5 的 HTTP/WS 入口未落地） | `teams-view.test.tsx` 创建/编辑/复制/归档；`test_repository.py` 修订推进 |
| A02 | 空 Leader / 重复 / 越权成员 | **通过（本批可达范围）** | 后端 `test_service.py` 引用校验；前端 `拒绝没有 Leader` `成员越出 Leader 委派范围` 两例断言 `saveTeam` 未被调用 |
| A04 | 普通聊天与旧 spawn 行为不变 | **通过** | WU-03 后 `tests/agent` 全量绿；`_run_admitted_subagent` 保留字符串适配器语义 |
| A14 | 重复 mutation 同 key 同结果 | **通过（存储层）** | `test_repository.py` 幂等重放；`team-api.test.ts` 保证 requestId 送出 |
| A15 | 运行中改档案不影响活动快照 | **部分**（冻结快照已建，绑定尚未接） | `test_service.py::freeze` |
| A16 | 归档团队后历史可读 | **部分**（archive/引用查询已建） | `teams_referencing_profile`、`set_archived` 测试；前端确认框文案 |
| A19 | 零工具权限仍为空 | **通过** | `execution.py` 对 `tool_names is None` 直接拒绝，空集合保持为空 |
| A18 | 跨工作空间不可读别人的团队数据 | **部分（存储层，§5 后补齐）** | `test_teams_from_another_workspace_are_invisible`；`test_run_child_tables_are_scoped_to_the_owning_workspace`（事件/派单子表） |
| A03 / A05–A13 / A17 / A20 | 依赖 Task 4–7、9–11 | **未覆盖** | 按计划留给后续 GROUP，不在本批声称为绿 |

## 4. 风险与遗留（带入后续 GROUP）

1. **前端写通道尚未接通**：`teams.save/archive/clone` 的 mutation 白名单与
   `/api/settings/teams` 读路由属 Task 5（WU-05）。G2 的绿是「组件 + 通道契约」层绿，
   不是端到端绿。
2. **创建撞已有 ID 的提示文案**：服务端把主键冲突映射为 `TEAM_REVISION_CONFLICT`，
   前端统一显示「已被其他改动更新，请重新加载」。语义可接受（确实要重新加载），
   但对「新建重名」略含糊 → 记为 Task 11 的文案打磨项。
3. **plan §7 的 `bun` 口径**：本机不可用，已在 G0 与本文件用 `npx vitest run` 等价替代，
   需要 Leader/CI 在真实环境用 `bun run test` 复核一次。
4. **基线既有失败**（`test_mcp_reconnect_*`、`tests/agent` 4 处 F401、`test_serve_*`）
   按 plan §4 不在本批修，尾盘 Task 11 统一归因。

## 5. 尾盘审查后的修复与复验（HEAD `03377c4`）

集体测试之后做了两轮分离式复核（代码审查 + 安全审查，见
`reviews/2026-10-15-agent-team-g1g2-{code,security}-review.md`），共发现 3 处必须修的缺陷。
**每处都先写测试见红，再改实现见绿**，因此下表「见红证据」一栏是真实的失败输出，不是推断。

| # | 缺陷 | 修复提交 | 见红证据 | 复验 |
| --- | --- | --- | --- | --- |
| F1 | 编辑器保存后基线回填冲掉用户正在输入的草稿 | `42e89aa` | 新增用例先失败（草稿被重置成服务端值） | `npx vitest run src/tests/teams-view.test.tsx` 通过 |
| F2 | 保存时 `limits` 整体替换成默认值，`maxTasksPerRun` 等被静默重置 | `daf6e3e` | 同上 | `teams-view.test.tsx` **12 passed** |
| F3 | `team_events` / `team_dispatches` 无 `workspace_key` 列，`list_events`、`events_page`、`get_dispatch`、`record_dispatch` 按 runId 直查会跨工作空间命中（A18 High） | `03377c4` | `test_run_child_tables_are_scoped_to_the_owning_workspace` → `Failed: DID NOT RAISE RunNotFound` | 见下 |

F3 复验命令与实跑输出：

| 命令 | 输出 |
| --- | --- |
| `.venv/bin/python -m pytest tests/teams -q` | `284 passed in 1.74s` |
| `.venv/bin/python -m ruff check nanobot/teams tests/teams` | `All checks passed!` |
| `.venv/bin/python -m basedpyright nanobot/teams` | `0 errors, 0 warnings, 0 notes` |

修复后的全量前端门（`webui/`）：

| 命令 | 输出 |
| --- | --- |
| `npx vitest run` | `Test Files 116 passed (116)` / `Tests 1553 passed (1553)` |
| `npm run lint` | exit 0，无输出 |
| `npm run build` | exit 0，`✓ built in 5.35s` |

F3 附带收口：`prune_activity_events` 原先按 `type='task.activity'` 全库删除，跨 workspace 生效；
已加 `run_id IN (SELECT run_id FROM team_runs WHERE workspace_key = ?)` 限定。**该函数目前没有
调用方，这条修复没有专属测试**，WU-07 接线时必须补。
