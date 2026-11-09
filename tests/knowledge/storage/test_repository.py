import sqlite3

import pytest

import nanobot.knowledge.repository as repository_module
from nanobot.knowledge.repository import DuplicateKnowledgeBaseError


def test_create_and_get_kb(repo):
    kb = repo.create_kb(name="产品手册", workspace_id="ws1")
    assert kb.id.startswith("kb_")
    assert repo.get_kb(kb.id).name == "产品手册"


def test_kb_name_unique_per_workspace(repo):
    repo.create_kb(name="手册", workspace_id="ws1")
    with pytest.raises(DuplicateKnowledgeBaseError):
        repo.create_kb(name="手册", workspace_id="ws1")
    assert repo.create_kb(name="手册", workspace_id="ws2").workspace_id == "ws2"


def test_list_kbs_respects_scope_and_global(repo):
    repo.create_kb(name="a", workspace_id="ws1")
    repo.create_kb(name="b", workspace_id="ws2")
    repo.create_kb(name="g", workspace_id="ws9", visibility="global")
    names = {kb.name for kb in repo.list_kbs(workspace_ids=["ws1"])}
    assert names == {"a", "g"}


def test_list_kbs_without_global_scope(repo):
    repo.create_kb(name="a", workspace_id="ws1")
    repo.create_kb(name="g", workspace_id="ws9", visibility="global")
    names = {kb.name for kb in repo.list_kbs(workspace_ids=["ws1"], include_global=False)}
    assert names == {"a"}


def test_list_kbs_empty_scope_is_safe(repo):
    repo.create_kb(name="a", workspace_id="ws1")
    repo.create_kb(name="g", workspace_id="ws9", visibility="global")
    assert {kb.name for kb in repo.list_kbs(workspace_ids=[])} == {"g"}
    assert repo.list_kbs(workspace_ids=[], include_global=False) == []


def test_update_kb_and_immutable_workspace(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    updated = repo.update_kb(kb.id, description="新描述", is_enabled=False)
    assert updated.description == "新描述"
    assert updated.is_enabled is False
    with pytest.raises(ValueError):
        repo.update_kb(kb.id, workspace_id="ws2")


def test_delete_kb_cascades(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    repo.delete_kb(kb.id)
    assert repo.get_kb(kb.id) is None


def test_create_kb_validation_error_is_not_duplicate(repo):
    with pytest.raises(sqlite3.IntegrityError) as bad_size:
        repo.create_kb(name="bad-size", chunk_size=100)
    assert not isinstance(bad_size.value, DuplicateKnowledgeBaseError)

    with pytest.raises(sqlite3.IntegrityError) as bad_visibility:
        repo.create_kb(name="bad-visibility", visibility="bogus")
    assert not isinstance(bad_visibility.value, DuplicateKnowledgeBaseError)


def test_update_kb_duplicate_name_maps_to_duplicate_error(repo):
    repo.create_kb(name="a", workspace_id="ws1")
    other = repo.create_kb(name="b", workspace_id="ws1")
    with pytest.raises(DuplicateKnowledgeBaseError) as excinfo:
        repo.update_kb(other.id, name="a")
    assert "a" in str(excinfo.value)


def test_update_kb_reads_in_same_transaction(repo, monkeypatch):
    kb = repo.create_kb(name="a", workspace_id="ws1")

    def _boom(_kb_id: str):
        raise AssertionError("update_kb must not re-read via get_kb")

    monkeypatch.setattr(repo, "get_kb", _boom)
    updated = repo.update_kb(kb.id, description="同事务")
    assert updated.id == kb.id
    assert updated.name == "a"
    assert updated.description == "同事务"


def test_list_kbs_orders_same_second_by_insertion(repo, monkeypatch):
    monkeypatch.setattr(repository_module, "utc_now", lambda: "2026-01-01T00:00:00+00:00")
    ids = iter(["kb_zz", "kb_mm", "kb_aa"])
    monkeypatch.setattr(repository_module, "new_id", lambda prefix: next(ids))

    created = [repo.create_kb(name=f"kb-{i}", workspace_id="ws1") for i in range(3)]
    listed = repo.list_kbs(workspace_ids=["ws1"])
    assert [kb.id for kb in listed] == [kb.id for kb in created]
