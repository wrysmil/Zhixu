"""Leader 每回合装配：把冻结的团队快照变成一次回合的 prompt / tools / skills / model。

这一层刻意做成**纯函数**：给定 :class:`LeaderTurnContext` 就得到 registry 与 runtime，
不读 session、不写库、不碰 ``AgentLoop`` 的私有状态。装配写进 loop 会让「团队模式」和
「普通聊天」互相污染 —— 那是本功能最贵的失败模式。

四条边界（契约 §5）：

1. 工具白名单来自 Leader 档案的 core scope，**新建 registry** 而不是在 ``self.tools`` 上
   ``unregister`` 再恢复；后者会让并发的普通聊天回合看到团队的裁剪结果。
2. Team 模式移除普通 ``spawn``：成员授权来自冻结档案，绕过团队自发子 Agent 等于扩权。
3. ``team_dispatch`` 由宿主挂上，不在档案白名单里，模型也无法自行注册。
4. 档案指定的 model preset 失效时拒绝起跑，不静默退回默认模型 —— 静默回退会让
   「这个团队跑在哪个模型上」变成一个只有出错才知道的答案。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from nanobot.agent.model_runtime import ModelRuntimeResolver
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.utils.llm_runtime import LLMRuntime

# 契约 §5：Team 模式下对 Leader 隐藏的工具。普通 spawn 会绕过成员白名单与并发配额。
TEAM_HIDDEN_TOOLS: frozenset[str] = frozenset({"spawn"})


class ModelPresetUnavailable(RuntimeError):  # noqa: N818  # 与契约 §4 的 400 错误码同名，名称即口径
    """档案指向的 preset 名已不存在。

    调用方不需要再翻译一次：类名就是 ``MODEL_PRESET_UNAVAILABLE``。
    """


@dataclass(frozen=True)
class LeaderTurnContext:
    """一次 Leader 回合所需的全部不可变装配输入。

    字段一律来自 run 的冻结快照（``TeamRevisionSnapshot``），不是模型可控值：模型只能
    在 ``team_dispatch`` 的参数里说话，不能改自己这一回合能用哪些工具、跑在哪个模型上。
    """

    team_id: str
    team_revision: int
    team_name: str
    run_id: str
    turn_id: str
    role_prompt: str
    tool_allow: frozenset[str]
    skill_exclude: frozenset[str] = frozenset()
    model_id: str | None = None
    dispatch_tool: Tool | None = None
    # 写作会话扩展注入的工具：来自可信运行上下文，不受档案白名单裁剪（契约 §5）。
    extra_tools: tuple[Tool, ...] = ()
    # 回合结束时的收口动作（由 WU-06 的回合装配挂上）：参数是「本回合有没有交付答案」。
    # 没有它，Leader 不回话/报错时运行会一直停在 running，UI 上是一个永远转圈的任务。
    turn_finalizer: Callable[[bool], Awaitable[None]] | None = None


# 委派纪律：固定文本，因为它是「主管」的定义而不是某个团队的可编辑内容。
# 团队想改口径写在 ``instructions`` 里，这一段负责兜住最坏的失败模式。
_DELEGATION_DISCIPLINE = """## 主管的做事方式
1. 先判断这件事要不要成员：一句话能说清的问答、讨论、方案解释、简单计算 → 自己回答；
   需要专业分工、并行取证或成批产出 → 用 team_dispatch 派活。
2. 一次只交一件边界清楚的事给一个成员，写清目标、需要的产出形态、以及怎样算完成。
3. 给成员的说明要像给一个刚进门的同事做简报：他**没参与过这次对话**，不知道你问过什么、
   查过什么、排除了什么。把背景、已知事实和范围一并写进派活说明。
4. 不委派理解：不要写「按你的调查结果处理」「看着改一下」。派活说明里要给出具体内容与
   位置，先证明你自己弄懂了这件事。
5. 不要预测成员的结果：派出去之后简短告诉用户派了什么，然后等真实回执；成员没回来之前
   不得宣称已完成、编造产出或替它填数字。失败时带上完整错误上下文重试同一件事，
   多次失败再换思路或如实说明。
