---
artifact: research-report
route: research (仓库范围调研，无 stage skill)
skills: []
skills_evidence:
  - skipped: web-investigator (本次为本地仓库静态扫描，无网页检索需求)
source:
  - 用户请求：统一改掉 Nanobot 这类名称（含包与提示词），先调研改动范围
  - harness-kit/core/routing.md
  - 既有决策 .ai-runtime-artifacts/decisions/2026-10-09-mira-rebrand-scope.md
  - 既有方案 .ai-runtime-artifacts/specs/2026-09-30-brand-rebrand-spec.md
created_at: 2026-10-19
tier: 0
---

# nanobot → 新名称 全仓改名改动范围调研

## 1. 调研目标

用户要求：项目此前叫 nanobot，现在要**统一改掉**这类名称，**包含包（Python 包 / npm 包）以及提示词（prompt 模板 / skill 文档 / 系统提示词）**，先做改动范围调研。

本次只调研、只出结论，**不改任何业务代码**。

## 2. 调研方法与口径

```bash
# 全仓大小写不敏感扫描（排除 .git / .harness-worktrees / .venv / node_modules / __pycache__ 等）
rg --hidden -i -c --glob '!.git/**' --glob '!.harness-worktrees/**' --glob '!.venv/**' \
   --glob '!**/node_modules/**' --glob '!**/__pycache__/**' -e nanobot .
```

排除项：`.git/`、`.harness-worktrees/`（工作树副本）、`.venv/`、`node_modules/`、`__pycache__/`、`.logo-cache/`、`.pytest_cache/`、`.ruff_cache/`。

## 3. 总量结论

| 指标 | 数值 |
| --- | --- |
| 命中**文件**数 | **1429** |
| 命中**行**数（`-c` 口径） | **14278** |
| 命中**出现次数**（`-o` 口径） | **15350** |

**大小写变体分布**（按出现次数）：

| 变体 | 次数 | 主要用途 |
| --- | --- | --- |
| `nanobot` | 14300 | 包名、import、路径、命令、数据目录 |
| `Nanobot` | 741 | 类名 `Nanobot`、HTTP 头 `X-Nanobot-Auth`、文档展示名 |
| `NANOBOT` | 307 | 环境变量 `NANOBOT_*`、容器构建参数 |
| `NanoBot` | 2 | 文档展示名 |

**按区域分布**（行数 / 文件数）：

| 区域 | 文件 | 行 | 备注 |
| --- | ---: | ---: | --- |
| `tests/`（Python） | 483 | 4567 | 绝大多数是 `from nanobot...` import |
| `nanobot/`（Python 包本体） | 418 | 3317 | 含包内 2672+2211 行 import |
| `.ai-runtime-artifacts/` | 230 | 3139 | **历史过程产物，建议不改** |
| `docs/` | 60 | 1563 | 64 个 md 中 60 个含旧名 |
| `webui/` | 122 | 801 | 含 `package-lock.json` |
| `tui/` | 30 | 279 | 含 `@nanobot/tui` scope |
| `scripts/` | 4 | 128 | 安装脚本 |
| 根级 md（README/AGENTS/SECURITY/…） | ~15 | ~290 | |
| `desktop/` | 2 | 14 | 命名不统一（见 §6） |
| `.github/` | 5 | 22 | CI + Issue 模板 |

**Python 细分**（909 文件 / 7934 行）：

| 类别 | 行 | 文件 |
| --- | ---: | ---: |
| `from/import nanobot…` 语句 | 4887 | 866 |
| 其余（prompt 字符串、CLI help、路径常量、环境变量、docstring） | ~3047 | ~430 |

**结论：约 74% 的 Python 命中是纯 import 语句**，可用脚本 + `git mv` 批量处理；真正需要人工判断的约 300–400 处。

## 4. 既有决策回顾（重要）

本仓已做过一次品牌重塑，**只改了用户可见层，没动引擎标识符**：

