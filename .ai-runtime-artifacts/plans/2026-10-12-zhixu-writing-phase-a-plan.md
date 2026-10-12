---
artifact: implementation-plan
route: superpowers:writing-plans
batch: zhixu-writing-phase-a
skills:
  - writing-plans
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-10-12-zhixu-writing-phase-a-dispatch.md
spec: .ai-runtime-artifacts/specs/2026-10-10-zhixu-writing-capability-spec.md
contract: .ai-runtime-artifacts/contracts/2026-10-12-contract-writing-article-store.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - nanobot/webui/memory_routes.py
  - nanobot/memory/database.py
  - webui/src/components/creative/
created_at: 2026-10-12
status: draft
approved: false
tier: 2
---

# 知序写作能力 · 阶段 A（文章存储与编辑器）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把创作空间从「组件内演示状态」换成真实数据链路：文章、提纲、素材按同一快照原子落盘到服务端 SQLite，支持重开恢复、自动保存、多标签页版本冲突处理与 Markdown 导出；不触碰阶段 B 的 AI 协作。

**Architecture:** 三层，全部沿用仓库既有模式，不引入新依赖。

- **存储层** `nanobot/writing/{database,models,repository}.py`：stdlib SQLite + `threading.Lock`，一行 = 一个文章快照；版本 CAS、内容摘要短路、`client_request_id` 幂等都在同一事务内完成。
- **传输层** `nanobot/webui/writing_{services,api,routes}.py` + 三处接线（`settings_routes.py` / `ws_http.py` / `gateway_services.py`）：与 memory/identity/agents 领域同构，读走 HTTP GET，写只走已认证 WS mutation。
- **前端层** `webui/src/lib/writing-api.ts` + `writing-draft-cache.ts` + `components/creative/*`：保存状态机持有 `base_revision` 与 `local_edit_seq` 两个独立计数；冲突时停止自动保存并保留本地草稿。

**Tech Stack:** Python 3.11 asyncio + stdlib `sqlite3` / `hashlib` / `uuid`；webui React 18 + TypeScript + Tailwind + Vitest + Testing Library；Markdown 预览复用 `webui/src/components/MarkdownText.tsx`；i18n 复用 `webui/src/i18n/locales/*/common.json`。**零新增依赖。**

**Spec:** `.ai-runtime-artifacts/specs/2026-10-10-zhixu-writing-capability-spec.md` §4（A-01~A-09）、§5、§9、§10、§11.1
**Contract:** `.ai-runtime-artifacts/contracts/2026-10-12-contract-writing-article-store.md` — 路径、动作名、字段、错误 message、上限全部以契约为准；Task 与契约冲突时**先停下上报**。

## Hard constraints

- 契约 §1 的六条「现有通道依据」不得被绕过：不新增 REST 写接口、不在浏览器直连模型、不把 `revision` 放进 mutation payload 之外的缓存当作服务端真相。
- 不动 `nanobot/memory/*`、`nanobot/session/manager.py`、`nanobot/webui/transcript.py`、`webui/src/lib/nanobot-client.ts`（阶段 A 不需要改客户端）。
- 不动阶段 B 契约（`Suggestion` 持久化结构保持 v2 原样，避免半成品字段）。
- 本地草稿只是恢复用，**禁止**当作「保存成功」依据（spec A-06 末条）。
- 演示数据只能经 `PreviewApp` 的 preview 入口进入正式工作区（spec A-01/A-T11）。
- 每个 Task 一次 commit，只 commit 不 push；commit message 用 Angular 风格（`feat(writing): …` / `test(writing): …`）。
- 用户工作区未提交改动不得被覆盖；本批次全部为新增代码。

## Global Constraints

- 全程中文注释与文档，代码标识符英文；产物只落 `.ai-runtime-artifacts/`。
- Python ≤100 列，ruff E/F/I/N/W；类型注解完整，收尾过 CI 等价的 basedpyright。
- 单测跑完即可 commit，**每个 Task 都要先看测试失败再实现**（红-绿）。
- 前端逻辑（状态机、摘要、diff 定位、冲突解析）必须有可单测的纯函数出口，不埋在 JSX 里。
- 工作树：见 dispatch；仅当走 WU 编排且委派 worker 时才建。

## 验证命令（所有 Task 通用）

```bash
python -m pytest tests/writing -v                 # 存储层
python -m pytest tests/webui/test_writing_routes.py -v
ruff check nanobot/writing nanobot/webui
uv run --no-sync python -m scripts.install_channel_dependencies --all-channels
uv run --no-sync basedpyright nanobot/writing nanobot/webui
cd webui && npm run test
cd webui && npm run lint
cd webui && npm run build
```

本机无 bun，统一用 npm。

## File Structure

| 动作 | Task | 文件 | 职责 |
| --- | --- | --- | --- |
| Create | A1.1 | `nanobot/writing/__init__.py` | 包声明 |
| Create | A1.1 | `nanobot/writing/database.py` | `WritingDatabase`：连接、锁、schema v1、`workspace_key` |
| Create/Modify | A1.1 | `tests/writing/test_database.py`、`tests/writing/conftest.py` | schema 与主键隔离测试 |
| Create | A1.2 | `nanobot/writing/models.py` | 字段上限、规范化、`snapshot_digest`、`ArticleRecord` |
| Create | A1.2 | `tests/writing/test_models.py` | 校验与摘要稳定性 |
| Create | A1.3 | `nanobot/writing/repository.py` | 快照 CAS 读写 + `client_request_id` 幂等 |
| Create | A1.3 | `tests/writing/test_repository.py` | A-06/A-08 核心行为 |
| Create | A1.4 | `nanobot/webui/writing_services.py` | 工作区级 services |
| Create | A1.4 | `nanobot/webui/writing_api.py` | HTTP 形状 + 错误 message（契约 §6） |
| Create | A1.4 | `tests/webui/test_writing_api.py` | 列表分页与摘要投影 |
| Create | A1.5 | `nanobot/webui/writing_routes.py` | action 名单 + operations + handler |
| Create | A1.5 | `tests/webui/test_writing_routes.py` | 契约 8 条错误 message 逐字断言 |
| Modify | A1.6 | `nanobot/webui/settings_routes.py`、`ws_http.py`、`gateway_services.py` | 路由、WS-only 门禁、operations 装配 |
| Modify | A1.7 | `nanobot/config/paths.py` | `get_writing_dir()` |
| Modify | A2.1 | `webui/src/components/creative/types.ts` | 英文枚举 + `revision`/`updatedAt` + 展示映射 |
| Create | A2.1 | `webui/src/components/creative/templates.ts` | 4 个模板（ascii id + i18n 键） |
| Create/Modify | A2.1 | 10 个 `locales/*/common.json`、`tests/article-model.test.ts`、5 个 creative 组件 | 键与字面量迁移 |
| Create | A2.2 | `webui/src/lib/writing-api.ts` | 读写封装 + 冲突解析 |
| Create | A2.2 | `webui/src/tests/writing-api.test.ts` | URL / payload / 错误解析 |
| Create | A2.3 | `webui/src/lib/writing-draft-cache.ts` | 本地恢复稿（按工作区命名空间隔离） |
| Create | A2.3 | `webui/src/tests/writing-draft-cache.test.ts` | 隔离、损坏、配额失效 |
| Modify | A2.4 | `webui/src/components/creative/article-model.ts` | 服务端 ID、双计数、字符统计、副本语义 |
| Modify | A3.1 | `webui/src/components/creative/CreativeWorkspace.tsx`、`App.tsx` | 真实 API 容器 |
| Create | A3.2 | `webui/src/components/creative/use-article-save-flow.ts` | 保存状态机（A-06） |
| Modify | A3.3 | `ArticleEditor.tsx`、`article-model.ts`、`tests/ime-composition.test.tsx` | IME/光标安全（A-03） |
| Modify | A3.4 | `MaterialPanel.tsx`、`OutlinePanel.tsx` | 字段补齐与稳定 ID |
| Modify | A3.5 | `ConflictDialog.tsx`、`MaterialPanel.tsx`、locales | 冲突三分支（A-08） |
| Modify/Create | A3.6 | `ArticleRecoveryBanner.tsx`、`tests/creative-recovery.test.tsx`、`App.tsx` | 重开恢复与离开守卫（A-07） |
| Create | A3.7 | `webui/src/lib/writing-export.ts` | Markdown 导出（A-09） |
| Modify | A3.8 | `ArticleList.tsx`、`locales/*/common.json` | 删除/复制/晚到防护（A-02） |

---

# Group A1 — 服务端存储与接口（Python）

> 独占文件集：`nanobot/writing/`、`tests/writing/`、`nanobot/webui/writing_*`、`tests/webui/test_writing_*`。与 A2/A3 前端文件零交集。

### Task A1.1：WritingDatabase 与 schema v1

**Files:**
- Create: `nanobot/writing/__init__.py`
- Create: `nanobot/writing/database.py`
- Create: `tests/writing/conftest.py`
- Create: `tests/writing/test_database.py`

**Interfaces:**
- Produces: `WritingDatabase(workspace: Path, *, db_path: Path | None = None)`、`.connect() -> ContextManager[sqlite3.Connection]`、`.ensure_schema()`、`.workspace_key: str`、模块函数 `compute_workspace_key(workspace: Path | str) -> str`

- [ ] **Step 1：写失败测试** `tests/writing/test_database.py`

```python
from __future__ import annotations

from pathlib import Path

from nanobot.writing.database import WritingDatabase, compute_workspace_key


def _tables(db: WritingDatabase) -> set[str]:
    with db.connect() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row["name"] for row in rows}


def test_ensure_schema_is_idempotent(tmp_path: Path) -> None:
    db = WritingDatabase(tmp_path)
    db.ensure_schema()
    db.ensure_schema()
    assert {"articles", "article_requests", "_schema_meta"} <= _tables(db)


def test_composite_primary_key_isolates_workspaces(tmp_path: Path) -> None:
    db = WritingDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        for key in ("a" * 16, "b" * 16):
            conn.execute(
                "INSERT INTO articles (id, workspace_key, title, content_digest,"
                " created_at, updated_at) VALUES (?, ?, '标题', 'x', 't1', 't1')",
                ("same-id", key),
            )
        rows = conn.execute(
            "SELECT workspace_key FROM articles WHERE id = 'same-id' ORDER BY workspace_key",
        ).fetchall()
    assert [row["workspace_key"] for row in rows] == ["a" * 16, "b" * 16]


def test_workspace_key_is_path_scoped_hex(tmp_path: Path) -> None:
    first = compute_workspace_key(tmp_path)
    assert len(first) == 16
    assert first == compute_workspace_key(tmp_path)
    assert first != compute_workspace_key(tmp_path / "other")
```

- [ ] **Step 2：跑测试确认失败**

Run: `python -m pytest tests/writing/test_database.py -v` → Expected: `ModuleNotFoundError: No module named 'nanobot.writing'`

- [ ] **Step 3：写 `nanobot/writing/__init__.py`**

```python
"""知序写作域：文章持久化与版本保护。

`database.py` 负责连接与 schema，`models.py` 负责字段语义与校验，
`repository.py` 负责单事务内的版本 CAS 与幂等。WebUI 传输层见
``nanobot/webui/writing_routes.py``；接口形状的唯一真相源是
``.ai-runtime-artifacts/contracts/2026-10-12-contract-writing-article-store.md``。
"""
```

- [ ] **Step 4：写 `nanobot/writing/database.py`**

连接/锁/`_schema_meta` 结构照搬 `nanobot/memory/database.py:193-224`，表用契约 §2.1 的 DDL（本 Task 全文照抄，不改列名）。

```python
"""写作域 SQLite 入口：连接管理、schema 初始化、工作区作用域键。"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = "1"

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
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
    )
    """,
    "CREATE INDEX IF NOT EXISTS articles_updated_idx"
    " ON articles (workspace_key, updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS article_requests (
      request_id    TEXT NOT NULL,
      workspace_key TEXT NOT NULL,
      article_id    TEXT NOT NULL,
      op            TEXT NOT NULL,
      response      TEXT NOT NULL,
      created_at    TEXT NOT NULL,
      PRIMARY KEY (workspace_key, request_id)
    )
    """,
    "CREATE TABLE IF NOT EXISTS _schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)


def compute_workspace_key(workspace: Path | str) -> str:
    """工作区作用域键：同一路径稳定同值，客户端无法伪造。"""
    resolved = Path(workspace).expanduser().resolve()
    return hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]


class WritingDatabase:
    """写作域 SQLite 入口，模式与 ``nanobot/memory/database.py`` 一致。"""

    def __init__(self, workspace: Path, *, db_path: Path | None = None) -> None:
        self.workspace = Path(workspace)
        self.db_path = (
            Path(db_path) if db_path is not None
            else self.workspace / "writing" / "state.db"
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace_key = compute_workspace_key(self.workspace)
        self._lock = threading.Lock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """取一个 SQLite 连接；同一进程内由锁串行化，事务由调用方决定。"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.row_factory = sqlite3.Row
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            for stmt in _SCHEMA_STATEMENTS:
                conn.execute(stmt)
            conn.execute(
                "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES (?, ?)",
                ("version", SCHEMA_VERSION),
            )

    def ensure_schema(self) -> None:
        """幂等补建表与索引；DDL 全为 IF NOT EXISTS，任何状态都能重跑。"""
        self.init_schema()
```

