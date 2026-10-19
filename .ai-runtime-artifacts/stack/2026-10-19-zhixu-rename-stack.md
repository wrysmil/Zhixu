---
artifact: research-report
route: source-driven-development
skills: [source-driven-development]
skills_evidence: [.claude/skills/source-driven-development/SKILL.md]
source: [pyproject.toml, webui/package.json, tui/package.json, desktop/package.json, Dockerfile, docker-compose.yml, .github/workflows/ci.yml, harness-kit/project.profile.md, harness-kit/context-map.md]
created_at: 2026-10-19
status: verified
---

# zhixu 改名技术栈核对

基线分支 `feature/desktop-app`，提交 `1d478d7`。`project.profile.md` 仍写「feature/memory-system / nanobot-ai v0.3.0」，**以本文件与源码为准**，画像文件待本轮结束后同步更新。

## 运行时与工具链

| 工具 | 版本 | 来源 |
| --- | --- | --- |
| Python（项目 venv） | 3.13.3 | `.venv/Scripts/python.exe --version` |
| uv | 0.12.9 | `uv --version` |
| Node | v20.20.2 | `node --version` |
| **bun** | **PATH 上不可用** | `bun --version` → 术语无法识别；`~/.bun/bin`、`%LOCALAPPDATA%\bun\bin` 均无 |
| git | 当前仓 `wrysmil/Zhixu` | `git remote -v` |

> ⚠️ **bun 缺失是本轮改名的执行前置**：`webui` 与 `tui` 的 `build` / `test` 脚本都以 bun 为入口（`webui/package.json` 的 `test: vitest run` 可用 npx 兜底，但 `bun run build` 走 `tsc -p tsconfig.build.json && vite build`）。Phase 2 验收「重建 webui dist」前必须先确认 bun 可用，或明确改用 `npm` 执行等价命令。

## 依赖版本（声明 / 锁文件）

| 层 | 声明 | 备注 |
| --- | --- | --- |
| Python 包 | `>=3.11` | venv 实为 3.13.3 |
| 分发名 | `nanobot-ai` 0.3.0 | `pyproject.toml:2`；本轮改 `zhixu-ai` |
| 构建后端 | `hatchling` + 自定义 hook `hatch_build.py` | hook 内 `from nanobot.webui import build`，**改包名后此 import 必须同步** |
| Pydantic | `>=2.12,<3` | 配置 schema 基于它 |
| pytest / pytest-asyncio | `9.1.1` / `1.4.0` | `asyncio_mode = "auto"` |
| basedpyright / ruff | dev | strict；`ruff` 规则 `E,F,I,N,W`，`line-length = 100` |
| React | `^18.3.1` | `webui/package.json` |
| TypeScript | `^5.7.2` | |
| Vite | `^5.4.11` | `outDir` 指向 `../nanobot/web/dist` |
| Vitest | `^2.1.8` | 覆盖率阈值 statements/lines 85、branches/functions 80 |
| Tailwind | `^3.4.17` | |
| TUI | `@nanobot/tui`（private），`@opentui/core` 0.5.10 | Bun + TS |
| Desktop | `zhixu-desktop`，Electron `^33.2.0`，electron-builder `^25.1.8` | `appId: ai.zhixu.desktop`，`productName: 知序` |

## 改名相关的构建耦合点（改包目录时必须同步）

| # | 位置 | 内容 |
| --- | --- | --- |
| 1 | `webui/vite.config.ts:131` | `outDir: path.resolve(__dirname, "../nanobot/web/dist")` |
| 2 | `hatch_build.py` | `dist_dir = root / "nanobot" / "web" / "dist"`；`from nanobot.webui import build` |
| 3 | `pyproject.toml` | `[tool.hatch.build] include` / `artifacts` / `targets.wheel.packages` / `sources` / `targets.sdist` |
| 4 | `Dockerfile` | `RUN mkdir -p /app/nanobot/web`；`COPY nanobot/ nanobot/`；`COPY --from=webui-builder /app/nanobot/web/dist/`；`rm -rf nanobot` |
| 5 | `.dockerignore:8` | `nanobot/web/dist/` |

## 改名相关的工具配置耦合点

| 位置 | 内容 |
| --- | --- |
| `pyproject.toml:179` | `[tool.basedpyright] include = ["nanobot"]` |
| `pyproject.toml:186` | `testpaths = ["tests", "nanobot/channels"]` |
| `pyproject.toml:189` | `[tool.coverage.run] source = ["nanobot"]` |
| `.github/workflows/ci.yml:90,95` | 路径过滤器 `nanobot/*`、`nanobot/channels/*/webui/*` |
| `.github/workflows/ci.yml:196,207,215` | `ruff check nanobot ...`、`--cov=nanobot` |
| `webui/eslint.config.js:16`、`webui/tailwind.config.js:10`、`webui/tsconfig.json:37-38`、`webui/package.json:13` | `nanobot/channels/*/webui/**` glob |

## 进程入口（兼容层挂载点）

| 入口 | 位置 |
| --- | --- |
| CLI | `nanobot/cli/entry.py:85` `main()` |
| Python SDK | `nanobot/nanobot.py:68` `class Nanobot`（`__init__` 78 行） |
| OpenAI 兼容 API | `nanobot/api/server.py:478` `create_app()` |
| Gateway | `nanobot/gateway/runtime.py` |

环境变量在仓内共 **131 处** `os.environ` / `os.getenv` 调用，**无中心访问器**（`nanobot/config/loader.py:339` 是配置 `${VAR}` 插值，例外）。这一事实直接决定 Phase 3 的兼容层设计——见 `contracts/2026-10-19-contract-zhixu-rename-mapping.md` §5。

## 设计约束

- 沿用 asyncio、pytest `asyncio_mode=auto`、ruff 规则集、Vitest 覆盖率阈值，**不因改名调整任何质量门禁**。
- 不引入新的包管理器或依赖；前端继续沿用仓内现有 npm/bun 约定，禁止在本轮顺便升级依赖。
- 改名是**纯命名替换 + 兼容层**，不夹带任何功能变更或重构（brainstorming skill「不要夹带无关重构」）。