- `.ai-runtime-artifacts/decisions/2026-10-09-mira-rebrand-scope.md`
  「Mira 品牌重塑范围 = 用户可见层，**不改 Python 包名**」；结论末句写明「未来若做引擎改名，另立 spec；届时 localStorage/i18n key 一并迁移」。
- `.ai-runtime-artifacts/specs/2026-09-30-brand-rebrand-spec.md`
  §2 明确：**换** 品牌层/界面文案/包名(CLI)；**不换** Python 包目录、数据目录、环境变量。

**当前状态：视觉层已换成「知序 / Zhixu」，引擎层仍是 nanobot，两层割裂。**
本次用户诉求正是补上当年决策里刻意留下的「引擎改名」那半边。

证据：
- `webui/index.html:122` `<title>知序</title>`、`webui/public/brand/zhixu-logo.png`
- `desktop/package.json` `"name": "zhixu-desktop"`、`"productName": "知序"`、`"appId": "ai.zhixu.desktop"`
- 仓库目录名 `Zhixu`、远端 `wrysmil/Zhixu.git`
- 但 README 仍是 nanobot.wiki / HKUDS/nanobot / pypi nanobot-ai

## 5. 分层改动清单（按兼容风险排序）

### R1 — 破坏兼容（改了会打断现有部署 / 数据 / 第三方客户端）

| # | 标识符 | 位置 | 影响 |
| --- | --- | --- | --- |
| 1 | `~/.nanobot` 数据目录 | `nanobot/config/paths.py:53,75,81,82,88,93`、`loader.py:39`、`Dockerfile`、`entrypoint.sh:2`、`desktop/main.cjs:14` | 历史会话 / 配置 / 凭据 / media 全在此目录；改名 = 要迁移脚本或双读 |
| 2 | `NANOBOT_*` 环境变量（~50 个名字） | `cli/tui_launcher.py`(17)、`providers/*`、`security/workspace_access.py`、`utils/restart.py`、`webui/build.py`、`apps/cli/service.py`、`skills/tmux/` | 用户已有环境配置失效 |
| 3 | `X-Nanobot-Auth` / `x-nanobot-auth` HTTP 头 | `nanobot/webui/http_utils.py`、`channels/websocket/runtime.py:184`、`tui/src/protocol.ts`、`webui/src/lib/bootstrap.ts` | WebUI↔gateway、TUI↔gateway 认证握手 |
| 4 | `x-nanobot-session-key` 头 | `webui/src/lib/session-drag.ts:…` | 拖拽会话 |
| 5 | localStorage key（6 个） | `App.tsx:131-135`、`ChatList.tsx:86`、`i18n/config.ts:1`、`index.html:104,135` → `nanobot-webui.*`、`nanobot.locale` | 用户主题/语言/侧栏状态丢失 |
| 6 | SW 缓存前缀 `nanobot-static-` | `webui/public/sw.js:1` | 旧缓存不失效 |
| 7 | 会话持久化 metadata key | `session/model_selection.py:9-10`（`_nanobot_model_preset` / `_nanobot_reasoning_effort`）、`security/workspace_access.py:25`（`nanobot_workspace_scope`）、`llm_usage/context.py:13`（`nanobot_llm_usage_source`） | **写入 SQLite 的 key**，改名后旧会话读不到设置，需迁移或双读 |
| 8 | HTTP 路由 + WS payload key | `webui/ws_http.py:190-191`、`settings_system.py:685`、`webui/nanobot_features_api.py`、`channels/weixin/webui/WeixinPanel.tsx:149` → `/api/settings/nanobot-features[/enable\|/disable]`、`nanobot_features` | 前后端必须同步改；旧 WebUI 前端连新后端会 404 |
| 9 | SKILL.md frontmatter `metadata.nanobot` | `agent/skills.py:345`、`webui/skills_api.py:260`（已带 `openclaw` 回退） | 第三方/外部 skill 兼容性 |
| 10 | 第三方服务客户端标识 | `X-OpenRouter-Title`、`X-Keenable-Title`（`agent/tools/web.py:648`）、`x-grok-client-identifier`（`xai_grok_provider.py`）、`DEFAULT_ORIGINATOR`（`openai_codex_provider.py:51`）、`TOKEN_APP_NAME`（`github_copilot_provider.py:36`）、`_VOLCENGINE_TRAFFIC_TAG`（`web.py:38`）、MCP `client_name="nanobot"`、xai OAuth `referrer` | 服务端风控 / 配额归属 |
| 11 | Docker 系统用户 + 路径 | `Dockerfile`（`useradd nanobot`、`/home/nanobot`）、`entrypoint.sh`（`setpriv --reuid=nanobot`）、`docker-compose.yml:8`、`render.yaml:47`（mountPath） | **改用户会影响已挂载 volume 的 UID 1000 权限** |
| 12 | compose service / 容器名 | `docker-compose.yml:22,39,56`、`ci.yml:391,419` → `nanobot-gateway` / `nanobot-api` / `nanobot-cli` | 运维脚本 / CI 引用 |
| 13 | 服务管理器默认服务名 | `cli/gateway.py:357,389` → `--name` 默认 `nanobot-gateway` | 用户已注册的 systemd/launchd 服务 |
| 14 | OpenAI 兼容 API 默认值 | `api/server.py:307,452,461,480` → `model_name="nanobot"`、`owned_by="nanobot"` | 客户端可见 |
| 15 | PyPI 发行名 | `pyproject.toml:2` `nanobot-ai`、`nanobot/__init__.py:49` `_pkg_version("nanobot-ai")`、`uv.lock:2580` | 装包名变更 |
| 16 | tmux 标识 | `skills/tmux/SKILL.md:14,16,17,35`、`find-sessions.sh` → `nanobot-tmux-sockets` / `nanobot.sock` / 会话 `nanobot-python` | 存量 tmux 会话失联 |
| 17 | 进程名 | `cli/process_identity.py:29` → `setproctitle(f"nanobot-{role}")` | 运维 pgrep 脚本 |

