---
artifact: spec
route: superpowers:brainstorming
skills:
  - brainstorming
  - source-driven-development
skills_evidence:
  - .claude/skills/brainstorming/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/research/2026-09-16-openakita-vectorization-research.md
  - .ai-runtime-artifacts/research/2026-09-16-openakita-identity-config-and-memory-md-research.md
  - .ai-runtime-artifacts/reviews/2026-09-16-research-docs-review.md
  - .ai-runtime-artifacts/specs/2026-09-15-memory-retrieval-rca.md
  - .ai-runtime-artifacts/stack/2026-09-16-stack.md
created_at: 2026-09-16
status: draft
approved: false
topic: nanobot-vector-retrieval
---

# 方案：nanobot 记忆向量检索接入（ChromaDB + bge-small-zh-v1.5）

> 本 spec 只描述**目标 / 前置准备 / 接口 / 约束 / 取舍 / 验收**，不写实现代码。
> 范围内：SQLite `memories` → 向量索引这条下游。MEMORY.md 程序化生成**不在本次范围**（用户已确认）。

## 0. 证据基础与四个关键发现

### 0.1 已读产物（Step 0 结果）

| 产物 | 用途 |
| --- | --- |
| `research/2026-09-16-openakita-vectorization-research.md` | 主要参照（openakita 向量实现，已对抗性核验） |
| `research/2026-09-16-openakita-identity-config-and-memory-md-research.md` | 背景（Layer 0 / MEMORY.md，本次不用） |
| `reviews/2026-09-16-research-docs-review.md` | 核验结论：结论性判断 100% 成立，但**代码块不可逐字照抄** |
| `specs/2026-09-15-memory-retrieval-rca.md` | 检索链路根因分析（直接影响接入点选择） |
| `stack/2026-09-16-stack.md` | 版本与模型实测数据 |

**无产物冲突**，但发现下列 4 项与调研文档的**隐含前提不符**，必须先纠正。

### 0.2 🔴 发现 A：检索链路当前是死的（Gate 0）

实测（2026-09-16，`feature/memory-system` @ `46ce04f`）：

```
$ python -c "from nanobot.memory.database import MemoryDatabase; \
             print(hasattr(MemoryDatabase,'search_semantic_scored'))"
False
$ python -c "...search_semantic(db, query='test query here', limit=15, ...)"
SEMANTIC CHANNEL RAISED: AttributeError
  'MemoryDatabase' object has no attribute 'search_semantic_scored'
```

根因链：

1. `nanobot/cli/gateway_runtime.py:488` 装配 `RetrievalEngine(store=_memory_services.database, ...)`，而 `_memory_services.database` 是 `MemoryDatabase` 实例（`webui/memory_services.py:34`）。
2. `MemoryDatabase` 公开方法仅 `connect / init_schema / ensure_schema / replay_fallback`——**不含任何检索方法**（实测四个 `hasattr` 全 False）。
3. 四个通道却以**方法**形式调用：`channels/semantic.py:27` `store.search_semantic_scored(...)`、`recent.py:24` `store.query_semantic(...)`、`episodes.py:37` `store.search_episodes(...)`、`attachments.py:51` `store.search_attachments(...)`。
4. 真实实现是 `nanobot/memory/repository.py` 的**模块级函数**（首参 `conn`）：`:589` / `:629` / `:607` / `:659`。
5. `engine.py:161-170` 用 `asyncio.gather(..., return_exceptions=True)` + `if isinstance(chunk, Exception): continue` **静默吞掉** `AttributeError`。

→ `candidates` 恒空 → `retrieve_with_ids` **恒返回 `("", [])`**。影响每轮自动注入与 `memory_search` 工具**两条**路径（共用同一 engine 实例）。

### 0.3 🔴 发现 B：修复批次 stranded 在未合并分支

```
$ git merge-base --is-ancestor c6f66ac HEAD
c6f66ac is NOT ancestor of HEAD
$ git branch -r --contains c6f66ac
  remotes/origin/harness/wt-2026-09-15-memory-retrieval-rca-fix
```

该分支含 3 个 commit：

| commit | 内容 |
| --- | --- |
| `c6f66ac` | store 契约适配（新增 `retrieval/store_adapter.py`，104 行）+ CJK LIKE 回退 + `episodes.updated_at` 列名修正 + preprocessor 门禁放行 + `memory_search` ToolContext 注入 + `identity.md` 重写 |
| `d726dfa` | 记忆注入块加固，闭合二阶提示词注入面 |
| `e7bd551` | 修正门禁分支与验收测试判别力 |

**分支落后程度（2026-09-16 实测修正）**：

```
分叉点 merge-base = 20e4d94 "docs(memory): 新增记忆检索链路根因分析 RCA"（HEAD 往下数第 3 个）

HEAD 侧领先：2 个 commit（cf2d901、46ce04f）
分支侧领先：3 个 commit（c6f66ac、d726dfa、e7bd551）
两侧改动文件：零重叠
```

| 侧 | 改动的代码文件 |
| --- | --- |
| HEAD（`cf2d901`） | `agent/hooks/memory_extraction.py`、`agent/loop.py`、`memory/experience_extractor.py`、`memory/extractor.py`、`memory/llm_error.py`、`memory/profile_extractor.py`、`memory/scratchpad_writer.py`、`session/labels.py` |
| 修复分支 | `agent/tools/memory_search.py`、`cli/gateway_runtime.py`、`memory/repository.py`、`memory/retrieval/engine.py`、`memory/retrieval/preprocessor.py`、`memory/retrieval/store_adapter.py`（新增）、`templates/agent/identity.md` |

**合并预演（只读，`git merge-tree --write-tree HEAD e7bd551`）**：

```
exit=0
ec925fd60c296f2a74eff90104963c17f344ba53
（冲突信息段为空）
```

→ **合并完全干净，零冲突。** 直接 merge 即可，无需逐文件移植。

> ⚠️ **勘误记录**：本 spec 初稿曾据 `git diff --stat HEAD <branch>` 的 3579 行删除判定「分支严重落后、建议逐文件手工移植」。该结论**错误**——`git diff HEAD <branch>` 把 HEAD 新增的内容显示为删除。正确做法是比对 `merge-base..HEAD` 与 `merge-base..branch`。

