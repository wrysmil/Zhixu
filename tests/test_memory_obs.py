"""记忆观测层单测：配置解析、截断、落盘、关闭开关与 trace 联动。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.memory import obs
from nanobot.memory.obs import MemoryLogSettings
from nanobot.memory.retrieval import trace


@pytest.fixture(autouse=True)
def _reset_obs():
    obs.reset()
    yield
    obs.reset()


def _settings(tmp_path: Path, **overrides: object) -> MemoryLogSettings:
    base: dict[str, object] = {
        "file_path": str(tmp_path / "memory.log"),
        "events_path": str(tmp_path / "events.jsonl"),
        "events_enabled": True,
    }
    base.update(overrides)
    return MemoryLogSettings(**base)  # type: ignore[arg-type]


def _events(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_config_accepts_camel_and_snake_case() -> None:
    from nanobot.config.schema import AgentDefaults

    camel = AgentDefaults.model_validate(
        {
            "memoryLogging": {
                "enabled": False,
                "maxContentChars": 120,
                "eventsEnabled": True,
            }
        }
    )
    settings = camel.memory_logging.to_settings()
    assert settings.enabled is False
    assert settings.max_content_chars == 120
    assert settings.events_enabled is True
    assert settings.file_enabled is True  # 默认落盘

    snake = AgentDefaults.model_validate({"memory_logging": {"maxItems": 3}})
    assert snake.memory_logging.max_items == 3


def test_emit_writes_text_and_jsonl(tmp_path: Path) -> None:
    obs.configure(lambda: _settings(tmp_path))
    obs.emit("extraction_completed", "done memories=2", session="s1", memories=2)

    text = (tmp_path / "memory.log").read_text(encoding="utf-8")
    assert "done memories=2" in text

    events = _events(tmp_path)
    assert len(events) == 1
    assert events[0]["kind"] == "extraction_completed"
    assert events[0]["session"] == "s1"
    assert events[0]["memories"] == 2
    assert str(events[0]["ts"])


def test_disabled_writes_nothing(tmp_path: Path) -> None:
    obs.configure(lambda: _settings(tmp_path, enabled=False))
    obs.emit("injected", "should not appear", session="s1")

    assert not (tmp_path / "memory.log").exists()
    assert not (tmp_path / "events.jsonl").exists()
    assert obs.enabled() is False


def test_clip_truncates_and_redacts(tmp_path: Path) -> None:
    obs.configure(lambda: _settings(tmp_path, max_content_chars=10))
    clipped = obs.clip("a" * 30)
    assert clipped == "a" * 10 + "…"

    obs.configure(lambda: _settings(tmp_path, include_content=False))
    assert obs.clip("hello").startswith("<omitted")


def test_sink_rebuilds_when_path_changes(tmp_path: Path) -> None:
    state = {"settings": _settings(tmp_path, file_path=str(tmp_path / "a.log"))}
    obs.configure(lambda: state["settings"])
    obs.emit("injected", "first")

    state["settings"] = _settings(tmp_path, file_path=str(tmp_path / "b.log"))
    obs.emit("injected", "second")

    assert "first" in (tmp_path / "a.log").read_text(encoding="utf-8")
    assert "second" in (tmp_path / "b.log").read_text(encoding="utf-8")


def test_trace_events_share_trace_id(tmp_path: Path) -> None:
    obs.configure(lambda: _settings(tmp_path))
    trace.start(
        raw_query="杭州天气",
        cleaned_query="杭州天气",
        recent_count=2,
        tokens=400,
    )
    trace.skipped(query="杭州天气", reason="no_match")

    events = _events(tmp_path)
    assert [event["kind"] for event in events] == [
        "retrieval_started",
        "retrieval_skipped",
    ]
    assert events[0]["trace_id"] == events[1]["trace_id"]
    assert events[0]["trace_id"] != "-"


def test_shortlist_emits_reranked_for_structured_hits(tmp_path: Path) -> None:
    from types import SimpleNamespace

    obs.configure(lambda: _settings(tmp_path))
    trace.start(raw_query="猫", cleaned_query="猫", recent_count=1, tokens=100)
    candidate = SimpleNamespace(
        hit=SimpleNamespace(kind="memory", rank=1),
        memory_id="m-1",
        composite_score=0.5,
        content="用户养了一只猫",
    )
    trace.shortlist(ranked=[candidate], selected=[candidate], limit=10, tokens=100)

    events = _events(tmp_path)
    assert [event["kind"] for event in events] == ["retrieval_started", "reranked"]
    assert events[1]["surface"] == "hits"
    assert events[1]["selected"] == 1
    assert events[0]["trace_id"] == events[1]["trace_id"]


def test_emit_never_raises_on_bad_path(tmp_path: Path) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("x", encoding="utf-8")
    obs.configure(lambda: _settings(tmp_path, file_path=str(blocker / "memory.log")))
    obs.emit("injected", "still safe")
    # 文本 sink 建不起来不影响进程；事件仍写默认独立路径。
    assert (tmp_path / "events.jsonl").exists()
