"""Agent 档案的传输无关 payload 函数。

本模块不知道 HTTP 也不知道 WebSocket：入参是纯 Python，出参是纯 dict。
``nanobot/webui/agents_routes.py`` 负责把 action 映射到这些函数。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, cast

from nanobot.agents.catalog import DEFAULT_TOOL_CATEGORY, TOOL_CATEGORIES, TOOL_RISK_OVERRIDES
from nanobot.agents.models import AgentProfile, AgentProfileError
from nanobot.agents.store import AgentStore, AgentStoreError, agent_builtin_projection
from nanobot.config.schema import Config, ToolsConfig
from nanobot.webui.settings_contracts import WebUISettingsError

# 描述符首行截断长度：工具 description 动辄上千字符，前端列表用不上。
_DESCRIPTION_LIMIT = 200

# 前端只认这三个来源值，SkillsLoader 的 "unknown" 归一到 "builtin"。
_SKILL_SOURCES = frozenset({"builtin", "workspace", "plugin"})

# MCP 传输类型里走 URL 的两个；stdio 走 command。
_MCP_HTTP_TYPES = frozenset({"sse", "streamableHttp"})

# ``MCPServerConfig.type`` 省略时由运行时自动探测，catalog 侧回退成这个占位值。
_MCP_TYPE_AUTO = "auto"

# contextWindow 缺省兜底：出现在 config 里的模型至少得有这个量级的窗口。
_DEFAULT_CONTEXT_WINDOW = 200_000

_TOOL_RISK_BY_READ_ONLY = ("low", "medium")

# 档案页「锁定」的最小集合：勾上也无法取消的工具。
#
# 只有「没有它子 Agent 就完全无法工作」的才该进这里。``locked`` 会作为
# ``resolve_selection`` 的 lockedIds 强制保留，所以锁错等于静默扩权。
# 判据不能是「工具声明了 core scope」——子 Agent 可用的工具全都声明了
# core，那样会锁死全部 13 个，档案页等于无法收紧能力。
#
# 目前只有两个：读文件与列目录。任何子 Agent 任务都从「看现状」开始，
# 没有它们连任务描述都无法落地。write_file / exec / web_* / apply_patch
# 都必须可取消——``code-reviewer`` 这类只读档案正是靠白名单把它们排除的。
_MIN_LOCKED_TOOL_NAMES = frozenset({"read_file", "list_dir"})


def _translate(exc: AgentStoreError) -> WebUISettingsError:
    return WebUISettingsError(exc.message, status=exc.status)


def _store(workspace: Path) -> AgentStore:
    return AgentStore(Path(workspace))


def _require_id(agent_id: Any) -> str:
    """id 归一化：非字符串、空串、纯空白一律 400。"""
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise WebUISettingsError("id is required", status=400)
    return agent_id.strip()


def _truncate(text: str, limit: int = _DESCRIPTION_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _project(profile: AgentProfile) -> dict[str, Any]:
    """档案投影 + 创作内置归属字段（契约 §7）。

    非内置档案拿到的是与 ``to_dict()`` 逐字相同的字典——普通用户资源不得多出
    任何内置字段，前端也无从把用户档案误判成受保护资产。
    """
    payload = profile.to_dict()
    payload.update(agent_builtin_projection(profile.id))
    return payload


# -- 档案 CRUD --------------------------------------------------------------


def agents_list(workspace: Path) -> dict[str, Any]:
    try:
        return {"agents": [_project(p) for p in _store(workspace).list_profiles()]}
    except AgentStoreError as exc:
        raise _translate(exc) from exc


def agents_save(workspace: Path, raw: Any) -> dict[str, Any]:
    """校验并落盘。``type`` / ``customized`` / ``updatedAt`` 由 store 决定，客户端传的作废。"""
    if not isinstance(raw, dict):
        raise WebUISettingsError("agent must be an object", status=400)
    try:
        profile = AgentProfile.from_dict(raw)
        return {"agent": _project(_store(workspace).save_profile(profile))}
    except (AgentStoreError, AgentProfileError) as exc:
        # 两类都带 message/status；后者覆盖 422（空名、超长 prompt）这类字段级失败。
        raise WebUISettingsError(exc.message, status=exc.status) from exc
    except ValueError as exc:
        # 兜底：from_dict 里的非字段级解析失败一律按 400 回传，不把栈泄给前端。
        raise WebUISettingsError(str(exc), status=400) from exc


def agents_delete(
    workspace: Path,
    agent_id: Any,
    *,
    team_reference_lookup: Callable[[str], list[str]] | None = None,
) -> dict[str, Any]:
    normalized = _require_id(agent_id)
    if team_reference_lookup is not None:
        # 契约 §3：未归档团队引用中的档案不能删。归档团队与历史快照不绑住删除。
        referencing = sorted(team_reference_lookup(normalized))
        if referencing:
            raise WebUISettingsError(
                "AGENT_IN_USE_BY_TEAM " + ",".join(referencing), status=409
            )
    try:
        _store(workspace).delete_profile(normalized)
    except AgentStoreError as exc:
        raise _translate(exc) from exc
    return {"id": normalized}


def agents_reset(workspace: Path, agent_id: Any) -> dict[str, Any]:
    normalized = _require_id(agent_id)
    try:
        return {"agent": _project(_store(workspace).reset_profile(normalized))}
    except AgentStoreError as exc:
        raise _translate(exc) from exc


def agents_visibility(workspace: Path, agent_id: Any, hidden: Any) -> dict[str, Any]:
    normalized = _require_id(agent_id)
    if not isinstance(hidden, bool):
        raise WebUISettingsError("hidden must be a boolean", status=400)
    try:
        return {"agent": _project(_store(workspace).set_visibility(normalized, hidden))}
    except AgentStoreError as exc:
        raise _translate(exc) from exc


# -- 目录 -------------------------------------------------------------------


def _tool_descriptors(workspace: Path, tools_config: ToolsConfig) -> list[dict[str, Any]]:
    from nanobot.agent.tools.context import ToolContext
    from nanobot.agent.tools.loader import ToolLoader
    from nanobot.agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    ctx = ToolContext(config=tools_config, workspace=str(Path(workspace).resolve()))
    loader = ToolLoader()
    # 两个 scope 各打一次、进同一个 registry：同名工具后者覆盖前者，
    # 结果就是全部工具的并集。
    for scope in ("core", "subagent"):
        loader.load(ctx, registry, scope=scope)

    descriptors: list[dict[str, Any]] = []
    # ``tool_names`` 是注册表已有的公开只读属性；契约 §8.1 提到的 ``names()``
    # 是同义的排序别名，等 WU-04 补上后两者等价，这里先用现成的那一个。
    for name in sorted(registry.tool_names):
        tool = registry.get(name)
        if tool is None:
            continue
        scopes = set(getattr(type(tool), "_scopes", {"core"}))
        description = _truncate((tool.description or "").strip())
        # ``scope`` 回答的是「这个工具能不能出现在子 Agent 档案的能力集里」。
        # 判据是「工具自己声明了 subagent scope」，不是「它不是 core-only」——
        # 子 Agent 可用的 13 个工具**全部**同时声明了 core 与 subagent
        # （``_scopes = {"core", "subagent"}``），用「core not in scopes」判会
        # 永远得出 False，档案页就一个子 Agent 工具都选不出来。
        usable_by_subagent = "subagent" in scopes
        descriptors.append(
            {
                "name": name,
                # 后端不产出中文工具名；要 i18n 走分类那套 label key。
                "label": name,
                "description": description,
                "category": TOOL_CATEGORIES.get(name, DEFAULT_TOOL_CATEGORY),
                "risk": TOOL_RISK_OVERRIDES.get(
                    name, _TOOL_RISK_BY_READ_ONLY[0 if tool.read_only else 1]
                ),
                "scope": "subagent" if usable_by_subagent else "core",
                # 主 Agent 专属工具：档案里看得见但勾不了，否则用户会以为
                # 勾了就能用，实际运行时会因不在子 Agent 工具集里被静默丢弃。
                "blocked": not usable_by_subagent,
                "blockedReason": (
                    None if usable_by_subagent else "主 Agent 专属，子 Agent 不可用"
                ),
                # 只有「档案跑起来的最小组装集」才锁死。之前用
                # ``scopes & {"core","memory"}`` 判会让全部 14 个工具都被锁死，
                # 档案页等于无法收紧能力——而收紧能力正是档案的核心用途。
                "locked": name in _MIN_LOCKED_TOOL_NAMES,
            }
        )
    return descriptors


def _skill_descriptors(workspace: Path) -> list[dict[str, Any]]:
    from nanobot.webui.skills_api import webui_skills_payload  # local: 避免导入环

    entries = webui_skills_payload(Path(workspace)).get("skills", [])
    descriptors: list[dict[str, Any]] = []
    for raw in cast("list[object]", entries):
        if not isinstance(raw, dict):
            continue
        entry = cast("dict[str, object]", raw)
        source = entry.get("source", "builtin")
        descriptors.append(
            {
                "name": entry.get("name", ""),
                "description": entry.get("description", ""),
                "source": source if source in _SKILL_SOURCES else "builtin",
                # SkillsLoader 不产出标签。给空数组比编造标签诚实。
                "tags": [],
            }
        )
    return descriptors


def _model_descriptors(config: Config) -> list[dict[str, Any]]:
    """按 ``settings_models`` 的口径投影一份模型清单。

    整份 ``model_settings_payload`` 还带 provider 认证态，与档案选择器无关；
    这里只要 ``model_presets`` 那一段，所以复用同一套 default 预设解析，
    避免在第二处复刻它。provider 行是这份 payload 的必经计算，用真实的
    ``oauth_provider_status`` 读一遍；这里不需要认证态本身，只要不抛异常。
    """
    from nanobot.webui.settings_models import (  # local: 避免导入环
        model_settings_payload,
        oauth_provider_status,
    )

    payload = model_settings_payload(config, oauth_status=oauth_provider_status)
    descriptors: list[dict[str, Any]] = []
    for raw in cast("list[object]", payload.get("model_presets", [])):
        if not isinstance(raw, dict):
            continue
        preset = cast("dict[str, object]", raw)
        window = preset.get("context_window_tokens")
        provider = preset.get("resolved_provider") or preset.get("provider") or "auto"
        name = preset.get("name", "")
        descriptors.append(
            {
                "id": name,
                "label": preset.get("label") or name,
                "provider": provider,
                "contextWindow": (
                    window
                    if isinstance(window, int) and window > 0
                    else _DEFAULT_CONTEXT_WINDOW
                ),
                # 出现在 config.model_presets 里就是用户已配置可用的。
                "health": "healthy",
                # 配置层没有逐模型能力表；nanobot 的 agent loop 本身就以工具
                # 调用驱动，图片走同一 provider 通道。见契约 §8.3。
                "vision": True,
                "toolUse": True,
            }
        )
    return descriptors


def _mcp_server_descriptors(config: Config) -> list[dict[str, Any]]:
    """按契约 §8.4 把全局 ``tools.mcp_servers`` 投影成只读展示用描述符。

    ``mcp_servers`` 是全局配置，MCP 工具运行时才动态注册为 ``mcp_<server>_<tool>``，
    不在静态工具目录里；「按 Agent 勾选 MCP」当前没有运行时语义，所以一期只读展示。
    保持 config 里的书写顺序，不排序 —— 那是用户在配置文件里排的序。

    **安全**：`headers` / `env` 可能含凭据（OAuth token、API key），一律不下发；
    ``args`` / ``cwd`` 一并省略，``command`` / ``url`` 已足够让用户认出是哪个 server。
    """
    descriptors: list[dict[str, Any]] = []
    for name, server in config.tools.mcp_servers.items():
        server_type = server.type or _MCP_TYPE_AUTO
        all_tools = server.enabled_tools == ["*"]
        descriptors.append(
            {
                "name": name,
                "type": server_type,
                "command": server.command if server_type == "stdio" else "",
                "url": server.url if server_type in _MCP_HTTP_TYPES else "",
                # ``["*"]`` 是「全放行」的哨兵值，不是「启用了 1 个工具」。
                "toolCount": 0 if all_tools else len(server.enabled_tools),
                "allTools": all_tools,
            }
        )
    return descriptors


def agents_catalog(
    workspace: Path,
    *,
    load_config: Callable[[], Config] | None = None,
) -> dict[str, Any]:
    if load_config is None:
        raise WebUISettingsError("agent catalog is not configured", status=503)
    config = load_config()
    return {
        "tools": _tool_descriptors(workspace, config.tools),
        "skills": _skill_descriptors(workspace),
        "models": _model_descriptors(config),
        "mcpServers": _mcp_server_descriptors(config),
        "categories": [c.to_dict() for c in _store(workspace).list_categories()],
    }
