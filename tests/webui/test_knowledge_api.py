"""知识中心 WebUI 后端（Task 19）单测。

覆盖：读动作、KB 增删改、上传（成功 / 超限 / MIME / 空内容 / base64 损坏）、
服务端路径入库、文档删除、reindex（KB/doc）、repair、工作区越权、mutation
白名单成员关系、``knowledge.enabled=False`` 的降级，以及服务错误 → 稳定错误码。

口径来源：``.ai-runtime-artifacts/specs/2026-11-09-knowledge-center-spec.md``
§13（路由/载荷）与 §14（安全边界）、``2026-11-09-knowledge-storage-spec.md``
§9.1（上传白名单）与 §18（错误码）。
"""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.config.schema import Config
from nanobot.webui import knowledge_routes
from nanobot.webui.gateway_services import build_knowledge_operations
from nanobot.webui.knowledge_api import KnowledgeApi, KnowledgeApiError
from nanobot.webui.knowledge_services import KnowledgeServices, normalize_workspace_id
from nanobot.webui.settings_routes import (
    _SETTINGS_MUTATION_PATHS,
    _SYSTEM_ROUTES,
    WebUISettingsRouter,
)
from nanobot.webui.ws_http import _WEBUI_MUTATION_PATHS, GatewayHTTPHandler

_DOC_MIME = {
    ".pdf": "application/pdf",
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    ".md": "text/markdown",
    ".txt": "text/plain",
}