- [ ] **Step 5：跑测试确认通过**

Run: `python -m pytest tests/writing/test_database.py -v` → Expected: 3 passed

- [ ] **Step 6：提交**

```bash
git add nanobot/writing tests/writing
git commit -m "feat(writing): 新增写作域 SQLite 存储与 schema v1"
```

### Task A1.2：models.py — 字段语义、上限与内容摘要

**Files:**
- Create: `nanobot/writing/models.py`
- Create: `tests/writing/test_models.py`

**Interfaces:**
- Consumes: 契约 §3 字段、§5.3 上限与摘要算法
- Produces: `ARTICLE_STATUSES` / `MATERIAL_TYPES`、`ArticleLimitError(message, status)`、`normalize_article_fields(raw: Mapping) -> dict`、`normalize_snapshot(payload: object) -> dict`、`snapshot_digest(snapshot: Mapping) -> str`、`ArticleRecord`（`revision` / `updated_at_iso` / `to_article_dict()` / `to_summary_dict()`）

- [ ] **Step 1：写失败测试** `tests/writing/test_models.py`

```python
from __future__ import annotations

import pytest

from nanobot.writing.models import (
    ArticleLimitError,
    normalize_article_fields,
    normalize_snapshot,
    snapshot_digest,
)


def _snapshot(**overrides: object) -> dict:
    base: dict = {
        "title": "标题", "writing_type": "tech-explainer", "summary": "",
        "brief": {}, "body": "正文", "outline": [], "materials": [], "tags": [],
        "status": "draft",
    }
    base.update(overrides)
    return base


def test_digest_is_stable_across_key_order() -> None:
    first = normalize_snapshot({"status": "draft", "body": "正文", "title": "标题"})
    second = normalize_snapshot({"body": "正文", "title": "标题", "status": "draft"})
    assert snapshot_digest(first) == snapshot_digest(second)


def test_digest_ignores_revision_and_timestamps() -> None:
    assert snapshot_digest(normalize_snapshot(_snapshot())) == snapshot_digest(
        normalize_snapshot(_snapshot(revision=7, updated_at="2026-01-01T00:00:00+00:00"))
    )


def test_empty_title_rejected() -> None:
    with pytest.raises(ArticleLimitError, match="article title is required"):
        normalize_article_fields({"title": "  "})


def test_body_limit_rejects_without_truncating() -> None:
    with pytest.raises(ArticleLimitError) as excinfo:
        normalize_article_fields({"title": "t", "body": "x" * 200_001})
    assert excinfo.value.status == 413
    assert str(excinfo.value) == "article body is too long"


def test_material_url_must_be_http_https() -> None:
    with pytest.raises(ArticleLimitError, match="article material url must be http\\(s\\)"):
        normalize_article_fields({
            "title": "t",
            "materials": [{
                "id": "m1", "type": "link", "title": "l", "content": "",
                "url": "javascript:alert(1)", "included": True, "order": 0,
            }],
        })


def test_status_enum_is_enforced() -> None:
    with pytest.raises(ArticleLimitError, match="unsupported article status"):
        normalize_article_fields({"title": "t", "status": "已完成"})


def test_outline_item_ids_are_generated_not_index_based() -> None:
    fields = normalize_article_fields({
        "title": "t", "outline": [{"text": "第一节"}, {"text": "第二节"}],
    })
    assert fields["outline"][0]["id"] != fields["outline"][1]["id"]
    assert [item["order"] for item in fields["outline"]] == [0, 1]
```

- [ ] **Step 2：跑测试确认失败** → `ModuleNotFoundError: ... 'nanobot.writing.models'`

- [ ] **Step 3：实现 `nanobot/writing/models.py`**

```python
"""写作域字段语义、上限与内容摘要。

上限取值与错误文案的唯一真相源是契约 §5.3 / §6，本模块只做校验与规范化，
不生成 id / 时间戳（那是 repository 的职责）。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

ARTICLE_STATUSES = ("idea", "draft", "review", "done")
MATERIAL_TYPES = ("note", "text", "code", "link")

MAX_TITLE_CHARS = 200
MAX_SUMMARY_CHARS = 500
MAX_BODY_CHARS = 200_000
MAX_TAGS = 20
MAX_TAG_CHARS = 50
MAX_OUTLINE_ITEMS = 200
MAX_OUTLINE_TEXT_CHARS = 200
MAX_MATERIALS = 100
MAX_MATERIAL_CONTENT_CHARS = 20_000
MAX_URL_CHARS = 2_000
MAX_SNAPSHOT_BYTES = 1024 * 1024

_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_URL_PREFIXES = ("http://", "https://")


class ArticleLimitError(ValueError):
    """字段校验失败；message 与 status 直接按契约 §6 原样透传给 WebUI。"""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def new_id() -> str:
    return uuid.uuid4().hex


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: I001 见 Step 3 备注


def _text(value: Any, field: str, limit: int, *, required: bool = False) -> str:
    if value is None:
        if required:
            raise ArticleLimitError(f"unsupported article field: {field}")
        return ""
    if not isinstance(value, str):
        raise ArticleLimitError(f"unsupported article field: {field}")
    if len(value) > limit:
        raise ArticleLimitError(
            f"article {field} is too long", status=413 if field == "body" else 400,
        )
    return value


def _tags(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise ArticleLimitError("unsupported article field: tags")
    if len(value) > MAX_TAGS:
        raise ArticleLimitError("article tags limit exceeded")
    return [
        item.strip() for item in value
        if isinstance(item, str) and item.strip() and len(item.strip()) <= MAX_TAG_CHARS
    ]


def _brief(value: Any) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping):
        raise ArticleLimitError("unsupported article field: brief")
    out: dict[str, str] = {}
    for key in ("goal", "audience", "style"):
        raw = value.get(key)
        if raw is None or not isinstance(raw, str) or not raw.strip():
            continue
        out[key] = raw.strip()[:MAX_SUMMARY_CHARS]
    return out


def _outline(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise ArticleLimitError("unsupported article field: outline")
    if len(value) > MAX_OUTLINE_ITEMS:
        raise ArticleLimitError("article outline limit exceeded")
    items: list[dict[str, Any]] = []
    for order, raw in enumerate(value):
        if isinstance(raw, Mapping):
            text = raw.get("text", "")
            item_id = raw.get("id")
        else:
            text, item_id = raw, None
        if not isinstance(text, str):
            raise ArticleLimitError("unsupported article field: outline")
        text = text.strip()
        if not text:
            continue
        if len(text) > MAX_OUTLINE_TEXT_CHARS:
            raise ArticleLimitError("article outline limit exceeded")
        if item_id is not None and (
            not isinstance(item_id, str) or not _ID_RE.fullmatch(item_id)
        ):
            raise ArticleLimitError("unsupported article field: outline.id")
        items.append({"id": item_id or new_id(), "text": text, "order": order})
    return items


def _materials(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise ArticleLimitError("unsupported article field: materials")
    if len(value) > MAX_MATERIALS:
        raise ArticleLimitError("article materials limit exceeded")
    out: list[dict[str, Any]] = []
    for order, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ArticleLimitError("unsupported article field: materials")
        kind = raw.get("type", "note")
        if kind not in MATERIAL_TYPES:
            raise ArticleLimitError("unsupported article material type")
        title = _text(raw.get("title"), "materials.title", MAX_TITLE_CHARS)
        content = _text(raw.get("content"), "materials.content", MAX_MATERIAL_CONTENT_CHARS)
        url = _text(raw.get("url"), "materials.url", MAX_URL_CHARS)
        if kind == "link" and url and not url.lower().startswith(_URL_PREFIXES):
            raise ArticleLimitError("article material url must be http(s)")
        material_id = raw.get("id")
        if material_id is not None and (
            not isinstance(material_id, str) or not _ID_RE.fullmatch(material_id)
        ):
            raise ArticleLimitError("unsupported article field: materials.id")
        out.append({
            "id": material_id or new_id(),
            "type": kind,
            "title": title,
            "content": content,
            "url": url if kind == "link" else "",
            "source": _text(raw.get("source"), "materials.source", MAX_URL_CHARS),
            "note": _text(raw.get("note"), "materials.note", MAX_MATERIAL_CONTENT_CHARS),
            "language": _text(raw.get("language"), "materials.language", 50),
            "included": bool(raw.get("included", True)),
            "order": order,
            "created_at": raw.get("created_at") or utc_now_iso(),
            "updated_at": raw.get("updated_at") or utc_now_iso(),
        })
    return out


def normalize_article_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    """创建/保存共用：产出可入库字段（不含 id / revision / 时间戳）。"""
    title = _text(raw.get("title"), "title", MAX_TITLE_CHARS, required=True).strip()
    if not title:
        raise ArticleLimitError("article title is required")
    status = raw.get("status", "idea")
    if status not in ARTICLE_STATUSES:
        raise ArticleLimitError("unsupported article status")
    return {
        "title": title,
        "writing_type": _text(raw.get("writing_type"), "writing_type", 50).strip(),
        "template_id": _text(raw.get("template_id"), "template_id", 50).strip(),
        "status": status,
        "summary": _text(raw.get("summary"), "summary", MAX_SUMMARY_CHARS).strip(),
        "brief": _brief(raw.get("brief")),
        "body": _text(raw.get("body"), "body", MAX_BODY_CHARS),
        "outline": _outline(raw.get("outline")),
        "materials": _materials(raw.get("materials")),
        "tags": _tags(raw.get("tags")),
    }


_SNAPSHOT_KEYS = (
    "status", "writing_type", "template_id", "title", "summary",
    "brief", "tags", "outline", "materials", "body",
)


def normalize_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    """摘要口径：只留契约列出的持久化内容，按固定键序排列。"""
    return {key: payload[key] for key in _SNAPSHOT_KEYS if key in payload}


def snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    """契约 §5.3 定义的 content_digest。"""
    encoded = json.dumps(
        normalize_snapshot(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise ArticleLimitError("article snapshot is too large", status=413)
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ArticleRecord:
    """一行 articles 记录。"""

    id: str
    workspace_key: str
    revision: int
    created_at: str
    updated_at: str
    fields: dict[str, Any]

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "ArticleRecord":
        fields = {
            "title": row["title"],
            "writing_type": row["writing_type"],
            "template_id": row["template_id"],
            "status": row["status"],
            "summary": row["summary"],
            "body": row["body"],
        }
        for key in ("brief", "outline", "materials", "tags"):
            fields[key] = json.loads(row[key] or "null")
        return cls(
            id=row["id"],
            workspace_key=row["workspace_key"],
            revision=int(row["revision"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            fields=fields,
        )

    def to_article_dict(self) -> dict[str, Any]:
        """完整文章投影（契约 §4 article-get）。"""
        return {
            "id": self.id,
            "workspace_key": self.workspace_key,
            **self.fields,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_summary_dict(self) -> dict[str, Any]:
        """列表投影：不含 body / outline / materials / brief（契约 §4）。"""
        return {
            "id": self.id,
            "title": self.fields["title"],
            "summary": self.fields["summary"],
            "status": self.fields["status"],
            "writing_type": self.fields["writing_type"],
            "tags": self.fields["tags"],
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
```

备注：Step 3 顶部的 `utc_now_iso` 需要 `from datetime import datetime, timezone`；写文件时直接用正确 import，不要保留注释里的 `noqa`。

- [ ] **Step 4：跑测试确认通过** → 6 passed
- [ ] **Step 5：`ruff check nanobot/writing` 退出码 0**
- [ ] **Step 6：提交** `git commit -m "feat(writing): 文章字段规范化、上限与内容摘要"`

### Task A1.3：repository.py — 单事务 CAS 与请求幂等

**Files:**
- Create: `nanobot/writing/repository.py`
- Create: `tests/writing/test_repository.py`

