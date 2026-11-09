---
artifact: spec
route: superpowers:brainstorming
scope: storage-only
skills:
  - brainstorming
  - source-driven-development
skills_evidence:
  - harness-kit/.agents/skills/brainstorming/SKILL.md
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
source:
  - .ai-runtime-artifacts/specs/2026-11-09-knowledge-center-spec.md
  - .ai-runtime-artifacts/stack/2026-11-09-knowledge-center-stack.md
  - WeKnora/docs/原理/00-总览-端到端运行原理.md
  - WeKnora/docs/原理/01-存储与索引原理.md
  - WeKnora/docs/原理/02-任务与状态机原理.md
  - WeKnora/docs/原理/03-检索原理.md
  - nanobot/memory/database.py
  - nanobot/evaluation/database.py
  - nanobot/utils/document.py
  - nanobot/memory/vector/store.py
  - nanobot/config/paths.py
created_at: 2026-11-09
status: draft
approved: false
parent: 2026-11-09-knowledge-center-spec.md
---

# 知序知识中心 · 存储层方案（v1）

> 本文只覆盖**知识存储**：字节真源、结构真源、FTS 派生索引、向量派生索引、状态机持久化、崩溃一致性与重建。
> 检索算法（融合/MMR/装配）、工具契约、CLI/WebUI 入口见父方案 `2026-11-09-knowledge-center-spec.md`。

## 0. 决定摘要

| # | 决定 | 依据 |
| --- | --- | --- |
| D1 | SQLite 单库：`~/.nanobot/knowledge/state.db`；KBs / documents / chunks 三表 | 单机轻量；WeKnora 的「结构真源」等价裁剪（`01 §2`） |
| D2 | `document_chunks.row_id INTEGER PRIMARY KEY` 作为 FTS 的 `content_rowid` | 显式整数主键才是稳定的 rowid 别名（SQLite 文档：无 `INTEGER PRIMARY KEY` 的表 VACUUM **可能**重排 rowid） |
| D3 | FTS5 **external content + `detail=column`** + 触发器 | 全版本可移植（≥3.9）、与 `memories_fts` 同构、自动同步；实测比 `detail=full` 省 14% |
| D4 | 自研 CJK `unigram+bigram` 写入 `search_text`，索引与查询同一函数 | `unicode61` 把整段中文当一个 token（实测 `"部署"` 命中 0），不预处理则中文检索恒空 |
| D5 | 查询用显式 `OR`，不用相邻并列 | 实测 FTS5 中 `"a" "b"` 是 **AND**（反序也命中），不是短语 |
| D6 | FTS 查询用 `CROSS JOIN` 强制 FTS 驱动 | 记忆域实测 5 万行：索引驱动 25 ms vs 计划器换驱动 4867 ms（`repository.py:754-758`） |
| D7 | 写入序列「先算向量 → 单事务落库 → 提交后写向量」 | embedding 是慢操作，不能持有 SQLite 写锁 |
| D8 | 文档级租约（`lease_owner/lease_expires_at`）+ `attempt` 单调守卫 | 允许 gateway 与 CLI 并发；对齐 WeKnora 至少一次 + epoch（`02 §8`） |
| D9 | 删除顺序「行/派生索引 → 向量 → 文件」；用户源文件永不删除 | WeKnora `01 §3.3` 的僵尸文档论证 |
| D10 | 向量按 `(backend, model, dims)` 指纹分 collection | 维度是物理结构；混维只能靠分 collection 隔离（`01 §2.3`） |

## 1. 范围与边界

**本文负责**

- 磁盘布局、路径解析、文件权限。
- SQLite 连接口径、事务边界、迁移与版本闸门。
- `knowledge_bases` / `documents` / `document_chunks` 的完整 DDL、不变量、索引。
- FTS5 表结构、分词、查询构造、同步、重建、容量模型。
- Chroma collection 的命名、元数据、写入/删除/修复契约。
- 字节真源（managed / external）的复制、哈希、去重、删除。
- 文档状态机、租约、attempt、崩溃窗口矩阵。
- 一致性检查与重建路径。
- 存储层测试矩阵与验收。

**本文不负责**（见父方案）

- 分块算法（见父方案 §7.1）、召回融合、RRF/MMR、结果装配、工具输出格式。
- CLI/WebUI 路由与页面。
- 抽取器实现细节（只定义输入契约与 PDF 续读规则）。

## 2. 调研证据

### 2.1 从 WeKnora 保留的系统级不变量

| WeKnora 原理 | 本文落地 | 不保留的理由 |
| --- | --- | --- |
| 字节真源 + 结构真源 + 派生索引三层（`01 §1/§2`） | 文件 / SQLite 三表 / FTS+Chroma | 单机不引入对象存储 |
| `file_path` + `file_hash` 是可重建能力的全部基础（`01 §2.1`） | `source_path` + `file_hash` + `source_kind` | 直接复用 |
| 正文与面包屑分离，位置偏移只指向正文（`01 §2.2/§5.5`） | `content` / `heading_path` / `start_offset,end_offset` | 直接复用 |
| 索引与查询同一分词器（`03 §4.2.2`） | `tokenize_index()` 与 `tokenize_query()` 共享底层 | 直接复用 |
| 先删后写 + upsert 幂等 + attempt 守卫（`01 §8`、`02 §8`） | 同一事务内清旧写新 + attempt 守卫 | 直接复用 |
| 删除顺序：行/索引 → 文件（`01 §3.3`） | 行/派生 → 向量 → 文件 | 直接复用 |
| 失败 ≠ 空（`05 §4.5`） | 状态机区分 failed/ready；检索侧只读 ready | 存储层只保证状态可判 |
| 维度是索引的一部分（`01 §2.3`） | collection 按指纹命名 | 直接复用 |

**明确舍弃**：对象存储、Redis/Postgres、父子块、`finalizing` 状态、图片/OCR 资产、多租户、图数据库。

### 2.2 zhixu 现有实现的可复用点

| 位置 | 复用内容 |
| --- | --- |
| `nanobot/evaluation/database.py:451-505` | `isolation_level=None` + 显式 `BEGIN`/`BEGIN IMMEDIATE`、WAL、版本迁移器、拒绝「库版本比代码新」 |
| `nanobot/memory/database.py:313-402` | `_schema_meta`、`CREATE ... IF NOT EXISTS` 幂等、连接锁与 busy_timeout 口径 |
| `nanobot/memory/database.py:111-142` | FTS5 external content + `ai/ad/au` 触发器模板 |
| `nanobot/memory/repository.py:754-764` | `CROSS JOIN` 强制 FTS 驱动 + `rank` 排序 |
| `nanobot/utils/document.py:135-418` | `open_document_line_source()` 的行流与 locator（pdf/docx/xlsx/pptx） |
| `nanobot/utils/document.py:606-615` | `_extract_text_file()` 的反例：UTF-8 失败回退 latin-1，GBK 必乱码（E17），只可借鉴不可复用 |
| `nanobot/utils/media_decode.py:61-92` | `save_base64_data_url()` 的 data URL 解码与大小守卫（WebUI 上传解码复用，落盘路径由知识库自己决定） |
| `nanobot/webui/ingress_policy.py:23-26` | `AttachmentIngressLimits.max_file_bytes = 6 MB`（WebUI 上传上限口径） |
| `nanobot/evaluation/memory/checkpoints.py:52-58` | `hash_file()`：流式 sha256（1 MiB 分块，不整文件入内存）；`files.py` 本地实现等价逻辑（AL-S6） |
| `nanobot/session/manager.py:732-740` | `_fsync_directory()`：目录 fsync 的标准写法（`EINVAL` 容忍） |
| `nanobot/config/paths.py:20-28` | `get_runtime_subdir()` 的**反例**：它内部 `ensure_dir`，只读推导会留下空目录；知识库用 `get_knowledge_dir()`（§4） |

### 2.3 本次实测（`.venv`：Python 3.13.9 / SQLite 3.52.0 / Chroma 1.5.9）

| 编号 | 问题 | 实测结果 | 对设计的影响 |
| --- | --- | --- | --- |
| E1 | `unicode61` 如何处理中文 | `"部署失败怎么回滚"` 命中 1；`"部署"` 命中 0；`"部署"*` 命中 1 | 必须预分词（D4） |
| E2 | 相邻引号 token 的语义 | `"失败" "部署"` 反序仍命中 1 → 是 AND | 查询必须显式 `OR`（D5） |
| E3 | `detail=column` 是否支持 `bm25()` | 支持；短语查询报 `fts5: phrase queries are not supported` | 可用 `detail=column`，但放弃短语查询 |
| E4 | FTS 索引体积（300 块 × 300 汉字） | 正文 270 KB；external+full 2.07 MB；external+column 1.78 MB；contentless+delete 0.64 MB | 采用 external+column（D3）；contentless 省空间但需 ≥3.43 且**不支持 `rebuild`**（实测报错），记 v2 |
| E5 | Chroma 体积（300 × 512 维） | 1.76 MB → 5.9 KB/chunk | 写入容量模型 §15 |
| E6 | `VACUUM` 是否重排 rowid | 3.52 上本例保持；文档仍声明「无 INTEGER PRIMARY KEY 的表可能重排」 | 用 `row_id INTEGER PRIMARY KEY` 消除风险（D2） |
| E7 | 外键级联删除是否触发 FTS 触发器 | 触发；`chunks=0` 且 `fts=0` | 可依赖 `ON DELETE CASCADE` 清理 FTS |
| E8 | `content_rowid` 用自定义列名 | `content_rowid='row_id'` 可用，但触发器必须写 `fts(rowid, ...)` | 明确触发器写法 |
| E9 | `bm25()` 的符号 | 返回 ≤0（越负越相关）；同长度文档并列 | 分数转换用 `-bm25()` |
| E10 | Chroma 元数据过滤 | `where={"kb_id":{"$in":[...]}}`、`$and` 组合可用；`delete(where=...)` 可用 | 支持作用域过滤与按文档清理 |
| E11 | `open_document_line_source` 的格式覆盖 | 仅 pdf/docx/xlsx/pptx；md/txt 返回 `None` | md/txt 走独立读取路径 |
| E12 | PDF 单次调用页数上限 | `_MAX_PDF_ATTACHMENT_PAGES=100`，返回 `continuation="pages='101-200'"` | 必须循环续读，否则静默漏 100 页以后内容 |
| E13 | 行流中的合成行 | `--- Page N ---` / `--- Sheet: X ---` / `--- Slide N ---` 为 `searchable=False` | 合成行不入正文，但更新定位上下文 |
| E14 | `MAX_VARIABLE_NUMBER` | 编译选项 250000 | `IN (...)` 仍按 900 一批以防旧环境 |
| E15 | §7.3 查询是否必须 `CROSS JOIN`（5 万块 / 200 命中块 / LIMIT 20） | `CROSS JOIN` 0.6 ms；普通 `JOIN` 1694 ms；后者计划变成 `documents(idx_docs_vector)` 驱动 + 逐块扫 FTS + 临时 B 树排序 | 查询 SQL 固定 `CROSS JOIN`，并在测试里断言执行计划首行为 FTS 扫描 |
| E16 | 800 字块的真实静态开销（3000 块，`dbstat` 分表计量） | 结构表 12.0 KB/块（content 2.34 + search_text 7.8–8.2 + 行/页开销 ≈1.4）；FTS 倒排 0.5–3.8 KB/块；合计 **12.6–16.2 KB/块 → 1 万块 123–158 MB** | 容量模型按实测区间重写（§15）；旧表把 FTS 与正文重复相加，已废 |
| E17 | 现有 `_extract_text_file` 的编码兜底 | UTF-8 失败后回退 **latin-1**（永不报错），GBK 文件会变成乱码 | md/txt 必须自带 `chardet` 解码，不能复用该函数 |
| E18 | 删除与向量写入并发 | 删库提交后旧任务再 upsert 向量 → 留下孤儿向量（行已删） | 读路径回表过滤 + `purge` 清理；写进崩溃窗口矩阵 |

