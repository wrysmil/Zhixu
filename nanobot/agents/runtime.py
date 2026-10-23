"""把 Agent 档案解析成运行时可执行的约束。

一期的 ``AgentProfile`` 只描述「档案长什么样」；本模块回答「跑这个档案时，
提示词是什么、能用哪些工具、能用哪些技能、用哪个模型」。

``resolve_selection`` 是前端 ``webui/src/lib/agents/types.ts:148`` 的后端镜像
—— 两边必须同语义，否则 WebUI 里勾的能力和实际跑的能力会不一致。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from nanobot.agents.models import AgentProfile, AgentSelection
from nanobot.agents.store import AgentStore, AgentStoreError

if TYPE_CHECKING:
    from nanobot.config.schema import ToolsConfig

# 入站 metadata 里指名「本轮由哪个 Agent 接管」的键。WebUI 已在信封层校验，
# 主循环只消费已归一化的 id。
AGENT_PROFILE_METADATA_KEY = "agent_id"

# 单趟模板替换。escaped 分支处理 Jinja 的四花括号转义写法。
_TOKEN_RE = re.compile(r"\{\{\{\{(?P<escaped>\w+)\}\}\}\}|\{\{(?P<plain>\w+)\}\}")


def resolve_selection(
    selection: AgentSelection,
    all_ids: list[str],
    locked_ids: list[str] | None = None,
) -> set[str]:
    """把 ``AgentSelection`` 解析成具体 id 集合。

    语义与前端 ``resolveSelection`` 严格一致：``all`` 全要、``include`` 只要
    白名单、``exclude`` 全要减去黑名单；``locked_ids`` 任何模式下都在。

    ``entries`` 里不存在的 id 直接丢弃——档案可以预配置尚未安装的技能或工具。
    """
    locked = set(locked_ids or [])
    if selection.mode == "all":
        return set(all_ids) | locked
    present = set(all_ids)
    if selection.mode == "include":
        return (present & set(selection.entries)) | locked
    return (present - set(selection.entries)) | locked


@dataclass(frozen=True)
class ResolvedAgent:
    """一个档案跑起来之后的全部约束。"""

    id: str
    name: str
    description: str
    model_id: str | None
    prompt: str
    tool_names: frozenset[str] | None = None
    skill_exclude: frozenset[str] = field(default_factory=frozenset)


def render_profile_prompt(
    profile: AgentProfile,
    *,
    workspace: Path,
    enabled_skills: list[str] | None = None,
    enabled_tools: list[str] | None = None,
) -> str:
    """把档案的 ``prompt`` 模板渲染成一段可直接拼进 system prompt 的文本。

    未知变量**原样保留**：模板里出现 ``{{unknown}}`` 说明用户还没填上下文，
    静默替换成空串会让提示词莫名其妙地少一块。调用方（``SubagentManager``）
    在这段文本前面还会拼上身份行，所以这里返回空串时调用方要能跳过。

    **单趟替换**。顺序 ``str.replace`` 没有单次替换语义，替换值本身若含
    ``{{...}}`` 会被后续 token 二次替换：档案名叫 ``{{tools}}`` 时，
    ``{{name}}`` 展开成 ``{{tools}}`` 后又会被 ``{{tools}}`` 展开成工具清单，
    档案名被静默改写。这里用一次 ``re.sub`` 完成全部替换，替换值不参与
    下一次匹配。四花括号 ``{{{{name}}}}`` 按 Jinja 惯例输出为字面量
    ``{{name}}``。
    """
    replacements = {
        "name": profile.name,
        "description": profile.description,
        "skills": "、".join(enabled_skills or []) or "无",
        "tools": "、".join(enabled_tools or []) or "无",
        "date": date.today().isoformat(),
        "user_profile": "",
        "workspace": workspace.name,
    }

    def _sub(match: re.Match[str]) -> str:
        escaped, plain = match.group("escaped"), match.group("plain")
        if escaped is not None:
            # 四花括号 = 转义，输出双花括号包裹的 token
            return f"{{{{{escaped}}}}}"
        if plain is None:
            return match.group(0)
        return replacements.get(plain, match.group(0))

    rendered = _TOKEN_RE.sub(_sub, profile.prompt)
    return rendered.strip()


def profile_identity_line(agent: ResolvedAgent) -> str:
    """档案的身份行。

    子 Agent 的 system prompt 与主回合的 role_section 共用这一个构造器：两处各写
    一句的话，同一份档案在两条路径上会被念成两个名字。
    """
    return f"你是{agent.name}，{agent.description}。" if agent.description else f"你是{agent.name}。"


def profile_role_section(agent: ResolvedAgent) -> str:
    """主回合注入 ``context.py`` 的角色段：档案文本优先，没写就退回身份行。

    返回 ``None`` 会让整段不注入，那时「选了档案」和「没选档案」在模型侧没有区别。
    """
    return agent.prompt or profile_identity_line(agent)


class AgentProfileRuntime:
    """按 id 解析档案，供 ``SubagentManager`` 在每次 spawn 时查询。

    每次 :meth:`resolve` 都新建 ``AgentStore`` 并重新读盘：档案可能刚被 WebUI
    改过，进程内缓存不能比用户看到的更旧。
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace)

    @property
    def workspace(self) -> Path:
        return self._workspace

    def _store(self) -> AgentStore:
        """每次现建。store 内部有 ``_cache``，但那是单实例内的缓存。"""
        return AgentStore(self._workspace)

    @property
    def store(self) -> AgentStore:
        """给测试与运维用的直读入口。与 :meth:`resolve` 一样是现建的。"""
        return self._store()

    def _known_skill_ids(self) -> list[str]:
        """当前 workspace 下真实存在的技能名。

        直接用 ``SkillsLoader`` 而不绕 ``webui.skills_api``：后者还会 import
        ``config.loader``，那条链会把 ``nanobot.agent`` 拉进来，与本模块的
        导入形成环（``ImportError: cannot import name 'agent'``）。技能清单的
        权威来源本就是 ``SkillsLoader``，webui 那层只是加了路径脱敏与 i18n。

        ``list_skills`` 返回的是 **dict** 列表（``nanobot/agent/skills.py``），
        不是带属性的对象——按属性取名会恒得空列表。
        """
        from nanobot.agent.skills import SkillsLoader

        names: list[str] = []
        for entry in SkillsLoader(self._workspace).list_skills(filter_unavailable=False):
            name = entry.get("name")
            if name:
                names.append(name)
        return names

    def _known_tool_ids(self, tools_config: object | None) -> list[str]:
        """当前 scope 下真实存在的工具名。

        档案的 ``tools.entries`` 要和真实工具求交，否则档案里写了
        ``exec`` 而该工具因配置被关掉时，解析结果会撒谎。
        """
        import importlib

        # ToolLoader 的包内 import 有个导入顺序前提（loader.py:29）：
        #   ``import nanobot.agent.tools as _pkg`` 里的 ``as`` 要求
        #   ``nanobot.agent`` 已经是父包上的属性，而该属性由
        #   ``nanobot/agent/__init__.py`` 执行时绑定。本模块从
        #   ``nanobot.agents`` 先进，这条初始化链没跑过，属性就不存在
        #   （``nanobot`` 用 __getattr__ 惰性导出，且 _LAZY_EXPORTS 不含
        #   子模块名 → AttributeError 转 ImportError）。
        # 显式 import nanobot.agent 触发绑定，然后照常构造。
        importlib.import_module("nanobot.agent")
        from nanobot.agent.tools.context import ToolContext
        from nanobot.agent.tools.loader import ToolLoader
        from nanobot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        ctx = ToolContext(
            config=self._coerce_tools_config(tools_config),
            workspace=str(self._workspace.resolve()),
        )
        loader = ToolLoader()
        # 子 Agent 的工具集是 ``scope="subagent"``；``core`` 那层主 Agent 才有，
        # 且 SpawnTool 在没有 manager 时会抛（loader 吞掉并留下告警）。档案只
        # 约束子 Agent，所以只加载 subagent scope，避免噪声日志。
        loader.load(ctx, registry, scope="subagent")
        return registry.names()

    @staticmethod
    def _coerce_tools_config(tools_config: object | None) -> "ToolsConfig":
        """把调用方给的配置收敛成 ``ToolsConfig``；没给就造一个默认的。

        ``ToolsConfig`` 的字段类型靠 ``config.schema._resolve_tool_config_refs``
        在**导入顺序允许时**才补全（``schema.py:790-794`` 捕获 ImportError 后
        静默跳过，靠后续运行时惰性补）。直接 import ``config.schema`` 的模块
        可能抢在补全之前拿到半成品定义，此时 ``ToolsConfig()`` 会抛
        ``PydanticUserError``。先摸一下工具模块即可触发补全。
        """
        from nanobot.config.schema import ToolsConfig

        if isinstance(tools_config, ToolsConfig):
            return tools_config
        try:
            return ToolsConfig()
        except Exception:
            # ``_resolve_tool_config_refs`` 在导入期跑过一次，被循环依赖挡下后
            # 不会自动重试（schema.py:790-794 捕获 ImportError 即 pass）。
            # 这里显式补一次，函数幂等。
            #
            # 跨模块调用私有名是刻意的：config.schema 没有暴露公开的 rebuild
            # 入口，而这是唯一能把 pydantic 的延迟字段类型补全的路径。改用
            # 「先 import 工具模块再试构造」不管用——那个函数已经跑过了。
            from nanobot.config.schema import (
                _resolve_tool_config_refs,  # type: ignore[reportPrivateUsage]  # 见上
            )

            _resolve_tool_config_refs()
            return ToolsConfig()

    def resolve(
        self,
        agent_id: str,
        *,
        workspace: Path | None = None,
        tools_config: object | None = None,
        known_tools: Iterable[str] | None = None,
        known_skills: Iterable[str] | None = None,
    ) -> ResolvedAgent | None:
        """解析一个档案；id 非法或不存在时返回 ``None``（调用方回退到默认行为）。

        ``known_tools`` / ``known_skills`` 让主回合直接拿 ``AgentLoop`` 里已在用的
        真实注册表求交：主 Agent 还包含 MCP 与运行时动态工具，子 Agent 那套
        ``scope="subagent"`` 的静态枚举在这里会少算。传空集合表示「本回合确实
        没有工具/技能」，与 ``None``（去枚举）严格区分。
        """
        try:
            profile = self._store().get_profile(agent_id)
        except AgentStoreError:
            return None

        all_tools = list(known_tools) if known_tools is not None else self._known_tool_ids(
            tools_config
        )
        tool_names = frozenset(resolve_selection(profile.tools, all_tools))

        all_skills = (
            list(known_skills) if known_skills is not None else self._known_skill_ids()
        )
        enabled_skills = resolve_selection(profile.skills, all_skills)
        # build_skills_summary 只认 exclude，所以 include 模式在这里取补集。
        skill_exclude = frozenset(set(all_skills) - enabled_skills)

        return ResolvedAgent(
            id=profile.id,
            name=profile.name,
            description=profile.description,
            model_id=profile.model_id,
            prompt=render_profile_prompt(
                profile,
                workspace=workspace or self._workspace,
                enabled_skills=sorted(enabled_skills),
                enabled_tools=sorted(tool_names),
            ),
            tool_names=tool_names,
            skill_exclude=skill_exclude,
        )
