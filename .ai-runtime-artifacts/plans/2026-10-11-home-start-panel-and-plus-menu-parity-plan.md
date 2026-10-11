---
artifact: implementation-plan
route: writing-plans -> api-and-interface-design
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
  - harness-kit/.agents/skills/api-and-interface-design/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-10-11-home-start-panel-and-plus-menu-parity-dispatch.md
source:
  - .ai-runtime-artifacts/specs/2026-10-11-home-start-panel-and-plus-menu-parity-spec.md
  - .ai-runtime-artifacts/contracts/2026-10-11-contract-home-start-panel.md
  - .ai-runtime-artifacts/research/2026-10-11-deepseek-harness-homepage-parity-research.md
  - 用户四项决策确认（AskUserQuestion，2026-10-11 本会话）
created_at: 2026-10-11
status: draft
approved: false
tier: 2
---

# 首页「开始」右面板 + Composer「+」指令组 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: orchestration（Leader）+ subagent-driven-development / executing-plans（执行）。步骤用 `- [ ]` 勾选。当前仅计划，不派 worker、不改业务代码。

**Goal：** 对标 deepseek-harness，交付知序首页右侧「开始」面板（工作区文件 ⌘P / 新建终端多开 / 浏览器 ⌘T）与 Composer「+」菜单指令组（compact / permission / model / feedback），前后端集成闭环。

**Architecture：** 后端在既有 WS 命令路由（`inbound_commands.py`）与出站事件流（`outbound_wire.py`）上增量扩展终端通道，包装既有 `ExecSessionManager`（管道式、无 PTY）；新增 `/feedback` 命令与 workspace 维度只读文件端点。前端新增 `HomeStartPanel`（数据驱动 guide 卡片，挂首页 hero 右列），终端/浏览器作为 `SessionWorkbench` 新分区；`ComposerPlusMenu` 行模型扩展为 insert-token / action / popup-select 三类。

**Tech Stack：** Python 3.11 asyncio（nanobot gateway/WS）；React 18.3 + TS + Vite + Tailwind 3.4 + Radix + Vitest（webui）；新增前端依赖 `@xterm/xterm`（仅渲染输出）；Electron 壳 `desktop/main.cjs` 增 1 个 IPC。

## 1. 范围与边界

| 做 | 不做 |
| --- | --- |
| 终端 = 行式命令控制台（本地回显、回车提交、流式输出、ANSI 色、多开 ≤8） | 真 PTY、全屏 TUI、vim/top、密码输入 |
| 浏览器 = iframe + URL 白名单 + 拒嵌兜底外开 | Electron webview、代理抓取渲染 |
| 文件 = 复用现有树/预览，锚 default workspace | 全库搜索端点、文件写操作 |
| 「+」指令组 4 项 + goal/plan 状态点 | SessionWorkbench 三态重构、git 提交动作 |
| feedback 落 `feedback.jsonl` + session metadata | 外部上报渠道 |

安全红线：终端通道是新增执行面，必须过 `security/workspace_access.py` access_mode 与 ExecTool guard；iframe 白名单前后端各一份校验；路径参数复用 `workspace_files.py` 逃逸校验。

## 2. 文件结构与所有权

| WU | 新增 | 修改 | 测试 |
| --- | --- | --- | --- |
| A 后端终端 | `nanobot/webui/terminal_bridge.py` | `nanobot/webui/inbound_commands.py`（4 帧）、`nanobot/webui/outbound_wire.py`（4 事件）、`nanobot/channels/websocket/runtime.py`（如需分发） | `tests/test_webui_terminal.py` |
| B 后端 feedback+files | — | `nanobot/command/builtin.py`（`cmd_feedback` + palette 条目）、`nanobot/webui/ws_http.py`（`GET /api/workspace/files`、file-preview `scope` 参数）、`nanobot/webui/workspace_files.py`（锚点解析函数） | `tests/test_command_feedback.py`、`tests/test_webui_workspace_files.py` |
| C 前端开始面板 | `webui/src/components/home/HomeStartPanel.tsx` | `webui/src/App.tsx`（hero 右列挂载 + ⌘P/⌘T keymap）、`webui/src/lib/api.ts`（`fetchWorkspaceFiles`、preview scope 参数） | `webui/src/tests/home-start-panel.test.tsx` |
| D 前端终端 | `webui/src/components/workbench/TerminalSection.tsx` | `webui/package.json`（+`@xterm/xterm`）、`webui/src/lib/nanobot-client.ts`（4 帧方法+回调）、`webui/src/types.ts`（TerminalEvent）、`webui/src/hooks/useNanobotStream.ts`、`webui/src/components/workbench/SessionWorkbench.tsx`（Terminal+Browser 两分区接线） | `webui/src/tests/terminal-section.test.tsx` |
| E 前端浏览器 | `webui/src/components/workbench/BrowserSection.tsx`、`webui/src/lib/browser-url.ts` | `desktop/main.cjs`（`mira:open-external` IPC） | `webui/src/tests/browser-url.test.ts`、`webui/src/tests/browser-section.test.tsx` |
| F 前端「+」菜单 | `webui/src/components/thread/FeedbackPopover.tsx` | `webui/src/components/thread/ComposerPlusMenu.tsx`（行模型重构 + 指令组）、`webui/src/components/thread/ThreadComposer.tsx`（回调接线）、`webui/src/i18n/locales/{9 语言}/common.json`（**唯一 locales 属主**，键表见 §5） | `webui/src/tests/composer-plus-menu.test.tsx` |
| G 尾盘 | verification/review/security 产物 | 冲突修复 | collective-test |