## 3. 存储分层与不变量

```
┌────────────── 字节真源（可选托管） ──────────────┐
│ external: 用户原路径（只读，永不删除）            │
│ managed : ~/.nanobot/knowledge/uploads/<doc>/... │
└─────────────────────┬────────────────────────────┘
                      │ 抽取 → 归一化 → 分块 → 分词
┌─────────────────────▼────────────────────────────┐
│ 结构真源：SQLite（state.db）                      │
│ knowledge_bases / documents / document_chunks     │
│ + _schema_meta                                    │
└───────┬──────────────────────────────┬───────────┘
        │ 触发器同步                    │ 显式 upsert
┌───────▼──────────────┐      ┌────────▼────────────┐
│ FTS5（派生，可重建）  │      │ Chroma（派生，可重建）│
│ document_chunks_fts  │      │ kc_<model>_<dims>    │
└──────────────────────┘      └─────────────────────┘
```

**不变量（存储层必须始终成立）**

1. `document_chunks.content` 是归一化文本的**连续切片**；`heading_path` 不混入正文。
2. `start_offset/end_offset` 是**归一化文本**的半开区间 `[start, end)`；写入时断言 `normalized[start:end] == content`。
3. `documents` + `document_chunks` 是唯一结构真源；删掉 FTS 与 Chroma 后可完整重建。
4. `documents.attempt` 单调递增；任何落库写入必须携带 attempt 并在 SQL 中校验，未命中即整体回滚。
5. 检索只能返回 `documents.parse_status='ready'`、`documents.is_enabled=1`、`document_chunks.is_enabled=1`、`knowledge_bases.is_enabled=1` 的块。
6. 显式整数主键 `document_chunks.row_id` 永不更新、永不复用；`chunk_id` 由 `(doc_id, chunk_index)` 确定性派生。
7. `file_hash` 是文档内容指纹；同一 KB 内 `ready/processing/pending` 同 hash 只保留一行（`failed` 行可被复用）。
8. **删除文档/KB 永不删除 `source_kind='external'` 的文件**；只删除 `uploads/` 下由本服务写入的副本。
9. 任何 SQL 全部参数化；FTS 查询串只由「过滤后的 token + 引号 + OR」构造。
10. 结构化数据不外泄密钥：embedding key 只存在于配置/环境变量，不落库、不入日志。
11. `documents.workspace_id` 与 `document_chunks.workspace_id` **必须**等于所属 KB 的 `workspace_id`（写入时同事务断言）。
    因此 **KB 的 `workspace_id` 创建后不可修改**；要换作用域 = 新建库并重新入库（v1）。
12. 派生层允许短暂不一致，但不允许「结构真源缺失」。删除与向量写入并发可能留下孤儿向量（E18），
    读路径回表过滤、`purge` 清理即可，不得因此报错或返回半可见结果。

## 4. 磁盘布局

```
~/.nanobot/                       # get_data_dir() / get_config_path().parent
└── knowledge/                    # 可由 knowledge.databasePath 覆盖父目录语义
    ├── state.db                  # 结构真源（WAL）
    ├── state.db-wal / -shm
    ├── chromadb/                 # 向量派生层（knowledge.vectorPath）
    │   └── chroma.sqlite3 + <collection uuid>/
    ├── uploads/                  # WebUI 上传副本（managed）
    │   └── <doc_id>/
    │       └── <safe-name>.pdf
    └── tmp/                      # 原子写临时文件（与目标同盘）
```

**路径解析规则**

- `knowledge.database_path` 为空 → `get_knowledge_dir() / "state.db"`。
- `vectorPath` 为空 → `database_path.parent / "chromadb"`。
- `uploadsPath` 为空 → `database_path.parent / "uploads"`（不单独暴露配置项，减少口径）。
- 新增 `nanobot/config/paths.py::get_knowledge_dir()`：`return get_data_dir() / "knowledge"`，
  **只推导不建目录**（与 `get_teams_dir` 同口径）。注意**不能**写成 `get_runtime_subdir("knowledge")`
  —— 后者内部调用 `ensure_dir`，会让任何只读路径推导都留下空目录。
- 目录权限：POSIX 上 `0o700`；`state.db` 与上传副本 `0o600`（`chmod` best-effort，失败只告警）。
  落点在 `init_schema()`（建库后 chmod 目录与 DB 文件）与 `save_upload()`（`os.replace` 前对临时文件
  `chmod`，见 §9.2）——不要把权限交给 umask。

**存储层自有的配置面**（完整模型见父方案 §10）

| 键 | 默认 | 存储层用途 |
| --- | --- | --- |
| `knowledge.databasePath`（`database_path`） | `""` → `~/.nanobot/knowledge/state.db` | 结构真源路径；进程级单例按解析后的路径做 key |
| `knowledge.vectorPath`（`vector_path`） | `""` → `<db 目录>/chromadb` | Chroma 持久化目录 |
| `knowledge.chunking.maxFileMb`（`max_file_mb`） | `50` | CLI 入库单文件上限（WebUI 另有 6 MB） |

**为什么 uploads 按 `doc_id` 分组**：整文档删除时前缀即分组；文件名不参与键，避免路径注入与非 ASCII 编码问题（WeKnora `01 §3.1` 的同一条理由）。

## 5. 连接、事务与迁移

### 5.1 连接口径

```python
class KnowledgeDatabase:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()          # 进程内串行；写事务不可嵌套
        self._closed = False

    @contextmanager
    def connect(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, isolation_level=None)
            try:
                conn.execute("PRAGMA journal_mode = WAL")   # 必须在事务外
                conn.execute("PRAGMA synchronous = FULL")
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA busy_timeout = 15000")
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                try:
                    yield conn
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
            finally:
                conn.close()
```

**逐条理由**

| 设置 | 值 | 理由 |
| --- | --- | --- |
| `isolation_level=None` | — | 事务边界显式化；避免 sqlite3 的隐式 BEGIN 让 `BEGIN IMMEDIATE` 失效（`evaluation/database.py` 同口径） |
| `journal_mode=WAL` | 持久化属性 | 读写不互斥；必须在事务外设置，否则报 `cannot change into wal mode from within a transaction` |
| `synchronous=FULL` | 每次提交 fsync | 库里有「已入库但源文件可能被删」的托管副本，结构真源优先耐久 |
| `foreign_keys=ON` | 每连接 | SQLite 默认关闭；级联删除依赖它 |
| `busy_timeout=15000` | 15 s | gateway 与 CLI 并发时避免瞬时 `database is locked`（记忆域真机踩过 5 s 不够） |
| `immediate=True` | 写事务 | 开始即取写锁，避免「读事务升级写」在 WAL 下的 `SQLITE_BUSY` 死锁 |
| `RLock` | 进程内串行 | 单写者；同线程重入不会在 Python 层自锁。但 **SQLite 写锁不可嵌套**：内层 `BEGIN IMMEDIATE` 会等外层释放直到 `busy_timeout`，所以 repository 不得在已开事务里再调 `connect(immediate=True)` |

**异步边界**：所有公开方法都是同步的；调用方（Agent 工具 / Ingestor / WebUI handler）必须通过 `asyncio.to_thread` 或单线程执行器调用，禁止在事件循环里直接跑。

### 5.2 迁移

```python
SCHEMA_VERSION = "1"

_MIGRATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (("1", _V1_STATEMENTS),)

def init_schema(self) -> None:
    with self.connect(immediate=True) as conn:
        current = self._read_version(conn)          # 无 _schema_meta → None
        if current is not None and int(current) > int(SCHEMA_VERSION):
            raise KnowledgeSchemaTooNewError(current, SCHEMA_VERSION)
        for version, statements in _MIGRATIONS:
            if current is not None and int(version) <= current:
                continue
            for statement in statements:
                conn.execute(statement)
        conn.execute(
            "INSERT OR REPLACE INTO _schema_meta(key, value) VALUES ('version', ?)",
            (SCHEMA_VERSION,),
        )
```

- **只增不改**：已发布迁移条目不得修改；新版本只追加。
- **拒绝新库**：库版本高于代码时启动即失败（`KNOWLEDGE_SCHEMA_TOO_NEW`），绝不在未知结构上写。
- 迁移在 `BEGIN IMMEDIATE` 内完成，避免 gateway/CLI 同时初始化。
- **逐条 `execute`，不要 `executescript`**：`Connection.executescript()` 会在执行前隐式提交，
  把已经开启的 `BEGIN IMMEDIATE` 丢掉；而它按 `;` 切分语句的语义还会截断
  `CREATE TRIGGER ... BEGIN ... ; END` 的触发器体（本方案预验证时踩到，
  `sqlite3.OperationalError: incomplete input`）。`_V1_STATEMENTS` 的每个元素是一条完整语句。
- **SQLite 能力探测（不硬编码版本号）**：启动时执行
  `SELECT sqlite_compileoption_used('ENABLE_FTS5')`，并用 `CREATE VIRTUAL TABLE ... USING fts5(...)`
  做一次临时表探测；任一失败抛 `KNOWLEDGE_SQLITE_UNSUPPORTED` 并在错误里带上
  `sqlite3.sqlite_version`。本方案只依赖 FTS5（3.9+）与 `detail=column`，不使用
  `contentless_delete`（3.43+）或 `RETURNING`（3.35+），因此不设版本号闸门。
  > **实现现状（AL-S1）**：`init_schema()` 未做该探测（`nanobot/knowledge/database.py:193-209`）。
  > 迁移里的 `RETURNING`（`claim_document`）在 SQLite < 3.35 上会直接报错，
  > 因此探测应同时覆盖 `RETURNING` 能力，并在错误里指明最低版本 3.35。

