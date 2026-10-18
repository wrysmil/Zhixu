---
artifact: verification-lite
route: 实现（Tier 1 Leader 直做，用户「直接执行吧」）
skills:
  - test-driven-development
  - verification-before-completion
skills_evidence:
  - /Users/mima0000/.qoder-cn/plugins/cache/qoder-marketplace/superpowers/6.3.0/skills/test-driven-development/SKILL.md
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户原话：「子Agent里面的执行参数和输出最好也能看到」+ 截图（read_file 行只有 失败/已完成）
  - 用户对 3 个设计问题的选择：与主聊天对齐 / 行内默认折叠 / 落盘 ≤600 字
  - 用户追加：「帧预算抬到 8192，按 600 中文字算」「直接执行吧」「直接commit然后push」
created_at: 2026-10-18
tier: 1
---

# 子 Agent 活动参数/输出投影 — 轻量验证

## 范围

- 后端（投影与账本）：
  - `nanobot/subagent_executions/activity_detail.py`（新增）：白名单字段投影 + 脱敏 + 截断
  - `nanobot/subagent_executions/models.py`：`ExecutionActivity` 增 `detail` / `output`
  - `nanobot/subagent_executions/repository.py`：`finish_activity(..., detail=, output=)`
  - `nanobot/subagent_executions/events.py`：`ACTIVITY_FIELDS` 放行两字段；`ACTIVITY_MAX_DATA_BYTES` 4096 → **8192**；出口再脱敏一次并压到 600
  - `nanobot/subagent_executions/__init__.py`：导出 `project_tool_activity` / `redact_activity_text` / `ACTIVITY_TEXT_MAX_CHARS`
  - `nanobot/agent/subagent.py`：`_finish_tool` 收口时生成投影，`after_execute_tool` 传 `result`、`on_execute_tool_error` 传 `error`
- 前端：
  - `webui/src/features/subagent-execution/types.ts`：`ExecutionActivity` 增 `detail` / `output`
  - `webui/src/features/subagent-execution/parser.ts`：`asProjectionText()`——缺失/null/类型不对/空串 → `null`，**非致命**（不让整行作废）
  - `webui/src/components/thread/SubagentExecutionActivity.tsx`：有摘要才渲染展开控件，默认折叠，展开显示「执行参数」「输出摘要」，值以 `…` 结尾提示「内容已截断」
  - `webui/src/i18n/locales/*/common.json` × 10：`thread.subagentExecution.{parameters,toolOutput,detailToggle}`
- 契约：`.ai-runtime-artifacts/contracts/2026-10-18-contract-subagent-observability.md` §1 结构体 + §5 隐私条款

## 投影口径（对齐主聊天，但不越隐私线）

| 工具类别 | detail | output（成功） | output（失败） |
| --- | --- | --- | --- |
| 文件读列（read_file / read_pdf / list_dir） | `path=` | `lines=N` / `entries=N` | `error=<脱敏首行>` |
| 检索（grep / search_code / find_files） | `pattern= · path=` | `hits=N` / `files=N` | 同上 |
| Web（web_search / x_search / memory_search / web_fetch） | `query=` / `url=` | `lines=N`（fetch） | 同上 |
| 写改（write_file / edit_file） | `path=` | 无（不报字节数以外内容） | `error=<脱敏首行>` |
| 命令（exec / exec_session / run_cli_app） | `command=首行` | `exit=N` | `error=<脱敏首行>` |
| 其余（含 MCP、spawn、team_dispatch） | null | null | **null**（白名单外的失败正文不外传） |

- 脱敏：`Bearer x`、`key=value` 形态（api_key/token/secret/password/credential/…）、`sk-`/`ghp_`/`AKIA`/`xoxb`/JWT/私钥、URL 内嵌账号、`/Users|home/<name>/` → `~/`。
- 长度：单字段值 ≤200、错误首行 ≤300、整条摘要 ≤600（超出补 `…`）；活动事件 data 序列化 ≤8192 字节，超预算先删 detail/output 等可补拉字段，保留身份/状态/seq。
- 文件内容、stdout/stderr 正文、原始参数 JSON 均不出服务端；账本里落的是投影结果，重启后仍在（`test_projection_survives_restart`）。

