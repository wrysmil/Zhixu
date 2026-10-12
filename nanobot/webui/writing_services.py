"""工作区作用域的写作服务：持有该工作区的文章仓储。

与 ``memory_services.py`` 同构：不认识 WebSocket/HTTP，也不认识具体持久化
细节，只把 ``WritingDatabase`` 包成 gateway 可注入的实例。一个 gateway 进程
绑定一个 ``workspace_path``，因此本实例在 gateway 生命周期内复用；文章库
``<workspace>/writing/state.db`` 与记忆库 ``<workspace>/memory/state.db`` 并列。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nanobot.writing.database import WritingDatabase
from nanobot.writing.repository import ArticleRepository


@dataclass(frozen=True)
class WritingServices:
    """Holds a workspace-scoped ``ArticleRepository`` for the WebUI gateway.

    Construction is cheap and idempotent (the schema is created with
    ``CREATE ... IF NOT EXISTS``), so the gateway can build one instance per
    workspace at startup.
    """

    workspace_id: str
    repository: ArticleRepository

    @classmethod
    def for_workspace(
        cls, workspace_id: str, workspace_path: Path | str
    ) -> "WritingServices":
        """Build a ready-to-use services instance for the given workspace path."""
        database = WritingDatabase(Path(workspace_path))
        database.ensure_schema()
        return cls(workspace_id=workspace_id, repository=ArticleRepository(database))
