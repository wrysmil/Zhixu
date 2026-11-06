"""注入侧：文件模式下注入两份索引、不再注入核心事实；关闭开关回到旧路径。"""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.context import ContextBuilder
from nanobot.memory.files import GLOBAL_SCOPE, PROJECT_SCOPE, MemoryFileStore


def _home(tmp_path: Path, monkeypatch) -> Path:
    """把 ``Path.home()`` 指到临时目录，隔离用户级记忆目录。"""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_file_mode_injects_memory_doc(monkeypatch, tmp_path: Path) -> None:
    home = _home(tmp_path, monkeypatch)
    store = MemoryFileStore.for_project(str(tmp_path))
    store.write_memory_doc(
        GLOBAL_SCOPE,
        "# 全局记忆\n\n## 偏好\n- 解释机制先讲因果",
    )

    builder = ContextBuilder(tmp_path, memory_file_mode=True)
    prompt = builder.build_system_prompt(workspace=tmp_path)

    assert "解释机制先讲因果" in prompt
    assert "长期记忆（正文）" in prompt
    assert str(home / ".nanobot" / "memory") in prompt


def test_file_mode_skips_core_facts(monkeypatch, tmp_path: Path) -> None:
    _home(tmp_path, monkeypatch)
    builder = ContextBuilder(tmp_path, memory_file_mode=True)
    prompt = builder.build_system_prompt(workspace=tmp_path)
    assert "核心记忆" not in prompt


def test_legacy_mode_has_no_index(monkeypatch, tmp_path: Path) -> None:
    _home(tmp_path, monkeypatch)
    store = MemoryFileStore.for_project(str(tmp_path))
    store.write_memory(GLOBAL_SCOPE, "x", "内容", description="d")

    builder = ContextBuilder(tmp_path, memory_file_mode=False)
    prompt = builder.build_system_prompt(workspace=tmp_path)
    assert "记忆索引" not in prompt


def test_project_doc_uses_project_scope(monkeypatch, tmp_path: Path) -> None:
    _home(tmp_path, monkeypatch)
    store = MemoryFileStore.for_project(str(tmp_path))
    store.write_memory_doc(PROJECT_SCOPE, "# 项目记忆\n\n## 技术栈\n- 本项目用 bun")

    builder = ContextBuilder(tmp_path, memory_file_mode=True)
    prompt = builder.build_system_prompt(workspace=tmp_path)
    assert "本项目用 bun" in prompt
