---
artifact: implementation-plan
route: superpowers:writing-plans -> orchestration
skills:
  - writing-plans
  - orchestration
skills_evidence:
  - .claude/skills/writing-plans/SKILL.md（已读，按 Goal/Architecture/Tech Stack + Task 细步 + 自检结构撰写）
  - .claude/skills/orchestration/SKILL.md（编排纪律见 harness-kit/core/orchestration/dispatcher-workflow.md）
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - harness-kit/core/artifacts.md
  - .ai-runtime-artifacts/specs/2026-11-05-memory-injection-redesign-spec.md
  - .ai-runtime-artifacts/specs/2026-11-05-memory-injection-redesign-overview.md
  - C:/Users/Huangqh/.qoder-cn/projects/D--studyspace-project-Zhixu/memory/project-memory-injection-redesign.md
created_at: 2026-11-05
status: approved
approved: true
approval_quote: 用户原话「写计划然后实现」「继续写计划然后实现」
dispatch: .ai-runtime-artifacts/plans/2026-11-05-memory-injection-redesign-dispatch.md
---

# 记忆注入改造实施计划（文件为本体，SQLite 退为检索索引）

> **For agentic workers:** REQUIRED SUB-SKILL: 按 `orchestration` 调度，逐 WU 执行；每步用 checkbox 跟踪。

**Goal:** 把长期记忆从「SQLite 投影」改成「文件即本体」：全局 `~/.nanobot/memory/` 与项目 `~/.nanobot/projects/<id>/memory/` 两级目录，
每轮注入两份 `MEMORY.md` 索引（Qoder 式一行一条），模型按需 `read_file` 读正文；SQLite 只服务 `memory_search`。

**Architecture:** 新增文件层模块 `nanobot/memory/files.py`（读写真相源 + 索引维护 + 写锁），注入侧在 `context.py` 增加「索引块」并停掉
`_load_core_hits` 的每轮注入；写入侧改由模型用 `write_file`/`edit_file` 直接写记忆文件（工具白名单放开两个记忆目录），后台抽取与 Dream 走同一套
文件写入函数，SQLite 写入降级为「同步一条索引记录」。

**Tech Stack:** Python 3.11+ / asyncio / Pydantic（`nanobot/config/schema.py`）/ Jinja2 模板（`nanobot/templates/`）/ pytest（`asyncio_mode=auto`）。

---

## 0. 与 spec 的两处修正（核验后更新，实现按此执行）

| spec 表述 | 核验结论 | 实现口径 |
| --- | --- | --- |
| `identity/catalog.py:112` 把 MEMORY.md 列入 `MEMORY_DIR_DERIVED_FILES`（连同 USER.md） | `MEMORY_DIR_DERIVED_FILES` 只有 `{"MEMORY.md"}`；`USER.md` 真身在 `identity/`，被**刻意排除**在记忆目录之外 | USER.md 不迁入记忆目录；写保护只针对 `identity/USER.md` 与 `identity/MEMORY.md` |
| 「注入分三段，各有字符预算」 | 三段（工作记忆 / 核心事实 / 检索命中）共用**一份** `MEMORY_CONTEXT_MAX_TOKENS = 700`（`nanobot/agent/context.py:63`） | 索引块**独立预算**（`MEMORY_INDEX_MAX_CHARS = 2000`），不占用 700 token 记忆预算 |

其余 spec 代码事实核验全部成立：`_load_core_hits`（`context.py:278`）、USER.md bootstrap 注入（`context.py:548-552`）、
`add_memory` 描述含「不允许改 MEMORY.md / USER.md」（`memory_admin.py:234`）、`loop.py:863` 建抽取工厂未传 `idle_seconds`、
`LLMMemoryItem` 无 `confidence` 字段（`extractor.py:308-318`）。

## 1. 关键设计决策

| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 文件格式用 YAML frontmatter（`name` / `description` / `metadata.type`）+ 正文 | 与 Qoder 记忆一致，模型可读可改；解析只依赖 frontmatter 三键，不引第三方 YAML 依赖 |
| D2 | 记忆目录落在 `~/.nanobot/memory/`、`~/.nanobot/projects/<workspace_id>/memory/`，**不进项目工作区** | 避免隐私进 git；`workspace_id` 复用 `memory_workspace_id()`（`nanobot/memory/provenance.py:118`） |
| D3 | 索引行格式 `- [标题](<name>.md) — 一句话钩子`，每目录一份 `MEMORY.md` | 注入侧预算可控（2000 字符），与 Qoder 索引结构一致 |
| D4 | 抽取产物用 `importance` 当门槛（全局 ≥0.7，项目 ≥0.5），不用 `confidence` | `LLMMemoryItem` 没有 `confidence` 字段（`extractor.py:308-318`），DB 层默认 0.5 不可用 |
| D5 | 作用域判定按记忆类型：`preference`/`rule` → 全局；`fact`/`experience`/`error`/`skill` → 项目 | 与 `dream.md` 既有路由语义一致，不新增判定模型 |
| D6 | 空闲抽取在文件模式下**停用**，改由 cron `memory_extract`（默认 30 分钟）驱动 | spec §5.3；空闲触发「用户离开就烧钱、连续聊反而不记」 |
| D7 | 主对话撤下 `add_memory`/`memory_delete`/`memory_stats`/`consolidate_memory`（`enabled()` 在文件模式返回 False），类本身保留给 WebUI/评测 | 避免模型双写；WebUI 与评测路径不受影响 |
| D8 | `memoryFileMode`（默认 true）作为总开关，关闭即回到现有 SQLite 注入路径 | spec §7 回滚要求 |
| D9 | 文件写入用一把模块级 `threading.RLock` 串行化（定时抽取与 Dream 共用） | spec §5.4 并发写冲突 |

## 2. 文件结构映射

**新增**

| 文件 | 职责 |
| --- | --- |
| `nanobot/memory/files.py` | 记忆文件读写、索引维护、目录解析、写锁、scope 判定、迁移 |
| `nanobot/memory/extract_writer.py` | 抽取产物 → 文件的落盘适配（scope + importance 门槛 + 索引刷新） |
| `tests/memory/test_memory_files.py` | 文件层单测（写/读/索引/锁/门槛） |
| `tests/memory/test_memory_injection.py` | 注入侧单测（索引块出现、核心事实退出、回滚开关） |

**修改**

| 文件 | 改动 |
| --- | --- |
| `nanobot/config/schema.py` | 新增 `memory_file_mode`、`memory_extract_interval_minutes` |
| `nanobot/agent/context.py` | 索引块注入；文件模式下停掉核心事实注入 |
| `nanobot/agent/loop.py` | 传 `memory_file_mode` 与记忆目录给工具装配；关闭空闲抽取；暴露定时抽取入口 |
| `nanobot/agent/tools/filesystem.py` | 记忆目录进读/写白名单 |
| `nanobot/agent/tools/memory_admin.py` | 文件模式下不注册；描述文案更新 |
| `nanobot/memory/extractor.py` | 新增可选 `memory_file_writer` 回调（双出口） |
| `nanobot/memory/lifecycle.py` | 文件模式下 USER.md 自动区改由全局记忆文件渲染；不再投影 MEMORY.md |
| `nanobot/agent/memory.py` | Dream 工具集重做（写记忆目录，撤下记忆库工具） |
| `nanobot/cli/gateway_runtime.py` | 注册 `memory_extract` 系统 job 与处理分支 |
| `nanobot/templates/AGENT.md` | 「记忆使用规范」整节重写 + `add_memory` 引用替换 + 身份文件职责表更新 |
| `nanobot/templates/agent/identity.md` | Workspace 段与 Memory feedback 段重写 |
| `nanobot/templates/agent/dream.md` | 整体重写为「文件记忆整理者」 |
| `nanobot/templates/memory/MEMORY.md` | 改为 Qoder 式索引模板 |
| `docs/memory.md` | 架构与目录同步 |