**Interfaces:**
- Consumes: `WritingDatabase`（A1.1）、`normalize_article_fields` / `snapshot_digest` / `ArticleRecord` / `new_id` / `utc_now_iso`（A1.2）
- Produces: `ArticleConflict(article_id, current_revision)`、`ArticleRequestReplay(response: dict)`、`ArticleRepository(db)`：`list_articles` / `get_article` / `create_article` / `save_snapshot` / `duplicate_article` / `delete_article`

- [ ] **Step 1：写失败测试** `tests/writing/test_repository.py`

覆盖 spec A-06 / A-08 全部行为，逐条对应验收编号：

```python
from __future__ import annotations

import pytest

from nanobot.writing.database import WritingDatabase
from nanobot.writing.repository import (
    ArticleConflict,
    ArticleRepository,
    ArticleRequestReplay,
)
from nanobot.writing.models import ArticleLimitError


@pytest.fixture
def repo(tmp_path):
    db = WritingDatabase(tmp_path)
    db.ensure_schema()
    return ArticleRepository(db)


def _snapshot(**overrides):
    base = {
        "title": "标题", "writing_type": "", "template_id": "", "status": "draft",
        "summary": "", "brief": {}, "body": "正文", "outline": [], "materials": [],
        "tags": [],
    }
    base.update(overrides)
    return base


def test_create_returns_revision_one_and_stable_id(repo) -> None:      # A-T01
    created = repo.create_article("r-1", _snapshot())
    assert created["article"]["revision"] == 1
    assert created["article"]["id"]


def test_create_is_idempotent_on_client_request_id(repo) -> None:      # A-T05
    first = repo.create_article("r-1", _snapshot())
    replay = repo.create_article("r-1", _snapshot())
    assert replay == first


def test_reused_request_id_with_other_payload_is_rejected(repo) -> None:
    repo.create_article("r-1", _snapshot())
    with pytest.raises(ValueError, match="request_id was already used"):
        repo.create_article("r-1", _snapshot(title="另一个标题"))


def test_same_content_save_does_not_advance_revision(repo) -> None:    # A-T05
    created = repo.create_article("r-1", _snapshot())
    again = repo.save_snapshot(
        created["article"]["id"], 1, _snapshot(), client_request_id="r-2",
    )
    assert again["no_change"] is True
    assert again["revision"] == 1


def test_save_advances_revision_and_rejects_stale_base(repo) -> None:  # A-T06
    created = repo.create_article("r-1", _snapshot())
    saved = repo.save_snapshot(
        created["article"]["id"], 1, _snapshot(body="改过"), client_request_id="r-2",
    )
    assert saved["revision"] == 2
    with pytest.raises(ArticleConflict) as conflict:
        repo.save_snapshot(created["article"]["id"], 1, _snapshot(body="B 稿"), client_request_id="r-3")
    assert conflict.value.current_revision == 2


def test_save_replay_returns_stored_response_only(repo) -> None:       # A-T05
    created = repo.create_article("r-1", _snapshot())
    first = repo.save_snapshot(
        created["article"]["id"], 1, _snapshot(body="改过"), client_request_id="r-2",
    )
    replay = repo.save_snapshot(
        created["article"]["id"], 1, _snapshot(body="改过"), client_request_id="r-2",
    )
    assert replay["revision"] == first["revision"] == 2


def test_duplicate_reissues_all_ids(repo) -> None:                     # A-T10
    created = repo.create_article("r-1", _snapshot(
        outline=[{"id": "o-1", "text": "第一章", "order": 0}],
        materials=[{"id": "m-1", "type": "note", "title": "观点",
                    "content": "内容", "included": True, "order": 0}],
    ))
    copy = repo.duplicate_article(created["article"]["id"], 1, client_request_id="r-2")
    article = copy["article"]
    assert article["id"] != created["article"]["id"]
    assert article["materials"][0]["id"] != "m-1"
    assert article["outline"][0]["id"] != "o-1"
    assert article["revision"] == 1


def test_operations_on_missing_article_raise_lookup(repo) -> None:
    from nanobot.writing.repository import ArticleNotFound

    with pytest.raises(ArticleNotFound):
        repo.save_snapshot("nope", 1, _snapshot(), client_request_id="r-9")


def test_expired_base_revision_is_rejected(repo) -> None:              # A-T10
    created = repo.create_article("r-1", _snapshot())
    repo.delete_article(created["article"]["id"], 1, client_request_id="r-2")
    with pytest.raises(ArticleLimitError) as late:
        repo.save_snapshot(
            created["article"]["id"], 1, _snapshot(body="晚到"), client_request_id="r-3",
        )
    assert late.value.status == 409
    assert str(late.value) == "article deleted"


def test_list_sorts_by_updated_at_desc_and_excludes_body(repo) -> None:  # A-T01/A-T11
    repo.create_article("r-1", _snapshot(title="第一"))
    repo.create_article("r-2", _snapshot(title="第二"))
    page = repo.list_articles(limit=10, offset=0)
    assert page["total"] == 2
    assert all("body" not in item for item in page["articles"])
    listed = repo.list_articles(query="第二")
    assert [item["title"] for item in listed["articles"]] == ["第二"]


def test_status_filter_rejects_unknown_value(repo) -> None:
    with pytest.raises(ArticleLimitError, match="unsupported article status"):
        repo.list_articles(status="已完成")


def test_workspace_isolation_across_two_databases(tmp_path) -> None:   # A-T11
    shared = tmp_path / "state.db"
    a = WritingDatabase(tmp_path / "a", db_path=shared)
    a.ensure_schema()
    b = WritingDatabase(tmp_path / "b", db_path=shared)
    repo_a, repo_b = ArticleRepository(a), ArticleRepository(b)
    repo_a.create_article("r-1", _snapshot())
    assert repo_b.list_articles()["total"] == 0
```

- [ ] **Step 2：跑测试确认失败** → `ModuleNotFoundError: ... repository`

- [ ] **Step 3：实现 `nanobot/writing/repository.py`**

```python
"""文章快照仓储：单事务内的版本 CAS、内容摘要短路与请求幂等。

契约 §5.1 / §5.2 是行为唯一真相源；错误 message 只在 webui 层拼装，
本模块抛类型化异常（ArticleNotFound / ArticleConflict / 复用
ArticleLimitError），便于 repository 单测直接断言语义。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import (
    ArticleLimitError,
    ArticleRecord,
    new_id,
    normalize_article_fields,
    snapshot_digest,
    utc_now_iso,
)

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200

_READ_COLUMNS = (
    "id, workspace_key, title, writing_type, template_id, status, summary, brief,"
    " body, outline, materials, tags, revision, created_at, updated_at"
)
_SUMMARY_COLUMNS = (
    "id, workspace_key, title, summary, status, writing_type, tags, revision,"
    " created_at, updated_at"
)


class ArticleNotFound(LookupError):
    """文章在本工作区从未存在。"""


class ArticleConflict(ValueError):
    """base_revision 与服务端当前版本不一致（契约 §6）。"""

    def __init__(self, article_id: str, current_revision: int) -> None:
        super().__init__(
            f"article_conflict current_revision={current_revision} article_id={article_id}"
        )
        self.article_id = article_id
        self.current_revision = current_revision


class ArticleRequestReplay(ValueError):
    """同一 client_request_id 被用于不同内容：拒绝，不重复执行。"""


class ArticleRepository:
    """一个工作区一个实例；所有写操作都在单事务内完成。"""

    def __init__(self, db: WritingDatabase) -> None:
        self._db = db
        self._workspace_key = db.workspace_key

    # ---- 读取 --------------------------------------------------------------

    def get_article(self, article_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {_READ_COLUMNS} FROM articles"
                " WHERE workspace_key = ? AND id = ?",
                (self._workspace_key, article_id),
            ).fetchone()
        if row is None:
            raise ArticleNotFound(article_id)
        return {"article": ArticleRecord.from_row(row).to_article_dict()}

    def list_articles(
        self,
        *,
        query: str | None = None,
        status: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        from nanobot.writing.models import ARTICLE_STATUSES

        if status not in (None, "", "all"):
            if status not in ARTICLE_STATUSES:
                raise ArticleLimitError("unsupported article status")
        resolved_limit = max(1, min(int(limit or DEFAULT_LIST_LIMIT), MAX_LIST_LIMIT))
        resolved_offset = max(0, int(offset or 0))
        clauses = ["workspace_key = ?"]
        params: list[Any] = [self._workspace_key]
        if status not in (None, "", "all"):
            clauses.append("status = ?")
            params.append(status)
        if query and query.strip():
            clauses.append("(title LIKE ? OR summary LIKE ?)")
            needle = f"%{query.strip()}%"
            params.extend([needle, needle])
        where = " AND ".join(clauses)
        with self._db.connect() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM articles WHERE {where}", params,
                ).fetchone()["n"]
            )
            rows = conn.execute(
                f"SELECT {_SUMMARY_COLUMNS} FROM articles WHERE {where}"
                " ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                [*params, resolved_limit, resolved_offset],
            ).fetchall()
        return {
            "articles": [ArticleRecord.from_row(row).to_summary_dict() for row in rows],
            "total": total,
            "limit": resolved_limit,
            "offset": resolved_offset,
        }

    # ---- 写入 --------------------------------------------------------------

    def create_article(
        self,
        client_request_id: str,
        payload: Mapping[str, Any],
        *,
        template_outlines: Mapping[str, Sequence[str]] | None = None,
    ) -> dict[str, Any]:
        replay = self._replay_response(client_request_id, "article.create")
        if replay is not None:
            return replay
        fields = normalize_article_fields(payload)
        if not fields["outline"] and fields["template_id"] and template_outlines:
            seeds = template_outlines.get(fields["template_id"]) or []
            fields["outline"] = [
                {"id": new_id(), "text": text, "order": order}
                for order, text in enumerate(seeds)
            ]
        article_id = new_id()
        now = utc_now_iso()
        digest = snapshot_digest(fields)
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO articles (id, workspace_key, title, writing_type, template_id,"
                " status, summary, brief, body, outline, materials, tags, revision,"
                " content_digest, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (
                    article_id, self._workspace_key, fields["title"],
                    fields["writing_type"], fields["template_id"], fields["status"],
                    fields["summary"], json.dumps(fields["brief"], ensure_ascii=False),
                    fields["body"], json.dumps(fields["outline"], ensure_ascii=False),
                    json.dumps(fields["materials"], ensure_ascii=False),
                    json.dumps(fields["tags"], ensure_ascii=False),
                    digest, now, now,
                ),
            )
            article = self._read_one(conn, article_id)
            response = {"article": article.to_article_dict()}
            self._record_request(conn, client_request_id, article_id, "article.create", response)
        return response

    def save_snapshot(
        self,
        article_id: str,
        base_revision: int,
        snapshot: Mapping[str, Any],
        *,
        client_request_id: str,
    ) -> dict[str, Any]:
        replay = self._replay_response(client_request_id, f"article.save:{article_id}")
        if replay is not None:
            return replay
        fields = normalize_article_fields(snapshot)
        with self._db.connect() as conn:
            current = self._read_revision_row(conn, article_id)
            if fields_body_digest := snapshot_digest(fields) == current["content_digest"]:
                return self._finish(
                    conn, client_request_id, article_id, "article.save",
                    {
                        "article_id": article_id,
                        "revision": int(current["revision"]),
                        "updated_at": current["updated_at"],
                        "digest": current["content_digest"],
                        "no_change": True,
                    },
                )
            if int(current["revision"]) != int(base_revision):
                raise ArticleConflict(article_id, int(current["revision"]))
            now = utc_now_iso()
            digest = snapshot_digest(fields)
            updated = conn.execute(
                "UPDATE articles SET title = ?, writing_type = ?, template_id = ?,"
                " status = ?, summary = ?, brief = ?, body = ?, outline = ?,"
                " materials = ?, tags = ?, revision = revision + 1, content_digest = ?,"
                " updated_at = ?"
                " WHERE workspace_key = ? AND id = ? AND revision = ?",
                (
                    fields["title"], fields["writing_type"], fields["template_id"],
                    fields["status"], fields["summary"],
                    json.dumps(fields["brief"], ensure_ascii=False), fields["body"],
                    json.dumps(fields["outline"], ensure_ascii=False),
                    json.dumps(fields["materials"], ensure_ascii=False),
                    json.dumps(fields["tags"], ensure_ascii=False),
                    digest, now, self._workspace_key, article_id, int(base_revision),
                ),
            )
            if updated.rowcount != 1:
                latest = self._read_revision_row(conn, article_id)
                raise ArticleConflict(article_id, int(latest["revision"]))
            return self._finish(
                conn, client_request_id, article_id, "article.save",
                {
                    "article_id": article_id, "revision": int(base_revision) + 1,
                    "updated_at": now, "digest": digest, "no_change": False,
                },
            )

    def duplicate_article(
        self, article_id: str, base_revision: int, *, client_request_id: str,
    ) -> dict[str, Any]:
        replay = self._replay_response(client_request_id, f"article.duplicate:{article_id}")
        if replay is not None:
            return replay
        with self._db.connect() as conn:
            record = ArticleRecord.from_row(self._read_row(conn, article_id))
            if record.revision != int(base_revision):
                raise ArticleConflict(article_id, record.revision)
            now = utc_now_iso()
            copy_id = new_id()
            fields = dict(record.fields)
            fields["title"] = f"{fields['title']}（副本）"[:200]
            fields["outline"] = [
                {"id": new_id(), "text": item["text"], "order": item["order"]}
                for item in fields["outline"]
            ]
            fields["materials"] = [
                {**material, "id": new_id(), "created_at": now, "updated_at": now}
                for material in fields["materials"]
            ]
            digest = snapshot_digest(fields)
            conn.execute(
                "INSERT INTO articles (id, workspace_key, title, writing_type, template_id,"
                " status, summary, brief, body, outline, materials, tags, revision,"
                " content_digest, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (
                    copy_id, self._workspace_key, fields["title"],
                    fields["writing_type"], fields["template_id"], fields["status"],
                    fields["summary"], json.dumps(fields["brief"], ensure_ascii=False),
                    fields["body"], json.dumps(fields["outline"], ensure_ascii=False),
                    json.dumps(fields["materials"], ensure_ascii=False),
                    json.dumps(fields["tags"], ensure_ascii=False),
                    digest, now, now,
                ),
            )
            article = self._read_one(conn, copy_id)
            return self._finish(
                conn, client_request_id, copy_id, "article.duplicate",
                {"article": article.to_article_dict()},
            )

    def delete_article(
        self, article_id: str, base_revision: int, *, client_request_id: str,
    ) -> dict[str, Any]:
        replay = self._replay_response(client_request_id, f"article.delete:{article_id}")
        if replay is not None:
            return replay
        with self._db.connect() as conn:
            record = ArticleRecord.from_row(self._read_row(conn, article_id))
            if record.revision != int(base_revision):
                raise ArticleConflict(article_id, record.revision)
            conn.execute(
                "DELETE FROM articles WHERE workspace_key = ? AND id = ?",
                (self._workspace_key, article_id),
            )
            conn.execute(
                "INSERT INTO article_requests"
                " (request_id, workspace_key, article_id, op, response, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    client_request_id, self._workspace_key, article_id, "article.delete",
                    json.dumps(
                        {"deleted_article_id": article_id, "revision": record.revision},
                        ensure_ascii=False,
                    ),
                    now_iso,  # 见 Step 3 备注：用 utc_now_iso() 的局部变量
                ),
            )
            return {"deleted_article_id": article_id, "revision": record.revision}

    # ---- 内部 --------------------------------------------------------------

    def _replay_response(self, client_request_id: str, op: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT op, response FROM article_requests"
                " WHERE workspace_key = ? AND request_id = ?",
                (self._workspace_key, client_request_id),
            ).fetchone()
            if row is None:
                return None
            if row["op"] != op:
                raise ArticleRequestReplay(
                    "request_id was already used for a different article mutation"
                )
            stored = json.loads(row["response"])
            if op == "article.create" or op.startswith("article.duplicate"):
                current = conn.execute(
                    "SELECT revision FROM articles WHERE workspace_key = ? AND id = ?",
                    (self._workspace_key, stored.get("article", {}).get("id", "")),
                ).fetchone()
                if current is None:
                    raise ArticleLimitError("article deleted", status=409)
            return stored

    def _read_row(self, conn, article_id: str):
        row = conn.execute(
            f"SELECT {_READ_COLUMNS} FROM articles WHERE workspace_key = ? AND id = ?",
            (self._workspace_key, article_id),
        ).fetchone()
        if row is None:
            raise ArticleNotFound(article_id)
        return row

    def _read_one(self, conn, article_id: str) -> ArticleRecord:
        return ArticleRecord.from_row(self._read_row(conn, article_id))

    def _read_revision_row(self, conn, article_id: str) -> dict[str, Any]:
        row = conn.execute(
            "SELECT revision, content_digest, updated_at FROM articles"
            " WHERE workspace_key = ? AND id = ?",
            (self._workspace_key, article_id),
        ).fetchone()
        if row is None:
            if conn.execute(
                "SELECT 1 FROM article_requests WHERE workspace_key = ? AND article_id = ?"
                " AND op = 'article.delete' LIMIT 1",
                (self._workspace_key, article_id),
            ).fetchone():
                raise ArticleLimitError("article deleted", status=409)
            raise ArticleNotFound(article_id)
        return dict(row)

    def _finish(
        self, conn, client_request_id: str, article_id: str, op: str, response: dict[str, Any],
    ) -> dict[str, Any]:
        self._record_request(conn, client_request_id, article_id, op, response)
        return response

    def _record_request(
        self, conn, client_request_id: str, article_id: str, op: str, response: dict[str, Any],
    ) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO article_requests"
            " (request_id, workspace_key, article_id, op, response, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                client_request_id, self._workspace_key, article_id, op,
                json.dumps(response, ensure_ascii=False), utc_now_iso(),
            ),
        )
```

