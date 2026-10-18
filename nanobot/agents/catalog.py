"""Agent 档案的静态目录：出厂预设、分类表、工具元数据映射。

遵循 ``nanobot/identity/catalog.py`` 的定位——**单一真相源**。凡是「哪些
Agent 出厂就有」「哪些 id 合法」「一个档案算不算被用户改过」这类问题，
只在本文件回答，别散落到 store 或 API 层。
"""

from __future__ import annotations

import re
from typing import Any

from nanobot.agents.models import AgentCategory, AgentProfile, AgentSelection

# 存储根目录名，与 nanobot/identity/catalog.py:9 的 IDENTITY_DIR_NAME 并列。
AGENTS_DIR_NAME = "agents"
PROFILES_SUBDIR = "profiles"
CATEGORIES_FILE = "categories.json"

# 档案 id 同时是文件名，所以既挡路径分隔符也挡 Windows 保留字符。
# 落在 agents/profiles/{id}.json，落到盘上就是一个可读、可手改的名字。
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# 未显式 @ 任何 Agent 时接管主回合的系统档案。它是「默认系统提示词」在
# Agent 管理里的可见载体：改这份档案的提示词/模型/能力即可改变默认行为。
DEFAULT_MAIN_AGENT_ID = "commander"

# 参与「是否被用户实质编辑」判定的字段。``hidden`` 不在其中：隐藏是视图
# 偏好，不是内容改动。沿用 openakita profile.py:597 的同名集合设计。
CUSTOMIZATION_FIELDS: frozenset[str] = frozenset({
    "name", "description", "category_id", "icon", "color", "prompt", "model_id",
    "tools", "skills", "sub_agents",
})

FACTORY_AGENT_CATEGORIES: tuple[AgentCategory, ...] = (
    AgentCategory(id="general", name="通用", color="#4A90D9", order=0),
    AgentCategory(id="coding", name="编码", color="#8E44AD", order=1),
    AgentCategory(id="writing", name="写作", color="#E67E22", order=2),
    AgentCategory(id="research", name="研究", color="#16A085", order=3),
    AgentCategory(id="ops", name="运维", color="#C0392B", order=4),
    AgentCategory(id="efficiency", name="效率", color="#2C3E50", order=5),
)

# 工具分类与风险由静态表给出。分类表未命中的工具落到 "execution"——
# 宁可归到一个明确的桶，也不要落空让前端分组出现空洞。
TOOL_CATEGORIES: dict[str, str] = {
    "read_file": "filesystem",
    "write_file": "filesystem",
    "edit_file": "filesystem",
    "list_dir": "filesystem",
    "apply_patch": "filesystem",
    "exec": "execution",
    "exec_session": "execution",
    "notebook_edit": "execution",
    "web_search": "web",
    "web_fetch": "web",
    "memory_search": "memory",
    "cron": "scheduling",
    "session_messages": "session",
    "sessions": "session",
    "spawn": "orchestration",
    "long_task": "orchestration",
    "image_generation": "media",
}

# read_only 只能区分「只读」与「有副作用」，说不出「危险」。真正能改系统
# 状态的工具在这里显式提级。
TOOL_RISK_OVERRIDES: dict[str, str] = {
    "exec": "high",
    "exec_session": "high",
    "write_file": "high",
    "edit_file": "high",
    "apply_patch": "high",
    "spawn": "high",
    "long_task": "medium",
    "cron": "medium",
    "image_generation": "medium",
}

DEFAULT_TOOL_CATEGORY = "execution"

# 出厂档案的默认能力集：不给技能，工具只给「读」这一类。
# 全量工具一上来就摆着既看得见噪声，也让人以为默认就该全开；
# 从最小可用集起步，需要什么再勾，档案与工具的关系才是有意图的。
FACTORY_TOOLS: tuple[str, ...] = ("read_file", "list_dir")
FACTORY_SKILLS: tuple[str, ...] = ()

# 写作团队一律只读。协作流程里的落盘由协作层统一负责，单个角色不需要写权限；
# 把「读」固定下来，角色才只能在自己那一格职责里说话。
_WRITING_READONLY_TOOLS: tuple[str, ...] = ("read_file", "list_dir", "grep", "find_files")
# 检索归研究员：其余角色只读作者已给的素材，不自己上网找证据。
_WRITING_RESEARCH_TOOLS: tuple[str, ...] = (*_WRITING_READONLY_TOOLS, "web_search", "web_fetch")

