from nanobot.webui.terminal_scrollback import (
    MAX_SCROLLBACK_BYTES,
    MAX_SCROLLBACK_CHARS,
    TerminalScrollback,
)


def test_small_text_roundtrips_without_truncation() -> None:
    buffer = TerminalScrollback()
    buffer.append("hello\nworld\n")
    text, truncated = buffer.snapshot()
    assert text == "hello\nworld\n"
    assert truncated is False


def test_byte_cap_keeps_tail_and_flags_truncation() -> None:
    buffer = TerminalScrollback()
    buffer.append("a" * (MAX_SCROLLBACK_BYTES + 1024))
    text, truncated = buffer.snapshot()
    assert len(text) <= MAX_SCROLLBACK_BYTES
    assert text.endswith("a" * 128)
    assert truncated is True


def test_char_cap_keeps_tail() -> None:
    buffer = TerminalScrollback()
    buffer.append("b" * (MAX_SCROLLBACK_CHARS + 512))
    text, truncated = buffer.snapshot()
    assert len(text) == MAX_SCROLLBACK_CHARS
    assert set(text) == {"b"}
    assert truncated is True


def test_utf8_boundary_never_starts_with_replacement_char() -> None:
    buffer = TerminalScrollback()
    buffer.append("é" * (MAX_SCROLLBACK_BYTES // 2 + 64))
    text, truncated = buffer.snapshot()
    assert truncated is True
    assert text
    assert not text.startswith("\ufffd")


def test_empty_snapshot() -> None:
    text, truncated = TerminalScrollback().snapshot()
    assert text == ""
    assert truncated is False