冲突控制：`SessionWorkbench.tsx` 仅 D 写（E 交付组件由 D 挂载）；`App.tsx` 仅 C 写；locales 仅 F 写（C/D/E 所需卡片与分区文案键由 F 按 §5 键表一次补齐，C/D/E 只引用）。

## 3. Task / WU 明细

### Task 0：基线（Leader）

- [ ] 记录分支（feature/desktop-app）与既有未提交改动清单（`git status`），WORKTREE-INIT 或经用户豁免在主 checkout 实施；登记 execution-log。
- [ ] 基线命令：`cd webui && bun run test`、`bun run build`、`bun run lint`；`uv run --no-sync python -m pytest tests/test_webui_* tests/test_command_* -q`；记录退出码。

### Task A：后端终端通道

**Files:** Create `nanobot/webui/terminal_bridge.py`; Modify `nanobot/webui/inbound_commands.py`、`nanobot/webui/outbound_wire.py`; Test `tests/test_webui_terminal.py`

- [ ] Step 1 失败测试：`terminal_start` 经 bridge 创建 exec 会话并回 `terminal_started{status:"ok"}`；`terminal_write "echo hi\n"` 收 `terminal_output` 含 `hi`；`terminal_kill` 后收 `terminal_exit`；第 9 个终端 `limit_reached`；`restricted` 模式被 guard 拒绝 → stderr 输出 + `exit_code=-1`。
- [ ] Step 2 `pytest tests/test_webui_terminal.py -v` 确认 FAIL。
- [ ] Step 3 实现 `terminal_bridge.py`：

```python
class WebuiTerminalBridge:
    """Maps webui terminal_id -> ExecSession; no PTY, line-based."""
    def __init__(self, exec_manager: ExecSessionManager, bus_out): ...
    async def start(self, terminal_id: str, scope: WorkspaceScope) -> dict: ...
    async def write(self, terminal_id: str, data: str) -> None: ...   # 累积到 \n 提交
    async def kill(self, terminal_id: str) -> None: ...
    def list(self) -> list[dict]: ...
```

shell 选择：`os.environ.get("SHELL", "/bin/bash")`，Windows `cmd.exe`；cwd=`scope.project_path`；输出经 `_BoundedOutputBuffer` 轮询循环分片（≤8KB）发 `terminal_output{seq}`。
- [ ] Step 4 `inbound_commands.py` dispatch 增 4 个 type（契约 §1.1 帧名/字段完全一致）；`outbound_wire.py` 增 4 事件（契约 §1.2）。
- [ ] Step 5 pytest 全绿 + `ruff check nanobot/`；commit `feat(webui-terminal): ws terminal channel over exec sessions`。

### Task B：后端 feedback + workspace 文件端点

**Files:** Modify `nanobot/command/builtin.py`、`nanobot/webui/ws_http.py`、`nanobot/webui/workspace_files.py`; Test `tests/test_command_feedback.py`、`tests/test_webui_workspace_files.py`

- [ ] Step 1 失败测试：`/feedback "text"` 写 `feedback.jsonl` 一行 `{ts,session_key,text}` + metadata `feedbacks` 追加 + 返回确认；空参数返回用法；`GET /api/workspace/files` 无默认工作区 404 `no_default_workspace`；`path=../../etc` 400 `invalid_path`。
- [ ] Step 2 pytest 确认 FAIL。
- [ ] Step 3 `cmd_feedback`（注册与 `cmd_goal` 同模式，`builtin.py:917` 附近）+ `builtin_command_palette()` 增条目；存储路径 `config.data_dir / "feedback.jsonl"`，原子追加。
- [ ] Step 4 `ws_http.py` 路由表（:1218 区域）加 `GET /api/workspace/files`；`workspace_files.py` 抽 `resolve_workspace_anchor(scope_param) -> Path|None`，session 版与 workspace 版 handler 共用；file-preview 同规则加 `scope` 参数。
- [ ] Step 5 pytest + ruff 全绿；commit `feat(api): feedback command + workspace-scoped file listing`。

