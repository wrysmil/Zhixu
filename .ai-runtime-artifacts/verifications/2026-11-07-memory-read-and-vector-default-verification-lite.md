---
artifact: verification-lite
route: leader-direct
skills:
  - source-driven-development
  - verification-before-completion
skills_evidence:
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
source:
  - "读回项目共享记忆时被工作区硬边界拦住…建议提供 memory_read/memory_list"
  - "然后那个改为向量默认开启"
created_at: 2026-11-07
tier: 1
---

# 记忆读回工具 + 向量默认开启 — 轻量验证（Tier 1）

## 范围

改动文件：

| 文件 | 改动 |
| --- | --- |
| `nanobot/agent/tools/memory_write.py` | 新增只读 `memory_read`（`both/global/project`，可按 `name` 读单条）；`memory_write` 返回值回显实际落盘路径 |
| `nanobot/config/schema.py` | `memory_search_backend` 默认 `fts5` → `chromadb` |
| `nanobot/memory/vector/store.py` | 向量依赖缺失（ImportError）时日志附 `nanobot[vector]` 安装提示 |
| `nanobot/cli/gateway_runtime.py` | 同步默认后端的装配注释 |
| `tests/memory/test_memory_experience_mvp.py` | 新增 `memory_read` 用例；断言 `memory_write` 回显路径 |
| `tests/memory/vector/test_settings.py`、`tests/memory/test_vector_config_default.py` | 默认值断言改为 chromadb；显式 fts5 仍验证零向量副作用 |
| `docs/memory.md`、`AGENTS.md`、`.ai-runtime-artifacts/specs/2026-09-16-vector-retrieval-spec.md` | 文档与契约变更记录 |

路由判定：Tier 1（Leader 直做）。

## 契约变更

原 spec 的 D5/R6 明确要求默认 `fts5`、升级零行为变化、默认安装体积不变，并有
`tests/memory/test_vector_config_default.py` 守护。本次按用户决策改为默认
`chromadb`，已在 spec 对应位置追加 2026-11-07 变更说明；`[vector]` 仍为可选
extra，未安装时向量层初始化失败并自动降级到 FTS5（召回仍可用）。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `uv run --no-sync pytest tests/memory tests/config tests/gateway -q` | `1258 passed, 1 skipped` |
| `uv run --no-sync pytest tests/memory/vector tests/memory/test_memory_experience_mvp.py tests/cli tests/webui/test_settings_services.py -q` | 向量与 memory 用例通过；`tests/cli/test_commands.py` 10 项失败，均为既有测试桩 `_FakeAgentLoop` 缺 `memory_extractor_provider`（`nanobot/cli/commands.py:403`），与本次改动无关 |
| `uv run --no-sync pytest tests/agent tests/webui/test_memory_api.py tests/webui/test_memory_routes.py tests/webui/test_memory_ws.py -q` | `1785 passed`；3 项既有失败：MCP 关闭期重连超时、SessionManager 预览预算、身份模板含「知序指挥官」示例文本，均不涉及本次文件 |
| `uv run --no-sync ruff check <改动文件>` | `All checks passed!` |
| `uv run --no-sync basedpyright nanobot/agent/tools/memory_write.py nanobot/config/schema.py` | `0 errors, 0 warnings, 0 notes` |

运行日志确认 `memory_read` 已随工具发现机制注册（`Registered 28 tools: … 'memory_read' …`）。

## 未验证项

- 未启动真实网关用 `chromadb` 跑一轮召回：会触发 `BAAI/bge-small-zh-v1.5` 模型下载；本次只验证配置映射与降级路径。
- 未在浏览器里验证 WebUI 是否展示新的默认后端状态。
- Episode ID 抄错与 R2 修复范围偏大不是代码缺陷：前者无任何代码消费该 ID（引用计数只统计 memory id），后者属模型行为，`nanobot/templates/AGENT.md` 已有「只动与任务相关的部分」约束；本次未改。

## Next

- 任务完成 → 未提交（按用户此前约束）。
- 如需验证真实向量召回 → 重启隔离实例并确认 `[vector]` 已安装、模型可下载。
