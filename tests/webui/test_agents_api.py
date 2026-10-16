"""agents_api 的 payload 函数：形状、字段所有权与错误翻译。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.config.schema import Config, MCPServerConfig, ToolsConfig
from nanobot.webui import agents_api
from nanobot.webui.settings_contracts import WebUISettingsError

PROFILE_WIRE: dict[str, Any] = {
    "id": "writer",
    "name": "写作",
    "description": "",
    "type": "custom",
    "customized": False,
    "categoryId": "writing",
    "icon": "✍️",
    "color": "#E67E22",
    "prompt": "",
    "modelId": None,
    "tools": {"mode": "all", "entries": []},
    "skills": {"mode": "all", "entries": []},
    "subAgents": {"mode": "all", "entries": []},
    "hidden": False,
    "updatedAt": "2020-01-01T00:00:00+00:00",
}


def test_list_returns_factory_presets_with_frontend_keys(tmp_path: Path) -> None:
    payload = agents_api.agents_list(tmp_path)
    assert "agents" in payload
    assert payload["agents"]
    first = payload["agents"][0]
    assert "categoryId" in first and "subAgents" in first and "updatedAt" in first


def test_list_does_not_create_directories(tmp_path: Path) -> None:
    agents_api.agents_list(tmp_path)
    assert not (tmp_path / "agents").exists()


def test_save_strips_client_owned_fields(tmp_path: Path) -> None:
    payload = dict(PROFILE_WIRE, type="system", customized=True)
    saved = agents_api.agents_save(tmp_path, payload)["agent"]
    assert saved["type"] == "custom"
    assert saved["customized"] is False
    assert saved["updatedAt"] != PROFILE_WIRE["updatedAt"]


def test_save_rejects_illegal_id(tmp_path: Path) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        agents_api.agents_save(tmp_path, dict(PROFILE_WIRE, id="../escape"))
    assert excinfo.value.status == 400


def test_save_rejects_blank_name(tmp_path: Path) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        agents_api.agents_save(tmp_path, dict(PROFILE_WIRE, name="  "))
    assert excinfo.value.status == 422


def test_delete_rejects_system_preset(tmp_path: Path) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        agents_api.agents_delete(tmp_path, "code-reviewer")
    assert excinfo.value.status == 409


def test_delete_returns_echoed_id(tmp_path: Path) -> None:
    agents_api.agents_save(tmp_path, PROFILE_WIRE)
    assert agents_api.agents_delete(tmp_path, "writer") == {"id": "writer"}


def test_reset_returns_factory_profile(tmp_path: Path) -> None:
    payload = agents_api.agents_reset(tmp_path, "code-reviewer")["agent"]
    assert payload["id"] == "code-reviewer"
    assert payload["customized"] is False


def test_visibility_toggles_hidden(tmp_path: Path) -> None:
    # writer 不是出厂预设，必须先落盘才有「可切换」的档案。
    agents_api.agents_save(tmp_path, PROFILE_WIRE)
    assert agents_api.agents_visibility(tmp_path, "writer", True)["agent"]["hidden"] is True
    assert agents_api.agents_visibility(tmp_path, "writer", False)["agent"]["hidden"] is False


def test_visibility_requires_boolean(tmp_path: Path) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        agents_api.agents_visibility(tmp_path, "writer", "yes")  # type: ignore[arg-type]
    assert excinfo.value.status == 400


# -- catalog ---------------------------------------------------------------


def _minimal_workspace(tmp_path: Path) -> Path:
    (tmp_path / "identity").mkdir()
    (tmp_path / "identity" / "SOUL.md").write_text("# soul", encoding="utf-8")
    return tmp_path


def _catalog(workspace: Path) -> dict[str, Any]:
    # 目录构造依赖注入的 Config；用默认构造器，不读用户真实的 ~/.nanobot 配置。
    return agents_api.agents_catalog(workspace, load_config=Config)


def test_catalog_has_all_five_collections(tmp_path: Path) -> None:
    catalog = _catalog(_minimal_workspace(tmp_path))
    assert set(catalog) == {"tools", "skills", "models", "mcpServers", "categories"}


def test_catalog_without_config_is_503(tmp_path: Path) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        agents_api.agents_catalog(_minimal_workspace(tmp_path))
    assert excinfo.value.status == 503


def test_catalog_tool_descriptors_match_frontend_type(tmp_path: Path) -> None:
    catalog = _catalog(_minimal_workspace(tmp_path))
    assert catalog["tools"]
    for tool in catalog["tools"]:
        assert set(tool) == {
            "name", "label", "description", "category", "risk", "scope", "locked",
            "blocked", "blockedReason",
        }
        assert tool["label"] == tool["name"]
        assert tool["risk"] in {"low", "medium", "high"}
        assert tool["scope"] in {"core", "subagent"}
        assert isinstance(tool["locked"], bool)
        assert isinstance(tool["blocked"], bool)
    names = {t["name"] for t in catalog["tools"]}
    assert "read_file" in names
    assert "exec" in names


def test_catalog_marks_core_only_tools_as_blocked(tmp_path: Path) -> None:
    """主 Agent 专属工具在档案页可见但不可勾选。

    它们不进子 Agent 的工具集，勾了会在运行时被静默丢弃——用户以为配上了，
    实际没有。``CapabilityPicker`` 的 blocked 分支已就位，这里只保证后端
    标得对。
    """
    catalog = _catalog(_minimal_workspace(tmp_path))
    by_name = {t["name"]: t for t in catalog["tools"]}
    assert "message" in by_name, "message 工具应出现在目录里（可见但不可选）"
    assert by_name["message"]["scope"] == "core"
    assert by_name["message"]["blocked"] is True
    assert by_name["message"]["blockedReason"]


def test_catalog_does_not_lock_every_subagent_tool(tmp_path: Path) -> None:
    """回归防线：子 Agent 可用的工具不应被大面积锁死。

    旧判据 ``scopes & {"core","memory"}`` 会把全部 14 个工具都标成 locked
    （子 Agent 可用的工具全都声明了 core scope），档案页等于无法收紧能力，
    而收紧能力正是档案的核心用途。
    """
    catalog = _catalog(_minimal_workspace(tmp_path))
    by_name = {t["name"]: t for t in catalog["tools"]}
    subagent_tools = {n: t for n, t in by_name.items() if t["scope"] == "subagent"}
    assert len(subagent_tools) >= 10, "子 Agent 可用工具数量异常"
    locked = {n for n, t in subagent_tools.items() if t["locked"]}
    assert locked == {"read_file", "list_dir"}, f"锁定集合应收窄到最小组装集，实际 {locked}"
    # code-reviewer 预设正是靠 include 白名单排除 exec/write_file，
    # 若这两项被锁，白名单就失去意义。
    assert by_name["exec"]["locked"] is False
    assert by_name["write_file"]["locked"] is False
    assert by_name["exec"]["blocked"] is False


def test_catalog_marks_dangerous_tools_high_risk(tmp_path: Path) -> None:
    catalog = _catalog(_minimal_workspace(tmp_path))
    risk = {t["name"]: t["risk"] for t in catalog["tools"]}
    # 写类工具命中 TOOL_RISK_OVERRIDES 静态表。
    assert risk["write_file"] == "high"
    assert risk["edit_file"] == "high"
    # 只读工具未命中表，回落到 read_only 推导。
    assert risk["read_file"] == "low"
    assert risk["web_search"] == "low"


def test_catalog_shell_tool_risk_matches_real_tool_name(tmp_path: Path) -> None:
    """shell 工具的注册名是 ``exec``，风险表必须按这个名字提级到 ``high``。

    契约 §8.1 要求命中 ``TOOL_RISK_OVERRIDES`` 就取其值，未命中才回落到
    ``read_only`` 推导。表里若还留着改名前的 ``execute_command``，``exec``
    就会落到兜底桶拿到 ``medium``。
    """
    from nanobot.agents.catalog import TOOL_RISK_OVERRIDES

    catalog = _catalog(_minimal_workspace(tmp_path))
    risk = {t["name"]: t["risk"] for t in catalog["tools"]}
    assert "execute_command" not in risk
    assert "execute_command" not in TOOL_RISK_OVERRIDES
    assert TOOL_RISK_OVERRIDES["exec"] == "high"
    assert risk["exec"] == "high"


def test_catalog_tool_descriptions_are_truncated(tmp_path: Path) -> None:
    catalog = _catalog(_minimal_workspace(tmp_path))
    for tool in catalog["tools"]:
        assert len(tool["description"]) <= 200


def test_catalog_skill_descriptors_match_frontend_type(tmp_path: Path) -> None:
    catalog = _catalog(_minimal_workspace(tmp_path))
    assert catalog["skills"]
    for skill in catalog["skills"]:
        assert set(skill) == {"name", "description", "source", "tags"}
        assert skill["source"] in {"builtin", "workspace", "plugin"}
        assert skill["tags"] == []


def test_catalog_model_descriptors_match_frontend_type(tmp_path: Path) -> None:
    catalog = _catalog(_minimal_workspace(tmp_path))
    assert catalog["models"]
    for model in catalog["models"]:
        assert set(model) == {
            "id", "label", "provider", "contextWindow", "health", "vision", "toolUse",
        }
        assert model["health"] == "healthy"
        assert model["contextWindow"] > 0


def test_catalog_categories_match_factory_list(tmp_path: Path) -> None:
    catalog = _catalog(_minimal_workspace(tmp_path))
    assert [c["id"] for c in catalog["categories"]] == [
        "general", "coding", "writing", "research", "ops", "efficiency",
    ]


def test_catalog_does_not_leak_absolute_paths(tmp_path: Path) -> None:
    workspace = _minimal_workspace(tmp_path)
    catalog = _catalog(workspace)
    serialized = json.dumps(catalog, ensure_ascii=False)
    assert str(workspace) not in serialized


# -- catalog / mcpServers（契约 §8.4）----------------------------------------


def _mcp_catalog(tmp_path: Path, servers: dict[str, MCPServerConfig]) -> dict[str, Any]:
    """带自定义 ``mcp_servers`` 的 catalog：目录构造不读用户真实的 ~/.nanobot 配置。"""
    config = Config(tools=ToolsConfig(mcp_servers=servers))
    return agents_api.agents_catalog(_minimal_workspace(tmp_path), load_config=lambda: config)


def test_catalog_mcp_servers_empty_array_when_none_configured(tmp_path: Path) -> None:
    # 缺字段会让前端拿不到可迭代的数组，渲染时直接炸；必须是空数组。
    catalog = _catalog(_minimal_workspace(tmp_path))
    assert catalog["mcpServers"] == []


def test_catalog_mcp_descriptors_match_frontend_type(tmp_path: Path) -> None:
    catalog = _mcp_catalog(
        tmp_path,
        {
            "github": MCPServerConfig(type="stdio", command="npx"),
            "docs": MCPServerConfig(type="streamableHttp", url="https://mcp.example.com/mcp"),
            "legacy": MCPServerConfig(type="sse", url="https://mcp.example.com/sse"),
        },
    )
    assert catalog["mcpServers"]
    for server in catalog["mcpServers"]:
        assert set(server) == {"name", "type", "command", "url", "toolCount", "allTools"}
        assert isinstance(server["toolCount"], int)
        assert isinstance(server["allTools"], bool)
    by_name = {s["name"]: s for s in catalog["mcpServers"]}
    assert set(by_name) == {"github", "docs", "legacy"}
    # stdio 取 command、不取 url；http 系反之。
    assert by_name["github"]["command"] == "npx"
    assert by_name["github"]["url"] == ""
    assert by_name["docs"]["url"] == "https://mcp.example.com/mcp"
    assert by_name["docs"]["command"] == ""
    assert by_name["legacy"]["url"] == "https://mcp.example.com/sse"


def test_catalog_mcp_type_falls_back_to_auto_when_omitted(tmp_path: Path) -> None:
    # ``MCPServerConfig.type`` 省略时由运行时自动探测，catalog 侧回退成 "auto"，
    # 不能把 None 原样透给前端（前端要按字符串匹配传输类型）。
    catalog = _mcp_catalog(
        tmp_path,
        {
            "auto": MCPServerConfig(command="npx"),
            "stdio": MCPServerConfig(type="stdio", command="uvx"),
        },
    )
    types = {s["name"]: s["type"] for s in catalog["mcpServers"]}
    assert types == {"auto": "auto", "stdio": "stdio"}


def test_catalog_mcp_all_tools_sentinel_yields_zero_count(tmp_path: Path) -> None:
    # ["*"] 是「全放行」的哨兵值，不是「启用了 1 个工具」；按 1 下发会让 UI 骗人。
    catalog = _mcp_catalog(
        tmp_path,
        {
            "wide": MCPServerConfig(type="stdio", command="npx"),
            "narrow": MCPServerConfig(type="stdio", command="npx", enabled_tools=["a", "b"]),
            "empty": MCPServerConfig(type="stdio", command="npx", enabled_tools=[]),
        },
    )
    by_name = {s["name"]: s for s in catalog["mcpServers"]}
    assert by_name["wide"]["allTools"] is True
    assert by_name["wide"]["toolCount"] == 0
    assert by_name["narrow"]["allTools"] is False
    assert by_name["narrow"]["toolCount"] == 2
    assert by_name["empty"]["allTools"] is False
    assert by_name["empty"]["toolCount"] == 0


def test_catalog_never_leaks_mcp_headers_or_env(tmp_path: Path) -> None:
    """安全断言：``headers`` / ``env`` 可能含 OAuth token 与 API key，一律不下发。

    这里给的是真实可识别的凭据值，断言它们不出现在序列化后的整份响应里 ——
    只查描述符的字段集合不够，万一哪天有人在别处塞进去就漏了。
    """
    catalog = _mcp_catalog(
        tmp_path,
        {
            "remote": MCPServerConfig(
                type="streamableHttp",
                url="https://mcp.example.com/mcp",
                headers={"Authorization": "Bearer sk-super-secret-token"},
            ),
            "local": MCPServerConfig(
                type="stdio",
                command="npx",
                args=["-y", "@some/mcp"],
                env={"API_KEY": "env-super-secret-key"},
                cwd="/srv/secret-cwd",
            ),
        },
    )
    servers = catalog["mcpServers"]
    for server in servers:
        assert "headers" not in server
        assert "env" not in server
        # args / cwd 同样不下发：够认出 server 就行，不外泄运行细节。
        assert "args" not in server
        assert "cwd" not in server
    serialized = json.dumps(catalog, ensure_ascii=False)
    for secret in (
        "sk-super-secret-token",
        "env-super-secret-key",
        "secret-cwd",
        "@some/mcp",
    ):
        assert secret not in serialized


def test_catalog_mcp_servers_are_not_persisted_into_profiles(tmp_path: Path) -> None:
    """``mcp_servers`` 是全局配置，只读投影：档案落盘后不能沾上它。"""
    config = Config(
        tools=ToolsConfig(
            mcp_servers={"github": MCPServerConfig(type="stdio", command="npx")}
        )
    )
    workspace = _minimal_workspace(tmp_path)

    class _Fixed:
        def __call__(self) -> Config:
            return config

    agents_api.agents_catalog(workspace, load_config=_Fixed())
    saved = agents_api.agents_save(workspace, PROFILE_WIRE)["agent"]

    assert "mcpServers" not in saved
    assert "mcpServers" not in json.dumps(saved, ensure_ascii=False)


def test_delete_rejects_agent_referenced_by_an_active_team(tmp_path: Path) -> None:
    """契约 §3：被未归档团队引用的档案不能删，错误里要给出团队 id。"""
    from nanobot.webui.agents_api import agents_save

    agents_save(tmp_path, dict(PROFILE_WIRE, id="writer", name="写作"))
    with pytest.raises(WebUISettingsError) as excinfo:
        agents_api.agents_delete(
            tmp_path, "writer", team_reference_lookup=lambda _id: ["content-team"]
        )
    assert excinfo.value.status == 409
    assert excinfo.value.message.startswith("AGENT_IN_USE_BY_TEAM")
    assert "content-team" in excinfo.value.message


def test_delete_still_works_without_any_team_data(tmp_path: Path) -> None:
    """没注入引用查询、或注入了但查不到：现有删除行为必须保持一致（契约 §3）。"""
    from nanobot.webui.agents_api import agents_save

    agents_save(tmp_path, dict(PROFILE_WIRE, id="writer", name="写作"))
    assert agents_api.agents_delete(tmp_path, "writer") == {"id": "writer"}
    agents_save(tmp_path, dict(PROFILE_WIRE, id="writer2", name="写作2"))
    assert agents_api.agents_delete(
        tmp_path, "writer2", team_reference_lookup=lambda _id: []
    ) == {"id": "writer2"}


# ------------------------------------------- 内置保护业务码（F4：不丢 code）


def test_builtin_agent_protection_code_is_carried_to_the_error_body(tmp_path: Path) -> None:
    """存储层异常携带 ``BUILTIN_RESOURCE_PROTECTED``，API 错误体必须把它透出（F4）。

    契约 §4 + agent-team 契约 §3「失败码放固定前缀」：口径与
    ``teams_routes.translate`` 一致 —— ``<CODE> <原文>``。
    """
    from nanobot.agents.store import AgentStore, AgentStoreError, protected_write_message
    from nanobot.writing.builtin import BUILTIN_RESOURCE_PROTECTED

    # 异常对象自己带码：这里断言的是真实对象，不是本用例硬编码的字符串。
    with pytest.raises(AgentStoreError) as store_error:
        AgentStore(tmp_path).delete_profile("writing-editor")
    assert store_error.value.code == BUILTIN_RESOURCE_PROTECTED

    with pytest.raises(WebUISettingsError) as api_error:
        agents_api.agents_delete(tmp_path, "writing-editor")
    assert api_error.value.status == 409
    assert api_error.value.message == (
        f"{BUILTIN_RESOURCE_PROTECTED} {protected_write_message('agent', 'writing-editor')}"
    )

    with pytest.raises(WebUISettingsError) as reset_error:
        agents_api.agents_reset(tmp_path, "writing-editor")
    assert reset_error.value.status == 409
    assert reset_error.value.message.startswith(BUILTIN_RESOURCE_PROTECTED)


def test_ordinary_agent_errors_keep_an_unprefixed_message(tmp_path: Path) -> None:
    """没有业务码的错误不得被加上前缀：口径只对带码的拒绝路径生效。"""
    with pytest.raises(WebUISettingsError) as excinfo:
        agents_api.agents_delete(tmp_path, "code-reviewer")
    assert excinfo.value.status == 409
    assert excinfo.value.message == "系统预设「code-reviewer」不可删除，可重置或隐藏"
