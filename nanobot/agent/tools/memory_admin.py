# Tool schemas are installed by the ``@tool_parameters`` class decorator at
# runtime; static analyzers cannot observe that it clears ``parameters`` from
# ``__abstractmethods__`` before these classes are instantiated.
# pyright: reportAbstractUsage=false, reportPrivateUsage=false

"""LLM 可调用的记忆写入工具（P1）。

在此之前模型只有一个 ``memory_search``——它只能查、不能记。于是「记住我
不吃香菜」这类请求的实际效果是模型回答「好的，我记住了」，然后什么都没有
发生。这四个工具补上缺口：

* :class:`AddMemoryTool`    写一条（复用 ``writer.apply_memory_changes``）
* :class:`MemoryDeleteTool` 按查询删一批（两次确认）
* :class:`MemoryStatsTool`  看库里的规模与来源分布
* :class:`ConsolidateMemoryTool` 立刻重算 MEMORY.md / USER.md

三条设计约束，都是 openakita 那边踩过的坑：

1. **工具只写库，不写 md 文件。** MEMORY.md / USER.md 是 ``memories`` 表的
   投影，由 ``MemoryLifecycle`` 渲染。模型拿不到「打印机」的开关，格式、长度、
   去重才统一可控。
2. **删除必须两次确认。** ``dry_run=True`` 先列出将删什么并返回 ``confirm_token``，
   带着同一个 token 再 ``dry_run=False`` 才真删。没有 token 一律拒绝——模型
   自行跳过预览属于误删，比不删更糟。
3. **失败要说出原因。** Dream 用的是同一套工具，参数校验报错时如果只回
   「失败」，它会原样重试整批，白烧 token。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable, ClassVar
from uuid import uuid4

from loguru import logger

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import (
    ToolContext,
    current_request_context,
    current_request_session_key,
)
from nanobot.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    NumberSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.memory.dream_audit import active_run
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.writer import MemoryChange, apply_memory_changes

if TYPE_CHECKING:
    from nanobot.webui.memory_services import MemoryServices

#: ``add_memory`` 的合法记忆类型（与 ``models.MemoryType`` 同源）。
_MEMORY_TYPES: tuple[str, ...] = tuple(t.value for t in MemoryType)

#: 保留时长：``long_term`` 长期有效；``session`` 24 小时后自动失效
#: （复用 ``expires_at``，``current_memory_sql`` 已经会把它判为无效）。
_RETENTION_VALUES: tuple[str, ...] = ("long_term", "session")

#: 临时记忆的默认有效期。
_SESSION_TTL = timedelta(hours=24)

#: 删除预览的默认条数上限。
_DELETE_PREVIEW_LIMIT = 10


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _MemoryAdminBase(Tool):
    """四个记忆工具的公共部分。

    服务来源在**构造时**定死（实例属性），不从每轮请求上下文里现取：
    Dream 走 ``process_direct``，主 Agent 走消息总线，两条路径的上下文形状
    不一样；把服务挂实例上，两条路都只需要在构造时给对值。
    """

    def __init__(
        self,
        services: "MemoryServices | Callable[[], MemoryServices | None] | None" = None,
        *,
        source: str = "manual",
        trigger: str = "tool",
    ) -> None:
        self._services_holder = services
        #: 写入 ``memories.source`` 的值；Dream 覆写成 ``consolidation``。
        self.source_label = source
        #: ``provenance.trigger_reason``；Dream 覆写成 ``dream``。
        self.trigger_label = trigger

    # -- 装配 -------------------------------------------------------------

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        attrs = getattr(ctx, "attributes", {}) or {}
        return cls(
            attrs.get("memory_services"),
            source=str(attrs.get("memory_write_source", "manual")),
            trigger=str(attrs.get("memory_write_trigger", "tool")),
        )

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        """记忆总开关关着时，工具根本不出现（与 ``memory_search`` 同模式）。"""
        attrs = getattr(ctx, "attributes", {}) or {}
        if attrs.get("memory_file_mode"):
            # 文件模式：记忆本体是文件，模型用 write_file/edit_file 直接写，
            # 再走记忆库写入工具就是双写（模型说不清哪份是真的）。
            return False
        gate = attrs.get("memory_write_enabled")
        if callable(gate):
            try:
                return bool(gate())
            except Exception:  # noqa: BLE001 - 门控异常按关闭处理
                return False
        # 没有门控（Dream 自建上下文）：有服务就可用。
        return attrs.get("memory_services") is not None

    # -- 公共辅助 ---------------------------------------------------------

    def _services(self) -> "MemoryServices | None":
        holder = self._services_holder
        if holder is None:
            return None
        if callable(holder):
            try:
                return holder()
            except Exception as exc:  # noqa: BLE001 - 解析失败不当成工具崩溃
                logger.warning("memory tool: services resolution failed: {!r}", exc)
                return None
        return holder

    def _fail(self, message: str) -> ToolResult:
        return ToolResult.error(f"记忆操作失败：{message}")

    def _is_dream(self) -> bool:
        return self.trigger_label == "dream"

    def _provenance(self, workspace_id: str) -> dict[str, Any]:
        """写库时附带的来源信息（面板的「来源 / 触发方式」徽标读它）。"""
        is_dream = self._is_dream()
        provenance: dict[str, Any] = {
            "origin_kind": "ai" if is_dream else "manual",
            "generation_method": "ai" if is_dream else "manual",
            "trigger_mode": "automatic" if is_dream else "manual",
            "trigger_reason": self.trigger_label,
        }
        ctx = current_request_context()
        if ctx is not None:
            if ctx.session_key:
                provenance["session_id"] = ctx.session_key
            if ctx.turn_id:
                provenance["turn_id"] = ctx.turn_id
        return {"provenance": provenance}

    def _refresh_derived(self, services: "MemoryServices") -> None:
        """写库后立刻重算两个投影文件（失败只记日志）。"""
        try:
            from nanobot.memory.lifecycle import MemoryLifecycle

            lifecycle = MemoryLifecycle.for_workspace(services.workspace_id, services)
            lifecycle.refresh_derived_sync(
                services.workspace_id, trigger=self.trigger_label
            )
        except Exception as exc:  # noqa: BLE001 - 派生失败不当成写入失败
            logger.warning("memory tool: derived refresh failed: {!r}", exc)

    def _record_audit(self, services: "MemoryServices", kind: str) -> None:
        """把真实写入决策累加进活跃的 Dream 运行（主 Agent 的写入不计数）。"""
        audit = active_run(services.workspace_id)
        if audit is None:
            return
        if kind == "added":
            audit.record_added()
        elif kind == "updated":
            audit.record_updated()
        else:
            audit.record_skipped()


# ---------------------------------------------------------------------------
# add_memory
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        content=StringSchema(
            "要记住的内容。一条一个事实，写成完整句子，例如「用户不吃香菜」。"
            "不要写任务流水账（做了什么、改了哪个文件）——那些由对话记录承载。",
            min_length=1,
            max_length=2000,
        ),
        type=StringSchema(
            "记忆类型：preference=用户偏好，rule=用户定的规则，fact=客观事实，"
            "skill=可复用做法，error=踩过的坑，experience=经验总结。默认 fact。",
            enum=_MEMORY_TYPES,
        ),
        retention=StringSchema(
            "保留时长。long_term=长期有效（默认）；用户说「永久记住 / 以后都要 / "
            "每次都要」时必须显式传 long_term。session=只在本次对话附近有效，"
            "24 小时后自动失效，用于「先记一下待会儿用」。",
            enum=_RETENTION_VALUES,
        ),
        importance=NumberSchema(
            description=(
                "重要性 0~1。用户明确要求记住的偏好/规则建议 0.8 以上，"
                "闲聊中提到的一般事实 0.5 左右即可。默认 0.6。"
            ),
            minimum=0.0,
            maximum=1.0,
        ),
        subject=StringSchema(
            "主语，留空表示「用户」。项目/工具/技术名也可以，例如「知序」「pnpm」。",
            max_length=100,
        ),
        predicate=StringSchema(
            "属性名，例如 称呼、包管理器、饮食禁忌、沟通风格。同主语同属性的"
            "纠正会自动更新旧值，不会堆成两条。",
            max_length=100,
        ),
        required=["content"],
    )
)
class AddMemoryTool(_MemoryAdminBase):
    """写入一条长期记忆。"""

    name: ClassVar[str] = "add_memory"
    description: ClassVar[str] = (
        "把一条值得长期记住的信息写进记忆库。用户说「记住…」「以后都…」「"
        "我更喜欢…」或你判断某条偏好/规则/事实对后续协作用得着时调用。"
        "写完后 MEMORY.md / USER.md 会自动重算，不需要也不允许你去改这两个文件。"
        "同一内容重复调用只会强化已有记录，不会产生重复行。"
    )

    async def execute(
        self,
        *,
        content: str,
        type: str = "fact",
        retention: str = "long_term",
        importance: float = 0.6,
        subject: str = "",
        predicate: str = "",
        **kwargs: Any,
    ) -> str:
        services = self._services()
        if services is None:
            return self._fail("记忆库不可用（记忆功能可能已关闭）")
        content = (content or "").strip()
        if not content:
            return self._fail("content 不能为空")

        try:
            memory_type = MemoryType(type)
        except ValueError:
            return self._fail(f"type 必须是 {', '.join(_MEMORY_TYPES)} 之一，收到 {type!r}")

        now = _now_iso()
        priority = MemoryPriority.LONG_TERM
        expires_at: str | None = None
        if retention == "session":
            priority = MemoryPriority.SHORT_TERM
            expires_at = (datetime.now(timezone.utc) + _SESSION_TTL).isoformat()

        memory = Memory(
            id=uuid4().hex,
            content=content,
            created_at=now,
            updated_at=now,
            type=memory_type,
            priority=priority,
            source=self.source_label,
            importance_score=float(importance),
            subject=(subject or "用户").strip(),
            predicate=(predicate or "").strip(),
            scope="global",
            scope_owner=services.workspace_id,
            workspace_id=services.workspace_id,
            expires_at=expires_at,
            metadata=self._provenance(services.workspace_id),
        )

        # 幂等键：同一轮里重试同一条内容不重复入库（writer 的证据键机制）。
        session_key = current_request_session_key() or ""
        ctx = current_request_context()
        turn_id = ctx.turn_id if ctx is not None else None
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        evidence_key = (
            f"{self.trigger_label}:{session_key}:{turn_id or 'x'}:{digest}"
        )

        change = MemoryChange(
            memory=memory,
            action="add",
            # 用户明确要求记住 ⇒ 显式候选，跳过短文本门槛。
            explicit=True,
        )
        try:
            with services.database.connect() as conn:
                result = apply_memory_changes(
                    conn, [change], evidence_key=evidence_key
                )
        except Exception as exc:  # noqa: BLE001 - 落库失败要带原因返回
            # 注意：本方法的入参名叫 ``type``，内置 ``type()`` 已被遮住，
            # 取异常类型名要走 ``__class__.__name__``。
            reason = exc.__class__.__name__
            logger.warning("add_memory write failed: {}: {!r}", reason, exc)
            return self._fail(f"写入记忆库失败：{reason}")

        outcome = result.outcomes[0] if result.outcomes else None
        kind = outcome.kind if outcome is not None else "skipped"
        reason = outcome.reason if outcome is not None else "no outcome"
        self._record_audit(services, kind)

        for created in result.created:
            try:
                from nanobot.memory.vector.indexer import index_memory_best_effort

                index_memory_best_effort(created)
            except Exception as exc:  # noqa: BLE001 - 索引失败不影响写入结果
                logger.debug("add_memory index skipped: {!r}", exc)

        if kind in {"added", "updated"}:
            self._refresh_derived(services)
        return self._describe(kind, reason, memory_type.value)

    def _describe(self, kind: str, reason: str, type_value: str) -> str:
        if kind == "added":
            return f"已记住（新增一条 {type_value} 记忆），MEMORY.md / USER.md 已同步。"
        if kind == "updated":
            return "已记住（更新了已有的同一条记忆，未新增行）。"
        if kind == "unchanged":
            return "这条已经在记忆库里了，没有重复添加。"
        if kind == "suppressed":
            return (
                "这条记忆此前被用户删除过，已按遗忘登记拒绝重新写入"
                f"（{reason}）。请告诉用户这条已删，不要再记。"
            )
        return f"未写入（{kind}：{reason}）。"


# ---------------------------------------------------------------------------
# memory_delete
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        query=StringSchema(
            description=(
                "要删除的记忆关键词或原文片段。会按全文检索找出候选，"
                "只删当前有效的记录。"
            ),
            min_length=1,
            max_length=200,
        ),
        dry_run=BooleanSchema(
            description=(
                "True=只预览将删哪些、不真删（默认）；False=真删，"
                "且必须带上同一批预览返回的 confirm_token。"
            ),
            default=True,
        ),
        confirm_token=StringSchema(
            description=(
                "dry_run=True 返回的确认令牌。dry_run=False 时必须原样带上，"
                "否则一律拒绝。"
            ),
            max_length=64,
        ),
        limit=IntegerSchema(
            description="最多处理几条（默认 10，上限 50）。",
            minimum=1,
            maximum=50,
        ),
        required=["query"],
    )
)
class MemoryDeleteTool(_MemoryAdminBase):
    """按查询删除记忆；两次确认。"""

    name: ClassVar[str] = "memory_delete"
    description: ClassVar[str] = (
        "删除用户要求删掉的记忆。删除是破坏性操作：先 dry_run=True 拿到候选列表和 "
        "confirm_token，把列表给用户看过之后，再带同一个 token 以 dry_run=False "
        "真删。没有 token 的删除请求会被拒绝。用户说「把刚才那条删了」「你记错了」"
        "时使用。"
    )

    async def execute(
        self,
        *,
        query: str,
        dry_run: bool = True,
        confirm_token: str = "",
        limit: int = _DELETE_PREVIEW_LIMIT,
        **kwargs: Any,
    ) -> str:
        services = self._services()
        if services is None:
            return self._fail("记忆库不可用（记忆功能可能已关闭）")
        query = (query or "").strip()
        if not query:
            return self._fail("query 不能为空")

        try:
            matches = self._candidates(services, query, limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory_delete search failed: {}: {!r}", type(exc).__name__, exc)
            return self._fail(f"检索候选记忆失败：{type(exc).__name__}")

        if not matches:
            return f"没有找到与「{query}」匹配的有效记忆，未删除任何内容。"

        if dry_run:
            return self._preview(services, query, matches)

        expected = self._token(services.workspace_id, query, matches)
        if not confirm_token:
            return ToolResult.error(
                "拒绝删除：dry_run=False 时必须带上 dry_run=True 返回的 confirm_token。"
                "请先用 dry_run=True 预览并取得用户确认。"
            )
        if confirm_token.strip() != expected:
            return ToolResult.error(
                "拒绝删除：confirm_token 与候选列表不匹配（查询条件或结果已变化）。"
                "请重新 dry_run=True 取得新 token。"
            )
        return self._apply_delete(services, matches)

    # -- 内部 -------------------------------------------------------------

    def _candidates(
        self, services: "MemoryServices", query: str, limit: int
    ) -> list[Memory]:
        from nanobot.memory.repository import search_memories

        with services.database.connect() as conn:
            matches = search_memories(
                conn,
                query,
                workspace_id=services.workspace_id,
                limit=limit,
                include_inactive=False,
            )
        if matches:
            return matches
        # FTS 对纯中文单字或不规范词形不敏感时返回空，补一次原文包含匹配，
        # 否则「删掉香菜那条」会因 FTS 不命中被误判成「没有这条记忆」。
        return self._fallback_substring(services, query, limit)

    def _fallback_substring(
        self, services: "MemoryServices", query: str, limit: int
    ) -> list[Memory]:
        from nanobot.memory.repository import list_memories

        with services.database.connect() as conn:
            rows = list_memories(
                conn,
                workspace_id=services.workspace_id,
                order_by="importance",
                limit=200,
                include_inactive=False,
            )
        needle = query.lower()
        return [m for m in rows if needle in (m.content or "").lower()][:limit]

    def _preview(
        self, services: "MemoryServices", query: str, matches: list[Memory]
    ) -> str:
        token = self._token(services.workspace_id, query, matches)
        lines = [f"将删除 {len(matches)} 条记忆："]
        lines.extend(f"  {i}. [{m.type.value}] {m.content}" for i, m in enumerate(matches, 1))
        lines.append("")
        lines.append(
            "请把以上列表给用户确认。确认后带上 confirm_token 再次调用，并设 dry_run=False："
        )
        lines.append(f"confirm_token={token}")
        return "\n".join(lines)

    def _apply_delete(self, services: "MemoryServices", matches: list[Memory]) -> str:
        from nanobot.memory.repository import delete_memory, get_memory

        deleted = 0
        for memory in matches:
            try:
                with services.database.connect() as conn:
                    if get_memory(conn, memory.id) is None:
                        continue
                    delete_memory(conn, memory.id)
                deleted += 1
            except Exception as exc:  # noqa: BLE001 - 单条失败不放弃其余
                logger.warning("memory_delete failed for {}: {!r}", memory.id, exc)

        if deleted:
            audit = active_run(services.workspace_id)
            if audit is not None:
                audit.record_deleted(deleted)
            try:
                from nanobot.memory.vector.indexer import remove_memory_best_effort

                for memory in matches:
                    remove_memory_best_effort(memory.id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("memory_delete vector cleanup skipped: {!r}", exc)
            self._refresh_derived(services)
        return f"已删除 {deleted} 条记忆。MEMORY.md / USER.md 已同步更新。"

    @staticmethod
    def _token(workspace_id: str, query: str, matches: list[Memory]) -> str:
        """把「工作区 + 查询 + 候选 id 集合」绑成一个短令牌。

        候选集一变 token 就变：模型拿第一次预览的 token 去删一批已经不同的记录，
        会被挡下来。
        """
        ids = ",".join(sorted(m.id for m in matches))
        digest = hashlib.sha256(f"{workspace_id}|{query}|{ids}".encode("utf-8")).hexdigest()
        return digest[:16]


# ---------------------------------------------------------------------------
# memory_stats
# ---------------------------------------------------------------------------


@tool_parameters(tool_parameters_schema())
class MemoryStatsTool(_MemoryAdminBase):
    """记忆库的规模与来源分布。"""

    name: ClassVar[str] = "memory_stats"
    description: ClassVar[str] = (
        "查看记忆库的统计信息：总条数、按类型分布、按来源分布、"
        "MEMORY.md 上次刷新时间、最近几次 Dream 整理的结果。"
        "用户问「你记了多少东西」「最近整理过吗」时调用。"
    )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        services = self._services()
        if services is None:
            return self._fail("记忆库不可用（记忆功能可能已关闭）")

        from nanobot.memory.dream_audit import list_recent_runs
        from nanobot.memory.lifecycle import MemoryLifecycle
        from nanobot.memory.repository import list_memories

        try:
            with services.database.connect() as conn:
                rows = list_memories(
                    conn, workspace_id=services.workspace_id, limit=5000
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory_stats failed: {}: {!r}", type(exc).__name__, exc)
            return self._fail(f"读取统计失败：{type(exc).__name__}")

        by_type: dict[str, int] = {}
        by_source: dict[str, int] = {}
        active = 0
        for memory in rows:
            by_type[memory.type.value] = by_type.get(memory.type.value, 0) + 1
            by_source[memory.source] = by_source.get(memory.source, 0) + 1
            if not memory.superseded_by:
                active += 1

        lines = [
            f"记忆库共 {len(rows)} 条（当前有效 {active} 条）。",
            "按类型：" + "、".join(f"{k} {v}" for k, v in sorted(by_type.items())) + "。",
            "按来源：" + "、".join(f"{k} {v}" for k, v in sorted(by_source.items())) + "。",
        ]

        last_refresh = MemoryLifecycle._last_refresh_iso.get(services.workspace_id)
        if last_refresh:
            lines.append(f"MEMORY.md 上次刷新：{last_refresh}。")

        runs = list_recent_runs(services.database, services.workspace_id, limit=3)
        if runs:
            lines.append("最近整理：")
            for run in runs:
                lines.append(
                    f"  {run['started_at']} [{run['status']}] "
                    f"新增 {run['added']} / 更新 {run['updated']} / "
                    f"删除 {run['deleted']} / 跳过 {run['skipped']}"
                    + (f"（{run['error']}）" if run["error"] else "")
                )
        else:
            lines.append("还没有整理记录。")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# consolidate_memory
# ---------------------------------------------------------------------------


@tool_parameters(tool_parameters_schema())
class ConsolidateMemoryTool(_MemoryAdminBase):
    """立刻重算 MEMORY.md / USER.md。"""

    name: ClassVar[str] = "consolidate_memory"
    description: ClassVar[str] = (
        "从记忆库重新生成 MEMORY.md 与 USER.md，让两个投影文件与库保持一致。"
        "用户说「整理一下记忆」「更新一下记忆文件」时调用。"
        "注意：这一步只做重新投影，不会合并重复或衰减旧记忆——"
        "那是每日整理任务的事。"
    )

    async def execute(self, **kwargs: Any) -> str:
        services = self._services()
        if services is None:
            return self._fail("记忆库不可用（记忆功能可能已关闭）")

        from nanobot.memory.lifecycle import MemoryLifecycle

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

        return (
            "整理完成："
            + _describe("MEMORY.md", memory_md)
            + "，"
            + _describe("USER.md", user_md)
            + "。"
        )


def build_memory_admin_tools(
    *,
    services: "MemoryServices | Callable[[], MemoryServices | None] | None",
    source: str = "manual",
    trigger: str = "tool",
) -> list[Tool]:
    """构造一套记忆工具，供主 Agent 与 Dream 共用。

    Args:
        services: 已构造好的 :class:`MemoryServices`，或按需解析它的 callable。
        source: 写入 ``memories.source`` 的值（Dream 传 ``consolidation``）。
        trigger: ``provenance.trigger_reason``（Dream 传 ``dream``）。
    """
    classes: tuple[type[_MemoryAdminBase], ...] = (
        AddMemoryTool,
        MemoryDeleteTool,
        MemoryStatsTool,
        ConsolidateMemoryTool,
    )
    return [
        cls(services, source=source, trigger=trigger) for cls in classes
    ]


__all__ = [
    "AddMemoryTool",
    "ConsolidateMemoryTool",
    "MemoryDeleteTool",
    "MemoryStatsTool",
    "build_memory_admin_tools",
]