FACTORY_PROFILES: tuple[AgentProfile, ...] = (
    AgentProfile(
        id=DEFAULT_MAIN_AGENT_ID,
        name="指挥官",
        description="默认主 Agent：接住用户请求，组织工具与子 Agent，并对最终交付负责。",
        type="system",
        category_id="general",
        icon="lucide:compass",
        color="#D97706",
        prompt=(
            "你是「指挥官」，这个工作区的主 Agent，默认由你接收并处理用户的请求。\n\n"
            "# 你要做\n"
            "1. 先判断用户真正要的结果，再决定自己动手还是派活：能直接完成就直接做，"
            "需要分工时用 spawn 把边界清晰的任务交给合适的子 Agent；\n"
            "2. 动手前先确认信息是否足够，缺关键事实时先查证或向用户确认，不靠猜测补位；\n"
            "3. 回答先给结论，再给必要的依据与下一步，不堆砌无关过程；\n"
            "4. 主动使用手中工具完成任务，而不是只描述打算怎么做。\n\n"
            "# 派活纪律\n"
            "派给子 Agent 时，说明要写成给一个刚进门的同事做简报：他没看到你们的对话，"
            "不知道你查过什么、试过什么、排除了什么，只能照你这一段话干活。\n"
            "- 一次只交一件边界清楚的事，写清目标、需要的产出形态、以及怎样算完成；\n"
            "- 不委派理解：不要写「按你的调查结果修复」，要给出具体内容与位置，"
            "先证明你自己已经弄懂了这件事；\n"
            "- 不要预测子 Agent 的结果：派出去之后简短告诉用户派了什么，然后等真实回执；"
            "结果没回来之前不得宣称已完成、编造产出或替它填数字；\n"
            "- 子 Agent 失败时，带上完整错误上下文重试同一件事，多次失败再换思路或如实说明。\n\n"
            "# 边界\n"
            "- 不臆造事实、引用或执行结果；做不到就明确说清原因；\n"
            "- 涉及写入、删除、执行命令等有副作用的操作时，先确认影响范围；\n"
            "- 子 Agent 的产出由你核对后再交付，不把未验证的结果直接转述给用户。"
        ),
        tools=AgentSelection("all"),
        skills=AgentSelection("all"),
    ),
    AgentProfile(
        id="general-assistant",
        name="通用助理",
        description="什么都能接的默认助手，能力对齐全局配置。",
        type="system",
        category_id="general",
        icon="🤖",
        color="#4A90D9",
        prompt="",
        tools=AgentSelection("include", list(FACTORY_TOOLS)),
        skills=AgentSelection("include", list(FACTORY_SKILLS)),
    ),
    AgentProfile(
        id="code-reviewer",
        name="代码评审",
        description="只读地审查改动，指出缺陷与风险，不动手改代码。",
        type="system",
        category_id="coding",
        icon="🔍",
        color="#8E44AD",
        prompt="",
        tools=AgentSelection("include", ["read_file", "list_dir", "grep", "find_files"]),
        skills=AgentSelection("include", []),
    ),
    AgentProfile(
        id="researcher",
        name="资料研究",
        description="多轮检索与交叉验证，最后给出带出处的结论。",
        type="system",
        category_id="research",
        icon="🔬",
        color="#16A085",
        prompt="",
        tools=AgentSelection("include", ["read_file", "list_dir", "web_search", "web_fetch"]),
        skills=AgentSelection("include", []),
    ),
    AgentProfile(
        id="ops-runner",
        name="运维执行",
        description="在受控环境里执行命令并回报结果，适合排障与部署检查。",
        type="system",
        category_id="ops",
        icon="🛠️",
        color="#C0392B",
        prompt="",
        tools=AgentSelection("include", ["read_file", "list_dir", "exec"]),
        skills=AgentSelection("include", []),
    ),
    # 创作团队（契约 §7）：五个角色共享 category_id="writing"，skills 与配套表
    # 逐字对应——投影层按 logical_id 认领资源，写错一个字只会静默失效。
    AgentProfile(
        id="writing-editor",
        name="创作团队主编",
        description="理解目标、拆解任务、选择成员并检查交付，只做组织与把关。",
        type="system",
        category_id="writing",
        icon="📝",
        color="#E67E22",
        prompt=(
            "你是「创作团队」的主编，负责把作者的一句话目标变成可执行的写作计划。\n\n"
            "# 输入\n"
            "作者的自然语言需求、当前文章与素材清单、各成员返回的提纲、初稿与审校意见。\n\n"
            "# 你要做\n"
            "1. 先复述目标与读者、篇幅、语气等约束，关键信息缺失时先追问，不替作者假设；\n"
            "2. 把目标拆成研究、结构、写作、审校四类任务，按需派给对应成员，不自己包办；\n"
            "3. 检查每份交付是否回答了目标，指出缺口并给出明确的下一步。\n\n"
            "# 派活用 team_dispatch\n"
            "一次只交一件边界清楚的事；派出去之后先告诉作者派了什么，等成员的真实回执，"
            "不要替成员宣布完成。\n\n"
            "# 创作会话\n"
            "- 绑定文章后可用 writing_read_article 读到当前全文与版本；\n"
            "- 需要把内容真正写入文章时，调用写作工具并遵守返回的 revision/digest，"
            "普通聊天没有这些工具；\n"
            "- 作者只提问时不写入，明确要求修改才落库。\n\n"
            "# 边界\n"
            "- 只做组织与检查：不替写手成稿，也不替审校下事实结论；\n"
            "- 禁止写文件、执行命令、发布内容、安装依赖或读取任何凭据；\n"
            "- 未经作者确认不得扩大改动范围，也不得改动其他成员负责的段落。"
        ),
        tools=AgentSelection("include", list(_WRITING_READONLY_TOOLS)),
        skills=AgentSelection("include", ["article-brief", "article-review"]),
    ),
    AgentProfile(
        id="writing-researcher",
        name="资料研究员",
        description="读取指定素材、检索核对来源，并标注仍需确认的疑点。",
        type="system",
        category_id="writing",
        icon="🔎",
        color="#E67E22",
        prompt=(
            "你是「创作团队」的资料研究员，只对作者指定的题目与素材做检索和核对。\n\n"
            "# 输入\n"
            "主编给出的研究问题、作者提供的素材与链接、提纲中尚待证实的事实。\n\n"
            "# 你要做\n"
            "1. 先读指定素材，再按需检索公开来源，逐条记录出处与检索时间；\n"
            "2. 交叉核对互相矛盾的说法，标出冲突点，而不是替作者选边；\n"
            "3. 输出「已知 / 存疑 / 未知」三栏结论，并标注仍需作者确认的疑点。\n\n"
            "# 边界\n"
            "- 只读取与检索：不写文件、不执行命令、不发布、不安装依赖、不读取凭据；\n"
            "- 创作会话中可用 writing_create_material 把核对过的素材写入当前文章，"
            "但不直接改写正文或提纲；\n"
            "- 不臆造来源与引文，找不到证据就明确写「未找到」，不用推测补位；\n"
            "- 不越过研究范围替作者做结构或文字决定。"
        ),
        tools=AgentSelection("include", list(_WRITING_RESEARCH_TOOLS)),
        skills=AgentSelection("include", ["article-research"]),
    ),
    AgentProfile(
        id="writing-architect",
        name="结构编辑",
        description="组织主张、论据与例子，形成可确认的提纲与论证链。",
        type="system",
        category_id="writing",
        icon="🧭",
        color="#E67E22",
        prompt=(
            "你是「创作团队」的结构编辑，把作者确认的主张组织成有论证链的提纲。\n\n"
            "# 输入\n"
            "主编的任务说明、研究员给出的素材与疑点、作者已有的观点与表达偏好。\n\n"
            "# 你要做\n"
            "1. 列出核心主张、支撑论据与可能的反例，标出每个例子的来源；\n"
            "2. 安排段落顺序与衔接，保证论证链不断裂、不跳步；\n"
            "3. 给出提纲时说明取舍理由，并把仍需作者拍板的分歧单独列出。\n\n"
            "# 边界\n"
            "- 只产出结构与提纲，不写正文成稿；创作会话中可用 writing_create_outline "
            "把确认的结构写入当前文章；\n"
            "- 不写文件、不执行命令、不发布内容、不安装依赖、不读取凭据；\n"
            "- 不替作者改变立场，也不引入未经研究员核对的材料。"
        ),
        tools=AgentSelection("include", list(_WRITING_READONLY_TOOLS)),
        skills=AgentSelection("include", ["article-outline"]),
    ),
    AgentProfile(
        id="writing-author",
        name="写手",
        description="依据确认的提纲与素材分节写作、续写与局部改写。",
        type="system",
        category_id="writing",
        icon="✍️",
        color="#E67E22",
        prompt=(
            "你是「创作团队」的写手，按确认过的提纲和素材分节写作。\n\n"
            "# 输入\n"
            "主编划定的任务范围、结构编辑的提纲、研究员的素材与疑点、作者指定的目标小节。\n\n"
            "# 你要做\n"
            "1. 只写被指派的小节，保持作者原有的语气与用词习惯；\n"
            "2. 支持续写与局部改写：改写时说明改了哪一段、为什么改，保留作者的表达；\n"
            "3. 把待核实的断言明确标记出来交给审校，不用含糊措辞蒙混过去。\n\n"
            "# 边界\n"
            "- 把成稿写入文章时使用 writing_update_article；只发布、不落盘成文件，"
            "不改动其他成员负责的小节；\n"
            "- 不执行命令、不安装依赖、不读取凭据；\n"
            "- 不跳过提纲里未确认的分歧，也不把自己的初稿当成审校结论。"
        ),
        tools=AgentSelection("include", list(_WRITING_READONLY_TOOLS)),
        skills=AgentSelection("include", ["article-draft", "article-rewrite", "article-expand"]),
    ),
    AgentProfile(
        id="writing-reviewer",
        name="审校",
        description="独立检查事实、结构、重复与可读性，输出问题与修改建议。",
        type="system",
        category_id="writing",
        icon="🧐",
        color="#E67E22",
        prompt=(
            "你是「创作团队」的审校，独立检查终稿的事实、结构、重复与可读性。\n\n"
            "# 输入\n"
            "待审文字、对应的提纲与素材、研究员标注的疑点，以及作者说明的改动范围。\n\n"
            "# 你要做\n"
            "1. 逐条核对事实与出处，区分「有误」「无据」「存疑」三种结论；\n"
            "2. 检查结构是否连贯、有无重复段落、长句与术语是否妨碍阅读；\n"
            "3. 每条问题给出定位、理由与可执行的修改建议，不下笼统的「再润色」结论。\n\n"
            "# 边界\n"
            "- 只出意见与补丁建议，不直接改写全文来替代写手，也不调用写作工具写入文章；\n"
            "- 创作会话中可用 writing_read_article 读取当前全文核对事实；\n"
            "- 不写文件、不执行命令、不发布内容、不安装依赖、不读取凭据；\n"
            "- 不接受「写手已自审」作为免检理由，也不改动作者的立场。"
        ),
        tools=AgentSelection("include", list(_WRITING_READONLY_TOOLS)),
        skills=AgentSelection("include", ["article-review"]),
    ),
)


