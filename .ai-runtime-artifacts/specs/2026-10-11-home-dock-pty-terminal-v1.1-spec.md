---
artifact: spec
route: brainstorming
batch: home-dock-pty-terminal-v1.1
date: 2026-10-11
supersedes_decisions:
  - 原批次决策 1（终端=管道版行式控制台）→ 升级为 PTY 真终端（用户 2026-10-11 拍板）
  - 原批次决策 4 的「不建独立右侧 dock 体系」→ 改标签式 dock（用户 2026-10-11 拍板）
reference: deepseek-harness（用户截图 5 张，2026-10-11）
---

# 知序 WebUI v1.1：标签式右栏 dock + PTY 真终端

## 0. 背景与走查结论

v1 已合并推送（83ce1ca）并实机验收 5/5 PASS，但对照 deepseek-harness 截图发现**交互模型差距**：参考版点卡片后卡片让位给全高标签式 dock（文件/浏览器/终端成标签，可并存、× 关闭、顶栏 + 新建），终端是直接键盘输入的活终端（有 shell 提示符），且卡片带 zsh/bash/cmd 下拉。

实机走查（headless Chrome + CDP）确认 v1 功能面无硬 bug：命令执行/状态保持/stderr 分流/exit code/多标签/kill、浏览器导航/白名单/sandbox/外部打开均正确。「bug」实为下述体验缺陷 T1/T2/B1。

## 1. 目标

- **G1 标签式 dock**：首页与活动中会话共用一套右栏 dock；三卡片成为启动器，点击即开对应标签；多标签并存、× 关闭、+ 新建；全部关闭后回到卡片启动器态。
- **G2 PTY 真终端**：xterm.js 直通键盘输入，OS PTY 回显与提示符，支持 `terminal_resize`；zsh/bash/cmd(/powershell) shell 下拉。
- **G3 体验修复**：终端标签命名（`>. zsh`）、kill 幂等、浏览器/文件全高化、⌘P/⌘T 直达 dock 标签。
- **G4 安全不回退**：iframe 无 allow-same-origin、白名单、feedback 上限、连接 gate 等 v1 安全结论全部保留；restricted 模式守卫模型重设计并过安全复审。

## 2. 非目标

- 不做 tmux/screen 会话恢复、不做终端内容搜索、不做多显示器布局。
- 不动 SessionWorkbench 的三态折叠框架（dock 作为其内容区的新宿主）。
- Windows PTY（ConPTY/pywinpty）不在本批：Windows 保留 v1 管道控制台降级路径（见 §4.5）。
- 不做独立右侧 dock 的拖拽宽度调整（沿用现有右栏宽度控制）。

## 3. G1 标签式 dock

### 3.1 结构

新组件 `webui/src/components/dock/StartDock.tsx`：

```
┌ dock 头：[文件 ×] [>. zsh ×] [浏览器 ×] [+] ─────── [折叠] ┐
│ dock 体：当前激活标签的分区内容（全高）                       │
└──────────────────────────────────────────────────────┘
```

- 标签类型：`file | terminal | browser`；terminal 标签标题 = `>. <shell短名>`（跟随 terminal_started 回报的 shell），其余 = i18n 标题。
- `+` 打开类型菜单（新终端→shell 子菜单；浏览器/文件→直接新开标签；file 与 browser 各最多 1 个，重复点击 = 聚焦既有标签，terminal 可多开≤8）。
- × 关闭：terminal 标签关闭时发 `terminal_kill`（沿用现逻辑）；其余仅关标签。
- 空态（0 标签）：显示 v1 三卡片启动器（现 HomeStartPanel 卡片组），点卡片 = 开标签。

### 3.2 状态与挂载

- dock 标签表存前端内存 store（`useDockStore`），键 = chat scope：首页用占位 key `websocket:home`（与 v1 文件预览一致），活动中会话用真实 chat key；切换会话时 dock 内容随之切换，不持久化（刷新后回到卡片启动器态；terminal 服务端进程仍在，`terminal_list` 回推后以「重连提示」标签呈现——见 §3.3）。
- 首页挂载：替换现 HomeStartPanel 的「卡片+内联分区」结构（卡片保留为启动器态）。
- 会话中挂载：SessionWorkbench 现有「文件/终端/浏览器」内联分区改为同一 StartDock 组件（单一实现两处宿主），原内联分区代码删除。
- 快捷键：⌘P = 开/聚焦 文件标签，⌘T = 开/聚焦 浏览器标签（首页与会话中一致；替换 v1 的「仅首页生效」）。

