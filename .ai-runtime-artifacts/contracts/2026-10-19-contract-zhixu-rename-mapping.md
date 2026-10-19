---
artifact: decision-record
route: api-and-interface-design
skills: [api-and-interface-design]
skills_evidence: [.claude/skills/api-and-interface-design/SKILL.md]
source:
  - research/2026-10-19-nanobot-rename-scope-research.md
  - stack/2026-10-19-zhixu-rename-stack.md
  - decisions/2026-10-09-mira-rebrand-scope.md
  - specs/2026-09-30-brand-rebrand-spec.md
created_at: 2026-10-19
status: draft
approved: false
---

# 契约：nanobot → zhixu 标识符映射表（三期共享唯一真相源）

## 0. 这份契约解决什么

改名横跨三期，存在**跨期一致性风险**：Phase 1 改了提示词里的名字，Phase 3 又要改同一个文件里的环境变量名，两边对「新名字怎么拼」的理解一旦不一致，就会出现 `ZHIXU_TUI_BIN` vs `Zhixu_TUI_BIN` 这类漂移。

**本表是唯一真相源。** 三期的所有替换必须查本表，不得自行发挥。表中任何一条要改，先改本表再改代码。

## 1. 命名规则（先定规则，再填表）

| 类别 | 规则 | 示例 |
| --- | --- | --- |
| Python 包 / 模块 | 全小写，无分隔符 | `nanobot` → `zhixu` |
| Python 类名 | CapWords | `Nanobot` → `Zhixu` |
| 前端 Hook | `use` + CapWords | `useNanobotStream` → `useZhixuStream` |
| 环境变量 | `大写下划线` | `NANOBOT_TUI_BIN` → `ZHIXU_TUI_BIN` |
| npm 私有包 | 小写连字符 | `nanobot-webui` → `zhixu-webui` |
| npm scope | `@小写/tui` | `@nanobot/tui` → `@zhixu/tui` |
| PyPI 分发名 | 小写连字符 | `nanobot-ai` → `zhixu-ai` |
| HTTP 头 | `X-Word-Word` | `X-Nanobot-Auth` → `X-Zhixu-Auth` |
| localStorage key | 小写连字符 | `nanobot-webui.theme` → `zhixu-webui.theme` |
| 数据目录（点开头小写） | `~/.nanobot` → `~/.zhixu` | |
| entry-point group | 小写点分 | `nanobot.tools` → `zhixu.tools` |
| Docker 系统用户 | 小写 | **不改**，见 §4.3 |

**注意大小写敏感性**：仓内 `nanobot` 命中 14300 次、`Nanobot` 741 次、`NANOBOT` 307 次、`NanoBot` 2 次。替换脚本必须**区分大小写**分别处理，不能用 `sed -i 's/nanobot/zhixu/g'` 一把梭——那会把 `NANOBOT` 变成 `zhixu`（错）、把 `Nanobot` 变成 `zhixu`（错，应为 `Zhixu`）。

## 2. 主映射表

### 2.1 产品标识

| 旧 | 新 | 位置 | 期 |
| --- | --- | --- | --- |
| `nanobot-ai`（PyPI 分发名） | `zhixu-ai` | `pyproject.toml:2`、`uv.lock`、`__init__.py:49` `_pkg_version()` | 3 |
| `nanobot`（CLI 命令） | `zhixu` + **保留 `nanobot` alias** | `pyproject.toml:117`、`cli/commands.py:87`、`cli/entry.py:101` | 3 |
| `nanobot agent`（子命令提示） | `zhixu agent` | `cli/entry.py:101` | 3 |

### 2.2 Python 代码标识符

