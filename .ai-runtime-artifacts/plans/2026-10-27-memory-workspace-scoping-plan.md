---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - .claude/skills/writing-plans/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-dispatch.md
source:
  - 用户本轮需求：记忆按工作区隔离；全局与工作区记忆都要读取；与工作台记忆产出一致
  - 用户复核意见：取消“默认工作区”概念；手动新增可为全局也可为工作区；评估检索性能对首轮的影响
  - .ai-runtime-artifacts/specs/2026-10-27-memory-page-presentation-spec.md
  - .ai-runtime-artifacts/contracts/2026-10-27-contract-memory-presentation.md
  - .ai-runtime-artifacts/contracts/2026-10-21-contract-session-workbench.md
  - nanobot/memory/{provenance,models,database,repository,extractor}.py
  - nanobot/memory/retrieval/{engine,store_adapter}.py 与 channels/
  - nanobot/agent/{loop,context}.py 与 nanobot/agent/tools/memory_search.py
  - nanobot/webui/{memory_api,memory_presentation,session_workbench}.py
  - .ai-runtime-artifacts/research/2026-10-27-memory-recall-bench.py（实测数据）
  - .ai-runtime-artifacts/research/2026-10-27-chroma-where-bench.py（实测数据）
created_at: 2026-10-27
updated_at: 2026-10-27
status: approved
approved: true
approval_evidence: 用户在本会话回复「并行执行」
---

# 记忆工作区隔离实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 按 `harness-kit/core/orchestration/dispatcher-workflow.md` 执行（不是 Claude Code 原生 plan 工具）。步骤用复选框（`- [ ]`）跟踪。

**Goal:** 让长期记忆按「工作区」隔离存储与检索——工作区里产生的记忆只在该工作区的对话里被召回，全局记忆在所有工作区都能被召回，并与会话工作台的记忆产出保持同一套归属口径。

**Architecture:** 不做物理分库，仍是「一个 gateway 一个 `<agent-workspace>/memory/state.db`」。把 `memories.workspace_id` 从恒为 `"default"` 的哑列升级为**权威隔离键**：`'default'` 表示全局记忆，其余值是工作区目录路径派生的稳定哈希（复用既有 `workspace_from_path`）。写入侧在提取/手动新增时按会话工作区打标；检索侧（Layer 4 自动注入 + `memory_search` 工具）统一按「全局 ∪ 当前工作区」过滤，可见性判定只发生在 SQLite 侧（SQL 过滤 + 向量候选回表判定），不引入向量库侧过滤。

**Tech Stack:** Python 3.11+ / asyncio / SQLite(FTS5) / ChromaDB(可选向量层) / pytest / React + TypeScript(Vite + bun)

---

## 1. 现状核查（已逐处读码确认）

| 位置 | 现状 | 对本次的影响 |
| --- | --- | --- |
| `nanobot/agent/loop.py:153` | `_MEMORY_WORKSPACE_ID = "default"` | agent 侧写入的工作区键恒为 `"default"` |
| `nanobot/webui/gateway_services.py:770,777` | `build_memory_operations(workspace_id="default", ...)` | WebUI 面板写入同样恒为 `"default"` |
| `nanobot/memory/database.py:53` | `memories.workspace_id TEXT NOT NULL DEFAULT 'default'`，索引 `idx_memories_owner (workspace_id, user_id, scope, scope_owner)` 前导列就是 `workspace_id` | 列和索引都已就位，可直接升级语义，无需新列 |
| `nanobot/memory/repository.py:719,824,789` | `search_semantic_scored` / `query_semantic` / `search_episodes` 的 SQL **无任何 workspace 条件** | 检索完全不过滤 |
| `nanobot/memory/retrieval/store_adapter.py` | FTS5 + 向量并集；向量只按 `query_embeddings` 查，无 `where` | 向量候选需在回表后判定可见性（见 §5） |
| `nanobot/agent/context.py:212` | `_build_memory_section_with_ids` → `engine.retrieve_with_ids(...)`，无作用域参数 | 需要逐层透传可见集合 |
| `nanobot/agent/tools/memory_search.py:100` | 工具直接调 `engine.retrieve(...)` | 同上 |
| `nanobot/memory/extractor.py:1541,1572` | 写入 `workspace_id=self.workspace_id`（store 标签）；`provenance["workspaces"]` 才是项目快照 | 归属口径分裂：列是哑的，归属只在 metadata 里 |
| `nanobot/memory/extractor.py:1483` | `find_memory_by_subject_predicate(..., workspace_id=self.workspace_id)` | 画像合并跨工作区串扰（当前因列恒定而看不出来） |
| `nanobot/webui/memory_api.py:231,415` | `list_memories(conn, workspace_id=services.workspace_id)` | 面板按 `"default"` 过滤，等于全量；一旦列语义升级，这行会漏掉工作区记忆，必须改 |
| `nanobot/webui/memory_api.py:249,647,692,715` | 详情/改/删守卫 `row.workspace_id != services.workspace_id` → 404 | 列语义升级后会把工作区记忆误判为「不存在」 |
| `nanobot/webui/memory_presentation.py:37-38,127-135` | 面板的工作区筛选**只认 provenance 快照**；`workspace_options` 的「默认工作区」取自 agent 工作区目录 | 展示口径与列口径需要统一 |
| `nanobot/webui/session_workbench.py:142-191` | 工作台记忆项 = 会话 scratchpad + 本会话 episode 关联的长期记忆 | 需要补归属标记，才能和记忆页口径一致 |
| `nanobot/memory/vector/indexer.py:80` | 向量 metadata 已写 `workspace_id` | 向量候选回表判定可见性时无需额外查询结构 |
| `.ai-runtime-artifacts/nanobot-data/workspace/memory/state.db` | 实测：17 条记忆全部 `scope=global, workspace_id=default`，episodes 15 条 | 历史数据全部落在 `'default'`，天然是「全局记忆」，无需迁移 |

**结论**：本次要改的是「隔离键语义 + 写入打标 + 检索过滤 + 面板口径」四处，存储结构本身（单库）不动。

---

## 2. 关键决策

| 编号 | 决策 | 理由 |
| --- | --- | --- |
| **D1** | `workspace_id='default'` 表示**全局记忆**；工作区记忆用**稳定工作区身份 id**（`<工作区>/.nanobot/workspace-id`，32 位 hex） | 用户复核：不能按 path 找。仓库已有同构机制（`SessionManager._load_or_create_workspace_id`），语义是「改名/移动跟随、复制目录换 id」；path 哈希在改名后会永久失联且不可改归属 |
| **D2** | **取消“默认工作区”这个概念**：任何工作区目录（含 agent 默认工作区）都按自己的身份 id 归属，互不相同；只有「显式全局」与历史数据才是 `'default'` | 用户复核意见。语义只由「有无工作区」决定；面板上「默认工作区」这一项随之取消，工作区下拉里只剩「全局记忆」与各工作区 |
| **D3** | 可见集合 = `('default', 当前工作区键)`；会话没有工作区（如非 WebUI 渠道）时 = `('default',)` | 一条规则覆盖“全局 + 工作区都读”；非当前工作区的记忆不可见 |
| **D4** | provenance 快照与 `workspace_id` **同源**：都由 `session_memory_workspace_id(session)` 推导；会话无工作区时 provenance 不写 `workspaces`（= 全局） | 避免两套口径漂移；面板「全局记忆」标签与检索可见性一致 |
| **D5** | `services.workspace_id` 保留为 **store 标签**（恒 `'default'`），只用于 `provenance.store_workspace_id` 与 scratchpad 主键，不再作为记忆行过滤条件 | 现有两处引用不断裂，改动面最小 |
| **D6** | episodes 新增 `workspace_id` 列（幂等 `ALTER`），与 memories 对齐 | 情节通道（`search_episodes`）目前完全没有归属信息，无法隔离 |
| **D7** | **手动新增记忆**：可显式选「全局记忆」或某个工作区；省略选择时归全局 | 用户复核意见。归属由调用方显式表达，避免默认落到某个项目 |
| **D8** | **允许事后改归属**：记忆编辑可换工作区，同时更新 `workspace_id` 列与 `metadata.provenance.workspaces` 快照 | 用户复核意见。兜底改名/迁移/误归类场景（面板里改一次即可，不必动数据库） |
| **D9** | 身份 marker 写在 `<工作区>/.nanobot/workspace-id`；**目录不可写时回退 path 哈希**（面板标注为派生归属） | 与既有 session 机制同构；只读工作区不强行落盘 |

---

## 3. 变更文件地图

