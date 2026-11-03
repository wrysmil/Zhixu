---
route: orchestration.handoff
artifact: handoff
date: 2026-11-01
topic: dontforget-extraction
plan: .ai-runtime-artifacts/plans/2026-11-01-dontforget-extraction-plan.md
target_repo: /Users/mima0000/Documents/学习-001/do-project/dontforget
source_repo: /Users/mima0000/Documents/学习-001/do-project/zhixu
status: 进行中（后端基本可导入，测试与前端未收口）
---

# Dont Forget 抽离项目 · 交接文档

## 一句话现状

新仓库 `dontforget` 已建好并完成包重命名，**Python 后端核心链路可正常导入**，记忆测试 954/955 通过；**前端裁剪只做了一半且未验证，测试收集仍有 21 个错误，Task 4d / 品牌改写 / 最终验证未开始**。当前所有改动都在工作区，未提交。

---

## 一、已确认的决策（不要再改）

| 决策 | 值 |
| --- | --- |
| 目标目录 | `/Users/mima0000/Documents/学习-001/do-project/dontforget` |
| Python 包 / CLI | `dontforget` |
| 配置与数据目录 | `~/.dontforget`（全新，不迁移 `~/.nanobot`） |
| 交付形态 | **桌面端**（Electron + gateway + WebUI），不要浏览器 `webui` 命令、不要 TUI |
| 保留能力 | 对话、Skill、Agent、MCP、记忆、Cron、身份 |
| 移除能力 | 团队、写作、评估、远程触发、CLI Apps、OpenAI HTTP API、15 个聊天通道 |
| 前端工具链 | **本机没有 bun，统一用 npm** |
| UI 方向 | 已确认 demo 方向（暖纸张 + 墨色 + 赭石强调），demo 路径见产物索引 |

---

## 二、已完成（有证据）

### 2.1 仓库与重命名

- 新仓库已 `git init -b main`，首个提交：`fe961d7 chore: scaffold dontforget from zhixu and rename package`（1413 个文件）。
- 包目录 `nanobot/` → `dontforget/`。
- `pyproject.toml`：`name`、`[project.scripts]`、`packages`、`include`、`artifacts`、`sdist`、`pytest.testpaths`（改为 `["tests"]`）、`coverage.source`、`basedpyright.include` 全部改名；删除 optional `api` extra。
- `uv lock` 已重建（`dontforget v0.3.0` 替代 `nanobot-ai`）。
- 配置目录与环境变量：`~/.nanobot` → `~/.dontforget`，`NANOBOT_*` → `DONTFORGET_*`。
- 裸字符串包名（`_pkg_version("nanobot-ai")`、`pkg_files("nanobot")`、`hatch_build` 路径、provider 标识头）已替换。

### 2.2 身份守卫（GREEN）

`tests/test_identity.py`：**5 passed**

- 包以 `dontforget` 导入
- 旧包名不可导入
- 源码无旧包 import
- 默认配置目录含 `.dontforget`
- `__version__` 可解析

### 2.3 已删除的模块与文件

- 整包：`evaluation/`、`writing/`、`teams/`、`triggers/`、`apps/`、`sdk/`、`api/`
- 通道：dingtalk / discord / email / feishu / matrix / mattermost / mochat / msteams / napcat / qq / signal / slack / telegram / wecom / weixin / whatsapp（保留 `websocket` 与基础设施）
- 技能：`article-*`（6 个）、`clawhub`、`my`、`update-setup`、`tmux`
- WebUI 后端：`teams_api.py`、`teams_routes.py`、`writing_api.py`、`writing_collab_api.py`、`writing_collab_routes.py`、`writing_routes.py`、`writing_services.py`、`cli_apps_api.py`
- 工具：`agent/tools/cli_apps.py`、`agent/tools/team.py`、`nanobot.py`（SDK 门面）
- 测试：teams / writing / evaluation / triggers / apps / cli_apps 对应文件，以及 `test_nanobot_facade.py`、`test_openai_api.py` 等

### 2.4 后端断点修复（已改到可导入）

- `agent/loop.py`：删除团队 Leader 装配、`team_context_provider`、`_leader_runtime`、revision 标记、`persist_message_binding`、`build_leader_registry`、`LocalTriggerTurnCoordinator`。
- `agent/context.py`：删除 `apps.cli.utils` 依赖。
- `command/builtin.py`：删除 `/trigger` 命令与注册。
- `cli/commands.py`：删除 `trigger`、`serve` 两个命令。
- `config/schema.py`：删除 `TeamsConfig`、`ApiConfig` 及对应字段。
- `cli/gateway_runtime.py`：删除 teams / writing / evaluation / local-triggers 四处接线。
- `webui/gateway_services.py`：删除团队/写作用模块导入与装配；`GatewayServices` 去掉团队/写作字段。
- `webui/inbound_commands.py`：删除团队暂存/领取/过期校验、写作上下文校验、写作会话绑定。
- `webui/settings_capabilities.py` / `settings_system.py` / `settings_routes.py`：删除 API 服务、团队域、写作域、CLI Apps 的设置与路由。
- `webui/mcp_presets_api.py`：MCP 预设保留，内联了 `app_manifest` / `compact_dict` 小工具，内置 MCP 清单置空。
- `channels/websocket/runtime.py`：删除团队运行快照补发。
- `session/automation_turns.py`：删除 local trigger 的自动化规格。