| 旧 | 新 | 位置 | 期 |
| --- | --- | --- | --- |
| 包目录 `nanobot/` | `zhixu/` | 仓库根 | 3 |
| `from nanobot…` / `import nanobot…` | `from zhixu…` / `import zhixu…` | 866 文件 / 4887 行 | 3 |
| `nanobot/nanobot.py` | `zhixu/zhixu.py` | | 3 |
| `class Nanobot` | `class Zhixu` | `nanobot/nanobot.py:68`、`__init__.py` `_LAZY_EXPORTS` | 3 |
| `_set_nanobot_logs` | `_set_zhixu_logs` | `cli/log_control.py:5,8` | 3 |
| `_nanobot_metadata()` / `_parse_nanobot_metadata()` | `_zhixu_metadata()` / `_parse_zhixu_metadata()` | `agent/skills.py:356,358`、`webui/skills_api.py:266` | 3 |
| `_isolate_nanobot_log_activation` | `_isolate_zhixu_log_activation` | `conftest.py:15` | 3 |
| `NanobotDingTalkHandler` | `ZhixuDingTalkHandler` | `channels/dingtalk/runtime.py:64` | 1 |
| `_set_process_title(f"nanobot-{role}")` | `f"zhixu-{role}"` | `cli/process_identity.py:29` | 3 |
| loguru 命名空间 `logger.enable("nanobot")` | `"zhixu"` | `cli/log_control.py:10,12`、`conftest.py:19` | 3 |
| `pkg_files("nanobot")` | `pkg_files("zhixu")` | `utils/helpers.py:905,977` | 3 |
| `_nanobot_webui_mutation_payload/_request/_caller_chats` | `_zhixu_webui_*` | `webui/ws_http.py:150-153` | 3 |
| `_nanobot_trusted_proxy_authenticated` | `_zhixu_trusted_proxy_authenticated` | `webui/ws_http.py:469,482,523` | 3 |

### 2.3 前端标识符

| 旧 | 新 | 位置 | 期 |
| --- | --- | --- | --- |
| `NanobotClient` | `ZhixuClient` | `webui/src/lib/nanobot-client.ts` | 2 |
| `useNanobotStream` | `useZhixuStream` | `webui/src/hooks/useNanobotStream.ts` | 2 |
| `getTerminalStore(client: NanobotClient)` | 参数类型同步 | `useNanobotStream.ts:1730,1732` | 2 |
| `NanobotFeatureInfo` / `NanobotFeaturesPayload` | `ZhixuFeatureInfo` / `ZhixuFeaturesPayload` | `lib/api.ts`、`channel-plugins/types.ts` | 2 |
| `fetchNanobotFeatures` / `enableNanobotFeature` / `disableNanobotFeature` | `fetchZhixuFeatures` / `enableZhixuFeature` / `disableZhixuFeature` | `lib/api.ts:618,657,670` | 2 |
| `nanobot_features_api.py` | `zhixu_features_api.py` | `nanobot/webui/` | 2 |
| `nanobot_features_payload` / `nanobot_feature_instance_target` / `nanobot_features_action` | `zhixu_features_*` | 同上 | 2 |
| `nanobot_features_action`（SettingsOperation 字段） | `zhixu_features_action` | `webui/settings_capabilities.py:61,732`、`settings_system.py:48,49` | 2 |
| 文件名 `nanobot-client.ts` / `useNanobotStream.ts` | `zhixu-client.ts` / `useZhixuStream.ts` | 及其 2 个测试文件 | 2 |
| npm 包 `nanobot-webui` | `zhixu-webui` | `webui/package.json`、`package-lock.json` | 2 |
| npm scope `@nanobot/tui` | `@zhixu/tui` | `tui/package.json`、`bun.lock` | 2 |
| Vite 插件名 `nanobot-guard-webui-entry-chunk` / `nanobot-gzip-webui-assets` | `zhixu-*` | `webui/vite.config.ts:19,49` | 2 |
| Vite HMR 路径 `/__nanobot_vite_hmr` | `/__zhixu_vite_hmr` | `webui/vite.config.ts:112` | 2 |

### 2.4 前端持久化与缓存标识符（**无兼容需求，见 §4.4**）

| 旧 | 新 | 位置 |
| --- | --- | --- |
| `nanobot-webui.theme` | `zhixu-webui.theme` | `webui/index.html:104` |
| `nanobot-webui.sidebar` | `zhixu-webui.sidebar` | `App.tsx:131` |
| `nanobot-webui.sidebar.session-updates.v1` | `zhixu-webui.sidebar.session-updates.v1` | `App.tsx:132` |
| `nanobot-webui.sidebar.completed-runs.v1` | `zhixu-webui.sidebar.completed-runs.v1` | `App.tsx:133` |
| `nanobot-webui.restartStartedAt` | `zhixu-webui.restartStartedAt` | `App.tsx:134` |
| `nanobot-webui.restartRoute` | `zhixu-webui.restartRoute` | `App.tsx:135` |
| `nanobot-webui.collapsed-pane-groups.v1` | `zhixu-webui.collapsed-pane-groups.v1` | `ChatList.tsx:86` |
| `nanobot.locale` | `zhixu.locale` | `i18n/config.ts:1`、`index.html:135` |
| `nanobot-static-`（SW 缓存前缀） | `zhixu-static-` | `webui/public/sw.js:1` |