### 0.4 🔴 发现 C：调研文档建议的接入点是死代码

调研文档 §10.2（1）建议「保持 nanobot 的 `list[dict]` 契约，ChromaBackend 自己组装 dict」，即改造 `create_search_backend`。

实测：

```
$ grep -rn "create_search_backend" nanobot/ --include=*.py
nanobot/memory/retrieval/search_backend.py:109:def create_search_backend(...)   ← 仅定义
```

**全仓零调用点**。`SearchBackend` 协议、`Fts5SearchBackend`、`create_search_backend` 三件套均为死代码；`channels/semantic.py:1` 的 docstring 说「调用底层 search_backend」也是过时描述。

→ **真正的接入点是 `MemoryStoreAdapter`**，其契约为 `search_semantic_scored(query, *, limit) -> list[(Memory, float)]`。本方案据此修正调研文档的建议。

### 0.5 🔴 发现 D：英文 embedding 模型在本项目不可用

用户初始候选 `all-MiniLM-L6-v2`（模型卡语言 tag 仅 `English`）经实测否决：

```
vocab.txt：30522 行，其中 CJK 字符 244 个（0.8%）
探针："用户热爱创作，希望AI主动提供创作灵感" → 16 个 CJK，覆盖 1 个
```

真实记忆库内容为中文，该模型会把中文编码成近乎全 `[UNK]` 的序列。**默认模型改为 `BAAI/bge-small-zh-v1.5`**（用户已确认）。

---

## 1. 目标与非目标

### 1.1 目标

| # | 目标 | 可验证形式 |
| --- | --- | --- |
| G1 | 修复检索链路（Gate 0），使四路召回真正产出候选 | `retrieve_with_ids` 对含记忆的临时库返回非空块 |
| G2 | 引入真向量语义召回，默认本地 CPU 推理、可离线 | `search_semantic_scored` 返回含 `channel="vector"` 的候选 |
| G3 | 向量索引**可重建**，SQLite 始终是唯一真相源 | 删除 chroma 目录后 `reindex` 能完整恢复 |
| G4 | 依赖缺失/模型下载失败/推理异常时**优雅降级为 FTS5**，绝不阻塞启动 | 未装 chromadb 时全链路行为与今日一致 |
| G5 | 向量层状态对用户**可见可操作**（openakita 的反面教材） | `GET .../stats` 含 `search_backend` / `vector_available` / `vector_count`；`POST .../reindex` 可手动重建 |

### 1.2 非目标（YAGNI，明确排除）

| 排除项 | 理由 |
| --- | --- |
| MEMORY.md 程序化生成 | 用户确认本次范围仅向量（另一份调研文档的 §六，另开 spec） |
| 身份配置 API / 前端 | 同上，与向量无依赖关系 |
| `api_embedding` 后端（DashScope/OpenAI） | 用户选择本地 ST。**但保留 `search_backend` 三值枚举与 `embedding_cache` 表设计**，使未来接入不需改 schema |
| Cross-encoder rerank | 需再下载 reranker 模型（+400 MB），nanobot 已有 `reranker.py` 综合分，收益待实测 |
| 关系图谱 / Relational 检索 | 无数据支撑，不做 |
| CUDA 支持 | 默认 `device="cpu"`，`cuda` 仅作为可配置值留给用户 |
| 中文分词器插件（jieba） | FTS5 侧已由修复批次的 LIKE 回退覆盖；引入分词器会改变现有 FTS5 行为，属独立决策 |
| 前端向量状态面板 | 后端 API 先行；前端为后续独立 WU |

---

## 2. 前置准备（你需要做的事）

> 本节是**你（用户）在实现开始前必须完成的动作**，按顺序执行。

### 2.1 系统基线

| 项 | 要求 | 现状 |
| --- | --- | --- |
| Python | ≥3.11（**CI 基线 3.11**；本机 3.13.13 亦满足） | ✅ |
| 磁盘可用 | ≥ **1 GB**（依赖 ≈600 MB + 模型 96 MB + 余量） | 待你确认 |
| 内存 | ≥ 4 GB 可用（bge-small-zh CPU 推理峰值 ≈400 MB） | 待你确认 |
| 网络 | 首次需能访问 ModelScope / hf-mirror / HF 之一 | ✅（三源实测 200） |

> 🔵 **先分清三个东西（常见混淆点）**
>
> | 层 | 名字 | 是什么 | 怎么来 | 本 spec |
> | --- | --- | --- | --- | --- |
> | **库** | `sentence-transformers` | Python 包，提供 `SentenceTransformer` 类，负责加载模型 + 跑推理 | `pip install` | §2.2 |
> | **库** | `chromadb` | Python 包，向量数据库 | `pip install` | §2.2 |
> | **模型** | `BAAI/bge-small-zh-v1.5` | 权重文件（95.8 MB），自己不会跑 | 下载 | §2.3 |
>
> **`sentence-transformers` 不是模型，是「跑模型的引擎」。** 两者都得有。
>
> 容易误认的点：用户常搜到的 `sentence-transformers/all-MiniLM-L6-v2` 里，**`sentence-transformers` 是 HuggingFace 上的组织名**，与那个 pip 包**同名但不同物**（类似 GitHub 仓库 `numpy/numpy` vs `pip install numpy`）。`BAAI/bge-small-zh-v1.5` 里的 `BAAI` 同理是组织名。
>
> ```python
> from sentence_transformers import SentenceTransformer       # 库（pip 装）
> model = SentenceTransformer("BAAI/bge-small-zh-v1.5")       # 模型（这句触发下载）
> emb = model.encode("用户热爱创作", normalize_embeddings=True)
> ```
>
> **职责划分**：`sentence-transformers` 负责「文本 → 512 维向量」；`chromadb` 负责「存向量 + 近似最近邻查询」——**它只认数字不认文本**。二者靠 `embeddingDimensions: 512` 对齐。
>
> 另一条等价路线是用 BAAI 自家的 `FlagEmbedding` 加载 bge，此处选 `sentence-transformers` 因其生态更通用（换模型不改代码）。

