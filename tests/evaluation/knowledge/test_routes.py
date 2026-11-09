"""知识评测路由必须挂进**同一份** RouteDef 清单（继承 guard/CSRF/405 兜底）。

这份文件锁的是**边界契约**，不是 happy path：

- 挂载：知识路由必须出现在 ``server._routes()`` 这份共享清单里，不能另起一套
  前缀相同但不受准入保护的路由表；
- 形状：路径统一在 ``/api/eval/v1/knowledge`` 之下，复数名词、无动词后缀；
- 占位：Task 8 接入真实 handler 之前，占位 handler 必须稳定返回 501，
  **不能静默 200**（否则前端会把「还没实现」当成「评测跑完了」）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from nanobot.evaluation.knowledge.routes import (
    KNOWLEDGE_API_PREFIX,
    knowledge_route_defs,
)
from nanobot.evaluation.server import API_PREFIX, _routes

PREFIX = "/api/eval/v1"
PASSPHRASE = "correct-horse-battery-staple"

#: 契约 §2 的知识路由矩阵（方法 + 路径）。Task 8 换真 handler 时这份清单不能变。
EXPECTED_ROUTES: set[tuple[str, str]] = {
    ("GET", "/knowledge/capabilities"),
    ("GET", "/knowledge/metrics"),
    ("GET", "/knowledge/datasets"),
    ("POST", "/knowledge/datasets"),
    ("GET", "/knowledge/datasets/{id}"),
    ("PATCH", "/knowledge/datasets/{id}"),
    ("DELETE", "/knowledge/datasets/{id}"),
    ("POST", "/knowledge/runs"),
    ("GET", "/knowledge/runs"),
    ("GET", "/knowledge/runs/{id}"),
    ("GET", "/knowledge/runs/{id}/report"),
    ("POST", "/knowledge/runs/{id}/cancel"),
    ("GET", "/knowledge/compare"),
}

#: 动词后缀黑名单：资源路由用复数名词，动作语义交给 HTTP 方法。
VERB_SUFFIXES = ("/create", "/delete", "/update", "/list", "/fetch", "/remove")


# --------------------------------------------------------------------------- 挂载


def test_knowledge_routes_are_mounted_in_shared_route_table() -> None:
    """知识路由必须在共享清单里——guard/CSRF/限流/405 兜底都从这份清单推导。"""
    shared = {(route.method, route.path) for route in _routes()}
    knowledge = {(route.method, route.path) for route in knowledge_route_defs(PREFIX)}

    assert knowledge, "知识路由清单不能为空"
    missing = knowledge - shared
    assert not missing, f"未挂进共享清单：{sorted(missing)}"


def test_knowledge_route_paths_match_contract() -> None:
    """契约 §2 的路由矩阵：方法与路径一一对应，不多不少。"""
    actual = {(route.method, route.path) for route in knowledge_route_defs(PREFIX)}
    expected = {(method, f"{PREFIX}{path}") for method, path in EXPECTED_ROUTES}
    assert actual == expected


def test_knowledge_routes_use_plural_nouns_without_verbs() -> None:
    for route in knowledge_route_defs(PREFIX):
        assert route.path.startswith(f"{PREFIX}/knowledge"), route.path
        for suffix in VERB_SUFFIXES:
            assert not route.path.endswith(suffix), route.path


def test_knowledge_default_prefix_matches_shared_api_prefix() -> None:
    """默认前缀必须与评测平台既有 API 前缀一致，否则会落到别的路由树。"""
    assert KNOWLEDGE_API_PREFIX == API_PREFIX == PREFIX


def test_knowledge_routes_do_not_shadow_existing_routes() -> None:
    """同一 (method, path) 只能挂一次；重复会让 aiohttp 静默选第一条。"""
    knowledge = {(route.method, route.path) for route in knowledge_route_defs(PREFIX)}
    shared = [(route.method, route.path) for route in _routes()]
    duplicated = {key for key in shared if key in knowledge and shared.count(key) > 1}
    assert not duplicated, f"路由重复挂载：{sorted(duplicated)}"


# --------------------------------------------------------------------------- 占位 handler


async def test_placeholder_handlers_are_callable_and_return_501() -> None:
    """占位 handler 必须真的能被调用（签名不炸），并稳定返回 501。"""
    for route in knowledge_route_defs(PREFIX):
        handler = route.handler
        assert handler is not None, route.path
        assert callable(handler), route.path
        response = await handler(None)
        assert isinstance(response, web.Response)
        assert response.status == 501, route.path
        assert b"KNOWLEDGE_NOT_IMPLEMENTED" in response.body, route.path


async def test_placeholder_error_envelope_shape() -> None:
    """占位的错误信封与既有评测错误形状一致：``{"error": {"code", "message"}}``。"""
    for route in knowledge_route_defs(PREFIX):
        response = await route.handler(None)
        body = json.loads(response.body)
        assert body["error"]["code"] == "KNOWLEDGE_NOT_IMPLEMENTED"
        assert body["error"]["message"]


# --------------------------------------------------------------------------- HTTP 层


@pytest.fixture
def app(tmp_path: Path) -> Any:
    """真应用（起调度器生命周期）；装配写在本文件里，不改共享 conftest。"""
    from nanobot.evaluation.auth import AuthConfig
    from nanobot.evaluation.database import EvaluationDatabase
    from nanobot.evaluation.repository import EvaluationRepository
    from nanobot.evaluation.scheduler import SchedulerConfig
    from nanobot.evaluation.server import create_app
    from nanobot.evaluation.services import EvaluationService

    root = tmp_path / "evaluation"
    root.mkdir(parents=True, exist_ok=True)
    database = EvaluationDatabase(root / "evaluation.db")
    database.init_schema()
    repository = EvaluationRepository(database)
    service = EvaluationService(repository=repository, data_dir=root, profiles=())
    return create_app(
        repository=repository,
        service=service,
        auth_config=AuthConfig(
            passphrase=PASSPHRASE,
            allowed_hosts=frozenset({"127.0.0.1", "localhost"}),
            allowed_origins=frozenset(),
        ),
        scheduler_config=SchedulerConfig(
            data_dir=root, concurrency=1, poll_interval_s=0.05
        ),
        profiles=(),
    )


@pytest.fixture
async def client(app: Any) -> Any:
    async with TestClient(TestServer(app, host="127.0.0.1")) as test_client:
        yield test_client


async def _login(client: TestClient) -> str:
    response = await client.post(
        f"{PREFIX}/auth/login", json={"passphrase": PASSPHRASE}
    )
    assert response.status == 200
    return str((await response.json())["csrf_token"])


async def test_knowledge_read_route_requires_login(client: TestClient) -> None:
    """知识路由必须继承共享清单的准入：未登录不给任何内容。"""
    response = await client.get(f"{PREFIX}/knowledge/capabilities")
    assert response.status == 401
    assert (await response.json())["error"]["code"] == "AUTH_REQUIRED"


async def test_knowledge_route_is_reachable_and_returns_501(client: TestClient) -> None:
    """登录后能打到知识路由，且占位稳定返回 501（不是静默 200）。"""
    await _login(client)
    response = await client.get(f"{PREFIX}/knowledge/capabilities")
    assert response.status == 501
    assert (await response.json())["error"]["code"] == "KNOWLEDGE_NOT_IMPLEMENTED"


async def test_knowledge_route_inherits_405_fallback(client: TestClient) -> None:
    """未匹配方法走共享清单推出的 405 兜底（说明路由真的进了那份清单）。"""
    csrf = await _login(client)
    response = await client.put(
        f"{PREFIX}/knowledge/datasets",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status == 405
    assert (await response.json())["error"]["code"] == "METHOD_NOT_ALLOWED"


# --------------------------------------------------------------------------- config 贯通


def test_service_context_accepts_optional_config() -> None:
    """``config`` 可选且默认 None：既有调用方行为不变。"""
    import inspect

    from nanobot.evaluation.server import ServiceContext

    signature = inspect.signature(ServiceContext.__init__)
    assert "config" in signature.parameters
    assert signature.parameters["config"].default is None


def test_create_app_and_create_eval_app_accept_optional_config() -> None:
    """两个装配入口都要能透传 config，且默认 None。"""
    import inspect

    from nanobot.evaluation.server import create_app, create_eval_app

    for factory in (create_app, create_eval_app):
        parameter = inspect.signature(factory).parameters["config"]
        assert parameter.default is None, factory.__name__


def test_create_eval_app_threads_config_into_service_context(tmp_path: Any) -> None:
    """``create_eval_app(config=...)`` 之后 config 挂在 ServiceContext 上。"""
    from nanobot.evaluation.server import create_eval_app

    sentinel = object()
    app = create_eval_app(
        data_dir=tmp_path / "evaluation",
        passphrase=PASSPHRASE,
        config=sentinel,
    )
    context = app["eval_context"]
    assert context.config is sentinel


def test_create_eval_app_defaults_config_to_none(tmp_path: Any) -> None:
    """默认不传 config 时为 None——既有调用方不受影响。"""
    from nanobot.evaluation.server import create_eval_app

    app = create_eval_app(
        data_dir=tmp_path / "evaluation",
        passphrase=PASSPHRASE,
    )
    context = app["eval_context"]
    assert context.config is None