### R2 — 结构性（改目录/符号需同步大量文件，但无外部兼容负担）

| # | 标识符 | 位置 | 说明 |
| --- | --- | --- | --- |
| 18 | **Python 包目录 `nanobot/`** | 仓库根 | `git mv nanobot/ zhixu/` + 4887 行 import 重写（866 文件） |
| 19 | SDK 门面 | `nanobot/nanobot.py:68 class Nanobot`、`__init__.py` 的 `_LAZY_EXPORTS` | 公开 API 符号 `Nanobot` |
| 20 | 构建产物目录 | `webui/vite.config.ts:131` `outDir: ../nanobot/web/dist`、`hatch_build.py` `dist_dir`、`pyproject.toml` `artifacts`、`Dockerfile` COPY、`.dockerignore:8` | **五处必须同步**，漏一处 wheel 里就没 WebUI |
| 21 | 渠道前端 glob | `webui/src/channel-plugins/registry.ts:11,25`（正则 `nanobot\/channels\/([^/]+)\/`）、`locale-registry.ts:15,26`、`tsconfig.json:37-38`、`eslint.config.js:16`、`tailwind.config.js:10`、`package.json:13` | 正则硬编码路径 |
| 22 | entry-point group | `agent/tools/loader.py:74` → `entry_points(group="nanobot.tools")`；`pyproject.toml:121` 注释 | 第三方工具插件注册 |
| 23 | loguru logger 命名空间 | `cli/log_control.py:10,12` → `logger.enable("nanobot")`；`conftest.py:15,19` | 与包名绑定 |
| 24 | 模板资源解析 | `utils/helpers.py:905,977` → `pkg_files("nanobot") / "templates"` | |
| 25 | WebUI 源码符号 | `lib/nanobot-client.ts`（`NanobotClient`）、`hooks/useNanobotStream.ts`、`lib/api.ts`（`fetchNanobotFeatures` / `NanobotFeatureInfo` / `NanobotFeaturesPayload`）、`webui/nanobot_features_api.py` | 5 个文件名 + 大量类型名 |
| 26 | npm 包名 | `webui/package.json:2` `nanobot-webui` + `package-lock.json`；`tui/package.json:2` `@nanobot/tui` + `bun.lock` | 私有包，影响小 |
| 27 | CLI 命令 | `pyproject.toml:117` `nanobot = "nanobot.cli.entry:main"`、`cli/commands.py:87` `name="nanobot"`、`cli/entry.py:101` `prog_name`、`command/builtin.py:263` `[sys.executable, "-m", "nanobot"]` | 需决定是否留 alias |
| 28 | 测试与工具配置路径 | `pyproject.toml:179,186,189`（basedpyright include / pytest testpaths / coverage source）、`ci.yml:90,95,196,207` | |
| 29 | Vite HMR 路径 | `webui/vite.config.ts:112` `/__nanobot_vite_hmr` | 仅 dev |
| 30 | `monkeypatch` 目标字符串 | `conftest.py:38,42,53`（`"nanobot.session.manager..."`）等大量测试 | 随 import 一并改 |

