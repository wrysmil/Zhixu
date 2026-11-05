---
artifact: verification
route: superpowers:verification-before-completion
skills:
  - verification-before-completion
skills_evidence:
  - .claude/skills/verification-before-completion/SKILL.md（按「先证据后断言」执行）
source:
  - .ai-runtime-artifacts/plans/2026-11-05-memory-injection-redesign-plan.md
  - .ai-runtime-artifacts/specs/2026-11-05-memory-injection-redesign-spec.md
created_at: 2026-11-05
status: completed
---

# 记忆注入改造 — 验证记录

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `uv run --no-sync python -m pytest tests/memory tests/agent tests/identity tests/config tests/webui -q` | **4142 passed, 6 skipped, 4 failed**（4 个失败均为基线既有，见下） |
| `uv run --no-sync python -m pytest tests/memory -q` | 1037+ 通过，0 失败（改造相关用例全绿） |
| `uv run --no-sync ruff check nanobot/` | 4 errors，**均为基线既有**（`nanobot/cli/commands.py` I001、`nanobot/memory/__init__.py` F401×3），本次改动文件 0 告警 |
| `uv run --no-sync basedpyright nanobot/memory/files.py nanobot/memory/extract_writer.py` | **0 errors** |
| `uv run --no-sync basedpyright nanobot/agent/loop.py nanobot/agent/context.py nanobot/agent/memory.py nanobot/memory/lifecycle.py nanobot/memory/extractor.py` | 65 errors（基线 63 errors，新增 2 个已消除 → 最终 **63 = 基线**） |

### 基线既有失败（与本次改动无关，已用 worktree 对照 HEAD 复核）

```
tests/agent/test_mcp_reconnect_crash.py::test_mcp_reconnect_during_shutdown_does_not_crash
tests/agent/test_session_atomic.py::TestAtomicSave::test_provider_state_does_not_consume_list_preview_budget
tests/agent/test_turn_agent_profile.py::test_mentioned_agent_applies_prompt_tools_and_model
tests/webui/test_session_list_index.py::test_webui_session_list_skips_provider_state_before_preview_budget
```

复核方式：`git worktree add /tmp/zhixu-base HEAD` → 在 HEAD 基线上跑同样 4 条 → 同样 4 条失败（另复核 `tests/webui/test_refresh_md_route.py` 子集导入顺序导致的循环导入，基线同样失败）。

## 新增测试

| 文件 | 覆盖 |
| --- | --- |
| `tests/memory/test_memory_files.py` | 文件读写、同名追加、作用域隔离、删除、索引块渲染、类型映射、名称规范化（全中文 → 内容哈希） |
| `tests/memory/test_memory_injection.py` | 文件模式注入两份索引与目录路径；核心事实不进 prompt；关闭开关回到旧路径；项目索引作用域 |
| `tests/memory/test_memory_file_tools.py` | 记忆目录可写、其它目录被拒；文件模式下 `add_memory` 不注册 |
| `tests/memory/test_scheduled_extraction.py` | 偏好→全局、事实→项目、importance 门槛过滤、同 subject 追加同一文件 |
| `tests/memory/test_memory_migration.py` | 旧 MEMORY.md 拆分迁移、备份、幂等 |

## 改写的既有测试（契约变了）

- `tests/agent/test_dream.py`、`tests/agent/test_dream_audit.py`：Dream 工具集改为
  `read_file/edit_file/apply_patch/write_file/grep/find_files`；记忆库工具不再注册；
  dream.md 路由改为全局/项目记忆目录。
- `tests/agent/test_identity_template.py`：identity.md 不再文档化 `add_memory`，改为文档化记忆文件写入。
- `tests/identity/test_identity_compiler.py`：AGENT.md 编译产物断言改为「记忆文件 / 索引」语义。
- `tests/agent/test_context_builder.py`、`tests/memory/retrieval/test_integration_context.py`、
  `tests/memory/test_reference_tracking.py`：核心事实注入路径显式 `memory_file_mode=False`。

## 未验证项

- 真实 LLM 端到端：定时抽取与 Dream 的实际模型输出质量（需真实额度；本机评测受 429 限制）。
- WebUI 实机：记忆页的 MEMORY.md / USER.md 编辑入口在文件模式下的行为（未开浏览器验证）。
- gateway 真实启动：`nanobot gateway` 的 cron 注册与迁移启动钩子未实机跑（只做了单测覆盖装配路径）。
