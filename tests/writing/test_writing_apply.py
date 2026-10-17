"""字段级写入工具（契约 §3 / §5.5）：CAS、严格幂等、撤销与取消后拒绝写入。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import pytest

from nanobot.writing.apply import WritingActor, apply_change, undo_change
from nanobot.writing.database import WritingDatabase
from nanobot.writing.repository import (
    ArticleConflict,
    ArticleRepository,
    ArticleRequestReplay,
)
from nanobot.writing.tools import (
    WritingToolRuntime,
    build_writing_tools,
)

BODY = "第一段。\n第二段。"


def _snapshot(**overrides: Any) -> dict[str, Any]:
    base = {
        "title": "标题", "writing_type": "", "template_id": "", "status": "draft",
        "summary": "", "brief": {}, "body": BODY, "outline": [], "materials": [],
        "tags": [],
    }
    base.update(overrides)
    return base


@pytest.fixture
def db(tmp_path) -> WritingDatabase:
    database = WritingDatabase(tmp_path)
    database.ensure_schema()
    return database


@pytest.fixture
def article(db: WritingDatabase) -> dict[str, Any]:
    return ArticleRepository(db).create_article("create-1", _snapshot())["article"]


def _apply(
    db: WritingDatabase,
    article: dict[str, Any],
    change: dict[str, Any],
    *,
    client_request_id: str | None = None,
    actor: WritingActor | None = None,
) -> dict[str, Any]:
    if client_request_id is None:
        encoded = json.dumps([article["id"], change], sort_keys=True).encode("utf-8")
        client_request_id = f"apply-{hashlib.sha256(encoded).hexdigest()[:16]}"
    return apply_change(
        db,
        article_id=article["id"],
        base_revision=article["revision"],
        base_digest=article["digest"],
        change=change,
        actor=actor or WritingActor(member_id="writing-author"),
        request_fingerprint="fp-1",
        client_request_id=client_request_id,
    )


def test_replace_range_advances_revision_and_records_change(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    result = _apply(db, article, {
        "kind": "body", "mode": "replace_range", "start": 0, "end": 3, "content": "开头",
    })
    assert result["revision"] == 2
    assert result["change_id"]
    current = ArticleRepository(db).get_article(article["id"])["article"]
    assert current["body"] == "开头。\n第二段。"
    assert current["digest"] == result["digest"]
    assert current["digest"] != article["digest"]


def test_same_request_id_replays_and_different_payload_is_rejected(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    change = {"kind": "body", "mode": "append", "content": "补充"}
    first = _apply(db, article, change, client_request_id="shared-1")
    replay = _apply(db, article, change, client_request_id="shared-1")
    assert replay == first

    with pytest.raises(ArticleRequestReplay):
        _apply(
            db, article, {"kind": "body", "mode": "append", "content": "另一段"},
            client_request_id="shared-1",
        )


def test_stale_digest_or_revision_is_a_conflict(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    _apply(db, article, {"kind": "body", "mode": "append", "content": "补充"})
    with pytest.raises(ArticleConflict) as raised:
        _apply(db, article, {"kind": "body", "mode": "append", "content": "再补充"})
    assert raised.value.current_revision == 2


def test_identical_body_is_no_change_and_does_not_create_revision(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    result = _apply(db, article, {
        "kind": "body", "mode": "replace_range", "start": 0, "end": 3, "content": "第一段",
    })
    assert result["no_change"] is True
    assert result["change_id"] == ""
    assert result["revision"] == 1


def test_undo_restores_only_that_write(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    result = _apply(db, article, {
        "kind": "body", "mode": "replace_body", "content": "团队改写",
    })
    undone = undo_change(
        db,
        change_id=result["change_id"],
        base_revision=2,
        client_request_id="undo-1",
    )
    assert undone["revision"] == 3
    current = ArticleRepository(db).get_article(article["id"])["article"]
    assert current["body"] == BODY


def test_undo_is_blocked_after_a_newer_edit(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    result = _apply(db, article, {"kind": "body", "mode": "append", "content": "甲"})
    newer = dict(article, revision=2, digest=result["digest"])
    _apply(db, newer, {"kind": "body", "mode": "append", "content": "乙"})
    with pytest.raises(Exception, match="undo blocked by newer edits") as raised:
        undo_change(
            db,
            change_id=result["change_id"],
            base_revision=3,
            client_request_id="undo-1",
        )
    assert getattr(raised.value, "status", None) == 409


def test_undo_twice_is_rejected(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    result = _apply(db, article, {"kind": "outline", "mode": "replace", "items": ["一"]})
    undo_change(
        db, change_id=result["change_id"], base_revision=2, client_request_id="undo-1",
    )
    with pytest.raises(Exception, match="already reverted"):
        undo_change(
            db, change_id=result["change_id"], base_revision=3, client_request_id="undo-2",
        )


def test_material_and_outline_append_keep_existing_items(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    with_outline = _apply(db, article, {
        "kind": "outline", "mode": "append", "items": [{"text": "开场"}, "论证"],
    })
    refreshed = dict(article, revision=2, digest=with_outline["digest"])
    with_material = _apply(db, refreshed, {
        "kind": "material",
        "material": {"type": "note", "title": "来源", "content": "原文引用"},
    })
    current = ArticleRepository(db).get_article(article["id"])["article"]
    assert [item["text"] for item in current["outline"]] == ["开场", "论证"]
    assert current["materials"][0]["title"] == "来源"
    assert with_material["revision"] == 3


# ---- 工具层：角色、取消与错误口径 ------------------------------------------


def _runtime(
    db: WritingDatabase,
    article: dict[str, Any],
    *,
    member_id: str,
    cancelled: bool = False,
    on_change=None,
) -> WritingToolRuntime:
    return WritingToolRuntime(
        db=db,
        article_id=article["id"],
        chat_id="chat-1",
        session_key="websocket:chat-1",
        team_id="team-1",
        team_revision=1,
        run_id="run-1",
        turn_id="turn-1",
        member_id=member_id,
        base_revision=article["revision"],
        base_digest=article["digest"],
        cancelled=lambda: cancelled,
        on_change=on_change,
    )


def test_tool_set_is_scoped_by_role(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    def names(role: str) -> set[str]:
        return {tool.name for tool in build_writing_tools(_runtime(db, article, member_id=role))}

    assert names("writing-reviewer") == {"writing_read_article"}
    assert names("writing-researcher") == {
        "writing_read_article", "writing_create_material",
    }
    assert names("writing-architect") == {
        "writing_read_article", "writing_create_outline",
    }
    assert names("writing-author") == {
        "writing_read_article", "writing_update_article",
    }
    assert names("writing-editor") == {
        "writing_read_article", "writing_create_material",
        "writing_create_outline", "writing_update_article",
    }


def test_write_tool_rejects_after_cancel(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    runtime = _runtime(db, article, member_id="writing-author", cancelled=True)
    tool = next(t for t in build_writing_tools(runtime) if t.name == "writing_update_article")
    result = asyncio.run(tool.execute(mode="append", content="晚到内容"))
    assert result.is_error
    assert "writing_run_cancelled" in result
    assert ArticleRepository(db).get_article(article["id"])["article"]["revision"] == 1


def test_write_tool_reports_conflict(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    _apply(db, article, {"kind": "body", "mode": "append", "content": "作者先改了"})
    runtime = _runtime(db, article, member_id="writing-author")
    tool = next(t for t in build_writing_tools(runtime) if t.name == "writing_update_article")
    result = asyncio.run(tool.execute(mode="append", content="团队后写"))
    assert result.is_error
    assert "writing_conflict" in result
    assert "current_revision=2" in result


def test_write_tool_publishes_change_and_persists(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    changes: list[dict[str, Any]] = []
    runtime = _runtime(
        db, article, member_id="writing-author", on_change=changes.append,
    )
    tool = next(t for t in build_writing_tools(runtime) if t.name == "writing_update_article")
    result = asyncio.run(tool.execute(mode="append", content="团队新增"))
    assert not result.is_error
    payload = json.loads(str(result))
    assert payload["revision"] == 2
    assert changes and changes[0]["change_id"] == payload["change_id"]
    assert changes[0]["actor"] == "writing-author"


def test_same_run_member_can_keep_writing_after_its_own_change(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    """同一成员在同一 run 的第二次写入不该被自己的上一版判成外部并发。"""
    changes: list[dict[str, Any]] = []
    runtime = _runtime(
        db, article, member_id="writing-architect", on_change=changes.append,
    )
    outline = next(
        t for t in build_writing_tools(runtime) if t.name == "writing_create_outline"
    )
    first = asyncio.run(outline.execute(items=[{"text": "第一节"}], mode="replace"))
    second = asyncio.run(
        outline.execute(items=[{"text": "第一节"}, {"text": "第二节"}], mode="replace")
    )

    assert not first.is_error
    assert not second.is_error
    assert json.loads(str(first))["revision"] == 2
    payload = json.loads(str(second))
    assert payload["revision"] == 3
    current = ArticleRepository(db).get_article(article["id"])["article"]
    assert [item["text"] for item in current["outline"]] == ["第一节", "第二节"]
    assert len(changes) == 2


def test_other_member_in_same_run_still_conflicts(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    _apply(
        db,
        article,
        {"kind": "outline", "items": [{"text": "结构编辑的提纲"}], "mode": "replace"},
        actor=WritingActor(member_id="writing-architect", run_id="run-1"),
        client_request_id="arch-1",
    )
    runtime = _runtime(db, article, member_id="writing-author")
    tool = next(t for t in build_writing_tools(runtime) if t.name == "writing_update_article")
    result = asyncio.run(tool.execute(mode="append", content="写手后写"))
    assert result.is_error
    assert "writing_conflict" in result


def test_external_edit_between_writes_is_still_a_conflict(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    runtime = _runtime(db, article, member_id="writing-author")
    tool = next(t for t in build_writing_tools(runtime) if t.name == "writing_update_article")
    first = asyncio.run(tool.execute(mode="append", content="团队先写"))
    payload = json.loads(str(first))
    current = dict(article, revision=payload["revision"], digest=payload["digest"])
    _apply(
        db,
        current,
        {"kind": "body", "mode": "append", "content": "作者插入"},
        actor=WritingActor(member_id="author-user"),
        client_request_id="author-1",
    )

    second = asyncio.run(tool.execute(mode="append", content="团队后写"))
    assert second.is_error
    assert "writing_conflict" in second


def test_read_article_refreshes_the_write_base(
    db: WritingDatabase, article: dict[str, Any]
) -> None:
    """方案 §6.4：读到新版本后重试，写工具应以读到的 revision/digest 为基线。"""
    runtime = _runtime(db, article, member_id="writing-author")
    tools = build_writing_tools(runtime)
    writer = next(t for t in tools if t.name == "writing_update_article")
    reader = next(t for t in tools if t.name == "writing_read_article")

    external = _apply(
        db, article, {"kind": "body", "mode": "append", "content": "作者先改"},
    )
    read = asyncio.run(reader.execute())
    assert not read.is_error
    assert json.loads(str(read))["revision"] == external["revision"]

    result = asyncio.run(writer.execute(mode="append", content="团队基于最新版"))
    assert not result.is_error
    assert json.loads(str(result))["revision"] == external["revision"] + 1