Step 3 的两处待修正点（worker 落地时必须改掉，不能留 TODO）：

1. `save_snapshot` 里的 `if fields_body_digest := snapshot_digest(fields) == ...` 写成显式两行：`digest_now = snapshot_digest(fields)`，然后 `if digest_now == current["content_digest"]:`；后续复用 `digest_now`，不要重复算第二次。
2. `delete_article` 的 `now_iso` 换成 `utc_now_iso()` 调用；`Sequence` 需要 `from collections.abc import Mapping, Sequence`。

- [ ] **Step 4：跑测试确认通过** → 13 passed
- [ ] **Step 5：`ruff check nanobot/writing` 与 `uv run --no-sync basedpyright nanobot/writing` 均无错误**
- [ ] **Step 6：提交** `git commit -m "feat(writing): 文章快照仓储支持版本 CAS、摘要短路与请求幂等"`

### Task A1.4：writing_services.py 与 writing_api.py

**Files:**
- Create: `nanobot/webui/writing_services.py`
- Create: `nanobot/webui/writing_api.py`
- Create: `tests/webui/test_writing_api.py`

**Interfaces:**
- Consumes: `ArticleRepository`（A1.3）、`ArticleNotFound` / `ArticleConflict` / `ArticleRequestReplay` / `ArticleLimitError`
- Produces: `WritingServices(workspace_id: str, repository: ArticleRepository)` + `WritingServices.for_workspace(workspace_id, workspace_path)`；`writing_api` 的 6 个 payload 函数（契约 §4/§5 的输入输出形状 + §6 的错误 message）

- [ ] **Step 1：写失败测试** `tests/webui/test_writing_api.py`

```python
from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.webui import writing_api
from nanobot.webui.memory_routes import WebUISettingsError  # 复用同一错误类型？见 Step 2 备注
from nanobot.webui.writing_services import WritingServices


@pytest.fixture
def services(tmp_path: Path) -> WritingServices:
    return WritingServices.for_workspace("default", tmp_path)


def test_create_payload_shape(services: WritingServices) -> None:
    payload = writing_api.create_article(
        services, client_request_id="r-1", title="标题", template_id="tech-explainer",
    )
    assert set(payload["article"]) >= {"id", "revision", "updated_at", "outline"}


def test_list_payload_has_paging(services: WritingServices) -> None:
    writing_api.create_article(services, client_request_id="r-1", title="甲")
    payload = writing_api.list_articles(services, query=None, status=None, limit=None, offset=None)
    assert payload["total"] == 1 and payload["limit"] == 50 and payload["offset"] == 0
    assert "body" not in payload["articles"][0]


def test_conflict_becomes_contract_message(services: WritingServices) -> None:
    article = writing_api.create_article(
        services, client_request_id="r-1", title="甲",
    )["article"]
    writing_api.save_article(
        services, client_request_id="r-2", article_id=article["id"], base_revision=1,
        snapshot={"title": "甲", "body": "一"},
    )
    with pytest.raises(WebUISettingsError) as excinfo:
        writing_api.save_article(
            services, client_request_id="r-3", article_id=article["id"], base_revision=1,
            snapshot={"title": "甲", "body": "二"},
        )
    assert excinfo.value.status == 409
    assert excinfo.value.message == (
        f"article_conflict current_revision=2 article_id={article['id']}"
    )


def test_get_missing_article_is_404(services: WritingServices) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        writing_api.fetch_article(services, "nope")
    assert (excinfo.value.status, excinfo.value.message) == (404, "article not found")
```

Step 2 备注：错误类型统一用 `nanobot.webui.settings_contracts.WebUISettingsError`（memory/identity 域同源），测试 import 路径应为 `from nanobot.webui.settings_contracts import WebUISettingsError`。

- [ ] **Step 2：实现 `writing_services.py`**（对照 `nanobot/webui/memory_services.py` 的 docstring 风格：transport-neutral，每 gateway 一个实例）

```python
"""工作区作用域的写作服务：持有该工作区的文章仓储。

与 ``memory_services.py`` 同构：不认识 WebSocket/HTTP，也不认识具体持久化
细节，只把 ``WritingDatabase`` 包成 gateway 可注入的实例。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nanobot.writing.database import WritingDatabase
from nanobot.writing.repository import ArticleRepository


@dataclass(frozen=True)
class WritingServices:
    workspace_id: str
    repository: ArticleRepository

    @classmethod
    def for_workspace(cls, workspace_id: str, workspace_path: Path | str) -> "WritingServices":
        database = WritingDatabase(Path(workspace_path))
        database.ensure_schema()
        return cls(workspace_id=workspace_id, repository=ArticleRepository(database))
```

- [ ] **Step 3：实现 `nanobot/webui/writing_api.py`**

每个函数签名 `(services, **契约字段) -> dict`，把 repository 异常翻译成 `WebUISettingsError`（status + 契约 §6 message 原文）；`client_request_id` 先校验 `^[A-Za-z0-9._:-]{1,128}$`，非法则 400 `client_request_id is required`。

```python
def create_article(
    services: WritingServices,
    *,
    client_request_id: str,
    title: str,
    template_id: str = "",
    writing_type: str = "",
    summary: str = "",
    brief: dict[str, str] | None = None,
    body: str = "",
    outline: list[dict[str, Any]] | None = None,
    materials: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
    status: str = "idea",
) -> dict[str, Any]: ...
```

其余入口：`list_articles(services, *, query, status, limit, offset)`、`fetch_article(services, article_id)`、`save_article(services, *, client_request_id, article_id, base_revision, snapshot)`、`duplicate_article(services, *, client_request_id, article_id, base_revision)`、`delete_article(services, *, client_request_id, article_id, base_revision)`。

异常翻译表（唯一真相源，写在模块 docstring 里，handler 不再二次包装）：

| repository 异常 | status | message |
| --- | --- | --- |
| `ArticleNotFound` | 404 | `article not found` |
| `ArticleConflict` | 409 | `article_conflict current_revision=<n> article_id=<id>` |
| `ArticleLimitError("article deleted")` | 409 | `article deleted` |
| 其他 `ArticleLimitError` | `exc.status` | `exc.message` |
| `ArticleRequestReplay` | 409 | `request_id was already used for a different WebUI mutation` |

