"""Runtime path helpers derived from the active config context."""

from __future__ import annotations

from pathlib import Path

from nanobot.utils.helpers import ensure_dir


def get_config_path() -> Path:
    """Get the configuration file path (lazy import to break circular dependency).

    Delegates to ``nanobot.config.loader.get_config_path`` at call time so
    that importing this module never triggers a circular import during startup.
    """
    from nanobot.config.loader import get_config_path as _loader_get_config_path
    return _loader_get_config_path()


def get_data_dir() -> Path:
    """Return the instance-level runtime data directory."""
    return ensure_dir(get_config_path().parent)


def get_runtime_subdir(name: str) -> Path:
    """Return a named runtime subdirectory under the instance data dir."""
    return ensure_dir(get_data_dir() / name)


def get_media_dir(channel: str | None = None) -> Path:
    """Return the media directory, optionally namespaced per channel."""
    base = get_runtime_subdir("media")
    return ensure_dir(base / channel) if channel else base


def get_cron_dir() -> Path:
    """Return the cron storage directory."""
    return get_runtime_subdir("cron")


def get_logs_dir() -> Path:
    """Return the logs directory."""
    return get_runtime_subdir("logs")


def get_webui_dir() -> Path:
    """Return the directory for WebUI-only persisted display threads (JSON)."""
    return get_runtime_subdir("webui")


def get_workspace_path(workspace: str | Path | None = None) -> Path:
    """Resolve and ensure the agent workspace path."""
    path = Path(workspace).expanduser() if workspace else Path.home() / ".nanobot" / "workspace"
    return ensure_dir(path)


def get_writing_dir(workspace: str | Path | None = None) -> Path:
    """知序写作域的数据目录（文章 SQLite 状态）。

    与 memory 的 ``<workspace>/memory/state.db`` 并列；**不在此处建目录**——
    目录由 ``WritingDatabase`` 在真正要写库时创建，避免任何只做路径推导的读取
    调用都留下空的 ``writing/`` 目录。
    """
    return get_workspace_path(workspace) / "writing"


def get_teams_dir(workspace: str | Path | None = None) -> Path:
    """Agent Team 域的数据目录（``<agent-workspace>/teams``）。

    与 ``get_writing_dir`` 同样的读取口径：**不在此处建目录**——目录与 ``state.db``
    由 Team 存储层（WU-02）在真正要写时创建，因此只读调用（列表、读历史、配置校验）
    不会在只读工作空间里留下空的 ``teams/``。同理这里不调用 ``get_workspace_path``
    （它会 ``ensure_dir``），默认工作空间只按同一路径推导并 ``resolve``。
    """
    base = Path(workspace).expanduser() if workspace else Path.home() / ".nanobot" / "workspace"
    return base.resolve(strict=False) / "teams"


def is_default_workspace(workspace: str | Path | None) -> bool:
    """Return whether a workspace resolves to Zhixu's default workspace path."""
    current = Path(workspace).expanduser() if workspace is not None else Path.home() / ".nanobot" / "workspace"
    default = Path.home() / ".nanobot" / "workspace"
    return current.resolve(strict=False) == default.resolve(strict=False)


def get_cli_history_path() -> Path:
    """Return the shared CLI history file path."""
    return Path.home() / ".nanobot" / "history" / "cli_history"


def get_legacy_sessions_dir() -> Path:
    """Return the legacy global session directory used for migration fallback."""
    return Path.home() / ".nanobot" / "sessions"
