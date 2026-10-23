---
artifact: spec
route: superpowers:brainstorming
skills: [brainstorming, source-driven-development, api-and-interface-design]
skills_evidence:
  - .claude/skills/brainstorming/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/api-and-interface-design/SKILL.md
source:
  - specs/2026-10-19-zhixu-rename-spec.md
  - contracts/2026-10-19-contract-zhixu-rename-mapping.md
  - stack/2026-10-19-zhixu-rename-stack.md
created_at: 2026-10-19
status: draft
approved: false
---

# Phase 3 方案：引擎与数据（🔴 高风险）

## 0. 定位

第三期，也是把「改名」真正做进引擎的一期。**触碰用户真实数据**（`~/.nanobot` 里的会话、配置、凭据、工作区）与**用户已有配置**（50 个环境变量）。

风险等级高不是因为改动量大，而是因为**失败形态是静默的**：

- 数据目录没迁移 → 用户看到的是「历史会话全没了」，而不是报错。
- 环境变量硬切 → `loader.py:68` 只在 JSON 解析失败时才提示 `NANOBOT_*`，**变量名拼错不报错**，表现为「配置读不到」。

因此本期所有对外标识符的改动，都必须**带兼容层**，不允许裸切。

## 1. 本期边界

| 做 | 不做（已在 Phase 1/2 完成） |
| --- | --- |
| Python 包目录 `nanobot/` → `zhixu/` | 提示词与文案（Phase 1） |
| CLI 命令、PyPI 分发名 | 前端符号、HTTP 路由/头（Phase 2） |
| 环境变量 `NANOBOT_*` → `ZHIXU_*` | |
| 数据目录 `~/.nanobot` → `~/.zhixu` | |
| SQLite 旧 key 惰性回退 | |
| 构建五连、渠道前端 glob | |
| Docker service 名、CI | |

## 2. 兼容策略总览

| 标识符 | 策略 | 兼容期 |
| --- | --- | :---: |
| `~/.nanobot` → `~/.zhixu` | 自动迁移 + 旧目录缺失即回退 | ≥1 大版本 |
| `NANOBOT_*` → `ZHIXU_*` | 启动期归一（单向）+ deprecation warning | ≥1 大版本 |
| SQLite 4 个 `_nanobot_*` key | 惰性回退 + 首次读到即回写 | ≥1 大版本 |
| `hatch_build.py` / `Dockerfile` build-arg | 构建期不经过 Python 入口，**逐点接受旧名**（唯一例外） | ≥1 大版本 |
| Docker 系统用户 `nanobot` | **不改** | 永久 |
| 第三方服务标识符（8 处） | **不改** | 永久 |
| CLI 命令 `nanobot` | **保留 alias** | 永久 |
| 上游归属 `HKUDS/nanobot`、`nanobot.wiki` | **不改** | 永久 |

## 3. 改动清单

### 3.1 WU-A：Python 包目录与 import（**必须最先，独占执行**）

| 动作 | 内容 |
| --- | --- |
| `git mv nanobot/ zhixu/` | 418 个包内文件 |
| `git mv zhixu/nanobot.py zhixu/zhixu.py` | SDK 门面 |
| `class Nanobot` → `class Zhixu` | `zhixu/zhixu.py:68` |
| `__init__.py` 的 `_LAZY_EXPORTS` / `__all__` | `"Nanobot": ".zhixu"`，导出名同步 |
| 替换 import | **4887 行 / 866 文件**：`tests/` 2672 行、`zhixu/` 内 2211 行 |
| `pkg_files("nanobot")` → `pkg_files("zhixu")` | `zhixu/utils/helpers.py:905,977` |
| `logger.enable("nanobot")` → `"zhixu"` | `zhixu/cli/log_control.py:10,12`、`conftest.py:15,19` |
| `_set_nanobot_logs` → `_set_zhixu_logs` | `cli/log_control.py:5,8`、`cli/commands.py:58`、`cli/agent.py:15` + 3 处调用 |
| `_parse_nanobot_metadata` / `_nanobot_metadata` → `_zhixu_*` | `agent/skills.py:358`、`webui/skills_api.py:266` |
| `_nanobot_webui_mutation_payload` / `_request` / `_caller_chats` / `_trusted_proxy_authenticated` → `_zhixu_*` | `webui/ws_http.py:150-153,469,482,523` |
| `_isolate_nanobot_log_activation` → `_isolate_zhixu_log_activation` | `conftest.py:15` |
| monkeypatch 目标字符串 | `conftest.py:38,42,53`（`"nanobot.session.manager.*"`）等**全部**按名解析点 |