### Task C：前端「开始」面板 + 快捷键

**Files:** Create `webui/src/components/home/HomeStartPanel.tsx`; Modify `App.tsx`、`lib/api.ts`; Test `tests/home-start-panel.test.tsx`

- [ ] Step 1 失败测试：hero 视图（chatKey=null）渲染三卡片（标题/描述/快捷键来自条目数组 `{id,icon,title,desc,shortcut}`）；`fetchWorkspaceFiles` 404 时文件卡片显示「选择项目后使用」空态；⌘P/⌘T 触发 `openStartSection("files"|"browser")` 回调。
- [ ] Step 2 `vitest run src/tests/home-start-panel.test.tsx` FAIL。
- [ ] Step 3 `api.ts` 加 `fetchWorkspaceFiles(token, path?)`（镜像 `fetchSessionDirectory` :299，URL 换 `/api/workspace/files`）；`HomeStartPanel.tsx`：宽 22rem、可折叠、卡片点击回调上抛 App。
- [ ] Step 4 `App.tsx`：hero 分支（chatKey=null）main 右侧挂 `HomeStartPanel`；keymap（:1903 区域）加 ⌘P/⌘T（`e.metaKey||e.ctrlKey`，`preventDefault`，仅 chat view 生效），动作 = 展开右面板对应分区。
- [ ] Step 5 `bun run test && bun run lint` 全绿；commit `feat(webui): home start panel with workspace files entry`。

### Task D：前端终端分区

**Files:** Create `workbench/TerminalSection.tsx`; Modify `package.json`、`nanobot-client.ts`、`types.ts`、`useNanobotStream.ts`、`SessionWorkbench.tsx`; Test `tests/terminal-section.test.tsx`

- [ ] Step 1 失败测试：mock client 下，「+ 新建」发 `terminalStart(uuid)` 并出现新 tab；`terminal_output` 事件按 terminal_id 路由到对应 tab 的 xterm 写入；`terminal_exit` 显示退出码；8 tab 后新建按钮禁用（tooltip 文案键 `startPanel.terminalLimit`）；输入行回车 → `terminalWrite(id, line+"\n")` + 本地回显。
- [ ] Step 2 vitest FAIL。
- [ ] Step 3 `bun add @xterm/xterm`；`types.ts` 加 `TerminalEvent`（契约 §1.2 四事件）；`nanobot-client.ts` 加 `terminalStart/terminalWrite/terminalKill/terminalList` 与 `onTerminalEvent(cb)`（模式照抄 `setWorkspaceScope` :1039 帧发送）；`useNanobotStream.ts` 消费并入 store。
- [ ] Step 4 `TerminalSection.tsx`：tab 条 + 每 tab（xterm 只读区 + 输入行，↑↓ 历史）；`SessionWorkbench.tsx` 挂 Terminal、Browser 两分区（Browser 引 Task E 组件路径，E 未完成前用 `null` 占位 import 由 Leader 合并时保证顺序：D 在 E 之后合入）。
- [ ] Step 5 vitest + build 全绿（记录 bundle 体积变化）；commit `feat(webui): terminal section over ws exec-session bridge`。

### Task E：前端浏览器分区 + 桌面外开

**Files:** Create `workbench/BrowserSection.tsx`、`lib/browser-url.ts`; Modify `desktop/main.cjs`; Test `browser-url.test.ts`、`browser-section.test.tsx`

- [ ] Step 1 失败测试：`normalizeBrowserUrl` 用例表——`https://x.com` ok、`http://localhost:3000` ok、`http://127.0.0.1:8765` ok、`file:///etc/passwd` reject(scheme)、`javascript:alert(1)` reject(scheme)、`http://192.168.1.1` reject(private)、`http://169.254.1.1` reject(private)、`notaurl` reject(malformed)；组件：非法 URL 行内报错且不改 iframe src；4s 未 load 显示「外部打开」兜底。
- [ ] Step 2 vitest FAIL。
- [ ] Step 3 `browser-url.ts`（IP 判断用正则解析十进制段，覆盖 loopback/私网/保留段；不引新依赖）；`BrowserSection.tsx`：地址栏 + src 历史栈（后退/前进/刷新）+ `<iframe sandbox="allow-scripts allow-same-origin allow-forms allow-popups" referrerPolicy="no-referrer">`；外开 = `window.mira?.openExternal?.(url) ?? window.open(url,"_blank")`。
- [ ] Step 4 `desktop/main.cjs` ipcMain 增 `mira:open-external`（校验 `^https?://` 后 `shell.openExternal`），preload 暴露 `openExternal`。
- [ ] Step 5 vitest 全绿；commit `feat(webui): embedded browser panel with url allowlist`。

