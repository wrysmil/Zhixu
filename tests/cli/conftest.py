"""CLI test isolation."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _never_build_the_webui_bundle(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """Keep CLI tests from shelling out to ``npm install`` and ``vite build``.

    ``nanobot webui`` refreshes the bundled frontend whenever ``webui/src`` is
    newer than ``nanobot/web/dist``, which is the normal state of a checkout
    under active development. Without this stub a unit test that only wants to
    assert on port handling spends a minute building the SPA and then fails with
    whatever the local toolchain happens to do.
    """
    calls: list[dict[str, Any]] = []

    def record(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("nanobot.cli.webui_support.ensure_webui_bundle", record)
    yield calls
