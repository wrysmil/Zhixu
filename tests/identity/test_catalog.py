"""Tests for identity catalog (whitelist, grouping, constants)."""

from pathlib import Path

from nanobot.identity.catalog import (
    CHAR_LIMIT,
    CORE_FILES,
    IDENTITY_DIR_NAME,
    PERSONAS_SUBDIR,
    discover_personas,
    resolve_identity_dir,
)


def test_char_limit_matches_memory_lifecycle_constant():
    """Frontend charMax and backend truncation constant must be the same number."""
    from nanobot.memory.lifecycle import MEMORY_MD_MAX_CHARS

    assert CHAR_LIMIT == MEMORY_MD_MAX_CHARS


def test_core_files_match_frontend_contract():
    names = [spec.name for spec in CORE_FILES]
    assert names == [
        "SOUL.md",
        "AGENT.md",
        "USER.md",
        "MEMORY.md",
        "prompts/policies.md",
    ]


def test_restricted_set_matches_frontend_contract():
    restricted = {spec.name for spec in CORE_FILES if spec.restricted}
    # USER.md 自 P0 起与 MEMORY.md 同构：都是记忆库的派生产物，无直写入口。
    assert restricted == {"AGENT.md", "USER.md", "MEMORY.md", "prompts/policies.md"}


def test_resolve_identity_dir_is_under_workspace(tmp_path: Path):
    assert resolve_identity_dir(tmp_path) == tmp_path / IDENTITY_DIR_NAME


def test_discover_personas_returns_sorted_markdown_only(tmp_path: Path):
    persona_dir = tmp_path / IDENTITY_DIR_NAME / PERSONAS_SUBDIR
    persona_dir.mkdir(parents=True)
    (persona_dir / "b.md").write_text("B", encoding="utf-8")
    (persona_dir / "a.md").write_text("A", encoding="utf-8")
    (persona_dir / "notes.txt").write_text("ignored", encoding="utf-8")

    assert [p.name for p in discover_personas(tmp_path)] == ["a.md", "b.md"]


def test_discover_personas_missing_dir_is_empty(tmp_path: Path):
    assert discover_personas(tmp_path) == []


def test_personas_carry_no_badge(tmp_path: Path):
    from nanobot.identity.catalog import build_persona_specs

    personas = tmp_path / IDENTITY_DIR_NAME / PERSONAS_SUBDIR
    personas.mkdir(parents=True)
    (personas / "tech_expert.md").write_text("x", encoding="utf-8")

    specs = build_persona_specs(tmp_path)

    assert [s.badge_label_key for s in specs] == [None]


def test_tech_expert_template_title_is_not_soul():
    from nanobot.utils.helpers import load_bundled_template

    content = load_bundled_template("personas/tech_expert.md")
    assert content is not None
    assert content.splitlines()[0] == "# 技术专家"
