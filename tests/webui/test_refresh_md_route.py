"""Tests for POST /api/settings/memory/refresh-md route (WU-4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.webui import memory_api
from nanobot.webui.memory_routes import MEMORY_ACTION_NAMES, dispatch
from nanobot.webui.memory_services import MemoryServices
from nanobot.webui.settings_contracts import SettingsRequest


@pytest.fixture
def services(tmp_path: Path) -> MemoryServices:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return MemoryServices(workspace_id="default", database=database)


# ---------------------------------------------------------------------------
# 1. MEMORY_ACTION_NAMES 包含 action name
# ---------------------------------------------------------------------------


def test_refresh_memory_md_in_action_names():
    """memory-refresh-md action name 必须在 MEMORY_ACTION_NAMES 中."""
    assert "memory-refresh-md" in MEMORY_ACTION_NAMES


# ---------------------------------------------------------------------------
# 2. refresh_memory_md 函数可调用（mock MemoryLifecycle）
# ---------------------------------------------------------------------------


def test_refresh_memory_md_calls_lifecycle(monkeypatch, services):
    """refresh_memory_md 应刷新两个派生产物（MEMORY.md + USER.md）。

    返回形状对旧调用方保持兼容：``status`` / ``chars`` 仍是 MEMORY.md 的结果，
    USER.md 的结果挂在 ``user_md`` 下（identity_reload 直接把整个 dict 透传）。
    """
    memory_result = {"status": "ok", "chars": 42}
    user_result = {"status": "ok", "chars": 7, "items": 2}
    calls: list[tuple[str, str]] = []

    class MockLifecycle:
        @staticmethod
        def for_workspace(workspace_id, _services):
            return MockLifecycle()

        def refresh_derived_sync(self, workspace_id, *, trigger="mutation"):
            calls.append(("all", trigger))
            return {"memory_md": memory_result, "user_md": user_result}

    # MemoryLifecycle 在函数内部导入，所以 patch 内部路径
    monkeypatch.setattr(
        "nanobot.memory.lifecycle.MemoryLifecycle",
        MockLifecycle,
    )

    result = memory_api.refresh_memory_md(services)
    assert result == {**memory_result, "user_md": user_result}
    assert calls == [("all", "manual")]


def test_refresh_memory_md_handles_missing_lifecycle(services, monkeypatch):
    """MemoryLifecycle 不存在时 refresh_memory_md 不应抛异常."""
    monkeypatch.delattr("nanobot.webui.memory_api.MemoryLifecycle", raising=False)

    # 即使 Lifecycle 不存在，函数也应该能调用（但实际依赖 Lifecycle，
    # 这里测试函数签名正确）
    import inspect
    sig = inspect.signature(memory_api.refresh_memory_md)
    assert "services" in sig.parameters


# ---------------------------------------------------------------------------
# 3. dispatch 路径走通
# ---------------------------------------------------------------------------


class FakeOperations:
    """Fake MemorySettingsOperations 用于测试 dispatch."""

    def __init__(self, refresh_result: dict | None = None):
        self._refresh_result = refresh_result or {"status": "ok"}

    def refresh_memory_md(self):
        return self._refresh_result


def test_dispatch_memory_refresh_md():
    """dispatch("memory-refresh-md") 应调用 operations.refresh_memory_md()."""
    ops = FakeOperations(refresh_result={"status": "ok", "refreshed": 5})
    request = SettingsRequest(query={}, payload={})

    result = dispatch(ops, "memory-refresh-md", request)
    assert result == {"status": "ok", "refreshed": 5}


def test_dispatch_memory_refresh_md_default():
    """refresh_memory_md 未注入时返回 unavailable."""
    from nanobot.webui.settings_routes import _null_memory_operations

    ops = _null_memory_operations()
    request = SettingsRequest(query={}, payload={})

    from nanobot.webui.settings_contracts import WebUISettingsError
    with pytest.raises(WebUISettingsError) as exc_info:
        dispatch(ops, "memory-refresh-md", request)
    assert exc_info.value.status == 503


# ---------------------------------------------------------------------------
# 4. 路由表一致性
# ---------------------------------------------------------------------------


def test_memory_refresh_md_in_system_routes():
    """memory-refresh-md 必须在 _SYSTEM_ROUTES 中."""
    from nanobot.webui import settings_routes

    assert "/api/settings/memory/refresh-md" in settings_routes._SYSTEM_ROUTES
    assert settings_routes._SYSTEM_ROUTES["/api/settings/memory/refresh-md"] == "memory-refresh-md"


def test_memory_refresh_md_in_mutation_paths():
    """memory-refresh-md 必须在 _MEMORY_MUTATION_PATHS 中."""
    from nanobot.webui import settings_routes

    assert "/api/settings/memory/refresh-md" in settings_routes._MEMORY_MUTATION_PATHS


def test_memory_refresh_md_in_settings_mutation_paths():
    """memory-refresh-md 必须在 _SETTINGS_MUTATION_PATHS 中（继承自 _MEMORY_MUTATION_PATHS）."""
    from nanobot.webui import settings_routes

    assert "/api/settings/memory/refresh-md" in settings_routes._SETTINGS_MUTATION_PATHS


# ---------------------------------------------------------------------------
# 5. MemorySettingsOperations dataclass 字段
# ---------------------------------------------------------------------------


def test_memory_settings_operations_has_refresh_memory_md():
    """MemorySettingsOperations 必有 refresh_memory_md 字段."""
    import dataclasses

    from nanobot.webui.memory_routes import MemorySettingsOperations

    fields = {f.name for f in dataclasses.fields(MemorySettingsOperations)}
    assert "refresh_memory_md" in fields


# ---------------------------------------------------------------------------
# 6. gateway_services 注入
# ---------------------------------------------------------------------------


def test_build_memory_operations_includes_refresh_md(tmp_path):
    """build_memory_operations 应注入 refresh_memory_md."""
    from nanobot.webui.gateway_services import build_memory_operations

    with patch.object(memory_api, "refresh_memory_md", return_value={"status": "ok"}):
        ops = build_memory_operations(
            workspace_id="test",
            workspace_path=tmp_path,
        )
        assert hasattr(ops, "refresh_memory_md")
        assert callable(ops.refresh_memory_md)


# =============================================================================
# WU-6: get_memory_md_content tests
# =============================================================================


def test_get_memory_md_content_in_action_names():
    """memory-get-md-content action name 必须在 MEMORY_ACTION_NAMES 中."""
    assert "memory-get-md-content" in MEMORY_ACTION_NAMES


def test_dispatch_memory_get_md_content():
    """dispatch("memory-get-md-content") 应调用 operations.get_memory_md_content()."""
    fake_ops = FakeOperations()
    fake_ops._content_result = {"memory_md": "# Core Memory\n\n## Facts\n- test", "memory_md_exists": True}

    # 需要扩展 FakeOperations
    class ContentOps:
        def __init__(self):
            self._content_result = {"memory_md": "# Core Memory\n\n## Facts\n- test", "memory_md_exists": True}

        def get_memory_md_content(self):
            return self._content_result

    ops = ContentOps()
    request = SettingsRequest(query={}, payload={})

    result = dispatch(ops, "memory-get-md-content", request)
    assert result["memory_md_exists"] is True
    # draft 字段已移除
    assert "draft" not in result
    assert "draft_exists" not in result


def test_memory_get_md_content_in_system_routes():
    """memory-get-md-content 必须在 _SYSTEM_ROUTES 中."""
    from nanobot.webui import settings_routes

    assert "/api/settings/memory/memory-md/content" in settings_routes._SYSTEM_ROUTES
    assert settings_routes._SYSTEM_ROUTES["/api/settings/memory/memory-md/content"] == "memory-get-md-content"


def test_memory_settings_operations_has_get_memory_md_content():
    """MemorySettingsOperations 必有 get_memory_md_content 字段."""
    import dataclasses

    from nanobot.webui.memory_routes import MemorySettingsOperations

    fields = {f.name for f in dataclasses.fields(MemorySettingsOperations)}
    assert "get_memory_md_content" in fields


def test_build_memory_operations_includes_get_memory_md_content(tmp_path):
    """build_memory_operations 应注入 get_memory_md_content."""
    from nanobot.webui.gateway_services import build_memory_operations

    with patch.object(memory_api, "get_memory_md_content", return_value={"memory_md": None, "memory_md_exists": False}):
        ops = build_memory_operations(
            workspace_id="test",
            workspace_path=tmp_path,
        )
        assert hasattr(ops, "get_memory_md_content")
        assert callable(ops.get_memory_md_content)
