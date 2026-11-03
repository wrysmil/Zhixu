"""规则编译器的单测：取舍规则、字符预算、新鲜度判据。

编译是注入路径的上游，所以这里既测纯函数（取舍规则可单独推理），也测落盘与
过期（它们决定注入侧到底读产物还是读全文）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from nanobot.identity.bootstrap import load_identity_template
from nanobot.identity.catalog import COMPILED_SCHEMA_VERSION, IDENTITY_DIR_NAME
from nanobot.identity.compiler import (
    COMPILE_TARGETS,
    COMPILED_AT_FILENAME,
    COMPILER_VERSION_FILENAME,
    MAX_LINE_CHARS,
    CompileTarget,
    compile_content,
    compile_identity,
    compiled_status,
    read_compiled,
    runtime_dir,
)

SOUL_SOURCE = "# Soul\n\n## 核心原则\n\n- 先做再说\n"
SOUL_COMPILED = "# Soul\n## 核心原则\n- 先做再说"


def _target(key: str) -> CompileTarget:
    return next(target for target in COMPILE_TARGETS if target.key == key)


def _seed(workspace: Path, name: str, content: str) -> Path:
    identity_dir = workspace / IDENTITY_DIR_NAME
    identity_dir.mkdir(parents=True, exist_ok=True)
    path = identity_dir / name
    path.write_text(content, encoding="utf-8")
    return path


# ---- 取舍规则 ---------------------------------------------------------------


def test_owned_sections_are_kept_and_other_sections_dropped():
    content = "# Soul\n\n## 核心原则\n\n- 先做再说\n\n## 平台职责\n\n- 不该出现\n"

    out = compile_content(content, _target("identity_core"))

    assert "先做再说" in out
    assert "不该出现" not in out


def test_english_headings_are_kept_for_identity_core():
    """用户的 SOUL.md 未必用中文标题；英文标题也要能收进来。

    回归用例：``# Soul`` 命中 marker、``## Core Principles`` / ``## Execution
    Rules`` 不命中时，产物只剩导语，12 条规则整份丢失。
    """
    content = (
        "# Soul\n\n"
        "I am a personal AI assistant.\n\n"
        "## Core Principles\n\n"
        "- Solve by doing, not by describing what I would do.\n"
        "- Keep responses short unless depth is asked for.\n"
        "\n"
        "## Execution Rules\n\n"
        "- Act immediately on single-step tasks.\n"
        "- Read before you write.\n"
    )

    out = compile_content(content, _target("identity_core"))

    assert "Solve by doing" in out
    assert "Keep responses short" in out
    assert "Act immediately" in out
    assert "Read before you write" in out


def test_thin_owned_coverage_falls_back_to_all_body_lines():
    """只命中导语标题、正文几乎全落选时，按漏料处理并收全文。

    覆盖 :data:`MIN_OWNED_COVERAGE` 触发路径——这类缺失比「一个标题都没命中」
    更隐蔽，因为 ``matched_any`` 为真，旧的降级判断不会触发。
    """
    content = "# Soul\n\n- 导语一行\n\n## 某个完全不认识的章节\n\n- 规则一\n- 规则二\n- 规则三\n"

    out = compile_content(content, _target("identity_core"))

    assert "规则一" in out
    assert "规则三" in out


def test_falls_back_to_all_body_lines_when_no_section_matches():
    """用户的标题未必和出厂模板一致；收窄失败时宁可全收，不要编译出空产物。"""
    content = "# 我的设定\n\n- 规则一\n- 规则二\n"

    out = compile_content(content, _target("agent_behavior"))

    assert "规则一" in out
    assert "规则二" in out
    assert "# 我的设定" not in out


def test_user_placeholders_are_dropped():
    content = "# User Profile\n\n- **称呼**：（待填）\n- **语气**：轻松\n"

    out = compile_content(content, _target("user_profile_core"))

    assert "待填" not in out
    assert "语气" in out


def test_duplicate_lines_and_markdown_noise_are_removed():
    content = "# Soul\n\n## 核心原则\n\n- 重复\n- 重复\n\n---\n\n- 唯一\n"

    out = compile_content(content, _target("identity_core"))

    assert out.count("- 重复") == 1
    assert "---" not in out
    assert "- 唯一" in out


def test_overlong_line_is_truncated():
    content = "# Soul\n\n## 核心原则\n\n- " + "长" * 500 + "\n"

    out = compile_content(content, _target("identity_core"))

    assert max(len(line) for line in out.splitlines()) <= MAX_LINE_CHARS


def test_char_budget_is_enforced():
    body = "\n".join(f"- 规则{i}：" + "字" * 40 for i in range(80))
    target = _target("identity_core")

    out = compile_content(f"# Soul\n\n## 核心原则\n\n{body}\n", target)

    assert 0 < len(out) <= target.max_chars


def test_placeholder_only_source_compiles_to_empty():
    content = "# User Profile\n\n- **称呼**：（待填）\n"

    assert compile_content(content, _target("user_profile_core")) == ""


# ---- 落盘 -------------------------------------------------------------------


def test_compile_identity_writes_products_and_stamp(tmp_path: Path):
    _seed(tmp_path, "SOUL.md", SOUL_SOURCE)

    result = compile_identity(tmp_path)

    assert result["status"] == "ok"
    assert result["modeUsed"] == "rules"
    assert result["compiledFiles"] == ["identity.core.md"]
    # 缺源文件的目标要报 skipped，而不是静默当成成功。
    assert {entry["target"] for entry in result["skipped"]} == {
        "agent_behavior",
        "user_profile_core",
    }
    assert {entry["reason"] for entry in result["skipped"]} == {"source_missing"}

    rdir = runtime_dir(tmp_path)
    assert (rdir / "identity.core.md").read_text(encoding="utf-8") == SOUL_COMPILED
    assert (rdir / COMPILER_VERSION_FILENAME).read_text(encoding="utf-8") == (
        COMPILED_SCHEMA_VERSION
    )
    assert (rdir / COMPILED_AT_FILENAME).is_file()


def test_all_placeholder_workspace_compiles_nothing_but_records_attempt(tmp_path: Path):
    """全是占位符 = 编译过了、但确实没有可注入的规则——时间戳仍要写。"""
    _seed(tmp_path, "USER.md", "# User Profile\n\n- **称呼**：（待填）\n")

    result = compile_identity(tmp_path)

    assert result["compiledFiles"] == []
    assert (runtime_dir(tmp_path) / COMPILED_AT_FILENAME).is_file()
    assert read_compiled(tmp_path) is None


# ---- 新鲜度 -----------------------------------------------------------------


def test_status_reports_not_compiled_for_empty_workspace(tmp_path: Path):
    assert compiled_status(tmp_path) == {
        "compiled": False,
        "fresh": False,
        "reason": "not_compiled",
    }
    assert read_compiled(tmp_path) is None


def test_fresh_products_are_readable_after_compile(tmp_path: Path):
    _seed(tmp_path, "SOUL.md", SOUL_SOURCE)

    compile_identity(tmp_path)

    assert compiled_status(tmp_path) == {"compiled": True, "fresh": True, "reason": "ok"}
    assert read_compiled(tmp_path) == {"identity_core": SOUL_COMPILED}


def test_source_edit_invalidates_compiled_set(tmp_path: Path):
    soul = _seed(tmp_path, "SOUL.md", SOUL_SOURCE)
    compile_identity(tmp_path)

    # 显式把源文件 mtime 推到未来：秒级精度文件系统下同秒写入会误判。
    future = time.time() + 5
    os.utime(soul, (future, future))

    assert compiled_status(tmp_path)["reason"] == "source_newer"
    assert read_compiled(tmp_path) is None


def test_schema_bump_invalidates_compiled_set(tmp_path: Path):
    _seed(tmp_path, "SOUL.md", SOUL_SOURCE)
    compile_identity(tmp_path)

    (runtime_dir(tmp_path) / COMPILER_VERSION_FILENAME).write_text("0", encoding="utf-8")

    assert compiled_status(tmp_path)["reason"] == "schema_mismatch"
    assert read_compiled(tmp_path) is None


def test_missing_products_mark_set_unusable(tmp_path: Path):
    _seed(tmp_path, "SOUL.md", SOUL_SOURCE)
    compile_identity(tmp_path)

    (runtime_dir(tmp_path) / "identity.core.md").unlink()

    assert compiled_status(tmp_path)["reason"] == "no_products"
    assert read_compiled(tmp_path) is None


def test_recompile_clears_source_newer_state(tmp_path: Path):
    soul = _seed(tmp_path, "SOUL.md", SOUL_SOURCE)
    compile_identity(tmp_path)
    future = time.time() + 5
    os.utime(soul, (future, future))
    assert compiled_status(tmp_path)["fresh"] is False

    compile_identity(tmp_path)

    assert compiled_status(tmp_path)["fresh"] is True


# ---- 出厂模板 ---------------------------------------------------------------


def test_factory_agent_and_user_templates_produce_no_products(tmp_path: Path):
    """出厂模板不落产物——否则点一次「规则编译」就把模板灌进 system prompt。

    注入侧对出厂模板的跳过只在回退分支成立，产物分支够不到那个判断；出厂
    AGENT.md / USER.md 又含实质句子（编译得出非空产物），所以必须在源头拦住。
    """
    for name in ("AGENT.md", "USER.md"):
        template = load_identity_template(name)
        assert template is not None
        _seed(tmp_path, name, template)

    result = compile_identity(tmp_path)

    assert result["compiledFiles"] == []
    reasons = {entry["target"]: entry["reason"] for entry in result["skipped"]}
    assert reasons["agent_behavior"] == "factory_template"
    assert reasons["user_profile_core"] == "factory_template"
    assert not (runtime_dir(tmp_path) / "agent.behavior.md").exists()
    assert not (runtime_dir(tmp_path) / "user.profile.core.md").exists()


def test_factory_soul_template_is_still_compiled(tmp_path: Path):
    """SOUL.md 正相反：出厂人格本来就该进 prompt，跳过它等于让人格消失。"""
    template = load_identity_template("SOUL.md")
    assert template is not None
    _seed(tmp_path, "SOUL.md", template)

    result = compile_identity(tmp_path)

    assert result["compiledFiles"] == ["identity.core.md"]


def test_edited_agent_file_is_compiled_even_if_close_to_template(tmp_path: Path):
    """改过就不再是模板，必须照常出产物——这是「用户改了就生效」的保证。"""
    _seed(tmp_path, "AGENT.md", "# Agent 行为准则\n\n## 任务执行\n\n- 我自己写的规则\n")

    result = compile_identity(tmp_path)

    assert result["compiledFiles"] == ["agent.behavior.md"]
    body = (runtime_dir(tmp_path) / "agent.behavior.md").read_text(encoding="utf-8")
    assert "我自己写的规则" in body


# ---- P3：AGENT.md 成长循环 ---------------------------------------------------


def test_agent_growth_loop_section_is_compiled():
    template = load_identity_template("AGENT.md")
    assert template is not None

    body = compile_content(template, _target("agent_behavior"))

    assert "成长循环" in body
    assert "add_memory" in body


def test_agent_compiled_output_stays_within_budget():
    template = load_identity_template("AGENT.md")
    assert template is not None

    body = compile_content(template, _target("agent_behavior"))

    assert len(body) <= _target("agent_behavior").max_chars


def test_agent_compiled_output_contains_no_memory_file_instruction():
    """Ralph 的「每轮保存进度到 MEMORY.md」不能混进提示词。"""
    template = load_identity_template("AGENT.md")
    assert template is not None

    body = compile_content(template, _target("agent_behavior"))

    assert "MEMORY.md" not in body


def test_soul_value_sections_are_compiled_within_budget():
    """P3 增补：SOUL 的三节价值取向必须整节进产物，且不超预算。"""
    template = load_identity_template("SOUL.md")
    assert template is not None

    body = compile_content(template, _target("identity_core"))

    for heading in ("## 真正的帮助", "## 诚实", "## 自主与边界"):
        assert heading in body
    assert len(body) <= _target("identity_core").max_chars
