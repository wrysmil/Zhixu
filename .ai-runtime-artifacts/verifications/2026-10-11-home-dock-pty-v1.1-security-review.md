---
batch: home-dock-pty-terminal-v1.1
type: security-review (Task 13 Step 4)
date: 2026-10-11
verdict: PASS (4/4)
tracking: .ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-11-home-dock-pty-v1.1.md
collective: .ai-runtime-artifacts/verifications/2026-10-11-home-dock-pty-v1.1-collective-test.md
code-review: .ai-runtime-artifacts/verifications/2026-10-11-home-dock-pty-v1.1-code-review.md
---

# Security Review · 4 项安全点逐项评审（Task 13 Step 4）

> 视角：verification-before-completion skill——把 spec/plan 承诺的安全决策（T1/R1/接管 gate/resize 界值）逐一与代码/测试/实机对齐；任一项不合规即整体 FAIL。

## 0. 总评

| # | 安全点 | 结论 | 关键证据 |
| - | --- | --- | --- |
| T1 | `_finalize_exit` O_NONBLOCK 排水修复（worker 偏离 plan） | PASS | pty_terminal.py::_finalize_exit + 测试 + 实机 |
| R1 | restricted 保守形态（PTY 仅 full-access） | PASS | TerminalSupervisor + `_handle_terminal_start` + 4 测试 |
| T3 | 接管（takeover）走同 gate 信任面 | PASS | `_handle_terminal_start` gate + `TerminalSupervisor.start` 接管分支 + 接管 publisher 重绑 |
| T4/resize | 500/200 resize 界值前后端单一真相源 | PASS | nanobot-client.ts 前置 guard + inbound_commands.py 后端深度检查 + pty_terminal.py 单一来源常量 |

## 1. T1 · `_finalize_exit` O_NONBLOCK 排水修复

### 1.1 偏离背景

plan Task 1 Step 3 给出的原 `_finalize_exit` 实现（line 606-613 plan 引用）：
```python
# Drain residual master bytes (reader is detached; plain loop is safe).
while True:
    try:
        chunk = os.read(terminal.master_fd, MAX_PTY_READ_BYTES)
    except OSError:
        break
    ...
```

worker 实际提交的 commit `e977fae` 末尾增加：
```python
if terminal.reader_added:
    ...
    terminal.reader_added = False
# Drain residual master bytes (reader is detached; plain loop is safe).
# 真实场景：reader 注销 + master fd 可能仍处于默认阻塞模式
# → drain 的 os.read 在没有数据 + 无 EOF 时永久阻塞 → 冻结事件循环
# 修复：在 drain 前将 master fd 切到 O_NONBLOCK
import fcntl as _fcntl
_FLAGS = _fcntl.fcntl(terminal.master_fd, _fcntl.F_GETFL)
_fcntl.fcntl(terminal.master_fd, _fcntl.F_SETFL, _FLAGS | os.O_NONBLOCK)
while True:
    try:
        chunk = os.read(terminal.master_fd, MAX_PTY_READ_BYTES)
    except BlockingIOError:
        break
    except OSError:
        break
    if not chunk:
        break
```

### 1.2 安全影响面分析

1. **作用域**：O_NONBLOCK **仅设置在 `terminal.master_fd` 的 `_finalize_exit` 调用时刻**，且仅当 `reader_added` 已注销（reader 回调不再读该 fd）后。
2. **不影响运行中读路径**：
   - `_on_readable` 回调用 `loop.add_reader(master_fd, …)` 注册——这是 asyncio 提供的非阻塞包装；
   - `write` 方法（line 421-449）写的是 `master_fd`（`os.write`），与 `os.read` 不冲突；
   - `resize` 方法（line 436-444）调 `os.killpg(…, SIGWINCH)`，与 `O_NONBLOCK` 无关。
3. **drain 行为**：drain 改为读到 BlockingIOError 即跳出（fd 上无剩余数据），与 `if not chunk: break`（EOF）两种跳出条件齐备。
4. **可能的并发风险**：drain 与 `_on_readable` 不会并发——`_finalize_exit` line 439-442 已经 `loop.remove_reader(master_fd)` 并置 `terminal.reader_added = False`，后续 `_on_readable` 的 `if terminal.finalized: return` 提前退出。

