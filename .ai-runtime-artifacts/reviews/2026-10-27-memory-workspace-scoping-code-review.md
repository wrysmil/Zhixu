---
artifact: code-review
route: orchestration.dispatch -> batch-closeout
plan: .ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-plan.md
created_at: 2026-10-27
reviewer: native-subagent (code-review lane)
verdict: APPROVE
---

# 记忆工作区隔离改造 — 代码审查

## 审查范围与方式

只读审查本批（WU-02/03/04/05 + Leader 补修）的全部未提交变更，对照 plan §2 决策 D1–D9 与 §6 风险 R1–R11。

## 初次结论：BLOCK（1×P1、3×P2、1×P3、0×P0）

### P1（已修）生产 idle 提取丢失工作区 → 项目记忆全局可见

- 位置：`nanobot/agent/hooks/memory_extraction.py`（`_run_idle_extraction` / `_run_incremental_extraction` 重建 `Session`）
- 触发 → 后果：重建的 Session 只复制 messages，不带 `metadata.workspace_scope` → `session_memory_workspace_id` 退化为 `'default'`
  → memory 与 episode 都标成全局 → **其它工作区可召回本工作区的记忆**（跨工作区泄漏）。
- 修法：hook 构造 Session 时带上归属作用域；loop 侧抽出 `_memory_workspace_project_path` 作为读写唯一真相源，
  提取 hook 通过 `workspace_scope_provider` 取同一口径的归属。
- 回归：`tests/memory/test_hook_workspace_scoping.py`（6 用例）；**去掉归属透传即变红（已实测）**。

### P2（已修）展示归属以快照为准，与列筛选/编辑校验不同源

- 位置：`nanobot/webui/memory_presentation.py::PresentationContext.workspaces`
- 触发 → 后果：历史行「列 = `default`、快照指向项目」→ 面板显示项目标签，实际却全局可见；
  旧快照 id 进入筛选下拉但筛不出东西，改归属时报 `invalid workspace`。
- 修法：归属以行的 `workspace_id` 列为准（列与快照同源，D4），快照只补充名称/路径；
  缺快照时按列 id 生成「未登记工作区 {id[:8]}」占位。
- 回归：`tests/webui/test_memory_presentation_api.py` 补 2 条 + 改写 1 条旧用例。

### P2（已修）受管渠道无显式作用域时读侧与写侧口径不同

- 位置：`nanobot/agent/loop.py::_memory_workspace_ids_for_turn`
- 触发 → 后果：消息与会话都无 `workspace_scope` 时，读侧经 resolver 回退到默认执行目录 → `("default", 默认目录ID)`，
  而写入侧（会话 metadata）只有 `"default"` → 同一会话两套可见集合。
- 修法：抽出 `_has_explicit_workspace_scope`，**没有显式归属一律只见全局**（D3）。
- 回归：`tests/agent/test_memory_visibility.py` 补 2 条。

### P2（不改）工具侧缓存解析器仍可能读写磁盘

- 位置：`nanobot/agent/loop.py::_memory_workspace_ids_for_key`
- 说明：会话本身只读内存缓存（`SessionManager.get_cached` → `_cached`，无落盘）；但缓存未命中时会继续走
  `workspace_identity_id`，而后者**会**读写 marker 与注册表（首次创建 marker 实测 1.27ms）。
- 判定：不改。身份解析是冷路径（每轮一次、进程内缓存命中后 5.76µs），改成完全无 I/O 会牺牲「稳定 id」的初衷；
  已在 perf 审查中量化为可接受。

### P3（不改）派生归属缺少提示

- 位置：`webui/src/components/settings/memory/MemoryPresentation.tsx::WorkspaceLabels`
- 判定：留待 UI 专项；本批已用「未登记工作区 {id[:8]}」占位名标注派生归属。

## 复核通过项（审查员逐条确认）

- FTS5 / LIKE / 向量独有 / 混合 / 近期 / episode 六条路径，在 `None` / 空元组 / 仅全局 / 全局+当前工作区
  四种集合下共 **24 项纯内存检查通过**；正常向量候选路径未发现跨工作区泄漏。
- `None`=不过滤、空元组=恒假 `0 = 1`、非空集合三态在 repository → store_adapter → channels → engine →
  context → loop → tool 全链一致，无「`None` 被当空集合」或「空集合被当不过滤」。
- D8 偏离实现正确：省略 `workspace` 保留归属，显式 `"default"` 改为全局；
  `store_workspace_id` / `trigger_mode` / `generation_method` / `origin_kind` 保持原值；SQL 用占位参数。
- 旧测试拆分（同工作区合并 / 跨工作区不合并）符合新契约，未见为让实现通过而削弱断言。
- `git diff --check` 通过；ruff 仅报 HEAD 已存在的 `repository.py` N806。

## 结论

**verdict: APPROVE** —— 初次审查提出的 1 个 P1 与 2 个 P2 全部修复并各配会变红的回归测试；剩余 1 个 P2 与 1 个 P3
经复核判定为可接受并记录理由。
