---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - ~/.agents/skills/writing-plans/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-11-08-memory-agentmd-scope-consolidation-dispatch.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - nanobot/agent/context.py
  - nanobot/memory/decay.py
  - nanobot/cli/gateway_runtime.py
  - nanobot/agent/tools/memory_admin.py
created_at: 2026-11-08
status: implemented
approved: true
---

# 记忆作用域对齐实施计划（AGENTS.md 双层注入 + 全工作区盘账）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让项目作用域真正生效：① AGENTS.md 全局与项目各注入一份；② 每日盘账覆盖库内全部工作区；③ `consolidate_memory` 工具从"只重投影"升级为"先去重/衰减，再重投影"。

**Architecture:** 三处改动彼此独立、文件不相交：`ContextBuilder` 只改 bootstrap 读取源；`decay.py` 新增一个多工作区编排函数并让 cron job 改用它；`memory_admin.py` 在工具内按"全局 + 当前项目"逐个调用现有 `run_consolidation`，再走原有投影刷新。所有工作区隔离仍由 `workspace_id` 承担，不新增存储层。

**Tech Stack:** Python 3.11 / asyncio / sqlite3 / pytest（`asyncio_mode = "auto"`）

**TDD Required:** YES（每个 Task 先写失败测试）

---

## 现状与约束（执行前必读）

| 事实 | 位置 |
| --- | --- |
| 每轮 `workspace=effective_scope.project_path` 传入 `build_transcript` | `nanobot/agent/loop.py:1760-1769` |
| `build_system_prompt` 用 `root = workspace or self.workspace`，再把 `root` 交给 `_load_bootstrap_files` | `nanobot/agent/context.py:403-431` |
| `_load_bootstrap_files` 当前只读 `root/AGENTS.md`，项目生效时全局 AGENTS.md 被丢弃 | `nanobot/agent/context.py:525-564` |
| 出厂模板内容的 AGENTS.md 会被跳过（`_SKIPPABLE_DEFAULTS`） | `nanobot/agent/context.py:147,586` |
| `run_consolidation` 严格按单个 `workspace_id` 执行，运行记录/checkpoint 也按工作区 | `nanobot/memory/decay.py:486-544` |
| cron job `memory_consolidate` 只盘 `services.workspace_id`（`"default"`） | `nanobot/cli/gateway_runtime.py:788-810` |
| `known_workspace_ids(conn)` 返回库内出现过的全部工作区键 | `nanobot/memory/repository.py:1384` |
| 记忆工具的服务固定为全局 `"default"` | `nanobot/agent/loop.py:176,650-676` |
| 当前回合项目作用域可从 contextvar 读取 | `nanobot/security/workspace_access.py:344-352` |
| 路径 → 记忆隔离键稳定映射 | `nanobot/memory/provenance.py:118` |
| `consolidate_memory` 目前只重投影，描述里明确"不会合并重复" | `nanobot/agent/tools/memory_admin.py:602-650` |

**不变量：**

- 不新增存储层，不改表结构，不改 `memory_visibility_ids` 的"全局 + 当前项目"口径。
- 全局 AGENTS.md 始终注入；项目 AGENTS.md 仅在项目根 ≠ agent workspace 时追加。
- 盘账失败必须失败隔离：单个工作区失败不影响其它工作区，也不影响 `MEMORY.md` 重投影。
- `run_consolidation` 的最小时间预算是 30 秒（`max(30, ...)`），测试里不要断言更小的预算。

---

## Task 1: AGENTS.md 全局 + 项目双层注入（WU-A）

**Files:**
- Modify: `nanobot/agent/context.py:525-588`
- Test: `tests/agent/test_context_agents_md_scope.py`（新建）

- [x] **Step 1: 写失败测试**

新建 `tests/agent/test_context_agents_md_scope.py`：