### 2.2 依赖安装：新增 `vector` extra

在 `pyproject.toml` 的 `[project.optional-dependencies]` 新增（**具体版本约束由 plan 阶段定稿**，此处给设计意图）：

```toml
[project.optional-dependencies]
vector = [
    # CPU-only torch：必须走 PyTorch 官方 index，避免拉入 ~2GB 的 CUDA wheel
    # （openakita 的 2500MB 声明即因此产生）
    "chromadb>=1.0,<2.0",
    "sentence-transformers>=3.0,<7.0",
]
```

**torch 的 CPU-only 安装是本方案唯一有平台陷阱的一步。** `pip install torch` 在 Windows 默认拉 CUDA 版（约 2.5 GB）。正确做法是显式指定 index：

```powershell
# Windows PowerShell —— CPU-only，约 200MB
uv pip install torch --index-url https://download.pytorch.org/whl/cpu

# 然后装本方案的 extra
uv pip install -e ".[vector]"
```

若使用 `uv sync`，等价写法是在 `pyproject.toml` 加：

```toml
[tool.uv.sources]
torch = { index = "pytorch-cpu" }

[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true
```

**验收本步**：
```powershell
python -c "import torch, chromadb, sentence_transformers as st; \
print('torch', torch.__version__, 'cuda', torch.cuda.is_available()); \
print('chromadb', chromadb.__version__); print('st', st.__version__)"
# 期望：cuda False（CPU 版）；三个版本号均能打印
```

### 2.3 模型获取（三选一）

**方案 A —— ModelScope（国内直连，推荐）**

```powershell
uv pip install modelscope
modelscope download --model BAAI/bge-small-zh-v1.5 --local_dir "$env:USERPROFILE\.nanobot\models\bge-small-zh-v1.5"
```

**方案 B —— HF 镜像 `hf-mirror.com`**

```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"    # 仅本会话生效
uv pip install "huggingface_hub[cli]"
huggingface-cli download BAAI/bge-small-zh-v1.5 --local-dir "$env:USERPROFILE\.nanobot\models\bge-small-zh-v1.5"
```

**方案 C —— 交给程序自动下（实现后）**

配置 `embedding_download_source: "auto"`，由 `model_hub` 探测最优源并下载到 HF 缓存（`~/.cache/huggingface/hub/`）。

**模型地址（均实测 HTTP 200）**
```
https://modelscope.cn/models/BAAI/bge-small-zh-v1.5
https://hf-mirror.com/BAAI/bge-small-zh-v1.5
https://huggingface.co/BAAI/bge-small-zh-v1.5
```

> ⚠️ **不建议手工下载**。`huggingface-cli download` 与 `modelscope download` 会同时拉取 `model.safetensors` / `config.json` / `tokenizer.json` / `vocab.txt` / `modules.json` / `1_Pooling/` 等**全部必需文件**（`sentence-transformers` 依赖 `modules.json` 与 `1_Pooling/config.json`，缺一个就加载失败）。手动只下权重文件是最常见的踩坑点。

### 2.4 环境变量（可选，但建议先设）

| 变量 | 建议值 | 作用 |
| --- | --- | --- |
| `HF_HUB_DOWNLOAD_TIMEOUT` | `60` | 大文件慢网兜底（openakita 同款） |
| `ANONYMIZED_TELEMETRY` | `False` | **必须在 `import chromadb` 之前设**，否则 posthog 缺失会直接 `ImportError` |
| `CHROMA_TELEMETRY` | `False` | 同上 |
| `HF_ENDPOINT` | `https://hf-mirror.com` | 仅当走方案 B |

> 🔴 **`HF_ENDPOINT` 有个必须知道的坑**：`huggingface_hub` 在**模块导入时**就把 `HF_ENDPOINT` 缓存成模块常量，之后再改 `os.environ` **完全无效**。必须同时 patch `huggingface_hub.constants.ENDPOINT`（≥0.25）与 `HF_ENDPOINT`（旧版）。这是 openakita `model_hub._sync_hf_hub_endpoint`（调研文档 §4.3）存在的唯一原因，也是「VectorStore 必须在 `import sentence_transformers` **之前**调 `_apply_source_env`」的原因。实现时必须照搬该双写机制。

### 2.5 冒烟自检（装完先跑，不要等到集成后）

```python
# 一次性验证：依赖 + 模型 + Chroma 三者可用
import os
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
import chromadb
from sentence_transformers import SentenceTransformer

m = SentenceTransformer(r"C:\Users\<你>\.nanobot\models\bge-small-zh-v1.5", device="cpu")
e = m.encode(["用户热爱创作，希望AI主动提供创作灵感"], normalize_embeddings=True)
print("dim =", e.shape)          # 期望 (1, 512)   ← 与 embedding_dimensions 配置必须一致

c = chromadb.PersistentClient(path="./_smoke_chroma")
col = c.get_or_create_collection("memories", metadata={"hnsw:space": "cosine"})
col.add(ids=["m1"], embeddings=e.tolist(), documents=["用户热爱创作"])
q = m.encode(["创作灵感"], normalize_embeddings=True)
print(col.query(query_embeddings=q.tolist(), n_results=1)["distances"])
# 期望：距离明显 < 1（cosine 距离，越小越相似）；若接近 1 说明模型/归一化有问题
```

**这一跑同时验证三件事**：维度是 512、`normalize_embeddings=True` 生效、Chroma `cosine` 距离语义符合预期。**dim 若与配置不符，后续一切排序都是错的。**

### 2.6 目录约定

| 路径 | 用途 |
| --- | --- |
| `{workspace}/memory/state.db` | SQLite 真相源（**已存在**，不动） |
| `{workspace}/memory/chromadb/` | ChromaDB `PersistentClient` 目录（**新增**，可安全删除重建） |
| `{workspace}/memory/vector_sync_state.json` 或 SQLite 表 | 增量同步游标（见 §5.2） |
| `~/.cache/huggingface/hub/` 或 `~/.nanobot/models/` | 模型权重 |