- [ ] **Step 4：跑测试确认通过** → 4 passed
- [ ] **Step 5：提交** `git commit -m "feat(writing): 写作域 services 与 payload 层"

### Task A1.5：writing_routes.py — action 名单与 handler

**Files:**
- Create: `nanobot/webui/writing_routes.py`
- Create: `tests/webui/test_writing_routes.py`

**Interfaces:**
- Consumes: `writing_api`（A1.4）、`SettingsRequest` / `SettingsRouteResult` / `WebUISettingsError` / `query_first`
- Produces: `WRITING_ACTION_NAMES`（frozenset：`article.list, article.get, article.create, article.save, article.duplicate, article.delete`）、`WritingSettingsOperations`（6 个 Callable）、`WritingSettingsHandler.handle(action, request) -> SettingsRouteResult`
- 动作名口径：点号分隔，不得用连字符。WS 传输层对 `webui_request.action` 强制 `re.fullmatch(r"[a-z][a-z0-9_.]{0,127}", action)`（`nanobot/webui/inbound_commands.py:1180-1183`），`article-create` 这类名字会在进入领域 handler 之前就被 400 拒掉。领域内部动作名（`_SYSTEM_ROUTES` 的值）本身不受该正则约束——现有 memory 域就是「WS `memory.create` → 领域 `memory-create`」双口径——写作域按契约把两套统一成点号，`article.create` 既是 WS 动作名也是领域动作名。

- [ ] **Step 1：写失败测试** `tests/webui/test_writing_routes.py`（对照 `tests/webui/test_memory_routes.py:1-60` 的 fixture 风格）

必须逐条断言契约 §6 的 8 条 message，以及 handler 从 query/payload 取参的形状：

```python
def test_handler_dispatches_all_six_actions(services) -> None:
    handler = _handler(services)
    created = handler.handle("article.create", _request({
        "client_request_id": "r-1", "title": "标题",
    }))
    assert created.status == 200 and created.payload["article"]["revision"] == 1

    article_id = created.payload["article"]["id"]
    listed = handler.handle("article.list", _request({}, query={"limit": ["10"]}))
    assert listed.payload["total"] == 1

    fetched = handler.handle("article.get", _request({}, query={"id": [article_id]}))
    assert fetched.payload["article"]["id"] == article_id

    saved = handler.handle("article.save", _request({
        "client_request_id": "r-2", "article_id": article_id, "base_revision": 1,
        "snapshot": {"title": "标题", "body": "正文"},
    }))
    assert saved.payload["revision"] == 2

    duplicated = handler.handle("article.duplicate", _request({
        "client_request_id": "r-3", "article_id": article_id, "base_revision": 2,
    }))
    assert duplicated.payload["article"]["revision"] == 1

    deleted = handler.handle("article.delete", _request({
        "client_request_id": "r-4", "article_id": article_id, "base_revision": 2,
    }))
    assert deleted.payload["deleted_article_id"] == article_id


def test_unknown_action_is_404(services) -> None:
    result = _handler(services).handle("article.teleport", _request({}))
    assert (result.status, result.error) == (404, "unknown writing action: article.teleport")


def test_missing_client_request_id_is_400(services) -> None:
    result = _handler(services).handle(
        "article.save",
        _request({"article_id": "a", "base_revision": 1, "snapshot": {}}),
    )
    assert (result.status, result.error) == (400, "client_request_id is required")


def test_missing_base_revision_is_400(services) -> None:
    result = _handler(services).handle(
        "article.save",
        _request({"client_request_id": "r", "article_id": "a"}),
    )
    assert (result.status, result.error) == (400, "base_revision is required")


def test_conflict_message_is_contract_literal(services) -> None:
    """409 + 契约 §6 原文：article_conflict current_revision=<int> article_id=<id>。"""
    article = _handler(services).handle(
        "article.create", _request({"client_request_id": "r-1", "title": "标题"}),
    ).payload["article"]
    _handler(services).handle(
        "article.save",
        _request({"client_request_id": "r-2", "article_id": article["id"],
                  "base_revision": 1, "snapshot": {"title": "标题", "body": "新"}}),
    )
    stale = _handler(services).handle(
        "article.save",
        _request({"client_request_id": "r-3", "article_id": article["id"],
                  "base_revision": 1, "snapshot": {"title": "标题", "body": "旧"}}),
    )
    assert stale.status == 409
    assert stale.error == (
        f"article_conflict current_revision=2 article_id={article['id']}"
    )


def test_deleted_article_message_is_409(services) -> None:
    """A-T10：删除后晚到保存不得复活文章（契约 §6 `article deleted`）。"""
    article = _handler(services).handle(
        "article.create", _request({"client_request_id": "r-1", "title": "标题"}),
    ).payload["article"]
    _handler(services).handle(
        "article.delete",
        _request({"client_request_id": "r-2", "article_id": article["id"],
                  "base_revision": 1}),
    )
    late = _handler(services).handle(
        "article.save",
        _request({"client_request_id": "r-3", "article_id": article["id"],
                  "base_revision": 1, "snapshot": {"title": "标题", "body": "复活"}}),
    )
    assert (late.status, late.error) == (409, "article deleted")


def test_limit_query_non_integer_is_400(services) -> None:
    result = _handler(services).handle(
        "article.list", _request({}, query={"limit": ["abc"]}),
    )
    assert (result.status, result.error) == (400, "limit must be an integer")


def test_unsupported_status_query_is_400(services) -> None:
    result = _handler(services).handle(
        "article.list", _request({}, query={"status": ["已完成"]}),
    )
    assert (result.status, result.error) == (400, "unsupported article status")
```

- [ ] **Step 2：实现 handler** — 结构照抄 `nanobot/webui/memory_routes.py:30-90`（frozen dataclass operations + 名单 + handler + `dispatch()`），额外要求：

```python
# 写入动作都要求 client_request_id；缺失即 400，让 handler 而不是 repository
# 负责协议级校验（与 memory_routes 里 "content is required" 同层）。
_WRITING_MUTATION_ACTIONS = frozenset({
    "article.create", "article.save", "article.duplicate", "article.delete",
})


def _client_request_id(payload: dict[str, Any]) -> str:
    raw = payload.get("client_request_id")
    if not isinstance(raw, str) or not _CLIENT_REQUEST_ID_RE.fullmatch(raw):
        raise WebUISettingsError("client_request_id is required")
    return raw


def _base_revision(payload: dict[str, Any]) -> int:
    raw = payload.get("base_revision")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise WebUISettingsError("base_revision is required")
    return raw
```

`article.save` 从 `payload["snapshot"]` 取快照，缺省 `{}` 时按 `article.get` 的当前持久化字段合并（前端必须发完整快照，缺失即 400 `article snapshot is required`——本 Task 选择严格策略：不做服务端补齐，避免半保存）。

- [ ] **Step 3：跑测试确认通过** → 8 passed；`ruff check nanobot/webui/writing_routes.py`
- [ ] **Step 4：提交** `git commit -m "feat(webui): 写作域 action handler 与契约错误口径"`

### Task A1.6：路由与 gateway 接线

**Files:**
- Modify: `nanobot/webui/settings_routes.py`（`_SYSTEM_ROUTES:152-168` 之后、`_MEMORY_MUTATION_PATHS:190-200` 之后、`_SETTINGS_MUTATION_PATHS:247-249`、`_null_memory_operations:279-305` 之后、`__init__:345-395`、`dispatch:455-480`）
- Modify: `nanobot/webui/ws_http.py`（`_WEBUI_MUTATION_PATHS:149-203`、`GatewayHTTPHandler.__init__:356-373`、settings router 构造 `:393-411`）
- Modify: `nanobot/webui/gateway_services.py`（`build_memory_operations:62-90` 之后、`GatewayHTTPHandler(...)` 调用 `:227-234`）
- Create: `tests/webui/test_writing_wiring.py`

**Interfaces:**
- Consumes: `WritingSettingsHandler` / `WritingSettingsOperations` / `WRITING_ACTION_NAMES`（A1.5）、`writing_api`（A1.4）
- Produces: `build_writing_operations(*, workspace_id: str, workspace_path: Path) -> WritingSettingsOperations`（与 `build_memory_operations` 同签名同风格）；6 条可路由路径；4 条 WS-only 路径

- [ ] **Step 1：写失败测试** `tests/webui/test_writing_wiring.py`

```python
from __future__ import annotations

from nanobot.webui import settings_routes, ws_http


def test_writing_reads_are_routed_to_system_domain() -> None:
    for path, action in (
        ("/api/writing/articles", "article.list"),
        ("/api/writing/articles/get", "article.get"),
        ("/api/writing/articles/create", "article.create"),
        ("/api/writing/articles/save", "article.save"),
        ("/api/writing/articles/duplicate", "article.duplicate"),
        ("/api/writing/articles/delete", "article.delete"),
    ):
        assert settings_routes._SYSTEM_ROUTES[path] == action


def test_writing_writes_are_ws_only() -> None:
    for path in (
        "/api/writing/articles/create", "/api/writing/articles/save",
        "/api/writing/articles/duplicate", "/api/writing/articles/delete",
    ):
        assert settings_routes.WebUISettingsRouter.is_mutation_path(path) is True
        assert ws_http._WEBUI_MUTATION_PATHS[f"article.{path.rsplit('/', 1)[-1]}"] == path


def test_reads_are_not_ws_only() -> None:
    assert settings_routes.WebUISettingsRouter.is_mutation_path("/api/writing/articles") is False


def test_build_writing_operations_is_fully_bound(tmp_path) -> None:
    from dataclasses import fields

    from nanobot.webui.gateway_services import build_writing_operations
    from nanobot.webui.writing_routes import WritingSettingsOperations

    ops = build_writing_operations(workspace_id="default", workspace_path=tmp_path)
    for field in fields(WritingSettingsOperations):
        assert callable(getattr(ops, field.name))
```

- [ ] **Step 2：跑测试确认失败** → `KeyError: '/api/writing/articles'`

- [ ] **Step 3：`settings_routes.py` 四处改动**

1. `_SYSTEM_ROUTES` 追加 6 条路径（契约 §4/§5 原文路径与 action），带一行注释说明写路径也必须登记（照 `:179` 的 agents 注释措辞）。
2. 新增 `_WRITING_MUTATION_PATHS = frozenset({4 条写路径})`，并在 `_SETTINGS_MUTATION_PATHS` 的字面量里以 `*_WRITING_MUTATION_PATHS,` 并入（现有 `*_MEMORY_MUTATION_PATHS` 同位置，`:247-249`）。
3. 新增 `_null_writing_operations()`（照 `:279-305` 的 `_null_memory_operations`，503 `writing service is not configured`），`__init__` 增加 `writing_operations: WritingSettingsOperations | None = None` 参数并构造 `self._writing = writing_domain.WritingSettingsHandler(...)`。
4. `dispatch()` 增加分支：`elif action in writing_domain.WRITING_ACTION_NAMES:` → `await asyncio.to_thread(lambda: self._writing.handle(action, domain_request))`（与 memory 分支同构，SQLite + `threading.Lock` 是阻塞的，不能跑在事件循环里）。

- [ ] **Step 4：`ws_http.py` 两处改动**：`_WEBUI_MUTATION_PATHS` 增加 4 条 `article.create/save/duplicate/delete`；`GatewayHTTPHandler.__init__` 增加 `writing_operations: WritingSettingsOperations | None = None` 并透传给 `WebUISettingsRouter(..., writing_operations=self.writing_operations)`。

- [ ] **Step 5：`gateway_services.py`**：新增 `build_writing_operations`（`partial(writing_api.<fn>, services)` 六项），并在 `GatewayHTTPHandler(...)` 调用里紧随 `identity_operations` 之后加上 `writing_operations=build_writing_operations(workspace_id="default", workspace_path=workspace_path)`。

- [ ] **Step 6：跑测试确认通过 + 全量后端回归**

Run: `python -m pytest tests/webui/test_writing_wiring.py tests/webui/test_writing_routes.py tests/writing -v` → Expected: 全绿
Run: `cd webui && npm run test`（A1 未改前端，必须仍全绿，证明未回归）
Run: `uv run --no-sync basedpyright nanobot/webui nanobot/writing` → Expected: 0 errors

- [ ] **Step 7：提交** `git commit -m "feat(webui): 写作域接入 settings 路由与 gateway 装配"`

### Task A1.7：库目录与配置入口

**Files:**
- Modify: `nanobot/config/paths.py`（`:25-54` 的 `get_runtime_subdir` / `get_media_dir` 附近）
- Create: `tests/test_writing_paths.py`

- [ ] **Step 1：写失败测试**：`get_writing_dir(workspace)` 返回 `<workspace>/writing`，且**不**调用 `ensure_dir`（建库由 `WritingDatabase.__init__` 负责，避免任何读取路径都 mkdir）。
- [ ] **Step 2：实现**

```python
def get_writing_dir(workspace: str | Path | None = None) -> Path:
    """知序写作域的数据目录（文章 SQLite 状态）。

    与 memory 的 `<workspace>/memory/state.db` 并列；不在此处建目录，
    目录由 ``WritingDatabase`` 在真正要写库时创建。
    """
    base = get_workspace_path(workspace)
    return base / "writing"