## 3. 执行图（并行边界）

```markdown
GROUP-1（顺序推进，文件有依赖）:
  WU-01: 文件层基础设施 + 配置开关        | 文件: nanobot/memory/files.py, nanobot/config/schema.py | 依赖: 无
  WU-02: 注入改造（索引块 / 退出核心事实）  | 文件: nanobot/agent/context.py                            | 依赖: WU-01
  WU-03: 主动写（工具白名单 + 撤下记忆工具） | 文件: nanobot/agent/tools/filesystem.py, memory_admin.py, nanobot/agent/loop.py | 依赖: WU-01
  WU-04: 提示词重写（AGENT/identity/dream/MEMORY 模板） | 文件: nanobot/templates/**            | 依赖: WU-03
  WU-05: 定时抽取 cron + 双出口            | 文件: nanobot/memory/extract_writer.py, extractor.py, loop.py, gateway_runtime.py | 依赖: WU-01, WU-03
  WU-06: Dream 改造（工具集 + 审计）        | 文件: nanobot/agent/memory.py                             | 依赖: WU-01, WU-03
  WU-07: USER.md 自动区切源 + 生命周期退役   | 文件: nanobot/memory/lifecycle.py                          | 依赖: WU-01
  WU-08: 迁移 + 回滚 + 文档 + 端到端验收     | 文件: nanobot/memory/files.py, docs/memory.md, tests/**   | 依赖: WU-01..WU-07
```

> 并行可行性：WU-04（纯模板）与 WU-02/WU-03（代码）文件不相交，可并行；其余存在数据流依赖，顺序推进。

---

## Task 1（WU-01）：文件层基础设施与配置开关

**Files:**
- Create: `nanobot/memory/files.py`
- Modify: `nanobot/config/schema.py:296-329`（`AgentDefaults`）
- Test: `tests/memory/test_memory_files.py`

- [ ] **Step 1：写失败测试**（`tests/memory/test_memory_files.py`）

```python
def test_write_memory_creates_file_and_index(tmp_path):
    store = MemoryFileStore(global_dir=tmp_path / "g", project_dir=tmp_path / "p")
    store.write_memory("global", "prefer-direct", "解释机制先讲因果", description="用户讨厌绕弯", type="user")
    files = store.list_memories("global")
    assert [f.name for f in files] == ["prefer-direct"]
    assert files[0].type == "user"
    assert "prefer-direct.md" in store.read_index("global")
```

- [ ] **Step 2：跑测试确认失败**：`uv run --no-sync python -m pytest tests/memory/test_memory_files.py -q` → FAIL（模块不存在）
- [ ] **Step 3：实现 `nanobot/memory/files.py`**

```python
"""记忆文件层：文件是记忆本体，索引是目录。

每个作用域（global / project）一个目录，目录里一条记忆一个 md 文件，
外加一份 ``MEMORY.md`` 索引。写入方（对话内主动写、定时抽取、Dream）
共用本模块的函数，避免出现第二个整份覆盖的写手。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path

from nanobot.memory.provenance import GLOBAL_MEMORY_WORKSPACE_ID

GLOBAL_SCOPE = "global"
PROJECT_SCOPE = "project"

#: 索引注入的字符上限（两份索引合计）。
MEMORY_INDEX_MAX_CHARS = 2000
#: 写入全局目录的 importance 门槛。
GLOBAL_IMPORTANCE_THRESHOLD = 0.7
#: 写入项目目录的 importance 门槛。
PROJECT_IMPORTANCE_THRESHOLD = 0.5

_MEMORY_TYPES = ("user", "feedback", "project", "reference")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_WRITE_LOCK = threading.RLock()


@dataclass(frozen=True)
class MemoryFile:
    """一条记忆文件。"""

    name: str
    path: Path
    description: str
    type: str
    content: str


def global_memory_dir() -> Path:
    """全局记忆目录：``~/.nanobot/memory/``。"""
    return Path.home() / ".nanobot" / "memory"


def project_memory_dir(workspace_id: str) -> Path:
    """项目记忆目录：``~/.nanobot/projects/<workspace_id>/memory/``。"""
    key = (workspace_id or "").strip() or GLOBAL_MEMORY_WORKSPACE_ID
    return Path.home() / ".nanobot" / "projects" / key / "memory"
```