**为什么必须独占**：整目录 `git mv` 期间仓库处于中间态，任何并行 WU 都会冲突。

**注意**：`nanobot/channels/*/tests/` 也在包目录内（`pyproject.toml` `exclude = ["nanobot/channels/*/tests/**"]`），随目录一并移动，`exclude` 路径同步改。

### 3.2 WU-B：CLI / 分发名 / 插件注册

| 旧 | 新 | 位置 |
| --- | --- | --- |
| `name = "nanobot-ai"` | `name = "zhixu-ai"` | `pyproject.toml:2` |
| `nanobot = "nanobot.cli.entry:main"` | `zhixu = "zhixu.cli.entry:main"` + **`nanobot = "zhixu.cli.entry:main"`（alias）** | `pyproject.toml:117` |
| `the nanobot contributors` | `the zhixu contributors` | `pyproject.toml:10` |
| `_pkg_version("nanobot-ai")` | `_pkg_version("zhixu-ai")` | `zhixu/__init__.py:49` |
| `name = "nanobot-ai"` | `name = "zhixu-ai"` | `uv.lock:2580`（**注意 `uv.lock` 当前被 `.gitignore` 排除，见 profile 待确认项**） |
| `entry_points(group="nanobot.tools")` | `group="zhixu.tools"` | `zhixu/agent/tools/loader.py:74` |
| `# [project.entry-points."nanobot.tools"]` | `"zhixu.tools"` | `pyproject.toml:121`（注释） |
| typer `name="nanobot"` | `name="zhixu"` + 旧名 alias | `zhixu/cli/commands.py:87` |
| `prog_name = "nanobot"` / `"nanobot agent"` | `"zhixu"` / `"zhixu agent"` | `zhixu/cli/entry.py:101` |
| `[sys.executable, "-m", "nanobot"]` | `"-m", "zhixu"` | `zhixu/command/builtin.py:263` |
| `_set_process_title(f"nanobot-{role}")` | `f"zhixu-{role}"` | `zhixu/cli/process_identity.py:29` |
| `name: str = "nanobot-gateway"`（服务名默认值） | `zhixu-gateway` | `zhixu/cli/gateway.py:357,389` |
| `["nanobot", "gateway", …]` / `["nanobot", "gateway", "--background", …]` | `["zhixu", …]` | `zhixu/cli/webui_support.py:571`、`zhixu/cli/webui.py:112` |
| `--name nanobot-gateway` 默认值 | `zhixu-gateway` | 同上 |

**⚠️ `typer` alias 机制**：typer/click 的多命令名需要显式注册。本期保留旧命令的推荐做法是在 `[project.scripts]` 多注册一行指向同一 `main`（最简单、无副作用）；**不要**试图用 typer 的 alias API（`@app.command(name=…)` 只设主名）。若需要旧命令在 `--help` 中可见，再评估 typer 层面的方案。

**别名冲突**：`zhixu` 是新命令，`nanobot` 是 alias。venv 重建后 `.venv/Scripts/` 下会同时出现 `zhixu.exe` 与 `nanobot.exe`，指向同一个 `main`。

### 3.3 WU-C：环境变量

**读取点**（只改字符串字面量，**不加 fallback 分支**）：

