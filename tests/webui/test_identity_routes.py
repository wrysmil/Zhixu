"""Integration tests for the identity settings domain handler.

Exercises ``IdentitySettingsHandler.handle`` with real operations backed by a
temporary workspace, plus the two behaviours the handler must get right and
nothing else can prove:

* MEMORY.md never reaches ``IdentityStore`` (the lifecycle owns it), and
* ``IdentityStoreError`` keeps its status through the error translation.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from nanobot.identity.catalog import CHAR_LIMIT, IDENTITY_DIR_NAME, char_limit_for
from nanobot.identity.store import IdentityStore, IdentityStoreError
from nanobot.webui import identity_api
from nanobot.webui.identity_routes import (
    IDENTITY_ACTION_NAMES,
    IdentitySettingsHandler,
    IdentitySettingsOperations,
)
from nanobot.webui.settings_contracts import (
    SettingsRequest,
    SettingsRouteResult,
    WebUISettingsError,
)


class FakeLifecycle:
    """Records manual MEMORY.md writes; the real one backs up before writing."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def write_memory_md(self, content: str) -> None:
        self.written.append(content)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    identity_dir = tmp_path / IDENTITY_DIR_NAME
    (identity_dir / "prompts").mkdir(parents=True)
    (identity_dir / "personas").mkdir()
    (identity_dir / "SOUL.md").write_text("# soul", encoding="utf-8")
    (identity_dir / "prompts" / "policies.md").write_text("# policies", encoding="utf-8")
    (identity_dir / "personas" / "default.md").write_text("# default", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def lifecycle() -> FakeLifecycle:
    return FakeLifecycle()


@pytest.fixture()
def operations(workspace: Path, lifecycle: FakeLifecycle) -> IdentitySettingsOperations:
    return IdentitySettingsOperations(
        list_files=partial(identity_api.identity_list_files, workspace),
        read_file=partial(identity_api.identity_read_file, workspace),
        write_file=partial(identity_api.identity_write_file, workspace, lifecycle=lifecycle),
        reload=partial(identity_api.identity_reload),
        list_presets=identity_api.identity_list_presets,
    )


@pytest.fixture()
def handler(operations: IdentitySettingsOperations) -> IdentitySettingsHandler:
    return IdentitySettingsHandler(operations)


def _request(
    query: Mapping[str, list[str]] | None = None,
    payload: dict[str, Any] | None = None,
) -> SettingsRequest:
    return SettingsRequest(query=dict(query or {}), payload=payload)


# ---- dispatch wiring --------------------------------------------------------


def test_all_actions_are_registered():
    """编译接口已下架：保存即编译，没有手动 compile action。"""
    assert IDENTITY_ACTION_NAMES == frozenset({
        "identity-list-files",
        "identity-read-file",
        "identity-write-file",
        "identity-reload",
        "identity-list-presets",
        "identity-get-active-persona",
        "identity-set-active-persona",
    })


def test_every_known_action_dispatches(handler: IdentitySettingsHandler, lifecycle: FakeLifecycle):
    """每个 action 都能落到自己的分支，不会掉进末尾的 guard。"""
    results = [
        handler.handle("identity-list-files", _request()),
        handler.handle("identity-read-file", _request(query={"name": ["SOUL.md"]})),
        handler.handle(
            "identity-write-file",
            _request(payload={"name": "SOUL.md", "content": "# new"}),
        ),
        handler.handle("identity-reload", _request()),
        handler.handle("identity-list-presets", _request()),
    ]
    for result in results:
        assert isinstance(result, SettingsRouteResult)
        assert result.error is None, result.error
        assert result.status == 200
        assert result.payload is not None


def test_unknown_action_returns_404(handler: IdentitySettingsHandler):
    result = handler.handle("identity-frobnicate", _request())
    assert result.status == 404
    assert result.error
    assert result.payload is None


# ---- list ------------------------------------------------------------------


def test_list_files_payload_shape(handler: IdentitySettingsHandler):
    result = handler.handle("identity-list-files", _request())

    assert result.error is None
    assert set(result.payload) == {"files", "charLimit", "compile"}
    assert result.payload["charLimit"] == CHAR_LIMIT
    assert result.payload["compile"]["fresh"] is False  # never compiled in this fixture

    files = result.payload["files"]
    assert isinstance(files, list)
    by_name = {f["name"]: f for f in files}
    assert by_name["SOUL.md"]["exists"] is True
    assert by_name["AGENT.md"]["exists"] is False
    assert by_name["SOUL.md"]["restricted"] is False
    assert by_name["AGENT.md"]["restricted"] is True
    assert all(f["charLimit"] == char_limit_for(f["name"]) for f in files)
    # personas are discovered from disk, not hard-coded
    assert by_name["default.md"]["group"] == "personas"


def test_list_files_without_identity_dir(tmp_path: Path):
    """「还没建 identity/」是合法状态，清单也要能出得来。"""
    operations = IdentitySettingsOperations(
        list_files=partial(identity_api.identity_list_files, tmp_path),
        read_file=partial(identity_api.identity_read_file, tmp_path),
        write_file=partial(identity_api.identity_write_file, tmp_path),
        reload=partial(identity_api.identity_reload),
        list_presets=identity_api.identity_list_presets,
    )
    result = IdentitySettingsHandler(operations).handle("identity-list-files", _request())

    assert result.error is None
    assert {f["group"] for f in result.payload["files"]} == {"core"}


# ---- read ------------------------------------------------------------------


def test_read_file_returns_content(handler: IdentitySettingsHandler):
    result = handler.handle("identity-read-file", _request(query={"name": ["SOUL.md"]}))

    assert result.error is None
    assert result.payload == {
        "name": "SOUL.md",
        "content": "# soul",
        "exists": True,
        "fromTemplate": False,
    }


def test_read_file_accepts_nested_whitelisted_path(handler: IdentitySettingsHandler):
    result = handler.handle(
        "identity-read-file", _request(query={"name": ["prompts/policies.md"]})
    )
    assert result.payload["content"] == "# policies"


@pytest.mark.parametrize("query", [{}, {"name": [""]}, {"name": ["   "]}])
def test_read_file_missing_name_is_4xx(handler: IdentitySettingsHandler, query):
    result = handler.handle("identity-read-file", _request(query=query))
    assert 400 <= result.status < 500
    assert result.error


def test_read_missing_file_returns_factory_template(handler: IdentitySettingsHandler):
    """缺失不再 404：返回出厂模板 + fromTemplate，前端据此预填。"""
    result = handler.handle("identity-read-file", _request(query={"name": ["USER.md"]}))

    assert result.status == 200
    assert result.error is None
    assert result.payload["exists"] is False
    assert result.payload["fromTemplate"] is True
    assert "User Profile" in result.payload["content"]


def test_read_missing_core_file_with_no_template_is_empty_not_404(
    handler: IdentitySettingsHandler,
):
    # AGENT.md 有出厂模板；用一个白名单内但无模板的场景不可造（core 全有模板）。
    # 非白名单仍 403，见下。
    result = handler.handle("identity-read-file", _request(query={"name": ["AGENT.md"]}))
    assert result.status == 200
    assert result.payload["fromTemplate"] is True


def test_read_non_whitelisted_is_403(handler: IdentitySettingsHandler):
    result = handler.handle("identity-read-file", _request(query={"name": ["id_rsa"]}))
    assert result.status == 403


def test_list_presets_returns_five_with_content(handler: IdentitySettingsHandler):
    result = handler.handle("identity-list-presets", _request())

    assert result.error is None
    presets = result.payload["presets"]
    assert [p["name"] for p in presets] == [
        "balanced",
        "mentor",
        "creative",
        "companion",
        "tech_expert",
    ]
    # 标题是用户可编辑的：钉具体文字（当年的 "# Soul"）是把可编辑内容当常量断言。
    assert all(p["content"].startswith("# ") for p in presets)
    assert all(p["labelKey"].startswith("settings.identity.preset.") for p in presets)


# ---- write: MEMORY.md against the real MemoryLifecycle ----------------------


@pytest.fixture()
def real_lifecycle(tmp_path: Path):
    """真实的 MemoryLifecycle，用于验证 write_memory_md 的备份/上限语义。"""
    from nanobot.memory.database import MemoryDatabase
    from nanobot.memory.lifecycle import MemoryLifecycle
    from nanobot.webui.memory_services import MemoryServices

    database = MemoryDatabase(tmp_path)
    database.init_schema()
    services = MemoryServices(workspace_id="default", database=database)
    return MemoryLifecycle("default", services)


def test_memory_md_write_through_real_lifecycle_creates_backup(
    workspace: Path, real_lifecycle
):
    """端到端：identity_write_file -> core-note 记录 + 导出文件（.bak 语义保留）。"""
    from nanobot.memory.lifecycle import read_core_note

    result = identity_api.identity_write_file(
        workspace, "MEMORY.md", "# 手工记忆", lifecycle=real_lifecycle
    )

    assert result == {"name": "MEMORY.md", "saved": True, "autoRegenerated": True}
    with real_lifecycle.services.database.connect() as conn:
        note = read_core_note(conn, "default")
    assert note is not None and "手工记忆" in note.content

    memory_file = real_lifecycle.memory_file
    content = memory_file.read_text(encoding="utf-8")
    assert "zhixu:core-note:begin" in content
    assert "手工记忆" in content
    # 第一次写入前没有旧文件，因此没有备份
    assert not memory_file.with_suffix(".md.bak").exists()

    identity_api.identity_write_file(
        workspace, "MEMORY.md", "# 第二版", lifecycle=real_lifecycle
    )
    assert "第二版" in memory_file.read_text(encoding="utf-8")
    backup = memory_file.with_suffix(".md.bak").read_text(encoding="utf-8")
    assert "手工记忆" in backup
    assert "第二版" not in backup


def test_memory_md_over_limit_through_real_lifecycle_is_4xx(workspace: Path, real_lifecycle):
    from nanobot.memory.lifecycle import MEMORY_MD_MAX_CHARS

    operations = IdentitySettingsOperations(
        list_files=partial(identity_api.identity_list_files, workspace),
        read_file=partial(identity_api.identity_read_file, workspace),
        write_file=partial(
            identity_api.identity_write_file, workspace, lifecycle=real_lifecycle
        ),
        reload=partial(identity_api.identity_reload),
        list_presets=identity_api.identity_list_presets,
    )
    result = IdentitySettingsHandler(operations).handle(
        "identity-write-file",
        _request(payload={"name": "MEMORY.md", "content": "x" * (MEMORY_MD_MAX_CHARS + 1)}),
    )

    assert result.status == 400
    assert not real_lifecycle.memory_file.exists()


# ---- write: MEMORY.md split -------------------------------------------------
def test_write_memory_md_never_reaches_store(
    handler: IdentitySettingsHandler,
    lifecycle: FakeLifecycle,
    monkeypatch: pytest.MonkeyPatch,
):
    """最关键的一条：MEMORY.md 必须走 lifecycle，绝不能碰 store。"""

    def _must_not_be_called(self: IdentityStore, name: str, content: str) -> None:
        raise AssertionError("MEMORY.md 不允许经过 IdentityStore.write_file")

    monkeypatch.setattr(IdentityStore, "write_file", _must_not_be_called)

    result = handler.handle(
        "identity-write-file",
        _request(payload={"name": "MEMORY.md", "content": "# 手工记忆"}),
    )

    assert result.error is None
    assert result.payload == {"name": "MEMORY.md", "saved": True, "autoRegenerated": True}
    assert lifecycle.written == ["# 手工记忆"]


def test_write_memory_md_without_lifecycle_is_500(workspace: Path):
    operations = IdentitySettingsOperations(
        list_files=partial(identity_api.identity_list_files, workspace),
        read_file=partial(identity_api.identity_read_file, workspace),
        write_file=partial(identity_api.identity_write_file, workspace),  # lifecycle=None
        reload=partial(identity_api.identity_reload),
        list_presets=identity_api.identity_list_presets,
    )
    result = IdentitySettingsHandler(operations).handle(
        "identity-write-file",
        _request(payload={"name": "MEMORY.md", "content": "# x"}),
    )

    assert result.status == 500
    assert result.error
    assert not (workspace / IDENTITY_DIR_NAME / "MEMORY.md").exists()


# ---- write: store branch ----------------------------------------------------


def test_write_regular_file_goes_to_store(handler: IdentitySettingsHandler, workspace: Path):
    result = handler.handle(
        "identity-write-file",
        _request(payload={"name": "SOUL.md", "content": "# updated"}),
    )

    assert result.error is None
    assert result.payload == {"name": "SOUL.md", "saved": True}
    assert "autoRegenerated" not in result.payload
    assert (workspace / IDENTITY_DIR_NAME / "SOUL.md").read_text(encoding="utf-8") == "# updated"


def test_write_empty_content_is_allowed(handler: IdentitySettingsHandler, workspace: Path):
    """清空文件是合法编辑；不能把空串当成「缺 content」。"""
    result = handler.handle(
        "identity-write-file",
        _request(payload={"name": "SOUL.md", "content": ""}),
    )

    assert result.error is None
    assert (workspace / IDENTITY_DIR_NAME / "SOUL.md").read_text(encoding="utf-8") == ""


def test_write_creates_missing_whitelisted_file(
    handler: IdentitySettingsHandler, workspace: Path
):
    result = handler.handle(
        "identity-write-file",
        _request(payload={"name": "prompts/policies.md", "content": "# new policies"}),
    )

    assert result.error is None
    assert (
        workspace / IDENTITY_DIR_NAME / "prompts" / "policies.md"
    ).read_text(encoding="utf-8") == "# new policies"


# ---- write: 保存即编译 ------------------------------------------------------


def test_write_soul_triggers_auto_compile(handler: IdentitySettingsHandler, workspace: Path):
    """保存编译源文件后，runtime 产物应新鲜，无需任何手动编译动作。"""
    from nanobot.identity.compiler import compiled_status

    assert compiled_status(workspace)["compiled"] is False  # 前置：从未编译

    result = handler.handle(
        "identity-write-file",
        _request(payload={"name": "SOUL.md", "content": "# soul\n\n核心原则：诚实。"}),
    )
    assert result.status == 200

    status = compiled_status(workspace)
    assert status["compiled"] is True
    assert status["fresh"] is True


def test_write_memory_does_not_compile(handler: IdentitySettingsHandler, workspace: Path):
    """MEMORY.md 走 lifecycle 分支，不是编译源，保存不该触发编译。"""
    from nanobot.identity.compiler import compiled_status

    handler.handle(
        "identity-write-file",
        _request(payload={"name": "MEMORY.md", "content": "derived"}),
    )
    assert compiled_status(workspace)["compiled"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"content": "# x"},  # 缺 name
        {"name": "SOUL.md"},  # 缺 content
        {"name": "SOUL.md", "content": 123},  # content 非字符串
    ],
)
def test_write_rejects_incomplete_payload(handler: IdentitySettingsHandler, payload):
    result = handler.handle("identity-write-file", _request(payload=payload))
    assert 400 <= result.status < 500
    assert result.error


