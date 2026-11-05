"""Tests for agent identity.md template (T-15)."""

from __future__ import annotations

from pathlib import Path

IDENTITY = Path("nanobot/templates/agent/identity.md")


class TestIdentityTemplate:
    def test_memory_search_tool_documented(self) -> None:
        text = IDENTITY.read_text(encoding="utf-8")
        assert "memory_search" in text

    def test_memory_search_has_description(self) -> None:
        text = IDENTITY.read_text(encoding="utf-8")
        # Must explain proactive vs passive retrieval
        assert "proactive" in text.lower() or "检索" in text

    def test_memory_files_documented(self) -> None:
        text = IDENTITY.read_text(encoding="utf-8")
        # 文件模式：记忆本体是文件，模型用文件工具写；记忆库写入工具不再出现。
        assert "write_file" in text
        assert "~/.nanobot/memory/" in text
        assert "add_memory" not in text
        assert "确认" in text or "confirmed" in text.lower()

    def test_not_too_long(self) -> None:
        text = IDENTITY.read_text(encoding="utf-8")
        assert len(text) < 4000
