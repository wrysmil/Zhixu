---
artifact: collective-test
route: orchestration.dispatch
plan: .ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-plan.md
dispatch: .ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-dispatch.md
tracking: .ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-27-memory-workspace-scoping.md
created_at: 2026-10-27
batch_id: GROUP-2
verdict: PASS
---

# 记忆工作区隔离改造 — 集体测试（GROUP-2）

> **纪律：** 先 Load verification-before-completion；**先跑命令、再给结论**。下表命令均为 Leader 在本机重跑，
> 不是 WU 自报。

## 变更范围

- 写入侧：`nanobot/memory/extractor.py`、`nanobot/memory/provenance.py`
- 读取侧：`nanobot/memory/retrieval/**`、`nanobot/agent/context.py`、`nanobot/agent/loop.py`、`nanobot/agent/tools/memory_search.py`
- 存储层：`nanobot/memory/repository.py`、`nanobot/workspace_identity.py`
- 面板侧：`nanobot/webui/memory_api.py`、`memory_presentation.py`、`memory_routes.py`、`session_workbench.py`
- 前端：`webui/src/lib/{types,api}.ts`、`webui/src/components/settings/memory/**`
- 提取 hook：`nanobot/agent/hooks/memory_extraction.py`（Leader 补修归属透传）

## 执行模型偏差

dispatch 原定「顺序执行」，理由是 worktree 播种/移植机制；该机制已于 2026-10-27 19:55 随 WU-01c/01d
取消（代码全部落在主 checkout 当前分支）。dispatch §并行边界表的文件归属表经复核**互不相交**，
故本批按 AGENTS.md「独立并行子任务」改回 **4 路并行**（WU-02/03/04/05）。
实测 4 个子 Agent 写域零交叠，无冲突。

## WU 已覆盖项（引用，非替代）

| WU | 先红 → 后绿 |
| --- | --- |
| WU-02 | `tests/memory/test_extractor_workspace.py` 10 failed/3 passed → **13 passed**；`tests/memory` 全量绿 |
| WU-03 | 检索/可见集合 4 文件 18 failed/20 passed → **193 passed** |
| WU-04 | `tests/webui` 23 failed/1132 passed → **1155 passed / 2 skipped** |
| WU-05 | `vitest memory-workspace-filter api-memory` 8 failed/13 passed → **29 passed / 1 skipped** |

## 命令表（Leader 本机重跑）

| 命令 | cwd | exit | 关键输出 |
| --- | --- | --- | --- |
| `uv run --no-sync pytest -q` | 仓库根 | 1 | 1 failed / **10256 passed** / 21 skipped（289.97s） |
| `uv run --no-sync pytest tests/memory tests/webui tests/agent -q` | 仓库根 | 1 | 1 failed / 3787 passed / 2 skipped（90.30s） |
| `uv run --no-sync ruff check nanobot/` | 仓库根 | 1 | 11 条既有，0 新增 |
| `uv run --no-sync basedpyright <16 生产文件>` | 仓库根 | 1 | 新增诊断 **0** |
| `npx vitest run` | webui | 0 | **1931 passed / 2 skipped / 0 failed** |
| `npm run build` | webui | 0 | built in 6.18s |
| `npm run lint` | webui | 1 | 3 条外域既有错误，不在本批写域 |
| `git diff --check` | 仓库根 | 0 | 通过 |

## 集成 / E2E

- 后端全量：唯一失败 `tests/agent/test_mcp_reconnect_crash.py::test_mcp_reconnect_during_shutdown_does_not_crash`
  —— 在 HEAD `2b447c6` 的独立 worktree 上以同一 venv 复现同样失败，**属既有红灯，非本批引入**。
- 跨工作区不可见的纯内存检查：FTS5 / LIKE / 向量独有 / 混合 / 近期 / episode 六条路径，
  在 `None` / 空元组 / 仅全局 / 全局+当前工作区四种集合下共 24 项全通过（审查员复核）。
- **真机场景未做**（需用户参与）：起 gateway → 两个会话切到不同项目 → 验证互不可见、
  面板工作区筛选、工作台归属标签。见 verification-lite §未验证项。

## 尾盘审查发现与处置

| # | 来源 | 级别 | 问题 | 处置 |
| --- | --- | --- | --- | --- |
| 1 | 代码审查 | **P1** | 生产 idle/增量提取重建 Session 时丢失 `workspace_scope` → 记忆落成全局 → 跨工作区泄漏 | **已修**：hook 携带归属作用域；loop 侧抽出 `_memory_workspace_project_path` 作为读写唯一真相源；新增 `tests/memory/test_hook_workspace_scoping.py`（6 用例，回退即变红） |
| 2 | 性能审查 | **P1** | 工作区过滤把 FTS5 查询计划改成「索引驱动 + 逐行全扫 FTS5」，5 万行单工作区 25ms → **4867ms** | **已修**：两处 FTS 查询改 `CROSS JOIN` 强制 FTS5 驱动 → **9.2ms**；新增 `tests/memory/test_fts_workspace_filter_plan.py`（3 用例，回退即变红） |
| 3 | 代码审查 | P2 | 面板归属以快照为准，与列筛选/编辑校验不同源（历史行「列全局、快照项目」） | **已修**：`PresentationContext.workspaces` 改为以行的 `workspace_id` 列为归属真相，快照只补名称；补 2 条测试 |
| 4 | 代码审查 | P2 | 受管渠道无显式作用域时，读侧回退默认执行目录，与写入侧口径不同 | **已修**：`_has_explicit_workspace_scope` + 显式作用域优先，无显式归属一律只见全局（D3）；补 2 条测试 |
| 5 | 性能审查 | P2 | 先截断后过滤，可见候选被丢弃且不补齐（R3） | **不修**：plan §6 R2/R3 已明确接受该折中（向量层默认关闭），记录为已知折中 |
| 6 | 性能审查 | P2 | 编辑态保存总带 `workspace` → 后端每次全库读 + 重建 payload | **不修**：设置页成本、非对话热路径；记入遗留风险 |
| 7 | 性能审查 | P3 | 向量独有候选逐条回查（N+1） | **不修**：HEAD 已存在该形态，正常上限内最多 45 条、实测 <1ms |
| 8 | 代码审查 | P3 | 派生归属（路径哈希回退）缺少提示 | **不修**：本批已用「未登记工作区 {id[:8]}」占位名标注，留待 UI 专项 |

## 结论

**verdict: PASS** —— 4 个 WU 的目标全部达成；2 个 P1 已在尾盘修复并各自补了会变红的回归测试；
1 个既有红灯（MCP 重连）已用独立 worktree 证明与本批无关。

## Next

- 真机场景验证需用户参与（见上）。
- 提交由 Leader 按 `harness-kit/project.git.md` 统一执行（不自动 push）。
