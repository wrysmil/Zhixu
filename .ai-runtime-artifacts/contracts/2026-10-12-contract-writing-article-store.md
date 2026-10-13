---
artifact: contract
route: superpowers:writing-plans
skills:
  - writing-plans
  - api-and-interface-design
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
source:
  - .ai-runtime-artifacts/specs/2026-10-10-zhixu-writing-capability-spec.md
  - nanobot/webui/settings_contracts.py
  - nanobot/webui/memory_routes.py
  - nanobot/webui/settings_routes.py
  - nanobot/webui/ws_http.py
  - nanobot/webui/inbound_commands.py
  - nanobot/memory/database.py
  - webui/src/lib/api.ts
  - webui/src/lib/nanobot-client.ts
created_at: 2026-10-12
status: draft
approved: false
tier: 2
---

# 知序写作能力 — 文章存储接口契约 v1

> 本文件锁定阶段 A（A-01~A-09）与阶段 B 共用的**稳定接口边界**。plan 内的 Task 只能引用本契约的标识符，不得自行改形状。
> 契约变动须先改本文件并说明理由，再同步两份 plan。

## 1. 现有通道依据（不新造机制）

| 结论 | 代码依据 |
| --- | --- |
| 读取走 HTTP GET + Bearer token，由 `WebUISettingsRouter` 按 `_SYSTEM_ROUTES` 路径表分发 | `nanobot/webui/settings_routes.py:136-200`、`_route():496-509`、`ws_http.py:548`（settings 路由对全部路径优先委派） |
| 写入只能走已认证 WebSocket 的 `webui_request`，动作名 → 路径映射表是 `_WEBUI_MUTATION_PATHS` | `nanobot/webui/ws_http.py:149-203`、`_is_webui_mutation_path():490-505` |
| mutation 错误体是**纯文本 message**，不是 JSON；HTTP 状态码单独携带 | `nanobot/webui/http_utils.py:142-144`、`inbound_commands.py:1349-1352` |
| 传输层已有 `request_id` 幂等：同 `request_id` 重放复用同一 operation，payload 不同则 409；缓存窗口 5 分钟 / 256 条 | `nanobot/webui/inbound_commands.py:1199-1244`、`_WEBUI_REQUEST_CACHE_TTL_S:63` |
| 同一连接的 mutation 已被 `asyncio.Lock` 串行执行 | `inbound_commands.py:1326-1327` |
| 客户端每次 `requestMutation` 自行生成传输层 `request_id`，断线重连用同一 id 重放 | `webui/src/lib/nanobot-client.ts:915-935` |
| SQLite 连接管理模式（stdlib sqlite3 + `threading.Lock` + `row_factory` + `_schema_meta` 版本） | `nanobot/memory/database.py:167-224` |
| 领域 handler 模式：`*SettingsOperations`（frozen dataclass of Callables）+ `*_ACTION_NAMES` + `handle(action, request)` | `nanobot/webui/memory_routes.py:30-90` |

**推论（必须落进实现）：**

1. 传输层 `request_id` **不会**透传给领域 handler（`execute_webui_request` 只收到 `action` + `payload`）。因此**业务级去重需要 payload 内显式携带 `client_request_id`**，前端在重试时复用同一个值。
2. 「相同内容重复保存不推进 revision」不能依赖传输层，必须在 repository 内用内容摘要判定。
3. 冲突信息必须编码进 message 文本（前端按正则解析），见 §6。

## 2. 持久化边界