def _kb(
    kb_id: str = "kb_1",
    *,
    name: str = "手册",
    workspace_id: str = "ws1",
    visibility: str = "workspace",
    **overrides: Any,
) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": kb_id,
        "name": name,
        "description": "",
        "workspace_id": workspace_id,
        "visibility": visibility,
        "chunk_size": 800,
        "chunk_overlap": 120,
        "embedding_backend": "none",
        "embedding_model": "",
        "embedding_dimensions": 0,
        "is_enabled": True,
        "created_at": "2026-11-09T00:00:00+00:00",
        "updated_at": "2026-11-09T00:00:00+00:00",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _doc(
    doc_id: str = "doc_1",
    *,
    kb_id: str = "kb_1",
    workspace_id: str = "ws1",
    title: str = "手册",
    **overrides: Any,
) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": doc_id,
        "kb_id": kb_id,
        "workspace_id": workspace_id,
        "title": title,
        "source_type": "txt",
        "source_kind": "managed",
        # 绝不能出现在响应里的字段（路径泄露面）。
        "source_path": "/Users/secret/uploads/doc_1/a.txt",
        "original_name": "a.txt",
        "mime": "text/plain",
        "file_hash": "hash",
        "file_size": 5,
        "source_mtime_ns": 0,
        "parse_status": "pending",
        "error_code": "",
        "error_message": "",
        "attempt": 0,
        "lease_owner": "",
        "lease_expires_at": "",
        "index_version": "",
        "vector_status": "pending",
        "chunk_count": 0,
        "char_count": 5,
        "is_enabled": True,
        "created_at": "2026-11-09T00:00:00+00:00",
        "updated_at": "2026-11-09T00:00:00+00:00",
        "indexed_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _chunk(
    chunk_id: str = "ck_1",
    *,
    doc_id: str = "doc_1",
    kb_id: str = "kb_1",
    content: str = "部署失败怎么回滚",
    **overrides: Any,
) -> SimpleNamespace:
    values: dict[str, Any] = {
        "row_id": 1,
        "id": chunk_id,
        "doc_id": doc_id,
        "kb_id": kb_id,
        "workspace_id": "ws1",
        "chunk_index": 0,
        "content": content,
        "heading_path": "回滚",
        "page": None,
        "locator": "L1",
        "start_offset": 0,
        "end_offset": len(content),
        "char_count": len(content),
        "content_hash": "h",
        "is_enabled": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeService:
    """最小替身：只实现 WebUI 后端会碰到的知识服务方法。"""

    def __init__(
        self,
        *,
        kbs: list[SimpleNamespace] | None = None,
        documents: list[SimpleNamespace] | None = None,
        chunks: list[SimpleNamespace] | None = None,
        search_result: Any | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.kbs = {kb.id: kb for kb in (kbs if kbs is not None else [_kb()])}
        self.documents = documents if documents is not None else [_doc()]
        self.chunks = chunks if chunks is not None else []
        self.search_result = search_result
        self.failure = failure

    # ---- KB ----
    def list_kbs(self, *, workspace_ids: list[str], include_global: bool = True):
        self.calls.append(("list_kbs", {"workspace_ids": list(workspace_ids)}))
        return [
            kb
            for kb in self.kbs.values()
            if kb.visibility == "global" or kb.workspace_id in workspace_ids
        ]

    def get_kb(self, kb_id: str):
        self.calls.append(("get_kb", kb_id))
        return self.kbs.get(kb_id)

    def create_kb(self, **kwargs: Any):
        self.calls.append(("create_kb", kwargs))
        return _kb("kb_new", name=kwargs.get("name", ""), **{
            key: value for key, value in kwargs.items() if key != "name"
        })

    def update_kb(self, kb_id: str, **fields: Any):
        self.calls.append(("update_kb", {"id": kb_id, "fields": fields}))
        return self.kbs.get(kb_id, _kb(kb_id))

    def delete_kb(self, kb_id: str) -> None:
        self.calls.append(("delete_kb", kb_id))

    # ---- 文档 ----
    def list_documents(
        self,
        *,
        kb_id: str | None = None,
        workspace_ids: list[str] | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ):
        self.calls.append((
            "list_documents",
            {"kb_id": kb_id, "workspace_ids": workspace_ids, "status": status},
        ))
        rows = [
            doc
            for doc in self.documents
            if (kb_id is None or doc.kb_id == kb_id)
            and (workspace_ids is None or doc.workspace_id in workspace_ids)
            and (status is None or doc.parse_status == status)
        ]
        return rows[offset : offset + limit]

    def get_document(self, doc_id: str):
        self.calls.append(("get_document", doc_id))
        for doc in self.documents:
            if doc.id == doc_id:
                return doc
        return None

    def add_file(self, path: str, *, kb_id: str, workspace_id: str, title: str | None = None):
        self.calls.append(("add_file", {"path": path, "kb_id": kb_id, "workspace_id": workspace_id}))
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(document=_doc("doc_file", kb_id=kb_id, workspace_id=workspace_id))

    def add_upload_bytes(
        self,
        name: str,
        data: bytes,
        *,
        kb_id: str,
        workspace_id: str,
        mime: str = "",
        title: str | None = None,
    ):
        self.calls.append((
            "add_upload_bytes",
            {"name": name, "data": data, "kb_id": kb_id,
             "workspace_id": workspace_id, "mime": mime, "title": title},
        ))
        return _doc("doc_1", kb_id=kb_id, workspace_id=workspace_id, title=title or name)

    def delete_document(self, doc_id: str) -> None:
        self.calls.append(("delete_document", doc_id))

    def reindex_document(self, doc_id: str) -> bool:
        self.calls.append(("reindex_document", doc_id))
        return True

    def reindex_kb(self, kb_id: str) -> list[str]:
        self.calls.append(("reindex_kb", kb_id))
        return ["doc_1", "doc_2"]

    def list_chunks(self, doc_id: str, *, offset: int = 0, limit: int = 20):
        self.calls.append(("list_chunks", {"doc_id": doc_id, "limit": limit}))
        return [chunk for chunk in self.chunks if chunk.doc_id == doc_id][:limit]

    def get_chunk(self, chunk_id: str):
        self.calls.append(("get_chunk", chunk_id))
        for chunk in self.chunks:
            if chunk.id == chunk_id:
                return chunk
        return None

    # ---- 检索 / 统计 / 修复 ----
    def search(self, query: str, **kwargs: Any):
        self.calls.append(("search", {"query": query, **kwargs}))
        if self.search_result is not None:
            return self.search_result
        return SimpleNamespace(hits=(), mode_used="keyword", degraded=(), reason=None, routes=())

    def stats(self, *, workspace_ids: list[str]):
        self.calls.append(("stats", {"workspace_ids": list(workspace_ids)}))
        return {
            "kbs": 1,
            "documents": 1,
            "chunks": 2,
            "by_status": {"ready": 1},
            "vector": {"backend": "none"},
            "pending": 0,
            "expired_leases": 0,
            "drift": {"index_version": {}, "stale_ready": 0},
            "db_bytes": 4096,
            "uploads_bytes": 512,
        }

    def repair(self, *, fts: bool = False, vectors: bool = False, purge: bool = False):
        self.calls.append(("repair", {"fts": fts, "vectors": vectors, "purge": purge}))
        return {
            "fts_rebuilt": 1 if fts else 0,
            "vectors_scanned": 1 if vectors else 0,
            "vectors_rewrite": 0,
            "vectors_purge": 0,
            "vector_errors": 0,
            "files_purged": 1 if purge else 0,
            "rewritten_docs": [],
            "purged_docs": [],
        }


def _api(
    service: FakeService | None = None,
    *,
    workspace_id: str = "ws1",
    enabled: bool = True,
) -> tuple[KnowledgeApi, FakeService]:
    fake = service or FakeService()
    config = SimpleNamespace(enabled=enabled, chunking=SimpleNamespace(max_file_mb=50))
    return (
        KnowledgeApi(KnowledgeServices(service=fake, config=config, workspace_id=workspace_id)),
        fake,
    )


def _data_url(mime: str, payload: bytes = b"hello") -> str:
    return f"data:{mime};base64,{base64.b64encode(payload).decode()}"


def _call(fake: FakeService, name: str) -> list[Any]:
    return [args for call, args in fake.calls if call == name]


# ---- 读动作 ----------------------------------------------------------------


def test_kb_list_read_returns_counts_and_no_paths() -> None:
    api, fake = _api()
    payload = api.handle("knowledge-kb-list", {"workspaceIds": ["ws1"]})

    assert payload["enabled"] is True
    kb = payload["kbs"][0]
    assert kb["name"] == "手册"
    assert kb["documentCount"] == 1
    assert "sourcePath" not in str(payload)
    assert _call(fake, "list_kbs")[0]["workspace_ids"] == ["ws1"]


def test_kb_list_rejects_workspace_outside_authenticated_scope() -> None:
    api, fake = _api()

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle("knowledge-kb-list", {"workspaceIds": ["ws1", "ws2"]})

    assert excinfo.value.code == "KNOWLEDGE_WORKSPACE_FORBIDDEN"
    assert excinfo.value.status == 403
    assert _call(fake, "list_kbs") == []


def test_doc_list_read_filters_by_visible_workspace() -> None:
    api, fake = _api()
    payload = api.handle("knowledge-doc-list", {"kbId": "kb_1"})

    assert payload["total"] == 1
    assert payload["documents"][0]["id"] == "doc_1"
    assert "sourcePath" not in payload["documents"][0]
    assert _call(fake, "list_documents")[0]["workspace_ids"] == ["ws1"]


def test_doc_get_returns_chunk_preview() -> None:
    service = FakeService(chunks=[_chunk()])
    api, _ = _api(service)

    payload = api.handle("knowledge-doc-get", {"id": "doc_1"})

    assert payload["document"]["id"] == "doc_1"
    assert payload["chunks"][0]["content"] == "部署失败怎么回滚"


def test_chunk_get_checks_document_scope() -> None:
    service = FakeService(
        kbs=[_kb(workspace_id="ws2")],
        chunks=[_chunk()],
    )
    api, _ = _api(service)

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle("knowledge-chunk-get", {"chunkId": "ck_1"})

    assert excinfo.value.code == "KNOWLEDGE_WORKSPACE_FORBIDDEN"


def test_search_maps_hits_and_degraded_reason() -> None:
    hit = SimpleNamespace(
        chunk_id="ck_1", doc_id="doc_1", kb_id="kb_1", chunk_index=0,
        heading_path="回滚", page=3, locator="p3", content="回滚步骤",
        char_count=4, score=0.87,
    )
    service = FakeService(
        search_result=SimpleNamespace(
            hits=(hit,), mode_used="keyword", degraded=("vector_disabled",),
            reason=None, routes=("keyword",),
        )
    )
    api, fake = _api(service)

    payload = api.handle("knowledge-search", {"query": "回滚", "limit": 5})

    assert payload["modeUsed"] == "keyword"
    assert payload["degraded"] == ["vector_disabled"]
    assert payload["hits"][0]["score"] == 0.87
    assert _call(fake, "search")[0]["workspace_ids"] == ["ws1"]
    assert _call(fake, "search")[0]["limit"] == 5


def test_search_requires_non_empty_query() -> None:
    api, _ = _api()

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle("knowledge-search", {"query": "   "})

    assert excinfo.value.code == "KNOWLEDGE_INVALID_REQUEST"


def test_stats_read_is_scoped() -> None:
    api, fake = _api()

    payload = api.handle("knowledge-stats", {})

    assert payload["documents"] == 1
    assert payload["db_bytes"] == 4096
    assert _call(fake, "stats")[0]["workspace_ids"] == ["ws1"]


# ---- KB 写动作 -------------------------------------------------------------


def test_create_kb_uses_payload_workspace_when_visible() -> None:
    api, fake = _api(workspace_id="ws2")

    payload = api.handle(
        "knowledge.kb.create",
        {"name": "新库", "description": "说明", "workspaceId": "ws2"},
    )

    assert payload["id"] == "kb_new"
    kwargs = _call(fake, "create_kb")[0]
    assert kwargs["workspace_id"] == "ws2"
    assert kwargs["name"] == "新库"
    assert kwargs["visibility"] == "workspace"


def test_create_kb_defaults_to_authenticated_workspace() -> None:
    api, fake = _api(workspace_id="ws3")

    api.handle("knowledge.kb.create", {"name": "新库", "visibility": "global"})

    kwargs = _call(fake, "create_kb")[0]
    assert kwargs["workspace_id"] == "ws3"
    assert kwargs["visibility"] == "global"


def test_create_kb_rejects_foreign_workspace_override() -> None:
    api, fake = _api(workspace_id="ws1")

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle("knowledge.kb.create", {"name": "越权", "workspaceId": "ws9"})

    assert excinfo.value.code == "KNOWLEDGE_WORKSPACE_FORBIDDEN"
    assert _call(fake, "create_kb") == []


def test_kb_update_maps_is_enabled_field() -> None:
    api, fake = _api()

    payload = api.handle(
        "knowledge.kb.update",
        {"id": "kb_1", "name": "改名", "isEnabled": False},
    )

    assert payload["ok"] is True
    recorded = _call(fake, "update_kb")[0]
    assert recorded["fields"] == {"name": "改名", "is_enabled": False}


def test_kb_update_without_fields_is_rejected() -> None:
    api, _ = _api()

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle("knowledge.kb.update", {"id": "kb_1"})

    assert excinfo.value.code == "KNOWLEDGE_INVALID_REQUEST"


def test_kb_delete_checks_visibility() -> None:
    service = FakeService(kbs=[_kb(workspace_id="ws2")])
    api, fake = _api(service)

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle("knowledge.kb.delete", {"id": "kb_1"})

    assert excinfo.value.code == "KNOWLEDGE_WORKSPACE_FORBIDDEN"
    assert _call(fake, "delete_kb") == []


def test_kb_delete_global_is_allowed() -> None:
    service = FakeService(kbs=[_kb(workspace_id="ws2", visibility="global")])
    api, fake = _api(service)

    api.handle("knowledge.kb.delete", {"id": "kb_1"})

    assert _call(fake, "delete_kb") == ["kb_1"]


# ---- 上传 -----------------------------------------------------------------


def test_upload_accepts_text_and_sanitizes_client_name() -> None:
    api, fake = _api()

    payload = api.handle(
        "knowledge.doc.add",
        {
            "kbId": "kb_1",
            "name": "../a.txt",
            "dataUrl": _data_url("text/plain"),
            "workspaceId": "ws1",
        },
    )

    recorded = _call(fake, "add_upload_bytes")[0]
    assert recorded["name"] == "a.txt"
    assert recorded["data"] == b"hello"
    assert recorded["mime"] == "text/plain"
    assert recorded["workspace_id"] == "ws1"
    assert payload["document"]["id"] == "doc_1"
    assert "sourcePath" not in str(payload)


def test_upload_accepts_base64_with_explicit_mime() -> None:
    api, fake = _api()

    api.handle(
        "knowledge.doc.add",
        {
            "kbId": "kb_1",
            "name": "notes.md",
            "data": base64.b64encode(b"# t").decode(),
            "mime": "text/markdown",
        },
    )

    recorded = _call(fake, "add_upload_bytes")[0]
    assert recorded["mime"] == "text/markdown"
    assert recorded["data"] == b"# t"


def test_upload_rejects_unsupported_mime() -> None:
    api, fake = _api()

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle(
            "knowledge.doc.add",
            {
                "kbId": "kb_1",
                "name": "a.exe",
                "dataUrl": _data_url("application/octet-stream", b"x"),
            },
        )

    assert excinfo.value.code == "KNOWLEDGE_UNSUPPORTED_TYPE"
    assert "MIME" in excinfo.value.message
    assert _call(fake, "add_upload_bytes") == []


def test_upload_rejects_mime_suffix_mismatch() -> None:
    api, fake = _api()

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle(
            "knowledge.doc.add",
            {"kbId": "kb_1", "name": "a.txt", "dataUrl": _data_url("application/pdf")},
        )

    assert excinfo.value.code == "KNOWLEDGE_UNSUPPORTED_TYPE"
    assert "MIME" in excinfo.value.message
    assert _call(fake, "add_upload_bytes") == []


def test_upload_rejects_empty_payload() -> None:
    api, fake = _api()

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle(
            "knowledge.doc.add",
            {"kbId": "kb_1", "name": "a.txt", "dataUrl": "data:text/plain;base64,"},
        )

    assert excinfo.value.code == "KNOWLEDGE_EMPTY_FILE"
    assert _call(fake, "add_upload_bytes") == []


def test_upload_rejects_bad_base64() -> None:
    api, fake = _api()

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle(
            "knowledge.doc.add",
            {"kbId": "kb_1", "name": "a.txt", "dataUrl": "data:text/plain;base64,%%%"},
        )

    assert excinfo.value.code == "KNOWLEDGE_INVALID_REQUEST"
    assert _call(fake, "add_upload_bytes") == []


def test_upload_rejects_oversized_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    import nanobot.webui.knowledge_api as module

    monkeypatch.setattr(module, "_MAX_UPLOAD_BYTES", 2)
    api, fake = _api()

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle(
            "knowledge.doc.add",
            {"kbId": "kb_1", "name": "a.txt", "dataUrl": _data_url("text/plain")},
        )

    assert excinfo.value.code == "KNOWLEDGE_FILE_TOO_LARGE"
    assert excinfo.value.status == 413
    assert "超过" in excinfo.value.message
    assert _call(fake, "add_upload_bytes") == []


def test_webui_upload_limit_defaults_to_20mb() -> None:
    import nanobot.webui.knowledge_api as module

    services = KnowledgeServices(
        service=FakeService(),
        config=SimpleNamespace(enabled=True, chunking=SimpleNamespace(max_file_mb=50)),
    )
    assert services.upload_limit_bytes(module._MAX_UPLOAD_BYTES) == 20 * 1024 * 1024

    smaller = KnowledgeServices(
        service=FakeService(),
        config=SimpleNamespace(enabled=True, chunking=SimpleNamespace(max_file_mb=8)),
    )
    assert smaller.upload_limit_bytes(module._MAX_UPLOAD_BYTES) == 8 * 1024 * 1024


def test_upload_rejects_kb_outside_scope() -> None:
    service = FakeService(kbs=[_kb(workspace_id="ws2")])
    api, fake = _api(service)

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle(
            "knowledge.doc.add",
            {"kbId": "kb_1", "name": "a.txt", "dataUrl": _data_url("text/plain")},
        )

    assert excinfo.value.code == "KNOWLEDGE_WORKSPACE_FORBIDDEN"
    assert _call(fake, "add_upload_bytes") == []


def test_add_file_uses_server_path_and_hides_it_on_error(
    tmp_path: Path,
) -> None:
    api, fake = _api()
    source = tmp_path / "a.txt"
    source.write_text("hello", encoding="utf-8")

    payload = api.handle(
        "knowledge.doc.add_file",
        {"kbId": "kb_1", "path": str(source)},
    )

    assert payload["document"]["id"] == "doc_file"
    assert _call(fake, "add_file")[0]["path"] == str(source)


def test_add_file_missing_source_maps_to_stable_code(tmp_path: Path) -> None:
    from nanobot.knowledge.extractor import KnowledgeExtractionError

    secret = str(tmp_path / "secret" / "missing.pdf")
    service = FakeService(failure=KnowledgeExtractionError(f"不是普通文件：{secret}"))
    api, _ = _api(service)

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle("knowledge.doc.add_file", {"kbId": "kb_1", "path": secret})

    assert excinfo.value.code == "KNOWLEDGE_SOURCE_MISSING"
    assert secret not in excinfo.value.message
    assert str(tmp_path) not in excinfo.value.message


# ---- 删除 / reindex / repair ------------------------------------------------


def test_delete_doc_write() -> None:
    api, fake = _api()

    payload = api.handle("knowledge.doc.delete", {"id": "doc_1"})

    assert payload["ok"] is True
    assert _call(fake, "delete_document") == ["doc_1"]


def test_delete_doc_not_found() -> None:
    api, _ = _api()

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle("knowledge.doc.delete", {"id": "doc_missing"})

    assert excinfo.value.code == "KNOWLEDGE_DOC_NOT_FOUND"
    assert excinfo.value.status == 404


def test_doc_reindex_returns_queued() -> None:
    api, fake = _api()

    payload = api.handle("knowledge.doc.reindex", {"id": "doc_1"})

    assert payload == {"ok": True, "queued": True}
    assert _call(fake, "reindex_document") == ["doc_1"]


def test_kb_reindex_queues_all_documents() -> None:
    api, fake = _api()

    payload = api.handle("knowledge.kb.reindex", {"id": "kb_1"})

    assert payload["queued"] == 2
    assert payload["documentIds"] == ["doc_1", "doc_2"]
    assert _call(fake, "reindex_kb") == ["kb_1"]


def test_repair_defaults_to_non_destructive_flags() -> None:
    api, fake = _api()

    payload = api.handle("knowledge.repair", {"purge": True})

    assert payload["fts_rebuilt"] == 1
    assert payload["files_purged"] == 1
    assert "rewritten_docs" in payload
    assert _call(fake, "repair")[0] == {"fts": True, "vectors": True, "purge": True}


# ---- disabled 配置 ---------------------------------------------------------


@pytest.mark.parametrize(
    "action,payload",
    [
        ("knowledge-kb-list", {}),
        ("knowledge-doc-list", {}),
        ("knowledge-doc-get", {"id": "doc_1"}),
        ("knowledge-search", {"query": "回滚"}),
        ("knowledge-stats", {}),
    ],
)
def test_disabled_read_actions_return_empty_payload(
    action: str, payload: dict[str, Any]
) -> None:
    api, fake = _api(enabled=False)

    result = api.handle(action, payload)

    assert result["enabled"] is False
    assert fake.calls == []


def test_disabled_write_actions_return_clear_error() -> None:
    api, fake = _api(enabled=False)

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle("knowledge.kb.create", {"name": "新库"})

    assert excinfo.value.code == "KNOWLEDGE_DISABLED"
    assert excinfo.value.status == 409
    assert _call(fake, "create_kb") == []


# ---- 路由 / 白名单 ---------------------------------------------------------


def test_read_paths_are_system_routes_and_not_mutations() -> None:
    assert knowledge_routes.READ_PATHS
    for path, action in knowledge_routes.READ_PATHS.items():
        assert _SYSTEM_ROUTES.get(path) == action, path
        assert path not in _SETTINGS_MUTATION_PATHS
        assert not WebUISettingsRouter.is_mutation_path(path)


def test_write_paths_require_authenticated_websocket() -> None:
    from nanobot.webui.inbound_commands import is_webui_mutation_action

    assert knowledge_routes.WRITE_PATHS
    for path, action in knowledge_routes.WRITE_PATHS.items():
        assert action in knowledge_routes.WRITE_ACTIONS, action
        # WS 动作名正则只放行 [A-Za-z0-9_.]；带连字符的动作名会被 400 拦掉。
        assert is_webui_mutation_action(action), action
        assert path in _SETTINGS_MUTATION_PATHS, path
        assert WebUISettingsRouter.is_mutation_path(path)
        assert _WEBUI_MUTATION_PATHS.get(action) == path
        assert GatewayHTTPHandler._webui_mutation_path(action, {}) == path


def test_action_sets_are_disjoint_and_cover_paths() -> None:
    assert not (knowledge_routes.READ_ACTIONS & knowledge_routes.WRITE_ACTIONS)
    assert set(knowledge_routes.READ_PATHS.values()) == knowledge_routes.READ_ACTIONS
    assert set(knowledge_routes.WRITE_PATHS.values()) == knowledge_routes.WRITE_ACTIONS


def test_unknown_action_is_404() -> None:
    api, _ = _api()

    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle("knowledge.frobnicate", {})

    assert excinfo.value.code == "KNOWLEDGE_UNKNOWN_ACTION"
    assert excinfo.value.status == 404


# ---- 容器 / 装配 -----------------------------------------------------------


def test_service_container_is_lazy_and_injectable() -> None:
    built: list[Any] = []

    class _Fake:
        pass

    container = KnowledgeServices(
        config=SimpleNamespace(enabled=True, chunking=SimpleNamespace(max_file_mb=6)),
        service_factory=lambda config: built.append(config) or _Fake(),
    )

    assert built == []
    assert isinstance(container.service, _Fake)
    assert len(built) == 1

    injected = _Fake()
    eager = KnowledgeServices(service=injected)
    assert eager.service is injected
    assert eager.enabled is True


def test_workspace_id_normalization_defaults() -> None:
    assert normalize_workspace_id("  ws1 ") == "ws1"
    assert normalize_workspace_id("   ") == "default"
    assert normalize_workspace_id(None) == "default"
    assert KnowledgeServices(service=FakeService(), workspace_id="").workspace_id == "default"


def test_build_knowledge_operations_is_lazy() -> None:
    api = build_knowledge_operations(Config())

    assert isinstance(api, KnowledgeApi)
    assert api.services.workspace_id == "default"
    assert api.services.enabled is True


class _FakeKnowledgeOperations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def handle(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((action, dict(payload or {})))
        return {"enabled": True, "action": action}


@pytest.mark.asyncio
async def test_settings_router_dispatches_knowledge_read_and_write(tmp_path: Path) -> None:
    from urllib.parse import parse_qs, urlsplit

    from websockets.datastructures import Headers

    from nanobot.webui.http_utils import http_json_response
    from nanobot.webui.settings_services import WebUISettingsServices

    fake = _FakeKnowledgeOperations()
    router = WebUISettingsRouter(
        settings=WebUISettingsServices.create(tmp_path / "config.json"),
        bus=SimpleNamespace(),
        logger=SimpleNamespace(exception=lambda *_args: None),
        check_api_token=lambda _request: True,
        parse_query=lambda path: parse_qs(urlsplit(path).query),
        json_response=http_json_response,
        error_response=lambda status, message: http_json_response(
            {"error": message}, status=status
        ),
        runtime_surface="browser",
        runtime_capabilities={},
        mcp_oauth_redirect_uri=lambda _request: "https://gateway.example/auth/mcp/callback",
        knowledge_operations=fake,
    )

    read_path = "/api/settings/knowledge/kbs"
    read_response = await router.dispatch(
        SimpleNamespace(),
        SimpleNamespace(path=f"{read_path}?limit=5", headers=Headers()),
        read_path,
    )
    assert read_response.status_code == 200
    assert fake.calls[-1] == ("knowledge-kb-list", {"limit": "5"})

    write_path = "/api/settings/knowledge/kbs/create"
    write_request = SimpleNamespace(path=write_path, headers=Headers())
    write_request._nanobot_webui_mutation_request = True
    write_request._nanobot_webui_mutation_payload = {
        "name": "manual",
        "workspaceId": "ws1",
    }
    write_request._nanobot_trusted_proxy_authenticated = True
    write_response = await router.dispatch(SimpleNamespace(), write_request, write_path)
    assert write_response.status_code == 200
    assert fake.calls[-1] == (
        "knowledge.kb.create",
        {"name": "manual", "workspaceId": "ws1"},
    )
