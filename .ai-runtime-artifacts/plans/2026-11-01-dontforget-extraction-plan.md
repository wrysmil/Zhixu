---
route: superpowers:writing-plans
artifact: plan
date: 2026-11-01
updated_at: 2026-11-01
topic: dontforget-extraction
dispatch: n/a
approved: false
skills_evidence:
  - writing-plans@/Users/mima0000/.agents/skills/writing-plans loaded
source_of_truth:
  - nanobot/memory/ (8424 行)
  - nanobot/agent/memory.py (1288 行)
  - nanobot/agent/hooks/memory_extraction.py (773 行)
  - nanobot/agent/tools/memory_search.py
  - /Users/mima0000/Documents/学习-001/do-project/dontforget/design-demo/index.html
  - .ai-runtime-artifacts/research/2026-11-01-dontforget-desktop-ui-research.md
  - .ai-runtime-artifacts/research/2026-11-01-dontforget-logo-prompt.md
---

# Dont Forget 抽离与重命名 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 zhixu 仓库中「对话 + /skill + /agent + MCP + 记忆」这条最小可用链路抽成独立项目 `Dont Forget`，包名/CLI/配置目录全部去 nanobot 化，以桌面端为唯一交付形态。

**Architecture:** 不做重写，采用「整树复制 → 按白名单裁剪 → 全局重命名 → 修断点 → 验证」的物理搬迁。记忆子系统保持原有边界（`memory/` 包 + `agent/memory.py` + extraction hook + memory_search 工具 + webui memory 路由），只改包名前缀，不改内部结构。

**产品方向：** 以长期记忆为差异化能力的桌面个人助理。默认进入对话，围绕「记住偏好与经历 → 在需要时找回 → 延续之前的对话」组织使用路径；不做记忆数据大屏或 IDE 式首页。正式 WebUI 采用已完成的 `design-demo/index.html` 作为结构与视觉参考，并使用小象 Logo。

**用户追加的结构要求：** 工作空间为必备入口，参考用户提供的 WorkBuddy 截图：左栏按空间归类会话，输入区下方选择新对话所属空间，首页使用居中标题、快捷建议和宽输入框。按用户最新要求，移除「记住／找回／回顾」切换条，输入框右上沿留空，不再使用插画；品牌位保留简洁 Logo。

**Tech Stack:** Python 3.11+ / asyncio / typer / pydantic；WebUI：Vite + React + TypeScript，本机执行 npm（沿用已有 package-lock.json）；桌面：Electron（desktop/main.cjs）。现有 `design-demo/` 是无框架的单文件静态原型，不是正式应用。

**TDD Required:** YES。搬迁类任务的 RED 由「身份守卫测试 + 现有测试套件」承担：先写包身份/残留守卫测试（对 `nanobot` 包失败），再执行搬迁使其转绿；每阶段结束跑全量保留测试。

**目标路径:** `/Users/mima0000/Documents/学习-001/do-project/dontforget`（已存在，目前包含 `design-demo/`；业务代码与运行链路尚未迁移）。

### 当前进度与已确定的设计输入（2026-11-01）

- [x] 已参考 Cherry Studio、AnythingLLM、Jan 的官方界面资料，完成桌面工作台原型优化。
- [x] 已完成两栏会话布局、按需记忆侧栏、中性灰白与深绿色强调、系统字体、最近会话和搜索等原型交互。
- [x] 已生成并经用户认可「小象 + 折角书签」Logo：`design-demo/assets/dontforget-elephant-logo-v1.png`，1254 × 1254，RGBA 透明底。
- [x] 已按用户补充调整首页文案，突出记忆与连续对话；具体文本见 Task 5b。
- [x] 已完成独立工作空间入口、空间会话分组/折叠、新建与切换空间、输入区空间选择、已有会话空间只读、当前空间的示例记忆侧栏。
- [x] 已参考用户 WorkBuddy 截图完成首页结构；按最新要求删除记忆任务切换条及输入区插画，输入区上沿留空。
- [ ] 正式 React WebUI 接入原型设计与真实记忆状态。
- [ ] 将 Logo 接入正式 React 页面、启动页和桌面应用图标；静态原型仅品牌位使用 Logo，正式应用未迁移；不接入输入区插画。
- [ ] Task 1–6 的仓库迁移、后端接线和最终交付验证。

**执行约束：** 保留现有 `design-demo/` 及 Logo，不清空目标目录、不覆盖用户设计资产；复制源仓库时排除其 `/design-demo`，禁止使用会删除目标独有文件的同步选项。原型通过的浏览器检查只能证明演示交互，不代表正式模型、记忆和桌面链路已完成。`approved: false` 继续表示整体迁移计划尚未获执行确认，不把 Logo 的确认扩大为全部计划授权。

**证据索引（均位于源仓库 zhixu）：**

- 界面调研：`.ai-runtime-artifacts/research/2026-11-01-dontforget-desktop-ui-research.md`。
- Logo 说明与提示词：`.ai-runtime-artifacts/research/2026-11-01-dontforget-logo-prompt.md`。
- 原型验证：`.ai-runtime-artifacts/verifications/2026-11-01-dontforget-ui-verification-lite.md`；浏览器脚本、JSON 和截图在同名 `2026-11-01-dontforget-ui/` 目录。
- 工作空间与插画验证：`.ai-runtime-artifacts/verifications/2026-11-01-workspace-ui/`；包含新版截图、空间行为检查及 reviewer 修复回归证据。

---

## 一、保留 / 删除白名单（锁定边界）

### 保留（新项目需要的文件/目录）

| 路径 | 理由 |
| --- | --- |
| `nanobot/agent/` | agent loop / runner / subagent / skills / hooks / tools |
| `nanobot/providers/` | 模型接入 |
| `nanobot/memory/` | **记忆主体** |
| `nanobot/session/` | 会话与上下文压缩 |
| `nanobot/bus/` | 消息总线 |
| `nanobot/config/` | 配置 |
| `nanobot/cli/` | 启动入口（裁剪 `eval_commands.py`、`tui_launcher.py`） |
| `nanobot/command/` | 斜杠命令（/skill /agent 等） |
| `nanobot/skills/` | 保留 memory / skill-creator / summarize / weather / github / cron / image-generation |
| `nanobot/templates/` | system prompt / dream 模板 |
| `nanobot/utils/` | 工具库（`gitstore` 被记忆依赖） |
| `nanobot/webui/` | WebUI 后端（裁剪 teams/writing/evaluation 路由）；`webui/build.py` 被 `hatch_build.py` 调用，必须保留 |
| `nanobot/web/` | 前端构建产物目录（`web/dist/` + `__init__.py` 静态服务），由 `webui/` 构建生成 |
| `nanobot/channels/{base,manager,registry,_manifest,_setup,contracts,plugin,validation,connect,notification_routes,__init__}.py` + `channels/websocket/` | 只留 WebSocket 通道 |
| `nanobot/gateway/` | 桌面端启动的 gateway 进程 |
| `nanobot/cron/` | 定时任务（基础能力） |
| `nanobot/identity/` | 身份/人设，被记忆依赖 |
| `nanobot/llm_usage/` | 记忆依赖（`llm_usage.context`） |
| `nanobot/subagent_executions/` | 子 Agent 执行记录 |
| `nanobot/security/` | 安全基线 |
| `nanobot/audio/` | 语音转写（可选保留） |
| `nanobot/pairing/` | 会话配对（小，754 行） |
| `nanobot/events.py` `runtime_context.py` `workspace_identity.py` `__main__.py` `__init__.py` | 根级依赖 |
| `webui/` | 前端（裁剪 teams/writing/evaluation 页面与 i18n key） |
| `desktop/` | Electron 桌面壳 |
| `scripts/` `pyproject.toml` `uv.lock` `hatch_build.py` `conftest.py` `LICENSE` `.gitignore` | 构建与测试 |
| `tests/` | 裁剪后跟随模块保留 |

