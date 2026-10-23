"""发起前的冻结准备：解析 Skill 完整正文与哈希、固定模型与来源范围（plan Task 6 / spec §13.4）。

唯一真相源：L4 契约 §1（batch ``frozen`` 字段）、§4（模型不可用与素材越界 message）、§7
（内置 Skill→角色配套）。本模块解决 spec §13.4 的缺口：``execute_frozen`` 只冻结
``ResolvedAgent``，Skill 摘要仍由 ``SkillsLoader`` 每次按名称实时重建、preset 也可能重新解析。
写作 preparation 在**发起前**把本批要用的 Skill 正文逐字内联并记录 ``sha256``，同时固定
``model_id`` 与本次明确可用的来源范围（勾选素材 + 实际 Skill 来源）。

拒付口径（全部在发起前报错，不降级、不回退默认值）：

- 禁用 / 缺失 / 依赖未满足 / 被角色档案排除的 Skill → :class:`SkillUnavailableError`；
- 显式失效的模型 preset → :class:`ModelUnavailableError`（message 即契约 §4 的原文，
  由调用方翻译为 HTTP 409）；
- 不属于该文章的素材 id → :class:`MaterialNotFoundError`（契约 §4 原文）。

模型可用性通过可注入的 ``model_available`` predicate 判定，本模块不硬编码任何供应商或型号。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nanobot.agents.catalog import FACTORY_PROFILES
from nanobot.agents.models import AgentSelection
from nanobot.agents.runtime import resolve_selection
from nanobot.writing.collab.recipe import TaskRecipe, validate_recipe

if TYPE_CHECKING:
    from nanobot.agent.skills import SkillsLoader

#: 模型可用性判定：给定 preset id，返回该模型当前是否真的可用。生产调用方注入真实解析器。
ModelAvailabilityPredicate = Callable[[str], bool]

#: 出厂档案的角色 → ``skills`` 选择（契约 §7 的 Skill→角色配套）。
_FACTORY_ROLE_SKILLS: dict[str, AgentSelection] = {
    profile.id: profile.skills for profile in FACTORY_PROFILES
}

_SOURCE_SCOPE_MATERIAL_IDS = "material_ids"
_SOURCE_SCOPE_SKILL_SOURCES = "skill_sources"


class PreparationError(ValueError):
    """冻结准备失败基类；``status`` 供 HTTP 层翻译，``field`` 指向出错的输入。"""

    status = 400
    field = ""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SkillUnavailableError(PreparationError):
    """Skill 不可用：禁用 / 缺失 / 依赖未满足 / 被角色档案排除；message 含技能名与原因。"""

    status = 409
    field = "skill"

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"writing skill is not available: {name} ({reason})")
        self.skill = name
        self.reason = reason


class ModelUnavailableError(PreparationError):
    """模型 preset 显式失效；message 即契约 §4 的 ``writing model is not available: <id>``。"""

    status = 409
    field = "model_id"

    def __init__(self, model_id: str) -> None:
        super().__init__(f"writing model is not available: {model_id}")
        self.model_id = model_id


class MaterialNotFoundError(PreparationError):
    """素材不属于该文章；message 即契约 §4 的 ``article material not found: <id>``。"""

    status = 400
    field = "material_ids"

    def __init__(self, material_id: str) -> None:
        super().__init__(f"article material not found: {material_id}")
        self.material_id = material_id


@dataclass(frozen=True)
class FrozenSkill:
    """一个被冻结的 Skill：实际路径、**完整正文**与正文 ``sha256``。

    正文只在冻结上下文里存续（批内同一任务不得在后台切换到不同 Skill 内容）；
    契约 §1 的 batch ``frozen`` 只持久化 ``name`` / ``path`` / ``sha256``，见 :meth:`to_dict`。
    """

    name: str
    path: str
    body: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        """契约 §1 的 ``frozen.skills[]`` 形状：不落正文，只落身份与哈希。"""
        return {"name": self.name, "path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class FrozenContext:
    """一次批次的冻结执行上下文（契约 §1 batch ``frozen``）。"""

    skills: tuple[FrozenSkill, ...]
    model_id: str
    source_scope: dict[str, Any]
    workspace_key: str
    article_id: str
    collaboration_id: str
    batch_id: str

    def to_dict(self) -> dict[str, Any]:
        """契约 §1 的 ``frozen`` JSON 形状，可直接写入 batch ``frozen_json``。"""
        return {
            "skills": [skill.to_dict() for skill in self.skills],
            "model_id": self.model_id,
            "source_scope": self.source_scope,
            "workspace_key": self.workspace_key,
            "article_id": self.article_id,
            "collaboration_id": self.collaboration_id,
            "batch_id": self.batch_id,
        }


def prepare_frozen(
    *,
    loader: SkillsLoader,
    recipe: TaskRecipe,
    article_snapshot: Mapping[str, Any],
    material_ids: Sequence[str],
    model_id: str,
    workspace_key: str,
    article_id: str,
    collaboration_id: str,
    batch_id: str,
    model_available: ModelAvailabilityPredicate | None = None,
    role_skills: Mapping[str, AgentSelection] | None = None,
) -> FrozenContext:
    """冻结本批次的 Skill 正文、模型与来源范围；任一前置条件不满足即明确报错。

    ``role_skills`` 允许调用方注入**实际**解析出来的角色 skill 选择（例如带工作区覆写的
    档案）；它按角色覆写出厂档案（契约 §7），未列出的角色仍用出厂身份。两者都按
    ``resolve_selection`` 的同一语义判定「该角色是否被允许看到这个 Skill」。
    ``model_available`` 为 ``None`` 表示调用方自行担保模型可用性（生产路径必须注入真实
    解析器），但显式失效的 preset 不会被默认模型顶替。
    """
    validate_recipe(recipe)

    resolved_model = model_id if isinstance(model_id, str) else ""
    if not resolved_model.strip():
        raise ModelUnavailableError(resolved_model)
    if model_available is not None and not model_available(resolved_model):
        raise ModelUnavailableError(resolved_model)

    entries = _skill_entries(loader)
    selections: dict[str, AgentSelection] = {
        **_FACTORY_ROLE_SKILLS,
        **({} if role_skills is None else role_skills),
    }

    skills = tuple(
        _freeze_skill(loader, recipe, name, entries, selections) for name in recipe.skills
    )
    return FrozenContext(
        skills=skills,
        model_id=resolved_model,
        source_scope={
            _SOURCE_SCOPE_MATERIAL_IDS: _selected_material_ids(article_snapshot, material_ids),
            _SOURCE_SCOPE_SKILL_SOURCES: sorted({entries[name]["source"] for name in recipe.skills}),
        },
        workspace_key=workspace_key,
        article_id=article_id,
        collaboration_id=collaboration_id,
        batch_id=batch_id,
    )


def _skill_entries(loader: SkillsLoader) -> dict[str, dict[str, str]]:
    """本次实际生效的 Skill 目录：工作区同名遮蔽内置、禁用项已剔除。"""
    return {entry["name"]: entry for entry in loader.list_skills(filter_unavailable=False)}


def _freeze_skill(
    loader: SkillsLoader,
    recipe: TaskRecipe,
    name: str,
    entries: Mapping[str, dict[str, str]],
    selections: Mapping[str, AgentSelection],
) -> FrozenSkill:
    if name in loader.disabled_skills:
        raise SkillUnavailableError(name, "disabled")
    entry = entries.get(name)
    if entry is None:
        raise SkillUnavailableError(name, "not found")
    # 只对配方内这几项查依赖，避免为整本 Skill 目录做一遍全文解析。
    available, missing = loader.get_skill_availability(name)
    if not available:
        raise SkillUnavailableError(
            name, f"missing requirements: {missing}" if missing else "requirements are not met"
        )
    if not _profile_allows(recipe, name, entries, selections):
        raise SkillUnavailableError(name, "excluded by agent profile")

    body = loader.load_skill(name)
    if body is None:
        raise SkillUnavailableError(name, "not found")
    path = Path(entry["path"])
    try:
        full_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillUnavailableError(name, f"unreadable: {exc.strerror or exc}") from exc
    if body != full_text:
        # 摘要/仅名称会被这里挡住：冻结的必须是磁盘上的完整正文。
        raise SkillUnavailableError(name, "loaded body is not the full skill text")
    return FrozenSkill(
        name=name,
        path=str(path),
        body=body,
        sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )


def _profile_allows(
    recipe: TaskRecipe,
    name: str,
    entries: Mapping[str, dict[str, str]],
    selections: Mapping[str, AgentSelection],
) -> bool:
    """Skill 至少要被配方内一个角色（含主编）的档案选择放行，否则该 Skill 无人可用。"""
    known = list(entries)
    for role_id in (recipe.lead_role, *recipe.roles):
        selection = selections.get(role_id)
        if selection is not None and name in resolve_selection(selection, known):
            return True
    return False


def _selected_material_ids(
    article_snapshot: Mapping[str, Any], material_ids: Sequence[str]
) -> list[str]:
    """本次明确勾选的素材 id（保持作者给出的顺序，去重）；越界素材一律拒绝。"""
    known: set[str] = set()
    materials: Any = article_snapshot.get("materials") or []
    if isinstance(materials, Sequence) and not isinstance(materials, (str, bytes)):
        for item in materials:
            if isinstance(item, Mapping):
                material_id = item.get("id")
                if isinstance(material_id, str) and material_id:
                    known.add(material_id)
    selected: list[str] = []
    for raw in material_ids:
        if not isinstance(raw, str) or raw not in known:
            raise MaterialNotFoundError(raw if isinstance(raw, str) else str(raw))
        if raw not in selected:
            selected.append(raw)
    return selected
