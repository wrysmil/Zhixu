"""AGENTS.md 双层注入：全局始终读，项目作用域存在时再读项目根。"""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.context import ContextBuilder
from nanobot.utils.helpers import load_bundled_template

GLOBAL_MARKER = "GLOBAL-AGENTS-RULE-MARKER"
PROJECT_MARKER = "PROJECT-AGENTS-RULE-MARKER"


def _builder(workspace: Path) -> ContextBuilder:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "memory").mkdir(parents=True, exist_ok=True)
    return ContextBuilder(workspace=workspace, timezone="UTC")


def test_global_agents_md_is_injected_without_project(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    builder = _builder(ws)
    (ws / "AGENTS.md").write_text(GLOBAL_MARKER, encoding="utf-8")

    prompt = builder.build_system_prompt(include_memory=False)

    assert GLOBAL_MARKER in prompt


def test_project_agents_md_is_injected_alongside_global(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    builder = _builder(ws)
    (ws / "AGENTS.md").write_text(GLOBAL_MARKER, encoding="utf-8")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "AGENTS.md").write_text(PROJECT_MARKER, encoding="utf-8")

    prompt = builder.build_system_prompt(workspace=project, include_memory=False)

    assert GLOBAL_MARKER in prompt
    assert PROJECT_MARKER in prompt
    assert "## AGENTS.md (project)" in prompt


def test_same_root_does_not_inject_agents_md_twice(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    builder = _builder(ws)
    (ws / "AGENTS.md").write_text(GLOBAL_MARKER, encoding="utf-8")

    prompt = builder.build_system_prompt(workspace=ws, include_memory=False)

    assert prompt.count(GLOBAL_MARKER) == 1
    assert "## AGENTS.md (project)" not in prompt


def test_factory_template_agents_md_is_skipped(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    builder = _builder(ws)
    template = load_bundled_template("AGENTS.md")
    assert template
    (ws / "AGENTS.md").write_text(template, encoding="utf-8")

    prompt = builder.build_system_prompt(include_memory=False)

    assert "## AGENTS.md" not in prompt
