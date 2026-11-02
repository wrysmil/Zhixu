"""provider 错误识别：记忆抽取链路不得把 HTTP 错误读成「模型输出」。

背景（2026-09-15 spec §2，systematic-debugging Phase 1 取证）：
provider 层在 HTTP 失败时**不抛异常**，而是返回
``LLMResponse(content="Error: {...}", finish_reason="error")``
（``nanobot/providers/openai_compat_provider.py`` 的 ``_handle_error``）。
记忆抽取原先只看 ``content``，于是 403 被报成 ``unparseable JSON``，
错误文本甚至会被当成草稿本正文落库。

本文件用复刻真实字段名的鸭子类型响应，验证仍在用的调用点都先问 ``response_error``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import MemoryExtractor
from nanobot.memory.llm_error import response_error
from nanobot.memory.profile_extractor import ProfileExtractor

# 真实错误体（来自 2026-09-15 22:10 的 gateway 日志）。
_AUTH_ERROR_BODY = (
    "Error: {'message': 'Authentication failed. Please check your credentials.', "
    "'type': 'permission_error'}"
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _ErrorResponse:
    """复刻 provider 在 HTTP 失败时返回的对象（字段名与 LLMResponse 一致）。"""

    def __init__(self, status: int = 403, body: str = _AUTH_ERROR_BODY) -> None:
        self.content = body
        self.finish_reason = "error"
        self.error_status_code = status
        self.error_kind = "authentication"
        self.error_type = "permission_error"
        self.error_code = None


class _OkResponse:
    def __init__(self, content: str) -> None:
        self.content = content
        self.finish_reason = "stop"


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 2048
    reasoning_effort = None


class _FakeProvider:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls = 0

    async def chat_with_retry(self, **_kwargs: Any) -> Any:
        self.calls += 1
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class _FakeRuntime:
    def __init__(self, response: Any) -> None:
        self.provider = _FakeProvider(response)
        self.model = "fake-model"
        self.generation = _FakeGeneration()
        self.context_window_tokens = 8192


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


def _capture(records: list[str], level: str = "WARNING") -> int:
    return logger.add(records.append, level=level, format="{message}")


# ---------------------------------------------------------------------------
# response_error
# ---------------------------------------------------------------------------


class TestResponseError:
    def test_none_for_normal_response(self):
        assert response_error(_OkResponse("{}")) is None

    def test_none_when_finish_reason_missing(self):
        """既有 fake（只有 content）必须继续走正常路径。"""

        class _Bare:
            content = "hello"

        assert response_error(_Bare()) is None

    def test_reports_status_and_error_type(self):
        reason = response_error(_ErrorResponse())
        assert reason is not None
        assert "403" in reason
        assert "permission_error" in reason

    def test_uses_declared_prefix(self):
        assert (response_error(_ErrorResponse()) or "").startswith("llm_error:")

    def test_degrades_when_metadata_missing(self):
        class _BareError:
            content = "Error: boom"
            finish_reason = "error"

        assert response_error(_BareError()) == "llm_error"


# ---------------------------------------------------------------------------
# MemoryExtractor._call_track
# ---------------------------------------------------------------------------


class TestCallTrack:
    async def test_auth_error_reported_as_call_failed(self, db):
        extractor = MemoryExtractor(db, _FakeRuntime(_ErrorResponse()))
        payload, error = await extractor._call_track(
            "episode", [{"role": "user", "content": "x"}]
        )
        assert payload is None
        assert error is not None
        assert error.startswith("call_failed:")
        assert "403" in error
        assert "invalid_json" not in error

    async def test_no_unparseable_json_warning(self, db):
        records: list[str] = []
        sink = _capture(records)
        try:
            extractor = MemoryExtractor(db, _FakeRuntime(_ErrorResponse()))
            await extractor._call_track("semantic", [{"role": "user", "content": "x"}])
        finally:
            logger.remove(sink)
        assert not any("unparseable JSON" in r for r in records)
        assert any("403" in r for r in records)

    async def test_normal_response_still_parsed(self, db):
        extractor = MemoryExtractor(db, _FakeRuntime(_OkResponse('{"summary": "ok"}')))
        payload, error = await extractor._call_track(
            "episode", [{"role": "user", "content": "x"}]
        )
        assert payload == {"summary": "ok"}
        assert error is None

    async def test_garbage_content_still_invalid_json(self, db):
        extractor = MemoryExtractor(db, _FakeRuntime(_OkResponse("这不是 JSON")))
        payload, error = await extractor._call_track(
            "episode", [{"role": "user", "content": "x"}]
        )
        assert payload is None
        assert error == "invalid_json"


# ---------------------------------------------------------------------------
# ProfileExtractor
# ---------------------------------------------------------------------------


class TestProfileExtractor:
    async def test_error_reason_surfaces(self):
        extractor = ProfileExtractor(_FakeRuntime(_ErrorResponse()))
        result = await extractor.extract(
            [
                {"role": "user", "content": "帮我看看金价"},
                {"role": "assistant", "content": "好的"},
            ],
            "ep-1",
        )
        assert result.error is not None
        assert "403" in result.error
        assert result.error != "invalid_json"