```python
"""AGENTS.md 双层注入：全局始终读，项目作用域存在时再读项目根。"""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.context import ContextBuilder
from nanobot.utils.helpers import load_bundled_template

GLOBAL_MARKER = "GLOBAL-AGENTS-RULE-MARKER"
PROJECT_MARKER = "PROJECT-AGENTS-RULE-MARKER"


def _builder(workspace: Path) -> ContextBuilder:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "memory").mkdir(parents=True, exist_ok=True)
    return ContextBuilder(workspace=workspace, timezone="UTC")


def test_global_agents_md_is_injected_without_project(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    builder = _builder(ws)
    (ws / "AGENTS.md").write_text(GLOBAL_MARKER, encoding="utf-8")

    prompt = builder.build_system_prompt(include_memory=False)

    assert GLOBAL_MARKER in prompt


def test_project_agents_md_is_injected_alongside_global(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    builder = _builder(ws)
    (ws / "AGENTS.md").write_text(GLOBAL_MARKER, encoding="utf-8")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "AGENTS.md").write_text(PROJECT_MARKER, encoding="utf-8")

    prompt = builder.build_system_prompt(workspace=project, include_memory=False)

    assert GLOBAL_MARKER in prompt
    assert PROJECT_MARKER in prompt
    assert "## AGENTS.md (project)" in prompt


def test_same_root_does_not_inject_agents_md_twice(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    builder = _builder(ws)
    (ws / "AGENTS.md").write_text(GLOBAL_MARKER, encoding="utf-8")

    prompt = builder.build_system_prompt(workspace=ws, include_memory=False)

    assert prompt.count(GLOBAL_MARKER) == 1
    assert "## AGENTS.md (project)" not in prompt


def test_factory_template_agents_md_is_skipped(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    builder = _builder(ws)
    template = load_bundled_template("AGENTS.md")
    assert template
    (ws / "AGENTS.md").write_text(template, encoding="utf-8")

    prompt = builder.build_system_prompt(include_memory=False)

    assert "## AGENTS.md" not in prompt
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/agent/test_context_agents_md_scope.py -v`
Expected: `test_project_agents_md_is_injected_alongside_global` FAIL（项目规则没进 prompt）；其余 3 条 PASS。

- [x] **Step 3: 最小实现**

把 `nanobot/agent/context.py:525-588` 两个方法替换为：