### 3.3 刷新恢复（计划期修正）

计划期核实：首页默认 chat 每次连接都是新随机 uuid（`runtime.py:806 default_chat_id = str(uuid.uuid4())`），整页刷新后 `terminal_list` 按「本连接已 attach 的 chat」过滤，**看不到**旧 chat 的 running 终端；纯靠 list 回推的接管方案不成立。修正为：

- dock 的 terminal 标签清单持久化到 `sessionStorage`（键 `nanobot.dock.terminals.<scopeKey>`，值 `[{id, shell}]`）；仅存 terminal 标签，file/browser 不持久化。
- 挂载时若持久化清单存在且无对应标签，在 dock 头下显示「已运行终端 · 点击接管」提示条；点击 = 用原 `terminal_id` 重建标签并发 `terminal_start`（同 id）。
- **接管语义（契约 v2）**：`terminal_start` 命中已存在 terminal_id 时，服务端把该终端的 publisher/chat 归属**重绑到请求方连接**（同一 webui gate 校验通过即可），返回 `status=already_running`；PTY 不回灌历史输出，前端提示「回车重绘画面」。
- 会话作用域（显式 sessionKey）下 `terminal_list` 仍可见，提示条两种来源合并去重。

## 4. G2 PTY 终端（契约 v2）

### 4.1 后端

新文件 `nanobot/webui/pty_terminal.py`：POSIX 用 `pty.openpty()` + `asyncio.create_subprocess_exec(shell, ..., stdin/stdout/stderr=slave_fd)`（`setsid`/`TIOCSCTTY` 建会话），master fd 经 `loop.add_reader` 读、`sendall` 写、`fcntl.ioctl(TIOCSWINSZ)` 调尺寸。`terminal_bridge.py` 保留为 Windows/降级路径（管道控制台），两者实现同一 `ITerminalBridge` 接口，`start()` 按平台与 §4.5 条件选实现。

### 4.2 WS 契约 v2（增量，旧帧语义不破坏）

```jsonc
// 入站
{ "type": "terminal_start", "terminal_id": "…", "session_key": "…?", "shell": "zsh|bash|fish|cmd|powershell|pwsh"?  }  // 缺省=平台默认 $SHELL
{ "type": "terminal_write", "terminal_id": "…", "data": "<原始按键块，≤64000>" }   // PTY 模式=直通不回显由 PTY 负责；管道模式=维持行式语义
{ "type": "terminal_resize","terminal_id": "…", "cols": 80, "rows": 24 }          // 新增
// 出站
terminal_started 增加 "pty": true|false                                            // 前端据此切换交互模式
terminal_output  语义不变（PTY 下 stdout/stderr 天然合流，stream 恒为 "stdout"）
```

- `shell` 值白名单校验（正则 `^[a-z]{2,12}$`）+ 服务端 `shutil.which` 解析；解析失败 → `terminal_started status=error message=shell_unavailable`。
- `terminal_start` 命中同 `terminal_id` 的存活终端 = **接管**：publisher/chat 归属重绑到请求方（见 §3.3），返回 `already_running`；不回放历史输出。
- 新增 `GET /webui/terminal/shells`（api token gate，同 `/api/workspace/files` 的 `check_api_token`）返回 `{shells:[{id,label,path}], default:"zsh"}` = 白名单 ∩ 本机存在，供下拉渲染。

### 4.3 前端交互模式

- `pty:true`：xterm 转可交互（`onData → terminal_write` 直通、`onResize → terminal_resize`、FitAddon 自适应 dock 高度）；删除命令输入框与 `appendEcho` 链路；Tab 补全/Ctrl-C/Ctrl-L 等全部由 PTY 承担。
- `pty:false`（Windows 降级）：维持 v1 行式 UI（输入框 + echo）。
- 卡片/`+` 菜单上的 shell 下拉：数据来自 §4.2 端点，缓存于 store；默认选中平台默认 shell 并打勾（对齐参考版图4）。

