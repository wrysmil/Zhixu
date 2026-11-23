"""Runtime prompt overrides served from Langfuse.

Only system-prompt templates (``agent/*.md``) and agent profiles
(``profile/<id>``) are managed in Langfuse; identity files (SOUL/AGENT/USER),
personas and policies stay local. Managed prompts replace the bundled text at
runtime. Lookups are cache-only; a daemon thread refreshes the prompts a turn
actually asks for, so the agent event loop never blocks on Langfuse. Fail-open:
when Langfuse is unreachable or a prompt is missing, the bundled template is
used unchanged.

Env vars:
- ``LANGFUSE_PROMPTS_ENABLED``: ``0/false/off/no`` disables remote overrides.
- ``LANGFUSE_PROMPT_LABEL``: label to read, default ``production``.
- ``LANGFUSE_PROMPTS_TTL_S``: cache lifetime, default ``60``.
- ``LANGFUSE_PROMPTS_REFRESH_S``: refresher poll interval, default ``2``.
- ``LANGFUSE_PROMPTS_FETCH_TIMEOUT_S``: per-fetch timeout, default ``3``.
"""

from __future__ import annotations

import os
import threading
import time
from contextvars import ContextVar
from typing import Any

from loguru import logger

DEFAULT_TTL_S = 60.0
DEFAULT_REFRESH_S = 2.0
DEFAULT_FETCH_TIMEOUT_S = 3.0

_client: Any = None
_client_lock = threading.Lock()

_cache: dict[str, tuple[float, Any | None]] = {}
_cache_lock = threading.Lock()
_wanted: set[str] = set()
_refresher: threading.Thread | None = None
_refresher_lock = threading.Lock()

_used_prompts: ContextVar[dict[str, int]] = ContextVar(
    "zhixu_langfuse_used_prompts",
    default={},
)
_linked_prompt: ContextVar[Any] = ContextVar("zhixu_langfuse_linked_prompt", default=None)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _label() -> str:
    return os.environ.get("LANGFUSE_PROMPT_LABEL", "production").strip() or "production"


def enabled() -> bool:
    if os.environ.get("LANGFUSE_PROMPTS_ENABLED", "1").strip().lower() in {
        "0",
        "false",
        "off",
        "no",
    }:
        return False
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")
    )


def _get_client() -> Any | None:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            try:
                from langfuse import get_client

                _client = get_client()
            except Exception:
                return None
    return _client


def remote_name_for(local_key: str) -> str | None:
    """Map a bundled template key to its Langfuse prompt name.

    Returns ``None`` for prompts that stay local: identity files
    (SOUL/AGENT/USER), personas and policies are user-owned workspace content,
    not framework wiring, so they are not managed in Langfuse.
    """
    key = local_key.replace("\\", "/")
    if key.endswith(".md"):
        key = key[:-3]
    if "/_snippets/" in f"/{key}/":
        # Included partials are not registered on their own.
        return None
    if key.startswith("agent/"):
        return f"zhixu/agent/{key.split('/', 1)[1]}"
    if key.startswith("profile/"):
        return f"zhixu/profile/{key.split('/', 1)[1]}"
    return None


def _fetch(name: str) -> Any | None:
    client = _get_client()
    if client is None:
        return None
    try:
        return client.get_prompt(
            name,
            label=_label(),
            type="text",
            cache_ttl_seconds=0,
            max_retries=0,
            fetch_timeout_seconds=int(
                _env_float("LANGFUSE_PROMPTS_FETCH_TIMEOUT_S", DEFAULT_FETCH_TIMEOUT_S) * 1000
            ),
        )
    except Exception:
        return None


def _refresh_once() -> None:
    ttl = _env_float("LANGFUSE_PROMPTS_TTL_S", DEFAULT_TTL_S)
    with _cache_lock:
        names = list(_wanted)
    for name in names:
        with _cache_lock:
            cached = _cache.get(name)
        if cached is not None and time.monotonic() - cached[0] < ttl:
            continue
        prompt = _fetch(name)
        with _cache_lock:
            _cache[name] = (time.monotonic(), prompt)


def _refresh_loop() -> None:
    while True:
        try:
            if enabled():
                _refresh_once()
        except Exception:
            logger.exception("Langfuse prompt refresh failed")
        time.sleep(max(0.5, _env_float("LANGFUSE_PROMPTS_REFRESH_S", DEFAULT_REFRESH_S)))


def _ensure_refresher() -> None:
    global _refresher
    with _refresher_lock:
        if _refresher is not None and _refresher.is_alive():
            return
        _refresher = threading.Thread(
            target=_refresh_loop,
            name="zhixu-langfuse-prompts",
            daemon=True,
        )
        _refresher.start()


def get_prompt(local_key: str) -> Any | None:
    """Return the cached Langfuse prompt for a local template key, or None.

    Never touches the network: the refresher thread fills the cache in the
    background, so callers on the event loop stay non-blocking. The first
    render of a prompt therefore falls back to the bundled template until the
    refresher has fetched it (one poll interval).
    """
    if not enabled():
        return None
    name = remote_name_for(local_key)
    if name is None:
        return None
    with _cache_lock:
        _wanted.add(name)
        cached = _cache.get(name)
    _ensure_refresher()
    return cached[1] if cached is not None else None


def prompt_snapshot(local_key: str) -> tuple[Any, str, int | None] | None:
    """Return ``(prompt, text, version)`` for a usable remote prompt, else None."""
    prompt = get_prompt(local_key)
    if prompt is None:
        return None
    text = getattr(prompt, "prompt", None)
    if not isinstance(text, str) or not text.strip():
        return None
    version = getattr(prompt, "version", None)
    return prompt, text, version if isinstance(version, int) else None


def prompt_text(local_key: str) -> str | None:
    """Raw prompt text (same Jinja syntax as the bundled template)."""
    snapshot = prompt_snapshot(local_key)
    return snapshot[1] if snapshot is not None else None


def prompt_version(local_key: str) -> int | None:
    snapshot = prompt_snapshot(local_key)
    return snapshot[2] if snapshot is not None else None


def record_used(local_key: str, version: int | None) -> None:
    """Remember which prompt version served this turn."""
    name = remote_name_for(local_key)
    if name is None:
        return
    used = dict(_used_prompts.get())
    used[name] = version if version is not None else 0
    _used_prompts.set(used)


def used_prompts() -> dict[str, int]:
    return dict(_used_prompts.get())


def reset_turn_state() -> None:
    """Clear per-turn prompt bookkeeping before a new turn starts."""
    _used_prompts.set({})
    _linked_prompt.set(None)


def set_linked_prompt(prompt: Any) -> None:
    """Link a prompt object to the generation that follows."""
    _linked_prompt.set(prompt)


def linked_prompt() -> Any | None:
    return _linked_prompt.get()