---

## 3. 方案对比与选型

### 3.1 接入点：改哪里挂向量检索

| 方案 | 做法 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- | --- |
| **A** | 改造 `create_search_backend` / `SearchBackend` 协议（调研文档建议） | 与调研文档一致 | 该三件套**是死代码**（§0.4 实测）；改造后还要新写接线 | ❌ 否决 |
| **B** | 在 `MemoryStoreAdapter` 内做并集（**推荐**） | 唯一活着的接入点；契约已明确；不引入新抽象层；修复分支已交付该文件 | 需先合并修复分支 | ✅ **采用** |
| **C** | 让 `RetrievalEngine` 直接持有 vector store，绕开 store | 少一层 | 与四通道契约脱节；破坏现有测试替身 `_StubStore` 形状 | ❌ 否决 |

**决策：方案 B。** 向量检索在 `MemoryStoreAdapter.search_semantic_scored` 内部与 FTS5 结果做并集，对外契约不变。

> **附带清理项**：`retrieval/search_backend.py` 三件套（`SearchBackend` / `Fts5SearchBackend` / `create_search_backend`）全仓零调用。建议在 plan 阶段评估**删除**还是保留；本 spec 不强制（删除会改动测试面，属独立决策，列入 §8 待拍板）。

### 3.2 写入路径挂载：怎么让向量跟上 SQLite

实测 `add_memory` **只有 3 个调用点**（`extractor.py:1510` / `webui/memory_api.py:286` / `database.py:242`），`update_memory` / `delete_memory` 集中在 `memory_api.py:333/344`。

| 方案 | 做法 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- | --- |
| **A** | 纯后台增量同步（按 `updated_at` 游标拉增量，不动写路径） | **零写路径改动**；天然覆盖未来新增的写入路径 | 有滞后窗口（下次同步前搜不到新记忆）；硬删除需靠反向 diff 兜 | ⚠️ 备选 |
| **B** | 纯写路径挂钩（openakita 路线） | 实时 | 5+ 个调用点，新增路径易漏挂；每个都要 try/except 不抛 | ⚠️ 备选 |
| **C** | **写路径挂钩（best-effort）+ 后台对账（双向修复）** | 实时 + 自愈；与 openakita `_sync_vector_store` 一致；漏挂也能被对账捞回 | 两处代码 | ✅ **采用** |

**决策：方案 C。** 具体：

- **同步写**：3 处 `add_memory` 之后调 `indexer.index(memory)`；`delete/update` 之后调 `indexer.remove(id)` / `indexer.index(updated)`。**全部 try/except 吞掉仅 log**——向量失败绝不影响主流程（openakita 纪律）。
- **对账**：`sync_from_sqlite()` 做双向修复——删 stale（Chroma 有、SQLite 无）+ 补 missing（SQLite 有、Chroma 无）。触发点：启动后台就绪后一次、idle 提取末尾、以及新增的手动 `reindex` API。
- **硬删除**：靠对账的反向 diff 处理，写路径的 `remove` 是优化不是保证。

> **为什么必须有对账**：openakita 的注释已明确动机——向量写入是异步/可失败的，新写入的记忆会被静默漏掉。nanobot 的抽取走 idle timer 异步路径，会遇到同样问题。调研文档 §6.2 也记录了「SQLite 写成功但向量写失败 → 索引缺条目」这个洞。

### 3.3 分数融合策略

`MemoryStoreAdapter` 需要把 Chroma 的 **cosine 距离**（越小越好）与 FTS5 的 **`1/(1+bm25)`**（越大越好）合并成一个 `list[(Memory, float)]`。

| 方案 | 做法 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- | --- |
| **A** | 取最高分并集（openakita 做法） | 简单；与参照实现一致；不引入新参数 | 两个量纲直接比大小，理论上不可校准 | ✅ **v1 采用** |
| **B** | RRF（Reciprocal Rank Fusion） | 只依赖排名、对量纲不敏感；工业界标准 | 需改 `RetrievalCandidate.relevance` 语义（从分数变排名分）；与 `reranker._MIN_COMPOSITE=0.35` 阈值需要重新校准 | ⚠️ v2 候选 |

**决策：v1 用方案 A**（`score = max(vector_score, fts5_score)`，各取 `limit*3` 候选）。理由：与 openakita 行为一致便于对比排障；`reranker.py` 下游还有 `0.4×Rel+...` 与 `0.35` 阈值，先拿到端到端可用再谈校准。

**但必须记录两个已知风险**（openakita 自己也没解决）：

1. **量纲不一致**：Chroma 的 `1-distance` ∈ [0,1]（归一化后 cosine 距离 ∈ [0,2]，需 clamp）与 FTS5 的 `1/(1+bm25_rank)` ∈ (0,1] 数值分布不同。同一条记忆在两条路上得分可能系统性偏高/偏低。
2. **`reranker._MIN_COMPOSITE = 0.35` 会被向量分数影响**：若向量相似度普遍低于 0.35，并集后候选仍被 reranker 滤掉，表现为「向量接上了但注入块还是空」。**验收时必须实测这个阈值**（见 §6.1 V4）。

### 3.4 分数语义契约（最容易错的地方）

| 层 | 数值语义 | 来源 |
| --- | --- | --- |
| Chroma `collection.query` | **distance，越小越相似**，cosine 空间 ∈ [0,2] | `hnsw:space = "cosine"` |
| **VectorStore 边界** | `score = max(0.0, min(1.0, 1.0 - distance))` | **符号翻转必须在此完成** |
| FTS5 | `score = 1.0/(1.0 + max(0.0, rank))`，越大越相关 | `database.py:269-271` 实测 |
| Adapter 输出 | `float`，**越大越相关** | 契约 |
| `RetrievalCandidate.relevance` | 越大越相关 | `channels/semantic.py:34` |
| `reranker` | `0.4×Rel + 0.2×Rec + 0.2×Imp + 0.2×Access`，阈值 `0.35` | `reranker.py:17-21` 实测 |

