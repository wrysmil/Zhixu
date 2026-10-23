"""Tests for ContextBuilder — system prompt and message assembly."""

import os
import time
from pathlib import Path

import pytest

from nanobot.agent.context import ContextBuilder, TranscriptInput
from nanobot.identity.catalog import IDENTITY_DIR_NAME
from nanobot.identity.compiler import compile_identity
from nanobot.runtime_context import RuntimeContextBlock
from nanobot.utils.helpers import load_bundled_template

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _builder(tmp_path: Path, **kw) -> ContextBuilder:
    return ContextBuilder(workspace=tmp_path, **kw)


# ---------------------------------------------------------------------------
# _merge_message_content (static)
# ---------------------------------------------------------------------------


class TestMergeMessageContent:
    def test_str_plus_str(self):
        result = ContextBuilder._merge_message_content("hello", "world")
        assert result == "hello\n\nworld"

    def test_empty_left_plus_str(self):
        result = ContextBuilder._merge_message_content("", "world")
        assert result == "world"

    def test_list_plus_list(self):
        left = [{"type": "text", "text": "a"}]
        right = [{"type": "text", "text": "b"}]
        result = ContextBuilder._merge_message_content(left, right)
        assert len(result) == 2
        assert result[0]["text"] == "a"
        assert result[1]["text"] == "b"

    def test_str_plus_list(self):
        right = [{"type": "text", "text": "b"}]
        result = ContextBuilder._merge_message_content("hello", right)
        assert len(result) == 2
        assert result[0]["text"] == "hello"
        assert result[1]["text"] == "b"

    def test_list_plus_str(self):
        left = [{"type": "text", "text": "a"}]
        result = ContextBuilder._merge_message_content(left, "world")
        assert len(result) == 2
        assert result[0]["text"] == "a"
        assert result[1]["text"] == "world"

    def test_none_plus_str(self):
        result = ContextBuilder._merge_message_content(None, "hello")
        assert result == [{"type": "text", "text": "hello"}]

    def test_str_plus_none(self):
        result = ContextBuilder._merge_message_content("hello", None)
        assert result == [{"type": "text", "text": "hello"}]

    def test_none_plus_none(self):
        result = ContextBuilder._merge_message_content(None, None)
        assert result == []

    def test_list_items_not_dicts_wrapped(self):
        result = ContextBuilder._merge_message_content(["raw_item"], None)
        assert result == [{"type": "text", "text": "raw_item"}]


# ---------------------------------------------------------------------------
# _load_bootstrap_files
# ---------------------------------------------------------------------------


