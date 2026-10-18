"""工具活动参数/输出投影：只放行白名单字段，先脱敏再截断。"""
from __future__ import annotations

import pytest

from nanobot.subagent_executions.activity_detail import (
    ACTIVITY_TEXT_MAX_CHARS,
    project_tool_activity,
    redact_activity_text,
)


def detail_of(tool_name: str, arguments: dict | None, output: str = "", *, is_error: bool = False):
    return project_tool_activity(tool_name, arguments, output, is_error=is_error)[0]


def output_of(tool_name: str, arguments: dict | None, output: str = "", *, is_error: bool = False):
    return project_tool_activity(tool_name, arguments, output, is_error=is_error)[1]


class TestDetail:
    def test_reads_path_for_file_tools(self):
        assert detail_of("read_file", {"path": "src/app.py"}) == "path=src/app.py"
        assert detail_of("list_dir", {"path": "webui/src"}) == "path=webui/src"
        assert detail_of("write_file", {"path": "a.txt"}) == "path=a.txt"

    def test_keeps_pattern_and_scope_for_search_tools(self):
        assert detail_of("grep", {"pattern": "TODO", "path": "webui/src"}) == (
            "pattern=TODO · path=webui/src"
        )
        assert detail_of("find_files", {"pattern": "**/*.py"}) == "pattern=**/*.py"

    def test_keeps_query_and_url_for_web_tools(self):
        assert detail_of("web_search", {"query": "今日金价"}) == "query=今日金价"
        assert detail_of("web_fetch", {"url": "https://example.com/a"}) == (
            "url=https://example.com/a"
        )

    def test_keeps_first_line_of_command_only(self):
        detail = detail_of("exec", {"command": "npm test\necho hi"})
        assert detail == "command=npm test"

    def test_unknown_tools_are_not_projected(self):
        assert detail_of("mcp_some_tool", {"token": "abc"}) is None
        assert detail_of("spawn", {"task": "do things"}) is None

    def test_missing_arguments_are_not_projected(self):
        assert detail_of("read_file", None) is None
        assert detail_of("read_file", {}) is None
        assert detail_of("read_file", {"other": "x"}) is None

    def test_redacts_credentials_before_projection(self):
        detail = detail_of("read_file", {"path": "/Users/mima/.env"})
        assert detail == "path=~/.env"

        assert redact_activity_text("Bearer sk-live-abcdef123456") == "Bearer ***"
        assert redact_activity_text("TOKEN=ghp_abcdefghijklmno") == "TOKEN=***"
        assert redact_activity_text("password: hunter2") == "password: ***"
        assert redact_activity_text("https://user:pw@host/x") == "https://***@host/x"

    def test_truncates_long_values(self):
        detail = detail_of("read_file", {"path": "a" * 900}) or ""
        assert len(detail) <= ACTIVITY_TEXT_MAX_CHARS
        assert detail.endswith("…")


class TestOutput:
    def test_counts_read_lines(self):
        assert output_of("read_file", {"path": "a.py"}, "l1\nl2\nl3") == "lines=3"

    def test_counts_search_hits(self):
        assert output_of("grep", {"pattern": "x"}, "a.py:1:x\nb.py:2:x") == "hits=2"

    def test_counts_found_files_and_entries(self):
        assert output_of("find_files", {"pattern": "*.py"}, "a.py\nb.py") == "files=2"
        assert output_of("list_dir", {"path": "."}, "a\nb\nc") == "entries=3"

    def test_reports_exit_code_for_commands(self):
        assert output_of("exec", {"command": "npm test"}, "boom\nExit code: 1") == "exit=1"
        assert output_of("exec", {"command": "ls"}, "a\nExit code: 0") == "exit=0"

    def test_command_exit_code_absent_when_not_reported(self):
        assert output_of("exec", {"command": "sleep 1"}, "still running") is None

    def test_failures_keep_first_error_line_only(self):
        assert output_of(
            "read_file", {"path": "a.py"}, "Error: File not found: a.py\nmore detail",
            is_error=True,
        ) == "error=Error: File not found: a.py"

    def test_failures_redact_and_truncate(self):
        assert output_of("grep", {"pattern": "x"}, "Error: api_key=abcdef123456", is_error=True) == (
            "error=Error: api_key=***"
        )

    def test_unknown_tools_and_tools_without_output_have_none(self):
        assert output_of("spawn", {"task": "x"}, "done") is None
        assert output_of("write_file", {"path": "a"}, "wrote 3 bytes") is None

    def test_failures_outside_the_whitelist_leak_no_error_text(self):
        # 白名单之外（含 MCP 与 spawn）的失败正文一律不外传：只留状态，不留原因。
        assert output_of("mcp_tool", {"q": "x"}, "boom\ntraceback", is_error=True) is None
        assert output_of("spawn", {"task": "x"}, "Traceback (most recent call last)", is_error=True) is None

    def test_empty_success_output_still_counts(self):
        assert output_of("grep", {"pattern": "x"}, "") == "hits=0"


class TestPair:
    def test_returns_detail_and_output_together(self):
        assert project_tool_activity(
            "read_file", {"path": "a.py"}, "l1\nl2", is_error=False,
        ) == ("path=a.py", "lines=2")

    def test_never_raises_on_odd_argument_shapes(self):
        for arguments in (None, [], "text", 42, {"path": {"nested": 1}}, {"path": ["a"]}):
            detail, output = project_tool_activity("read_file", arguments, None, is_error=False)
            assert detail is None or isinstance(detail, str)
            assert output is None or isinstance(output, str)


class TestRedaction:
    @pytest.mark.parametrize(
        "raw",
        [
            "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "AWS AKIAIOSFODNN7EXAMPLE",
            "xoxb-1234567890-abcdefghij",
            "-----BEGIN PRIVATE KEY-----",
            "postgres://user:secret@db.internal:5432/app",
        ],
    )
    def test_known_secret_shapes_are_masked(self, raw: str) -> None:
        assert raw.split()[-1] not in redact_activity_text(raw)

    def test_home_directory_is_compacted(self):
        assert redact_activity_text("/home/other/work/x.py") == "~/work/x.py"
        assert redact_activity_text("/Users/mima/Documents/x.py") == "~/Documents/x.py"

    def test_plain_text_passes_through(self):
        assert redact_activity_text("src/app.py") == "src/app.py"