```

- [ ] **Step 3：`WritingDatabase.__init__` 改用 `get_writing_dir(self.workspace) / "state.db"` 作为默认 `db_path`**（保持 `db_path` 参数可覆盖，测试依赖它）。
- [ ] **Step 4：跑 `python -m pytest tests/test_writing_paths.py tests/writing -v` 全绿**
- [ ] **Step 5：提交** `git commit -m "chore(config): 暴露写作域数据目录路径"`

**GROUP A1 完成条件**：契约 §4/§5/§6 的每条都能用 handler 单测打到；A-T01/A-T05/A-T06/A-T10 的存储侧行为在 `tests/writing/` 有对应断言。前端可以开始接。

---

# Group A2 — 前端数据层（TypeScript，无 UI）

> A2.1 是 A2/A3 的串行前置（枚举改名会波及所有 creative 组件）；A2.2 / A2.3 文件彼此独立，可并发。

### Task A2.1：类型层迁移到英文枚举 + 模板抽出

**Files:**
- Modify: `webui/src/components/creative/types.ts`
- Create: `webui/src/components/creative/templates.ts`
- Modify: `webui/src/components/creative/ArticleList.tsx:17-28`、`ArticleEditor.tsx:17-23`、`MaterialPanel.tsx:11-13`、`OutlinePanel.tsx:8-10`、`SuggestionPanel.tsx:9-13`、`demo-data.ts`
- Modify: `webui/src/tests/article-model.test.ts`、`webui/src/tests/creative-workspace.test.tsx`
- Modify: 10 个 `webui/src/i18n/locales/*/common.json`

**Interfaces:**
- Produces（契约 §3/§7）：

```ts
export type ArticleStatus = "idea" | "draft" | "review" | "done";
export type MaterialType = "note" | "text" | "code" | "link";

export interface OutlineItem { id: string; text: string; order: number }
export interface Material {
  id: string; type: MaterialType; title: string; content: string;
  url?: string; source?: string; note?: string; language?: string;
  included: boolean; order: number; createdAt: string; updatedAt: string;
}
export interface ArticleBrief { goal?: string; audience?: string; style?: string }

export interface Article {
  id: string; title: string; writingType: string; templateId?: string;
  status: ArticleStatus; summary: string; brief: ArticleBrief; body: string;
  outline: OutlineItem[]; materials: Material[]; tags: string[];
  revision: number; createdAt: string; updatedAt: string;
}
export type ArticleSummaryView = Pick<
  Article, "id" | "title" | "summary" | "status" | "writingType" | "tags" | "revision" | "updatedAt"
>;

export const ARTICLE_STATUSES: ArticleStatus[] = ["idea", "draft", "review", "done"];
export const MATERIAL_TYPES: MaterialType[] = ["note", "text", "code", "link"];
export const ARTICLE_STATUS_LABEL_KEY: Record<ArticleStatus, string> = {
  idea: "creative.status.idea", draft: "creative.status.draft",
  review: "creative.status.review", done: "creative.status.done",
};
export const MATERIAL_TYPE_LABEL_KEY: Record<MaterialType, string> = {
  note: "creative.material.type.note", text: "creative.material.type.text",
  code: "creative.material.type.code", link: "creative.material.type.link",
};
```

- `templates.ts`：`WRITING_TEMPLATES: WritingTemplate[]`，`{ id, nameKey, descKey, outlineKeys }`，四个 id 为 `tech-explainer / source-reading / practice-retro / learning-summary`（现 `types.ts:50-95` 四个中文模板的内容照搬为 i18n 键）。
- 服务端字段是 snake_case，前端类型是 camelCase，映射只在 A2.2 的 `writing-api.ts` 里做一次；组件层只见 camelCase。

- [ ] **Step 1：改测试先失败**：`article-model.test.ts` 与 `creative-workspace.test.tsx` 里 `"构思中"` / `"写作中"` / `status: "全部"` 全部替换为 `"idea"` / `"draft"` / `"all"`；`outline: string[]` 断言改成 `{id,text,order}` 对象数组。
- [ ] **Step 2：`npm run test` 确认失败**（类型错误 + 断言失败）。
- [ ] **Step 3：写 `types.ts` 新形状 + `templates.ts`**（上面的接口，逐字采用）。
- [ ] **Step 4：5 个 locale 之外的 10 个 `common.json` 增加 `creative.status.*`（4 键）与 `creative.material.type.*`（4 键）**；中文值沿用现展示文案（构思中/写作中/待校对/已完成、观点/文本/代码/链接）；`creative.template.*` 承接原 `TEMPLATES` 的 12 条中文串。
- [ ] **Step 5：把 5 个 creative 组件的字面量迁移**：`ARTICLE_STATUSES` 遍历渲染处一律 `t(ARTICLE_STATUS_LABEL_KEY[status])`；`filterByStatus` 的哨兵由 `"全部"` 改 `"all"`；`MaterialPanel` 的类型选择器用 `MATERIAL_TYPES` + label 键。
- [ ] **Step 6：`npm run test && npm run lint && npm run build` 全绿**（`i18n.test.tsx:584` 的 locale 一致性测试会捕获漏键）。
- [ ] **Step 7：提交** `git commit -m "refactor(creative): 文章状态与素材类型改为英文枚举"`

### Task A2.2：writing-api.ts — 读写封装与冲突解析

**Files:**
- Create: `webui/src/lib/writing-api.ts`
- Create: `webui/src/tests/writing-api.test.ts`

**Interfaces:**
- Consumes: `request` / `mutation` / `ApiError` / `WebUIMutationTransport`（`webui/src/lib/api.ts:73-156`）、A2.1 类型
- Produces:

```ts
const WRITING_BASE = "/api/writing";
export interface ArticleListParams { q?: string; status?: ArticleStatus | "all"; limit?: number; offset?: number }
export interface ArticleSnapshotPayload {
  title: string; writing_type: string; template_id?: string; status: ArticleStatus;
  summary: string; brief: ArticleBrief; body: string;
  outline: OutlineItem[]; materials: Material[]; tags: string[];
}
export interface SaveArticleResult { article_id: string; revision: number; updated_at: string; digest: string; no_change: boolean }
export interface ArticleConflict { currentRevision: number; articleId: string }
export function parseArticleConflict(message: string): ArticleConflict | null
export async function fetchArticleList(token: string, params: ArticleListParams, base = ""): Promise<{ articles: ArticleSummaryView[]; total: number; limit: number; offset: number }>
export async function fetchArticle(token: string, id: string, base = ""): Promise<Article>
export async function createArticle(transport: WebUIMutationTransport, req: CreateArticleRequest): Promise<Article>
export async function saveArticle(transport: WebUIMutationTransport, req: SaveArticleRequest): Promise<SaveArticleResult>
export async function duplicateArticle(transport: WebUIMutationTransport, req: { articleId: string; baseRevision: number; clientRequestId: string }): Promise<Article>
export async function deleteArticle(transport: WebUIMutationTransport, req: { articleId: string; baseRevision: number; clientRequestId: string }): Promise<{ deletedArticleId: string; revision: number }>
```

`clientRequestId` 由调用方生成并**跨重试复用**：`crypto.randomUUID()`；这是业务级幂等键，与传输层 `nanobot-client.ts:929` 自己生成的 `request_id` 无关（契约 §1 推论 1）。

- [ ] **Step 1：写失败测试** `webui/src/tests/writing-api.test.ts`

```ts
import { describe, expect, it, vi } from "vitest";

import {
  fetchArticleList, parseArticleConflict, saveArticle,
} from "@/lib/writing-api";

describe("writing-api", () => {
  it("builds the contract list URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ articles: [], total: 0, limit: 50, offset: 0 }),
      { status: 200, headers: { "content-type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    await fetchArticleList("tok", { q: "记忆", status: "draft", limit: 20, offset: 40 });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/api/writing/articles?");
    expect(url).toContain("q=%E8%AE%B0%E5%BF%86");
    expect(url).toContain("status=draft");
    expect(url).toContain("limit=20");
    expect(url).toContain("offset=40");
  });

  it("parses the contract conflict message literally", () => {
    expect(parseArticleConflict("article_conflict current_revision=13 article_id=a1"))
      .toEqual({ currentRevision: 13, articleId: "a1" });
    expect(parseArticleConflict("article deleted")).toBeNull();
    expect(parseArticleConflict("")).toBeNull();
  });

  it("sends snake_case payload with base_revision and client_request_id", async () => {
    const requestMutation = vi.fn().mockResolvedValue({
      article_id: "a1", revision: 2, updated_at: "t", digest: "d", no_change: false,
    });
    await saveArticle({ requestMutation } as never, {
      articleId: "a1", baseRevision: 1, clientRequestId: "r-1",
      snapshot: {
        title: "标题", writing_type: "", status: "draft", summary: "",
        brief: {}, body: "正文", outline: [], materials: [], tags: [],
      },
    });
    expect(requestMutation.mock.calls[0][0]).toBe("article.save");
    expect(requestMutation.mock.calls[0][1]).toMatchObject({
      article_id: "a1", base_revision: 1, client_request_id: "r-1",
    });
  });
});
```

- [ ] **Step 2：`npm run test -- writing-api` 确认失败**
- [ ] **Step 3：实现**（GET 用 `new URLSearchParams()` 拼 query，跳过 `undefined`/`"all"`；返回前把 snake_case 映射成 camelCase，映射函数 `toArticle(raw: Record<string, unknown>): Article` / `toSnapshotPayload(article: Article): ArticleSnapshotPayload` 是本文件唯一允许出现 snake_case 的地方）
- [ ] **Step 4：`parseArticleConflict` 实现**：`/^article_conflict current_revision=(\d+) article_id=(\S+)$/`（契约 §6）。
- [ ] **Step 5：跑测试确认通过 + lint**
- [ ] **Step 6：提交** `git commit -m "feat(webui): 写作域读写 API 封装与版本冲突解析"`

### Task A2.3：writing-draft-cache.ts — 本地恢复稿

**Files:**
- Create: `webui/src/lib/writing-draft-cache.ts`
- Create: `webui/src/tests/writing-draft-cache.test.ts`
- Modify: `webui/src/lib/local-preferences.ts`（只复用其 `safeLocalStorage` 写法，**不**改导出）

**Interfaces:**
- Produces:

```ts
export interface LocalArticleDraft {
  workspaceKey: string; articleId: string; baseRevision: number;
  localEditSeq: number; snapshot: ArticleSnapshotPayload; cachedAt: string;
}
export const DRAFT_STORAGE_KEY = "zhixu.writing.draft.v1";
export function readDraft(workspaceKey: string, articleId: string): LocalArticleDraft | null
export function writeDraft(draft: Omit<LocalArticleDraft, "cachedAt">): boolean
export function clearDraft(workspaceKey: string, articleId: string): void
export function listDrafts(workspaceKey: string): LocalArticleDraft[]
```

- 存储结构 `{ [workspaceKey]: { [articleId]: draft } }`，按工作区命名空间隔离（spec A-07）。
- 这里的 `workspaceKey` 是前端可得的当前项目路径（`activeWorkspaceScope.project_path`），与服务端 `sha256[:16]` 的 `workspace_key` **不是同一个值**（契约 §2 说明服务端键不下发到浏览器）；缓存只需要「同机同项目」隔离，用路径即可，绝不能把服务端键当已获取。
- 写失败（配额、隐私模式）返回 `false`，调用方必须显示「恢复保障不可用」而不是失败保存（A-07 末条）。

- [ ] **Step 1：写失败测试**：读不到→`null`；`writeDraft` 后 `readDraft` 一致；不同 `workspaceKey` 隔离；`localStorage` 里是坏 JSON 时 `readDraft` 返回 `null` 且**不清空其他条目**；`setItem` 抛错时 `writeDraft` 返回 `false`。
- [ ] **Step 2：实现并跑绿**（全部包在 `try/catch` 内，`typeof window === "undefined"` 时直接返回 `false`/`null`，与 `App.tsx:410-419` 的容错写法一致）。
- [ ] **Step 3：提交** `git commit -m "feat(webui): 文章本地恢复稿缓存"`

### Task A2.4：article-model.ts — 双计数与纯函数改造

**Files:**
- Modify: `webui/src/components/creative/article-model.ts`（整文件重写）
- Modify: `webui/src/components/creative/demo-data.ts`
- Modify: `webui/src/tests/article-model.test.ts`

**Interfaces:**
- Produces（删除 `nextId` / `touch`，替换为）：

```ts
export function nextLocalEditSeq(prev: number): number
export function canAcceptSuggestion(
  article: { id: string; revision: number },
  suggestion: { articleId: string; baseRevision: number },
): boolean                                   // 保持原语义，v2 沿用
export function createDraftInput(title: string, templateId?: string): CreateArticleRequest
export function duplicateTitle(title: string): string
export function countBodyChars(body: string): number
export function isDirty(a: ArticleSnapshotPayload, b: ArticleSnapshotPayload): boolean
export function buildSnapshot(article: Article): ArticleSnapshotPayload
export function searchArticles(articles: ArticleSummaryView[], query: string): ArticleSummaryView[]
export function filterByStatus(articles: ArticleSummaryView[], status: ArticleStatus | "all"): ArticleSummaryView[]
export function buildExportFilename(title: string): string
export function generateMarkdown(article: Article): string
export function isSafeUrl(url: string): boolean
```

`nextId` / `touch` 直接删除（ID 与 revision 由服务端负责，A-06/A-08）。

- [ ] **Step 1：改测试**：`createArticle` → `createDraftInput`；`touch` 断言删除；新增 `countBodyChars("a 中\nb") === 3`（去掉所有空白后的 Unicode 字符数，spec A-03 口径，**不得**叫「词数」）与 `isDirty` 用例。
- [ ] **Step 2：确认失败 → 实现 → 通过**

```ts
const WHITESPACE_RE = /\s+/u;

export function countBodyChars(body: string): number {
  return body.replace(WHITESPACE_RE, "").length;
}
```

`generateMarkdown` 保持契约 §7：只输出标题、摘要、正文，不混入素材/建议；文件名逻辑 `buildExportFilename` 原样保留（`article-model.ts:83-91` 已处理非法字符与空标题）。
- [ ] **Step 3：`demo-data.ts` 适配新类型**，`updated: "刚刚"` 改为真实 ISO 串 + `revision` 递增，样例条目带 `sample: true`。
- [ ] **Step 4：提交** `git commit -m "refactor(creative): 拆分服务端 revision 与本地编辑计数"`

---

# Group A3 — 创作空间 UI

> 依赖：A2.1 必须先完成（枚举改名）；A3.1 必须先于 A3.2~A3.8（`draft` 状态形状）。
> `sample` 字段只存在于 `demo-data.ts` 的返回类型 `DemoArticle extends Article { sample: true }`，服务端接口不返回它（A-01 演示隔离）。

### Task A3.1：容器改造 —— 真实列表、加载态与创建

**Files:**
- Modify: `webui/src/components/creative/CreativeWorkspace.tsx`（整文件重写）
- Modify: `webui/src/App.tsx:2894-2911`（挂载点传 props）
- Modify: `webui/src/preview/PreviewApp.tsx:111`
- Modify: `webui/src/components/creative/ArticleList.tsx`（props 与展示）
- Create: `webui/src/tests/creative-workspace-loading.test.tsx`

**Interfaces:**
- Consumes: `fetchArticleList` / `fetchArticle` / `createArticle`（A2.2）、`WebUIMutationTransport`
- Produces:

```ts
export interface CreativeWorkspaceProps {
  getToken: () => string;
  transport: WebUIMutationTransport;
  /** 仅用于本地恢复稿命名空间隔离；服务端不接收该值（契约 §2）。 */
  workspacePath: string;
  /** 只有预览入口为 true：允许装载演示数据。 */
  preview?: boolean;
}
```

- [ ] **Step 1：写失败测试** `creative-workspace-loading.test.tsx`

```tsx
it("shows four distinct list states", async () => {
  const cases = [
    { body: { articles: [], total: 0, limit: 50, offset: 0 }, status: 200, expect: "creative.empty" },
    { body: {}, status: 500, expect: "creative.loadFailed" },
  ];
  for (const item of cases) { /* stub fetch → 断言对应 i18n 文案节点出现 */ }
});

