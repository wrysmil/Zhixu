"""出厂身份模板：字符上限、YAML 结构、SOUL 与 balanced 同源、播种幂等。"""

from __future__ import annotations

from pathlib import Path

import yaml

from nanobot.identity.bootstrap import (
    PERSONA_PRESET_STEMS,
    ensure_identity_templates,
    load_identity_template,
    upgrade_legacy_factory_templates,
)
from nanobot.identity.catalog import IDENTITY_DIR_NAME, char_limit_for
from nanobot.utils.helpers import load_bundled_template, sync_workspace_templates

ALL_TEMPLATE_NAMES = [
    "SOUL.md",
    "USER.md",
    "AGENT.md",
    "POLICIES.yaml",
    "prompts/policies.md",
    "memory/MEMORY.md",
    *[f"personas/{stem}.md" for stem in PERSONA_PRESET_STEMS],
]


def test_all_templates_exist_and_within_char_limit():
    for name in ALL_TEMPLATE_NAMES:
        content = load_bundled_template(name)
        assert content is not None, name
        # 上限按文件取：SOUL.md / AGENT.md 的出厂基线是出厂模板自身的体量，
        # 用默认上限（记忆视图预算）会把出厂内容挡在编辑器之外。
        limit = char_limit_for(name)
        assert len(content) <= limit, f"{name}: {len(content)} > {limit}"
        assert content.strip(), name


def test_policies_yaml_is_mapping_with_three_top_keys():
    content = load_bundled_template("POLICIES.yaml")
    assert content is not None
    parsed = yaml.safe_load(content)
    assert isinstance(parsed, dict)
    assert {"tool_policies", "scope_policy", "auto_confirm"} <= set(parsed)
    assert isinstance(parsed["tool_policies"], dict)


def test_personas_are_not_copies_of_soul():
    """persona 是可切换的表现层，SOUL 是不可切换的本体。

    早期所有 persona 都是 SOUL 的副本，激活 persona 等于把本体再注入一遍。
    出厂 SOUL 改版后体量更大，副本关系会让系统提示凭空翻倍，所以改成：
    persona 只描述「怎么表现」，不复制本体的价值观段落。
    """
    soul = load_bundled_template("SOUL.md")
    assert soul is not None
    for stem in PERSONA_PRESET_STEMS:
        persona = load_bundled_template(f"personas/{stem}.md")
        assert persona is not None, stem
        assert persona != soul, stem
        assert not persona.lstrip().startswith("# Soul"), stem


def test_load_identity_template_maps_core_and_persona():
    assert "User Profile" in (load_identity_template("USER.md") or "")
    assert "行为准则" in (load_identity_template("AGENT.md") or "")
    assert "私人导师" in (load_identity_template("mentor.md") or "")
    assert load_identity_template("id_rsa") is None


def test_ensure_seeds_identity_dir_and_personas(tmp_path: Path):
    added = ensure_identity_templates(tmp_path)

    identity = tmp_path / IDENTITY_DIR_NAME
    assert (identity / "AGENT.md").is_file()
    assert (identity / "POLICIES.yaml").is_file()
    assert (identity / "prompts" / "policies.md").is_file()
    for stem in PERSONA_PRESET_STEMS:
        assert (identity / "personas" / f"{stem}.md").is_file()
    # Legacy root seeds
    assert (tmp_path / "SOUL.md").is_file()
    assert (tmp_path / "USER.md").is_file()
    assert (tmp_path / "memory" / "MEMORY.md").is_file()
    assert added  # first run creates files


def test_ensure_never_overwrites_existing_user_file(tmp_path: Path):
    identity = tmp_path / IDENTITY_DIR_NAME
    identity.mkdir()
    (identity / "AGENT.md").write_text("# mine", encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("# my soul", encoding="utf-8")

    ensure_identity_templates(tmp_path)

    assert (identity / "AGENT.md").read_text(encoding="utf-8") == "# mine"
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == "# my soul"


# ---- 出厂模板升级 -----------------------------------------------------------


def test_upgrade_replaces_untouched_old_factory_template(tmp_path: Path):
    """老 workspace 里没被改过的出厂副本要升到新出厂模板。

    出厂基线是产品的一部分：老用户拿不到新版，等于改进对他们不生效。
    """
    identity = tmp_path / IDENTITY_DIR_NAME
    identity.mkdir()
    old_soul = load_bundled_template("legacy/SOUL-cn.md")
    old_agent = load_bundled_template("legacy/AGENT.md")
    assert old_soul and old_agent
    (identity / "SOUL.md").write_text(old_soul, encoding="utf-8")
    (identity / "AGENT.md").write_text(old_agent, encoding="utf-8")

    upgraded = upgrade_legacy_factory_templates(tmp_path)

    assert sorted(Path(p) for p in upgraded) == [
        Path("identity") / "AGENT.md",
        Path("identity") / "SOUL.md",
    ]
    assert (identity / "SOUL.md").read_text(encoding="utf-8") == (
        load_bundled_template("SOUL.md") or ""
    )
    assert (identity / "AGENT.md").read_text(encoding="utf-8") == (
        load_bundled_template("AGENT.md") or ""
    )


def test_upgrade_leaves_user_edited_files_alone(tmp_path: Path):
    """升级出厂模板绝不能吃掉用户写的东西。"""
    identity = tmp_path / IDENTITY_DIR_NAME
    identity.mkdir()
    (identity / "SOUL.md").write_text("# 我自己的灵魂", encoding="utf-8")
    (identity / "AGENT.md").write_text("# 我自己的准则", encoding="utf-8")

    assert upgrade_legacy_factory_templates(tmp_path) == []
    assert (identity / "SOUL.md").read_text(encoding="utf-8") == "# 我自己的灵魂"
    assert (identity / "AGENT.md").read_text(encoding="utf-8") == "# 我自己的准则"


def test_ensure_upgrades_untouched_old_factory_template(tmp_path: Path):
    """播种路径也要顺带升级，否则升级只在单独调用时生效。"""
    identity = tmp_path / IDENTITY_DIR_NAME
    identity.mkdir()
    old_agent = load_bundled_template("legacy/AGENT.md")
    assert old_agent
    (identity / "AGENT.md").write_text(old_agent, encoding="utf-8")

    ensure_identity_templates(tmp_path)

    assert (identity / "AGENT.md").read_text(encoding="utf-8") == (
        load_bundled_template("AGENT.md") or ""
    )


def test_ensure_is_idempotent(tmp_path: Path):
    first = ensure_identity_templates(tmp_path)
    second = ensure_identity_templates(tmp_path)
    assert first
    assert second == []


def test_sync_workspace_templates_calls_ensure(tmp_path: Path):
    sync_workspace_templates(tmp_path, silent=True)
    identity = tmp_path / IDENTITY_DIR_NAME
    assert (identity / "AGENT.md").is_file()
    assert (identity / "personas" / "balanced.md").is_file()
    # workspace 根目录的 SOUL 是出厂模板本身（播种时写入）
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == (
        load_bundled_template("SOUL.md") or ""
    )