6. 成员的交付由你核对后再对用户说：是否回答了本次请求、缺口在哪、下一步是什么。
   未经核对不转述，不把成员的原话当成你的结论。"""


def render_role_prompt(
    *,
    team_name: str,
    team_revision: int,
    instructions: str,
    leader_name: str = "",
    leader_prompt: str,
    members: Sequence[tuple[str, str]],
) -> str:
    """渲染 Leader 看得见的团队段：主管交接、角色、委派纪律、团队指令与成员。

    ``members`` 是 ``(成员名, 角色)`` 序列。空白的段落整块省略，避免拼出只有标题的空壳；
    修订号必须出现，因为「运行期间团队配置冻结」是用户可感知的承诺。

    主管交接行是这个函数存在的理由：档案的 ``leader_prompt`` 早就注入了，但没有一处
    告诉模型「这一轮由谁主管、默认人格让到什么程度」，读起来就还是默认助手顺带带了段
    说明。人格仍然保留（用户在全局调的偏好是他自己的），让位的只是**决策权**。

    委派纪律参照 openakita 的「协作优先原则」（调研
    ``.ai-runtime-artifacts/research/2026-09-30-openakita-subagent-integration-research.md``
    §6.2）：主管感来自「怎么写委派说明」，不是来自一句「别自己干」的禁令。缺这几条时
    Leader 会自己做完再转述，成员席位数就成了装饰。
    """
    trimmed_leader = leader_name.strip()
    parts = [
        "# Team Mode",
        f"当前团队：{team_name}（修订 {team_revision}）。"
        "团队配置在本次运行期间冻结，之后修改档案不影响本次运行。",
    ]
    if trimmed_leader:
        parts.append(
            f"本回合由「{trimmed_leader}」主管：谁来回答、派给谁、算不算交付完成，"
            "都按这个团队的规则走。你的全局人格与长期偏好只决定**表达风格**，"
            "不决定这一轮的决策与分工。"
        )
    else:
        parts.append(
            "本回合由团队 Leader 主管：谁来回答、派给谁、算不算交付完成，"
            "都按这个团队的规则走；全局人格只决定表达风格，不决定决策与分工。"
        )
    if leader_prompt.strip():
        parts.append(f"## 你的角色\n{leader_prompt.strip()}")
    parts.append(_DELEGATION_DISCIPLINE)
    if instructions.strip():
        parts.append(f"## 团队指令\n{instructions.strip()}")
    if members:
        lines = "\n".join(
            f"- {name}：{role}" if role.strip() else f"- {name}"
            for name, role in members
            if name.strip()
        )
        if lines:
            parts.append(f"## 团队成员\n{lines}\n派活用 team_dispatch；成员那部分工作不替他们做完。")
    return "\n\n".join(parts)


def build_leader_registry(base: ToolRegistry, context: LeaderTurnContext) -> ToolRegistry:
    """按 Leader 白名单造一个**新** registry；``base`` 不被修改。

    白名单里写了 loop 没注册的工具时直接忽略：不能凭档案里一个字就凭空造出一个入口。
    """
    registry = ToolRegistry()
    for name in sorted(context.tool_allow):
        if name in TEAM_HIDDEN_TOOLS:
            continue
        tool = base.get(name)
        if tool is None:
            continue
        registry.register(tool)
    if context.dispatch_tool is not None:
        registry.register(context.dispatch_tool)
    for tool in context.extra_tools:
        registry.register(tool)
    return registry


def resolve_leader_runtime(
    resolver: ModelRuntimeResolver,
    context: LeaderTurnContext,
) -> LLMRuntime:
    """解析本回合 Leader 的 runtime；未指定 preset 时沿用当前默认，且不改全局默认。"""
    if context.model_id is None:
        return resolver.runtime
    try:
        return resolver.resolve_preset(context.model_id)
    except (KeyError, ValueError) as error:
        raise ModelPresetUnavailable(f"MODEL_PRESET_UNAVAILABLE {error}") from error