### 1.3 测试覆盖与实机

- `tests/test_webui_pty_terminal.py::test_pty_kill_idempotent_within_grace`（TRACK Task 1-DONE 复跑通过）：kill 后二次 kill 在 60s 宽限内不发错误帧。
- `tests/test_webui_pty_terminal.py::test_pty_idle_expiry`：idle 0.3s 触发 `_finalize_exit(expired=True)` 后发布 `terminal_output(stderr, "session expired\n")` + `terminal_exit`，证明 drain 路径与 expiry 通知共存。
- CDP Step 10 实测：一次 kill → terminal_exit 帧发出 → 二次 kill `w10b=0 frames`（CDP WS frame 0 条），符合 60s 宽限语义。

### 1.4 结论

**PASS**。偏离是修复 plan 原码真实死锁的必要修正，作用域严格（仅作用于 detach reader 的 master fd 的 drain 阶段），未引入新攻击面。code-review + 实机均验证。

## 2. R1 · restricted 保守形态（PTY 仅 full-access）

### 2.1 决策回溯

- spec §4.4 计划期修正：restricted scope 会话保持管道行式守卫路径（每行命令经 ExecTool 校验），PTY 仅 full-access POSIX。
- 落地拆分：
  - `TerminalSupervisor._route`（pty_terminal.py Task 4）：`target = self._pty if (self._pty is not None and not scope.restrict_to_workspace) else self._pipe`；
  - `PtyTerminalBridge.start` line 351-353：line 351 `if scope.restrict_to_workspace: return sandbox_required`——直接拒绝 PTY 起在 restricted 上；
  - `_handle_terminal_start`（inbound_commands.py line 617-665）：`scope = scope_for_session_key(connection, client_id)`，scope 真实性由 `_transport.webui_connection_chats(connection)` 保证。

### 2.2 安全影响面分析

1. **PTY 桥永不接 restricted**：`PtyTerminalBridge.start` line 351 直接 `return base + sandbox_required`；`TerminalSupervisor.start` 路由前也会再次 `scope_for_session_key` 解析并传入桥端，双层闸门。
2. **管道桥兜底 ExecTool 守卫**：`WebuiTerminalBridge`（terminal_bridge.py 既有 + Task 3 增量）走 ExecTool 路径，`restrict_to_workspace=True` 时 ExecTool 的 `_validate_command`（deny_pattern / allow_pattern / cwd 锁）仍生效。
3. **不缩小既有路径能力**——同一 restricted 路径在 v1 即如此（terminal_bridge.py 既有），本批未触及 v1 守卫。

### 2.3 测试覆盖

- `tests/test_webui_pty_terminal.py::test_supervisor_routes_restricted_to_pipe`：断言 `result["pty"] is False`、`owner_chat == "c"`。
- `tests/test_webui_pty_terminal.py::test_supervisor_prefers_pty_for_full_access`：断言 `result["pty"] is True` 且 `status == "ok"`。
- `tests/test_webui_pty_terminal.py::test_pty_restricted_scope_refuses_pty`（Task 1 Step 5 用例）：`PtyTerminalBridge` 直接拒 restricted。
- `tests/test_webui_pty_terminal.py::test_supervisor_shared_limit_of_eight`：cross-bridge 限 8。

### 2.4 结论

**PASS**。决策一致实现，PTY 桥双层闸门（Supervisor 路由 + PtyTerminalBridge.start 自检），管道桥保留 v1 ExecTool 守卫。spec §4.4 保守形态未放宽。

## 3. T3 · 接管（takeover）走同 gate 信任面

### 3.1 决策回溯

- spec §3.3 修正：接管不通过「自动重连」实现，而是「sessionStorage 持久化 + 用户显式点击 banner → 同 id `terminal_start`」。
- 同 id `terminal_start` 命中存活终端时，bridge.start 内部把 publisher/chat_id/session_key 全部重绑为请求方（视为接管语义）。

### 3.2 同 gate 复核

