"""契约 §1 的 8 个写作动作 → 参与角色 / 主编 / 产物类型 / Skill 的固定配方，以及 §9 批次限额。

唯一真相源：L4 契约 §1（action / artifact.kind / artifact.role_id 枚举）、§7（内置 Skill→
角色配套）、§9（批次与限额）。本模块只**声明**与**校验**配方，不解析 Skill 正文、不绑定模型、
不落库——那是 :mod:`nanobot.writing.collab.preparation` 与协作运行器的职责。

``roles`` 的口径
---------------

``roles`` 是**可派成员任务的成员角色白名单**，不含主编：``nanobot/teams/models.py::_check_members``
规定「leaderAgentId must not also be a member」，固定创作团队（契约 §7 ``writing-team``）的
主编是 ``writing-editor``，四个成员是研究员 / 结构编辑 / 写手 / 审校。因此 ``lead_role`` 对每个
动作都是主编，而 ``roles`` 只列允许收到成员任务的角色（``ideate`` 由主编直接产出 brief，
``roles`` 为空元组）。``MAX_MEMBERS = 5`` 即「1 名主编 + 最多 4 名成员」。

配方外角色、产物类型与 Skill 一律拒绝，异常 message 必含非法值（契约 §4 同款错误体口径）。
"""

from __future__ import annotations

from dataclasses import dataclass

from nanobot.writing.collab.models import ACTIONS, ARTIFACT_KINDS

# ---- 固定团队角色（契约 §1 artifact.role_id / §7） --------------------------

EDITOR_ROLE = "writing-editor"
RESEARCHER_ROLE = "writing-researcher"
ARCHITECT_ROLE = "writing-architect"
AUTHOR_ROLE = "writing-author"
REVIEWER_ROLE = "writing-reviewer"

#: 固定团队全部角色；主编 ``LEAD_ROLE`` 由契约 §7 的 ``writing-team`` 固定。
WRITING_ROLES: tuple[str, ...] = (
    EDITOR_ROLE,
    RESEARCHER_ROLE,
    ARCHITECT_ROLE,
    AUTHOR_ROLE,
    REVIEWER_ROLE,
)
#: 可派成员任务的角色（团队 leader 不能同时是成员）。
MEMBER_ROLES: tuple[str, ...] = (
    RESEARCHER_ROLE,
    ARCHITECT_ROLE,
    AUTHOR_ROLE,
    REVIEWER_ROLE,
)
#: 固定团队主编（契约 §7：``writing-editor`` 是「创作团队主编」）。
LEAD_ROLE = EDITOR_ROLE

# ---- 内置 Skill（契约 §7） --------------------------------------------------

BRIEF_SKILL = "article-brief"
RESEARCH_SKILL = "article-research"
OUTLINE_SKILL = "article-outline"
DRAFT_SKILL = "article-draft"
REWRITE_SKILL = "article-rewrite"
EXPAND_SKILL = "article-expand"
REVIEW_SKILL = "article-review"

WRITING_SKILLS: tuple[str, ...] = (
    BRIEF_SKILL,
    RESEARCH_SKILL,
    OUTLINE_SKILL,
    DRAFT_SKILL,
    REWRITE_SKILL,
    EXPAND_SKILL,
    REVIEW_SKILL,
)

# ---- action（契约 §1） ------------------------------------------------------

ACTION_START_FULL = "start_full"
ACTION_IDEATE = "ideate"
ACTION_RESEARCH = "research"
ACTION_OUTLINE = "outline"
ACTION_DRAFT = "draft"
ACTION_REWRITE_SELECTION = "rewrite_selection"
ACTION_REVIEW_ALL = "review_all"
ACTION_REPLY = "reply"

# ---- 批次与限额（契约 §9） --------------------------------------------------

#: 团队规模上限：1 名主编 + 4 名成员（契约 §7 五个写作档案）。
MAX_MEMBERS = 5
#: 单次自动批次最多派出的成员任务数。
MAX_MEMBER_TASKS_PER_BATCH = 8
#: 自动派工轮数上限（主编主动追加派工），超过即收口。
MAX_AUTO_DISPATCH_ROUNDS = 2
#: 单个成员任务超时（秒）；作者等待时间不计入模型批次超时。
MEMBER_TIMEOUT_SECONDS = 300
#: 单次自动批次超时（秒）。
BATCH_TIMEOUT_SECONDS = 900
#: 同时运行的成员任务数上限。
MAX_CONCURRENT_MEMBERS = 2