- 存储引擎：**SQLite**（stdlib `sqlite3`），与 `nanobot/memory/database.py` 同构。理由见 `.ai-runtime-artifacts/plans/2026-10-12-zhixu-writing-phase-a-plan.md` § 决策记录。
- 库文件：`<workspace_path>/writing/state.db`（`workspace_path` 即 gateway 的 `get_workspace_path()`，与 memory 的 `<workspace>/memory/state.db` 并列）。
- 模块归属：`nanobot/writing/`（`database.py` / `models.py` / `repository.py`），WebUI 层 `nanobot/webui/writing_{services,api,routes}.py`。写作领域**不写** `nanobot/memory/*`，也不复用其表。
- 工作区隔离：每行携带 `workspace_key = sha256(str(Path(workspace_path).resolve()).encode("utf-8")).hexdigest()[:16]`，所有读写都带 `WHERE workspace_key = ?`。客户端**不传**工作区标识（避免伪造与跨区读取）。
- 已知边界（不掩盖）：一个 gateway 进程绑定一个 `workspace_path`（`gateway_services.py:212-234` 的 `skills_workspace_path`/`build_memory_operations` 同源），因此「切换项目」只有在新 gateway 生效时才产生新 `workspace_key`。阶段 A 不宣称能在同一进程内切换文章工作区。

### 2.1 Schema v1

