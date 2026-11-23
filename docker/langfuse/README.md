# 本地自托管 Langfuse（知序接入用）

为知序提供本地 LLM 调用追踪（trace / generation / 成本 / 延迟）。服务端为 **Langfuse v4**（观测优先数据模型），
知序侧仍用 `pyproject.toml` 中 `langfuse>=3.0.0,<4.0.0` 的 Python SDK 3.15，已实测在 v4 上正常写入。

## 组成与端口

| 服务 | 镜像 | 宿主端口（仅本机） | 用途 |
| --- | --- | --- | --- |
| langfuse-web | `langfuse/langfuse:4` | 127.0.0.1:3000 | Web UI + API + MCP |
| langfuse-worker | `langfuse/langfuse-worker:4` | 127.0.0.1:3030 | 异步 ingestion / 后台迁移 |
| postgres | `postgres:17` | 127.0.0.1:5432 | 元数据、队列、prompt、数据集 |
| clickhouse | `clickhouse/clickhouse-server:26.4.2.10` | 127.0.0.1:8123 / 9000 | 事件与分析存储 |
| minio | `cgr.dev/chainguard/minio` | 127.0.0.1:9090 / 9091 | 事件 / 媒体对象存储 |
| redis | `redis:7` | 127.0.0.1:6379 | 队列缓存 |

## MCP（AI 直接操作 Langfuse）

v4 内置原生 MCP 服务端，AI 助手（Claude Code / Qoder / Cursor / Codex CLI 等）可直接读写：

- 端点：`http://localhost:3000/api/public/mcp`
- 认证：Basic Auth，`base64(pk-lf-...:sk-lf-...)`
- Host 校验：MCP 会校验 `Host` 头，必须用 `localhost` 或在 compose 里用
  `LANGFUSE_MCP_ALLOWED_HOSTS=localhost:3000,127.0.0.1:3000` 放行（本项目已默认带上）。
- 工具集（v4）：prompts、skills、observations、annotation queues、datasets、scores、score configs、
  evaluators、evaluation rules、dashboards、experiments、comments、alerts、models、llm connections 等，
  其中创建/更新类工具默认可写，可用 allowlist 收紧。

用法示例（PowerShell，写入 MCP 配置时填）：

```json
{
  "mcpServers": {
    "langfuse": {
      "type": "http",
      "url": "http://localhost:3000/api/public/mcp",
      "headers": { "Authorization": "Basic <base64(pk:sk)>" }
    }
  }
}
```

## v4 升级后的接口与数据变化

| 变化 | 说明 |
| --- | --- |
| 读 trace | `/api/public/traces` 已弃用（v4 返回空），改用 `/api/public/v2/observations`；trace 由根 observation 表示 |
| 读 score | `/api/public/scores` 已弃用，改用 `/api/public/v3/scores`；写 score 仍走 `/api/public/scores` |
| 写 dataset item | `/api/public/dataset-items` 仍可用 |
| trace 输入输出 | v4 不再随接口返回；知序改为在进程内缓存每轮问答内容（`nanobot/observability/langfuse_tracing.py`） |
| 历史数据 | 升级后由后台迁移逐步回填，**新数据先可见**；旧实验 run 可能不可见（官方 FAQ） |
| 磁盘 | 迁移期 ClickHouse 体积约为原来的 3 倍 |

升级前的备份与回滚步骤在 `C:\Users\Huangqh\AppData\Local\lf-backup-20261120-v3\ROLLBACK.md`。

## Prompt 管理（注册 + 只读对照）

`scripts/langfuse_prompt_sync.py` 把知序的静态模板与人设登记到 Langfuse，并检测线上与本地的漂移：

```bash
# 只读对照：逐条比较本地模板与线上 production 版本，有漂移时退出码为 1
LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=... LANGFUSE_BASE_URL=http://127.0.0.1:3000 \
  .venv/Scripts/python.exe scripts/langfuse_prompt_sync.py

# 同步（仅在内容确实变化时创建新版本并移动 production 标签）
LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=... LANGFUSE_BASE_URL=http://127.0.0.1:3000 \
  .venv/Scripts/python.exe scripts/langfuse_prompt_sync.py --sync
```

覆盖范围（只管理会影响每轮系统提示的框架提示词）：`nanobot/templates/agent/*.md` 与内置 Agent 档案（`zhixu/profile/<id>`），共 22 条，命名空间 `zhixu/agent/*` 与 `zhixu/profile/*`，标签 `production`。
身份类内容（SOUL/AGENT/USER、persona、policies）属于用户自己的工作区文件，**不注册、不覆盖**，因此不会出现在运行时拉取与 Linked Generations 里。

运行时拉取（第二阶段，已接入）：知序按 `production` 标签读取受管提示词并覆盖代码内模板，后台线程按 `LANGFUSE_PROMPTS_TTL_S`（默认 60s）刷新、不阻塞回合；Langfuse 不可达或提示词缺失时回落到本地文件。实际使用的模板版本与 Agent 档案版本会写进 turn trace 的 `prompts` 元数据，其中档案版本会链接到 generation（Prompts → Metrics / Linked Generations 可看版本分布）。开关与调参见 `docs/configuration.md` 的 `LANGFUSE_PROMPTS_*` 表。

## 启动 / 停止

```bash
cd docker/langfuse
docker compose up -d          # 首次会拉取镜像
docker compose ps             # 等待全部 healthy
docker compose down           # 停止（保留数据卷）
docker compose down -v        # 停止并清空所有数据
```

首次启动会通过 `LANGFUSE_INIT_*` 自动创建组织、项目、API key 和管理员账号，无需手工注册。
管理员账号与项目 key 都在 `.env` 中（该文件已被仓库 `.gitignore` 忽略，不要提交）。

