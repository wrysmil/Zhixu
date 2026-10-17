"""协作/团队批次按**会话**解析模型 runtime，冻结记录取同一份解析结果。

spec：``.ai-runtime-artifacts/specs/2026-10-17-writing-partner-model-selection-spec.md`` §4.2。
四条口径：

1. ``resolve_session_runtime`` 按 ``session_key`` 优先解析会话 runtime，取不到（能力缺失 /
   返回 ``None`` / 解析抛错）才回落装配期的零参 ``runtime_provider``；
2. ``build_team_turns`` 只在 host 真的具备 ``runtime_for_session`` + 会话存储时才透出这份
   能力（host 是鸭子类型，必须 ``getattr`` 探测），否则回落既有 ``current_runtime``；
3. 批次路径（``TeamWritingPort.start_batch``）与重试路径（``TeamRunController._drive``）
   同口径 —— 否则重试会用错模型；
4. ``build_writing_collab_runner`` 的冻结准备按协作会话（``websocket:<chat_id>``，chat_id
   从协作行取）解析模型名，解析不可用时回落装配期的全局模型名。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.teams.binding import SESSION_TEAM_BINDING_KEY, TeamBinding
from nanobot.teams.repository import TeamRepository
from nanobot.teams.runs import TeamRunController
from nanobot.teams.turn import (
    TeamLeaderTurns,
    build_team_turns,
    resolve_session_runtime,
)
from nanobot.writing.collab.team_port import TeamWritingPort, builtin_team_definition

BINDING = TeamBinding(team_id="content-team", team_revision=1, team_name="内容团队")
SESSION_KEY = "websocket:art-1:partner"
CHAT_ID = "art-1:partner"


# ---- 1. 共享解析口径 ---------------------------------------------------------


def test_resolve_session_runtime_prefers_the_session_runtime() -> None:
    runtime, source = resolve_session_runtime(
        SESSION_KEY,
        session_runtime_provider=lambda key: f"session-runtime:{key}",
        fallback=lambda: "global-runtime",
    )

    assert (runtime, source) == (f"session-runtime:{SESSION_KEY}", "session")


def test_resolve_session_runtime_falls_back_when_the_session_lookup_raises() -> None:
    """解析抛错不能拖垮批次：回落全局 runtime，并留下结构化的可排查事件。"""
    from loguru import logger as loguru_logger

    events: list[str] = []

    def _sink(message: Any) -> None:
        events.append(str(message.record["extra"].get("event") or ""))

    sink_id = loguru_logger.add(_sink, level="WARNING")

    def _boom(_session_key: str) -> Any:
        raise RuntimeError("session preset metadata is broken")

    try:
        runtime, source = resolve_session_runtime(
            SESSION_KEY, session_runtime_provider=_boom, fallback=lambda: "global-runtime"
        )
    finally:
        loguru_logger.remove(sink_id)

    assert (runtime, source) == ("global-runtime", "default")
    assert "team_session_runtime_unavailable" in events


def test_resolve_session_runtime_falls_back_without_session_capability() -> None:
    runtime, source = resolve_session_runtime(
        SESSION_KEY, session_runtime_provider=None, fallback=lambda: "global-runtime"
    )

    assert (runtime, source) == ("global-runtime", "default")


def test_resolve_session_runtime_reports_none_without_any_provider() -> None:
    """旧装配（零参 provider 都没有）保持既有语义：runtime 由调度器自己兜底。"""
    runtime, source = resolve_session_runtime(
        SESSION_KEY, session_runtime_provider=None, fallback=None
    )

    assert (runtime, source) == (None, "none")


# ---- 2. build_team_turns 的 host 探测 ----------------------------------------


class _Host:
    """鸭子类型宿主：可选地暴露 ``runtime_for_session`` + 会话存储。"""

    def __init__(self, *, session_runtime_capable: bool) -> None:
        self.subagents = SimpleNamespace(subagent_tool_names=lambda: ["read_file"])
        self.context = SimpleNamespace(skills=None)
        self.runtime_resolver = SimpleNamespace(runtime="global-runtime")
        self.tool_names = ["read_file"]
        self.created: list[str] = []
        if session_runtime_capable:
            self.sessions = SimpleNamespace(get_or_create=self._get_or_create)
            self.runtime_for_session = self._runtime_for_session

    def _get_or_create(self, key: str) -> Any:
        self.created.append(key)
        return SimpleNamespace(key=key, metadata={})

    def _runtime_for_session(self, session: Any) -> str:
        return f"session-runtime:{session.key}"


def _built_turns(host: _Host, workspace: Path) -> TeamLeaderTurns:
    return build_team_turns(
        service=SimpleNamespace(repository=SimpleNamespace()),  # type: ignore[arg-type]
        host=host,  # type: ignore[arg-type]
        workspace=workspace,
    )


def test_build_team_turns_exposes_session_runtime_when_host_supports_it(
    team_workspace: Path,
) -> None:
    host = _Host(session_runtime_capable=True)

    built = _built_turns(host, team_workspace)

    assert built.session_runtime_provider is not None
    assert built.runtime_for(SESSION_KEY) == f"session-runtime:{SESSION_KEY}"
    assert host.created == [SESSION_KEY], "会话按协作会话键取，不能自造键"


def test_build_team_turns_falls_back_to_the_default_runtime_without_host_support(
    team_workspace: Path,
) -> None:
    host = _Host(session_runtime_capable=False)

    built = _built_turns(host, team_workspace)

    assert built.session_runtime_provider is None
    assert built.runtime_for(SESSION_KEY) == "global-runtime"


# ---- 3. Leader 回合 / 批次 / 重试三条路径同口径 -------------------------------


class _RecordingScheduler:
    """记录 ``start_run`` 收到的 runtime；不真的起跑（本文件只验解析口径）。"""

    def __init__(self, revision: Any) -> None:
        self.revision = revision
        self.started: list[dict[str, Any]] = []
        self.observed: list[Any] = []

    def observe(self, observer: Any) -> None:
        self.observed.append(observer)

    def open_run(self, *, session_key: str, turn_id: str) -> None:
        return None

    async def start_run(self, **kwargs: Any) -> Any:
        self.started.append(kwargs)
        return SimpleNamespace(
            run_id="run-1", turn_id=kwargs["turn_id"], revision=self.revision
        )

    def forget(self, run_id: str) -> None:
        return None


async def test_leader_turn_starts_the_run_on_the_session_runtime(
    repository: TeamRepository, team_workspace: Path
) -> None:
    scheduler = _RecordingScheduler(repository.run_binding_snapshot(BINDING))
    turns = TeamLeaderTurns(
        service=SimpleNamespace(  # type: ignore[arg-type]
            prepare_new_run=lambda team_id, *, revision=None: repository.run_binding_snapshot(
                BINDING
            )
        ),
        scheduler=scheduler,  # type: ignore[arg-type]
        workspace=team_workspace,
        tool_names=lambda: ["read_file"],
        skill_names=lambda: (),
        runtime_provider=lambda: "global-runtime",
        session_runtime_provider=lambda key: f"session-runtime:{key}",
    )
    session = SimpleNamespace(
        key=SESSION_KEY, metadata={SESSION_TEAM_BINDING_KEY: BINDING.to_dict()}
    )
    msg = SimpleNamespace(chat_id=CHAT_ID, metadata={"webui_turn_id": "turn-visible"})

    context = await turns(msg, session, "internal-1")

    assert context is not None
    assert scheduler.started[0]["session_key"] == SESSION_KEY
    assert scheduler.started[0]["runtime"] == f"session-runtime:{SESSION_KEY}"


class _BatchHandle:
    def __init__(self) -> None:
        self.run_id = "run-1"
        self.finalized: list[bool] = []

    async def dispatch(self, *, batch_id: str, tasks: Any) -> list[Any]:
        return []

    async def finalize(self, *, delivered: bool) -> None:
        self.finalized.append(delivered)


class _BatchScheduler:
    """只记录 ``start_run`` 的 runtime；调度细节由 ``test_collab_team_port`` 覆盖。"""

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.observed: list[Any] = []
        self.forgotten: list[str] = []

    def observe(self, observer: Any) -> None:
        self.observed.append(observer)

    async def start_run(self, **kwargs: Any) -> Any:
        self.started.append(kwargs)
        return _BatchHandle()

    def forget(self, run_id: str) -> None:
        self.forgotten.append(run_id)


def _port(
    *,
    scheduler: _BatchScheduler,
    runtime_provider: Any,
    session_runtime_provider: Any,
    workspace: Path,
) -> TeamWritingPort:
    turns = SimpleNamespace(
        scheduler=scheduler,
        runtime_provider=runtime_provider,
        session_runtime_provider=session_runtime_provider,
    )
    return TeamWritingPort(
        service=SimpleNamespace(  # type: ignore[arg-type]
            repository=SimpleNamespace(),
            prepare_new_run=lambda team_id, revision=None: None,
        ),
        workspace=workspace,
        turns=turns,  # type: ignore[arg-type]
        runtime_provider=runtime_provider,
        session_runtime_provider=session_runtime_provider,
    )


async def _start_batch(port: TeamWritingPort, *, frozen: Any = None) -> str:
    return await port.start_batch(
        collaboration_id="collab-1",
        batch_id="batch-1",
        attempt=1,
        action="outline",
        instruction="",
        session_key=SESSION_KEY,
        chat_id=CHAT_ID,
        frozen={"skills": []} if frozen is None else frozen,
        selection=None,
        material_ids=(),
    )


async def test_start_batch_starts_the_run_on_the_session_runtime(
    team_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduler = _BatchScheduler()
    seen: list[str] = []

    def _session_runtime(session_key: str) -> str:
        seen.append(session_key)
        return f"session-runtime:{session_key}"

    port = _port(
        scheduler=scheduler,
        runtime_provider=lambda: "global-runtime",
        session_runtime_provider=_session_runtime,
        workspace=team_workspace,
    )
    # 只桩掉「取哪个团队」，runtime 解析与起跑口径保持真实实现。
    monkeypatch.setattr(port, "resolve_team", lambda: builtin_team_definition("writing-team"))

    run_id = await _start_batch(port)
    await port.drain()

    assert run_id == "run-1"
    assert seen == [SESSION_KEY]
    assert scheduler.started[0]["runtime"] == f"session-runtime:{SESSION_KEY}"


async def test_start_batch_falls_back_to_the_global_runtime(
    team_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧装配（只有零参 ``runtime_provider``）不许回归。"""
    scheduler = _BatchScheduler()
    port = _port(
        scheduler=scheduler,
        runtime_provider=lambda: "global-runtime",
        session_runtime_provider=lambda _key: None,
        workspace=team_workspace,
    )
    monkeypatch.setattr(port, "resolve_team", lambda: builtin_team_definition("writing-team"))

    await _start_batch(port)
    await port.drain()

    assert scheduler.started[0]["runtime"] == "global-runtime"


