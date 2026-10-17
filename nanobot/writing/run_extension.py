"""把写作会话变成团队运行的**可信扩展开关**。

团队调度只认识 ``leader_context`` / ``member_tools`` 两个抽象方法；本模块负责：
读取会话上的文章绑定、校验团队/成员/运行身份、按角色生成写作工具。普通聊天、克隆
团队、非 WebUI 会话都会在这里拿到空列表，写作工具名不会出现在它们的 registry 里。
"""

from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from loguru import logger

from nanobot.agent.tools.base import Tool
from nanobot.writing.database import WritingDatabase
from nanobot.writing.session_binding import (
    WRITING_ARTICLE_METADATA_KEY,
    WritingSessionBinder,
)
from nanobot.writing.tools import WritingToolRuntime, build_writing_tools

READ_ROLES = frozenset({
    "writing-editor", "writing-researcher", "writing-architect",
    "writing-author", "writing-reviewer",
})
LEADER_ROLE = "writing-editor"
TEAM_BINDING_KEY = "team_binding"
WRITING_CONTEXT_KEY = "writing_context"
_MAX_SEEDS = 128


class HandleLike(Protocol):
    run_id: str
    turn_id: str
    session_key: str
    revision: Any

    @property
    def accepts_writes(self) -> bool: ...


@dataclass(frozen=True)
class _WritingSeed:
    article_id: str
    base_revision: int
    base_digest: str
    member_id: str
    task: str
    selection: Mapping[str, Any] | None


class WritingRunExtension:
    """一个 gateway 一个实例；种子按 runId 保存，运行结束由 ``forget`` 清掉。"""

    def __init__(
        self,
        *,
        db: WritingDatabase,
        binder: WritingSessionBinder,
        team_ids: Callable[[], set[str]],
        publish_change: Callable[[str, Mapping[str, Any]], None] | None = None,
        logger_: Any = logger,
    ) -> None:
        self._db = db
        self._binder = binder
        self._team_ids = team_ids
        self._publish_change = publish_change
        self._logger = logger_
        self._seeds: OrderedDict[str, _WritingSeed] = OrderedDict()
        self._lock = threading.Lock()

    # ---- Leader ----------------------------------------------------------

    def leader_context(
        self,
        msg: Any,
        session: Any,
        handle: HandleLike,
    ) -> tuple[Sequence[Tool], str]:
        seed = self._seed_for(msg, session, handle, leader=True)
        if seed is None:
            return (), ""
        runtime = self._runtime(handle, seed, LEADER_ROLE, msg)
        self._remember(handle.run_id, seed)
        return build_writing_tools(runtime), self._prompt_for(seed)

    # ---- Members ---------------------------------------------------------

    def member_tools(self, handle: HandleLike, member_id: str) -> Sequence[Tool]:
        role = self._member_role(handle, member_id)
        if role is None:
            return ()
        with self._lock:
            seed = self._seeds.get(handle.run_id)
        if seed is None:
            return ()
        if not self._team_matches(
            session_key=handle.session_key, handle=handle, article_id=seed.article_id
        ):
            return ()
        runtime = self._runtime(handle, seed, role, None)
        return build_writing_tools(runtime)

    def forget(self, run_id: str) -> None:
        with self._lock:
            self._seeds.pop(run_id, None)

    # ---- 校验 ------------------------------------------------------------

    def _seed_for(
        self,
        msg: Any,
        session: Any,
        handle: HandleLike,
        *,
        leader: bool,
    ) -> _WritingSeed | None:
        article_id = self._trusted_session(session, msg)
        if article_id is None:
            return None
        if not self._team_matches(
            session_key=handle.session_key, handle=handle, article_id=article_id
        ):
            return None
        if leader and str(handle.revision.definition.leader_agent_id) != LEADER_ROLE:
            return None
        metadata = _as_dict(getattr(msg, "metadata", None))
        raw = _as_dict(metadata.get(WRITING_CONTEXT_KEY)) if metadata is not None else None
        if raw is not None:
            try:
                seed = _parse_seed(raw)
            except (KeyError, TypeError, ValueError):
                return None
            with self._lock:
                self._seeds[handle.run_id] = seed
                self._seeds.move_to_end(handle.run_id)
                while len(self._seeds) > _MAX_SEEDS:
                    self._seeds.popitem(last=False)
            return seed
        with self._lock:
            return self._seeds.get(handle.run_id)

    def _trusted_session(self, session: Any, msg: Any) -> str | None:
        """可信写作会话返回文章 id；任何一项不满足都返回 ``None``。"""
        session_key = str(getattr(session, "key", "") or "")
        if not session_key.startswith("websocket:"):
            return None
        if str(getattr(msg, "channel", "") or "") != "websocket":
            return None
        metadata = _as_dict(getattr(session, "metadata", None))
        binding = _as_dict(metadata.get(TEAM_BINDING_KEY)) if metadata is not None else None
        writing = (
            _as_dict(metadata.get(WRITING_ARTICLE_METADATA_KEY))
            if metadata is not None
            else None
        )
        if binding is None or writing is None:
            return None
        article_id = writing.get("articleId")
        if not isinstance(article_id, str) or not article_id:
            return None
        try:
            resolved = self._binder.resolve(article_id)
        except Exception:  # noqa: BLE001 - 绑定坏了就当作没有写作身份
            return None
        if resolved is None or resolved.session_key != session_key:
            return None
        return article_id

    def _team_matches(self, *, session_key: str, handle: HandleLike, article_id: str) -> bool:
        """只认「绑定进来的内置团队」：克隆团队 id 不同，拿不到写作工具。"""
        definition = handle.revision.definition
        if str(definition.id) not in self._team_ids():
            return False
        try:
            resolved = self._binder.resolve(article_id)
        except Exception:  # noqa: BLE001 - 读不到绑定就按不匹配处理
            return False
        return (
            resolved is not None
            and resolved.session_key == session_key
            and resolved.team_id == str(definition.id)
        )

    def _runtime(
        self,
        handle: HandleLike,
        seed: _WritingSeed,
        role: str,
        msg: Any | None,
    ) -> WritingToolRuntime:
        resolved = self._binder.resolve(seed.article_id)
        if resolved is None:
            raise RuntimeError("writing session is not bound")
        origin_message_id = ""
        if msg is not None:
            metadata = _as_dict(getattr(msg, "metadata", None))
            if metadata is not None:
                raw = metadata.get("message_id")
                if isinstance(raw, str):
                    origin_message_id = raw

        def cancelled() -> bool:
            return not handle.accepts_writes

        actor_names: dict[str, str] = {}
        leader = getattr(handle.revision, "leader", None)
        leader_name = str(getattr(leader, "name", "") or "")
        if leader_name:
            actor_names[str(getattr(leader, "id", ""))] = leader_name
        for item in handle.revision.members:
            member_name = str(getattr(item.profile, "name", "") or "")
            if member_name:
                actor_names[str(item.member.member_id)] = member_name

        def on_change(body: Mapping[str, Any]) -> None:
            if self._publish_change is None:
                return
            try:
                actor_id = str(body.get("actor") or "")
                event = {
                    "article_id": seed.article_id,
                    "revision": body.get("revision"),
                    "digest": body.get("digest"),
                    "change_id": body.get("change_id"),
                    "kind": body.get("kind"),
                    "actor": body.get("actor"),
                }
                if actor_names.get(actor_id):
                    event["actor_name"] = actor_names[actor_id]
                self._publish_change(resolved.chat_id, event)
            except Exception as error:  # noqa: BLE001 - 投影失败不能回滚已提交的写入
                self._logger.warning(f"writing change publish failed: {error}")

        return WritingToolRuntime(
            db=self._db,
            article_id=seed.article_id,
            chat_id=resolved.chat_id,
            session_key=resolved.session_key,
            team_id=resolved.team_id,
            team_revision=resolved.team_revision,
            run_id=handle.run_id,
            turn_id=handle.turn_id,
            member_id=role,
            base_revision=seed.base_revision,
            base_digest=seed.base_digest,
            origin_message_id=origin_message_id,
            cancelled=cancelled,
            on_change=on_change,
        )

    def _member_role(self, handle: HandleLike, member_id: str) -> str | None:
        for item in handle.revision.members:
            if str(item.member.member_id) != str(member_id):
                continue
            role = str(item.profile.id)
            return role if role in READ_ROLES else None
        return None

    def _remember(self, run_id: str, seed: _WritingSeed) -> None:
        with self._lock:
            self._seeds[run_id] = seed
            self._seeds.move_to_end(run_id)

    @staticmethod
    def _prompt_for(seed: _WritingSeed) -> str:
        lines = [
            "# 创作会话",
            f"本次基于文章 `{seed.article_id}` 的版本 {seed.base_revision}。",
            "读取/写入文章必须使用 writing_* 工具；不要把文章内容改写成另一篇文章。",
            "只有作者明确要求修改时才写正文；解释、讨论与备选方案只回答，不落库。",
        ]
        if seed.member_id:
            lines.append(
                f"作者点名优先由成员 `{seed.member_id}` 处理；仍通过 team_dispatch 派活，"
                "并在最终答复中保留该成员的实际交付。"
            )
        if seed.task:
            lines.append(f"本次任务类型：{seed.task}。")
        selection = seed.selection
        if selection:
            start = selection.get("start")
            end = selection.get("end")
            text = str(selection.get("text") or "")
            lines.append(f"作者选区（Python 码点 [{start}, {end})）：\n{text}")
            if selection.get("prefix"):
                lines.append(f"选区前文：{selection['prefix']}")
            if selection.get("suffix"):
                lines.append(f"选区后文：{selection['suffix']}")
        return "\n".join(lines)