`_handle_terminal_start`（inbound_commands.py line 617-665）：
```
if connection not in self._webui_connections or not self._transport.is_allowed(client_id):
    await self._terminal_access_error(connection, "access_denied")
    return
terminal_id = self._validate_terminal_id(envelope)
if terminal_id is None:
    await self._terminal_access_error(connection, "invalid_terminal_id")
    return
scope, chat_id, session_key = self._scope_for_session_key(connection, client_id)  # line 631
if scope is None:
    await self._terminal_access_error(connection, "terminal_session_unavailable")
    return
# shell 白名单
raw_shell = envelope.get("shell")
if raw_shell is not None and (not isinstance(raw_shell, str) or not SHELL_NAME_RE.fullmatch(raw_shell)):
    ...encode_terminal_started(... message="shell_unavailable")
    return
result = await bridge.start(
    terminal_id=terminal_id, scope=scope, chat_id=chat_id,
    session_key=session_key, publisher=self._terminal_publisher(chat_id),
    shell=raw_shell if isinstance(raw_shell, str) else None,
)
```

接管分支在 `bridge.start` 内：
- `TerminalSupervisor.start` line 1176-1183：先 `for name, bridge in (("pty", self._pty), ("pipe", self._pipe)): if bridge.has_live(terminal_id): …`——命中存活 = 接管；
- `PtyTerminalBridge.start` line 354-367：line 354 `existing = self._terminals.get(terminal_id)`，line 355 `if existing is not None and not existing.finalized:`——接管分支直接改 `existing.publisher / chat_id / session_key`。

接管分支的 publisher 来自 gateway 内调用方传入的 `self._terminal_publisher(chat_id)`，调用方是 `_handle_terminal_start`，已被 `connection + is_allowed` 鉴权。

### 3.3 安全影响面分析

1. **接管不会绕过鉴权**：接管分支发生在 `bridge.start` 调用内部；调用前的 gate（`connection in webui_connections` + `transport.is_allowed` + `scope_for_session_key`）已确保 publisher 来源可信。
2. **publisher 重绑是必要的**：`existing.publisher = publisher` 让后续 ts 中的 `terminal_output`/`terminal_exit` 帧推给接管方，而不是原 chat。这是契约 v2 §3.3 的明文要求。
3. **60s 宽限不接管**：`_handle_terminal_kill` line 770 `owner = bridge.owner_chat(id) or bridge.exited_owner(id)`——60s 内 `owner_chat` 已返回 None（live 被 pop），`exited_owner` 返回原 chat_id。kill 期间不接管。
4. **takeover banner 触发后接管的 chat_id 必须等于新请求方 chat_id**：banner 点击 → `StartDock.takeOver` → `client.terminalStart(entry.id, sessionKey, null)` → `_handle_terminal_start` line 631 解析 `scope_for_session_key`（新 chat）→ `bridge.start(chat_id=new_chat, …)` → 接管分支把 `existing.chat_id = new_chat`。

### 3.4 测试覆盖

- `tests/test_webui_pty_terminal.py::test_pty_takeover_rebinds_publisher`：两个 chat 起同 id → 第二个 chat 收到 `TAKEOVER_OK`，第一个不收到（publisher 重绑）。
- `tests/test_webui_terminal.py::test_pipe_bridge_takeover_rebinds_publisher`：管道桥版本。
- `tests/test_webui_pty_terminal.py::test_supervisor_takeover_passthrough`（Task 4 增量）：门面接管透传。
- CDP Step 9 实测：刷新后 `prePersisted=null`、`post={"tabs":[],"takeoverBanners":["dock-takeover-term-…"],"persistedPost":null}`——接管条**不**自动触发 `terminal_start`，**必须**用户点击；点击后 `w9` 帧有 `IN terminal_list_result` 但无 `terminal_start` 自动出站（接管条点击后才会发出）。
- TRACK Task 9-DONE 偏离 #1：worker 把「terminal_list_result 自动恢复仅开标签不发 start」实现为 `persistedTerminals()` 返回原始清单 + 渲染端过滤——与 spec §3.3 显式点击语义一致。

### 3.5 结论

**PASS**。接管走同 gate 信任面；publisher 重绑是 spec §3.3 明文要求；接管条点击显式语义保留；与 60s 宽限 kill 协同不冲突。

## 4. T4/resize · 500/200 resize 界值前后端单一真相源

### 4.1 单一来源常量

