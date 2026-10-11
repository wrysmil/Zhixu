---
artifact: spec
route: source-driven-development -> brainstorming
skills:
  - source-driven-development
  - brainstorming
skills_evidence:
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
  - harness-kit/.agents/skills/brainstorming/SKILL.md
source:
  - .ai-runtime-artifacts/research/2026-10-11-deepseek-harness-homepage-parity-research.md
  - 用户截图 ×3（deepseek-harness 开始页右面板 / 红框标注 / 「+」上拉菜单）
  - AskUserQuestion 四项决策确认（2026-10-11 本会话）
  - .ai-runtime-artifacts/specs/2026-10-09-codex-parity-ui-workbench-spec.md
created_at: 2026-10-11
status: draft
approved: false
topic: home-start-panel-and-plus-menu-parity
---

# 知序首页「开始」右面板 + Composer「+」菜单指令组（对标 deepseek-harness）

## 0. 目标与已确认决策

参考 deepseek-harness 首页，为知序 WebUI 交付两块前后端集成功能：

1. **首页右侧「开始」面板**：工作区文件（⌘P）、新建终端（可多开）、浏览器（⌘T）三卡片。
2. **Composer「+」上拉菜单**：添加组补齐 feedback；新增指令组 compact / permission / model。

用户已确认的四项决策（本会话 AskUserQuestion）：

| 决策点 | 选定方案 |
| --- | --- |
| 终端后端 | exec_session 管道版（不引入 PTY 依赖） |
| 浏览器形态 | iframe + URL 白名单，失败兜底外部打开 |
| feedback | 后端 `/feedback` 命令 + 本地结构化存储 |
| 面板宿主 | 首页新增「开始」面板；会话中并入 SessionWorkbench 右栏分区 |

## 1. 总体布局

```
┌ Sidebar ┬────────── main ──────────┬── 右面板 ──┐
│         │ hero ThreadShell（首页） │  开始       │
│         │  logo + hero composer    │  ▸ 工作区文件 ⌘P │
│         │                          │  ▸ 新建终端   │
│         │                          │  ▸ 浏览器   ⌘T │
└─────────┴──────────────────────────┴────────────┘
会话中：右面板收起为 SessionWorkbench 手风琴（Files 段已有，新增 Terminal / Browser 段）
```

- 首页（`chatKey=null`，`routes.ts:49`）：`App.tsx` shell 的 main 区右侧挂载新组件 `HomeStartPanel`（宽 ~22rem，可整列折叠）。三张卡片为数据驱动条目 `{icon,title,desc,shortcut,action}`（复刻参考项目 guide 页模式）。
- 会话中：点击卡片对应功能在 `SessionWorkbench`（`workbench/SessionWorkbench.tsx`）右栏展开对应分区；首页面板仅作为无会话时的入口与预览。
- 锚点问题：首页无 sessionKey，而文件 API 以会话为锚。后端新增 **workspace 维度只读枚举**（契约 §3），锚定 `workspaces.default_scope`；无默认工作区时卡片显示「选择项目后使用」空态。

## 2. 功能 A：终端（新建终端，可多开）

### 2.1 后端（nanobot/）
- 复用 `agent/tools/exec_session.py::ExecSessionManager`（max 8 并发、`_BoundedOutputBuffer` 流式缓冲、idle 回收），新增 webui 通道：
  - 入站 WS 帧（`webui/inbound_commands.py` dispatch 扩展）：`terminal_start {terminal_id, session_key|workspace_scope, cols?}`、`terminal_write {terminal_id, data}`、`terminal_kill {terminal_id}`、`terminal_list {}`。`terminal_start` 幂等（同 id 重复 = 现状返回）。
  - 出站事件（`webui/outbound_wire.py`）：`terminal_output {terminal_id, seq, data}`（增量）、`terminal_exit {terminal_id, exit_code}`。
  - shell 选择：POSIX `$SHELL`（回退 `/bin/bash`）、Windows `cmd.exe`；cwd 锁定会话/默认工作区根。
- **已知限制（明确写进 UI 文案）**：无 PTY → 无对端回显、无全屏 TUI（vim/top 不可用）。交互模型为**行式命令控制台**：前端本地回显、回车提交一行、流式读输出。
- 安全：受 `security/workspace_access.py` access_mode 约束（restricted 时走既有 ExecTool guard 白名单逻辑）；terminal_id 由前端生成 uuid，服务端校验归属会话；输出帧限长分片。禁止把该通道暴露为任意 RCE：仅 loopback gateway 可达（现状即然）+ 沿用 `.agent/security.md` 边界。

### 2.2 前端（webui/）
- 新增依赖 `@xterm/xterm`（仅用其**渲染输出**：ANSI 色、滚动回看；不开 readline）。
- 新组件 `workbench/TerminalSection.tsx`：多终端 tab 条（+ 新建 / 关闭，上限 8 与后端对齐，超限提示）；每 tab = xterm 只读输出区 + 底部输入行（↑↓ 历史）。
- `nanobot-client.ts` 增加 4 个出站帧方法 + 事件回调；`useNanobotStream` 消费 `terminal_output/exit`。
- 首页卡片「新建终端」：点击 → 右面板内联打开终端分区并 create 第一个终端；下拉箭头 = 列出/切换已开终端（对齐截图）。

## 3. 功能 B：浏览器（⌘T）

