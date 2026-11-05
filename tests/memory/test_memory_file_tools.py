"""文件模式下的工具边界：记忆目录可写、其它目录不可写、记忆库写入工具撤下。"""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.filesystem import WriteFileTool
from nanobot.agent.tools.memory_admin import AddMemoryTool
from nanobot.config.schema import ToolsConfig, _resolve_tool_config_refs

_resolve_tool_config_refs()


def _ctx(tmp_path: Path, *, memory_mode: bool) -> ToolContext:
    return ToolContext(
        config=ToolsConfig(restrict_to_workspace=True),
        workspace=str(tmp_path / "ws"),
        attributes={"memory_file_mode": memory_mode},
    )


@pytest.mark.asyncio
async def test_write_file_allows_memory_dir(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    tool = WriteFileTool.create(_ctx(tmp_path, memory_mode=True))
    target = home / ".nanobot" / "memory" / "note.md"
    result = await tool.execute(path=str(target), content="记住：提交信息用中文")

    assert "Successfully wrote" in str(result)
    assert target.read_text(encoding="utf-8") == "记住：提交信息用中文"


@pytest.mark.asyncio
async def test_write_file_rejects_other_dirs(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    tool = WriteFileTool.create(_ctx(tmp_path, memory_mode=True))
    outside = tmp_path / "outside.txt"
    result = await tool.execute(path=str(outside), content="不该写")

    assert "Error" in str(result)
    assert not outside.exists()


def test_memory_admin_tools_disabled_in_file_mode(tmp_path: Path) -> None:
    assert AddMemoryTool.enabled(_ctx(tmp_path, memory_mode=True)) is False