| 文件 | 职责变化 |
| --- | --- |
| `nanobot/workspace_identity.py`（**新建**） | 稳定工作区身份：读/建 `<工作区>/.nanobot/workspace-id`、复制检测轮换、只读目录回退 path 哈希、进程内缓存 |
| `nanobot/memory/provenance.py` | 新增工作区键推导与可见集合函数；`extraction_provenance` 支持全局态 |
| `nanobot/memory/models.py` | `Episode` 增加 `workspace_id` 字段 |
| `nanobot/memory/database.py` | `episodes` 表加 `workspace_id` 列 + 幂等迁移 + 索引；schema 版本 2→3 |
| `nanobot/memory/repository.py` | 查询函数接受 `workspace_ids`；新增「库中已登记工作区」查询；`update_memory` 支持改 `workspace_id`；episode 读写带列 |
| `nanobot/memory/extractor.py` | 提取写入按会话工作区打标；画像合并按工作区隔离 |
| `nanobot/memory/retrieval/store_adapter.py` | 透传可见集合；向量候选回表后判定可见性 |
| `nanobot/memory/retrieval/channels/{semantic,recent,episodes}.py` | 通道接受并转发 `workspace_ids` |
| `nanobot/memory/retrieval/engine.py` | `retrieve_with_ids` 接受 `workspace_ids` 并下发三路 |
| `nanobot/agent/context.py` | `_build_memory_section_with_ids` 接受 `workspace_ids` |
| `nanobot/agent/loop.py` | 每轮按有效工作区计算可见集合；注册内存态解析器给工具 |
| `nanobot/agent/tools/memory_search.py` | 工具按会话解析可见集合后检索 |
| `nanobot/webui/memory_api.py` | 面板读写不再按 store 标签过滤；手动新增/编辑按归属写列并同步 provenance 快照；payload 暴露 `workspace_id` |
| `nanobot/webui/memory_routes.py` | `memory-update` 透传 `workspace` 参数 |
| `nanobot/webui/memory_presentation.py` | 工作区选项与筛选以 `workspace_id` 为准，含固定「全局记忆」项，取消「默认工作区」 |
| `nanobot/webui/session_workbench.py` | 工作台记忆项带归属标记 |
| `webui/src/lib/types.ts`、`webui/src/components/settings/memory/*` | 前端类型、筛选下拉、标签、编辑弹窗可改归属 |

> `nanobot/memory/vector/store.py` **不改**：向量侧不做库内过滤（依据 §5 实测），可见性统一在 SQLite 侧判定。

---

## 4. WU 划分与依赖

```
WU-1 存储基座（Task 1a 身份模块 / 1b 隔离键委托 / 2 episode 列 / 3 repository 过滤）
   ├──> WU-2 写入归属（Task 4 extractor）
   ├──> WU-3 检索可见性（Task 5-6 retrieval/agent）
   ├──> WU-4 面板与工作台（Task 7-8 webui，含归属可改）
   └──> WU-5 前端（Task 9，含编辑态改归属；依赖 WU-4 的 payload 契约）
```

---

## 5. 性能评估（实测结论）

### 5.1 测量方法

- 脚本：`.ai-runtime-artifacts/research/2026-10-27-memory-recall-bench.py`（真实 schema：memories + memories_fts + 触发器 + `idx_memories_owner`）、`2026-10-27-chroma-where-bench.py`（chromadb 1.5.9，显式 embedding 绕过模型下载）。
- 规模：1k / 10k / 50k 条记忆；1 个 / 10 个工作区；查询命中集与真实检索一致（`LIMIT 90` = 真实调用 `limit*3`）。

### 5.2 FTS5 召回加 `workspace_id` 过滤（默认后端）

| 行数 | 工作区数 | 无过滤 (ms) | 有过滤 (ms) | 差值 |
| --- | --- | --- | --- | --- |
| 1 000 | 1 | 0.66 | 0.71 | +0.05 |
| 1 000 | 10 | 0.65 | 0.47 | −0.18 |
| 10 000 | 1 | 5.92 | 5.73 | −0.19 |
| 10 000 | 10 | 5.27 | 3.31 | −1.96 |
| 50 000 | 1 | 26.76 | 29.50 | +2.74 |
| 50 000 | 10 | 27.47 | 16.34 | −11.13 |

查询计划（加过滤前后一致）：

```
SCAN f VIRTUAL TABLE INDEX 0:M4
SEARCH m USING INTEGER PRIMARY KEY (rowid=?)
USE TEMP B-TREE FOR ORDER BY
```

**结论**：
1. 过滤**几乎零成本**，多数场景更快——`m.workspace_id` 是回表后顺带判定的一列，不新增索引扫描；命中集变小还让 TEMP B-TREE 排序更小（故出现负差值）。
2. 主导成本是 FTS5 扫描 + 排序，与本次改造无关（既有问题：`ORDER BY rank` 先物化全部命中再排序，`LIMIT` 不裁剪扫描量）。
3. `workspace_id` 列已有前导索引 `idx_memories_owner`，即使将来走索引也无额外建索引成本。

### 5.3 Chroma 向量路 `where` 过滤（**决定不采用**）

| 行数 | 工作区数 | 无 where (ms) | 有 where (ms) | 差值 |
| --- | --- | --- | --- | --- |
| 1 000 | 1 | 1.71 | 3.74 | +2.03 |
| 1 000 | 10 | 1.38 | 2.22 | +0.84 |
| 10 000 | 1 | 1.89 | 19.78 | **+17.89** |
| 10 000 | 10 | 1.83 | 8.37 | **+6.54** |

（已含预热调用，排除首次构建元数据索引的一次性成本。`where` 语义正确：返回结果严格落在可见集合内。）

**决定**：向量路**不使用** Chroma `where`。理由：固定开销 1–18ms/查询，且 Chroma 的元数据过滤实现随版本变动；改为在 `store_adapter` 回表读到的 SQLite 行上统一判定可见性（FTS5 命中的行本就带 `workspace_id`，零额外 IO）。代价是向量 top-N 中属于其它工作区的候选被丢弃后可能不足量——这是「召回变少」而非「泄漏」，可接受。

### 5.4 对首次对话的影响

| 环节 | 增量 | 说明 |
| --- | --- | --- |
| Layer 4 自动注入（每轮都跑，含首轮） | **≈ 0 ms** | 见 5.2；默认 `memory_search_backend=fts5`，向量层默认关闭 |
| 向量后端（opt-in） | **≈ 0 ms** | 不使用 Chroma `where`（5.3），改 SQLite 侧判定 |
| 每轮计算可见集合 | µs 级 | 1 次会话 metadata 字典读取（已在内存）+ 1 次工作区身份解析 |
| 工作区身份解析（marker 方案新增） | 首次 ~1 ms（写 marker），之后 **µs 级** | 身份解析带**进程内缓存**（`nanobot/workspace_identity._CACHE`），同一路径每轮只做一次字典查找；marker 只在首次解析时读/写一次 |
| `memory_search` 工具 | ≈ 0 ms | 复用工具本来就取过的 `get_cached` 会话对象，**不新增磁盘读**；会话未命中缓存时退化为全局集合，不做 `read_session_metadata` 磁盘 I/O |
| 记忆面板（设置页，非对话路径） | 无回归 | 改造前 `list_memories(conn, workspace_id='default')` 已返回全部行（历史数据全是 `'default'`），改造后 `list_memories(conn)` 集合相同 |

**结论：对首次对话没有可感知影响。** 需要守住两条：①「工具侧不得为拿工作区归属而新增磁盘读」（Task 6 用 `get_cached`）；②「身份解析必须带进程内缓存」（Task 1a），否则每轮一次 stat+read 会在高频会话里累积。

---

## Task 1a（WU-01）: 稳定工作区身份模块

**Files:**
- Create: `nanobot/workspace_identity.py`
- Test: `tests/test_workspace_identity.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_workspace_identity.py
import os
import re
import shutil
from pathlib import Path

from nanobot.workspace_identity import workspace_identity_id


def test_first_call_creates_marker_and_returns_stable_id(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    first = workspace_identity_id(project)
    assert re.fullmatch(r"[0-9a-f]{32}", first)
    marker = project / ".nanobot" / "workspace-id"
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8").strip() == first
    assert workspace_identity_id(project) == first


def test_renamed_workspace_keeps_identity(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    original = workspace_identity_id(project)
    moved = tmp_path / "proj-renamed"
    shutil.move(str(project), str(moved))
    assert workspace_identity_id(moved) == original


def test_copied_workspace_gets_a_new_identity(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    original = workspace_identity_id(project)
    copy = tmp_path / "proj-copy"
    shutil.copytree(str(project), str(copy))
    assert workspace_identity_id(copy) != original


def test_unwritable_workspace_falls_back_to_path_hash(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    original = workspace_identity_id(project)
    # 让 marker 目录不可写，模拟只读工作区
    os.chmod(project / ".nanobot", 0o500)
    try:
        fallback = workspace_identity_id(tmp_path / "proj2")
    finally:
        os.chmod(project / ".nanobot", 0o700)
    assert re.fullmatch(r"[0-9a-f]{24}", fallback)
    assert fallback != original
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-sync pytest tests/test_workspace_identity.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nanobot.workspace_identity'`

