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

# Phase 1 方案：文案与提示词（🟢 低风险）

## 0. 定位

三期中的第一期，**零兼容负担**：只改自然语言与文件名，不碰任何「程序会读到的标识符」。失败模式是「文案读起来别扭」，不是「功能坏掉」。

**本期的产物价值**：提示词是人机交互界面，改完用户立刻能感知；同时它把 25 个提示词/skill 文档里的品牌口径一次性对齐，为后两期清场。

## 1. 本期边界规则（最重要的一条）

> **只改「描述性文字」。凡是「程序会读到的字符串」一律留到对应期。**

这条规则解决一个真实陷阱：文案里大量内嵌**尚未改名的命令与变量**（如「Restart `nanobot` gateway」「use `NANOBOT_WEB_TOKEN`」）。如果 Phase 1 一把改成 `zhixu`，用户会照着提示执行一个**还不存在**的命令。

| 类别 | 例子 | 归属期 |
| --- | --- | ---: |
| 描述性文字 | 「我是 nanobot 🐈，跑在用户自己设备上的个人 AI 助手。」 | **1** |
| 命令字面量 | 「do not call `nanobot cron` via exec」 | 3 |
| 环境变量 | `` `${NANOBOT_WEB_TOKEN}` `` | 3 |
| 包名 / 路径 | `nanobot-ai`、`nanobot/channels/…`、`~/.nanobot` | 3 |
| HTTP 头 / 路由 | `X-Nanobot-Auth`、`/api/settings/nanobot-features` | 2 |
| 上游归属 | `HKUDS/nanobot`、`nanobot.wiki` | 3 |

## 2. 改动清单

### 2.1 系统提示词模板（15 文件，`nanobot/templates/`）

Jinja2 markdown，由 `nanobot/utils/prompt_templates.py` 加载，**直接进入 LLM 上下文**。

| 文件 | 现状 | 改为 |
| --- | --- | --- |
| `templates/SOUL.md:3` | 我是 nanobot 🐈，跑在用户自己设备上的个人 AI 助手。 | 我是知序 🐈，跑在用户自己设备上的个人 AI 助手。 |
| `templates/legacy/SOUL.md:3` | I am nanobot 🐈, a personal AI assistant. | I am Zhixu 🐈, a personal AI assistant. |
| `templates/USER.md:3` | 帮助 nanobot 了解你，以便用你舒服的方式协作。 | 帮助知序了解你，以便用你舒服的方式协作。 |
| `templates/HEARTBEAT.md:4` | 前后两个 `nanobot`：一个指 agent、一个指 `nanobot gateway` 命令 | agent → 知序；**命令字面量留到 Phase 3** |
| `templates/AGENTS.md:10,18` | `nanobot cron` / `nanobot gateway` | **纯命令，本期不动**（Phase 3） |
| `templates/agent/identity.md:6` | `Nanobot's agent workspace is at: {{ agent_workspace_path }}` | `知序的 agent 工作区位于：{{ agent_workspace_path }}` |
| `templates/agent/subagent_system.md:10` | `Nanobot's agent workspace: {{ agent_workspace }}` | `知序的 agent 工作区：{{ agent_workspace }}` |
| `templates/agent/tool_contract.md:75` | `nanobot cron` | **纯命令，本期不动** |
| `templates/memory/MEMORY.md:23` | automatically updated by nanobot | automatically updated by 知序 |
| `templates/personas/balanced.md:3` | 我是 nanobot 🐈 | 我是知序 🐈 |
| `templates/personas/companion.md:3` | 我是 nanobot 🐈，用户的日常伙伴 | 我是知序 🐈，用户的日常伙伴 |
| `templates/personas/creative.md:3` | 我是 nanobot 🐈，用户的创作搭档 | 我是知序 🐈，用户的创作搭档 |
| `templates/personas/mentor.md:3` | 我是 nanobot 🐈，用户的私人导师 | 我是知序 🐈，用户的私人导师 |
| `templates/personas/tech_expert.md:3` | 你是 nanobot 的技术搭档 | 你是知序的技术搭档 |
| `templates/prompts/README.md:13` | to return to nanobot's default memory behavior | 改为「知序的默认记忆行为」 |

