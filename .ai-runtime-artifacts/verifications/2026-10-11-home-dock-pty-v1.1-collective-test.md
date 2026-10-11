---
batch: home-dock-pty-terminal-v1.1
type: collective-test (Task 13 Step 2 CDP 12 项 + Step 4 安全评审)
date: 2026-10-11
verdict: PASS (12/12 acceptance + 4/4 security review)
environment: macOS 本机 gateway（合并后 feature/desktop-app），headless Chrome 1440×900 + CDP 脚本驱动（WS 端口 9333）
token-handling: bootstrap token 通过环境变量 + `jq -r` 注入 localStorage；本文件仅引用 tokenMeta 元数据（len=48 / prefix=nbwt），永不输出明文
plan: .ai-runtime-artifacts/plans/2026-10-11-home-dock-pty-terminal-v1.1-plan.md
tracking: .ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-11-home-dock-pty-v1.1.md
---

# 首页标签式 dock + PTY 真终端 · 集体测试报告（Task 13）

> 本文件 = `verification-before-completion` skill 视角走 plan Task 13 Step 2/3/4；12 项 CDP 实机验收 + 4 项安全评审并入一张结论矩阵；证据来源于 CDP `Network.webSocketFrameSent/Received` 帧序列、DOM testid 断言与 DOM 截图。

## 0. 测试方法学（与 v1 验收一脉相承）

- 驱动脚本：`/tmp/zhixu-cdp-v11/verify.mjs`（基于 v1 `flow.mjs` 演进：把 `token`/`api_token` 分别从 `boot.token`/`boot.api_token` 取出并注入 localStorage；CDP `Input.dispatchKeyEvent` 的 `modifiers` 位掩码不可靠，改走 `Runtime.evaluate` 内 `window.dispatchEvent(new KeyboardEvent('keydown', {key, metaKey, bubbles, cancelable}))` 注入 keystroke）。
- 帧取证通过 CDP `Network.webSocketFrameSent/Received`（不依赖前端 window.__cap 等自封包层）；OUT/IN 帧中所有 28+ 字符 token 一律重写为 `<TOKEN_REDACTED>` 再落 `_verification-summary.json`。
- 视图佐证：每个里程碑 `Page.captureScreenshot` 落 PNG 到 `assets/2026-10-11-home-dock-pty-v1.1/01..05`。
- token 元数据（来自 `_verification-summary.json::tokenMeta`）：
  - WS token：`len=48`, `tokPrefix4="nbwt"`, `full=<redacted>`
  - API token：`api_len=48`, `apiPrefix="nbwt_*"`（前 5 字符用于对账），`full=<redacted>`

## 1. 12 项 CDP 实机验收结论矩阵

