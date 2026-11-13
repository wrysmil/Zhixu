"""知识中心 WebUI 动作名单、路径表与传输无关分发。

读动作（认证 HTTP GET，登记 ``settings_routes._SYSTEM_ROUTES``）与写动作
（WS mutation，登记 ``settings_routes._SETTINGS_MUTATION_PATHS`` 与
``ws_http._WEBUI_MUTATION_PATHS``）以知识中心 spec §13.1 的表为准；动作名由
``knowledge_api.KnowledgeApi`` 提供实现，本模块不 import 任何传输层。

除 spec §13.1 的 6 个写动作外，另登记 KB 级 reindex、repair、服务端路径入库
和块详情读取（Task 19 行为清单要求；均为本地已认证 WebUI 才可达）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

from nanobot.webui.knowledge_services import KnowledgeServices

# ---- 动作名（前端 api.ts 逐字依赖）-----------------------------------------

KB_LIST: Final = "knowledge-kb-list"
DOC_LIST: Final = "knowledge-doc-list"
DOC_GET: Final = "knowledge-doc-get"
CHUNK_GET: Final = "knowledge-chunk-get"
DOC_CONTENT: Final = "knowledge-doc-content"
QUERY_HISTORY: Final = "knowledge-query-history"
SEARCH: Final = "knowledge-search"
STATS: Final = "knowledge-stats"
GRAPH_STATS: Final = "knowledge-graph-stats"
GRAPH_ENTITIES: Final = "knowledge-graph-entities"
GRAPH_ENTITY: Final = "knowledge-graph-entity"
GRAPH_SUBGRAPH: Final = "knowledge-graph-subgraph"
SETTINGS_GET: Final = "knowledge-settings-get"

KB_CREATE: Final = "knowledge.kb.create"
KB_UPDATE: Final = "knowledge.kb.update"
KB_DELETE: Final = "knowledge.kb.delete"
KB_REINDEX: Final = "knowledge.kb.reindex"
DOC_ADD: Final = "knowledge.doc.add"
DOC_ADD_FILE: Final = "knowledge.doc.add_file"
DOC_DELETE: Final = "knowledge.doc.delete"
DOC_REINDEX: Final = "knowledge.doc.reindex"
QUERY_HISTORY_CLEAR: Final = "knowledge.query_history.clear"
REPAIR: Final = "knowledge.repair"
GRAPH_EXTRACT: Final = "knowledge.graph.extract"
GRAPH_REBUILD: Final = "knowledge.graph.rebuild"
SETTINGS_UPDATE: Final = "knowledge.settings.update"

READ_ACTIONS: Final = frozenset({
    KB_LIST,
    DOC_LIST,
    DOC_GET,
    CHUNK_GET,
    DOC_CONTENT,
    QUERY_HISTORY,
    SEARCH,
    STATS,
    GRAPH_STATS,
    GRAPH_ENTITIES,
    GRAPH_ENTITY,
    GRAPH_SUBGRAPH,
    SETTINGS_GET,
})

WRITE_ACTIONS: Final = frozenset({
    KB_CREATE,
    KB_UPDATE,
    KB_DELETE,
    KB_REINDEX,
    DOC_ADD,
    DOC_ADD_FILE,
    DOC_DELETE,
    DOC_REINDEX,
    QUERY_HISTORY_CLEAR,
    REPAIR,
    GRAPH_EXTRACT,
    GRAPH_REBUILD,
    SETTINGS_UPDATE,
})

ACTION_NAMES: Final = READ_ACTIONS | WRITE_ACTIONS

# ---- 路径表 ----------------------------------------------------------------

READ_PATHS: Final[dict[str, str]] = {
    "/api/settings/knowledge/kbs": KB_LIST,
    "/api/settings/knowledge/docs": DOC_LIST,
    "/api/settings/knowledge/doc": DOC_GET,
    "/api/settings/knowledge/chunk": CHUNK_GET,
    "/api/settings/knowledge/doc/content": DOC_CONTENT,
    "/api/settings/knowledge/query-history": QUERY_HISTORY,
    "/api/settings/knowledge/search": SEARCH,
    "/api/settings/knowledge/stats": STATS,
    "/api/settings/knowledge/graph/stats": GRAPH_STATS,
    "/api/settings/knowledge/graph/entities": GRAPH_ENTITIES,
    "/api/settings/knowledge/graph/entity": GRAPH_ENTITY,
    "/api/settings/knowledge/graph/subgraph": GRAPH_SUBGRAPH,
    "/api/settings/knowledge/settings": SETTINGS_GET,
}

WRITE_PATHS: Final[dict[str, str]] = {
    "/api/settings/knowledge/kbs/create": KB_CREATE,
    "/api/settings/knowledge/kbs/update": KB_UPDATE,
    "/api/settings/knowledge/kbs/delete": KB_DELETE,
    "/api/settings/knowledge/kbs/reindex": KB_REINDEX,
    "/api/settings/knowledge/docs/add": DOC_ADD,
    "/api/settings/knowledge/docs/add-file": DOC_ADD_FILE,
    "/api/settings/knowledge/docs/delete": DOC_DELETE,
    "/api/settings/knowledge/docs/reindex": DOC_REINDEX,
    "/api/settings/knowledge/query-history/clear": QUERY_HISTORY_CLEAR,
    "/api/settings/knowledge/repair": REPAIR,
    "/api/settings/knowledge/graph/extract": GRAPH_EXTRACT,
    "/api/settings/knowledge/graph/rebuild": GRAPH_REBUILD,
    "/api/settings/knowledge/settings/update": SETTINGS_UPDATE,
}


class KnowledgeRouteError(ValueError):
    """知识域用户可见错误：稳定错误码 + HTTP 状态，不携带文件系统路径。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "KNOWLEDGE_INVALID_REQUEST",
        status: int = 400,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


Handler = Callable[[dict[str, Any], KnowledgeServices], dict[str, Any]]


def dispatch(
    handlers: Mapping[str, Handler],
    action: str,
    payload: dict[str, Any] | None,
    services: KnowledgeServices,
) -> dict[str, Any]:
    """按动作名调用注入的 handler；未知动作给稳定的 404 错误码。"""
    if action not in ACTION_NAMES:
        raise KnowledgeRouteError(
            f"unknown knowledge action: {action}",
            code="KNOWLEDGE_UNKNOWN_ACTION",
            status=404,
        )
    handler = handlers.get(action)
    if handler is None:
        raise KnowledgeRouteError(
            f"knowledge action is not wired: {action}",
            code="KNOWLEDGE_UNKNOWN_ACTION",
            status=404,
        )
    return handler(dict(payload or {}), services)