### 删除

| 路径 | 规模 |
| --- | --- |
| `nanobot/evaluation/` | 18214 行 |
| `nanobot/writing/` | 7996 行 |
| `nanobot/teams/` | 6011 行 |
| `nanobot/agent/tools/team.py` | — |
| `nanobot/channels/` 除 websocket 与基础设施外的 15 个通道 | ~4.9 万行 |
| `nanobot/triggers/` | 986 行（外部触发/远程控制） |
| `nanobot/tui/` 相关（`cli/tui_launcher.py`、根 `tui/`） | 桌面端不需要 |
| `nanobot/apps/` `nanobot/sdk/` `nanobot/api/` `nanobot/nanobot.py` | 非桌面链路（SDK / HTTP API / 门面） |
| `nanobot/cli/eval_commands.py`、`trigger` 命令、`serve` 命令（OpenAI API 服务） | 评估 / 触发 / HTTP API 链路 |
| `nanobot/agent/tools/cli_apps.py` | 依赖 `nanobot/apps/`，随 apps 一起删 |
| `tests/{evaluation,writing,teams,triggers,apps,cli_apps}/`、`tests/test_nanobot_facade.py`、`tests/test_openai_api.py`、`tests/test_api_*.py`、`tests/test_sdk_streaming.py`、`tests/test_writing_paths.py`、`tests/test_webui_terminal*.py`、`tests/test_webui_pty_terminal.py`、`tests/test_docker.sh` | 对应模块已删 |
| `.github/`（CI、tui-release）、`.agent/`、`.idea/`、`.coverage`、`.dockerignore`、`core_agent_lines.sh` | 本仓库脚手架 / 已失效流水线 |
| `nanobot/webui/{teams_*,writing_*,evaluation_*,...}` | 对应上述删除模块 |
| `nanobot/skills/article-*`（6 个）、`clawhub`、`my`、`update-setup`、`tmux` | 写作/市场/终端类技能 |
| 根 `memory/` `teams/` `writing/`（运行时 `state.db`）、`docs/`、`images/`、`packages/`、`dist/`、`node_modules/`、`harness-kit/`、`Dockerfile`、`docker-compose*.yml`、`entrypoint.sh`、`render*.{yaml,json}`、`COMMUNICATION.md` | 运行时残留 / 部署脚手架 / 本仓库 harness |

---

## 二、重命名映射（唯一真相源）

| 类别 | 旧 | 新 |
| --- | --- | --- |
| Python 包目录 | `nanobot/` | `dontforget/` |
| import | `nanobot.x` | `dontforget.x` |
| 发行名 | `nanobot-ai` | `dontforget` |
| CLI 入口 | `nanobot` | `dontforget` |
| 配置目录 | `~/.nanobot` | `~/.dontforget` |
| 环境变量 | `NANOBOT_*` | `DONTFORGET_*` |
| 桌面 productName | `nanobot` / 知序 | `Dont Forget` |
| 品牌展示文案 | Nanobot / 知序 / Zhixu / Mira | Dont Forget |

**不替换：** `THIRD_PARTY_NOTICES.md`、`LICENSE` 中指向上游 `nanobot-ai` 的归属声明；`docs` 已删除；代码注释中引用上游 issue 编号可保留。

---

## 三、任务分解

### Task 1: 建立新仓库骨架与身份守卫测试（RED）

**Files:**
- Reuse: `/Users/mima0000/Documents/学习-001/do-project/dontforget/`（保留既有 `design-demo/`，检查 Git 状态后按需 git init）
- Create: `dontforget/tests/test_identity.py`
- Create: `dontforget/scripts/check_no_nanobot.py`

- [ ] **Step 1: 创建目录并初始化 git**

```bash
mkdir -p /Users/mima0000/Documents/学习-001/do-project/dontforget
cd /Users/mima0000/Documents/学习-001/do-project/dontforget
git init -b main
```

- [ ] **Step 2: 写失败的身份守卫测试**

`tests/test_identity.py`：

```python
import importlib
import pathlib

import pytest


def test_package_imports_as_dontforget():
    mod = importlib.import_module("dontforget")
    assert mod.__name__ == "dontforget"


def test_old_package_name_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("nanobot")


def test_no_nanobot_imports_in_source():
    root = pathlib.Path(__file__).resolve().parents[1] / "dontforget"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "from nanobot" in text or "import nanobot" in text:
            offenders.append(str(path))
    assert offenders == []


def test_default_config_dir_is_dontforget():
    from dontforget.config.schema import Config

    cfg = Config()
    assert ".dontforget" in str(getattr(cfg.agents, "workspace", ""))


def test_version_resolves_without_installed_metadata():
    import dontforget

    assert isinstance(dontforget.__version__, str)
    assert dontforget.__version__ != "0.0.0"
```

- [ ] **Step 3: 跑测试确认失败（RED）**

新目录此时还没有 `pyproject.toml`，`uv run pytest` 会先报「找不到项目」而不是目标错误。用裸 Python 取 RED 证据：

Run: `python3 -c "import dontforget"`
Expected: `ModuleNotFoundError: No module named 'dontforget'`

pytest 版本留到 Task 3 Step 5 再跑。

- [ ] **Step 4: 写残留守卫脚本**

`scripts/check_no_nanobot.py`：扫描 `dontforget/`、`webui/src/`、`desktop/`，排除 `THIRD_PARTY_NOTICES.md`，发现 `nanobot`（大小写不敏感）时打印文件:行并 `sys.exit(1)`。

- [ ] **Step 5: 提交骨架**

```bash
git add tests/test_identity.py scripts/check_no_nanobot.py
git commit -m "test: add package identity and residual-name guards"
```

---

### Task 2: 复制保留集（GREEN 前的准备）

**Files:** 从源仓库复制白名单。

- [ ] **Step 1: 用 rsync 复制，排除删除项**

**排除项必须区分「根级目录」和「同名嵌套目录」**：`--exclude='memory'` 会连 `nanobot/memory/`、`nanobot/skills/memory/` 一起排掉，导致记忆整包丢失。根级目录一律用 `/` 锚定。

```bash
SRC=/Users/mima0000/Documents/学习-001/do-project/zhixu
DST=/Users/mima0000/Documents/学习-001/do-project/dontforget
rsync -a --exclude='__pycache__' --exclude='.venv' --exclude='node_modules' \
  --exclude='/dist' --exclude='.git' --exclude='.ai-runtime-artifacts' \
  --exclude='.github' --exclude='.agent' --exclude='.idea' --exclude='.claude' \
  --exclude='.coverage' --exclude='.DS_Store' --exclude='.dockerignore' \
  --exclude='.gitattributes' --exclude='.logo-cache' --exclude='/design-demo' \
  --exclude='harness-kit' --exclude='/tui' --exclude='/docs' --exclude='/images' \
  --exclude='/packages' --exclude='/memory' --exclude='/teams' --exclude='/writing' \
  --exclude='nanobot/evaluation' --exclude='nanobot/writing' --exclude='nanobot/teams' \
  --exclude='nanobot/triggers' --exclude='nanobot/apps' --exclude='nanobot/sdk' \
  --exclude='nanobot/api' --exclude='nanobot/cli/eval_commands.py' \
  --exclude='nanobot/cli/tui_launcher.py' --exclude='nanobot/agent/tools/team.py' \
  --exclude='desktop/release' --exclude='desktop/node_modules' \
  "$SRC"/ "$DST"/
```

- [ ] **Step 1b: 复制后立刻校验记忆包没被误排**

```bash
cd /Users/mima0000/Documents/学习-001/do-project/dontforget
test "$(find dontforget/memory -name '*.py' | wc -l | tr -d ' ')" -ge 38 || echo "FAIL: memory 包缺失"
test -d dontforget/web/dist || echo "FAIL: web/dist 缺失"
test -d dontforget/skills/memory || echo "FAIL: skills/memory 缺失"
```

Expected: 三行都不输出 FAIL。