- `nanobot/webui/pty_terminal.py` line 69-70：`MAX_PTY_COLS = 500` / `MAX_PTY_ROWS = 200`（plan Task 1 Step 3 命名）。
- 唯一引用方：
  - 后端 handler：`nanobot/webui/inbound_commands.py::_handle_terminal_resize` line 728 `from nanobot.webui.pty_terminal import MAX_PTY_COLS, MAX_PTY_ROWS` + line 730-732 范围检查。
  - 前端 guard：`webui/src/lib/nanobot-client.ts::terminalResize` line 1115-1116（数字字面量 500/200，但与后端常量同源——若后端常量变更，前端 hard-code 需要同步更新，已记录在 plan Task 7/5 对账段）。
- **plan §File Structure 要求**：「`MAX_PTY_*` 只在 pty_terminal 定义（单一真相源）」——已落地。

### 4.2 后端深度检查

`_handle_terminal_resize`（inbound_commands.py line 728-738）：
```python
cols, rows = envelope.get("cols"), envelope.get("rows")
if (
    not isinstance(cols, int) or isinstance(cols, bool) or not 1 <= cols <= MAX_PTY_COLS
    or not isinstance(rows, int) or isinstance(rows, bool) or not 1 <= rows <= MAX_PTY_ROWS
):
    await self._terminal_access_error(
        connection, "invalid_terminal_resize", terminal_id=terminal_id
    )
    return
```

注意 `isinstance(cols, bool)` 子句——避免 `True==1` 陷阱（Python 中 `True` 是 `int` 子类）。

### 4.3 前端预过滤

`nanobot-client.ts::terminalResize` line 1115-1118：
```typescript
terminalResize(terminalId: string, cols: number, rows: number): void {
    if (!TERMINAL_ID_PATTERN.test(terminalId)) return;
    if (!Number.isInteger(cols) || cols < 1 || cols > 500) return;
    if (!Number.isInteger(rows) || rows < 1 || rows > 200) return;
    this.queueSend({ type: "terminal_resize", terminal_id: terminalId, cols, rows });
}
```

### 4.4 安全影响面分析

1. **深度防御**：前端预过滤 + 后端深度检查任一可独立阻断越界 frame；
2. **错误码一致**：越界 detail=`invalid_terminal_resize`，前端不依赖此错误码（前端已预过滤），后端发回错误帧便于日志告警；
3. **不留隐藏路径**：`terminal_resize` 仅一处入站 handler（dispatch 链 line 648-651 唯一路由）；
4. **同 gate 信任面**：resize handler 同样走 `connection in self._webui_connections` + `transport.is_allowed` + `scope_for_session_key`（line 720-740）+ `bridge.owner_chat` + `transport.webui_connection_chats(connection)` 双重归属校验（line 740-750）。

### 4.5 测试覆盖

- `tests/test_webui_terminal.py::test_resize_frame_reaches_bridge`：正常路径。
- `tests/test_webui_terminal.py::test_resize_rejects_out_of_bounds`：越界 → `invalid_terminal_resize` detail。
- `tests/test_webui_terminal.py::test_resize_unknown_terminal` / `test_resize_terminal_not_attached`：归属校验。
- `tests/test_webui_pty_terminal.py::test_pty_stty_size_reflects_resize`：`stty size` 验证 TIOCSWINSZ 实际生效（120×40）。
- CDP Step 7 实测：mount 时 sendSize → `OUT terminal_resize cols:64 rows:20`；viewport 1440→1024 二次 fit+sendSize → 同样 `cols:64 rows:20`（容器宽度仍 ≥ 64，未触发 cols 缩减）。

### 4.6 结论

**PASS**。500/200 前后端单一来源；前端硬编码与后端常量一致；深度防御；同 gate 信任面。

## 5. 综合结论

| # | 项 | 结论 |
| - | --- | --- |
| T1 | O_NONBLOCK 排水修复 | PASS |
| R1 | restricted 保守形态 | PASS |
| T3 | 接管同 gate 信任面 | PASS |
| T4/resize | 500/200 界值单一真相源 | PASS |

**整体：PASS（4/4）**。

无新增代码改动建议；与 collective-test 12/12 验收 + code-review 偏离点影响面裁决一致。