**导入冒烟（真实输出）：**

```
uv run python -c "import dontforget; import dontforget.agent.loop; import dontforget.cli.commands; import dontforget.cli.gateway_runtime; import dontforget.channels.manager; print('full import ok')"
→ full import ok
```

### 2.5 记忆回归

```
uv run pytest tests/memory -q
→ 1 failed, 954 passed in 8.42s
```

唯一失败：`tests/memory/vector/test_model_hub.py::test_sync_endpoint_writes_both_env_and_constants`。
原因：新 venv 未安装可选依赖 `huggingface_hub`，函数走 `except ImportError: return`。
**不是代码回归**；源仓库基线为 955 passed（其环境已装该依赖）。若要 955，执行 `uv sync --extra vector`（会拉 torch，体积大）。

### 2.6 UI Demo

`dontforget/design-demo/index.html`：10 个页面（New Chat / Chat / Memory / Identity / Agents / Skills / Automations / Workbench / Settings / Connect）+ ⌘K 命令面板 + 明暗主题。
用浏览器逐页验证：全部渲染、控制台 0 报错、命令面板 9 项。

---

## 三、未完成 / 已知破损（按优先级）

### P0 — 测试收集失败（21 个错误）

`uv run pytest tests --collect-only -q` → **21 errors**。已确认其中 13 个文件仍在 import 已删模块：

```
tests/agent/test_loop_save_turn.py
tests/agent/test_runner_injections.py
tests/agent/test_runtime_context.py
tests/agent/test_session_model_runtime.py
tests/agent/test_turn_agent_profile.py
tests/agent/test_workspace_scope.py
tests/cli/test_commands.py
tests/utils/test_media_decode.py
tests/webui/test_agents_api.py
tests/webui/test_mcp_presets_api.py
tests/webui/test_session_list_index.py
tests/webui/test_session_projection.py
tests/webui/test_skills_api.py
```

另有 settings / subagent / writing 相关测试需处理（`test_cli_apps_api.py`、`test_settings_*.py`、`test_subagent_spawn_e2e.py` 等）。
**做法：** 功能专属的直接删；agent 测试里只删团队/写作分支，保留其余断言。

### P0 — 源码仍有 2 个文件带旧模块引用

- `dontforget/webui/gateway_services.py`：**已无用**的 `build_writing_operations`、`build_writing_collab_operations`、`build_writing_collab_runner`、`seed_writing_team`、`build_team_stack`、`build_team_eval_runner` 函数仍在文件里（函数体内部懒 import 已删模块）。导入不受影响，但属死代码，应整段删除。
- `dontforget/channels/websocket/tests/test_websocket_http_routes.py`：仍 import `LocalTriggerStore`，并有两个 local-trigger 测试；应删该 import 与两个用例。

### P1 — 前端裁剪未完成、未验证

- 子 Agent 做了 96 个删除、62 个修改后撞到 150 轮上限中断。
- **未运行** `npm ci` / `npx tsc -p tsconfig.build.json --noEmit` / `npm run test`。
- `App.tsx`、`routes.ts`、10 个语言包的团队/写作/评估残留仍需逐处确认。
- `webui/package.json` 的 `lint` 脚本仍引用 `nanobot/channels/*/webui/**`。
- `webui/node_modules` 未迁移，需先 `npm ci`。

### P1 — Task 4d（会话结束记忆闭环）未开始

设计已定（见计划 Task 4d）：WS mutation `session.end` + `AgentLoop.end_session` + `session_end_factory` + bridge 透传 + 前端 `endSession`。
**注意：源仓库里这条链路本来就是断的**，`SessionEndOrchestrator.run()` 零调用方，不是搬迁造成的回归。

### P2 — 品牌改写未做

- `desktop/main.cjs` 仍有 `nanobot gateway` / `-m nanobot gateway`、`APP_NAME = "知序"`、`MIRA_GATEWAY_CMD`。
- `desktop/package.json` 仍是 `zhixu-desktop` / `productName: 知序` / `appId: ai.zhixu.desktop`。
- 全仓库品牌残留（实测）：`zhixu` 86 文件、`Zhixu` 82、`知序` 54、`mira` 13；`X-Zhixu-Auth` 头、`application/x-zhixu-session-key` 拖拽类型。
- `AGENTS.md` / `CLAUDE.md` 是从 zhixu 复制来的，引用被排除的 `harness-kit/`，**必须重写为精简版或删除**。

### P2 — 工程可用性