- [ ] **Step 3: 实现**

新建 `nanobot/workspace_identity.py`：

```python
"""稳定工作区身份。

与 ``nanobot.session.manager`` 的会话命名空间同构：身份写在工作区内的
``.nanobot/workspace-id``（32 位 hex），因此**改名/移动后 id 不变**，而把目录
复制到别处时会轮换出新 id（副本不继承原目录的记忆）。

工作区只读时无法落盘 marker，回退到 path 哈希——这种归属是"派生的"，
改名后会失联，面板需标注。
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import posixpath
import re
import secrets
import threading
from pathlib import Path, PurePosixPath, PureWindowsPath

_MARKER_DIR = ".nanobot"
_MARKER_NAME = "workspace-id"
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_FALLBACK_LEN = 24
_LOCK = threading.Lock()
_CACHE: dict[str, str] = {}


def workspace_marker_path(workspace: Path) -> Path:
    return Path(workspace) / _MARKER_DIR / _MARKER_NAME


def path_fallback_id(workspace: Path) -> str:
    """不可写工作区的降级身份：路径归一化后的哈希。"""
    raw = str(Path(workspace).expanduser())
    windows_path = PureWindowsPath(raw)
    if windows_path.is_absolute():
        identity = ntpath.normcase(ntpath.normpath(raw))
    elif PurePosixPath(raw).is_absolute():
        identity = posixpath.normpath(raw)
    else:
        identity = raw
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:_FALLBACK_LEN]


def workspace_identity_id(workspace: Path) -> str:
    """工作区 → 稳定身份 id。

    顺序：已有 marker → 复制检测 → 新建 marker → 不可写时回退 path 哈希。
    任何异常都不得冒泡：记忆链路不能因为身份解析失败而整体中断。
    """
    resolved = Path(workspace).expanduser()
    cache_key = str(resolved)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached
    with _LOCK:
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return cached
        value = _resolve(resolved)
        _CACHE[cache_key] = value
        return value


def _resolve(workspace: Path) -> str:
    marker = workspace_marker_path(workspace)
    try:
        existing = _read_marker(marker)
    except Exception:
        existing = None
    if existing is not None and not _is_claimed_by_other_path(existing, workspace):
        return existing
    try:
        created = existing or secrets.token_hex(16)
        _write_marker(marker, created)
        _claim(created, workspace)
        return created
    except OSError:
        return path_fallback_id(workspace)
```

配套私有函数（同一文件内）：

```python
def _read_marker(marker: Path) -> str | None:
    if marker.is_symlink():
        return None
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if _ID_RE.fullmatch(value) else None


def _write_marker(marker: Path, value: str) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_name(f".{marker.name}.{secrets.token_hex(8)}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, f"{value}\n".encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, marker)
    finally:
        tmp.unlink(missing_ok=True)


def _registry_dir() -> Path:
    from nanobot.config.paths import get_runtime_subdir

    return get_runtime_subdir("workspace-registry")


def _claim(workspace_id: str, workspace: Path) -> None:
    directory = _registry_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{workspace_id}.json").write_text(
        json.dumps({"path": str(workspace)}, ensure_ascii=False), encoding="utf-8"
    )


def _is_claimed_by_other_path(workspace_id: str, workspace: Path) -> bool:
    """复制检测：同一 id 被另一个**仍然存在**的路径占用 → 视为副本。"""
    record = _registry_dir() / f"{workspace_id}.json"
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    other = data.get("path") if isinstance(data, dict) else None
    if not isinstance(other, str) or not other:
        return False
    try:
        if Path(other).resolve(strict=False) == Path(workspace).resolve(strict=False):
            return False
        return Path(other).exists()
    except OSError:
        return False
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --no-sync pytest tests/test_workspace_identity.py -q`
Expected: PASS（4 passed；复制检测用例依赖注册表目录，测试需用 `monkeypatch` 把 `nanobot.config.paths.get_runtime_subdir` 指向 `tmp_path`，否则会写真实数据目录——实现时按此调整测试）

- [ ] **Step 5: 提交**

```bash
git add nanobot/workspace_identity.py tests/test_workspace_identity.py
git commit -m "feat(workspace): 新增稳定工作区身份模块"
```

---

## Task 1b（WU-01）: 记忆隔离键委托给身份模块

**Files:**
- Modify: `nanobot/memory/provenance.py`
- Test: `tests/memory/test_workspace_scope.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/test_workspace_scope.py
import re
from pathlib import Path

from nanobot.memory.provenance import (
    GLOBAL_MEMORY_WORKSPACE_ID,
    memory_visibility_ids,
    memory_workspace_id,
    session_memory_workspace_id,
)


def test_workspace_id_comes_from_stable_identity(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    key = memory_workspace_id(str(project))
    assert re.fullmatch(r"[0-9a-f]{32}", key)
    assert key != GLOBAL_MEMORY_WORKSPACE_ID


def test_missing_or_invalid_path_is_global() -> None:
    assert memory_workspace_id(None) == GLOBAL_MEMORY_WORKSPACE_ID
    assert memory_workspace_id("") == GLOBAL_MEMORY_WORKSPACE_ID
    assert memory_workspace_id("relative/path") == GLOBAL_MEMORY_WORKSPACE_ID


def test_default_workspace_is_not_special_cased(tmp_path: Path) -> None:
    """取消“默认工作区”概念：默认工作区与任何项目目录一样按自己的身份归属。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    key = memory_workspace_id(str(workspace))
    assert key != GLOBAL_MEMORY_WORKSPACE_ID
    assert re.fullmatch(r"[0-9a-f]{32}", key)


def test_visibility_always_includes_global() -> None:
    assert memory_visibility_ids(GLOBAL_MEMORY_WORKSPACE_ID) == ("default",)
    assert memory_visibility_ids("abc123") == ("default", "abc123")
    assert memory_visibility_ids("") == ("default",)


def test_session_workspace_id_reads_session_scope(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    class _Session:
        metadata: dict = {"workspace_scope": {"project_path": str(project)}}

    assert session_memory_workspace_id(_Session()) == memory_workspace_id(str(project))


def test_session_without_scope_is_global() -> None:
    class _Session:
        metadata: dict = {}

    assert session_memory_workspace_id(_Session()) == GLOBAL_MEMORY_WORKSPACE_ID
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-sync pytest tests/memory/test_workspace_scope.py -q`
Expected: FAIL — `ImportError: cannot import name 'GLOBAL_MEMORY_WORKSPACE_ID'`

- [ ] **Step 3: 实现**

在 `nanobot/memory/provenance.py` 追加：

```python
GLOBAL_MEMORY_WORKSPACE_ID = "default"


def memory_workspace_id(project_path: str | None) -> str:
    """工作区路径 → 记忆隔离键。

    走稳定工作区身份（``nanobot.workspace_identity``）：id 写在工作区内的
    ``.nanobot/workspace-id``，改名/移动后不变，复制目录会换新 id。缺失或
    非法路径 → ``'default'``（全局记忆）。
    """
    if not isinstance(project_path, str) or not project_path.strip() or "\x00" in project_path:
        return GLOBAL_MEMORY_WORKSPACE_ID
    try:
        return workspace_identity_id(Path(project_path))
    except Exception:
        return GLOBAL_MEMORY_WORKSPACE_ID


def session_memory_workspace_id(session: "Session") -> str:
    """会话 → 记忆隔离键（读 session.metadata.workspace_scope.project_path）。"""
    scope = session.metadata.get("workspace_scope")
    project_path = cast(dict[str, Any], scope).get("project_path") if isinstance(scope, dict) else None
    return memory_workspace_id(project_path if isinstance(project_path, str) else None)


def memory_visibility_ids(workspace_id: str) -> tuple[str, ...]:
    """某工作区内可见的记忆隔离键集合：全局 + 本工作区。"""
    key = (workspace_id or "").strip() or GLOBAL_MEMORY_WORKSPACE_ID
    if key == GLOBAL_MEMORY_WORKSPACE_ID:
        return (GLOBAL_MEMORY_WORKSPACE_ID,)
    return (GLOBAL_MEMORY_WORKSPACE_ID, key)
```

并加 import：`from pathlib import Path`、`from nanobot.workspace_identity import workspace_identity_id`。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --no-sync pytest tests/memory/test_workspace_scope.py tests/test_workspace_identity.py -q`
Expected: PASS（10 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/memory/provenance.py tests/memory/test_workspace_scope.py
git commit -m "feat(memory): 记忆隔离键改用稳定工作区身份"
```

---

## Task 2（WU-1）: Episode 携带工作区列

