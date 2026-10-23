"""T01：受控路径解析与输入模型校验（契约 §2 / plan L132-L145）。

本文件按测试矩阵逐条红绿推进：受控路径解析、输入模型与上限、SchemaSubset 限制、
状态枚举与 Worker IPC 载荷。
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from nanobot.evaluation.schemas import (
    AgentWorkerResult,
    AssertionResult,
    CaseRead,
    CaseWrite,
    JudgeResult,
    JudgeWorkerResult,
    RunCreate,
    SuiteRead,
    SuiteWrite,
    TraceEvent,
    WorkerBoot,
)
from nanobot.evaluation.validation import resolve_inside, validate_relative_path

# 载荷工厂的唯一真相源在 conftest（WU-01 拥有），测试模块按契约原文直接引用其产物。
from .conftest import make_case_payload, make_rubric_payload, make_tool_fixture_case_payload

TS = "2026-10-15T00:00:00+00:00"


def _make_dir_link(link: Path, target: Path) -> None:
    """建立指向 root 外目录的链接：POSIX symlink，Windows 退化为 junction（reparse point）。"""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("平台不支持创建目录符号链接")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not link.exists():
        pytest.skip(f"平台不支持创建目录链接：{result.stderr.strip()[:80]}")


# --------------------------------------------------------------------------- 基本包含


def test_path_resolves_inside(tmp_path: Path) -> None:
    assert resolve_inside(tmp_path, "inputs/a.txt") == tmp_path / "inputs" / "a.txt"


def test_path_resolves_nested_inside(tmp_path: Path) -> None:
    assert resolve_inside(tmp_path, "a/b/c.txt") == tmp_path / "a" / "b" / "c.txt"


def test_path_cannot_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="PATH_OUTSIDE_ROOT"):
        resolve_inside(tmp_path, "../outside.txt")


def test_path_cannot_escape_from_deep_relative(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="PATH_OUTSIDE_ROOT"):
        resolve_inside(tmp_path, "inputs/../../outside.txt")


# --------------------------------------------------------------------------- 空值与自身


@pytest.mark.parametrize("relative", ["", "   ", "/", "\\"])
def test_path_blank_or_root_only_is_rejected(tmp_path: Path, relative: str) -> None:
    with pytest.raises(ValueError, match="PATH_OUTSIDE_ROOT"):
        resolve_inside(tmp_path, relative)


@pytest.mark.parametrize("relative", [".", "..", "./x", "x/.", "x/.."])
def test_path_dot_components_are_rejected(tmp_path: Path, relative: str) -> None:
    """`.` / `..` 段一律拒绝：不依赖解析结果，避免与软链接组合出歧义。"""
    with pytest.raises(ValueError, match="PATH_OUTSIDE_ROOT"):
        resolve_inside(tmp_path, relative)


# --------------------------------------------------------------------------- 绝对路径与 Windows 形态


@pytest.mark.parametrize(
    "relative",
    [
        "/etc/passwd",
        "C:\\Windows\\system32\\evil.exe",
        "C:/Windows/win.ini",
        "C:foo.txt",  # 盘符相对路径
        "\\\\server\\share\\f.txt",  # UNC
        "\\\\127.0.0.1\\C$\\windows\\win.ini",
    ],
)
def test_absolute_drive_and_unc_paths_are_rejected(tmp_path: Path, relative: str) -> None:
    with pytest.raises(ValueError, match="PATH_OUTSIDE_ROOT"):
        resolve_inside(tmp_path, relative)


@pytest.mark.parametrize(
    "relative",
    [
        "inputs\\..\\..\\outside.txt",  # 反斜线逃逸
        "..\\outside.txt",
        "a\\b\\c.txt",
    ],
)
def test_backslash_paths_are_rejected(tmp_path: Path, relative: str) -> None:
    """相对路径只允许正斜线：反斜线在 Windows 上是分隔符，在 POSIX 上是可混淆字符。"""
    with pytest.raises(ValueError, match="PATH_OUTSIDE_ROOT"):
        resolve_inside(tmp_path, relative)


# --------------------------------------------------------------------------- 链接指向外部


def test_junction_pointing_outside_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _make_dir_link(root / "link", outside)
    with pytest.raises(ValueError, match="PATH_OUTSIDE_ROOT"):
        resolve_inside(root, "link/secret.txt")


def test_link_inside_root_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "root"
    inner = root / "inner"
    inner.mkdir(parents=True)
    _make_dir_link(root / "link", inner)
    assert resolve_inside(root, "link/a.txt") == (root / "inner" / "a.txt").resolve()


# --------------------------------------------------------------------------- 模型层（无 root）语法检查


@pytest.mark.parametrize(
    "relative",
    [
        "",
        "   ",
        "/abs/path.txt",
        "C:\\abs.txt",
        "C:relative.txt",
        "\\\\server\\share\\x",
        "..\\escape.txt",
        "../escape.txt",
        "a/../../escape.txt",
        "./a.txt",
        "..",
    ],
)
def test_relative_path_syntax_rejects_escapes(relative: str) -> None:
    with pytest.raises(ValueError, match="PATH_OUTSIDE_ROOT"):
        validate_relative_path(relative)


@pytest.mark.parametrize("relative", ["a.txt", "inputs/a.txt", "out/nested/result.json"])
def test_relative_path_syntax_accepts_clean_paths(relative: str) -> None:
    assert validate_relative_path(relative) == relative


# =========================================================================== 输入模型
# 以下切片依赖 nanobot.evaluation.schemas：契约 §2 的 CaseWrite/CaseRead/SuiteWrite/
# SuiteRead/RunCreate，全部 extra="forbid"、整数与布尔严格区分、上限取契约原文。


def _reject_case(**overrides: object) -> ValidationError:
    with pytest.raises(ValidationError) as excinfo:
        CaseWrite.model_validate(make_case_payload(**overrides))
    return excinfo.value


# ----------------------------------------------------------------- 字段集合与未知字段


def test_case_write_field_set_matches_contract() -> None:
    assert set(CaseWrite.model_fields) == {
        "name", "description", "tags", "enabled", "suite_id", "base_revision",
        "turns", "fixtures", "memory_seed", "clock_iso", "tool_fixtures",
        "execution", "assertions", "rubric", "script", "artifact_paths",
    }


def test_case_read_field_set_matches_contract() -> None:
    assert set(CaseRead.model_fields) == (
        set(CaseWrite.model_fields) - {"base_revision"}
        | {"id", "revision", "created_at", "updated_at", "is_deleted"}
    )


def test_suite_write_and_read_field_sets_match_contract() -> None:
    assert set(SuiteWrite.model_fields) == {
        "name", "description", "tags", "base_revision"
    }
    assert set(SuiteRead.model_fields) == {
        "id", "revision", "case_count", "name", "description", "tags",
        "created_at", "updated_at", "is_deleted",
    }


def test_run_create_field_set_matches_contract() -> None:
    # target / run_timeout_s 属 2026-10-23 评估目标体系扩展（WU-03）。
    assert set(RunCreate.model_fields) == {
        "suite_id", "case_ids", "agent_profile_id", "judge_profile_id", "limits",
        "target", "run_timeout_s",
    }


def test_unknown_top_level_field_is_rejected(case_payload_factory) -> None:
    excinfo = _reject_case(unexpected="x")
    assert "unexpected" in str(excinfo)


@pytest.mark.parametrize(
    "overrides",
    [
        {"execution": {**make_case_payload()["execution"], "sandbox": "docker"}},
        {"turns": [{"content": "hi", "role": "user"}]},
        {"fixtures": [{"path": "a.txt", "content": "x", "encoding": "utf-8", "mode": 0o600}]},
        {"memory_seed": [{"kind": "semantic", "content": "x", "scope": "global"}]},
        {"tool_fixtures": [
            {**make_tool_fixture_case_payload()["tool_fixtures"][0], "delay_ms": 1}]},
        {"assertions": [{
            "id": "a1", "kind": "answer_contains", "required": True,
            "text": "alpha", "case_sensitive": True, "pattern": "a.*"}]},
        {"rubric": {**make_rubric_payload(), "model": "gpt"}},
    ],
    ids=["execution", "turns", "fixtures", "memory_seed", "tool_fixtures",
         "assertion", "rubric"],
)
def test_nested_unknown_field_is_rejected(overrides: dict[str, object]) -> None:
    """extra="forbid" 必须贯穿嵌套结构：未知字段一律拒绝，不当作合法透传。"""
    _reject_case(**overrides)


def test_case_read_rejects_base_revision_and_extra_field(case_payload_factory) -> None:
    payload = make_case_payload()
    payload.pop("base_revision")
    read = {**payload, "id": str(uuid.uuid4()), "revision": 1, "created_at": TS,
            "updated_at": TS, "is_deleted": False}
    CaseRead.model_validate(read)
    with pytest.raises(ValidationError):
        CaseRead.model_validate({**read, "base_revision": None})
    with pytest.raises(ValidationError):
        CaseRead.model_validate({**read, "unexpected": 1})


def test_suite_read_requires_case_count_and_rejects_extra(suite_payload_factory) -> None:
    written = suite_payload_factory()
    written.pop("base_revision")  # 契约 §2：读模型不含 base_revision
    read = {
        **written,
        "id": str(uuid.uuid4()), "revision": 1, "case_count": 0,
        "created_at": TS, "updated_at": TS, "is_deleted": False,
    }
    assert SuiteRead.model_validate(read).case_count == 0
    with pytest.raises(ValidationError):
        SuiteRead.model_validate({**read, "base_revision": None})


def test_minimal_case_payload_uses_contract_defaults(case_payload_factory) -> None:
    """只提交无默认值的字段即可通过：集合字段与 clock_iso/rubric 允许省略。"""
    minimal = {
        "name": "最小用例",
        "enabled": True,
        "suite_id": str(uuid.uuid4()),
        "turns": [{"content": "只回答 ok"}],
        "execution": {"tool_mode": "fixture", "allowed_tools": ["read_file"]},
        "assertions": [{
            "id": "a1", "kind": "answer_contains", "required": True,
            "text": "ok", "case_sensitive": True}],
    }
    case = CaseWrite.model_validate(minimal)
    assert case.description == ""
    assert case.tags == [] and case.fixtures == [] and case.memory_seed == []
    assert case.tool_fixtures == [] and case.artifact_paths == []
    assert case.clock_iso is None and case.rubric is None
    assert case.base_revision is None
    assert case.execution.max_iterations == 12 and case.execution.timeout_s == 120


# ----------------------------------------------------------------- 整数与布尔严格区分


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution", {"tool_mode": "fixture", "allowed_tools": ["read_file"],
                       "max_iterations": True}),
        ("execution", {"tool_mode": "fixture", "allowed_tools": ["read_file"],
                       "timeout_s": True}),
        ("execution", {"tool_mode": "fixture", "allowed_tools": ["read_file"],
                       "max_iterations": "12"}),
        ("tool_fixtures", [{"tool_name": "test_lookup", "arguments_match": {},
                            "result": {"ok": 1}, "is_error": 1}]),
    ],
    ids=["max_iterations_bool", "timeout_s_bool", "max_iterations_str",
         "is_error_int"],
)
def test_integer_and_boolean_fields_are_not_interchangeable(
    field: str, value: object
) -> None:
    """``bool`` 不得被当成 ``int`` 通过校验，反之亦然（契约 §2 SchemaSubset 同口径）。"""
    _reject_case(**{field: value})


@pytest.mark.parametrize(
    "overrides",
    [{"enabled": 1}, {"enabled": "true"}, {"clock_iso": 123},
     {"artifact_paths": ["a.txt", 2]}],
    ids=["enabled_int", "enabled_str", "clock_iso_int", "artifact_int"],
)
def test_boolean_and_string_fields_reject_coercion(overrides: dict[str, object]) -> None:
    _reject_case(**overrides)


def test_json_value_keeps_int_and_bool_distinct(tool_case_payload_factory) -> None:
    """tool_fixtures.result 是 JSON 值：1 仍是整数、True 仍是布尔，不被相互吞掉。"""
    payload = tool_case_payload_factory(
        tool_fixtures=[{"tool_name": "test_lookup", "arguments_match": {"q": 1},
                        "result": {"count": 1, "flag": True, "rate": 1.5,
                                   "label": "x", "items": [1, True], "none": None}}],
    )
    case = CaseWrite.model_validate(payload)
    result = case.tool_fixtures[0].result
    assert [type(result[key]).__name__ for key in
            ("count", "flag", "rate", "label", "none")] == ["int", "bool", "float", "str", "NoneType"]
    assert [type(x).__name__ for x in result["items"]] == ["int", "bool"]


def test_json_value_rejects_non_json_types(tool_case_payload_factory) -> None:
    _reject_case(**{"tool_fixtures": [
        {"tool_name": "test_lookup", "arguments_match": {}, "result": {"raw": b"bytes"},
         "is_error": False}]})


# ----------------------------------------------------------------- 字段上限


@pytest.mark.parametrize("name", ["", "   ", "a" * 121], ids=["empty", "blank", "too_long"])
def test_name_bounds(name: str) -> None:
    _reject_case(name=name)


def test_name_accepts_boundary_lengths() -> None:
    assert CaseWrite.model_validate(make_case_payload(name="a")).name == "a"
    assert len(CaseWrite.model_validate(make_case_payload(name="a" * 120)).name) == 120


def test_description_bounds() -> None:
    assert len(CaseWrite.model_validate(
        make_case_payload(description="d" * 4000)).description) == 4000
    _reject_case(description="d" * 4001)


@pytest.mark.parametrize("overrides", [
    {"tags": [f"t{i}" for i in range(21)]},
    {"tags": ["t" * 41]},
], ids=["21_tags", "tag_41_chars"])
def test_tag_limits(overrides: dict[str, object]) -> None:
    _reject_case(**overrides)


def test_tags_accept_boundary() -> None:
    assert len(CaseWrite.model_validate(make_case_payload(tags=[f"t{i}" for i in range(20)])).tags) == 20
    assert CaseWrite.model_validate(make_case_payload(tags=["t" * 40])).tags[0]


@pytest.mark.parametrize("turns", [
    [],
    [{"content": "x"} for _ in range(21)],
    [{"content": ""}],
    [{"content": "   "}],
    [{"content": "x" * 32001}],
    [{"content": "x"} for _ in range(20)],
], ids=["empty", "21_items", "empty_content", "blank_content", "32001_chars", "20_ok"])
def test_turn_limits(turns: list[dict[str, str]]) -> None:
    if len(turns) == 20 and all(len(item["content"]) == 1 for item in turns):
        assert len(CaseWrite.model_validate(make_case_payload(turns=turns)).turns) == 20
    else:
        _reject_case(turns=turns)


def test_turn_accepts_max_length() -> None:
    case = CaseWrite.model_validate(make_case_payload(turns=[{"content": "x" * 32000}]))
    assert len(case.turns[0].content) == 32000


@pytest.mark.parametrize("count,size", [
    (51, 1),          # 条数超限
    (2, 1_100_000),   # 合计超过 2 MiB
])
def test_fixture_limits(count: int, size: int) -> None:
    _reject_case(fixtures=[
        {"path": f"f{i}.txt", "content": "x" * size, "encoding": "utf-8"}
        for i in range(count)
    ])


def test_fixtures_accept_exactly_two_mebibytes() -> None:
    case = CaseWrite.model_validate(make_case_payload(fixtures=[
        {"path": f"f{i}.txt", "content": "x" * (1024 * 1024), "encoding": "utf-8"}
        for i in range(2)
    ]))
    assert len(case.fixtures) == 2


@pytest.mark.parametrize("content", ["\x00binary", "a\x07b"], ids=["nul", "control_char"])
def test_fixtures_are_text_only(content: str) -> None:
    """只支持文本：NUL 与控制字符（制表/换行除外）一律拒绝。"""
    _reject_case(fixtures=[{"path": "a.txt", "content": content, "encoding": "utf-8"}])


def test_fixture_encoding_is_literal_utf8() -> None:
    _reject_case(fixtures=[{"path": "a.txt", "content": "x", "encoding": "latin-1"}])


def test_fixture_path_rejects_escape() -> None:
    _reject_case(fixtures=[{"path": "../evil.txt", "content": "x", "encoding": "utf-8"}])
    _reject_case(fixtures=[{"path": "C:/evil.txt", "content": "x", "encoding": "utf-8"}])


@pytest.mark.parametrize("overrides", [
    {"memory_seed": [{"kind": "semantic", "content": "x"} for _ in range(21)]},
    {"memory_seed": [{"kind": "semantic", "content": "x" * 64001}]},
    {"memory_seed": [{"kind": "episodic", "content": "x"}]},
], ids=["21_items", "over_64000", "wrong_kind"])
def test_memory_seed_limits(overrides: dict[str, object]) -> None:
    _reject_case(**overrides)


def test_memory_seed_accepts_boundary() -> None:
    case = CaseWrite.model_validate(make_case_payload(
        memory_seed=[{"kind": "semantic", "content": "s" * 3200},
                     {"kind": "semantic", "content": "s" * 32000}]))
    assert len(case.memory_seed) == 2


@pytest.mark.parametrize("overrides", [
    {"tool_fixtures": [
        {"tool_name": "test_lookup", "arguments_match": {}, "result": {"b": "x" * 200},
         "is_error": False} for _ in range(51)]},
    {"tool_fixtures": [
        {"tool_name": "test_lookup", "arguments_match": {},
         "result": {"blob": "x" * 1_500_000}, "is_error": False}]},
    {"tool_fixtures": [{"tool_name": "rm -rf /", "arguments_match": {},
                        "result": {}, "is_error": False}]},
    {"tool_fixtures": [{"tool_name": "test_arbitrary", "arguments_match": {},
                        "result": {}, "is_error": False}]},
], ids=["51_items", "over_1_mib", "shell_name", "unregistered_tool"])
def test_tool_fixture_limits(overrides: dict[str, object]) -> None:
    _reject_case(**overrides)


def test_tool_fixtures_accept_boundary_count() -> None:
    case = CaseWrite.model_validate(make_case_payload(
        tool_fixtures=[{"tool_name": "test_unstable", "arguments_match": {},
                        "result": {"ok": True}, "is_error": False}
                       for _ in range(50)]))
    assert len(case.tool_fixtures) == 50


@pytest.mark.parametrize("assertions", [
    [],
    [{"id": f"a{i}", "kind": "file_exists", "required": False, "path": f"f{i}.txt"}
     for i in range(51)],
], ids=["empty", "51_items"])
def test_assertion_count_limits(assertions: list[dict[str, object]]) -> None:
    _reject_case(assertions=assertions)


def test_artifact_path_limits_and_escapes() -> None:
    _reject_case(artifact_paths=[f"o{i}.txt" for i in range(51)])
    _reject_case(artifact_paths=["../outside.txt"])
    _reject_case(artifact_paths=["C:out.txt"])
    assert len(CaseWrite.model_validate(
        make_case_payload(artifact_paths=[f"o{i}.txt" for i in range(50)])).artifact_paths) == 50


# ----------------------------------------------------------------- 执行块


@pytest.mark.parametrize("execution", [
    {"tool_mode": "fixture", "allowed_tools": ["read_file"], "max_iterations": 0},
    {"tool_mode": "fixture", "allowed_tools": ["read_file"], "max_iterations": 51},
    {"tool_mode": "fixture", "allowed_tools": ["read_file"], "timeout_s": 4},
    {"tool_mode": "fixture", "allowed_tools": ["read_file"], "timeout_s": 901},
    {"tool_mode": "sandbox", "allowed_tools": ["read_file"]},
    {"tool_mode": "fixture", "allowed_tools": ["mcp_anything"]},
    {"tool_mode": "fixture", "allowed_tools": ["web_search"]},
    {"tool_mode": "fixture", "allowed_tools": []},
    {"tool_mode": "fixture", "allowed_tools": ["read_file", "read_file"]},
], ids=["iter_0", "iter_51", "timeout_4", "timeout_901", "bad_mode", "unknown_tool",
        "web_in_fixture", "empty_tools", "duplicate_tool"])
def test_execution_rejects_out_of_range(execution: dict[str, object]) -> None:
    _reject_case(execution=execution)


@pytest.mark.parametrize("iterations", [1, 50])
@pytest.mark.parametrize("timeout", [5, 900])
def test_execution_accepts_contract_boundaries(iterations: int, timeout: int) -> None:
    case = CaseWrite.model_validate(make_case_payload(execution={
        "tool_mode": "live", "allowed_tools": ["read_file", "web_fetch"],
        "max_iterations": iterations, "timeout_s": timeout}))
    assert case.execution.max_iterations == iterations
    assert case.execution.timeout_s == timeout
    assert case.execution.tool_mode == "live"


def test_allowed_tools_covers_contract_default_whitelist() -> None:
    case = CaseWrite.model_validate(make_case_payload(execution={
        "tool_mode": "fixture",
        "allowed_tools": ["read_file", "write_file", "edit_file", "list_dir",
                          "test_lookup", "test_unstable"]}))
    assert len(case.execution.allowed_tools) == 6


# ----------------------------------------------------------------- 必过规则与量表


def test_case_requires_at_least_one_required_rule_or_required_rubric() -> None:
    """契约 §2/§4：每个用例至少有一个必过规则或必需 rubric，否则无法给出 verdict。"""
    optional_only = [{
        "id": "a1", "kind": "answer_contains", "required": False,
        "text": "alpha", "case_sensitive": True}]
    with pytest.raises(ValidationError) as excinfo:
        CaseWrite.model_validate(make_case_payload(assertions=optional_only, rubric=None))
    assert "REQUIRED_RULE_OR_RUBRIC" in str(excinfo.value)

    with pytest.raises(ValidationError):
        CaseWrite.model_validate(
            make_case_payload(assertions=optional_only,
                              rubric=make_rubric_payload(required=False)))

    # 可选规则 + 必需量表：允许
    assert CaseWrite.model_validate(
        make_case_payload(assertions=optional_only,
                          rubric=make_rubric_payload(required=True))).rubric is not None


@pytest.mark.parametrize("rubric", [
    {**make_rubric_payload(), "dimensions": []},
    {**make_rubric_payload(), "dimensions": [
        {**make_rubric_payload()["dimensions"][0], "id": f"d{i}"} for i in range(7)]},
    {**make_rubric_payload(), "version": ""},
    {**make_rubric_payload(), "required": 1},
], ids=["no_dimension", "7_dimensions", "blank_version", "required_int"])
def test_rubric_limits(rubric: dict[str, object]) -> None:
    _reject_case(rubric=rubric)


def test_rubric_requires_all_five_anchors() -> None:
    """五档锚点缺一不可：省略锚点会让 0–4 分口径无法复核。"""
    dimension = make_rubric_payload()["dimensions"][0]
    for anchors in (
        dimension["anchors"][:4],
        [*dimension["anchors"][:4], "  "],
        [*dimension["anchors"], "extra"],
    ):
        _reject_case(rubric={**make_rubric_payload(),
                             "dimensions": [{**dimension, "anchors": anchors}]})


@pytest.mark.parametrize("pass_score", [-1, 5, True, "3"])
def test_rubric_pass_score_bounds(pass_score: object) -> None:
    dimension = make_rubric_payload()["dimensions"][0]
    _reject_case(rubric={**make_rubric_payload(),
                         "dimensions": [{**dimension, "pass_score": pass_score}]})


def test_rubric_pass_score_defaults_to_three_and_accepts_zero_and_four() -> None:
    dimension = make_rubric_payload()["dimensions"][0]
    stripped = {k: v for k, v in dimension.items() if k != "pass_score"}
    case = CaseWrite.model_validate(
        make_case_payload(rubric={**make_rubric_payload(), "dimensions": [stripped,
                         {**dimension, "pass_score": 0}, {**dimension, "pass_score": 4}]}))
    assert case.rubric is not None
    assert [d.pass_score for d in case.rubric.dimensions] == [3, 0, 4]


def test_clock_iso_must_be_iso8601() -> None:
    assert CaseWrite.model_validate(
        make_case_payload(clock_iso="2026-10-15T09:30:00+08:00")).clock_iso
    _reject_case(clock_iso="15/10/2026")
    _reject_case(clock_iso="   ")


def test_suite_id_must_be_uuid() -> None:
    _reject_case(suite_id="suite-1")
    _reject_case(suite_id="")


# ----------------------------------------------------------------- 断言 kind 闭集


@pytest.mark.parametrize("assertion", [
    {"id": "x", "kind": "answer_starts_with", "required": True, "text": "alpha"},
    {"id": "x", "kind": "python_eval", "required": True, "code": "1"},
    {"id": "x", "kind": "file_json", "required": True, "schema": {"type": "object"}},
    {"id": "x", "kind": "answer_json", "required": True,
     "checks": [{"pointer": "/a", "equals": 1}]},
    {"id": "x", "kind": "file_contains", "required": True, "path": "a.txt"},
    {"id": "x", "kind": "tool_called", "required": True, "name": "read_file",
     "arguments_subset": {}, "max_calls": None},
    {"id": "x", "kind": "tool_forbidden", "required": True, "names": []},
    {"id": "x", "kind": "iteration_limit", "required": True, "maximum": 0},
    {"id": "x", "kind": "answer_contains", "required": True, "case_sensitive": True},
], ids=["unknown_kind", "code_kind", "file_json_no_path", "answer_json_no_schema",
        "file_contains_no_text", "tool_called_no_min_calls", "empty_forbidden",
        "maximum_zero", "missing_text"])
def test_assertion_kind_payloads_are_validated(assertion: dict[str, object]) -> None:
    """每种 kind 的必填字段是闭集约束：缺字段/未知 kind/越界都拒绝。"""
    _reject_case(assertions=[assertion])


@pytest.mark.parametrize("assertion", [
    {"id": "a", "kind": "answer_not_contains", "required": True, "text": "beta",
     "case_sensitive": False},
    {"id": "b", "kind": "file_exists", "required": True, "path": "out.json"},
    {"id": "c", "kind": "file_contains", "required": True, "path": "out.txt",
     "text": "hello"},
    {"id": "d", "kind": "tool_called", "required": True, "name": "read_file",
     "arguments_subset": {"path": "in.txt"}, "min_calls": 1, "max_calls": 2},
    {"id": "e", "kind": "tool_called", "required": True, "name": "list_dir",
     "arguments_subset": {}, "min_calls": 0, "max_calls": None},
    {"id": "f", "kind": "tool_forbidden", "required": True, "names": ["web_search"]},
    {"id": "g", "kind": "iteration_limit", "required": True, "maximum": 5},
    {"id": "h", "kind": "tool_call_limit", "required": True, "maximum": 7},
    {"id": "i", "kind": "answer_json", "required": True,
     "schema": {"type": "object", "required": ["status"],
                "properties": {"status": {"type": "string"},
                               "count": {"type": "integer"}}},
     "checks": [{"pointer": "/count", "equals": 3}]},
], ids=["answer_not_contains", "file_exists", "file_contains", "tool_called_max",
        "tool_called_no_max", "tool_forbidden", "iteration_limit", "tool_call_limit",
        "answer_json"])
def test_supported_assertion_kinds_validate(assertion: dict[str, object]) -> None:
    case = CaseWrite.model_validate(make_case_payload(assertions=[assertion]))
    assert case.assertions[0].kind == assertion["kind"]


def test_tool_called_rejects_inverted_bounds() -> None:
    _reject_case(assertions=[{"id": "a", "kind": "tool_called", "required": True,
                             "name": "read_file", "arguments_subset": {},
                             "min_calls": 3, "max_calls": 2}])


# ----------------------------------------------------------------- 运行请求


def test_run_create_rejects_duplicate_case_ids(run_payload_factory) -> None:
    case_id = str(uuid.uuid4())
    with pytest.raises(ValidationError) as excinfo:
        RunCreate.model_validate(run_payload_factory([case_id, case_id]))
    assert "DUPLICATE_CASE_IDS" in str(excinfo.value)


@pytest.mark.parametrize("case_ids", [
    [],
    [str(uuid.uuid4()) for _ in range(201)],
    ["not-a-uuid"],
    [""],
], ids=["empty", "201_items", "bad_uuid", "blank"])
def test_run_create_case_id_limits(run_payload_factory, case_ids: list[str]) -> None:
    with pytest.raises(ValidationError):
        RunCreate.model_validate(run_payload_factory(case_ids))


def test_run_create_accepts_two_hundred_cases(run_payload_factory) -> None:
    ids = [str(uuid.uuid4()) for _ in range(200)]
    assert len(RunCreate.model_validate(run_payload_factory(ids)).case_ids) == 200


@pytest.mark.parametrize("limits", [
    {"concurrency": 0},
    {"concurrency": 4},
    {"concurrency": True},
    {"max_iterations": 0},
    {"timeout_s": 901},
])
def test_run_limits_are_tightened_by_contract_ranges(run_payload_factory, limits: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        RunCreate.model_validate(run_payload_factory([str(uuid.uuid4())], limits=limits))


def test_run_limits_defaults(run_payload_factory) -> None:
    payload = run_payload_factory([str(uuid.uuid4())])
    payload.pop("limits")
    run = RunCreate.model_validate(payload)
    assert run.limits.concurrency == 1
    assert run.limits.max_iterations == 12
    assert run.limits.timeout_s == 120
    assert run.judge_profile_id is None


def test_run_create_rejects_extra_field(run_payload_factory) -> None:
    with pytest.raises(ValidationError):
        RunCreate.model_validate(
            {**run_payload_factory([str(uuid.uuid4())]), "profile": "gpt"})


# ----------------------------------------------------------------- 读模型


def test_case_read_roundtrips_written_fields(case_payload_factory, new_uid) -> None:
    payload = make_case_payload()
    read = CaseRead.model_validate({
        **{k: v for k, v in payload.items() if k != "base_revision"},
        "id": new_uid(), "revision": 2, "created_at": TS, "updated_at": TS,
        "is_deleted": False,
    })
    assert read.revision == 2 and read.is_deleted is False
    assert read.name == payload["name"]
    assert read.turns[0].content == payload["turns"][0]["content"]


@pytest.mark.parametrize("revision", [0, -1, True, "1"])
def test_case_read_revision_is_positive_int(revision: object, new_uid) -> None:
    with pytest.raises(ValidationError):
        CaseRead.model_validate({
            **{k: v for k, v in make_case_payload().items() if k != "base_revision"},
            "id": new_uid(), "revision": revision, "created_at": TS,
            "updated_at": TS, "is_deleted": False,
        })


@pytest.mark.parametrize("suite_id_field", ["suite_id", "id"])
def test_uuid_fields_reject_non_uuid(suite_id_field: str, case_payload_factory) -> None:
    payload = {**make_case_payload(), "id": "case-1"}
    payload[suite_id_field] = "case-1"
    with pytest.raises(ValidationError):
        CaseRead.model_validate({
            **{k: v for k, v in payload.items() if k != "base_revision"},
            "revision": 1, "created_at": TS, "updated_at": TS, "is_deleted": False,
        })


def test_serialised_case_round_trips_back_to_identical_model(case_payload_factory) -> None:
    """类型化序列化口径：model_dump(mode="json") 的产物可被同一模型重新校验。"""
    case = CaseWrite.model_validate(make_case_payload())
    dumped = case.model_dump(mode="json")
    assert CaseWrite.model_validate(dumped) == case


# =========================================================================== SchemaSubset 限制
# plan T01 最后一条 checkbox：递归深度上限 16、节点上限 1000、禁远程 $ref 与代码执行。
# 这些断言直接进 answer_json / file_json 的 schema，因此必须由模型层强制，而不是
# 留给 WU-04 的评分器事后判断。


def _nested_array_schema(levels: int) -> dict[str, object]:
    node: dict[str, object] = {"type": "integer"}
    for _ in range(levels - 1):
        node = {"type": "array", "items": node}
    return node


def _wide_object_schema(property_count: int) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {f"p{i}": {"type": "string"} for i in range(property_count)},
    }


def _schema_case(schema: dict[str, object]) -> dict[str, object]:
    return {
        "id": "s1", "kind": "answer_json", "required": True,
        "schema": schema, "checks": [],
    }


@pytest.mark.parametrize("levels", [16], ids=["depth_16_ok"])
def test_schema_subset_accepts_maximum_nesting(levels: int) -> None:
    case = CaseWrite.model_validate(make_case_payload(assertions=[_schema_case(
        _nested_array_schema(levels))]))
    assert case.assertions[0].schema is not None


def test_schema_subset_rejects_nesting_beyond_depth_limit() -> None:
    excinfo = _reject_case(assertions=[_schema_case(_nested_array_schema(17))])
    assert "SCHEMA_TOO_DEEP" in str(excinfo)


def test_schema_subset_accepts_exactly_one_thousand_nodes() -> None:
    case = CaseWrite.model_validate(make_case_payload(assertions=[_schema_case(
        _wide_object_schema(999))]))  # 根节点 + 999 个属性 = 1000
    assert case.assertions[0].schema is not None


def test_schema_subset_rejects_more_than_one_thousand_nodes() -> None:
    excinfo = _reject_case(assertions=[_schema_case(_wide_object_schema(1000))])
    assert "SCHEMA_TOO_MANY_NODES" in str(excinfo)


@pytest.mark.parametrize("keyword,value", [
    ("$ref", "https://evil.example/schema.json#/definitions/Thing"),
    ("$ref", "#/definitions/Local"),
    ("pattern", "^a.*$"),
    ("$where", "1 == 1"),
    ("const", "arbitrary code"),
], ids=["remote_ref", "local_ref", "pattern", "where", "const"])
def test_schema_subset_rejects_reference_and_code_keywords(
    keyword: str, value: str
) -> None:
    """``extra="forbid"`` 顺带禁掉一切可指向远程或执行代码的关键词。"""
    _reject_case(assertions=[_schema_case({"type": "string", keyword: value})])


def test_schema_subset_rejects_unknown_type() -> None:
    _reject_case(assertions=[_schema_case({"type": "date"})])


@pytest.mark.parametrize("pointer", ["/a", "/a/0", "", "/a~0b", "/a~1b"],
                         ids=["root_key", "index", "whole_doc", "tilde0", "tilde1"])
def test_json_pointer_checks_accept_rfc6901_forms(pointer: str) -> None:
    case = CaseWrite.model_validate(make_case_payload(assertions=[_schema_case(
        {"type": "object"})]) )
    del case
    CaseWrite.model_validate(make_case_payload(assertions=[{
        **_schema_case({"type": "object"}),
        "checks": [{"pointer": pointer, "equals": None}],
    }]))


def test_json_pointer_check_rejects_unknown_check_field() -> None:
    _reject_case(assertions=[{
        **_schema_case({"type": "object"}),
        "checks": [{"pointer": "/a", "equals": 1, "tolerance": 0.1}],
    }])


# =========================================================================== Worker IPC 载荷
# 契约 §5：boot/result 只在父子进程私有管道里出现，形状由这里锁定。


def _agent_boot_payload() -> dict[str, object]:
    return {
        "type": "boot", "role": "agent", "protocol_version": 1,
        "attempt_id": str(uuid.uuid4()),
        "runtime_root": "C:/eval/attempt-1/runtime",
        "workspace": "C:/eval/attempt-1/workspace",
        "profile_bundle": {"provider": "openai", "model": "gpt-test"},
        "case_snapshot": make_case_payload(),
    }


def test_agent_boot_accepts_contract_shape() -> None:
    boot = TypeAdapter(WorkerBoot).validate_python(_agent_boot_payload())
    assert boot.role == "agent"
    assert boot.protocol_version == 1


@pytest.mark.parametrize("mutation", [
    {"protocol_version": 2},
    {"type": "start"},
    {"role": "shell"},
    {"workspace": ""},
], ids=["bad_protocol", "bad_type", "bad_role", "blank_workspace"])
def test_agent_boot_rejects_protocol_violations(mutation: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(WorkerBoot).validate_python({**_agent_boot_payload(), **mutation})


def test_judge_boot_carries_rubric_and_no_case_snapshot() -> None:
    payload = _agent_boot_payload()
    payload.pop("case_snapshot")
    payload.update({"role": "judge", "rubric": make_rubric_payload(),
                    "evidence": {"trace_refs": ["trace:x:1"]}})
    boot = TypeAdapter(WorkerBoot).validate_python(payload)
    assert boot.role == "judge"


def test_trace_event_type_set_is_closed() -> None:
    payload = {
        "schema_version": 1, "attempt_id": str(uuid.uuid4()), "seq": 1,
        "event_id": str(uuid.uuid4()), "type": "tool.executed", "ts": TS,
        "elapsed_ms": 3, "payload": {},
    }
    with pytest.raises(ValidationError):
        TraceEvent.model_validate(payload)
    payload["type"] = "tool.completed"
    assert TraceEvent.model_validate(payload).seq == 1


def test_trace_event_rejects_zero_seq_and_bad_timestamp() -> None:
    payload = {
        "schema_version": 1, "attempt_id": str(uuid.uuid4()), "seq": 1,
        "event_id": str(uuid.uuid4()), "type": "attempt.started", "ts": TS,
        "elapsed_ms": 0, "payload": {},
    }
    with pytest.raises(ValidationError):
        TraceEvent.model_validate({**payload, "seq": 0})
    with pytest.raises(ValidationError):
        TraceEvent.model_validate({**payload, "ts": "not-a-time"})


def _usage_payload() -> dict[str, object]:
    return {
        "source": "reported", "coverage": "complete", "physical_requests": 2,
        "iterations": 2, "missing_usage_calls": 0,
    }


def test_worker_result_separates_agent_and_judge_shapes() -> None:
    payload = {
        "type": "result", "attempt_id": str(uuid.uuid4()),
        "execution_status": "succeeded", "usage": _usage_payload(),
        "timing": {"agent_ms": 120, "total_ms": 130}, "errors": [],
        "trace_complete": True, "final_answer": "42", "stop_reason": "done",
    }
    assert AgentWorkerResult.model_validate(payload).final_answer == "42"

    judge_payload = {
        **{k: v for k, v in payload.items()
           if k not in {"final_answer", "stop_reason"}},
        "final_answer": None, "stop_reason": None,
        "judge": {"status": "completed", "model": "judge-test",
                  "version": "judge-v1", "calibrated": True, "error": None,
                  "dimensions": [{"id": "task_completion", "score": 4,
                                  "reason": "完整完成", "evidence_refs": []}]},
    }
    assert JudgeWorkerResult.model_validate(judge_payload).judge is not None


def test_judge_worker_result_cannot_carry_an_answer() -> None:
    """契约 §5：评分结果不能覆盖 Agent 的 execution_status 语义。"""
    payload = {
        "type": "result", "attempt_id": str(uuid.uuid4()),
        "execution_status": "succeeded", "usage": _usage_payload(),
        "timing": {}, "errors": [], "trace_complete": True,
        "final_answer": "42", "stop_reason": None, "judge": None,
    }
    with pytest.raises(ValidationError):
        JudgeWorkerResult.model_validate(payload)


@pytest.mark.parametrize("score", [-1, 5, True, "4"])
def test_judge_dimension_score_is_strict_zero_to_four(score: object) -> None:
    with pytest.raises(ValidationError):
        JudgeResult.model_validate({
            "status": "completed", "model": "m", "version": "judge-v1",
            "calibrated": False, "error": None,
            "dimensions": [{"id": "d", "score": score, "reason": "r",
                            "evidence_refs": []}],
        })


def test_assertion_result_keeps_unreadable_evidence_as_null() -> None:
    """契约 §4：证据读不到时 passed 必须是 null，不能退化成 false 混进通过率。"""
    result = AssertionResult.model_validate(
        {"id": "a1", "passed": None, "required": True,
         "message": "产物缺失", "evidence_refs": []}
    )
    assert result.passed is None

