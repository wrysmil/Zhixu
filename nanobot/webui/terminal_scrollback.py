"""Bounded UTF-8 tail buffer used to replay terminal output on takeover."""

from __future__ import annotations

MAX_SCROLLBACK_BYTES = 64 * 1024
MAX_SCROLLBACK_CHARS = 32_768


class TerminalScrollback:
    """Keep the newest output only; snapshot never begins mid-codepoint."""

    def __init__(
        self,
        max_bytes: int = MAX_SCROLLBACK_BYTES,
        max_chars: int = MAX_SCROLLBACK_CHARS,
    ) -> None:
        self._max_bytes = max(1, int(max_bytes))
        self._max_chars = max(1, int(max_chars))
        self._buffer = bytearray()
        self._truncated = False

    def append(self, text: str) -> None:
        if not text:
            return
        self._buffer.extend(text.encode("utf-8"))
        if len(self._buffer) > self._max_bytes:
            overflow = len(self._buffer) - self._max_bytes
            self._buffer = self._buffer[overflow:]
            while self._buffer and (self._buffer[0] & 0xC0) == 0x80:
                del self._buffer[0]
            self._truncated = True
        if len(self._buffer) > self._max_chars:
            text_now = self._buffer.decode("utf-8", "replace")
            if len(text_now) > self._max_chars:
                trimmed = text_now[-self._max_chars:]
                self._buffer = bytearray(trimmed.encode("utf-8"))
                self._truncated = True

    def snapshot(self) -> tuple[str, bool]:
        return self._buffer.decode("utf-8", "replace"), self._truncated