**注意**：`templates/AGENTS.md` 与 `templates/agent/tool_contract.md` 虽在本表，但改动归零（纯命令字面量）。列出是为了避免执行者误改。

### 2.2 Skills（10 文件，`nanobot/skills/`）

| 文件 | 命中 | 本期改 | 留到 Phase 3 |
| --- | ---: | --- | --- |
| `skills/update-setup/SKILL.md` | 17 | 描述文案、`# Update Nanobot` 标题、「Upgrade Nanobot to the latest version」 | `pip install "nanobot-ai[EXTRAS]"`、`nanobot --version`、`command -v nanobot` |
| `skills/tmux/SKILL.md` | 7 | 说明文字里的 agent 名 | `NANOBOT_TMUX_SOCKET_DIR`、`nanobot.sock`、`nanobot-python` |
| `skills/image-generation/SKILL.md` | 4 | 「this nanobot instance」→「this 知序 instance」；「nanobot's media directory」→「知序 的媒体目录」；示例 prompt 里的 `nanobot` | `/home/user/.nanobot/media/…` 路径 |
| `skills/clawhub/SKILL.md` | 5 | 说明文字 | `~/.nanobot/workspace` |
| `skills/skill-creator/SKILL.md` | 2 | 「For `nanobot`」→「在知序中」 | — |
| `skills/README.md` | 3 | 标题与描述 | — |
| `skills/cron/SKILL.md` | 1 | 示例里的 `HKUDS/nanobot` → **保留**（上游归属） | — |
| `skills/github` / `weather` / `summarize` | 各 1 | frontmatter `metadata: {"nanobot": {...}}` → **本期不动**（Phase 2，按契约 §4.8 双回退） | — |

**⚠️ 陷阱**：`SKILL.md` 的 YAML frontmatter 里的 `nanobot` **不是文案**，是代码读取的键（`agent/skills.py:345`）。改错了 emoji/依赖声明全部失效且不报错。

### 2.3 CLI 与运行时文案（Python）

| 位置 | 内容 | 动作 |
| --- | --- | --- |
| `config/schema.py:206` | `bot_name: str = "nanobot"` | → `"知序"`（CLI 显示名，改后 `知序 is thinking...`） |
| `cli/stream.py:49,99` | `bot_name: str = "nanobot"` 默认参数 | 同步 |
| `api/server.py:307,452,480` | `model_name` 默认 `"nanobot"` | → `"zhixu"` |
| `api/server.py:461` | `"owned_by": "nanobot"` | → `"zhixu"` |
| `apps/cli/service.py:1126` | `## Nanobot execution` | → `## 知序 执行`（进入 prompt） |
| `channels/dingtalk/runtime.py:714` | `"title": "Nanobot Reply"` | → `"知序回复"` |
| `channels/dingtalk/runtime.py:64,67,193` | `NanobotDingTalkHandler` 类名 + docstring | 类名改 `ZhixuDingTalkHandler`，docstring 中文 |
| `channels/feishu/config.py:14` | `name: str = "nanobot"` | → `"知序"`（**⚠️ 会改变已存配置的显示**：仅默认值，已存 `config.json` 不受影响） |
| `channels/feishu/instances.py:61,79,216`、`connect.py:205` | 实例默认名 | 同步 |
| `cli/agent.py:67` | `help="Show nanobot runtime logs…"` | → 「Show 知序 runtime logs…」 |
| `cli/commands.py`、`cli/gateway.py`、`cli/webui.py`、`cli/webui_support.py`、`command/builtin.py` | 混杂 help 文本与命令字面量 | **逐行判断**：help 文本改，命令字面量（`["nanobot", "gateway", …]`）留到 Phase 3 |
| `cli/agent.py:15`、`cli/commands.py:58` | `_set_nanobot_logs` 导入 | **不动**（符号名，Phase 3） |
| `agent/context.py:459`、`channels/qq/runtime.py:6`、`channels/telegram/runtime.py:51` | 注释中的 Nanobot | 改注释 |
| `channels/email/tests/*`、`channels/msteams/tests/*` | 测试夹具 `Nanobot <bot@example.com>`、`<at>Nanobot</at>` | 同步改断言与夹具 |