- venv 里没有 `ruff`（`uv run ruff` 报 Failed to spawn），需确认 dev 依赖组或 `uv sync --dev`。
- 未跑 `basedpyright`。
- 未跑 `uv run dontforget --help` / gateway 启动冒烟。
- 未落盘验证报告。

### 已知临时妥协（需在收口时清理）

| 位置 | 内容 | 处理 |
| --- | --- | --- |
| `webui/inbound_commands.py` | 本地 `normalize_cli_app_mentions()` 恒返回 `[]` | CLI Apps 已移除，保留空实现可接受；若要彻底清理，删掉 cli_apps 元数据链路 |
| `webui/mcp_presets_api.py` | 内联 `app_manifest` / `compact_dict` | MCP 预设 UI 仍需要该 manifest 形状，保留 |
| `webui/mcp_presets_api.py` | `_builtin_mcp_logical_ids()` 恒返回空集 | 内置 MCP 清单已随 writing 删除，保留空集 |
| `webui/inbound_commands.py` | `_validate_writing_context()` 已无调用方 | 应整段删除 |
| `webui/gateway_services.py` | 6 个 writing/team 构建函数已无调用方 | 应整段删除 |

---

## 四、下一步执行顺序（可直接照做）

1. **清测试（P0）**
   ```bash
   cd /Users/mima0000/Documents/学习-001/do-project/dontforget
   uv run pytest tests --collect-only -q   # 目标：0 errors
   ```
   处理 13 个已知文件 + settings/subagent/writing 相关文件；功能专属直接删，混合文件只删相关分支。

2. **删死代码（P0）**
   - `webui/gateway_services.py` 删 6 个无调用函数
   - `webui/inbound_commands.py` 删 `_validate_writing_context`
   - `channels/websocket/tests/test_websocket_http_routes.py` 删 trigger 相关

3. **提交后端成果**
   ```bash
   git add -A && git commit -m "refactor: drop team, writing, evaluation, api and extra channels"
   ```

4. **前端收口（P1）**
   ```bash
   cd webui && npm ci
   npx tsc -p tsconfig.build.json --noEmit
   npm run test
   ```
   失败逐项修；i18n 10 个语言包保持与 `en` 同 key 结构。

5. **Task 4d**：按计划实现 `session.end` 闭环（约 1～1.5 天）。

6. **品牌改写（P2）**：desktop → locales → 运行契约头 → README/AGENTS。

7. **验证（P2）**：
   ```bash
   uv run pytest tests/memory tests/session tests/config -q
   uv run --no-sync basedpyright
   uv run dontforget --version
   uv run dontforget gateway --background --config ~/.dontforget/config.json
   ```
   结果写入 `.ai-runtime-artifacts/verifications/2026-11-01-dontforget-extraction-verification.md`。

---

## 五、关键陷阱（已踩过，别重复）

1. **rsync 排除项必须根级锚定**：`--exclude='memory'` 会把 `nanobot/memory/` 整包排掉；必须写 `/memory`。
2. **BSD sed 不支持 `\b`**：重命名统一用 `perl -pi -e`。
3. **`git mv` 对未跟踪文件无效**：先 `mv` 再 `git add -A`。
4. **`uv.lock` 改名后必须重建**：否则 `uv sync` 卡在旧包名。
5. **裸字符串包名不在 `nanobot.` 替换范围内**：`_pkg_version`、`pkg_files`、`hatch_build` 路径都会漏。
6. **`pytest.ini_options.testpaths` 含已删目录会直接报错**：已改为 `["tests"]`。
7. **本机无 bun**：所有前端命令用 npm。
8. **`Config` 的默认 workspace 在 `cfg.agents.defaults.workspace`**，不是 `cfg.agents.workspace`。
9. **`config/loader.py` 与 `agent/tools/self.py` 的配置键 `"my"` 与已删的 `skills/my` 无关**。
10. **子 Agent 单任务过大容易撞 150 轮上限**：后续拆成「单个文件 / 单组测试」的小任务再派。

---

## 六、产物索引

| 产物 | 路径 |
| --- | --- |
| 实施计划（含 Task 4d 详细设计） | `.ai-runtime-artifacts/plans/2026-11-01-dontforget-extraction-plan.md` |
| 本交接文档 | `.ai-runtime-artifacts/execution-logs/2026-11-01-dontforget-extraction-handoff.md` |
| UI Demo（已确认方向） | `/Users/mima0000/Documents/学习-001/do-project/dontforget/design-demo/index.html` |
| 新仓库 | `/Users/mima0000/Documents/学习-001/do-project/dontforget` |
| 首个提交 | `fe961d7 chore: scaffold dontforget from zhixu and rename package` |

---

## 七、当前工作区状态

```
git log --oneline -1  → fe961d7 chore: scaffold dontforget from zhixu and rename package
git status --short    → 191 项未提交（120 删除 / 71 修改）
```

**接手第一件事：`git status` 核对，再按第四节 P0 → P1 → P2 顺序推进。**
