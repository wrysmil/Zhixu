"""WU-10：创作空间内置资产的归属投影与受保护写入口。

覆盖两层断言：

1. **服务端权威事实**——四类资源（Agent / Team / Skill / MCP）的管理投影统一追加
   ``ownerModule`` / ``origin`` / ``builtinVersion`` / ``deletable`` / ``protectionReason``，
   且这些字段只出现在内置资产上（普通用户资源投影必须一个都不多）。
2. **写入口拦截**——命中受保护内置资产即拒绝，message 统一为
   ``builtin resource is protected: <asset_kind>/<logical_id>``（契约 §4）。
   普通用户资源的既有语义（删除 / 覆盖 / 归档）不受影响。

数据源一律取 ``nanobot.writing.builtin``：本文件不复制第二份内置名单。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.agents.models import AgentProfile
from nanobot.agents.store import (
    AgentStore,
    AgentStoreError,
    protected_write_message,
)
from nanobot.teams.database import TeamDatabase
from nanobot.teams.models import (
    SERVER_OWNED_DEFINITION_FIELDS,
    TeamDefinition,
    TeamLimits,
    TeamMember,
)
from nanobot.teams.repository import TeamRepository
from nanobot.teams.service import ProfileRecord, TeamService
from nanobot.webui import agents_api, skills_api, teams_api
from nanobot.webui.gateway_services import build_team_stack
from nanobot.webui.mcp_presets_api import custom_mcp_action, mcp_presets_action
from nanobot.webui.skills_api import SkillManagementError
from nanobot.webui.skills_marketplace import SkillsMarketplaceError, install_marketplace_skill
from nanobot.webui.teams_routes import TeamSettingsHandler, translate
from nanobot.writing.builtin import (
    BUILTIN_RESOURCE_PROTECTED,
    ORIGIN_BUILTIN,
    OWNER_MODULE,
    WRITING_BUILTIN_ASSETS,
    WRITING_BUILTIN_VERSION,
    WRITING_TEAM_LOGICAL_ID,
    BuiltinAssetRegistry,
    BuiltinResourceProtected,
)
from nanobot.writing.database import WritingDatabase

_AGENT_LOGICAL_IDS = tuple(
    asset.logical_id for asset in WRITING_BUILTIN_ASSETS if asset.kind == "agent"
)
_SKILL_LOGICAL_IDS = tuple(
    asset.logical_id for asset in WRITING_BUILTIN_ASSETS if asset.kind == "skill"
)
_MCP_LOGICAL_IDS = tuple(
    asset.logical_id for asset in WRITING_BUILTIN_ASSETS if asset.kind == "mcp"
)

# 统一 message 口径（契约 §4）：三个受保护写入口共用同一个 helper 生成。
_UNIFORM_FIELDS = ("ownerModule", "origin", "builtinVersion", "deletable", "protectionReason")


def _builtin_message(kind: str, logical_id: str) -> str:
    return f"builtin resource is protected: {kind}/{logical_id}"


def _coded_builtin_message(kind: str, logical_id: str) -> str:
    """传输层错误体口径：业务码前置 + 契约 §4 原文（与 ``teams_routes.translate`` 同口径）。

    F4 的统一口径是「失败码放固定前缀」（agent-team 契约 §3）：三个非 teams 入口
    （agents / skills / mcp）的错误体必须与 teams 一样带上 ``BUILTIN_RESOURCE_PROTECTED``。
    """
    return f"{BUILTIN_RESOURCE_PROTECTED} {_builtin_message(kind, logical_id)}"


def _assert_shielded(payload: dict[str, Any], kind: str, logical_id: str) -> None:
    """管理投影的内置来源字段：键齐、值对、deletable 为假。"""
    assert payload["ownerModule"] == OWNER_MODULE
    assert payload["origin"] == ORIGIN_BUILTIN
    assert payload["builtinVersion"] == WRITING_BUILTIN_VERSION
    assert payload["deletable"] is False
    assert payload["protectionReason"] == _builtin_message(kind, logical_id)


# --------------------------------------------------------------- Agent 归属投影


def test_agent_projection_has_writing_builtin_fields(tmp_path: Path) -> None:
    payload = agents_api.agents_list(tmp_path)
    by_id = {item["id"]: item for item in payload["agents"]}

    assert set(_AGENT_LOGICAL_IDS) <= set(by_id), "五个写作档案必须来自 catalog 出厂预置"
    for logical_id in _AGENT_LOGICAL_IDS:
        entry = by_id[logical_id]
        _assert_shielded(entry, "agent", logical_id)
        # 既有语义保留兼容：type 仍是 system。
        assert entry["type"] == "system"


def test_agent_detail_projection_keeps_client_keys(tmp_path: Path) -> None:
    """追加字段不得挤掉前端既有键（``categoryId`` / ``subAgents`` / ``updatedAt``）。"""
    first = agents_api.agents_list(tmp_path)["agents"][0]
    assert {"id", "name", "type", "categoryId", "subAgents", "updatedAt"} <= set(first)


# --------------------------------------------------------------- Agent 写入口


def test_delete_builtin_agent_rejected_409(tmp_path: Path) -> None:
    with pytest.raises(AgentStoreError) as store_error:
        AgentStore(tmp_path).delete_profile("writing-editor")
    assert store_error.value.status == 409
    assert store_error.value.message == _builtin_message("agent", "writing-editor")

    with pytest.raises(Exception) as api_error:
        agents_api.agents_delete(tmp_path, "writing-editor")
    assert api_error.value.status == 409
    assert api_error.value.message == _coded_builtin_message("agent", "writing-editor")


def test_reset_builtin_agent_rejected(tmp_path: Path) -> None:
    with pytest.raises(AgentStoreError) as store_error:
        AgentStore(tmp_path).reset_profile("writing-architect")
    assert store_error.value.status == 409
    assert store_error.value.message == _builtin_message("agent", "writing-architect")

    with pytest.raises(Exception) as api_error:
        agents_api.agents_reset(tmp_path, "writing-architect")
    assert api_error.value.status == 409
    assert api_error.value.message == _coded_builtin_message("agent", "writing-architect")


def test_builtin_agent_cannot_be_saved_as_custom(tmp_path: Path) -> None:
    """内置定义不得被改写（含把 ``type`` 改成 ``custom``）。"""
    store = AgentStore(tmp_path)
    factory = store.get_profile("writing-author")
    hostile = AgentProfile.from_dict({**factory.to_dict(), "id": "writing-author"})
    hostile.name = "被改写的写手"
    hostile.type = "custom"
    hostile.customized = True

    with pytest.raises(AgentStoreError) as store_error:
        store.save_profile(hostile)
    assert store_error.value.status == 409
    assert store_error.value.message == _builtin_message("agent", "writing-author")

    # 磁盘上不得留下任何被改写的档案。
    assert not (tmp_path / "agents" / "profiles" / "writing-author.json").exists()

    with pytest.raises(Exception) as api_error:
        agents_api.agents_save(
            tmp_path, {**factory.to_dict(), "name": "被改写的写手", "type": "custom"}
        )
    assert api_error.value.status == 409


def test_builtin_agent_visibility_toggle_still_allowed(tmp_path: Path) -> None:
    """``hidden`` 是视图偏好而非内容定制：状态里的既有承诺不能被本 WU 顺手撤销。"""
    store = AgentStore(tmp_path)
    hidden = store.set_visibility("writing-author", True)
    assert hidden.hidden is True
    assert store.get_profile("writing-author").hidden is True


# --------------------------------------------------------------- Team 写入口


class _DictDirectory:
    """ProfileDirectory 的内存替身：每次查询现合成一条可用档案，任意 id 都不缺人。"""

    def get(self, agent_id: str) -> ProfileRecord | None:
        if agent_id.startswith(("writing-", "user-", "clone-")):
            return ProfileRecord(id=agent_id, name=agent_id)
        return None


def _team_payload(team_id: str, *, leader: str, member: str) -> dict[str, Any]:
    """创建载荷不得携带服务端所有权字段（``from_create_payload`` 会直接拒）。"""
    definition = TeamDefinition(
        id=team_id,
        revision=1,
        name="创作团队",
        description="内置团队",
        instructions="按契约 §7 执行。",
        leader_agent_id=leader,
        members=[TeamMember(member_id="member-1", agent_id=member, role="成员")],
        limits=TeamLimits(),
        archived=False,
        created_at="2026-10-17T00:00:00Z",
        updated_at="2026-10-17T00:00:00Z",
    )
    return {
        key: value
        for key, value in definition.to_dict().items()
        if key not in SERVER_OWNED_DEFINITION_FIELDS
    }


def _builtin_team_payload(team_id: str = WRITING_TEAM_LOGICAL_ID) -> dict[str, Any]:
    return _team_payload(team_id, leader="writing-editor", member="writing-author")


def _builtin_team_definition(team_id: str = WRITING_TEAM_LOGICAL_ID) -> TeamDefinition:
    """内置团队的**内容模板**：物理 id 由 registry 的 allocator 决定，模板 id 只是逻辑身份。"""
    return TeamDefinition.from_dict(
        {
            **_builtin_team_payload(team_id),
            "revision": 1,
            "createdAt": "2026-10-17T00:00:00Z",
            "updatedAt": "2026-10-17T00:00:00Z",
        }
    )


def _team_service(tmp_path: Path) -> TeamService:
    """接上真实内置注册表（``nanobot.writing.builtin`` 是唯一真相源）。"""
    db = TeamDatabase(tmp_path / "team-ws")
    db.init_schema()
    writing_db = WritingDatabase(tmp_path)
    # 写入口要读「逻辑 id → 物理 id」登记表，登记表由写作 schema 拥有（契约 §7）。
    writing_db.ensure_schema()
    return TeamService(
        TeamRepository(db),
        _DictDirectory(),
        builtin_registry=BuiltinAssetRegistry(writing_db),
    )


def _seed_builtin_team(service: TeamService) -> str:
    """内置团队的落地走不受保护的内部路径 + registry allocator（WU-06 协议）。"""
    seeded = service.ensure_builtin_team(_builtin_team_definition(), request_id="seed-builtin")
    assert seeded == WRITING_TEAM_LOGICAL_ID
    return seeded


def test_team_archive_save_guarded_and_clone_allowed(tmp_path: Path) -> None:
    service = _team_service(tmp_path)
    _seed_builtin_team(service)
    assert service.repository.get_team(WRITING_TEAM_LOGICAL_ID).revision == 1

    # 覆盖（更新修订）被拒。
    with pytest.raises(Exception) as overwrite:
        service.save_team(
            {"id": WRITING_TEAM_LOGICAL_ID, "name": "被改名的创作团队"},
            expected_revision=1,
            request_id="req-overwrite",
        )
    assert overwrite.value.status == 409
    assert overwrite.value.message == _builtin_message("team", WRITING_TEAM_LOGICAL_ID)

    # 归档被拒。领域异常 → 传输层 (状态码, 错误码) 必须落在契约 §4 的 409。
    with pytest.raises(Exception) as archived:
        service.archive_team(
            WRITING_TEAM_LOGICAL_ID, expected_revision=1, request_id="req-archive"
        )
    status, error = translate(archived.value)
    assert status == 409
    assert error.startswith(BUILTIN_RESOURCE_PROTECTED)
    assert _builtin_message("team", WRITING_TEAM_LOGICAL_ID) in error
    assert service.repository.get_team(WRITING_TEAM_LOGICAL_ID).archived is False

    # 复制为自定义：允许，且新团队 id 与内置逻辑身份无关。
    cloned = service.clone_team(
        WRITING_TEAM_LOGICAL_ID, name="我的创作团队", request_id="req-clone"
    )
    clone_id = cloned["team"]["id"]
    assert clone_id != WRITING_TEAM_LOGICAL_ID
    assert service.repository.get_team(clone_id).name == "我的创作团队"
    # 复制不得把「内置」身份带过去：副本照常可归档。
    service.archive_team(clone_id, expected_revision=1, request_id="req-archive-clone")
    assert service.repository.get_team(clone_id).archived is True


def test_builtin_team_cannot_be_created_by_a_client(tmp_path: Path) -> None:
    """客户端不得抢占内置团队的逻辑 id（避免伪造出「未登记的内置团队」）。"""
    service = _team_service(tmp_path)
    with pytest.raises(Exception) as create:
        service.save_team(_builtin_team_payload(), expected_revision=None, request_id="req-new")
    assert create.value.status == 409
    assert create.value.message == _builtin_message("team", WRITING_TEAM_LOGICAL_ID)


def test_production_wiring_always_injects_the_builtin_registry(tmp_path: Path) -> None:
    """生产装配路径必须自带内置登记处。

    这是 F1 的根因断言：`build_service` 不注入 registry 时 `_require_team_writable`
    恒为 no-op，契约 §4 的 409 永不产生。两条真实装配入口都必须注入。
    """
    service = teams_api.build_service(tmp_path)
    assert service.builtin_registry is not None

    wired, _operations, _bindings = build_team_stack(workspace_path=tmp_path, enabled=True)
    assert wired.builtin_registry is not None


def test_builtin_team_rejected_through_the_production_wiring(tmp_path: Path) -> None:
    """生产装配（``teams_api.build_service``）下，内置团队逻辑 id 不可被客户端抢占/覆盖/归档。"""
    service = teams_api.build_service(tmp_path)

    # 创建路径：客户端不得抢占内置逻辑 id（避免伪造出「未登记的内置团队」）。
    with pytest.raises(BuiltinResourceProtected) as create:
        service.save_team(
            _builtin_team_payload(), expected_revision=None, request_id="po-create"
        )
    assert create.value.status == 409
    assert create.value.code == BUILTIN_RESOURCE_PROTECTED
    assert create.value.message == _builtin_message("team", WRITING_TEAM_LOGICAL_ID)
    assert service.repository.list_teams()["teams"] == []

    # 播种内置记录（内部路径）后再试覆盖与归档：同样拒绝，且记录保持原样。
    _seed_builtin_team(service)
    with pytest.raises(BuiltinResourceProtected) as overwrite:
        service.save_team(
            {"id": WRITING_TEAM_LOGICAL_ID, "name": "ATTACKER-OWNED"},
            expected_revision=1,
            request_id="po-overwrite",
        )
    assert overwrite.value.status == 409
    assert overwrite.value.message == _builtin_message("team", WRITING_TEAM_LOGICAL_ID)

    with pytest.raises(BuiltinResourceProtected) as archive:
        service.archive_team(
            WRITING_TEAM_LOGICAL_ID, expected_revision=1, request_id="po-archive"
        )
    assert archive.value.status == 409
    assert archive.value.message == _builtin_message("team", WRITING_TEAM_LOGICAL_ID)

    definition = service.repository.get_team(WRITING_TEAM_LOGICAL_ID)
    assert definition.name == "创作团队"
    assert definition.archived is False

    # 复制为自定义放行：内置身份不得被带进副本，副本照常可归档。
    cloned = service.clone_team(
        WRITING_TEAM_LOGICAL_ID, name="我的创作团队", request_id="po-clone"
    )
    clone_id = cloned["team"]["id"]
    assert clone_id != WRITING_TEAM_LOGICAL_ID
    service.save_team(
        {**_team_payload(clone_id, leader="writing-editor", member="writing-author"),
         "name": "改名"}, expected_revision=1, request_id="po-clone-save",
    )
    service.archive_team(clone_id, expected_revision=2, request_id="po-clone-archive")


def test_builtin_team_seeding_never_overwrites_a_user_asset(tmp_path: Path) -> None:
    """allocator 协议：逻辑 id 已存在团队记录时，内置记录退让到 ``-builtin`` 后缀。

    契约 §7：``allocator(candidate)`` 返回空串表示「候选物理 id 已被用户对象占用」，
    registry 追加后缀重试，**始终保留用户对象原样**。播种本身不能走受保护的公开入口
    （会自锁），只能走 :meth:`TeamService.ensure_builtin_team`。

    冲突场景只可能来自「守卫生效之前就存在的团队记录」（老库/外部写入）：守卫接上之后
    公开路径再也造不出逻辑 id 上的用户团队。这里按仓储层直接播下那条老记录。
    """
    service = _team_service(tmp_path)
    legacy = TeamDefinition.from_dict(
        {
            **_team_payload(
                WRITING_TEAM_LOGICAL_ID, leader="user-lead", member="user-member"
            ),
            "name": "老库里的用户团队",
            "revision": 1,
            "createdAt": "2020-01-01T00:00:00Z",
            "updatedAt": "2020-01-01T00:00:00Z",
        }
    )
    service.repository.save_team(
        definition=legacy,
        snapshot=service.freeze(legacy),
        expected_revision=None,
        request_id="legacy-user-team",
    )

    seeded = service.ensure_builtin_team(_builtin_team_definition(), request_id="seed")

    assert seeded == f"{WRITING_TEAM_LOGICAL_ID}-builtin"
    # 用户对象原样保留：名字、修订号都没被播种改动。
    user_team = service.repository.get_team(WRITING_TEAM_LOGICAL_ID)
    assert user_team.name == "老库里的用户团队" and user_team.revision == 1
    assert service.repository.get_team(seeded).name == "创作团队"
    # 播种幂等：重复调用不再创建第二条记录，也不再触发 allocator。
    assert service.ensure_builtin_team(_builtin_team_definition(), request_id="seed-2") == seeded
    assert len(service.repository.list_teams()["teams"]) == 2

    # 两处都受保护：逻辑 id 与内置记录的后缀物理 id；message 统一用逻辑 id。
    for target in (WRITING_TEAM_LOGICAL_ID, seeded):
        with pytest.raises(BuiltinResourceProtected) as overwrite:
            service.save_team(
                {"id": target, "name": "ATTACKER-OWNED"}, expected_revision=1, request_id="po"
            )
        assert overwrite.value.status == 409
        assert overwrite.value.code == BUILTIN_RESOURCE_PROTECTED
        assert overwrite.value.message == _builtin_message("team", WRITING_TEAM_LOGICAL_ID)
        with pytest.raises(BuiltinResourceProtected):
            service.archive_team(target, expected_revision=1, request_id="po-archive")

    # 用户对象依旧是那一行：内容与修订号都没被任何一次拒绝路径改写。
    assert service.repository.get_team(WRITING_TEAM_LOGICAL_ID).name == "老库里的用户团队"


def test_builtin_team_seeding_is_idempotent_and_migrates_content(tmp_path: Path) -> None:
    """播种幂等；内容变了的迁移只推进一版，且走的是不受保护的内部路径。"""
    service = _team_service(tmp_path)
    assert service.ensure_builtin_team(_builtin_team_definition(), request_id="seed") == (
        WRITING_TEAM_LOGICAL_ID
    )
    before = service.repository.get_team(WRITING_TEAM_LOGICAL_ID)
    assert service.ensure_builtin_team(_builtin_team_definition(), request_id="seed-2")
    assert service.repository.get_team(WRITING_TEAM_LOGICAL_ID).revision == before.revision

    migrated = TeamDefinition.from_dict(
        {**_builtin_team_definition().to_dict(), "name": "创作团队 v2"}
    )
    assert service.ensure_builtin_team(migrated, request_id="seed-3") == (
        WRITING_TEAM_LOGICAL_ID
    )
    after = service.repository.get_team(WRITING_TEAM_LOGICAL_ID)
    assert after.name == "创作团队 v2"
    assert after.revision == before.revision + 1
    assert after.created_at == before.created_at


def test_builtin_team_seeding_requires_a_registry(tmp_path: Path) -> None:
    """没有登记处就没有内置身份可言：播种入口静默拒绝，不写任何记录。"""
    db = TeamDatabase(tmp_path / "team-ws")
    db.init_schema()
    service = TeamService(TeamRepository(db), _DictDirectory())
    assert service.ensure_builtin_team(_builtin_team_definition(), request_id="seed") is None
    assert service.repository.list_teams()["teams"] == []


# --------------------------------------------------------------- Skill 写入口


def _disable_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """内置保护必须在读 config 之前就拒绝，因此这里把 config 相关调用钉死。"""
    monkeypatch.setattr(
        skills_api,
        "load_config",
        lambda _path=None: pytest.fail("builtin protection must reject before loading config"),
    )


@pytest.mark.asyncio
async def test_skill_same_name_install_does_not_shadow_builtin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同名导入必须拒绝遮蔽内置身份，而不是回一句幂等的 already_installed。"""
    logical_id = _SKILL_LOGICAL_IDS[0]
    # 守卫若晚于子进程启动，这个替身会直接把用例打红——顺序也被钉住了。
    monkeypatch.setattr(
        "nanobot.webui.skills_marketplace.asyncio.create_subprocess_exec",
        pytest.fail,
    )

    with pytest.raises(SkillsMarketplaceError) as install_error:
        await install_marketplace_skill("acme/agent-skills", logical_id, tmp_path)
    assert install_error.value.status == 409
    assert install_error.value.message == _builtin_message("skill", logical_id)

    # 运行记录显示实际来源仍是内置，且工作区没有多出一份遮蔽副本。
    skill_dir = tmp_path / "skills" / logical_id
    assert not skill_dir.exists()
    payload = skills_api.webui_skills_payload(tmp_path)
    entry = next(item for item in payload["skills"] if item["name"] == logical_id)
    assert entry["source"] == "builtin"
    _assert_shielded(entry, "skill", logical_id)


