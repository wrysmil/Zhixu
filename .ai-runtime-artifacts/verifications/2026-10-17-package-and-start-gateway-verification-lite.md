---
artifact: verification-lite
route: leader-direct | Tier 1 Leader 直做
skills:
  - verification-before-completion
skills_evidence:
  - ~/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户原话「打包项目然后启动」
  - 用户结构化选择：打包范围 = Web 版（webui build + uv build wheel + 重启 gateway）；启动方式 = 后台重启 gateway + 浏览器打开 Web UI
created_at: 2026-10-17
tier: 1
---

# 打包（Web 版）并重启 gateway — 轻量验证（Tier 1）

> **适用：** Leader 直做、无业务代码变更、以构建与进程生命周期验证为主。
> **纪律：** 先跑命令取证据，再给结论。

## 防跳过提醒

| 合理化借口 | 现实 |
| --- | --- |
| 「前端构建绿了就等于打包好了」 | wheel 由 hatch 钩子重新打包，必须核对 wheel 内 `nanobot/web/dist/index.html` 与本地构建产物同哈希，否则线上仍是旧包 |
| 「gateway 已经在跑，不用重启」 | 运行中的 Python 进程加载的是旧代码；且当前实例是 `launch_mode=foreground`，`gateway restart` 会直接拒绝（`gateway_foreground_restart_required`） |
| 「`uv build` 成功过一次就够了」 | 第一次因沙箱拒绝写 `~/.cache/uv` 而失败，必须用 `--no-cache` 重跑并留下成功输出 |

## 范围

- 构建：`webui/` → `nanobot/web/dist`；`uv build` → `dist/nanobot_ai-0.3.0-py3-none-any.whl` + `.tar.gz`
- 进程：停掉旧前台 gateway（PID 4451，端口 18790，启动于 07:35Z），以 background 模式重启（PID 19254）
- 未改动任何业务源码；本次无代码 diff

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `cd webui && npm run build` | `✓ built in 6.26s`，产物落 `nanobot/web/dist`（outDir 见 `webui/vite.config.ts:131`） |
| `uv build` | **失败**：`Failed to initialize cache at /Users/mima0000/.cache/uv … Operation not permitted`（沙箱拒绝写工作区外缓存） |
| `uv build --no-cache` | `Successfully built dist/nanobot_ai-0.3.0.tar.gz` / `Successfully built dist/nanobot_ai-0.3.0-py3-none-any.whl`；日志含 `[webui-build] reusing existing build … (already fresh)` |
| wheel 内容比对（`zipfile` 读 `nanobot/web/dist/index.html` 与本地 `sha256`） | 两侧 `c611dfd3980a798b…` 一致 → wheel 内含本次新前端 |
| `.venv/bin/nanobot gateway status` | `Running: yes` / `PID: 4451` / `Port: 18790` / `Launch Mode: foreground` |
| `.venv/bin/nanobot gateway stop` | `Gateway stopped.` |
| `.venv/bin/nanobot gateway --background` | `Gateway started in the background.` / `PID: 19254` / `Port: 18790` / `Launch Mode: background` / `Lifetime: explicit` |
| `curl -s http://127.0.0.1:18790/health` | `{"status": "ok", "process": "alive", "ready": true, "websocket": "running"}` |
| `curl -s http://127.0.0.1:8765/` | `HTTP 200`，`bytes=7330`，`<title>知序</title>`；`cmp` 与 `nanobot/web/dist/index.html` 完全一致 |
| `curl http://127.0.0.1:8765/assets/index-CpN4PbpY.js` + `shasum -a 256` | 服务端与构建产物同为 `fe351bc07c3c7c4d…` → 网关托管的是新 bundle |
| `open "http://127.0.0.1:8765/#/?bootstrapSecret=<channels.websocket.tokenIssueSecret>"` | 已打开；secret 长度 43，取自 `~/.nanobot/config.json` |

产物校验（本次落盘）：

```
07b83823b1ca5a637c0de412e478940164bba77b73bd1092eff64607393f7172  dist/nanobot_ai-0.3.0-py3-none-any.whl   (10,374,113 B, 16:30)
8769bb0a92cce5d4ceb8f1b0dc3592713a1bafa103f9a10b746c6b38f33c51cc  dist/nanobot_ai-0.3.0.tar.gz            (10,644,566 B, 16:30)
```

## 沙箱约束与绕行

- `uv build` 需写 `~/.cache/uv`（工作区外）→ 用 `uv build --no-cache`，或预设 `UV_CACHE_DIR` 到工作区内目录。
- `nanobot gateway status|stop|restart|--background` 需读写 `~/.nanobot/run/*.lock|gateway.json` 与日志 → 必须以 `danger-full-access` 运行（本次已按审批弹窗授权）。
- `webui` 端口的 HTTP 探测、`open` 浏览器不受影响。

## 未验证项

- 未打桌面端安装包（用户选择 Web 版路线；`desktop/release/` 仍是最早的 `win-arm64-unpacked`，且无 NSIS 安装包）。
- 未跑 `pytest` / `vitest` 全量（本次无源码改动，构建即校验编译面）。
- 未对 Web UI 做浏览器内人工走查（仅 HTTP 200 + 产物哈希一致）。
