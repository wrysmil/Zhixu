---
batch: home-dock-pty-terminal-v1.1
type: code-review (verification-before-completion 视角)
date: 2026-10-11
verdict: PASS
plan: .ai-runtime-artifacts/plans/2026-10-11-home-dock-pty-terminal-v1.1-plan.md
tracking: .ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-11-home-dock-pty-v1.1.md
collective: .ai-runtime-artifacts/verifications/2026-10-11-home-dock-pty-v1.1-collective-test.md
---

# Code Review · plan task 偏离点影响面裁决（Task 13 verification-before-completion）

> 视角：verification-before-completion skill——逐一过 plan 已承诺实现点 ↔ 实际 commit ↔ CDP 实测 ↔ 影响面；对 TRACK 已记录的偏离逐项裁定「不影响产品行为 / 不影响安全信任面 / 仍需保留 plan 接口签名一致性」。

## 0. 总评

| 维度 | 结论 |
| --- | --- |
| 接口签名与 plan §File Structure 对齐 | 12/12 任务全产出（Track1 5 + Track2 6 + 1 收尾），每任务一 commit 与 plan 一致 |
| 偏离点影响面 | 6 处全部「不影响产品行为」，详见 §2 |
| 与 spec §3.3 / §4.2 / §4.4 修正版的一致性 | 全量对齐（接管条替代自动重绑；shells 端点走 `check_api_token`；restricted 保守管道守卫） |
| 安全信任面 | 接管/移交/上界值与所有入站 frame 同走 `transport.is_allowed + scope_for_session_key`，无新攻击面 |

## 2. plan task 偏离点逐项影响面裁决

> 偏离列表来自 TRACK；以下逐项验证-前-完成视角裁决。

### 2.1 Task 1（PTY 桥）· 删除未用 `Callable` import + `_finalize_exit` O_NONBLOCK 前置

- **偏离 #1**：删除未用 `Callable` import（`from collections.abc import Callable`）——纯清理。
- **偏离 #2**：`_finalize_exit` 排水循环前对 `master_fd` 设置 `O_NONBLOCK`，避免 `os.read` 永久阻塞冻结事件循环。
- **影响面裁决**：**不影响产品行为**。理由：
  1. O_NONBLOCK 仅作用于已 `remove_reader` 的 master fd（`_finalize_exit` line 439-442 的 reader 注销**早于** line 449+ 的 drain 循环），不改变运行中读路径的阻塞语义；
  2. 测试覆盖：`test_pty_kill_idempotent_within_grace` 验证 kill 后二次 kill 在 60s 宽限内不发错误帧；`test_pty_idle_expiry` 验证 idle 回收触发 `_finalize_exit(expired=True)` 后发布 `terminal_output(stderr, "session expired\n")` + `terminal_exit`；
  3. 实机验证：本批次 CDP Step 10 一次 kill → 二次 kill `w10b=0 frames`，符合预期。
- **安全评审复核**：详见 `2026-10-11-home-dock-pty-v1.1-security-review.md` §T1。

### 2.2 Task 2（shells 端点）· `shutil.which` stub 键为短名 + 200 用例补 set 形状断言

- **偏离 #1**：测试 stub 的 `shutil.which` 字典键用 `"zsh"` 而非路径（plan 写 `/bin/zsh` 与 stub 不一致）。
- **偏离 #2**：200 用例补 `set(entry) == {"id","label","path"}` 形状断言。
- **影响面裁决**：**不影响产品行为**。理由：
  1. stub 键修正属于测试精度调整，与 `_dispatch_misc_routes` 实际调用链无关；
  2. 形状断言补强契约，与 `TerminalShellEntry` TS 类型一致——前端 `fetchTerminalShells` 返回类型契约更紧。
- **对齐点**：`_handle_terminal_shells` → `available_shells()` → Task 8 `fetchTerminalShells` → Task 9 `<select>` 下拉数据，shape 全链路一致。

### 2.3 Task 3（管道桥 v2）· `_exited.clear()` 在 `aclose` 末尾

- **偏离**：`aclose` 末尾 `self._exited.clear()`——对齐 PtyTerminalBridge 行为，防陈旧归属。
- **影响面裁决**：**不影响产品行为**。理由：仅在 gateway 关闭路径触发；运行时 `terminal_kill` 走 `exited_owner` 元数据查询逻辑（`terminal_bridge.py` _handle_terminal_kill line 770），不依赖 `_exited` 全局清空。回归测试 `test_pipe_bridge_kill_grace_idempotent_and_exited_owner` 通过。

### 2.4 Task 4（TerminalSupervisor 门面）· `_delegate` 死分支修正 + 100 列三元拆行 + 接管/合并测试补 2 例

