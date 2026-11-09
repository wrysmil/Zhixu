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
  - nanobot/utils/document.py
  - nanobot/memory/vector/store.py
created_at: 2026-11-09
---

# 知识中心 v1：技术栈检测

本轮只做调研与设计，不安装依赖、不修改业务代码。版本以仓库自带的 `.venv` 为准（系统 `python3` 是 macOS 自带的 3.9.6，不能用于本项目）。

## 1. 后端

| 项目 | 声明约束 | `.venv` 实测 | 证据 |
| --- | --- | --- | --- |
| Python | `>=3.11` | 3.13.9 | `pyproject.toml:7`；`.venv/bin/python` |
| SQLite | 随 Python | 3.52.0 | `sqlite3.sqlite_version` |
| FTS5 | 随 Python | 可用 | 建虚拟表实测通过 |
| FTS5 trigram | SQLite ≥3.34 | 可用（本方案不依赖，留作备选） | `tokenize='trigram'` 实测通过 |
| Pydantic | `>=2.12.0,<3.0.0` | 2.13.5 | `pyproject.toml:33` |
| httpx | `>=0.28.0,<1.0.0` | 已装 | `pyproject.toml:38` |
| ChromaDB | `vector` extra：`>=1.0,<2.0` | 1.5.9 | `pyproject.toml:105`；import 实测通过 |
| sentence-transformers | `vector` extra：`>=3.0,<7.0` | 6.1.0 | `pyproject.toml:106` |
| torch | `vector` extra：`>=2.2,<3.0` | 2.14.0 | `pyproject.toml:104` |
| numpy | 由 torch 带入 | 2.5.3 | importlib.metadata |
| pypdf | `>=5.0.0,<6.0.0` | 5.9.0 | `pyproject.toml:60` |
| python-docx | `>=1.1.0,<2.0.0` | 1.2.0 | `pyproject.toml:61` |
| openpyxl | `>=3.1.0,<4.0.0` | 3.1.5 | `pyproject.toml:62` |
| python-pptx | `>=1.0.0,<2.0.0` | 1.0.2 | `pyproject.toml:63` |
| readability-lxml | `>=0.8.4,<1.0.0` | 0.9 | `pyproject.toml:41`（v1 不做 URL 抓取，暂不使用） |

**结论：** 文档抽取（pdf/docx/xlsx/pptx/文本）已是核心依赖，无需新增；向量能力仍属可选 `vector` extra，因此设计必须允许「无向量依赖时降级纯关键词」。

## 2. 前端

| 项目 | 声明约束 | 证据 |
| --- | --- | --- |
| React | `^18.3.1` | `webui/package.json` |
| Vite | `^5.4.11` | 同上 |
| TypeScript | `^5.7.2` | 同上 |
| Tailwind | `^3.4.17` | 同上 |
| Vitest | `^2.1.8` | 同上 |
| i18next | `^26.0.6` | 同上 |

WebUI 已有 `#/memory`、`#/identity` 独立页面与 `WorkspaceNavigation` 导航项，可作为 `#/knowledge` 的直接范本；测试使用 Vitest + Testing Library。

## 3. 现有可复用资产

| 资产 | 位置 | 复用方式 |
| --- | --- | --- |
| 文档抽取（含页码 locator） | `nanobot/utils/document.py:99`、`:135` | 入库时按行抽取，保留 locator |
| 记忆库 SQLite 范式（schema 版本 + FTS5 + 触发器） | `nanobot/memory/database.py:23-142`、`:344-392` | knowledge DB 直接沿用同一范式 |
| RRF 实现 | `nanobot/memory/retrieval/reranker.py:27` | 语义参考；knowledge 用带权 RRF 单独实现，避免改动记忆语义 |
| 本地向量模型加载 | `nanobot/memory/vector/model_hub.py` | 本地 embedder 复用 `ensure_model` / `apply_source_env` |
| 工具注册范式 | `nanobot/agent/tools/base.py:318`、`memory_search.py:24-99`、`loop.py:1108-1129` | `@tool_parameters` + `create/enabled/execute` + attributes 注入 |
| WebUI 设置域范式 | `nanobot/webui/settings_routes.py:161-235`、`memory_routes.py`、`gateway_services.py:107-137` | 读走 HTTP、写走 WS mutation 白名单 |
| 上传解码 | `nanobot/webui/attachment_ingress.py:79-172`、`nanobot/utils/media_decode.py` | 复用 data URL 解码、大小/MIME 校验 |
| 配置范式 | `nanobot/config_base.py`、`nanobot/config/schema.py` | 新增 `KnowledgeConfig`，自动 camelCase |

## 4. 关键限制

1. WebUI 附件通道单文件上限 6 MB（`nanobot/webui/ingress_policy.py:24`），data URL 走 WS 帧；v1 WebUI 上传沿用该上限，CLI 可到配置上限（默认 50 MB）。
2. SQLite FTS5 的 `unicode61` 不会把中文长串切成词，必须自带 CJK 分词（方案采用 unigram+bigram 写入），且索引与查询必须使用同一分词函数。
3. ChromaDB 在同一进程内重复实例化会争用内部 SQLite 锁，knowledge 必须走进程级单例（与 `gateway_services.py` 对记忆向量的处理一致）。
4. `vector` extra 体积大（torch），知识中心在缺失该 extra 时必须仍可用（纯关键词）。