def test_write_over_char_limit_is_4xx(handler: IdentitySettingsHandler, workspace: Path):
    result = handler.handle(
        "identity-write-file",
        _request(
            payload={
                "name": "prompts/policies.md",
                "content": "x" * (CHAR_LIMIT + 1),
            }
        ),
    )

    assert result.status == 400
    # 被拒的写入不能破坏原文件
    policies = workspace / IDENTITY_DIR_NAME / "prompts" / "policies.md"
    assert policies.read_text(encoding="utf-8") == "# policies"


def test_write_soul_over_its_own_limit_is_4xx(
    handler: IdentitySettingsHandler, workspace: Path
):
    """SOUL.md 用的是自己的上限，不是默认上限。"""
    result = handler.handle(
        "identity-write-file",
        _request(
            payload={
                "name": "SOUL.md",
                "content": "x" * (char_limit_for("SOUL.md") + 1),
            }
        ),
    )

    assert result.status == 400
    assert (workspace / IDENTITY_DIR_NAME / "SOUL.md").read_text(encoding="utf-8") == "# soul"


# ---- error translation ------------------------------------------------------


def test_identity_store_status_is_preserved(monkeypatch: pytest.MonkeyPatch, workspace: Path):
    """IdentityStoreError.status → WebUISettingsError.status（404 例外：读兜底见上）。"""
    for status in (400, 403, 500):
        def _boom(self: IdentityStore, name: str, _status: int = status) -> str:
            raise IdentityStoreError(f"boom-{_status}", status=_status)

        monkeypatch.setattr(IdentityStore, "read_file", _boom)
        with pytest.raises(WebUISettingsError) as exc:
            identity_api.identity_read_file(workspace, "SOUL.md")
        assert exc.value.status == status
        assert exc.value.message == f"boom-{status}"