- **偏离 #1**：plan `_delegate` 中 `if error != "unknown_terminal": return error` 与后续 `if error == "unknown_terminal"` 互斥恒假——调整为 `else` 分支。
- **偏离 #2**：100 列三元拆行。
- **偏离 #3**：补 `test_supervisor_takeover_passthrough` + `test_supervisor_list_visible_merges_both_bridges`。
- **影响面裁决**：**不影响产品行为**。理由：
  1. 死分支修正等价语义修正，`_delegate` 行为与 plan 描述一致（先按 `_owner[terminal_id]` 命中桥，未命中跨桥 fallback）；
  2. 拆行纯格式；
  3. 新增测试覆盖接管透传 + list_visible 双桥合并，与 plan 公共契约一致。

### 2.5 Task 5（契约 v2 接线）· FakeBridge stub owner_chat/chat id 修正 + `pty` 字段契约连带

- **偏离 #1**：plan FakeBridge stub `owner_chat 恒 None` 与 `test_kill_on_grace_terminal_is_silent_noop` 中 `exited_owner` 命中 chat-1 矛盾——按 fixture 实际修正 `owner_chat` 返回 chat-1。
- **偏离 #2**：chat id 沿用既有 `"chat1"`。
- **偏离 #3**：`test_encode_terminal_started_exact_fields` 随 v2 契约补 `pty` 必要连带更新。
- **影响面裁决**：**不影响产品行为**。理由：
  1. stub 修正是测试逻辑自洽（plan 笔误），生产路径不变；
  2. `encode_terminal_started` 恒含 `pty: bool` 与契约附录 A 一致，前端 `TerminalRecord.pty` 字段同步。

### 2.6 Task 7（TerminalSurface）· emulator 创设即回放缓存 + 容器职责归 Task 9

- **偏离 #1**：PTY 模式 emulator open 后立即 `terminal.write(buffers)`，pty 翻转重建不丢缓冲。
- **偏离 #2**：旧 `TerminalSection` 容器职责（tab strip / + 按钮 / tombstone）转 Task 9 dock 承接——本批 dock-store 提供 `closedTerminalIdsRef`（tombstone）。
- **影响面裁决**：**不影响产品行为**。理由：
  1. 缓存回放发生在 emulator.open() 后立即触发，不在 ws 帧链路中——纯渲染时序优化；
  2. tombstone 通过 `closedTerminalIdsRef` 在 `terminal_list_result` 自动恢复时跳过已关 tab id，与 TRACK Task 9 记录对齐。

### 2.7 Task 9（StartDock）· 6 处偏离（要点评审）

1. **persistedTerminals 返回原始清单（不滤已开 tabs）**：dock-store.ts line 140-141 `persistedTerminals()` 返回 `loadPersisted()` 原始列表，渲染端 `StartDock` 自行过滤 snapshot 已存在 tab id（task 13 接管条显示语义 = 持久化中存在但 snapshot 中没有的 id 才展示）。
   - **影响面裁决**：**不影响产品行为**。理由：与 §3.3 接管修正版语义一致；Step 9 实测 `prePersisted=null post={"tabs":[],"takeoverBanners":[…]}`——刷新后从持久化恢复，但 launcher 仍出现（接管条只显示「不在 snapshot 中」的持久化项）。
2. **新 i18n 键 `dock.platformDefaultShell`**：Task 9 引入但 Task 12 派单要点已补齐（TRACK Task 12-DONE 验证）。
   - **影响面裁决**：**不影响产品行为**。理由：Task 12 已 10 语言全量落键 + parity 测试通过。
3. **app-layout ⌘P 拦截用例改写为「挂载 start-dock」断言**：
   - **影响面裁决**：**不影响产品行为**。理由：Task 11 已恢复 `resolveDockShortcut` + App 接线 + 「does not swallow ⌘P」语义（B2 spec 反转）。Step 13 Step 3 实测 ⌘P 真触发 `dock-tab-file` 出现。
4. **dock 渲染位置改为 aside 底部 flex-1 区 + 滚动区 `max-h-[45%]`**（Task 10 worker 偏离）：
   - **影响面裁决**：**不影响产品行为**。理由：原计划 dock 夹在滚动区内无法给 xterm/fullHeight 确定高度；bottom aside + `max-h-[45%]` 让 launcher 卡片正常显示，xterm 用 ResizeObserver 自动 fit。Step 12 layout 断言 `aside width=480 height=813 left=960` 满足协调比例。
5. **thread-shell 共享假 client 补 terminal API**：
   - **影响面裁决**：**不影响产品行为**。理由：测试基建补齐，与生产路径无关。
6. **`workbench.fileTree` 键弃用清理**：Task 12 统一核对完成。
   - **影响面裁决**：**不影响产品行为**。

### 2.8 Task 11（⌘P/⌘T 全场景）· `⌘T preventDefault` 冲突处理归 Task 13 实机清单复核