### 2.4 WebUI 文案（仅 value）

| 位置 | 内容 | 动作 |
| --- | --- | --- |
| `webui/src/hooks/useNanobotStream.ts:328` | `new Notification("nanobot", { body })` | → `"知序"`（**通知标题，纯文案**） |
| `i18n/locales/*/common.json` | `gatewayHint` 内的 `` `nanobot gateway` `` | **不动**（命令，Phase 3） |
| `FilePreviewPanel.tsx:93`、`lib/api.ts:132` | 「Restart nanobot gateway」 | **不动**（命令） |
| `i18n/locales/*/common.json` | key `nanobotFeatures.*` | **不动**（Phase 2） |

### 2.5 品牌资产文件名（`git mv`）

| 旧路径 | 新路径 | 引用处需同步 |
| --- | --- | --- |
| `images/nanobot_logo.png` / `.svg` | `images/zhixu_logo.png` / `.svg` | `README.md` |
| `images/nanobot_mark.svg` | `images/zhixu_mark.svg` | `README.md` |
| `images/nanobot_arch.png` | `images/zhixu_arch.png` | `README.md` |
| `images/nanobot_webui.png` / `-source.png` | `images/zhixu_webui.png` / `-source.png` | `README.md` |
| `images/nanobot-apps/automations/context/workbench.png` | `images/zhixu-*.png` | `README.md` |
| `webui/public/brand/mira_favicon_32.png` | `webui/public/brand/zhixu_favicon_32.png` | `index.html`、`manifest.json`、`components/Sidebar.tsx`、`public/sw.js`（若有 precache） |
| `webui/public/brand/mira_apple_touch.png` | `webui/public/brand/zhixu_apple_touch.png` | 同上 |
| `webui/public/brand/mira_icon_192/512/maskable.png` | `webui/public/brand/zhixu_icon_*.png` | 同上 |
| `webui/public/brand/mira_mark.png` | `webui/public/brand/zhixu_mark.png` | 同上 |
| `docs/guides/deploy-nanobot-gateway.md` | `docs/guides/deploy-zhixu-gateway.md` | `docs/guides/README.md`、README、`docs/README.md` |

> `webui/public/brand/zhixu-logo.png` **已存在**，与新命名的 `zhixu_*` 系列并存。本期统一为 `zhixu_*` 命名族，`zhixu-logo.png` 重命名为 `zhixu_logo.png` 归入同一族，消灭三套命名混用。

### 2.6 文档（仅描述性文字）

`docs/` 64 个 md、`README.md`、`SECURITY.md`、`CONTRIBUTING.md`、`AGENTS.md`、`.agent/gotchas.md`、`.agent/design.md`、`harness-kit/` 内文档。

**规则**：每篇文档按 §1 拆两类，只改描述性文字。举例：

| 改 | 不改 |
| --- | --- |
| 「nanobot 是一个轻量级开源 AI agent 框架」 | ```bash nanobot gateway``` |
| 「nanobot WebUI 源码位于 `webui/`」 | `pip install nanobot-ai` |
| 标题、描述、alt 文本 | `NANOBOT_*` 环境变量表 |
| 「nanobot 支持 Windows」 | `HKUDS/nanobot`、`nanobot.wiki`（上游归属） |
| 「nanobot Skills」小节标题 | 代码路径 `nanobot/agent/loop.py:116`（Phase 3） |

`AGENTS.md` 顶部「项目背景：nanobot-ai（v0.3.0…）」的**描述性描述**改；`ruff check nanobot/`、`cd webui && bun run dev` 等**命令块**不动。

### 2.7 `.claude/settings.json`