**Files:**
- Modify: `nanobot/memory/models.py`、`nanobot/memory/database.py`、`nanobot/memory/repository.py`
- Test: `tests/memory/test_repository.py`、`tests/memory/test_database.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/test_repository.py 追加
def test_episode_roundtrip_keeps_workspace_id(tmp_path) -> None:
    from nanobot.memory.database import MemoryDatabase
    from nanobot.memory.models import Episode
    from nanobot.memory.repository import add_episode, get_episode

    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    episode = Episode(
        id="ep-1",
        session_id="websocket:a",
        summary="摘要",
        started_at="2026-10-27T00:00:00+00:00",
        ended_at="2026-10-27T00:01:00+00:00",
        workspace_id="ws-abc",
    )
    with db.connect() as conn:
        add_episode(conn, episode)
        loaded = get_episode(conn, "ep-1")
    assert loaded is not None
    assert loaded.workspace_id == "ws-abc"
```

```python
# tests/memory/test_database.py 追加
def test_legacy_episodes_table_gets_workspace_id_column(tmp_path) -> None:
    from nanobot.memory.database import MemoryDatabase

    db = MemoryDatabase(tmp_path)
    with db.connect() as conn:
        conn.execute("CREATE TABLE episodes (id TEXT PRIMARY KEY)")  # 极简旧表
    db.ensure_schema()
    with db.connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(episodes)")}
    assert "workspace_id" in columns
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-sync pytest tests/memory/test_repository.py tests/memory/test_database.py -q`
Expected: FAIL — `TypeError: Episode.__init__() got an unexpected keyword argument 'workspace_id'`

- [ ] **Step 3: 实现**

`nanobot/memory/models.py`：`Episode` 在 `workspace_snapshot_id` 之后加字段，并在 `to_row`/`from_row` 同步：

```python
    compaction_checkpoint_id: str = ''
    workspace_snapshot_id: str = ''
    workspace_id: str = 'default'
    metadata: dict[str, Any] = field(default_factory=dict)
```

```python
            'workspace_snapshot_id': self.workspace_snapshot_id,
            'workspace_id': self.workspace_id,
            'metadata': json.dumps(self.metadata, ensure_ascii=False),
```

```python
            workspace_snapshot_id=row['workspace_snapshot_id'],
            workspace_id=row['workspace_id'],
            metadata=json.loads(row['metadata']) if row['metadata'] else {},
```

`nanobot/memory/database.py`：`episodes` 建表语句加列 + 索引；`init_schema` 的幂等迁移加一行：

```python
    workspace_id     TEXT    NOT NULL DEFAULT 'default',
```

```python
    "CREATE INDEX IF NOT EXISTS idx_episodes_workspace  ON episodes(workspace_id)",
```

```python
            if "workspace_id" not in episode_columns:
                conn.execute(
                    "ALTER TABLE episodes ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'default'"
                )
```

同时把 `_SCHEMA_VERSION = "2"` 改为 `"3"`。

`nanobot/memory/repository.py`：`_INSERT_EPISODE_SQL` 与 `_SELECT_EPISODE_COLUMNS` 各加 `workspace_id`，`_row_to_episode` 读取 `row['workspace_id']`。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --no-sync pytest tests/memory -q`
Expected: PASS（含 Task 1 的新文件）

- [ ] **Step 5: 提交**

```bash
git add nanobot/memory/models.py nanobot/memory/database.py nanobot/memory/repository.py tests/memory
git commit -m "feat(memory): episode 增加工作区列与幂等迁移"
```

---

## Task 3（WU-1）: repository 查询支持 `workspace_ids`

**Files:**
- Modify: `nanobot/memory/repository.py`
- Test: `tests/memory/test_repository_workspace_filter.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/test_repository_workspace_filter.py
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Episode, Memory, MemoryType
from nanobot.memory.repository import (
    add_episode,
    add_memory,
    list_episodes,
    list_memories,
    query_semantic,
    search_episodes,
    search_memories,
    search_semantic_scored,
)


def _seed(tmp_path):
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        for index, workspace in enumerate(("default", "ws-a", "ws-b")):
            add_memory(conn, Memory(
                id=f"m-{index}",
                content=f"创作灵感 项目{index}",
                created_at="2026-10-27T00:00:00+00:00",
                updated_at="2026-10-27T00:00:00+00:00",
                type=MemoryType.FACT,
                workspace_id=workspace,
            ))
        for index, workspace in enumerate(("default", "ws-a")):
            add_episode(conn, Episode(
                id=f"ep-{index}",
                session_id="websocket:a",
                summary=f"创作灵感 情节{index}",
                started_at="2026-10-27T00:00:00+00:00",
                ended_at="2026-10-27T00:01:00+00:00",
                workspace_id=workspace,
            ))
    return db


def test_search_memories_filters_by_workspace_ids(tmp_path) -> None:
    db = _seed(tmp_path)
    with db.connect() as conn:
        hits = search_memories(conn, "创作灵感", workspace_ids=("default", "ws-a"))
    assert {m.workspace_id for m in hits} == {"default", "ws-a"}


def test_semantic_and_recent_respect_workspace_ids(tmp_path) -> None:
    db = _seed(tmp_path)
    with db.connect() as conn:
        scored = search_semantic_scored(conn, "创作灵感", workspace_ids=("default",))
        recent = query_semantic(conn, min_importance=0.0, since_days=3650, limit=10,
                                workspace_ids=("ws-a",))
    assert {m.workspace_id for m, _ in scored} == {"default"}
    assert {m.id for m in recent} == {"m-1"}


def test_empty_workspace_ids_matches_nothing(tmp_path) -> None:
    db = _seed(tmp_path)
    with db.connect() as conn:
        hits = list_memories(conn, workspace_ids=())
    assert hits == []


def test_episodes_filter_by_workspace_ids(tmp_path) -> None:
    db = _seed(tmp_path)
    with db.connect() as conn:
        rows = list_episodes(conn, workspace_ids=("ws-a",))
        entity_hits = search_episodes(conn, entity="创作灵感", workspace_ids=("default",))
    assert {r.workspace_id for r in rows} == {"ws-a"}
    assert {r.id for r in entity_hits} == {"ep-0"}


