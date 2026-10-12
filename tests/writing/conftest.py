"""写作域测试共享装置。

所有写作测试都只允许写入 ``tmp_path`` 临时工作区：autouse 装置把 HOME/USERPROFILE
指向临时目录，任何意外触碰用户真实 ``<workspace>/writing/state.db`` 的写法都会在
收集阶段就失败，而不是污染开发机数据。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_writing_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    yield
