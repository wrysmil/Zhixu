"""Cross-suite test infrastructure."""

from __future__ import annotations

import os
import ssl
import sys
from collections.abc import Iterator
from pathlib import Path

import certifi
import pytest
from loguru import logger


@pytest.fixture(autouse=True)
def _isolate_nanobot_log_activation() -> Iterator[None]:
    """Keep CLI log settings from leaking into later tests in the same process."""
    logger.enable("nanobot")
    try:
        yield
    finally:
        logger.enable("nanobot")


@pytest.fixture(autouse=True)
def _isolate_sessions_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect session storage away from the real active config data directory.

    Session storage lives under the active runtime data root (outside the workspace,
    per ADR-0001), so without redirection tests would write into the real home.
    """
    runtime_root = tmp_path.parent / f"{tmp_path.name}-runtime-root"
    legacy_root = tmp_path.parent / f"{tmp_path.name}-legacy-sessions-root"

    def runtime_subdir(name: str) -> Path:
        path = runtime_root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(
        "nanobot.session.manager.get_runtime_subdir",
        runtime_subdir,
    )
    monkeypatch.setattr(
        "nanobot.session.manager.get_legacy_sessions_dir",
        lambda: legacy_root,
    )
    yield


@pytest.fixture(autouse=True)
def _isolate_proxy_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make proxy detection read environment variables only.

    ``urllib.request.getproxies()`` merges the environment with the OS-level
    proxy configuration (on macOS: SystemConfiguration). A developer machine
    with a system-wide proxy therefore flips every proxy-sensitive assertion:
    ``env_proxy_applies_to_url`` reports "proxied", the direct-TCP probe is
    skipped, and the pinned-DNS transport is bypassed by the proxy mounts. Tests
    that care about proxying set ``HTTP_PROXY``/``NO_PROXY`` themselves, so the
    OS source has to be taken out of the picture for the suite to be
    reproducible on any machine.
    """
    from urllib.request import getproxies_environment

    monkeypatch.setattr(
        "nanobot.security.network.getproxies",
        getproxies_environment,
    )


@pytest.fixture(autouse=True)
def _isolate_pairing_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep channel pairing tests out of the user's active pairing store."""
    pairing_path = tmp_path / "pairing.json"
    monkeypatch.setattr(
        "nanobot.pairing.store._store_path",
        lambda: pairing_path,
    )


@pytest.fixture(scope="session", autouse=True)
def _use_windows_system_ca_for_default_http_clients() -> Iterator[None]:
    """Avoid reparsing certifi's CA bundle for every offline HTTP client.

    Loading certifi takes roughly 0.7 seconds per client on Windows. The test
    suite constructs hundreds of clients while mocking their I/O. System roots
    preserve certificate verification for accidental local requests; explicit
    ``cafile``, ``capath``, and ``cadata`` arguments still use the real loader.
    """
    if sys.platform != "win32":
        yield
        return

    original = ssl.create_default_context
    certifi_path = os.path.normcase(os.path.abspath(certifi.where()))

    def create_default_context(
        purpose: ssl.Purpose = ssl.Purpose.SERVER_AUTH,
        *,
        cafile: str | None = None,
        capath: str | None = None,
        cadata: str | bytes | None = None,
    ) -> ssl.SSLContext:
        requested_path = os.path.normcase(os.path.abspath(cafile)) if cafile else None
        if requested_path == certifi_path and capath is None and cadata is None:
            return original(purpose)
        return original(
            purpose,
            cafile=cafile,
            capath=capath,
            cadata=cadata,
        )

    ssl.create_default_context = create_default_context
    try:
        yield
    finally:
        ssl.create_default_context = original