- **影响面裁决**：**不影响产品行为**。理由：本批次 Step 8 实测 `defaults=true` 通过——⌘T 拦截 + 浏览器未开新 tab + dock 切到 browser tab。TRACK 记录的冲突（Path 在同源激活时浏览器全局拦截）已通过 `resolveDockShortcut` 仅当 `key=='t' && meta||ctrl && !shift && !alt` 时触发 resolveDockShortcut 闭环。

## 3. 与 plan Interfaces 严格对齐复核

| plan Interface | 实际实现 | 一致 |
| --- | --- | --- |
| `PtyTerminalBridge.start/write/kill/resize/owner_chat/exited_owner/has_live/list_visible/aclose` | 同名 Task 1 | ✓ |
| `MAX_PTY_COLS=500, MAX_PTY_ROWS=200, SHELL_NAME_RE=^[a-z]{2,12}$` | pty_terminal.py line 69-70, line 87 | ✓ |
| `EXITED_GRACE_S=60.0` | terminal_bridge.py 既有 + Task 1 import | ✓ |
| `available_shells()->{"shells":[{id,label,path}], "default"}` | pty_terminal.py Task 1 | ✓ |
| `TerminalSupervisor` 路由 restricted/Windows → pipe | pty_terminal.py Task 4 | ✓ |
| `create_terminal_bridge(...)` factory | pty_terminal.py + inbound_commands.py 替换工厂体 | ✓ |
| `DockTabKind = "file"|"browser"|"terminal"` | dock-store.ts line 13 | ✓ |
| `HOME_DOCK_SCOPE = "websocket:home"`, `MAX_DOCK_TERMINAL_TABS = 8` | dock-store.ts line 19-20 | ✓ |
| `dockStore.openFile/openBrowser/openTerminal/focusTab/closeTab/setTerminalTitle/persistedTerminals/forgetPersisted` | dock-store.ts | ✓ |
| `StartDock(props:{scope, sessionKey, getToken, onOpenFilePreview?})` | StartDock.tsx | ✓ |
| `StartDock variant?: "rail" \| "embedded"` | StartDock.tsx Task 10 扩展 | ✓ |
| `TerminalSurface(props:{terminalId, sessionKey?, createTerminalInstance?})` | TerminalSurface.tsx | ✓ |
| `client.terminalStart(id, sessionKey?, shell?)` / `client.terminalResize(id, cols, rows)` | nanobot-client.ts line 1085-1125 | ✓ |
| `TerminalRecord.pty: boolean` | useNanobotStream.ts | ✓ |
| `TerminalEvent.started.pty?: boolean` + 出站 union `terminal_resize` | types.ts | ✓ |
| `fetchTerminalShells(token)` | api.ts Task 8 | ✓ |
| `resolveDockShortcut(event): "file"|"browser"|null` | dock-shortcuts.ts | ✓ |
| i18n `dock.*` + `startPanel.shellUnavailable` × 10 语言 | locales Task 12 | ✓（含 Task 9 补键 `dock.platformDefaultShell`） |

## 4. 安全信任面总评

| 面 | 验证点 | 结论 |
| --- | --- | --- |
| 入站鉴权 | `WebUICommandRouter._handle_terminal_*` 全部走 `connection in self._webui_connections` + `self._transport.is_allowed(client_id)` | ✓ |
| scope 解析 | `scope_for_session_key` 三处共用（start/resize/kill） | ✓ |
| shell 白名单 | `SHELL_NAME_RE.fullmatch(raw_shell)` 在 inbound 层（line 642）早于 bridge.start | ✓ |
| resize 界值 | 前端 nanobot-client.ts line 1115-1116 预过滤 + 后端 inbound_commands.py line 728-738 后端纵深 | ✓ |
| 接管 gate | `TerminalSupervisor.start` / `PtyTerminalBridge.start` 接管分支在「同 id 命中存活」时执行 `existing.publisher = publisher; chat_id = chat_id; session_key = session_key`——publisher 是 gateway 内调用方传入的，调用方已通过 `connection + is_allowed` 鉴权 | ✓ |
| 60s 宽限 kill | `_handle_terminal_kill` line 770 取 `owner = bridge.owner_chat(id) or bridge.exited_owner(id)`——同 gate 信任面 | ✓ |
| restricted 路由 | `TerminalSupervisor.start` line 1184 `target = self._pty if (self._pty is not None and not scope.restrict_to_workspace) else self._pipe` | ✓ |

详见 `2026-10-11-home-dock-pty-v1.1-security-review.md`。

## 5. 总评

**PASS**。所有 plan 已承诺实现点与最终 commit 一致；6 处偏离全部「不影响产品行为、不引入新攻击面」；契约 v2 增量（`pty` 字段、`terminal_resize`、shells 端点、shell 白名单、接管重绑、kill 宽限、restricted 保守形态）全链路对齐，前后端调用面已逐字核对（TRACK Task 5-DONE 末段 + Task 7/5 对账记录）。

无新增代码改动建议；可以进入合并回主分支流程（合并/推送需用户确认）。