🔴 **绝不能让 distance 泄漏到 `reranker.py` / `formatter.py`**。调研文档 §6.4 把它列为「移植时最容易踩的坑」，openakita 在 backend 边界统一翻符号正是为此。

---

## 4. 详细设计

### 4.1 分层架构

```
                    ┌──────────────────────────────┐
                    │  SQLite state.db (唯一真相源) │
                    │  memories / episodes / ...    │
                    └───────────┬──────────────────┘
                                │ 单向派生
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
        ▼                       ▼                       ▼
  ┌───────────┐        ┌─────────────────┐     ┌──────────────┐
  │ FTS5 索引  │        │  ChromaDB 索引   │     │  (本次不做)   │
  │ (已有)     │        │  (新增, 可重建)  │     │  MEMORY.md   │
  └─────┬─────┘        └────────┬────────┘     └──────────────┘
        │                       │
        └───────────┬───────────┘
                    ▼
        ┌───────────────────────────────┐
        │ MemoryStoreAdapter            │  ← 唯一接入点
        │  search_semantic_scored()     │
        │    = max(vector, fts5) 并集    │
        └───────────────┬───────────────┘
                        ▼
        ┌───────────────────────────────┐
        │ RetrievalEngine (4 通道并行)   │
        │  → dedupe → rerank → format   │
        └───────────────┬───────────────┘
                        ▼
                system prompt 注入块
```

**读写路径不对称是有意的**：写路径 SQLite 先落库（真相源优先），向量 best-effort 跟上；读路径两路并集，任一路失败仍可用。

### 4.2 组件清单

| 组件 | 文件 | 职责 | 依赖 |
| --- | --- | --- | --- |
| `MemoryStoreAdapter` | `nanobot/memory/retrieval/store_adapter.py` | **已由修复分支交付**。本次扩展 `search_semantic_scored` 做并集 | repository |
| `VectorStore` | `nanobot/memory/vector/store.py` | ChromaDB client + collection + `model.encode`；状态机 + 后台线程 + 冷却 | chromadb, sentence-transformers |
| `model_hub` | `nanobot/memory/vector/model_hub.py` | 三源探测 + `_sync_hf_hub_endpoint` 双写 + 分层重试 + 缓存命中检测 | huggingface_hub |
| `MemoryIndexer` | `nanobot/memory/vector/indexer.py` | `index(memory)` / `remove(id)` / `sync_from_sqlite()` 双向对账 | VectorStore, MemoryDatabase |
| `VectorConfig` | `nanobot/config/schema.py` 新增字段 | 配置载体 | pydantic |

**边界纪律**（brainstorming 要求：每个单元可独立理解与测试）：
- `VectorStore` **不知道** `Memory` 领域模型——只收 `(id, content, metadata: dict)`。可脱离 nanobot 单测。
- `MemoryIndexer` **不知道** Chroma API——只调 `VectorStore` 的 3 个方法。
- `MemoryStoreAdapter` **不知道** embedding——只调 `MemoryIndexer`/`VectorStore` 的查询结果。

### 4.3 `VectorStore` 状态机（照抄 openakita 的工程学）

调研文档 §10.1 把这条列为「可以照搬」第 1 项，理由是纯工程问题、与业务无关，且 nanobot 的 `MemoryDatabase` 是纯 SQLite，**同步初始化向量库会直接拖慢启动**。

```
      ┌──────┐  构造末尾启后台线程   ┌─────────┐
      │ idle │ ──────────────────▶ │ loading │
      └──────┘                      └────┬────┘
                                   ┌────┴────┐
                                   ▼         ▼
                              ┌────────┐ ┌────────┐
                              │ ready  │ │ failed │
                              └────────┘ └───┬────┘
                                             │ 冷却到期 → 重试
                                             └──────────┘
```

| 机制 | 规则 | 理由 |
| --- | --- | --- |
| `_ensure_initialized()` | **绝不阻塞调用方**：`loading` 中直接返回 `False`，查询立即返回空 | 启动期与模型加载期不能卡住 agent |
| 普通失败冷却 | **固定 300 s** | 避免反复重试拖慢 |
| `ImportError` 冷却 | **指数退避** 300→600→1200→…→**3600 s 封顶** | 依赖可能被用户中途装上，需持续探测 |
| 重试触发点 | 每次读 `enabled` 属性都是一次潜在重试触发 | 无独立定时器 |
| 异常纪律 | 每个公开方法 `try/except` 吞掉 → 返回 `[]` / `False`，仅 log | 向量故障绝不上抛 |

### 4.4 ChromaDB 使用细节

```python
# 顺序敏感：遥测变量必须在 import chromadb 之前设置
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY", "False")
import chromadb

client = chromadb.PersistentClient(
    path=str(workspace / "memory" / "chromadb"),
    settings=Settings(anonymized_telemetry=False),
)
collection = client.get_or_create_collection(
    name="memories",
    metadata={"hnsw:space": "cosine"},     # ← 与 normalize_embeddings=True 配套
)
```

| 决策 | 值 | 理由 |
| --- | --- | --- |
| embedding 来源 | **自己 `model.encode`，不用 Chroma 内置 embedding function** | 内置函数会自行下载模型，绕开我们的 model_hub / 镜像 / 降级控制 |
| `normalize_embeddings` | **`True`** | bge 官方示例；与 `cosine` 空间语义一致（stack 文档 §四） |
| collection 名 | `memories`（固定） | 与 openakita 一致，便于对照排障 |
| metadata 字段 | `type` / `priority` / `importance` / `tags` | 与 openakita 一致；⚠️ 见下方限制 |
| 是否需要指令前缀 | **不需要**（bge-v1.5 无指令） | 模型卡实测：v1.5「enhance its retrieval ability without instruction」 |

⚠️ **metadata 限制（继承自 openakita，需知情）**：
- Chroma 的 `where` 过滤对 `float` 比较支持有限 → `min_importance` **只能在 Python 侧过滤**（取回后再筛）。
- `tags` 若拼成单个字符串，**只能整体匹配，不能多标签过滤**。本方案**不把 tags 写进向量 metadata**（避免制造「看起来能过滤其实不能」的假象），改由并集后从 SQLite 回查权威记录时过滤。