- [ ] **Step 2: 删除非 websocket 通道**

```bash
cd $DST/nanobot/channels
for d in dingtalk discord email feishu matrix mattermost mochat msteams napcat qq signal slack telegram wecom weixin whatsapp; do
  rm -rf "$d"
done
```

- [ ] **Step 3: 删除写作类技能**

```bash
cd $DST/nanobot/skills
rm -rf article-* clawhub my update-setup tmux
```

- [ ] **Step 4: 删除 root 级部署脚手架**

```bash
cd $DST
rm -f Dockerfile docker-compose*.yml entrypoint.sh render.yaml render-config.json COMMUNICATION.md
rm -rf __pycache__
```

- [ ] **Step 5: 确认记忆相关文件完整**

Run: `ls dontforget/memory/ dontforget/agent/memory.py dontforget/agent/hooks/memory_extraction.py dontforget/agent/tools/memory_search.py dontforget/webui/memory_*.py`
Expected: 全部存在，记忆包 38 个 py 文件

---

### Task 3: 包目录与 import 重命名（GREEN）

**Files:** 全仓库。

- [ ] **Step 1: 重命名包目录**

Task 2 只是 rsync 复制，文件尚未纳入 git 索引，`git mv` 会失败，必须用 `mv`：

```bash
cd /Users/mima0000/Documents/学习-001/do-project/dontforget
mv nanobot dontforget
git add -A && git status --short | head
```

- [ ] **Step 2: 重写 Python import**

BSD `sed` 不支持 `\b`，改用 perl：

```bash
cd /Users/mima0000/Documents/学习-001/do-project/dontforget
find dontforget -name '*.py' -print0 | xargs -0 perl -pi -e 's/\bnanobot\./dontforget./g'
grep -rnE "(from|import) nanobot\b" dontforget --include='*.py' | head
```

Expected: 第二条无输出。

- [ ] **Step 2b: 重写裸字符串**

`import` 之外还有一批会把包名当**字符串**用的地方，漏了会在运行时炸：

```bash
cd /Users/mima0000/Documents/学习-001/do-project/dontforget
grep -rlE '"(nanobot|nanobot-ai)"' dontforget hatch_build.py --include='*.py' \
  | xargs perl -pi -e 's/"nanobot-ai"/"dontforget"/g; s/"nanobot"/"dontforget"/g'
grep -rnE '"nanobot' dontforget hatch_build.py --include='*.py' | head
```

覆盖到的关键点：`__init__.py` 的 `_pkg_version("nanobot-ai")`、`optional_features.py` 的 `metadata("nanobot-ai")`、`utils/helpers.py` 的 `pkg_files("nanobot")`（模板加载会直接失效）、`hatch_build.py` 的 `root / "nanobot" / "web" / "dist"`、各 provider 的客户端标识头。

- [ ] **Step 3: 重写配置目录与 CLI 名**

```bash
cd /Users/mima0000/Documents/学习-001/do-project/dontforget
grep -rlE '\.nanobot' dontforget webui/src desktop --include='*.py' --include='*.ts' --include='*.tsx' --include='*.cjs' --include='*.json' \
  | xargs perl -pi -e 's/\.nanobot/.dontforget/g'
grep -rlE 'NANOBOT_' dontforget webui/src desktop --include='*.py' --include='*.ts' --include='*.tsx' --include='*.cjs' \
  | xargs perl -pi -e 's/NANOBOT_/DONTFORGET_/g'
```

- [ ] **Step 4: 改 pyproject**

`pyproject.toml`：
- `name = "dontforget"`
- `[project.scripts]` → `dontforget = "dontforget.cli.entry:main"`
- `packages` / `include` / `hatch_build.py` 中的 `nanobot` 路径改为 `dontforget`
- `[project.entry-points."nanobot.tools"]` 注释改为 `dontforget.tools`

- [ ] **Step 5: 跑身份测试**

`uv.lock` 里仍写着 `nanobot-ai`，直接 `uv sync` 会先卡在锁文件上，必须重建：

