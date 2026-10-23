"""AgentProfileRuntime：把档案解析成运行时约束。"""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agents.catalog import DEFAULT_MAIN_AGENT_ID
from nanobot.agents.models import AgentProfile, AgentSelection
from nanobot.agents.runtime import (
    AgentProfileRuntime,
    ResolvedAgent,
    profile_identity_line,
    profile_role_section,
    resolve_selection,
)


def test_resolve_selection_all_ignores_entries() -> None:
    selection = AgentSelection("all", ["ignored"])
    assert resolve_selection(selection, ["a", "b"]) == {"a", "b"}


def test_resolve_selection_include() -> None:
    assert resolve_selection(AgentSelection("include", ["b"]), ["a", "b", "c"]) == {"b"}


def test_resolve_selection_exclude() -> None:
    assert resolve_selection(AgentSelection("exclude", ["b"]), ["a", "b", "c"]) == {"a", "c"}


def test_resolve_selection_always_keeps_locked() -> None:
    assert resolve_selection(AgentSelection("include", []), ["a"], ["a"]) == {"a"}
    assert resolve_selection(AgentSelection("exclude", ["a"]), ["a"], ["a"]) == {"a"}


def test_resolve_selection_drops_unknown_entries() -> None:
    """档案可以预配置尚未安装的技能/工具；解析时只保留真实存在的。"""
    assert resolve_selection(AgentSelection("include", ["ghost"]), ["a"]) == set()


@pytest.fixture()
def runtime(tmp_path: Path) -> AgentProfileRuntime:
    return AgentProfileRuntime(tmp_path)


def test_missing_profile_resolves_to_none(runtime: AgentProfileRuntime) -> None:
    assert runtime.resolve("no-such-agent") is None


def test_illegal_id_resolves_to_none(runtime: AgentProfileRuntime) -> None:
    assert runtime.resolve("../../etc/passwd") is None


def test_hidden_agent_still_resolves(runtime: AgentProfileRuntime) -> None:
    """隐藏只影响 WebUI 列表，不影响运行时按 id 直接使用。"""
    runtime.store.set_visibility("code-reviewer", True)
    assert runtime.resolve("code-reviewer") is not None


def test_resolved_tool_names_follow_selection(runtime: AgentProfileRuntime) -> None:
    resolved = runtime.resolve("code-reviewer")
    assert resolved is not None
    assert "read_file" in resolved.tool_names
    assert "execute_command" not in resolved.tool_names


def test_resolved_skill_exclude_is_the_complement(runtime: AgentProfileRuntime) -> None:
    """出厂档案 ``skills=include[]``（不给技能），所以排除集就是全部真实技能。"""
    resolved = runtime.resolve("code-reviewer")
    assert resolved is not None
    assert resolved.skill_exclude == set(runtime._known_skill_ids())


def test_resolved_prompt_renders_template_variables(runtime: AgentProfileRuntime) -> None:
    runtime.store.save_profile(
        AgentProfile(
            id="templated",
            name="模板档案",
            type="custom",
            description="示例描述",
            prompt="你是{{name}}，负责{{description}}。",
        )
    )
    resolved = runtime.resolve("templated")
    assert resolved is not None
    assert "{{" not in resolved.prompt
    assert "模板档案" in resolved.prompt
    assert "示例描述" in resolved.prompt


def test_factory_profile_ships_a_prompt(runtime: AgentProfileRuntime) -> None:
    """出厂档案自带提示词，不再是空的。

    空 prompt 时子 Agent 拿到的只有「工具变少了」这一个信号，认不出自己是哪个档案；
    出厂值只是起点，用户仍然可以在档案页改写。
    """
    resolved = runtime.resolve("general-assistant")
    assert resolved is not None
    assert resolved.prompt.startswith("你是「通用助理」")
    assert "# 回报格式" in resolved.prompt


