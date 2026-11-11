# 知识库

知序的本地知识库（Knowledge Center）把 PDF / Office / Markdown / 纯文本切成块，
写入 SQLite + FTS5（关键词层）与可选的 Chroma（向量层），并同时暴露给 CLI、WebUI
和 Agent 工具（`knowledge_search` / `knowledge_read`）。v1 定位是「个人与企业内部的
私域文档检索」，不是通用 RAG 平台。

## 快速开始

```bash
# 1. 建库（--global 让所有工作区可见；不加则只在当前工作区可见）
nanobot knowledge create 产品手册 --global

# 2. 入库（CLI 默认同步等处理完成；--no-wait 只入队，交给常驻 gateway）
nanobot knowledge add ./docs/deploy.md --kb 产品手册
nanobot knowledge add ./docs/api.pdf --kb 产品手册 --title "API 手册"

# 3. 查看与检索
nanobot knowledge list
nanobot knowledge docs --kb 产品手册
nanobot knowledge doc doc_ab12cd34ef56
nanobot knowledge search "部署失败怎么回滚"
nanobot knowledge search "E-1024" --kb 产品手册 --mode keyword --json

# 4. 维护与删除（删除类命令必须显式 --yes，且不会删除你的源文件）
nanobot knowledge reindex 产品手册 --wait
nanobot knowledge repair --fts
nanobot knowledge stats
nanobot knowledge rm-doc doc_ab12cd34ef56 --yes
nanobot knowledge rm 产品手册 --yes
```

所有子命令都支持 `--config/-c <path>` 指定配置文件，`--workspace-id/--workspace`
指定工作区（默认 `default`）。退出码：`0` 成功、`1` 运行期失败（解析/入库/IO）、
`2` 用法或参数错误（含缺少 `--yes`）。

`add` 的完整选项：`--kb <id|名称>`（必填）、`--title`、`--force`（同内容也新建一条
记录）、`--no-wait`。`search` 支持 `--kb`、`--mode hybrid|semantic|keyword`、
`--limit`（1–20，默认 8）、`--json`。`reindex` 的 target 可以是知识库 id/名称或
`doc_` 开头的文档 id，`--wait` 同步等重建完成。`repair` 不带任何开关时等价于
`--fts --vectors --purge`；`--purge` 需要 `--yes`。

文档状态机：`pending → processing → ready`，失败为 `failed`。`pending` 表示已入队、
等待 gateway 的 worker 处理；CLI 的 `--no-wait` 依赖常驻 gateway，没有 worker 时
文档会一直停在 `pending`。

## 存储位置

默认目录（`get_knowledge_dir()`，即实例数据目录下的 `knowledge/`）：

```
~/.nanobot/knowledge/
├── state.db              # SQLite：knowledge_bases / documents / document_chunks + FTS5
├── state.db-wal / -shm   # WAL 附属文件
├── chromadb/             # 向量库（仅启用 embedding 时创建）
└── uploads/<doc_id>/<安全文件名>   # WebUI 上传的托管副本
```

可用配置覆盖（camelCase 与 snake_case 均可）：

```json
{
  "knowledge": {
    "enabled": true,
    "databasePath": "/data/zhixu/state.db",
    "vectorPath": "/data/zhixu/chromadb",
    "chunking": {
      "chunkSize": 800,
      "chunkOverlap": 120,
      "maxFileMb": 50,
      "tokenizerMode": "unigram_bigram",
      "paragraphMode": "blank_line"
    },
    "embedding": {
      "backend": "auto",
      "model": "BAAI/bge-small-zh-v1.5",
      "dimensions": 512,
      "device": "cpu",
      "batchSize": 16,
      "baseUrl": "",
      "apiKey": "",
      "apiKeyEnv": "",
      "localModelDir": "",
      "downloadSource": "auto",
      "timeoutSeconds": 30
    },
    "retrieval": {
      "vectorWeight": 0.7,
      "keywordWeight": 0.3,
      "rrfK": 60,
      "defaultLimit": 8,
      "maxLimit": 20
    }
  }
}
```

- `databasePath` 为空 → `~/.nanobot/knowledge/state.db`；`vectorPath` 为空 →
  `<state.db 所在目录>/chromadb`；`uploads/` 始终跟在 `state.db` 同级，没有独立配置项。
- `embedding.backend`：`auto`（优先 `remote`：`baseUrl` 非空且 key/`apiKeyEnv` 可用；
  否则 `local`：`sentence_transformers` 与 `chromadb` 都可用；都不满足则 `none`）/
  `local`（本地 sentence-transformers 模型）/ `remote`（OpenAI 兼容
  `/v1/embeddings`）/ `none`（纯关键词）。建库时 `auto` 会先解析成具体后端并把
  `(backend, model, dimensions)` 写入知识库行。