（其余方法：`parse_frontmatter` / `render_frontmatter` / `MemoryFileStore.list_memories` /
`read_memory` / `write_memory` / `delete_memory` / `read_index` / `rebuild_index` /
`upsert_index_entry` / `scope_for_memory` / `write_extracted_memories`，
以及 `migrate_legacy_memory_dir()` 迁移函数。实现须遵守：
① 同 `name` 追加正文而非覆盖；② 索引行追加到末尾；③ 写入持 `_WRITE_LOCK`；④ 索引重建只读文件元信息。）

- [ ] **Step 4：配置开关**

```python
    memory_file_mode: bool = Field(
        default=True,
        validation_alias=AliasChoices("memoryFileMode", "memory_file_mode"),
        serialization_alias="memoryFileMode",
    )  # 文件即记忆本体；关闭后回到旧的 SQLite 注入路径
    memory_extract_interval_minutes: int = Field(
        default=30,
        ge=5,
        validation_alias=AliasChoices("memoryExtractIntervalMinutes", "memory_extract_interval_minutes"),
        serialization_alias="memoryExtractIntervalMinutes",
    )  # 定时抽取间隔（分钟）
```

- [ ] **Step 5：跑测试 + lint**：`pytest tests/memory/test_memory_files.py -q` → PASS；`ruff check nanobot/` → clean
- [ ] **Step 6：提交**：`git add nanobot/memory/files.py nanobot/config/schema.py tests/memory/test_memory_files.py && git commit -m "feat(memory): 新增记忆文件层与文件模式配置开关"`

## Task 2（WU-02）：注入改造——索引块进 prompt，核心事实退出

**Files:**
- Modify: `nanobot/agent/context.py:278-297`（`_load_core_hits`）、`:299-341`（`_compose_memory_block`）、`:403-483`（`build_system_prompt`）
- Test: `tests/memory/test_memory_injection.py`

- [ ] **Step 1：写失败测试**：文件模式下 system prompt 含两份索引文本与目录绝对路径；不含核心事实渲染；`memory_file_mode=False` 时行为与改造前一致。
- [ ] **Step 2：跑测试确认失败**
- [ ] **Step 3：实现**

```python
    def _load_memory_index_block(self) -> str:
        """两份 MEMORY.md 索引（全局 + 当前项目）。索引是目录，不是内容。"""
        if not self._memory_file_mode:
            return ""
        from nanobot.memory.files import MemoryFileStore, render_index_block

        store = MemoryFileStore.for_project(self.workspace)
        return render_index_block(store)
```

`_compose_memory_block` 中核心事实改为条件注入：

```python
        core_hits = self._load_core_hits() if include_memory and not self._memory_file_mode else []
```

`build_system_prompt` 在 `memory_block` 之后追加索引块（目录先于内容检索，模型先知道去哪找）：

```python
        index_block = self._load_memory_index_block()
        if index_block:
            parts.append(index_block)
```

- [ ] **Step 4：跑测试 + lint**
- [ ] **Step 5：提交**

## Task 3（WU-03）：主动写——白名单放开 + 撤下记忆库工具

**Files:**
- Modify: `nanobot/agent/tools/filesystem.py:82-104`、`nanobot/agent/tools/memory_admin.py:108-119`、`:231-236`、`nanobot/agent/loop.py:1099-1131`
- Test: `tests/memory/test_memory_file_tools.py`

- [ ] **Step 1：写失败测试**：`write_file` 能写 `~/.nanobot/memory/x.md`、不能写 `~/.nanobot/config.json`；文件模式下主对话工具表里没有 `add_memory`。
- [ ] **Step 2：跑测试确认失败**
- [ ] **Step 3：实现白名单**