### R3 — 用户可见文案 / **提示词**（低风险，可纯机械替换）

**3.1 系统提示词模板**（`nanobot/templates/`，Jinja2 markdown，由 `utils/prompt_templates.py` 加载）：

| 文件 | 命中 | 内容 |
| --- | ---: | --- |
| `templates/SOUL.md:3` | 1 | 「我是 nanobot 🐈，跑在用户自己设备上的个人 AI 助手。」 |
| `templates/legacy/SOUL.md:3` | 1 | 「I am nanobot 🐈, a personal AI assistant.」 |
| `templates/USER.md:3` | 1 | 「帮助 nanobot 了解你…」 |
| `templates/HEARTBEAT.md:4` | 1 | 「checked periodically by your nanobot agent…`nanobot gateway` starts…」 |
| `templates/AGENTS.md:10,18` | 2 | 「do not call `nanobot cron` via exec」「`nanobot gateway` registers…」 |
| `templates/agent/identity.md:6` | 1 | 「Nanobot's agent workspace is at: {{ agent_workspace_path }}」 |
| `templates/agent/subagent_system.md:10` | 1 | 「Nanobot's agent workspace: {{ agent_workspace }}」 |
| `templates/agent/tool_contract.md:75` | 1 | 「do not run `nanobot cron` through `exec`」 |
| `templates/memory/MEMORY.md:23` | 1 | 「automatically updated by nanobot…」 |
| `templates/personas/*.md`（balanced/companion/creative/mentor/tech_expert） | 各 1 | 5 套人格开场白「我是 nanobot 🐈」 |

未命中但同属提示词面：`templates/AGENT.md`、`templates/POLICIES.yaml`、`templates/agent/*.md` 其余 12 个（无 nanobot 字面量）。

**3.2 Skills**（`nanobot/skills/`，共 16 个 skill）：

| 文件 | 命中 | 性质 |
| --- | ---: | --- |
| `skills/update-setup/SKILL.md` | 17 | **最高**：prompt 里写死 `nanobot --version`、`pip show nanobot-ai`、`pip install "nanobot-ai[EXTRAS]"`、描述「Upgrade nanobot」 |
| `skills/tmux/SKILL.md` | 7 | socket/session 名 + 环境变量 |
| `skills/image-generation/SKILL.md` | 4 | 「this nanobot instance」「nanobot's media directory」、示例 prompt 里带 `nanobot` |
| `skills/skill-creator/SKILL.md` | 2 | 「For `nanobot`, custom skills should live under…」 |
| `skills/clawhub/SKILL.md` | 5 | `~/.nanobot/workspace` |
| `skills/github|weather|summarize|cron/SKILL.md` | 各 1 | frontmatter `metadata: {"nanobot": {...}}` |
| `skills/README.md` | 3 | |