def test_known_workspace_ids_lists_store_workspaces(tmp_path) -> None:
    db = _seed(tmp_path)
    with db.connect() as conn:
        ids = known_workspace_ids(conn)
    assert ids == {"default", "ws-a", "ws-b"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-sync pytest tests/memory/test_repository_workspace_filter.py -q`
Expected: FAIL — `TypeError: search_memories() got an unexpected keyword argument 'workspace_ids'`

- [ ] **Step 3: 实现**

在 `nanobot/memory/repository.py` 增加条件构造器（放在 `_escape_like` 附近）：

```python
def _workspace_ids_condition(
    column: str, workspace_ids: Sequence[str] | None
) -> tuple[str, list[Any]]:
    """可见集合 → (SQL 条件, 参数)。

    ``None`` 表示不过滤（管理面/全量读）；空元组表示"什么都看不到"，必须返回
    恒假条件而不是退化成不过滤——否则会泄漏其他工作区的记忆。
    """
    if workspace_ids is None:
        return "", []
    ids = [str(item) for item in workspace_ids if str(item)]
    if not ids:
        return "0 = 1", []
    placeholders = ", ".join("?" for _ in ids)
    return f"{column} IN ({placeholders})", ids
```

并在 `Sequence` 加入 `from collections.abc import Sequence`（若尚未导入）。

逐个改造：

- `list_memories(..., workspace_ids: Sequence[str] | None = None)`：`clauses` 里追加该条件（列名 `workspace_id`）。
- `list_episodes(..., workspace_ids: Sequence[str] | None = None)`：列名 `workspace_id`。
- `search_memories(..., workspace_ids=None)`：把 `clauses.append("m.workspace_id = ?")` 改为追加 `m.workspace_id IN (...)`；单值 `workspace_id` 参数保留（向后兼容）。
- `_search_memories_like(..., workspace_ids=None)`：同样追加。
- `search_semantic_scored(..., workspace_ids=None)`：FTS5 分支 `WHERE memories_fts MATCH ?` 后追加 `AND <条件>`；LIKE 回退分支同样追加。
- `query_semantic(..., workspace_ids=None)`：`WHERE importance_score >= ? AND datetime(updated_at) >= datetime(?)` 后追加 `AND <条件>`。
- `search_episodes(..., workspace_ids=None)`：把现有条件加括号后追加：

```python
    where = "(summary LIKE ? ESCAPE '\\' OR session_id LIKE ? ESCAPE '\\')"
    condition, condition_params = _workspace_ids_condition("workspace_id", workspace_ids)
    if condition:
        where = f"{where} AND {condition}"
    cur = conn.execute(
        f"SELECT id, summary, ended_at FROM episodes WHERE {where} LIMIT ?",
        [pattern, pattern, *condition_params, int(limit)],
    )
```

新增「库中已登记工作区」查询（面板手动新增时校验归属）：

```python
def known_workspace_ids(conn: sqlite3.Connection) -> set[str]:
    """库中已出现的工作区键（memories + episodes）。"""
    ids: set[str] = set()
    for table in ("memories", "episodes"):
        try:
            rows = conn.execute(f"SELECT DISTINCT workspace_id FROM {table}").fetchall()
        except sqlite3.OperationalError:
            continue
        ids.update(str(row[0]) for row in rows if row[0])
    return ids
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --no-sync pytest tests/memory -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/memory/repository.py tests/memory/test_repository_workspace_filter.py
git commit -m "feat(memory): 记忆与情节查询支持按可见集合过滤"
```

---

## Task 4（WU-2）: 提取写入按会话工作区打标

**Files:**
- Modify: `nanobot/memory/extractor.py`、`nanobot/memory/provenance.py`
- Test: `tests/memory/test_extractor_workspace.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/test_extractor_workspace.py
from nanobot.memory.models import MemoryType
from nanobot.memory.provenance import workspace_from_path
from nanobot.memory.repository import list_memories


class _Session:
    key = "websocket:a"

    def __init__(self, project_path: str | None) -> None:
        self.metadata = (
            {"workspace_scope": {"project_path": project_path}} if project_path else {}
        )


def _extractor(tmp_path):
    from nanobot.memory.database import MemoryDatabase
    from nanobot.memory.extractor import MemoryExtractor

    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    return MemoryExtractor(db, runtime=None, workspace_id="default")


def test_persist_tags_memory_with_session_workspace(tmp_path, monkeypatch) -> None:
    extractor = _extractor(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    monkeypatch.setattr(
        "nanobot.memory.extractor.MemoryExtractor._persist",
        _capturing_persist(extractor),
    )
    ...
```

（最终测试直接构造 `FilteredExtractionResult`（含一条 `LLMMemoryItem`）调用 `_persist`，断言落库行的 `workspace_id` 等于 `workspace_from_path(str(project))['id']`；再用一个无 `workspace_scope` 的会话断言落库 `'default'`。）

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-sync pytest tests/memory/test_extractor_workspace.py -q`
Expected: FAIL — 落库行 `workspace_id` 仍为 `'default'`（项目会话未被标记）

- [ ] **Step 3: 实现**

`nanobot/memory/provenance.py`：`extraction_provenance` 改签名，全局态不写快照：

```python
def extraction_provenance(
    session: Session, source: str, *, workspace_id: str | None = None
) -> dict[str, Any]:
    """捕获提取时的来源与工作区。

    ``workspace_id`` 为 ``'default'``（全局）时不写 ``workspaces`` 快照——面板的
    「全局记忆」标签与检索可见性据此保持同一口径（D4）。
    """
    trigger_mode = (
        "automatic" if source in _AUTOMATIC_SOURCES
        else "manual" if source == "manual"
        else "unknown"
    )
    workspaces: list[dict[str, str]] = []
    if workspace_id != GLOBAL_MEMORY_WORKSPACE_ID:
        scope = session.metadata.get("workspace_scope")
        if isinstance(scope, dict):
            project_path = cast(dict[str, Any], scope).get("project_path")
            if isinstance(project_path, str):
                try:
                    workspaces.append(workspace_from_path(project_path))
                except ValueError:
                    pass
    return {
        "origin_kind": "manual" if trigger_mode == "manual" else "ai",
        "generation_method": "ai",
        "trigger_mode": trigger_mode,
        "trigger_reason": source,
        "workspaces": workspaces,
        "session_id": session.key,
    }
```

`nanobot/memory/extractor.py`：

1) 引入依赖：

```python
from nanobot.memory.provenance import (
    GLOBAL_MEMORY_WORKSPACE_ID,
    extraction_provenance,
    session_memory_workspace_id,
)
```

2) 新增实例方法：

```python
    @staticmethod
    def _row_workspace_id(session: Session) -> str:
        """本次落库使用的记忆隔离键（会话工作区；无工作区 → 全局）。"""
        return session_memory_workspace_id(session)
```

3) `_load_existing_memories(self, candidates, *, workspace_id: str)`：把 `list_memories(..., workspace_id=self.workspace_id, ...)` 改为 `workspace_id=workspace_id`。

4) 三处调用点（`extract` / `context_compress` / idle 提取）改为：

```python
        row_workspace_id = self._row_workspace_id(session)
        existing_memories = self._load_existing_memories(
            llm_result.memories, workspace_id=row_workspace_id
        )
```

5) `_persist` 内：

```python
        row_workspace_id = self._row_workspace_id(session)
        provenance = extraction_provenance(
            session, source, workspace_id=row_workspace_id
        )
        provenance["store_workspace_id"] = self.workspace_id
```

`Memory(...)` 的 `workspace_id=self.workspace_id` 改为 `workspace_id=row_workspace_id`；`Episode(...)` 增加 `workspace_id=row_workspace_id`。

6) `_merge_into_existing(self, conn, item, provenance, *, workspace_id: str)`：`find_memory_by_subject_predicate(conn, item.subject, item.predicate, workspace_id=workspace_id)`，调用处传 `row_workspace_id`。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --no-sync pytest tests/memory -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/memory/extractor.py nanobot/memory/provenance.py tests/memory/test_extractor_workspace.py
git commit -m "feat(memory): 提取写入按会话工作区打标并隔离画像合并"
```

---

## Task 5（WU-3）: 检索链路透传可见集合

**Files:**
- Modify: `nanobot/memory/retrieval/store_adapter.py`、`nanobot/memory/retrieval/channels/{semantic,recent,episodes}.py`、`nanobot/memory/retrieval/engine.py`
- Test: `tests/memory/retrieval/test_store_adapter.py`、`tests/memory/retrieval/test_engine.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/retrieval/test_store_adapter.py 追加
def test_search_semantic_scored_filters_by_workspace_ids(tmp_path) -> None:
    from nanobot.memory.database import MemoryDatabase
    from nanobot.memory.models import Memory, MemoryType
    from nanobot.memory.repository import add_memory
    from nanobot.memory.retrieval.store_adapter import MemoryStoreAdapter

    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        for index, workspace in enumerate(("default", "ws-a", "ws-b")):
            add_memory(conn, Memory(
                id=f"m-{index}",
                content="创作灵感 共享",
                created_at="2026-10-27T00:00:00+00:00",
                updated_at="2026-10-27T00:00:00+00:00",
                type=MemoryType.FACT,
                workspace_id=workspace,
            ))
    adapter = MemoryStoreAdapter(db)
    hits = adapter.search_semantic_scored("创作灵感", limit=10, workspace_ids=("default", "ws-a"))
    assert {mem.workspace_id for mem, _ in hits} == {"default", "ws-a"}
```

```python
# tests/memory/retrieval/test_engine.py 追加
def test_engine_passes_workspace_ids_to_channels() -> None:
    import asyncio
    from nanobot.memory.retrieval.engine import RetrievalEngine

    seen: dict[str, object] = {}

    class _Store:
        def search_semantic_scored(self, query, *, keywords=None, limit=30, workspace_ids=None):
            seen["semantic"] = workspace_ids
            return []

        def search_episodes(self, *, entity, limit=5, workspace_ids=None):
            seen["episodes"] = workspace_ids
            return []

        def query_semantic(self, *, min_importance, since_days, limit, workspace_ids=None):
            seen["recent"] = workspace_ids
            return []

        def search_attachments(self, term, *, intent, limit=5, workspace_ids=None):
            return []

    engine = RetrievalEngine(store=_Store())
    asyncio.run(engine.retrieve_with_ids(
        query="创作灵感",
        recent_messages=[],
        precomputed_keywords=["创作灵感"],
        workspace_ids=("default", "ws-a"),
    ))
    assert seen["semantic"] == ("default", "ws-a")
    assert seen["episodes"] == ("default", "ws-a")
    assert seen["recent"] == ("default", "ws-a")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-sync pytest tests/memory/retrieval -q`
Expected: FAIL — `TypeError: search_semantic_scored() got an unexpected keyword argument 'workspace_ids'`

- [ ] **Step 3: 实现**

`store_adapter.py`：

```python
    def search_semantic_scored(
        self,
        query: str,
        *,
        keywords: list[str] | None = None,
        limit: int = 30,
        workspace_ids: Sequence[str] | None = None,
    ) -> list[tuple[Any, float]]:
```

FTS5 调用加 `workspace_ids=workspace_ids`；向量调用**不加** where（见 §5.3），改为在回表行上判定：

```python
            for rank, (mid, _score) in enumerate(vector_hits, start=1):
                rrf[str(mid)] = rrf.get(str(mid), 0.0) + 1.0 / (k + rank)