| 变量 | 位置 |
| --- | --- |
| `NANOBOT_LLM_TIMEOUT_S` | `zhixu/agent/loop.py`、`zhixu/agent/runner.py:1297`、`zhixu/session/goal_state.py` |
| `NANOBOT_STREAM_IDLE_TIMEOUT_S` | `agent/loop.py`、`agent/runner.py`、`providers/base.py` |
| `NANOBOT_MAX_CONCURRENT_REQUESTS` | `agent/loop.py:570` |
| `NANOBOT_PATH_APPEND` / `_PREPEND` | `agent/tools/shell.py` |
| `NANOBOT_TUI_*`（16 个） | `zhixu/cli/tui_launcher.py`、`tui/src/index.ts`、`tui/scripts/{pty,conpty}_smoke.py` |
| `NANOBOT_COPILOT_*` / `NANOBOT_GITHUB_*`（6 个） | `zhixu/providers/github_copilot_provider.py` |
| `NANOBOT_OPENAI_COMPAT_TIMEOUT_S` | `zhixu/providers/openai_compat_provider.py` |
| `NANOBOT_SANDBOX_ENFORCED` / `NANOBOT_WORKSPACE_SANDBOX_*` | `zhixu/security/workspace_access.py` |
| `NANOBOT_RESTART_*`（4 个） | `zhixu/utils/restart.py` |
| `NANOBOT_SKIP_WEBUI_BUILD` / `NANOBOT_FORCE_WEBUI_BUILD` | `zhixu/webui/build.py`（**构建期旧名例外见 §3.6**） |
| `NANOBOT_API_URL` | `zhixu/webui/dev.py`、`webui/vite.config.ts:111`（见 §3.5 归属） |
| `NANOBOT_EXTENSION_*`（2 个） | `zhixu/apps/cli/service.py` |
| `NANOBOT_TMUX_SOCKET_DIR` | `zhixu/skills/tmux/SKILL.md`、`find-sessions.sh` |
| `NANOBOT_HOME` | `desktop/main.cjs:14`（其余已是 `MIRA_*`，本期一并统一为 `ZHIXU_*`） |
| `NANOBOT_BIN_DIR` / `NANOBOT_VENV` / `NANOBOT_SKIP_WIZARD` | `scripts/install.sh`、`scripts/install.ps1` |
| `_NANOBOT_COMPLETE` | **不改**（typer shell 补全约定） |

**兼容层**（新建 `zhixu/utils/legacy_env.py`）：

```python
"""把遗留 NANOBOT_* 映射到 ZHIXU_*，使旧配置在新版本继续生效。"""
from __future__ import annotations
import os

_LEGACY_PREFIX = "NANOBOT_"
_NEW_PREFIX = "ZHIXU_"


def normalize_legacy_env() -> list[str]:
    """单向归一：旧名存在且新名缺失时复制到新名。返回命中的旧变量名。"""
    hits: list[str] = []
    for legacy, value in list(os.environ.items()):
        # 注意：_NANOBOT_COMPLETE 以下划线开头，天然不匹配本前缀。
        if not legacy.startswith(_LEGACY_PREFIX):
            continue
        new = _NEW_PREFIX + legacy[len(_LEGACY_PREFIX):]
        if new not in os.environ:
            os.environ[new] = value
            hits.append(legacy)
    return hits
```

**挂载点（4 个进程入口）**：

| 入口 | 位置 | 动作 |
| --- | --- | --- |
| CLI | `zhixu/cli/entry.py:85 main()` | 函数首行调用 |
| Python SDK | `zhixu/zhixu.py:78 Zhixu.__init__()` | `__init__` 首行调用 |
| OpenAI 兼容 API | `zhixu/api/server.py:478 create_app()` | 函数首行调用 |
| Gateway | `zhixu/gateway/runtime.py`（run/serve 启动处） | 启动后立即调用 |

**告警**：`hits` 非空时 `logger.warning`，输出形如：

```
[zhixu] 检测到 3 个遗留环境变量，将继续兼容但请改名：
  NANOBOT_WEB_TOKEN   -> ZHIXU_WEB_TOKEN
  NANOBOT_TUI_BIN     -> ZHIXU_TUI_BIN
  文档：docs/configuration.md
```

**单测要求**：`normalize_legacy_env` 需覆盖「旧名有值新名无值 → 复制」「两者都有 → 保留新名」「`_NANOBOT_COMPLETE` 不受影响」三条。

### 3.4 WU-D：数据目录与 SQLite

#### 3.4.1 数据目录迁移

**现状**（`zhixu/config/paths.py`）：

- `get_data_dir()` = `get_config_path().parent` —— 数据目录**由配置文件位置推导**，已有现成抓手。
- 但 `~/.nanobot/workspace`（`:53,75,81,82`）、`history/cli_history`（`:88`）、`sessions`（`:93`）是**硬编码**，不随 `--config` 走。

**设计**：新增统一入口，所有硬编码点收敛到它：

```python
# zhixu/config/paths.py
_LEGACY_DIR_NAME = ".nanobot"
_DIR_NAME = ".zhixu"


def resolve_data_root() -> Path:
    """解析数据目录；检测到遗留目录时自动迁移。"""
    new = Path.home() / _DIR_NAME
    legacy = Path.home() / _LEGACY_DIR_NAME
    if new.is_dir():
        return new
    if legacy.is_dir():
        try:
            os.rename(legacy, new)                    # 同设备：原子
        except OSError:                              # 跨设备 / 权限
            shutil.copytree(legacy, new)
            (legacy / ".migrated-to-zhixu").write_text(
                f"moved to {new}\n", encoding="utf-8")
        logger.warning(f"[zhixu] 数据目录已从 {legacy} 迁移到 {new}")
        return new
    new.mkdir(parents=True, exist_ok=True)
    return new
```