**3.3 CLI 提示串**：`cli/commands.py`(70)、`cli/gateway.py`(~20)、`cli/agent.py`(26)、`command/builtin.py`(33)、`cli/webui.py:112`、`cli/webui_support.py:571`、`cli/stream.py:49,99`（`bot_name` 默认）、`config/schema.py:206`（`bot_name: str = "nanobot"`）。

**3.4 渠道回复文案**：`channels/dingtalk/runtime.py:714`「Nanobot Reply」、`channels/msteams` 测试 `<at>Nanobot</at>`、`channels/feishu/config.py:14` + `instances.py:61,79,216` 默认实例名 `"nanobot"`、`channels/email` 测试 `Nanobot <bot@example.com>`。

**3.5 WebUI 文案**：`i18n/locales/en/common.json:302` gatewayHint、`components/FilePreviewPanel.tsx:93`、`lib/api.ts:132`、`hooks/useNanobotStream.ts:328`（`new Notification("nanobot", …)`）。
i18n **key** `nanobotFeatures.*`（10 语言各 1 处）属 key 名，非文案。

**3.6 品牌资产文件名**：`images/` 下 9 个文件（`nanobot_arch/logo/mark/webui/apps/automations/context/workbench`）、`docs/guides/deploy-nanobot-gateway.md`。
（视觉资产本体已在 `webui/public/brand/mira_*` + `zhixu-logo.png`，无需再生成。）

### R4 — 文档 / CI / 历史产物

| 区域 | 规模 | 建议 |
| --- | --- | --- |
| `docs/` 64 md | 1563 行 | 批量替换 + 文件名 `deploy-nanobot-gateway.md` 重命名 |
| `README.md` | 86 行 | 含 `nanobot.wiki` 文档站链接、`HKUDS/nanobot` 上游、`pypi nanobot-ai` 徽章 |
| `SECURITY.md`(25) / `CONTRIBUTING.md`(9) / `THIRD_PARTY_NOTICES.md`(4) / `AGENTS.md`(37) | | |
| `.agent/gotchas.md`(3) `.agent/design.md`(1) `harness-kit/`(26) `.claude/settings.json`(26) | | **`.claude/settings.json` 有 30+ 条 Bash allowlist 内嵌 `nanobot` 命令**，改名后全部失配，需整体重写 |
| `.github/workflows/ci.yml`(13)、`tui-release.yml`(2, 产物名 `nanobot-tui-*`)、`ISSUE_TEMPLATE/*` | 22 行 | |
| `.ai-runtime-artifacts/` | 230 文件 / 3139 行 | **建议不改**：历史过程产物，改了会破坏与已归档 spec/plan 的引用一致性 |

## 6. 顺带发现的命名不一致（本次可一并修）

1. **`desktop/main.cjs`**：已混用两套前缀 —— `MIRA_SMOKE` / `MIRA_WEBUI_URL` / `MIRA_GATEWAY_TIMEOUT_MS` / `MIRA_GATEWAY_CMD` vs `NANOBOT_HOME`（第 14 行）。
2. **`webui/public/brand/`**：资产文件名前缀是 `mira_*`，另有 `zhixu-logo.png`，README 用的是 `readme-cover-*.svg`。三套命名。
3. **i18n value** 已是「知序」，但 `webui/package.json` 名仍是 `nanobot-webui`、`tui/package.json` 是 `@nanobot/tui`。
4. `webui/src/i18n/locales/*/common.json` 中 `app.brand = "知序"`（10 语言统一中文，未做本地化）。

> 这说明「定名」是本次的前置决策：到底用 **Mira**、**Zhixu/知序**、还是别的名字，会直接决定上面 30+ 处资产/包名的目标值。

## 7. 改动量估算与分批建议

