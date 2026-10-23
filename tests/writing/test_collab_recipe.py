"""WU-06：写作任务配方（契约 §1/§7/§9）与发起前冻结准备（plan Task 6）。

契约依据：L4 契约 §1（8 个 action 与 artifact.kind 枚举）、§4（错误 message 与 status）、
§7（内置 Skill→角色配套）、§9（批次与限额）。plan Task 6 冻结了 `TaskRecipe` / `RECIPES` /
六个限额常量 / `FrozenContext` / `prepare_frozen` 的接口形状。

`roles` 与 `lead_role` 的口径：`nanobot/teams/models.py::_check_members` 规定
「leaderAgentId must not also be a member」，固定团队的主编（`lead_role` =
`writing-editor`）不是成员，所以 `roles` 只列**可派成员任务**的角色白名单，主编不占名额。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from nanobot.agent.skills import BUILTIN_SKILLS_DIR, SkillsLoader
from nanobot.agents.models import AgentSelection
from nanobot.writing.collab.models import ACTIONS
from nanobot.writing.collab.preparation import (
    FrozenContext,
    FrozenSkill,
    MaterialNotFoundError,
    ModelUnavailableError,
    SkillUnavailableError,
    prepare_frozen,
)
from nanobot.writing.collab.recipe import (
    ACTION_IDEATE,
    ACTION_RESEARCH,
    ACTION_REVIEW_ALL,
    ACTION_REWRITE_SELECTION,
    ACTION_START_FULL,
    AUTHOR_ROLE,
    BATCH_TIMEOUT_SECONDS,
    EDITOR_ROLE,
    MAX_AUTO_DISPATCH_ROUNDS,
    MAX_CONCURRENT_MEMBERS,
    MAX_MEMBER_TASKS_PER_BATCH,
    MAX_MEMBERS,
    MEMBER_ROLES,
    MEMBER_TIMEOUT_SECONDS,
    RECIPES,
    RESEARCHER_ROLE,
    REVIEWER_ROLE,
    TaskRecipe,
    UnknownActionError,
    UnknownRoleError,
    UnsupportedArtifactKindError,
    UnsupportedSkillError,
    recipe_for,
    require_artifact_kind,
    require_role,
    require_skill,
    validate_recipe,
)

# 契约 §1 逐字冻结的 8 个 action，顺序即持久化枚举顺序。
_CONTRACT_ACTIONS = (
    "start_full",
    "ideate",
    "research",
    "outline",
    "draft",
    "rewrite_selection",
    "review_all",
    "reply",
)

_BRIEF_SKILL = "article-brief"
_CONTRACT_FROZEN_KEYS = {
    "skills",
    "model_id",
    "source_scope",
    "workspace_key",
    "article_id",
    "collaboration_id",
    "batch_id",
}
_CONTRACT_SKILL_KEYS = {"name", "path", "sha256"}


def _loader(workspace: Path, *, disabled: set[str] | None = None) -> SkillsLoader:
    return SkillsLoader(workspace, disabled_skills=disabled or set())


def _write_skill(workspace: Path, name: str, body: str) -> Path:
    """在 tmp_path 造一个工作区 Skill；返回 SKILL.md 路径。"""
    target = workspace / "skills" / name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"---\nname: {name}\ndescription: 测试用技能，验证正文与哈希冻结口径。\n---\n\n{body}",
        encoding="utf-8",
    )
    return target


def _material(material_id: str) -> dict[str, str]:
    return {
        "id": material_id,
        "title": f"素材 {material_id}",
        "content": f"{material_id} 的内容",
        "url": "",
        "source": "",
        "note": "",
        "language": "",
    }


def _snapshot(materials: Sequence[Mapping[str, str]] = ()) -> dict[str, object]:
    """文章快照（`nanobot.writing.models.normalize_article_fields` 的键口径）。"""
    return {
        "title": "测试文章",
        "status": "draft",
        "summary": "",
        "brief": "",
        "body": "已有正文",
        "outline": [],
        "materials": [dict(item) for item in materials],
        "tags": [],
    }


def _prepare(
    loader: SkillsLoader,
    recipe: TaskRecipe,
    *,
    snapshot: Mapping[str, object] | None = None,
    material_ids: Sequence[str] = (),
    model_id: str = "openai/gpt-test",
    model_available: Callable[[str], bool] | None = None,
    role_skills: Mapping[str, AgentSelection] | None = None,
    workspace_key: str = "wk-1",
    article_id: str = "art-1",
    collaboration_id: str = "col-1",
    batch_id: str = "batch-1",
) -> FrozenContext:
    return prepare_frozen(
        loader=loader,
        recipe=recipe,
        article_snapshot=_snapshot() if snapshot is None else snapshot,
        material_ids=list(material_ids),
        model_id=model_id,
        model_available=model_available,
        role_skills=role_skills,
        workspace_key=workspace_key,
        article_id=article_id,
        collaboration_id=collaboration_id,
        batch_id=batch_id,
    )


# --------------------------------------------------------------------------- 配方


def test_recipe_rewrite_selection_only_author() -> None:
    recipe = RECIPES[ACTION_REWRITE_SELECTION]
    assert recipe.roles == (AUTHOR_ROLE,)
    assert set(recipe.artifact_kinds) <= {"rewrite"}
    assert set(recipe.skills) <= {"article-draft", "article-rewrite", "article-expand"}
    validate_recipe(recipe)


def test_recipe_review_all_includes_reviewer() -> None:
    recipe = RECIPES[ACTION_REVIEW_ALL]
    assert REVIEWER_ROLE in recipe.roles
    assert set(recipe.roles) <= {REVIEWER_ROLE, RESEARCHER_ROLE}
    assert "review" in recipe.artifact_kinds


def test_recipe_full_creation_stages() -> None:
    recipe = RECIPES[ACTION_START_FULL]
    # research/outline/draft/review 四类产物（material_list/outline/draft/review）全覆盖
    assert {"material_list", "outline", "draft", "review"} <= set(recipe.artifact_kinds)
    assert {RESEARCHER_ROLE, "writing-architect", AUTHOR_ROLE, REVIEWER_ROLE} <= set(recipe.roles)
    assert recipe.lead_role == EDITOR_ROLE
    validate_recipe(recipe)


def test_recipe_single_purpose_actions_map_to_contract_kinds() -> None:
    assert RECIPES[ACTION_IDEATE].artifact_kinds == ("brief",)
    assert RECIPES[ACTION_RESEARCH].artifact_kinds == ("material_list",)
    assert RECIPES["outline"].artifact_kinds == ("outline",)
    assert RECIPES["draft"].artifact_kinds == ("draft",)
    assert RECIPES[ACTION_REVIEW_ALL].artifact_kinds == ("review",)


def test_recipe_reply_follows_stage_with_editor_lead() -> None:
    recipe = RECIPES["reply"]
    assert recipe.lead_role == EDITOR_ROLE
    # 按当前阶段收窄：静态配方必须放行所有阶段可能用到的角色与产物类型
    assert set(recipe.roles) == set(MEMBER_ROLES)
    assert {"material_list", "outline", "draft", "review"} <= set(recipe.artifact_kinds)


def test_recipe_covers_all_contract_actions() -> None:
    assert set(RECIPES) == set(_CONTRACT_ACTIONS)
    # 与协作 store 的 action 枚举逐字一致（跨 WU 契约对账）
    assert set(RECIPES) == set(ACTIONS)
    for action, recipe in RECIPES.items():
        assert recipe.action == action
        validate_recipe(recipe)


def test_recipe_lead_role_is_fixed_editor_and_not_a_member() -> None:
    for recipe in RECIPES.values():
        assert recipe.lead_role == EDITOR_ROLE
        assert EDITOR_ROLE not in recipe.roles


def test_unknown_role_rejected() -> None:
    with pytest.raises(UnknownRoleError) as rejected:
        require_role(ACTION_REWRITE_SELECTION, REVIEWER_ROLE)
    assert REVIEWER_ROLE in str(rejected.value)
    assert rejected.value.value == REVIEWER_ROLE
    assert rejected.value.field == "role"
    assert rejected.value.status == 400

    with pytest.raises(UnknownRoleError):
        require_role(ACTION_START_FULL, "writing-ghost")

    # 配方内角色（含主编）放行
    require_role(ACTION_REWRITE_SELECTION, AUTHOR_ROLE)
    require_role(ACTION_REWRITE_SELECTION, EDITOR_ROLE)


def test_unknown_artifact_kind_rejected() -> None:
    with pytest.raises(UnsupportedArtifactKindError) as rejected:
        require_artifact_kind(ACTION_REWRITE_SELECTION, "review")
    assert "review" in str(rejected.value)
    assert rejected.value.value == "review"

    with pytest.raises(UnknownActionError) as rejected:
        recipe_for("write_everything")
    assert str(rejected.value) == "unknown writing collaboration action: write_everything"
    assert rejected.value.status == 404


def test_unknown_skill_rejected() -> None:
    with pytest.raises(UnsupportedSkillError) as rejected:
        require_skill(ACTION_REWRITE_SELECTION, "article-review")
    assert "article-review" in str(rejected.value)


def test_limits_match_contract() -> None:
    assert MAX_MEMBERS == 5
    assert MAX_MEMBER_TASKS_PER_BATCH == 8
    assert MAX_AUTO_DISPATCH_ROUNDS == 2
    assert MEMBER_TIMEOUT_SECONDS == 300
    assert BATCH_TIMEOUT_SECONDS == 900
    assert MAX_CONCURRENT_MEMBERS == 2
    for recipe in RECIPES.values():
        assert len(recipe.roles) + 1 <= MAX_MEMBERS


# --------------------------------------------------------------------------- 冻结准备


def test_prepare_inlines_skill_body_and_hash(tmp_path: Path) -> None:
    body = "## 用途\n\n" + ("逐字正文证据 " * 40)
    skill_file = _write_skill(tmp_path, _BRIEF_SKILL, body)

    frozen = _prepare(_loader(tmp_path), RECIPES[ACTION_IDEATE])

    assert isinstance(frozen, FrozenContext)
    assert len(frozen.skills) == 1
    skill = frozen.skills[0]
    assert isinstance(skill, FrozenSkill)
    assert skill.name == _BRIEF_SKILL
    assert skill.path == str(skill_file)
    # 正文逐字等于文件内容（摘要或仅名称都会在这里失败）
    assert skill.body == skill_file.read_text(encoding="utf-8")
    assert "逐字正文证据" in skill.body
    assert skill.sha256 == hashlib.sha256(skill.body.encode("utf-8")).hexdigest()
    assert frozen.source_scope["skill_sources"] == ["workspace"]


def test_prepare_uses_builtin_full_body_not_summary(tmp_path: Path) -> None:
    frozen = _prepare(_loader(tmp_path), RECIPES[ACTION_IDEATE])

    skill = frozen.skills[0]
    builtin_file = BUILTIN_SKILLS_DIR / _BRIEF_SKILL / "SKILL.md"
    assert skill.path == str(builtin_file)
    assert skill.body == builtin_file.read_text(encoding="utf-8")
    assert skill.sha256 == hashlib.sha256(skill.body.encode("utf-8")).hexdigest()
    # 内置正文是完整文档，不是 description 摘要
    assert len(skill.body) > 1000
    assert "## 用途" in skill.body
    assert frozen.source_scope["skill_sources"] == ["builtin"]


def test_prepare_freezes_recipe_skill_order(tmp_path: Path) -> None:
    frozen = _prepare(_loader(tmp_path), RECIPES[ACTION_START_FULL])
    assert [skill.name for skill in frozen.skills] == list(RECIPES[ACTION_START_FULL].skills)
    assert frozen.source_scope["skill_sources"] == ["builtin"]


def test_prepare_rejects_disabled_or_missing_skill(tmp_path: Path) -> None:
    _write_skill(tmp_path, _BRIEF_SKILL, "正文")

    disabled_loader = _loader(tmp_path, disabled={_BRIEF_SKILL})
    with pytest.raises(SkillUnavailableError) as disabled:
        _prepare(disabled_loader, RECIPES[ACTION_IDEATE])
    assert _BRIEF_SKILL in str(disabled.value)
    assert "disabled" in str(disabled.value)
    assert disabled.value.status == 409

    empty = tmp_path / "no-skills"
    empty.mkdir()
    missing_loader = SkillsLoader(empty, builtin_skills_dir=empty / "builtin")
    with pytest.raises(SkillUnavailableError) as missing:
        _prepare(missing_loader, RECIPES[ACTION_IDEATE])
    assert _BRIEF_SKILL in str(missing.value)
    assert "not found" in str(missing.value)


def test_prepare_rejects_skill_with_unmet_requirements(tmp_path: Path) -> None:
    target = tmp_path / "skills" / _BRIEF_SKILL / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\n"
        f"name: {_BRIEF_SKILL}\n"
        "description: 依赖未满足的测试技能。\n"
        'metadata: {"nanobot": {"requires": {"bins": ["nanobot-definitely-missing-bin"]}}}\n'
        "---\n\n正文\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillUnavailableError) as rejected:
        _prepare(_loader(tmp_path), RECIPES[ACTION_IDEATE])
    assert _BRIEF_SKILL in str(rejected.value)
    assert "missing requirements" in str(rejected.value)
    assert "nanobot-definitely-missing-bin" in str(rejected.value)


def test_prepare_rejects_skill_excluded_by_agent_profile(tmp_path: Path) -> None:
    # article-outline 只归结构编辑：资料研究员与主编的档案都不放行它
    recipe = TaskRecipe(
        action=ACTION_RESEARCH,
        roles=(RESEARCHER_ROLE,),
        lead_role=EDITOR_ROLE,
        artifact_kinds=("material_list",),
        skills=("article-outline",),
    )
    with pytest.raises(SkillUnavailableError) as rejected:
        _prepare(_loader(tmp_path), recipe)
    assert "article-outline" in str(rejected.value)
    assert "profile" in str(rejected.value)


def test_prepare_honours_injected_role_skill_selections(tmp_path: Path) -> None:
    """调用方可按角色覆写实际解析出的档案选择；覆写排除后必须拒绝，不回退出厂档案。"""
    locked = {
        EDITOR_ROLE: AgentSelection("include", []),
        RESEARCHER_ROLE: AgentSelection("include", []),
    }
    with pytest.raises(SkillUnavailableError) as rejected:
        _prepare(_loader(tmp_path), RECIPES[ACTION_IDEATE], role_skills=locked)
    assert _BRIEF_SKILL in str(rejected.value)
    assert "excluded by agent profile" in str(rejected.value)

    # 同一配方在出厂档案（契约 §7）下可用，证明拒绝来自注入的选择而非技能缺失
    default = _prepare(_loader(tmp_path), RECIPES[ACTION_IDEATE])
    assert [skill.name for skill in default.skills] == [_BRIEF_SKILL]


def test_prepare_rejects_unavailable_model(tmp_path: Path) -> None:
    seen: list[str] = []

    def _unavailable(model_id: str) -> bool:
        seen.append(model_id)
        return False

    with pytest.raises(ModelUnavailableError) as rejected:
        _prepare(
            _loader(tmp_path),
            RECIPES[ACTION_IDEATE],
            model_id="openai/gpt-5",
            model_available=_unavailable,
        )
    assert str(rejected.value) == "writing model is not available: openai/gpt-5"
    assert rejected.value.model_id == "openai/gpt-5"
    assert rejected.value.status == 409
    assert seen == ["openai/gpt-5"]


def test_prepare_accepts_available_model_and_records_actual_id(tmp_path: Path) -> None:
    seen: list[str] = []

    def _available(model_id: str) -> bool:
        seen.append(model_id)
        return True

    frozen = _prepare(
        _loader(tmp_path),
        RECIPES[ACTION_IDEATE],
        model_id="anthropic/claude-sonnet",
        model_available=_available,
    )
    assert seen == ["anthropic/claude-sonnet"]
    assert frozen.model_id == "anthropic/claude-sonnet"


def test_prepare_scope_excludes_unselected_materials(tmp_path: Path) -> None:
    snapshot = _snapshot([_material("m1"), _material("m2"), _material("m3")])

    frozen = _prepare(
        _loader(tmp_path),
        RECIPES[ACTION_IDEATE],
        snapshot=snapshot,
        material_ids=["m3", "m1"],
    )

    assert frozen.source_scope["material_ids"] == ["m3", "m1"]
    serialized = json.dumps(frozen.source_scope, ensure_ascii=False)
    assert "m2" not in serialized
    assert "素材 m2" not in serialized

    no_materials = _prepare(_loader(tmp_path), RECIPES[ACTION_IDEATE], snapshot=snapshot)
    assert no_materials.source_scope["material_ids"] == []


def test_prepare_rejects_material_outside_article(tmp_path: Path) -> None:
    snapshot = _snapshot([_material("m1")])
    with pytest.raises(MaterialNotFoundError) as rejected:
        _prepare(
            _loader(tmp_path),
            RECIPES[ACTION_IDEATE],
            snapshot=snapshot,
            material_ids=["m9"],
        )
    assert str(rejected.value) == "article material not found: m9"
    assert rejected.value.status == 400


def test_prepare_freezes_identity_and_payload(tmp_path: Path) -> None:
    frozen = _prepare(
        _loader(tmp_path),
        RECIPES[ACTION_IDEATE],
        model_id="anthropic/claude-sonnet",
        workspace_key="wk-9",
        article_id="art-9",
        collaboration_id="col-9",
        batch_id="batch-9",
    )

    assert frozen.workspace_key == "wk-9"
    assert frozen.article_id == "art-9"
    assert frozen.collaboration_id == "col-9"
    assert frozen.batch_id == "batch-9"

    payload = frozen.to_dict()
    assert set(payload) == _CONTRACT_FROZEN_KEYS
    assert payload["source_scope"] == frozen.source_scope
    assert payload["model_id"] == "anthropic/claude-sonnet"
    assert payload["skills"] == [
        {
            "name": _BRIEF_SKILL,
            "path": str(BUILTIN_SKILLS_DIR / _BRIEF_SKILL / "SKILL.md"),
            "sha256": frozen.skills[0].sha256,
        }
    ]
    for entry in payload["skills"]:
        assert set(entry) == _CONTRACT_SKILL_KEYS


def test_prepare_prefers_workspace_skill_over_builtin(tmp_path: Path) -> None:
    path = _write_skill(tmp_path, _BRIEF_SKILL, "工作区同名技能必须遮蔽内置正文。")

    frozen = _prepare(_loader(tmp_path), RECIPES[ACTION_IDEATE])

    assert frozen.skills[0].path == str(path)
    assert "工作区同名技能必须遮蔽内置正文。" in frozen.skills[0].body
    assert frozen.source_scope["skill_sources"] == ["workspace"]