`get_workspace_path()` / `get_cli_history_path()` / `get_legacy_sessions_dir()` 全部改为基于 `resolve_data_root()` 拼接，**消除硬编码**。

**回滚**：⚠️ 迁移是「移动」，回滚到旧版本会看不到数据。对应措施见 §7 待确认（是否提供 `zhixu migrate-data --rollback`）。

#### 3.4.2 SQLite 旧 key 惰性回退

| 常量 | 位置 |
| --- | --- |
| `SESSION_MODEL_PRESET_METADATA_KEY` | `zhixu/session/model_selection.py:9` |
| `SESSION_REASONING_EFFORT_METADATA_KEY` | 同上 `:10` |
| `WORKSPACE_SCOPE_*` | `zhixu/security/workspace_access.py:25` |
| LLM usage source | `zhixu/llm_usage/context.py:13` |

**模式**（以 model preset 为例）：

```python
SESSION_MODEL_PRESET_METADATA_KEY = "_zhixu_model_preset"
_LEGACY_SESSION_MODEL_PRESET_METADATA_KEY = "_nanobot_model_preset"


def read_model_preset(session) -> str | None:
    new = session.metadata.get(SESSION_MODEL_PRESET_METADATA_KEY)
    if new is not None:
        return new
    legacy = session.metadata.get(_LEGACY_SESSION_MODEL_PRESET_METADATA_KEY)
    if legacy is not None:
        session.metadata[SESSION_MODEL_PRESET_METADATA_KEY] = legacy  # 惰性回写
    return legacy
```

**不写迁移脚本**：惰性迁移 O(1)、无需停机、天然幂等。代价是每个读点多一个分支，共 4 处。

### 3.5 WU-E：构建、渠道 glob、Docker、CI

**构建五连**（漏一处即 wheel 缺 WebUI 或 Docker 失败）：

| # | 位置 | 改动 |
| --- | --- | --- |
| 1 | `webui/vite.config.ts:131` | `outDir: ../nanobot/web/dist` → `../zhixu/web/dist` |
| 2 | `hatch_build.py` | `dist_dir = root / "nanobot" / "web" / "dist"` → `zhixu`；`from nanobot.webui import build` → `from zhixu.webui import build` |
| 3 | `pyproject.toml` | `[tool.hatch.build] include` / `artifacts` / `targets.wheel.packages` / `sources` / `targets.sdist` 全部 `nanobot` → `zhixu` |
| 4 | `Dockerfile` | `mkdir -p /app/nanobot/web`；`COPY nanobot/ nanobot/`；`COPY --from=webui-builder /app/nanobot/web/dist/`；`rm -rf nanobot` |
| 5 | `.dockerignore:8` | `nanobot/web/dist/` → `zhixu/web/dist/` |

**渠道前端 glob**（漏改则**渠道面板静默消失**，不报错）：

| 位置 | 改动 |
| --- | --- |
| `webui/src/channel-plugins/registry.ts:11,25` | glob `"../../../nanobot/channels/*/webui/index.{ts,tsx}"` → `zhixu`；**正则 `/nanobot\/channels\/([^/]+)\//` → `/zhixu\/channels\/([^/]+)\//`** |
| `webui/src/channel-plugins/locale-registry.ts:15,26` | 同上 |
| `webui/tsconfig.json:37,38` | `../nanobot/channels/*/webui/**` |
| `webui/eslint.config.js:16` | `nanobot/channels/*/webui/**` |
| `webui/tailwind.config.js:10` | 同上 |
| `webui/package.json:13` | lint 脚本内的 glob |

**工具配置**：

| 位置 | 改动 |
| --- | --- |
| `pyproject.toml:179` | `[tool.basedpyright] include = ["zhixu"]` |
| `pyproject.toml:186` | `testpaths = ["tests", "zhixu/channels"]` |
| `pyproject.toml:189` | `[tool.coverage.run] source = ["zhixu"]` |
| `pyproject.toml:153,156,168` | wheel `exclude` / `sources` / sdist `exclude` |
| `core_agent_lines.sh`（15 处） | 路径 |

