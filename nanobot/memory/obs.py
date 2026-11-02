"""记忆链路可观测性：文本日志 + 结构化事件（JSONL）统一出口。

为什么单独成文件：提取、检索、注入三处的日志点如果各写各的，字段命名、
截断策略和开关会各漂各的。这里把「一条记忆事件」定义成唯一产物：

* 文本出口：loguru（stderr + 可选 ``logs/memory.log``，轮转/保留）；
* 结构化出口：JSONL（可选 ``logs/memory-events.jsonl``，超限轮转为 ``.1``）。

调用方只做 ``emit(kind, message, **fields)``，文本格式留在各自的 trace 模块。
日志永远不能打断记忆链路：所有异常在 :func:`emit` 内吞掉。
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.paths import get_logs_dir

#: 事件 kind 闭集。前 6 个与记忆评测契约 §4 的 ``memory.event.kind`` 对齐；
#: 其余是产品排障用的超集，消费方按已知 kind 过滤即可。
EVENT_KINDS = frozenset(
    {
        "extraction_started",
        "extraction_completed",
        "extraction_skipped",
        "extraction_failed",
        "memory_changed",
        "injected",
        "retrieval_started",
        "recall_candidates",
        "retrieval_deduped",
        "reranked",
        "retrieval_skipped",
        "retrieval_elapsed",
        "retrieval_time_browse",
        "retrieval_hybrid",
    }
)

_TEXT_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <5} | {message}"
)


@dataclass(frozen=True)
class MemoryLogSettings:
    """记忆日志配置的运行时快照（由 config schema 的 ``to_settings`` 生成）。"""

    enabled: bool = True
    console_level: str = "INFO"
    include_content: bool = True
    max_content_chars: int = 240
    max_items: int = 8
    file_enabled: bool = True
    file_path: str = ""
    events_enabled: bool = False
    events_path: str = ""
    rotation: str = "10 MB"
    retention: str = "7 days"
    events_max_mb: int = 20

    def text_path(self) -> Path:
        """文本日志路径；空 = ``<data_dir>/logs/memory.log``。"""
        if self.file_path.strip():
            return Path(self.file_path).expanduser()
        return get_logs_dir() / "memory.log"

    def event_path(self) -> Path:
        """结构化事件路径；空 = ``<data_dir>/logs/memory-events.jsonl``。"""
        if self.events_path.strip():
            return Path(self.events_path).expanduser()
        return get_logs_dir() / "memory-events.jsonl"

    def sink_signature(self) -> tuple[Any, ...]:
        """会影响 sink 装配的字段；变化时重建 sink，其余字段即时生效。"""
        return (
            self.enabled,
            self.console_level,
            self.file_enabled,
            self.file_path,
            self.rotation,
            self.retention,
            self.events_enabled,
            self.events_path,
            self.events_max_mb,
        )


_settings = MemoryLogSettings()
_provider: Callable[[], MemoryLogSettings] | None = None
_configured = False
_signature: tuple[Any, ...] | None = None
_text_sink: int | None = None
_event_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 装配 / 刷新
# ---------------------------------------------------------------------------


def configure(provider: Callable[[], MemoryLogSettings] | None) -> None:
    """装配配置来源。``provider`` 每次读取最新配置，支持免重启热更新。"""
    global _provider, _configured
    _provider = provider
    _configured = True
    _refresh(force=True)


def configure_from_config(config: Any) -> None:
    """从根 ``Config`` 装配；配置缺失时保持默认（不抛）。"""
    try:
        defaults = config.agents.defaults
        logging_config = defaults.memory_logging
    except AttributeError:
        return
    configure(lambda: logging_config.to_settings())


def reset() -> None:
    """恢复未装配状态并移除本模块的 sink（测试用）。"""
    global _provider, _configured, _settings, _signature, _text_sink
    _provider = None
    _configured = False
    _settings = MemoryLogSettings()
    _signature = None
    if _text_sink is not None:
        with suppress(Exception):
            logger.remove(_text_sink)
        _text_sink = None


def _refresh(force: bool = False) -> None:
    global _settings, _signature
    if _provider is not None:
        try:
            candidate = _provider()
        except Exception:  # noqa: BLE001 - 配置读取失败按上一份继续
            candidate = _settings
        if isinstance(candidate, MemoryLogSettings):
            _settings = candidate
    signature = _settings.sink_signature() if _configured else None
    if not force and signature == _signature:
        return
    _signature = signature
    _rebuild_text_sink()


def _rebuild_text_sink() -> None:
    global _text_sink
    if _text_sink is not None:
        with suppress(Exception):
            logger.remove(_text_sink)
        _text_sink = None
    if not (_configured and _settings.enabled and _settings.file_enabled):
        return
    try:
        path = _settings.text_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _text_sink = logger.add(
            str(path),
            format=_TEXT_FORMAT,
            level=_log_level(_settings.console_level),
            rotation=_settings.rotation,
            retention=_settings.retention,
            encoding="utf-8",
            enqueue=False,
            backtrace=False,
            diagnose=False,
            filter=lambda record: record["extra"].get("memory_log") is True,
        )
    except Exception as exc:  # noqa: BLE001 - 落盘失败退回 stderr
        with suppress(Exception):
            logger.debug("memory log sink unavailable: {}", exc)


# ---------------------------------------------------------------------------
# 读取（调用方用这些函数做门禁与截断）
# ---------------------------------------------------------------------------


def settings() -> MemoryLogSettings:
    _refresh()
    return _settings


def enabled() -> bool:
    _refresh()
    return _settings.enabled


def max_items() -> int:
    _refresh()
    return _settings.max_items


def max_content_chars() -> int:
    _refresh()
    return _settings.max_content_chars


def level_name() -> str:
    _refresh()
    return _log_level(_settings.console_level)


def clip(text: Any, *, limit: int | None = None) -> str:
    """压成单行并按配置截断；``includeContent=false`` 时只留长度。"""
    raw = str(text or "")
    if not _settings.include_content:
        return f"<omitted {len(raw)} chars>"
    flat = " ".join(raw.split())
    cap = limit if limit is not None else _settings.max_content_chars
    return flat if len(flat) <= cap else flat[:cap] + "…"


# ---------------------------------------------------------------------------
# 出口
# ---------------------------------------------------------------------------


def emit(kind: str, message: str, *, level: str | None = None, **fields: Any) -> None:
    """记录一条记忆事件：文本走 loguru，结构化写 JSONL（开启时）。"""
    try:
        _refresh()
        if not _settings.enabled or not message:
            return
        payload: dict[str, Any] = {"ts": _now_iso(), "kind": kind}
        payload.update({key: _jsonable(value) for key, value in fields.items()})
        record = logger.bind(memory_log=True, memory_event=payload)
        record.log(level or _log_level(_settings.console_level), message)
        if _configured and _settings.events_enabled:
            _write_event(payload)
    except Exception as exc:  # noqa: BLE001 - 日志失败绝不打断业务
        with suppress(Exception):
            logger.debug("memory observability emit failed: {}", exc)


def _write_event(payload: dict[str, Any]) -> None:
    path = _settings.event_path()
    limit_bytes = max(1, int(_settings.events_max_mb)) * 1024 * 1024
    with _event_lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size >= limit_bytes:
                backup = path.with_name(path.name + ".1")
                with suppress(OSError):
                    backup.unlink()
                path.replace(backup)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            with suppress(Exception):
                logger.debug("memory event write failed: {}", exc)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _log_level(name: str) -> str:
    return "DEBUG" if str(name).strip().upper() == "DEBUG" else "INFO"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return str(value)
