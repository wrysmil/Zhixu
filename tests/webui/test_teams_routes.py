"""团队域 WebUI 入口：动作分发、契约错误码与路由登记。

分两层：handler 只认注入进来的 operations（内存替身即可判定错误翻译），
另有一条用真实 :class:`TeamService` + 临时 SQLite 跑通的动作，防止「只测了 helper」。
路由表与 WS mutation 白名单是契约 §4/§5 的登记点，单独钉住。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from nanobot.teams.database import TeamDatabase
from nanobot.teams.models import (
    SERVER_OWNED_DEFINITION_FIELDS,
    TeamDefinition,
    TeamValidationError,
)
from nanobot.teams.repository import (
    RunNotFound,
    TeamNotFound,
    TeamRepository,
    TeamRevisionConflict,
)
from nanobot.teams.service import (
    ProfileRecord,
    TeamArchived,
    TeamDisabled,
    TeamProfileUnavailable,
    TeamService,
)
from nanobot.webui.settings_contracts import SettingsRequest
from nanobot.webui.teams_routes import (
    TEAMS_ACTION_NAMES,
    TeamSettingsHandler,
    TeamSettingsOperations,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "teams"


def _request(payload: dict[str, Any] | None = None, query: dict[str, Any] | None = None) -> SettingsRequest:
    """QueryParams 的真形状是 ``dict[str, list[str]]``（HTTP query 可重复），装置必须照抄。"""
    return SettingsRequest(
        query={key: [str(value)] for key, value in (query or {}).items()},
        payload=payload,
    )


_DEFAULTS: dict[str, Callable[..., Any]] = {
    "list_teams": lambda **kw: {"teams": [], "nextCursor": None},
    "get_detail": lambda **kw: {"team": {}, "snapshot": {}},
    "list_revisions": lambda **kw: {"revisions": []},
    "save_team": lambda **kw: {"team": {}},
    "archive_team": lambda **kw: {"id": "t", "archived": True, "revision": 2},
    "clone_team": lambda **kw: {"team": {}},
}


class _Ops:
    """记录调用的 operations 替身：判定 handler 传了什么、抛错怎么翻。

    六个动作**都有**默认实现：没被覆盖的字段如果缺席，handler 会拿到 AttributeError，
    被兜底分支翻译成 500，测试就在验证错东西。
    """

    def __init__(self, **overrides: Callable[..., Any]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        for name, impl in {**_DEFAULTS, **overrides}.items():
            setattr(self, name, self._record(name, impl))

    def _record(self, name: str, impl: Callable[..., Any]) -> Callable[..., Any]:
        def _inner(**kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            return impl(**kwargs)

        return _inner

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def last(self) -> dict[str, Any]:
        return self.calls[-1][1]


def _ops(**overrides: Callable[..., Any]) -> _Ops:
    return _Ops(**overrides)


def _handler(**overrides: Callable[..., Any]) -> TeamSettingsHandler:
    return TeamSettingsHandler(_ops(**overrides))


def _handler(**overrides: Callable[..., Any]) -> TeamSettingsHandler:
    return TeamSettingsHandler(_ops(**overrides))


def test_action_names_are_the_frozen_contract_set() -> None:
    assert TEAMS_ACTION_NAMES == frozenset({
        "teams-list",
        "teams-detail",
        "teams-revisions",
        "teams-save",
        "teams-archive",
        "teams-clone",
    })


def test_unknown_action_is_404() -> None:
    result = TeamSettingsHandler(_ops()).handle("teams-nope", _request())
    assert result.status == 404
    assert result.error is not None and "teams-nope" in result.error


def test_reads_pass_query_parameters_through() -> None:
    """limit 是字符串查询参数：不转成 int 就会被领域层的区间校验拒掉。"""
    ops = _Ops(list_teams=lambda **kw: {"teams": [], "nextCursor": None, "echo": kw})
    echoed = TeamSettingsHandler(ops).handle(
        "teams-list", _request(query={"limit": "7", "cursor": "abc"})
    )
    assert echoed.status == 200
    assert echoed.payload is not None
    assert echoed.payload["echo"] == {"limit": 7, "cursor": "abc"}


def test_list_rejects_a_non_numeric_limit() -> None:
    assert _handler().handle("teams-list", _request(query={"limit": "many"})).status == 400


def test_list_rejects_a_bare_string_query_value() -> None:
    """``{"limit": "7"}`` 不是合法 QueryParams：取 values[0] 会得到「7」的首字符。"""
    result = TeamSettingsHandler(_ops()).handle(
        "teams-list", SettingsRequest(query={"limit": "77"}, payload=None)
    )
    assert result.status == 400


def test_detail_requires_id_and_passes_revision() -> None:
    ops = _Ops()
    missing = TeamSettingsHandler(ops).handle("teams-detail", _request(query={}))
    assert missing.status == 400
    assert ops.names() == []

    found = TeamSettingsHandler(_Ops(get_detail=lambda **kw: {"echo": kw})).handle(
        "teams-detail", _request(query={"id": "content-team", "revision": "3"})
    )
    assert found.payload is not None
    assert found.payload["echo"] == {"team_id": "content-team", "revision": 3}


def test_detail_rejects_non_numeric_revision() -> None:
    result = _handler().handle("teams-detail", _request(query={"id": "t", "revision": "nope"}))
    assert result.status == 400


@pytest.mark.parametrize(
    ("action", "payload"),
    [
        ("teams-save", {"team": {"id": "t"}, "expectedRevision": None}),
        ("teams-archive", {"id": "t", "expectedRevision": 2}),
        ("teams-clone", {"id": "t", "name": "副本"}),
    ],
)
def test_writes_require_a_request_id(action: str, payload: dict[str, Any]) -> None:
    """requestId 是幂等键，缺了就只能靠「客户端别重试」的口头承诺（契约 §2）。"""
    ops = _Ops()
    result = TeamSettingsHandler(ops).handle(action, _request(payload=payload))
    assert result.status == 400
    assert ops.names() == []


def test_save_distinguishes_create_from_update() -> None:
    ops = _Ops()
    TeamSettingsHandler(ops).handle(
        "teams-save",
        _request(payload={"requestId": "req-1", "team": {"id": "t"}, "expectedRevision": None}),
    )
    assert ops.last()["expected_revision"] is None

    TeamSettingsHandler(ops).handle(
        "teams-save",
        _request(payload={"requestId": "req-2", "team": {"id": "t"}, "expectedRevision": 4}),
    )
    assert ops.last()["expected_revision"] == 4
    assert ops.last()["request_id"] == "req-2"


def test_archive_requires_an_integer_expected_revision() -> None:
    assert _handler().handle(
        "teams-archive", _request(payload={"requestId": "r", "id": "t", "expectedRevision": "x"})
    ).status == 400


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (TeamRevisionConflict("content-team", 3), 409, "TEAM_REVISION_CONFLICT"),
        (TeamProfileUnavailable("writer unavailable"), 409, "TEAM_MEMBER_UNAVAILABLE"),
        (TeamNotFound("content-team"), 404, "TEAM_NOT_FOUND"),
        (TeamArchived("archived"), 404, "TEAM_NOT_FOUND"),
        (TeamValidationError("name is required"), 400, "TEAM_INVALID"),
        (TeamDisabled("off"), 503, "TEAMS_DISABLED"),
        (RunNotFound("run-1"), 404, "RUN_NOT_FOUND"),
    ],
)
def test_domain_errors_are_translated_to_contract_codes(
    error: Exception, status: int, code: str
) -> None:
    def _raise(**_kw: Any) -> dict[str, Any]:
        raise error

    result = _handler(list_teams=_raise).handle("teams-list", _request())
    assert result.status == status
    assert result.error is not None and result.error.startswith(code)


def test_unexpected_error_is_not_leaked_as_a_success() -> None:
    def _boom(**_kw: Any) -> dict[str, Any]:
        raise RuntimeError("内部细节不该给客户端")

    result = _handler(list_teams=_boom).handle("teams-list", _request())
    assert result.status == 500
    assert "内部细节不该给客户端" not in (result.error or "")


# ------------------------------------------------------- 真实 service 打通一条动作


class _DictDirectory:
    def __init__(self, records: dict[str, ProfileRecord]) -> None:
        self.records = records

    def get(self, agent_id: str) -> ProfileRecord | None:
        return self.records.get(agent_id)


@pytest.fixture
def team_service(tmp_path: Path) -> TeamService:
    records = {
        "leader": ProfileRecord(id="leader", name="主笔"),
        "researcher": ProfileRecord(id="researcher", name="研究"),
        "writer": ProfileRecord(id="writer", name="写作"),
    }
    db = TeamDatabase(tmp_path / "team-ws")
    db.init_schema()
    return TeamService(TeamRepository(db), _DictDirectory(records))


def _load_json(name: str) -> dict[str, Any]:
    import json

    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _load_json(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _create_payload(definition: TeamDefinition) -> dict[str, Any]:
    """创建载荷不得携带服务端所有权字段（``from_create_payload`` 会直接拒）。"""
    return {
        key: value
        for key, value in definition.to_dict().items()
        if key not in SERVER_OWNED_DEFINITION_FIELDS
    }


def test_list_and_save_run_through_the_real_service(team_service: TeamService) -> None:
    definition = TeamDefinition.from_dict(_load_json("definition.json"))
    create = _create_payload(definition)
    operations = TeamSettingsOperations(
        list_teams=lambda **kw: team_service.list_teams(**kw),
        save_team=lambda **kw: team_service.save_team(**kw),
        get_detail=lambda **kw: team_service.get_team_detail(**kw),
        archive_team=lambda **kw: team_service.archive_team(**kw),
        clone_team=lambda **kw: team_service.clone_team(**kw),
        list_revisions=lambda **kw: {
            "revisions": team_service.repository.list_revisions(kw["team_id"])
        },
    )
    handler = TeamSettingsHandler(operations)

    saved = handler.handle(
        "teams-save",
        _request(payload={"requestId": "req-1", "team": create, "expectedRevision": None}),
    )
    assert saved.status == 200
    assert saved.payload is not None and saved.payload["team"]["revision"] == 1

    listed = handler.handle("teams-list", _request(query={"limit": "20"}))
    assert listed.status == 200
    assert listed.payload is not None
    assert [item["id"] for item in listed.payload["teams"]] == [definition.id]

    # 同 requestId 重放：结果与原响应一致，且数据库里不会多出一版修订（契约 §3 / A14）。
    replayed = handler.handle(
        "teams-save",
        _request(payload={"requestId": "req-1", "team": create, "expectedRevision": None}),
    )
    assert replayed.status == 200
    assert replayed.payload == saved.payload
    revisions = handler.handle("teams-revisions", _request(query={"id": definition.id}))
    assert revisions.payload is not None
    assert [item["revision"] for item in revisions.payload["revisions"]] == [1]

    conflict = handler.handle(
        "teams-save",
        _request(payload={"requestId": "req-2", "team": create, "expectedRevision": 9}),
    )
    assert conflict.status == 409
    assert conflict.error is not None and conflict.error.startswith("TEAM_REVISION_CONFLICT")

    archived = handler.handle(
        "teams-archive",
        _request(payload={"requestId": "req-3", "id": definition.id, "expectedRevision": 1}),
    )
    assert archived.status == 200
    assert archived.payload is not None
    assert archived.payload == {"id": definition.id, "archived": True, "revision": 2}

    # 归档后按契约 §4 的 404 口径回应：不能再被当成可用团队更新。
    after_archive = handler.handle(
        "teams-save",
        _request(payload={"requestId": "req-4", "team": create, "expectedRevision": 2}),
    )
    assert after_archive.status == 404
    assert after_archive.error is not None and after_archive.error.startswith("TEAM_NOT_FOUND")


# ---------------------------------------------------- 路由与白名单登记（传输层可达）


def test_teams_paths_are_registered_in_the_settings_router() -> None:
    """动作名必须与冻结契约和 TS 客户端逐字一致：写错一个字符就是前端 404。"""
    from nanobot.webui import settings_routes

    for path, action in {
        "/api/settings/teams": "teams-list",
        "/api/settings/teams/detail": "teams-detail",
        "/api/settings/teams/revisions": "teams-revisions",
        "/api/settings/teams/save": "teams-save",
        "/api/settings/teams/archive": "teams-archive",
        "/api/settings/teams/clone": "teams-clone",
    }.items():
        assert settings_routes._SYSTEM_ROUTES[path] == action
        assert settings_routes.WebUISettingsRouter._route(path) == ("system", action)


def test_team_writes_are_ws_only() -> None:
    """契约 §3：写只能走已认证 WS。漏登记 = 免认证写 HTTP；读误登记 = 正常 GET 被 405。"""
    from nanobot.webui import settings_routes

    router = settings_routes.WebUISettingsRouter
    for path in (
        "/api/settings/teams/save",
        "/api/settings/teams/archive",
        "/api/settings/teams/clone",
    ):
        assert router.is_mutation_path(path) is True
    for path in ("/api/settings/teams", "/api/settings/teams/detail", "/api/settings/teams/revisions"):
        assert router.is_mutation_path(path) is False


def test_ws_mutation_actions_resolve_to_team_paths() -> None:
    from nanobot.webui import ws_http

    for action, path in {
        "teams.save": "/api/settings/teams/save",
        "teams.archive": "/api/settings/teams/archive",
        "teams.clone": "/api/settings/teams/clone",
    }.items():
        assert ws_http._WEBUI_MUTATION_PATHS[action] == path


# --------------------------------------------------------------- teams_api 绑定层


def test_profile_directory_maps_agent_profiles(tmp_path: Path) -> None:
    """档案目录只搬运约束，绝不带上描述、图标或任何凭据字段。"""
    from nanobot.agents.models import AgentProfile, AgentSelection
    from nanobot.agents.store import AgentStore
    from nanobot.webui.teams_api import AgentProfileDirectory

    store = AgentStore(tmp_path)
    store.save_profile(
        AgentProfile(
            id="writer",
            name="写作",
            description="不该进快照",
            prompt="按提纲成稿",
            model_id="fast",
            tools=AgentSelection(mode="include", entries=["read_file"]),
            sub_agents=AgentSelection(mode="exclude", entries=["exec"]),
        )
    )
    record = AgentProfileDirectory(store).get("writer")
    assert record is not None
    assert record.name == "写作"
    assert record.prompt == "按提纲成稿"
    assert record.model_id == "fast"
    assert record.tools is not None and record.tools.mode == "include"
    assert record.tools.entries == ["read_file"]
    assert record.sub_agents is not None and record.sub_agents.mode == "exclude"
    assert AgentProfileDirectory(store).get("ghost") is None


def test_build_operations_covers_every_team_action(team_service: TeamService) -> None:
    from nanobot.webui.teams_api import build_team_operations

    operations = build_team_operations(team_service)
    handler = TeamSettingsHandler(operations)
    for action in sorted(TEAMS_ACTION_NAMES):
        result = handler.handle(
            action,
            _request(
                payload={"requestId": "req-probe", "id": "content-team", "team": {}},
                query={"id": "content-team", "limit": "20"},
            ),
        )
        # 每个动作都必须被本域接单：领域层的失败可以是 4xx/5xx，但不能是「不认识这个动作」。
        assert not (result.error or "").startswith("unknown team action"), action
        assert not (result.error or "").startswith("unsupported team action"), action


def test_team_reference_lookup_feeds_the_agent_delete_guard(
    tmp_path: Path, team_service: TeamService
) -> None:
    """删除档案前要知道它还被哪些**未归档**团队引用（契约 §3）。"""
    from nanobot.webui.teams_api import team_reference_lookup

    definition = TeamDefinition.from_dict(_load_json("definition.json"))
    lookup = team_reference_lookup(tmp_path / "team-ws")
    assert lookup(definition.leader_agent_id) == []

    save = team_service.save_team(
        _create_payload(definition), expected_revision=None, request_id="req-refs"
    )
    assert save["team"]["id"] == definition.id
    assert set(lookup("writer")) == {definition.id}
    assert lookup("nobody") == []