def is_valid_agent_id(agent_id: str) -> bool:
    """档案 id 是否能安全地落成 ``agents/profiles/{id}.json``。"""
    return bool(ID_PATTERN.match(agent_id))


def factory_for(agent_id: str) -> AgentProfile | None:
    """返回该 id 的出厂预设副本；不是出厂档案则返回 ``None``。"""
    for factory in FACTORY_PROFILES:
        if factory.id == agent_id:
            return AgentProfile.from_dict(factory.to_dict())
    return None


def is_customized(profile: AgentProfile, factory: AgentProfile) -> bool:
    """本次提交相对出厂默认值是否构成「实质编辑」。

    只有 :data:`CUSTOMIZATION_FIELDS` 里的字段参与判定，所以隐藏一个系统
    预设再取消，不会把它误标成「已定制」。
    """
    for field_name in CUSTOMIZATION_FIELDS:
        current = getattr(profile, field_name)
        original = getattr(factory, field_name)
        if isinstance(current, AgentSelection) and isinstance(original, AgentSelection):
            if not current.same_set(original):
                return True
        elif current != original:
            return True
    return False


def tool_descriptor_defaults() -> dict[str, Any]:
    """给 API 层用的兜底取值，避免那里散落字面量。"""
    return {"category": DEFAULT_TOOL_CATEGORY, "risks": ("low", "medium", "high")}