def _as_dict(value: object) -> dict[str, Any] | None:
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def _parse_seed(raw: Mapping[str, Any]) -> _WritingSeed:
    article_id = raw["article_id"]
    if not isinstance(article_id, str) or not article_id:
        raise ValueError("article_id is required")
    revision = raw["revision"]
    digest = raw["digest"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("revision is required")
    if not isinstance(digest, str) or not digest:
        raise ValueError("digest is required")
    selection = raw.get("selection")
    if selection is not None and not isinstance(selection, Mapping):
        raise ValueError("selection must be an object")
    return _WritingSeed(
        article_id=article_id,
        base_revision=revision,
        base_digest=digest,
        member_id=str(raw.get("member_id") or ""),
        task=str(raw.get("task") or ""),
        selection=cast(Mapping[str, Any] | None, selection),
    )


def publish_writing_changed(
    *,
    bus: Any,
    channel_name: str,
    chat_id: str,
    body: Mapping[str, Any],
) -> None:
    """把工具已提交的变更作为 ``writing_changed`` 帧投出去（旁路，不影响写结果）。"""

    async def _publish() -> None:
        from nanobot.bus.outbound_events import (
            WritingChangedEvent,
            outbound_message_for_event,
        )

        message = outbound_message_for_event(
            channel=channel_name,
            chat_id=chat_id,
            event=WritingChangedEvent(body=dict(body)),
        )
        await bus.publish_outbound(message)

    try:
        task = asyncio.get_running_loop().create_task(_publish())
    except RuntimeError:
        return

    def _log(future: asyncio.Task[Any]) -> None:
        if future.cancelled():
            return
        error = future.exception()
        if error is not None:
            logger.warning(f"writing_changed publish failed: {error}")

    task.add_done_callback(_log)