| 批次 | 内容 | 涉及文件 | 风险 | 预估 |
| --- | --- | ---: | --- | --- |
| **A. 定名与决策** | 新名字确定、兼容策略、批次边界，写 spec | 1（spec 产物） | 无 | 先做 |
| **B. 提示词 + 文案** | R3 全部：templates(11)、skills(9)、CLI 提示串、渠道回复名、WebUI 文案 | ~120 | 低（纯替换） | 小 |
| **C. 前端符号与 npm 包名** | R2 的 25/26：`NanobotClient`、`useNanobotStream`、`nanobot_features_api.py`、npm 包名、localStorage、SW、路由 | ~150 | **中**（前后端 + 兼容旧构建产物需同步） | 中 |
| **D. Python 包目录** | R2 的 18/19/22/23/24/27/28：`git mv nanobot/ → zhixu/` + 4887 行 import + SDK 类名 + 构建配置 | ~900 | **高**（`venv` 需重建、5 处构建路径同步） | 大 |
| **E. 兼容层与迁移** | R1 全部：`~/.nanobot`→新目录迁移、`NANOBOT_*` 别名、HTTP 头双读、SQLite 旧 key 迁移、Docker 用户、PyPI 发行名 | ~60 | **高**（涉及真实用户数据） | 大 |
| **F. 文档 / CI / 提示信息** | R4 | ~140 | 低 | 中 |

**依赖顺序**：`A → B → C → D → E → F`。
C 必须在 D 之前（前端 glob 正则 `nanobot/channels/*` 依赖包目录名），E 必须在 D 之后。

## 8. 高风险检查清单（执行时必须逐条验证）

1. **venv 重建**：当前 `.venv/Scripts/nanobot.exe` + `nanobot_ai-0.3.0.dist-info` + `_editable_impl_nanobot_ai.pth` 为 editable 安装，改包名后**必须重建**，否则 `import zhixu` 失败。
2. **构建五连**：`webui/vite.config.ts` outDir、`hatch_build.py` dist_dir、`pyproject.toml` artifacts/include、`Dockerfile` COPY、`.dockerignore` —— 任一漏改，wheel 缺 WebUI 或 Docker 构建失败。
3. **渠道前端正则**：`webui/src/channel-plugins/registry.ts:25` 的 `/nanobot\/channels\/([^/]+)\//` 是运行时字符串匹配，改包目录后必须同步，否则**渠道面板静默消失**（不报错，最难发现）。
4. **`conftest.py` monkeypatch 字符串**：`"nanobot.session.manager.get_runtime_subdir"` 等按名解析，漏改会让全部测试写进真实 home 目录。
5. **基于包名的工具配置**：`basedpyright include`、`pytest testpaths`、`coverage source`、`ruff check nanobot`。
6. **写入 DB 的旧 key**：`_nanobot_model_preset` / `_nanobot_reasoning_effort` / `nanobot_workspace_scope` / `nanobot_llm_usage_source` —— 改 key 名必须配套 SQLite 数据迁移，否则老会话丢设置。
7. **前后端协议**：`X-Nanobot-Auth` + `/api/settings/nanobot-features` + `nanobot_features` WS key —— 桌面端内置的是**已构建的 webui dist**，改了后端但没重建前端会直接登录失败。
8. **Docker UID 1000**：改系统用户会让已挂载的 `~/.nanobot` volume 归属错位，`entrypoint.sh` 的 `chown`/`setpriv` 逻辑需同步。
9. **`.claude/settings.json`**：30+ 条 Bash 允许规则内嵌 `nanobot` 命令，改名后需整体重写，否则后续 AI 会话大量命令被拒。
10. **残留自检**：改名后跑 `rg -i nanobot` 应只剩「兼容别名 / 迁移代码 / 历史产物 / 上游致谢」四类，逐条人工确认。

## 9. 未决问题与风险评级

**已确认（2026-10-19）：新名字 = `zhixu`。** PyPI 占用核查：`zhixu` 与 `zhixu-ai` 均返回 404（未被占用，可注册）。

### 9.1 风险分级标准

| 等级 | 判据 |
| --- | --- |
| **高** | 改错会让**真实数据或既有部署静默失效**，且用户不易察觉；必须有迁移或回退机制 |
| **中** | 改错会导致**功能不可用或需重建产物**，但可发现、可回滚 |
| **低** | 纯命名替换，坏了立刻报错，改回去即可 |

