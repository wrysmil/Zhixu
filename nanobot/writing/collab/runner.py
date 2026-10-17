"""协作批次运行器：启动 / 回复 / 确认 / 取消 / 超时 / 重试（契约 §1/§3/§4/§9）。

**职责边界。** 本模块是协作域的编排层：认识配方、冻结上下文、批次、产物与交接，
但**不认识** WebSocket、HTTP 与前端。两个外部依赖走注入：

- :class:`TeamPort` —— 起跑/取消团队运行（由装配层用 TeamService + TeamScheduler 实现）；
- :class:`ArtifactSink` —— 把成员输出落成类型化产物（由 WU-07 的 envelope 适配器实现）。

**并行依赖。** WU-06 的 ``recipe`` / ``preparation`` 一律**延迟 import**（只在默认实现内部），
它们还没落盘时本模块仍可导入、可测试。

**事务口径。** SQLite 事务只由 :class:`~nanobot.writing.collab.repository.CollabRepository`
在方法内部闭合。异步入口用 ``asyncio.to_thread`` 包仓储/文章库调用，**不**让事务跨
LLM/网络等待；:meth:`CollabRunner.on_team_event` 是冻结接口里的同步方法，只做单事务的
小读写、不跨 await。

**收口闸门。** 团队事件必须同时满足「运行仍是本次活动批次」与
``(collaborationId, activeBatchId, attempt, cancelGeneration)`` 四项一致，才允许落产物、
推可采纳事件；旧结果（取消/超时/换代之后到达的完成事件）只记一条 ``warning`` 结构化日志。

**日志。** 批次状态跃迁固定事件名 ``writing_collab_batch_transition``，字段
``collaboration_id/batch_id/attempt/from_status/to_status/cancel_generation``；
**不记录**文章正文或产物全文。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, is_dataclass, replace
from pathlib import Path
from typing import Any, Protocol, cast

from loguru import logger as default_logger

from nanobot.webui.session_identity import WEBUI_SESSION_STORAGE_PREFIX
from nanobot.writing.collab.models import (
    ACTIONS,
    ACTIVE_BATCH_STATUSES,
    IN_FLIGHT_STATUSES,
    Action,
    ArtifactKind,
    Batch,
    BatchStatus,
    Collaboration,
    CollaborationStatus,
    Handoff,
    HandoffKind,
    HandoffStatus,
    normalize_enum,
)
from nanobot.writing.collab.repository import (
    ActiveBatchConflict,
    CollabRepository,
    CollabVersionConflict,
    HandoffNotFound,
    InvalidFieldError,
    NoActiveBatch,
)
from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import snapshot_digest
from nanobot.writing.repository import ArticleConflict, ArticleNotFound, ArticleRepository

__all__ = [
    "DEFAULT_BATCH_TIMEOUT_SECONDS",
    "ArtifactSink",
    "BatchHandle",
    "CollabRunner",
    "CollabSelectionRequired",
    "CollabServiceNotConfigured",
    "ConfirmResult",
    "FrozenPreparer",
    "LiveBatch",
    "RecipeProvider",
    "StartCollabRequest",
    "TeamPort",
    "converge_collaborations",
    "frozen_preparer",
]

#: 契约 §9：单次自动批次 15 分钟。真值在 WU-06 的 ``recipe`` 里，缺失时按此回退。
DEFAULT_BATCH_TIMEOUT_SECONDS = 900.0

#: 这些动作没有选区就没有意义（契约 §4：400 ``collaboration target selection is required``）。
_SELECTION_REQUIRED_ACTIONS = frozenset({Action.REWRITE_SELECTION})

#: 成员任务完成事件：产物出口的唯一入口。
_TASK_FINISH_EVENTS = frozenset({"task.finished"})
#: 运行终态事件：决定批次是完成还是失败。
_RUN_FINISH_EVENTS = frozenset({"run.finished"})

_FROZEN_IDENTITY_KEYS = ("workspace_key", "article_id", "collaboration_id", "batch_id")


class CollabSelectionRequired(ValueError):  # noqa: N818  # 名称对齐契约 §4 的错误语义
    """契约 §4：局部改写等动作缺少选区。"""

    def __init__(self) -> None:
        super().__init__("collaboration target selection is required")
        self.status = 400


class CollabServiceNotConfigured(RuntimeError):  # noqa: N818  # 契约 §4 的 503 文案
    """协作层未装配（缺配方表/冻结准备/模型）：契约 §4 的 503。"""

    def __init__(self, detail: str = "") -> None:
        super().__init__("writing collaboration service is not configured")
        self.status = 503
        self.detail = detail


# ---- 注入端口 ---------------------------------------------------------------


class TeamPort(Protocol):
    """起跑与取消团队运行；返回 team run 标识（``TeamRunSnapshot.run_id``）。"""

    async def start_batch(
        self,
        *,
        collaboration_id: str,
        batch_id: str,
        attempt: int,
        action: str,
        instruction: str,
        session_key: str,
        chat_id: str,
        frozen: Any,
        selection: Mapping[str, Any] | None,
        material_ids: Sequence[str],
    ) -> str: ...

    async def cancel_run(self, run_id: str) -> Any: ...


class ArtifactSink(Protocol):
    """成员输出的落库出口（WU-07 的 envelope 适配器）：返回落库产物或 ``None``。"""

    def ingest(
        self,
        *,
        collaboration_id: str,
        batch_id: str,
        attempt: int,
        event: Any,
    ) -> Any: ...


class RecipeProvider(Protocol):
    """配方表来源；默认实现延迟 import ``nanobot.writing.collab.recipe.RECIPES``。"""

    def __call__(self) -> Mapping[str, Any]: ...


class FrozenPreparer(Protocol):
    """冻结准备端口；默认实现延迟 import ``nanobot.writing.collab.preparation``。"""

    def __call__(
        self,
        *,
        recipe: Any,
        article_snapshot: Mapping[str, Any],
        material_ids: Sequence[str],
        collaboration_id: str,
        workspace_key: str,
        article_id: str,
        batch_id: str,
    ) -> Any: ...


# ---- 请求与结果 -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StartCollabRequest:
    """``writing.start`` 的领域输入（契约 §3；服务端只认这些字段）。"""

    client_request_id: str
    article_id: str
    chat_id: str
    action: str
    base_revision: int
    snapshot_digest: str
    instruction: str = ""
    selection: Mapping[str, Any] | None = None
    material_ids: tuple[str, ...] = ()


#: ``writing.start`` 的字段名单（契约 §3）。payload 层
#: （``writing_collab_api._start_request``）逐字使用同一份名字组装普通 dict。
_START_REQUEST_FIELDS: tuple[str, ...] = (
    "client_request_id",
    "article_id",
    "chat_id",
    "action",
    "base_revision",
    "snapshot_digest",
    "instruction",
    "selection",
    "material_ids",
)


def coerce_start_request(request: Any) -> StartCollabRequest:
    """把 ``StartCollabRequest`` 或等价映射归一成领域输入（契约 §3）。

    payload 层刻意不 import runner 的领域类型（见该模块 docstring），只送普通
    ``dict``；执行栈这边按同一份字段名展开，缺字段或字段名漂移都会立刻
    ``TypeError``，而不是等到运行期才变成 ``AttributeError``（未登记异常会被
    路由层折成 500 通用文案，排查只能靠日志）。
    """
    if isinstance(request, StartCollabRequest):
        return request
    if isinstance(request, Mapping):
        payload = cast("Mapping[str, Any]", request)
        unknown = sorted(set(payload) - set(_START_REQUEST_FIELDS))
        if unknown:
            raise TypeError(f"unknown writing.start field(s): {', '.join(unknown)}")
        known = {name: payload[name] for name in _START_REQUEST_FIELDS if name in payload}
        return StartCollabRequest(**known)
    # 具名对象（鸭子类型）保持原样：下游按属性访问，形状不符照样炸在调用点。
    return cast("StartCollabRequest", request)


@dataclass(frozen=True, slots=True)
class BatchHandle:
    """``writing.start`` 的成功结果（契约 §3）。"""

    collaboration_id: str
    batch_id: str
    status: str
    stage: str
    session_key: str
    active_batch_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "collaboration_id": self.collaboration_id,
            "batch_id": self.batch_id,
            "status": self.status,
            "stage": self.stage,
            "session_key": self.session_key,
            "active_batch_id": self.active_batch_id,
        }


@dataclass(frozen=True, slots=True)
class ConfirmResult:
    """``writing.confirm`` 的成功结果（契约 §3）。"""

    collaboration_id: str
    batch_id: str
    article_revision: int
    article_digest: str
    handoff_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "collaboration_id": self.collaboration_id,
            "batch_id": self.batch_id,
            "article_revision": self.article_revision,
            "article_digest": self.article_digest,
            "handoff_status": self.handoff_status,
        }


@dataclass(frozen=True, slots=True)
class LiveBatch:
    """活动的批次句柄（运行标识只活在进程内，见 ``team_run_id`` 的持久化缺口）。"""

    collaboration_id: str
    batch_id: str
    attempt: int
    team_run_id: str
    cancel_generation: int


@dataclass
class _BatchRef:
    """运行 → 批次的进程内绑定：闸门用它核对事件身份与丢弃代数。"""

    collaboration_id: str
    batch_id: str
    attempt: int
    team_run_id: str
    started_generation: int
    current_generation: int
    live: bool = True


# ---- 默认注入实现（延迟 import WU-06） ---------------------------------------


def _load_default_recipes() -> Mapping[str, Any]:
    try:
        from nanobot.writing.collab.recipe import RECIPES
    except ImportError as error:  # 并行 WU-06 还没落盘
        raise CollabServiceNotConfigured(f"recipe table is unavailable: {error}") from error
    return RECIPES


def _batch_timeout_seconds() -> float:
    try:
        from nanobot.writing.collab.recipe import BATCH_TIMEOUT_SECONDS
    except ImportError:
        return DEFAULT_BATCH_TIMEOUT_SECONDS
    return float(BATCH_TIMEOUT_SECONDS)


def frozen_preparer(
    *,
    loader: Any,
    model_id: Callable[[], str | None] | str | None,
) -> FrozenPreparer:
    """默认冻结准备：把 WU-06 的 ``prepare_frozen`` 包成本模块的端口。

    ``loader`` 是技能加载器（AgentStore/SkillsLoader 侧的适配），``model_id`` 可以是
    取当回合默认模型的回调：换模型后装配期抓的那一份会过期，所以默认按回调取值。
    """

    def prepare(
        *,
        recipe: Any = None,
        article_snapshot: Mapping[str, Any] | None = None,
        material_ids: Sequence[str] = (),
        collaboration_id: str = "",
        workspace_key: str = "",
        article_id: str = "",
        batch_id: str = "",
    ) -> Any:
        try:
            from nanobot.writing.collab.preparation import prepare_frozen
        except ImportError as error:
            raise CollabServiceNotConfigured(
                f"frozen preparation is unavailable: {error}"
            ) from error
        resolved = model_id() if callable(model_id) else model_id
        if not resolved:
            raise CollabServiceNotConfigured("writing model is not available")
        return prepare_frozen(
            loader=loader,
            recipe=recipe,
            article_snapshot=dict(article_snapshot or {}),
            material_ids=list(material_ids or ()),
            model_id=str(resolved),
            # 四个身份都是 ``prepare_frozen`` 的必填 kw-only（契约 §1 的 frozen 形状）。
            # 批次的 ``batch_id`` 在冻结时尚未生成，落库那一份由 ``_with_identity`` 补，
            # 这里按空串传入即可（``FrozenContext`` 只持有、不校验非空）。
            workspace_key=str(workspace_key or ""),
            article_id=str(article_id or ""),
            collaboration_id=str(collaboration_id or ""),
            batch_id=str(batch_id or ""),
        )

    return cast("FrozenPreparer", prepare)


def converge_collaborations(workspace: str | Path) -> int:
    """装配期启动收敛：把在途批次与协作置 ``interrupted``，已交付产物一律保留。

    进程重启后本机没有任何活着的子进程/句柄，重放只会二次执行带副作用的工具调用，
    因此这里只收敛、不重放。
    """
    database = WritingDatabase(workspace)
    repository = CollabRepository(database)
    return repository.converge_interrupted(database.workspace_key)


# ---- 运行器 -----------------------------------------------------------------


class CollabRunner:
    """一个 workspace 一个实例；由 gateway 装配层持有，不通过模块全局读取。"""

    def __init__(
        self,
        *,
        repository: CollabRepository,
        workspace_key: str,
        articles: ArticleRepository,
        team: TeamPort,
        artifacts: ArtifactSink,
        recipes: RecipeProvider | Mapping[str, Any] | None = None,
        preparer: FrozenPreparer | None = None,
        publisher: Callable[[str, Mapping[str, Any]], None] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        logger: Any = default_logger,
        converge: bool = True,
    ) -> None:
        self._repository = repository
        self._workspace_key = workspace_key
        self._articles = articles
        self._team = team
        self._artifacts = artifacts
        self._recipes = recipes
        self._preparer = preparer
        self._publisher = publisher
        self._sleep = sleep or asyncio.sleep
        self._logger = logger
        self._runs: dict[str, _BatchRef] = {}
        self._by_batch: dict[str, _BatchRef] = {}
        self._generations: dict[str, int] = {}
        self._deadlines: dict[str, asyncio.Task[Any]] = {}
        self._releases: set[asyncio.Task[Any]] = set()
        self._started_batches: set[str] = set()
        if converge:
            # 装配期收敛（契约 §6/§7）：在途批次标 interrupted，不重放副作用工具调用。
            interrupted = repository.converge_interrupted(workspace_key)
            if interrupted:
                logger.bind(event="writing_collab_converged", interrupted=interrupted).warning(
                    "writing collaboration batches converged after restart"
                )

    # ---- 只读观测（测试与装配自检） -----------------------------------------

    def live_batches(self) -> tuple[LiveBatch, ...]:
        """仍然活着（未被取消/超时/交接释放）的批次句柄。"""
        return tuple(
            LiveBatch(
                collaboration_id=ref.collaboration_id,
                batch_id=ref.batch_id,
                attempt=ref.attempt,
                team_run_id=ref.team_run_id,
                cancel_generation=ref.current_generation,
            )
            for ref in self._runs.values()
            if ref.live
        )

    def pending_deadlines(self) -> tuple[asyncio.Task[Any], ...]:
        """批次自持的期限任务里仍然待触发的那几个。"""
        return tuple(
            task
            for task in self._deadlines.values()
            if not task.done() and not task.cancelling()
        )

    async def drain(self) -> None:
        """等待在后台释放的活句柄收尾（``on_team_event`` 是同步接口）。"""
        while self._releases:
            await asyncio.gather(*tuple(self._releases), return_exceptions=True)

    # ---- 写入口 -------------------------------------------------------------

    async def start(self, request: StartCollabRequest) -> BatchHandle:
        """解析配方 → 冻结准备 → 建批次 → 启 Team run → 协作置 ``running``。

        ``request`` 允许是 ``StartCollabRequest`` 或 payload 层组装的等价映射
        （见 :func:`coerce_start_request`）。
        """
        request = coerce_start_request(request)
        action = normalize_enum("action", request.action, ACTIONS)
        if action in _SELECTION_REQUIRED_ACTIONS and not _has_selection(request.selection):
            raise CollabSelectionRequired()
        article = await self._require_article_baseline(
            request.article_id, request.base_revision, request.snapshot_digest
        )
        collaboration = await asyncio.to_thread(
            self._repository.create_collaboration,
            article_id=request.article_id,
            chat_id=request.chat_id,
            session_key=f"{WEBUI_SESSION_STORAGE_PREFIX}{request.chat_id}",
            goal={"action": action},
            # 协作幂等键与批次幂等键分开：article_requests 的主键是 (workspace, request_id)，
            # 同一 id 落两行会命中 RequestReplayConflict。
            request_id=f"{request.client_request_id}:collaboration",
        )
        batch = await self._launch_batch(
            collaboration=collaboration,
            article=article,
            action=action,
            instruction=request.instruction,
            selection=request.selection,
            material_ids=request.material_ids,
            base_revision=request.base_revision,
            snapshot_digest=request.snapshot_digest,
            request_id=request.client_request_id,
        )
        return await self._handle_for(collaboration.id, batch)

    async def reply(
        self, *, collaboration_id: str, feedback: str, expect_version: int
    ) -> Batch:
        """作者回复等待中的交接：结束等待批次，按当前文章基线开一批 ``reply``。"""
        collaboration = await self._require_collaboration(collaboration_id)
        expected = _require_int("expect_version", expect_version)
        if expected != collaboration.version:
            raise CollabVersionConflict(expected=expected, actual=collaboration.version)
        article = await self._require_article(collaboration.article_id)
        # 结束等待批次本身要推进协作版本，所以 CAS 在上面先结清，建批次走内部路径。
        await self._close_waiting_author_batches(collaboration_id, reason="replied")
        return await self._launch_batch(
            collaboration=collaboration,
            article=article,
            action=Action.REPLY,
            instruction=feedback or "",
            selection=None,
            material_ids=(),
            base_revision=int(article.get("revision") or 0),
            snapshot_digest=snapshot_digest(article),
            request_id=_request_key("reply", collaboration_id, expected, feedback),
            expect_version=None,
        )

    async def confirm(
        self,
        *,
        collaboration_id: str,
        handoff_id: str,
        expected_handoff_version: int,
        outline: Any,
        article_revision: int,
        article_digest: str,
    ) -> ConfirmResult:
        """确认提纲交接：CAS 交接版本与文章基线 → 存作者编辑后的提纲 → 开后续批次。"""
        collaboration = await self._require_collaboration(collaboration_id)
        handoff = await asyncio.to_thread(self._repository.get_handoff, handoff_id)
        if handoff.collaboration_id != collaboration_id:
            raise HandoffNotFound(handoff_id)
        expected = _require_int("expected_handoff_version", expected_handoff_version)
        if expected != handoff.artifact_version:
            # 契约 §4：整数 artifact_version 的 CAS 口径。
            raise CollabVersionConflict(
                expected=expected, actual=handoff.artifact_version, kind="handoff"
            )
        article = await self._require_article_baseline(
            collaboration.article_id, article_revision, article_digest
        )
        snapshot = dict(article)
        if outline is not None:
            snapshot["outline"] = list(outline)
        saved = await asyncio.to_thread(
            self._articles.save_snapshot,
            collaboration.article_id,
            int(article_revision),
            snapshot,
            client_request_id=_request_key("confirm-outline", handoff_id, expected),
        )
        revision = int(saved.get("revision") or article_revision)
        digest = str(saved.get("digest") or "")
        resolved = await asyncio.to_thread(
            self._repository.resolve_handoff,
            handoff_id=handoff_id,
            expected_version=expected,
            status=HandoffStatus.CONFIRMED,
        )
        await self._close_waiting_author_batches(collaboration_id, reason="confirmed")
        article_after = await self._require_article(collaboration.article_id)
        current = await self._require_collaboration(collaboration_id)
        batch = await self._launch_batch(
            collaboration=current,
            article=article_after,
            action=Action.DRAFT,
            instruction="",
            selection=None,
            material_ids=(),
            base_revision=revision,
            snapshot_digest=digest,
            request_id=_request_key("confirm-next", handoff_id, expected),
        )
        return ConfirmResult(
            collaboration_id=collaboration_id,
            batch_id=batch.id,
            article_revision=revision,
            article_digest=digest,
            handoff_status=resolved.status,
        )

    async def cancel(self, *, collaboration_id: str, active_batch_id: str) -> Collaboration:
        """取消活动批次：记一代丢弃代数、停 Team run、批次与协作置 ``cancelled``。"""
        collaboration = await self._require_collaboration(collaboration_id)
        batch = await asyncio.to_thread(self._repository.get_batch, active_batch_id)
        if batch.collaboration_id != collaboration_id:
            raise NoActiveBatch(collaboration_id)
        if (
            collaboration.active_batch_id != active_batch_id
            or batch.status not in ACTIVE_BATCH_STATUSES
        ):
            raise NoActiveBatch(collaboration_id)
        return await self._cancel_chain(batch=batch, reason="cancelled")

    async def retry(
        self, *, collaboration_id: str, batch_id: str, expect_version: int
    ) -> Batch:
        """重开一次尝试：新 ``attempt``，复用仍有效的冻结上下文与既有产物。"""
        collaboration = await self._require_collaboration(collaboration_id)
        batch = await asyncio.to_thread(self._repository.get_batch, batch_id)
        if batch.collaboration_id != collaboration_id:
            raise ActiveBatchConflict(collaboration.article_id)
        if batch.status in ACTIVE_BATCH_STATUSES:
            raise ActiveBatchConflict(collaboration.article_id)
        previous = self._by_batch.get(batch_id)
        if previous is not None and previous.live:
            self._release_handle(previous)
            await self.drain()
        return await self._launch_batch(
            collaboration=collaboration,
            action=batch.action,
            instruction=batch.instruction,
            selection=None,
            material_ids=(),
            base_revision=batch.base_revision,
            snapshot_digest=batch.snapshot_digest,
            # 原批次已冻结的上下文仍然有效：重试不重新解析技能/模型，也不重复采纳产物。
            frozen=dict(batch.frozen) if batch.frozen else None,
            request_id=_request_key("retry", batch_id, expect_version),
            expect_version=expect_version,
        )

    # ---- 收口闸门 -----------------------------------------------------------

    def on_team_event(self, event: Any) -> None:
        """团队事件收口：核对身份与活动状态后落产物；旧结果只记日志。

        同步方法（冻结接口）：只做单事务的小读写，不跨 ``await``、不持锁等待。事件循环上
        的调用方如需更强隔离，可自行用 ``asyncio.to_thread`` 包裹。
        """
        ref = self._resolve_event(event)
        if ref is None:
            self._logger.bind(event="writing_collab_event_ignored").debug(
                "writing collaboration event ignored: unknown team run"
            )
            return
        dropped = self._gate(ref, event)
        if dropped is not None:
            self._logger.bind(
                event="writing_collab_event_dropped",
                collaboration_id=ref.collaboration_id,
                batch_id=ref.batch_id,
                attempt=ref.attempt,
                run_id=ref.team_run_id,
                reason=dropped,
            ).warning("writing collaboration late result dropped")
            return
        event_type = str(_field(event, "type", "event_type", "event") or "")
        if event_type in _TASK_FINISH_EVENTS:
            self._ingest_task_result(ref, event)
        elif event_type in _RUN_FINISH_EVENTS:
            self._finish_from_run(ref, event)

    async def on_batch_timeout(self, batch_id: str) -> None:
        """批次期限到点：走与取消完全相同的清理链（契约 §9 的 15 分钟上限）。"""
        try:
            batch = await asyncio.to_thread(self._repository.get_batch, batch_id)
        except LookupError:
            self._logger.bind(event="writing_collab_timeout_unknown", batch_id=batch_id).warning(
                "writing collaboration timeout for an unknown batch"
            )
            return
        if batch.status not in IN_FLIGHT_STATUSES:
            self._logger.bind(
                event="writing_collab_timeout_settled",
                collaboration_id=batch.collaboration_id,
                batch_id=batch.id,
                attempt=batch.attempt,
                status=batch.status,
            ).debug("writing collaboration timeout ignored: batch already settled")
            return
        await self._cancel_chain(batch=batch, reason="timeout")

    # ---- 内部：批次起跑 -----------------------------------------------------

    async def _launch_batch(
        self,
        *,
        collaboration: Collaboration,
        action: str,
        instruction: str,
        selection: Mapping[str, Any] | None,
        material_ids: Sequence[str],
        base_revision: int,
        snapshot_digest: str,
        request_id: str,
        expect_version: int | None = None,
        frozen: Any = None,
        article: Mapping[str, Any] | None = None,
    ) -> Batch:
        """冻结准备 → 建批次 → 启 Team run → 协作置 ``running``（幂等重放不二次起跑）。"""
        current_article = article
        if frozen is None:
            if current_article is None:
                current_article = await self._require_article(collaboration.article_id)
            frozen = self._prepare_frozen(
                recipe=self._recipe(action),
                article=current_article,
                material_ids=material_ids,
                collaboration_id=collaboration.id,
            )
        # 契约 §1 的 frozen 形状不含技能正文；batch_id 要等 create_batch 生成 id 才知道，
        # 因此落库的那一份 batch_id 留空，运行期那一份由 _with_identity 补齐。
        batch = await asyncio.to_thread(
            self._repository.create_batch,
            collaboration_id=collaboration.id,
            action=action,
            base_revision=base_revision,
            snapshot_digest=snapshot_digest,
            instruction=instruction or "",
            frozen=self._frozen_payload(
                frozen,
                collaboration_id=collaboration.id,
                article_id=collaboration.article_id,
                batch_id="",
            ),
            expect_version=expect_version,
            request_id=request_id,
        )
        if (
            batch.id in self._started_batches
            or batch.status != BatchStatus.QUEUED
            or batch.finished_at is not None
        ):
            # 幂等重放（同 client_request_id）或已终态：绝不能重复起跑。
            return batch
        self._started_batches.add(batch.id)
        run_frozen = _with_identity(
            frozen,
            workspace_key=self._workspace_key,
            article_id=collaboration.article_id,
            collaboration_id=collaboration.id,
            batch_id=batch.id,
        )
        try:
            run_id = await self._team.start_batch(
                collaboration_id=collaboration.id,
                batch_id=batch.id,
                attempt=batch.attempt,
                action=action,
                instruction=instruction or "",
                session_key=collaboration.session_key,
                chat_id=collaboration.chat_id,
                frozen=run_frozen,
                selection=dict(selection) if isinstance(selection, Mapping) else None,
                material_ids=tuple(material_ids or ()),
            )
        except Exception:
            failed = await asyncio.to_thread(
                self._repository.finish_batch,
                batch_id=batch.id,
                status=BatchStatus.FAILED,
            )
            self._log_transition(
                collaboration_id=collaboration.id,
                batch_id=failed.id,
                attempt=failed.attempt,
                from_status=BatchStatus.QUEUED,
                to_status=BatchStatus.FAILED,
                cancel_generation=failed.cancel_generation,
                reason="start_failed",
            )
            raise
        ref = self._register_run(
            collaboration_id=collaboration.id, batch=batch, run_id=str(run_id or "")
        )
        await asyncio.to_thread(
            self._repository.set_collaboration_status,
            collaboration_id=collaboration.id,
            status=CollaborationStatus.RUNNING,
            expect_version=None,
        )
        self._log_transition(
            collaboration_id=collaboration.id,
            batch_id=batch.id,
            attempt=batch.attempt,
            from_status=BatchStatus.QUEUED,
            to_status=CollaborationStatus.RUNNING,
            cancel_generation=ref.current_generation,
        )
        self._schedule_deadline(batch.id)
        return batch

    def _register_run(self, *, collaboration_id: str, batch: Batch, run_id: str) -> _BatchRef:
        # 协作级丢弃代数只活在进程内：仓储的 cancel_generation 由批次行承载（见模块说明）。
        generation = max(
            int(batch.cancel_generation or 0), self._generations.get(collaboration_id, 0)
        )
        self._generations[collaboration_id] = generation
        ref = _BatchRef(
            collaboration_id=collaboration_id,
            batch_id=batch.id,
            attempt=batch.attempt,
            team_run_id=run_id,
            started_generation=generation,
            current_generation=generation,
        )
        self._by_batch[batch.id] = ref
        if run_id:
            self._runs[run_id] = ref
        else:
            self._logger.bind(
                event="writing_collab_run_id_missing", batch_id=batch.id
            ).warning("writing collaboration team port returned no run id")
        return ref

    async def _handle_for(self, collaboration_id: str, batch: Batch) -> BatchHandle:
        collaboration = await self._require_collaboration(collaboration_id)
        return BatchHandle(
            collaboration_id=collaboration_id,
            batch_id=batch.id,
            status=collaboration.status,
            stage=collaboration.stage,
            session_key=collaboration.session_key,
            active_batch_id=collaboration.active_batch_id,
        )

    async def _require_collaboration(self, collaboration_id: str) -> Collaboration:
        return await asyncio.to_thread(self._repository.get_collaboration, collaboration_id)

    async def _require_article(self, article_id: str) -> dict[str, Any]:
        payload = await asyncio.to_thread(self._articles.get_article, article_id)
        article = payload.get("article")
        if not isinstance(article, Mapping):
            raise ArticleNotFound(article_id)
        return dict(cast("Mapping[str, Any]", article))

    async def _require_article_baseline(
        self, article_id: str, base_revision: int, digest: str
    ) -> dict[str, Any]:
        article = await self._require_article(article_id)
        current = int(article.get("revision") or 0)
        expected = _require_int("article_revision", base_revision)
        if current != expected:
            raise ArticleConflict(article_id, current)
        if digest and snapshot_digest(article) != str(digest):
            # 同一版本但内容摘要不同：调用方拿的是过期内容，按版本冲突处理。
            raise ArticleConflict(article_id, current)
        return article

    async def _close_waiting_author_batches(self, collaboration_id: str, *, reason: str) -> None:
        """作者已经答复：等待中的批次收尾为 ``completed``，好让新批次不被活动批次挡住。"""
        batches = await asyncio.to_thread(self._repository.list_batches, collaboration_id)
        for batch in batches:
            if batch.status != BatchStatus.WAITING_AUTHOR:
                continue
            finished = await asyncio.to_thread(
                self._repository.finish_batch,
                batch_id=batch.id,
                status=BatchStatus.COMPLETED,
            )
            self._log_transition(
                collaboration_id=collaboration_id,
                batch_id=finished.id,
                attempt=finished.attempt,
                from_status=BatchStatus.WAITING_AUTHOR,
                to_status=BatchStatus.COMPLETED,
                cancel_generation=finished.cancel_generation,
                reason=reason,
            )

    # ---- 内部：取消/超时清理链 ----------------------------------------------

    async def _cancel_chain(self, *, batch: Batch, reason: str) -> Collaboration:
        generation = self._bump_generation(batch.collaboration_id)
        ref = self._by_batch.get(batch.id)
        run_id = batch.team_run_id
        if ref is not None:
            # 先就地换代再 await：停 run 期间到达的晚到结果已经被闸门拦住。
            ref.live = False
            ref.current_generation = generation
            if ref.team_run_id:
                run_id = ref.team_run_id
        self._cancel_deadline(batch.id)
        if run_id:
            await self._team.cancel_run(run_id)
        await asyncio.to_thread(
            self._repository.finish_batch,
            batch_id=batch.id,
            status=BatchStatus.CANCELLED,
            cancel_generation=generation,
        )
        collaboration = await self._require_collaboration(batch.collaboration_id)
        self._log_transition(
            collaboration_id=batch.collaboration_id,
            batch_id=batch.id,
            attempt=batch.attempt,
            from_status=batch.status,
            to_status=BatchStatus.CANCELLED,
            cancel_generation=generation,
            reason=reason,
        )
        return collaboration

    def _bump_generation(self, collaboration_id: str) -> int:
        current = self._generations.get(collaboration_id, 0)
        generation = current + 1
        self._generations[collaboration_id] = generation
        return generation

    # ---- 内部：期限任务 -----------------------------------------------------

    def _schedule_deadline(self, batch_id: str) -> None:
        self._cancel_deadline(batch_id)
        task = asyncio.create_task(
            self._await_deadline(batch_id), name=f"writing-collab-deadline-{batch_id}"
        )
        self._deadlines[batch_id] = task
        task.add_done_callback(lambda finished, bid=batch_id: self._deadline_done(bid, finished))

    async def _await_deadline(self, batch_id: str) -> None:
        await self._sleep(_batch_timeout_seconds())
        await self.on_batch_timeout(batch_id)

    def _deadline_done(self, batch_id: str, task: asyncio.Task[Any]) -> None:
        if self._deadlines.get(batch_id) is task:
            self._deadlines.pop(batch_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._logger.bind(
                event="writing_collab_deadline_failed", batch_id=batch_id
            ).warning(f"writing collaboration deadline task failed: {error}")

    def _cancel_deadline(self, batch_id: str) -> None:
        task = self._deadlines.pop(batch_id, None)
        if task is None or task.done():
            return
        if task is asyncio.current_task():
            # 期限任务自己在跑：取消自己会在下一个 await 处打断清理链。
            return
        task.cancel()

    # ---- 内部：事件闸门与产物 ----------------------------------------------

    def _resolve_event(self, event: Any) -> _BatchRef | None:
        run_id = str(_field(event, "run_id", "runId") or "")
        if not run_id:
            return None
        return self._runs.get(run_id)

    def _gate(self, ref: _BatchRef, event: Any) -> str | None:
        """返回丢弃原因；``None`` 表示这条事件可以落产物、可以推可采纳事件。"""
        if not ref.live:
            return "batch handle has been released"
        identity = _event_identity(event, ref)
        if identity.collaboration_id != ref.collaboration_id:
            return "collaboration mismatch"
        if identity.batch_id != ref.batch_id:
            return "active batch mismatch"
        if identity.attempt != ref.attempt:
            return "attempt mismatch"
        if identity.cancel_generation != ref.current_generation:
            return "cancel generation mismatch"
        try:
            batch = self._repository.get_batch(ref.batch_id)
            collaboration = self._repository.get_collaboration(ref.collaboration_id)
        except LookupError:
            return "batch or collaboration is gone"
        if batch.status not in IN_FLIGHT_STATUSES:
            return f"batch status is {batch.status}"
        if collaboration.status not in IN_FLIGHT_STATUSES:
            return f"collaboration status is {collaboration.status}"
        if collaboration.active_batch_id != ref.batch_id:
            return "batch is no longer the active batch"
        return None

    def _ingest_task_result(self, ref: _BatchRef, event: Any) -> None:
        artifact = self._artifacts.ingest(
            collaboration_id=ref.collaboration_id,
            batch_id=ref.batch_id,
            attempt=ref.attempt,
            event=event,
        )
        if artifact is None:
            # 诊断性输出：只作为失败诊断，不生成可采纳卡。
            return
        artifact_id = str(_field(artifact, "id", "artifact_id") or "")
        kind = str(_field(artifact, "kind") or "")
        version = int(_field(artifact, "version") or 0)
        self._publish(
            "writing_artifact",
            {
                "collaboration_id": ref.collaboration_id,
                "artifact_id": artifact_id,
                "kind": kind,
                "version": version,
                "summary": str(_field(artifact, "summary") or ""),
            },
        )
        if kind != ArtifactKind.OUTLINE:
            return
        handoff = self._repository.add_handoff(
            collaboration_id=ref.collaboration_id,
            batch_id=ref.batch_id,
            artifact_id=artifact_id,
            kind=HandoffKind.OUTLINE_CONFIRM,
            artifact_version=version,
        )
        self._publish("writing_handoff", _handoff_body(handoff))
        self._end_batch_for_author(ref, handoff)

    def _end_batch_for_author(self, ref: _BatchRef, handoff: Handoff) -> None:
        """提纲交付：结束当前批次、协作进 ``waiting_author``、释放活句柄。"""
        finished = self._repository.finish_batch(
            batch_id=ref.batch_id, status=BatchStatus.WAITING_AUTHOR
        )
        self._release_handle(ref)
        self._log_transition(
            collaboration_id=ref.collaboration_id,
            batch_id=finished.id,
            attempt=finished.attempt,
            from_status=BatchStatus.RUNNING,
            to_status=BatchStatus.WAITING_AUTHOR,
            cancel_generation=finished.cancel_generation,
            reason=handoff.kind,
        )

    def _finish_from_run(self, ref: _BatchRef, event: Any) -> None:
        status = _run_status(event)
        if status in (BatchStatus.FAILED, BatchStatus.CANCELLED, BatchStatus.INTERRUPTED):
            finished = self._repository.finish_batch(batch_id=ref.batch_id, status=status)
            self._release_handle(ref)
            self._log_transition(
                collaboration_id=ref.collaboration_id,
                batch_id=finished.id,
                attempt=finished.attempt,
                from_status=BatchStatus.RUNNING,
                to_status=status,
                cancel_generation=finished.cancel_generation,
                reason="run_finished",
            )
            return
        finished = self._repository.finish_batch(
            batch_id=ref.batch_id, status=BatchStatus.COMPLETED
        )
        self._release_handle(ref)
        self._log_transition(
            collaboration_id=ref.collaboration_id,
            batch_id=finished.id,
            attempt=finished.attempt,
            from_status=BatchStatus.RUNNING,
            to_status=BatchStatus.COMPLETED,
            cancel_generation=finished.cancel_generation,
            reason="run_finished",
        )

    def _release_handle(self, ref: _BatchRef) -> None:
        """释放活句柄：摘掉期限任务、标记非活动，并停掉团队运行里的活模型任务。"""
        ref.live = False
        self._cancel_deadline(ref.batch_id)
        if not ref.team_run_id:
            return
        task = asyncio.create_task(
            self._team.cancel_run(ref.team_run_id),
            name=f"writing-collab-release-{ref.batch_id}",
        )
        self._releases.add(task)
        task.add_done_callback(self._release_done)

    def _release_done(self, task: asyncio.Task[Any]) -> None:
        self._releases.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._logger.bind(event="writing_collab_release_failed").warning(
                f"writing collaboration handle release failed: {error}"
            )

    def _publish(self, name: str, body: Mapping[str, Any]) -> None:
        if self._publisher is None:
            return
        try:
            self._publisher(name, dict(body))
        except Exception as error:  # noqa: BLE001 - 出口故障不能改变领域状态
            self._logger.bind(event="writing_collab_publish_failed").warning(
                f"writing collaboration event projection failed: {error}"
            )

    # ---- 内部：配方、冻结与日志 --------------------------------------------

    def _recipe(self, action: str) -> Any:
        recipes: Any = self._recipes
        if recipes is None:
            recipes = _load_default_recipes()
        mapping = recipes() if callable(recipes) else recipes
        try:
            return mapping[action]
        except (KeyError, TypeError) as error:
            raise CollabServiceNotConfigured(f"no recipe for action {action}") from error

    def _prepare_frozen(
        self,
        *,
        recipe: Any,
        article: Mapping[str, Any],
        material_ids: Sequence[str],
        collaboration_id: str,
        article_id: str = "",
        batch_id: str = "",
    ) -> Any:
        if self._preparer is None:
            raise CollabServiceNotConfigured("frozen preparation is not configured")
        frozen = self._preparer(
            recipe=recipe,
            article_snapshot=article,
            material_ids=tuple(material_ids or ()),
            collaboration_id=collaboration_id,
            workspace_key=self._workspace_key,
            article_id=article_id,
            batch_id=batch_id,
        )
        if frozen is None:
            raise CollabServiceNotConfigured("frozen preparation returned nothing")
        return frozen

    def _frozen_payload(
        self, frozen: Any, *, collaboration_id: str, article_id: str, batch_id: str
    ) -> dict[str, Any]:
        """契约 §1 的 ``frozen`` 形状：只留 name/path/sha256，**不含**技能正文。"""
        source_scope = _field(frozen, "source_scope", "sourceScope")
        return {
            "skills": _frozen_skills(frozen),
            "model_id": str(_field(frozen, "model_id", "modelId") or ""),
            "source_scope": dict(source_scope) if isinstance(source_scope, Mapping) else {},
            "workspace_key": self._workspace_key,
            "article_id": article_id,
            "collaboration_id": collaboration_id,
            "batch_id": batch_id,
        }

    def _log_transition(
        self,
        *,
        collaboration_id: str,
        batch_id: str,
        attempt: int,
        from_status: str,
        to_status: str,
        cancel_generation: int,
        reason: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "event": "writing_collab_batch_transition",
            "collaboration_id": collaboration_id,
            "batch_id": batch_id,
            "attempt": attempt,
            "from_status": from_status,
            "to_status": to_status,
            "cancel_generation": cancel_generation,
        }
        if reason is not None:
            fields["reason"] = reason
        self._logger.bind(**fields).info("writing collaboration batch transition")


# ---- 模块级工具 -------------------------------------------------------------


def _field(source: Any, *names: str) -> Any:
    """按名字从映射或对象上取值；取不到返回 ``None``（端口是鸭子类型）。"""
    for name in names:
        if isinstance(source, Mapping):
            mapping = cast("Mapping[str, Any]", source)
            if name in mapping:
                return mapping[name]
            continue
        value = getattr(source, name, None)
        if value is not None:
            return value
    return None


def _event_field(event: Any, *names: str) -> Any:
    value = _field(event, *names)
    if value is not None:
        return value
    return _field(getattr(event, "data", None), *names)


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _require_int(field_name: str, value: Any) -> int:
    resolved = _as_int(value)
    if resolved is None:
        raise InvalidFieldError(field_name, value)
    return resolved


@dataclass(frozen=True, slots=True)
class _EventIdentity:
    collaboration_id: str
    batch_id: str
    attempt: int
    cancel_generation: int


def _with_identity(
    frozen: Any,
    *,
    workspace_key: str,
    article_id: str,
    collaboration_id: str,
    batch_id: str,
) -> Any:
    """把仓储侧身份盖到交给 Team 的冻结上下文上（技能正文等原样保留）。"""
    identity: dict[str, Any] = {
        "workspace_key": workspace_key,
        "article_id": article_id,
        "collaboration_id": collaboration_id,
        "batch_id": batch_id,
    }
    if isinstance(frozen, Mapping):
        payload = dict(cast("Mapping[str, Any]", frozen))
        payload.update(identity)
        return payload
    if is_dataclass(frozen) and not isinstance(frozen, type):
        known = {key: value for key, value in identity.items() if hasattr(frozen, key)}
        try:
            return replace(frozen, **known)
        except TypeError:
            return frozen
    return frozen


def _event_identity(event: Any, ref: _BatchRef) -> _EventIdentity:
    """事件自称的身份；缺省即「这条事件属于绑定它的那次运行」。

    ``attemptId`` 不在读取范围内：团队域的 ``attemptId`` 是任务尝试标识（字符串），
    与协作批次的整数 ``attempt`` 不是同一个东西；批次与 attempt 在起跑时就与 run 绑定，
    事件显式带 ``attempt``/``collabAttempt`` 时才做交叉校验。
    """
    generation = _as_int(_event_field(event, "cancelGeneration", "cancel_generation", "cancelGen"))
    attempt = _as_int(_event_field(event, "collabAttempt", "collab_attempt", "attempt"))
    return _EventIdentity(
        collaboration_id=str(
            _event_field(event, "collaborationId", "collaboration_id") or ref.collaboration_id
        ),
        batch_id=str(
            _event_field(event, "activeBatchId", "active_batch_id", "batchId", "batch_id")
            or ref.batch_id
        ),
        attempt=attempt if attempt is not None else ref.attempt,
        cancel_generation=(
            generation if generation is not None else ref.started_generation
        ),
    )


def _has_selection(selection: Any) -> bool:
    if not isinstance(selection, Mapping):
        return False
    payload = cast("Mapping[str, Any]", selection)
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        return True
    start, end = _as_int(payload.get("start")), _as_int(payload.get("end"))
    return start is not None and end is not None and end > start


def _run_status(event: Any) -> str:
    run = _event_field(event, "run")
    status = _field(run, "status") if run is not None else None
    status = status or _event_field(event, "status")
    return str(status or "")


def _frozen_skills(frozen: Any) -> list[dict[str, Any]]:
    raw = _field(frozen, "skills")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    skills: list[dict[str, Any]] = []
    for item in cast("Sequence[Any]", raw):
        skills.append(
            {
                "name": str(_field(item, "name") or ""),
                "path": str(_field(item, "path") or ""),
                "sha256": str(_field(item, "sha256", "sha_256", "digest") or ""),
            }
        )
    return skills


def _handoff_body(handoff: Handoff) -> dict[str, Any]:
    return {
        "collaboration_id": handoff.collaboration_id,
        "handoff_id": handoff.id,
        "kind": handoff.kind,
        "artifact_id": handoff.artifact_id,
        "artifact_version": handoff.artifact_version,
        "status": handoff.status,
    }


def _request_key(op: str, *parts: Any) -> str:
    """确定性幂等键：同一动作 + 同一期望版本重放时返回既有结果，不二次执行。"""
    encoded = "|".join([op, *(str(part) for part in parts)]).encode("utf-8")
    return f"{op}:{hashlib.sha256(encoded).hexdigest()[:24]}"
