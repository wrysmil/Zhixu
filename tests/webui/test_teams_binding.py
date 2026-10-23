"""TeamBindingStore 与首条消息的持久化（plan Task 5）。

契约 §2/§4 的懒创建口径：新建聊天时只在服务端**暂存**绑定，第一条消息把它持久化进
session.metadata 之后才允许启动 Leader。所以这里必须保证：

1. 取走是一次性的——两个并发首消息只有一个能拿到，另一个必须走「已绑定/无绑定」分支，
   否则同一轮会起两个 Leader。
2. 暂存不是持久化：``stage`` 不碰 session 文件。
3. 暂存过期要说清「要重选」，不能静默退回普通聊天。
4. 过期时间用注入的时钟，测试不靠 sleep。
5. 写会话只认服务端带来的绑定：已绑定的会话不会被消息里的值覆盖。
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from nanobot.teams.binding import (
    SESSION_TEAM_BINDING_KEY,
    TeamBindingExpired,
    TeamBindingStore,
    persist_message_binding,
)
from nanobot.teams.models import TeamBinding
from nanobot.teams.repository import TeamNotFound
from nanobot.teams.service import TeamArchived, TeamDisabled


class _Service:
    """内存替身：只做准入检查与归档，不碰 SQLite。"""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.archived: set[str] = set()
        self.prepared: list[tuple[str, int | None]] = []

    def prepare_new_run(self, team_id: str, *, revision: int | None = None) -> Any:
        self.prepared.append((team_id, revision))
        if not self.enabled:
            raise TeamDisabled("agents.teams.enabled is false")
        if team_id == "ghost":
            raise TeamNotFound(team_id)
        if team_id in self.archived:
            raise TeamArchived(team_id)
        return _Snapshot(team_id)


class _Definition:
    def __init__(self, team_id: str) -> None:
        self.id = team_id
        self.revision = 3
        self.name = "内容团队"


class _Snapshot:
    def __init__(self, team_id: str) -> None:
        self.definition = _Definition(team_id)


@pytest.fixture
def service() -> _Service:
    return _Service()


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _store(service: _Service, clock: _Clock, ttl: float = 600.0) -> TeamBindingStore:
    return TeamBindingStore(service, now=clock, ttl_seconds=ttl)


def test_stage_then_claim_returns_the_binding_once(service: _Service) -> None:
    clock = _Clock()
    store = _store(service, clock)

    binding = store.stage(chat_id="chat-1", team_id="content-team")
    assert binding == TeamBinding(team_id="content-team", team_revision=3, team_name="内容团队")

    assert store.claim("chat-1") == binding
    # 第二次取必须空：否则同一会话会起两个 Leader。
    assert store.claim("chat-1") is None


def test_claim_of_a_plain_chat_is_not_expired(service: _Service) -> None:
    """从没暂存过团队 = 普通聊天，必须安静返回 None，而不是报错。

    暂存只在选团队时产生，所以「查无此项」不能当成过期；否则每条普通消息都要报错。
    """
    store = _store(service, _Clock())
    assert store.claim("never-bound") is None


def test_staged_binding_expires_without_touching_the_wall_clock(service: _Service) -> None:
    """暂存过期后要重选团队（契约 §4），不能静默按普通聊天回答。"""
    clock = _Clock()
    store = _store(service, clock, ttl=30.0)
    store.stage(chat_id="chat-1", team_id="content-team")

    clock.now += 29.0
    assert store.claim("chat-1") is not None

    store.stage(chat_id="chat-2", team_id="content-team")
    clock.now += 60.0
    with pytest.raises(TeamBindingExpired, match="TEAM_BINDING_EXPIRED"):
        store.claim("chat-2")
    # 报过一次就丢掉：同一条消息重试时按普通聊天走，而不是反复报错。
    assert store.claim("chat-2") is None


def test_expired_staging_is_readable_for_reconnect(service: _Service) -> None:
    """attach 时要把「暂存已失效」告诉客户端，让它显示重选提示。"""
    clock = _Clock()
    store = _store(service, clock, ttl=30.0)
    binding = store.stage(chat_id="chat-1", team_id="content-team")
    clock.now += 31.0

    assert store.expired_binding("chat-1") == binding
    # 一次性告知：第二次读已经没有了，客户端不会再收到同一个过期事件。
    assert store.expired_binding("chat-1") is None


def test_expired_staging_falls_out_of_memory_after_a_grace(service: _Service) -> None:
    """过期记录要留一个 TTL 的窗口用来报错，之后被暂存动作扫掉，不无限增长。"""
    clock = _Clock()
    store = _store(service, clock, ttl=30.0)
    store.stage(chat_id="stale", team_id="content-team")

    clock.now += 31.0
    store.stage(chat_id="fresh", team_id="content-team")
    assert store.expired_binding("stale") is not None

    clock.now += 61.0
    store.stage(chat_id="newer", team_id="content-team")
    assert store.expired_binding("stale") is None


def test_stage_runs_the_admission_check_and_leaves_nothing_on_failure(service: _Service) -> None:
    service.archived.add("content-team")
    store = _store(service, _Clock())

    with pytest.raises(TeamArchived):
        store.stage(chat_id="chat-1", team_id="content-team")
    assert store.claim("chat-1") is None

    with pytest.raises(TeamNotFound):
        store.stage(chat_id="chat-2", team_id="ghost")
    assert store.claim("chat-2") is None

    service.enabled = False
    with pytest.raises(TeamDisabled):
        store.stage(chat_id="chat-3", team_id="content-team")


def test_concurrent_claims_only_one_wins(service: _Service) -> None:
    """两个首消息同时到达：只有一个能拿到绑定。"""
    store = _store(service, _Clock())
    store.stage(chat_id="chat-1", team_id="content-team")
    results: list[Any] = []
    barrier = threading.Barrier(2)

    def _claim() -> None:
        barrier.wait()
        results.append(store.claim("chat-1"))

    threads = [threading.Thread(target=_claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(item is not None for item in results) == 1


def test_drop_clears_staging_when_the_chat_is_replaced(service: _Service) -> None:
    store = _store(service, _Clock())
    store.stage(chat_id="chat-1", team_id="content-team")
    store.drop("chat-1")
    assert store.claim("chat-1") is None


def test_persist_writes_the_camel_case_binding_into_session_metadata(service: _Service) -> None:
    """持久化形状必须是 ``team_binding`` + camelCase 值（契约 §2 与前端 types.ts 一致）。"""

    class _Session:
        key = "webui:chat-1"

        def __init__(self) -> None:
            self.metadata: dict[str, Any] = {}

        def add_meta(self, key: str, value: Any) -> None:
            self.metadata[key] = value

    session = _Session()
    store = _store(service, _Clock())
    binding = store.stage(chat_id="chat-1", team_id="content-team")
    persisted = store.persist(session, binding)

    assert persisted is binding
    assert session.metadata["team_binding"] == {
        "teamId": "content-team",
        "teamRevision": 3,
        "teamName": "内容团队",
    }
    # 已持久化的会话不再需要暂存；重连走 session 读数，不放回暂存池。
    assert store.claim("chat-1") is None
    assert store.bound_team_id(session) == "content-team"


def test_binding_of_a_session_without_team_reads_as_none() -> None:
    class _Session:
        key = "webui:chat-2"
        metadata: dict[str, Any] = {}

    store = _store(_Service(), _Clock())
    assert store.bound_team_id(_Session()) is None


class _Session:
    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        self.key = "webui:chat-1"
        self.metadata: dict[str, Any] = dict(metadata or {})


class _Message:
    def __init__(self, metadata: Any) -> None:
        self.metadata = metadata


_BINDING = TeamBinding(team_id="content-team", team_revision=3, team_name="内容团队")


def test_persist_message_binding_lands_before_the_leader_runs() -> None:
    """首条团队消息：绑定落在 session.metadata 上，调用方据此落盘，然后才装配 Leader。"""
    session = _Session()
    message = _Message({SESSION_TEAM_BINDING_KEY: _BINDING.to_dict()})

    assert persist_message_binding(session, message) is True
    assert session.metadata[SESSION_TEAM_BINDING_KEY] == {
        "teamId": "content-team",
        "teamRevision": 3,
        "teamName": "内容团队",
    }


def test_persist_message_binding_never_overrides_an_existing_binding() -> None:
    """会话已经绑定过就以此为真：消息里的团队快照不能改写既有绑定（契约 §4）。"""
    session = _Session(
        {SESSION_TEAM_BINDING_KEY: TeamBinding("other-team", 4, "别的团队").to_dict()}
    )
    message = _Message({SESSION_TEAM_BINDING_KEY: _BINDING.to_dict()})

    assert persist_message_binding(session, message) is False
    assert session.metadata[SESSION_TEAM_BINDING_KEY]["teamId"] == "other-team"


def test_persist_message_binding_ignores_missing_or_untrusted_values() -> None:
    """坏形状/缺字段/非字典的 metadata 一律不写：客户端传什么都不算。"""
    session = _Session()
    for metadata in ({}, {"team_binding": "content-team"}, None):
        assert persist_message_binding(session, _Message(metadata)) is False
    # 形状不合法（少 teamRevision）的绑定同样不落盘。
    assert persist_message_binding(
        session, _Message({"team_binding": {"teamId": "content-team", "teamName": "内容团队"}})
    ) is False
    assert session.metadata == {}


def test_fork_inherits_the_team_but_not_any_run(tmp_path: Any) -> None:
    """fork 继承团队修订与对话历史，不继承运行中任务（契约 §4）。

    runId 从来不在 session.metadata 里（它在团队自己的 SQLite），所以这条主要钉住
    「会话复制是深拷贝元数据」这一前提：将来若有人给 fork 加白名单，绑定不能被漏掉。
    """
    from nanobot.session.manager import SessionManager
    from nanobot.webui.session_identity import webui_session_key

    manager = SessionManager(tmp_path)
    source_key = webui_session_key("chat-source")
    session = manager.get_or_create(source_key)
    session.metadata[SESSION_TEAM_BINDING_KEY] = _BINDING.to_dict()
    session.add_message("user", "research this")
    manager.save(session)

    forked = manager.fork_session_before_user_index(
        source_key,
        webui_session_key("chat-fork"),
        before_user_index=1,
    )

    assert forked is not None
    assert forked.metadata[SESSION_TEAM_BINDING_KEY] == _BINDING.to_dict()
    assert "runId" not in forked.metadata