### 2.5 协议标识符（**需兼容，见 §4.1 / §4.2**）

| 旧 | 新 | 位置 | 兼容策略 |
| --- | --- | --- | --- |
| `X-Nanobot-Auth` / `x-nanobot-auth` | `X-Zhixu-Auth` | `webui/http_utils.py`、`channels/websocket/runtime.py:184`、`tui/src/protocol.ts`、`webui/src/lib/bootstrap.ts` | 双读 |
| `x-nanobot-session-key` | `x-zhixu-session-key` | `webui/src/lib/session-drag.ts` | 双读 |
| `/api/settings/nanobot-features` | `/api/settings/zhixu-features` | `webui/ws_http.py:190-191` | **不兼容**，随 dist 重建一并生效 |
| `/api/settings/nanobot-features/enable\|/disable` | `/api/settings/zhixu-features/*` | 同上 | 同上 |
| WS payload key `nanobot_features` | `zhixu_features` | `settings_system.py:685,693`、`channels/weixin/webui/WeixinPanel.tsx:149` | 同上 |

### 2.6 磁盘 / 数据库标识符（**需兼容，见 §4.5 / §4.6**）

| 旧 | 新 | 位置 | 兼容策略 |
| --- | --- | --- | --- |
| `~/.nanobot`（数据目录根） | `~/.zhixu` | `config/loader.py:39` | 迁移 + 回退 |
| `~/.nanobot/workspace` | `~/.zhixu/workspace` | `config/paths.py:53,75,81,82` | 迁移 + 回退 |
| `~/.nanobot/history/cli_history` | `~/.zhixu/history/cli_history` | `config/paths.py:88` | 同上 |
| `~/.nanobot/sessions`（legacy） | `~/.zhixu/sessions` | `config/paths.py:93` | 同上 |
| `_nanobot_model_preset` | `_zhixu_model_preset` | `session/model_selection.py:9` | 惰性回退 |
| `_nanobot_reasoning_effort` | `_zhixu_reasoning_effort` | `session/model_selection.py:10` | 惰性回退 |
| `nanobot_workspace_scope` | `zhixu_workspace_scope` | `security/workspace_access.py:25` | 惰性回退 |
| `nanobot_llm_usage_source` | `zhixu_llm_usage_source` | `llm_usage/context.py:13` | 惰性回退 |
| `nanobot.sock` / `nanobot-tmux-sockets` / 会话 `nanobot-python` | `zhixu.*` | `skills/tmux/SKILL.md:14,16,17`、`find-sessions.sh` | 无需兼容（存量会话失联可接受） |

### 2.7 环境变量（**需兼容，见 §4.4**）

统一前缀 `NANOBOT_` → `ZHIXU_`。**注意** `_NANOBOT_COMPLETE`（`cli/entry.py:92`，typer 补全内部变量）**不改**——它是 typer 的约定名，改了会破坏 shell 补全。