def test_skill_delete_and_update_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_config(monkeypatch)
    logical_id = _SKILL_LOGICAL_IDS[0]

    with pytest.raises(SkillManagementError) as delete_error:
        skills_api.delete_webui_skill(tmp_path, logical_id, disabled_skills=set())
    assert delete_error.value.status == 409
    assert delete_error.value.message == _coded_builtin_message("skill", logical_id)

    with pytest.raises(SkillManagementError) as update_error:
        skills_api.set_webui_skill_enabled(
            tmp_path, logical_id, enabled=False, disabled_skills=set()
        )
    assert update_error.value.status == 409
    assert update_error.value.message == _coded_builtin_message("skill", logical_id)


def test_skill_projection_shields_every_writing_builtin(tmp_path: Path) -> None:
    payload = skills_api.webui_skills_payload(tmp_path)
    by_name = {item["name"]: item for item in payload["skills"]}
    assert set(_SKILL_LOGICAL_IDS) <= set(by_name), "写作 Skill 必须随包内置"
    for logical_id in _SKILL_LOGICAL_IDS:
        _assert_shielded(by_name[logical_id], "skill", logical_id)
        assert by_name[logical_id]["source"] == "builtin"


# --------------------------------------------------------------- MCP 写入口


def test_mcp_guards_skipped_when_no_builtin_preset() -> None:
    """首批未提供内置 MCP 预设（契约 §7）：无对象可保护，用例显式 skip 并说明。"""
    if not _MCP_LOGICAL_IDS:
        pytest.skip(
            "契约 §7 首批不提供内置 MCP 预设（WRITING_BUILTIN_ASSETS 无 kind='mcp' 条目），"
            "remove/import/enable 三处守卫暂无可命中的对象；已在代码中按 kind='mcp' 派生接入"
            "（_builtin_mcp_logical_ids），首批条目出现即自动生效，无需本用例改动。"
        )
    # 一旦首批提供了内置预设，本用例转为实跑：任意内置 MCP 名字都必须被拒绝。
    for logical_id in _MCP_LOGICAL_IDS:
        assert isinstance(logical_id, str) and logical_id


