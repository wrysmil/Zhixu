---
artifact: research-report
route: source-driven-development
skills: [source-driven-development]
skills_evidence:
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
source:
  - pyproject.toml
  - webui/package.json
  - .venv
  - nanobot/evaluation/server.py
  - nanobot/evaluation/memory/routes.py
  - nanobot/knowledge/retriever.py
  - nanobot/knowledge/fusion.py
  - nanobot/webui/knowledge_routes.py
created_at: 2026-11-10
---

# 知识检索评测页：技术栈检测

本轮只做调研与方案设计，不安装依赖、不修改业务代码。版本以仓库自带 `.venv` 为准。

## 1. 后端（Python）

| 项目 | 声明约束 | `.venv` 实测 | 证据 |
| --- | --- | --- | --- |
| Python | `>=3.11` | 3.13.9 | `pyproject.toml:7`；`.venv/bin/python` |
| SQLite | 随 Python | 3.52.0 | `sqlite3.sqlite_version` |
| FTS5 | 随 Python | 可用 | `tests/knowledge/test_golden_queries.py` 建虚拟表实测 |
| Pydantic | `>=2.12.0,<3.0.0` | 2.13.5 | `pyproject.toml:33` |
| aiohttp | `>=3.9.0,<1.0.0`（api extra） | 3.14.3 | `pyproject.toml:66`；importlib.metadata |
| Starlette | 间接（aiohttp 无关） | 1.6.0 已装但**本项目不用** | importlib.metadata |
| ChromaDB | `vector` extra：`>=1.0,<2.0` | 1.5.9 | `pyproject.toml:96` |
| sentence-transformers | `vector` extra：`>=3.0,<7.0` | 6.1.0 | `pyproject.toml:97` |
| numpy | 由 torch 带入 | 2.5.3 | importlib.metadata |
| httpx | `>=0.28.0,<1.0.0` | 0.28.1 | `pyproject.toml:38` |
| pytest | `>=9.0.0,<10.0.0`（dev `>=9.1.1`） | 9.1.1 | `pyproject.toml:100,204` |
| loguru | `>=0.7.3` | 0.7.3 | `pyproject.toml:41` |
| FastAPI | — | **未安装** | importlib.metadata MISSING |
| Streamlit | — | **未安装** | — |

**结论：** 评测后端**不需要新依赖**。评测 HTTP 走 aiohttp（与 `nanobot/evaluation/server.py` 同一套），指标计算用纯 Python + Pydantic 模型，存储用 SQLite。不引入 FastAPI/Streamlit，也不引入 Ragas/DeepEval（它们会拖入 OpenAI SDK 与自建 LLM judge 依赖，与本项目「本地自托管、无外部 API 依赖」定位冲突）。

## 2. 前端（WebUI）

| 项目 | 声明约束 | 证据 |
| --- | --- | --- |
| React | `^18.3.1` | `webui/package.json` |
| Vite | `^5.4.11` | 同上 |
| TypeScript | `^5.7.2` | 同上 |
| Tailwind | `^3.4.17` | 同上 |
| Vitest | `^2.1.8` | 同上 |
| react-i18next | `^17.0.4` | 同上 |
| lucide-react | `^0.469.0` | 同上 |

**结论：** 评测页沿用现有 React + Tailwind + i18next + lucide 图标栈；不引入图表库（指标用表格 + 原生 CSS 条形呈现）。

## 3. 可复用基础设施（关键结论）

| 需要的能力 | 现成落点 | 证据 |
| --- | --- | --- |
| 评测 HTTP 路由 | `web.RouteDef` 列表，统一挂载，自动继承 Host/Origin guard、CSRF、限流、405 兜底 | `nanobot/evaluation/server.py:945-998`；`nanobot/evaluation/memory/routes.py:563-591` |
| 评测前端入口 | 设置页 `eval` 分区，已有 tab 结构 `overview/runs/datasets/memory` | `webui/src/components/settings/evaluation/EvaluationSettings.tsx:28,176-179` |
| 评测 API 客户端 | `evaluationTransport` + `memory-evaluation-api.ts` 分页/流式上传模式 | `webui/src/lib/memory-evaluation-api.ts:508+` |
| 检索入口 | `Retriever.search(query, workspace_ids, kb_ids, mode, limit)` 同步接口 | `nanobot/knowledge/retriever.py:130-142` |
| 命中判据 | `SearchHit.content` / `chunk_id` / `content_hash` 全在结构化对象里 | `nanobot/knowledge/fusion.py:34-51` |
| 检索 HTTP 返回 | 已有 `hits`（含 chunkId/score/content/heading/page/locator），**无 rank 与 content_hash** | `nanobot/webui/knowledge_api.py:603-622` |
| 知识 HTTP 读路径 | `GET /api/settings/knowledge/search`（已认证） | `nanobot/webui/knowledge_routes.py:63-82` |
| 既有回归基线 | golden queries：20+ 用例，`MIN_HIT_RATE=0.8`，纯关键词离线 | `tests/knowledge/test_golden_queries.py:30,85-120` |
| 已有检索指标口径 | spec §10 定义 Recall@5 ≥ 0.9 / MRR@10 ≥ 0.7 / exact-hit ≥ 0.95 | `.ai-runtime-artifacts/specs/2026-11-09-knowledge-retrieval-spec.md:651-658` |
| 记忆侧评测可借鉴 | 指标注册表 + `not_assessable` 语义（缺证据不是 0）、数据集→场景→probe→gold 分层 | `nanobot/evaluation/memory/metrics.py:39-120,502-515`；`nanobot/evaluation/memory/models.py:159-176` |

## 4. 缺口（需要新写的部分）

1. **检索级评测后端**：无 `nanobot/evaluation/knowledge/` 模块——数据集、指标计算、批量执行、报告全部缺失。
2. **ground truth 标注体系**：只有 `tests/knowledge/fixtures/golden_queries.json` 这种「query + expect_any 字符串 + 语料」的自带语料形态，没有面向真实知识库的 chunk 级标注（`chunk_id` / `content_hash`）。
3. **评测前端**：无知识检索评测页；现有 `EvaluationSettings.tsx` 只有 memory tab。
4. **检索 HTTP 返回缺 `rank` 与 `content_hash`**：`content_hash` 是标注绑定切分版本的锚点（切分规则一改旧 chunk_id 全失效），需在评测通道单独取，或在返回中补字段。

## 5. 与业界做法对照的关键结论（影响设计）

- TREC 口径区分 `Recall@k`（top-k 对 gold 的覆盖比例）与 `Hit@k`（0/1 是否命中）；本项目 spec §10 的「Recall@5 = 前 5 条中命中的期望块数 / 期望块总数」用的是**覆盖比例**口径，与 TREC 一致，命名不冲突。
- `Ragas` 的 context precision/recall 是**答案句级**指标、必须有 reference 答案，不是 chunk 级召回；不能直接搬。
- 主流开源项目（Ragas / DeepEval / ARES / RAGChecker）**基本只有 CLI 或库**，无 Web UI；带 UI 的是 RAGEval（React+FastAPI+Postgres，人工标注仍标「计划中」）、RagLab（FastAPI+前端，无 gold 标注）、Phoenix/Langfuse（独立服务，绑定其数据模型）。
- 无 gold 的 LLM 判定（UMBRELA / AutoNuggetizer / ARES）需要大模型调用与交叉判分，成本与偏差都高；本项目定位本地自托管，**首版不应依赖 LLM judge**。