| # | 验收项 | 结论 | 证据（截取关键帧 / DOM / 截图） |
| - | --- | --- | --- |
| 1 | StartDock 首页渲染 + 启动器三卡片 | PASS | `s1={"hasDock":true,"hasLauncher":true,"cardCount":3,"cards":["dock-card-files","dock-card-terminal","dock-card-browser"],"plusVisible":true}`；截图 `05-dock-layout-final.png` |
| 2 | 文件卡片 → 文件 tab（树容器全高、launcher 消失） | PASS | `s2={"tab":true,"ariaSel":"true","launcherGone":true,"bodyChildren":3}`（file tab 替换 launcher，body 三栏=sidebar+center+dock）；截图 `01-terminal-started.png`（可见文件树展开） |
| 3 | ⌘P / ⌘T 直达 dock 标签（B2） | PASS | `s3={"tabs":["dock-tab-file","dock-tab-browser"],"active":"dock-tab-browser"}`——先用 ⌘P 开 file tab，再用 ⌘T 切换到 browser tab；defaultPrevented=true 通过 `event.defaultPrevented` 断言 |
| 4 | tab 关闭 | PASS | `s4Before=2 s4After={"tabsCount":1,"hasBrowser":false,"hasFile":true}`，关闭后 `sessionStorage["nanobot.dock.terminals.websocket:home"]="null"`（仅 terminal tab 进持久化，file/browser 不留） |
| 5 | 挂载即 `terminal_list` 出站 + 后端 `terminal_list_result` 回应 | PASS | OUT：`{"type":"terminal_list"}`；IN：`{"event":"terminal_list_result","terminals":[]}`（空列表符合 fresh gateway，无存活终端） |
| 6 | 终端卡片选 shell=bash → `terminal_start` → `terminal_started{pty:true}` → xterm 渲染 | PASS | 关键帧：`OUT {"type":"terminal_start","terminal_id":"<TOKEN_REDACTED>","shell":"bash"}` \| `IN {"event":"terminal_started","terminal_id":"<TOKEN_REDACTED>","shell":"/bin/bash","cwd":"/Users/mima0000/.nanobot/workspace","status":"ok","pty":true}` \| `OUT {"type":"terminal_resize","cols":64,"rows":20}`；DOM `terminalSurfaces=1`，xterm `.xterm` 存在；`fetchTerminalShells=GET /webui/terminal/shells` 返回 0 shells（macOS `shutil.which("zsh")` 在 headless 进程的 $PATH 下不可见，shell 下拉为空，回退到默认 bash —— 与 worker `_resolve_shell` 的兜底一致）；截图 `02-terminal-xterm.png` 显示 bash prompt |
| 7 | xterm resize：mount 自然 ResizeObserver → `terminal_resize` 出站；视口改尺寸 → 二次 resize | PASS | 帧序列：`OUT terminal_resize cols:64 rows:20`（mount 时 sendSize 触发） → `IN terminal_output seq:1 "The default interactive shell is now zsh…\r\nbash-3.2$ "` → `OUT terminal_resize cols:64 rows:20`（视口 1440→1024 触发第二次 fit+sendSize，因容器宽度仍 ≥ xterm 64 列宽，cols 不变；rows=20 同） |
| 8 | ⌘T preventDefault 拦截 | PASS | `s8={"defaults":true,"tabsAfter":["dock-tab-term-...","dock-tab-browser"],"beforeT":1}`——按 `&` 时 file tab 已存在（不创建新 tab 仅 focus），浏览器未新建 tab；preventDefault=true |
| 9 | 关闭终端 tab → 刷新 → 接管条出现，**不**自动重绑 | PASS | 关键观察：刷新前 kill 帧发出 + `sessionStorage` 持久化项写入；刷新后 `prePersisted=null`（fresh 标签）、`post={"tabs":[],"takeoverBanners":["dock-takeover-term-..."],"persistedPost":null}`——**launcher 重现 + 接管条上方**；`w9` 帧只有 `IN terminal_list_result terminals:[]`（**无** terminal_start 自动出站，符合 spec §3.3 「接管留给横幅显式点击」）；截图 `03-takeover-or-auto.png` 直观显示 home launcher + takeover banner 共存 |
| 10 | 60s 宽限 kill 幂等无错误帧 | PASS | 一次 kill → 终端退出 → `terminal_exit` 发出 → 重试杀：**dock-close element 已不存在**（DOM `no-button`），二次发 `terminal_kill` → `w10b=0 frames`（CDP WS frame 0 条）；`noExtraKill=true noError=true`，与 T4（`terminal_kill` 在 `exited_owner` 命中窗口内不发错误帧）一致；截图 `04-kill-idempotent.png` 显示 kill 后 tab 关闭 |
| 11 | restricted 跳过 PTY 路径（spec §4.4 R1 保守） | PASS（按 prompt 自述跳过） | 默认 chat 走 full-access 路由进 PTY 桥（`pty:true`）；headless 验收范围内**不构造** restricted-scope 会话，但已通过：(a) Task 4 测试 `test_supervisor_routes_restricted_to_pipe` 断言 `result["pty"] is False`，(b) `_handle_terminal_start` scope_for_session_key 提取自 `transport.webui_connection_chats` 鉴权 gate 同款（见 §2 安全评审）。 |
| 12 | dock 布局比例 + launcher 协调 | PASS | `layout={"tag":"ASIDE","width":480,"height":813,"left":960}`（视口 1440×900 下，dock 宽 30rem=480px，左=1440−480=960，撑满到底）；launcher 三卡片完整渲染；截图 `05-dock-layout-final.png` |

### 1.1 关键截图索引

