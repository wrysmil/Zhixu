"""Leader 每回合装配测试（plan Task 4）。

三条口径各自钉住一处真实缺陷：

1. **空 registry 不回退**：``ToolRegistry.__len__`` 让空集合变成假值，
   ``tools or self.tools`` 会把「本轮零工具」静默扩成 loop 的全量工具。
2. **每回合 registry 按调用方给的算**：非空也要赢过 ``self.tools``，否则团队 Leader
   的白名单会被普通工具集盖掉。
3. **同一工具实例的上下文不串会话**：Leader 与普通聊天共用同名工具实例时，
   per-turn registry 必须是新对象，不能靠 ``unregister(self.tools)`` 再恢复。

全部用内存 provider 替身，不发真实网络请求、不读本机配置。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.context import ContextBuilder
from nanobot.agent.loop import AgentLoop
from nanobot.agent.model_runtime import ModelRuntimeResolver
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ModelPresetConfig
from nanobot.providers.base import LLMResponse, ProviderConversationState, ToolCallRequest
from nanobot.session.manager import Session
from nanobot.teams.leader import (
    LeaderTurnContext,
    ModelPresetUnavailable,
    build_leader_registry,
    render_role_prompt,
    resolve_leader_runtime,
)
from nanobot.teams.turn import build_team_turns
from nanobot.utils.llm_runtime import LLMRuntime


class _RecordingProvider:
    """记下 provider 实际收到的 tools 与 system prompt，用来判定「模型可见」的内容。"""

    def __init__(self, default_model: str = "test-model", gate: asyncio.Event | None = None) -> None:
        self.last_tool_definitions: list[Any] = []
        self.last_system_prompt: str = ""
        self.last_model: Any = None
        self.calls = 0
        self._gate = gate
        self.chat_with_retry = AsyncMock(side_effect=self._respond)
        self.get_default_model = MagicMock(return_value=default_model)
        self.generation = MagicMock(max_tokens=4096, temperature=0.1, reasoning_effort=None)
        self.estimate_prompt_tokens = MagicMock(return_value=(1_000, "test"))
        self.can_resume_conversation_state = MagicMock(return_value=False)

    async def _respond(self, **kwargs: Any) -> LLMResponse:
        if self._gate is not None:
            await self._gate.wait()
        self.calls += 1
        self.last_tool_definitions = list(kwargs.get("tools") or [])
        self.last_model = kwargs.get("model")
        messages = kwargs.get("messages") or []
        self.last_system_prompt = str(messages[0]["content"]) if messages else ""
        return LLMResponse(content="done", tool_calls=[])


def _make_loop(tmp_path: Any, provider: _RecordingProvider) -> AgentLoop:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    # loop 默认全量工具集里必须真有名目，否则「不回退」这一条无从判定。
    assert len(loop.tools) > 0
    return loop


def _names(definitions: list[Any]) -> list[str]:
    """provider 收到的是 OpenAI function schema：名字在 ``function.name`` 里。"""
    return [str(item["function"]["name"]) for item in definitions]


@pytest.mark.asyncio
async def test_empty_registry_stays_empty(tmp_path: Any) -> None:
    """plan Task 4 锚点：零工具授权不能被静默解释成「全量放行」。"""
    provider = _RecordingProvider()
    loop = _make_loop(tmp_path, provider)

    await loop.process_direct("hello", tools=ToolRegistry())

    assert provider.last_tool_definitions == []


@pytest.mark.asyncio
async def test_per_turn_registry_wins_over_loop_default(tmp_path: Any) -> None:
    """反向口径：调用方给了非空 registry 时，用它的定义而不是 loop 的全量集合。"""
    provider = _RecordingProvider()
    loop = _make_loop(tmp_path, provider)
    scoped = ToolRegistry()
    for name in loop.tools.tool_names[:1]:
        tool = loop.tools.get(name)
        assert tool is not None
        scoped.register(tool)

    await loop.process_direct("hello", tools=scoped)

    assert _names(provider.last_tool_definitions) == scoped.tool_names
    assert len(provider.last_tool_definitions) < len(loop.tools.get_definitions())


@pytest.mark.asyncio
async def test_restore_turn_keeps_the_caller_registry_identity(tmp_path: Any) -> None:
    """无会话级限制时 per-turn registry 必须原样留着，不能换成 loop 的那个或复制一份。"""
    provider = _RecordingProvider()
    loop = _make_loop(tmp_path, provider)
    scoped = ToolRegistry()
    captured: dict[str, Any] = {}

    async def stop_after_restore(ctx: Any) -> None:
        captured["tools"] = ctx.tools
        raise RuntimeError("captured after restore")

    loop._restore_turn = stop_after_restore  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="captured after restore"):
        await loop.process_direct("hello", tools=scoped)

    assert captured["tools"] is scoped


# ------------------------------------------------------------ 装配纯函数


class _StubTool(Tool):
    """最小可用工具替身：只为了判定「谁进了本回合的 registry」。"""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"stub {self._name}"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **_kwargs: Any) -> str:  # type: ignore[override]
        return "ok"


def _registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(_StubTool(name))
    return registry


def _context(**overrides: Any) -> LeaderTurnContext:
    values: dict[str, Any] = {
        "team_id": "content-team",
        "team_revision": 3,
        "team_name": "内容团队",
        "run_id": "run-1",
        "turn_id": "turn-1",
        "role_prompt": "你是主笔。",
        "tool_allow": frozenset({"read_file", "write_file", "spawn"}),
        "skill_exclude": frozenset({"cron"}),
        "model_id": None,
    }
    values.update(overrides)
    return LeaderTurnContext(**values)


def test_leader_registry_is_a_new_object_and_never_mutates_the_base() -> None:
    """Leader 用新 registry，不能 ``unregister(self.tools)`` 后再恢复（会串会话）。"""
    base = _registry("read_file", "write_file", "spawn", "exec")
    leader = build_leader_registry(base, _context())

    assert leader is not base
    assert base.tool_names == ["read_file", "write_file", "spawn", "exec"]
    assert leader.tool_names == ["read_file", "write_file"]


def test_leader_registry_drops_spawn_and_keeps_dispatch_tool() -> None:
    """契约 §5：Team 模式移除普通 spawn，否则成员限制可被绕过；调度工具由宿主挂上。"""
    base = _registry("spawn", "read_file")
    dispatch = _StubTool("team_dispatch")
    leader = build_leader_registry(base, _context(dispatch_tool=dispatch))

    assert "spawn" not in leader
    assert leader.has("team_dispatch")
    assert leader.get("team_dispatch") is dispatch


def test_leader_registry_ignores_allowed_names_that_are_not_installed() -> None:
    """档案白名单里写了 loop 没注册的工具时，不能凭空造出一个入口。"""
    leader = build_leader_registry(_registry("read_file"), _context(tool_allow=frozenset({"ghost"})))
    assert leader.tool_names == []


def test_role_prompt_carries_team_identity_and_members() -> None:
    prompt = render_role_prompt(
        team_name="内容团队",
        team_revision=3,
        instructions="先查证再成稿。",
        leader_name="主编",
        leader_prompt="你是主笔。",
        members=[("研究员", "查资料并给出来源"), ("写作", "成稿")],
    )

    assert "内容团队" in prompt
    assert "修订 3" in prompt
    assert "先查证再成稿。" in prompt
    assert "你是主笔。" in prompt
    assert "研究员" in prompt and "查资料并给出来源" in prompt


def test_role_prompt_hands_over_leadership_to_the_team_leader() -> None:
    """选团队 = 主管换人：段里必须点名团队 Leader，并说明全局人格只管表达风格。

    这条钉的是「默认聊天 leader 还在管」那个体感缺陷——档案的 prompt 早就注入了，
    但没有任何一处告诉模型「这一轮由谁主管」，读起来像默认助手顺带带了段说明。
    """
    prompt = render_role_prompt(
        team_name="内容团队",
        team_revision=3,
        instructions="",
        leader_name="创作团队主编",
        leader_prompt="你是创作团队的主编。",
        members=[],
    )

    assert "创作团队主编" in prompt
    assert "主管" in prompt
    assert "风格" in prompt


def test_role_prompt_teaches_delegation_discipline_not_just_a_ban() -> None:
    """主管感来自委派纪律，不是「不要自己干」那一句禁令（对标 openakita 协作原则）。

    缺任何一条都会退化成「Leader 亲自做完一切再转述」：判定顺序、简报式交接、
    永不委派理解、绝不预测成员结果、交付后由 Leader 核对。
    """
    prompt = render_role_prompt(
        team_name="内容团队",
        team_revision=3,
        instructions="",
        leader_name="主编",
        leader_prompt="你是主编。",
        members=[("研究员", "查资料")],
    )

    assert "team_dispatch" in prompt
    assert "简报" in prompt
    assert "never" not in prompt  # 中文口径，不混英文残句
    for cue in ("没参与过这次对话", "不要预测", "核对"):
        assert cue in prompt


def test_role_prompt_omits_the_leader_line_without_a_name() -> None:
    """缺 leader 名字时不拼出「由『』主管」这种空壳，段落整体退化。"""
    prompt = render_role_prompt(
        team_name="内容团队",
        team_revision=1,
        instructions="",
        leader_name="   ",
        leader_prompt="你是主编。",
        members=[("研究员", "查资料")],
    )

    assert "「」" not in prompt
    assert "内容团队" in prompt


def test_resolve_leader_runtime_keeps_default_without_model_id() -> None:
    runtime = LLMRuntime.capture(_RecordingProvider(), "test-model", context_window_tokens=128_000)  # type: ignore[arg-type]
    resolver = ModelRuntimeResolver(runtime)

    assert resolve_leader_runtime(resolver, _context()) is resolver.runtime


def test_resolve_leader_runtime_uses_the_named_preset() -> None:
    base = LLMRuntime.capture(_RecordingProvider(), "test-model", context_window_tokens=128_000)  # type: ignore[arg-type]
    resolver = ModelRuntimeResolver(
        base,
        model_presets={"fast": ModelPresetConfig.model_validate({"model": "fast-model"})},
    )

    runtime = resolve_leader_runtime(resolver, _context(model_id="fast"))

    assert runtime.model == "fast-model"
    assert runtime.model_preset == "fast"
    # 解析 preset 不得顺手改掉全局默认（普通聊天仍在用 test-model）。
    assert resolver.runtime.model == "test-model"


def test_resolve_leader_runtime_rejects_missing_preset_without_fallback() -> None:
    """失效 preset 必须拒绝起跑，不能静默退回默认模型（契约 §4 的 400 口径）。"""
    base = LLMRuntime.capture(_RecordingProvider(), "test-model", context_window_tokens=128_000)  # type: ignore[arg-type]
    resolver = ModelRuntimeResolver(base)

    with pytest.raises(ModelPresetUnavailable, match="MODEL_PRESET_UNAVAILABLE"):
        resolve_leader_runtime(resolver, _context(model_id="gone"))


# -------------------------------------------------- ContextBuilder 的纯参数（三入口一致）


def _write_skill(root: Path, name: str, *, always: bool, body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    meta = json.dumps({"nanobot": {"always": True}}, separators=(",", ":")) if always else ""
    lines = ["---"]
    if meta:
        lines.append(f"metadata: {meta}")
    lines.extend(["---", "", body])
    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")


def _skill_builder(tmp_path: Path) -> ContextBuilder:
    workspace = tmp_path / "ws"
    (workspace / "skills").mkdir(parents=True)
    _write_skill(workspace / "skills", "alpha", always=True, body="Alpha 正文")
    _write_skill(workspace / "skills", "beta", always=False, body="Beta 正文")
    return ContextBuilder(workspace)


def test_role_section_is_injected_without_touching_the_default_prompt(tmp_path: Any) -> None:
    """角色段是**参数**：同一 builder 不带参数时必须回到普通聊天的样子。"""
    builder = _skill_builder(tmp_path)
    plain = builder.build_system_prompt()
    prompted = builder.build_system_prompt(
        role_section="# Team Mode\n\n## 你的角色\n你是主笔。",
        skill_exclude={"alpha", "beta"},
    )

    assert "你是主笔。" in prompted
    assert "你是主笔。" not in plain
    assert "Alpha 正文" not in prompted
    # 排除只影响这一次调用，不写回 self.skills（那是所有会话共享的加载器）。
    assert "Alpha 正文" in builder.build_system_prompt()


def test_skill_exclude_covers_always_summary_and_explicit_reference(tmp_path: Any) -> None:
    """always 段、技能概览、显式 ``$skill`` 三个入口必须一起生效。"""
    builder = _skill_builder(tmp_path)
    plain = builder.build_system_prompt()
    assert "Beta 正文" not in plain  # 概览只列名字与描述，不灌正文
    assert "beta" in plain.lower()

    scoped = builder.build_system_prompt(skill_exclude={"beta"})
    assert "beta" not in scoped.lower()

    loader = builder.skills
    assert loader.build_explicit_skill_runtime_context("用 $beta 处理这件事") is not None
    assert loader.build_explicit_skill_runtime_context("用 $beta 处理这件事", exclude={"beta"}) is None


# --------------------------------------------------------------- loop 装配（每回合隔离）


def _leader_context(
    team_name: str = "内容团队",
    revision: int = 3,
    *,
    tool_allow: frozenset[str] = frozenset({"read_file", "write_file"}),
    model_id: str | None = None,
    **overrides: Any,
) -> LeaderTurnContext:
    return _context(
        team_id=f"team-{team_name}",
        team_name=team_name,
        team_revision=revision,
        role_prompt=render_role_prompt(
            team_name=team_name,
            team_revision=revision,
            instructions="先查证再成稿。",
            leader_prompt=f"你是 {team_name} 的主笔。",
            members=[("研究员", "查资料")],
        ),
        tool_allow=tool_allow,
        model_id=model_id,
        **overrides,
    )


def _wired_loop(tmp_path: Path, provider: _RecordingProvider) -> tuple[AgentLoop, dict[str, Any]]:
    loop = _make_loop(tmp_path, provider)
    holder: dict[str, Any] = {"context": None}
    loop.team_context_provider = lambda msg, session, turn_id: holder["context"]
    return loop, holder


@pytest.mark.asyncio
async def test_team_a_team_b_and_plain_chat_do_not_pollute_each_other(tmp_path: Path) -> None:
    """同一个 loop 上交错跑 Team A / Team B / 普通聊天，每回合只看到自己的装配。"""
    provider = _RecordingProvider()
    loop, holder = _wired_loop(tmp_path, provider)
    base_names = _names(loop.tools.get_definitions())

    holder["context"] = _leader_context("增长团队", 5)
    await loop.process_direct("第一回合", session_key="cli:team")
    assert "增长团队" in provider.last_system_prompt
    assert "你是 增长团队 的主笔。" in provider.last_system_prompt
    assert _names(provider.last_tool_definitions) == ["read_file", "write_file"]

    holder["context"] = None
    await loop.process_direct("普通回合", session_key="cli:team")
    assert "增长团队" not in provider.last_system_prompt
    assert _names(provider.last_tool_definitions) == base_names

    holder["context"] = _leader_context("研究团队", 1, tool_allow=frozenset({"read_file"}))
    await loop.process_direct("另一个团队", session_key="cli:team")
    assert "研究团队" in provider.last_system_prompt
    assert "增长团队" not in provider.last_system_prompt
    assert _names(provider.last_tool_definitions) == ["read_file"]


@pytest.mark.asyncio
async def test_leader_model_preset_is_resolved_per_turn_without_renaming_the_tool(tmp_path: Path) -> None:
    """Leader 档案的 preset 只影响本回合的 runtime，不改全局默认模型。"""
    provider = _RecordingProvider()
    loop, holder = _wired_loop(tmp_path, provider)
    loop.runtime_resolver._model_presets["fast"] = ModelPresetConfig.model_validate(
        {"model": "fast-model"}
    )

    holder["context"] = _leader_context(model_id="fast")
    await loop.process_direct("用团队的模型", session_key="cli:team")

    assert provider.last_model == "fast-model"
    assert loop.runtime_resolver.runtime.model == "test-model"


@pytest.mark.asyncio
async def test_unavailable_leader_preset_fails_the_turn_instead_of_falling_back(
    tmp_path: Path
) -> None:
    """preset 失效时不能退回普通聊天或全权限执行（契约 §4）。"""
    provider = _RecordingProvider()
    loop, holder = _wired_loop(tmp_path, provider)

    holder["context"] = _leader_context(model_id="gone")

    with pytest.raises(ModelPresetUnavailable):
        await loop.process_direct("起跑就该被拒", session_key="cli:team")
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_team_revision_switch_drops_only_the_provider_continuation(
    tmp_path: Path,
) -> None:
    """换修订只失效 provider 续接，用户 transcript 一条都不动（plan Task 4 Step 4）。

    续接状态直接挂在**本回合正在使用的 session 对象**上：磁盘回读有独立的缓存失效
    时序，会把这条测试变成在验证 SessionManager 而不是验证守卫。
    """
    provider = _RecordingProvider()
    provider.can_resume_conversation_state = MagicMock(return_value=True)
    loop, holder = _wired_loop(tmp_path, provider)
    seen: dict[str, Any] = {}

    def provide(msg: Any, session: Any, turn_id: str) -> LeaderTurnContext | None:
        context = holder["context"]
        if context is not None and context.team_revision == 4:
            seen["transcript_before"] = len(session.messages)
            session.provider_state = ProviderConversationState(
                kind="chat",
                provider="generic",
                model="test-model",
                version=1,
                payload={"stale": "只属于修订 3 的续接"},
            )
            seen["session"] = session
        return context

    loop.team_context_provider = provide

    holder["context"] = _leader_context(revision=3)
    await loop.process_direct("上一回合", session_key="cli:team")

    holder["context"] = _leader_context(revision=4)
    await loop.process_direct("换修订之后", session_key="cli:team")

    session = seen["session"]
    assert session.provider_state is None
    assert session.metadata["teamAppliedSignature"] == f"{holder['context'].team_id}@4"
    assert len(session.messages) > seen["transcript_before"]


def test_revision_guard_only_fires_on_an_actual_switch(tmp_path: Path) -> None:
    """对照组：守卫只在修订真的变了时开火，同一修订的续接不能被误删。"""
    provider = _RecordingProvider()
    loop = _make_loop(tmp_path, provider)
    session = Session(key="cli:team")

    # 从没跑过团队：会话上没有「实际执行过的团队」签名，此时不算切换
    # （普通聊天的 provider 续接不能被团队逻辑误删）。
    assert session.metadata.get("teamAppliedSignature") is None
    assert loop._team_revision_switched(session, _leader_context(revision=4)) is True

    loop._stamp_team_revision(session, _leader_context(revision=4))
    assert loop._team_revision_switched(session, _leader_context(revision=4)) is False
    assert loop._team_revision_switched(session, _leader_context(revision=5)) is True
    # 换团队也算切换：续接是按某个 Leader 档案装配出来的，修订号相同也不算同一份。
    assert loop._team_revision_switched(session, _leader_context("研究团队", revision=4)) is True



@pytest.mark.asyncio
async def test_leader_preset_runtime_reaches_tools_through_request_context(
    tmp_path: Path,
) -> None:
    """工具只能从 ``RequestContext.runtime`` 拿模型；Leader 的 preset 必须经这儿传给子执行。

    ``spawn`` 就是这样继承本次回合的 runtime 的（tools/spawn.py 读 request_ctx.runtime），
    所以这条同时钉住 Done 口径「subagent 默认模型继承本次 Leader runtime」。
    """
    seen: dict[str, Any] = {}

    class _ProbeTool(_StubTool):
        async def execute(self, **_kwargs: Any) -> str:
            from nanobot.agent.tools.context import current_request_context

            request = current_request_context()
            assert request is not None
            seen["runtime"] = request.runtime
            return "ok"

    provider = _RecordingProvider()
    # 本回合让模型真的调一次工具，否则 RequestContext 不会被工具读到。
    async def respond(**kwargs: Any) -> LLMResponse:
        await provider._respond(**kwargs)
        if provider.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="call_1", name="probe", arguments={})],
            )
        return LLMResponse(content="done", tool_calls=[])

    provider.chat_with_retry = AsyncMock(side_effect=respond)
    loop, holder = _wired_loop(tmp_path, provider)
    probe = _ProbeTool("probe")
    loop.tools.register(probe)
    loop.runtime_resolver._model_presets["fast"] = ModelPresetConfig.model_validate(
        {"model": "fast-model"}
    )

    holder["context"] = _leader_context(model_id="fast", tool_allow=frozenset({"probe"}))
    await loop.process_direct("派活给成员", session_key="cli:team")

    assert seen["runtime"] is not None
    assert seen["runtime"].model == "fast-model"
    assert seen["runtime"] is not loop.runtime_resolver.runtime


@pytest.mark.asyncio
async def test_first_team_message_persists_the_binding_before_the_leader(
    tmp_path: Path,
) -> None:
    """首条团队消息：绑定先落到会话，Leader 装配与磁盘读到同一份（契约 §4 的懒创建）。

    消息只送一次绑定：第二条起真值在 ``session.metadata`` 里，重启重连都不再依赖暂存。
    """
    from nanobot.bus.events import InboundMessage

    provider = _RecordingProvider()
    loop = _make_loop(tmp_path, provider)
    seen: list[dict[str, Any]] = []

    def provide(msg: Any, session: Any, turn_id: str) -> None:
        seen.append({"key": session.key, "metadata": dict(session.metadata)})
        return None

    loop.team_context_provider = provide
    binding = {"teamId": "content-team", "teamRevision": 3, "teamName": "内容团队"}
    await loop._dispatch(
        InboundMessage(
            channel="websocket",
            sender_id="u1",
            chat_id="team-chat",
            content="research this",
            metadata={"team_binding": binding},
        )
    )
    while loop.bus.outbound_size > 0:
        await loop.bus.consume_outbound()

    assert seen[0]["metadata"]["team_binding"] == binding
    stored = loop.sessions.read_session_metadata(seen[0]["key"])
    assert stored is not None
    assert stored["metadata"]["team_binding"] == binding

    # 第二条消息不带绑定也仍然是团队会话：provider 从会话读数拿到同一份。
    await loop._dispatch(
        InboundMessage(
            channel="websocket",
            sender_id="u1",
            chat_id="team-chat",
            content="and then this",
        )
    )
    while loop.bus.outbound_size > 0:
        await loop.bus.consume_outbound()
    assert seen[1]["metadata"]["team_binding"] == binding


@pytest.mark.asyncio
async def test_a_bound_session_is_not_overridden_by_the_message(tmp_path: Path) -> None:
    """会话已经绑定过就以此为真：消息里的团队快照不能改写既有绑定（契约 §4）。"""
    from nanobot.bus.events import InboundMessage

    provider = _RecordingProvider()
    loop = _make_loop(tmp_path, provider)
    seen: list[dict[str, Any]] = []
    loop.team_context_provider = lambda msg, session, turn_id: seen.append(
        dict(session.metadata)
    )

    loop.sessions.get_or_create("websocket:team-chat").metadata[
        "team_binding"
    ] = {"teamId": "other-team", "teamRevision": 1, "teamName": "别的团队"}
    loop.sessions.save(loop.sessions.get_or_create("websocket:team-chat"))

    await loop._dispatch(
        InboundMessage(
            channel="websocket",
            sender_id="u1",
            chat_id="team-chat",
            content="research this",
            metadata={"team_binding": {"teamId": "content-team", "teamRevision": 9, "teamName": "内容团队"}},
        )
    )
    while loop.bus.outbound_size > 0:
        await loop.bus.consume_outbound()

    assert seen[0]["team_binding"]["teamId"] == "other-team"
    assert seen[0]["team_binding"]["teamRevision"] == 1


# --------------------------------------------------------------- 回合收口与停止桥接


@pytest.mark.asyncio
async def test_leader_turn_finalizes_the_run_with_the_delivery_verdict(
    tmp_path: Path,
) -> None:
    """Leader 交付了答案才算 completed：收口必须拿到「本轮有没有交付」这个判定。"""
    provider = _RecordingProvider()
    loop, holder = _wired_loop(tmp_path, provider)
    seen: list[bool] = []

    async def finalize(delivered: bool) -> None:
        seen.append(delivered)

    async def respond(**kwargs: Any) -> LLMResponse:
        provider.last_tool_definitions = list(kwargs.get("tools") or [])
        return LLMResponse(content="答案在这里", tool_calls=[])

    provider.chat_with_retry.side_effect = respond
    holder["context"] = _leader_context(turn_finalizer=finalize)

    await loop.process_direct("派活", session_key="cli:team")

    assert seen == [True]


@pytest.mark.asyncio
async def test_stage_failure_still_finalizes_the_team_run(tmp_path: Path) -> None:
    """回合半路炸掉（保存阶段抛错）也必须收口，否则运行永远停在 running。"""
    provider = _RecordingProvider()
    loop, holder = _wired_loop(tmp_path, provider)
    seen: list[bool] = []

    async def finalize(delivered: bool) -> None:
        seen.append(delivered)

    async def exploding_save(ctx: Any) -> None:
        raise RuntimeError("save stage blew up")

    loop._persist_turn = exploding_save  # pyright: ignore[reportAttributeAccessIssue]
    holder["context"] = _leader_context(turn_finalizer=finalize)

    with pytest.raises(RuntimeError):
        await loop.process_direct("派活", session_key="cli:team")

    assert seen == [False], "异常收口必须是未交付"


@pytest.mark.asyncio
async def test_stop_bridge_cancels_team_runs_of_that_session(tmp_path: Path) -> None:
    """停聊天 = 停该会话的团队运行；别的会话不受影响（契约 §6）。"""
    loop = _make_loop(tmp_path, _RecordingProvider())
    stopped: list[str] = []

    async def cancel(key: str) -> int:
        stopped.append(key)
        return 2

    loop.team_run_cancel_hook = cancel

    total = await loop._cancel_active_tasks("websocket:team-chat")

    assert stopped == ["websocket:team-chat"]
    assert total >= 2


def test_team_stack_factory_matches_the_real_loop_surface(tmp_path: Path) -> None:
    """工厂只认 loop 的真实表面；属性名写错是 gateway 启动即坏，这里先钉住。"""
    loop = _make_loop(tmp_path, _RecordingProvider())
    entries = loop.context.skills.list_skills(filter_unavailable=False)
    assert all("name" in item for item in entries)
    assert "read_file" in loop.tool_names
    assert "read_file" in loop.subagents.subagent_tool_names()

    service = SimpleNamespace(repository=None, prepare_new_run=lambda *a, **k: None)
    turns = build_team_turns(service=service, host=loop, workspace=tmp_path)  # pyright: ignore[reportArgumentType]

    assert "read_file" in turns.tool_names()
    assert turns.runtime_provider is not None
    assert turns.runtime_provider() is loop.runtime_resolver.runtime