### 4.5 写入路径

| 路径 | 挂载点 | 动作 |
| --- | --- | --- |
| idle / session_end 抽取 | `extractor.py:1510`（`writer=lambda c, m=memory: add_memory(c, m)`） | 包一层：`add_memory` 成功后 `indexer.index(memory)` |
| WebUI 手动创建 | `webui/memory_api.py:286` | 同上 |
| fallback replay | `database.py:242` | 同上 |
| 更新 / 删除 | `webui/memory_api.py:333/344` | `indexer.index(updated)` / `indexer.remove(id)` |
| **对账（兜底）** | 启动就绪后 / idle 末尾 / `POST reindex` | `sync_from_sqlite()` |

**不变式**：
- `indexer.*` 任何异常 → `logger.warning` + 继续，**绝不传播**。
- `indexer.index` 幂等（Chroma `upsert` 语义，同一 id 重复写覆盖）。
- 向量写入**必须**在 SQLite 写入成功之后（真相源优先）。

### 4.6 读取路径

`MemoryStoreAdapter.search_semantic_scored(query, *, limit)`：

```
vector_result  = vector_store.search(query, limit=limit*3)    # [(id, score)]，可能为空
fts5_result    = repository.search_semantic_scored(conn, query, limit=limit*3)

merged = {id: score}                    # 取最高分
for id, s in vector_result + fts5_result:
    merged[id] = max(merged.get(id, 0.0), s)

按 score 降序 → 从 SQLite 回查权威 Memory（过滤 superseded/expired/scope）
              → 截断到 limit → list[(Memory, float)]
```

**为什么必须回查 SQLite**（openakita 同款，调研文档 §7 第 4 点）：
- 向量 metadata 不参与 scope / `superseded_by` / `expires_at` 校验（Chroma `where` 表达力不足）
- 保证「索引里多出来的僵尸 id」不会泄漏给 reranker
- scope 四元组过滤（`workspace_id` / `user_id` / `scope` / `scope_owner`）只在 SQLite 侧可靠

### 4.7 索引对账 `sync_from_sqlite()`

```
sqlite_ids = {m.id for m in load_all_memories()}
chroma_ids = set(collection.get()["ids"])

stale   = chroma_ids - sqlite_ids   → collection.delete(ids=list(stale))
missing = sqlite_ids - chroma_ids   → 逐条 encode + add
```

🔴 **必须实现为 `VectorStore` 的公共方法**，不能像 openakita 那样直接访问 `search._collection` 私有属性（调研文档 §10.1 第 3 项明确要求改造）。

`collection.get()` 在记忆量大时会一次拉全量 id 列表；v1 接受（个人 agent 量级），plan 阶段若需要可分页。

### 4.8 降级矩阵

| 场景 | 行为 | 用户可感知 |
| --- | --- | --- |
| `chromadb` / `sentence-transformers` 未装 | `_import_missing=True` → 指数退避；所有方法返回空 | `stats` 显示 `vector_available: false` + 原因 |
| 模型下载失败 | `failed` 状态，300 s 冷却后台重试；期间查询返回空 | `stats` 显示 `vector_error` |
| 后台仍在加载 | 查询立即返回空，**不阻塞** | `stats` 显示 `vector_state: "loading"` |
| `encode` / `query` 运行期异常 | 方法内吞掉 → `[]`，log error | 检索静默降级为纯 FTS5 |
| Chroma 目录损坏 | 启动失败 → 降级；`reindex` 可重建 | `stats` + 重建入口 |
| 配置 `search_backend: "fts5"` | 完全不初始化向量层 | 与今日行为完全一致 |

---

## 5. 数据与接口

### 5.1 ChromaDB collection

| 项 | 值 |
| --- | --- |
| name | `memories` |
| metadata | `{"hnsw:space": "cosine"}` |
| id | `memory.id`（与 SQLite 主键同值） |
| embedding | `model.encode(content, normalize_embeddings=True)`，**512 维** |
| document | `memory.content` |
| metadata | `{"type": str, "priority": str, "importance": float, "tags": str}`（tags 仅存档，不用于过滤） |

### 5.2 SQLite 新增

**可选（推荐）**：`embedding_cache` 表——为未来的 `api_embedding` 后端预留，本地 ST 路线**不需要**。

```sql
CREATE TABLE IF NOT EXISTS embedding_cache (
    cache_key   TEXT PRIMARY KEY,   -- sha256(f"{model}:{text}")
    model       TEXT NOT NULL,
    dimensions  INTEGER NOT NULL,
    vector      BLOB NOT NULL,      -- struct.pack(f"{n}f", ...)
    created_at  TEXT NOT NULL
);
```

**必需**：同步游标。两种实现二选一（plan 定稿）：

| 方式 | 优点 |
| --- | --- |
| `_schema_meta` 复用（`key='vector_sync_cursor'`） | 零 schema 变更 |
| 新表 `vector_sync_state(cursor, last_sync_at, last_error)` | 可记录错误、便于 `stats` 展示 |

**`_SCHEMA_VERSION` 须从 `"1"` 递增**（`database.py:20`），且新语句必须 `IF NOT EXISTS`（`ensure_schema` 会全量重跑，见 `database.py:203-211`）。

### 5.3 配置 schema（`nanobot/config/schema.py`）

紧贴现有 `memory_*` 字段（`:157-166`）：

```jsonc
{
  "memoryEnabled": true,
  "memoryIdleSeconds": 120,

  // ===== 新增 =====
  "memorySearchBackend": "fts5",              // "fts5" | "chromadb" | "api_embedding"
  "memoryVector": {
    "embeddingModel": "BAAI/bge-small-zh-v1.5",
    "embeddingDimensions": 512,                // ← openakita 漏配导致缺陷的字段，必须显式
    "device": "cpu",                           // "cpu" | "cuda"
    "downloadSource": "auto",                  // "auto" | "huggingface" | "hf-mirror" | "modelscope"
    "indexPath": "",                            // 空 = {workspace}/memory/chromadb
    "syncOnStartup": true,
    "maxCandidates": 45                         // limit*3 的上限
  }
}
```