- `embedding.dimensions=0` 表示按首次实际返回长度自适应；远程 embedding 在维度
  未知前不会创建向量集合。
- `apiKeyEnv` 非空时优先从环境变量读取 key（推荐），`apiKey` 只建议本机配置。
- `downloadSource`：`auto | huggingface | hf-mirror | modelscope`；
  `localModelDir` 非空时直接加载本地模型，跳过下载。

## 支持格式与限制

| 项 | 说明 |
| --- | --- |
| 格式 | `.pdf` `.docx` `.xlsx` `.pptx` `.md` `.txt`（后缀白名单 + WebUI MIME 双校验） |
| CLI 文件上限 | 建议单文件 ≤ 50 MB：pdf/docx/xlsx/pptx 解析层有 50 MB 上限（`nanobot/utils/document.py`），md/txt 直接解码、无单独预检；超过 20 MB 的文件请走 CLI |
| WebUI 上传上限 | `min(20 MB, knowledge.chunking.maxFileMb)`，超限返回 `KNOWLEDGE_FILE_TOO_LARGE` |
| 单文档块数 | 20 000 块（超出报 `KNOWLEDGE_CHUNK_LIMIT`） |
| 分块 | 默认 800 字符、重叠 120 字符；按空行分段（`paragraphMode=blank_line`），标题/代码块不做结构感知 |
| 表格/代码块 | 按普通文本切分，不做结构保留 |
| 扫描版 PDF | 没有文字层，v1 **不支持 OCR**；请先用其他工具识别成文本再导入 |
| 不支持的类型 | 直接拒绝并返回明确原因（`KNOWLEDGE_UNSUPPORTED_TYPE`） |

`md` / `txt` 由知识抽取层直接解码（UTF-8、chardet 兜底、GB18030 兜底），
其余格式复用 `nanobot/utils/document.py` 的行流解析（含压缩炸弹与单页流上限）。

## 检索与降级

`hybrid`（默认）同时走两路：

1. **关键词**：与索引同一套分词（NFKC + 中文单字/二字 + ASCII alnum run），
   FTS5 `MATCH`，显式 `OR` 连接 token，最多 64 个；用 `bm25()` 排序。
2. **向量**：按知识库的 `(backend, model, dimensions)` 指纹分组，每组一次 query
   embedding，Chroma top-N，再回表过滤已删除/停用的块。

融合口径：

- 两路都有候选 → 加权 RRF，`vectorWeight=0.7`、`keywordWeight=0.3`、`rrfK=60`，
  再除以 `maxRRF` 归一到 `[0,1]`。单路天花板 0.7 / 0.3 是刻意设计：只有两路共识
  才有最高分。
- 只有一路有候选 → 用该路原生分（向量 cosine / 关键词相对 BM25），不套 RRF。
- 候选池：关键词 `min(max(limit×10, keyword_pool_size), 300)`，向量
  `min(max(limit×5, pool_size), 200)`。
- 去重：`content_hash` 相同合并；短文本被长文本「包含」（包含率 ≥ 0.98 或高重合且
  独有 token ≤ 2%）时只留高分块。
- 冗余抑制：同文档相邻块（`|Δchunk_index| ≤ 1`）在 MMR 中把相似度抬到下限 0.25；
  进入 MMR 的候选须落在 `fused_max − 0.15` 的相关性带内；`mmr_lambda=0.7`。
- 排序：展示顺序始终按融合分降序，tie-break 按 `chunk_id`。**`score` 只是当前模式
  下的相对排序，不是置信度**，跨模式不可比。

降级矩阵（`search --json` / WebUI 会返回 `mode_used`、`routes`、`degraded`、
`reason`）：

| 情况 | 行为 | `degraded` / `reason` |
| --- | --- | --- |
| 未安装 `chromadb`/模型不可用/无 key | 关键词单路 | `vector_unavailable` |
| 知识库 `embedding_backend='none'` | 该库不走向量 | `vector_disabled:<kb_id>` |
| 知识库索引模型与当前 embedder 不一致 | 只对一致子集走向量 | `embedding_mismatch:<kb_id>`（需 reindex） |
| 显式 `--mode semantic` 但向量不可用 | 退回关键词 | `vector_unavailable,mode_downgraded` |
| 远程 embedding 调用失败 | 关键词单路 | `embedding_error:<类型>` |
| 查询分词后无 token（纯符号/emoji） | 跳过关键词，绝不发空 `MATCH` | `reason=empty_query_tokens` |
| 两路都无候选 | 空结果 | `reason=no_candidates` |
| 有候选但全被作用域/状态过滤 | 空结果 | `reason=filtered` |
| 作用域内没有可见库 | 空结果 | `reason=no_scope` |
| 所有尝试的路由都报错 | 抛错（CLI 退出码 1） | 不是空结果 |

