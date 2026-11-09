---
artifact: spec
route: writing-plans + api-and-interface-design
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
  - harness-kit/.agents/skills/api-and-interface-design/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/specs/2026-11-10-knowledge-rag-eval-spec.md
  - .ai-runtime-artifacts/research/2026-11-10-rag-evaluation-landscape-research.md
  - .ai-runtime-artifacts/contracts/2026-11-02-contract-memory-evaluation.md
  - nanobot/evaluation/server.py
  - nanobot/evaluation/memory/routes.py
  - nanobot/knowledge/retriever.py
  - nanobot/knowledge/fusion.py
  - nanobot/knowledge/service.py
  - webui/src/lib/api.ts
  - webui/src/lib/evaluation-api.ts
created_at: 2026-11-10
status: proposed
approved: false
---

# 知识检索评测台增量契约 v1

本契约随实施计划待审；仅定义增量，不表示接口已实现。沿用 `/api/eval/v1`、snake_case 后端 + camelCase 前端映射、认证/Origin/CSRF/限流、既有 `web.RouteDef` 挂载与 SQLite `evaluation.db`。

## 1. 与既有接口的关系

- **不改**任何既有端点、既有公开工具名、既有 WebUI 动作名。本轮是**纯增量**。
- 知识检索本身的公开契约（`knowledge_search` 工具、`/api/settings/knowledge/*`）不动；评测台只是**旁路调用** `Retriever.search()`，不改产品检索行为。
- `create_eval_app()` / `create_app()` 增加**可选** `config` 参数（默认 `None`）。缺省时既有调用方行为完全不变。

## 2. 路由清单（挂进 `nanobot/evaluation/server.py` 的同一份 `_routes()`）