def test_identity_store_404_becomes_template_not_error(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
):
    """store 404 被 API 层吞掉换成出厂模板——错误横幅不该再出现。"""

    def _missing(self: IdentityStore, name: str) -> str:
        raise IdentityStoreError(f"文件不存在：{name}", status=404)

    monkeypatch.setattr(IdentityStore, "read_file", _missing)
    payload = identity_api.identity_read_file(workspace, "USER.md")
    assert payload["exists"] is False
    assert payload["fromTemplate"] is True
    assert payload["content"]


def test_identity_settings_error_is_a_value_error():
    """下游若只 catch ValueError 也要能接住。"""
    assert issubclass(WebUISettingsError, ValueError)


# ---- path traversal ---------------------------------------------------------


@pytest.mark.parametrize(
    "evil",
    [
        "../etc/passwd",
        "..\\..\\windows\\system32\\cmd.exe",
        "/etc/passwd",
        "C:\\Windows\\System32\\drivers\\etc\\hosts",
        "prompts/../SOUL.md",
        "personas/../SOUL.md",
        "AGENTS.md",
        ".env",
        "",
    ],
)
def test_read_traversal_rejected_by_api(workspace: Path, evil: str):
    with pytest.raises(WebUISettingsError) as exc:
        identity_api.identity_read_file(workspace, evil)
    assert 400 <= exc.value.status < 500