Run: `uv lock && uv sync && uv run pytest tests/test_identity.py -q`
Expected: 5 passed（`Config.agents.workspace` 默认值已核实为 `~/.nanobot/workspace`，改名后断言成立）

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "refactor: rename nanobot package to dontforget"
```

---

### Task 4: 修复裁剪造成的断点

**Files（按命中修改）：** `dontforget/agent/loop.py`、`dontforget/webui/gateway_services.py`、`dontforget/webui/settings_routes.py`、`dontforget/config/schema.py`、`dontforget/cli/commands.py`、`dontforget/channels/registry.py`、`dontforget/webui/*.py`。

- [ ] **Step 1: 找出所有指向已删模块的 import**

```bash
cd /Users/mima0000/Documents/学习-001/do-project/dontforget
grep -rnE "^(from|import) dontforget\.(evaluation|writing|teams|triggers|apps|sdk|api)\b|^from \.(evaluation|writing|teams|triggers)\b" \
  dontforget --include='*.py' | grep -v __pycache__
```

已核实的断点分布（82 处命中，其中 18 个属于保留文件）：`webui/gateway_services.py`(8)、`webui/inbound_commands.py`(4)、`agent/loop.py`(3)、`webui/ws_http.py`(2)、`webui/mcp_presets_api.py`(2)、`cli/gateway_runtime.py`(2)、`agent/tools/cli_apps.py`(2)、`agent/context.py`、`channels/websocket/runtime.py` 等各 1 处。其余命中都落在被删模块内部，随删除一并消失。

- [ ] **Step 2: 逐个删除死分支**

原则：删 import → 删对应调用点 → 删只被该调用点使用的配置字段；不保留兼容 shim。

**`agent/loop.py` 单列，共 17 处**（已逐行核实，全部围着 `ctx.team` 判空，不是深改但要小心）：

| 位置 | 内容 | 处理 |
| --- | --- | --- |
| 118-120, 139 | `teams.binding` / `teams.leader` / `triggers.local_*` 的 4 个 import | 删 |
| 171-172 | `_team_signature()` | 删 |
| 242-243 | `Turn.tools` 上的 `team: LeaderTurnContext \| None` 字段 | 删字段及注释 |
| 501 | `self.team_context_provider` 参数 | 删 |
| 582-584 | `LocalTriggerTurnCoordinator(...)` | 删 |
| 1195-1201 | `_team_context_for_turn()` | 删 |
| 1213-1217 | `_leader_runtime()` | 删；2522 行调用改为 `self.runtime_for_session(session)` |
| 1302-1310 | `_team_revision_switched()` / `_stamp_team_revision()` | 删；2546/2552 调用一并删 |
| 2411-2418 | `persist_message_binding` + `build_leader_registry(tools, ctx.team)` 整块 | 删，工具集保持原样 |

其余文件：`webui/gateway_services.py`（851 行中 168 处 team/writing/evaluation 命中，是最大的一块外科手术）、`config/schema.py`、`command/builtin.py`。

**兜底策略：** 若 `gateway_services.py` 逐处删除超过半天仍不收敛，改为**重写成最小装配版**——只保留 memory / sessions / skills / mcp / settings 的服务注册，直接丢弃其余分支。这比在 168 个命中点里做减法更快，也更不容易留下半死代码。

**改完跑 `tests/agent` 时必须对照基线**：当前仓库基线是 `1 failed, 1677 passed`，唯一失败为 `test_mcp_reconnect_crash.py::test_mcp_reconnect_during_shutdown_does_not_crash`。只有出现这条之外的失败才算回归。

- [ ] **Step 3: 清理 webui 路由**

删除 `dontforget/webui/{teams_*,writing_*,evaluation_*}.py`，并从路由注册处移除。

- [ ] **Step 4: 前端清理移到 Task 4b**

前端没有 `src/pages/` 目录，实际结构是 `App.tsx`（3150 行，团队状态贯穿全文件）+ `workspace/routes.ts` + `components/{thread,settings,creative,...}` + `features/`。原步骤中的 `rm -rf src/pages/...` 路径不存在，已改写为独立任务（见 Task 4b）。此处只做后端。

- [ ] **Step 5: 导入冒烟**

Run: `uv run python -c "import dontforget; import dontforget.agent.loop; import dontforget.webui.gateway_services; print('ok')"`
Expected: `ok`

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "refactor: drop team, writing, evaluation and extra channel modules"
```

---

### Task 4b: 前端裁剪（团队 / 写作 / 评估 / CLI Apps）

**Files（已核实）：**
- Modify: `webui/src/App.tsx`（3150 行）、`webui/src/workspace/routes.ts`
- Delete: `webui/src/components/settings/system/AppsSettings.tsx`、`webui/src/components/creative/WritingChatPanel.tsx` 等写作/团队组件
- Delete: `webui/src/tests/` 中 team/writing/evaluation/apps 相关测试（约 20+ 个文件）
- Modify: `webui/src/i18n/locales/*/common.json`（10 个语言包，en 命中 110 处）

- [ ] **Step 1: 列出真实命中**

```bash
cd /Users/mima0000/Documents/学习-001/do-project/dontforget/webui
grep -rnE "teams?|writing|evaluation|article|apps" src --include='*.ts' --include='*.tsx' | grep -v '/tests/' | cut -d: -f1 | sort | uniq -c | sort -rn
```

- [ ] **Step 2: 按依赖顺序删**

1. 先删叶子组件（`components/creative/*`、`components/settings/system/AppsSettings.tsx`、团队选择器组件）
2. 再删 `App.tsx` 里的 `useTeamOptions` / `NewChatTeam` / `pendingTeam` / `view === "teams"` 分支与 `errors.teamBindingExpired` 文案
3. 最后改 `workspace/routes.ts`：`CAPABILITY_VIEWS` 去掉 `"apps"`、`"teams"`；`article` 路由与 `eval` 常量删除

- [ ] **Step 3: i18n 只删 key，不动结构**

10 个语言包必须保持与 `en` 相同的 key 结构（仓库有 parity 守卫测试）。

- [ ] **Step 4: 类型检查**

本机**没有 bun**（已实测 `bun not found`），统一用 npm（`webui/` 同时有 `package-lock.json`）：

Run: `cd webui && npx tsc -p tsconfig.build.json --noEmit`
Expected: 无报错

- [ ] **Step 5: 单测**

Run: `cd webui && npm run test`
Expected: 保留的测试全绿（vitest 全量偶发 waitFor flake，失败先单跑复核）

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "refactor(webui): remove team, writing, evaluation and cli-apps surfaces"
```

---

### Task 4c: 配置、CLI 与构建元数据收口

**Files：** `dontforget/config/schema.py`、`dontforget/cli/commands.py`、`dontforget/cli/onboard.py`、`pyproject.toml`、`uv.lock`

- [ ] **Step 1: 删配置段**

`Config` 去掉 `api: ApiConfig` 字段，删除 `TeamsConfig` 类；`onboard.py` 里 api/teams 相关开关同步删除（channel 列表是 pkgutil 动态发现，删目录后自动收敛）。

- [ ] **Step 2: 删 CLI 命令**

`commands.py` 删除 `trigger` 与 `serve` 两个命令；`command/builtin.py` 还要删掉 `/trigger` 斜杠命令本体 `cmd_trigger`（1127 行）与两处注册（1238-1239 行），否则它会继续 `import nanobot.triggers.local_store`。
保留 `gateway`、`onboard`、`agent`、`sessions`、`channels`、`plugins`、`status`、`version`。

- [ ] **Step 3: 改 pyproject 非 name 字段**

- `include`：`nanobot/**/*.py` → `dontforget/**/*.py`，删除 `nanobot/channels/*/webui/**/*`（只剩 websocket，保留该项亦可）
- `packages = ["nanobot"]` → `["dontforget"]`
- `[tool.hatch.build.targets.sdist] include` 同步
- `[tool.pytest.ini_options] testpaths = ["tests", "nanobot/channels"]` → `["tests"]`（**否则路径不存在直接报错**）
- `[tool.coverage] source = ["nanobot"]` → `["dontforget"]`
- 删除 optional-dependencies 里的 `api = ["aiohttp..."]`
- `webui/package.json` 的 `lint` 脚本里 `"nanobot/channels/*/webui/**/*.{ts,tsx}"` 改成 `"dontforget/channels/websocket/webui/**/*.{ts,tsx}"`（或直接删掉该 glob）

- [ ] **Step 4: 重建锁文件**

```bash
cd /Users/mima0000/Documents/学习-001/do-project/dontforget
uv lock && uv sync
```

- [ ] **Step 5: 冒烟**

Run: `uv run dontforget --help`
Expected: 命令列表里没有 `serve` / `trigger`

---

### Task 4d: 补回「会话结束 → 记忆抽取」闭环

**前置事实（已逐行核实）：** 这条链路在源仓库里**本身是断的**。

- `SessionEndOrchestrator.run()` 全仓库**零调用方**（只有 `memory_extraction.py:269` 的实例化）。
- `api/endpoints/sessions.py` 把 `SessionEndEvent` 发到 `event_bus`，但 `MessageBus.publish_event` 只做**出站投递**，没有内部消费者。
- `MemoryExtractionHook` 是每轮由工厂临时构造的，没有按 session_key 的全局注册表。

所以只补端点 = 补一个没人接收的事件。必须同时补消费者。

**设计（最小闭环，沿用现有 mutation allowlist 模式）：**

```
前端 api.ts endSession()
  → WS mutation "session.end"
  → ws_http._webui_mutation_path → /api/sessions/{key}/end
  → _handle_session_end 取 transcript
  → 注入的 session_end_action(key, reason, transcript)
  → 装配层构造 SessionEndOrchestrator 并 await run(event)
```

**Files:**
- Modify: `dontforget/webui/ws_http.py`（4 处：`_webui_mutation_path`、`_is_webui_mutation_path`、`_dispatch_session_routes`、新增 `_handle_session_end`；`__init__` 增加 `session_end_action` 参数）
- Modify: `dontforget/webui/gateway_services.py`（`build_gateway_services` 透传 `session_end_action`）
- Modify: `dontforget/channels/manager.py`（`ChannelManager.__init__` 收到 bridge 并透传给 `build_gateway_services`，第 200 行调用处）
- Modify: `dontforget/cli/gateway_runtime.py`（构造 bridge、传给 `ChannelManager`，loop 建好后回填，第 851 行附近）
- Modify: `dontforget/agent/loop.py`（新增 `async def end_session(session_key, *, reason="user_close", transcript=None) -> bool`）
- Create: `dontforget/memory/session_end_factory.py`（把 `MemoryExtractionHook._build_orchestrator_extractor` 的适配逻辑抽出来，供 loop 复用）
- Modify: `webui/src/lib/api.ts`（新增 `endSession`）
- Modify: `webui/src/hooks/useSessions.ts` 或 `App.tsx`（删除会话前先 end，或加菜单项）
- Test: `nanobot/channels/websocket/tests/test_websocket_http_routes.py`（新增 3 例）、`webui/src/tests/session-end.test.ts`

- [ ] **Step 1: 写失败测试（后端）**

测试加在 `nanobot/channels/websocket/tests/test_websocket_http_routes.py`，复用现成的 `_seed_session` / `_ch` / `_webui_mutate`（`_ch` 与 `build_gateway_services` 需新增 `session_end_action` 透传参数）：

```python
@pytest.mark.asyncio
async def test_session_end_invokes_action(bus, tmp_path, monkeypatch):
    monkeypatch.setattr("nanobot.config.paths.get_data_dir", lambda: tmp_path)
    sm = _seed_session(tmp_path, key="websocket:ending")
    calls = []

    async def fake_end(key, reason, transcript):
        calls.append((key, reason, transcript))
        return True

    channel = _ch(bus, session_manager=sm, port=29904, session_end_action=fake_end)
    server_task = asyncio.create_task(channel.start())
    try:
        resp = await _webui_mutate(
            channel, "session.end",
            {"key": "websocket:ending", "reason": "user_close"},
        )
        assert resp.status_code == 200
        assert calls[0][0] == "websocket:ending"
        assert calls[0][1] == "user_close"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_session_end_requires_key(bus, tmp_path, monkeypatch):
    monkeypatch.setattr("nanobot.config.paths.get_data_dir", lambda: tmp_path)
    channel = _ch(bus, session_manager=SessionManager(tmp_path / "workspace"), port=29905)
    server_task = asyncio.create_task(channel.start())
    try:
        resp = await _webui_mutate(channel, "session.end", {})
        assert resp.status_code == 400
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_session_end_unconfigured_returns_503(bus, tmp_path, monkeypatch):
    monkeypatch.setattr("nanobot.config.paths.get_data_dir", lambda: tmp_path)
    sm = _seed_session(tmp_path, key="websocket:noaction")
    channel = _ch(bus, session_manager=sm, port=29906)
    server_task = asyncio.create_task(channel.start())
    try:
        resp = await _webui_mutate(channel, "session.end", {"key": "websocket:noaction"})
        assert resp.status_code == 503
    finally:
        await channel.stop()
        await server_task
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest nanobot/channels/websocket/tests/test_websocket_http_routes.py -q -k session_end`
Expected: FAIL —— `unknown WebUI mutation action`（404）

- [ ] **Step 3: 实现 ws_http 四处改动**

```python
# _webui_mutation_path
if action == "session.end":
    key = payload.get("key")
    if not isinstance(key, str) or not key.strip():
        return _http_error(400, "missing session key")
    return f"/api/sessions/{quote(key, safe='')}/end"