Agent 工具对「失败」与「空结果」的措辞是分开的：失败会写「检索失败，没有执行检索」，
空结果写「没有检索到相关内容」，并附带 `mode_used` / `degraded`，不会把失败伪装成
「知识库里没有答案」。

## 工作区隔离与全局库

- 知识库有 `visibility`：`workspace`（默认，仅所属工作区可见）与 `global`
  （所有工作区可见，`create --global` 或 WebUI 勾选「全局」）。
- 可见规则：`visibility='global' OR workspace_id IN (当前可见工作区集合)`。
  Agent 工具用 `(default, 当前项目)` 两个工作区解析；WebUI 用当前会话工作区。
- 显式指定 `--kb` 时，目标库必须可见且启用；不存在/越权/已停用一律报错
  （`knowledge base not in scope`），不会静默降级成「搜不到」。
- 知识库名在 `(workspace_id, name)` 上唯一；CLI 按名称匹配要求唯一。

## 删除语义

- `rm-doc <doc_id> --yes`：删 `documents` 行 → 级联删 `document_chunks`（FTS 由
  触发器同步归零）→ 删该文档向量 → 删除 `uploads/` 下的托管副本。
- `rm <kb> --yes`：同上，作用于该库全部文档，并清理该库向量集合。
- **通过 CLI 导入的 `external` 源文件永远不会被删除**，只删除索引与托管副本；
  删除后可以重新 `add` 同一路径重建。
- 删除不会动 `state.db` 里其他库、其他工作区的数据；`--purge` 只清理孤儿向量/
  孤儿上传目录。

## 修复与统计

```bash
nanobot knowledge stats
nanobot knowledge repair                 # = --fts --vectors --purge
nanobot knowledge repair --fts --vectors # 只做指定项
nanobot knowledge repair --purge --yes   # 清理孤儿
```

- `stats`（JSON）：`kbs`、`documents`、`chunks`、`by_status`、`vector`、
  `pending`、`expired_leases`、`drift.index_version`、`drift.stale_ready`、
  `db_bytes`、`uploads_bytes`。`drift.stale_ready` 非零表示存在「已 ready 但
  索引版本过期」的文档，需要 `reindex`。
- `repair --fts`：FTS5 `rebuild` + `optimize`，修复统计与索引的漂移。
- `repair --vectors`：扫描文档与向量的期望集合，缺失/损坏的重排重建（rewrite），
  多余的清理（purge）；向量层不可用时会明确跳过。
- `repair --purge --yes`：删除 `uploads/` 下不属于任何文档的孤儿目录。
- 修复都是幂等、非破坏的：不删 SQLite 行；`failed` 文档仍可用关键词检索。

## 安全边界

- 路径：WebUI 上传只取安全 basename，拒绝 `..`、路径分隔符与超长名字；落盘前用
  `is_within` 校验目标位于 `uploads/` 内。`repair --purge` 只删 `uploads/` 内的
  子目录。
- 权限：知识目录逐级以 `0700` 创建，`state.db` 及 `-wal`/`-shm` 置 `0600`；
  上传文件走临时文件 + fsync + `os.replace`，同样 `0600`。
- 上传类型：后缀白名单 + MIME 白名单双校验；`csv` 等不在白名单内的类型直接拒绝。
- 查询：token 只保留字母/数字/CJK，天然挡掉 `*`、`-`、`NEAR`、`"` 等 FTS 语法；
  SQL 参数全部绑定；检索路径只读，不记录 query 或访问计数。
- 凭据：`embedding.apiKey` 不落库、不进日志；错误信息会抹掉 Bearer token 与
  `sk-` 形式的 key。

## v1 非目标

以下能力 v1 明确不做，别在文档/工单里当成已实现：

- 知识图谱、实体关系抽取。
- 重排模型（reranker）与 cross-encoder 精排。
- OCR：扫描版 PDF 需先自行转成文本。
- 入库期问题生成（doc2query）与查询改写。
- 多 embedder 并存（每个知识库一套模型）：v1 只有一个活动 embedder，其他库按
  `embedding_mismatch` 退出向量路。
- 结构化表格/代码块感知切分、跨库去重合并。
