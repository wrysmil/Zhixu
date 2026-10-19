---
generated_at: 2026-09-10
generator: harness-init
org_skill: git-xywh
---

# Project Git — Agent-With-Memory

本文件只记录**相对组织 Git 规范（`git-xywh` skill）的差异**与**本项目约束**。分支模型、MR 流程、Angular 提交格式全文见 skill，不在此重复。

## 组织基线（默认）

- **Skill**：`git-xywh`
- **何时 invoke**：建分支、提交、rebase、开 MR/PR、热修、合流、打标签、历史恢复
- **执行角色**：**Leader / 主 Agent**；子 Agent 默认不 `git commit` / `push`

## 本项目差异（delta）

| 项 | 值 |
| --- | --- |
| 默认主干 | `main`（CI 仅对 push/PR 到 main 触发）；未见 `develop`/`test` 主干 |
| 组织模型 | **偏离** git-xywh 三主干模型 → 实际为 `main` + `feature/*` 简化模型（推断，待确认） |
| 当前工作分支 | `feature/desktop-app`（与 `origin/feature/desktop-app` 同步，无未推送提交）；专项工作在其之上开临时 `feature/*` 分支 |
| 提交格式 | Angular 风格（近期提交 `feat(webui): ...`、`docs(memory): ...`）；无 commitlint |
| 提交前检查 | 无 `.husky/`、lint-staged（推断无本地 pre-commit 门禁） |
| MR / PR 平台 | GitHub（`github.com/wrysmil/Agent-With-Memory`） |
| Harness 脚手架提交 | 类型可用 `feat`/`chore`/`docs` 等；**标题与正文须中文**（如 `chore(harness-kit): 更新编排文档`），与业务 commit 分开 |
| Harness push/PR | Leader **不**自动 push / 开 PR；须**用户确认**后再按 `git-xywh` 执行 |

## 如何调用 git-xywh

| 环境 | 做法 |
| --- | --- |
| 支持 Skill 工具的平台 | **先** 加载 skill **`git-xywh`**，再读本文件 |
| 无 Skill 工具 | Read `~/.cursor/skills/git-xywh/SKILL.md`（或 `~/.agents/skills/` 下同路径） |
| 安装检查 | `bash harness-kit/scripts/install-ai-skills.sh` 会输出 `ok:` 或 `missing:` |

详见 `routing.md` § Git 协作。

## AI 执行约束

1. 提交 / 分支 / worktree 操作前：**已加载 `git-xywh` skill 正文** + 读本文件（仅 delta）。
2. 禁止（除非用户明确要求）：向受保护主干直推；公共分支 force push；子 Agent 擅自 commit。
3. 用户说「帮我提交」：Leader 声明 `「Harness：git-xywh + project.git.md」` 后执行。
4. 委派子 Agent（`.agents/agents/`）时：业务代码在 **worktree_path**；不派子 Agent 则在主 checkout；编排产物始终在主 checkout。

## 待确认项

- 分支模型：采用 git-xywh 三主干，还是沿用现有 `main` + `feature/*`？
- 是否允许 AI 直接 push `feature/desktop-app`（及在其之上的临时 `feature/*` 分支）到 origin。当前本地与 `origin/feature/desktop-app` 同步，无未推送提交。
- MR/PR 必填审查人要求。
- `uv.lock` 是否应恢复跟踪（现被 .gitignore 排除但 CI 引用）。

## 推断项

- 基于 `.github/workflows/ci.yml`：`main` 为保护主干、无 required-reviewer 配置、PR 触发 CI。
- 提交规范基于近期提交历史：Angular 风格但无自动化门禁。