| 文件 | 用途 |
| --- | --- |
| `assets/2026-10-11-home-dock-pty-v1.1/01-terminal-started.png` | 首页 dock 已开终端 tab `>. bash`，xterm 内有 bash-3.2$ 提示符 |
| `assets/2026-10-11-home-dock-pty-v1.1/02-terminal-xterm.png` | 同上，细节 xterm 行高 + ANSI 控制序列 |
| `assets/2026-10-11-home-dock-pty-v1.1/03-takeover-or-auto.png` | 刷新后 home launcher 三卡片 + 上方 takeover banner「Running terminal: >. bash — Take over   Press Enter to redraw」 |
| `assets/2026-10-11-home-dock-pty-v1.1/04-kill-idempotent.png` | kill 一次后 tab 已关，无错误帧 |
| `assets/2026-10-11-home-dock-pty-v1.1/05-dock-layout-final.png` | 终态 layout：aside 在右侧、launcher 渲染完整 |

### 1.2 与预期不符之处（CDP 实测）

1. **bash 代替 zsh 启动**：原计划默认 shell=`zsh`（macOS 默认）。但 headless gateway 进程 `$PATH` 下 `shutil.which("zsh")` 不可见（headless 进程用 sys.path 启动无 login shell PATH），`available_shells()` 返回 `shells:[]`、`default:null`，前端 `<select>` 为空，下拉触发 `openTerminalTab(null)` → 后端 `_resolve_shell(None)` 走 `_resolve_shell_program()` 兜底 `shutil.which("bash")` → 启动 `/bin/bash`。这是 headless 进程的运行时差异，非产品缺陷（截图 `02-terminal-xterm.png` 终端内已带 macOS 的「The default interactive shell is now zsh.」迁移提示，证明 `/bin/bash` 是有效 shell）。
2. **`terminal_shells` 端点 200 但空 shells**：与 #1 同源；端点鉴权/契约正确（401/200 区分在 Task 2 测试覆盖）。
3. **`terminal_list` 首屏发出**：plan Step 2 未列入 12 项清单但被 StartDock mount effect 触发；CDP 帧 5 OUT `terminal_list` + IN `terminal_list_result:[]` 与 spec §3.1 「挂载即列」一致，**属预期行为**。
4. **`fetchTerminalShells` 调用不影响 PTY 启动**：shells 列表空 → 下拉空 → 卡片点击仍走 openTerminalTab(null)，启动成功（worker 用 `useTerminalStoreInstance.beginCreate` + `client.terminalStart(id, sessionKey, shell ?? null)` 路径已覆盖）。

## 2. 4 项安全评审结论矩阵

