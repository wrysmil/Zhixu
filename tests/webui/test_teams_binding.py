"""TeamBindingStore：会话绑定与「首条消息」的竞态（plan Task 5）。

契约 §2 的懒创建口径：新建聊天时只在服务端**暂存**绑定，第一条消息把它持久化进
session.metadata 之后才允许启动 Leader。所以这里必须保证：

1. 取走是一次性的——两个并发首消息只有一个能拿到，另一个必须走「已绑定/无绑定」分支，
   否则同一轮会起两个 Leader。
2. 暂存不是持久化：``stage`` 不碰 session 文件。
3. 暂存过期要说清「要重选」，不能静默退回普通聊天。
4. 过期时间用注入的时钟，测试不靠 sleep。
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from nanobot.teams.binding import TeamBindingExpired, TeamBindingStore
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


def test_claim_of_unknown_chat_is_expired_not_plain_chat(service: _Service) -> None:
    """空聊天重连时暂存已失效要明确报错（契约 §2），不能静默按普通聊天回答。"""
    store = _store(service, _Clock())
    with pytest.raises(TeamBindingExpired):
        store.claim("never-bound", require=True)


def test_staged_binding_expires_without_touching_the_wall_clock(service: _Service) -> None:
    clock = _Clock()
    store = _store(service, clock, ttl=30.0)
    store.stage(chat_id="chat-1", team_id="content-team")

    clock.now += 29.0
    assert store.claim("chat-1") is not None

    store.stage(chat_id="chat-2", team_id="content-team")
    clock.now += 60.0
    assert store.claim("chat-2") is None
    with pytest.raises(TeamBindingExpired):
        store.claim("chat-2", require=True)


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