### 9.2 决策表

| # | 决策项 | 改成 | 风险 | 坏了会怎样 | 推荐 |
| --- | --- | --- | :---: | --- | --- |
| 1 | **数据目录** | `~/.nanobot` → `~/.zhixu` | 🔴 **高** | 历史会话 / 配置 / 凭据 / media 全在旧目录，老实例启动后是**空环境**（看着像数据丢了）。尤重：`paths.py:53,75,81` 的 `~/.nanobot/workspace` 是**硬编码**，不随 `--config` 走，工作区（身份 / 记忆 / 人设 / 写作数据）都在里面 | **改 + 自动迁移**：启动检测旧目录 → rename，失败回退 copy；旧目录读取保留 ≥1 个大版本 |
| 2 | **环境变量前缀** | `NANOBOT_*` → `ZHIXU_*`（~50 个） | 🔴 **高** | 用户 shell / docker-compose / CI secret 里的旧变量**静默失效**，表现为「配置读不到」而非报错，最难排查 | **改 + 兼容别名**：读取时 `os.getenv(NEW) or os.getenv(OLD)`，命中旧名打 deprecation warning |
| 3 | **Docker 系统用户** | `nanobot` → `zhixu` | 🔴 **高** | 已有部署 volume 归属错位：`entrypoint.sh` 的 `chown`/`setpriv --reuid=nanobot`、Render blueprint `mountPath: /home/nanobot/.nanobot` 全要改；老 volume 挂在旧 home，新容器找不到 | **不改**。保留系统用户 `nanobot`，只改对外可见的 service / container 名。风险收益比最差 |
| 4 | **SQLite 旧 metadata key** | `_nanobot_model_preset` / `_nanobot_reasoning_effort` / `nanobot_workspace_scope` / `nanobot_llm_usage_source` → 新 key | 🟠 **中** | 老会话的模型预设 / 推理强度 / 工作区范围失效，表现为「设置被重置」，**不报错** | **惰性兼容**：写入用新 key，读取时新 key 缺失则回退旧 key。无需迁移脚本 |
| 5 | **HTTP 认证头** | `X-Nanobot-Auth` → `X-Zhixu-Auth` | 🟠 **中** | 前后端同版本无碍；但**旧 webui dist + 新 gateway**（浏览器缓存、桌面端内置 dist）会登录失败 | **改 + 服务端同时接受两个头**（约 3 行）；或干脆保留原头名不动 |
| 6 | **HTTP 路由 / WS payload** | `/api/settings/nanobot-features[/enable\|/disable]`、`nanobot_features` | 🟠 **中** | 前后端同仓发布即可；但**桌面端内置的是已构建 dist**，改了后端不重建前端就 404 | **改**，但把「重建 webui dist」列为强制验收项 |
| 7 | **SKILL.md frontmatter** | `metadata.nanobot` → `metadata.zhixu` | 🟡 **低-中** | 第三方 / 外部 skill 的元数据不被识别 | **改 + 保留 `nanobot` 与 `openclaw` 双回退**，照抄 `agent/skills.py:345` 现有回退模式 |
| 8 | **`.claude/settings.json`** | 30+ 条 Bash allowlist 内嵌 `nanobot` 命令 | 🟡 **低**（但影响后续所有 AI 会话） | 命令被大量拒绝 | **改**，一次性重写，成本极低 |
| 9 | **旧 CLI 命令 alias** | 保留 `nanobot` 指向新实现 | 🟢 **低** | 老用户的命令 / 脚本 / 文档失效 | **保留**，`[project.scripts]` 多注册一行即可 |
| 10 | **PyPI 发行名** | `nanobot-ai` → `zhixu-ai` | 🟢 **低** | 已装 `nanobot-ai` 的环境不会自动升级（可另发一个只改名的占位包） | **改 + 发占位包**，两个名字都已确认未被占用 |
| 11 | **localStorage / SW 缓存** | `nanobot-webui.*`、`nanobot.locale`、`nanobot-static-` | 🟢 **低** | 老用户主题 / 语言 / 侧栏折叠状态重置一次 | **改**（可接受一次重置；若想无感可加双读） |
| 12 | **npm 包名 / scope** | `nanobot-webui` → `zhixu-webui`；`@nanobot/tui` → `@zhixu/tui` | 🟢 **低** | 私有包，无外部依赖 | **硬切**，同步 `package-lock.json` / `bun.lock` |
| 13 | **compose service / 容器名** | `nanobot-gateway/-api/-cli` → `zhixu-*` | 🟡 **低-中** | 用户运维脚本与 `ci.yml:391,419` 需同步 | **改**，CI 同步更新 |
| 14 | **Docker build 参数** | `NANOBOT_CHANNELS` / `NANOBOT_EXTRAS` / `NANOBOT_SKIP_WEBUI_BUILD` | 🟡 **低-中** | 已有部署的 `docker build --build-arg` 失效 | **改 + 接受旧名**（build arg 读取处一行 `or`） |
| 15 | **品牌资产文件名** | `images/nanobot_*`、`webui/public/brand/mira_*` → `zhixu_*` | 🟢 **低** | 仅文件名，引用处同步即可 | **改**，顺带消除 `mira_*` / `zhixu-*` / `readme-cover-*` 三套命名 |
| 16 | **`.ai-runtime-artifacts/` 历史产物** | 230 文件 / 3139 行 | 🟢 **低**（不改） | 改名会破坏与已归档 spec / plan 的引用一致性 | **不改** |

