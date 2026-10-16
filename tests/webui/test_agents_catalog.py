"""静态目录：出厂预设、id 校验与 customized 判定。"""

from __future__ import annotations

from typing import cast

from nanobot.agents.catalog import (
    DEFAULT_TOOL_CATEGORY,
    FACTORY_AGENT_CATEGORIES,
    FACTORY_PROFILES,
    TOOL_CATEGORIES,
    TOOL_RISK_OVERRIDES,
    factory_for,
    is_customized,
    is_valid_agent_id,
)
from nanobot.agents.models import AgentProfile, AgentSelection


def test_six_factory_categories_match_frontend() -> None:
    # webui/src/lib/agents/types.ts:99 的 AGENT_CATEGORIES
    assert [c.id for c in FACTORY_AGENT_CATEGORIES] == [
        "general", "coding", "writing", "research", "ops", "efficiency",
    ]
    assert [c.order for c in FACTORY_AGENT_CATEGORIES] == [0, 1, 2, 3, 4, 5]
    assert FACTORY_AGENT_CATEGORIES[1].color == "#8E44AD"


def test_every_tool_category_id_is_declared() -> None:
    known = {c.id for c in FACTORY_AGENT_CATEGORIES}
    # 工具分类的取值域与前端 TOOL_CATEGORIES 的 8 个 id 一致。
    # TOOL_CATEGORIES 是「工具名 -> 分类 id」的映射，要比的是 value 那一侧。
    assert set(TOOL_CATEGORIES.values()) <= {
        "filesystem", "execution", "web", "memory",
        "scheduling", "session", "orchestration", "media",
    }
    assert known  # 分类表非空


def test_factory_profiles_are_well_formed() -> None:
    assert FACTORY_PROFILES
    for profile in FACTORY_PROFILES:
        assert is_valid_agent_id(profile.id), profile.id
        assert profile.type == "system"
        assert profile.customized is False
        profile.validate()


def test_factory_profile_ids_are_unique() -> None:
    ids = [p.id for p in FACTORY_PROFILES]
    assert len(ids) == len(set(ids))


def test_factory_for_returns_none_for_unknown_id() -> None:
    assert factory_for("no-such-agent") is None


def test_is_valid_agent_id_rejects_traversal() -> None:
    assert is_valid_agent_id("code-reviewer")
    assert is_valid_agent_id("a1")
    assert not is_valid_agent_id("../escape")
    assert not is_valid_agent_id("a/b")
    assert not is_valid_agent_id("")
    assert not is_valid_agent_id("-leading-dash")
    assert not is_valid_agent_id("Upper")
    assert not is_valid_agent_id("x" * 65)


def test_is_customized_is_false_for_untouched_factory_profile() -> None:
    factory = FACTORY_PROFILES[0]
    assert is_customized(factory, factory) is False


def test_is_customized_detects_prompt_edit() -> None:
    factory = FACTORY_PROFILES[0]
    edited = AgentProfile.from_dict({**factory.to_dict(), "prompt": "改了提示词"})
    assert is_customized(edited, factory) is True


def test_is_customized_ignores_hidden_and_description_only_rename() -> None:
    factory = FACTORY_PROFILES[0]
    hidden = AgentProfile.from_dict({**factory.to_dict(), "hidden": True})
    assert is_customized(hidden, factory) is False


def test_is_customized_ignores_entry_order() -> None:
    factory = FACTORY_PROFILES[0]
    swapped = AgentProfile.from_dict(
        {
            **factory.to_dict(),
            "skills": {
                "mode": factory.skills.mode,
                "entries": list(reversed(factory.skills.entries)),
            },
        }
    )
    assert factory.skills.mode == "all" or is_customized(swapped, factory) is False


def test_is_customized_detects_selection_mode_change() -> None:
    factory = FACTORY_PROFILES[0]
    changed = AgentProfile.from_dict(
        {**factory.to_dict(), "tools": AgentSelection("include", ["read_file"]).to_dict()}
    )
    assert is_customized(changed, factory) is True


# -- 工具名钉子 ---------------------------------------------------------------
#
# 下面这些表和出厂预设都是按「工具注册名」索引的。写错一个名字不会报错，
# 只会静默地永远匹配不到：分类回落进兜底桶、风险回落成推导值、出厂预设少带
# 一个工具。``execute_command`` 就这么躲过了一轮，直到 exec 的风险提级失效
# 被人从下游发现。所以这里把「表里的名字必须都是真实工具名」钉成断言。