```

```python
                mem = fts5_by_id.get(mid)
                if mem is None:
                    # 向量独有 id：回查 SQLite 权威行并做活性 + 可见性过滤
                    mem = repository.get_memory(conn, mid)
                    if mem is None or not _is_live(mem) or not _is_visible(mem, workspace_ids):
                        discarded_ids.append(mid)
                        continue
                    content_by_id[mid] = mem.content
                elif not _is_visible(mem, workspace_ids):
                    discarded_ids.append(mid)
                    continue
```

并新增模块级函数：

```python
def _is_visible(memory: Any, workspace_ids: Sequence[str] | None) -> bool:
    """记忆行的可见性判定（向量候选回表后用；FTS5 侧已由 SQL 过滤）。"""
    if workspace_ids is None:
        return True
    allowed = {str(item) for item in workspace_ids}
    return str(getattr(memory, "workspace_id", "default") or "default") in allowed
```

`search_episodes` / `query_semantic` 同步加 `workspace_ids` 参数并透传。

`channels/semantic.py`、`channels/recent.py`、`channels/episodes.py`：函数签名加 `workspace_ids: Sequence[str] | None = None` 并转发给 store。

`engine.py`：`retrieve` / `retrieve_with_ids` 加 `workspace_ids: Sequence[str] | None = None`，三路 `asyncio.to_thread(...)` 调用加 `workspace_ids=workspace_ids`。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --no-sync pytest tests/memory -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/memory/retrieval tests/memory/retrieval
git commit -m "feat(memory): 检索链路按可见集合过滤并在回表侧判定"
```

---

## Task 6（WU-3）: 每轮计算可见集合并注入

**Files:**
- Modify: `nanobot/agent/context.py`、`nanobot/agent/loop.py`、`nanobot/agent/tools/memory_search.py`
- Test: `tests/agent/test_memory_visibility.py`（新建）、`tests/memory/test_memory_search_tool.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/agent/test_memory_visibility.py
from nanobot.agent.loop import AgentLoop


def test_visibility_includes_global_and_project() -> None:
    ids = AgentLoop._memory_workspace_ids_for_path("/tmp/proj-a")
    assert ids[0] == "default"
    assert len(ids) == 2


def test_visibility_without_path_is_global_only() -> None:
    assert AgentLoop._memory_workspace_ids_for_path(None) == ("default",)
```

```python
# tests/memory/test_memory_search_tool.py 追加
def test_tool_scopes_retrieval_to_session_workspace() -> None:
    import asyncio
    from nanobot.agent.tools.memory_search import MemorySearchTool

    seen: dict[str, object] = {}

    class _Engine:
        async def retrieve(self, *, query, recent_messages=None, max_tokens=500, workspace_ids=None):
            seen["workspace_ids"] = workspace_ids
            return "命中"

    tool = MemorySearchTool(
        retrieval_engine_provider=lambda: _Engine(),
        sessions=None,
        workspace_ids_provider=lambda key: ("default", "ws-a"),
    )
    result = asyncio.run(tool.execute(query="创作"))
    assert result == "命中"
    assert seen["workspace_ids"] == ("default", "ws-a")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-sync pytest tests/agent/test_memory_visibility.py tests/memory/test_memory_search_tool.py -q`
Expected: FAIL — `AttributeError: 'MemorySearchTool' object has no attribute ... workspace_ids_provider`

- [ ] **Step 3: 实现**

`nanobot/agent/loop.py`：

1) import：

```python
from nanobot.memory.provenance import memory_visibility_ids, memory_workspace_id
```

2) 新增方法：

```python
    @staticmethod
    def _memory_workspace_ids_for_path(path: str | None) -> tuple[str, ...]:
        """路径 → 本轮可见的记忆隔离键（全局 + 所属工作区）。"""
        return memory_visibility_ids(memory_workspace_id(path))

    def _memory_workspace_ids_for_turn(self, ctx: TurnContext) -> tuple[str, ...]:
        scope = self.workspace_scopes.for_turn(
            channel=ctx.msg.channel,
            message_metadata=ctx.msg.metadata,
            session_metadata=ctx.session.metadata if ctx.session is not None else None,
        )
        return self._memory_workspace_ids_for_path(str(scope.project_path))

    def _memory_workspace_ids_for_key(self, session_key: str) -> tuple[str, ...]:
        """工具侧按会话 key 解析可见集合。

        只读**内存缓存**里的会话（``get_cached``）：这是热路径，绝不能为拿归属
        而新增一次磁盘读；缓存未命中时退化为全局集合。
        """
        session = self.sessions.get_cached(session_key) if self.sessions is not None else None
        if session is None:
            return ("default",)
        scope = session.metadata.get("workspace_scope")
        project_path = scope.get("project_path") if isinstance(scope, dict) else None
        return self._memory_workspace_ids_for_path(
            project_path if isinstance(project_path, str) else None
        )
```

3) `_compute_retrieval_section` 增加 `workspace_ids` 形参并转发：

```python
    async def _compute_retrieval_section(
        self,
        *,
        query: str,
        recent_messages: list[dict[str, Any]],
        workspace_ids: Sequence[str] | None = None,
    ) -> tuple[str, list[str]]:
        ...
            return await self.context._build_memory_section_with_ids(
                query=query,
                recent_messages=recent_messages,
                workspace_ids=workspace_ids,
            )
```

4) `_build_turn` 里调用处：

```python
        (ctx.pending_retrieved_memory_section, ctx.pending_cited_memory_ids) = (
            await self._compute_retrieval_section(
                query=ctx.msg.content,
                recent_messages=list(ctx.history),
                workspace_ids=self._memory_workspace_ids_for_turn(ctx),
            )
        )
```

5) 工具注册处 `attributes` 增加解析器：

```python
            attributes={
                "retrieval_engine": self._retrieval_engine,
                "memory_workspace_ids": self._memory_workspace_ids_for_key,
            },
```

`nanobot/agent/context.py`：

```python
    async def _build_memory_section_with_ids(
        self,
        *,
        query: str,
        recent_messages: list[Any],
        workspace_ids: Sequence[str] | None = None,
    ) -> tuple[str, list[str]]:
        ...
            retrieved = await self._retrieval_engine.retrieve_with_ids(
                query=prepared.cleaned_query,
                recent_messages=recent_messages,
                workspace_ids=workspace_ids,
            )
```

`nanobot/agent/tools/memory_search.py`：

```python
    def __init__(
        self,
        retrieval_engine_provider: Callable[[], "RetrievalEngine | None"],
        sessions: "SessionManager | None" = None,
        workspace_ids_provider: Callable[[str], Sequence[str]] | None = None,
    ) -> None:
        self._get_engine = retrieval_engine_provider
        self._sessions = sessions
        self._workspace_ids_provider = workspace_ids_provider
```

```python
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        def provider() -> "RetrievalEngine | None":  # noqa: F401
            attrs = getattr(ctx, "attributes", {})
            return attrs.get("retrieval_engine")

        def workspace_ids_provider(session_key: str) -> tuple[str, ...]:
            resolve = getattr(ctx, "attributes", {}).get("memory_workspace_ids")
            if resolve is None:
                return ("default",)
            return tuple(resolve(session_key))

        return cls(
            retrieval_engine_provider=provider,
            sessions=getattr(ctx, "sessions", None),
            workspace_ids_provider=workspace_ids_provider,
        )
```

```python
        try:
            result = await engine.retrieve(
                query=query,
                recent_messages=recent,
                max_tokens=max_tokens,
                workspace_ids=self._workspace_ids_for(session_key),
            )
            return result if result else ""
        except Exception:
            return ""

    def _workspace_ids_for(self, session_key: str | None) -> tuple[str, ...]:
        if self._workspace_ids_provider is None or not session_key:
            return ("default",)
        try:
            return tuple(self._workspace_ids_provider(session_key))
        except Exception:
            return ("default",)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --no-sync pytest tests/agent tests/memory -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/agent/loop.py nanobot/agent/context.py nanobot/agent/tools/memory_search.py tests/agent tests/memory
git commit -m "feat(agent): 每轮按工作区计算记忆可见集合并注入检索"
```

---

## Task 7（WU-4）: 面板读写与归属口径

