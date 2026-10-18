"""工具活动的参数/输出投影。

只放行白名单字段：先脱敏，再截断，最后才进执行账本（落盘 + 下发）。原始参数与
原始输出绝不外传——契约 §「不外传」那条的落地点在这里。
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

# 单个字段上限：与 events.py 的帧预算一起决定一条 activity 的最坏体积。
ACTIVITY_TEXT_MAX_CHARS: Final = 600
# 单个键值对里值本身的长度上限，避免一个超长路径吃掉整条摘要。
_VALUE_MAX_CHARS: Final = 200
# 失败时的错误首行上限。
_ERROR_MAX_CHARS: Final = 300

_DETAIL_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "read_file": ("path", "file_path"),
    "read_pdf": ("path", "file_path"),
    "write_file": ("path", "file_path"),
    "edit_file": ("path", "file_path"),
    "list_dir": ("path", "file_path"),
    "find_files": ("pattern", "glob", "path"),
    "grep": ("pattern", "query", "path", "glob"),
    "search_code": ("pattern", "query", "path", "glob"),
    "web_search": ("query", "q"),
    "x_search": ("query", "q"),
    "web_fetch": ("url",),
    "memory_search": ("query", "q"),
    "exec": ("command", "cmd"),
    "exec_session": ("command", "cmd", "session_id"),
    "run_cli_app": ("app", "name", "command", "args"),
}

# 命令类工具：截到首行，避免把多行脚本整段带出来。
_COMMAND_TOOLS: Final = frozenset({"exec", "exec_session", "run_cli_app"})

_OUTPUT_FIELD: Final[dict[str, str]] = {
    "read_file": "lines",
    "read_pdf": "lines",
    "grep": "hits",
    "search_code": "hits",
    "find_files": "files",
    "list_dir": "entries",
    "web_fetch": "lines",
}

_EXIT_CODE_RE: Final = re.compile(r"^\s*Exit code:\s*(-?\d+)\s*$", re.MULTILINE)

# 只有出现在投影白名单里的工具才允许带失败原因；MCP 与未知工具的错误文本一律不外传。
_PROJECTABLE_TOOLS: Final = frozenset(_DETAIL_KEYS) | frozenset(_OUTPUT_FIELD) | _COMMAND_TOOLS

_SECRET_KEY_RE: Final = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?key|private[_-]?key|token|secret|password|passwd|credential)"
    r"(\s*[:=]\s*)(\S+)",
)
_BEARER_RE: Final = re.compile(r"(?i)\b(Bearer)\s+\S+")
_TOKEN_SHAPES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bgh[pous]_[A-Za-z0-9]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-.]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
_URL_CREDENTIALS_RE: Final = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/\s@]+@")
_HOME_DIR_RE: Final = re.compile(r"/(?:Users|home)/[^/\s]+/")
_MASK: Final = "***"


def redact_activity_text(value: str) -> str:
    """脱敏：凭据、URL 内嵌账号、绝对家目录。只做减法，不改变可读结构。"""
    text = _BEARER_RE.sub(f"Bearer {_MASK}", value)
    text = _SECRET_KEY_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_MASK}", text)
    for pattern in _TOKEN_SHAPES:
        text = pattern.sub(_MASK, text)
    text = _URL_CREDENTIALS_RE.sub(lambda m: f"{m.group(1)}{_MASK}@", text)
    return _HOME_DIR_RE.sub("~/", text)


def truncate_activity_text(value: str, limit: int = ACTIVITY_TEXT_MAX_CHARS) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: max(0, limit - 1)]}…"


def project_tool_activity(
    tool_name: str,
    arguments: Any,
    output: Any,
    *,
    is_error: bool,
) -> tuple[str | None, str | None]:
    """返回 ``(参数摘要, 输出摘要)``。非白名单工具或没有可用内容时返回 ``None``。"""
    return (
        _project_detail(tool_name, arguments),
        _project_output(tool_name, output, is_error=is_error),
    )


def _project_detail(tool_name: str, arguments: Any) -> str | None:
    keys = _DETAIL_KEYS.get(tool_name)
    if keys is None or not isinstance(arguments, Mapping):
        return None
    pairs: list[str] = []
    for key in keys:
        raw = _scalar(arguments.get(key))
        if raw is None:
            continue
        value = raw.splitlines()[0] if tool_name in _COMMAND_TOOLS else raw
        value = truncate_activity_text(
            redact_activity_text(" ".join(value.split())), _VALUE_MAX_CHARS,
        )
        if not value:
            continue
        pairs.append(f"{key}={value}")
    if not pairs:
        return None
    return truncate_activity_text(" · ".join(pairs))


def _project_output(tool_name: str, output: Any, *, is_error: bool) -> str | None:
    text = output.strip() if isinstance(output, str) else ""
    if is_error:
        if not text or tool_name not in _PROJECTABLE_TOOLS:
            # 没有可外传的错误文案，或工具不在白名单：不留占位，也不带出错误正文。
            return None
        first_line = text.splitlines()[0]
        return f"error={truncate_activity_text(redact_activity_text(first_line), _ERROR_MAX_CHARS)}"
    field = _OUTPUT_FIELD.get(tool_name)
    if field is not None:
        # 计数类工具的空输出是有意义的信息（0 行 / 0 命中）。
        return f"{field}={_count_non_empty_lines(text)}"
    if tool_name in _COMMAND_TOOLS:
        match = _EXIT_CODE_RE.search(text)
        return f"exit={match.group(1)}" if match else None
    return None


def _count_non_empty_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def _scalar(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return " ".join(part for item in value if (part := _scalar(item)))
    return None