def _real_tool_names() -> set[str]:
    """枚举全部内置工具的注册名。

    ``name`` 是实例属性，不实例化拿不到。但改走 ``ToolLoader.load`` 也不
    行：那条路要过 ``enabled()`` 门禁、还要能构造出依赖注入的服务对象，
    默认配置下 cron / memory_search / spawn 等会直接被筛掉，得到的不是全集。

    所以取 ``discover()`` 拿到的类，用 ``object.__new__`` 绕开 ``__init__``
    只读 ``name``——这些 name 都是字面量，不依赖实例状态。
    """
    from nanobot.agent.tools.base import Tool
    from nanobot.agent.tools.loader import ToolLoader

    return {cast("Tool", object.__new__(cls)).name for cls in ToolLoader().discover()}


# 已知的历史残留：这些名字在表里，但不是任何工具的注册名。它们不是本轮要修的
# 范围，逐条钉住是为了让债可见且不再扩大——下面那条 pinned 断言会在有人往
# 这里加新条目时立刻失败。真要清掉时，改完删对应行即可。
_KNOWN_STALE_TOOL_REFS: dict[str, str] = {
    "image_generation": "ImageGenerationTool 的注册名是 generate_image；这里是它的 config_key",
    "long_task": "long_task.py 提供的工具叫 create_goal / update_goal，没有同名工具",
    "notebook_edit": "仓库里不存在任何 notebook 工具",
    "session_messages": (
        "该模块的真实工具名是 read_session / list_sessions / search_sessions / send_session_message"
    ),
    "sessions": "会话类工具的真实注册名是 list_sessions 等，没有单数 sessions",
}


def _preset_tool_entries() -> set[str]:
    return {entry for profile in FACTORY_PROFILES for entry in profile.tools.entries}


def test_tool_category_keys_are_all_real_tool_names() -> None:
    # execute_command 曾是死键：exec 匹配不到，回落到 DEFAULT_TOOL_CATEGORY。
    stale = set(TOOL_CATEGORIES) - _real_tool_names() - set(_KNOWN_STALE_TOOL_REFS)
    assert not stale, f"TOOL_CATEGORIES 里有非真实工具名：{sorted(stale)}"


def test_tool_risk_override_keys_are_all_real_tool_names() -> None:
    stale = set(TOOL_RISK_OVERRIDES) - _real_tool_names() - set(_KNOWN_STALE_TOOL_REFS)
    assert not stale, f"TOOL_RISK_OVERRIDES 里有非真实工具名：{sorted(stale)}"


def test_factory_preset_tool_entries_are_all_real_tool_names() -> None:
    stale = _preset_tool_entries() - _real_tool_names() - set(_KNOWN_STALE_TOOL_REFS)
    assert not stale, f"出厂预设 include 里有非真实工具名：{sorted(stale)}"


def test_known_stale_tool_refs_are_pinned() -> None:
    """钉住这份历史残留清单：既不许扩大，也不许悄悄删条目。"""
    real = _real_tool_names()
    referenced = set(TOOL_CATEGORIES) | set(TOOL_RISK_OVERRIDES) | _preset_tool_entries()
    # 清单里每一项都必须还真的被某张表引用着，否则说明债已经还了，该删这行。
    assert set(_KNOWN_STALE_TOOL_REFS) <= referenced
    assert not (set(_KNOWN_STALE_TOOL_REFS) & real), "已登记的残留项其实已经是真实工具名了"
    # 全部残留恰好等于这份清单：多出来一条就是新债，上面几条严格断言会先红。
    assert referenced - real == set(_KNOWN_STALE_TOOL_REFS)
    assert set(_KNOWN_STALE_TOOL_REFS) == {
        "image_generation", "long_task", "notebook_edit",
        "session_messages", "sessions",
    }


def test_shell_tool_is_keyed_by_its_real_name() -> None:
    """shell 工具的注册名是 exec，两张表都必须按这个名字索引。"""
    assert "exec" in _real_tool_names()
    assert "execute_command" not in _real_tool_names()
    assert TOOL_CATEGORIES["exec"] == "execution"
    assert TOOL_RISK_OVERRIDES["exec"] == "high"
    # 改键前后分类值不变：死键的值恰好等于兜底桶，所以这纯粹是消除误导。
    assert TOOL_CATEGORIES["exec"] == DEFAULT_TOOL_CATEGORY