| 类别 | 归属期 |
| --- | --- |
| `Bash(nanobot --version)` / `Bash(nanobot gateway *)` / `./.venv/Scripts/nanobot --version` / `taskkill //F //IM nanobot.exe` / `-m nanobot.cli gateway` | **Phase 3** |
| `curl -H 'X-Nanobot-Auth: …'` | **Phase 2** |
| `.venv/Scripts/python.exe -m ruff check nanobot/…` | **Phase 3** |
| `/Users/mima0000/Documents/…/nanobot/docs/…`（他人本机绝对路径） | **Phase 1 清理**（本就失效的死条目，删掉） |

## 3. 执行顺序

1. `git mv` 资产文件（§2.5），同步引用处 → **先改名后改引用**，避免中间态断链。
2. 提示词模板（§2.1）。
3. Skills（§2.2）。
4. Python 文案（§2.3）—— 注意**逐行判断**，不要用全局 sed。
5. WebUI 文案（§2.4）。
6. 文档（§2.6）。
7. `.claude/settings.json` 清理（§2.7）。

## 4. 明确不做

- ❌ 不改任何命令字面量、环境变量、HTTP 头/路由、包路径（§1 规则）。
- ❌ 不改 `SKILL.md` frontmatter 的 `metadata` 键。
- ❌ 不改 i18n key 名。
- ❌ 不动 `.ai-runtime-artifacts/`（含本仓自己的 spec / plan）。
- ❌ 不动 `THIRD_PARTY_NOTICES.md` 的上游署名、不动 `SECURITY.md` 的上游维护者邮箱。

## 5. 验收

```powershell
# 1. 提示词渲染无 Jinja 语法破坏
.venv\Scripts\python.exe -c "from zhixu..."   # 本期包名仍是 nanobot
.venv\Scripts\python.exe -c "from nanobot.utils.prompt_templates import render_template; print(render_template('SOUL.md', {}))"

# 2. Python 侧无回归
uv run --no-sync pytest -q
uv run --no-sync ruff check nanobot tests conftest.py

# 3. WebUI 无回归（bun 不在 PATH 时用 npx 兜底）
cd webui; npx vitest run

# 4. 资产引用无断链
rg -n "nanobot_(logo|mark|arch|webui|apps|automations|context|workbench)|mira_" README.md webui/index.html webui/public/manifest.json webui/src/components/Sidebar.tsx

# 5. 本期不该出现的改动（应为空）
rg -n "X-Zhixu-Auth|/api/settings/zhixu-features"          # Phase 2 才有
rg -n "NANOBOT_" -g '!.ai-runtime-artifacts/**'            # 数量应与本期前一致
```

**行为验收**：启动 gateway，跑一轮对话，确认：

- CLI 流式输出显示「知序 is thinking...」而非「nanobot is thinking...」
- 5 套 persona 的开场白不再出现 nanobot
- 工作区新用户首次初始化时，`SOUL.md` / `USER.md` 模板写入的是知序文案
- 桌面通知标题为「知序」

## 6. 风险

| 风险 | 缓解 |
| --- | --- |
| 把命令字面量当文案改了 → 提示词教用户执行不存在的命令 | §1 规则 + §5 第 5 条断言 |
| 改了 `SKILL.md` frontmatter → emoji / 依赖声明失效且不报错 | §2.2 显式标注；验收跑 skill 相关 pytest |
| `git mv` 后 README 断链 | 先 mv 后改引用，验收 §5 第 4 条 |
| 提示词改动影响既有会话的行为一致性 | 提示词每次会话重新渲染，不存在已落盘的旧 system prompt，无需迁移 |

## 7. Spec 自检

- **占位符**：无。文件清单精确到路径，命令字面量例外已逐条标注。
- **内部一致**：§1 规则与 §2 各表的「留到 Phase N」列一致；与契约 §5 跨期边界一致（Phase 1 不碰标识符）。
- **歧义**：「描述性文字 vs 程序读到的字符串」的判定规则已给出正反例对照表。
- **范围**：单一 WU 可承载，无需再拆；文件数约 90（其中 25 为提示词/skill 文档）。

## Next

- 本期方案无误 → 说「写计划」或「制定实施计划」
- 需要调整 → 直接说修改意见