# _is_webui_mutation_path
if re.match(r"^/api/sessions/[^/]+/end$", path):
    return True

# _dispatch_session_routes
m = re.match(r"^/api/sessions/([^/]+)/end$", got)
if m:
    return await self._handle_session_end(request, m.group(1))

# _handle_session_end
async def _handle_session_end(self, request: WsRequest, key: str) -> Response:
    if self.session_end_action is None:
        return _http_error(503, "session end is not configured")
    payload = _webui_mutation_payload(request)
    reason = str(payload.get("reason") or "user_close")
    transcript = _session_transcript(self.session_manager, key)
    await self.session_end_action(key, reason, transcript)
    return _json_response({"status": "ok", "session_key": key})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest nanobot/channels/websocket/tests/test_websocket_http_routes.py -q -k session_end`
Expected: 3 passed

- [ ] **Step 5: 装配层接线（按现有「回捞」模式）**

`agent/loop.py` 新增方法，复用已有依赖，不复制第二套：

```python
async def end_session(
    self, session_key: str, *, reason: str = "user_close",
    transcript: list[dict[str, Any]] | None = None,
) -> bool:
    services = self._resolve_memory_services(None)
    session = self.sessions.get_or_create(session_key)
    from dontforget.memory.session_end_factory import run_session_end

    return await run_session_end(
        services=services,
        runtime=self.runtime_for_session(session),
        session_key=session_key,
        reason=reason,
        transcript=transcript if transcript is not None else session.messages,
    )
```

`cli/gateway_runtime.py` 用 bridge 解决「agent 是局部变量、manager 先构造」的顺序问题（与该文件既有的 `webui_gateway` 回捞模式一致）：

```python
class _SessionEndBridge:
    loop: Any | None = None

    async def __call__(self, key: str, reason: str, transcript: list[dict[str, Any]]) -> bool:
        if self.loop is None:
            return False
        return await self.loop.end_session(key, reason=reason, transcript=transcript)

