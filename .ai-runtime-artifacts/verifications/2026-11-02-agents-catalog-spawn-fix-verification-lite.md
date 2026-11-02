---
route: tier-1-leader-direct
artifact: verification-lite
date: 2026-11-02
topic: agents-catalog-spawn-fix
skills: source-driven-development, verification-before-completion
status: passed
---

# agents-catalog spawn 工具缺失修复 — Verification Lite

## 问题

桌面版 WebUI 打开 agents-catalog（Agent 档案设置页）时，`_tool_descriptors`
用独立 ToolContext（无 subagent_manager）走 `ToolLoader.load`，
`SpawnTool.create` 抛 `RuntimeError: SpawnTool requires an initialized
subagent manager`，被 loader 的 except 捕获后刷 ERROR traceback，
spawn 工具从目录中消失。附带 loguru `%s` 占位符不生效（loguru 是
`{}` 风格），日志字面显示 `%s`。

## 改动

1. `nanobot/webui/agents_api.py::_tool_descriptors`：构造 ToolContext 时
   注入仅带 workspace 的轻量 subagent_manager 替身
   （`SimpleNamespace(workspace=...)` + `cast`）。SpawnTool 只需
   `manager.workspace` 枚举 Agent 档案；AgentLoop 主路径注入不变。
2. `nanobot/agent/tools/loader.py`：5 处 loguru `%s` → `{}`
   （import/plugin/collision/register 四条日志路径）。

## 验证证据

- 直调 `_tool_descriptors(~/.nanobot/workspace, ToolsConfig())`：
  total 16 工具，`spawn present=True`，`scope=core / blocked=True`
  （主 Agent 专属语义保持）；日志正确渲染
  `Tool name collision: read_file from ReadFileTool overwrites existing`。
- `ruff check nanobot/webui/agents_api.py nanobot/agent/tools/loader.py`：
  All checks passed。
- pytest：
  - `tests/tools/test_tool_loader.py` + `tests/agent/test_tool_loader_scopes.py`
    + `tests/agent/test_subagent.py` = **46 passed**
  - `tests/webui/test_agents_api.py` + `test_agents_routes.py` +
    `test_agents_wiring.py` + `test_builtin_protection.py`
    = **96 passed, 1 skipped**

## 影响面与后续

- 仅影响 WebUI 设置页目录枚举路径，不改运行期行为。
- 用户需重启桌面版 / gateway 使修复生效（源码热改不 reload）。
