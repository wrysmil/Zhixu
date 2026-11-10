"""评测包在 **gateway 的导入顺序** 下不得出现循环导入。

这条不是形式检查：知识专项的 ``__init__`` 一旦在包初始化时导入 ``routes``，
就会形成 ``services → knowledge → routes → memory.routes → server → services``
的闭环，gateway 启动直接 ``ImportError``——而 pytest 的导入顺序恰好绕开了它，
所以只有按真实启动顺序跑才能钉住。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
    )


def test_importing_services_first_does_not_circular_import() -> None:
    """gateway 先 import ``services``（经 ``profiles``），必须成功。"""
    result = _run("import nanobot.evaluation.services")
    assert result.returncode == 0, result.stderr


def test_knowledge_package_exposes_routes_without_importing_them_eagerly() -> None:
    """包的导出仍然可用，但导入包本身不触发 routes。"""
    result = _run(
        "from nanobot.evaluation.knowledge import knowledge_route_defs, KNOWLEDGE_API_PREFIX;"
        "assert KNOWLEDGE_API_PREFIX == '/api/eval/v1';"
        "assert len(knowledge_route_defs(KNOWLEDGE_API_PREFIX)) == 13"
    )
    assert result.returncode == 0, result.stderr