## 6. Schema（v1 完整 DDL）

```sql
CREATE TABLE IF NOT EXISTS _schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- ---------- 知识库 ----------
CREATE TABLE IF NOT EXISTS knowledge_bases (
  id                   TEXT    PRIMARY KEY,               -- kb_<12 hex>
  name                 TEXT    NOT NULL,
  description          TEXT    NOT NULL DEFAULT '',
  workspace_id         TEXT    NOT NULL DEFAULT 'default',
  visibility           TEXT    NOT NULL DEFAULT 'workspace'
                       CHECK (visibility IN ('workspace','global')),
  chunk_size           INTEGER NOT NULL DEFAULT 800
                       CHECK (chunk_size BETWEEN 200 AND 4000),
  chunk_overlap        INTEGER NOT NULL DEFAULT 120
                       CHECK (chunk_overlap BETWEEN 0 AND 1000),
  embedding_backend    TEXT    NOT NULL DEFAULT 'none'
                       CHECK (embedding_backend IN ('local','remote','none')),
  embedding_model      TEXT    NOT NULL DEFAULT '',
  embedding_dimensions INTEGER NOT NULL DEFAULT 0
                       CHECK (embedding_dimensions >= 0),
  is_enabled           INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1)),
  created_at           TEXT    NOT NULL,
  updated_at           TEXT    NOT NULL,
  UNIQUE (workspace_id, name),
  CHECK (chunk_overlap < chunk_size)
);

CREATE INDEX IF NOT EXISTS idx_kb_scope ON knowledge_bases(workspace_id, visibility);

-- ---------- 文档 ----------
CREATE TABLE IF NOT EXISTS documents (
  id                TEXT    PRIMARY KEY,                  -- doc_<12 hex>
  kb_id             TEXT    NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  workspace_id      TEXT    NOT NULL DEFAULT 'default',
  title             TEXT    NOT NULL,
  source_type       TEXT    NOT NULL DEFAULT 'file' CHECK (source_type IN ('file')),
  source_kind       TEXT    NOT NULL DEFAULT 'external'
                    CHECK (source_kind IN ('external','managed')),
  source_path       TEXT    NOT NULL,                     -- 解析后的绝对路径
  original_name     TEXT    NOT NULL DEFAULT '',
  mime              TEXT    NOT NULL DEFAULT '',
  file_hash         TEXT    NOT NULL DEFAULT '',          -- sha256(原始字节)
  file_size         INTEGER NOT NULL DEFAULT 0,
  source_mtime_ns   INTEGER NOT NULL DEFAULT 0,           -- external 变更检测
  parse_status      TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (parse_status IN ('pending','processing','ready','failed')),
  error_code        TEXT    NOT NULL DEFAULT '',
  error_message     TEXT    NOT NULL DEFAULT '',
  attempt           INTEGER NOT NULL DEFAULT 0,
  lease_owner       TEXT    NOT NULL DEFAULT '',
  lease_expires_at  TEXT    NOT NULL DEFAULT '',          -- ISO8601；空 = 无租约
  index_version     TEXT    NOT NULL DEFAULT '',          -- norm/tokenizer/chunker 组合
  vector_status     TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (vector_status IN ('pending','ok','partial','failed','skipped')),
  chunk_count       INTEGER NOT NULL DEFAULT 0,
  char_count        INTEGER NOT NULL DEFAULT 0,
  is_enabled        INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1)),
  created_at        TEXT    NOT NULL,
  updated_at        TEXT    NOT NULL,
  indexed_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_docs_kb_status  ON documents(kb_id, parse_status);
CREATE INDEX IF NOT EXISTS idx_docs_hash       ON documents(kb_id, file_hash);
-- 同 KB 同路径只能有一行（§9.3 ①）；并发 add 由唯一索引兜底，不靠 check-then-insert
CREATE UNIQUE INDEX IF NOT EXISTS idx_docs_path ON documents(kb_id, source_path);
CREATE INDEX IF NOT EXISTS idx_docs_lease      ON documents(parse_status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_docs_vector     ON documents(parse_status, vector_status);

-- ---------- 分块（结构真源） ----------
CREATE TABLE IF NOT EXISTS document_chunks (
  row_id         INTEGER PRIMARY KEY,                    -- FTS content_rowid；永不复用
  id             TEXT    NOT NULL UNIQUE,                -- chk_<16 hex>，确定性派生
  doc_id         TEXT    NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kb_id          TEXT    NOT NULL,
  workspace_id   TEXT    NOT NULL DEFAULT 'default',
  chunk_index    INTEGER NOT NULL,
  content        TEXT    NOT NULL,                       -- 归一化文本的连续切片
  heading_path   TEXT    NOT NULL DEFAULT '',            -- "第一 章 > 1.2 节"
  page           INTEGER,                                -- PDF 页码（1-based）；其他 NULL
  locator        TEXT    NOT NULL DEFAULT '',            -- 首个内容行的原始 locator
  start_offset   INTEGER NOT NULL DEFAULT 0,
  end_offset     INTEGER NOT NULL DEFAULT 0,
  char_count     INTEGER NOT NULL DEFAULT 0,
  content_hash   TEXT    NOT NULL DEFAULT '',            -- sha256(归一化 content)
  search_text    TEXT    NOT NULL DEFAULT '',            -- CJK 预分词索引文本
  is_enabled     INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1)),
  created_at     TEXT    NOT NULL,
  UNIQUE (doc_id, chunk_index),
  CHECK (end_offset >= start_offset)
);

-- (doc_id, chunk_index) 已有 UNIQUE 自动索引，不再重复建 idx_chunks_doc；
-- content_hash 只用于同一文档内新旧块比对，走上面的索引取旧块即可，不单独建索引
CREATE INDEX IF NOT EXISTS idx_chunks_kb   ON document_chunks(kb_id, is_enabled);

-- 正文/索引文本一经写入不可原地修改：改内容 = 重新分块
CREATE TRIGGER IF NOT EXISTS document_chunks_immutable
BEFORE UPDATE OF content, search_text, heading_path, doc_id, chunk_index, kb_id, workspace_id
ON document_chunks
BEGIN
  SELECT RAISE(ABORT, 'CHUNK_IMMUTABLE');
END;

-- INSERT OR REPLACE 走 delete+insert，会绕过 UPDATE 触发器；默认 recursive_triggers=OFF 时
-- 内部删除也不触发 ad，FTS 索引残留旧行后任何 MATCH 都会报 missing row。
-- 因此重复主键/重复 (doc_id, chunk_index) 一律拒绝，改内容只能 DELETE 后 INSERT。
CREATE TRIGGER IF NOT EXISTS document_chunks_no_replace
BEFORE INSERT ON document_chunks
WHEN EXISTS (SELECT 1 FROM document_chunks WHERE id = new.id)
  OR EXISTS (
    SELECT 1 FROM document_chunks
    WHERE doc_id = new.doc_id AND chunk_index = new.chunk_index
  )
BEGIN
  SELECT RAISE(ABORT, 'CHUNK_IMMUTABLE');
END;

-- ---------- FTS5（派生，可重建） ----------
CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
  search_text,
  id            UNINDEXED,
  kb_id         UNINDEXED,
  workspace_id  UNINDEXED,
  content='document_chunks',
  content_rowid='row_id',
  detail=column,
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS document_chunks_ai AFTER INSERT ON document_chunks BEGIN
  INSERT INTO document_chunks_fts(rowid, search_text, id, kb_id, workspace_id)
  VALUES (new.row_id, new.search_text, new.id, new.kb_id, new.workspace_id);
END;

CREATE TRIGGER IF NOT EXISTS document_chunks_ad AFTER DELETE ON document_chunks BEGIN
  INSERT INTO document_chunks_fts(document_chunks_fts, rowid, search_text, id, kb_id, workspace_id)
  VALUES ('delete', old.row_id, old.search_text, old.id, old.kb_id, old.workspace_id);
END;
```

### 6.1 字段要点

| 字段 | 为什么必须有 |
| --- | --- |
| `source_kind` | 决定删除时是否允许 `unlink`；不能靠「路径是否在 uploads 下」猜（WeKnora `01 §4.3` 的「来源显式建模」） |
| `source_mtime_ns` | external 文档可检测源文件被替换；`reindex` 时提示「源已变化，请确认」 |
| `index_version` | `norm1:tok1:chunk1`；版本不一致的文档可被 `stats` 列出并批量重建 |
| `vector_status` | 向量写失败不能把文档打成 `failed`；关键词路仍可用，修复靠状态而非猜测 |
| `lease_owner/lease_expires_at` | gateway 与 CLI 可并发；过期租约可被回收（至少一次语义） |
| `row_id INTEGER PRIMARY KEY` | 稳定 rowid；同时避免 TEXT 主键表在 VACUUM 下的理论重排风险 |
| `chunk_index` + `UNIQUE(doc_id, chunk_index)` | 幂等键；重跑同文档时坐标唯一 |
| `content_hash` | 漂移检测与 `reindex` 变更报告（按 `(doc_id, chunk_index)` 取旧块比对） |
| `heading_path` | 面包屑只在 embedding/passage 时拼接，正文保持原样 |
| `documents.workspace_id` / `document_chunks.workspace_id` | 冗余列：Chroma 元数据与作用域过滤都要用；写入时从 KB 复制，由不变量 11 保证一致（KB 作用域不可改） |
| `document_chunks.kb_id`（**无外键**） | 级联链是 `KB → documents → chunks`；此处只做检索加速与向量元数据，不重复建外键 |
| `source_type` | v1 只有 `file`；列保留给 v2 的 `url`，CHECK 不提前扩展取值 |
| `is_enabled`（文档/块） | v1 **没有停用入口，写入恒为 1**；查询已按它过滤。v2 加「单文档/单块停用」时只需加写路径，不用迁移 |

**没有 `au` 触发器**：`content/search_text` 等索引列被 `document_chunks_immutable` 禁止原地修改，
`is_enabled` 又不参与 FTS，因此不存在「需要重写倒排」的更新路径；改内容一律走「删块 + 重插」。

**不建的表**

