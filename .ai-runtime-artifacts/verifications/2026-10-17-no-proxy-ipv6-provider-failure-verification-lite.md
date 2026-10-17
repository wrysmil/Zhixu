---
artifact: verification-lite
route: 缺陷调查（systematic-debugging）→ Tier 1 Leader 直做
skills:
  - systematic-debugging
  - verification-before-completion
skills_evidence:
  - ~/.agents/skills/systematic-debugging/SKILL.md
  - ~/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户原话「现在项目有报错模型提供商请求失败 请检查提供商配置或服务状态后重试」
created_at: 2026-10-17
tier: 1
---

# 模型提供商请求全线失败（NO_PROXY 括号 IPv6）— 轻量验证（Tier 1）

> **纪律：** 先根因、后修复；先跑命令取证据，再给结论。

## 防跳过提醒

| 合理化借口 | 现实 |
| --- | --- |
| 「UI 提示检查提供商配置/额度，那就是 key 或余额问题」 | 本次 provider key 均在位（deepseek/minimax/custom-* 均 SET），真实异常是 `httpx.InvalidURL: Invalid port: ':1]'` |
| 「重启一下服务就好了」 | 重启只改采样时机，不改 `NO_PROXY` 内容；同一环境重启前后都会复现 |
| 「`nanobot agent` 能回 OK 就算修完了」 | 必须再验网关自身进程（WebSocket 真实链路），否则只证明 CLI 路径 |

## 1. 症状与证据链

UI 错误文案来自 `webui/src/i18n/locales/zh-CN/common.json` 的
`chat.error.modelRequestFailed.unknownTitle / bodyWithAttempt`（「模型提供商请求失败 / 请检查提供商配置或服务状态后重试」）。

网关日志 `~/.nanobot/logs/gateway.log` 中真正的底层异常：

```
LLM returned error: Error calling LLM: Invalid port: ':1]'
```

同一异常在 `2026-10-17 03:40:17`（本次重启前的旧前台网关）已出现过一次 → 与重启无关。

完整栈（`~/.nanobot/logs/gateway.log`，`/api/settings/provider-models` 路径）：

```
httpx/_api.py:102 in request → Client(__init__)
httpx/_client.py:698  URLPattern(key)
httpx/_utils.py:172   url = URL(pattern)        # pattern = 'all://*[::1]'
httpx/_urlparse.py:411 normalize_port(':1]')
httpx.InvalidURL: Invalid port: ':1]'
```

## 2. 根因

进程环境变量 `NO_PROXY`（与 `no_proxy`）含**方括号 IPv6** 条目：

```
NO_PROXY=localhost,127.0.0.1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,::1,[::1]
```

`httpx` / `httpx2` 会把每个 `NO_PROXY` 条目装配成 `URLPattern`（`all://*[::1]`），
方括号写法无法解析 → 抛 `InvalidURL`。异常发生在 **`httpx.Client()` 构造阶段**，
因此所有走 httpx 的 LLM 调用（主模型、fallback、模型列表）全部失败，而
`nanobot` 自身的 `_no_proxy_mount_pattern()` 其实是能正确处理括号的 —— 是 httpx
读环境变量那条路先炸。

合法写法对照（实测）：

| `NO_PROXY` 条目 | `httpx.Client()` |
| --- | --- |
| `[::1]` | ✗ `InvalidURL: Invalid port: ':1]'` |
| `[::1]:8080` | ✗ `InvalidURL: Invalid port: ':1]:8080'` |
| `::1` | ✓ |
| `localhost` / `127.0.0.1` | ✓ |

## 3. 修复

| 文件 | 改动 |
| --- | --- |
| `nanobot/security/network.py` | 新增 `sanitize_no_proxy_env()` + `_unbracket_no_proxy_entry()`：把 `[::1]` / `[::1]:8080` 归一为 `::1`，顺带去重；幂等，返回是否改动 |
| `nanobot/cli/entry.py` | `main()` 首步 `_sanitize_no_proxy_env()`（best-effort，异常不阻断启动），覆盖 `gateway` / `webui` / `agent` / `serve` 全部 CLI 路径 |
| `nanobot/providers/factory.py` | `make_provider()` 入口同样清洗，覆盖 SDK / 内嵌调用方（不经过 CLI 入口） |
| `tests/security/test_security_network.py` | 6 个回归测试（含「`[::1]` 会让 `httpx.Client()` 抛错」的守卫测试、幂等、无括号 no-op、去重、保留 `*`/域名条目） |

## 4. 命令与结果

| 命令 | 结果 |
| --- | --- |
| 最小复现：`NO_PROXY=…,[::1] .venv/bin/python -c "httpx.Client()"` | `FAIL: InvalidURL Invalid port: ':1]'` |
| 去掉括号条目后同命令 | `client OK` |
| 修复前端到端（**仍用含 `[::1]` 的原始环境**）`.venv/bin/nanobot agent -m "reply with OK only"` | 修复前：`Error calling LLM: Invalid port: ':1]'`；修复后：输出 `OK`（`/tmp/agent_fixed.log`） |
| `.venv/bin/python -m pytest tests/security/test_security_network.py -q` | `57 passed` |
| `.venv/bin/python -m pytest tests/security -q` | `70 passed` |
| `.venv/bin/python -m pytest tests/providers -q`（干净 `NO_PROXY`） | `1001 passed` |
| `.venv/bin/python -m ruff check`（4 个改动文件） | `All checks passed!` |
| 网关重启：`nanobot gateway stop` → `nanobot gateway --background` | `PID: 20773` / `Port: 18790` / `Launch Mode: background` |
| `curl -s http://127.0.0.1:18790/health` | `{"status": "ok", "process": "alive", "ready": true, "websocket": "running"}` |
| **网关真实 WS 链路**（bootstrap token → `ws://127.0.0.1:8765/?token=…` → 发送 `{"content": "reply with OK only"}`） | 收到 `{"event": "delta", …, "text": "OK"}` → 网关进程内模型调用已恢复 |

## 5. 环境噪声与既有失败（非本次引入）

- **`tests/providers` 在含 `[::1]` 的 shell 环境下会 35 个失败**（例如
  `test_azure_openai_provider.py::test_get_default_model`），异常同样是
  `Invalid port: ':1]'`：这些测试直接构造 provider/client，不经过
  `make_provider()` / CLI 入口，所以拿不到清洗。换成干净 `NO_PROXY` 即
  `1001 passed`。属**环境触发的既有脆弱性**，不是本次改动引入。
- **`tests/cli` 12 个失败**属既有测试夹具漂移：`_fake_create_app()` 未同步
  `nanobot/api/server.py::create_app()` 新增的 `session_manager` 参数
  （`git diff HEAD -- nanobot/api/server.py tests/cli/test_commands.py` 为空，
  两文件均无未提交改动）。
- `uv build` 需写 `~/.cache/uv`，受限沙箱下用 `uv build --no-cache`。
- 另发现一处无关缺陷（未修）：`nanobot agent -m "<中文>"` 会在
  `setproctitle.getproctitle()` 抛 `UnicodeDecodeError`（进程标题多字节截断），
  改用 ASCII 提示词可绕过。

## 6. 未验证项

- 未覆盖 Windows / 其他 httpx 版本下 `NO_PROXY` 解析差异（本机 `httpx 0.28.x`）。
- 未给 `nanobot agent` 单条消息模式的「失败仍返回 exit 0」补修（独立缺陷）。
- 未做浏览器内人工点按验收（已用同源 WS 协议实测回复）。