session_end_bridge = _SessionEndBridge()
channels = ChannelManager(..., webui_session_end_action=session_end_bridge)
...
session_end_bridge.loop = loop   # loop 构造完成后回填
```

透传链：`ChannelManager` → `build_gateway_services` → `GatewayHTTPHandler(session_end_action=...)`。`session_end_factory.run_session_end` 内部复用 `ScratchpadWriter` + `MemoryExtractor` + `SessionEndOrchestrator`，异常只记日志、返回 `False`，不阻断 HTTP 响应。

- [ ] **Step 6: 前端最小调用**

`api.ts` 增加：

```ts
export async function endSession(
  transport: WebUIMutationTransport,
  key: string,
  reason: string = "user_close",
): Promise<{ status: string; session_key: string }> {
  return mutation(transport, "session.end", { key, reason });
}
```

调用点二选一：删除会话前先 `endSession`（先收尾再删），或在会话菜单加「结束会话」。默认后者更符合语义，改动也更小。

- [ ] **Step 7: 端到端手测**

桌面端发起对话 → 结束会话 → 确认记忆面板出现本次会话的抽取记录（source = `session_end`）。

- [ ] **Step 8: 提交**

```bash
git add -A
git commit -m "feat(memory): restore session-end extraction via WebUI mutation"
```

**工期修正：** 原估「半天」偏乐观——消费者缺失使实际约为 **1～1.5 天**（后端 0.5–0.75 天 + 前端接线 0.25 天 + 测试与手测 0.25–0.5 天）。

---

### Task 5: 品牌改写

**已实测的品牌面（Task 3 之后仍会残留）：** `zhixu` 86 文件、`Zhixu` 82 文件、`知序` 54 文件、`mira` 13 文件；另有品牌文件名与 HTTP 头。

**改写策略（分三档，不要一把梭）：**

| 档位 | 内容 | 处理 |
| --- | --- | --- |
| A. 用户可见 | i18n 文案、窗口标题、productName、README、boot.html | 全部改 `Dont Forget` |
| B. 运行契约 | `X-Zhixu-Auth` 头、`application/x-zhixu-session-key` 拖拽类型、`MIRA_GATEWAY_CMD` | 前后端 + 测试**同一提交**内一起改 |
| C. 内部文件名 | `zhixu-client.ts` / `useZhixuStream.ts` / 插图资源 | 可保留文件名为内部实现，但文件内品牌串按 A 档处理 |

- [ ] **Step 1: 桌面壳**

`desktop/main.cjs`：`spawn` 的 `nanobot gateway` / `-m nanobot gateway` → `dontforget gateway` / `-m dontforget gateway`；`APP_NAME`、"知序浏览器" → `Dont Forget`；`MIRA_GATEWAY_CMD` → `DONTFORGET_GATEWAY_CMD`。
`desktop/package.json`：`name` → `dontforget-desktop`，`productName` → `Dont Forget`，`appId` → `app.dontforget.desktop`。
`desktop/boot.html` 标题与 `<h1>` 同步。

- [ ] **Step 2: WebUI 文案**

`webui/src/i18n/locales/*/common.json`（10 个语言包，只改 value，key 结构必须与 `en` 一致）；`webui/index.html`、`webui/package.json` 的 name 同步。

- [ ] **Step 3: 运行契约改名（B 档，一次提交内完成）**

```bash
cd /Users/mima0000/Documents/学习-001/do-project/dontforget
grep -rlnE "X-Zhixu-Auth|x-zhixu-auth" dontforget webui/src --include='*.py' --include='*.ts' --include='*.tsx' \
  | xargs perl -pi -e 's/X-Zhixu-Auth/X-Dontforget-Auth/g; s/x-zhixu-auth/x-dontforget-auth/g'
grep -rln "X-Nanobot-Auth" dontforget --include='*.py' | xargs perl -pi -e 's/X-Nanobot-Auth/X-Dontforget-Auth/g; s/x-nanobot-auth/x-dontforget-auth/g'
grep -rln "x-zhixu-session-key" webui/src --include='*.ts' --include='*.tsx' | xargs perl -pi -e 's/x-zhixu-session-key/x-dontforget-session-key/g'
```

改完必须跑 `tests/` 与 `webui` 里所有引用这些头的测试。

- [ ] **Step 4: 文档与 AGENTS**

重写 `README.md` 为 Dont Forget 简介（保留上游 MIT 归属，不编造 benchmark）；新仓库写一份精简 `AGENTS.md`，**不要**继承 zhixu 的 harness 路由。

- [ ] **Step 5: 残留扫描**

Run: `uv run python scripts/check_no_nanobot.py && grep -rnE "Zhixu|知序|Mira" dontforget webui/src desktop --include='*.py' --include='*.ts' --include='*.tsx' --include='*.cjs' --include='*.html' | head`
Expected: 守卫退出码 0；第二条只剩允许保留的内部文件名或空输出

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "chore: rebrand to Dont Forget"
```

---

### Task 5b: 将记忆优先的桌面工作台落实到正式 WebUI

**前置：** Task 4b 前端裁剪与 Task 5 品牌改写完成。该任务会修改 `App.tsx` 与侧栏，不与 Task 4b 并行写同一文件；不改记忆抽取算法。

**Files（相对于目标 dontforget 项目）：**
- Reference only: `design-demo/index.html`。
- Modify: `webui/src/App.tsx`、`webui/src/components/Sidebar.tsx`、`webui/src/components/thread/ThreadWelcome.tsx`、`webui/src/components/thread/ThreadHeader.tsx`、`webui/src/components/thread/ThreadComposer.tsx`。
- Modify: `webui/src/components/workspace/WorkspaceHome.tsx`（收敛为同一个新对话入口）、`webui/src/workspace/routes.ts`、`webui/src/globals.css`、`webui/src/i18n/locales/*/common.json`。
- Reuse: `webui/src/components/SessionSearchDialog.tsx`、`webui/src/components/workspace/MemoryPage.tsx` 与现有 API/WS 数据层。
- Test: `webui/src/tests/memory-workbench.test.tsx`，并复用现有 session-search/chat-list 测试。

**固定的信息结构：**

| 区域 | 正式实现要求 |
| --- | --- |
| 左侧约 252px | 新对话、搜索、记忆库、自动化；独立对话与工作空间分区；空间可创建/选择/折叠，空间内按最近活动排序会话；底部为助手与工具、设置 |
| 新对话主区 | 居中记忆主题标题与说明、输入框上方三个直接可用的快捷建议、宽输入框；无记忆任务切换条；下方只显示当前空间的最近对话，不加统计卡和功能宣传栅格 |
| 对话区 | 当前会话标题与空间归属、消息正文、底部输入；左侧附件、右侧模型/记忆/发送；输入框下方有工作空间选择与空间记忆入口 |
| 输入区上沿 | 留空，不使用插画或占位图标；不保留原插画的额外间距 |
| 右侧详情 | 默认关闭，查看相关记忆或助手详情时展开；窄窗口下可通过按钮和 Escape 关闭 |
| 次级能力 | 助手、技能、个人偏好归到工具区；MCP 与模型服务配置进入设置 |

**首页中文文案（本轮已更新静态原型，正式 WebUI 仍待迁移）：**

| 位置 | 文案 |
| --- | --- |
| 主标题 | 聊过的事，不必从头说。 |
| 说明 | 记住你的偏好、决定与经历，在需要时为你找回。 |
| 输入提示 | 告诉我需要记住的事，或找回之前聊过的内容… |
| 快捷建议 | 记住一个新偏好 / 回顾之前的决定 / 找回聊过的事 |
| 最近对话标题 | 从上次的对话继续 |

说明体现产品目标；原型继续显示「交互原型／演示数据」。正式页面的召回条数、来源、连接状态和写入结果必须来自真实服务；不将 transcript、最近会话列表直接宣称为长期记忆，不在写入成功前提示「已记住」。记忆关闭、检索为空、检索失败分别给出准确状态。快捷建议只填入草稿，由用户编辑后发送。

**工作空间行为边界：** 新对话创建时绑定稳定空间标识；打开既有会话恢复其空间，输入区不允许将既有会话随意改归属。切换新对话的空间保留当前草稿；从既有聊天切到另一空间开始新对话，不能把消息误发到旧会话。侧栏空间、输入选择器与最近会话保持同步，移动端选择/创建成功后关闭导航遮罩。空间名称去除首尾空白、检查重名并按文本渲染。空间记忆侧栏只显示当前空间内容，空空间不借用其他项目记忆；「查看全部记忆」进入显式全局管理视图。原型创建的是内存分组，不会创建本地目录；正式实现先核对既有 workspace 身份、会话/API 契约，复用持久化与目录授权机制，不能仅靠前端筛选实现记忆隔离。

- [ ] **Step 1: 为正式界面编写失败的行为测试**

  在 `memory-workbench.test.tsx` 使用现有 React Testing Library / Vitest 测试设施，覆盖：从既有会话返回新对话后发送必须创建新会话；新消息使会话按真实最近活动时间排序；打开/关闭详情不丢失草稿；IME 的 Enter 不触发发送；记忆空结果、关闭和服务失败不显示虚假成功；没有会话时展示可发送消息的空状态。追加空间创建/重名/恶意名称转义、会话绑定与恢复、跨空间记忆隔离、移动端关闭遮罩、空间创建失败和刷新后的持久化恢复测试。

- [ ] **Step 2: 运行并确认 RED**

  在目标 `webui/` 执行 `npm run test -- src/tests/memory-workbench.test.tsx`。记录具体行为断言失败，不能用缺少依赖或测试设施报错充当 RED。

- [ ] **Step 3: 复用现有组件接入布局、文案和真实状态**

  按上表调整现有组件与路由；中性浅灰/白底、系统无衬线、少量 `#286453` 强调，复用主题 token 支持暗色。去掉装饰性大标题、全局统计栏与重复功能卡片。通过现有 API/WS 获取会话、记忆和连接状态；不复制静态原型里的 `MEMORIES`、示例回复、内存会话数组，不用 iframe 嵌入原型。记忆详情复用现有数据与权限边界。

- [ ] **Step 4: 确认 GREEN 并进行布局验证**

  在目标 `webui/` 执行 `npm run test -- src/tests/memory-workbench.test.tsx src/tests/session-search-dialog.test.tsx src/tests/chat-list.test.tsx`，再执行 `npm run test`、`npm run lint`、`npm run build`。在 1440 / 1024 / 768 / 390 / 320px 检查首页、对话、记忆和设置；浅/暗主题、空状态、长内容、键盘焦点和详情关闭均须可用。原型检查脚本可参考，正式验收需运行在真实 WebUI 上。

- [ ] **Step 5: 记录证据并形成独立提交**

  将实际命令输出、浏览器检查和截图写入 `.ai-runtime-artifacts/verifications/`，提交仅包含工作台 UI 与对应行为测试，遵循 Lore 提交格式。未通过正式应用检查前不勾选该任务。

---

### Task 5c: 应用小象 Logo 与桌面品牌资源

**前置：** Task 5 完成；Logo 主图已存在，不重复生成。此任务是静态品牌资源接入，使用文件检查、构建与视觉验收，不添加仅断言文件名的单元测试。

**Files（相对于目标 dontforget 项目）：**
- Preserve: `design-demo/assets/dontforget-elephant-logo-v1.png`（原始母图）。
- Preserve: `design-demo/assets/dontforget-elephant-mascot-v1.png`（历史备选插画，仅保留文件，不再在页面消费）。
- Create: `webui/public/brand/dontforget-elephant.png`（页面用 PNG）与 `webui/public/icons/dontforget-32.png`（favicon）。
- Modify: `webui/index.html`、正式 WebUI 当前品牌图形的消费组件、`desktop/assets/icon.png`、`desktop/boot.html`、`desktop/package.json`；检查 `desktop/main.cjs` 的窗口图标路径。

