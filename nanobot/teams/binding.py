"""团队绑定的服务端暂存与持久化（契约 §2 的懒创建）。

新建聊天时**只暂存**绑定：第一条消息把它写进 ``session.metadata["team_binding"]``
之后才允许启动 Leader。这样「先普通回答、再变团队」的竞态没有机会出现——同一会话
的绑定要么在第一条消息前就落地，要么这条消息根本不按团队模式跑。

暂存是进程内的：重启后聊天仍绑着原团队（真值在 session 文件里），只有「还没发出
第一条消息」的那批空聊天会退化成未绑定，此时明确报 :class:`TeamBindingExpired`
要求重选，而不是静默按普通聊天回答。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from nanobot.teams.models import TeamBinding

# session.metadata 里的键；前端 ``types.ts`` 与投影层都按这个名字读。
SESSION_TEAM_BINDING_KEY = "team_binding"

# 空聊天的暂存寿命：足够用户选好团队再发出第一条消息，又不至于让绑定无限期挂着。
DEFAULT_STAGING_TTL_SECONDS = 600.0


class TeamBindingExpired(RuntimeError):  # noqa: N818  # 对应契约 §2 的 TEAM_BINDING_EXPIRED
    """暂存已失效：必须让客户端重新选团队，不能退回普通聊天。"""


class _AdmittingService(Protocol):
    """binding 只用到服务层的准入检查，不依赖整个 TeamService 类型。"""

    def prepare_new_run(self, team_id: str, *, revision: int | None = None) -> Any: ...


@dataclass(frozen=True)
class _Staged:
    binding: TeamBinding
    expires_at: float


class TeamBindingStore:
    """按 chatId 暂存待生效的团队绑定；取走是一次性的。

    一次性和过期都靠同一把锁判定：两个首消息同时到达时只有一个能拿到绑定，
    否则同一会话会起两个 Leader（契约 §2 的单会话串行调度）。
    """

    def __init__(
        self,
        service: _AdmittingService,
        *,
        now: Callable[[], float],
        ttl_seconds: float = DEFAULT_STAGING_TTL_SECONDS,
    ) -> None:
        self._service = service
        self._now = now
        self._ttl = float(ttl_seconds)
        self._lock = threading.Lock()
        self._staged: dict[str, _Staged] = {}

    def stage(self, *, chat_id: str, team_id: str, revision: int | None = None) -> TeamBinding:
        """准入检查在暂存之前：归档/禁用/档案缺失的团队不会留下半个绑定。"""
        snapshot = self._service.prepare_new_run(team_id, revision=revision)
        definition = snapshot.definition
        binding = TeamBinding(
            team_id=definition.id,
            team_revision=definition.revision,
            team_name=definition.name,
        )
        with self._lock:
            self._staged[_require_chat(chat_id)] = _Staged(
                binding=binding, expires_at=self._now() + self._ttl
            )
        return binding

    def claim(self, chat_id: str, *, require: bool = False) -> TeamBinding | None:
        """取走并清除暂存。``require=True`` 时空聊天按契约报错而不是返回 None。"""
        key = _require_chat(chat_id)
        with self._lock:
            staged = self._staged.get(key)
            if staged is not None and staged.expires_at > self._now():
                del self._staged[key]
                return staged.binding
            self._staged.pop(key, None)
        if require:
            raise TeamBindingExpired(f"TEAM_BINDING_EXPIRED chat_id={key}")
        return None

    def drop(self, chat_id: str) -> None:
        """换团队或删聊天时丢掉旧暂存，避免下一条消息吃到过期的选择。"""
        with self._lock:
            self._staged.pop(_require_chat(chat_id), None)

    def persist(self, session: Any, binding: TeamBinding) -> TeamBinding:
        """把绑定写进 session.metadata，并丢掉同 chatId 的暂存。

        不落盘：保存由调用方的回合路径负责，暂存层不碰会话文件。
        """
        session.metadata[SESSION_TEAM_BINDING_KEY] = binding.to_dict()
        chat_id = session_chat_id(session)
        if chat_id:
            with self._lock:
                self._staged.pop(chat_id, None)
        return binding

    def bound_team_id(self, session: Any) -> str | None:
        """已持久化的绑定读法；没有绑定返回 None（普通聊天，不是错误）。"""
        binding = binding_from_metadata(getattr(session, "metadata", None))
        return binding.team_id if binding is not None else None


def binding_from_metadata(metadata: dict[str, Any] | None) -> TeamBinding | None:
    raw = (metadata or {}).get(SESSION_TEAM_BINDING_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return TeamBinding.from_dict(cast("dict[str, Any]", raw))
    except Exception:
        # 坏掉的绑定记录不能把整个会话读路径带崩，也不能被当成团队会话继续跑。
        return None


def _require_chat(chat_id: object) -> str:
    if not isinstance(chat_id, str) or not chat_id.strip():
        raise ValueError("chat_id is required")
    return chat_id.strip()


def session_chat_id(session: Any) -> str:
    """从会话 key（形如 ``<channel>:<chatId>``）取 chatId；取不到返回空串。"""
    key = str(getattr(session, "key", "") or "")
    return key.split(":", 1)[1] if ":" in key else key
