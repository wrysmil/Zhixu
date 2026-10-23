---
artifact: verification-lite
route: leader-direct | Tier 1 Leader 直做
skills:
  - systematic-debugging
  - verification-before-completion
skills_evidence:
  - /Users/mima0000/.qoder-cn/plugins/cache/qoder-marketplace/superpowers/6.3.0/skills/systematic-debugging/SKILL.md
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户原话：「有问题啊这个，工具里面怎么全是显示正在使用工具，应该显示工具名称」（附运行截图）
  - .ai-runtime-artifacts/specs/2026-10-18-subagent-observability-spec.md §4（时间线逐条显示工具活动）
created_at: 2026-10-18
tier: 1
---

# 子 Agent 时间线标题显示工具名 — 轻量验证（Tier 1）

## 范围

- 改动文件：
  - `nanobot/agent/subagent.py`（`_SubagentHook.before_execute_tool` 不再传 `label="正在使用工具"`）
  - `webui/src/components/thread/SubagentExecutionActivity.tsx`（活动行标题取 `toolName || label`，副标题只留状态）
  - `webui/src/tests/subagent-execution-panel.test.tsx`（新增/更新断言）
- 路由判定：Tier 1（Leader 直做，TDD）

## 根因（Phase 1–3）

1. 展示层 `SubagentExecutionActivity.tsx:199` 标题渲染 `activity.label`，工具名只作副标题 `状态 · toolName`。
2. 数据层 `subagent.py:271` 对**每一次**工具调用都传同一个 `label="正在使用工具"`，因此所有行标题完全相同。
3. 账本自身默认是工具名（`nanobot/subagent_executions/repository.py:241`：`label if label is not None else tool_name`），既有单测也按「不传 label」调用 —— 显式传参覆盖了这个既有默认值，属数据源缺陷，不是渲染缺陷。
4. 附带：该中文常量会随公开事件载荷（`lastAction`）流到 9 个非中文语种客户端。

修复取向：数据层回到账本默认（记工具名），展示层以 `toolName` 优先并去掉重复回显，历史已落库的行同样可正确显示。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `npx vitest run src/tests/subagent-execution-panel.test.tsx`（改前） | FAIL 2/17 — 新增用例报 `Received: 正在使用工具Done · read_file2 sec`，符合红态预期 |
| `npx vitest run src/tests/subagent-execution-panel.test.tsx`（改后） | PASS 17/17 |
| `npx vitest run`（webui 全量） | PASS 1782 passed / 2 skipped（139 files passed） |
| `uv run --no-sync pytest tests/subagent_executions/ -q` | PASS 14 passed |
| `uv run --no-sync pytest tests/agent/test_subagent_lifecycle.py tests/agent/test_runner_hooks.py -q` | PASS 50 passed |
| `uv run --no-sync ruff check nanobot/agent/subagent.py` | All checks passed |
| `npx tsc -p tsconfig.build.json` | exit 0（TSC_OK） |
| `npx vite build` | exit 0，`nanobot/web/dist` 已更新（index-CvMPBD3l.js） |

TDD 红绿：同一会话内先写用例→跑出失败（贴出实际输出）→改实现→复跑通过；未提交 commit，故无 commit hash 可引。

## 未验证项

- **未在真实浏览器/桌面端做端到端目视确认**：面板只在存在真实子 Agent 执行数据时才有内容，需真实派发一次 `spawn`（消耗真实模型调用）。用户当前桌面端已持有该执行数据，重载后即可目视复核。
- `kind="progress"` 活动当前后端未产生，其 label 兜底路径只由单测覆盖。
- 已落库的历史活动行（label 为旧常量字符串）由前端 `toolName` 优先逻辑兜住，但未用真实历史库验证。