@contextmanager
def _captured_events() -> Any:
    """捕获 loguru 的结构化事件名（端口用默认 loguru logger 写日志）。"""
    from loguru import logger as loguru_logger

    events: list[str] = []

    def _sink(message: Any) -> None:
        events.append(str(message.record["extra"].get("event") or ""))

    sink_id = loguru_logger.add(_sink, level="INFO")
    try:
        yield events
    finally:
        loguru_logger.remove(sink_id)


async def test_start_batch_warns_when_the_reused_frozen_model_differs(
    team_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``writing.retry`` 复用旧冻结上下文：与当前会话模型不一致时必须留痕（行为不变）。"""
    scheduler = _BatchScheduler()
    port = _port(
        scheduler=scheduler,
        runtime_provider=lambda: "global-runtime",
        session_runtime_provider=lambda _key: SimpleNamespace(model="new-model"),
        workspace=team_workspace,
    )
    monkeypatch.setattr(port, "resolve_team", lambda: builtin_team_definition("writing-team"))

    with _captured_events() as events:
        await _start_batch(port, frozen={"skills": [], "model_id": "old-model"})
        await port.drain()

    assert "writing_collab_frozen_model_mismatch" in events
    assert scheduler.started[0]["runtime"].model == "new-model", "起跑仍用当前会话模型"


async def test_start_batch_does_not_warn_when_the_frozen_model_matches(
    team_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduler = _BatchScheduler()
    port = _port(
        scheduler=scheduler,
        runtime_provider=lambda: "global-runtime",
        session_runtime_provider=lambda _key: SimpleNamespace(model="same-model"),
        workspace=team_workspace,
    )
    monkeypatch.setattr(port, "resolve_team", lambda: builtin_team_definition("writing-team"))

    with _captured_events() as events:
        await _start_batch(port, frozen={"skills": [], "model_id": "same-model"})
        await port.drain()

    assert "writing_collab_frozen_model_mismatch" not in events


class _RetryHandle:
    def __init__(self) -> None:
        self.pending_runs = 0
        self.finalized: list[bool] = []

    async def run_pending(self) -> None:
        self.pending_runs += 1

    async def finalize(self, *, delivered: bool) -> None:
        self.finalized.append(delivered)


class _RetryScheduler:
    def __init__(self) -> None:
        self.adopted: list[dict[str, Any]] = []
        self.forgotten: list[str] = []

    async def adopt_run(self, run_id: str, *, session_key: str, runtime: Any = None) -> Any:
        self.adopted.append({"run_id": run_id, "session_key": session_key, "runtime": runtime})
        return _RetryHandle()

    def forget(self, run_id: str) -> None:
        self.forgotten.append(run_id)


def _controller(
    scheduler: _RetryScheduler, *, runtime_provider: Any, session_runtime_provider: Any
) -> TeamRunController:
    return TeamRunController(
        service=SimpleNamespace(repository=SimpleNamespace()),  # type: ignore[arg-type]
        scheduler=scheduler,  # type: ignore[arg-type]
        runtime_provider=runtime_provider,
        session_runtime_provider=session_runtime_provider,
    )


async def test_retry_drive_adopts_the_run_on_the_session_runtime() -> None:
    """重试路径同样拿到 ``session_key``，必须与批次路径同口径（否则重试换回全局模型）。"""
    scheduler = _RetryScheduler()
    controller = _controller(
        scheduler,
        runtime_provider=lambda: "global-runtime",
        session_runtime_provider=lambda key: f"session-runtime:{key}",
    )

    await controller._drive("run-9", SESSION_KEY)  # noqa: SLF001 - 重试后台路径只有这一入口

    assert scheduler.adopted == [
        {
            "run_id": "run-9",
            "session_key": SESSION_KEY,
            "runtime": f"session-runtime:{SESSION_KEY}",
        }
    ]
    assert scheduler.forgotten == ["run-9"]


async def test_retry_drive_falls_back_to_the_global_runtime() -> None:
    scheduler = _RetryScheduler()
    controller = _controller(
        scheduler,
        runtime_provider=lambda: "global-runtime",
        session_runtime_provider=None,
    )

    await controller._drive("run-9", SESSION_KEY)  # noqa: SLF001

    assert scheduler.adopted[0]["runtime"] == "global-runtime"


# ---- 4. 冻结记录取协作会话解析结果 -------------------------------------------


def _frozen_context(**kwargs: Any) -> Any:
    from nanobot.writing.collab.preparation import FrozenContext

    return FrozenContext(
        skills=(),
        model_id=str(kwargs["model_id"]),
        source_scope={},
        workspace_key="",
        article_id="",
        collaboration_id="",
        batch_id="",
    )


def _collab_runner(tmp_path: Path, *, team_turns: Any, model_id: Any) -> Any:
    from nanobot.webui import gateway_services

    return gateway_services.build_writing_collab_runner(
        workspace_path=tmp_path,
        team_service=SimpleNamespace(repository=SimpleNamespace()),  # type: ignore[arg-type]
        team_turns=team_turns,
        bus=SimpleNamespace(publish_outbound=None),
        model_id=model_id,
        session_manager=None,
    )


def _idle_turns(*, session_runtime_provider: Any) -> SimpleNamespace:
    turns = SimpleNamespace(
        scheduler=SimpleNamespace(observe=lambda _observer: None),
        runtime_provider=None,
    )
    if session_runtime_provider is not None:
        turns.session_runtime_provider = session_runtime_provider
    return turns


def _patch_prepare_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "nanobot.writing.collab.preparation.prepare_frozen",
        lambda **kwargs: _frozen_context(**kwargs),
    )


def _patch_collaboration(
    monkeypatch: pytest.MonkeyPatch, *, session_key: str = SESSION_KEY
) -> None:
    from nanobot.writing.collab.repository import CollabRepository

    monkeypatch.setattr(
        CollabRepository,
        "get_collaboration",
        lambda _self, _collaboration_id: SimpleNamespace(
            chat_id=CHAT_ID, session_key=session_key
        ),
    )


def test_frozen_preparation_uses_the_collaboration_session_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """冻结模型按协作会话解析，会话键取协作行上的 ``session_key``（与起跑同键）。"""
    _patch_prepare_frozen(monkeypatch)
    _patch_collaboration(monkeypatch)
    seen: list[str] = []

    def _session_runtime(session_key: str) -> Any:
        seen.append(session_key)
        return SimpleNamespace(model="deepseek-v4", model_preset="deepseek")

    runner = _collab_runner(
        tmp_path,
        team_turns=_idle_turns(session_runtime_provider=_session_runtime),
        model_id=lambda: "global-model",
    )

    frozen = runner._preparer(  # noqa: SLF001 - 冻结入口只经 runner 暴露
        recipe=object(), collaboration_id="collab-1"
    )

    assert seen == [SESSION_KEY]
    assert frozen.model_id == "deepseek-v4"


def test_frozen_preparation_composes_the_session_key_from_the_chat_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """老行缺 ``session_key`` 时按 ``<channel>:<chat_id>`` 拼，口径与 CollabRunner 一致。"""
    _patch_prepare_frozen(monkeypatch)
    _patch_collaboration(monkeypatch, session_key="")
    seen: list[str] = []

    def _session_runtime(session_key: str) -> Any:
        seen.append(session_key)
        return SimpleNamespace(model="deepseek-v4")

    runner = _collab_runner(
        tmp_path,
        team_turns=_idle_turns(session_runtime_provider=_session_runtime),
        model_id=lambda: "global-model",
    )

    frozen = runner._preparer(  # noqa: SLF001
        recipe=object(), collaboration_id="collab-1"
    )

    assert seen == [SESSION_KEY]
    assert frozen.model_id == "deepseek-v4"


def test_frozen_preparation_falls_back_to_the_runtime_model_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_prepare_frozen(monkeypatch)
    _patch_collaboration(monkeypatch)

    runner = _collab_runner(
        tmp_path,
        team_turns=_idle_turns(session_runtime_provider=None),
        model_id=lambda: "global-model",
    )

    frozen = runner._preparer(  # noqa: SLF001
        recipe=object(), collaboration_id="collab-1"
    )

    assert frozen.model_id == "global-model"


def test_frozen_preparation_falls_back_when_the_session_lookup_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_prepare_frozen(monkeypatch)
    _patch_collaboration(monkeypatch)

    def _boom(_session_key: str) -> Any:
        raise RuntimeError("session store is unavailable")

    runner = _collab_runner(
        tmp_path,
        team_turns=_idle_turns(session_runtime_provider=_boom),
        model_id=lambda: "global-model",
    )

    frozen = runner._preparer(  # noqa: SLF001
        recipe=object(), collaboration_id="collab-1"
    )

    assert frozen.model_id == "global-model"