class RecipeError(ValueError):
    """配方违规基类：``field`` / ``value`` / ``action`` 可判定，``status`` 供 HTTP 层翻译。"""

    status = 400
    field = ""

    def __init__(self, message: str, *, action: str = "", value: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.action = action
        self.value = value


class UnknownActionError(RecipeError):
    """动作不在契约 §1 的 8 个 action 内；message 与契约 §4 同款（HTTP 404）。"""

    status = 404
    field = "action"

    def __init__(self, action: str) -> None:
        super().__init__(
            f"unknown writing collaboration action: {action}", action=action, value=action
        )


class UnknownRoleError(RecipeError):
    """角色不是该动作配方内的参与角色。"""

    field = "role"

    def __init__(self, action: str, role: str) -> None:
        super().__init__(
            f"writing role is not allowed for action={action}: {role}", action=action, value=role
        )


class UnsupportedArtifactKindError(RecipeError):
    """产物类型不在该动作配方的白名单内。"""

    field = "kind"

    def __init__(self, action: str, kind: str) -> None:
        super().__init__(
            f"writing artifact kind is not allowed for action={action}: {kind}",
            action=action,
            value=kind,
        )


class UnsupportedSkillError(RecipeError):
    """Skill 不在该动作配方的白名单内。"""

    field = "skill"

    def __init__(self, action: str, skill: str) -> None:
        super().__init__(
            f"writing skill is not allowed for action={action}: {skill}",
            action=action,
            value=skill,
        )


class RecipeDefinitionError(RecipeError):
    """配方表自身不合法（开发期错误，HTTP 500）。"""

    status = 500


@dataclass(frozen=True)
class TaskRecipe:
    """一个写作动作的固定配方。

    ``roles`` 只列可派成员任务的成员角色（不含主编 ``lead_role``）；``artifact_kinds`` 与
    ``skills`` 是该动作允许出现的产物类型与 Skill 白名单（配方外交付一律拒绝）。
    """

    action: str
    roles: tuple[str, ...]
    lead_role: str
    artifact_kinds: tuple[str, ...]
    skills: tuple[str, ...]


#: 契约 §1 的 8 个 action → 配方（顺序即枚举顺序）。
RECIPES: dict[str, TaskRecipe] = {
    ACTION_START_FULL: TaskRecipe(
        action=ACTION_START_FULL,
        roles=(RESEARCHER_ROLE, ARCHITECT_ROLE, AUTHOR_ROLE, REVIEWER_ROLE),
        lead_role=LEAD_ROLE,
        artifact_kinds=("brief", "material_list", "outline", "draft", "review"),
        skills=WRITING_SKILLS,
    ),
    ACTION_IDEATE: TaskRecipe(
        action=ACTION_IDEATE,
        roles=(),
        lead_role=LEAD_ROLE,
        artifact_kinds=("brief",),
        skills=(BRIEF_SKILL,),
    ),
    ACTION_RESEARCH: TaskRecipe(
        action=ACTION_RESEARCH,
        roles=(RESEARCHER_ROLE,),
        lead_role=LEAD_ROLE,
        artifact_kinds=("material_list",),
        skills=(RESEARCH_SKILL,),
    ),
    ACTION_OUTLINE: TaskRecipe(
        action=ACTION_OUTLINE,
        roles=(ARCHITECT_ROLE,),
        lead_role=LEAD_ROLE,
        artifact_kinds=("outline",),
        skills=(OUTLINE_SKILL,),
    ),
    ACTION_DRAFT: TaskRecipe(
        action=ACTION_DRAFT,
        roles=(AUTHOR_ROLE,),
        lead_role=LEAD_ROLE,
        artifact_kinds=("draft",),
        skills=(DRAFT_SKILL,),
    ),
    ACTION_REWRITE_SELECTION: TaskRecipe(
        action=ACTION_REWRITE_SELECTION,
        roles=(AUTHOR_ROLE,),
        lead_role=LEAD_ROLE,
        artifact_kinds=("rewrite",),
        skills=(REWRITE_SKILL,),
    ),
    ACTION_REVIEW_ALL: TaskRecipe(
        action=ACTION_REVIEW_ALL,
        roles=(REVIEWER_ROLE, RESEARCHER_ROLE),
        lead_role=LEAD_ROLE,
        artifact_kinds=("review",),
        skills=(REVIEW_SKILL,),
    ),
    ACTION_REPLY: TaskRecipe(
        # reply 按当前阶段收窄：静态配方放行全部成员角色与各阶段产物类型。
        action=ACTION_REPLY,
        roles=MEMBER_ROLES,
        lead_role=LEAD_ROLE,
        artifact_kinds=("brief", "material_list", "outline", "draft", "rewrite", "review"),
        skills=WRITING_SKILLS,
    ),
}


def recipe_for(action: str) -> TaskRecipe:
    """取动作配方；未知动作抛 :class:`UnknownActionError`（message 含非法值）。"""
    recipe = RECIPES.get(action)
    if recipe is None:
        raise UnknownActionError(action)
    return recipe


def _participants(recipe: TaskRecipe) -> tuple[str, ...]:
    """配方内的参与角色：主编（主编不派成员任务，但会亲自产出 brief 等）。"""
    return (recipe.lead_role, *recipe.roles)


def require_role(action: str, role: str) -> None:
    """角色必须是该动作配方内的参与角色，否则 :class:`UnknownRoleError`。"""
    recipe = recipe_for(action)
    if role not in _participants(recipe):
        raise UnknownRoleError(action, role)


def require_artifact_kind(action: str, kind: str) -> None:
    """产物类型必须在该动作配方的白名单内，否则 :class:`UnsupportedArtifactKindError`。"""
    recipe = recipe_for(action)
    if kind not in recipe.artifact_kinds:
        raise UnsupportedArtifactKindError(action, kind)


def require_skill(action: str, skill: str) -> None:
    """Skill 必须在该动作配方的白名单内，否则 :class:`UnsupportedSkillError`。"""
    recipe = recipe_for(action)
    if skill not in recipe.skills:
        raise UnsupportedSkillError(action, skill)


def validate_recipe(recipe: TaskRecipe) -> None:
    """配方表的静态自检：动作、角色、产物类型、Skill 与团队规模上限。

    只做**表内一致性**检查（不查 Skill 存储、档案白名单与模型），因此可在导入后随时调用。
    """
    if recipe.action not in ACTIONS:
        raise RecipeDefinitionError(
            f"unknown writing collaboration action: {recipe.action}",
            action=recipe.action,
            value=recipe.action,
        )
    if recipe.lead_role != LEAD_ROLE:
        raise RecipeDefinitionError(
            f"writing recipe lead_role must be {LEAD_ROLE}: {recipe.lead_role}",
            action=recipe.action,
            value=recipe.lead_role,
        )
    if recipe.lead_role in recipe.roles:
        raise RecipeDefinitionError(
            f"writing team leader must not be a member: {recipe.lead_role}",
            action=recipe.action,
            value=recipe.lead_role,
        )
    if len(recipe.roles) + 1 > MAX_MEMBERS:
        raise RecipeDefinitionError(
            f"writing recipe exceeds MAX_MEMBERS={MAX_MEMBERS}: {len(recipe.roles) + 1}",
            action=recipe.action,
            value=str(len(recipe.roles) + 1),
        )
    _require_unique(recipe, "role", recipe.roles, MEMBER_ROLES)
    _require_unique(recipe, "artifact kind", recipe.artifact_kinds, ARTIFACT_KINDS)
    if not recipe.roles and not recipe.artifact_kinds:
        raise RecipeDefinitionError(
            "writing recipe must declare artifact kinds", action=recipe.action
        )
    if not recipe.skills:
        raise RecipeDefinitionError(
            "writing recipe must declare skills", action=recipe.action
        )
    _require_unique(recipe, "skill", recipe.skills, WRITING_SKILLS)


def _require_unique(
    recipe: TaskRecipe,
    field: str,
    values: tuple[str, ...],
    allowed: tuple[str, ...],
) -> None:
    if len(set(values)) != len(values):
        raise RecipeDefinitionError(
            f"writing recipe has duplicate {field}: {values}", action=recipe.action
        )
    for value in values:
        if value not in allowed:
            raise RecipeDefinitionError(
                f"writing recipe has unknown {field}: {value}", action=recipe.action, value=value
            )
