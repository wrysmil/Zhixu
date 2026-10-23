"""WU-03：创作内置资产清单、幂等 preparation 与受保护写判定。

契约依据：L4 契约 §2（`writing_builtin_assets` DDL）、§4（`BUILTIN_RESOURCE_PROTECTED`）、
§7（内置资产清单与统一投影字段）；plan Task 3 冻结了 `BuiltinAssetRegistry` 的四方法签名。

allocator 协议（plan 未冻结语义，本文件与 `nanobot/writing/builtin.py` 的 docstring 一致）：
``allocator(candidate) -> str`` 以 candidate 为首选创建内置记录并返回其实物理 ID；
candidate 被**用户对象**占住时返回空串表示拒绝占用。协调器收到空串即追加后缀重试。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nanobot.writing.builtin import (
    BUILTIN_RESOURCE_PROTECTED,
    WRITING_BUILTIN_ASSETS,
    WRITING_BUILTIN_VERSION,
    WRITING_TEAM_LOGICAL_ID,
    BuiltinAsset,
    BuiltinAssetRegistry,
    BuiltinResourceProtected,
)
from nanobot.writing.database import SCHEMA_VERSION, WritingDatabase, compute_workspace_key

_EXPECTED_AGENTS = (
    "writing-editor",
    "writing-researcher",
    "writing-architect",
    "writing-author",
    "writing-reviewer",
)
_EXPECTED_SKILLS = (
    "article-brief",
    "article-research",
    "article-outline",
    "article-draft",
    "article-rewrite",
    "article-expand",
    "article-review",
)

_PROJECTION_KEYS = {
    "assetKind",
    "logicalId",
    "displayName",
    "physicalId",
    "ownerModule",
    "origin",
    "builtinVersion",
    "protected",
    "deletable",
    "protectionReason",
    "userAssetConflict",
}


def _rows(db: WritingDatabase) -> list[str]:
    with db.connect() as conn:
        return [
            row["physical_id"]
            for row in conn.execute(
                "SELECT physical_id FROM writing_builtin_assets"
                " WHERE workspace_key = ? ORDER BY physical_id",
                (db.workspace_key,),
            ).fetchall()
        ]


def test_registry_lists_team_five_agents_seven_skills() -> None:
    by_kind: dict[str, list[str]] = {}
    for asset in WRITING_BUILTIN_ASSETS:
        by_kind.setdefault(asset.kind, []).append(asset.logical_id)
        assert isinstance(asset, BuiltinAsset)
        assert asset.protected is True
        assert asset.display_name.strip()

    assert by_kind["team"] == [WRITING_TEAM_LOGICAL_ID]
    assert by_kind["agent"] == list(_EXPECTED_AGENTS)
    assert by_kind["skill"] == list(_EXPECTED_SKILLS)
    assert set(by_kind) <= {"team", "agent", "skill", "mcp"}
    assert len({(a.kind, a.logical_id) for a in WRITING_BUILTIN_ASSETS}) == len(
        WRITING_BUILTIN_ASSETS
    )


def test_prepare_is_idempotent(tmp_path: Path) -> None:
    db = WritingDatabase(tmp_path)
    db.ensure_schema()
    registry = BuiltinAssetRegistry(db)
    calls: list[str] = []

    def allocator(candidate: str) -> str:
        calls.append(candidate)
        return f"phys-{candidate}"

    first = registry.prepare("team", WRITING_TEAM_LOGICAL_ID, allocator=allocator)
    second = registry.prepare("team", WRITING_TEAM_LOGICAL_ID, allocator=allocator)

    assert first == second == f"phys-{WRITING_TEAM_LOGICAL_ID}"
    assert calls == [WRITING_TEAM_LOGICAL_ID]
    assert _rows(db) == [first]
    assert registry.physical_id("team", WRITING_TEAM_LOGICAL_ID) == first


def test_prepare_avoids_conflict_with_user_asset(tmp_path: Path) -> None:
    """候选 id 被用户对象占住时必须追加后缀，且用户对象不被改动。"""
    db = WritingDatabase(tmp_path)
    db.ensure_schema()
    registry = BuiltinAssetRegistry(db)
    user_objects = {"writing-team": "用户的同名自定义团队"}
    calls: list[str] = []

    def allocator(candidate: str) -> str:
        calls.append(candidate)
        if candidate in user_objects:
            return ""
        return candidate

    physical = registry.prepare("team", WRITING_TEAM_LOGICAL_ID, allocator=allocator)

    assert physical == f"{WRITING_TEAM_LOGICAL_ID}-builtin"
    assert calls == [WRITING_TEAM_LOGICAL_ID, f"{WRITING_TEAM_LOGICAL_ID}-builtin"]
    assert user_objects == {"writing-team": "用户的同名自定义团队"}
    assert physical not in user_objects


def test_protected_write_is_rejected(tmp_path: Path) -> None:
    db = WritingDatabase(tmp_path)
    db.ensure_schema()
    registry = BuiltinAssetRegistry(db)

    assert registry.is_protected("agent", "writing-editor") is True
    assert registry.is_protected("agent", "user-custom-agent") is False

    with pytest.raises(BuiltinResourceProtected) as error:
        registry.ensure_writable("agent", "writing-editor")

    assert error.value.code == BUILTIN_RESOURCE_PROTECTED
    assert error.value.status == 409
    assert str(error.value) == "builtin resource is protected: agent/writing-editor"
    # 非内置资源沿用既有语义：不受保护、不抛错。
    assert registry.ensure_writable("agent", "user-custom-agent") is None


_V1_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS articles (
      id             TEXT    NOT NULL,
      workspace_key  TEXT    NOT NULL,
      title          TEXT    NOT NULL,
      writing_type   TEXT    NOT NULL DEFAULT '',
      template_id    TEXT    NOT NULL DEFAULT '',
      status         TEXT    NOT NULL DEFAULT 'idea',
      summary        TEXT    NOT NULL DEFAULT '',
      brief          TEXT    NOT NULL DEFAULT '{}',
      body           TEXT    NOT NULL DEFAULT '',
      outline        TEXT    NOT NULL DEFAULT '[]',
      materials      TEXT    NOT NULL DEFAULT '[]',
      tags           TEXT    NOT NULL DEFAULT '[]',
      revision       INTEGER NOT NULL DEFAULT 1,
      content_digest TEXT    NOT NULL,
      created_at     TEXT    NOT NULL,
      updated_at     TEXT    NOT NULL,
      PRIMARY KEY (workspace_key, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS articles_updated_idx"
    " ON articles (workspace_key, updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS article_requests (
      request_id    TEXT NOT NULL,
      workspace_key TEXT NOT NULL,
      article_id    TEXT NOT NULL,
      op            TEXT NOT NULL,
      response      TEXT NOT NULL,
      created_at    TEXT NOT NULL,
      PRIMARY KEY (workspace_key, request_id)
    )
    """,
    "CREATE TABLE IF NOT EXISTS _schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)