| 变量 | 位置 |
| --- | --- |
| `NANOBOT_LLM_TIMEOUT_S` | `agent/loop.py`、`agent/runner.py`、`session/goal_state.py` |
| `NANOBOT_STREAM_IDLE_TIMEOUT_S` | `agent/loop.py`、`agent/runner.py`、`providers/base.py` |
| `NANOBOT_MAX_CONCURRENT_REQUESTS` | `agent/loop.py:570` |
| `NANOBOT_PATH_APPEND` / `NANOBOT_PATH_PREPEND` | `agent/tools/shell.py` |
| `NANOBOT_TUI_*`（16 个：ACCESS / API_TOKEN / API_URL / BIN / BOOTSTRAP_SECRET / BOOTSTRAP_URL / CHAT_ID / GATEWAY_STOP_COMMAND / HEALTH_URL / MODEL / MODEL_PRESET / NO_DOWNLOAD / THEME / VERSION / WORKSPACE / WS_URL） | `cli/tui_launcher.py`、`tui/src/index.ts`、`tui/scripts/*.py` |
| `NANOBOT_COPILOT_*` / `NANOBOT_GITHUB_*`（6 个） | `providers/github_copilot_provider.py` |
| `NANOBOT_OPENAI_COMPAT_TIMEOUT_S` | `providers/openai_compat_provider.py` |
| `NANOBOT_SANDBOX_ENFORCED` / `NANOBOT_WORKSPACE_SANDBOX_*` | `security/workspace_access.py` |
| `NANOBOT_RESTART_*`（4 个） | `utils/restart.py` |
| `NANOBOT_SKIP_WEBUI_BUILD` / `NANOBOT_FORCE_WEBUI_BUILD` | `hatch_build.py`、`webui/build.py` |
| `NANOBOT_API_URL` | `webui/vite.config.ts:111`、`webui/dev.py`、`webui/README.md` |
| `NANOBOT_EXTENSION_REGISTRY_URL` / `NANOBOT_EXTENSION_RAW_BASE` | `apps/cli/service.py` |
| `NANOBOT_TMUX_SOCKET_DIR` | `skills/tmux/` |
| `NANOBOT_CHANNELS` / `NANOBOT_EXTRAS` / `NANOBOT_WEB_TOKEN` | `Dockerfile`、`docker-compose.yml`、`render.yaml`、`render-config.json`（build-arg 与部署变量） |
| `NANOBOT_HOME` | `desktop/main.cjs:14` |
| `NANOBOT_BIN_DIR` / `NANOBOT_VENV` / `NANOBOT_SKIP_WIZARD` | `scripts/install.sh`、`scripts/install.ps1` |
| `_NANOBOT_COMPLETE` | **不改**（typer 约定） |

### 2.8 第三方服务标识符（**不改，见 §4.7**）

`X-OpenRouter-Title`、`X-Keenable-Title`、`x-grok-client-identifier`、`DEFAULT_ORIGINATOR`、`TOKEN_APP_NAME`、`_VOLCENGINE_TRAFFIC_TAG`、MCP `client_name`、`xai referrer` — **全部保留 `nanobot` 字面量**。

理由：这些是向外部服务自报客户端身份，改动会触发对方风控/配额策略变更，且**对用户零可见收益**。

### 2.9 品牌与文案

| 旧 | 新 | 位置 | 期 |
| --- | --- | --- | --- |
| 「我是 nanobot 🐈」等 5 套人格开场白 | 「我是知序 🐈」 | `templates/personas/*.md`、`templates/SOUL.md`、`templates/legacy/SOUL.md` | 1 |
| 「Nanobot's agent workspace is at」 | 「知序的 agent 工作区位于」 | `templates/agent/identity.md`、`subagent_system.md` | 1 |
| 「do not call `nanobot cron` via exec」 | 「do not call `zhixu cron` via exec」 | `templates/AGENTS.md:10`、`templates/agent/tool_contract.md:75` | 1 |
| `pip install "nanobot-ai[EXTRAS]"` | `pip install "zhixu-ai[EXTRAS]"` | `skills/update-setup/SKILL.md:88-90` | 1 |
| `Nanobot Reply`（钉钉标题） | `知序回复` | `channels/dingtalk/runtime.py:714` | 1 |
| `<at>Nanobot</at>`（Teams） | `<at>知序</at>` | `channels/msteams/tests/*` | 1 |
| `bot_name` 默认值 `"nanobot"` | `"知序"` | `config/schema.py:206`、`cli/stream.py:49,99` | 1 |
| Feishu 实例默认名 `"nanobot"` | `"知序"` | `channels/feishu/config.py:14`、`instances.py:61,79,216`、`connect.py:205` | 1 |
| `bot_name` 默认值（外部可见） | — | `api/server.py:307,452,480` `model_name`、`461` `owned_by` | 1（值改 `zhixu`） |
| `images/nanobot_*.png\|svg`（9 个） | `images/zhixu_*` | `images/` + README 引用 | 1 |
| `webui/public/brand/mira_*.png`（6 个） | `webui/public/brand/zhixu_*.png` | `Sidebar.tsx`、`manifest.json`、`index.html` | 1 |
| `docs/guides/deploy-nanobot-gateway.md` | `docs/guides/deploy-zhixu-gateway.md` | 文件名 + 内部链接 | 1 |
| i18n key `nanobotFeatures.*`（10 语言） | `zhixuFeatures.*` | `webui/src/i18n/locales/*/common.json` | 2（与 §2.3 类型名同批） |