```python
        # 文件模式下：记忆目录是模型的合法写入目标（记忆文件本体）。
        memory_dirs = memory_write_dirs(ctx) if ctx.attributes.get("memory_file_mode") else []
        return cls(
            workspace=agent_workspace,
            allowed_dir=allowed_dir,
            extra_read_allowed_dirs=[BUILTIN_SKILLS_DIR, resolved_agent_workspace / "skills", *memory_dirs],
            extra_write_allowed_dirs=memory_dirs,
            ...
        )
```

> 注意 `nanobot/agent/tools/path_utils.py:19`：`extra_roots` 只在 `allowed_dir` 非空时生效；
> 主对话 `allowed_dir` 在 `restrict_to_workspace=False` 时为 `None`，此时需由
> `resolve_allowed_path` 的 `extra_allowed_roots` 直通分支兜住（实现时以
> `nanobot/security/workspace_policy.py::resolve_allowed_path` 实际分支为准，写测试锁死行为）。

- [ ] **Step 4：撤下记忆库工具**（`memory_admin.py::enabled`）

```python
        if attrs.get("memory_file_mode"):
            # 文件模式：记忆本体是文件，模型直接写文件，不再走记忆库写入工具。
            return False
```

- [ ] **Step 5：`loop.py` 装配**：`_register_default_tools` 的 `attributes` 增加
`"memory_file_mode": self._memory_file_mode` 与 `"memory_dirs": ...`。
- [ ] **Step 6：跑测试 + lint + 提交**

## Task 4（WU-04）：提示词重写

**Files:**
- Modify: `nanobot/templates/AGENT.md:193-199`（记忆使用规范）、`:47`、`:74`、`:78`、`:83-85`、`:99`、`:110`（`add_memory` 引用）、`:220-227`（身份文件职责表）
- Modify: `nanobot/templates/agent/identity.md:7-25`（Workspace）、`:27-30`（Memory feedback）
- Modify: `nanobot/templates/agent/dream.md`（整体重写）
- Modify: `nanobot/templates/memory/MEMORY.md`（索引模板）

- [ ] **Step 1：`AGENT.md`「记忆使用规范」整节重写**：写明两个目录绝对路径、单条记忆的文件格式、
  何时写（仅用户明确要求或确认）、禁写内容（代码规范 / 架构 / 文件路径 / git 历史 / AGENTS.md 已有内容 / 临时进度）、
  写完必须同步更新同目录 `MEMORY.md` 索引；`memory_search` 保留（搜历史）。
- [ ] **Step 2：`AGENT.md` 其它 `add_memory` 引用改为「写记忆文件」**（`:47`、`:74`、`:78`、`:83-85`、`:99`、`:110`）。
- [ ] **Step 3：身份文件职责表更新**：USER.md = 手写区（用户维护）+ 自动区（系统维护）；MEMORY.md = 索引（目录）。
- [ ] **Step 4：`identity.md` Workspace 段重写**：记忆主载体 = 两个记忆目录（给绝对路径）；
  `MEMORY.md` 从「SQLite 导出」改为「索引」；删除 `add_memory` 说明；Memory feedback 段 Dream 描述改为「文件整理者」。
- [ ] **Step 5：`dream.md` 整体重写**：身份 = 文件记忆整理者；路由表 = 全局文件 / 项目文件 / SKILL.md；
  新增逐候选判 scope、文件合并/替换/修剪、索引维护、importance 门槛；保留 MECE / delete-or-keep / 报告纪律；删除所有 `add_memory` 说明与「不能写 md」禁令。
- [ ] **Step 6：`templates/memory/MEMORY.md` 改索引模板**（标题 + 目录说明 + 一行一条占位）。
- [ ] **Step 7：验证**：`uv run --no-sync python -m pytest tests/identity tests/config -q` + 渲染冒烟
  （`render_template("agent/dream.md")` 无未替换占位）