def test_ensure_schema_upgrades_v1_db(tmp_path: Path) -> None:
    """v1 库（仅三表）打开后补齐新表，既有 articles 数据与工作区隔离语义不变。"""
    db_path = tmp_path / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(db_path)
    try:
        for statement in _V1_STATEMENTS:
            raw.execute(statement)
        raw.execute(
            "INSERT INTO _schema_meta (key, value) VALUES ('version', '1')"
        )
        raw.execute(
            "INSERT INTO articles (id, workspace_key, title, content_digest,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-1", "w" * 16, "旧稿", "digest", "t1", "t2"),
        )
        raw.commit()
    finally:
        raw.close()

    db = WritingDatabase(tmp_path, db_path=db_path)
    db.ensure_schema()

    with db.connect() as conn:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        article = conn.execute(
            "SELECT id, title, content_digest, created_at, updated_at FROM articles"
        ).fetchone()
        version = conn.execute(
            "SELECT value FROM _schema_meta WHERE key = 'version'"
        ).fetchone()["value"]

    assert "writing_builtin_assets" in names
    assert SCHEMA_VERSION == "3"
    assert version == "3"
    assert tuple(article) == ("legacy-1", "旧稿", "digest", "t1", "t2")
    assert compute_workspace_key(tmp_path) == db.workspace_key


def test_user_asset_conflict_preserved(tmp_path: Path) -> None:
    """用户已有同名对象时：内置另取物理 ID，project() 标出双方来源，用户对象原样保留。"""
    db = WritingDatabase(tmp_path)
    db.ensure_schema()
    registry = BuiltinAssetRegistry(db)
    user_objects = {"writing-team": "自定义团队内容"}

    def allocator(candidate: str) -> str:
        if candidate in user_objects:
            return ""
        return candidate

    physical = registry.prepare("team", WRITING_TEAM_LOGICAL_ID, allocator=allocator)
    projection = registry.project("team", WRITING_TEAM_LOGICAL_ID)

    assert physical == f"{WRITING_TEAM_LOGICAL_ID}-builtin"
    assert projection["logicalId"] == WRITING_TEAM_LOGICAL_ID
    assert projection["physicalId"] == physical
    assert projection["origin"] == "builtin"
    assert projection["userAssetConflict"] is True
    assert user_objects == {"writing-team": "自定义团队内容"}


def test_project_fields_uniform(tmp_path: Path) -> None:
    db = WritingDatabase(tmp_path)
    db.ensure_schema()
    registry = BuiltinAssetRegistry(db)

    projections = [
        registry.project(asset.kind, asset.logical_id) for asset in WRITING_BUILTIN_ASSETS
    ]
    assert len(projections) == len(WRITING_BUILTIN_ASSETS)
    for asset, projection in zip(WRITING_BUILTIN_ASSETS, projections, strict=True):
        assert set(projection) == _PROJECTION_KEYS
        assert projection["assetKind"] == asset.kind
        assert projection["logicalId"] == asset.logical_id
        assert projection["displayName"] == asset.display_name
        assert projection["ownerModule"] == "writing"
        assert projection["origin"] == "builtin"
        assert projection["builtinVersion"] == WRITING_BUILTIN_VERSION == "v1"
        assert projection["protected"] is True
        assert projection["deletable"] is False
        assert projection["userAssetConflict"] is False
        assert projection["protectionReason"] == (
            f"builtin resource is protected: {asset.kind}/{asset.logical_id}"
        )


def test_builtin_team_member_roles_do_not_repeat_their_own_name() -> None:
    """名册渲染成「- 名字：角色」，``role`` 再带一遍名字就读成「资料研究员：研究员：…」。

    这条是真实渲染里抓到的：主管层把成员列给 Leader 看，重复前缀让名册一眼像是没配好。
    ``role`` 只写职责，身份由 ``profile.name`` 提供。
    """
    from nanobot.agents.catalog import FACTORY_PROFILES
    from nanobot.writing.team_seed import writing_team_template

    names = {profile.id: profile.name for profile in FACTORY_PROFILES}
    team = writing_team_template()
    assert team.members
    for member in team.members:
        assert member.role.strip(), f"{member.agent_id} 的成员角色不能为空"
        assert not member.role.startswith(f"{names[member.agent_id]}："), (
            f"{member.agent_id} 的 role 重复了档案名：{member.role}"
        )
        assert not member.role.startswith(f"{names[member.agent_id]}:"), (
            f"{member.agent_id} 的 role 重复了档案名：{member.role}"
        )
