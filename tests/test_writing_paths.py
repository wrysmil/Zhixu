"""写作域数据目录入口 ``get_writing_dir``（A1.7）。

契约 §2 把文章库定在 ``<workspace_path>/writing/state.db``，与记忆库
``<workspace>/memory/state.db`` 并列。本文件锁定两件事：路径推导正确，且
**读取路径不 mkdir**（建目录是 ``WritingDatabase`` 在真要写库时的职责）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.config.paths import get_writing_dir
from nanobot.writing.database import WritingDatabase


def test_writing_dir_is_workspace_scoped(tmp_path: Path) -> None:
    assert get_writing_dir(tmp_path / "proj") == tmp_path / "proj" / "writing"


def test_writing_dir_does_not_create_anything(tmp_path: Path) -> None:
    """目录由 WritingDatabase 建，读取路径不得顺手 mkdir。"""
    workspace = tmp_path / "proj"
    path = get_writing_dir(workspace)
    assert not path.exists()
    assert path == workspace / "writing"


def test_writing_dir_accepts_str_workspace(tmp_path: Path) -> None:
    assert get_writing_dir(str(tmp_path / "proj")) == tmp_path / "proj" / "writing"


def test_default_workspace_writing_dir(monkeypatch: pytest.MonkeyPatch,
                                       tmp_path: Path) -> None:
    """省略 workspace 时落到默认工作区（HOME 指向临时目录，不碰真实数据）。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    path = get_writing_dir()
    assert path.name == "writing"
    assert path.parent == tmp_path / ".nanobot" / "workspace"
    assert not path.exists()


def test_database_db_path_matches_writing_dir(tmp_path: Path) -> None:
    """WU-01 的默认库路径与本目录推导一致（``<workspace>/writing/state.db``）。"""
    database = WritingDatabase(tmp_path)
    assert database.db_path == get_writing_dir(tmp_path) / "state.db"


def test_database_db_path_is_overridable(tmp_path: Path) -> None:
    override = tmp_path / "elsewhere" / "state.db"
    assert WritingDatabase(tmp_path, db_path=override).db_path == override
