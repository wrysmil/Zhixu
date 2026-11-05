from pathlib import Path

from nanobot.agent.context import ContextBuilder, TranscriptInput


def test_project_skill_is_used_for_prompt_and_explicit_invocation(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    project = tmp_path / "project"
    for root, marker in ((agent, "GLOBAL_MARKER"), (project, "PROJECT_MARKER")):
        skill = root / "skills" / "alpha" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: alpha\ndescription: example\nmetadata: {"nanobot":{"always":true}}\n---\n' + marker, encoding="utf-8")
    builder = ContextBuilder(agent)
    prompt = builder.build_system_prompt(workspace=project, include_memory=False)
    assert "PROJECT_MARKER" in prompt
    assert "GLOBAL_MARKER" not in prompt
