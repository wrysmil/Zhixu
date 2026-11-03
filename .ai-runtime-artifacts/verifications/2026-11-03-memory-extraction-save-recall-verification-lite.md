---
artifact: verification-lite
route: verification-before-completion
skills: [verification-before-completion, test-driven-development]
created_at: 2026-11-03
status: passed
---

# 记忆抽取 / memory_save / 中文召回 / core-note 持久化 验证

## 变更范围

生产代码：
- `nanobot/memory/prompts.py`（抽取提示词重写）
- `nanobot/memory/profile_extractor.py`（问题/回忆/任务句式过滤 + 经验证据校验）
- `nanobot/memory/repository.py`（中文自然语言 LIKE 回退展开）
- `nanobot/memory/manual.py`（新增：显式保存写入路径）
- `nanobot/memory/lifecycle.py`（core-note 区域恢复）
- `nanobot/memory/extractor.py`（被拒候选计数可观测）
- `nanobot/agent/tools/memory_save.py`（新增：memory_save 工具）
- `nanobot/agent/loop.py`（工具 provider 装配）
- `nanobot/templates/agent/identity.md`、`nanobot/templates/AGENTS.md`、
  `nanobot/skills/memory/SKILL.md`、`docs/memory.md`、`docs/concepts.md`、
  `docs/architecture.md`（文档口径对齐）

测试：新增 4 个文件、修改 7 个文件。

## 自动化测试

```
.venv/bin/python -m pytest tests/memory tests/agent/tools -q
→ 1168 passed, 10 warnings
```

```
.venv/bin/python -m pytest tests/evaluation -q
→ 1349 passed, 155 warnings
```

```
.venv/bin/python -m pytest tests/agent/test_context_builder.py tests/agent/test_identity_template.py \
  tests/webui/test_memory_api.py tests/webui/test_memory_routes.py \
  tests/webui/test_memory_presentation_api.py tests/webui/test_identity_routes.py \
  tests/webui/test_refresh_md_route.py -q
→ 全部通过
```

新增回归：
- `tests/memory/test_natural_language_recall.py`：自然语言中文查询命中项目记忆；无关查询不误召回。
- `tests/memory/test_extraction_policy.py`：问题前提/任务指令不落画像；经验必须有本批证据。
- `tests/memory/test_manual_save.py`：显式保存落库、可检索、幂等。
- `tests/agent/tools/test_memory_save_tool.py`：工具参数、作用域、错误不泄露。
- `tests/memory/test_memory_save_tool_wiring.py`：AgentLoop 注册 `memory_save` 并真实落库。
- `tests/memory/test_lifecycle.py::TestLegacyMigration::test_core_note_region_is_recovered_when_db_row_is_missing`。

## 真实数据验证

1. 召回（真实 `state.db`，原句查询）：修复前 0 条 → 修复后命中
   `proj-zhizhou-pz-inc-027`（visible ids 分别为 `default+项目` 与 `default`）。
2. core-note 持久化（真实工作区 `~/.nanobot/workspace`）：
   - 备份 `state.db.bak-2026-11-03-0001`、`MEMORY.md.bak-2026-11-03-0001`；
   - 走官方 `MemoryLifecycle.write_memory_md` 后，SQLite 出现 `core-note:default`
     （`needs_review=false`）；
   - 再执行 `refresh_memory_md_sync` 返回 `{'status': 'skipped', 'reason': 'no_change'}`，
     手工笔记不再丢失。
3. 6 条测试记录：**未删除、未修改**，口径见
   `.ai-runtime-artifacts/research/2026-11-03-memory-extraction-audit.md`。

## 静态检查

- ruff（本次改动文件）：新文件与改动文件全部干净；`nanobot/memory/repository.py` 的
  N806 与 `tests/agent/test_identity_template.py` 的 F401 为 HEAD 已存在的问题（已用
  `git show HEAD:` 对比确认）。
- basedpyright：`nanobot/memory/manual.py` 0 error；`nanobot/agent/tools/memory_save.py` 0 error；
  `nanobot/memory/repository.py` 与 `nanobot/agent/loop.py` 的错误条数与 HEAD 基线持平
  （同一批诊断，行号位移），无新增。

## 未覆盖 / 遗留

- 6 条污染记录的数据治理未执行（等口径确认）。
- 中文召回依赖有界四字窗口，未引入分词器；若后续语料以长句为主，可能需要向量路兜底。
- `memory_save` 工具的可用性依赖运行中的桌面/网关进程加载新代码；本轮未重启用户进程。
