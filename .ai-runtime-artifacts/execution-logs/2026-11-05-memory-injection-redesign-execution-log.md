---
artifact: execution-log
route: orchestration:dispatcher-workflow
skills:
  - writing-plans
  - orchestration
skills_evidence:
  - harness-kit/core/orchestration/dispatcher-workflow.md
source:
  - .ai-runtime-artifacts/plans/2026-11-05-memory-injection-redesign-plan.md
  - .ai-runtime-artifacts/plans/2026-11-05-memory-injection-redesign-dispatch.md
created_at: 2026-11-05
status: completed
---

# 记忆注入改造 — 执行日志

## 执行方式

Leader 单线程顺序执行（未派 worker、未建 worktree）：8 个 WU 在 `files.py` / `context.py` /
`loop.py` / `lifecycle.py` 之间存在数据流依赖，并行派发会在同一 checkout 产生交叉改动。
每个 WU 单独跑定向测试后再进入下一个。

## 变更清单

**新增**

- `nanobot/memory/files.py`：目录解析、记忆文件读写、索引维护、写锁、scope 判定、迁移。
- `nanobot/memory/extract_writer.py`：抽取产物 → 记忆文件（scope + importance 门槛）。
- `tests/memory/test_memory_files.py`
- `tests/memory/test_memory_injection.py`
- `tests/memory/test_memory_file_tools.py`
- `tests/memory/test_scheduled_extraction.py`
- `tests/memory/test_memory_migration.py`

**修改**

| 文件 | 改动 |
| --- | --- |
| `nanobot/config/schema.py` | 新增 `memory_file_mode`（默认 true）、`memory_extract_interval_minutes`（默认 30，最小 5） |
| `nanobot/agent/context.py` | `_load_memory_index_block()`；文件模式下核心事实不再进 prompt；索引块排在记忆块之后 |
| `nanobot/agent/loop.py` | 透传 `memory_file_mode`；记忆目录进工具装配；`run_scheduled_memory_extraction()`；抽取器挂文件写入回调 |
| `nanobot/agent/tools/filesystem.py` | 记忆目录进读/写白名单 |
| `nanobot/agent/tools/memory_admin.py` | 文件模式不注册四个记忆库工具；文案更新 |
| `nanobot/agent/hooks/memory_extraction.py` | `memory_file_mode` 时停用空闲抽取计时器 |
| `nanobot/memory/extractor.py` | 新增 `set_memory_file_writer()`；`run_scheduled_extraction()`；空闲/定时抽取共用实现 |
| `nanobot/memory/dream_audit.py` | `record_added/updated` 支持计数 |
| `nanobot/memory/lifecycle.py` | `file_mode` 开关：USER.md 自动区来自全局记忆文件；MEMORY.md 投影停写 |
| `nanobot/agent/memory.py` | Dream 工具集重做（文件工具 + 记忆目录）；提示词带记忆目录绝对路径 |
| `nanobot/cli/gateway_runtime.py` | cron `memory_extract` 注册与处理；Dream 审计改文件 diff 驱动；启动时迁移旧 MEMORY.md |
| `nanobot/webui/gateway_services.py` | identity 装配透传 `file_mode` |
| `nanobot/templates/AGENT.md` | 「记忆使用规范」整节重写；`add_memory` 引用替换；身份文件职责表更新 |
| `nanobot/templates/agent/identity.md` | Workspace 段与 Memory feedback 段重写 |
| `nanobot/templates/agent/dream.md` | 整体重写为「文件记忆整理者」 |
| `nanobot/templates/memory/MEMORY.md` | 改为 Qoder 式索引模板 |
| `docs/memory.md` | 按新架构重写（目录结构、写入分工、注入、开关与回滚） |

## 关键决策（实现中确认）

1. **文件名**：kebab-case；全中文名称退化为内容哈希 `m-<sha1[:8]>`，保证「同一事实 → 同一文件 → 追加」。
2. **scope 判定**：按记忆类型（`preference`/`rule` → 全局，其余 → 项目），不引入新判定模型。
3. **抽取双出口**：DB 写入后回调写文件；文件写失败只记日志，不影响抽取结果。
4. **Dream 审计**：改为记忆目录文件 diff 驱动（新增/修改/删除），不再依赖工具回调计数。
5. **USER.md 标记文案**：保持与 `lifecycle.USER_VIEW_BEGIN` / `USER_NOTE_BEGIN` 逐字一致（改动会打断手写区解析），因此 spec 的「USER.md 文案微调」未做。

## 验证

见 `.ai-runtime-artifacts/verifications/2026-11-05-memory-injection-redesign-verification.md`。

## 遗留

- 真实 LLM 端到端质量未验（额度受限）。
- WebUI 记忆页在文件模式下的实机行为未验。
- `nanobot gateway` 实机启动未验（cron 注册 + 迁移钩子只做了装配级单测）。