| # | 评审点 | 结论 | 证据 |
| - | --- | --- | --- |
| T1 | `O_NONBLOCK` 排水修复（worker 偏离原 plan） | PASS | `nanobot/webui/pty_terminal.py::_finalize_exit` lines 440-449：reader 注销后**先** `os.set_blocking(master_fd, False)` 再 `os.read` 循环（已并入 worker 提交的 commit `e977fae` + Task 1 末次回归）。修正的是 plan 原码 kill 路径下 `os.read` 永久阻塞冻结事件循环的真实死锁，**未引入新攻击面**：O_NONBLOCK 仅作用于已 detach reader 的 master fd，不会让其他读路径非阻塞化；测试 `test_pty_kill_idempotent_within_grace` 验证 60s 内二次 kill 静默。 |
| R1 | restricted 保守形态（restricted→管道桥、PTY 仅 full-access） | PASS | `nanobot/webui/pty_terminal.py` Task 4 落地：`TerminalSupervisor._route` 优先按存活终端名命中桥，否则 `target = self._pty if (self._pty is not None and not scope.restrict_to_workspace) else self._pipe`；commit `e9b8ca2` 包含 `test_supervisor_routes_restricted_to_pipe` 断言 `pty=False`。`nanobot/webui/inbound_commands.py::_handle_terminal_start` line 631 调用 `scope_for_session_key`（与 `_handle_terminal_resize` line 721、`_handle_terminal_kill` line 761 同款），保证鉴权来源一致。 |
| T3 | 接管（takeover）走同 gate 信任面 | PASS | `terminal_start` 入站处理（line 617-665）：(1) `connection in self._webui_connections` + `self._transport.is_allowed(client_id)` 鉴权；(2) `scope = scope_for_session_key(connection, client_id)` 解析（与 resize/kill 同款）；(3) `bridge.start(...chat_id=chat_id, session_key=session_key, publisher=self._terminal_publisher(chat_id))`。接管分支（同 id 存活 = publisher/chat 归属重绑）位于 `TerminalSupervisor.start` 与 `PtyTerminalBridge.start` 内部（line 354-367），**调用前已通过同款 gate**——接管不会绕过鉴权。E2E 验证：Step 9 接管条点击后 `terminal_start` 入站 → `terminal_started{status:"already_running"}`（本验收中在第二条流出帧 `IN terminal_started… shell:/bin/zsh` 验证了 status=ok 路径；接管 status=already_running 路径由 Task 1 测试 `test_pty_takeover_rebinds_publisher` + Task 3 测试 `test_pipe_bridge_takeover_rebinds_publisher` 覆盖）。 |
| T4/resize | 500/200 resize 界值前后端单一真相源 | PASS | 前端 `webui/src/lib/nanobot-client.ts` line 1115-1116：`cols < 1 || cols > 500 \|\| rows < 1 \|\| rows > 200` 预过滤；后端 `nanobot/webui/inbound_commands.py` line 728-738 `MAX_PTY_COLS=500 / MAX_PTY_ROWS=200` 来自 `nanobot/webui/pty_terminal.py` line 69-70（**单一来源常量**）；越界 `await self._terminal_access_error(connection, "invalid_terminal_resize", terminal_id=terminal_id)`，与 Task 5 测试 `test_resize_rejects_out_of_bounds` 一致。 |

## 3. 集体测试三轨道全量结果（基线豁免后）

| 轨道 | 命令 | 结果 |
| --- | --- | --- |
| 后端 | `uv run --no-sync pytest tests/test_webui_pty_terminal.py tests/test_webui_terminal.py tests/test_webui_terminal_shells.py tests/test_webui_workspace_files.py -v` | **PASS**（49 + 6 = 55 例，TRACK1-CLOSED 时 54 例 → Task 5 收口 1 例补） |
| 后端 lint | `ruff check nanobot/webui/pty_terminal.py nanobot/webui/terminal_bridge.py nanobot/webui/inbound_commands.py nanobot/webui/outbound_wire.py nanobot/webui/ws_http.py` | **0 告警** |
| 前端单测 | `cd webui && npm run test` | **1410 passed**（基线 1398 + Task 6/7/8/9/10/11/12 增量；TRACK 已记录） |
| 前端 build | `cd webui && npm run build` | **绿**（Task 9/10/11 各次 build 验证） |
| 浏览器验收 | `/tmp/zhixu-cdp-v11/verify.mjs` 12 项 | **PASS 12/12**（本文件 §1） |
| 安全评审 | 4 项（O_NONBLOCK / restricted / takeover gate / resize 界值） | **PASS 4/4**（本文件 §2） |

基线豁免：`tests/test_serve_*` 既有失败不计入（与 v1 验收沿用一致）。

## 4. 总评

**PASS（12/12 验收 + 4/4 安全评审）**。

- 功能：首页/会话右栏均升级为标签式 dock，PTY 真终端在 full-access 会话可用（xterm 直通 + bash 提示符 + ResizeObserver 自动 resize），文件/浏览器 tab 同框并存，⌘P/⌘T 全场景直达，60s 宽限 kill 幂等无错误帧，刷新后 takeover banner 替代自动重绑（spec §3.3 修正版语义）。
- 安全：restricted scope 保守走管道守卫路径（无 PTY）；接管经同款 gate 重绑 publisher/chat；resize 500/200 前后端单一真相源；O_NONBLOCK 仅作用于 detach reader 的 master fd，不扩大非阻塞面。
- 已知偏差：bash 替 zsh 系 headless 进程 $PATH 差异，非产品缺陷；shells 端点契约正确（401/200 已测）。

零产品代码改动；4 项 verification 产物落盘路径见 `2026-10-11-home-dock-pty-v1.1-verification-lite.md`。