- `embeddings` 表：向量在 Chroma，不重复入 SQLite。
- `document_texts`：不存整篇归一化文本；偏移的可复现性由 `normalization_version` + 源文件保证。
- `documents.status_history`：v1 只用当前状态；审计放日志。

## 7. FTS5 设计

### 7.1 为什么是 external content + `detail=column`

| 方案 | 体积（E4） | 同步 | 可移植性 | 结论 |
| --- | --- | --- | --- | --- |
| external + `detail=full` | 2.07 MB / 300 chunk | 触发器自动 | ≥3.9 | 保留全部能力，但最大 |
| **external + `detail=column`** | **1.78 MB** | **触发器自动** | **≥3.9** | **采用**（唯一损失：短语查询） |
| contentless + `contentless_delete=1` | 0.64 MB | 手写（同一事务） | 需 ≥3.43 | 记 v2；旧 Linux 会硬失败 |
| 纯 `LIKE` 回退 | — | — | — | 否决：全表扫，且无法排序 |

`detail=column` 不支持短语查询（E3），而本方案的查询本来就是「token 的 OR」，不需要短语。

**FTS 表里的 `id/kb_id/workspace_id` 为什么是 `UNINDEXED`**：external content 表不存储列值，
查询时回 content 表取；声明这三列只为让 `ai/ad` 触发器与 `'delete'` 命令有完整列值可传、
并与 `memories_fts` 保持同构。`search_text` 是唯一被索引的列。

### 7.2 分词（索引与查询同一函数）

```python
_CJK_RANGES = (
    (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF), (0x20000, 0x2FA1F),
)

def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)

_ASCII_SEPARATORS = frozenset("-_.:/#@")

def tokenize_index(text: str, *, mode: str = "unigram_bigram") -> str:
    """把正文转成空格分隔的索引 token；ASCII 保持 alnum run。"""
    normalized = unicodedata.normalize("NFKC", text)
    tokens: list[str] = []
    i, n = 0, len(normalized)
    while i < n:
        ch = normalized[i]
        if _is_cjk(ch):
            j = i
            while j < n and _is_cjk(normalized[j]):
                j += 1
            tokens.extend(_cjk_run_tokens(normalized[i:j], mode))
            i = j
            continue
        if ch.isalnum():
            parts: list[str] = []
            j = i
            while j < n and (normalized[j].isalnum() or normalized[j] in _ASCII_SEPARATORS):
                if normalized[j].isalnum():
                    k = j
                    while k < n and normalized[k].isalnum():
                        k += 1
                    parts.append(normalized[j:k].lower())
                    j = k
                else:
                    j += 1
            tokens.extend(parts)
            compact = "".join(parts)
            if len(parts) > 1 and 2 <= len(compact) <= 32:
                tokens.append(compact)          # E-1024 → e1024
            i = j
            continue
        i += 1
    return " ".join(tokens)

def _cjk_run_tokens(run: str, mode: str) -> list[str]:
    if not run:
        return []
    out = list(run)                                      # unigram
    bigrams = [run[i:i + 2] for i in range(len(run) - 1)]
    if mode == "unigram_bigram":
        out.extend(bigrams)
    else:                                                # bigram
        out = bigrams or [run]
    return out

def tokenize_query(text: str, *, mode: str = "unigram_bigram", limit: int = 64) -> str:
    tokens = tokenize_index(text, mode=mode).split()
    # 去重、保序、限量；token 只保留字母/数字/CJK，杜绝 FTS 运算符与引号
    seen: list[str] = []
    for tok in tokens:
        cleaned = "".join(ch for ch in tok if ch.isalnum() or _is_cjk(ch))
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
        if len(seen) >= limit:
            break
    return " OR ".join(f'"{tok}"' for tok in seen)
```

规则与理由：

1. **CJK unigram + bigram**：单字保召回（人名/型号的单字），二字保精度；与 WeKnora「Lindera 分词」做等价兜底。
2. **ASCII 分隔符合并**：`E-1024` 同时产出 `e`、`1024`、`e1024`；实测 `"E-1024"` 直接查询命中 0（unicode61 会拆开），compact token 才能精确命中错误码/型号。
3. **查询全部加引号 + 只保留 `isalnum/CJK`**：`*`、`-`、`NEAR`、`"` 一律无法进入 MATCH，天然免疫 FTS 语法注入。
4. **索引与查询调用同一函数**（WeKnora 铁律）；`mode` 改变 = `tokenizer_version` 变化 = 必须全量重建。
5. 查询 token 上限 64：防止超长 query 造成 FTS 表达式爆炸。

> 注：索引侧 `tokenize_index` 返回的是「token 串」；`search_text` 只用于 FTS，不用于展示。

### 7.3 查询 SQL

```sql
SELECT c.id            AS chunk_id,
       c.doc_id,
       c.kb_id,
       c.chunk_index,
       c.heading_path,
       c.page,
       c.locator,
       c.content,
       c.char_count,
       -bm25(document_chunks_fts) AS keyword_score
FROM document_chunks_fts
CROSS JOIN document_chunks c ON c.row_id = document_chunks_fts.rowid
JOIN documents d        ON d.id = c.doc_id
JOIN knowledge_bases kb ON kb.id = c.kb_id
WHERE document_chunks_fts MATCH ?
  AND c.is_enabled = 1
  AND d.is_enabled = 1
  AND d.parse_status = 'ready'
  AND kb.is_enabled = 1
  AND (kb.visibility = 'global' OR kb.workspace_id IN (?, ?, ...))
ORDER BY document_chunks_fts.rank
LIMIT ?;
```

- `CROSS JOIN` 不是风格选择：普通 JOIN 下 SQLite 可能用 `idx_kb_scope`/`idx_chunks_kb` 驱动，对命中集逐行扫 FTS 虚表（记忆域实测 25 ms → 4867 ms）。过滤必须发生在 FTS 召回之后。
- **本方案的查询已实测**（E15，5 万块 / 200 命中块 / LIMIT 20）：`CROSS JOIN` 0.6 ms，普通 `JOIN` 1694–1806 ms（3 次运行区间）。执行计划对比见 `verifications/2026-11-09-knowledge-storage-ddl/verification.md` §3。
- 普通 `JOIN` 的计划**不稳定**：SQLite 3.52 选 documents 驱动（1.7 s），3.43 选 FTS 驱动（同 0.6 ms）。
  `CROSS JOIN` 的价值正是把「过滤发生在 FTS 召回之后」从优化器的偶然选择变成语法保证。
- `IN` 的占位符按实际数量生成；超过 900 个 workspace 时分批（当前上限远小于此）。
- `bm25()` 返回负值，取 `-bm25()` 得到正分；同一列表内除以最大值归一化（融合细节见父方案）。
- `ORDER BY rank` 与 `ORDER BY keyword_score DESC` 等价；用 `rank` 让 SQLite 直接走 FTS 排序。

### 7.4 同步与重建

| 操作 | 动作 |
| --- | --- |
| 插入块 | `INSERT INTO document_chunks` → `ai` 触发器写 FTS |
| 删除块/文档 | `DELETE FROM document_chunks`（含级联）→ `ad` 触发器删 FTS |
| 重新索引单文档 | 同一事务内 `DELETE ... WHERE doc_id=?` 再插入；触发器自动维护 |
| 全库重建 | 逐文档删除+重插；或 `INSERT INTO document_chunks_fts(document_chunks_fts) VALUES('rebuild')`（从 content 表重建索引） |
| 碎片整理 | `INSERT INTO document_chunks_fts(document_chunks_fts) VALUES('optimize')`；v1 只在 `knowledge repair --fts` 与进程空闲时执行，不在每次入库后自动跑 |
| 一致性抽查 | `SELECT count(*) FROM document_chunks` 与 `SELECT count(*) FROM document_chunks_fts` 比对；不一致 → `rebuild` |

**注意**：FTS5 `integrity-check` 对 external content **不会**比对 content 表（实测 E4 相关探针：直接删索引行后仍报 ok）。漂移检测以行数比对 + `rebuild` 为准，不把 `integrity-check` 当唯一证据。

## 8. 向量派生层（Chroma）

### 8.1 collection 命名与身份

```python
def collection_name(backend: str, model: str, dimensions: int) -> str:
    digest = hashlib.sha1(f"{backend}:{model}".encode()).hexdigest()[:10]
    return f"kc_{digest}_{dimensions}"
```

- 维度是物理结构：512 维与 1024 维不能同 collection（WeKnora `01 §2.3`）。
- 一个 KB 记录 `embedding_backend/model/dimensions`；写入前把三元组解析为 collection。
- 改 KB 的 embedding 配置 → 必须 `reindex`；API 层强制「先更新 KB 行，再批量入队」。
- **改配置前必须清旧 collection**：对旧三元组解析出的 collection 执行 `delete(where={"kb_id": ...})`，
  否则旧向量永久留在旧 collection 里成为不可见孤儿（新配置只查新 collection）。
  顺序是「删旧向量 → 更新 KB 行 → 重新入队」，删失败则不更新 KB。

### 8.2 写入与删除

```python
col.upsert(
    ids=[chunk.id for chunk in batch],
    embeddings=vectors,                      # list[list[float]]
    metadatas=[
        {"kb_id": c.kb_id, "doc_id": c.doc_id,
         "workspace_id": c.workspace_id, "chunk_index": c.chunk_index}
        for c in batch
    ],
    # 不传 documents：正文只在 SQLite，避免三份存储
)
```

- **不传 `documents`**：Chroma 里的文档副本会与 SQLite 正文分叉；展示与装配统一回表。
- 清理单文档：`col.delete(where={"doc_id": doc_id})`（实测 E10 可用；不存在时也是 no-op）。
- 批量删除 KB：按 `kb_id` 过滤删除；Chroma `where` 不支持空条件，因此必须逐 KB 调用。
- 批大小：每批 64 条；失败重试 3 次（指数退避），仍失败 → `documents.vector_status='failed'`，不阻塞 `ready`。
  > **实现现状（AL-I2）**：`Ingestor._write_vectors` 单批失败即 `break`，**没有重试**（`nanobot/knowledge/ingestor.py:289-305`）。
  > 本口径保留（3 次退避），实现补齐；这是「向量是派生层」的最后一层保险。
- `vector_status` 的写入点：认领时 `pending`；提交时按 ⑥ 的结果置 `pending | failed | skipped`；
  ⑧ 写完全部批次置 `ok`，部分批次失败置 `partial`。`failed` 只表示「有块但向量没写成」，
  修复扫描会重试；**没有块**时必须是 `skipped` 而不是 `pending`，否则修复扫描会对一篇失败文档无限重试。
  `partial` 表示「至少一批成功、至少一批失败」，修复扫描按 doc 全量重写（`delete` + `upsert`），不做增量补丁。
  > **实现现状（AL-I1）**：`claim_document` 的 `_CLAIM_SQL` 未写 `vector_status='pending'`
  > （`nanobot/knowledge/repository.py:43-51`），认领后该列仍是上一轮的值。
  > 后果：`failed`/`partial` 的文档被重试时，认领到写入之间存在窗口，修复扫描可能读到过期状态。补齐后认领即置 `pending`。