```python
    def _load_bootstrap_files(self, workspace: Path | None = None) -> str:
        """Load project instructions plus the agent's global profile files.

        ``AGENTS.md`` 分两层读取：agent workspace 根的全局副本始终注入；
        当回合项目根与 agent workspace 不同时，再追加项目根的副本。其余
        身份文件只解析一次。项目层用独立标题，避免同一份 prompt 里出现两个
        无从区分的 ``## AGENTS.md``。
        """
        parts: list[str] = []
        project_root = workspace or self.workspace
        identity_dir = resolve_identity_dir(self.workspace)
        compiled = read_compiled(self.workspace)

        global_agents = self._render_bootstrap_file(
            "AGENTS.md", self.workspace, compiled, None
        )
        if global_agents:
            parts.append(global_agents)

        same_root = (
            project_root.expanduser().resolve()
            == self.workspace.expanduser().resolve()
        )
        if not same_root:
            project_agents = self._render_bootstrap_file(
                "AGENTS.md",
                project_root,
                compiled,
                None,
                heading="AGENTS.md (project)",
            )
            if project_agents:
                parts.append(project_agents)

        sources: list[tuple[str, Path, str | None]] = [
            # Identity files: prefer identity/ dir, fall back to workspace root for unmigrated old workspaces
            (
                "SOUL.md",
                identity_dir if (identity_dir / "SOUL.md").exists() else self.workspace,
                "identity_core",
            ),
            ("AGENT.md", identity_dir, "agent_behavior"),
            (
                "USER.md",
                identity_dir if (identity_dir / "USER.md").exists() else self.workspace,
                "user_profile_core",
            ),
        ]

        for filename, root, compiled_key in sources:
            section = self._render_bootstrap_file(filename, root, compiled, compiled_key)
            if section:
                parts.append(section)
            if filename == "SOUL.md":
                persona = self._load_persona_section()
                if persona:
                    parts.append(persona)

        return "\n\n".join(parts) if parts else ""

    def _render_bootstrap_file(
        self,
        filename: str,
        root: Path,
        compiled: dict[str, str] | None,
        compiled_key: str | None,
        *,
        heading: str | None = None,
    ) -> str:
        """渲染单个 bootstrap 段；出厂未定制或缺失时返回空串。"""
        title = heading or filename
        if compiled is not None and compiled_key is not None:
            body = compiled.get(compiled_key, "")
            if body.strip():
                return f"## {title}\n\n{body}"
        file_path = root / filename
        if not file_path.exists():
            return ""
        content = file_path.read_text(encoding="utf-8")
        if filename == "SOUL.md" and self._is_template_content(content, "legacy/SOUL.md"):
            content = load_bundled_template("SOUL.md") or content
        if not content.strip():
            return ""
        if filename in self._SKIPPABLE_DEFAULTS and self._is_template_content(content, filename):
            return ""
        return f"## {title}\n\n{content}"
```

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/agent/test_context_agents_md_scope.py -v`
Expected: 4 passed。

再跑回归：`pytest tests/test_context_documents.py tests/test_tool_contextvars.py -v`
Expected: 全绿。

- [x] **Step 5: 提交**

```bash
git add nanobot/agent/context.py tests/agent/test_context_agents_md_scope.py
git commit -m "feat(memory): AGENTS.md 全局与项目双层注入"
```

---

## Task 2: 每日盘账覆盖库内全部工作区（WU-B1）

**Files:**
- Modify: `nanobot/memory/decay.py`（新增 `run_consolidation_for_all_workspaces`）
- Modify: `nanobot/cli/gateway_runtime.py:788-810`
- Test: `tests/memory/test_decay.py`（追加用例）

- [x] **Step 1: 写失败测试**

在 `tests/memory/test_decay.py` 末尾追加（文件顶部补充 `from dataclasses import replace`）：

```python
def test_run_consolidation_for_all_workspaces_covers_each_scope(services):
    other = replace(_mem("另一个工作区两个月没用的普通事实"), workspace_id="other-ws")
    local = replace(_mem("本工作区两个月没用的普通事实"), workspace_id=WS)
    _seed(services, local, other)

    results = decay.run_consolidation_for_all_workspaces(services.database)
    by_ws = dict(results)

    assert set(by_ws) >= {WS, "other-ws", "default"}
    assert by_ws[WS].stats.archived == 1
    assert by_ws["other-ws"].stats.archived == 1


def test_run_consolidation_for_all_workspaces_accepts_explicit_ids(services):
    local = replace(_mem("本工作区两个月没用的普通事实"), workspace_id=WS)
    _seed(services, local)

    results = decay.run_consolidation_for_all_workspaces(
        services.database, workspace_ids=[WS], include_global=False
    )

    assert [ws for ws, _ in results] == [WS]
    assert results[0][1].stats.archived == 1
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/memory/test_decay.py -k all_workspaces -v`
Expected: FAIL with `AttributeError: module 'nanobot.memory.decay' has no attribute 'run_consolidation_for_all_workspaces'`。

- [x] **Step 3: 最小实现**

3a. 在 `nanobot/memory/decay.py` 顶部导入区补：

```python
from collections.abc import Iterable