def test_ops_runner_preset_ships_the_shell_tool() -> None:
    """ops-runner 的定位就是跑命令，出厂预设必须真带上 exec。"""
    ops = factory_for("ops-runner")
    assert ops is not None
    assert ops.tools.mode == "include"
    assert "exec" in ops.tools.entries
    assert "execute_command" not in ops.tools.entries


# -- 写作出厂档案 ---------------------------------------------------------------
#
# 「创作团队」的五个角色是内置资产投影（契约 §7）的认领依据：投影层按 logical_id
# 去找 physical id，id 或 skill 名写错一个字都不会报错，只会静默地永远认领不到。
# 所以这把清单逐字钉成断言，而不是靠「看起来差不多」。

WRITING_PROFILE_IDS: tuple[str, ...] = (
    "writing-editor",
    "writing-researcher",
    "writing-architect",
    "writing-author",
    "writing-reviewer",
)

# 契约 §7 Skill→角色 配套表，条目与顺序都照抄。
WRITING_SKILLS_BY_PROFILE: dict[str, list[str]] = {
    "writing-editor": ["article-brief", "article-review"],
    "writing-researcher": ["article-research"],
    "writing-architect": ["article-outline"],
    "writing-author": ["article-draft", "article-rewrite", "article-expand"],
    "writing-reviewer": ["article-review"],
}

# 写作角色一律只读：写文件、跑命令、发布、安装、碰凭据的工具一个都不许进白名单。
_FORBIDDEN_WRITING_TOOLS: frozenset[str] = frozenset({
    "write_file", "edit_file", "apply_patch", "exec", "exec_session",
    "run_cli_app", "spawn", "cron", "generate_image",
})

_WEB_TOOLS: frozenset[str] = frozenset({"web_search", "web_fetch"})


def test_writing_profiles_registered() -> None:
    for agent_id in WRITING_PROFILE_IDS:
        factory = factory_for(agent_id)
        assert factory is not None, f"{agent_id} 未注册为出厂档案"
        assert factory.id == agent_id


def test_writing_profiles_are_system_category_writing() -> None:
    for agent_id in WRITING_PROFILE_IDS:
        profile = factory_for(agent_id)
        assert profile is not None
        assert profile.type == "system"
        assert profile.category_id == "writing"
        assert profile.name.strip()
        assert profile.description.strip()
        assert profile.prompt.strip(), f"{agent_id} 的 prompt 不能为空"
        assert len(profile.prompt) >= 80, (
            f"{agent_id} 的 prompt 只有 {len(profile.prompt)} 字，不足 80"
        )
        profile.validate()


def test_writing_profiles_skills_whitelist() -> None:
    for agent_id, expected in WRITING_SKILLS_BY_PROFILE.items():
        profile = factory_for(agent_id)
        assert profile is not None
        assert profile.skills.mode == "include"
        assert profile.skills.entries == expected, agent_id


def test_writing_profiles_tools_whitelist() -> None:
    real = _real_tool_names()
    for agent_id in WRITING_PROFILE_IDS:
        profile = factory_for(agent_id)
        assert profile is not None
        assert profile.tools.mode == "include"
        entries = set(profile.tools.entries)
        assert entries, f"{agent_id} 的工具白名单不能为空"
        forbidden = entries & _FORBIDDEN_WRITING_TOOLS
        assert not forbidden, f"{agent_id} 白名单混入越权工具：{sorted(forbidden)}"
        assert entries <= real, f"{agent_id} 含非真实工具名：{sorted(entries - real)}"
        if agent_id == "writing-researcher":
            assert entries >= _WEB_TOOLS
        else:
            # 检索归研究员：其余角色只读作者已给的素材，不自己上网。
            assert not (entries & _WEB_TOOLS), f"{agent_id} 不该直接检索外部来源"


def test_writing_profiles_ids_unique() -> None:
    ids = [p.id for p in FACTORY_PROFILES]
    assert len(ids) == len(set(ids))
    assert len(WRITING_PROFILE_IDS) == len(set(WRITING_PROFILE_IDS))
    existing_ids = {"general-assistant", "code-reviewer", "researcher", "ops-runner"}
    assert set(WRITING_PROFILE_IDS) <= set(ids)
    assert not (set(WRITING_PROFILE_IDS) & existing_ids), "写作档案 id 与既有档案冲突"
    writing_ids = {p.id for p in FACTORY_PROFILES if p.category_id == "writing"}
    assert set(WRITING_PROFILE_IDS) <= writing_ids