**Docker（service 名改，系统用户不改）**：

| 位置 | 改动 |
| --- | --- |
| `docker-compose.yml:22,23,39,40,56` | `nanobot-gateway` / `nanobot-api` / `nanobot-cli` → `zhixu-*` |
| `docker-compose.bwrap.yml:9,12,15` | 同上 |
| `Dockerfile` | build-arg `NANOBOT_EXTRAS` / `NANOBOT_CHANNELS` → `ZHIXU_*`，**同时保留读取旧名** |
| `Dockerfile` / `entrypoint.sh` / `render.yaml` | **系统用户 `nanobot`、`/home/nanobot`、`setpriv --reuid=nanobot`、mountPath 全部不动** |
| `render.yaml:7` | `name: nanobot` → `zhixu`（Render 服务名，对外可见） |
| `render.yaml:32,46` | `NANOBOT_WEB_TOKEN` → `ZHIXU_WEB_TOKEN`（**并在 `render-config.json:22` 同步 `${ZHIXU_WEB_TOKEN}`**） |
| `render-config.json:22` | 同上 |
| `Dockerfile` build-arg 读取 | `ARG ZHIXU_EXTRAS=${NANOBOT_EXTRAS}`（Docker ARG 默认值引用前一 ARG，需实测；备选方案：`RUN` 内 `or` 判断） |

**⚠️ `Dockerfile` build-arg 兼容是本期唯一必须逐点加 fallback 的地方**——Dockerfile 与 `hatch_build.py` 是构建期，不经过 Python 进程入口，`normalize_legacy_env()` 覆盖不到。`hatch_build.py` 侧：

```python
if (os.environ.get("ZHIXU_SKIP_WEBUI_BUILD") or os.environ.get("NANOBOT_SKIP_WEBUI_BUILD")) == "1":
    ...
```

**CI**：

| 位置 | 改动 |
| --- | --- |
| `.github/workflows/ci.yml:90,95` | 路径过滤器 `nanobot/*` → `zhixu/*` |
| `.github/workflows/ci.yml:196` | `ruff check nanobot tests conftest.py` |
| `.github/workflows/ci.yml:207,215` | `--cov=nanobot` |
| `.github/workflows/ci.yml:385` | `docker build -t nanobot:test .` → `zhixu:test` |
| `.github/workflows/ci.yml:391,392,419` | compose 服务名 |
| `.github/workflows/ci.yml:399` | `setpriv --reuid=nanobot` — **保留**（系统用户） |
| `.github/workflows/ci.yml:426,430,431` | `/app/nanobot` 路径 → `/app/zhixu` |
| `.github/workflows/tui-release.yml:72,91` | 产物名 `nanobot-tui-*` → `zhixu-tui-*` |

**tmux**：`zhixu/skills/tmux/SKILL.md:14,16,17` 与 `find-sessions.sh` 的 `nanobot-tmux-sockets` / `nanobot.sock` / 会话 `nanobot-python` → `zhixu-*`。**存量 tmux 会话失联可接受，不做兼容。**

### 3.6 WU-F：文档、配置、发布

| 位置 | 改动 |
| --- | --- |
| `docs/`（64 md） | 命令字面量、`NANOBOT_*` 表、`pip install nanobot-ai`、`nanobot/channels/…` 路径 |
| `README.md` | 安装命令、徽章、`pip install zhixu-ai` |
| `SECURITY.md` | **保留上游维护者邮箱与 `HKUDS/nanobot` 归属** |
| `AGENTS.md` | 命令块、路径、`ruff check zhixu/`、`cd zhixu/web/dist` |
| `.agent/gotchas.md` / `design.md` | `zhixu/templates/`、`zhixu/skills/` |
| `harness-kit/project.profile.md` | 项目身份、主要目录表 —— **profile 至今仍写「feature/memory-system 分支」，本轮顺带纠正为 `feature/desktop-app`** |
| `harness-kit/context-map.md` | 同上 |
| `.claude/settings.json` | `Bash(nanobot …)` allowlist 30+ 条 → `zhixu`（Phase 1 已清理的死路径除外） |
| `THIRD_PARTY_NOTICES.md` | **不改**（上游署名） |
| `nanobot/skills/README.md` 的 OpenClaw 来源 | **不改** |
| **PyPI 占位包** | 旧 `nanobot-ai` 发一个只含说明的占位版本，指向 `zhixu-ai` |