```sql
CREATE TABLE IF NOT EXISTS articles (
  id             TEXT    NOT NULL,
  workspace_key  TEXT    NOT NULL,
  title          TEXT    NOT NULL,
  writing_type   TEXT    NOT NULL DEFAULT '',
  template_id    TEXT    NOT NULL DEFAULT '',
  status         TEXT    NOT NULL DEFAULT 'idea',
  summary        TEXT    NOT NULL DEFAULT '',
  brief          TEXT    NOT NULL DEFAULT '{}',
  body           TEXT    NOT NULL DEFAULT '',
  outline        TEXT    NOT NULL DEFAULT '[]',
  materials      TEXT    NOT NULL DEFAULT '[]',
  tags           TEXT    NOT NULL DEFAULT '[]',
  revision       INTEGER NOT NULL DEFAULT 1,
  content_digest TEXT    NOT NULL,
  created_at     TEXT    NOT NULL,
  updated_at     TEXT    NOT NULL,
  PRIMARY KEY (workspace_key, id)
);
CREATE INDEX IF NOT EXISTS articles_updated_idx
  ON articles (workspace_key, updated_at DESC);

CREATE TABLE IF NOT EXISTS article_requests (
  request_id   TEXT    NOT NULL,
  workspace_key TEXT   NOT NULL,
  article_id   TEXT    NOT NULL,
  op           TEXT    NOT NULL,
  response     TEXT    NOT NULL,
  created_at   TEXT    NOT NULL,
  PRIMARY KEY (workspace_key, request_id)
);

CREATE TABLE IF NOT EXISTS _schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

- `_schema_meta.version = '1'`；`ensure_schema()` 幂等重跑全部 `CREATE ... IF NOT EXISTS`（与 `MemoryDatabase.ensure_schema` 同语义）。
- 时间一律 `datetime.now(timezone.utc).isoformat()`，展示层负责本地化；**禁止**存「刚刚」这类相对文案。
- `brief` / `outline` / `materials` / `tags` 为 JSON 文本列；一行为一个**完整文章快照**，这是 A-06 原子保存的实现前提。

## 3. 领域对象（持久化字段名即协议字段名，snake_case）

| 对象 | 字段 |
| --- | --- |
| Article | `id, workspace_key, title, writing_type, template_id, status, summary, brief, body, outline, materials, tags, revision, created_at, updated_at` |
| brief | `goal?, audience?, style?`（三选均可空字符串/缺省） |
| OutlineItem | `id, text, order` |
| Material | `id, type, title, content, url?, source?, note?, language?, included, order, created_at, updated_at` |

枚举取值（持久化用英文，展示文案走 i18n，见 §7）：

- `status`：`idea | draft | review | done`
- `Material.type`：`note | text | code | link`
- `id` 生成：服务端 `uuid4().hex`，创建后永不变更；复制文章时文章 ID 与全部素材/提纲项 ID 重新生成。

## 4. 读取接口（HTTP GET）

| 路径 | action | query | 返回 |
| --- | --- | --- | --- |
| `/api/writing/articles` | `article.list` | `q?`（匹配 title+summary）、`status?`（枚举或留空=全部）、`limit?`（默认 50，上限 200）、`offset?`（默认 0） | `{"articles": [ArticleSummary], "total": int, "limit": int, "offset": int}` |
| `/api/writing/articles/get` | `article.get` | `id`（必填，非空字符串） | `{"article": Article}` |

`ArticleSummary`（A-01 列表 + §10「列表不返回完整正文」）：

```
{"id","title","summary","status","writing_type","tags","revision","created_at","updated_at"}
```

- 排序固定 `updated_at DESC`，阶段 A 不提供其他排序。
- `total` 为同筛选条件下的总数，用于前端分页。
- 未知 `status` 值 → 400 `unsupported article status`；`limit` 非整数 → 400 `limit must be an integer`。
- 动作名一律用点号分隔，并且**两套名字空间必须同时登记**：① WS mutation 动作名是 `webui_request.action`，传输层强制 `[a-z][a-z0-9_.]{0,127}`（`nanobot/webui/inbound_commands.py:1180-1183`），连字符会被 400 拒掉；② 领域内部动作名来自 `_SYSTEM_ROUTES[path]`（`settings_routes.py`），不受该正则约束，现有 memory 域是「WS `memory.create` → 领域 `memory-create`」的双口径。写作域**两套统一取点号**（`article.create` 同时是 WS 动作名与领域动作名），减少一次映射歧义；因此 §4 的读动作与 §5 的写动作都写点号。

## 5. 写入接口（仅 WebSocket mutation）

`_WEBUI_MUTATION_PATHS` 新增四条；四条路径全部登记进 `_WRITING_MUTATION_PATHS` 并并入 `_SETTINGS_MUTATION_PATHS`（保证 HTTP 直连被 405 拒掉）。

| mutation action | 路径 | 必需输入 | 成功结果 |
| --- | --- | --- | --- |
| `article.create` | `/api/writing/articles/create` | `client_request_id, title`，可选 `template_id, writing_type, summary, brief, outline, materials` | `{"article": Article}`，`revision = 1` |
| `article.save` | `/api/writing/articles/save` | `client_request_id, article_id, base_revision, snapshot{title, writing_type, summary, brief, body, outline, materials, tags, status}` | `{"article_id","revision","updated_at","digest","no_change": bool}` |
| `article.duplicate` | `/api/writing/articles/duplicate` | `client_request_id, article_id, base_revision` | `{"article": Article}`（新 ID，`revision = 1`，不含会话/任务/建议/撤销记录） |
| `article.delete` | `/api/writing/articles/delete` | `client_request_id, article_id, base_revision` | `{"deleted_article_id","revision"}` |

`client_request_id` 校验：`^[A-Za-z0-9._:-]{1,128}$`（与传输层 `request_id` 同正则，`inbound_commands.py:1159-1162`）。

### 5.1 版本 CAS 与原子性（A-08）

`article.save` 在**单个 SQLite 事务**内按顺序完成：

1. `SELECT revision, content_digest FROM articles WHERE workspace_key=? AND id=?`；行不存在 → 404 `article not found`。
2. 服务端重算 `snapshot_digest`（§5.3），若与当前 `content_digest` 相同 → **不写不推进**，直接返回当前 `revision` + `no_change: true`（满足 A-06「相同内容重复保存不推进 revision」）。
3. 若 `revision != base_revision` → 409，message 见 §6。
4. `UPDATE ... SET revision = revision + 1, content_digest = ?, updated_at = ? WHERE workspace_key=? AND id=? AND revision=?`；`rowcount == 0` 视为冲突（并发窗口），走 §6。
5. 写 `article_requests` 幂等记录。

`article.duplicate` / `article.delete` 同样要求 `base_revision` 与服务端一致，不一致走 §6 的同一冲突 message。撤销与采纳（阶段 B）沿用同一 CAS 流程，**只推进版本，不回拨**。

### 5.2 业务幂等（超出传输层 5 分钟窗口）

每个写入动作先查 `article_requests(workspace_key, request_id)`：命中则直接返回存储的 `response`（不重复执行、不重复推进 revision）。未命中则在事务末尾插入。`op` 列存动作名，便于排查。

### 5.3 摘要与限制

`snapshot_digest = sha256(json.dumps(normalized_snapshot, ensure_ascii=False, sort_keys=True, separators=(",",":")).encode("utf-8")).hexdigest()`。`normalized_snapshot` 的键顺序与类型由 `nanobot/writing/models.py:normalized_snapshot()` 单点定义（含 `status/writing_type/template_id`，不含 `revision`/时间戳）。

大小上限（超限**拒绝写入并保留原稿**，绝不截断保存，spec §5.2）：

| 字段 | 上限 | 超限错误 |
| --- | --- | --- |
| `title` | 200 字符 | 400 `article title is too long` |
| `summary` | 500 字符 | 400 `article summary is too long` |
| `tags` | 20 个，单个 50 字符 | 400 `article tags limit exceeded` |
| `body` | 200 000 字符 | 413 `article body is too long` |
| `outline` | 200 项，单项 200 字符 | 400 `article outline limit exceeded` |
| `materials` | 100 条，`content` 单条 20 000 字符，`url` 2 000 字符 | 400 `article materials limit exceeded` |
| 整快照序列化 | 1 MiB | 413 `article snapshot is too large` |

- 空标题：`title.strip()` 为空 → 400 `article title is required`。
- `Material.type=link` 的 `url` 必须 `http://` 或 `https://` 开头 → 否则 400 `article material url must be http(s)`。
- 序列化后超过 transport `max_frame_bytes` 由客户端 `frameFitsTransport` 先行 413（`nanobot-client.ts:932-936`），服务端仍独立校验，双侧都不信任对方。

