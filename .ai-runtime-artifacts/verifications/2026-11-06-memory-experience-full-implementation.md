# 记忆 + 经验库完整实现验证（2026-11-06）

- 分支：`feature/memory-experience-mvp`（在 MVP 提交 `c8c6740` 之后继续）
- 范围：计划 Phase 1.4 → Phase 6（提示词、抽取分流、USER.md、经验检索、Dream、WebUI、迁移）
- 原则：长期规则只进 `MEMORY.md`；任务经验只进 SQLite `episodes`；不做文件/SQLite 同步

## 1. 已完成

| 阶段 | 结果 |
| --- | --- |
| Phase 1.4 | `AGENT.md` / `identity.md` / 根 `AGENTS.md` 改为“长期记忆正文 + 经验库”；加入主动沉淀询问 |
| Phase 1.5 | 抽取产物写入 `MEMORY.md` bullet；文件模式不再写 `memories`；`write_memory`/`delete_memory` 不再重写索引 |
| Phase 1.6 | 文件模式下 USER.md 停止从记忆正文推导；lifecycle 缓存键加入 `file_mode` |
| Phase 2/3 | `memory_search` 收敛为经验检索并支持 `outcome`；经验不参与衰减的回归测试通过 |
| Phase 4 | Dream 改为“记忆管理员 + 经验复盘员”；新增只读 `experience_query`；`memory_dream_runs.metadata` 落复盘摘要 |
| Phase 5 | 后端新增 MEMORY.md 文档读写、经验列表 API；前端新增“长期记忆 / 经验库 / 对话摘要”三个页签 |
| Phase 6 | 新增 `migrate_memories`（dry-run/apply）；归档旧行并写迁移指针；更新 `docs/memory.md` |

## 2. 验证命令与结果

```bash
# 后端影响面（348 个用例）
pytest tests/memory/test_memory_experience_mvp.py tests/memory/test_memory_files.py \
       tests/memory/test_user_profile_projection.py tests/memory/test_extractor.py \
       tests/memory/test_memory_migration.py tests/agent/test_dream_audit.py \
       tests/agent/test_dream.py tests/agent/test_dream_tools.py \
       tests/webui/test_memory_api.py tests/webui/test_memory_routes.py \
       tests/agent/test_context_builder.py tests/agent/test_memory_admin_tools.py -q
# 348 passed

# 前端记忆页（13 个用例）
cd webui && CI=true node_modules/.bin/vitest run src/tests/memory-presentation.test.tsx src/tests/memory-long-term-experience.test.tsx
# 13 passed（EPERM results.json 是 Windows 缓存写权限噪音，不影响用例结果）

# 端到端（假模型 + 真实 agent 两轮）
python .ai-runtime-artifacts/verifications/2026-11-06-memory-experience-e2e.py
# requests=4, experience_rows=1, memory_rows=0
# 第二轮 system prompt 出现 MEMORY.md 正文
# PASS
```

`ruff check` 覆盖本次修改的 Python 文件：全部通过。

## 3. 未验证 / 已知边界

- 没有用真实模型验证提示词遵循度（E2E 用假 provider 精确控制工具调用）。
- 没有在真实 `~/.nanobot` 数据上执行 `migrate_memories --apply`；迁移脚本只在临时库验证。
- 没有运行 `bun run build`（按约定由用户环境验证构建）。
- 前端新页面已有长期记忆/经验库组件测试，但未运行 `bun run build`（按约定由用户环境验证构建）。
- `memories` 旧行迁移后保留为归档行；是否物理清理由后续决定。

## 4. 迁移使用方式

```bash
# 预演，不写文件和库
python -m nanobot.memory.migrate_memories --dry-run

# 确认后执行（按需指定 workspace）
python -m nanobot.memory.migrate_memories --apply --workspace-id default
```

迁移规则：`fact/preference/rule` → MEMORY.md；`experience/error` → 经验库；`skill` 只报告；短期待办/已过期行只归档。旧行设置 `expires_at='1970-01-01T00:00:00Z'` 并写 `metadata.migrated` 指针。