- [ ] **Step 1: 检查母图与现有消费位置**

  确认母图为 1254 × 1254 RGBA 且存在透明像素；检索页面、启动页、BrowserWindow 与打包配置中的旧图标引用。PNG 是栅格资源，不将改后缀文件当作 SVG；保留原母图，派生文件单独保存。

- [ ] **Step 2: 派生并接入资源**

  使用已有图片处理工具，从母图导出页面图标、32px favicon 与 512px 桌面图标，保持纵横比和留白，不拉伸、不覆盖原母图。正式 Logo 使用「小象 + 折角书签」，深绿色与工作台一致。浅色、暗色背景下检查轮廓；如为暗色使用浅色派生版，保持同一轮廓并单独保存。`desktop/main.cjs` 与 `desktop/boot.html` 当前均消费 `assets/icon.png`，复用该入口；更新打包配置的品牌名与图标引用。按实际发布目标需要导出 `.ico` / `.icns`，不在尚未构建的平台上宣称图标验证完成。

  输入框右上沿按用户最新要求留空，不接入插画、卡通角色或额外图标；删除任务切换条及其状态、事件和多余间距。历史生成资产可保留归档，不作为正式接入任务。

- [ ] **Step 3: 构建与视觉验收**

  执行目标 WebUI 的 `npm run build` 与目标 desktop 的 `npm run smoke`；核对页面/启动页资源可加载、24/32px 下可辨认、浅暗主题有对比度，并检查应用窗口与实际打包图标。构建通过不替代任务栏/Dock 图标的 GUI 检查；无法运行的平台写入验证缺口。

- [ ] **Step 4: 记录资源与提交**

  报告母图、派生图、消费位置和实测平台，将截图放 `.ai-runtime-artifacts/verifications/`，品牌资源形成独立 Lore 提交。

---

### Task 6: 验证与交付证据

**前置：** Task 1–5、Task 5b 与 Task 5c 完成；原型验证和 Logo 生成不替代正式应用验收。

- [ ] **Step 1: 后端测试**

```bash
cd /Users/mima0000/Documents/学习-001/do-project/dontforget
uv run pytest tests/memory tests/session tests/agent tests/config -q
```

- [ ] **Step 2: lint / 类型**

```bash
uv run ruff check dontforget
uv run --no-sync basedpyright
```

- [ ] **Step 3: 前端构建**

本机无 bun，用 npm：

```bash
cd webui && npm ci && npm run build
```

Expected: `tsc -p tsconfig.build.json && vite build` 均通过，产物落到 `dontforget/web/dist`

- [ ] **Step 4: 运行冒烟**

```bash
cd /Users/mima0000/Documents/学习-001/do-project/dontforget
uv run dontforget --version
uv run dontforget gateway --background --config ~/.dontforget/config.json
curl -s http://127.0.0.1:8765/health || true
```

- [ ] **Step 5: 记忆链路手测**

WebUI 里发一轮对话 → 触发一次记忆抽取 → 在记忆面板看到条目；再执行「结束会话」（Task 4d）→ 确认出现 `source=session_end` 的抽取记录；确认数据落在 `~/.dontforget`。

- [ ] **Step 6: 落盘验证报告**

写 `.ai-runtime-artifacts/verifications/2026-11-01-dontforget-extraction-verification.md`，含每步命令与真实输出。

---

## 四、风险与对策

| 风险 | 对策 |
| --- | --- |
| `agent/loop.py`（3239 行）里团队/写作入口埋得深 | 先 grep 再删，逐段跑导入冒烟 |
| 前端团队状态贯穿 `App.tsx`（3150 行） | Task 4b 分三段：叶子组件 → App.tsx 分支 → routes；每段跑一次 typecheck |
| `sed` 误改上游归属（`nanobot-ai`） | 只对 `nanobot.` / `.nanobot` / `NANOBOT_` 做定向替换，最后跑残留守卫 |
| 记忆依赖 `webui.memory_services`，裁 webui 时误删 | 白名单锁死 `webui/memory_*.py` 五个文件 |
| WebUI 10 语言包 key 结构不一致 | 品牌只改 value，不动 key；跑现有 i18n parity 测试 |
| 桌面端无法自动化验证 | 只验证 gateway 启动 + 构建产物存在，GUI 手测项显式标注 |
| 搬迁覆盖目标目录已有原型和 Logo | 保留 `design-demo/`，排除源端同名目录；不清空目标、不使用删除目标独有文件的同步选项 |
| 复制演示数据或把静态验证当成产品完成 | 正式 WebUI 复用 API/WS，实际验证写入/召回/错误状态；原型与正式交付分别记录进度 |
| 工作台任务与裁剪任务共同修改 App.tsx | Task 4b → Task 5 → Task 5b 串行；品牌资产 Task 5c 单独提交并核对消费位置 |
| Logo 缩小或暗色背景下不可辨认 | 保留母图，检查 24/32px 与浅/暗背景，平台图标按实际构建结果验收 |

## 五、验收标准

1. `import dontforget` 成功，`nanobot` 模块不可导入。
2. 残留守卫脚本通过（排除上游归属文件）。
3. 记忆测试全绿（源仓库基线实测 **955 passed / 7.3s**）；会话与 agent 测试**不超过已知基线**：
   - `tests/agent`：基线 `1 failed, 1677 passed`，唯一失败 `test_mcp_reconnect_crash.py::test_mcp_reconnect_during_shutdown_does_not_crash`
   - `ruff check`：源仓库基线 **10 条**（`memory/__init__.py` 8 处 F401、`lifecycle.py`/`repository.py` 各 1 处 N806），不得新增
   - webui vitest 全量偶发 `waitFor` flake，失败先单跑复核再判定
4. `dontforget --version` / `dontforget gateway` 可启动，WebUI 可打开。
5. 新项目数据全部落在 `~/.dontforget`，不读取 `~/.nanobot`。
6. `git log` 按任务分段提交，可逐段回滚。
7. 正式首页采用 Task 5b 的记忆主题文案和两栏会话结构；右侧详情默认关闭，窄窗口可关闭，键盘操作与草稿保留通过行为测试。
8. 正式记忆状态来自真实服务：开启/关闭、召回为空、检索失败、写入成功均有准确反馈；首页最近会话不冒充长期记忆。
9. 小象 Logo 应用到正式 WebUI、favicon、启动页和桌面图标；母图保留，浅/暗背景与实际发布平台的视觉验证有记录。
10. 现有 `design-demo/` 及 Logo 在迁移前后均保留；原型已完成不计作 Task 5b/5c 已完成。
11. 工作空间入口、空间内会话分组、输入区空间选择及会话归属恢复均可用；正式空间与会话状态可持久化，记忆隔离由后端保障；移动端选择与创建后不会残留遮罩。
12. 首页无「记住／找回／回顾」切换条，首页与聊天输入框上沿无插画，保留工作空间与三个快捷建议；品牌位继续使用 Logo。

## 六、文档审计结论（2026-11-01 复核）

对保留集做了「保留文件 → 待删模块」反向依赖扫描，初版计划遗漏以下 12 项，已在上文修正：

