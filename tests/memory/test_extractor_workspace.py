"""提取写入、去重和画像合并必须按会话工作区隔离。"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nanobot import workspace_identity
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import (
    FilteredExtractionResult,
    LLMEpisodeItem,
    LLMExtractionResult,
    LLMMemoryItem,
    MemoryExtractor,
)
from nanobot.memory.provenance import (
    extraction_provenance,
    session_memory_workspace_id,
    workspace_from_path,
)
from nanobot.memory.repository import get_episode, get_memory, list_memories
from nanobot.session.manager import Session
from tests.memory.test_extractor import _FakeProvider, _make_extractor


@pytest.fixture(autouse=True)
def isolated_identity_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """身份 marker 和注册表只写入测试临时目录。"""
    def runtime_subdir(name: str) -> Path:
        path = tmp_path / "runtime" / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(workspace_identity, "get_runtime_subdir", runtime_subdir)
    workspace_identity.reset_workspace_identity_cache()
    yield
    workspace_identity.reset_workspace_identity_cache()


@pytest.fixture
def extractor(tmp_path: Path) -> MemoryExtractor:
    database = MemoryDatabase(tmp_path / "store")
    database.ensure_schema()
    return _make_extractor(database, _FakeProvider())


def _session(tmp_path: Path, project_name: str | None) -> Session:
    session = Session(key=f"websocket:{project_name or 'global'}")
    if project_name is not None:
        project = tmp_path / project_name
        project.mkdir(exist_ok=True)
        session.metadata["workspace_scope"] = {"project_path": str(project)}
    return session


def _item(content: str = "用户偏好中文回复") -> LLMMemoryItem:
    return LLMMemoryItem(
        content=content, type="preference", subject="用户", predicate="回复语言"
    )


@pytest.mark.parametrize("source", ["session_end", "context_compress", "idle"])
@pytest.mark.parametrize("project_name", ["project", None])
def test_persist_tags_memory_and_episode_with_session_workspace(
    extractor: MemoryExtractor, tmp_path: Path, source: str, project_name: str | None
) -> None:
    session = _session(tmp_path, project_name)
    expected_id = session_memory_workspace_id(session)

    result = extractor._persist(
        FilteredExtractionResult(
            memories=[_item()], episode=LLMEpisodeItem(summary="记住回复语言")
        ),
        session,
        source,
    )

    assert len(result.memory_ids) == len(result.episode_ids) == 1
    with extractor.database.connect() as conn:
        memory = get_memory(conn, result.memory_ids[0])
        episode = get_episode(conn, result.episode_ids[0])
    assert memory is not None and episode is not None
    assert memory.workspace_id == episode.workspace_id == expected_id
    for record in (memory, episode):
        provenance = record.metadata["provenance"]
        assert provenance["store_workspace_id"] == "default"
        if project_name is None:
            assert expected_id == "default"
            assert provenance["workspaces"] == []
        else:
            snapshot = workspace_from_path(str(tmp_path / project_name))
            assert provenance["workspaces"] == [{**snapshot, "id": expected_id}]
            marker = tmp_path / project_name / ".nanobot" / "workspace-id"
            assert marker.read_text(encoding="utf-8").strip() == expected_id


def test_global_provenance_omits_workspace_snapshot(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path, "project")

    provenance = extraction_provenance(session, "idle", workspace_id="default")

    assert provenance["workspaces"] == []


@pytest.mark.parametrize("other_project", ["other", None])
def test_profile_merge_stays_within_session_workspace(
    extractor: MemoryExtractor, tmp_path: Path, other_project: str | None
) -> None:
    first_session = _session(tmp_path, "project")
    other_session = _session(tmp_path, other_project)
    first = extractor._persist(
        FilteredExtractionResult(memories=[_item()]), first_session, "session_end"
    )
    other = extractor._persist(
        FilteredExtractionResult(memories=[_item("用户偏好英文回复")]), other_session, "idle"
    )
    updated = extractor._persist(
        FilteredExtractionResult(memories=[_item("用户偏好详细的中文说明")]),
        first_session,
        "idle",
    )

    assert first.memory_ids != other.memory_ids
    assert updated.memory_ids == first.memory_ids
    with extractor.database.connect() as conn:
        rows = list_memories(conn)
    assert len(rows) == 2
    by_workspace = {row.workspace_id: row for row in rows}
    first_row = by_workspace[session_memory_workspace_id(first_session)]
    other_row = by_workspace[session_memory_workspace_id(other_session)]
    assert first_row.content == "用户偏好详细的中文说明"
    assert other_row.content == "用户偏好英文回复"


def test_load_existing_memories_only_reads_requested_workspace(
    extractor: MemoryExtractor, tmp_path: Path
) -> None:
    session = _session(tmp_path, "project")
    other_session = _session(tmp_path, "other")
    for target in (session, other_session, _session(tmp_path, None)):
        extractor._persist(FilteredExtractionResult(memories=[_item()]), target, "idle")
    workspace_id = session_memory_workspace_id(session)

    existing = extractor._load_existing_memories([_item()], workspace_id=workspace_id)

    assert len(existing) == 1
    assert existing[0].workspace_id == workspace_id


@pytest.mark.parametrize("entrypoint", ["session", "incremental", "quick_facts"])
async def test_extraction_entrypoints_deduplicate_only_in_session_workspace(
    extractor: MemoryExtractor,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    candidate = LLMMemoryItem(content="以后都用中文回复", type="rule")
    extractor._persist(
        FilteredExtractionResult(memories=[candidate]), _session(tmp_path, None), "idle"
    )
    monkeypatch.setattr(
        extractor, "_llm_extract", AsyncMock(return_value=LLMExtractionResult(memories=[candidate]))
    )
    for name in ("project", "other", "project"):
        session = _session(tmp_path, name)
        session.messages = [{"role": "user", "content": candidate.content}]
        if entrypoint == "session":
            await extractor.extract_session(session)
        elif entrypoint == "incremental":
            await extractor.extract_incremental(session, 0, source="idle")
        else:
            extractor.extract_quick_facts(session)

    with extractor.database.connect() as conn:
        rows = list_memories(conn)
    assert len(rows) == 3
    assert {row.workspace_id for row in rows} == {
        "default",
        session_memory_workspace_id(_session(tmp_path, "project")),
        session_memory_workspace_id(_session(tmp_path, "other")),
    }