**Files:**
- Modify: `nanobot/webui/memory_api.py`、`nanobot/webui/memory_presentation.py`、`nanobot/webui/memory_routes.py`
- Test: `tests/webui/test_memory_api.py`、`tests/webui/test_memory_presentation_api.py`、`tests/webui/test_memory_routes.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/webui/test_memory_api.py 追加
def test_list_includes_workspace_memories(services) -> None:
    from nanobot.webui.memory_api import list_memories_payload

    payload = list_memories_payload(services)
    assert {item["workspace_id"] for item in payload["items"]} >= {"default", "ws-a"}


def test_fetch_workspace_memory_is_not_404(services) -> None:
    from nanobot.webui.memory_api import fetch_memory_payload

    payload = fetch_memory_payload(services, "ws-memory")
    assert payload["memory"]["workspace_id"] == "ws-a"


def test_create_memory_defaults_to_global(services) -> None:
    from nanobot.webui.memory_api import create_memory

    payload = create_memory(services, content="手动新增")
    assert payload["memory"]["workspace_id"] == "default"


def test_create_memory_can_target_a_known_workspace(services) -> None:
    from nanobot.webui.memory_api import create_memory

    payload = create_memory(services, content="手动新增到项目", workspace="ws-a")
    assert payload["memory"]["workspace_id"] == "ws-a"


def test_create_memory_rejects_unknown_workspace(services) -> None:
    import pytest
    from nanobot.webui.memory_api import create_memory
    from nanobot.webui.settings_contracts import WebUISettingsError

    with pytest.raises(WebUISettingsError):
        create_memory(services, content="手动新增", workspace="ws-unknown")


def test_update_memory_can_reassign_workspace(services) -> None:
    from nanobot.webui.memory_api import update_memory

    payload = update_memory(services, "ws-memory", workspace="default")
    assert payload["memory"]["workspace_id"] == "default"
    assert payload["memory"]["workspaces"] == []


def test_update_memory_rejects_unknown_workspace(services) -> None:
    import pytest
    from nanobot.webui.memory_api import update_memory
    from nanobot.webui.settings_contracts import WebUISettingsError

    with pytest.raises(WebUISettingsError):
        update_memory(services, "ws-memory", workspace="ws-unknown")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-sync pytest tests/webui/test_memory_api.py -q`
Expected: FAIL — `fetch_memory_payload` 对 `ws-a` 行返回 404（守卫仍按 store 标签比较）

- [ ] **Step 3: 实现**

`nanobot/webui/memory_api.py`：

1) `_read_presentation` 去掉 store 标签过滤：

```python
def _read_presentation(
    services: MemoryServices,
) -> tuple[list[Memory], list[Episode], PresentationContext]:
    with services.database.connect() as conn:
        rows = list_memories(conn)
        episodes = [ep for ep in list_episodes(conn) if episode_in_store(ep, services)]
    return rows, episodes, PresentationContext(services, episodes)
```

2) 详情/改/删守卫（`fetch_memory_payload`、`update_memory`、`delete_memory`、`fetch_episode_payload`、`update_episode`、`delete_episode`）中的
`row.workspace_id != services.workspace_id` 一律删除；单库部署下"服务绑定的数据范围"就是本库本身。

3) `episode_payload` 补 `workspace_id`：

```python
        "workspace_id": getattr(ep, "workspace_id", "default"),
```

4) `create_memory` 归属解析（全局或工作区，见 D7）：

```python
    with services.database.connect() as conn:
        known = known_workspace_ids(conn)
    if workspace in (None, "", "unassigned", "default"):
        row_workspace_id = "default"
        selected_workspaces: list[dict[str, str]] = []
    elif workspace in known:
        row_workspace_id = workspace
        selected_workspaces = [option for option in options if option["id"] == workspace]
    else:
        raise WebUIMemoryError("invalid workspace")
```

并把 `Memory(... workspace_id=services.workspace_id ...)` 改为 `workspace_id=row_workspace_id`。

5) **归属可改（D8）**：`update_memory` 增加 `workspace: str | None = None` 参数。传入时：

```python
    if workspace is not None:
        with services.database.connect() as conn:
            known = known_workspace_ids(conn)
        if workspace in ("", "unassigned", "default"):
            new_workspace_id = "default"
            new_workspaces: list[dict[str, str]] = []
        elif workspace in known:
            new_workspace_id = workspace
            new_workspaces = [option for option in options if option["id"] == workspace]
        else:
            raise WebUIMemoryError("invalid workspace")
        kwargs["workspace_id"] = new_workspace_id
        # provenance 快照同步：列与展示口径必须一致（D4）
        provenance = dict(existing_metadata.get("provenance") or {})
        provenance["workspaces"] = new_workspaces
        merged = dict(existing_metadata)
        merged["provenance"] = provenance
        kwargs["metadata"] = merged
```

`repository.update_memory` 需支持 `workspace_id` 形参并落到 `SET workspace_id = :workspace_id`（SQL 用具名参数，不拼串）。

`nanobot/webui/memory_routes.py` 的 `memory-update` 分支透传 `workspace=payload.get("workspace")`。

6) `stats_payload` 的 `list_memories(conn, workspace_id=services.workspace_id)` 改为 `list_memories(conn)`（统计全量）。

`nanobot/webui/memory_presentation.py`：

1) `workspace_options` 增加固定全局项，并补上「库中出现但无快照」的工作区 id：

```python
def workspace_options(
    services: MemoryServices, items: list[dict[str, Any]],
) -> list[dict[str, str]]:
    options: dict[str, dict[str, str]] = {
        "default": {"id": "default", "name": "全局记忆", "path": ""},
    }
    for item in items:
        for workspace in item["workspaces"]:
            options[workspace["id"]] = workspace
    with services.database.connect() as conn:
        for workspace_id in sorted(known_workspace_ids(conn)):
            if workspace_id not in options:
                options[workspace_id] = {
                    "id": workspace_id, "name": f"未登记工作区 {workspace_id[:8]}", "path": "",
                }
    return sorted(options.values(), key=lambda workspace: (workspace["name"].casefold(), workspace["path"]))
```

（取消原「默认工作区」项——按 D2 该概念已不存在。）

2) `filter_result` 的工作区筛选改按 `workspace_id`：

```python
    if workspace:
        items = [item for item in items if item.get("workspace_id") == workspace]
```

合法值集合改为 `{option["id"] for option in options}`（含 `"default"`）。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --no-sync pytest tests/webui -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/webui/memory_api.py nanobot/webui/memory_presentation.py tests/webui
git commit -m "feat(webui): 记忆面板按工作区列读写并暴露归属"
```

---

## Task 8（WU-4）: 工作台记忆项带归属

**Files:**
- Modify: `nanobot/webui/session_workbench.py`
- Test: `tests/webui/test_session_workbench.py`（若不存在则新建）

- [ ] **Step 1: 写失败测试**

```python
def test_workbench_memory_items_carry_workspace(services, session, operations) -> None:
    from nanobot.webui.session_workbench import session_workbench_payload

    payload = session_workbench_payload(session, memory_operations=operations)
    for item in payload["memories"]:
        assert "workspace_id" in item
        assert "is_global" in item
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-sync pytest tests/webui/test_session_workbench.py -q`
Expected: FAIL — KeyError: 'workspace_id'

- [ ] **Step 3: 实现**

在 `session_workbench.py` 的两条记忆构造处补字段：

```python
                if content:
                    memories.append({"id": "working:" + session.key, "kind": "working",
                                     "title": _text(row.get("current_focus"), 100) or "工作记忆",
                                     "content": content, "source": "session_scratchpad",
                                     "updated_at": _text(row.get("updated_at"), 80) or None,
                                     "persisted": True,
                                     "workspace_id": "default", "is_global": True})
```

```python
                    if row is not None and _text(row.get("content")):
                        workspace_id = _text(row.get("workspace_id"), 64) or "default"
                        memories.append({"id": _text(row.get("id"), 100), "kind": "long_term",
                                         "title": _text(row.get("content"), 80),
                                         "content": _text(row.get("content")),
                                         "source": "episode:" + _text(episode.get("id"), 100),
                                         "updated_at": _text(row.get("updated_at"), 80) or None,
                                         "persisted": True,
                                         "workspace_id": workspace_id,
                                         "is_global": workspace_id == "default"})
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --no-sync pytest tests/webui -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/webui/session_workbench.py tests/webui
git commit -m "feat(webui): 工作台记忆项补充工作区归属"
```

---

## Task 9（WU-5）: 前端筛选与标签

**Files:**
- Modify: `webui/src/lib/types.ts`、`webui/src/components/settings/memory/MemoryPresentation.tsx`、`webui/src/components/settings/memory/MemoryListView.tsx`、`webui/src/components/settings/memory/EpisodeListView.tsx`
- Test: `webui/src/tests/`（相关既有测试）

- [ ] **Step 1: 写失败测试**

```tsx
// webui/src/tests/memory-workspace-filter.test.tsx
it("工作区筛选包含全局记忆项", async () => {
  render(<MemoryFilters
    query="" onQuery={() => {}}
    origin="" onOrigin={() => {}}
    workspace="" onWorkspace={() => {}}
    result={{ items: [], total: 0, source_counts: { all: 0, ai: 0, manual: 0, unknown: 0 },
      workspace_options: [{ id: "default", name: "全局记忆", path: "" }], scope: "current_store" }}
    loading={false} />);
  expect(screen.getByRole("option", { name: "全局记忆" })).toBeTruthy();
});