### 4.4 安全模型（需安全复审确认）

- **本批实现取 R1 保守形态**：restricted 会话一律继续走管道行式守卫路径（逐行 `analyze_restricted_segment` + guard，v1 语义不变，`pty:false`）；PTY 真终端仅对 full-access 会话开放（与用户本机开终端等价，风险由权限预设承担）。「restricted 的 PTY 必须运行在 OS sandbox 内、不可用则 fail-closed `sandbox_required`」作为安全复审通过后的下一步演进，不在本批代码内。
- 连接 gate、`terminal_id`/data 上限、idle 1800s、上限 8 不变；`terminal_resize` 同样过 gate + cols/rows 上限（500/200）。
- publisher 迁移：terminal 事件按 chat 订阅者广播（现 `_publish_terminal` 已按 chat 发，需确认多连接/重连后旧连接清理，避免向死连接写）。

### 4.5 平台矩阵

| 平台 | 模式 | 说明 |
| --- | --- | --- |
| macOS/Linux | PTY | 本批主路径 |
| Windows | 管道（v1 行为） | `pty:false`，行式 UI；shell 下拉仅 cmd/powershell/pwsh |

## 5. G3 走查修复清单

| # | 项 | 修复 |
| --- | --- | --- |
| T1 | 终端无提示符/不能直输（参考版核心体验缺失） | §4 PTY + 交互 xterm |
| T2 | 无 shell 选择 | §4.2/4.3 下拉 + 端点 |
| T3 | 刷新后 running 终端失联（list_result 有、UI 无接管入口） | §3.3 接管提示条 |
| T4 | kill 已退出终端回 `unknown_terminal` 错误帧（应为幂等 no-op） | 后端保留 exited 终端元数据 60s 宽限期，期内 kill 返回成功 no-op |
| B1 | 浏览器 iframe 内联矮挤（实测 ~340px 高） | dock 全高 + FitAddon/100% 高度 |
| B2 | ⌘P/⌘T 仅首页生效 | §3.2 全场景直达标签 |
| D1 | 文件分区内联卡片下、无面包屑全高利用 | 文件标签化（面包屑+树+预览在 dock 内） |

## 6. 测试标准

- 后端单测：pty 生命周期（start/output/resize/kill/idle/上限/坏 shell）、restricted fail-closed、shells 端点 gate、kill 幂等；管道降级路径回归不变。
- 契约测试：v2 帧编解码 + 旧客户端兼容（无 `pty` 字段时前端走行式 UI）。
- 前端单测：dock store（开/关/聚焦/上限/标题跟随 shell）、PTY 交互模式切换、shell 下拉渲染、接管提示条。
- 实机验收（headless Chrome CDP，≥1440 宽）：三卡片→标签、多标签并存、× 关闭、+ 新建、活终端直输（`ls`、Tab 补全、Ctrl-C）、shell 切换、⌘P/⌘T、浏览器全高、刷新接管。
- 既有基线：webui vitest 全绿、`ruff`/目标文件 0 新增、pytest 目标全绿（基线失败清单照旧豁免）。

## 7. 风险与拆包

- **R1 安全模型变更**（restricted 逐行守卫→OS sandbox 依赖）：必须安全复审；备选（restricted 保持行式 UI）作为复审不通过时的回退方案。
- **R2 pty 与 asyncio 集成**：macOS 下 `add_reader` + 子进程会话细节易错，安排探针 spike 先行（WU-A 第一步）。
- **R3 dock 重构触及 SessionWorkbench**：范围锁在「内容宿主替换」，不动三态框架。
- 拆包建议：轨道1（终端 PTY：A 后端→B 契约/端点→C 前端交互+下拉）∥ 轨道2（dock：D 前端 dock+启动器+快捷键 → E 会话中接入+文件/浏览器标签化）→ F 走查修复+实机验收。C 依赖 A/B 的 `pty` 字段，D/E 与轨道1并行。