@pytest.mark.parametrize("evil", ["../etc/passwd", "prompts/../../x.md", "id_rsa", ""])
def test_write_traversal_rejected_by_api(workspace: Path, evil: str):
    with pytest.raises(WebUISettingsError):
        identity_api.identity_write_file(workspace, evil, "# pwned")


def test_read_traversal_is_4xx_through_handler(handler: IdentitySettingsHandler):
    result = handler.handle(
        "identity-read-file", _request(query={"name": ["../etc/passwd"]})
    )
    assert 400 <= result.status < 500
    assert result.error


def test_write_traversal_leaves_no_file_behind(handler: IdentitySettingsHandler, tmp_path: Path):
    """拒绝必须是「没写」，不能是「写完了才报错」。"""
    result = handler.handle(
        "identity-write-file",
        _request(payload={"name": "../marker.md", "content": "# pwned"}),
    )

    assert 400 <= result.status < 500
    assert not (tmp_path / "marker.md").exists()
    assert not (tmp_path.parent / "marker.md").exists()


def test_lowercase_memory_md_variants_do_not_hit_lifecycle(
    handler: IdentitySettingsHandler, lifecycle: FakeLifecycle, workspace: Path
):
    """大小写变体不能误入 lifecycle 分流；它们被 store 白名单拒掉。"""
    for name in ("memory.md", "memory.MD", "MEMORY.markdown"):
        result = handler.handle(
            "identity-write-file", _request(payload={"name": name, "content": "# x"})
        )
        assert result.error is not None, name

    assert lifecycle.written == []
    assert not (workspace / IDENTITY_DIR_NAME / "MEMORY.md").exists()