## 4. 执行顺序与 WU 拆分

```
WU-A（独占，整目录 git mv + 4887 行 import）
   │
   ├─ WU-B  CLI / 分发名 / entry-point / loguru / 私有符号
   ├─ WU-C  环境变量（字面量 + legacy_env + 4 入口挂载 + 单测）
   ├─ WU-D  数据目录迁移 + SQLite 惰性回退
   ├─ WU-E  构建五连 + 渠道 glob + Docker + CI
   └─ WU-F  文档 / 配置 / PyPI 占位
```

WU-B ~ WU-F **可并行**（文件基本不相交），唯一交叉点已提前切分：

| 交叉文件 | 归属 |
| --- | --- |
| `webui/vite.config.ts` 的 `NANOBOT_API_URL` | **WU-E**（构建配置） |
| `tui/src/index.ts`、`tui/scripts/*.py` 的 `NANOBOT_TUI_*` | **WU-C** |
| `pyproject.toml` | **WU-B** 负责 `[project]`/`[project.scripts]`，**WU-E** 负责 `[tool.hatch.*]`/`[tool.basedpyright]`/`[tool.pytest]`/`[tool.coverage]` —— 建议 **合并给 WU-B 一次改完**，避免两人同时编辑 |

**venv 重建**：WU-A 完成后**立即**重建 `.venv`（当前是 `nanobot_ai-0.3.0.dist-info` + `_editable_impl_nanobot_ai.pth` 的 editable 安装，不重建则 `import zhixu` 失败）。WU-B ~ F 才能运行测试。

## 5. 替换脚本

一次性脚本 `scripts/rename_nanobot_to_zhixu.py`，**按序区分大小写**执行：

```
1. NANOBOT_   → ZHIXU_       （排除 _NANOBOT_COMPLETE）
2. NanoBot    → Zhixu
3. Nanobot    → Zhixu
4. nanobot    → zhixu
5. 恢复 §6 不改清单
```

第 5 步不可省略。单条 `sed s/nanobot/zhixu/g` 会把 `NANOBOT` 变成 `zhixu`（错）、`Nanobot` 变成 `zhixu`（错）。

**排除目录**：`.git/`、`.harness-worktrees/`、`.venv/`、`**/node_modules/`、`.ai-runtime-artifacts/`、`__pycache__/`、`.logo-cache/`、`.pytest_cache/`、`.ruff_cache/`。

## 6. 明确不改（替换脚本必须恢复）

| 类别 | 具体 |
| --- | --- |
| Docker 系统用户 | `useradd nanobot`、`/home/nanobot`、`setpriv --reuid=nanobot`、`mountPath` |
| 构建期旧名回退 | `hatch_build.py`、`Dockerfile` 的 `NANOBOT_SKIP_WEBUI_BUILD` / `NANOBOT_EXTRAS` / `NANOBOT_CHANNELS` |
| SQLite 旧 key | `_nanobot_model_preset`、`_nanobot_reasoning_effort`、`nanobot_workspace_scope`、`nanobot_llm_usage_source` |
| 第三方标识符 | `X-OpenRouter-Title`、`X-Keenable-Title`、`x-grok-client-identifier`、`DEFAULT_ORIGINATOR`、`TOKEN_APP_NAME`、`_VOLCENGINE_TRAFFIC_TAG`、MCP `client_name`、xai `referrer` |
| frontmatter 回退 | `data_object.get("nanobot")` / `get("openclaw")` |
| 兼容层自身 | `legacy_env.py` 的 `_LEGACY_PREFIX = "NANOBOT_"` |
| CLI alias | `[project.scripts] nanobot = "zhixu.cli.entry:main"` |
| 上游归属 | `HKUDS/nanobot`、`nanobot.wiki`、`THIRD_PARTY_NOTICES.md`、`SECURITY.md` 邮箱、OpenClaw 来源 |
| 历史产物 | `.ai-runtime-artifacts/` |

## 7. 待确认

1. **回滚支持**：数据目录迁移是「移动」，回滚旧版本会看不到数据。是否提供 `zhixu migrate-data --rollback`（约 30 行）？**推荐提供** —— 否则最坏情况需要用户手工 `mv`。
2. **旧 PyPI 包**：`nanobot-ai` 是发占位版本，还是仅在新包描述里指引？
3. **`uv.lock`**：当前被 `.gitignore` 排除但 CI 引用。本期改它的 `name` 行是否需要同时解除排除？
4. **tmux 会话**：存量 `nanobot-python` 会话失联可接受吗？（默认接受）

