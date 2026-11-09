"""WebUI 知识域 payload 转换与业务调用。

职责边界（知识中心 spec §13.1 / §14）：

- **只做转换**：camelCase payload → 类型化调用 → JSON 安全响应；传输（HTTP/WS）
  与落盘（原子写、清理）都留在别的层。
- **作用域**：工作区只取自已认证的 ``KnowledgeServices``；payload 里的
  ``workspaceId`` / ``workspaceIds`` 只能在该可见集合内选择，``kbId`` 先做
  存在性 + 归属校验，越权一律 403。
- **上传**：后缀与 MIME 双校验、base64 安全解码、``max_file_mb`` / 6 MB
  取小者；客户端文件名只取 basename，交给服务层 ``safe_filename`` 再清洗。
- **错误**：服务异常映射成稳定错误码 + 状态，错误信息不含文件系统路径或密钥。
"""

from __future__ import annotations

import base64
import binascii
import re
import sqlite3
from pathlib import Path
from typing import Any, cast

from loguru import logger

from nanobot.webui import knowledge_routes as routes
from nanobot.webui.attachment_ingress import extract_data_url_mime
from nanobot.webui.knowledge_routes import KnowledgeRouteError
from nanobot.webui.knowledge_services import KnowledgeServices, normalize_workspace_id

#: WebUI 单文件上限（spec §13.1：沿用附件入口的 6 MB；配置更小时取配置）。
_MAX_UPLOAD_BYTES = 6 * 1024 * 1024

#: 后缀 → 可接受 MIME（storage spec §9.1；不含 csv）。
_SUFFIX_MIMES: dict[str, frozenset[str]] = {
    ".pdf": frozenset({"application/pdf"}),
    ".docx": frozenset({
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }),
    ".xlsx": frozenset({
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }),
    ".pptx": frozenset({
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }),
    ".md": frozenset({"text/markdown", "text/plain"}),
    ".txt": frozenset({"text/plain", "text/markdown"}),
}

_ALLOWED_MIMES: frozenset[str] = frozenset(
    mime for mimes in _SUFFIX_MIMES.values() for mime in mimes
)

_DOC_STATUSES = frozenset({"pending", "processing", "ready", "failed"})
_SEARCH_MODES = frozenset({"hybrid", "semantic", "keyword"})
_VISIBILITIES = frozenset({"workspace", "global"})

_DEFAULT_SEARCH_LIMIT = 8
_MAX_SEARCH_LIMIT = 50
_MAX_DOC_LIST_LIMIT = 200
_MAX_CHUNK_PREVIEW = 20
_DEFAULT_CHUNK_PREVIEW = 5
_KB_DOC_COUNT_LIMIT = 5000

_DATA_URL_RE = re.compile(r"^data:([^;,]+)(?:;[^,]*)*;base64,(.*)$", re.DOTALL)

_STATS_KEYS = (
    "kbs",
    "documents",
    "chunks",
    "by_status",
    "vector",
    "pending",
    "expired_leases",
    "drift",
    "db_bytes",
    "uploads_bytes",
)

_REPAIR_KEYS = (
    "fts_rebuilt",
    "vectors_scanned",
    "vectors_rewrite",
    "vectors_purge",
    "vector_errors",
    "files_purged",
    "rewritten_docs",
    "purged_docs",
)

#: 对外错误类型别名（路由层与测试共用）。
KnowledgeApiError = KnowledgeRouteError


# ---- JSON 安全 / 字段读取 ---------------------------------------------------