class TestLoadBootstrapFiles:
    def test_no_bootstrap_files(self, tmp_path):
        builder = _builder(tmp_path)
        assert builder._load_bootstrap_files() == ""

    def test_empty_bootstrap_files(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("\n", encoding="utf-8")
        builder = _builder(tmp_path)
        assert builder._load_bootstrap_files() == ""

    def test_agents_md(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Be helpful.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## AGENTS.md" in result
        assert "Be helpful." in result

    def test_multiple_bootstrap_files(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Rules.", encoding="utf-8")
        (tmp_path / "SOUL.md").write_text("Soul.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## AGENTS.md" in result
        assert "## SOUL.md" in result
        assert "Rules." in result
        assert "Soul." in result

    def test_all_bootstrap_files(self, tmp_path):
        # AGENT.md is the odd one out: it lives under identity/, not the
        # workspace root (SOUL/USER keep a root fallback for unmigrated
        # workspaces, AGENT.md has no legacy location).
        for name in ContextBuilder.BOOTSTRAP_FILES:
            root = tmp_path / IDENTITY_DIR_NAME if name == "AGENT.md" else tmp_path
            root.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(f"Content of {name}", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        for name in ContextBuilder.BOOTSTRAP_FILES:
            assert f"## {name}" in result
            assert f"Content of {name}" in result

    def test_legacy_tools_md_is_not_bootstrapped(self, tmp_path):
        (tmp_path / "TOOLS.md").write_text("workspace tool notes", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "TOOLS.md" not in result
        assert "workspace tool notes" not in result

    def test_utf8_content(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("用中文回复", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "用中文回复" in result

    def test_selected_project_supplies_only_agents_file(self, tmp_path):
        agent_home = tmp_path / "agent-home"
        project = tmp_path / "project"
        agent_home.mkdir()
        project.mkdir()
        (agent_home / "AGENTS.md").write_text("global project rules", encoding="utf-8")
        (agent_home / "SOUL.md").write_text("global soul", encoding="utf-8")
        (agent_home / "USER.md").write_text("global user", encoding="utf-8")
        (project / "AGENTS.md").write_text("selected project rules", encoding="utf-8")
        (project / "SOUL.md").write_text("project soul collision", encoding="utf-8")
        (project / "USER.md").write_text("project user collision", encoding="utf-8")

        result = ContextBuilder(agent_home).build_system_prompt(workspace=project)

        assert "selected project rules" in result
        assert "global project rules" not in result
        assert "global soul" in result
        assert "global user" in result
        assert "project soul collision" not in result
        assert "project user collision" not in result

    def test_selected_project_without_agents_does_not_fall_back(self, tmp_path):
        agent_home = tmp_path / "agent-home"
        project = tmp_path / "project"
        agent_home.mkdir()
        project.mkdir()
        (agent_home / "AGENTS.md").write_text("default workspace rules", encoding="utf-8")

        result = ContextBuilder(agent_home).build_system_prompt(workspace=project)

        assert "default workspace rules" not in result

    def test_unmodified_agents_and_user_templates_are_skipped(self, tmp_path):
        from nanobot.utils.helpers import sync_workspace_templates

        sync_workspace_templates(tmp_path, silent=True)

        result = ContextBuilder(tmp_path)._load_bootstrap_files()

        assert "## AGENTS.md" not in result
        assert "## USER.md" not in result
        assert "## SOUL.md" in result

    def test_customized_user_template_is_loaded(self, tmp_path):
        from nanobot.utils.helpers import sync_workspace_templates

        sync_workspace_templates(tmp_path, silent=True)
        (tmp_path / "USER.md").write_text("User prefers Chinese.", encoding="utf-8")

        result = ContextBuilder(tmp_path)._load_bootstrap_files()

        assert "## USER.md" in result
        assert "User prefers Chinese." in result


# ---------------------------------------------------------------------------
# _is_template_content (static)
# ---------------------------------------------------------------------------


class TestIsTemplateContent:
    def test_nonexistent_template_returns_false(self):
        assert ContextBuilder._is_template_content("anything", "nonexistent/path.md") is False

    def test_content_matching_template(self):
        from importlib.resources import files as pkg_files
        tpl = pkg_files("nanobot") / "templates" / "memory" / "MEMORY.md"
        if not tpl.is_file():
            pytest.skip("MEMORY.md template not bundled")
        original = tpl.read_text(encoding="utf-8")
        assert ContextBuilder._is_template_content(original, "memory/MEMORY.md") is True

    def test_modified_content_returns_false(self):
        from importlib.resources import files as pkg_files
        tpl = pkg_files("nanobot") / "templates" / "memory" / "MEMORY.md"
        if not tpl.is_file():
            pytest.skip("MEMORY.md template not bundled")
        assert ContextBuilder._is_template_content("totally different", "memory/MEMORY.md") is False


# ---------------------------------------------------------------------------
# Bundled bootstrap templates
# ---------------------------------------------------------------------------


class TestBundledToolContract:
    def test_tool_contract_balances_general_and_coding_workflows(self):
        from importlib.resources import files as pkg_files

        tpl = pkg_files("nanobot") / "templates" / "agent" / "tool_contract.md"
        content = tpl.read_text(encoding="utf-8")

        assert "## General Tool Contract" in content
        assert "Use the narrowest structured tool" in content
        assert "Do not use `exec` as a universal workaround" in content
        assert "## File and Coding Workflows" in content
        assert "`grep` returns matches with five context lines by default" in content
        assert "apply_patch" in content
        assert "acceptance criteria into concrete checks" in content
        assert "visual evidence reaches the model" in content
        assert "clear user request as authorization" in content
        assert "Never invent missing records or measurements" in content
        assert "## Web and External Information" in content
        assert "## Messaging and Media" in content
        assert "## Scheduling and Background Work" in content

    def test_tool_contract_is_injected_without_workspace_file(self, tmp_path):
        builder = _builder(tmp_path)
        prompt = builder.build_system_prompt()

        assert "# Tool Usage Notes" in prompt
        assert "## General Tool Contract" in prompt
        assert "Do not use `exec` as a universal workaround" in prompt


# ---------------------------------------------------------------------------
# build_user_content
# ---------------------------------------------------------------------------


class TestBuildUserContent:
    def test_no_media_returns_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_user_content("hello", None)
        assert result == "hello"

    def test_empty_media_returns_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_user_content("hello", [])
        assert result == "hello"

    def test_nonexistent_media_file_returns_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_user_content("hello", ["/nonexistent/image.png"])
        assert result == "hello"

    def test_non_image_file_returns_string(self, tmp_path):
        txt = tmp_path / "doc.txt"
        txt.write_text("not an image", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder.build_user_content("hello", [str(txt)])
        assert result == "hello"

    def test_valid_image_returns_list(self, tmp_path):
        png = tmp_path / "test.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        builder = _builder(tmp_path)
        result = builder.build_user_content("hello", [str(png)])
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["type"] == "image_url"
        assert result[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert result[1]["type"] == "text"
        assert result[1]["text"] == "hello"

    def test_image_meta_includes_path(self, tmp_path):
        png = tmp_path / "test.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        builder = _builder(tmp_path)
        result = builder.build_user_content("hello", [str(png)])
        assert "_meta" in result[0]
        assert "path" in result[0]["_meta"]


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    def test_returns_nonempty_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_identity_section(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert "workspace" in result.lower() or "python" in result.lower()

    def test_default_identity_uses_relative_agent_paths(self, tmp_path):
        result = ContextBuilder(tmp_path)._get_identity()

        assert str(tmp_path.resolve()) not in result
        assert "Agent profile: SOUL.md and USER.md" in result
        assert "History log: memory/history.jsonl" in result
        assert "Custom skills: skills/{skill-name}/SKILL.md" in result

    def test_selected_project_identity_keeps_agent_data_in_agent_workspace(self, tmp_path):
        agent_home = tmp_path / "agent-home"
        project = tmp_path / "project"
        agent_home.mkdir()
        project.mkdir()

        result = ContextBuilder(agent_home)._get_identity(workspace=project)

        assert str(project.resolve()) not in result
        assert f"知序的 agent 工作区位于：{agent_home.resolve()}" in result
        assert f"{agent_home.resolve()}/SOUL.md" in result
        assert f"{project.resolve()}/SOUL.md" not in result

    def test_includes_bootstrap_files(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Be helpful and concise.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert "Be helpful and concise." in result

    def test_includes_session_summary(self, tmp_path):
        builder = _builder(tmp_path)
        summary = {
            "text": "Previous chat about Python.",
            "last_active": "2026-08-19T10:00:00",
        }
        result = builder.build_system_prompt(session_summary=summary)
        assert "Previous chat about Python." in result
        assert "[Archived Context Summary]" in result

    def test_sections_separated_by_separator(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Rules.", encoding="utf-8")
        builder = _builder(tmp_path)
        summary = {"text": "Summary.", "last_active": "2026-08-19T10:00:00"}
        result = builder.build_system_prompt(session_summary=summary)
        assert "\n\n---\n\n" in result

    def test_no_bootstrap_no_summary(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert "## AGENTS.md" not in result
        assert "[Archived Context Summary]" not in result


# ---------------------------------------------------------------------------
# build_messages
# ---------------------------------------------------------------------------


class TestBuildMessages:
    def test_optional_arguments_are_keyword_only(self, tmp_path):
        builder = _builder(tmp_path)

        with pytest.raises(TypeError):
            builder.build_system_prompt(["legacy-skill"])
        with pytest.raises(TypeError):
            builder.build_messages([], "hello", ["legacy-skill"])

    def test_basic_empty_history(self, tmp_path):
        builder = _builder(tmp_path)
        messages = builder.build_messages([], "hello")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "hello" in str(messages[1]["content"])

    def test_public_builder_preserves_assistant_role_compatibility(self, tmp_path):
        from nanobot.agent import ContextBuilder as PublicContextBuilder

        builder = PublicContextBuilder(tmp_path)
        messages = builder.build_messages(
            history=[{"role": "assistant", "content": "previous result"}],
            current_message="subagent result",
            current_role="assistant",
            runtime_context_blocks=[
                RuntimeContextBlock(source="test", content="user-only runtime context"),
            ],
        )

        assert len(messages) == 2
        assert messages[-1]["role"] == "assistant"
        assert messages[-1]["content"] == "previous result\n\nsubagent result"
        assert "user-only runtime context" not in messages[-1]["content"]
        assert "_meta" not in messages[-1]

    def test_compatibility_builder_merges_system_role_without_history(self, tmp_path):
        builder = _builder(tmp_path)

        messages = builder.build_messages([], "system event", current_role="system")

        assert len(messages) == 1
        assert messages[0]["role"] == "system"
        assert str(messages[0]["content"]).endswith("system event")

    def test_explicit_skill_reference_loads_full_instructions_for_this_turn(self, tmp_path):
        skill_dir = tmp_path / "skills" / "review"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: review\n"
            "description: Review changes.\n"
            "---\n\n"
            "# Review workflow\n\nFollow the unique review checklist.",
            encoding="utf-8",
        )
        builder = _builder(tmp_path)

        messages = builder.build_messages([], "Please $review this patch and use $review carefully.")
        plain_messages = builder.build_messages([], "Please review this patch carefully.")

        system_prompt = messages[0]["content"]
        user_prompt = messages[-1]["content"]
        assert system_prompt == plain_messages[0]["content"]
        assert "Follow the unique review checklist." not in system_prompt
        assert "Please $review this patch" in user_prompt
        assert "[Active Skills — instructions for this user turn]" in user_prompt
        assert "### Skill: review" in user_prompt
        assert "Follow the unique review checklist." in user_prompt
        assert user_prompt.count("### Skill: review") == 1
        assert messages[-1]["_meta"]["runtime_context"]["sources"] == [
            "explicit_skills"
        ]

    def test_unknown_skill_reference_does_not_change_active_skills(self, tmp_path):
        messages = _builder(tmp_path).build_messages([], "Keep the shell literal $HOME.")

        assert "# Active Skills" not in messages[0]["content"]

    def test_runtime_context_is_not_injected_by_default(self, tmp_path):
        builder = _builder(tmp_path)
        messages = builder.build_messages([], "hello", channel="cli")
        user_msg = str(messages[-1]["content"])
        assert user_msg == "hello"

    def test_explicit_runtime_context_blocks_are_appended(self, tmp_path):
        builder = _builder(tmp_path)
        messages = builder.build_messages(
            [],
            "please use @zoom tonight",
            runtime_context_blocks=[
                RuntimeContextBlock(
                    source="cli_apps",
                    content="CLI App Attachment: @zoom (installed; tool=run_cli_app).",
                ),
            ],
        )
        user_msg = str(messages[-1]["content"])

        assert "CLI App Attachment: @zoom" in user_msg
        assert "tool=run_cli_app" in user_msg
        assert user_msg.index("please use @zoom tonight") < user_msg.index(
            "CLI App Attachment: @zoom"
        )
        assert messages[-1]["_meta"]["runtime_context"]["sources"] == ["cli_apps"]

    def test_consecutive_same_role_merged(self, tmp_path):
        builder = _builder(tmp_path)
        history = [{"role": "user", "content": "previous user message"}]
        messages = builder.build_messages(history, "new message")
        assert len(messages) == 2  # system + merged user
        assert "previous user message" in str(messages[1]["content"])
        assert "new message" in str(messages[1]["content"])

    def test_structured_transcript_preserves_fresh_turn_boundary(self, tmp_path):
        builder = _builder(tmp_path)
        transcript = TranscriptInput(
            history=[{"role": "user", "content": "previous user message"}],
            current_message="new message",
        )

        messages = builder.build_transcript(transcript)

        assert [message["role"] for message in messages] == ["system", "user", "user"]
        assert messages[-2]["content"] == "previous user message"
        assert messages[-1]["content"] == "new message"
        assert transcript.message_count == 3

    def test_current_message_can_be_built_without_history_merge(self, tmp_path):
        builder = _builder(tmp_path)
        current = builder.build_current_message(
            "new message",
            runtime_context_blocks=[
                RuntimeContextBlock(source="test", content="fresh context"),
            ],
        )

        assert current["role"] == "user"
        assert "new message" in current["content"]
        assert "fresh context" in current["content"]
        assert current["_meta"]["runtime_context"]["sources"] == ["test"]

    def test_different_role_appended(self, tmp_path):
        builder = _builder(tmp_path)
        history = [{"role": "assistant", "content": "previous response"}]
        messages = builder.build_messages(history, "new message")
        assert len(messages) == 3  # system + assistant + user

    def test_media_with_history(self, tmp_path):
        png = tmp_path / "img.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        builder = _builder(tmp_path)
        history = [{"role": "assistant", "content": "see this"}]
        messages = builder.build_messages(history, "check image", media=[str(png)])
        user_msg = messages[-1]["content"]
        assert isinstance(user_msg, list)
        assert any(b.get("type") == "image_url" for b in user_msg)


# ---------------------------------------------------------------------------
# identity layer: compiled products take over from full text
# ---------------------------------------------------------------------------


# Both sources carry a section the compiler drops, so "was the compiled
# product injected, or the raw file?" is decidable from the prompt alone.
_SOUL_SOURCE = "# Soul\n\n## 核心原则\n\n- 保留这句\n\n## 平台职责\n\n- 编译应剔除这句\n"
_AGENT_SOURCE = "# Agent 行为准则\n\n## 任务执行\n\n- 保留行为\n\n## 平台职责\n\n- 编译应剔除这句\n"


class TestCompiledIdentityInjection:
    def _seed_sources(self, workspace: Path) -> None:
        identity_dir = workspace / IDENTITY_DIR_NAME
        identity_dir.mkdir(parents=True, exist_ok=True)
        (identity_dir / "SOUL.md").write_text(_SOUL_SOURCE, encoding="utf-8")
        (identity_dir / "AGENT.md").write_text(_AGENT_SOURCE, encoding="utf-8")

    def test_fresh_products_replace_full_text(self, tmp_path):
        self._seed_sources(tmp_path)
        compile_identity(tmp_path)

        result = _builder(tmp_path)._load_bootstrap_files()

        assert "## SOUL.md" in result
        assert "## AGENT.md" in result
        assert "保留这句" in result
        assert "编译应剔除这句" not in result

    def test_full_text_fallback_before_any_compile(self, tmp_path):
        """没编译过也要照常注入——编译是优化，不是开关。"""
        self._seed_sources(tmp_path)

        result = _builder(tmp_path)._load_bootstrap_files()

        assert "保留这句" in result
        assert "编译应剔除这句" in result

    def test_stale_products_fall_back_to_full_text(self, tmp_path):
        """改了源文件没重新编译：改动必须立刻生效，不能等编译。"""
        self._seed_sources(tmp_path)
        compile_identity(tmp_path)
        future = time.time() + 5
        soul = tmp_path / IDENTITY_DIR_NAME / "SOUL.md"
        os.utime(soul, (future, future))

        result = _builder(tmp_path)._load_bootstrap_files()

        assert "编译应剔除这句" in result

    def test_factory_agent_template_is_not_injected(self, tmp_path):
        """出厂 AGENT.md 不该占用 system prompt；用户改过才进。"""
        identity_dir = tmp_path / IDENTITY_DIR_NAME
        identity_dir.mkdir(parents=True, exist_ok=True)
        template = load_bundled_template("AGENT.md")
        assert template is not None
        (identity_dir / "AGENT.md").write_text(template, encoding="utf-8")

        result = _builder(tmp_path)._load_bootstrap_files()

        assert "## AGENT.md" not in result

    def test_factory_agent_template_stays_out_after_compile(self, tmp_path):
        """出厂 AGENT.md 编译过也不进 prompt。

        上面那条只覆盖未编译的回退分支；产物分支拿非空正文就直接注入，够不到
        _SKIPPABLE_DEFAULTS。编译侧不产出才让两条路径的行为一致。
        """
        identity_dir = tmp_path / IDENTITY_DIR_NAME
        identity_dir.mkdir(parents=True, exist_ok=True)
        template = load_bundled_template("AGENT.md")
        assert template is not None
        (identity_dir / "AGENT.md").write_text(template, encoding="utf-8")

        compile_identity(tmp_path)
        result = _builder(tmp_path)._load_bootstrap_files()

        assert "## AGENT.md" not in result

    def test_products_without_persona_content_do_not_hide_the_source(self, tmp_path):
        """全部目标编译为空时产物集不可用，注入必须回到源文件。"""
        identity_dir = tmp_path / IDENTITY_DIR_NAME
        identity_dir.mkdir(parents=True, exist_ok=True)
        (identity_dir / "USER.md").write_text(
            "# User Profile\n\n- **称呼**：（待填）\n- **语气**：轻松\n", encoding="utf-8"
        )
        compile_identity(tmp_path)

        result = _builder(tmp_path)._load_bootstrap_files()

        assert "轻松" in result


class TestPoliciesSectionInjection:
    """``identity/prompts/policies.md`` 的注入：定制过才进，模板态与缺文件都不进。"""

    def _write_policies(self, workspace: Path, content: str) -> None:
        target = workspace / IDENTITY_DIR_NAME / "prompts"
        target.mkdir(parents=True, exist_ok=True)
        (target / "policies.md").write_text(content, encoding="utf-8")

    def test_custom_policies_are_injected(self, tmp_path):
        self._write_policies(
            tmp_path, "# 系统策略段落（覆写）\n\n- 涉及医疗的建议必须提示非专业意见。\n"
        )

        result = _builder(tmp_path).build_system_prompt()

        assert "## Policies" in result
        assert "涉及医疗的建议必须提示非专业意见" in result

    def test_factory_policies_template_is_not_injected(self, tmp_path):
        """出厂策略文件只有「在下方书写…」的引导语，不该占 prompt。"""
        template = load_bundled_template("prompts/policies.md")
        assert template is not None
        self._write_policies(tmp_path, template)

        result = _builder(tmp_path).build_system_prompt()

        assert "## Policies" not in result

    def test_blank_policies_file_is_not_injected(self, tmp_path):
        self._write_policies(tmp_path, "   \n\n")

        result = _builder(tmp_path).build_system_prompt()

        assert "## Policies" not in result

    def test_missing_policies_file_is_not_an_error(self, tmp_path):
        """老 workspace 没有 identity/prompts/：不该抛，也不该注入空段。"""
        result = _builder(tmp_path).build_system_prompt()

        assert "## Policies" not in result


# ---------------------------------------------------------------------------
# Persona injection（表现层段落）
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.parametrize(
    ("state", "persona_body", "expected"),
    [
        (None, "你是技术搭档", False),
        ("", "你是技术搭档", False),
        ("tech_expert", "你是技术搭档", True),
        ("missing_one", "你是技术搭档", False),
        ("tech_expert", "", False),
    ],
)
def test_persona_section_only_injects_when_active(tmp_path, state, persona_body, expected):
    ws = tmp_path / "workspace"
    _write(ws / "identity" / "SOUL.md", "# Soul\n\n本体内容。\n")
    _write(ws / "identity" / "AGENT.md", "# Agent 行为准则\n\n方法论内容。\n")
    _write(ws / "identity" / "personas" / "tech_expert.md", f"# 技术专家\n\n{persona_body}\n")
    if state is not None:
        _write(ws / "identity" / "active_persona", state + "\n")

    prompt = ContextBuilder(ws).build_system_prompt()

    assert ("## 当前人格：tech_expert" in prompt) is expected


def test_persona_strips_leading_h1_and_keeps_lower_headings(tmp_path):
    ws = tmp_path / "workspace"
    _write(ws / "identity" / "personas" / "tech_expert.md", "# 技术专家\n\n## 人格\n\n严谨。\n")
    _write(ws / "identity" / "active_persona", "tech_expert\n")

    prompt = ContextBuilder(ws).build_system_prompt()

    assert "## 当前人格：tech_expert" in prompt
    tail = prompt.split("## 当前人格：tech_expert", 1)[1]
    assert "技术专家" not in tail
    assert "## 人格" in tail


def test_persona_section_sits_between_soul_and_agent(tmp_path):
    ws = tmp_path / "workspace"
    _write(ws / "identity" / "SOUL.md", "SOUL_BODY")
    _write(ws / "identity" / "AGENT.md", "AGENT_BODY")
    _write(ws / "identity" / "personas" / "tech_expert.md", "PERSONA_BODY")
    _write(ws / "identity" / "active_persona", "tech_expert")

    prompt = ContextBuilder(ws).build_system_prompt()

    assert prompt.index("## SOUL.md") < prompt.index("## 当前人格") < prompt.index("## AGENT.md")


def test_persona_ignores_path_traversal_state(tmp_path):
    """状态文件是用户可写的，``../`` 不能被当成 persona 路径拼进 prompt。

    逃逸目标必须真实存在——指向不存在的文件只会走到 ``OSError`` 兜底分支，
    那样即使删掉 ``is_safe_persona_stem`` 守卫这条测试也照样绿，等于没钉住任何东西。
    """
    ws = tmp_path / "workspace"
    _write(ws / "identity" / "personas" / "tech_expert.md", "PERSONA_BODY")
    _write(ws / "identity" / "active_persona", "../../../escape")
    # ws/identity/personas/../../../escape.md == tmp_path/escape.md
    _write(tmp_path / "escape.md", "ESCAPED_BODY")

    prompt = ContextBuilder(ws).build_system_prompt()

    assert "## 当前人格" not in prompt
    assert "ESCAPED_BODY" not in prompt


def test_persona_stem_with_dot_is_injected(tmp_path):
    """``foo.bar.md`` 的 stem 含点：能被列出就必须能被激活、能注入。

    旧守卫用 ``str.isidentifier()``，会静默吞掉这类 persona——文件在选择列表里
    可见，激活却永远失败。
    """
    ws = tmp_path / "workspace"
    _write(ws / "identity" / "personas" / "foo.bar.md", "# 点号人格\n\nDOTTED_BODY\n")
    _write(ws / "identity" / "active_persona", "foo.bar")

    prompt = ContextBuilder(ws).build_system_prompt()

    assert "## 当前人格：foo.bar" in prompt
    assert "DOTTED_BODY" in prompt


def test_persona_injects_once_when_soul_absent(tmp_path):
    """SOUL.md 缺失时 persona 仍要注入，且只注入一次。

    段位挂在 ``sources`` 的 SOUL 槽位上而非「SOUL 渲染成功之后」，所以本体
    缺失不会连带把人格吞掉——这条钉的就是这个行为不被回归掉。
    """
    ws = tmp_path / "workspace"
    _write(ws / "identity" / "personas" / "tech_expert.md", "PERSONA_BODY")
    _write(ws / "identity" / "active_persona", "tech_expert")

    prompt = ContextBuilder(ws).build_system_prompt()

    assert prompt.count("## 当前人格：tech_expert") == 1
    assert "PERSONA_BODY" in prompt


def test_factory_template_persona_is_injected_when_activated(tmp_path):
    """出厂 persona 预设被显式激活时照常注入。

    这条断言原本是反的，理由是「没被改过的出厂模板不该占 prompt」。但 persona
    是否生效由 ``active_persona`` 决定，而用户写这个文件的目的就是激活某个
    人格——跳过出厂内容等于「选了等于没选」，还必须手动改一个字才生效。省下的
    那点 token 不值这个代价。与 SOUL 走同一套判断：SOUL 也不跳过出厂模板。

    真正该零成本的是**未被激活**的 persona，由下面那条用例钉住。
    """
    template = load_bundled_template("personas/tech_expert.md")
    assert template is not None
    ws = tmp_path / "workspace"
    _write(ws / "identity" / "personas" / "tech_expert.md", template)
    _write(ws / "identity" / "active_persona", "tech_expert")

    prompt = ContextBuilder(ws).build_system_prompt()

    assert prompt.count("## 当前人格：tech_expert") == 1
    assert "你是知序的技术搭档" in prompt


def test_unactivated_factory_persona_costs_nothing(tmp_path):
    """没被选中的出厂 persona 不占 prompt——这才是零 token 成本该管的地方。"""
    template = load_bundled_template("personas/tech_expert.md")
    assert template is not None
    ws = tmp_path / "workspace"
    _write(ws / "identity" / "personas" / "tech_expert.md", template)
    _write(ws / "identity" / "personas" / "companion.md", template)
    _write(ws / "identity" / "active_persona", "companion")

    prompt = ContextBuilder(ws).build_system_prompt()

    assert "## 当前人格：companion" in prompt
    assert "## 当前人格：tech_expert" not in prompt


class TestWorkingMemoryInjection:
    """``working_memory_section`` 的注入位置与空值语义。

    位置是刻意的：工作记忆排在长期记忆**之前**——「我现在在做什么」是
    「我平时知道什么」的语境，反过来会让长期记忆抢掉当前任务的前景。
    """

    _SECTION = "# Working Memory\n\n## 当前任务\n重写注入链路"

    def _seed_long_term_memory(self, builder: ContextBuilder) -> None:
        builder.memory.write_memory("长期记忆正文")

    def test_injected_before_long_term_memory(self, tmp_path):
        builder = _builder(tmp_path)
        self._seed_long_term_memory(builder)

        result = builder.build_system_prompt(working_memory_section=self._SECTION)

        working = result.index("# Working Memory")
        long_term = result.index("长期记忆正文")
        assert working < long_term

    def test_default_none_does_not_append(self, tmp_path):
        result = _builder(tmp_path).build_system_prompt()

        assert "# Working Memory" not in result

    def test_empty_string_does_not_append_shell(self, tmp_path):
        """空串不能变成一个只剩分隔线的空壳段落。"""
        builder = _builder(tmp_path)
        baseline = builder.build_system_prompt()

        result = builder.build_system_prompt(working_memory_section="")

        assert "# Working Memory" not in result
        assert result == baseline

    def test_build_transcript_forwards_section(self, tmp_path):
        transcript = TranscriptInput(history=[], current_message="hi")

        messages = _builder(tmp_path).build_transcript(
            transcript,
            working_memory_section=self._SECTION,
        )

        assert "# Working Memory" in messages[0]["content"]

    def test_build_messages_forwards_section(self, tmp_path):
        messages = _builder(tmp_path).build_messages(
            history=[],
            current_message="hi",
            working_memory_section=self._SECTION,
        )

        assert "# Working Memory" in messages[0]["content"]

    def test_build_transcript_empty_section_no_shell(self, tmp_path):
        transcript = TranscriptInput(history=[], current_message="hi")

        messages = _builder(tmp_path).build_transcript(transcript, working_memory_section="")

        assert "# Working Memory" not in messages[0]["content"]