def test_mcp_import_and_enable_derived_from_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """守卫集合必须从 ``nanobot.writing.builtin`` 派生，而不是另写一份硬编码名单。"""
    from nanobot.webui import mcp_presets_api

    assert hasattr(mcp_presets_api, "_builtin_mcp_logical_ids")
    assert set(mcp_presets_api._builtin_mcp_logical_ids()) == set(_MCP_LOGICAL_IDS)

    # 注入一个假的内置 MCP 条目：remove / import / enable 三处守卫必须同时生效。
    # 清单是唯一真相源（``BuiltinAssetRegistry.get_asset`` 查的也是这张表），
    # 因此registry 与 ``_builtin_mcp_logical_ids()`` 会同时看到它。
    from nanobot.writing import builtin as writing_builtin

    fake = writing_builtin.BuiltinAsset("mcp", "writing-fake", "假内置 MCP")
    monkeypatch.setattr(
        writing_builtin,
        "_ASSETS_BY_KEY",
        {**writing_builtin._ASSETS_BY_KEY, ("mcp", fake.logical_id): fake},
    )
    assert set(mcp_presets_api._builtin_mcp_logical_ids()) == {"writing-fake"}
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"agents": {"defaults": {"workspace": str(tmp_path / "workspace")}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    query: dict[str, list[str]] = {"name": ["writing-fake"]}
    for action in ("remove", "enable"):
        with pytest.raises(Exception) as excinfo:
            mcp_presets_action(action, query, config_path=config_path)
        assert excinfo.value.message == _coded_builtin_message("mcp", "writing-fake")

    with pytest.raises(Exception) as import_error:
        custom_mcp_action(
            "import",
            {
                "config": [
                    json.dumps(
                        {"mcpServers": {"writing-fake": {"type": "stdio", "command": "echo"}}}
                    )
                ]
            },
            config_path=config_path,
        )
    assert import_error.value.message == _coded_builtin_message("mcp", "writing-fake")


# --------------------------------------------------------------- 普通资源不回归


def test_user_assets_unaffected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """普通用户资源删除 / 覆盖 / 归档语义原样，且投影不得多出内置字段。"""
    # Agent：保存、列表投影、删除三步都不受影响。
    saved = agents_api.agents_save(
        tmp_path, {"id": "my-writer", "name": "我的写手", "prompt": "写吧"}
    )["agent"]
    assert set(_UNIFORM_FIELDS).isdisjoint(saved)
    listed = {item["id"]: item for item in agents_api.agents_list(tmp_path)["agents"]}
    assert set(_UNIFORM_FIELDS).isdisjoint(listed["my-writer"])
    assert listed["my-writer"]["type"] == "custom"
    assert agents_api.agents_delete(tmp_path, "my-writer") == {"id": "my-writer"}
    with pytest.raises(AgentStoreError) as gone:
        AgentStore(tmp_path).get_profile("my-writer")
    assert gone.value.status == 404

    # 既有系统 Agent（非创作内置）语义不变：可保存、可重置、删除仍是 409。
    store = AgentStore(tmp_path)
    store.save_profile(AgentProfile(id="code-reviewer", name="代码审查", prompt="看代码"))
    assert store.get_profile("code-reviewer").customized is True
    assert store.reset_profile("code-reviewer").customized is False
    with pytest.raises(AgentStoreError) as system_delete:
        store.delete_profile("code-reviewer")
    assert system_delete.value.status == 409

    # Team：普通团队可保存、可归档、可覆盖 —— 即使接上了内置注册表也一样。
    service = _team_service(tmp_path)
    service.save_team(
        _team_payload("user-team", leader="user-lead", member="user-member"),
        expected_revision=None,
        request_id="u1",
    )
    service.save_team(
        {
            **_team_payload("user-team", leader="user-lead", member="user-member"),
            "name": "改过的名字",
        },
        expected_revision=1,
        request_id="u2",
    )
    assert service.repository.get_team("user-team").name == "改过的名字"
    assert service.archive_team("user-team", expected_revision=2, request_id="u3")["archived"]

    # Skill：工作区技能照常可删、可禁用；投影无内置字段。
    skill_dir = tmp_path / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: mine.\n---\n", encoding="utf-8"
    )
    mine = next(
        item
        for item in skills_api.webui_skills_payload(tmp_path)["skills"]
        if item["name"] == "my-skill"
    )
    # Skill 投影的既有键（``deletable``）之外，不得出现本 WU 新增的归属字段。
    assert {"ownerModule", "origin", "builtinVersion", "protectionReason"}.isdisjoint(mine)
    assert mine["deletable"] is True
    assert skills_api.delete_webui_skill(tmp_path, "my-skill", disabled_skills=set())["deleted"]
    assert not skill_dir.exists()

    # MCP：自定义 server 照常可创建、可删除。
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"agents": {"defaults": {"workspace": str(tmp_path / "workspace")}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    custom_mcp_action(
        "custom",
        {"name": ["my-server"], "type": ["stdio"], "command": ["echo"]},
        config_path=config_path,
    )
    mcp_presets_action("remove", {"name": ["my-server"]}, config_path=config_path)


# --------------------------------------------------------------- 业务码一致性


def test_protected_code_is_uniform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """统一业务码 ``BUILTIN_RESOURCE_PROTECTED`` 与统一 message 必须覆盖全部入口。

    业务码取自**异常对象自己携带的** ``.code``（F4）：不再由本用例硬编码，
    因此「异常没带码」会直接让这条用例变红，而不是被断言掩盖过去。
    """
    _disable_config(monkeypatch)
    logical_skill = _SKILL_LOGICAL_IDS[0]

    observed: list[tuple[int, str, str, str, str, bool]] = []

    with pytest.raises(AgentStoreError) as agent_delete:
        AgentStore(tmp_path).delete_profile(_AGENT_LOGICAL_IDS[0])
    observed.append(
        (
            agent_delete.value.status,
            agent_delete.value.code,
            agent_delete.value.message,
            "agent",
            _AGENT_LOGICAL_IDS[0],
            False,  # 领域层异常：message 是契约 §4 原文，码只走 .code
        )
    )

    with pytest.raises(AgentStoreError) as agent_reset:
        AgentStore(tmp_path).reset_profile(_AGENT_LOGICAL_IDS[0])
    observed.append(
        (
            agent_reset.value.status,
            agent_reset.value.code,
            agent_reset.value.message,
            "agent",
            _AGENT_LOGICAL_IDS[0],
            False,
        )
    )

    with pytest.raises(SkillManagementError) as skill_delete:
        skills_api.delete_webui_skill(tmp_path, logical_skill, disabled_skills=set())
    observed.append(
        (
            skill_delete.value.status,
            skill_delete.value.code,
            skill_delete.value.message,
            "skill",
            logical_skill,
            True,  # 传输层错误对象：错误体就是 message，必须带码前缀
        )
    )

    service = _team_service(tmp_path)
    _seed_builtin_team(service)
    with pytest.raises(Exception) as team_archive:
        service.archive_team(
            WRITING_TEAM_LOGICAL_ID, expected_revision=1, request_id="c2"
        )
    assert isinstance(team_archive.value, BuiltinResourceProtected)
    observed.append(
        (
            team_archive.value.status,
            team_archive.value.code,
            team_archive.value.message,
            "team",
            WRITING_TEAM_LOGICAL_ID,
            False,
        )
    )

    for status, code, message, kind, logical_id, coded in observed:
        # 业务码一律由异常对象自己携带（F4）：本用例不硬编码任何一个入口的码。
        assert code == BUILTIN_RESOURCE_PROTECTED
        assert status == 409
        # 契约 §4 原文在四个域逐字统一，只有 <asset_kind>/<logical_id> 不同。
        assert protected_write_message(kind, logical_id) in message
        if coded:
            # 传输层口径与 teams 一致（agent-team 契约 §3「失败码放固定前缀」）。
            assert message == _coded_builtin_message(kind, logical_id)

    assert len({message.split(": ", 1)[1] for _, _, message, _, _, _ in observed}) == 3


def test_handler_translates_builtin_protection_to_409() -> None:
    """handler 的失败信封必须带 409 与业务码前缀（契约 §4）。"""
    from nanobot.writing.builtin import BuiltinResourceProtected

    handler = TeamSettingsHandler.__new__(TeamSettingsHandler)
    assert handler is not None
    status, error = translate(BuiltinResourceProtected("team", WRITING_TEAM_LOGICAL_ID))
    assert status == 409
    assert error.startswith(BUILTIN_RESOURCE_PROTECTED)
    assert _builtin_message("team", WRITING_TEAM_LOGICAL_ID) in error
