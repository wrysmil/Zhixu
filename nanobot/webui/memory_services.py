"""Workspace-scoped access to the three-layer SQLite memory store.

This module is transport-neutral — it does not import any websocket or HTTP
machinery. Callers (settings routes, tests) construct ``MemoryServices`` once
per workspace and share it across the gateway lifetime.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from nanobot.memory.database import MemoryDatabase


@dataclass(frozen=True)
class MemoryServices:
    """Holds a workspace-scoped ``MemoryDatabase`` for the WebUI gateway.

    The database is shared across processes — SQLite serialises writes with
    its internal lock. Construction is cheap: the caller can build one
    instance per gateway and pass it via ``settings.memory`` wiring.
    """

    workspace_id: str
    database: MemoryDatabase
    session_metadata: Callable[[str], dict[str, Any] | None] | None = None

    @classmethod
    def for_workspace(
        cls, workspace_id: str, workspace_path: Path,
        *, session_metadata: Callable[[str], dict[str, Any] | None] | None = None,
    ) -> "MemoryServices":
        """Build a ready-to-use services instance for the given workspace path.

        The schema is created lazily the first time it is absent, so
        construction is idempotent across gateway restarts.
        """
        database = MemoryDatabase(workspace_path)
        database.ensure_schema()
        return cls(workspace_id=workspace_id, database=database, session_metadata=session_metadata)