- Web UI：<http://127.0.0.1:3000>
- 登录邮箱 / 密码：`.env` 的 `LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD`
- 项目 API key：`.env` 的 `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` / `LANGFUSE_INIT_PROJECT_SECRET_KEY`
- 若 key 丢失：登录 UI，Project Settings → API Keys 可重新生成，再同步更新 `.env` 与知序环境变量

## 接入知序

知序只在**进程环境变量**里检测 Langfuse（见 `nanobot/providers/openai_compat_provider.py`），
配置写在环境变量里而不是 `config.json`。桌面端启动网关时会继承当前用户环境变量。

在仓库根执行（PowerShell），把 key 写入当前用户级环境变量后重启知序桌面端 / 网关：

```powershell
$envFile = "D:\studyspace\project\Zhixu\docker\langfuse\.env"
$vars = @{}
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*([^#][^=]*)=(.*)$') { $vars[$matches[1].Trim()] = $matches[2].Trim() }
}
[Environment]::SetEnvironmentVariable('LANGFUSE_PUBLIC_KEY', $vars['LANGFUSE_INIT_PROJECT_PUBLIC_KEY'], 'User')
[Environment]::SetEnvironmentVariable('LANGFUSE_SECRET_KEY', $vars['LANGFUSE_INIT_PROJECT_SECRET_KEY'], 'User')
[Environment]::SetEnvironmentVariable('LANGFUSE_BASE_URL', 'http://127.0.0.1:3000', 'User')
```

只想给单次终端会话启用（不写用户环境变量）：

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_BASE_URL="http://127.0.0.1:3000"
.venv/Scripts/nanobot gateway
```

注意：

- 运行知序的 Python 环境必须装有 `langfuse`（本仓库 `.venv` 已含 3.x；新环境执行
  `nanobot plugins enable langfuse` 或 `uv sync --extra langfuse`）。
- 只覆盖走 OpenAI 兼容客户端的 provider；原生 SDK provider 不产生 OpenAI 包装 trace。
- 设置页「系统 → Langfuse」显示已配置，需要网关进程能看到上面三个变量。

## 验证

1. UI 能打开：<http://127.0.0.1:3000>
2. API key 可用：

   ```bash
   curl -u "pk-lf-...:sk-lf-..." http://127.0.0.1:3000/api/public/projects
   ```

3. 在知序里跑一次模型调用，Langfuse 的 Tracing 页面出现新 trace。
   自动化回归可参考 `.ai-runtime-artifacts/verifications/2026-11-20-langfuse-*-verification-lite.md` 中的脚本说明。

## 中文界面（可选，社区扩展）

官方 UI 只有英文（i18n 请求 [#12890](https://github.com/langfuse/langfuse/issues/12890) 已关闭为 NOT_PLANNED）。
可以用社区离线扩展 [zleo-ai/langfuse-zh-ui](https://github.com/zleo-ai/langfuse-zh-ui)（MIT，v0.1.0）：

- 在本机 3.174.1 上的实测：登录页 6/6 文案汉化；Tracing 页 100 个控件中 82 个汉化。
  残留英文主要是 "Hide filters"、"IDs / Names"、"Off"、"SELECT/TEXT"、分页等 v3 特有文案。
- 原理：按精确词典替换界面文案，不联网、不调用翻译服务、不改服务端与数据；用户数据（trace 内容、
  项目名等）保持原文。
- 本机解压目录：`%LOCALAPPDATA%\qf-langfuse-zh\langfuse-zh-ui`（保留该目录，扩展需从它加载）。
- 安装：Chrome/Edge → 扩展页 → 开启「开发者模式」→「加载已解压的扩展程序」→ 选择上面的目录。
- 使用：打开 <http://127.0.0.1:3000> → 点工具栏扩展图标 →「切换为中文」。
  **整页刷新后需再点一次**（扩展只申请了 `activeTab` 权限）。
- 实测截图与覆盖率脚本：`.ai-runtime-artifacts/verifications/2026-11-20-langfuse-zh-ui/`、
  `2026-11-20-langfuse-zh-ui-coverage.cjs`。
- 本地副本已打补丁（2026-11-20）：
  1. 补充 v3 设置页子导航与标题词条；允许 `nav/h4` 内的文案；处理 React 拆分的组合标签（如 `Hide` + ` filters`）。
  2. 改为对本机实例自动注入：`manifest.json` 增加 `host_permissions` 与 `content_scripts`
     （仅 `http://127.0.0.1:3000/*`、`http://localhost:3000/*`），新增 `auto.js`。
     重新在扩展页点一次「重新加载」后生效，之后**整页刷新也会自动中文**，无需再点图标。
  3. 重新下载官方包会覆盖这些补丁。实测脚本：
     `.ai-runtime-artifacts/verifications/2026-11-20-langfuse-zh-ui-autoinject.cjs`。

## 运维要点

- 数据持久化在 Docker 命名卷 `langfuse_*`；`docker compose down` 不会删除。
- 升级：修改 compose 中镜像 tag（如 `langfuse/langfuse:3.x.y`）后 `docker compose pull && docker compose up -d`。
- 所有端口只绑定 `127.0.0.1`。如需局域网访问，自行把 web 端口改为 `3000:3000` 并加访问控制。
- trace 里包含完整 prompt 与模型输出，视同敏感数据；生产与个人环境请使用不同项目 / 不同实例。
- 安全：`.env` 与其中的 `ENCRYPTION_KEY`、`SALT`、`NEXTAUTH_SECRET` 一旦泄漏，需轮换对应变量并重建实例。