| # | 遗漏点 | 实际情况 | 处理 |
| --- | --- | --- | --- |
| 1 | `nanobot/web/` 未列入保留 | 前端构建产物 `web/dist` + 静态服务，`hatch_build.py` 写入此处 | 已加入保留表 |
| 2 | 前端没有 `src/pages/` | 实际为 `App.tsx`(3150 行) + `workspace/routes.ts` + `components/*`；非测试命中 77 个文件 | 新增 Task 4b |
| 3 | 团队状态贯穿 `App.tsx` | `useTeamOptions` / `NewChatTeam` / `pendingTeam` / `view === "teams"` 分布在 3150 行文件内 | Task 4b 按依赖顺序剥 |
| 4 | `skills_api.py` 依赖 `writing.builtin` | 内置技能清单来自写作模块 | Task 4 改为枚举保留技能 |
| 5 | `mcp_presets_api.py` 混装三类预设 | MCP + `apps.protocol` + `writing.builtin` | Task 4 裁剪为纯 MCP |
| 6 | `settings_routes.py` 依赖 `api.runtime` | OpenAI API 服务设置段 | Task 4c 删段 |
| 7 | `cli/gateway_runtime.py` 有 4 处功能接线 | teams 绑定、writing 协作收敛、嵌入式 evaluation、local triggers | Task 4 逐处删除 |
| 8 | `agent/tools/cli_apps.py` 依赖 `apps/` | 与 `agent/context.py` 一起引用 `apps.cli.utils` | 已加入删除表 + 改 context.py |
| 9 | `trigger` / `serve` 两个 CLI 命令 | 分别依赖 `triggers/` 与 `api/` | Task 4c 删除 |
| 10 | `pyproject` 的 `testpaths` 含 `nanobot/channels` | 目录删除后 pytest 直接报错 | Task 4c 改为 `["tests"]` |
| 11 | `.github/` 会被 rsync 带过去 | CI 与 tui-release 流水线引用已删模块 | 已加入删除表 |
| 12 | `uv.lock` 仍锁定 `nanobot-ai` | 改 name 后必须重建 | Task 4c `uv lock` |

补充核实（执行时直接可用）：

- 硬编码 `~/.nanobot` 共 **54 处** Python 代码，Task 3 的定向替换会全部覆盖。
- `channels/notification_routes.py` 硬编码了 telegram/matrix/feishu/slack/mattermost 的线程字段映射，删通道后成为死映射，Task 4 顺手清掉。
- `desktop/main.cjs` 除 `nanobot` 外还有 `APP_NAME = "知序"`、`MIRA_GATEWAY_CMD`、窗口标题「知序浏览器」，`boot.html` 标题「知序」；`desktop/package.json` 为 `zhixu-desktop` / `productName: 知序` / `appId: ai.zhixu.desktop`。这些都要归到 Task 5。
- `config/loader.py` 与 `agent/tools/self.py` 里的配置键 `"my"` 是 `self` 工具开关，**与要删的 `skills/my` 目录无关**，不要误删。
- `onboard.py` 的通道列表由 pkgutil 动态发现，删通道目录后自动收敛；只需处理 api/teams 开关。

## 七、自测结论（第二轮，逐条试跑后修正）

对计划里的命令做了可行性试跑，发现 9 处会直接卡住或漏改的问题，均已写回上文：

| # | 自测项 | 结果 | 修正 |
| --- | --- | --- | --- |
| 1 | Task 1 用 `uv run pytest` 取 RED | 新目录还没有 `pyproject.toml`，会先报「找不到项目」 | 改用 `python3 -c "import dontforget"` |
| 2 | `sed 's/\bnanobot\./.../'` | BSD sed 不支持 `\b`，macOS 上不生效 | 全部换 `perl -pi -e` |
| 3 | `git mv nanobot dontforget` | rsync 复制的文件未入索引，`git mv` 失败 | 改 `mv` + `git add -A` |
| 4 | `uv sync` | `uv.lock` 仍锁 `nanobot-ai` | 先 `uv lock` |
| 5 | 裸字符串包名 | `_pkg_version("nanobot-ai")`、`pkg_files("nanobot")`、`hatch_build` 的 `"nanobot"` 路径、provider 标识头，共 14+ 处不在 `nanobot.` 替换范围内 | 新增 Task 3 Step 2b |
| 6 | `bun run typecheck` | `webui/package.json` 没有这个脚本（只有 dev/build/preview/test/lint） | 改 `bunx tsc -p tsconfig.build.json --noEmit` |
| 7 | `webui` 的 `lint` 脚本 | glob 写死 `nanobot/channels/*/webui/**` | 加入 Task 4c |
| 8 | 品牌面 | 不只是 locales：`zhixu` 86 文件、`Zhixu` 82、`知序` 54、`mira` 13；含 `X-Zhixu-Auth` 头与拖拽类型等运行契约 | Task 5 重写为 A/B/C 三档 |
| 9 | rsync 排除项 | 会带入 `.github/`、`.agent/`、`.idea/`、`.claude/`、`.coverage` 等 | 已补 exclude |
| 10 | `--exclude='memory'` 未锚定 | **实测会把 `nanobot/memory/`（42 条）和 `nanobot/skills/memory/` 一起排掉，记忆整包丢失** | 根级目录全部改 `/` 锚定，并新增复制后校验步骤 |

试跑通过、可直接依赖的假设：

- `Config.agents.workspace` 默认值确为 `~/.nanobot/workspace`，改名后身份测试断言成立。
- `webui` 确实有 `build` / `test` 脚本，Task 6 的构建与单测命令有效。
- `tests/memory` 有 79 个测试文件，作为记忆链路回归的基线足够厚。
- `pytest.ini_options.testpaths` 确实包含 `nanobot/channels`，删除通道后不改必崩——这条已进 Task 4c。

## 八、第三轮自测：假设被现实推翻的部分

这一轮不再检查「文档写没写全」，而是逐条验证「计划里假设成立吗」。5 条不成立：

| # | 原假设 | 实测 | 影响 |
| --- | --- | --- | --- |
| 1 | 用 bun 构建/测试前端 | **本机没有 bun**（`bun not found`），但 `webui/` 有 `package-lock.json` | 所有 `bun` 命令改 `npm ci` / `npm run` / `npx` |
| 2 | `loop.py` 只是删几个 import | 共 **17 处**，含 `ctx.team` 字段、`team_context_provider`、`_leader_runtime`（2522 行调用）、`_stamp_team_revision`（2552 行调用） | 已单列成 17 行表格，逐处给处理方式 |
| 3 | `/trigger` 只存在于 CLI | `command/builtin.py` 里还有斜杠命令 `cmd_trigger` + 两处注册，同样 import `triggers.local_store` | 已加入 Task 4c |
| 4 | agent 测试有 4 个已知失败 | 实测 `1 failed, 1677 passed`，只剩 `test_mcp_reconnect_crash` | 验收口径改为「不超过 1 个已知失败」 |
| 5 | 删除 `api/` 无副作用 | **`SessionEndEvent` 全仓库只有 `api/endpoints/sessions.py` 会发** | 见下方行为变化 |

**功能变化与决策（用户已确认：保留）：** 删掉 `api/` 后，`POST /api/sessions/{key}/end` 入口消失。进一步核实发现这条链路**在源仓库本来就是断的**——`SessionEndOrchestrator.run()` 零调用方。因此 Task 4d 不只补端点，还要补消费者，实际工期修正为 **1～1.5 天**。保留的其余路径：每条消息后的增量抽取、Dream 周期巩固（`gateway_runtime` 的 `dream` job）、空闲/主题切换触发。

**已验证通过的基线（可直接写进验收报告）：**

- `tests/memory`：**955 passed / 7.33s**
- `tests/agent`：1 failed / 1677 passed（唯一失败为 MCP 重连用例）
- `nanobot/webui/build.py` 支持 npm 回退（`pick_webui_build_runner()`），无需 bun
- `gateway_services.py` 851 行、168 处 team/writing/evaluation 命中——**这是整个搬迁中最重的一处外科手术**，比 `loop.py` 更费工

**执行环境提醒：** macOS 没有 `timeout` 命令，验证脚本里不要用；需要限时用 `gtimeout` 或 Bash 工具自身的超时。

## Next

- 「开始实现」——保留现有原型和 Logo，从 Task 1 起顺序执行，正式 UI 与品牌资产分别按 Task 5b / 5c 验收
- 「并行执行」——Task 2/3 机械搬迁由 Leader 直做；**Task 4 与 Task 4d 都改 `ws_http.py` / `gateway_services.py`，必须串行**；4b（纯前端）与 4c（配置/CLI/构建）可与 4 并行；Task 5b 必须在 4b 与 5 后执行，避免同文件冲突
- 「先只做 Task 1-3」——先拿到一个能 `import dontforget` 的可运行骨架，再决定裁剪节奏