## 3. 替换策略

**禁止**单条 `sed s/nanobot/zhixu/g`。按下列顺序、区分大小写替换：

1. `NANOBOT_` → `ZHIXU_`（排除 `_NANOBOT_COMPLETE`）
2. `NanoBot` → `Zhixu`（2 处，先处理，避免被 `Nanobot` 规则吞掉）
3. `Nanobot` → `Zhixu`
4. `nanobot` → `zhixu`
5. 上述 4 步之后，**逐条走 §4 的「不改清单」把不该改的改回来**

第 5 步不可省略。§4 列出的项会被步骤 1–4 误伤。

## 4. 兼容策略（逐项「改 / 不改 / 兼容」裁决）

### 4.1 HTTP 认证头 — **双读**

服务端同时接受 `X-Zhixu-Auth` 与 `X-Nanobot-Auth`，新头优先。`nanobot/webui/http_utils.py` 的 `issue_route_secret_matches` 与 `channels/websocket/runtime.py` 的 bootstrap 校验各加一次「旧头回退」，约 3 行。保留至少 1 个大版本。

### 4.2 会话拖拽头 `x-nanobot-session-key` — **双读**

同 4.1 处理，优先级低于认证头。

### 4.3 Docker 系统用户 `nanobot` — **不改**

保留 `useradd -m -u 1000 nanobot`、`ENV HOME=/home/nanobot`、`setpriv --reuid=nanobot`、`render.yaml` 的 `mountPath: /home/nanobot/.nanobot`。

理由：UID 1000 是与宿主 bind-mount 的契约，改 home 路径会让存量 volume 归属错位；且系统用户名对用户零可见。**只改** compose service / container 名（`nanobot-gateway` → `zhixu-gateway` 等）与 Dockerfile build-arg 名。

### 4.4 环境变量 — **单向归一，不改读取点**

仓内 131 处 `os.environ` / `os.getenv` 调用**无中心访问器**。逐点加 fallback 要改 131 处，不可行。

**采用启动期归一**：在 4 个进程入口（`cli/entry.py:main`、`nanobot.py:Zhixu.__init__`、`api/server.py:create_app`、`gateway/runtime.py`）调用同一个 `normalize_legacy_env()`：

```python
def normalize_legacy_env() -> list[str]:
    """把遗留 NANOBOT_* 映射到 ZHIXU_*；返回命中的旧变量名供调用方告警。"""
    hit: list[str] = []
    for legacy in LEGACY_ENV_NAMES:          # 契约 §2.7 的常量表
        new = "ZHIXU_" + legacy.removeprefix("NANOBOT_")
        if new not in os.environ and legacy in os.environ:
            os.environ[new] = os.environ[legacy]
            hit.append(legacy)
    return hit
```

要点：

- **读取点只改字符串字面量**（`os.environ.get("NANOBOT_X")` → `os.environ.get("ZHIXU_X")`），不加 fallback 分支。
- **写入方向单一**：只做「旧 → 新」。反方向（旧读点）不存在，因为所有读点都会在本轮被改成新名。
- 命中旧名时打一次 deprecation warning（`loguru.warning`），文案含变量名与替换建议。
- `_NANOBOT_COMPLETE` **不在** `LEGACY_ENV_NAMES` 中。
- `hatch_build.py` 与 `Dockerfile` 是**构建期**，不经过 Python 入口 → 这两处**必须保留对旧 build-arg 的接受**（`os.environ.get("ZHIXU_SKIP_WEBUI_BUILD") or os.environ.get("NANOBOT_SKIP_WEBUI_BUILD")`）。这是唯一需要 per-site fallback 的例外，计 2 处。

### 4.5 数据目录 — **迁移 + 回退**

新增 `zhixu/config/paths.py::resolve_data_root()`：