it("编辑态可以选择归属工作区并提交", async () => {
  const updateMemory = vi.fn().mockResolvedValue(undefined);
  vi.mock("@/lib/api", () => ({ updateMemory, createMemory: vi.fn() }));
  render(<MemoryEditDialog
    memory={{ id: "m-1", content: "旧内容", workspace_id: "ws-a" } as MemoryPayload}
    onClose={() => {}} onSaved={() => {}}
    workspaces={[{ id: "ws-a", name: "项目A", path: "/tmp/a" }, { id: "default", name: "全局记忆", path: "" }]}
    initialWorkspace="ws-a" />);
  await userEvent.selectOptions(screen.getByLabelText("工作区"), "default");
  await userEvent.click(screen.getByRole("button", { name: /保存|Save/ }));
  expect(updateMemory).toHaveBeenCalledWith(expect.anything(), "m-1", expect.objectContaining({ workspace: "default" }));
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd webui && bun run test memory-workspace-filter`
Expected: FAIL — 编辑态找不到「工作区」下拉；`updateMemory` 未收到 `workspace`

- [ ] **Step 3: 实现**

- `webui/src/lib/types.ts`：`MemoryWorkspace` 增加可选 `path?: string`；`MemoryPayload` 增加 `workspace_id?: string`；`EpisodePayload` 增加 `workspace_id?: string`；`updateMemory` 的入参类型增加 `workspace?: string`。
- `webui/src/lib/api.ts`：`updateMemory` 透传 `workspace` 字段（body 里带 `workspace`，缺省不发）。
- `MemoryPresentation.tsx`：`MemoryFilters` 的下拉第一项保持「全部工作区」，其余由 `workspace_options` 渲染（后端已提供「全局记忆」项与各工作区项）。
- `MemoryListView.tsx` / `EpisodeListView.tsx`：筛选值继续用 `workspace` 参数直传（`'default'` 即全局）。
- `MemoryEditDialog.tsx`（**归属可改 D8**）：
  - 工作区 `<select>` 在**新增与编辑两种态都渲染**；编辑态初值取 `memory.workspace_id`（缺省 `"default"`）。
  - 选项语义：`<option value="default">全局记忆</option>` + `workspaces.map(...)`（含各工作区）；**移除**原来的空值「默认工作区」项与 `unassigned` 项（`unassigned` 与 `default` 语义重复）。
  - 保存时：`memory` 分支传 `workspace`（始终传当前选择，未改动也传当前归属，保持幂等）；新增分支维持传 `workspace`。
  - `<select>` 需有可访问名称（`aria-label` 或 `<label>`），测试用 `getByLabelText("工作区")` 定位。
- `MemoryListView.tsx`：编辑弹窗的 `initialWorkspace` 改为 `editing?.workspace_id ?? workspace`（编辑已有记忆时带出其当前归属，而不是当前筛选值）。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd webui && bun run test`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add webui/src
git commit -m "feat(webui): 记忆筛选支持全局记忆与工作区归属"
```

---

## Task 10: 全量验证

- [ ] **Step 1: Python 静态与单测**

```bash
uv run --no-sync ruff check nanobot/
uv run --no-sync basedpyright
uv run --no-sync pytest tests/memory tests/webui tests/agent -q
```

Expected: 全部通过，无新增类型错误。

- [ ] **Step 2: 前端**

```bash
cd webui && bun run test && bun run build
```

Expected: 测试通过、构建成功。

- [ ] **Step 3: 性能回归（对照 §5 基线）**

用同一脚本复测，确认过滤增量仍在噪声范围内：

```bash
.venv/Scripts/python.exe .ai-runtime-artifacts/research/2026-10-27-memory-recall-bench.py
```

Expected：1k/10k/50k 各档的「有过滤 − 无过滤」差值仍在 ±3ms 量级（50k 档允许更大抖动，但不应出现数量级恶化）。

- [ ] **Step 4: 真机场景验证（手工，需用户参与）**

1. 启动 gateway（`nanobot gateway`）与 WebUI。
2. 新建会话 A，把项目切到目录 `P1`，聊几句并触发记忆提取（会话结束或 idle 抽取）。
3. 新建会话 B，把项目切到目录 `P2`，让 agent 调用 `memory_search` 查刚才那条记忆 → **应查不到**。
4. 在会话 B 里查一条全局记忆（手动新增为全局）→ **应查得到**。
5. 打开设置 → 记忆 → 工作区下拉 → 应出现「全局记忆」与 `P1`；按 `P1` 过滤只出 `P1` 的记忆。
6. 打开会话 A 的工作台 → 记忆页签的长期记忆应带 `P1` 归属标签。

- [ ] **Step 5: 落盘验证产物**

写 `.ai-runtime-artifacts/verifications/2026-10-27-memory-workspace-scoping-verification-lite.md`，记录上述命令的实际输出。

---

## 6. 风险与已知缺口

| 编号 | 风险 / 缺口 | 处置 |
| --- | --- | --- |
| R1 | 工作区目录改名或移动 → 稳定 id **不变**（marker 随目录走），记忆跟随 | 已由 D1/D9 解决；复制目录会轮换新 id，副本不继承原目录记忆 |
| R8 | **用户项目目录会出现 `.nanobot/workspace-id`**（git 里显示 untracked） | 用户已知情选择；文件为隐藏单文件、0o600、内容是随机 id，不含路径明文 |
| R9 | 只读工作区无法写 marker → 回退 path 哈希，该归属是**派生的**，改名后会失联 | 面板对派生归属标注提示；用户可在编辑弹窗里改归属（D8）迁到正式工作区 |
| R10 | 复制检测依赖 `~/.nanobot/workspace-registry/` 注册表；删掉注册表后复制检测失效 | 退化行为：副本与原目录共享 id（记忆互通）。注册表丢失不影响 marker 本身 |
| R2 | 向量路不做库内过滤 → 向量 top-N 中属于其它工作区的候选被丢弃，有效命中可能少于 FTS5 路 | 召回变少而非泄漏；向量层默认关闭（`memory_search_backend=fts5`） |
| R3 | 向量 top-N 被过滤后可能不足 `limit`，注入块变短 | 已知折中，写入验证记录；若实测影响明显再单独评估 |
| R4 | 面板详情/删除守卫放宽（去掉 `workspace_id != services.workspace_id`） | 单库部署下等价于"库内即可访问"；跨库场景不存在（每 gateway 一个库） |
| R5 | `nanobot/memory/lifecycle.py:116` 的 MEMORY.md 派生过滤 `scope="user"`，而生产写入全是 `scope="global"`，导致 MEMORY.md 实际恒空（实测文件仅 3 字节） | **本次不修**，属独立缺陷，不在计划范围 |
| R6 | 画像合并按工作区隔离后，同一 subject+predicate 在不同工作区会产生两条记忆 | 符合隔离语义；跨工作区不互相污染 |
| R7 | FTS5 查询 `ORDER BY rank` 会物化全部命中再排序（`LIMIT` 不裁剪扫描量），5 万行全命中时约 27ms | 既有架构问题，与本次无关；若要优化需单独立项改召回 SQL |
| R11 | 改归属（D8）会改写 `metadata.provenance.workspaces` 快照，与「编辑不改原始来源」的旧契约相反 | 用户复核明确要求可改归属；`store_workspace_id` 与 `trigger_*`/`generation_method` 仍保持原值，只改归属部分 |

## 7. 自检（writing-plans §Self-Review）

- **需求覆盖**：工作区隔离（D1/D9 + Task 1a/1b/2/3）✅；全局 + 工作区都读（D3 + Task 3/5/6）✅；与工作台产出一致（Task 7 面板口径 + Task 8 工作台归属）✅；取消默认工作区概念（D2 + Task 1b 测试）✅；手动新增可选全局或工作区（D7 + Task 7）✅；**归属事后可改（D8 + Task 7 步骤 5 + Task 9 编辑态）**✅；性能评估（§5 + Task 10 Step 3）✅。
- **占位符扫描**：无 TBD/TODO；每步均给出文件、代码与命令。
- **类型一致性**：`workspace_ids` 在 repository → store_adapter → channels → engine → context/loop/tool 全链同名同义（`Sequence[str] | None`）；`memory_visibility_ids` 返回 `tuple[str, ...]`；`memory_workspace_id(path)` 单参；身份 id 为 32 位 hex（`GLOBAL_MEMORY_WORKSPACE_ID='default'` 是唯一的非 hex 值）；`workspace_identity_id(path)` 在 `nanobot/workspace_identity.py`。
- **与既有契约的冲突**：`2026-10-27-contract-memory-presentation.md` 写的是「编辑不改原始来源与项目归属」，与 D8 冲突——已按用户复核意见改写归属规则（只改归属，不动 `trigger_*`/`generation_method`/`store_workspace_id`），需在尾盘 review 时显式记录该偏离。