### 8.3 检索侧契约（存储层只保证可查）

- 查询必须带 `where={"kb_id": {"$in": kb_ids}}`。`kb_ids` 是**先用 SQL 解析出的可见 KB 集合**
  （`visibility='global' OR workspace_id IN (...)`），与 FTS 路共用同一份；不能改成过滤
  `workspace_id`——`global` 库的 `workspace_id` 是它的属主工作区，按当前工作区过滤会把其他
  工作区的全局库静默漏掉。
- `include=["distances", "metadatas"]`，不取 `documents`。
- 命中 id 必须回表 join `document_chunks`，过滤已删除/停用块后再参与融合（**孤儿向量容忍**）。
- 余弦距离 → 分数：`score = max(0.0, 1 - distance)`。Chroma 的余弦距离理论范围是 `[0, 2]`，
  不夹取会出现负分（RRF 只用名次，分数仅用于展示与阈值判断）。

### 8.4 不可用与修复

| 情况 | 行为 |
| --- | --- |
| 未安装 chromadb / 模型加载失败 | 服务照常启动；`vector_status='skipped'`；检索只走关键词 |
| 单文档向量写失败 | `vector_status='failed'`；文档仍 `ready`；关键词可检索 |
| 进程在写向量前崩溃 | 文档 `ready` + `vector_status='pending'`；启动修复扫描重投 |
| 进程在写向量中崩溃 | 部分向量已写；`pending` 扫描重做 `delete(where=doc)` + 全量 upsert（幂等） |
| collection 维度与模型不符 | 该 KB 跳过向量路；`degraded="embedding_mismatch:<kb>"` |

**修复扫描的输入是状态，不是猜测**：

```sql
SELECT id FROM documents
WHERE parse_status = 'ready'
  AND is_enabled = 1
  AND chunk_count > 0
  AND vector_status IN ('pending', 'failed', 'partial')
```

逐文档执行 `delete(where={"doc_id": ...})` + 全量 upsert，成功后置 `ok`。启动时跑一次；
`ok` 的文档不参与（数量漂移由 §14.1 单独报告）。`skipped` 也不参与——它表示「本就不该有向量」。

## 9. 文件生命周期

### 9.1 入库两种来源

| 来源 | `source_kind` | `source_path` | 删除时 |
| --- | --- | --- | --- |
| CLI 本地路径 | `external` | `Path(p).expanduser().resolve()` | **只删库，不删文件** |
| WebUI 上传 | `managed` | `uploads/<doc_id>/<safe-name>` | 删库后删副本 |

- CLI 也允许 `--copy`（可选，v2）把文件托管进 `uploads/`；v1 用 `external` 即可。
- 上传文件名：只取 `Path(name).name`，再 `safe_filename()`；扩展名与 MIME 双校验（复用 `attachment_ingress` 的文档 MIME 白名单）。
- 单文件上限：CLI `knowledge.chunking.max_file_mb`（默认 50）；WebUI 沿用 `AttachmentIngressLimits.max_file_bytes`（6 MB）。

### 9.2 哈希与原子落盘

```python
def copy_and_hash(src: Path, dest: Path) -> tuple[str, int]:
    """流式复制并计算 sha256；临时文件 + fsync + os.replace。"""
    digest = hashlib.sha256()
    size = 0
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.tmp")
    try:
        with src.open("rb") as fi, tmp.open("wb") as fo:
            while block := fi.read(1024 * 1024):
                digest.update(block)
                fo.write(block)
                size += len(block)
            fo.flush()
            os.fsync(fo.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
        _fsync_directory(dest.parent)    # 同 session/manager.py:732-740
    finally:
        tmp.unlink(missing_ok=True)
    return digest.hexdigest(), size
```

- `external` 文档只读哈希：`nanobot/knowledge/files.py` 本地实现流式 sha256（1 MiB 分块，不整文件入内存），不复制。
  不要用 `agent/tools/file_state.py::_hash_file`（`read_bytes()` 整文件入内存，50 MB 文件会翻倍占用）。
  > **实现现状（AL-S6）**：本地实现与 `evaluation/memory/checkpoints.py::hash_file` 行为等价，故不再点名复用后者。
- 哈希在**抽取之前**完成：重复入库可在解析前短路。
- `reindex` 必须重新哈希并更新 `file_hash/source_mtime_ns`（同一个写事务、带 attempt 守卫），
  否则源文件在两次入库之间被替换时，去重与漂移报告都会基于旧指纹。

### 9.3 去重

```
① 同 KB + 同 source_path（仅 external）→ 视为「同一文档的新版本」
   → 复用该行：更新 file_hash/file_size/source_mtime_ns，status='pending'，updated=true
② 同 KB + 同 file_hash + parse_status IN ('ready','processing','pending')
   → 返回已有文档，duplicate=true
③ 同 KB + 同 file_hash + parse_status='failed'
   → 复用该行：更新 source_path/size/mtime，status='pending'
   （①③ 都不动 attempt；attempt 只在 claim 时 +1）
④ 其余 → 新建 documents 行
```

规则 ① 必须在 ② 之前：文件被更新后 hash 已变，先按 hash 判会把它当成新文档，同一路径出现两行。
`--force` 只跳过 ② 的短路（强制重新解析），不改变 ①③ 的复用语义。

不使用 `UNIQUE(kb_id, file_hash)`：`failed` 行需要可复用、`force` 需要可并存；唯一性由应用层判定，索引只做查询加速。
**但 `(kb_id, source_path)` 是唯一索引**：同一 KB 同一路径出现两行没有任何合法语义，并发 `add` 时由
`IntegrityError` 兜底，捕获后重跑 ① 的查询并返回已有行。

### 9.4 删除顺序

```
1) 事务内：取 chunk 行（row_id, id）→ 删 chunks（触发器清 FTS）→ 删 document 行
2) 提交后：Chroma delete(where={"doc_id": ...})（best-effort，孤儿由读路径过滤）
3) 提交后：source_kind='managed' → unlink 副本 + 删除空目录
```

- 先删行后删文件的理由：反过来会留下「行在、文件没了」的僵尸文档，重解析与删除都会反复失败（WeKnora `01 §3.3`）。
- 文件删除失败只告警，记 `orphan_files` 统计，由 `stats`/`purge` 处理。
- 删除与正在处理的 worker 并发：文档行已删时，worker 插 `document_chunks` 会触发外键失败
  （`foreign_keys=ON`）→ worker 捕获 `IntegrityError` 后按「文档已删除」静默退出，不回写状态、不记 `failed`。
- KB 删除：先逐文档按上面顺序处理，再删 KB 行；不做「裸 `DELETE FROM knowledge_bases` 依赖级联」——级联不会清理 Chroma 与文件。

## 10. 文档状态机、租约与 attempt

```
             claim(lease)             commit(attempt 匹配)
pending ─────────────────► processing ─────────────────► ready
   ▲                            │
   │ lease 过期 / 失败           │ 失败
   └────────────────────────────┴────────────────────► failed
                                                       │ 复用/重试
                                                       └──► pending
```

**v1 没有 `finalizing`**：没有摘要/问题/图谱等富化扇出，正文与索引写完即可检索（WeKnora 的 `finalizing` 是为富化设计的）。

### 10.1 认领（claim）

```sql
UPDATE documents
SET parse_status = 'processing',
    attempt = attempt + 1,
    lease_owner = ?,
    lease_expires_at = ?,
    error_code = '',
    error_message = '',
    updated_at = ?
WHERE id = ?
  AND (parse_status IN ('pending','failed') OR
       (parse_status = 'processing' AND lease_expires_at < ?))
```

- `lease_expires_at` 建议 `now + max(300s, 2 × 预估处理时长)`；v1 固定 600 s。
- 认领时清空 `error_code/error_message`：上一次失败的原因不能挂在正在重试的文档上（WebUI 会误报）。
- 认领失败（rowcount=0）→ 说明别人在处理或状态不允许，直接返回。
- `attempt` 在认领时 +1；后续所有写入都带 `WHERE id=? AND attempt=?`。
- **reindex 不接受 `ready` 直接认领**：先在同一事务里
  `UPDATE documents SET parse_status='pending', updated_at=? WHERE id=? AND parse_status='ready'`，再入队；
  否则正常入库路径会误把可检索文档重复处理。`failed` 可以直接被 claim（重试语义）。
- **v1 不做租约续租**：单篇处理超过 600 s 会被别的进程重新认领，旧任务提交时被 attempt 守卫拒绝
  （`KNOWLEDGE_ATTEMPT_SUPERSEDED`）——是重复劳动，不是数据损坏。因此 600 s 必须覆盖最慢的单篇
  抽取+embedding；超长 PDF 的用户在日志里会看到同文档多个 attempt。

### 10.2 提交守卫

```sql
UPDATE documents
SET parse_status='ready', chunk_count=?, char_count=?,
    index_version=?, vector_status=?,          -- pending | failed | skipped
    error_code='', error_message='', indexed_at=?, updated_at=?
WHERE id=? AND attempt=?
```

- `vector_status` 是参数：向量待写 → `pending`；⑥ 已失败 → `failed`；无后端或无块 → `skipped`。
  三种情况都**照常提交 chunks**，关键词路可用（§8.2）。
- rowcount=0 → 本任务已被新 attempt 取代 → **整体 ROLLBACK**（含刚写入的 chunks），绝不覆盖新结果。

### 10.3 回收

| 时机 | 动作 |
| --- | --- |
| 启动 | `processing` 且租约过期 → 置 `pending` 并入队；未过期则不动（可能是另一个进程） |
| 周期 | 每 60 s 扫一次过期租约 |
| 关闭 | 不主动改状态；让租约自然过期，保证崩溃安全 |

> **实现现状（AL-I4）**：只有启动时的一次回收（`nanobot/knowledge/service.py:591`），
> **没有 60 s 周期任务**。后果：worker 崩溃/被杀后，其租约要等到下一次进程启动才会被回收，
> 文档在此期间一直停在 `processing`，检索侧看到的是「文档未就绪」。本口径保留，实现补齐周期任务。

## 11. 写入序列与崩溃窗口

