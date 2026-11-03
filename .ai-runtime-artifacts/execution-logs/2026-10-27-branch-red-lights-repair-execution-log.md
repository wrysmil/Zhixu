---
artifact: execution-log
route: leader-direct + native-subagents
spec: .ai-runtime-artifacts/specs/2026-10-27-branch-red-lights-repair-spec.md
dispatch: .ai-runtime-artifacts/plans/2026-10-27-branch-red-lights-repair-dispatch.md
created_at: 2026-10-27
tier: 2
---

# 分支既有红灯修复 — 执行日志

## 基线

| 范围 | 修复前 | 修复后 |
| --- | --- | --- |
| `nanobot/channels/websocket/tests/`（簇 A） | 16 failed | **402 passed** |
| `tests/tools/test_{mcp_probe,mcp_tool,web_fetch_security}.py` + `tests/security/test_security_network.py`（簇 B） | 7 failed | **172 passed** |
| 全量 `pytest` | 40 failed / 10080 passed | 见收尾章节 |

---

## 簇 A（Leader 直做）：websocket 通道 16 条

**根因（规则 1，测试落后于协议）**
`subagent-observability` 契约规定：新连接挂到已有会话时，服务端在 hydrate 阶段补发一帧
`subagent_execution_snapshot`（`nanobot/webui/outbound_projection.py::hydrate` 无条件发送；
`nanobot/channels/websocket/runtime.py:405` 始终注入 `subagent_hydration`）。
websocket 测试写于该协议之前，全部假设严格事件序列，于是出现三类失准：

1. `assert_not_called()` / `assert_awaited_once()` 被多出的一帧冲垮；
2. 取 `await_args`（最后一帧）拿到的是快照帧而非 `goal_state`；
3. 裸 `client.recv()` 读到的第一帧是快照帧而非 `attached`/`message`。

**改动**
- `nanobot/channels/websocket/tests/ws_test_client.py`：新增 `TELEMETRY_EVENTS` 与
  `recv_business()`；`recv_ready`/`recv_message`/`recv_delta`/`recv_n`/`collect_stream` 改走
  `recv_business()`，跳过带外遥测帧；`recv`/`recv_raw` 保持原样（仍可显式读遥测帧）。
- `nanobot/channels/websocket/tests/test_websocket_channel.py`：新增 `_sent_frames()` /
  `_frames_with_event()` / `_recv_frame()`；hydrate 系列用例改为**按事件类型挑帧**，
  并断言「除契约快照帧外没有别的回放帧」；attach 用例按契约补齐 `reasoning_effort` /
  `team_binding` 两个可空字段（依据 `2026-10-15-agent-team-contract.md` 第 180 行）；
  multiplex 用例改用 `_recv_frame()`。
- `nanobot/channels/websocket/tests/test_websocket_integration.py`：两处裸 `recv()` 改
  `recv_business()`。
- `nanobot/channels/websocket/tests/test_websocket_envelope_media.py`：原断言
  `mock_conn.send.assert_not_awaited()`（"不发错误帧"）改为**筛出 error 帧断言为空**，
  因为 hydrate 的快照帧本就该发。

**断言强度**：无削弱。原先「恰好一帧」的断言被替换为「事件类型恰好一次 + 其余帧构成被显式锁定」，
比原来更严。

---

## 簇 B（Leader 直做）：tools 安全相关 7 条

**根因（规则 3，环境差异）**
本机配置了**系统级代理** `127.0.0.1:7897`。macOS 上 `urllib.request.getproxies()` 会把
SystemConfiguration 的系统代理与环境变量合并（`getproxies_macosx_sysconf()`），
而各测试文件自带的 `_clear_proxy_env` fixture **只清环境变量**，于是：

- `env_proxy_applies_to_url()` 恒为 True → `_probe_http_url` 走「有代理就不直连探测」分支，
  直连 TCP 探测分支永远不执行（`test_mcp_probe` 2 条：断言 `attempts == []`）；
- `httpx_env_proxy_mounts()` 非空 → 请求走代理 mount，**绕过 pinned-DNS 传输**，
  本地 SSRF 校验不生效 → `test_web_fetch_does_not_fallback_after_pinned_dns_rebind_rejection`
  的 DNS rebind 拒绝没触发、Jina fallback 照跑；同理
  `test_web_fetch_blocks_private_redirect_before_returning_image` 没能拦下私网重定向；
  `test_connect_mcp_servers_http_clients_reject_unsafe_redirect_targets` 的不安全重定向未被记录。

**验证**：临时 pytest 插件把 `nanobot.security.network.getproxies` 换成
`getproxies_environment` 后，三个文件 **115 条全绿** —— 根因确认。

**改动**
- 根 `conftest.py` 新增 `_isolate_proxy_source`（autouse）：把
  `nanobot.security.network.getproxies` 替换为 `getproxies_environment`，
  使套件的代理判定只认环境变量。与既有的 log/session/pairing 隔离 fixture 同类。

**未改动产品**：代理模式下 proxy mount 绕过 pinned-DNS 传输，是 `2026-10-18` 代理支持
工作留下的**设计取舍**（`test_web_fetch_env_proxy_adds_proxy_mounts_and_keeps_pinned_transport`
明确锁定了「代理模式 + 保留 pinned transport」这一形状）。本轮只让测试可复现，
不改该设计；该取舍的安全含义单独记录，见收尾「风险」。

---

## 簇 C / D / E

由子 Agent 并行处理，结论见各 Agent 回报与收尾章节。

## 模型路由事故

`agent_type=executor` 锁定 `gpt-5.2-codex`，ChatGPT 账户不支持 → 两个 Agent 立即 400 失败。
改用 `agent_type=default`（继承当前模型）后正常。