### Task F：「+」菜单指令组 + feedback + i18n

**Files:** Create `thread/FeedbackPopover.tsx`; Modify `ComposerPlusMenu.tsx`、`ThreadComposer.tsx`、9×`common.json`; Test `composer-plus-menu.test.tsx`

- [ ] Step 1 失败测试：菜单渲染出「指令」组（compact/permission/model/feedback 四行）；compact 行点击 → 调用注入的 `onRunCommand("/compact")` 且无会话时 disabled；permission popup 当前值勾选 = `access_mode`，选择 → `onSetAccessMode("restricted")`；model popup 选项来自 presets props，选择 → `onRunCommand("/model gpt-x")`；feedback 提交 → `onRunCommand("/feedback hello")`（换行折叠为 ` ⏎ `）。
- [ ] Step 2 vitest FAIL。
- [ ] Step 3 行模型重构（契约 §4 `PlusRow` 三类型），现有段（Skills/插件/MCP/Agents）行为不变；「添加」组 goal/plan 行加激活状态点 prop（goal 来自现有 `GoalStateWsPayload`）。
- [ ] Step 4 `ThreadComposer.tsx` 接线：`onRunCommand` = `client.sendSystemCommand`；permission 复用 `scopeWithAccessMode`（`lib/workspace.ts`）+ `client.setWorkspaceScope`；model 列表 = `SettingsPayload.model_presets`；`FeedbackPopover.tsx`（radix popover 多行输入）。
- [ ] Step 5 按 §5 键表补齐 9 locale；`bun run test && bun run lint`；commit `feat(webui): composer plus menu command group`。

### Task G：尾盘（Leader）

- [ ] 全量：`bun run test`、`bun run build`、`bun run lint`、`ruff check nanobot/`、`pytest tests/test_webui_terminal.py tests/test_command_feedback.py tests/test_webui_workspace_files.py -v`、`uv run --no-sync basedpyright`（CI 同档）。
- [ ] 浏览器人工/自动化验收（spec §9 五条）+ 截图落 `.ai-runtime-artifacts/verifications/`。
- [ ] 并行扇出 code-review + security-review（重点：终端执行面、iframe 白名单、路径逃逸）；collective-test 与 execution-log 收尾。

## 4. 验证命令汇总

```bash
cd webui && bun run test && bun run build && bun run lint
uv run --no-sync python -m pytest tests/test_webui_terminal.py tests/test_command_feedback.py tests/test_webui_workspace_files.py -v
ruff check nanobot/
```

## 5. i18n 键表（F 一次写入 9 locale，C/D/E 引用）

`startPanel.{title,files,filesDesc,terminal,terminalDesc,terminalLimit,browser,browserDesc,openProjectFirst,externalOpen,embedBlocked}`、`plusMenu.{groupAdd,groupCommands,compact,compactDesc,permission,permissionDesc,model,modelDesc,feedback,feedbackDesc,feedbackPlaceholder,feedbackSend}`。zh-CN 按截图文案（「工作区文件/浏览会话工作区的文件/新建终端/在会话工作区运行命令/浏览器/浏览网页/压缩以上对话内容/切换权限预设（沙箱模式与审批策略）/选择本会话使用的模型/发送关于当前会话的反馈」），其余语言沿用现有 common.json 对应风格。

## 6. Spec 自检

- spec §2-§7 全覆盖：A(终端)、B(feedback+files 锚点)、C(面板/快捷键)、D(终端 UI)、E(浏览器)、F(指令组+菜单行模型)、i18n §5；spec §9 验收 = Task G。
- 无 TBD；契约引用一致（帧名/事件名/端点/键表三处互查通过）。

## Next

**（计划已写入，暂停等确认 —— harness-kit/core/routing.md § 阶段门禁）**

- 认可 → 说「**开始实现**」，Leader 激活 orchestration：WORKTREE-INIT（或明示「不用 worktree」沿用上批）→ 按 dispatch 图派发 GROUP 1-3 → 尾盘。
- 调整 → 指出 WU 拆分/边界/优先级修改意见（例：先只做「+」菜单组、终端后置）。