```
① 校验（扩展名/大小/存在性/可读）        ← 旧数据仍在
② 哈希 + 去重
③ 认领：pending → processing, attempt+1, lease
④ 抽取（循环 PDF continuation）+ 归一化 + 分块
⑤ 校验器（未切/切太碎）→ 失败
⑥ 批量计算 embedding（不持 DB 锁）
⑦ 单事务：删旧 chunks（触发器清 FTS）→ 插新 chunks → attempt 守卫置 ready
⑧ 提交后：Chroma 按 doc_id 清理 + 批量 upsert → vector_status='ok'
⑨ 失败路径（②–⑤ 任一失败）：⑦ 事务从未开始 → **旧 chunks 原样保留** → failed + error_code；
   全新文档没有块时 vector_status='skipped'，有旧版本时不动旧向量状态
```

> **实现现状与口径修订（AL-I5）**：`Ingestor` 在 embedding 失败（⑥ 抛错）且新块已提交时，
> 会调用 `_discard_vectors(doc_id)` **删除该文档的旧向量**（`nanobot/knowledge/ingestor.py:135-137`），
> 与上句「有旧版本时不动旧向量状态」冲突。
> **采纳实现口径、修订方案**：旧块的 chunk id 可能与新块相同（分块确定性 → 同 id），
> 旧向量却对应旧正文，留在集合里就是**脏召回**——命中后回表拿到的是新正文，语义完全不对。
> 因此 embedding 失败时必须删除该文档的旧向量，并把 `vector_status` 置 `failed`；
> 关键词路不受影响。修订后的 ⑨：
>
> ```
> ⑨ 失败路径（②–⑤ 任一失败）：⑦ 事务从未开始 → 旧 chunks 原样保留 → failed + error_code；
>    全新文档没有块时 vector_status='skipped'
> ⑩ embedding 失败（⑥ 失败、⑦ 已提交）：新块已替换旧块 → 删除该文档旧向量（防脏召回）
>    → vector_status='failed'，error_code='KNOWLEDGE_EMBEDDING_FAILED'
> ```

**为什么是这个顺序**（WeKnora `01 §8` 的同一条因果链）

| 顺序错误 | 后果 |
| --- | --- |
| 先删后校验 | 配置/模型错时旧数据已删，文档变空 |
| 先写新块不删旧块 | 重跑一次分块翻倍，检索重复 |
| 先写向量后写 chunks | 向量指向不存在的块，回填失败 |
| 先写 chunks 后算向量（同一事务） | 长时间持有写锁，WebUI 全部 503 |
| 失败后不清理 | `failed` 文档的块仍可能被检索 |

### 11.1 崩溃窗口矩阵

| 崩溃点 | 库状态 | 派生状态 | 恢复动作 |
| --- | --- | --- | --- |
| ③ 之后、⑦ 提交前 | `processing` + 旧 chunks 完好 | 旧向量仍可用 | 租约过期 → 重新认领 |
| ⑦ 事务内 | WAL 回滚：旧 chunks 完好 | 一致 | 同上 |
| ⑦ 提交后、⑧ 前 | `ready` + 新 chunks | `vector_status='pending'` | 启动修复扫描 |
| ⑧ 中途 | `ready` + 新 chunks | 部分向量（新旧混合） | 修复扫描：按 doc 删除后重写 |
| ⑧ 之后、置 `ok` 前 | `ready` | 向量已全 | 修复扫描重复 upsert（幂等） |
| 删除：行已删、文件未删 | 行不存在 | 孤儿向量/文件 | 读路径过滤；`purge` 清理 |
| 删除与 ⑧ 并发：⑦ 已提交、删除已执行、⑧ 才 upsert | 行不存在 | 孤儿向量（E18） | 读路径回表过滤；`purge` 按 id 回表清理，不报错 |
| 双 worker：旧任务 ⑦ 成功后租约过期被重新认领 | 新 attempt 的 chunks | 旧任务 ⑧ 可能写入旧块向量 | 这些 id 不在新 chunks 中 → 孤儿；同样由回表过滤 + `purge` 兜底 |

## 12. 分块落库规范

> 本节只定义**输入契约**（行流）与**落库字段/身份**；切分算法（段落聚合、超长切分、重叠、碎片合并、校验器）
> 在父方案 §7.1，两者共享同一份 `normalized_text` 定义。

### 12.1 归一化（`normalization_version = 1`）

**顺序不可交换**（先做 NFKC 再算偏移，否则偏移会指向旧长度）：

1. 解码（整文件一次）
2. `unicodedata.normalize("NFKC", text)` + 去 BOM
3. `\r\n` / `\r` → `\n`
4. 每行 `rstrip()`（去掉行尾空白）
5. 连续 3 个以上 `\n` 折叠为 2 个
6. 在**结果字符串**上计算所有 `start_offset/end_offset`

| 步骤 | 规则 |
| --- | --- |
| 解码 | PDF/Office 由解析器给 str；txt/md 先 UTF-8，失败用 `chardet.detect`（置信度 ≥0.7，核心依赖）按检出编码解码，再失败用 `errors="replace"` |
| 字符 | `unicodedata.normalize("NFKC", text)`；去 BOM（NFKC 会把全角标点/数字折叠成半角，长度可能变化） |
| 换行 | `\r\n` / `\r` → `\n` |
| 空白 | 每行 `rstrip()`；连续 3 个以上 `\n` 折叠为 2 个 |
| 偏移 | 以上全部是 Python **字符**偏移，不是字节偏移 |

**不能复用 `_extract_text_file()`（E17）**：它对 UTF-8 失败的文件回退 `latin-1`，而 latin-1 对任意字节都不会抛错，
GBK/GB18030 文档会被静默解成乱码并"成功"入库。知识库必须自己解码，且解码失败要落到
`KNOWLEDGE_EXTRACT_FAILED`，不能靠 `replace` 假装成功（`replace` 只作为最后兜底，并在日志里记 `decoded_with=replace`）。

`index_version = f"norm{1}:tok{1}:chunk{1}"`；任一版本变化 → `stats` 列出待重建文档。

### 12.2 行流适配

```python
def iter_lines(path: Path) -> Iterator[tuple[str, str, int | None]]:
    """yield (text, locator, page)；合成行不产出。"""
    if path.suffix.lower() in {".pdf", ".docx", ".xlsx", ".pptx"}:
        pages: str | None = None
        while True:
            # 后缀已在上游白名单校验，office 分支必然返回 source（非 office 才返回 None）
            source = open_document_line_source(path, pages=pages)
            assert source is not None
            for line in source.lines:
                if not line.searchable:
                    continue                     # 页/表/幻灯片分隔符不进正文
                page = _parse_pdf_page(line.locator) if path.suffix.lower() == ".pdf" else None
                yield line.text, line.locator, page
            if not source.continuation:
                break
            pages = _parse_pages(source.continuation)   # "pages='101-200'" → "101-200"
    else:
        for text in _read_text_lines(path):
            yield text, "", None
```

- **PDF 必须循环 `continuation`**（E12）：单次最多 100 页；不循环会静默丢掉第 100 页之后的所有内容。
- `searchable=False` 的合成行（`--- Page N ---` 等）不进入正文；页码从后续行的 locator 解析。
- md/txt 不走 `open_document_line_source`（E11），由 `_read_text_lines` 直接读取全量文本；大小受 `max_file_mb` 限制。
- locator 原样落库：`page=12,line=3` / `paragraph=7` / `sheet='Sheet1',row=5` / `slide=2,line=1`；`page` 列只在 PDF 时解析为整数。
- 三个辅助函数（存储层内部，不对外）：
  - `_parse_pages("pages='101-200'")` → `"101-200"`，用 `re.fullmatch(r"pages='(\d+)-(\d+)'", s)`；不匹配直接 `KNOWLEDGE_EXTRACT_FAILED`，绝不静默截断。
  - `_parse_pdf_page(locator)` → `int | None`，用 `re.search(r"page=(\d+)", locator)`。
  - `_read_text_lines(path)`：`chardet.detect` 置信度 ≥0.7 按检出编码解码，否则 UTF-8 + `errors="replace"`；按 `str.splitlines()` 产行。

### 12.3 块身份与内容指纹

```python
def chunk_id_for(doc_id: str, chunk_index: int) -> str:
    return "chk_" + hashlib.sha256(f"{doc_id}:{chunk_index}".encode()).hexdigest()[:16]

def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
```

- `chunk_id` 只依赖 `(doc_id, chunk_index)`：重跑同文档得到同一组 id，向量 upsert 天然幂等。
- `content_hash` 独立记录内容指纹：用于漂移检测与「重跑后哪些块变了」。
- 块数变少时，旧的多余块已被「先删后写」清除；块数变多时同理。

## 13. 检索侧对存储的硬约束

这些是存储层对检索层的要求，违反即破坏不变量：

1. 只读查询使用 `connect()` 的默认（deferred）事务；不得在检索路径写状态（访问计数不进知识库）。
2. FTS 查询必须 `CROSS JOIN` + 作用域过滤 + `parse_status='ready'`（§7.3）。
3. 向量命中必须回表 join `document_chunks`，丢弃孤儿/停用块。
4. 结果必须携带 `chunk_id`/`doc_id`；没有句柄的结果不返回。
5. 空结果与失败必须可区分：查询异常上抛，不得吞成 `[]`（父方案 §8 的降级表）。
6. 同一次检索中，FTS 与向量必须看到同一份「可见 KB 集合」。
7. `limit` 上限由配置钳制（默认 20），防止一次拉爆装配。

## 14. 一致性与重建

### 14.1 漂移检测（`knowledge stats` / 启动可选）

| 检查 | 方法 | 阈值 |
| --- | --- | --- |
| 本库文档数 / 块数 | `COUNT(*)` | 只报告 |
| FTS 漂移 | `chunks` 行数 vs `document_chunks_fts` 行数 | 不等 → 告警 |
| 向量漂移 | `ready AND vector_status='ok'` 文档的块数 vs collection 中该 KB 的 `count()` | 不等 → 告警（Chroma 可能包含其他 KB） |
| 孤儿向量 | 抽样 id 回表不存在 | 计数上报，`purge` 清理 |
| 孤儿文件 | `uploads/*` 无对应 `documents.id` | 计数上报，`purge` 清理 |
| 过期索引版本 | `index_version != current` | 列出文档 id |

### 14.2 重建路径

| 操作 | 范围 | 是否重解析 |
| --- | --- | --- |
| `reindex_document` | 单文档 | 是（源文件必须在） |
| `reindex_kb` | KB 内全部文档 | 是 |
| FTS `rebuild` | 全部块 | 否（从 `search_text` 重建） |
| 向量重建 | 按 KB / 全库 | 否（从 `content` 重新 embedding） |
| `purge` | 孤儿向量/文件 | 否 |