| 字段 | 默认 | 必须存在的理由 |
| --- | --- | --- |
| `memorySearchBackend` | `"fts5"` | **显式开关**。openakita 靠 `search_backend == "chromadb"` 隐式决定，导致无独立布尔开关（调研文档 §5 缺陷 2） |
| `embeddingDimensions` | `512` | openakita **缺此字段**，硬编码 1024，与 `text-embedding-3-small` 的 1536 混用直接报维度错（§5 缺陷 1）。**本方案把它列为必填** |
| `device` | `"cpu"` | 默认 CPU；`cuda` 仅留给有 GPU 的用户 |
| `downloadSource` | `"auto"` | 中文系统优先 hf-mirror，避免网络探测浪费时间 |
| `indexPath` | `""`（回落 workspace） | openakita 固定 `{data_dir}/chromadb` 不可配（§5 缺陷 3） |

**兼容性**：默认 `"fts5"` → **现有用户升级后行为完全不变**，零迁移成本。

> **2026-11-07 变更（用户决策）：** `memorySearchBackend` 默认改为 `"chromadb"`，覆盖原 D5/R6 契约（默认 fts5、升级零变化、默认安装体积不变）。`[vector]` 仍为可选 extra，未安装时向量层初始化失败并自动降级到 FTS5；`fts5` 仍可显式回退。对应测试、`docs/memory.md` 与 `AGENTS.md` 已同步更新。

### 5.4 HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/settings/memory/stats`（**扩字段**） | 现有返回 `{total, by_type}`（`webui/memory_api.py:232-243`）。**追加** `search_backend` / `vector_available` / `vector_state` / `vector_count` / `vector_model` / `vector_dimensions` / `vector_error` |
| POST | `/api/settings/memory/vector/reindex` | 手动重建索引。返回 `{indexed, deleted, took_ms}`。**openakita 没有此端点**（只能等每日整合，运维别扭，调研文档 §9 末） |
| POST | `/api/settings/memory/vector/sync` | 只跑对账（增量），不重建 |

三处均需注册进 `webui/settings_routes.py` 的路由表（`_SYSTEM_ROUTES`，`:138-170`）；`reindex` / `sync` 是写操作，须同时加入 `_MEMORY_MUTATION_PATHS`（`:172-179`）。

> ⚠️ 调研文档 §8.2 记录了 openakita 的反面教材：向量状态在 UI 上**完全不可见**，降级时用户无感知。本方案的 `stats` 扩字段就是为了补这个洞。

---

## 6. 验收口径

### 6.1 功能验收

| # | 验收项 | 判定方式 |
| --- | --- | --- |
| V1 | **Gate 0 已解除** | 向临时库写 1 条中文记忆 → `retrieve_with_ids` 返回**非空块**（RCA §Next 要求的「能失败的测试」） |
| V2 | 四路召回真实产出 | `MemoryStoreAdapter` 四个方法各自单测通过 |
| V3 | 语义召回生效 | 写入「用户热爱创作，希望AI主动提供创作灵感」，查询「创作灵感」→ 该条命中且 `channel="vector"` |
| V4 | **`reranker._MIN_COMPOSITE=0.35` 不被击穿** | 实测向量相似度分布；若普遍 < 0.35 则调整阈值或改为 RRF（§3.3 风险 2） |
| V5 | 同义召回（向量独有能力） | 查询「情绪低落」能召回「心情不好」类记忆，而纯 FTS5 召回不到 |
| V6 | 字面召回不退化 | 专有名词/错误码查询仍能命中（FTS5 并集兜底） |
| V7 | 索引可重建 | `rm -rf memory/chromadb` → `POST reindex` → `vector_count` 恢复 |
| V8 | 对账双向修复 | 手动删 Chroma 一条 → `sync` → 补回；手动加幽灵 id → `sync` → 删除 |
| V9 | 状态可见 | `stats` 返回 7 个新字段且值正确 |

### 6.2 降级验收

| # | 场景 | 期望 |
| --- | --- | --- |
| D1 | 未装 chromadb | 全链路行为与今日**完全一致**（纯 FTS5）；启动不报错 |
| D2 | 模型下载失败 | 固定 300 s 冷却；期间查询返回空；`stats.vector_error` 有值 |
| D3 | 加载中查询 | **不阻塞**，立即返回（不与 FTS5 结果互相拖慢） |
| D4 | `encode` 抛异常 | 吞掉 → 返回 `[]`；主流程不受影响 |
| D5 | 配置 `search_backend: "fts5"` | 向量层完全不初始化，无目录、无线程、无网络 |
| D6 | 启动耗时 | **向量层对启动耗时的贡献 < 100 ms**（后台线程启动即返回） |

### 6.3 回归验收

| # | 项 |
| --- | --- |
| R1 | `tests/memory/` 现有测试全过 |
| R2 | 修复分支合并后原有 `_StubStore` 测试替身仍兼容 |
| R3 | `ruff check nanobot/` 无**新增**错误 |
| R4 | `basedpyright`（strict）无新增错误 |
| R5 | 覆盖率不低于 75% 下限 |
| R6 | 默认配置（`fts5`）下，无向量依赖的安装**体积不变** |

### 6.4 证据要求

所有验收项须产出可复现的命令 + 输出，落盘 `.ai-runtime-artifacts/verifications/`。**不接受「应该没问题」这类无证据声明。**

---

## 7. WU 拆分建议（供 `writing-plans` 参考，非最终）

> 按依赖关系排序。★ = 阻塞项。

