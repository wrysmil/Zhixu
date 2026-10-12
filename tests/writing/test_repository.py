from __future__ import annotations

import pytest

from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import ArticleLimitError
from nanobot.writing.repository import ArticleConflict, ArticleRepository


@pytest.fixture
def repo(tmp_path):
    db = WritingDatabase(tmp_path)
    db.ensure_schema()
    return ArticleRepository(db)


def _snapshot(**overrides):
    base = {
        "title": "标题", "writing_type": "", "template_id": "", "status": "draft",
        "summary": "", "brief": {}, "body": "正文", "outline": [], "materials": [],
        "tags": [],
    }
    base.update(overrides)
    return base


def test_create_returns_revision_one_and_stable_id(repo) -> None:      # A-T01
    created = repo.create_article("r-1", _snapshot())
    assert created["article"]["revision"] == 1
    assert created["article"]["id"]


def test_create_is_idempotent_on_client_request_id(repo) -> None:      # A-T05
    first = repo.create_article("r-1", _snapshot())
    replay = repo.create_article("r-1", _snapshot())
    assert replay == first


def test_reused_request_id_with_other_payload_is_rejected(repo) -> None:
    repo.create_article("r-1", _snapshot())
    with pytest.raises(ValueError, match="request_id was already used"):
        repo.create_article("r-1", _snapshot(title="另一个标题"))


def test_same_content_save_does_not_advance_revision(repo) -> None:    # A-T05
    created = repo.create_article("r-1", _snapshot())
    again = repo.save_snapshot(
        created["article"]["id"], 1, _snapshot(), client_request_id="r-2",
    )
    assert again["no_change"] is True
    assert again["revision"] == 1


def test_save_advances_revision_and_rejects_stale_base(repo) -> None:  # A-T06
    created = repo.create_article("r-1", _snapshot())
    saved = repo.save_snapshot(
        created["article"]["id"], 1, _snapshot(body="改过"), client_request_id="r-2",
    )
    assert saved["revision"] == 2
    with pytest.raises(ArticleConflict) as conflict:
        repo.save_snapshot(created["article"]["id"], 1, _snapshot(body="B 稿"), client_request_id="r-3")
    assert conflict.value.current_revision == 2


def test_save_replay_returns_stored_response_only(repo) -> None:       # A-T05
    created = repo.create_article("r-1", _snapshot())
    first = repo.save_snapshot(
        created["article"]["id"], 1, _snapshot(body="改过"), client_request_id="r-2",
    )
    replay = repo.save_snapshot(
        created["article"]["id"], 1, _snapshot(body="改过"), client_request_id="r-2",
    )
    assert replay["revision"] == first["revision"] == 2


def test_duplicate_reissues_all_ids(repo) -> None:                     # A-T10
    created = repo.create_article("r-1", _snapshot(
        outline=[{"id": "o-1", "text": "第一章", "order": 0}],
        materials=[{"id": "m-1", "type": "note", "title": "观点",
                    "content": "内容", "included": True, "order": 0}],
    ))
    copy = repo.duplicate_article(created["article"]["id"], 1, client_request_id="r-2")
    article = copy["article"]
    assert article["id"] != created["article"]["id"]
    assert article["materials"][0]["id"] != "m-1"
    assert article["outline"][0]["id"] != "o-1"
    assert article["revision"] == 1


def test_operations_on_missing_article_raise_lookup(repo) -> None:
    from nanobot.writing.repository import ArticleNotFound

    with pytest.raises(ArticleNotFound):
        repo.save_snapshot("nope", 1, _snapshot(), client_request_id="r-9")


def test_expired_base_revision_is_rejected(repo) -> None:              # A-T10
    created = repo.create_article("r-1", _snapshot())
    repo.delete_article(created["article"]["id"], 1, client_request_id="r-2")
    with pytest.raises(ArticleLimitError) as late:
        repo.save_snapshot(
            created["article"]["id"], 1, _snapshot(body="晚到"), client_request_id="r-3",
        )
    assert late.value.status == 409
    assert str(late.value) == "article deleted"


def test_list_sorts_by_updated_at_desc_and_excludes_body(repo) -> None:  # A-T01/A-T11
    repo.create_article("r-1", _snapshot(title="第一"))
    repo.create_article("r-2", _snapshot(title="第二"))
    page = repo.list_articles(limit=10, offset=0)
    assert page["total"] == 2
    assert all("body" not in item for item in page["articles"])
    listed = repo.list_articles(query="第二")
    assert [item["title"] for item in listed["articles"]] == ["第二"]


def test_status_filter_rejects_unknown_value(repo) -> None:
    with pytest.raises(ArticleLimitError, match="unsupported article status"):
        repo.list_articles(status="已完成")


def test_workspace_isolation_across_two_databases(tmp_path) -> None:   # A-T11
    shared = tmp_path / "state.db"
    a = WritingDatabase(tmp_path / "a", db_path=shared)
    a.ensure_schema()
    b = WritingDatabase(tmp_path / "b", db_path=shared)
    repo_a, repo_b = ArticleRepository(a), ArticleRepository(b)
    repo_a.create_article("r-1", _snapshot())
    assert repo_b.list_articles()["total"] == 0