- [ ] **Step 8：提交**

## Task 5（WU-05）：定时抽取（cron 30 分钟）+ 文件/库双出口

**Files:**
- Create: `nanobot/memory/extract_writer.py`
- Modify: `nanobot/memory/extractor.py`（新增可选 `memory_file_writer` 回调）、`nanobot/agent/hooks/memory_extraction.py`（文件模式停用空闲计时）、`nanobot/agent/loop.py`（暴露 `run_scheduled_memory_extraction`）、`nanobot/cli/gateway_runtime.py:1056-1065` 附近（注册 job 与分支）
- Test: `tests/memory/test_scheduled_extraction.py`

- [ ] **Step 1：写失败测试**：抽取批次落库后，文件目录出现对应条目且索引更新；importance <0.7 的事实不进全局目录；<0.5 不进项目目录。
- [ ] **Step 2：跑测试确认失败**
- [ ] **Step 3：`extract_writer.py`**

```python
def write_extracted_memories(
    store: MemoryFileStore,
    memories: Sequence[Memory],
    *,
    workspace_id: str,
) -> list[Path]:
    """把抽取产物写进记忆文件（scope + importance 门槛），返回写入路径。"""
```

scope 判定：类型 `preference`/`rule` → 全局；`fact`/`experience`/`error`/`skill` → 项目；
门槛：全局 `importance >= 0.7`，项目 `>= 0.5`。
- [ ] **Step 4：`extractor.py` 双出口**：`MemoryExtractor.__init__` 增加 `memory_file_writer: Callable[[list[Memory], Session], None] | None = None`；
  在 `process_request` 事务提交后调用（失败只 warning，不影响抽取结果）。
- [ ] **Step 5：空闲抽取停用**：文件模式下 `MemoryExtractionHook._arm_idle_timer` 直接返回（保留类与旧路径供回滚）。
- [ ] **Step 6：cron 注册**（`gateway_runtime.py`）

```python
        cron.register_system_job(CronJob(
            id="memory_extract",
            name="memory_extract",
            schedule=CronSchedule(kind="every", every_ms=extract_cfg.memory_extract_interval_minutes * 60_000),
            payload=CronPayload(kind="system_event"),
        ))
```

  处理分支：`if job.name == "memory_extract":` → `await agent.run_scheduled_memory_extraction()`，
  异常只记日志（后台任务不打断 gateway）。
- [ ] **Step 7：`AgentLoop.run_scheduled_memory_extraction()`**：遍历 `self.sessions.list_sessions()` →
  `load(key)` → 交给 extractor（复用 `run_idle_extraction` 的批次协议，但 source=`scheduled`）→
  写文件；返回处理会话数。
- [ ] **Step 8：跑测试 + lint + 提交**

## Task 6（WU-06）：Dream 改造——文件整理者

**Files:**
- Modify: `nanobot/agent/memory.py:654-714`（`build_dream_tools`）、`nanobot/cli/gateway_runtime.py:759-760`（审计）
- Modify: `nanobot/templates/agent/dream.md`（Task 4 已重写）
- Test: `tests/memory/test_dream_file_tools.py`

- [ ] **Step 1：写失败测试**：Dream 工具集里没有 `add_memory`/`memory_delete`/`memory_stats`/`consolidate_memory`；
  `write_file`/`edit_file` 能写两个记忆目录；`read_file` 能读记忆目录。
- [ ] **Step 2：跑测试确认失败**
- [ ] **Step 3：`build_dream_tools` 重做**：读工具加记忆目录；写工具 `allowed_dir=skills_dir` + `extra_write_allowed_dirs=[全局记忆目录, 项目记忆目录]`；
  撤下四个记忆库工具。
- [ ] **Step 4：审计改文件 diff 驱动**：新增/修改/删除按记忆目录文件 diff 统计，写入 `memory_dream_runs`。
- [ ] **Step 5：跑测试 + lint + 提交**

