---
artifact: verification-lite
route: orchestration:dispatcher-workflow -> batch-closeout
skills:
  - verification-before-completion
skills_evidence:
  - ~/.agents/skills/verification-before-completion/SKILL.md
source:
  - .ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-plan.md
  - .ai-runtime-artifacts/execution-logs/HANDOFF.md
  - .ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-27-memory-workspace-scoping.md
created_at: 2026-10-27
tier: 2
status: passed
---

# 记忆工作区隔离改造 — 验证记录（Tier 2）

## 范围

本批 = WU-02（提取打标）/ WU-03（检索可见集合）/ WU-04（面板归属口径）/ WU-05（前端筛选与改归属）
+ Leader 直做的 3 处修复（见 §4）。改动 34 个已跟踪文件、+845/−109，另新增 5 个测试文件。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `uv run --no-sync pytest -q`（全量） | **1 failed / 10256 passed / 21 skipped**（289.97s）；唯一失败 `tests/agent/test_mcp_reconnect_crash.py::test_mcp_reconnect_during_shutdown_does_not_crash` 为**既有红灯**（在 HEAD `2b447c6` 的独立 worktree 上同样失败） |
| `uv run --no-sync pytest tests/memory tests/webui tests/agent -q` | 1 failed / 3787 passed / 2 skipped（90.30s）；同一条既有 MCP 红灯 |
| `uv run --no-sync ruff check nanobot/` | 11 条，**与本批基线逐条一致**（`cli/commands.py` I001、`memory/__init__.py` 7×F401、`memory/lifecycle.py` N806、`memory/repository.py` N806），0 新增 |
| `uv run --no-sync ruff check <本批全部 py 文件>` | 仅 1 条既有 N806（`memory/repository.py`），0 新增 |
| `uv run --no-sync basedpyright <16 个生产文件>` | 与 HEAD 逐条比对：**新增诊断 0**（after 独有集合为空） |
| `cd webui && npx vitest run` | **1931 passed / 2 skipped / 0 failed**（154 文件通过、2 文件跳过） |
| `cd webui && npm run build` | 退出码 0，built in 6.18s |
| `cd webui && npm run lint` | 3 条**外域既有**错误（`evaluation-status.tsx` 2 条 `rules-of-hooks`、`browser-url.ts` 1 条 `no-useless-assignment`），不在本批写域 |
| `git diff --check` | 通过 |

## 性能回归（对照 plan §5 基线）

同一生产查询（`repository.search_semantic_scored`，5 万行 / 10 个工作区）：

| 可见集合 | 修复前 | 修复后 |
| --- | --- | --- |
| 不过滤 | 23.3ms | 23.3ms |
| 1 个工作区 | **4866.6ms** | **9.2ms** |
| 生产形态（`default` + 1 个工作区） | — | **10.5ms** |

根因与修法见 `reviews/2026-10-27-memory-workspace-scoping-perf-review.md` §1；回归测试
`tests/memory/test_fts_workspace_filter_plan.py` 锁死「FTS5 必须是驱动表」这一不变量
（回退到旧 JOIN 即变红，已实测）。

## 未验证项

- **真机场景（plan Task 10 Step 4）未执行**：需要用户参与（起 gateway、开两个会话分别切到不同项目目录、
  验证互不可见 + 面板筛选 + 工作台归属标签）。本机未做浏览器端到端验证。
- `nanobot/memory/lifecycle.py:116` 的 MEMORY.md 派生过滤 `scope="user"` 恒空 —— 属独立缺陷（plan §6 R5），不在本批范围。
- `workspace_id` 的历史数据回填未做（按设计：`'default'` 即全局，无需回填）。

## 风险

- 面板详情/删除守卫按 plan §6 R4 放宽为「库内即可访问」（单库部署下等价）；跨库场景不存在（每 gateway 一个库）。
- 向量路过滤后候选可能不足 `limit`（plan §6 R2/R3，已接受；向量层默认关闭）。

## Next

- 集体测试与三层审查见 `verifications/2026-10-27-memory-workspace-scoping-collective-test.md`
  与 `reviews/2026-10-27-memory-workspace-scoping-*.md`。
