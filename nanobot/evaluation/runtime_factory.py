"""T05：为单个 case_attempt 构造隔离运行环境（spec §8）。

顺序是硬要求：**先**把本进程的配置路径上下文指向 attempt 目录，**再**构造 SDK ——
``Config.runtime_data_dir`` 由 ``bind_source_path`` 的父目录决定，只覆盖 workspace 并不能
隔离会话、记忆与运行时数据。

凭据只存在于内存与私有管道：profile_bundle 深合并进 ``Config`` 的内存副本，原始 boot 与合并后的
配置都不落盘；落盘的只有该 attempt 自己的会话/记忆/Trace 目录。

工具侧沿用 SDK 的构造链（``AgentLoop.from_config`` + 现有 hook factories），默认注册的 20 多个工具
在注册表里被**逐个 unregister**（不是只隐藏名字），再补上契约允许的受控工具；MCP provider 干脆不构造，
因此不存在自动连接。最后把生效能力清单交给报告（spec §7.3 的工具口径）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.agent.hooks import create_file_edit_activity_hook
from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config import loader
from nanobot.config.schema import Config
from nanobot.evaluation.fixtures import (
    CONTROLLED_TOOL_NAMES,
    ControlledClock,
    FixtureTable,
    materialize_fixtures,
    register_controlled_tools,
    seed_memory,
)
from nanobot.evaluation.schemas import AgentWorkerBoot, CaseWrite, JudgeWorkerBoot, ToolFixture
from nanobot.nanobot import Nanobot
from nanobot.session.manager import SessionManager

ATTEMPT_SESSION_PREFIX = "eval:"
CONFIG_FILE_NAME = "config.json"

RUNTIME_ROOT_NOT_ABSOLUTE = "RUNTIME_ROOT_NOT_ABSOLUTE"
PROFILE_BUNDLE_INVALID = "PROFILE_BUNDLE_INVALID"
PROFILE_MODEL_MISSING = "PROFILE_MODEL_MISSING"
TOOL_NOT_REGISTERED = "TOOL_NOT_REGISTERED"
SANDBOX_VIOLATION = "SANDBOX_VIOLATION"

#: 首版 Worker 绝不允许出现的能力（spec §8：原生 shell、任意 MCP、投递、自修改、goal、插件等）。
FORBIDDEN_TOOLS: frozenset[str] = frozenset(
    {
        "exec", "exec_session", "list_exec_sessions", "apply_patch", "run_cli_app",
        "spawn", "message", "send_session_message", "my",
        "create_goal", "update_goal", "list_sessions", "read_session", "search_sessions",
        "ask_question",
    }
)
#: 报告里显式声明已关闭的外部副作用子系统。
DISABLED_SUBSYSTEMS: tuple[str, ...] = (
    "mcp_auto_connect",
    "channel_delivery",
    "cron_jobs",
    "dream_consolidation",
    "self_modification",
    "native_shell",
    "subagent_spawn",
    "sustained_goals",
    "cli_apps",
    "image_generation",
    "third_party_tool_plugins",
)

_MCP_PREFIX = "mcp_"


@dataclass(frozen=True)
class AttemptPaths:
    """一个 attempt 专属的目录布局（全部落在服务端下发的 runtime root 之下）。

    会话存储是唯一的外插：放在 attempt 目录里会变成
    ``<attempt>/runtime/sessions/<workspace-id>/<base64 会话键>.jsonl.<随机>.tmp``，
    在 Windows 上直接顶穿 260 字符路径上限（实测 pytest 临时目录 + attempt 目录
    就已经到 281）。会话内容靠会话键前缀与按工作区哈希的命名空间隔离，不与
    attempt 目录同住也不会串。
    """

    attempt_id: str
    root: Path
    runtime_root: Path
    workspace: Path
    config_path: Path
    sessions_root: Path
    trace_dir: Path
    artifact_dir: Path
    log_path: Path

    def create(self) -> None:
        for directory in (
            self.root, self.runtime_root, self.workspace,
            self.trace_dir, self.artifact_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@dataclass
class AttemptRuntime:
    """Worker 拿到的全部运行侧对象；关闭时只释放本 attempt 的 SDK 资源。"""

    attempt_id: str
    role: str
    paths: AttemptPaths
    config: Config
    bot: Nanobot
    loop: AgentLoop
    registry: ToolRegistry
    capability: dict[str, Any]
    session_key: str
    clock: ControlledClock
    _closed: bool = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def provider(self) -> Any:
        """实际生效的 provider 实例（Worker 只在这里挂 usage observer）。"""
        return self.loop.provider

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.bot.aclose()


def attempt_paths(*, attempt_id: str, runtime_root: str, workspace: str) -> AttemptPaths:
    root = Path(runtime_root)
    if not root.is_absolute() or not Path(workspace).is_absolute():
        raise ValueError(
            f"{RUNTIME_ROOT_NOT_ABSOLUTE}: runtime_root and workspace must be absolute"
        )
    runtime = root.resolve()
    resolved_workspace = Path(workspace).resolve()
    attempt_dir = runtime.parent
    return AttemptPaths(
        attempt_id=attempt_id,
        root=attempt_dir,
        runtime_root=runtime,
        workspace=resolved_workspace,
        config_path=runtime / CONFIG_FILE_NAME,
        sessions_root=attempt_dir.parent / "sessions",
        trace_dir=attempt_dir / "trace",
        artifact_dir=attempt_dir / "artifacts",
        log_path=attempt_dir / "worker.log",
    )


def _merge(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """递归合并配置片段；列表与非 dict 值整体替换，不做元素级追加。"""
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _materialize_target_files(
    paths: Any, target: Mapping[str, Any] | None
) -> tuple[list[str], list[dict[str, Any]]]:
    """把快照里冻结的 Agent 档案与 Skill 文件写进 attempt workspace。

    路径校验是硬边界：rel 必须落在 workspace 内，防止快照被塞进 ``../`` 逃逸。
    """
    # 返回 (写入清单, 技能清单)：技能清单带每个文件的内容哈希，
    # 判分侧要能回答「这次跑的到底是哪份技能内容」，只记技能名等于没有证据。
    if not isinstance(target, Mapping):
        return [], []
    written: list[str] = []
    skill_files: dict[str, list[dict[str, Any]]] = {}
    agent = target.get("agent") or {}
    profile = agent.get("profile") if isinstance(agent, Mapping) else None
    if isinstance(profile, Mapping) and profile.get("id"):
        base = paths.workspace / "agents" / "profiles"
        base.mkdir(parents=True, exist_ok=True)
        dest = base / f"{profile['id']}.json"
        dest.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
        written.append(f"agents/profiles/{profile['id']}.json")
    skill = target.get("skill") if isinstance(target, Mapping) else None
    payload: Mapping[str, Any] = {}
    if isinstance(skill, Mapping) and isinstance(skill.get("files"), Mapping):
        payload = skill["files"]
    elif isinstance(agent, Mapping) and isinstance(agent.get("skills"), Mapping):
        payload = agent["skills"]
    root = paths.workspace.resolve()
    for rel, content in payload.items():
        dest = (paths.workspace / str(rel)).resolve()
        if not dest.is_relative_to(root):
            raise ValueError("TARGET_PATH_ESCAPE")
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = str(content)
        dest.write_text(text, encoding="utf-8")
        written.append(str(rel))
        parts = str(rel).split("/")
        if len(parts) >= 3 and parts[0] == "skills":
            raw = text.encode("utf-8")
            skill_files.setdefault(parts[1], []).append({
                "path": str(rel),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            })
    skills = [
        {"name": name, "files": skill_files[name]} for name in sorted(skill_files)
    ]
    return written, skills


def _sandbox_overlay(
    paths: AttemptPaths,
    max_iterations: int | None,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "workspace": str(paths.workspace),
        "idleCompactAfterMinutes": 0,
        "idle_compact_check_interval_seconds": 0,
        "unifiedSession": False,
        # 外部投递与后台任务一律关掉：评估进程没有渠道，也不该有。
        "dream": {"enabled": False},
        "fallbackModels": [],
    }
    if max_iterations is not None:
        defaults["maxToolIterations"] = max_iterations
    return {
        "agents": {"defaults": defaults},
        "tools": {
            "restrictToWorkspace": True,
            "mcpServers": {},
            "ssrfWhitelist": [],
            # 原生 shell 永远关闭：live 模式也不再提供 exec/exec_session/apply_patch，
            # 需要执行能力时用受控工具表达，不靠放开 shell。
            "exec": {"enable": False},
            "my": {"enable": False},
            "cliApps": {"enable": False},
            "imageGeneration": {"enabled": False},
            "webuiAllowLocalServiceAccess": False,
            "webuiAllowRemotePackageInstall": False,
        },
        # ChannelsConfig 是 extra=allow：整体替换才能连用户配置里的渠道条目一起清掉。
        "channels": {
            "sendProgress": False,
            "sendToolHints": False,
            "showReasoning": False,
        },
    }


def build_attempt_config(
    *,
    profile_bundle: Mapping[str, Any],
    paths: AttemptPaths,
    max_iterations: int | None = None,
    base_config: Config | None = None,
) -> Config:
    """内存里合成执行配置：profile 先合并，沙箱覆盖层最后生效且不可被 profile 翻转。"""
    config_part = profile_bundle.get("config")
    if not isinstance(config_part, Mapping):
        raise ValueError(
            f"{PROFILE_BUNDLE_INVALID}: profile_bundle.config must be an object"
        )
    base = base_config if base_config is not None else Config()
    data = _merge(
        base.model_dump(mode="json", by_alias=True), config_part
    )
    data = _merge(data, _sandbox_overlay(paths, max_iterations))
    config = Config.model_validate(data)
    # 只记路径归属，不写文件：runtime_data_dir 由此指向 attempt 的 runtime root。
    config.bind_source_path(paths.config_path)
    if not config.resolve_preset().model.strip():
        raise ValueError(f"{PROFILE_MODEL_MISSING}: agents.defaults.model is empty")
    return config


def _tool_fixtures(case: CaseWrite | None) -> list[ToolFixture]:
    return list(case.tool_fixtures) if case is not None else []


def _apply_tool_whitelist(
    registry: ToolRegistry, *, allowed: list[str], table: FixtureTable
) -> tuple[list[str], list[str], list[str]]:
    """注销所有未授权的默认工具，再注册契约允许的受控工具。"""
    registered_before = list(registry.tool_names)
    for name in registered_before:
        if name not in allowed:
            registry.unregister(name)
    controlled = register_controlled_tools(registry, table, allowed)
    registered = list(registry.tool_names)
    missing = [name for name in allowed if name not in registered]
    if missing:
        raise ValueError(f"{TOOL_NOT_REGISTERED}: {', '.join(sorted(missing))}")
    forbidden = [
        name for name in registered
        if name in FORBIDDEN_TOOLS or name.startswith(_MCP_PREFIX)
    ]
    if forbidden:
        raise ValueError(f"{SANDBOX_VIOLATION}: {', '.join(sorted(forbidden))}")
    removed = [name for name in registered_before if name not in registered]
    return registered, removed, controlled


def _missing_required_inputs(workspace: Path, required: list[str]) -> list[str]:
    """返回运行期缺失的必需输入文件。"""
    missing: list[str] = []
    for rel in required:
        if not (workspace / rel).exists():
            missing.append(rel)
    return missing


def _requested_skill_names(
    target: Any, skills: list[dict[str, Any]]
) -> list[str]:
    """本次运行声明要加载的技能名。

    Skill 目标以被测对象的技能名为准；Agent 目标由档案冻结出来的技能目录名决定。
    只列被冻结的技能：评估不加载用户工作区里的其它技能，避免「跑的不是声明的那个」。
    """
    names: set[str] = set()
    if isinstance(target, Mapping):
        skill = target.get("skill")
        if isinstance(skill, Mapping) and skill.get("name"):
            names.add(str(skill["name"]))
    names.update(str(item.get("name")) for item in skills if item.get("name"))
    return sorted(names)


async def create_attempt(
    boot: AgentWorkerBoot | JudgeWorkerBoot,
    *,
    provider: Any | None = None,
    base_config: Config | None = None,
) -> AttemptRuntime:
    """按 boot 建立隔离运行环境，并完成文件种子、记忆种子与能力清单。"""
    paths = attempt_paths(
        attempt_id=str(boot.attempt_id),
        runtime_root=boot.runtime_root,
        workspace=boot.workspace,
    )
    paths.create()
    # 先设配置上下文，再构造 SDK：任何按 get_config_path() 派生路径的子系统都指向本 attempt。
    loader.set_config_path(paths.config_path)

    case = getattr(boot, "case_snapshot", None)
    execution = case.execution if isinstance(case, CaseWrite) else None
    allowed = list(execution.allowed_tools) if execution is not None else []
    clock = ControlledClock(case.clock_iso if isinstance(case, CaseWrite) else None)
    # 生效迭代上限 = 用例限制与 run 级限制的更严值。boot.limits 是服务端
    # 收敛后下发的；直接把 run 的更宽值放行等于让 run 覆盖放宽用例限制。
    effective_max_iterations: int | None = (
        execution.max_iterations if execution is not None else None
    )
    boot_limits = getattr(boot, "limits", None)
    if (
        boot_limits is not None
        and effective_max_iterations is not None
    ):
        effective_max_iterations = min(
            effective_max_iterations, int(boot_limits.max_iterations)
        )
    written_files, skills = _materialize_target_files(
        paths, getattr(boot, "target", None)
    )
    config = build_attempt_config(
        profile_bundle=boot.profile_bundle,
        paths=paths,
        max_iterations=effective_max_iterations,
        base_config=base_config,
    )
    if provider is None and isinstance(case, CaseWrite) and case.script:
        # fixture 模式 + 声明了脚本：走受控 provider，判分链路才能离线端到端跑通。
        # 没声明脚本就照常按配置构造真实 provider——静默回放空回答会让「Agent 什么
        # 都没说」看起来像一次正常执行。
        from nanobot.evaluation.controlled_provider import (
            ControlledProvider,
            steps_from_script,
        )

        provider = ControlledProvider(
            steps=steps_from_script(
                [step.model_dump(mode="json") for step in case.script]
            ),
            generation=dict(boot.profile_bundle.get("generation") or {}),
        )

    registry = ToolRegistry()
    # 不构造 MCPProvider：首版 Worker 没有自动连接外部 MCP 的入口。
    # 会话根显式注入：不能靠 config 路径推导（那会把会话逼进 attempt 深目录，
    # Windows 260 上限撑不住），见 AttemptPaths 的说明。
    session_manager = SessionManager(
        config.workspace_path, sessions_root=paths.sessions_root
    )
    loop = AgentLoop.from_config(
        config,
        tool_registry=registry,
        hook_factories=[create_file_edit_activity_hook],
        provider=provider,
        session_manager=session_manager,
    )
    table = FixtureTable.from_fixtures(
        _tool_fixtures(case if isinstance(case, CaseWrite) else None), clock=clock
    )
    registered, removed, controlled = _apply_tool_whitelist(
        registry, allowed=allowed, table=table
    )

    bot = Nanobot(loop, config=config)
    session_key = f"{ATTEMPT_SESSION_PREFIX}{boot.attempt_id}"
    fixtures_written: list[str] = []
    seeded = 0
    if isinstance(case, CaseWrite):
        fixtures_written = materialize_fixtures(paths.workspace, case.fixtures)
        missing = _missing_required_inputs(paths.workspace, case.required_inputs)
        if missing:
            # 缺输入是 setup 级失败：明确报缺，不让后续用空文件假装任务成功。
            raise ValueError(f"CASE_INPUT_MISSING: {', '.join(missing)}")
        seeded = seed_memory(bot.memory, case.memory_seed, session_key=session_key)

    capability = {
        "role": boot.role,
        "tool_mode": execution.tool_mode if execution is not None else "none",
        "allowed_tools": allowed,
        "registered_tools": sorted(registered),
        "removed_tools": sorted(removed),
        "controlled_tools": sorted(controlled),
        "fixture_files": sorted(fixtures_written),
        "memory_seed_entries": seeded,
        "external_dependencies": execution.tool_mode == "live" if execution is not None else False,
        "disabled_subsystems": list(DISABLED_SUBSYSTEMS),
        "controlled_tool_names": sorted(CONTROLLED_TOOL_NAMES),
        "model": config.resolve_preset().model,
        "provider": config.resolve_preset().provider,
        "clock_iso": clock.clock_iso,
        "max_iterations": effective_max_iterations,
        "timeout_s": execution.timeout_s if execution is not None else None,
        # 生效口径：真正注册进本次运行的工具白名单，以及真正生效的限额。
        "effective_allowed_tools": sorted(registered),
        "effective_limits": {
            "max_iterations": effective_max_iterations,
            "timeout_s": execution.timeout_s if execution is not None else None,
        },
        "skills": {
            "requested": _requested_skill_names(getattr(boot, "target", None), skills),
            "materialized": skills,
            "restricted_to_frozen": True,
        },
    }
    return AttemptRuntime(
        attempt_id=str(boot.attempt_id),
        role=boot.role,
        paths=paths,
        config=config,
        bot=bot,
        loop=loop,
        registry=registry,
        capability=capability,
        session_key=session_key,
        clock=clock,
    )