from nanobot.memory.provenance import GLOBAL_MEMORY_WORKSPACE_ID
from nanobot.memory.repository import current_memory_sql, known_workspace_ids
```

（若 `current_memory_sql` 已在，合并成一行。）

3b. 在 `run_consolidation` 之后追加：

```python
def run_consolidation_for_all_workspaces(
    database: Any,
    *,
    workspace_ids: Iterable[str] | None = None,
    include_global: bool = True,
    dry_run: bool = False,
    time_budget_seconds: int = 1500,
    archive_threshold: float = DECAY_ARCHIVE_THRESHOLD,
    trigger: str = "cron",
    resume: bool = True,
) -> list[tuple[str, ConsolidationReport]]:
    """按工作区逐个盘账，返回 ``(workspace_id, report)`` 列表。

    不传 ``workspace_ids`` 时取库内已出现过的全部工作区键，并按需补上全局
    ``"default"``。总时间预算在本次涉及的工作区之间均分（每个不低于 30 秒，
    与 ``run_consolidation`` 的下限一致），避免工作区一多就整体超时。
    """
    if workspace_ids is None:
        with database.connect() as conn:
            resolved = set(known_workspace_ids(conn))
    else:
        resolved = {ws for ws in workspace_ids if ws}
    if include_global:
        resolved.add(GLOBAL_MEMORY_WORKSPACE_ID)

    ordered = sorted(resolved)
    if not ordered:
        return []
    per_workspace_budget = (
        max(30, int(time_budget_seconds) // len(ordered))
        if time_budget_seconds > 0
        else 0
    )

    results: list[tuple[str, ConsolidationReport]] = []
    for workspace_id in ordered:
        report = run_consolidation(
            database,
            workspace_id=workspace_id,
            dry_run=dry_run,
            time_budget_seconds=per_workspace_budget,
            archive_threshold=archive_threshold,
            trigger=trigger,
            resume=resume,
        )
        results.append((workspace_id, report))
    return results
```

3c. 把 `nanobot/cli/gateway_runtime.py:788-810` 的 job 体替换为：

```python
        # memory_consolidate is an internal job — pure rule pass, no agent loop.
        if job.name == "memory_consolidate":
            from nanobot.memory.decay import run_consolidation_for_all_workspaces

            if not config.agents.defaults.memory_enabled:
                logger.debug("memory_consolidate: memory disabled, skipping")
                return None
            services = agent.context.memory.memory_services
            cfg = config.agents.defaults.consolidate
            try:
                results = run_consolidation_for_all_workspaces(
                    services.database,
                    dry_run=cfg.dry_run,
                    time_budget_seconds=cfg.time_budget_seconds,
                    archive_threshold=cfg.archive_threshold,
                    trigger="cron",
                )
                for workspace_id, report in results:
                    logger.info(
                        "memory_consolidate[{}]: {}", workspace_id, report.summary()
                    )
                    if report.status == "interrupted":
                        logger.warning(
                            "memory_consolidate[{}] hit time budget; will resume from checkpoint",
                            workspace_id,
                        )
            except Exception:
                logger.exception("memory_consolidate job failed")
            return None
```

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/memory/test_decay.py -v`
Expected: 全绿（新增 2 条 + 原有全部）。

- [x] **Step 5: 提交**

```bash
git add nanobot/memory/decay.py nanobot/cli/gateway_runtime.py tests/memory/test_decay.py
git commit -m "feat(memory): 每日盘账覆盖全部工作区"
```

---

## Task 3: `consolidate_memory` 支持去重/过期（WU-B2）

**Files:**
- Modify: `nanobot/agent/tools/memory_admin.py:1-40`（导入）、`:602-650`（工具实现）
- Test: `tests/agent/test_memory_admin_tools.py`（追加用例）

- [x] **Step 1: 写失败测试**

在 `tests/agent/test_memory_admin_tools.py` 的 `TestStatsAndConsolidate` 类里追加（文件顶部补 `from dataclasses import replace`、`from datetime import datetime, timedelta, timezone`、`from nanobot.memory.models import Memory, MemoryPriority, MemoryType`）：

```python
    async def test_consolidate_archives_stale_memories(self, services, tmp_ws):
        stale = _stale_memory(workspace_id=services.workspace_id)
        with services.database.connect() as conn:
            add_memory(conn, stale)

        out = await _tool(ConsolidateMemoryTool, services).execute()

        assert "归档" in out
        out = out + _describe_projection(tmp_ws)
        assert "MEMORY.md" in out

    async def test_consolidate_covers_current_project_scope(
        self, services, monkeypatch, tmp_path
    ):
        from types import SimpleNamespace

        from nanobot.agent.tools import memory_admin

        project = tmp_path / "proj"
        project.mkdir()
        monkeypatch.setattr(
            memory_admin,
            "current_workspace_scope",
            lambda: SimpleNamespace(project_path=project),
        )
        monkeypatch.setattr(memory_admin, "memory_workspace_id", lambda _path: "proj-ws")
        stale = _stale_memory(workspace_id="proj-ws")
        with services.database.connect() as conn:
            add_memory(conn, stale)

        out = await _tool(ConsolidateMemoryTool, services).execute()

        assert "项目" in out
        with services.database.connect() as conn:
            stored = get_memory(conn, stale.id)
        assert stored is not None
        assert stored.expires_at is not None

    def test_consolidation_workspace_ids_dedupe_global(self, services, monkeypatch):
        from nanobot.agent.tools import memory_admin

        monkeypatch.setattr(
            memory_admin,
            "current_workspace_scope",
            lambda: type("S", (), {"project_path": "/tmp/whatever"})(),
        )
        monkeypatch.setattr(
            memory_admin, "memory_workspace_id", lambda _path: services.workspace_id
        )

        assert memory_admin._consolidation_workspace_ids(services) == [services.workspace_id]
```

文件顶部/类外补充两个测试辅助：

```python
def _stale_memory(*, workspace_id: str) -> Memory:
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    return Memory(
        id=f"stale-{workspace_id}",
        content=f"{workspace_id} 两个月没用的普通事实",
        created_at=old,
        updated_at=old,
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        source="manual",
        importance_score=0.3,
        decay_rate=0.1,
        last_accessed_at=None,
        workspace_id=workspace_id,
    )


def _describe_projection(tmp_ws) -> str:
    return "MEMORY.md" if (tmp_ws / "memory" / "MEMORY.md").exists() else ""
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/agent/test_memory_admin_tools.py -k consolidate -v`
Expected: 新用例失败——`test_consolidate_archives_stale_memories` 输出里没有"归档"；`test_consolidate_covers_current_project_scope` 失败于 `memory_admin.current_workspace_scope` 不存在；`_consolidation_workspace_ids` 不存在。

- [x] **Step 3: 最小实现**

3a. `nanobot/agent/tools/memory_admin.py` 顶部导入区补：

```python
import asyncio

from nanobot.memory.decay import run_consolidation
from nanobot.memory.provenance import memory_workspace_id
from nanobot.security.workspace_access import current_workspace_scope
```

3b. 在 `_MemoryAdminBase` 定义之前加常量与辅助函数：

```python
#: 工具触发的盘账单工作区预算（秒）。用户主动说「整理记忆」时不该等太久；
#: ``run_consolidation`` 内部会把下限抬到 30 秒。
_TOOL_CONSOLIDATION_BUDGET_SECONDS = 60


def _consolidation_workspace_ids(services: "MemoryServices") -> list[str]:
    """本次整理要覆盖的工作区：全局 + 当前回合项目（去重，保持顺序）。"""
    ids = [services.workspace_id]
    try:
        scope = current_workspace_scope()
    except Exception:  # noqa: BLE001 - 作用域解析失败按全局处理
        scope = None
    if scope is not None:
        project_id = memory_workspace_id(str(scope.project_path))
        if project_id not in ids:
            ids.append(project_id)
    return ids
```

3c. 替换 `ConsolidateMemoryTool` 的 `description` 与 `execute`：

```python
class ConsolidateMemoryTool(_MemoryAdminBase):
    """立刻盘账（去重/衰减）并重算 MEMORY.md / USER.md。"""

    name: ClassVar[str] = "consolidate_memory"
    description: ClassVar[str] = (
        "整理记忆：先对全局与当前项目做一次去重与过期清理，再从记忆库"
        "重新生成 MEMORY.md 与 USER.md。"
        "用户说「整理一下记忆」「更新一下记忆文件」「清理重复记忆」时调用。"
    )

    async def execute(self, **kwargs: Any) -> str:
        services = self._services()
        if services is None:
            return self._fail("记忆库不可用（记忆功能可能已关闭）")

        from nanobot.memory.lifecycle import MemoryLifecycle

        reports: list[tuple[str, Any]] = []
        for workspace_id in _consolidation_workspace_ids(services):
            try:
                report = await asyncio.to_thread(
                    run_consolidation,
                    services.database,
                    workspace_id=workspace_id,
                    time_budget_seconds=_TOOL_CONSOLIDATION_BUDGET_SECONDS,
                    trigger="tool",
                )
            except Exception as exc:  # noqa: BLE001 - 盘账失败不阻止投影刷新
                logger.warning(
                    "consolidate_memory: consolidation failed for {}: {!r}",
                    workspace_id,
                    exc,
                )
                continue
            reports.append((workspace_id, report))

        try:
            lifecycle = MemoryLifecycle.for_workspace(services.workspace_id, services)
            result = lifecycle.refresh_derived_sync(
                services.workspace_id, trigger=self.trigger_label
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("consolidate_memory failed: {}: {!r}", type(exc).__name__, exc)
            return self._fail(f"整理失败：{type(exc).__name__}")

        memory_md = result.get("memory_md", {})
        user_md = result.get("user_md", {})

        def _describe(label: str, item: dict[str, Any]) -> str:
            if item.get("status") == "skipped":
                return f"{label} 无变化"
            return f"{label} 已重写（{item.get('chars', 0)} 字符）"

        scope_summary = "；".join(
            f"{'全局' if ws == services.workspace_id else '项目'}：{report.summary()}"
            for ws, report in reports
        ) or "盘账未执行"

        return (
            f"整理完成：{scope_summary}。"
            + _describe("MEMORY.md", memory_md)
            + "，"
            + _describe("USER.md", user_md)
            + "。"
        )
```

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/agent/test_memory_admin_tools.py -v`
Expected: 全绿（新增 3 条 + 原有全部）。

再跑回归：`pytest tests/memory/test_decay.py -v`
Expected: 全绿。

- [x] **Step 5: 提交**

```bash
git add nanobot/agent/tools/memory_admin.py tests/agent/test_memory_admin_tools.py
git commit -m "feat(memory): consolidate_memory 同时执行去重与过期清理"
```

---

## 验证口径（三 Task 合并后）

```bash
pytest tests/agent/test_context_agents_md_scope.py tests/agent/test_memory_admin_tools.py tests/memory/test_decay.py tests/test_context_documents.py -v
ruff check nanobot/agent/context.py nanobot/memory/decay.py nanobot/cli/gateway_runtime.py nanobot/agent/tools/memory_admin.py
```

人工确认（可选，需起 gateway）：

1. 有项目作用域的会话里问"这个项目的约定是什么"，模型应同时复述全局 AGENTS.md 与项目 AGENTS.md 的内容。
2. 把会话切到另一个项目，项目规则随项目变化，全局规则不变。
3. 面板/对话触发 `consolidate_memory` 后，`memory_consolidate_runs` 里应出现全局与当前项目两条 run 记录。
4. 每日 job 触发后，`memory_consolidate_runs` 覆盖库内全部 `workspace_id`。

## 计划自检

- [x] 三个目标各有对应 Task，且文件不相交
- [x] 每个 Task 以失败测试开头，含运行命令与预期
- [x] 代码块为可直接落地的完整片段，无占位符
- [x] 类型/函数名一致：`run_consolidation_for_all_workspaces`、`_consolidation_workspace_ids`、`heading=`
- [x] 未改动存储层与表结构

## Next

- 计划确认 → 说「开始实现」或「并行执行」
- 需要调整 → 直接说修改意见（例如 `consolidate_memory` 的作用域改成全部工作区）
