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

## 实机验证（WebUI + 真实 gateway，2026-11-05）

环境：本机已在跑的 gateway（PID 53776，端口 8765 / 健康 18790），启动时已加载新代码——启动迁移在 `~/.nanobot/memory/` 生成了
`legacy-*.md` 记忆文件与 `legacy/MEMORY.md` 备份。用 Playwright（chromium headless）驱动 WebUI 做三轮真实对话。

| 轮次 | 输入 | 结果 |
| --- | --- | --- |
| 1 | 「请记住：这个项目的验证约定是——提交信息一律用中文。」 | 模型的推理里直接引用了索引条目 `legacy-feedback-3.md — 用户要求提交信息（git commit message）统一使用中文`，据此判断「已有记忆、不重复写」→ **索引注入生效** |
| 2 | 「请记住：知序 WebUI 实机验证的临时约定——回答一律先给结论，再给理由。」 | 写出 `~/.nanobot/projects/<workspace-id>/memory/webui-verification-conclusion-first.md`（frontmatter 正确）+ 同目录 `MEMORY.md` 索引行 → **项目级文件写入 + 索引维护生效**（模型经 `exec` 调用 `MemoryFileStore` 完成） |
| 3 | 明确要求「用 write_file 写全局记忆目录 + edit_file 追加索引行，不要用 exec」 | 直接写出 `~/.nanobot/memory/webui-tool-write-check.md`（frontmatter：name/description/metadata.type）+ `MEMORY.md` 末尾追加索引行（第 13 行）→ **工具白名单路径可用** |

截图：`C:/Users/Huangqh/AppData/Local/Temp/zhixu-memtest/webui-3.png`（WebUI 对话页，右侧工作台显示产出 2 条）。

**观察到的两点（非缺陷，已处理）**

1. 本机 `C:\Users\Huangqh\.nanobot` 是 `D:\studyspace\project\Zhixu\.ai-runtime-artifacts\nanobot-data` 的链接/映射，
   所以工具回显的写入路径与提示词里的 `~/.nanobot/...` 不一致，但读写同一份存储（回读一致）。
2. 索引行标题被截断（旧的 40 字上限切断句子）→ 已改为「description 优先、60 字上限带省略号、标题与钩子相同则不重复」，并重建了现有索引。

**清理**：两轮测试写入的记忆条目（全局 `webui-tool-write-check`、项目 `webui-verification-conclusion-first`）已删除，
两份索引已重建。

## 未验证项

- 真实 LLM 端到端：定时抽取（cron `memory_extract`）与 Dream 的实际模型输出质量（需等到 cron 触发或手动触发）。
- WebUI 记忆页的 MEMORY.md / USER.md 编辑入口在文件模式下的行为（未点开验证）。