## 6. 错误契约

`SettingsRouteResult.failure(status, message)` → `http_error(status, message)` → mutation 侧 `{"ok": false, "error": {"status", "message"}}`，`message` 即下表原文。

| 场景 | status | message |
| --- | --- | --- |
| 缺少/非法 `article_id` | 400 | `article id is required` |
| 文章从未存在 | 404 | `article not found` |
| 文章已被删除（晚到的 save/duplicate/adopt） | 409 | `article deleted` |
| 版本冲突 | 409 | `article_conflict current_revision=<int> article_id=<id>` |
| 非法字段类型 | 400 | `unsupported article field: <name>` |
| 服务未配置 | 503 | `writing service is not configured` |

前端解析：`/^article_conflict current_revision=(\d+) article_id=(\S+)$/`。除该格式外，任何 409/404 一律按「不可自动合并」处理，保留本地草稿并停止自动保存。

## 7. 展示映射（前端单点）

`webui/src/components/creative/types.ts` 导出并由 UI 独占使用：

- `ARTICLE_STATUS_LABEL_KEY: Record<ArticleStatus, string>` → `creative.status.idea` 等 i18n 键。
- `MATERIAL_TYPE_LABEL_KEY: Record<MaterialType, string>` → `creative.material.type.note` 等。
- 模板：`webui/src/components/creative/templates.ts` 导出 `WRITING_TEMPLATES: {id, nameKey, descKey, outlineKeys}[]`，id 为 `tech-explainer | source-reading | practice-retro | learning-summary`（对应现有四个中文模板）；提纲文案走 i18n，模板初始化只产出 `{id, text, order}` 列表，**不含正文**。

新增 i18n 键必须同时写入 `webui/src/i18n/locales/{en,es,fr,id,ja,ko,pt-BR,vi,zh-CN,zh-TW}/common.json`（10 个 locale）。强制校验已存在：`webui/src/tests/i18n.test.tsx:584` 「keeps every locale aligned with the English resource shape」对每个 locale 断言 `missing/extra` 为空且插值键一致，漏一个 locale 即测试失败。

## 8. 不在本契约内（阶段 B 另立）

建议包（`Suggestion`）字段、协作 task/turn 关联、会话绑定表、采纳/撤销 mutation、写作档案与 Skill 契约，见 `.ai-runtime-artifacts/contracts/2026-10-12-contract-writing-collaboration.md`（B 契约）与 `2026-10-12-zhixu-writing-phase-b-plan.md`；阶段 A 的 `article.save` 快照**不含** `suggestions`，建议也不进导出正文。

## Next

- 契约确认 → 说「开始实现」（须先在本会话单独确认 plan）
- 需要调整上限、枚举或路径 → 直接指出条目，改本文件后再同步 plan
