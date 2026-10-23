"""文章会话绑定与写作运行扩展：一篇一聊、团队修订刷新、克隆团队拿不到工具。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.writing.database import WritingDatabase
from nanobot.writing.repository import ArticleNotFound, ArticleRepository
from nanobot.writing.run_extension import WRITING_CONTEXT_KEY, WritingRunExtension
from nanobot.writing.team_seed import EDITOR_AGENT_ID, MEMBER_AGENT_IDS


class _Sessions:
    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}

    def get_or_create(self, key: str) -> Any:
        return self.rows.setdefault(key, SimpleNamespace(key=key, metadata={}))

    def save(self, session: Any, *, fsync: bool = False) -> None:
        self.rows[session.key] = session


@pytest.fixture
def db(tmp_path) -> WritingDatabase:
    database = WritingDatabase(tmp_path)
    database.ensure_schema()
    return database


@pytest.fixture
def article(db: WritingDatabase) -> dict[str, Any]:
    return ArticleRepository(db).create_article(
        "create-1", {"title": "标题", "body": "正文", "status": "draft"},
    )["article"]


def _binder(db: WritingDatabase, sessions: _Sessions, team_revision: int = 1):
    from nanobot.writing.session_binding import WritingSessionBinder

    return WritingSessionBinder(
        db,
        sessions,
        team_resolver=lambda: {
            "team_id": "team-1",
            "team_revision": team_revision,
            "team_name": "创作团队",
        },
        session_key_for=lambda chat_id: f"websocket:{chat_id}",
    )


def test_bind_is_idempotent_and_writes_session_metadata(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    sessions = _Sessions()
    binder = _binder(db, sessions)
    first = binder.bind(article["id"])
    again = binder.bind(article["id"])
    assert first.chat_id == again.chat_id
    assert first.session_key == f"websocket:{first.chat_id}"
    session = sessions.rows[first.session_key]
    assert session.metadata["team_binding"]["teamId"] == "team-1"
    assert session.metadata["writing_article"]["articleId"] == article["id"]
    assert again.to_dict()["revision"] == 1
    assert again.article_digest == article["digest"]


def test_rebind_refreshes_team_revision(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    sessions = _Sessions()
    binder = _binder(db, sessions, team_revision=1)
    bound = binder.bind(article["id"])
    upgraded = _binder(db, sessions, team_revision=2).bind(article["id"])
    assert upgraded.chat_id == bound.chat_id
    assert upgraded.team_revision == 2
    assert binder.resolve(article["id"]).team_revision == 2
    assert sessions.rows[bound.session_key].metadata["team_binding"]["teamRevision"] == 2


def test_resolve_reads_latest_article_version(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    binder = _binder(db, _Sessions())
    binder.bind(article["id"])
    ArticleRepository(db).save_snapshot(
        article["id"], 1, {"title": "标题", "body": "新正文"}, client_request_id="save-1",
    )
    resolved = binder.resolve(article["id"])
    assert resolved is not None
    assert resolved.article_revision == 2
    assert resolved.article_digest != article["digest"]


def test_deleted_article_cannot_be_bound(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    binder = _binder(db, _Sessions())
    ArticleRepository(db).delete_article(article["id"], 1, client_request_id="delete-1")
    with pytest.raises(ArticleNotFound):
        binder.bind(article["id"])
    with pytest.raises(ArticleNotFound):
        binder.resolve(article["id"])


# ---- 运行扩展 ---------------------------------------------------------------


def _handle(team_id: str = "team-1") -> Any:
    members = [
        SimpleNamespace(
            member=SimpleNamespace(member_id=member_id),
            profile=SimpleNamespace(id=member_id, name=f"{member_id}-显示名"),
        )
        for member_id in MEMBER_AGENT_IDS
    ]
    return SimpleNamespace(
        run_id="run-1",
        turn_id="turn-1",
        session_key="",
        revision=SimpleNamespace(
            definition=SimpleNamespace(id=team_id, leader_agent_id=EDITOR_AGENT_ID),
            leader=SimpleNamespace(id=EDITOR_AGENT_ID, name="创作团队主编"),
            members=members,
        ),
        accepts_writes=True,
    )


def _context(article: dict[str, Any], *, selection: dict[str, Any] | None = None) -> dict[str, Any]:
    context: dict[str, Any] = {
        "article_id": article["id"],
        "revision": article["revision"],
        "digest": article["digest"],
        "task": "rewrite",
    }
    if selection is not None:
        context["selection"] = selection
    return context


def test_leader_context_grants_tools_only_for_bound_builtin_team(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    sessions = _Sessions()
    binder = _binder(db, sessions)
    bound = binder.bind(article["id"])
    extension = WritingRunExtension(
        db=db, binder=binder, team_ids=lambda: {"team-1"},
    )
    handle = _handle()
    handle.session_key = bound.session_key
    session = sessions.rows[bound.session_key]
    msg = SimpleNamespace(
        channel="websocket",
        metadata={WRITING_CONTEXT_KEY: _context(article)},
    )

    tools, prompt = extension.leader_context(msg, session, handle)
    assert {tool.name for tool in tools} == {
        "writing_read_article", "writing_create_material",
        "writing_create_outline", "writing_update_article",
    }
    assert article["id"] in prompt
    assert "版本 1" in prompt
    assert "本次任务类型：rewrite" in prompt

    cloned = _handle(team_id="cloned-team")
    cloned.session_key = bound.session_key
    assert extension.leader_context(msg, session, cloned) == ((), "")

    plain = SimpleNamespace(
        key="websocket:plain", metadata=dict(session.metadata),
    )
    assert extension.leader_context(msg, plain, handle) == ((), "")


def test_member_tools_require_leader_seed_and_role(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    sessions = _Sessions()
    binder = _binder(db, sessions)
    bound = binder.bind(article["id"])
    extension = WritingRunExtension(
        db=db, binder=binder, team_ids=lambda: {"team-1"},
    )
    handle = _handle()
    handle.session_key = bound.session_key
    session = sessions.rows[bound.session_key]
    msg = SimpleNamespace(
        channel="websocket", metadata={WRITING_CONTEXT_KEY: _context(article)},
    )
    assert extension.member_tools(handle, "writing-author") == ()

    extension.leader_context(msg, session, handle)
    author_tools = extension.member_tools(handle, "writing-author")
    assert {tool.name for tool in author_tools} == {
        "writing_read_article", "writing_update_article",
    }
    reviewer_tools = extension.member_tools(handle, "writing-reviewer")
    assert {tool.name for tool in reviewer_tools} == {"writing_read_article"}

    extension.forget(handle.run_id)
    assert extension.member_tools(handle, "writing-author") == ()


def test_selection_is_forwarded_in_prompt(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    sessions = _Sessions()
    binder = _binder(db, sessions)
    bound = binder.bind(article["id"])
    extension = WritingRunExtension(
        db=db, binder=binder, team_ids=lambda: {"team-1"},
    )
    handle = _handle()
    handle.session_key = bound.session_key
    session = sessions.rows[bound.session_key]
    msg = SimpleNamespace(
        channel="websocket",
        metadata={WRITING_CONTEXT_KEY: _context(
            article, selection={"text": "正文", "start": 0, "end": 2, "prefix": "",
                               "suffix": ""},
        )},
    )
    _tools, prompt = extension.leader_context(msg, session, handle)
    assert "选区（Python 码点 [0, 2)）" in prompt
    assert "正文" in prompt


def test_change_event_carries_actor_display_name(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    sessions = _Sessions()
    binder = _binder(db, sessions)
    bound = binder.bind(article["id"])
    events: list[tuple[str, dict[str, Any]]] = []
    extension = WritingRunExtension(
        db=db,
        binder=binder,
        team_ids=lambda: {"team-1"},
        publish_change=lambda chat_id, body: events.append((chat_id, dict(body))),
    )
    handle = _handle()
    handle.session_key = bound.session_key
    session = sessions.rows[bound.session_key]
    msg = SimpleNamespace(
        channel="websocket", metadata={WRITING_CONTEXT_KEY: _context(article)},
    )
    extension.leader_context(msg, session, handle)

    tools = extension.member_tools(handle, "writing-architect")
    outline = next(tool for tool in tools if tool.name == "writing_create_outline")
    result = asyncio.run(outline.execute(items=[{"text": "第一节"}], mode="replace"))

    assert not result.is_error
    assert events and events[0][0] == bound.chat_id
    assert events[0][1]["actor"] == "writing-architect"
    assert events[0][1]["actor_name"] == "writing-architect-显示名"
