# 记忆 + 经验库 MVP 验证（2026-11-06）

- 分支：`feature/memory-experience-mvp`（从 `feature/desktop-app` 新建）
- 状态：最小切片通过，未提交
- 原则：只验证“长期记忆正文 + SQLite 经验库”两条主链路；不迁移旧数据、不改 WebUI、不改提示词

## 1. 本次改动

| 文件 | 改动 |
| --- | --- |
| `nanobot/memory/files.py` | 新增 `MEMORY_DOC_MAX_CHARS`、`read_memory_doc`、`write_memory_doc`、`append_memory_section`、`render_memory_content_block`；旧索引格式自动回退 |
| `nanobot/agent/context.py` | 注入优先使用 MEMORY.md 正文；没有正文时回退旧索引 |
| `nanobot/memory/models.py` | 新增 `EpisodeSource.EXPERIENCE` |
| `nanobot/memory/experience.py` | 新增 `record_experience` / `list_experiences` / `ExperienceRow` |
| `nanobot/agent/tools/memory_write.py` | 新增 `memory_write` 工具 |
| `nanobot/agent/tools/experience.py` | 新增 `experience_record` / `experience_search` 工具 |
| `tests/memory/test_memory_experience_mvp.py` | 7 个 MVP 验证用例 |

## 2. 验证结果

```bash
pytest tests/memory/test_memory_experience_mvp.py -v
# 7 passed

pytest tests/memory/test_memory_files.py tests/memory/test_memory_injection.py \
       tests/agent/test_context_builder.py tests/agent/test_memory_admin_tools.py -q
# 114 passed
```

工具自动发现验证：

```text
memory_write True
experience_record True
experience_search True
```

## 3. 已证明的行为

1. `MEMORY.md` 可以按正文写入/读取，超过上限（全局 3000 / 项目 6000 字符）直接拒绝。
2. `ContextBuilder` 的注入路径能读到 MEMORY.md 正文；旧索引格式仍能回退，不会在迁移期丢内容。
3. `memory_write` 工具能把规则写进 MEMORY.md。
4. `experience_record` 能把失败/任务经验写进 SQLite `episodes`（`source=experience`）。
5. `experience_search` 能按关键词召回经验，且不同 `workspace_id` 互相隔离。
6. 经验记录包含标题、现象、根因、解法、验证方式，检索结果能带出根因/解法。
7. 新工具能被 `ToolLoader` 自动发现，不需要手工注册。

## 4. 本次没有验证的部分

- 真实模型对话中的提示词遵循度（提示词还没改）；
- 抽取器停止写 `memories`、旧数据迁移、Dream 复盘；
- WebUI 长期记忆/经验库分栏；
- 向量索引清理、并发编辑冲突；
- 真实用户的 `~/.nanobot` 数据（本次全部使用临时目录/临时 DB）。

## 4.1 补充：真实 agent 两轮 E2E（假模型）

脚本：`.ai-runtime-artifacts/verifications/2026-11-06-memory-experience-e2e.py`

做法：本地起一个 OpenAI-compatible 假服务，用隔离的 HOME / workspace / config 跑两次
`python -m nanobot agent`；第一轮让假模型依次发起 `memory_write` 和 `experience_record`，
第二轮检查 system prompt 与数据库。

```text
requests=4
experience_rows=1 memory_rows=0
# 第二轮 system prompt:
# 长期记忆（正文）
# ## 项目长期记忆
# - 构建用 bun
PASS: memory_write -> next-turn injection; experience_record -> SQLite; no dual write
```

结论：

- 第一轮 agent 真实执行了两个工具；
- 第二轮 system prompt 出现了 MEMORY.md 正文，且没有出现旧索引块；
- `episodes` 有 1 条 `source=experience`，`memories` 没有写入同一条规则；
- 静态提示词里仍有“记忆索引”字样（提示词尚未改），这是下一步 Phase 1.4 要处理的。

## 5. 下一步建议

1. 用这版 MVP 做一次真实对话：让 agent 调 `memory_write` 和 `experience_record`，确认下一轮注入与召回。
2. 通过后再进入 Phase 1 的提示词与抽取器改造。
3. 若要继续，建议先把 MVP 提交到当前分支，再按计划逐 Task 推进。
