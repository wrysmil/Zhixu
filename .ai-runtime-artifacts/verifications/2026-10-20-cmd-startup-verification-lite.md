---
artifact: verification-lite
route: leader-direct
skills: [systematic-debugging, source-driven-development, test-driven-development, verification-before-completion]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/systematic-debugging/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - C:/Users/Huangqh/.codex/skills/test-driven-development/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - 用户报告 CMD 一直处于启动中并要求修复排查
  - .ai-runtime-artifacts/verifications/2026-10-20-sidebar-tools-v1.2.md
  - .ai-runtime-artifacts/plans/2026-10-20-sidebar-tools-v1.2-plan.md
created_at: 2026-10-20
tier: 1
status: passed
---

# Windows CMD 启动恢复验证

## 上下文与边界

- 已先扫描相关 specs/plans/decisions/contracts/research，并阅读侧栏 v1.2 计划、验证记录、project.profile.md、context-map.md。
- 本次是独立的有界缺陷调查与运行恢复，不重新执行已有侧栏 v1.2 dispatch，不扩展侧栏功能。
- 调查入口：StartDock → ZhixuClient → WebUICommandRouter → TerminalSupervisor → Windows pipe bridge。
- 保留工作区原有大量未提交改动；不提交、不推送、不更改配置和凭据。

## 原因与实际处理

Windows 的 PTY bridge 为 None。旧版 TerminalSupervisor._live_count 直接读取 self._pty._terminals，创建终端时触发 AttributeError。WebSocket 连接处理捕获异常后结束连接，终端没有收到 terminal_started；前端记录继续显示 starting。

本轮开始时，工作区已有过滤 None bridge 的兼容修复，但常驻网关仍是 15:42:49 启动的 PID 40904；对应源码最后修改于 16:04:58。已有侧栏验证记录也明确保留了“终端真实启动未通过”的缺口。

本轮没有覆写已有修复，新增真实 Windows CMD 回归用例，并使用项目 gateway restart 重新加载当前代码。新网关 PID 51808，状态 running，管理端口 18790、WebUI 端口 8765 恢复监听。

## 验证证据

| 检查 | 结果 |
| --- | --- |
| 旧逻辑复现 | 仅在测试进程内，从 Git HEAD 提取旧 _live_count 并临时替换；真实 CMD 回归用例以 AttributeError: NoneType has no attribute _terminals 失败。没有回退工作区源码。 |
| 当前逻辑回归 | `.venv/Scripts/python.exe -m pytest tests/webui/test_terminal_supervisor.py tests/webui/test_terminal_scrollback.py tests/test_webui_terminal.py tests/test_webui_pty_terminal.py -q`：16 passed, 46 skipped in 1.23s。 |
| 静态检查 | `.venv/Scripts/python.exe -m ruff check nanobot/webui/pty_terminal.py tests/webui/test_terminal_supervisor.py`：All checks passed。 |
| 网关健康 | `.venv/Scripts/python.exe -m nanobot gateway status`：Running yes、Reason running、PID 51808。 |
| 浏览器实测 | Chrome 新建本地验证页，首页展开开始面板 → 新建终端，正常出现 cmd.EXE 提示符；输入 echo ZHIXU_CMD_READY，实际输出 ZHIXU_CMD_READY，并返回提示符。 |

新增测试不模拟 shell：使用真实 ExecSessionManager 和 CMD，覆盖启动、非 PTY 模式、命令返回的独立输出行、同 ID 接管与输出快照、关闭与 exit 事件，并在 finally 中清理进程。

红绿验证已完成；没有创建测试或实现提交。46 个跳过用例为 POSIX 专用，在 Windows 上预期跳过。未重跑整个仓库套件，未修改前端代码，未重新打包 Electron。

截图：[CMD 实际运行](2026-10-20-cmd-startup-fixed.png)。

## Next

- 已恢复可用；旧页面如保留 starting 状态，刷新后关闭旧 CMD 标签，再新建终端。
- 本轮未提交、未推送。