## Task 7（WU-07）：USER.md 自动区切源

**Files:**
- Modify: `nanobot/memory/lifecycle.py:1008-1085`（`_render_user_export` / `_render_user_md`）
- Test: `tests/memory/test_lifecycle.py`

- [ ] **Step 1：写失败测试**：文件模式下 USER.md 自动区内容来自全局记忆文件；手写区保持不变；
  `memory_file_mode=False` 时回到 SQLite 来源。
- [ ] **Step 2：跑测试确认失败**
- [ ] **Step 3：实现**：文件模式下把全局记忆文件解析成 `Memory` 记录，复用 `_render_user_md` 三分区渲染；
  文件模式不再写 `memory/MEMORY.md` 投影（保留函数，改为 no-op + 日志）。
- [ ] **Step 4：跑测试 + lint + 提交**

## Task 8（WU-08）：迁移、回滚、文档与端到端验收

**Files:**
- Modify: `nanobot/memory/files.py`（`migrate_legacy_memory_dir`）、`docs/memory.md`
- Test: `tests/memory/test_memory_migration.py`

- [ ] **Step 1：写失败测试**：旧 `~/.nanobot/workspace/memory/MEMORY.md` 迁移后备份到 `memory/legacy/`，
  全局索引出现迁移条目；迁移幂等（跑两次不重复）。
- [ ] **Step 2：跑测试确认失败**
- [ ] **Step 3：实现迁移**：解析旧 MEMORY.md 的 `zhixu:core-view` / `core-note` 区块，
  索引区块转成全局记忆文件，手写笔记转成一条全局记忆；旧文件备份。
- [ ] **Step 4：`docs/memory.md` 同步**：单库 + 文件索引架构、目录结构、写入分工表、回滚开关。
- [ ] **Step 5：端到端验收**（对照 spec §8）：

| spec §8 验收项 | 验证方式 |
| --- | --- |
| 新会话首轮含 USER.md + 两份索引 + 目录路径；不含核心事实 | `tests/memory/test_memory_injection.py` |
| 「记住：提交信息用中文」→ 全局记忆文件 + 索引更新 → 下轮可见 | `tests/memory/test_memory_file_tools.py` + 手工冒烟 |
| 项目会话事实 → 只进项目目录 | `tests/memory/test_memory_files.py` |
| 定时抽取：文件有新条目且 `memories` 表有对应记录 | `tests/memory/test_scheduled_extraction.py` |
| Dream：文件层出现合并/修剪，审计计数与 diff 一致 | `tests/memory/test_dream_file_tools.py` |
| `memory_search` 仍可搜历史 | `tests/memory/retrieval/` 全绿 |
| 关闭 `memoryFileMode` 回到旧行为 | `tests/memory/test_memory_injection.py` 回滚用例 |

- [ ] **Step 6：全量验证**：`uv run --no-sync python -m pytest tests/memory tests/identity tests/config -q`、
  `uv run --no-sync ruff check nanobot/`、`uv run --no-sync basedpyright`（若耗时可后置）
- [ ] **Step 7：提交**

---

## 4. 自检

**1. spec 覆盖**：spec §5.1 → Task 2；§5.2 → Task 3+4；§5.3 → Task 5；§5.4 → Task 6；§5.5 session-end → 明确不做（spec 第一版不做）；
§5.6 USER.md → Task 7；§6 提示词清单 → Task 4（`memory_admin.py` 文案在 Task 3 一并改）；§7 迁移与回滚 → Task 8；§8 验收 → Task 8 表。

**2. 占位符扫描**：无 TBD / TODO / 「类似 Task N」；所有步骤给出具体文件路径与验证命令。

**3. 类型一致性**：`MemoryFileStore.list_memories(scope)` / `read_memory(scope, name)` / `write_memory(scope, name, content, *, description, type)` /
`read_index(scope)` 在 Task 1 定义，Task 2/5/6/7 一律按此调用；`memory_file_mode` 字段名在配置、context、loop、工具层一致。