前缀 `/api/eval/v1`，路径 `/knowledge/...`，方法与路径均**复数名词、无动词**：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/knowledge/capabilities` | 能力与可用性（是否有 config、可用库、可用预设、judge 是否可用） |
| GET | `/knowledge/metrics` | **指标口径与计算公式元数据**（首页公式区渲染的唯一来源） |
| GET | `/knowledge/datasets` | 数据集列表（分页） |
| POST | `/knowledge/datasets` | 建数据集（空集或带用例） |
| GET | `/knowledge/datasets/{id}` | 数据集详情（含用例列表） |
| PATCH | `/knowledge/datasets/{id}` | 局部更新数据集/用例（`base_revision` 不匹配 → 409） |
| DELETE | `/knowledge/datasets/{id}` | 删数据集（幂等） |
| POST | `/knowledge/runs` | 发起运行 → **202**，请求内不执行检索 |
| GET | `/knowledge/runs` | 运行列表（分页，可按 dataset 过滤） |
| GET | `/knowledge/runs/{id}` | 运行状态、进度、配置快照 |
| GET | `/knowledge/runs/{id}/report` | 聚合指标 + 逐题明细 |
| POST | `/knowledge/runs/{id}/cancel` | 取消（终态重复取消 200，其余 202） |
| GET | `/knowledge/compare` | 对比两个运行：`?base=<run_id>&candidate=<run_id>` |

**错误语义统一**：所有错误走既有 `error_body(code, message)` 形状（`nanobot/evaluation/errors.py`），状态码映射沿用 `status_for`。本域稳定错误码：

| 错误码 | 状态 | 含义 |
| --- | --- | --- |
| `KNOWLEDGE_DATASET_NOT_FOUND` | 404 | 数据集不存在 |
| `KNOWLEDGE_DATASET_REVISION_CONFLICT` | 409 | `base_revision` 与服务端当前版本不一致 |
| `KNOWLEDGE_DATASET_NOT_EMPTY` | 422 | 非空数据集禁止改 id/label_source 语义 |
| `KNOWLEDGE_RUN_NOT_FOUND` | 404 | 运行不存在 |
| `KNOWLEDGE_CONFIG_UNSUPPORTED` | 422 | 请求的检索参数不在白名单内 |
| `KNOWLEDGE_CAPABILITY_REQUIRED` | 422 | 缺知识服务/模型预设等必要能力 |
| `KNOWLEDGE_CONFIG_UNAVAILABLE` | 503 | 评测服务无 config，知识评测不可用 |

**分页**：列表端点统一走既有 `_page(request, items, key_of=..., scope=..., filters=..., limit=...)`，游标式（`after_key` + `limit`）。

## 3. 数据模型

### 3.1 后端 Pydantic（`nanobot/evaluation/knowledge/models.py`）

- `KnowledgeEvalCase`：id、query(1..500)、expect_any(list[str] ≤ 8)、kb_ids(list[str] ≤ 32)、mode=hybrid/semantic/keyword、limit(1..20)、label_source=expect/judge、ordinal。
  - `expect_any` 非空 → `label_source` 强制 `expect`；`expect_any` 为空 → 强制 `judge`。二者由服务端推导，**客户端不传** `label_source`（避免口径漂移）。
- `KnowledgeEvalDatasetCreate`：name(1..120)、notes(≤ 500)、cases(0..500)。
- `KnowledgeEvalDatasetPatch`：base_revision(str，必填)、name?、notes?、cases?（整表替换语义）。
- `KnowledgeRetrievalConfig`（**白名单模型**，只允许覆写下列字段，其余一律 `422 KNOWLEDGE_CONFIG_UNSUPPORTED`）：
  - vector_weight: float 0..1、keyword_weight: float 0..1（两者不必和为 1，与产品 `KnowledgeRetrievalConfig` 口径一致）
  - rrf_k: int 1..1000、pool_size: int 1..200、keyword_pool_size: int 1..300
  - default_limit: int 1..20、max_limit: int 1..20
  - mmr_lambda: float 0..1、relevance_band: float 0..1、adjacent_similarity_floor: float 0..1
  - dedup_containment: float 0..1、dedup_unique_ratio: float 0..1、dedup_jaccard: float 0..1、dedup_length_ratio: float 1..100
  - mode: hybrid/semantic/keyword、limit: int 1..20
  - **不开放**：`tool_output_chars`（工具层参数，不影响检索结果）。
- `KnowledgeEvalRunCreate`：dataset_id、config(检索配置白名单，1..3 组，id 唯一)、judge_profile_id(可空；`label_source=judge` 的用例存在时必填)、budgets(max_queries 1..500、max_judge_calls 1..5000、per_query_timeout_s 1..300、concurrency 1..8)。
- `KnowledgeEvalRun`：id、dataset_id、status=queued/running/completed/cancelled/interrupted/failed、configs(快照)、judge_config(模型+提示词指纹)、metrics(聚合)、pool(候选池摘要)、progress、created_at、finished_at。
- `KnowledgeEvalResult`：run_id、case_id、status=scored/not_assessable/failed、hits(逐条 chunk_id/rank/score/content_hash/grade/reason)、metrics、error。

### 3.2 指标（`nanobot/evaluation/knowledge/metrics.py`）

指标 ID（与既有记忆评测 ID 不重名）：`hit_at_k`、`precision_at_k`、`mrr`、`ndcg_at_k`、`recall_at_k`、`pool_coverage`、`unscored_rate`。

**指标元数据（公式单点定义）**：`metrics.py` 内置 `METRIC_SPECS` 注册表，每项含 `id`、`label`、`formula`、`numerator`、`denominator`、`direction`、`description`。`GET /knowledge/metrics` 原样下发该注册表（camelCase 键：`metricId` / `label` / `formula` / `numerator` / `denominator` / `direction` / `description`）。

**公式串是契约的一部分**：后端用它算、首页用它渲染、报告用它标注。**前端不得在 i18n 文件里另抄一份公式**——公式与实现必须同源，避免漂移。指标展示名（`label`）是用户可见文案，走 i18n；`formula` 是数学表达式，随后端下发。

公式口径（与 spec §6.1 一一对应）：

| 指标 | 公式 | 分子 | 分母 | 方向 |
| --- | --- | --- | --- | --- |
| `precision_at_k` | `相关块数(top-k) ÷ k` | top-k 中 grade≥2 的块数 | k | higher_is_better |
| `recall_at_k` | `相关块数(top-k) ÷ 相关块数(pool)` | top-k 中 grade≥2 的块数 | 候选池中 grade≥2 的块数 | higher_is_better |
| `hit_at_k` | `命中查询数 ÷ 可评查询数` | top-k 含 ≥1 相关块的查询数 | 全部可评查询数 | higher_is_better |
| `mrr` | `平均( 1 ÷ 首个相关块名次 )` | — | — | higher_is_better |
| `ndcg_at_k` | `DCG ÷ IDCG`，`DCG = Σ (2^grade − 1) ÷ log₂(名次+1)` | — | — | higher_is_better |
| `pool_coverage` | `相关块数(pool) ÷ 池候选总数` | 池中 grade≥2 的块数 | 池候选总数 | higher_is_better |
| `unscored_rate` | `not_assessable 查询数 ÷ 总查询数` | `not_assessable` 查询数 | 全部查询数 | lower_is_better |

**聚合规则**：`precision_at_k` / `recall_at_k` / `mrr` / `ndcg_at_k` 为逐题值再平均（macro）；`hit_at_k` / `pool_coverage` / `unscored_rate` 为整体比值（micro）。

**每次测评的输出义务（后端强制）**：

1. 逐题：每条查询在每个被评配置下必须带 `precision_at_k` / `recall_at_k` / `hit_at_k` / `mrr` / `ndcg_at_k` 五个值 + `status`。
2. 聚合：按 `label_source` 分组给出全部 7 个指标；不可算的指标值为 `null` + `status=not_assessable`，**不补 0、不省略**。
3. 报告响应 `GET /knowledge/runs/{id}/report` 必须同时返回聚合与逐题两段——**不允许只有聚合**。

- `precision@k` = top-k 中 grade≥2 的块数 / k，逐题平均。
- `recall@k` = top-k 中 grade≥2 的块数 / **候选池中 grade≥2 的块数**（池相对；池分母为 0 → 该题 `not_assessable`）。
- `ndcg@k`：gain = 2^grade − 1，grade∈0..3；IDCG 用该题池内 grade 降序的理想排列。
- `hit@k`：top-k 中存在 grade≥2 的查询占比。
- `mrr`：首个 grade≥2 块名次的倒数，逐题平均，无命中记 0。
- `pool_coverage`：池中 grade≥2 的块数 / 池候选总数。
- `unscored_rate`：`not_assessable` 查询数 / 总查询数。
- **聚合按 `label_source` 分组**，不跨组混算；每组另出 `scored_queries` / `total_queries`。
- 缺证据一律 `not_assessable`（value=null），**不补 0**。

### 3.3 候选池与标签

- 池 = 本次所有配置的 top-`limit` 候选按 `chunk_id` 去重合并；每块记 `content_hash`、来源配置集合、chunk 正文（截断 ≤600 字符）。
- 标签绑 `content_hash`：入库后与检索返回的 `content_hash` 不一致 → 该标签 `stale` → 该题 `not_assessable`。
- `label_source=expect`：命中任一 `expect_any`（大小写不敏感子串）即 grade=3，否则 grade=0；**不调用 LLM**。
- `label_source=judge`：每查询一次批量 LLM 调用，输出严格 JSON 数组 `[{chunk_id, grade, reason}]`；`grade` 为 0..3 整数。解析失败/超时/含 `tool_calls` → 该查询 `not_assessable`。

## 4. 后端内部接口（跨 WU 边界，必须稳定）

```python
# nanobot/evaluation/knowledge/judge.py
async def judge_relevance(
    query: str,
    candidates: Sequence[JudgeCandidate],
    provider: JudgeProviderView,
    *,
    model: str | None = None,
    timeout_s: float = 60.0,
) -> JudgeBatchResult:
    """批量判定候选块对 query 的相关性（0..3）。

    永不抛出判分相关错误：任何失败都收敛成 ``status="not_assessable"``，
    原始异常文本留在 ``error`` 字段。tools 恒为 None。
    """
