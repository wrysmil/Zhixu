"""`nanobot knowledge` 子命令测试（Task 18）。

服务通过 `_service_factory` 注入假实现：不接触真实 `~/.nanobot`、不建库、不起 worker。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from nanobot.cli import knowledge as cli
from nanobot.knowledge.extractor import KnowledgeExtractionError
from nanobot.knowledge.fusion import SearchHit, SearchResult
from nanobot.knowledge.retriever import KnowledgeSearchError

runner = CliRunner()


class FakeKb:
    def __init__(self, kb_id: str, name: str, visibility: str = "workspace") -> None:
        self.id = kb_id
        self.name = name
        self.visibility = visibility
        self.embedding_backend = "none"


class FakeDocument:
    def __init__(
        self,
        doc_id: str = "doc_1",
        *,
        kb_id: str = "kb_1",
        title: str = "手册",
        parse_status: str = "pending",
        chunk_count: int = 0,
        error_message: str = "",
    ) -> None:
        self.id = doc_id
        self.kb_id = kb_id
        self.title = title
        self.parse_status = parse_status
        self.chunk_count = chunk_count
        self.error_message = error_message
        self.source_kind = "external"
        self.source_path = "/tmp/source.md"


class FakeService:
    def __init__(self) -> None:
        self.kbs: list[FakeKb] = [FakeKb("kb_1", "手册")]
        self.documents: dict[str, FakeDocument] = {}
        self.created: list[dict[str, object]] = []
        self.deleted: list[str] = []
        self.enqueued: list[str] = []
        self.ingested: list[str] = []
        self.reindexed_docs: list[str] = []
        self.reindexed_kbs: list[str] = []
        self.repaired: list[dict[str, bool]] = []
        self.add_calls: list[tuple[Path, dict[str, object]]] = []
        self.add_error: Exception | None = None
        self.duplicate = False
        self.ingest_status = "ready"
        self.ingest_error = ""
        self.search_error: Exception | None = None
        self.search_result = SearchResult(
            hits=(
                SearchHit(
                    chunk_id="chk_1",
                    doc_id="doc_1",
                    kb_id="kb_1",
                    chunk_index=0,
                    heading_path="",
                    page=None,
                    locator="",
                    content="回滚步骤",
                    char_count=4,
                    score=0.9,
                ),
            ),
            mode_used="keyword",
            degraded=("mode_downgraded",),
            routes=("keyword",),
        )

    # KB
    def list_kbs(self, *, workspace_ids: list[str], include_global: bool = True):
        return list(self.kbs)

    def get_kb(self, kb_id: str):
        return next((kb for kb in self.kbs if kb.id == kb_id), None)

    def create_kb(self, **kwargs: object):
        self.created.append(kwargs)
        kb = FakeKb("kb_new", str(kwargs["name"]), str(kwargs["visibility"]))
        self.kbs.append(kb)
        return kb

    def delete_kb(self, kb_id: str) -> None:
        self.deleted.append(kb_id)

    # 文档
    def add_file(self, path: Path, **kwargs: object):
        self.add_calls.append((path, kwargs))
        if self.add_error is not None:
            raise self.add_error
        document = FakeDocument("doc_1", kb_id=str(kwargs["kb_id"]))
        self.documents[document.id] = document
        return SimpleNamespace(id=document.id, document=document, duplicate=self.duplicate)

    def ingest_now(self, doc_id: str) -> bool:
        self.ingested.append(doc_id)
        document = self.documents.setdefault(doc_id, FakeDocument(doc_id))
        document.parse_status = self.ingest_status
        document.chunk_count = 2 if self.ingest_status == "ready" else 0
        document.error_message = self.ingest_error
        return True

    def enqueue_document(self, doc_id: str) -> None:
        self.enqueued.append(doc_id)

    def get_document(self, doc_id: str):
        return self.documents.get(doc_id)

    def delete_document(self, doc_id: str) -> None:
        self.deleted.append(doc_id)

    def list_documents(self, **kwargs: object):
        documents = list(self.documents.values())
        kb_id = kwargs.get("kb_id")
        if kb_id is not None:
            documents = [doc for doc in documents if doc.kb_id == kb_id]
        return documents

    def list_chunks(self, doc_id: str, *, offset: int = 0, limit: int = 20):
        return [
            SimpleNamespace(chunk_index=index, content=f"片段{index}", page=None)
            for index in range(offset, offset + limit)
        ]

    def reindex_document(self, doc_id: str) -> bool:
        self.reindexed_docs.append(doc_id)
        document = self.documents.get(doc_id)
        return document is not None

    def reindex_kb(self, kb_id: str) -> list[str]:
        self.reindexed_kbs.append(kb_id)
        return ["doc_1", "doc_2"]

    # 检索 / 修复 / 统计
    def search(self, query: str, **kwargs: object):
        if self.search_error is not None:
            raise self.search_error
        return self.search_result

    def repair(self, **kwargs: bool):
        self.repaired.append(kwargs)
        return {
            "fts_rebuilt": 1,
            "vectors_rewrite": 0,
            "vectors_purge": 0,
            "files_purged": 0,
            "rewritten_docs": [],
        }

    def stats(self, *, workspace_ids: list[str]):
        return {"kbs": 1, "documents": 0, "chunks": 0, "pending": 0}


def _run(monkeypatch, args, service=None):
    service = service or FakeService()
    monkeypatch.setattr(cli, "_load_config", lambda config=None: object())
    monkeypatch.setattr(cli, "_service_factory", lambda config: service)
    return CliRunner().invoke(cli.knowledge_app, args), service


def test_knowledge_subapp_registered():
    from nanobot.cli.commands import app

    result = CliRunner().invoke(app, ["knowledge", "--help"])
    assert result.exit_code == 0, result.output
    assert "create" in result.output
    assert "rm-doc" in result.output


def test_create_global_flag(monkeypatch):
    result, service = _run(monkeypatch, ["create", "手册", "--global"])
    assert result.exit_code == 0, result.output
    assert service.created[0]["visibility"] == "global"
    assert "kb_new" in result.output


def test_create_workspace_default(monkeypatch):
    result, service = _run(monkeypatch, ["create", "手册", "--desc", "说明"])
    assert result.exit_code == 0, result.output
    assert service.created[0]["visibility"] == "workspace"
    assert service.created[0]["workspace_id"] == "default"
    assert service.created[0]["description"] == "说明"


def test_list_kbs(monkeypatch):
    result, _ = _run(monkeypatch, ["list"])
    assert result.exit_code == 0, result.output
    assert "kb_1" in result.output
    assert "手册" in result.output


def test_rm_requires_yes(monkeypatch):
    result, service = _run(monkeypatch, ["rm", "kb_1"])
    assert result.exit_code == 2
    assert service.deleted == []
    assert "--yes" in result.output


def test_rm_deletes_with_yes(monkeypatch):
    result, service = _run(monkeypatch, ["rm", "kb_1", "--yes"])
    assert result.exit_code == 0, result.output
    assert service.deleted == ["kb_1"]
    assert "deleted kb_1" in result.output


def test_rm_resolves_kb_name(monkeypatch):
    result, service = _run(monkeypatch, ["rm", "手册", "--yes"])
    assert result.exit_code == 0, result.output
    assert service.deleted == ["kb_1"]


def test_rm_unknown_kb_exit_2(monkeypatch):
    result, service = _run(monkeypatch, ["rm", "kb_missing", "--yes"])
    assert result.exit_code == 2
    assert service.deleted == []


def test_rm_doc_requires_yes(monkeypatch):
    result, service = _run(monkeypatch, ["rm-doc", "doc_1"])
    assert result.exit_code == 2
    assert service.deleted == []


def test_rm_doc_unknown_exit_2(monkeypatch):
    result, service = _run(monkeypatch, ["rm-doc", "doc_missing", "--yes"])
    assert result.exit_code == 2
    assert service.deleted == []


def test_add_rejects_directory(monkeypatch, tmp_path):
    result, service = _run(monkeypatch, ["add", str(tmp_path), "--kb", "kb_1"])
    assert result.exit_code == 2
    assert service.add_calls == []


def test_add_rejects_missing_file(monkeypatch, tmp_path):
    result, service = _run(monkeypatch, ["add", str(tmp_path / "nope.md"), "--kb", "kb_1"])
    assert result.exit_code == 2
    assert service.add_calls == []


def test_add_unknown_kb_exit_2(monkeypatch, tmp_path):
    source = tmp_path / "a.md"
    source.write_text("内容", encoding="utf-8")
    result, service = _run(monkeypatch, ["add", str(source), "--kb", "kb_missing"])
    assert result.exit_code == 2
    assert service.add_calls == []


def test_add_no_wait_enqueues(monkeypatch, tmp_path):
    source = tmp_path / "a.md"
    source.write_text("内容", encoding="utf-8")
    result, service = _run(monkeypatch, ["add", str(source), "--kb", "kb_1", "--no-wait"])
    assert result.exit_code == 0, result.output
    assert service.enqueued == ["doc_1"]
    assert service.ingested == []
    assert "queued doc_1" in result.output


def test_add_waits_and_prints_status(monkeypatch, tmp_path):
    source = tmp_path / "a.md"
    source.write_text("内容", encoding="utf-8")
    result, service = _run(monkeypatch, ["add", str(source), "--kb", "手册"])
    assert result.exit_code == 0, result.output
    assert service.ingested == ["doc_1"]
    assert "ready doc_1 chunks=2" in result.output


def test_add_duplicate_does_not_ingest(monkeypatch, tmp_path):
    source = tmp_path / "a.md"
    source.write_text("内容", encoding="utf-8")
    service = FakeService()
    service.duplicate = True
    result, service = _run(monkeypatch, ["add", str(source), "--kb", "kb_1"], service)
    assert result.exit_code == 0, result.output
    assert service.ingested == []
    assert "duplicate doc_1" in result.output


def test_add_failed_ingest_exit_1(monkeypatch, tmp_path):
    source = tmp_path / "a.md"
    source.write_text("内容", encoding="utf-8")
    service = FakeService()
    service.ingest_status = "failed"
    service.ingest_error = "无法解析"
    result, service = _run(monkeypatch, ["add", str(source), "--kb", "kb_1"], service)
    assert result.exit_code == 1
    assert "failed" in result.output
    assert "无法解析" in result.output


def test_add_extraction_error_exit_2(monkeypatch, tmp_path):
    source = tmp_path / "a.md"
    source.write_text("内容", encoding="utf-8")
    service = FakeService()
    service.add_error = KnowledgeExtractionError("不是普通文件")
    result, service = _run(monkeypatch, ["add", str(source), "--kb", "kb_1"], service)
    assert result.exit_code == 2


def test_search_prints_metadata_and_hits(monkeypatch):
    result, _ = _run(monkeypatch, ["search", "回滚"])
    assert result.exit_code == 0, result.output
    assert "mode=keyword" in result.output
    assert "routes=keyword" in result.output
    assert "degraded=mode_downgraded" in result.output
    assert "chk_1" in result.output
    assert "0.900" in result.output


def test_search_empty_prints_message(monkeypatch):
    service = FakeService()
    service.search_result = SearchResult(hits=(), mode_used="keyword", reason="no_scope")
    result, service = _run(monkeypatch, ["search", "回滚"], service)
    assert result.exit_code == 0, result.output
    assert "没有检索到" in result.output


def test_search_failure_exit_1(monkeypatch):
    service = FakeService()
    service.search_error = KnowledgeSearchError("索引不可用")
    result, _ = _run(monkeypatch, ["search", "回滚"], service)
    assert result.exit_code == 1
    assert "索引不可用" in result.output


def test_search_rejects_bad_mode(monkeypatch):
    result, service = _run(monkeypatch, ["search", "回滚", "--mode", "fuzzy"])
    assert result.exit_code == 2


def test_search_json(monkeypatch):
    result, _ = _run(monkeypatch, ["search", "回滚", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload[0]["chunk_id"] == "chk_1"
    assert payload[0]["score"] == 0.9


def test_reindex_document(monkeypatch):
    service = FakeService()
    service.documents["doc_1"] = FakeDocument("doc_1", parse_status="ready")
    result, service = _run(monkeypatch, ["reindex", "doc_1"], service)
    assert result.exit_code == 0, result.output
    assert service.reindexed_docs == ["doc_1"]
    assert "requeued doc_1" in result.output


def test_reindex_unknown_document_exit_2(monkeypatch):
    result, service = _run(monkeypatch, ["reindex", "doc_missing"])
    assert result.exit_code == 2
    assert service.reindexed_docs == []


def test_reindex_kb_wait(monkeypatch):
    result, service = _run(monkeypatch, ["reindex", "kb_1", "--wait"])
    assert result.exit_code == 0, result.output
    assert service.reindexed_kbs == ["kb_1"]
    assert service.ingested == ["doc_1", "doc_2"]
    assert "requeued 2 documents" in result.output


def test_repair_default_runs_all(monkeypatch):
    result, service = _run(monkeypatch, ["repair"])
    assert result.exit_code == 0, result.output
    assert service.repaired == [{"fts": True, "vectors": True, "purge": True}]
    assert "fts_rebuilt" in result.output


def test_repair_purge_requires_yes(monkeypatch):
    result, service = _run(monkeypatch, ["repair", "--purge"])
    assert result.exit_code == 2
    assert service.repaired == []


def test_repair_purge_with_yes(monkeypatch):
    result, service = _run(monkeypatch, ["repair", "--purge", "--yes"])
    assert result.exit_code == 0, result.output
    assert service.repaired == [{"fts": False, "vectors": False, "purge": True}]


def test_stats_json(monkeypatch):
    result, _ = _run(monkeypatch, ["stats"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["kbs"] == 1


def test_docs_lists_documents(monkeypatch):
    service = FakeService()
    service.documents["doc_1"] = FakeDocument("doc_1", parse_status="ready", chunk_count=3)
    result, _ = _run(monkeypatch, ["docs"], service)
    assert result.exit_code == 0, result.output
    assert "doc_1" in result.output
    assert "ready" in result.output


def test_doc_detail_previews_chunks(monkeypatch):
    service = FakeService()
    service.documents["doc_1"] = FakeDocument("doc_1", title="手册", parse_status="ready")
    result, _ = _run(monkeypatch, ["doc", "doc_1"], service)
    assert result.exit_code == 0, result.output
    assert "手册" in result.output
    assert "片段0" in result.output


def test_no_home_writes(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result, _ = _run(monkeypatch, ["create", "手册"])
    assert result.exit_code == 0, result.output
    assert not (home / ".nanobot").exists()