it("never auto-inserts demo articles into the real workspace", async () => {
  render(<CreativeWorkspace getToken={token} transport={transport} workspacePath="/p" />);
  await waitFor(() => expect(screen.getByTestId("article-list-empty")).toBeInTheDocument());
  expect(screen.queryByText(/为什么需要记忆/)).not.toBeInTheDocument();
});
```

- [ ] **Step 2：`CreativeWorkspace.tsx` 重写要点**（删除 `useState(demoArticles)`、`demoSuggestions(...)`、`undoStack`、`restoreDemo` 四个状态；阶段 A 建议面板改为「等待阶段 B」空态，不删 `SuggestionPanel.tsx` 文件本身）
  - 状态：`summaries` / `activeArticle` / `listState: "loading" | "empty" | "error" | "ready"` / `query` / `statusFilter`。
  - 列表加载：`fetchArticleList(getToken(), { q, status, limit, offset })`，切筛选或搜索时**重新请求服务端**（契约 §4：过滤与排序由服务端负责，前端不再本地 `filter`）。
  - 打开编辑器：`fetchArticle(getToken(), id)`；404 → `creative.articleMissing`；409 `article deleted` → `creative.articleDeleted`（spec A-07：读取失败不回退演示数据）。
  - 新建：`createArticle(transport, createDraftInput(title, templateId))`；失败保留已填标题与模板选择（A-01）。
  - 预览模式 `preview`：`preview === true` 时才 `setSummaries(demoArticles())`，且不发起任何请求。
- [ ] **Step 3：`App.tsx` 挂载点**改为

```tsx
<CreativeWorkspace
  getToken={getToken}
  transport={client}
  workspacePath={activeWorkspaceScope?.project_path ?? ""}
/>
```

`PreviewApp.tsx:111` 加 `preview`。
- [ ] **Step 4：跑测试确认通过 + `npm run build`**
- [ ] **Step 5：提交** `git commit -m "feat(creative): 创作空间接入服务端文章列表与创建"`

### Task A3.2：保存状态机（A-06）

**Files:**
- Create: `webui/src/components/creative/use-article-save-flow.ts`
- Create: `webui/src/tests/article-save-flow.test.ts`
- Modify: `ArticleEditor.tsx:17-23`（props 增加 `saveState` / `onSaveNow`）、`CreativeWorkspace.tsx`

**Interfaces:**
- Consumes: `saveArticle` / `SaveArticleResult` / `ArticleConflict` / `parseArticleConflict` / `ApiError`（A2.2）、`writeDraft` / `readDraft` / `clearDraft`（A2.3）、`buildSnapshot` / `isDirty` / `nextLocalEditSeq`（A2.4）
- Produces:

```ts
export type SavePhase = "saved" | "dirty" | "saving" | "failed" | "conflict" | "offline";
export interface SaveFlowState {
  phase: SavePhase; baseRevision: number; localEditSeq: number;
  lastSavedAt: string | null; lastError: string | null; conflict: ArticleConflict | null;
}
export const AUTOSAVE_DEBOUNCE_MS = 1500;   // spec A-06 推荐默认值，集中一处
export interface Draft { /* 编辑器内工作副本 */ }
export function useArticleSaveFlow(options: {
  getToken: () => string; transport: WebUIMutationTransport;
  article: Article; workspacePath: string;
}): {
  draft: ArticleSnapshotPayload; dirty: boolean; state: SaveFlowState;
  setField: <K extends keyof ArticleSnapshotPayload>(key: K, value: ArticleSnapshotPayload[K]) => void;
  saveNow: () => Promise<void>;
  acknowledgeConflict: (resolution: "reload" | "savedCopy" | "discardLocal") => Promise<void>;
};
```

- [ ] **Step 1：写失败测试**（用假 `transport` + `vi.useFakeTimers()`，覆盖 A-T03/A-T05/A-T06/A-T08/A-T09）

```ts
it("merges edits made while a save is in flight instead of overwriting them", async () => {
  const requestMutation = vi.fn().mockImplementationOnce(async () => {
    await Promise.resolve();
    return { article_id: "a1", revision: 2, updated_at: "t", digest: "d", no_change: false };
  });
  const { result } = renderFlow({ article: articleFixture({ revision: 1 }), requestMutation });
  act(() => result.current.setField("body", "第一稿"));
  const pending = act(() => result.current.saveNow());
  act(() => result.current.setField("body", "保存中又改了"));  // A-T03
  await pending;
  expect(result.current.state.baseRevision).toBe(2);
  expect(result.current.dirty).toBe(true);
  expect(result.current.draft.body).toBe("保存中又改了");
  await advance(AUTOSAVE_DEBOUNCE_MS);
  expect(requestMutation).toHaveBeenCalledTimes(2);
  expect(requestMutation.mock.calls[1][1].snapshot.body).toBe("保存中又改了");
});

it("does not advance revision when the content is unchanged", async () => { ... no_change: true → phase "saved"，localEditSeq 不变 ... });
it("stops autosave and keeps the local draft on 409 conflict", async () => { ... phase "conflict"，writeDraft 被调用，AUTOSAVE 定时器不再触发 ... });
it("reuses one client_request_id across retries of the same draft", async () => { ... 失败重试两次 calls[1] 的 client_request_id 相同 ... });
it("falls back to offline phase when the transport rejects with 503", async () => { ... });
it("shows 已保存 only after the server confirms", async () => { ... });
```

- [ ] **Step 2：实现要点**
  - 内部 `queue = Promise` 链，保证同一编辑器串行提交（服务端另有每连接锁，`inbound_commands.py:1326`，两者都要，前端不依赖后者）。
  - 提交时冻结快照 + `baseRevision` + 复用 `clientRequestId`（同一草稿内容复用同一 id；草稿再变化才生成新 id）。
  - 成功且 `no_change === false` → `baseRevision = result.revision`，`clearDraft`；`no_change === true` → 仅更新 `lastSavedAt`。
  - `ApiError.status === 409`：先试 `parseArticleConflict`；命中 → `phase: "conflict"`、存 `conflict`、停止自动保存；`article deleted` → `phase: "conflict"` 且 `conflict.currentRevision = -1`（UI 显示「文章已不存在，可另存副本」，A-02）。
  - `phase === "offline"`：`navigator.onLine === false` 或 503 时进入；`window.addEventListener("online")` 恢复后先 `fetchArticle` 校验版本再决定是否允许保存。
- [ ] **Step 3：跑测试确认通过**
- [ ] **Step 4：提交** `git commit -m "feat(creative): 文章保存状态机与冲突暂停"`

### Task A3.3：正文编辑、预览与中文输入法安全（A-03）

**Files:**
- Modify: `webui/src/components/creative/ArticleEditor.tsx`
- Modify: `webui/src/components/creative/article-model.ts`（若 A2.4 未含 IME 合并纯函数）
- Create: `webui/src/tests/article-editor-ime.test.tsx`

**Interfaces:**
- Consumes: `useArticleSaveFlow`（A3.2）、`MarkdownText`（`@/components/MarkdownText`）、`countBodyChars`（A2.4）
- Produces: `EditorMode = "edit" | "preview" | "split"`；纯函数 `mergeImeValue(original: string, previousRendered: string, current: string): string`

IME 合并算法（A-T04 的关键，写进 `article-model.ts`）：

```ts
/** 组合输入结束时的值：编辑器里的值优先，除非远端/其它面板在此期间改过正文。
 *  返回 null 表示「正文被第三方改动过」，调用方保留两个值走冲突提示，绝不覆盖。 */
