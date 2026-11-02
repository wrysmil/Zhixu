"""知序记忆评测隔离适配器（plan §5 Task 3 / 契约 §3「后端 adapter 与完成屏障」）。

职责
----

把同一个 trial 的记忆链路装配在**隔离的 trial 评估根**里：真实的
``MemoryServices`` / ``MemoryExtractor`` / ``RetrievalEngine`` / 画像派生
（``MemoryLifecycle``）/ 工作记忆（scratchpad），全部路径限制在 trial 根内。

- ``ingest_session`` 固定 transcript 回放进产品 ``Session``（``metadata.
  workspace_scope`` 走产品同一套作用域映射 → ``session_memory_workspace_id``），
  不伪造跨用户授权。
- ``settle`` 调 WU-02 公共入口 ``flush_session_memory``（``wait_pending=False``
  直接触发真实抽取，避开产品 120s idle 阈值）；真实超时如实记 ``timeout``。
  registry 的 ``partial`` 状态在**本适配层**映射为契约 ``SettleResult.status``
  闭集（:meth:`ZhixuMemoryAdapter.map_flush_status`），契约不改。
- ``snapshot`` / ``restore`` / ``restart`` 经 :class:`MemoryCheckpointStore`
  用 SQLite backup API 保存/恢复；恢复在独立 probe 子目录；不保留隐藏
  Python 缓存（extractor / 检索引擎 / lifecycle 全部重建，类级单例在 close
  时清理）。
- ``recall`` / ``answer``：recall 走产品检索引擎（工作区可见集合 =
  ``memory_visibility_ids``）；answer 用**新会话**调用真实 provider（注入块
  由产品检索引擎产出）。向量后端缺失时显式 ``capability_missing``，不回退成
  假向量成绩。
- ``forget``：``product_request`` 走产品 WebUI 删除入口
  （``nanobot.webui.memory_api.delete_memory``，含向量挂钩与 MEMORY.md 派生
  刷新）；``admin_delete`` 走管理原语（``repository.delete_memory`` + 向量
  移除 + 派生刷新）。两者都是真实产品操作，没有测试专用清库逻辑。
- ``rebuild``：真实重建入口——MEMORY.md 派生（``MemoryLifecycle.
  refresh_memory_md_sync``）+ 向量全量对账（``MemoryIndexer.sync_from_sqlite``
  ，仅当向量就绪）；向量期望但缺失时 ``capability_missing``。
- gold 永不出现在执行端：本模块只消费 ``MemoryExecutionInput`` 形状的数据，
  snapshot / manifest / 数据库里没有任何 gold 字段。

已知能力边界（写进 :attr:`capabilities`，不粉饰）：

- ``clock_coverage = partial``：``repository.query_semantic`` 的 ``since_days``
  SQL 窗口走真实时间（WU-02 时钟未覆盖 SQL 窗口），评测时钟只能覆盖部分链路。
- ``MemoryLifecycle`` 的 ``_last_refresh_at`` 等类级 dict 按 workspace_id 记账
  ——本适配器用 trial 根内的独立工作区身份（产品 ``workspace_identity``），
  与生产实例时间轴天然隔离；``close()`` 时清掉自己的键。
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from nanobot.evaluation.memory.checkpoints import (
    CheckpointError,
    MemoryCheckpointStore,
)
from nanobot.evaluation.memory.models import (
    ForgetRequest,
    ForgetResult,
    MemoryCandidate,
    MemoryCapabilities,
    MemoryCapability,
    MemoryProbe,
    MemoryRecallItem,
    MemoryScope,
    MemorySessionEvent,
    MemoryStateSnapshot,
    MemoryUsage,
    ProbeExecution,
    RecallResult,
    SettleResult,
)
from nanobot.memory.clock import MemoryClock
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import MemoryExtractor
from nanobot.memory.lifecycle import MemoryLifecycle
from nanobot.memory.provenance import memory_visibility_ids, memory_workspace_id
from nanobot.memory.repository import delete_memory as repo_delete_memory
from nanobot.memory.repository import get_memory
from nanobot.memory.retrieval.engine import RetrievalEngine
from nanobot.memory.retrieval.store_adapter import MemoryStoreAdapter
from nanobot.memory.signals import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_SKIPPED,
    MemoryFlushOutcome,
    MemoryOperationRegistry,
    flush_session_memory,
)
from nanobot.session.manager import Session
from nanobot.webui.memory_services import MemoryServices

__all__ = ["TrialPathError", "ZhixuMemoryAdapter"]

#: 恢复出的 probe 工作区在 trial 根内的相对目录。
_PROBES_DIRNAME = "probes"
_WORKSPACES_DIRNAME = "workspaces"
_CHECKPOINTS_DIRNAME = "checkpoints"


class TrialPathError(Exception):
    """workspace 路径不在 trial 评估根之内（路径越界，一律拒绝）。"""


def _resolve_inside(root: Path, candidate: Path) -> Path:
    """把 candidate 解析到 root 之内；越界抛 :class:`TrialPathError`。"""
    resolved_root = Path(root).resolve()
    resolved = Path(candidate).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise TrialPathError(f"path outside trial root: {candidate}")
    return resolved


class ZhixuMemoryAdapter:
    """契约 :class:`~nanobot.evaluation.memory.protocol.MemoryAdapter` 的知序实现。

    构造时显式装配全部记忆组件（不依赖隐藏全局），并把**实际能力**写进
    :attr:`capabilities`——unsupported 与试验失败分开，缺能力显式上报。
    """

    def __init__(
        self,
        *,
        trial_root: Path,
        scope: MemoryScope,
        runtime: Any,
        workspace_path: Path | None = None,
        vector_store: Any | None = None,
        vector_expected: bool = False,
        clock: MemoryClock | None = None,
        registry: MemoryOperationRegistry | None = None,
        namespace: str | None = None,
        checkpoint_store: MemoryCheckpointStore | None = None,
    ) -> None:
        self._trial_root = _resolve_inside(Path(trial_root), Path(trial_root))
        self._scope = scope
        self._runtime = runtime
        self._clock = clock
        self._vector_store = vector_store
        self._vector_expected = vector_expected
        self._closed = False
        self._mode = "zhixu"
        self._candidate: MemoryCandidate | None = None

        # ---- 工作区：全部限制在 trial 根内 -------------------------------
        if workspace_path is None:
            workspace_path = self._trial_root / _WORKSPACES_DIRNAME / scope.workspace_id / scope.subject_id
        self.workspace_path = _resolve_inside(self._trial_root, workspace_path)
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        # 产品同一套隔离键：身份 marker（.nanobot/workspace-id）决定隔离键，
        # 与生产实例互不重叠（trial 根路径独立 → marker 独立）。
        self.workspace_id = memory_workspace_id(str(self.workspace_path))

        # ---- 显式装配产品组件 --------------------------------------------
        self.services = MemoryServices.for_workspace(self.workspace_id, self.workspace_path)
        self._registry = registry if registry is not None else MemoryOperationRegistry()
        self._namespace = namespace or f"evaluation-{uuid.uuid4().hex[:12]}"
        self._extractor = MemoryExtractor(
            self.services.database,
            runtime,
            workspace_id=self.workspace_id,
            user_id=scope.subject_id,
            clock=clock,
        )
        self._store = MemoryStoreAdapter(
            self.services.database, vector_store=vector_store, clock=clock
        )
        self._retrieval = RetrievalEngine(store=self._store, clock=clock)
        # 直接构造，不进 MemoryLifecycle._instances 类级单例（restart/restore
        # 不得复用绑定旧 services 的缓存实例——「不保留隐藏 Python 缓存」）。
        self._lifecycle = MemoryLifecycle(self.workspace_id, self.services, clock=clock)
        self._checkpoints = checkpoint_store or MemoryCheckpointStore(
            self._trial_root / _CHECKPOINTS_DIRNAME
        )
        self._sessions: dict[str, Session] = {}
        self._origin_refs: dict[str, list[str]] = {}
        self._capabilities = self._build_capabilities()

    # ------------------------------------------------------------------
    # 能力
    # ------------------------------------------------------------------

    def _build_capabilities(self) -> MemoryCapabilities:
        """按实际装配结果声明能力（契约 §3：unsupported ≠ 试验失败）。"""
        vector_ready = self._vector_is_ready()
        if self._vector_expected and not vector_ready:
            semantic = MemoryCapability(
                status="unsupported",
                detail="capability_missing: vector backend not ready (FTS5-only would be a fake vector result)",
            )
        elif vector_ready:
            semantic = MemoryCapability(status="supported", detail="vector ∪ FTS5 (RRF)")
        else:
            semantic = MemoryCapability(
                status="partial", detail="FTS5-only; vector backend not configured"
            )
        return MemoryCapabilities(
            extraction=MemoryCapability(status="supported", detail="MemoryExtractor idle pipeline"),
            semantic_recall=semantic,
            episodes=MemoryCapability(status="supported", detail="episode track"),
            working_memory=MemoryCapability(status="supported", detail="scratchpad"),
            profile=MemoryCapability(status="supported", detail="MemoryLifecycle derived MEMORY.md"),
            delete=MemoryCapability(status="supported", detail="product_request=webui delete; admin_delete=repository delete"),
            rebuild=MemoryCapability(
                status="supported" if not self._vector_expected or vector_ready else "partial",
                detail="derived MEMORY.md rebuild; vector reindex when ready",
            ),
            restart=MemoryCapability(status="supported", detail="checkpoint restore from persisted files"),
            # 风险 #3：query_semantic 的 since_days SQL 窗口走真实时间 → 部分覆盖。
            clock_coverage=MemoryCapability(
                status="partial",
                detail="since_days SQL window uses real time; wall/monotonic injectable",
            ),
            scope_coverage=MemoryCapability(
                status="supported", detail="workspace identity marker scoping"
            ),
        )

    @property
    def capabilities(self) -> MemoryCapabilities:
        return self._capabilities

    def _vector_is_ready(self) -> bool:
        if self._vector_store is None:
            return False
        return str(getattr(self._vector_store, "state", "")) == "ready"

    # ------------------------------------------------------------------
    # reset / close
    # ------------------------------------------------------------------

    async def reset(self, candidate: MemoryCandidate) -> None:
        """按候选重置运行模式。no_persistent_memory 显式关闭全部持久读写。"""
        self._candidate = candidate
        self._mode = candidate.mode
        if candidate.mode == "no_persistent_memory":
            self._capabilities = MemoryCapabilities(
                extraction=MemoryCapability(status="unsupported", detail="memory disabled"),
                semantic_recall=MemoryCapability(status="unsupported", detail="memory disabled"),
                episodes=MemoryCapability(status="unsupported", detail="memory disabled"),
                working_memory=MemoryCapability(status="unsupported", detail="memory disabled"),
                profile=MemoryCapability(status="unsupported", detail="memory disabled"),
                delete=MemoryCapability(status="unsupported", detail="memory disabled"),
                rebuild=MemoryCapability(status="unsupported", detail="memory disabled"),
                restart=MemoryCapability(status="unsupported", detail="memory disabled"),
                clock_coverage=MemoryCapability(status="unsupported", detail="memory disabled"),
                scope_coverage=MemoryCapability(status="unsupported", detail="memory disabled"),
            )
        else:
            self._capabilities = self._build_capabilities()

    async def close(self) -> None:
        """释放本 adapter 的运行时状态；清掉本工作区在类级 dict 的记账（风险 #4）。"""
        if self._closed:
            return
        self._closed = True
        for session in self._sessions.values():
            pending = self._registry.cancel_pending(self._namespace, session.key)
            if pending is not None and not pending.done():
                try:
                    await pending
                except Exception:  # noqa: BLE001 - close 路径不因后台任务失败而失败
                    pass
        self._registry.close()
        self._sessions.clear()
        # MemoryLifecycle 的类级 dict 按 workspace_id 记账（去抖/单例）。
        # 本 adapter 的 lifecycle 是直接构造的、未注册；但产品删除路径
        # （memory_api._refresh_memory_md_after_mutation）可能注册过同名键，
        # close 时清掉，避免泄漏到后续同 id 的实例。
        MemoryLifecycle._instances.pop(self.workspace_id, None)
        MemoryLifecycle._refresh_tasks.pop(self.workspace_id, None)
        MemoryLifecycle._last_refresh_at.pop(self.workspace_id, None)
        MemoryLifecycle._last_refresh_iso.pop(self.workspace_id, None)
        MemoryLifecycle._last_refresh_trigger.pop(self.workspace_id, None)

    # ------------------------------------------------------------------
    # ingest / settle
    # ------------------------------------------------------------------

    async def ingest_session(self, event: MemorySessionEvent) -> None:
        """把固定 transcript 回放进产品 ``Session``（产品同一套作用域映射）。

        会话 metadata 带 ``workspace_scope.project_path`` → 记忆行落在产品
        ``session_memory_workspace_id`` 推导出的隔离键下；subject_id 映射为
        记忆行的 ``user_id``。不做任何随机 session 伪造跨用户授权。
        """
        session_key = f"evaluation:{self._scope.subject_id}:{event.id}"
        session = Session(
            key=session_key,
            messages=[
                {"role": message.role, "content": message.content}
                for message in event.messages
            ],
            metadata={
                "workspace_scope": {"project_path": str(self.workspace_path)},
            },
        )
        self._sessions[event.id] = session
        self._origin_refs[event.id] = [message.id for message in event.messages]

    @staticmethod
    def map_flush_status(status: str) -> str:
        """registry / flush 状态 → 契约 ``SettleResult.status`` 闭集（风险 #2）。

        映射**只发生在本适配层**，契约不改：

        - ``completed`` → ``completed``（所有必需轨道干净完成）
        - ``skipped`` → ``skipped_by_policy``（空结果 / 无抽取入口；空结果绝不
          伪装 completed）
        - ``partial`` → ``failed``（部分轨道失败——契约闭集没有 partial，
          失败轨道进 ``failed_stages``）
        - ``failed`` / ``cancelled`` → ``failed``
        """
        mapping = {
            STATUS_COMPLETED: "completed",
            STATUS_SKIPPED: "skipped_by_policy",
            STATUS_PARTIAL: "failed",
            STATUS_FAILED: "failed",
            "cancelled": "failed",
        }
        if status not in mapping:
            raise ValueError(f"unknown flush status: {status!r}")
        return mapping[status]

    async def settle(self, session_id: str, timeout_s: float) -> SettleResult:
        """等待该会话的记忆处理完成（WU-02 公共入口）。

        ``wait_pending=False``：评测不等产品 120s idle 阈值，直接触发与 idle
        同一套的真实抽取（flush 内部会取消待机的 idle 定时器避免重复抽取）。
        ``timeout_s`` 是**真实执行超时**（事件循环时钟），超时如实记 ``timeout``。
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"unknown session: {session_id}")
        if self._closed:
            raise RuntimeError("adapter is closed")
        if self._mode == "no_persistent_memory":
            return SettleResult(
                status="skipped_by_policy",
                reason_codes=["memory_disabled"],
            )

        try:
            outcome: MemoryFlushOutcome = await asyncio.wait_for(
                flush_session_memory(
                    session.key,
                    extractor=self._extractor,
                    session=session,
                    registry=self._registry,
                    namespace=self._namespace,
                    wait_pending=False,
                ),
                timeout=max(0.0, float(timeout_s)),
            )
        except asyncio.TimeoutError:
            return SettleResult(
                status="timeout",
                reason_codes=["flush_timeout"],
                state_version=self._state_version(),
            )

        status = self.map_flush_status(outcome.status)
        completed_stages: list[str] = []
        failed_stages: list[str] = []
        reason_codes: list[str] = []
        if status == "completed":
            completed_stages.append("extraction")
            index_ready = self._sync_vector_index()
            if index_ready:
                completed_stages.append("index")
            else:
                reason_codes.append("index_absent")
        if status == "failed":
            failed_tracks = list(outcome.detail.get("failed_tracks") or [])
            if failed_tracks:
                failed_stages.extend(failed_tracks)
                reason_codes.append("track_failed")
            else:
                failed_stages.append("extraction")
            if outcome.error:
                reason_codes.append(outcome.error)
        if status == "skipped_by_policy":
            reason = str(outcome.detail.get("reason") or "no_result")
            reason_codes.append(reason)
        if outcome.partial:
            reason_codes.append("observer_partial")

        index_version = self._index_version()
        return SettleResult(
            status=status,  # type: ignore[arg-type]
            completed_stages=completed_stages,
            failed_stages=failed_stages,
            reason_codes=reason_codes,
            state_version=self._state_version(),
            index_version=index_version,
        )

    def _sync_vector_index(self) -> bool:
        """向量就绪时把新记忆增量对账进索引；未配置/未就绪返回 False。"""
        if not self._vector_is_ready():
            return False
        try:
            from nanobot.memory.vector.indexer import MemoryIndexer

            MemoryIndexer(self._vector_store, self.services.database).sync_from_sqlite()
            return True
        except Exception:  # noqa: BLE001 - 向量故障绝不阻断主流程
            return False

    def _index_version(self) -> str | None:
        if not self._vector_is_ready():
            return None
        return f"vector:{getattr(self._vector_store, 'model_name', 'unknown')}"

    def _state_version(self) -> str:
        """当前存储状态版本（schema 版本 + 记忆行数；可校验、非自造）。"""
        import sqlite3

        try:
            with self.services.database.connect() as conn:
                row = conn.execute(
                    "SELECT value FROM _schema_meta WHERE key = 'version'"
                ).fetchone()
                count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            version = str(row[0]) if row is not None else "unknown"
            return f"sqlite:{version}:rows:{int(count)}"
        except sqlite3.Error:
            return "sqlite:unknown"

    # ------------------------------------------------------------------
    # snapshot / restore / restart
    # ------------------------------------------------------------------

    async def snapshot(self, checkpoint_id: str) -> MemoryStateSnapshot:
        """完成屏障后保存状态快照（SQLite backup API + 原子 manifest）。

        后台任务终态取自本 adapter 的 registry 快照（评测链路里抽取由 settle
        同步完成，这里只记录证据；若有 pending 任务则拒绝快照而不是装睡）。
        """
        background = self._registry.snapshot()
        pending = list(background.get("pending") or [])
        if pending:
            raise CheckpointError("BARRIER_INCOMPLETE")
        return self._checkpoints.capture(
            checkpoint_id,
            self.services.database,
            workspace_path=self.workspace_path,
            vector_store=self._vector_store,
            scope_mapping={
                "subject_id": [self._scope.subject_id],
                "workspace_id": [self._scope.workspace_id],
            },
            origin_refs=dict(self._origin_refs),
            background_tasks={
                "evidence_status": background.get("evidence_status"),
                "operations": background.get("operations"),
            },
        )

    async def restore(self, state: MemoryStateSnapshot) -> None:
        """从持久化快照恢复到**独立 probe 子目录**，并重建全部运行时对象。"""
        await self._apply_snapshot(state, probe_label=f"restore-{uuid.uuid4().hex[:8]}")

    async def restart(self, state: MemoryStateSnapshot) -> None:
        """关闭当前运行时对象并从持久化文件重建（不留旧 Python 缓存）。"""
        await self._apply_snapshot(state, probe_label=f"restart-{uuid.uuid4().hex[:8]}")

    async def _apply_snapshot(
        self, state: MemoryStateSnapshot, *, probe_label: str
    ) -> None:
        if self._closed:
            raise RuntimeError("adapter is closed")
        probe_root = _resolve_inside(
            self._trial_root, self._trial_root / _PROBES_DIRNAME / state.checkpoint_id
        )
        probe_root.mkdir(parents=True, exist_ok=True)
        destination = _resolve_inside(probe_root, probe_root / probe_label)
        result = self._checkpoints.restore(
            state,
            destination,
            expected_vector_version=(
                None
                if not self._vector_expected
                else f"ready:{getattr(self._vector_store, 'model_name', 'unknown')}"
                if self._vector_is_ready()
                else None
            ),
        )
        # 产品规则「复制目录会换新键」：probe 解析身份会旋转出自己的隔离键。
        # 把恢复出的记忆行重新绑定到 probe 身份（probe 接管），保证恢复后的
        # 抽取/检索在同一隔离键下自洽——只动本 probe 自己的 DB，不触他者。
        self.workspace_path = _resolve_inside(self._trial_root, result.workspace_path)
        self.workspace_id = memory_workspace_id(str(self.workspace_path))
        original_key = result.manifest.workspace_identity
        if original_key and original_key != self.workspace_id:
            self._adopt_restored_state(original_key)
        self._rebuild_runtime_on()

    def _adopt_restored_state(self, original_workspace_id: str) -> None:
        """把恢复出的行从原隔离键重新绑定到本 probe 的隔离键（probe 接管）。"""
        database = MemoryDatabase(self.workspace_path)
        database.init_schema()
        with database.connect() as conn:
            for table in ("memories", "episodes"):
                conn.execute(
                    f"UPDATE {table} SET workspace_id = ? WHERE workspace_id = ?",
                    (self.workspace_id, original_workspace_id),
                )
            conn.execute(
                "UPDATE scratchpad SET workspace_id = ? WHERE workspace_id = ?",
                (self.workspace_id, original_workspace_id),
            )

    def _rebuild_runtime_on(self) -> None:
        """在（恢复出的）工作区路径上重建全部运行时对象。

        丢掉旧 extractor / 检索引擎 / lifecycle / services 引用——extractor 的
        增量游标、记忆行、派生状态全部来自恢复出的持久化文件，没有隐藏的
        Python 缓存。transcript 由执行输入（场景事件）如实重建，session key
        不变，因此恢复后的 DB 里的抽取游标继续生效。
        """
        self.services = MemoryServices.for_workspace(self.workspace_id, self.workspace_path)
        self._extractor = MemoryExtractor(
            self.services.database,
            self._runtime,
            workspace_id=self.workspace_id,
            user_id=self._scope.subject_id,
            clock=self._clock,
        )
        self._store = MemoryStoreAdapter(
            self.services.database, vector_store=self._vector_store, clock=self._clock
        )
        self._retrieval = RetrievalEngine(store=self._store, clock=self._clock)
        self._lifecycle = MemoryLifecycle(self.workspace_id, self.services, clock=self._clock)
        # transcript 重建：执行输入是场景事实（不是 gold），恢复后 settle 的
        # 增量游标由恢复出的 DB 决定。
        self._rebuild_sessions()

    def _rebuild_sessions(self) -> None:
        """从已 ingest 的场景事件重建 Session（不丢 transcript，不重复抽取）。"""
        rebuilt: dict[str, Session] = {}
        for event_id in list(self._sessions):
            session = self._sessions[event_id]
            rebuilt[event_id] = Session(
                key=session.key,
                messages=list(session.messages),
                metadata={
                    "workspace_scope": {"project_path": str(self.workspace_path)},
                },
            )
        self._sessions = rebuilt

    # ------------------------------------------------------------------
    # recall / answer
    # ------------------------------------------------------------------

    async def recall(self, probe: MemoryProbe) -> RecallResult:
        """产品检索引擎的真实召回（工作区可见集合 = 全局 + 本工作区）。

        向量被期望但缺失时：返回 ``capability_missing``、空结果、
        ``index_version=None``——绝不回退成假向量成绩。
        """
        if self._mode == "no_persistent_memory":
            return RecallResult(
                probe_id=probe.id,
                items=[],
                coverage=_coverage(reasons=["memory_disabled"]),
            )
        if self._vector_expected and not self._vector_is_ready():
            return RecallResult(
                probe_id=probe.id,
                items=[],
                coverage=_coverage(reasons=["capability_missing"]),
                state_version=self._state_version(),
                index_version=None,
            )

        block, injected_ids = await self._retrieval.retrieve_with_ids(
            query=probe.question,
            recent_messages=[],
            workspace_ids=list(memory_visibility_ids(self.workspace_id)),
        )
        _ = block  # 注入块由 answer() 消费；recall 只上报结构化结果
        items = self._rows_to_recall_items(injected_ids)
        return RecallResult(
            probe_id=probe.id,
            items=items,
            coverage=_coverage(scored=1, planned=1),
            state_version=self._state_version(),
            index_version=self._index_version(),
        )

    def _rows_to_recall_items(self, memory_ids: list[str]) -> list[MemoryRecallItem]:
        items: list[MemoryRecallItem] = []
        for rank, memory_id in enumerate(memory_ids, start=1):
            with self.services.database.connect() as conn:
                row = get_memory(conn, memory_id)
            if row is None:
                continue
            items.append(
                MemoryRecallItem(
                    rank=rank,
                    key=row.predicate or row.subject or "memory",
                    value=row.content,
                    subject=row.subject or None,
                    origin_refs=[memory_id],
                    score=None,
                )
            )
        return items

    async def answer(self, probe: MemoryProbe) -> ProbeExecution:
        """新会话 + 真实 provider：产品检索注入块 + provider 回答。

        新会话不含历史 transcript（防原始历史成为 query 额外输入）；注入块
        完全来自检索引擎的真实召回。
        """
        probe_session_key = f"evaluation:{self._scope.subject_id}:probe:{probe.id}"
        _ = probe_session_key  # 新会话只以 key 区分；transcript 不进入上下文

        import time as _time

        started = _time.perf_counter()
        if self._mode == "no_persistent_memory":
            block = ""
            injected_ids: list[str] = []
            recalled: list[MemoryRecallItem] = []
        elif self._vector_expected and not self._vector_is_ready():
            recalled = []
            injected_ids = []
            block = ""
        else:
            block, injected_ids = await self._retrieval.retrieve_with_ids(
                query=probe.question,
                recent_messages=[],
                workspace_ids=list(memory_visibility_ids(self.workspace_id)),
            )
            recalled = self._rows_to_recall_items(injected_ids)

        messages: list[dict[str, Any]] = []
        if block:
            messages.append({"role": "system", "content": block})
        messages.append({"role": "user", "content": probe.question})

        provider = getattr(self._runtime, "provider")
        answer_text: str | None = None
        usage = MemoryUsage()
        try:
            response = await provider.chat_with_retry(messages=messages)
        except Exception as exc:  # noqa: BLE001 - 模型失败如实上报，不伪装回答
            answer_text = f"PROVIDER_ERROR: {exc}"
        else:
            answer_text = _response_text(response)
            usage = _response_usage(response)

        return ProbeExecution(
            probe_id=probe.id,
            answer=answer_text,
            recalled=recalled,
            injected_evidence=injected_ids,
            usage=usage,
            coverage=(
                _coverage(reasons=["memory_disabled"])
                if self._mode == "no_persistent_memory"
                else _coverage(
                    reasons=["capability_missing"]
                    if self._vector_expected and not self._vector_is_ready()
                    else [],
                    scored=1,
                    planned=1,
                )
            ),
            timing={"answer_ms": int((_time.perf_counter() - started) * 1000)},
        )

    # ------------------------------------------------------------------
    # forget / rebuild
    # ------------------------------------------------------------------

    async def forget(self, request: ForgetRequest) -> ForgetResult:
        """真实产品删除；没有测试专用清库逻辑。

        - ``product_request``：走 ``nanobot.webui.memory_api.delete_memory``
          （产品 WebUI 的删除入口：校验存在性 → 删行 → 向量移除挂钩 →
          MEMORY.md 派生刷新）。
        - ``admin_delete``：走管理原语 ``repository.delete_memory`` + 向量移除
          + 派生刷新（跳过 WebUI 校验层的真实管理路径）。

        目标解析：``target_id`` 优先；否则在可见集合内按 ``predicate == fact_key``
        或 ``subject == fact_key`` 精确匹配（评测服务端也可先解析成 id 再下发）。
        """
        if self._mode == "no_persistent_memory":
            return ForgetResult(status="unsupported", reason_codes=["memory_disabled"])

        target_ids = self._resolve_forget_targets(request)
        deleted: list[str] = []
        for memory_id in target_ids:
            if request.mode == "product_request":
                from nanobot.webui.memory_api import delete_memory as product_delete_memory

                product_delete_memory(self.services, memory_id)
            else:
                with self.services.database.connect() as conn:
                    repo_delete_memory(conn, memory_id)
                try:
                    from nanobot.memory.vector.indexer import remove_memory_best_effort

                    remove_memory_best_effort(memory_id)
                except Exception:  # noqa: BLE001 - 向量缺失不阻断删除
                    pass
                self._lifecycle.refresh_memory_md_sync(self.workspace_id)
            deleted.append(memory_id)

        # 状态核对：删除后存储里确实查不到（state 检查）
        state_checked = True
        for memory_id in deleted:
            with self.services.database.connect() as conn:
                if get_memory(conn, memory_id) is not None:
                    state_checked = False

        return ForgetResult(
            status="completed",
            deleted_ids=deleted,
            state_checked=state_checked,
        )

    def _resolve_forget_targets(self, request: ForgetRequest) -> list[str]:
        with self.services.database.connect() as conn:
            from nanobot.memory.repository import list_memories

            rows = list_memories(
                conn,
                workspace_ids=list(memory_visibility_ids(self.workspace_id)),
            )
        if request.target_id:
            return [str(request.target_id)] if any(row.id == str(request.target_id) for row in rows) else []
        if request.fact_key is None:
            return []
        key = str(request.fact_key)
        return [
            row.id
            for row in rows
            if row.predicate == key or row.subject == key
        ]

    async def rebuild(self) -> SettleResult:
        """真实重建：MEMORY.md 派生 +（向量就绪时）全量对账。"""
        if self._mode == "no_persistent_memory":
            return SettleResult(status="skipped_by_policy", reason_codes=["memory_disabled"])
        completed: list[str] = []
        reason_codes: list[str] = []
        derived = self._lifecycle.refresh_memory_md_sync(self.workspace_id)
        if derived.get("status") in ("ok", "skipped"):
            completed.append("derived_memory_md")
        else:
            reason_codes.append("derived_memory_md_failed")

        if self._vector_is_ready():
            try:
                from nanobot.memory.vector.indexer import MemoryIndexer

                MemoryIndexer(self._vector_store, self.services.database).sync_from_sqlite()
                completed.append("vector_index")
            except Exception as exc:  # noqa: BLE001
                reason_codes.append(f"vector_reindex_failed:{exc}")
        elif self._vector_expected:
            # 向量被期望但缺失：显式 capability_missing，不假装重建成功。
            reason_codes.append("capability_missing")

        status = "completed"
        if "derived_memory_md" not in completed:
            status = "failed"
        elif "capability_missing" in reason_codes:
            status = "skipped_by_policy"
        return SettleResult(
            status=status,  # type: ignore[arg-type]
            completed_stages=completed,
            reason_codes=reason_codes,
            state_version=self._state_version(),
            index_version=self._index_version(),
        )

    # ------------------------------------------------------------------
    # 评测侧只读辅助（不属于契约，仅供测试/评分端取证）
    # ------------------------------------------------------------------

    def list_memory_rows(self) -> list[Any]:
        """当前可见集合内的全部记忆行（评测取证用，只读）。"""
        from nanobot.memory.repository import list_memories

        with self.services.database.connect() as conn:
            return list_memories(
                conn,
                workspace_ids=list(memory_visibility_ids(self.workspace_id)),
            )


def _coverage(*, scored: int = 0, planned: int = 0, reasons: list[str] | None = None):
    from nanobot.evaluation.memory.models import MemoryCoverage

    return MemoryCoverage(scored=scored, planned=planned, reasons=list(reasons or []))


def _response_text(response: Any) -> str | None:
    """从 provider 响应里取文本；取不到如实返回 None，不编造。"""
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    message = getattr(response, "message", None)
    if message is not None:
        text = getattr(message, "content", None)
        if isinstance(text, str):
            return text
    return None


def _response_usage(response: Any) -> MemoryUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return MemoryUsage()
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if input_tokens is None and output_tokens is None:
        return MemoryUsage()
    try:
        return MemoryUsage(
            input_tokens=int(input_tokens) if input_tokens is not None else None,
            output_tokens=int(output_tokens) if output_tokens is not None else None,
            total_tokens=(
                int(input_tokens) + int(output_tokens)
                if input_tokens is not None and output_tokens is not None
                else None
            ),
            physical_requests=1,
            source="reported",
        )
    except (TypeError, ValueError):
        return MemoryUsage()