```

```python
# nanobot/evaluation/knowledge/runner.py
class KnowledgeEvalRunner:
    async def run(self, run_id: str) -> None: ...
    async def cancel(self, run_id: str) -> dict[str, Any]: ...
```

```python
# nanobot/knowledge/service.py（既有类上的新增方法）
def retriever_with(self, settings: RetrieverSettings) -> Retriever:
    """用同一份 repo/embedder/vector 构造指定参数的检索器（评测用）。"""
```

**`retriever_with` 契约**：只替换 `RetrieverSettings`，其余依赖（`_repo`/`_embedder`/`_vector`）与产品实例共享；不修改产品默认 `search()` 行为；不新建 Chroma 客户端、不新建 embedding 模型。

## 5. 前端类型与传输（camelCase）

新增 `webui/src/lib/knowledge-eval-api.ts`，复用 `evaluationTransport`（端口 8790，与记忆评测同源）。

| 后端字段 | 前端字段 | 类型 |
| --- | --- | --- |
| `dataset_id` / `label_source` / `not_assessable` / `content_hash` | `datasetId` / `labelSource` / `notAssessable` / `contentHash` | string 枚举 |
| `metrics.hit_at_k` | `metrics.hitAtK` | number \| null |
| `metrics.precision_at_k` | `metrics.precisionAtK` | number \| null |
| `metrics.recall_at_k` | `metrics.recallAtK` | number \| null |
| `metrics.mrr` / `metrics.ndcg_at_k` | `metrics.mrr` / `metrics.ndcgAtK` | number \| null |
| `metrics.pool_coverage` | `metrics.poolCoverage` | number \| null |
| `metrics.unscored_rate` | `metrics.unscoredRate` | number \| null |
| `hits[].grade` | `hits[].grade` | 0..3 number |

**新增前端路由**：`#/knowledge-eval` → `WorkspaceView` 新增成员 `"knowledge-eval"`；`parseWorkspaceHash` 解析 `/knowledge-eval`；`workspaceRouteHash` 反向生成 `#/knowledge-eval`；`WorkspaceNavigation` 增导航项（挂 `CAPABILITY_ITEMS`，紧随 knowledge）；`App.tsx` 增渲染分支。

