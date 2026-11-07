---
name: vector-live-and-citation-verification-lite
route: leader-direct
skills:
  - verification-before-completion
skills_evidence:
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
source: 用户要求验证向量默认开启后的真实召回效果，并收敛记忆引用与检索触发问题
created_at: 2026-11-07
tier: 1
---

# 向量召回实测 + Episode 短标识 — 轻量验证（Tier 1）

## 范围

- 新增离线核对脚本：`.ai-runtime-artifacts/verifications/2026-11-07-vector-retrieval-live-check.py`
- 改动：`nanobot/memory/retrieval/engine.py`（Episode 展示 id 截到 8 位）、
  `nanobot/memory/vector/store.py`（兼容 sentence-transformers 6.x 的维度方法改名）、
  `tests/memory/retrieval/test_engine.py`
- 只跑检索链路与真实 embedding，不启动网关、不调用 LLM。

## 环境

- `.venv` 已含 `[vector]` 依赖：torch 2.14.0 / chromadb 1.5.9 / sentence-transformers 6.1.0
- 模型 `BAAI/bge-small-zh-v1.5`（512 维，CPU）已在本机缓存，无需下载
- 隔离数据库：`.ai-runtime-artifacts/verifications/2026-11-07-vector-retrieval-live/state.db`
- 种子数据：与浏览器测试一致的 6 条经验（3 pitfall / 1 skill / 1 decision / 1 reference）

## 结果一：词法 vs 词法+向量

`RetrievalEngine.retrieve_with_ids`（与每轮自动注入同一条链路），workspace=default。

| 查询 | 纯词法命中 | 词法+向量命中 | 目标条目在向量结果中的名次 |
| --- | --- | --- | --- |
| `8137 connection refused 怎么修` | 1 | 5 | 第 1 |
| `服务明明在跑，客户端却连不上，该查什么` | **0** | 5 | 第 1 |
| `redis container cannot connect to localhost` | 3 | 5 | 第 2（第 1 是排障手册） |
| `包装好了还是导不进来` | **0** | 5 | 第 2（第 1 是 Redis 坑） |
| `探针排障手册放在哪个文件` | **0** | 5 | 第 1 |
| `为什么健康检查不用 /health` | 1 | 5 | 第 1 |

- 4 个改写/抽象查询在纯词法下是 **0 命中**，开启向量后全部命中目标条目，其中 3 个排第 1。
- 耗时：词法 1.6–4.5 ms；向量 15.2–18.4 ms（含查询编码，CPU）。
- 代价：向量路对每个查询都返回 top-k，不相关条目也会进注入块（`包装好了还是导不进来` 把 Redis 坑排在第 1）。当前 6 条数据下噪声有限，规模上去后需要相似度阈值或更严的截断。

原始结果：`2026-11-07-vector-retrieval-live/report.json`。

## 结果二：自动检索已接线，不是只靠模型自觉

- `nanobot/cli/gateway_runtime.py` 以 `active_retrieval_enabled=True` 装配 `RetrievalEngine`，并把 `MemoryStoreAdapter(vector_store=...)` 传进去。
- `nanobot/agent/loop.py` 每个 USER 回合在 BUILD 阶段调用 `_compute_retrieval_section`，结果拼进 system prompt；失败隔离。
- 结论：WebUI/网关路径每轮都会自动做一次语义 + 情节召回，`memory_search` 是补充，不是唯一入口。之前在浏览器测试报告里「检索靠模型自觉」的推断不成立，特此更正。
- 未覆盖：`nanobot/nanobot.py`（SDK）与 `cli/commands.py`（OpenAI 兼容 API）不装配记忆服务，经验检索在这两个入口不可用；属已知架构差异，本次不改。

## 结果三：Episode 引用标识

- `_render_injection_block` 里 Episode 从完整 32 位 UUID 改为前 8 位（`_DISPLAY_ID_CHARS`）。
- 依据：Episode id 只用于展示，`retrieve_with_ids` 的第二个返回值只收集事实 id；仓库内没有解析 `(Episode: ...)` 的消费方。
- 记忆 ID 保持完整，避免影响既有引用/评分链路。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python .ai-runtime-artifacts/verifications/2026-11-07-vector-retrieval-live-check.py` | 6 条经验入库并完成索引；6 组查询对比完成，见上表；重跑无 `FutureWarning` |
| `uv run --no-sync pytest tests/memory -q` | 1084 passed, 10 warnings in 7.56s |
| `uv run --no-sync ruff check nanobot/memory/retrieval/engine.py nanobot/memory/vector/store.py tests/memory/retrieval/test_engine.py` | All checks passed |
| `uv run --no-sync basedpyright <同上三个文件>` | 仅剩该仓库既有的 `partially unknown` 告警（engine.py 37–399、store.py 既有行），本次改动行无新增错误 |

## 未验证项

- 没有重启网关做「默认配置 + 真实 chromadb」的启动冒烟；证据停在检索链路层。
- 向量排序精度只用了 6 条种子数据，未覆盖大规模经验库与近似重复条目。
- 未加相似度阈值，噪声控制仍是已知风险。

## Next

- 如需闭环：用 `.venv` 重启隔离网关，确认启动日志出现 `vector store ready` 且 `/api/settings/memory/stats` 报告 `search_backend=chromadb`。
- 规模上量后再评估相似度阈值（当前不调，避免无数据支撑的拍脑袋参数）。