def _json_safe(value: Any) -> Any:
    """把响应递归收敛成 JSON 原生类型；未知对象只留字符串，避免意外泄露字段。"""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        return {str(key): _json_safe(item) for key, item in mapping.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        sequence = cast(list[Any], value)
        return [_json_safe(item) for item in sequence]
    return str(value)


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise KnowledgeRouteError(f"{key} 必须是字符串")
    text = value.strip()
    return text or None


def _required_str(payload: dict[str, Any], key: str, message: str) -> str:
    text = _optional_str(payload, key)
    if text is None:
        raise KnowledgeRouteError(message)
    return text


def _optional_int(
    payload: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = payload.get(key)
    if value is None or value == "":
        return default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise KnowledgeRouteError(f"{key} 必须是整数")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise KnowledgeRouteError(f"{key} 必须是整数") from exc
    if number < minimum or number > maximum:
        raise KnowledgeRouteError(f"{key} 必须在 {minimum}..{maximum} 之间")
    return number


def _optional_bool(payload: dict[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes"}:
            return True
        if text in {"0", "false", "no"}:
            return False
    raise KnowledgeRouteError(f"{key} 必须是布尔值")


# ---- 作用域 -----------------------------------------------------------------


def _forbidden() -> KnowledgeRouteError:
    return KnowledgeRouteError(
        "工作区无权访问该知识资源",
        code="KNOWLEDGE_WORKSPACE_FORBIDDEN",
        status=403,
    )


def _requested_workspaces(
    payload: dict[str, Any], services: KnowledgeServices
) -> list[str]:
    """读动作的工作区过滤：只能是已认证可见集合的子集。"""
    raw = payload.get("workspaceIds")
    if raw is None:
        raw = payload.get("workspaceId")
    if raw is None:
        return list(services.workspace_ids)
    if isinstance(raw, str):
        values = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, (list, tuple, set)):
        items = cast(list[Any], raw)
        values = [str(item).strip() for item in items if str(item).strip()]
    else:
        raise KnowledgeRouteError("workspaceIds 必须是字符串或字符串数组")
    if not values:
        return list(services.workspace_ids)
    requested: list[str] = []
    for value in values:
        normalized = normalize_workspace_id(value)
        if not services.is_visible(normalized):
            raise _forbidden()
        if normalized not in requested:
            requested.append(normalized)
    return requested


def _target_workspace(payload: dict[str, Any], services: KnowledgeServices) -> str:
    """写动作目标工作区：payload 只能在可见集合内选择，缺省用认证上下文。"""
    raw = _optional_str(payload, "workspaceId")
    if raw is None:
        return services.workspace_id
    normalized = normalize_workspace_id(raw)
    if not services.is_visible(normalized):
        raise _forbidden()
    return normalized


def _kb_visible(kb: Any, services: KnowledgeServices) -> bool:
    if str(getattr(kb, "visibility", "workspace")) == "global":
        return True
    return services.is_visible(getattr(kb, "workspace_id", None))


def _require_kb(kb_id: str, services: KnowledgeServices) -> Any:
    kb = services.service.get_kb(kb_id)
    if kb is None:
        raise KnowledgeRouteError(
            "知识库不存在",
            code="KNOWLEDGE_KB_NOT_FOUND",
            status=404,
        )
    if not _kb_visible(kb, services):
        raise _forbidden()
    return kb


def _require_document(doc_id: str, services: KnowledgeServices) -> Any:
    document = services.service.get_document(doc_id)
    if document is None:
        raise KnowledgeRouteError(
            "文档不存在",
            code="KNOWLEDGE_DOC_NOT_FOUND",
            status=404,
        )
    kb = services.service.get_kb(str(getattr(document, "kb_id", "")))
    if kb is not None:
        if not _kb_visible(kb, services):
            raise _forbidden()
    elif not services.is_visible(getattr(document, "workspace_id", None)):
        raise _forbidden()
    return document


def _kb_id_from(payload: dict[str, Any]) -> str:
    return _required_str(payload, "kbId", "kbId 不能为空")


# ---- 读模型 -----------------------------------------------------------------


def _kb_payload(kb: Any, document_count: int) -> dict[str, Any]:
    return {
        "id": str(getattr(kb, "id", "")),
        "name": str(getattr(kb, "name", "")),
        "description": str(getattr(kb, "description", "")),
        "workspaceId": str(getattr(kb, "workspace_id", "")),
        "visibility": str(getattr(kb, "visibility", "workspace")),
        "embeddingBackend": str(getattr(kb, "embedding_backend", "none")),
        "chunkSize": int(getattr(kb, "chunk_size", 0) or 0),
        "chunkOverlap": int(getattr(kb, "chunk_overlap", 0) or 0),
        "isEnabled": bool(getattr(kb, "is_enabled", True)),
        "createdAt": str(getattr(kb, "created_at", "")),
        "updatedAt": str(getattr(kb, "updated_at", "")),
        "documentCount": document_count,
    }


def _document_payload(document: Any) -> dict[str, Any]:
    """文档读模型；**绝不包含** ``source_path``（路径泄露面）。"""
    return {
        "id": str(getattr(document, "id", "")),
        "kbId": str(getattr(document, "kb_id", "")),
        "title": str(getattr(document, "title", "")),
        "originalName": str(getattr(document, "original_name", "")),
        "mime": str(getattr(document, "mime", "")),
        "sourceKind": str(getattr(document, "source_kind", "")),
        "status": str(getattr(document, "parse_status", "")),
        "errorCode": str(getattr(document, "error_code", "")),
        "error": str(getattr(document, "error_message", "")),
        "chunkCount": int(getattr(document, "chunk_count", 0) or 0),
        "charCount": int(getattr(document, "char_count", 0) or 0),
        "vectorStatus": str(getattr(document, "vector_status", "")),
        "isEnabled": bool(getattr(document, "is_enabled", True)),
        "createdAt": str(getattr(document, "created_at", "")),
        "updatedAt": str(getattr(document, "updated_at", "")),
    }


def _chunk_payload(chunk: Any) -> dict[str, Any]:
    return {
        "id": str(getattr(chunk, "id", "")),
        "docId": str(getattr(chunk, "doc_id", "")),
        "kbId": str(getattr(chunk, "kb_id", "")),
        "index": int(getattr(chunk, "chunk_index", 0) or 0),
        "heading": str(getattr(chunk, "heading_path", "")),
        "page": getattr(chunk, "page", None),
        "locator": str(getattr(chunk, "locator", "")),
        "content": str(getattr(chunk, "content", "")),
        "charCount": int(getattr(chunk, "char_count", 0) or 0),
    }


# ---- 上传解码 ---------------------------------------------------------------


def _client_basename(payload: dict[str, Any]) -> str:
    raw = payload.get("name")
    if not isinstance(raw, str):
        return "file"
    base = Path(raw.replace("\\", "/")).name.strip()
    return base or "file"


def _decode_upload(payload: dict[str, Any]) -> tuple[str, str, bytes]:
    """返回 ``(basename, mime, data)``；任何一步不合法都抛稳定错误码。"""
    name = _client_basename(payload)
    declared = _optional_str(payload, "mime")
    declared_mime = declared.lower() if declared else None

    data_url = payload.get("dataUrl")
    if data_url is not None and not isinstance(data_url, str):
        raise KnowledgeRouteError("dataUrl 必须是字符串")
    if isinstance(data_url, str) and data_url.strip():
        match = _DATA_URL_RE.match(data_url.strip())
        if match is None:
            if extract_data_url_mime(data_url) is None:
                raise KnowledgeRouteError("dataUrl 缺少 MIME 或不是 base64 data URL")
            raise KnowledgeRouteError("dataUrl base64 解码失败")
        mime = extract_data_url_mime(data_url) or ""
        if not mime:
            raise KnowledgeRouteError("dataUrl 缺少 MIME")
        if declared_mime is not None and declared_mime != mime:
            raise KnowledgeRouteError("MIME 与 dataUrl 声明不一致")
        encoded = match.group(2)
    else:
        raw = payload.get("data")
        if raw is None:
            raw = payload.get("base64")
        if not isinstance(raw, str) or not raw.strip():
            raise KnowledgeRouteError(
                "文件内容为空",
                code="KNOWLEDGE_EMPTY_FILE",
            )
        encoded = raw
        if declared_mime is None:
            raise KnowledgeRouteError("缺少 MIME 类型")
        mime = declared_mime

    if mime not in _ALLOWED_MIMES:
        raise KnowledgeRouteError(
            f"不支持的 MIME：{mime}",
            code="KNOWLEDGE_UNSUPPORTED_TYPE",
        )
    suffix = Path(name).suffix.lower()
    if suffix not in _SUFFIX_MIMES:
        raise KnowledgeRouteError(
            f"不支持的上传类型：{suffix or name}",
            code="KNOWLEDGE_UNSUPPORTED_TYPE",
        )
    if mime not in _SUFFIX_MIMES[suffix]:
        raise KnowledgeRouteError(
            f"MIME 与后缀不匹配：{mime} / {suffix}",
            code="KNOWLEDGE_UNSUPPORTED_TYPE",
        )
    try:
        data = base64.b64decode(re.sub(r"\s+", "", encoded), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise KnowledgeRouteError("dataUrl base64 解码失败") from exc
    if not data:
        raise KnowledgeRouteError("文件内容为空", code="KNOWLEDGE_EMPTY_FILE")
    return name, mime, data


def _human_size(limit: int) -> str:
    if limit >= 1024 * 1024 and limit % (1024 * 1024) == 0:
        return f"{limit // (1024 * 1024)} MB"
    return f"{limit} 字节"


# ---- 错误映射 ---------------------------------------------------------------


def _translate_service_error(exc: BaseException) -> KnowledgeRouteError:
    """服务/仓储异常 → 稳定错误码；消息里不带路径、密钥或原始堆栈。"""
    from nanobot.knowledge.extractor import (
        KnowledgeExtractionError,
        KnowledgeUnsupportedType,
    )
    from nanobot.knowledge.repository import DuplicateKnowledgeBaseError
    from nanobot.knowledge.service import KnowledgeServiceError

    if isinstance(exc, sqlite3.OperationalError):
        text = str(exc).lower()
        if "locked" in text or "busy" in text:
            return KnowledgeRouteError(
                "知识库正忙，请稍后重试",
                code="KNOWLEDGE_DB_BUSY",
                status=503,
            )
    if isinstance(exc, DuplicateKnowledgeBaseError):
        return KnowledgeRouteError(
            "同名知识库已存在",
            code="KNOWLEDGE_KB_DUPLICATE",
            status=409,
        )
    if isinstance(exc, KnowledgeUnsupportedType):
        return KnowledgeRouteError(
            "不支持的文件类型",
            code="KNOWLEDGE_UNSUPPORTED_TYPE",
        )
    if isinstance(exc, KnowledgeExtractionError):
        return KnowledgeRouteError(
            "源文件不存在或不是普通文件",
            code="KNOWLEDGE_SOURCE_MISSING",
            status=404,
        )
    if isinstance(exc, KnowledgeServiceError):
        return KnowledgeRouteError("知识服务拒绝了该操作", code="KNOWLEDGE_INVALID_REQUEST")
    if isinstance(exc, KeyError):
        return KnowledgeRouteError(
            "知识库或文档不存在",
            code="KNOWLEDGE_NOT_FOUND",
            status=404,
        )
    if isinstance(exc, sqlite3.Error):
        logger.exception("knowledge webui sqlite failure")
        return KnowledgeRouteError(
            "知识库存储不可用",
            code="KNOWLEDGE_STORE_UNAVAILABLE",
            status=503,
        )
    if isinstance(exc, ValueError):
        # 仓储层参数校验（字段名白名单）拼出的消息不含路径，可原样回传。
        return KnowledgeRouteError(str(exc) or "请求不合法")
    logger.exception("knowledge webui action failed")
    return KnowledgeRouteError(
        "知识服务暂不可用",
        code="KNOWLEDGE_SERVICE_ERROR",
        status=500,
    )


# ---- API --------------------------------------------------------------------


class KnowledgeApi:
    """知识域动作入口：``handle(action, payload)`` → JSON 安全 dict。"""

    def __init__(self, services: KnowledgeServices) -> None:
        self._services = services
        self._handlers: dict[str, routes.Handler] = {
            routes.KB_LIST: self._kb_list,
            routes.DOC_LIST: self._doc_list,
            routes.DOC_GET: self._doc_get,
            routes.CHUNK_GET: self._chunk_get,
            routes.SEARCH: self._search,
            routes.STATS: self._stats,
            routes.KB_CREATE: self._kb_create,
            routes.KB_UPDATE: self._kb_update,
            routes.KB_DELETE: self._kb_delete,
            routes.KB_REINDEX: self._kb_reindex,
            routes.DOC_ADD: self._doc_add,
            routes.DOC_ADD_FILE: self._doc_add_file,
            routes.DOC_DELETE: self._doc_delete,
            routes.DOC_REINDEX: self._doc_reindex,
            routes.REPAIR: self._repair,
        }

    @property
    def services(self) -> KnowledgeServices:
        return self._services

    def handle(
        self, action: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if action not in routes.ACTION_NAMES:
            raise KnowledgeRouteError(
                f"unknown knowledge action: {action}",
                code="KNOWLEDGE_UNKNOWN_ACTION",
                status=404,
            )
        if not self._services.enabled:
            if action in routes.READ_ACTIONS:
                return self._disabled_payload(action)
            raise KnowledgeRouteError(
                "知识中心未启用（knowledge.enabled=false）",
                code="KNOWLEDGE_DISABLED",
                status=409,
            )
        try:
            result = routes.dispatch(self._handlers, action, payload, self._services)
        except KnowledgeRouteError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一映射为稳定错误码
            raise _translate_service_error(exc) from exc
        return cast(dict[str, Any], _json_safe(result))

    # ---- 读 ----
    def _kb_list(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> dict[str, Any]:
        workspace_ids = _requested_workspaces(payload, services)
        kbs = services.service.list_kbs(workspace_ids=workspace_ids)
        payload_kbs: list[dict[str, Any]] = []
        for kb in kbs:
            documents = services.service.list_documents(
                kb_id=str(getattr(kb, "id", "")),
                limit=_KB_DOC_COUNT_LIMIT,
            )
            payload_kbs.append(_kb_payload(kb, len(documents)))
        return {"enabled": True, "kbs": payload_kbs}

    def _doc_list(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> dict[str, Any]:
        workspace_ids = _requested_workspaces(payload, services)
        kb_id = _optional_str(payload, "kbId")
        if kb_id is not None:
            _require_kb(kb_id, services)
        status = _optional_str(payload, "status")
        if status is not None and status not in _DOC_STATUSES:
            raise KnowledgeRouteError(f"status 必须是 {sorted(_DOC_STATUSES)} 之一")
        limit = _optional_int(
            payload, "limit", default=100, minimum=1, maximum=_MAX_DOC_LIST_LIMIT
        )
        offset = _optional_int(
            payload, "offset", default=0, minimum=0, maximum=1_000_000
        )
        documents = services.service.list_documents(
            kb_id=kb_id,
            workspace_ids=workspace_ids,
            status=status,
            offset=offset,
            limit=limit,
        )
        items = [_document_payload(document) for document in documents]
        return {"enabled": True, "documents": items, "total": len(items)}

    def _doc_get(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> dict[str, Any]:
        doc_id = _required_str(payload, "id", "id 不能为空")
        document = _require_document(doc_id, services)
        limit = _optional_int(
            payload,
            "chunkLimit",
            default=_DEFAULT_CHUNK_PREVIEW,
            minimum=1,
            maximum=_MAX_CHUNK_PREVIEW,
        )
        chunks = services.service.list_chunks(doc_id, limit=limit)
        return {
            "enabled": True,
            "document": _document_payload(document),
            "chunks": [_chunk_payload(chunk) for chunk in chunks],
            "chunkCount": int(getattr(document, "chunk_count", 0) or 0),
        }

    def _chunk_get(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> dict[str, Any]:
        chunk_id = _required_str(payload, "chunkId", "chunkId 不能为空")
        chunk = services.service.get_chunk(chunk_id)
        if chunk is None:
            raise KnowledgeRouteError(
                "块不存在",
                code="KNOWLEDGE_CHUNK_NOT_FOUND",
                status=404,
            )
        _require_kb(str(getattr(chunk, "kb_id", "")), services)
        return {"enabled": True, "chunk": _chunk_payload(chunk)}

    def _search(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> dict[str, Any]:
        query = _required_str(payload, "query", "query 不能为空")
        mode = (_optional_str(payload, "mode") or "hybrid").lower()
        if mode not in _SEARCH_MODES:
            raise KnowledgeRouteError(f"mode 必须是 {sorted(_SEARCH_MODES)} 之一")
        limit = _optional_int(
            payload,
            "limit",
            default=_DEFAULT_SEARCH_LIMIT,
            minimum=1,
            maximum=_MAX_SEARCH_LIMIT,
        )
        kb_ids = self._requested_kb_ids(payload, services)
        result = services.service.search(
            query,
            workspace_ids=_requested_workspaces(payload, services),
            kb_ids=kb_ids,
            mode=mode,
            limit=limit,
        )
        hits = getattr(result, "hits", ())
        return {
            "enabled": True,
            "modeUsed": str(getattr(result, "mode_used", mode)),
            "degraded": [str(reason) for reason in getattr(result, "degraded", ())],
            "reason": getattr(result, "reason", None),
            "routes": [str(route) for route in getattr(result, "routes", ())],
            "hits": [
                {
                    "chunkId": str(getattr(hit, "chunk_id", "")),
                    "docId": str(getattr(hit, "doc_id", "")),
                    "kbId": str(getattr(hit, "kb_id", "")),
                    "score": float(getattr(hit, "score", 0.0)),
                    "content": str(getattr(hit, "content", "")),
                    "heading": str(getattr(hit, "heading_path", "")),
                    "page": getattr(hit, "page", None),
                    "locator": str(getattr(hit, "locator", "")),
                }
                for hit in hits
            ],
        }

    def _stats(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> dict[str, Any]:
        result = services.service.stats(
            workspace_ids=_requested_workspaces(payload, services)
        )
        payload_stats = {key: result.get(key) for key in _STATS_KEYS}
        payload_stats["enabled"] = True
        return payload_stats

    # ---- 写 ----
    def _kb_create(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> dict[str, Any]:
        name = _required_str(payload, "name", "name 不能为空")
        if len(name) > 200:
            raise KnowledgeRouteError("name 过长（上限 200 字符）")
        description = _optional_str(payload, "description") or ""
        visibility = (_optional_str(payload, "visibility") or "workspace").lower()
        if visibility not in _VISIBILITIES:
            raise KnowledgeRouteError(f"visibility 必须是 {sorted(_VISIBILITIES)} 之一")
        kb = services.service.create_kb(
            name=name,
            description=description,
            workspace_id=_target_workspace(payload, services),
            visibility=visibility,
        )
        return {"id": str(getattr(kb, "id", ""))}

    def _kb_update(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> dict[str, Any]:
        kb_id = _required_str(payload, "id", "id 不能为空")
        _require_kb(kb_id, services)
        fields: dict[str, Any] = {}
        name = _optional_str(payload, "name")
        if name is not None:
            fields["name"] = name
        if "description" in payload:
            fields["description"] = _optional_str(payload, "description") or ""
        if "isEnabled" in payload:
            fields["is_enabled"] = _optional_bool(payload, "isEnabled", default=True)
        if not fields:
            raise KnowledgeRouteError("没有可更新的字段")
        services.service.update_kb(kb_id, **fields)
        return {"ok": True, "id": kb_id}

    def _kb_delete(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> dict[str, Any]:
        kb_id = _required_str(payload, "id", "id 不能为空")
        _require_kb(kb_id, services)
        services.service.delete_kb(kb_id)
        return {"ok": True, "id": kb_id}

    def _kb_reindex(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> dict[str, Any]:
        kb_id = _required_str(payload, "id", "id 不能为空")
        _require_kb(kb_id, services)
        document_ids = list(services.service.reindex_kb(kb_id))
        return {"ok": True, "queued": len(document_ids), "documentIds": document_ids}

    def _doc_add(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> dict[str, Any]:
        kb_id = _kb_id_from(payload)
        _require_kb(kb_id, services)
        workspace_id = _target_workspace(payload, services)
        name, mime, data = _decode_upload(payload)
        limit = services.upload_limit_bytes(_MAX_UPLOAD_BYTES)
        if len(data) > limit:
            raise KnowledgeRouteError(
                f"文件超过 {_human_size(limit)} 上限，请改用 CLI 导入",
                code="KNOWLEDGE_FILE_TOO_LARGE",
                status=413,
            )
        title = _optional_str(payload, "title")
        document = services.service.add_upload_bytes(
            name,
            data,
            kb_id=kb_id,
            workspace_id=workspace_id,
            mime=mime,
            title=title,
        )
        return {"document": _document_payload(document)}

    def _doc_add_file(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> dict[str, Any]:
        kb_id = _kb_id_from(payload)
        _require_kb(kb_id, services)
        path = _required_str(payload, "path", "path 不能为空")
        outcome = services.service.add_file(
            path,
            kb_id=kb_id,
            workspace_id=_target_workspace(payload, services),
            title=_optional_str(payload, "title"),
        )
        document = getattr(outcome, "document", outcome)
        return {"document": _document_payload(document)}

    def _doc_delete(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> dict[str, Any]:
        doc_id = _required_str(payload, "id", "id 不能为空")
        _require_document(doc_id, services)
        services.service.delete_document(doc_id)
        return {"ok": True, "id": doc_id}

    def _doc_reindex(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> dict[str, Any]:
        doc_id = _required_str(payload, "id", "id 不能为空")
        _require_document(doc_id, services)
        queued = bool(services.service.reindex_document(doc_id))
        return {"ok": queued, "queued": queued}

    def _repair(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> dict[str, Any]:
        result = services.service.repair(
            fts=_optional_bool(payload, "fts", default=True),
            vectors=_optional_bool(payload, "vectors", default=True),
            purge=_optional_bool(payload, "purge", default=False),
        )
        merged: dict[str, Any] = {"ok": True}
        for key in _REPAIR_KEYS:
            default: Any = [] if key.endswith("_docs") else 0
            merged[key] = result.get(key, default)
        return merged

    # ---- 读动作在禁用时的空响应 ----
    @staticmethod
    def _disabled_payload(action: str) -> dict[str, Any]:
        if action == routes.KB_LIST:
            return {"enabled": False, "kbs": []}
        if action == routes.DOC_LIST:
            return {"enabled": False, "documents": [], "total": 0}
        if action == routes.DOC_GET:
            return {"enabled": False, "document": None, "chunks": [], "chunkCount": 0}
        if action == routes.CHUNK_GET:
            return {"enabled": False, "chunk": None}
        if action == routes.SEARCH:
            return {
                "enabled": False,
                "hits": [],
                "modeUsed": "none",
                "degraded": ["disabled"],
                "reason": "knowledge_disabled",
                "routes": [],
            }
        return {key: (None if key in {"vector", "drift"} else 0) for key in _STATS_KEYS} | {
            "enabled": False,
            "by_status": {},
        }

    def _requested_kb_ids(
        self, payload: dict[str, Any], services: KnowledgeServices
    ) -> list[str] | None:
        raw = payload.get("kbIds")
        if raw is None:
            raw = payload.get("kbId")
        if raw is None:
            return None
        if isinstance(raw, str):
            values = [item.strip() for item in raw.split(",") if item.strip()]
        elif isinstance(raw, (list, tuple, set)):
            items = cast(list[Any], raw)
            values = [str(item).strip() for item in items if str(item).strip()]
        else:
            raise KnowledgeRouteError("kbIds 必须是字符串或字符串数组")
        if not values:
            return None
        for value in values:
            _require_kb(value, services)
        return values