export function mergeImeValue(original: string, previousRendered: string, current: string): string | null {
  if (previousRendered === original) return current;
  if (previousRendered === current) return original;
  return null;
}
```

- [ ] **Step 1：写失败测试**
  - 用 `fireEvent.compositionStart(textarea)` → `compositionEnd` 断言：组合期间 `useArticleSaveFlow` 未被调用（自动保存延迟到 `compositionEnd`）；textarea 的 DOM value 与光标 `selectionStart/End` 保持不变。
  - 代码块粘贴保留缩进：`fireEvent.change` 插入含 `    ` 与换行的文本，断言 `draft.body` 与输入完全一致（不做 trim）。
  - 切 `edit` → `preview` → `edit` 后 textarea 值不变（A-03「内容不能因模式切换丢失」）。
- [ ] **Step 2：实现要点**
  - textarea 非受控 + `ref`，`onChange` 只写 ref 值与 `setField("body", value)`；**禁止**在 render 中用 `value={draft.body}` 强制回写，因为保存响应可能晚于输入。
  - 正文 DOM id 固定 `article-body-input`（A3.5 合并逻辑依赖此选择器）。
  - 组合输入期间不调 `setField`，在 `compositionend` 一次性提交。
  - 统计区文案 `t("creative.editor.charCount", { count })`，**不得**出现「词」字样。
- [ ] **Step 3：跑测试确认通过**
- [ ] **Step 4：提交** `git commit -m "fix(creative): 正文输入避免受控回写打断中文组合输入"`

### Task A3.4：素材与提纲面板（A-04/A-05）

**Files:**
- Modify: `webui/src/components/creative/MaterialPanel.tsx`、`OutlinePanel.tsx`
- Modify: `webui/src/tests/creative-panels.test.tsx`（新建，若无）

**Interfaces:**
- Consumes: `useArticleSaveFlow` 的 `setField("materials" | "outline", ...)`
- Produces: 两个面板继续走文章快照（**不新增 mutation**，契约 §5 末段：素材随快照提交）。

- [ ] **Step 1：写失败测试**
  - 新增素材默认 `included: true`（A-05）；`code` 类型保留换行与缩进；`link` 类型只接受 http(s)（客户端预校验复用 `isSafeUrl`，服务端再校验一次）。
  - 删除素材前确认；确认取消时列表不变（A-05 操作失败保留数据）。
  - 提纲上移/下移只改 `order`，`id` 不变（A-04 稳定 ID）；模板初始化只在新建时发生（`createDraftInput`，面板不重置已有提纲）。
- [ ] **Step 2：实现**：`Material` 字段补齐 `url/source/language/order/createdAt/updatedAt`；渲染标签走 `MATERIAL_TYPE_LABEL_KEY`；面板折叠状态（`collapsed`）**不进快照**（A-08 第 2 条：面板开关不推进版本）。
- [ ] **Step 3：`npm run test && npm run lint`**
- [ ] **Step 4：提交** `git commit -m "feat(creative): 素材与提纲面板对齐持久化字段"`

### Task A3.5：版本冲突处理（A-08）

**Files:**
- Create: `webui/src/components/creative/ConflictDialog.tsx`
- Modify: `webui/src/components/creative/ArticleEditor.tsx`、`locales/*/common.json`
- Create: `webui/src/tests/creative-conflict.test.tsx`

**Interfaces:**
- Consumes: `SaveFlowState.conflict`、`acknowledgeConflict`（A3.2）、`fetchArticle`（A2.2）、`buildSnapshot`（A2.4）
- Produces:

```ts
export interface ConflictDialogProps {
  conflict: ArticleConflict | null;
  latest: Article | null;
  localBody: string;
  onReloadLatest: () => void;
  onSaveCopy: () => Promise<void>;
  onDiscardLocal: () => Promise<void>;
  onCancel: () => void;
}
```

- [ ] **Step 1：写失败测试**（A-T07/A-T08）
  - 打开对话框展示「服务器最新版本」与「本地未保存内容」两份正文（`<pre data-testid="conflict-remote">` / `conflict-local`）。
  - 「另存副本」用**本地草稿**新建文章：`createArticle(transport, { title: duplicateTitle(local.title), ...buildSnapshotFromDraft(draft) })`，成功后把编辑器切到新文章（新 `id` / `revision = 1`），服务端那份最新版本原样保留。语义来自 spec A-08 第 5 条：保住的是本地稿，不是服务端行，因此**不调** `duplicateArticle`（那个动作复制的是服务端当前内容，语义不同）。
  - 「查看最新版本并手动合并」：拉 `fetchArticle` 填入编辑器，保留本地稿副本，`baseRevision = latest.revision`；随后再次保存必须携带新 `base_revision`（断言第二次 `requestMutation` 的 `base_revision` 等于最新版本）。
  - 「确认放弃本地修改后重新载入」需二次确认。
  - 断言：三种分支之外**没有**「用本地稿强制覆盖」按钮。
- [ ] **Step 2：实现并提交** `git commit -m "feat(creative): 文章版本冲突的三条处理路径"`

### Task A3.6：离开守卫与重开恢复（A-07）

**Files:**
- Create: `webui/src/components/creative/ArticleRecoveryBanner.tsx`
- Modify: `CreativeWorkspace.tsx`、`use-article-save-flow.ts`、`App.tsx`
- Create: `webui/src/tests/creative-recovery.test.tsx`

- [ ] **Step 1：写失败测试**（A-T08/A-T09）
  - 切文章 / 返回创作列表时若有待保存内容 → 先触发保存；失败显示「重试 / 留在当前页 / 确认离开」三选项。
  - 重开同一 `articleId` 且本地存在恢复稿：`服务端 revision 未变` → banner「恢复本地编辑 / 放弃恢复稿」；`revision 已变` → 走 A3.5 冲突合并流程。
  - `writeDraft` 返回 `false`（配额满）时，界面显示「恢复保障不可用，保存与导出仍可用」的静态提示。
- [ ] **Step 2：实现要点**：`useEffect` 返回的清理函数里 `flush()`；`window.addEventListener("beforeunload", handler)` 仅在 `dirty` 时 `event.preventDefault()`。
- [ ] **Step 3：提交** `git commit -m "feat(creative): 文章离开守卫与本地恢复稿对比"`

### Task A3.7：导出（A-09）

**Files:**
- Create: `webui/src/lib/writing-export.ts`
- Create: `webui/src/tests/writing-export.test.ts`
- Modify: `ArticleEditor.tsx`

**Interfaces:**
- Consumes: `ArticleSnapshotPayload`（A2.2）、`buildExportFilename` / `generateMarkdown`（A2.4）
- Produces: `exportArticleMarkdown(snapshot: ArticleSnapshotPayload, options: { includeMaterials?: false }): void`

- [ ] **Step 1：写失败测试**：文件名非法字符清理；`generateMarkdown` 只输出标题/摘要/正文；下载不依赖服务端（`saveArticle` 抛错时仍可导出）；「含未保存修改」提示分支断言 `dirty === true` 时 `draft` 内容用于导出。
- [ ] **Step 2：实现**（`new Blob([markdown], { type: "text/markdown;charset=utf-8" })` + `URL.createObjectURL` + `<a download>`）。
- [ ] **Step 3：提交** `git commit -m "feat(creative): 文章 Markdown 导出"`

### Task A3.8：删除 / 复制 / 晚到请求防护（A-02）

**Files:**
- Modify: `webui/src/components/creative/ArticleList.tsx`、`CreativeWorkspace.tsx`
- Modify: `webui/src/tests/creative-workspace.test.tsx`

- [ ] **Step 1：写失败测试**（A-T10）
  - 删除按钮走 `alert-dialog`（`ArticleList.tsx:10-12` 已引入组件），若 `dirty` 再追加「有未保存内容」提示。
  - 删除调 `deleteArticle({ articleId, baseRevision: article.revision, clientRequestId })`；409 时提示「文章已被其他地方修改，请刷新」。
  - 复制调 `duplicateArticle`，副本标题 `…（副本）`（服务端生成），列表顶部出现副本且 ID 与服务端返回一致。
  - **删除后晚到保存**：删除成功后在途的 `saveArticle` 返回 409 `article deleted` → 界面不重新创建文章，`articleDeleted` 空态保留。
- [ ] **Step 2：实现并提交** `git commit -m "feat(creative): 文章删除复制与晚到请求防护"`

**GROUP A3 完成条件**：A-T01~A-T12 的交互面全部有自动化断言；`npm run test` / `lint` / `build` 三项零退出码错误。

---

# Group A4 — 尾盘（Leader + reviewer）

### Task A4.1：集体测试（A-T01~A-T12 逐条）

**Files:**
- Create: `.ai-runtime-artifacts/verifications/2026-10-12-zhixu-writing-phase-a-collective-test.md`

- [ ] **Step 1：启动真实 gateway + webui dev**

```bash
cd webui && npm run dev   # 代理 /api 与 WS 到 gateway :8765
nanobot gateway
```

- [ ] **Step 2：按 A-T01~A-T12 逐条执行并记录证据**（命令、页面截图或 DOM 断言、后端 SQL 查询结果）。人工必做三条：
  - **A-T01**：建文章 → 重启 gateway → 重开，`id` 与内容不变。
  - **A-T04**：中文连续输入「知识库」不出现候选框被打断；粘贴 4 空格缩进代码块后 `updated_at` 与缩进保持。
  - **A-T06**：两个标签页同基线保存，第二页必须收到冲突并停止自动保存。
- [ ] **Step 3：全量回归**

```bash
cd webui && npm run test && npm run lint && npm run build
python -m pytest tests/ -q
uv run --no-sync basedpyright
ruff check nanobot/
```

Expected：全部退出码 0。任何失败先修复，不得跳过。

### Task A4.2：代码审查与安全审查

**Files:**
- Create: `.ai-runtime-artifacts/reviews/2026-10-12-zhixu-writing-phase-a-code-review.md`
- Create: `.ai-runtime-artifacts/reviews/2026-10-12-zhixu-writing-phase-a-security-review.md`

- [ ] **Step 1：** Load `requesting-code-review` + `code-review-and-quality` → 扇出 reviewer（范围：A1~A3 全部 diff）。
- [ ] **Step 2：** Load `security-and-hardening` → security-auditor 重点核查：Markdown 按不可信内容渲染、素材 URL 校验、日志不整篇落正文（spec §10）、工作区 SQL 作用域、本地草稿不存敏感内容。
- [ ] **Step 3：** Leader 落盘两份产物并更新 execution-log。

### Task A4.3：执行日志与追踪收口

**Files:**
- Create: `.ai-runtime-artifacts/execution-logs/2026-10-12-zhixu-writing-phase-a-execution-log.md`
- Modify: `.ai-runtime-artifacts/execution-logs/tracking/`（dispatch-track）

- [ ] **Step 1：** 记录各 WU 返回、审查结论、偏差与遗留项。
- [ ] **Step 2：** 标记阶段 B 前置就绪，提示「阶段 B plan 需按本阶段实际形状审阅」。

---

## 决策记录

1. **SQLite 而非 JSON**（用户 2026-10-12 选定）。理由：A-06/A-08 要求「同事务内校验 `base_revision` → 写快照 → 推进版本」，SQLite 一条带条件的 `UPDATE` 即 CAS，JSON 方案需手写跨进程锁 + 摘要比较 + 半保存防护；A-01 列表筛选/搜索/排序也直接由 SQL 承担。仓库已有同构实现（`nanobot/memory/database.py:167-224`）。
2. **文章快照 = 单表单行**（`outline` / `materials` / `tags` / `brief` 为 JSON 列）而非多表。理由：spec A-06 要求同一快照原子保存，单行让原子性 trivially 成立；素材跨文章共享是 P1，届时再拆表并保留 schema 版本迁移。
3. **业务级 `client_request_id` 由前端生成**。理由：传输层 `request_id` 不进入领域 handler（契约 §1 推论 1），5 分钟缓存窗口外的重试必须由 repository 挡住。
4. **`workspace_key` 服务端派生，不下发**。理由：避免客户端伪造作用域键跨区读写；已知边界如实写进契约 §2，不在 plan 里假装多工作区已完全隔离。
5. **首版不引入富文本 / 协同编辑**。理由：spec A-03 与 §10 明确排除；textarea + IME 合并已覆盖验收。

## Plan 自检（writing-plans Step: Self-Review）

**1. Spec 覆盖**

| spec 项 | 覆盖 Task |
| --- | --- |
| A-01 创建与列表 | A1.3 / A1.5 / A2.2 / A3.1 |
| A-02 元信息与生命周期 | A1.2 / A1.3 / A2.1 / A3.1 / A3.4 / A3.8 |
| A-03 正文编辑与预览 | A2.4 / A3.3 |
| A-04 提纲管理 | A1.2 / A3.1 / A3.4 |
| A-05 素材管理 | A1.2 / A3.4 |
| A-06 保存机制 | A1.3 / A2.2 / A3.2 |
| A-07 重开、离开与恢复 | A1.3 / A2.3 / A3.1 / A3.6 |
| A-08 版本保护与冲突 | A1.3 / A2.2 / A3.2 / A3.5 |
| A-09 导出与恢复范围 | A2.4 / A3.7 |
| §5 数据与持久化 | A1.1 / A1.2 / A1.4 / A1.6 |
| §5.3 接口能力边界 | 契约 §4/§5 + A1.5 / A1.6 |
| §9 页面与交互 | A3.1~A3.8（含 i18n 与键盘可达） |
| §10 失败与非功能 | A1.3 事务 / A2.3 / A3.2 / A4.2 |
| §11.1 A-T01~A-T12 | A4.1 |

**阶段 B（B-01~B-10、B-T01~B-T13）不在本计划内**，见 `2026-10-12-zhixu-writing-phase-b-plan.md`；本阶段唯一向 B 暴露的既有事实是 `revision` / 快照摘要（A3.2 输出）。

**2. 占位符扫描**：A1.3 Step 3 代码里有两处「worker 落地必须改掉」的显式修正点（`save_snapshot` 摘要变量、`delete_article` 的时间变量）。它们**不是 TODO**，是给实现者的具体改写指令；执行时若仍残留 `now_iso` 或 walrus 比较写法，Task 判定为未完成。

**3. 类型一致性**：`ArticleSnapshotPayload`（A2.2）↔ `snapshot`（契约 §5）↔ `normalize_article_fields`（A1.2）三处字段集合一致；`SaveFlowState.conflict.currentRevision = -1` 只用于「文章已删除」分支，A3.5 的「另存副本」逻辑必须显式处理该哨兵值。

## Next

**（写入后须暂停 — 即使用户句末含「然后执行」）**

- 计划确认 → 说「开始实现」或「执行」
- 需要调整 → 直接说修改意见（例如「自动保存改手动」「上限改数值」「A3 拆成两个 WU」）
- 想拆分并行 → 审 `2026-10-12-zhixu-writing-phase-a-dispatch.md` 后说「开始实现」或「并行执行」
- 阶段 B 计划 → 已同批写入 `2026-10-12-zhixu-writing-phase-b-plan.md`，需在 A 实现落定后再审

