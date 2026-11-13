---
artifact: verification
route: implementation (Tier 2)
branch: feature/trajectory-tracing
created_at: 2026-11-13
status: partial
evidence:
  - pytest tests/trajectory tests/webui/test_trajectory_routes.py
  - basedpyright nanobot/trajectory nanobot/webui/trajectory_routes.py
  - ruff check nanobot/trajectory nanobot/webui/trajectory_routes.py
  - node node_modules/typescript/bin/tsc -p tsconfig.build.json --noEmit
  - node node_modules/vite/bin/vite.js build
  - node node_modules/vitest/vitest.mjs run src/tests/trajectory-projection.test.ts
---

# 轨迹 V1 验证记录

## 1. 后端

| 命令 | 结果 |
| --- | --- |
| `uv run --no-sync python -m pytest tests/trajectory tests/webui/test_trajectory_routes.py tests/webui/test_trajectory_wire.py tests/webui/test_outbound_wire.py tests/agent/tools/test_subagent_tools.py tests/agent/test_loop_runner_integration.py tests/agent/test_runner_core.py -q` | 90 passed |
| `uv run --no-sync python -m pytest nanobot/channels/websocket/tests/test_websocket_channel.py -q` | 205 passed |
| `uv run --no-sync basedpyright nanobot/trajectory nanobot/webui/outbound_wire.py nanobot/webui/outbound_projection.py nanobot/channels/websocket/runtime.py` | 0 errors |
| `uv run --no-sync ruff check …`（轨迹域、WS 接线、subagent/spawn、测试） | All checks passed |

覆盖事实：

- 记录投影顺序为 user → assistant → tool → assistant；assistant 的 `requestRef` 可回读完整有效 system/messages。
- tool 状态覆盖 requested/started/finished（failed 用例）；耗时来自执行边界。
- 每次 provider attempt 独立 event，usage/ttft/generation 在 attempt.finished 上。
- 正文分片按 UTF-8 边界返回；cursor 分页不重复记录身份。
- metadata 模式不保留正文预览；off 模式不创建 recorder。
- `Authorization: Bearer …`、`api_key=…`、URL `token=…` 在落盘前替换为 `[REDACTED]`。
- 越权 session 返回 404 且不暴露存在性；非法 limit 返回 `TRAJECTORY_INVALID_CURSOR`。
- `ContextCompactionEvent` 经 EventSink 旁路生成 compaction 记录。
- 临时聊天使用内存 store；删除 session 通过删除观察者清理轨迹。
- 保留期只删除连续事件前缀并推进 `retainedFromSeq`/`historyGeneration`；旧游标返回 410。
- `trajectory_batch` 帧包含连续 seq、durableSeq 与 recordUpdates；客户端按 appliedSeq 应用，断档时 HTTP 补拉。

## 2. 前端

| 命令 | 结果 |
| --- | --- |
| `node node_modules/typescript/bin/tsc -p tsconfig.build.json --noEmit` | 通过 |
| `TEMP=<repo>/.tmp-build node node_modules/vite/bin/vite.js build` | 构建成功（3087 modules） |
| `node node_modules/vitest/vitest.mjs run src/tests/trajectory-projection.test.ts` | 9 passed |
| `node node_modules/vitest/vitest.mjs run src/tests/i18n.test.tsx src/tests/thread-shell.test.tsx` | 104 passed |

说明：vitest 退出时写 `node_modules/.vite/vitest/results.json` 在本机返回 EPERM，属既有环境限制，不影响用例结果；构建需把 TEMP/TMP 指到仓库内目录，避免 `%TEMP%` 清理被拒。

## 3. 未验证

- 真实 gateway + 真实模型调用的端到端轨迹（含浏览器截图对照 1600×1000、深色/浅色、360/768/1024 响应式）。
- 断线重连、慢消费者与大批量 retention 清理的真机表现。
- provider 内部 SDK 隐式重试的可见性（仍按 `provider_call` 标注）。
- 切换标签时 composer 会重新挂载，半截草稿不保留。
- `tests/providers` 中依赖真实 HTTP 客户端构造的用例在本机因系统代理变量非法（`http://[::1]:1]`）失败，与本次改动无关；轨迹相关 provider 路径已由 `tests/providers/test_llm_usage_observer.py` 与轨迹集成测试覆盖。