**源文件缺失**：`external` 文档在 `reindex` 时若 `source_path` 不存在 → `failed` + `KNOWLEDGE_SOURCE_MISSING`，错误文案明确「请重新上传或恢复源文件」；绝不静默跳过。

## 15. 容量与性能模型（实测推导）

基准：CJK 文本，块大小 800 字符，`unigram+bigram`，FTS `detail=column`，Chroma 512 维。
数据来自 E16（3000 块、`dbstat` 分表计量，`measure_size.py` 可复跑），不是纸面估算。

| 组成部分 | 单块实测 | 10 000 块 | 说明 |
| --- | --- | --- | --- |
| `content` | 2.34 KB | 23 MB | 800 CJK 字 × 3 B |
| `search_text` | 7.76–8.24 KB | 76–80 MB | unigram+bigram，约 10 B/字 |
| `document_chunks` 结构表 | 12.01 KB | 117 MB | **已包含**上两项 + 行/页开销（≈1.4 KB/块） |
| FTS 倒排（`detail=column`） | 0.51–3.83 KB | 5–37 MB | 重复语料 vs 真实多样语料 |
| **SQLite 合计** | **12.64–16.16 KB** | **123–158 MB** | E16，dbstat 实测 |
| Chroma（512 维） | ≈ 5.9 KB | **≈ 58 MB** | E5 实测 |
| **总计** | **≈ 18.5–22.1 KB** | **≈ 181–216 MB** | 10k 块 ≈ 500 篇 × 20 块 |

**不要再把 `content`/`search_text` 与结构表相加**：`document_chunks` 的行内已存这两列，
旧版容量表把它们重复计入，虚高了约 40%。FTS 倒排是唯一额外的 SQLite 开销。

**结论**：个人知识库（≤ 2 万块）落在 362–432 MB，可接受。若需要压缩：

1. `tokenizer_mode="bigram"`：`search_text` 约 7 B/字，省约 32%（≈2.5 KB/块），损失单字召回。
2. 降低 `chunk_size` 不省空间（总字符不变），反而因行/页开销随块数增长而变大。
3. FTS 侧的下一步是 contentless（E4：0.64 MB / 300 块，省约 64%），代价是需 SQLite ≥3.43
   且**不支持 `rebuild`**（实测报错，只能重灌），恢复路径退化，故列 v2。
4. 更小的 embedding 模型（384/256 维）直接减少 Chroma 占用（384 维 ≈ 4.4 KB/块）。

**性能目标**（本机 SSD，10k 块）：

| 操作 | 目标 |
| --- | --- |
| 关键词召回 top-50 | < 50 ms |
| 单文档入库（不含 embedding 网络） | < 2 s |
| 单文档入库（远程 embedding，20 块） | < 5 s |
| 全库 FTS `rebuild`（10k 块） | < 30 s |
| 启动 schema 检查 | < 50 ms |

## 16. 并发与多进程

| 场景 | 机制 |
| --- | --- |
| 同进程内 WebUI/Agent/Ingestor 并发 | `KnowledgeDatabase._lock` 串行；读取也串行（短查询，代价可接受） |
| gateway 与 CLI 同时写 | WAL + `busy_timeout=15s` + `BEGIN IMMEDIATE`；文档级租约防止双处理 |
| 两个 CLI 同时 reindex 同一文档 | claim 的 `attempt+1` + 提交守卫；后提交者被拒 |
| Chroma 并发 | 进程内单例 `get_knowledge_service(config)`；不跨进程共享（CLI 进程独立打开自己的 client） |
| 迁移 | `BEGIN IMMEDIATE`；版本号在最后一步写入 |

**进程级单例**（父方案 §11）：按解析后的 DB 路径缓存；`reset_knowledge_service()` 供测试清理。理由：Chroma 每个 client 都有自己的 HNSW 内存副本，多实例会放大内存与锁竞争。

## 17. 安全

| 风险 | 处理 |
| --- | --- |
| 路径穿越 | 上传名只取 basename + `safe_filename`；托管路径必须 `resolve()` 后 `relative_to(uploads_root)`；`external` 路径 `resolve()` 后原样记录 |
| 符号链接 | 托管目录内禁止符号链接；external 允许（读用户真实文件），记录解析后的真实路径 |
| 误删用户文件 | 只有 `source_kind='managed'` 才 `unlink`；`external` 永不删 |
| 解压炸弹 / 超大文件 | 后缀白名单 + 大小上限 + `document.py` 已有的 office 归档上限（成员数/解压总量/单成员） |
| SQL 注入 | 全部参数化；`IN` 占位符动态生成；id 只接受 `[A-Za-z0-9_]` |
| FTS 语法注入 | token 白名单过滤 + 引号包裹 + 禁用 `*`/`-`/`NEAR` |
| 内容型提示注入 | 存储层不执行任何文档内容；工具层负责转义与「不可信数据」声明 |
| 密钥泄露 | key 只在配置/环境；`error_message` 落库前 redact（`sk-...`、`Bearer ...`） |
| 资源耗尽 | 单文档块数上限 20 000；单文件 50 MB；embedding 批 64；FTS 查询 token ≤ 64 |
| 备份泄露 | DB 与上传副本 `0600`，目录 `0700` |

## 18. 可观测性

### 18.1 错误码（`documents.error_code`）

| 错误码 | 触发 |
| --- | --- |
| `KNOWLEDGE_SOURCE_MISSING` | 源文件不存在/被移动 |
| `KNOWLEDGE_UNSUPPORTED_TYPE` | 扩展名不在白名单 |
| `KNOWLEDGE_FILE_TOO_LARGE` | 超 `max_file_mb` |
| `KNOWLEDGE_EXTRACT_FAILED` | 解析器异常（PDF 内容流过大、Office 归档超限等） |
| `KNOWLEDGE_TEXT_EMPTY` | 抽取后无有效文本（扫描版 PDF 的明确失败，不静默成功） |
| `KNOWLEDGE_CHUNK_INVALID` | 校验器否决（未切/切太碎） |
| `KNOWLEDGE_CHUNK_LIMIT` | 单文档块数超上限 |
| `KNOWLEDGE_EMBEDDING_FAILED` | 向量化失败（文档仍 ready，`vector_status='failed'`） |
| `KNOWLEDGE_ATTEMPT_SUPERSEDED` | 写库时守卫未命中（租约过期不单独报错：v1 不续租，被取代即走 attempt 守卫） |
| `KNOWLEDGE_DB_BUSY` | `SQLITE_BUSY` 超时（映射为可重试 503） |
| `KNOWLEDGE_SCHEMA_TOO_NEW` | 库版本高于代码 |

### 18.2 日志字段

每条入库日志固定：`doc_id`、`kb_id`、`attempt`、`stage`（extract/chunk/embed/persist/vector）、`duration_ms`、`chunk_count`、`error_code`。不记录正文与密钥。

### 18.3 `stats` 返回

```python
{
  "kbs": 3, "documents": 128, "chunks": 2611,
  "by_status": {"ready": 120, "failed": 5, "processing": 1, "pending": 2},
  "vector": {"status": "ready", "backend": "local", "model": "...", "dimensions": 512,
             "ok_docs": 118, "failed_docs": 2, "pending_docs": 3},
  "drift": {"fts_rows": 2611, "chunk_rows": 2611, "orphan_vectors": 0, "orphan_files": 0},
  "db_bytes": 40_894_464, "uploads_bytes": 12_582_912,   # 2611 块 × ≈15.7 KB
}
```

## 19. 存储层测试矩阵

目录：`tests/knowledge/storage/`

| 文件 | 覆盖 |
| --- | --- |
| `test_database.py` | schema 幂等、`_schema_meta.version`、拒绝新库、WAL/foreign_keys 生效、`BEGIN IMMEDIATE` 事务回滚 |
| `test_tokenizer.py` | 中文单字/二字、中英混合、`E-1024` compact、查询上限 64、`"`/`*`/`NEAR` 不透传、索引/查询同函数 |
| `test_fts.py` | 触发器增删、级联删除清 FTS、`detail=column` 下 bm25 排序、`rebuild` 后行数一致、`OR` 语义（反序仍命中） |
| `test_repository_chunks.py` | 块插入/读取、`CHUNK_IMMUTABLE`、`is_enabled` 可改、按 doc 批量删、offset 断言 |
| `test_vectors.py` | 假 embedder + 临时 Chroma：按 doc 删除、按 KB 过滤、维度不匹配隔离、孤儿 id 回表丢弃 |
| `test_ingest_state.py` | claim/lease/attempt、过期租约回收、提交守卫回滚、`processing` 恢复 |
| `test_files.py` | 原子写、哈希、去重（ready/failed 两种）、external 不删、managed 删除、路径穿越拒绝 |
| `test_integrity.py` | 行数比对、FTS drift 修复、`vector_status` 修复扫描、`purge` 孤儿 |
| `test_concurrency.py` | 双线程同时 claim 同一文档只有一个成功；写事务 busy 超时映射 |
| `test_extract_lines.py` | PDF `continuation` 循环（构造 250 页样例）、合成行不进正文、xlsx/pptx/docx locator、md/txt 读取 |

CI 不下载模型、不联网：`local` embedder 用假实现，`remote` 用 `httpx.MockTransport`；真实模型测试打 `@pytest.mark.integration`。

### 19.1 方案期预验证（已执行）

§6 DDL、§7 分词/查询、§15 容量模型在写计划前都已被实际执行过一遍（不是纸面推演），
记录见 `.ai-runtime-artifacts/verifications/2026-11-09-knowledge-storage-ddl/`：

| 脚本 | 作用 |
| --- | --- |
| `verify_ddl.py` | 从方案里抽出 DDL 原文执行，跑 14 条断言（幂等、FTS 同步、作用域、不可变、级联、CHECK） |
| `verify_plan.py` | 50 000 块 / 200 命中，对比 `CROSS JOIN` 与普通 `JOIN` 的执行计划与耗时（E15：0.6 ms vs 1690 ms） |
| `measure_size.py` | 3000 块两套语料，`dbstat` 分表计量单块开销（E16：12.64 / 16.16 KB） |

关键结论：

- 原文 DDL 在 `BEGIN IMMEDIATE` 内一次建齐，重复执行无副作用；
- 插入 → FTS 行数同步；中文 `创作`、型号 `E-1024` 均命中；
- `parse_status='failed'` 与实际工作区外查询均取不到；
- 改 `content` 触发 `CHUNK_IMMUTABLE`，改 `is_enabled` 正常；
- 删文档、删知识库级联后 FTS 归零，无残留可匹配；
- `optimize` / `rebuild` 可用；`chunk_overlap >= chunk_size` 被 CHECK 拒绝。