### 9.3 已核实的关键事实（支撑上面的评级）

- **数据目录 = 配置文件所在目录**：`config/paths.py:20` `get_data_dir()` → `get_config_path().parent`；`loader.py:35-39` 默认 `~/.nanobot/config.json`。
  → 意味着 `--config <path>` 已能整体搬迁数据目录，迁移机制有现成抓手。
- **`~/.nanobot/workspace` 是硬编码**（`paths.py:53,75,81-82`），不随 `--config` 走，是 #1 风险最高的具体点位。
- **`NANOBOT_*` 是静默失效型**：`loader.py:68` 只在 JSON 解析失败时报 `'Check that complex NANOBOT_* values use valid JSON.'`，变量名本身拼错不会报错 → 支撑 #2 的高评级。
- **Docker home 与 volume 强耦合**：`Dockerfile` `useradd -m -u 1000 nanobot` + `mkdir -p /home/nanobot/.nanobot` + `ENV HOME=/home/nanobot`；`render.yaml:47` `mountPath: /home/nanobot/.nanobot` → 支撑 #3 的高评级。
- **`X-Nanobot-Auth` 是对外契约**：`docs/websocket.md:267` 明确写为客户端鉴权方式 → 支撑 #5 的中评级。

## 10. 结论

- 改动面**广但可控**：1429 文件 / 14278 行，其中 ~74% 的 Python 命中是机械 import，可脚本化。
- 真正的工作量集中在 **4 个耦合点**：构建产物五连、渠道前端正则、`conftest` monkeypatch 字符串、写入 DB 的旧 metadata key。
- 兼容性风险集中在 **R1 的 17 项**，其中 `~/.nanobot` 数据目录、`NANOBOT_*`、`X-Nanobot-Auth` 三项建议**保留旧值做别名**，其余可硬切。
- 本次是「补做 2026-09-30 spec 与 2026-10-09 decision 刻意留下的引擎改名」，属于 Tier 2 编排级任务，应先出 spec 再拆 WU。

## Next

- 回答 §9 未决问题 → 说「**写方案**」，我按 `brainstorming` 出 `.ai-runtime-artifacts/specs/2026-10-19-nanobot-rename-spec.md`
- 想先看具体某一块的明细（如 R1 迁移方案） → 说明要深挖的方向
- 仅需存档调研结论 → 无需继续