## 5. 风险与回滚

| 风险 | 处置 |
| --- | --- |
| 文件写手冲突（抽取 / Dream / 对话内主动写） | 模块级 `RLock` 串行化；Dream 每 2 小时、抽取每 30 分钟，冲突窗口小 |
| 记忆文件数量膨胀 | 同 `name` 追加 + Dream 合并；索引注入 2000 字上限倒逼收敛 |
| 文件与 SQLite 漂移 | 接受：检索服务历史，注入服务当前事实（spec §9 已定） |
| 主对话可写用户目录 | 仅两个记忆目录 + skills 目录，写白名单由 `extra_write_allowed_dirs` 精确控制 |
| 迁移误伤 | 旧文件备份到 `memory/legacy/`；迁移幂等；`memoryFileMode=false` 一键回滚 |

## 6. 实施结果（2026-11-05 已实现）

| WU | 状态 | 落点 |
| --- | --- | --- |
| WU-01 文件层 + 配置开关 | ✅ | `nanobot/memory/files.py`、`nanobot/config/schema.py`（`memory_file_mode` / `memory_extract_interval_minutes`） |
| WU-02 注入改造 | ✅ | `nanobot/agent/context.py`：`_load_memory_index_block()`，核心事实退出注入 |
| WU-03 主动写 | ✅ | `nanobot/agent/tools/filesystem.py`（记忆目录读写白名单）、`memory_admin.py`（文件模式不注册）、`loop.py`（透传 `memory_file_mode`） |
| WU-04 提示词重写 | ✅ | `templates/AGENT.md`、`templates/agent/identity.md`、`templates/agent/dream.md`、`templates/memory/MEMORY.md` |
| WU-05 定时抽取 + 双出口 | ✅ | `nanobot/memory/extract_writer.py`、`extractor.py`（`set_memory_file_writer`）、`hooks/memory_extraction.py`（空闲触发停用）、`loop.py::run_scheduled_memory_extraction`、`cli/gateway_runtime.py`（cron `memory_extract`） |
| WU-06 Dream 改造 | ✅ | `nanobot/agent/memory.py`（工具集改为文件工具 + 读写记忆目录；审计改文件 diff 驱动）、`cli/gateway_runtime.py`（审计落库） |
| WU-07 USER.md 自动区切源 | ✅（部分） | `nanobot/memory/lifecycle.py`：`file_mode` 下自动区来自全局记忆文件，MEMORY.md 投影停写；WebUI 装配透传 `file_mode` |
| WU-08 迁移 + 回滚 + 文档 | ✅ | `files.py::migrate_legacy_memory_dir`（启动时幂等迁移 + 备份）、`docs/memory.md` 重写 |

### 与 spec 的偏离（已核验原因）

| 项 | spec | 实际 | 原因 |
| --- | --- | --- | --- |
| USER.md 模板文案微调（「来自 SQLite」→「自动整理」） | 要改 | **未改** | 标记文本被 `lifecycle.USER_VIEW_BEGIN` / `USER_NOTE_BEGIN` 按整串匹配解析，改文案会打断手写区提取；改用 `USER_VIEW_INTRO` 承载语义 |
| USER.md 自动区维护方 | 切到文件写入方 | 已切（`file_mode=True` 时） | 旧路径保留在开关关闭时 |
| MEMORY.md 的 SQLite 投影 | 完全退役 | 文件模式下停写，WebUI 面板仍读库 | WebUI 记忆面板需要库视图；停写只影响文件，不再有第二个写手 |
| session-end 装配 | 第一版不做 | 未做 | 与 spec 一致 |

## Next

- 计划已获授权（用户原话「写计划然后实现」）→ 已按 WU-01 → WU-08 顺序实现完毕
- 验证证据见 `.ai-runtime-artifacts/verifications/2026-11-05-memory-injection-redesign-verification.md`
- 调整范围 → 直接改本文件的 Task 拆分
- 调整并行策略 → 改同 stem 的 `*-dispatch.md`