这组预验证是 §19 测试矩阵的下限：正式实现仍需把这些断言落成 pytest。脚本直接读方案文件，
方案被改动后重跑即可发现漂移。**必须用仓库 `.venv/bin/python`（SQLite 3.52）跑**：系统 Python
3.9 自带 SQLite 3.43，对普通 `JOIN` 会选出不同计划，E15 的对比结论不成立。

## 20. 验收标准（存储层）

1. 空目录启动 → `state.db` + 三表 + FTS + 触发器一次建齐；重复启动无副作用。
2. 把 `_schema_meta.version` 改成 `99` → 启动失败并给出 `KNOWLEDGE_SCHEMA_TOO_NEW`。
3. 中文查询「部署失败回滚」能命中含该串的块；`E-1024` 能命中正文中的 `E-1024`。
4. `DELETE FROM knowledge_bases WHERE id=?` 后：`documents`/`chunks`/FTS 均为 0，Chroma 中该 KB 向量为 0，上传副本被删，external 源文件仍在。
5. 处理中杀掉进程再启动：`documents` 从 `processing` 恢复为 `pending` 并最终 `ready`，块数不重复。
6. 模拟 attempt 被取代：旧任务写库返回 0 行并回滚，新结果不被覆盖。
7. FTS 行数与块数不一致时 `stats` 报告漂移；执行 `rebuild` 后一致。
8. `vector_status='failed'` 的文档仍能被关键词检索；启动修复扫描能重写向量。
9. `pytest tests/knowledge/storage -q` 全绿；`ruff check nanobot/knowledge tests/knowledge`、`basedpyright` 通过。

## 21. 实施拆分（映射到父方案 WU）

| 存储 WU | 内容 | 对应父方案 |
| --- | --- | --- |
| S-01 | `database.py`（连接/迁移）、`models.py`（行模型）、`repository.py`（KB/文档/块 CRUD）、`tokenizer.py` | WU-01 |
| S-02 | `files.py`（哈希/原子复制/去重/删除顺序/路径安全）、`extractor.py`（行流 + PDF continuation + txt 解码） | WU-02 上半 |
| S-03 | `chunker.py`（标题/段落/句子 + 校验器）与块字段落库 | WU-02 下半 |
| S-04 | `vector.py`（collection 指纹、upsert/delete/修复扫描） | WU-03 |
| S-05 | `ingestor.py`（claim/lease/attempt/写入序列/崩溃恢复）+ `stats/integrity` | WU-05 |

S-01 → S-02/S-03 → S-04 → S-05 串行；S-04 只依赖 S-01 的块模型。

## 22. 风险与待确认

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| `search_text` 约为正文 3.3–3.5 倍 | 1 万块约 76–80 MB；SQLite 合计 123–158 MB | 容量模型透明（E16 实测）；`bigram` 模式可再省约 32% |
| `detail=column` 不支持短语 | 精确短语检索不可用 | 本方案只做 token OR；短语需求走 v2 |
| unigram 噪声 | 关键词召回偏宽 | RRF 中关键词权重 0.3 + MMR 去冗余 |
| SQLite 写锁与 WebUI | 大批入库时写超时 | 单文档单事务、向量在事务外、busy_timeout 15 s、503 可重试 |
| 源文件被外部修改 | 索引与文件不一致 | `source_mtime_ns` 检测 → `reindex` 提示 |
| Chroma 与 SQLite 不同步 | 向量孤儿 | 读路径回表过滤 + `stats` 漂移 + 修复扫描 |
| FTS 行数比对不能发现同数量错位 | 极端 bug 下漏检 | `rebuild` 作为兜底；块内容自身可回表校验 |

**待确认**

1. `synchronous=FULL` 是否符合桌面端性能预期（若入库慢，可降为 `NORMAL`，代价是掉电丢最后几次提交）。
2. `chunk_size` 默认 800 是否合适（中文语义密度高，600–1000 都合理）。
3. 是否需要 `nanobot knowledge backup`（用 `sqlite3.Connection.backup` 做一致快照）——当前列为 v2。

## 23. 实现对齐修订（2026-11-10）

> 本节登记**代码实现与本方案不一致**的地方，逐条给出「实现现状 → 修订口径 → 处置」。
> 依据：`nanobot/knowledge/` 全部模块 + `nanobot/cli/knowledge.py` 的实读审计
> （2026-11-10，代码基线 commit `1532b9b8`）。
> 编号 `AL-*` 供实施计划引用；处置列 `改代码` / `改方案` / `补测试`。

### 24.1 高优先级（破坏不变量，必须改代码）

| # | 位置 | 实现现状 | 修订口径 | 处置 |
| --- | --- | --- | --- | --- |
| AL-S1 | `database.py:193-209` | `init_schema()` 无 SQLite 能力探测 | 启动时探测 `ENABLE_FTS5` + 临时 fts5 表 + `RETURNING` 能力；失败抛 `KNOWLEDGE_SQLITE_UNSUPPORTED`（§5.2） | 改代码 |
| AL-I1 | `repository.py:43-51` | `_CLAIM_SQL` 不写 `vector_status='pending'` | 认领即置 `pending`（§8.2） | 改代码 |
| AL-I2 | `ingestor.py:289-305` | 向量批写失败无重试，直接 `break` | 每批 3 次指数退避（1s/2s/4s），仍失败置 `partial`/`failed`（§8.2） | 改代码 |
| AL-I6 | `ingestor.py:232-236` | embedding 失败只写日志，不落 `documents.error_code` | 置 `error_code='KNOWLEDGE_EMBEDDING_FAILED'`（§18.1 错误码表） | 改代码 |
| AL-I8 | `ingestor.py:108-110` | KB 缺失时直接 `return`，文档停在 `processing` 直到租约过期 | KB 缺失属于不可恢复错误：立即 `_fail(doc_id, attempt, 'KNOWLEDGE_KB_MISSING')` | 改代码 |
| AL-R1 | `retriever.py:362-364` | 向量回表只查 `chunk.is_enabled`，未 join `documents.parse_status='ready' AND documents.is_enabled=1` | 两路过滤口径必须一致（§13 第 3 条）；否则失败文档的残留旧块会从向量路漏出 | 改代码（见检索 spec §14） |

### 24.2 中优先级（与方案口径冲突，改代码或改方案二选一）

| # | 位置 | 实现现状 | 修订口径 | 处置 |
| --- | --- | --- | --- | --- |
| AL-I4 | `service.py:591` | 只有启动时一次租约回收，无 60 s 周期任务 | 保留周期口径（§10.3），实现补齐 | 改代码 |
| AL-I7 | `service.py:606-607` | `_queued` 在出队瞬间 discard，处理中可被重复入队 | 出队时移入 `_processing` 集合，处理结束再移除；重复入队直接跳过 | 改代码 |
| AL-I9 | `service.py:307-308` | 改 KB 的 embedding 配置只更新行，不清旧向量/旧 collection | 「先删旧向量 → 更新 KB 行 → 重新入队」，删失败则不更新（§8.1） | 改代码 |
| AL-I3 | `service.py:732` | 修复扫描用 id 对账（`vector_expectations` 只列 `ready` 文档），`start()` 不触发向量扫描 | 补：`vector_status IN ('pending','failed','partial')` 的文档按 doc 重投；启动时触发一次扫描（§8.4、§14.1） | 改代码 |
| AL-S2 | `cli/knowledge.py` | `knowledge add` 无 `max_file_mb` 校验 | 入库前校验 `min(config.max_file_mb, 50MB)`，超限报 `KNOWLEDGE_FILE_TOO_LARGE`（§4、§9.1） | 改代码 |
| AL-I5 | `ingestor.py:135-137` | embedding 失败时删除旧向量 | **采纳实现**：旧向量与新正文语义不符，删除优于保留（§11 ⑩ 已改写） | 改方案（已改） |
| AL-S3 | `chunker.py:42-51,119-129` | txt/md 的「空行=段落边界」只作为切点优先；office 未按「每行一段」切 | 方案 §7.1 的「结构单元」改写为「切点优先规则」：txt/md 空行优先、office 标点优先，均非硬边界 | 改方案（父方案 §7.1 已同步） |
| AL-S5 | `extractor.py:41-42,74-79,116` | BOM 先 `lstrip` 再 NFKC；多出 `gb18030` 兜底；replace 兜底无日志；office 逐行归一化 | 采纳实现并写回方案：归一化顺序 = 去 BOM → NFKC；解码链 utf-8 → gb18030 → chardet(≥0.7) → replace（带 `decoded_with` 日志）；office 逐行归一化，偏移以行内偏移为准 | 改方案（父方案 §7 已同步） |

### 24.3 低优先级（口径澄清，不改行为）

| # | 位置 | 实现现状 | 修订口径 | 处置 |
| --- | --- | --- | --- | --- |
| AL-S4 | `chunker.py:231-241` | 缺不变量 `end_i <= end_{i+1}` | 补断言（父方案 §7.1 不变量表） | 改代码 |
| AL-S6 | `files.py:28-40` | 自带流式 sha256，未复用 `evaluation/memory/checkpoints.py::hash_file` | 方案 §9.2 改为「本地实现流式 sha256」，不再点名复用 | 改方案（已改） |
| AL-I10 | `embedding.py:269-270` | 远端自适应维度只改自身，未写回 KB 行 | KB 记录 `dimensions=0` 时采用首个响应长度并写回（父方案 §10.1） | 改代码 |
| AL-I11 | `embedding.py:297-301` | 远端 4xx 归为 `EmbeddingUnavailable`（当作后端不可用） | 4xx 属请求错误，不是后端不可用：应归 `EmbeddingError`，检索侧记 `embedding_error:<Type>` | 改代码 |
| AL-I13 | `knowledge/__init__.py:3` | `__all__ = []`，未导出单例 | 导出 `get_knowledge_service` / `reset_knowledge_service`（父方案 §11） | 改代码 |

## 24. Next

**（存储方案已写入，须暂停等确认 — 见 `harness-kit/core/routing.md` § 阶段门禁）**

- 认可存储方案 → 回「写计划」，我把父方案 + 本方案合并成实施计划
- 要调整（例如改块大小 / 改租约时长 / 把 `synchronous` 降为 `NORMAL`）→ 直接给修改意见
- **本轮（2026-11-10）**：§23 的 `AL-*` 偏差已转成收口实施计划
  `.ai-runtime-artifacts/plans/2026-11-10-knowledge-center-alignment-plan.md`，等确认后执行。
