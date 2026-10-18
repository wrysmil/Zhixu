"""从 WebUI 保存档案到 spawn 消费它的完整链路。"""

from __future__ import annotations

from pathlib import Path

from nanobot.agents.models import AgentProfile, AgentSelection
from nanobot.agents.runtime import AgentProfileRuntime
from nanobot.agents.store import AgentStore
from nanobot.webui import agents_api


def test_saved_profile_is_visible_to_runtime_without_restart(tmp_path: Path) -> None:
    agents_api.agents_save(
        tmp_path,
        {
            "id": "reviewer-2",
            "name": "评审二号",
            "type": "custom",
            "tools": {"mode": "include", "entries": ["read_file"]},
            "prompt": "你是{{name}}，看这里。",
        },
    )
    resolved = AgentProfileRuntime(tmp_path).resolve("reviewer-2")
    assert resolved is not None
    assert resolved.tool_names == frozenset({"read_file"})
    assert resolved.prompt.startswith("你是评审二号")


def test_edited_profile_replaces_previous_resolution(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    store.save_profile(
        AgentProfile(id="x", name="旧", tools=AgentSelection("include", ["read_file"]))
    )
    first = AgentProfileRuntime(tmp_path).resolve("x")
    assert first is not None
    assert first.tool_names == frozenset({"read_file"})

    store.save_profile(
        AgentProfile(id="x", name="新", tools=AgentSelection("include", ["list_dir"]))
    )
    second = AgentProfileRuntime(tmp_path).resolve("x")
    assert second is not None
    assert second.tool_names == frozenset({"list_dir"})


def test_deleted_profile_stops_resolving(tmp_path: Path) -> None:
    agents_api.agents_save(tmp_path, {"id": "temp", "name": "临时"})
    assert AgentProfileRuntime(tmp_path).resolve("temp") is not None
    agents_api.agents_delete(tmp_path, "temp")
    assert AgentProfileRuntime(tmp_path).resolve("temp") is None


def test_agent_loop_wires_profile_runtime(tmp_path: Path) -> None:
    """宿主接线回归防线。

    ``AgentLoop`` 必须把 ``AgentProfileRuntime`` 传给 ``SubagentManager``，
    否则 spawn 拿到 ``agent=xxx`` 也只会静默退回默认子 agent —— 链路看着
    通、实际档案不生效，而且没有任何报错。
    """
    from unittest.mock import MagicMock, patch

    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.base import GenerationSettings

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings()

    with patch("nanobot.agent.loop.ContextBuilder"), \
         patch("nanobot.agent.loop.SessionManager"), \
         patch("nanobot.agent.loop.SubagentManager") as mock_mgr:
        mock_mgr.return_value.cancel_by_session = MagicMock(return_value=0)
        AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)

    kwargs = mock_mgr.call_args.kwargs
    assert "agent_profiles" in kwargs, "AgentLoop 未把 AgentProfileRuntime 接到 SubagentManager"
    assert isinstance(kwargs["agent_profiles"], AgentProfileRuntime)
    # model_resolver 必须接上：漏接时档案的 modelId 在子 Agent 侧是惰性的——
    # 界面显示选了模型、实际跑父会话的模型，而且没有任何信号。
    # 解析失败如何收敛成 None 由 tests/agent/test_turn_agent_profile.py 的装配锁负责。
    assert kwargs.get("model_resolver") is not None, "AgentLoop 漏接 model_resolver"
