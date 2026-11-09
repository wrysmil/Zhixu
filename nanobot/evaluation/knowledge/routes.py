"""知识检索评测的 HTTP 路由清单。

只提供 ``web.RouteDef``；handler 实现随 Task 8 接入。挂载进
``nanobot/evaluation/server.py`` 的**同一份**路由表，从而继承 Host/Origin
guard、CSRF、限流与未匹配方法的 405 兜底——不另建一套前缀相同但不受准入
保护的路由表。
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

#: 与既有记忆专项共用同一前缀（``nanobot/evaluation/server.py`` 的 ``API_PREFIX``）。
KNOWLEDGE_API_PREFIX = "/api/eval/v1"


async def _placeholder(request: web.Request) -> web.Response:
    """Task 8 前的占位：稳定返回 501，不静默 200。"""
    return web.json_response(
        {"error": {"code": "KNOWLEDGE_NOT_IMPLEMENTED", "message": "评测接口尚未接入"}},
        status=501,
    )


def knowledge_route_defs(prefix: str = KNOWLEDGE_API_PREFIX) -> list[web.RouteDef]:
    """契约 §2 的路由清单。

    形状约束：路径统一在 ``{prefix}/knowledge`` 之下，复数名词、无动词后缀；
    动作语义交给 HTTP 方法。
    """

    def r(method: str, path: str, handler: Any = _placeholder) -> web.RouteDef:
        return web.route(method, f"{prefix}{path}", handler)

    return [
        r("GET", "/knowledge/capabilities"),
        r("GET", "/knowledge/metrics"),
        r("GET", "/knowledge/datasets"),
        r("POST", "/knowledge/datasets"),
        r("GET", "/knowledge/datasets/{id}"),
        r("PATCH", "/knowledge/datasets/{id}"),
        r("DELETE", "/knowledge/datasets/{id}"),
        r("POST", "/knowledge/runs"),
        r("GET", "/knowledge/runs"),
        r("GET", "/knowledge/runs/{id}"),
        r("GET", "/knowledge/runs/{id}/report"),
        r("POST", "/knowledge/runs/{id}/cancel"),
        r("GET", "/knowledge/compare"),
    ]


__all__ = ["KNOWLEDGE_API_PREFIX", "knowledge_route_defs"]