## 8. 验收

```powershell
# 0. venv 重建（WU-A 之后立即）
uv sync --all-extras --dev
.\.venv\Scripts\python.exe -c "import zhixu; print(zhixu.__version__, zhixu.__file__)"

# 1. Python 全绿
uv run --no-sync pytest -q
uv run --no-sync ruff check zhixu tests conftest.py
uv run --no-sync basedpyright

# 2. WebUI / TUI 无回归
cd webui; npx tsc -p tsconfig.build.json; npx vitest run; npm run build
cd tui;   npx tsc --noEmit

# 3. 构建五连齐全
uv build                                   # wheel 内应含 zhixu/web/dist/index.html
docker build -t zhixu:test . && docker run --rm zhixu:test zhixu --version

# 4. 残留自检（须逐条归入 §6）
rg -i nanobot --glob '!.git/**' --glob '!.harness-worktrees/**' --glob '!.venv/**' --glob '!**/node_modules/**' --glob '!.ai-runtime-artifacts/**'

# 5. 兼容层行为
#   a) 设 NANOBOT_WEB_TOKEN 启动 → 认证可用 + 打出 deprecation warning
#   b) 同时设 NANOBOT_WEB_TOKEN 与 ZHIXU_WEB_TOKEN → 新值优先
#   c) 手工建 ~/.nanobot/config.json + workspace，跑一次启动 → 自动迁移到 ~/.zhixu
#   d) 用带 _nanobot_model_preset 的旧 session DB 启动 → 设置被读到并回写新 key
#   e) NANOBOT_SKIP_WEBUI_BUILD=1 uv pip install → 构建期旧名仍生效

# 6. 测试隔离未被破坏
#   pytest 跑完后检查 home 目录无新增文件（验证 conftest monkeypatch 字符串已同步）
```

**行为验收**：

1. `zhixu --version` 与 `nanobot --version` **都可用**，输出一致。
2. 升级后历史会话、配置、凭据、工作区（身份/记忆/人设/写作）**完好**。
3. 老会话的模型预设与推理强度**未被重置**。
4. 旧环境变量配置**继续生效**，且日志有明确的改名提示。
5. WebUI 渠道面板**全部可见**（验证 `registry.ts` 正则）。
6. `docker compose run zhixu-cli status` 正常。

## 9. 风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 数据迁移失败被当成「数据丢了」 | 🔴 最坏 | `rename` 失败自动退化 `copytree` + 标记文件；验收 §8 行为项 3 实测；考虑提供 `--rollback` |
| 环境变量硬切 | 🔴 静默 | 启动期归一 + 单测 + 验收 §8 第 5 条 |
| `registry.ts` 正则漏改 | 🟠 渠道面板静默消失 | 验收行为项 5 逐渠道目视 |
| 构建五连漏改 | 🟠 wheel 缺 WebUI | 验收 §8 第 3 条实测 `uv build` + `docker build` |
| `conftest` monkeypatch 漏改 | 🟠 测试写进真实 home | 验收 §8 第 6 条 |
| `Dockerfile` build-arg 兼容方案不可行 | 🟡 构建期旧名失效 | `ARG` 默认值引用需实测，备选 `RUN` 内 `or` 判断；两条路都在 spec 里 |
| venv 未重建导致测试全红 | 🟡 | WU-A 后立即重建，列为 WU-A 完成判据 |
| 回滚旧版本看不到数据 | 🟡 | 待确认项 1 |

## 10. Spec 自检

- **占位符**：无。行号来自调研实测；Docker ARG 兼容方案标注「需实测 + 备选」，不是 TBD。
- **内部一致**：§2 兼容策略总览与 §3 各 WU 的做法一致；§6 不改清单与契约 §6 残留分类一一对应。
- **歧义**：`normalize_legacy_env` 与 `resolve_data_root` 均给出完整代码形态；Docker ARG 的不确定性已显式标注并给备选。
- **范围**：可拆为 6 个 WU（1 独占 + 5 并行），符合 Tier 2 编排门禁。

## Next

- 本期方案无误 → 说「写计划」或「制定实施计划」
- 先回答 §7 四个待确认项 → 我据此定稿
- 需要调整 → 直接说修改意见