## 验证证据

| 命令 | 结果 |
| --- | --- |
| `uv run --no-sync pytest tests/subagent_executions tests/agent/test_subagent_activity_projection.py tests/webui/test_subagent_execution_{projection,runtime,routes,e2e}.py -q` | **85 passed** |
| `uv run --no-sync pytest tests/agent tests/subagent_executions -q` | **4 failed, 1709 passed** —4 例全为 `project-known-baseline-failures` 已登记基线（auto_compact / history_replay / mcp_reconnect_crash / `failure_error_kind`） |
| `uv run --no-sync ruff check`（本批全部 Python 文件） | **All checks passed!** |
| `npx tsc -p tsconfig.build.json --noEmit` | 无输出（干净） |
| `npx vitest run`（全量） | **1801 passed, 2 failed, 2 skipped** —2 例在 identity/memory 页，属并发会话在途改动，见下 |
| `npx vitest run`（本批 5 个子 Agent 测试文件） | **43 passed** |
| `npx vite build` | **✓ built in 6.32s** |

新增/改写测试：`tests/subagent_executions/test_activity_detail.py`（27，含白名单外失败不外传）、`test_events.py`（+2：600 字截断仍保留事件、全角中文对落在 8192 内）、`test_repository.py`（+2：投影重启后仍在、未投影为 null）、`tests/agent/test_subagent_activity_projection.py`（4，走真实 `_SubagentHook` + 账本）、`tests/webui/test_subagent_execution_projection.py`（+1 出口脱敏与封顶）、`webui/src/tests/subagent-execution-events.test.ts`（+4）、`subagent-execution-panel.test.tsx`（+3）。

## 隐私测试口径变更（需知会）

两个既有断言按 §5 的新口径改写，不是简单放宽：

1. `test_realtime_encoder_is_allowlisted_and_carries_outer_identity`：原断言「activity 里不能出现 `output` 键」。`output` 现在是公开投影字段，故注入哨兵改名 `raw_output`（非白名单键，仍被丢弃），断言改为「非白名单键不上帧」；另加 `test_activity_summary_is_redacted_and_capped_at_the_wire_boundary` 证明编码层兜底脱敏 + 600 封顶。
2. `test_tools_are_live_sanitized_and_failure_can_recover`：原用例把 `"SECRET_OUTPUT"` 当**失败原因**传入并断言其不上帧。现改为断言 `error=no such file` 上帧、同文本里的 `token=SENSITIVE_TOKEN` 被掩掉，而 `"secret"`（原始参数）与 `PRIVATE_RAW_TASK`（任务正文）仍不外传。

## 已知边界与并发影响

- **不做**：web_search 结果标题、写改类 diff、`exec` 的 stdout/stderr 正文（脆弱解析 + 隐私），本期只到计数与退出码。
- 前端不重复截断（服务端是唯一长度真值），只归一化类型与空值。
- 本批与另一会话共享同一工作区：`webui/src/components/workspace/IdentityPage.tsx`、`nanobot/agents/catalog.py`、`tests/webui/test_agents_catalog.py` 与 10 个 locale 里的 identity/memory 键属那批在途改动。`en/common.json` 23:14:44 一度被写成非法 JSON（`"identity": {` 后多一个 `{`）导致首次 build 失败，23:16:10 由该会话自行修复，build 随即通过；全量 vitest 的 2 个失败与 `tests/webui` 整体 collect 失败（`profile_identity_line` ImportError）同因。
- locale 文件与本批同文件，提交时把那批的 identity/memory 键一并带上（纯新增键，未被提交的组件引用不会报错，属可接受的交叉）。
- 桌面端需重新加载才会吃到新的 `nanobot/web/dist`。