**i18n**：新增文案键挂在既有 `knowledge` 顶层块下的 `knowledge.eval.*`，**10 个 locale 全部补齐**——`webui/src/tests/i18n.test.tsx` 断言各 locale 与 en 的 key 形状完全一致（missing/extra 均为空），漏一个语言即测试失败。

## 6. 状态与配额

- 运行状态机：`queued → running → (completed | cancelled | interrupted | failed)`；`interrupted` 用于服务重启后回收残留 `running`。
- 预算硬闸：`max_queries` 与 `max_judge_calls` 任一触顶 → 运行置 `failed`（错误码 `KNOWLEDGE_BUDGET_EXCEEDED`），**不产出半截指标**。
- `per_query_timeout_s` 与 `concurrency` 由运行创建时冻结，运行中不可改。
- 取消是**协作式**：runner 在查询边界检查取消位；已完成的逐题结果保留。

## 7. 边界与不做

- 不接受任意 Python 类名/模块/路径/shell/动态导入参数（沿用记忆契约的注入防线）。
- 检索参数只走 §3.1 白名单；未知字段 → `422 KNOWLEDGE_CONFIG_UNSUPPORTED`，**不静默忽略**。
- 不跨模式比较 score：报告只呈现跨模式的**集合质量**指标（hit/recall/mrr/ndcg），`score` 继续标注为相对排序分。
- 首版不支持人工 chunk 标注、不支持生成端质量、不接外部观测平台。
- LLM 判分会把 chunk 正文发给配置的模型服务（可能远程）——前端在发起 `judge` 运行前必须显式提示。