def test_resolved_prompt_fills_date_and_workspace(runtime: AgentProfileRuntime) -> None:
    runtime.store.save_profile(
        AgentProfile(
            id="dated",
            name="日期档案",
            type="custom",
            prompt="今天 {{date}}，工作区 {{workspace}}。",
        )
    )
    resolved = runtime.resolve("dated", workspace=Path("/tmp/ws"))
    assert resolved is not None
    assert "{{date}}" not in resolved.prompt
    assert "ws" in resolved.prompt


def test_resolved_model_id_is_optional(runtime: AgentProfileRuntime) -> None:
    assert runtime.resolve("code-reviewer").model_id is None  # type: ignore[union-attr]
    runtime.store.save_profile(
        AgentProfile(
            id="picky",
            name="挑剔",
            type="system",
            prompt="",
            model_id="default",
        )
    )
    assert runtime.resolve("picky").model_id == "default"  # type: ignore[union-attr]


def test_unresolvable_variable_is_left_verbatim(runtime: AgentProfileRuntime) -> None:
    runtime.store.save_profile(
        AgentProfile(id="odd", name="古怪", type="custom", prompt="见 {{unknown_token}}")
    )
    resolved = runtime.resolve("odd")
    assert resolved is not None
    assert "{{unknown_token}}" in resolved.prompt


def test_default_main_agent_is_the_commander(runtime: AgentProfileRuntime) -> None:
    assert DEFAULT_MAIN_AGENT_ID == "commander"
    resolved = runtime.resolve(
        "commander",
        known_tools=["read_file", "exec"],
        known_skills=["alpha"],
    )
    assert resolved is not None
    assert resolved.name == "知序指挥官"
    assert "知序指挥官" in resolved.prompt
    # 全量选择：主回合拿到调用方真实注册表里的全部工具，技能不裁剪。
    assert resolved.tool_names == frozenset({"read_file", "exec"})
    assert resolved.skill_exclude == frozenset()


def test_known_capabilities_skip_scope_enumeration(runtime: AgentProfileRuntime) -> None:
    """主回合传入实时清单时，不能再走子 Agent 的静态枚举。"""

    def _unexpected(_tools_config: object | None) -> list[str]:
        raise AssertionError("known_tools 已给出时不应枚举 subagent scope")

    runtime._known_tool_ids = _unexpected  # type: ignore[method-assign]
    resolved = runtime.resolve(
        "code-reviewer",
        known_tools=["read_file"],
        known_skills=["alpha"],
    )
    assert resolved is not None
    assert resolved.tool_names == frozenset({"read_file"})
    assert resolved.skill_exclude == frozenset({"alpha"})


# -- 身份行与主回合角色段 ------------------------------------------------------
#
# 两处消费方（子 Agent 的 system prompt、主回合的 role_section）必须用同一个构造器，
# 否则同一份档案在两条路径上会被念成两个名字。


def _resolved(*, name: str, description: str, prompt: str) -> ResolvedAgent:
    return ResolvedAgent(
        id="any-id", name=name, description=description, model_id=None, prompt=prompt
    )


def test_profile_identity_line_uses_name_and_description() -> None:
    agent = _resolved(name="代码评审", description="只读地审查改动", prompt="")
    assert profile_identity_line(agent) == "你是代码评审，只读地审查改动。"


def test_profile_identity_line_omits_empty_description() -> None:
    agent = _resolved(name="沉默", description="", prompt="")
    assert profile_identity_line(agent) == "你是沉默。"


def test_profile_role_section_prefers_the_profile_prompt() -> None:
    """写了提示词就用自己的文本，不再额外拼一句身份。"""
    agent = _resolved(name="评审", description="只读", prompt="逐条列出问题，不要给修复代码。")
    assert profile_role_section(agent) == "逐条列出问题，不要给修复代码。"


def test_profile_role_section_falls_back_to_identity_when_prompt_empty() -> None:
    """没写提示词的自建档案，主回合的角色段退回身份行而不是 None。

    返回 None 时 ``context.py`` 整段不注入，选了档案与没选档案在模型那边没有区别。
    """
    agent = _resolved(name="空白", description="没写提示词的自建档案", prompt="")
    assert profile_role_section(agent) == "你是空白，没写提示词的自建档案。"