1. 若 `~/.zhixu` 已存在 → 用它。
2. 否则若 `~/.nanobot` 存在 → 尝试 `os.rename` 迁移到 `~/.zhixu`；成功则告警并继续；失败（跨设备 / 权限）则退化为 `shutil.copytree` + 在旧目录留 `.migrated-to-zhixu` 标记文件。
3. 两者都不存在 → 创建 `~/.zhixu`。

**回退保留期 ≥ 1 个大版本**：迁移函数额外保留旧目录读取路径，仅当新目录缺失时使用。`~/.nanobot/workspace`（`paths.py:53,75,81,82`）是硬编码点，必须走同一个 `resolve_data_root()`，不能各算各的。

### 4.6 SQLite 旧 metadata key — **惰性回退**

4 个 key 写新名、读时新名缺失则回退旧名。**不写迁移脚本**（惰性迁移，代价 O(1)，无需停机）。

```python
# session/model_selection.py 现有模式
SESSION_MODEL_PRESET_METADATA_KEY = "_zhixu_model_preset"
_LEGACY_SESSION_MODEL_PRESET_KEY = "_nanobot_model_preset"

def read_preset(session) -> str | None:
    return session.metadata.get(SESSION_MODEL_PRESET_METADATA_KEY) \
        or session.metadata.get(_LEGACY_SESSION_MODEL_PRESET_KEY)
```

**首次读到旧 key 时顺手回写新 key**，避免每次读都双查。

### 4.7 第三方服务标识符 — **不改**

见 §2.8。替换脚本执行完 §3 第 4 步后，必须把这批字面量**改回 `nanobot`**。建议做法：把这些字面量收进一个常量模块，后续改名只动一处；本轮**不做**该重构（避免夹带），只在替换脚本的排除清单里显式列出。

### 4.8 SKILL.md frontmatter `metadata.nanobot` — **改 + 双回退**

`agent/skills.py:345` 已有 `openclaw` 回退模式，照抄加一层：

```python
payload = (data_object.get("zhixu")
           or data_object.get("nanobot")
           or data_object.get("openclaw", {}))
```

第三方与外部 skill 的元数据继续可用。

## 5. 跨期边界（三期如何协作）

| 边界 | 约束 |
| --- | --- |
| Phase 1 → Phase 2 | Phase 1 只改**自然语言文案**，不改任何标识符。Phase 2 可安全地批量改标识符。 |
| Phase 2 → Phase 3 | Phase 2 改前端符号 + 协议路由 + 构建路径，**不改 Python 包目录**。此时前端 glob 仍指向 `nanobot/channels/*`，包目录改名由 Phase 3 一并处理。 |
| Phase 1 ↔ Phase 3 文件重叠 | `skills/update-setup/SKILL.md`（Phase 1 改文案，Phase 3 改 `NANOBOT_*` 变量名）、`skills/tmux/SKILL.md`（Phase 1 改 socket 名，Phase 3 改环境变量名）、`skills/clawhub/SKILL.md`（Phase 1 改 `~/.nanobot/workspace` 文案，Phase 3 改目录常量）。**Phase 3 执行时必须重读这些文件**，不得基于 Phase 1 的版本盲替换。 |

## 6. 验收断言（三期共用）

改完后执行，除各期自身的验收项外，**所有三期都必须通过**：

```bash
# 残留自检：输出应只剩 §4 的「不改 / 兼容回退 / 上游致谢 / 历史产物」四类
rg -i nanobot --glob '!.git/**' --glob '!.harness-worktrees/**' \
   --glob '!.venv/**' --glob '!**/node_modules/**' \
   --glob '!.ai-runtime-artifacts/**'
```

残留必须逐条归入下列之一，否则视为漏改：

- §4.3 Docker 系统用户
- §4.4 构建期 build-arg 的旧名回退
- §4.6 惰性回退的旧 SQLite key
- §4.7 第三方服务标识符
- §4.8 frontmatter 的 `nanobot` / `openclaw` 回退
- 上游致谢（`THIRD_PARTY_NOTICES.md`、`skills/README.md` 的 OpenClaw 来源、`SECURITY.md` 中的上游维护者邮箱）
- `.ai-runtime-artifacts/` 历史产物（明确不改）

## Next

- 契约与三期 spec 确认无误 → 说「写计划」或「制定实施计划」
- 需要调整映射表 → 直接说修改意见（改本表，三期 spec 随之同步）