| WU | 范围 | wu_type | 依赖 |
| --- | --- | --- | --- |
| **WU-0** ★ | 合并/移植 `origin/harness/wt-2026-09-15-memory-retrieval-rca-fix` 到 HEAD（rebase + 解冲突 + 复跑测试） | `bugfix` | — |
| **WU-1** ★ | `pyproject.toml` 新增 `vector` extra + CPU-only torch index 配置 | `config` | — |
| **WU-2** | 配置 schema：`memorySearchBackend` + `memoryVector.*`（含 `embeddingDimensions`） | `feature` | — |
| **WU-3** | `model_hub.py`：三源探测 + `_sync_hf_hub_endpoint` 双写 + 分层重试 + 缓存检测 | `feature` | WU-2 |
| **WU-4** | `VectorStore`：状态机 + 后台线程 + 冷却 + `encode`/`query` + 降级 | `feature` | WU-2, WU-1 |
| **WU-5** | `MemoryIndexer`：`index` / `remove` / `sync_from_sqlite` 双向对账 | `feature` | WU-4 |
| **WU-6** | 写路径挂载（3 处 `add_memory` + 2 处 update/delete） | `feature` | WU-5 |
| **WU-7** | `MemoryStoreAdapter.search_semantic_scored` 并集（`max` 融合 + SQLite 回查） | `feature` | WU-5, WU-0 |
| **WU-8** | `stats` 扩字段 + `reindex` / `sync` 端点 + 路由注册 | `feature` | WU-5 |
| **WU-9** | 测试：Contract（VectorStore 边界符号翻转）+ Integration（V1/V3/V7/V8）+ 降级 D1-D6 | `test` | WU-4~WU-8 |

**并行性**：WU-0 / WU-1 / WU-2 三者互不依赖，可并行起步。WU-3 与 WU-4 可并行。**WU-6 与 WU-7 必须在 WU-5 之后**（都需要 indexer 的稳定契约）。

**关键路径**：`WU-0 → WU-2 → WU-4 → WU-5 → WU-7 → WU-9`。

**预估写文件数**：≥ 3 个净新增（`vector/store.py` / `vector/model_hub.py` / `vector/indexer.py`）+ 多个修改 → **满足 Tier 2 编排硬触发**，不得 Leader 直做。

---

## 8. 风险与待拍板

### 8.1 风险

| # | 风险 | 影响 | 缓解 |
| --- | --- | --- | --- |
| R-1 | **torch 误装 CUDA 版** | 磁盘 +2 GB，安装时间大幅拉长 | §2.2 显式 CPU index；验收 D1 检查 `torch.cuda.is_available()` 与安装体积 |
| R-2 | **`HF_ENDPOINT` 缓存陷阱** | 设了镜像却仍走原站，国内下载失败 | 照搬 `_sync_hf_hub_endpoint` 双写；WU-3 必须有单测 |
| R-3 | **维度不匹配** | 换模型后排序全错且**不报错**（Chroma 只校验长度） | `embeddingDimensions` 显式配置 + 启动时校验 `model.get_sentence_embedding_dimension()` 是否一致，不一致则拒绝启用并写 `stats.vector_error` |
| R-4 | **量纲混排导致排序质量下降** | 并集后 FTS5 结果系统性压制向量结果（或反之） | §3.3 已记录；验收 V4 实测分布；v2 备选 RRF |
| R-5 | **`_MIN_COMPOSITE=0.35` 击穿** | 向量接上了但注入块仍为空——**表现为「做了但没效果」** | V4 强制实测，作为独立验收项 |
| R-6 | ~~修复分支冲突~~ **已解除** | — | `git merge-tree` 实测 **exit=0、零冲突**（§0.3）。原判「严重落后」系 `git diff` 方向用错导致的误判，已勘误 |
| R-7 | **Python 3.13 vs CI 3.11 落差** | 本机通过但 CI 失败 | R3/R4 须在 3.11 复跑 |
| R-8 | 记忆量大时 `collection.get()` 全量拉 id | 对账变慢 | 个人 agent 量级可接受；plan 阶段若需可分页 |

### 8.2 待你拍板

| # | 事项 | 选项 | 我的建议 |
| --- | --- | --- | --- |
| Q1 | **`retrieval/search_backend.py` 三件套（死代码）** | (a) 保留 (b) 删除 | **(a) 保留**。删除会改动测试面，与本次目标无关，属独立清理 |
| Q2 | **同步游标载体** | (a) 复用 `_schema_meta` (b) 新表 `vector_sync_state` | **(b)**。可记录 `last_error`，`stats` 能展示，排障成本低 |
| Q3 | **`embedding_cache` 表是否本次就建** | (a) 建 (b) 不建 | **(b) 不建**。本地 ST 路线不需要；等真要接 API embedding 时再加，避免无用的 schema 变更 |
| Q4 | **修复分支合并方式** | (a) merge (b) rebase 到 HEAD (c) 逐文件移植 | **(a) merge**。实测零冲突；merge 保留原 commit 与作者信息，改动面最小。原建议 (c) 已因误判撤回 |
| Q6 | **合并时机** | (a) 立刻合（独立于本 spec 的 git 操作） (b) 并入 WU-0 一起做 | **(a) 立刻合**。理由：检索链路是死的，早修早收益；且它独立于向量工作，不阻塞任何事 |
| Q5 | **是否需要前端向量状态面板** | (a) 本次做 (b) 后置 | **(b) 后置**。本次只交付后端 API（G5），前端另开 WU |

---

## 9. Spec 自检

| 检查项 | 结果 |
| --- | --- |
| 占位符扫描（TBD/TODO） | ✅ 无。所有「待定」项均已列入 §8.2 并给出建议 |
| 内部一致性 | ✅ §3.1 接入点（Adapter）与 §4.1 架构图、§7 WU-7 一致；§3.4 分数契约与 §4.6 读取路径一致 |
| 范围检查 | ✅ 单一实现计划可覆盖；非目标已在 §1.2 显式排除 8 项 |
| 歧义检查 | ✅ 「where 用不用 tags 过滤」已明确为「不做」（§4.4）；「游标载体」歧义已交给 §8.2 Q2 决策 |
| 证据完整 | ✅ 四个关键发现均有实测命令与输出；模型数据均实测拉取 |

## Next

**（写入后须暂停，等用户明确继续 —— 见 `harness-kit/core/routing.md` § 阶段门禁）**

- 确认方案无误 → 说「**写计划**」或「制定实施计划」
- 先回答 §8.2 的 5 个待拍板项（Q1-Q5）→ 我按你的选择调整 spec
- 需要调整方案 → 直接说修改意见
