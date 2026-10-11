---
batch: home-start-panel-and-plus-menu-parity
type: browser-acceptance
date: 2026-10-11
verdict: PASS (5/5)
environment: macOS 本机 gateway（feature/desktop-app @ 83ce1ca 合并后），headless Chrome 1440×900 + CDP 脚本驱动
---

# 首页开始面板 + Composer「+」菜单 · spec §9 浏览器验收

验收对象为合并回主 checkout 后的真实运行实例（`.venv/bin/nanobot gateway --background`，WebUI http://127.0.0.1:8765）。Qoder 内置浏览器视口宽度不足 767px（`mobileWorkbench` 生效，按设计隐藏开始面板），故改用本机 headless Chrome（`--window-size=1440,900 --remote-debugging-port=9333`），以 CDP `Runtime.evaluate` + `Network.webSocketFrame*` 驱动与取证。截图见 `assets/01..04`。

## 结果

| # | 验收项 | 结论 | 证据 |
| --- | --- | --- | --- |
| 1 | 文件卡 + scope 文件预览链（首页 ⌘P / 文件卡 → home preview host） | PASS | 中断前已验证 `data-testid=home-file-preview` 渲染与 `scope=default` 接线；`assets/01-home-start-panel.png` |
| 2 | 终端创建 → 执行 → 输出回显（管道版 exec_session） | PASS | WS 帧序列：`terminal_start(term-323854ff)` → `terminal_started shell=/bin/zsh cwd=~/.nanobot/workspace status=ok` → `terminal_write "echo hello-term\n"` → `terminal_output seq=1 stdout "hello-term"`；xterm 视图渲染出回显两行；`assets/02-terminal-working.png` |
| 3 | 浏览器白名单 + iframe 安全 | PASS | `http://192.168.1.10/admin` 拒绝（Private or local-network addresses are not allowed）；`file:///etc/passwd` 拒绝（Only http/https）；loopback `http://127.0.0.1:8765` 挂载 iframe，`sandbox="allow-scripts allow-forms allow-popups"` 确认**无 allow-same-origin**；「Open externally」兜底按钮存在；`assets/03-browser-loopback.png` |
| 4 | Composer「+」菜单分组（add 组 + commands 组） | PASS | 菜单渲染：Files and folders / Work in a project / Goal / Plan mode / Draw + Compact / Permissions / Model / Feedback，其后为 skills 列表；`assets/04-plus-menu.png` |
| 5 | `/feedback` → 本地 feedback.jsonl | PASS | composer 提交 `/feedback acceptance probe home-start-panel 2026-10-11` 后 `~/.nanobot/feedback.jsonl` 追加一行 `{ts, session_key: websocket:c0a6…, text}` |

## 过程中的伪缺陷排除（重要）

终端项最初表现为「点了没反应、xterm 空白、gateway 日志无 terminal 活动」，逐项排查后确认**全部为自动化操作误差，非产品缺陷**：

1. 「New terminal」文本匹配命中的是首页**卡片**（卡片职责=打开右栏终端分区），真正的创建入口是分区内的 `+` 图标按钮（`data-testid=terminal-new-button`，其 aria-label 也叫 "New terminal"，与卡片同名）。点卡片只触发 `terminal_list`，不触发 `terminal_start`。
2. 合成 `KeyboardEvent(key=Enter)` 不会触发浏览器表单**隐式提交**（仅 trusted 事件触发），因此命令未发出；自动化改走 `form.requestSubmit()` 后端到端立即打通。真人键盘操作不受影响。
3. 早先注入的 WebSocket 包装钩子 IN 侧捕获不可靠（`__cap` 里 CONNECT/IN 均缺失，与 CDP Network 域证据矛盾），最终采信 CDP `Network.webSocketFrameSent/Received` 原始帧作为判据。
4. 探针连接（独立 bootstrap token 的新 WS）首次即收到 `terminal_started status=ok`，同步排除了「首页连接无 default chat 导致 `terminal_session_unavailable`」假设——`_connection_loop` 在 ready 后即注册 `_conn_default`（runtime.py:820）。

## 结论

spec §9 五项全部 PASS；批次功能在真实运行实例上可用，安全决策（iframe 无 allow-same-origin、loopback 白名单、feedback 本地落盘）与已拍板方案一致。无新增代码改动。
