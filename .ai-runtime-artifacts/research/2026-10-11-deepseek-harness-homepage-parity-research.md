---
artifact: research
route: source-driven-development -> brainstorming
skills:
  - source-driven-development
created_at: 2026-10-11
topic: deepseek-harness-homepage-parity
source:
  - 并行 Explore 调研 ×2（deepseek-harness 参考实现 / zhixu 现状盘点），全部结论带路径证据
  - 用户截图 ×3（开始页右面板 / 红框标注 / Composer「+」上拉菜单）
---

# 调研：deepseek-harness 首页右面板 + Composer「+」菜单 ↔ zhixu 现状差距

## 1. 参考项目（deepseek-harness）实现要点

> 更正前提：该项目 Host 端是 Node/TS（cordis DI + typert WebSocket RPC），`python/` 仅 SDK。客户端与 Host 是单一 WS 上的多命名空间 Remote RPC + 事件流。

### 1.1 右面板「开始」页
- 面板宿主：`packages/client/ui-sidebar-right/src/client/shell/SidebarRight.tsx`；「开始」= `guide` 类型 tab（`tabs/guide/GuideBody.tsx:48`），卡片条目由各 tab 包注册 `guide` 字段（order 排序），数据驱动渲染 `{icon,title,desc,shortcut,kind}`。
- 三卡片分别由独立包贡献：文件（`ui-sidebar-files`，⌘P）、终端（`ui-sidebar-terminal`，多开=同 kind 多 tab）、浏览器（`ui-sidebar-browser`，⌘T）。
- 文件后端：`packages/api/workspace-files/src/index.ts:183`，RPC 五方法 `stat / read(分页) / readBytes / list(单层) / changes(目录监听流)`；资源地址 `dsh-resource://file/session/<id>/…`。
- 终端后端：`packages/api/terminal-controller/src/index.ts:103`，PTY 用 node-pty；RPC = `environment/shells/list/create(幂等id)/retain/follow(快照+帧流)/write/resize/rename/close`；Host 侧用 `@xterm/headless` 重建屏幕。
- 浏览器：Web 端为 **URL 白名单 iframe**（`IframeImpl.ts`，仅公共+loopback HTTP(S)）；Electron 端才用 `<webview>`。

### 1.2 Composer「+」上拉菜单
- 「+」与 `/` 共用同一弹层：`ui-input-trigger/src/client/MenuView.tsx`；分组定义 `packages/client/ui-commands/src/client/presentation.ts:19-22`：`add:[file,goal,plan,feedback]`、`commands:[compact,permission,model,export]`。
- `goal/plan/feedback/compact/permission` 全部是 **slash command 走同一条 RPC**（`packages/interaction/commands/src/index.ts:314 list / :360 execute`），执行落 `command/run`、`command/done` 会话日志事件。
- `file`/`model` 是纯客户端贡献项：file → 系统文件选择器 + 附件通道；model → popup 选项列表，切换走 `remote.session.selectModel`。
- 菜单行两种类型：action（直接执行）与 popupSelect（二级选项，含当前值勾选态）；permission/model 的当前态由 session 投影回推。

## 2. zhixu 现状盘点（证据见路径:行号）

### 2.1 前端
- 首页（`#/new`，chatKey=null）= hero 版 `ThreadShell` 空态（`webui/src/workspace/routes.ts:49`、`ThreadShell.tsx:1747`）；`WorkspaceHome.tsx` 为无引用死代码。首页右面板**无挂载点**。
- 「+」菜单已有：`webui/src/components/thread/ComposerPlusMenu.tsx`（挂载 `ThreadComposer.tsx:2509`）。添加组已有 file(附件)/project/goal(插 `/goal`)/plan(插 `/plan`)/draw；**缺 feedback**；**无指令组**（compact/permission/model 不在菜单，但能力散在别处：`/compact` 手输、`WorkspaceControls.tsx:313` 权限 chip、`ModelPresetBadge.tsx` 模型徽章）。
- 文件树/预览可复用：`WorkspaceFileTree.tsx` + `GET /api/sessions/{key}/files`（`nanobot/webui/ws_http.py:953`）+ `FilePreviewPanel.tsx`。
- 终端组件：**零**（无 xterm 依赖）；浏览器面板：**零**。
- 快捷键：仅 ⌘⇧O、⌘K（`App.tsx:1903`）；无 ⌘P/⌘T。
- WS 客户端：`webui/src/lib/nanobot-client.ts`（`webui_request` 通用 RPC 通道 :894、`sendSystemCommand` :1022）；HTTP 走 `lib/api.ts`。

### 2.2 后端
- 命令：`/goal`（`nanobot/command/builtin.py:917` + `session/goal_state.py`，turn_end 回传闭环 ✅）；`/plan`（:959，纯 prompt 重写）；`/compact`（:357，事件 `outbound_wire.py:58`）；`/model`（:414，会话级 ✅）；权限 = WS 帧 `set_workspace_scope`（`inbound_commands.py:414`，restricted/full ✅）；**feedback 全缺**。
- 终端底座：`agent/tools/exec_session.py` `ExecSessionManager`（:270，max 8 会话、idle 1800s、`_BoundedOutputBuffer` 流式缓冲）——**仅 LLM 工具面，无 webui 通道**；非 PTY（asyncio 管道），Windows 下 PTY 依赖 pywinpty。
- 浏览器：无内嵌能力；`web.py` 有服务端 fetch（SSRF 防护 :111）；desktop 壳仅 `shell.openExternal`（`desktop/main.cjs:192`）。

### 2.3 差距矩阵（对照目标）

| 目标 | 后端 | 前端 |
| --- | --- | --- |
| 开始页右面板宿主 + 三卡片 | 已有（files API） | 🆕 面板宿主 + guide 卡片（数据驱动） |
| 工作区文件（⌘P） | ✅ 复用；⚠️ 首页无 sessionKey，需 default workspace 锚点 | 🆕 快捷键 + 树接入 |
| 新建终端（多开） | 🆕 webui 通道（包装 exec_session 或 pty） | 🆕 终端组件（xterm.js 或自绘） |
| 浏览器（⌘T） | 无 | 🆕 iframe 面板（白名单）/外跳降级 |
| 「+」添加组 goal/plan | ✅ 已闭环 | ✅ 已有（可加状态回显） |
| 「+」feedback | 🆕 新命令 + 存储 | 🆕 菜单项 + 输入面 |
| 「+」指令组 compact/permission/model | ✅ 能力全有 | 🆕 菜单入口（action / popupSelect 两类行） |

## 3. 最小可移植结论

1. 右面板：一个 guide 页组件收条目数组渲染卡片；文件复用现有 API；终端最小 RPC 集 = create/list/write/kill + 输出事件流；浏览器 iframe 起步。
2. 「+」菜单：菜单行需支持 action（点击即执行，如 compact）与 popupSelect（二级选项 + 当前值勾选，如 model/permission）两种类型，现有 `onInsertToken` 模式不够。
3. 参考项目的「命令统一 RPC」与 zhixu 的 `sendSystemCommand`（把 `/xxx` 当消息发）语义等价，compact/model 可直接复用该通道。
