"""启动使用现有模型配置、隔离会话与记忆的本地浏览器测试实例。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from nanobot.config.loader import (  # noqa: E402
    load_config,
    resolve_config_env_vars,
    set_config_path,
)


def main() -> None:
    config = resolve_config_env_vars(load_config())
    run_dir = ROOT / ".ai-runtime-artifacts/verifications/2026-11-07-memory-browser"
    home = run_dir / "home"
    workspace = run_dir / "workspace"
    runtime = run_dir / "runtime"
    for directory in (home, workspace, runtime):
        directory.mkdir(parents=True, exist_ok=True)
    config_path = runtime / "config.json"

    config.agents.defaults.workspace = str(workspace)
    config.agents.defaults.model_preset = "MiniMax-M3.1-Flash"
    config.agents.defaults.fallback_models = []
    config.agents.defaults.dream.enabled = False
    config.agents.defaults.consolidate.enabled = False
    config.tools.mcp_servers = {}
    config.tools.restrict_to_workspace = True
    config.api.api_key = ""
    config.evaluation.passphrase = ""
    config.gateway.host = "127.0.0.1"
    config.gateway.port = 18791
    config.gateway.heartbeat.enabled = False
    from nanobot.config.schema import ChannelsConfig

    config.channels = ChannelsConfig.model_validate({
        "websocket": {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 8765,
            "path": "/",
            "tokenIssuePath": "/auth/token",
            "tokenIssueSecret": "memory-browser-local-test-20261107",
            "websocketRequiresToken": True,
            "allowFrom": ["*"],
            "streaming": True,
        },
    })
    config.bind_source_path(config_path)
    data = config.model_dump(mode="json", by_alias=True)
    counter = 0

    def environment_references(value):
        nonlocal counter
        if isinstance(value, dict):
            return {
                key: item if key == "apiType" else environment_references(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [environment_references(item) for item in value]
        if isinstance(value, str) and value:
            counter += 1
            name = f"ZHIXU_BROWSER_TEST_PROVIDER_{counter}"
            os.environ[name] = value
            return "${" + name + "}"
        return value

    # 凭据仅留在本进程环境；测试配置不复制真实 provider 密钥。
    data["providers"] = environment_references(data.get("providers", {}))
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.environ["HOME"] = str(home)
    os.environ["USERPROFILE"] = str(home)
    set_config_path(config_path)

    from loguru import logger
    from nanobot.cli.gateway_runtime import _run_gateway

    logger.add(run_dir / "gateway.log", level="INFO", encoding="utf-8")
    print("Isolated browser test: http://127.0.0.1:5173", flush=True)
    print("Model preset: MiniMax-M3.1-Flash; memory backend: "
          + config.agents.defaults.memory_search_backend, flush=True)
    _run_gateway(config, port=18791, webui_static_dist=False)


if __name__ == "__main__":
    main()