def test_memory_md_name_is_whitespace_insensitive(
    handler: IdentitySettingsHandler, lifecycle: FakeLifecycle
):
    """名字两端空白被规范化后仍走 lifecycle，不能漏到 store 去吃 500。"""
    result = handler.handle(
        "identity-write-file",
        _request(payload={"name": "  MEMORY.md ", "content": "# x"}),
    )

    assert result.error is None
    assert result.payload == {"name": "MEMORY.md", "saved": True, "autoRegenerated": True}
    assert lifecycle.written == ["# x"]


# ---- reload ----------------------------------------------------------------


def test_reload_without_services_is_skipped(handler: IdentitySettingsHandler):
    result = handler.handle("identity-reload", _request())

    assert result.error is None
    assert result.payload == {"status": "skipped", "reason": "no_services"}


def test_reload_invokes_refresh_memory_md(workspace: Path):
    calls: list[int] = []

    def _refresh() -> dict[str, Any]:
        calls.append(1)
        return {"status": "ok", "chars": 42}

    operations = IdentitySettingsOperations(
        list_files=partial(identity_api.identity_list_files, workspace),
        read_file=partial(identity_api.identity_read_file, workspace),
        write_file=partial(identity_api.identity_write_file, workspace),
        reload=partial(identity_api.identity_reload, refresh_memory_md=_refresh),
        list_presets=identity_api.identity_list_presets,
    )
    result = IdentitySettingsHandler(operations).handle("identity-reload", _request())

    assert calls == [1]
    assert result.payload == {"status": "ok", "chars": 42}


def test_reload_failure_is_reported_not_raised(workspace: Path):
    """重载失败不该把设置页打成 500。"""

    def _boom() -> dict[str, Any]:
        raise RuntimeError("db down")

    operations = IdentitySettingsOperations(
        list_files=partial(identity_api.identity_list_files, workspace),
        read_file=partial(identity_api.identity_read_file, workspace),
        write_file=partial(identity_api.identity_write_file, workspace),
        reload=partial(identity_api.identity_reload, refresh_memory_md=_boom),
        list_presets=identity_api.identity_list_presets,
    )
    result = IdentitySettingsHandler(operations).handle("identity-reload", _request())

    assert result.error is None
    assert result.payload == {"status": "error"}
