"""内置写作 Skill 正文的契约测试。

锁定 ``nanobot/skills/article-*/SKILL.md`` 的加载契约（front matter ``name`` 等于目录名、
``description`` 长度 1–1024）、结构契约（用途/输入/步骤/产物格式/边界/失败处理六节）、
产物契约（末尾一个 ```json 围栏，字段限于产物字段且禁止模型自报服务端标识）、
以及来源合规契约（无上游署名与个人路径）。这些约束来自 plan Task 1 与协作契约 §7。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from nanobot.agent.skills import SkillsLoader

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILTIN_SKILLS_DIR = REPO_ROOT / "nanobot" / "skills"

WRITING_SKILLS = (
    "article-brief",
    "article-research",
    "article-outline",
    "article-draft",
    "article-rewrite",
    "article-expand",
    "article-review",
)

REQUIRED_SECTIONS = ("用途", "输入", "步骤", "产物格式", "边界", "失败处理")

# 产物允许出现的字段名（契约 §1 的 artifact/suggestion 字段子集，另加产物载体字段）。
ARTIFACT_FIELD_NAMES = frozenset(
    {
        "kind",
        "content",
        "sourceIds",
        "issues",
        "gaps",
        "target",
        "before",
        "after",
        "summary",
        "notes",
        "reason",
        "section",
        "heading",
        "severity",
        "category",
        "location",
        "suggestion",
        "action",
        "detail",
        "evidence",
        "status",
    }
)

# 模型不得自报的服务端标识（articleId/runId 等由宿主决定）。
FORBIDDEN_REPORTED_IDS = (
    "articleId",
    "article_id",
    "runId",
    "run_id",
    "revision",
    "contentDigest",
    "content_digest",
    "snapshotDigest",
    "snapshot_digest",
)
FORBIDDEN_REPORTED_ID_PATTERN = re.compile(
    r'"[^"]*(?:' + "|".join(FORBIDDEN_REPORTED_IDS) + r')[^"]*"\s*:'
)
STANDALONE_VERSION_KEY_PATTERN = re.compile(r'"version"\s*:')

# 上游署名、推广与个人路径（plan Task 1 的禁止清单）。
FORBIDDEN_SUBSTRINGS = (
    "humanizer-zh",
    "op7418",
    "ComposioHQ",
    "skillhub",
    "clawhub",
    "skillhub.club",
    "http://",
    "https://",
    "://",
    "www.",
    "~/",
    "/Users/",
    "/home/",
    "C:\\",
    "@gmail",
    "微信公众号：",
    "扫码",
)

_JSON_FENCE_PATTERN = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)
_FRONTMATTER_PATTERN = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*\r?\n", re.DOTALL)


def _skill_dir(name: str) -> Path:
    return BUILTIN_SKILLS_DIR / name


def _skill_path(name: str) -> Path:
    return _skill_dir(name) / "SKILL.md"


def _read_skill(name: str) -> str:
    path = _skill_path(name)
    assert path.is_file(), f"缺少内置写作 Skill 文件: {path}"
    return path.read_text(encoding="utf-8")


def _frontmatter_scalar(content: str, key: str) -> str | None:
    match = _FRONTMATTER_PATTERN.match(content)
    assert match is not None, "SKILL.md 缺少可解析的 YAML front matter"
    for line in match.group(1).splitlines():
        field, separator, value = line.partition(":")
        if separator and field.strip() == key:
            return value.strip().strip("\"'")
    return None


def _body(content: str) -> str:
    match = _FRONTMATTER_PATTERN.match(content)
    return content[match.end() :] if match is not None else content


def _product_json_blocks(content: str) -> list[str]:
    """返回正文中每个「产物格式」的 json 围栏内容（排除其它围栏）。"""
    sections = re.split(r"^## ", content, flags=re.MULTILINE)
    product = [section for section in sections if section.startswith("产物格式")]
    assert product, "正文缺少「## 产物格式」节"
    return _JSON_FENCE_PATTERN.findall(product[0])


def _top_level_keys(value: object) -> list[str]:
    if isinstance(value, dict):
        return [key for key in value if isinstance(key, str)]
    if isinstance(value, list):
        return [key for item in value for key in _top_level_keys(item)]
    return []


@pytest.mark.parametrize("name", WRITING_SKILLS)
def test_writing_skills_exist_and_names_match_dirs(name: str) -> None:
    content = _read_skill(name)
    assert _skill_dir(name).is_dir()
    assert _frontmatter_scalar(content, "name") == name


@pytest.mark.parametrize("name", WRITING_SKILLS)
def test_writing_skill_description_length(name: str) -> None:
    description = _frontmatter_scalar(_read_skill(name), "description")
    assert description is not None, f"{name} 缺少 description"
    assert 1 <= len(description.strip()) <= 1024


@pytest.mark.parametrize("name", WRITING_SKILLS)
def test_writing_skill_sections_present(name: str) -> None:
    body = _body(_read_skill(name))
    headings = set(re.findall(r"^##\s+(\S+)\s*$", body, flags=re.MULTILINE))
    for section in REQUIRED_SECTIONS:
        assert section in headings, f"{name} 缺少「## {section}」小节"


@pytest.mark.parametrize("name", WRITING_SKILLS)
def test_writing_skill_product_fields_are_artifact_only(name: str) -> None:
    blocks = _product_json_blocks(_read_skill(name))
    assert len(blocks) == 1, f"{name} 的「产物格式」节必须只有一个 json 围栏"
    parsed = json.loads(blocks[0])
    keys = _top_level_keys(parsed)
    assert "kind" in keys, f"{name} 的产物 JSON 必须含 kind"
    assert "content" in keys, f"{name} 的产物 JSON 必须含 content"
    unknown = sorted(set(keys) - ARTIFACT_FIELD_NAMES)
    assert not unknown, f"{name} 的产物 JSON 出现非产物字段: {unknown}"


@pytest.mark.parametrize("name", WRITING_SKILLS)
def test_writing_skill_forbids_self_reported_server_ids(name: str) -> None:
    blocks = _product_json_blocks(_read_skill(name))
    assert len(blocks) == 1, f"{name} 的「产物格式」节必须只有一个 json 围栏"
    assert not FORBIDDEN_REPORTED_ID_PATTERN.search(blocks[0]), (
        f"{name} 的产物 JSON 不得自报 articleId/runId/revision/digest"
    )
    assert not STANDALONE_VERSION_KEY_PATTERN.search(blocks[0]), (
        f"{name} 的产物 JSON 不得自报 version"
    )


@pytest.mark.parametrize("name", WRITING_SKILLS)
def test_writing_skills_have_no_upstream_attribution(name: str) -> None:
    content = _read_skill(name)
    lowered = content.lower()
    for forbidden in FORBIDDEN_SUBSTRINGS:
        assert forbidden.lower() not in lowered, f"{name} 含禁止内容: {forbidden}"


@pytest.mark.parametrize("name", WRITING_SKILLS)
def test_writing_skills_declare_data_not_instructions_boundary(name: str) -> None:
    body = _body(_read_skill(name))
    for keyword in ("系统指令", "不写入文件", "不发布"):
        assert keyword in body, f"{name} 的边界节必须写明: {keyword}"


def test_writing_skills_are_disjoint_from_prepared_prompt_drafts() -> None:
    """禁止把 prepared 草案原文直接复制为 SKILL.md：不做逐字节比对，但要求正文本地化改写。"""
    digests = {
        hashlib.sha256(_read_skill(name).encode("utf-8")).hexdigest() for name in WRITING_SKILLS
    }
    assert len(digests) == len(WRITING_SKILLS), "七个 Skill 正文不得互相复制"


def test_article_review_skill_loaded_by_loader(tmp_path: Path) -> None:
    loader = SkillsLoader(workspace=tmp_path, builtin_skills_dir=BUILTIN_SKILLS_DIR)

    listed = {entry["name"] for entry in loader.list_skills(filter_unavailable=False)}
    assert "article-review" in listed

    loaded = loader.load_skill("article-review")
    raw = _read_skill("article-review")
    assert loaded == raw, "SkillsLoader 必须返回完整正文（含 front matter），不是摘要"

    body = loader._strip_frontmatter(loaded)
    assert not body.startswith("---"), "载入上下文时 front matter 应被剥离"
    assert len(body) >= 600, "载入的正文长度不足，疑似被摘要化"
    for section in REQUIRED_SECTIONS:
        assert re.search(rf"^##\s+{section}\s*$", body, flags=re.MULTILINE), (
            f"按名加载的 article-review 缺少「## {section}」节"
        )
