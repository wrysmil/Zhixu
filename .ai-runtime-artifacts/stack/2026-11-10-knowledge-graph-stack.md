---
artifact: stack
route: superpowers:brainstorming
skills:
  - source-driven-development
skills_evidence:
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
source:
  - pyproject.toml
  - webui/package.json
  - desktop/package.json
  - .venv（本机实际安装版本，importlib.metadata）
  - webui/node_modules（实际安装版本）
created_at: 2026-11-10
---

# STACK DETECTION — 知识图谱方案（2026-11-10）

> 基线：`feature/desktop-app` @ `28a4b321`；本机 macOS arm64。

## 1. 运行时

| 组件 | 版本 / 约束 |
| --- | --- |
| Python | 要求 `>=3.11`；本机 venv **3.13.9** |
| Node | 本机 **24.18.0**（webui 用 npm/npx；仓库脚本里另有 bun dev 说明） |
| Electron | `desktop/package.json`：`^45.0.0`（桌面壳 zhixu-desktop 0.1.0） |
| 包管理 | Python `uv`（pyproject/uv.lock）；webui `npm`（package-lock） |

## 2. 后端依赖（pyproject + 实际安装）

| 包 | 约束 | 本机安装 | 与本方案关系 |
| --- | --- | --- | --- |
| pydantic | `>=2.12,<3` | 2.13.5 | 新增 Graph 配置块 |
| httpx[socks] | `>=0.28,<1` | 0.28.1 | 抽取/实体嵌入的 OpenAI 兼容调用（可复用 questions.py 模式） |
| openai | `>=2.8` | 3.11.0 | 现有 LLM 客户端；图谱抽取可直接用 httpx 兼容协议 |
| chromadb | `vector` extra `>=1.0,<2` | 1.5.9 | 现有向量索引（不改动） |
| torch | `vector` extra `>=2.2,<3` | 2.14.0 | 现有本地 embedding/rerank（不改动） |
| sentence-transformers | `vector` extra `>=3.0,<7` | 6.1.0 | 现有本地 embedding/cross-encoder |
| aiohttp | `api` extra + dev | 3.14.3 | 评测 API / WebUI HTTP 路由 |
| jieba | `>=0.42.1,<1` | **未安装**（pyproject 已声明） | FTS 分词；本机环境缺包，验收前需 `uv sync` 补齐 |
| pytest / ruff / basedpyright | dev | 9.1.1 / 已配置 | 新增 SQLite/Neo4j/抽取测试 |

**现有 extras 模式**：`vector = [torch, chromadb, sentence-transformers]`、`api = [aiohttp]`。  
**本方案新增**（可选、默认不装）：

| 新增依赖 | 归属 | 版本建议 | 说明 |
| --- | --- | --- | --- |
| `neo4j`（官方 Python driver） | 新 extra `graph` | `>=5.26,<6.0` | 仅 `knowledge.graph.enabled=true` 时需要；桌面/Lite 默认不装 |
| `d3-force` + `@types/d3-force` | webui dependencies | `^3.0.0` | 力导向布局（SVG 自绘），不引入大型图组件库 |

## 3. 前端依赖（webui/package.json + 实际安装）

| 包 | 约束 | 本机 |
| --- | --- | --- |
| react / react-dom | `^18.3.1` | 18.3.1 |
| vite | `^5.4.11` | 5.4.21 |
| vitest | `^2.1.8` | 2.1.9 |
| i18next / react-i18next | `^26.0.6` / `^17.0.4` | 同左 |
| tailwindcss | （dev） | 3.4.19 |
| lucide-react | `^0.469.0` | 0.469.0 |
| 图可视化库 | — | **无**（d3-force / cytoscape / force-graph 均未安装） |

样式基线：`rounded-panel / rounded-control` + CSS 变量主题；最近视觉约定：运行态用行内状态（无彩色胶囊），上传/表格用卡片式区块（见 `2026-11-10-knowledge-ui-polish`）。

## 4. 测试与验证工具

| 能力 | 现状 |
| --- | --- |
| 后端单测 | pytest 9.1.1，`tests/knowledge`（15 文件）+ `tests/evaluation/knowledge`（7 文件） |
| 前端单测 | vitest 2.1.9（`webui/src/tests`，167 文件量级） |
| 类型/构建 | `tsc -p tsconfig.build.json --noEmit`、`vite build` |
| 浏览器 E2E | Playwright（npx `playwright-core` 1.64.1）+ Chromium 1223，1440×950 |
| 假件先例 | `FakeEmbedder/FakeVector/FakeQuestionGenerator`、`FakeRepo`、`StubReranker`、`controlled_provider` |

## 5. 部署约束（决定“可选 Neo4j”）

| 形态 | 约束 |
| --- | --- |
| 默认安装 / Lite | 只装核心 + `vector` extra；**不得**要求 Neo4j/JVM |
| 桌面（Electron） | 只启动 Python gateway；**不打包** Neo4j；图开启时连接用户自备实例 |
| Docker/服务器 | 可选 compose profile 起 `neo4j:5.26-community`（APOC 非必需；本方案只用 Cypher） |
| 不可用降级 | 图后端不可用 → 检索 `degraded=["graph_unavailable"]`，关键词/向量照常 |

## 6. 结论

- 后端无需新增默认依赖；`neo4j` 走新 extra，`d3-force` 是唯一前端新增包（SVG 自绘，避免引入大库）。
- 图谱属于**派生索引**：SQLite 仍是真相源，Neo4j 可随时重建；这条与现有 FTS/Chroma 的“可重建派生”约束一致。
- 现有抽取/问题生成（`knowledge/questions.py`）与评测预算（`evaluation/knowledge/runner.py`）是可以直接复用的 LLM 调用与并发/预算范式。