- 新组件 `workbench/BrowserSection.tsx`：地址栏 + iframe + 工具行（后退/前进=维护 src 历史栈、刷新、外部打开）。
- URL 白名单工具 `lib/browser-url.ts`：仅 `http/https`；允许公网与 loopback；拒绝 `file:`、`javascript:`、非 http 协议、以及除 loopback 外的私网地址（防 SSRF 式面板滥用，与 `web.py:111` 校验思路一致但独立实现于前端 + 后端各一份）。
- 加载失败检测（多数站点 X-Frame-Options/CSP frame-ancestors 拒嵌）：`onload` 超时启发（~4s 未 load 或 load 后 contentWindow 不可探测）→ 显示「该网站拒绝内嵌」+「在浏览器中打开」按钮。
- 桌面壳：`desktop/main.cjs` 新增 `mira:open-external` IPC（复用 `shell.openExternal`），Web 端 `window.open` 兜底。
- sandbox iframe（`sandbox="allow-scripts allow-same-origin allow-forms allow-popups"`，禁 downloads/popups 由壳拦截）。

## 4. 功能 C：工作区文件（⌘P）

- 复用 `WorkspaceFileTree.tsx` + `FilePreviewPanel.tsx`，零改动接入首页右面板（数据源切到 §5 契约的 workspace 维度端点）。
- ⌘P：快速打开面板并聚焦文件树搜索（v1 = 树内懒加载浏览 + 面包屑，不做全库搜索端点；YAGNI）。
- ⌘T：打开/聚焦浏览器分区。快捷键注册进 `App.tsx` 现有 keymap（:1903 区域），仅桌面与全屏态生效，避免与输入法冲突。

## 5. 后端契约（详见 contracts/2026-10-11-contract-home-start-panel.md）

- `GET /api/workspace/files?path=<rel>&scope=default` → 与 `GET /api/sessions/{key}/files` 同构 payload（`workspace_directory_payload` 复用），只读、路径校验同 `workspace_files.py`。
- WS 终端 4 帧 + 2 事件（§2.1）。
- `/feedback <text>` 命令（§6）。

## 6. 功能 D：feedback

- 后端：`command/builtin.py` 新增 `cmd_feedback`（`/feedback`）：
  - 空参数 → 返回用法提示；非空 → 追加写 `~/.nanobot/data/feedback.jsonl`（`{ts, session_key, text}`）+ 记入 session metadata `feedbacks`；返回短确认（「已记录反馈」）。
  - 注册进 `builtin_command_palette()`，`/` 菜单同步可见。
- 前端：「+」菜单 feedback 行 → 弹出小输入面板（radix popover，多行）→ 提交走 `sendSystemCommand("/feedback ...")`（文本需单行化转义换行）。

## 7. 功能 E：「+」菜单指令组

`ComposerPlusMenu.tsx` 行类型从单一 `onInsertToken` 扩展为三种：
| 类型 | 行为 | 用于 |
| --- | --- | --- |
| `action` | 点击即执行回调 | compact、feedback（开弹窗） |
| `popup-select` | 二级选项列表 + 当前值勾选 | permission、model |
| `insert-token` | 现状保留 | goal、plan、draw、project、file |

- **压缩 compact**：action → `sendSystemCommand('/compact')`；无活动会话时禁用并提示。
- **权限 permission**：popup-select `restricted/full`，当前值来自 `workspaceScope.access_mode`，选择 → `client.setWorkspaceScope(scopeWithAccessMode(...))`（复用 `WorkspaceControls.tsx:324` 逻辑，抽成共享函数）。
- **模型 model**：popup-select 列出 `SettingsPayload.model_presets`，当前会话 preset 勾选，选择 → `sendSystemCommand('/model <preset>')`（与 `ModelPresetBadge` 同通道，复用 `ThreadShell.tsx:1044` handler）。
- 分组标题「添加 / 指令」对齐截图；goal/plan 行在有激活态时显示状态点（goal 来自 turn_end 的 `goal_state`）。
- i18n：9 个 locale 文件补齐新文案（zh-CN 为准，其余语言同步英文/对应翻译，沿用现有 common.json 模式）。

## 8. 不做的事（YAGNI / 边界）

- 不做真 PTY、全屏 TUI 终端；不做全库文件搜索端点；不做 webview 桌面浏览器；不做 `提交或推送` git 动作（属 workbench spec 阶段 3）；不改 SessionWorkbench 三态 rail/expanded 架构（沿用现手风琴）；feedback 不接外部上报渠道。

## 9. 验收口径

1. 首页右面板显示三卡片；⌘P/⌘T 直达对应分区；无默认工作区时文件卡片为空态提示。
2. 终端：新建/多开（≤8）/输入命令回车/看到流式输出（含 ANSI 色）/退出码展示/kill 后 tab 可关闭；restricted 权限下越界命令被后端 guard 拦截并可见错误输出。
3. 浏览器：白名单校验（file:/私网拒绝）；可嵌入站点正常显示；拒嵌站点 4s 内出现「外部打开」兜底；桌面壳走 openExternal。
4. 「+」菜单：指令组三项行为正确（compact 触发压缩事件流、permission 切换后 chip 同步、model 切换后徽章同步）；feedback 提交后 `feedback.jsonl` 有记录且收到确认。
5. 前端 `bun run test` + `ruff check` + 相关 pytest 全绿；9 locale 无缺键。

## 10. 风险

- **pipe 终端体验**：无 echo/无行编辑，输入密码类命令不安全也不可用——UI 明确标注「命令控制台模式」。
- **iframe 拒嵌率高**：主流站点多数不可嵌，兜底路径必须顺滑（一键外开）。
- **WS 帧扩展**：inbound dispatch 与 outbound wire 是核心链路，改动需回归既有会话流（attach/turn_end）。
- **xterm.js 体积**：新增依赖进 bundle，构建产物尺寸核对一次即可。

## Next

- 认可 → 说「写计划」或直接进入计划评审（本 spec 与 plan 同轮产出时以 plan 为准）。
- 调整 → 指出分区/交互/边界修改意见。
