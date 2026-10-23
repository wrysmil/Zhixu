---
artifact: verification-lite
route: verification
skills:
  - verification-before-completion
skills_evidence:
  - C:/Users/Huangqh/Downloads/harness-kit/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户要求「拉取最新代码，然后 分别再前端测试项目中的团队能力的效果和记忆能力的效果」
created_at: 2026-10-19
tier: 1
---

# 团队能力与记忆能力前端验收 — 轻量验证（Tier 1）

## 范围

- 改动文件：无业务代码改动；仅新增本验证记录与 10 张验收截图。
- 验收对象：真实 WebUI（`http://127.0.0.1:8765`）+ 真实 gateway + 本机 SQLite 状态库。
- 交互方式：本机 Chrome（CDP 9333）离屏真实 GUI，DOM 断言 + `Page.captureScreenshot`，不使用 CLI 浏览器模拟。

## 基线与构建

| 命令 | 结果 |
| --- | --- |
| `git fetch origin --prune` + `git rev-parse HEAD` | 无新提交；HEAD `1d478d7` 与 `origin/feature/desktop-app` 一致 |
| `node node_modules/vitest/vitest.mjs run <11 个 teams/memory 测试文件> --no-cache` | 9 passed / 2 skipped；102 passed / 2 skipped |
| `node node_modules/typescript/bin/tsc -p tsconfig.build.json` | EXIT 0 |
| `node node_modules/vite/bin/vite.js build --logLevel error` | EXIT 0；仅 Browserslist 与 Tailwind 类名歧义 warning，产物 `nanobot/web/dist` |
| `.venv/Scripts/nanobot.exe gateway --foreground` | 健康端 200；WebSocket `ws://127.0.0.1:8765/` 正常；根页面 200 |

## 团队能力（真实 GUI）

| 验收点 | 证据 |
| --- | --- |
| 团队列表 | `#/teams` 显示 3 个活跃团队（验收团队-青竹、创作团队、副本3432we） |
| 创建 | UI 新建「验收团队-青竹-2026-10-19」v1，Leader=指挥官，成员=通用助理/资料研究；DB `team-9afdfc05` |
| 归档/恢复 | Archive → `archived=1` rev2，归档列表可见；Unarchive → rev3 `archived=0` |
| 聊天选择器 | 仅列出活跃团队；临时聊天（Temporary chat）隐藏团队选择器 |
| 选中绑定 | 侧栏选择团队后 composer 显示 `Team: 验收团队-青竹-2026-10-19`，当前 agent 变为队长「指挥官」 |
| 真实运行 | 会话 `websocket:372ffaab…` 发送「请让通用助理用一句话说明自己负责什么，然后汇总回复。不要读取任何文件。」；5s 后队长输出职责一句话 + 汇总 |
| 运行状态 UI | 右侧状态面板显示 `验收团队-青竹-2026-10-19 · Leader · idle · Completed`，并注明 `The Leader handled this turn directly.` |
| 运行数据 | `run-44b7daa3bc6f43bf` status=completed；事件 3 条：`run.started` → `leader.updated(idle)` → `run.finished`；`team_tasks=0` |
| 结论 | 团队绑定、运行生命周期、队长直接作答链路均生效；该简单目标下队长判断无需派工（idle），未产生成员任务属于预期决策而非失败 |

截图：`2026-10-19-teams-ui.png`、`teams-created.png`、`teams-archived.png`、`team-selector.png`、`team-selected.png`、`temporary-chat.png`、`team-run.png`。

## 记忆能力（真实 GUI）

| 验收点 | 证据 |
| --- | --- |
| 记忆页 | `#/memory` 显示 16 条记忆、Semantic memory / Episodes 两个 Tab、自动抽取开关 On |
| 搜索 | 搜索 `spawn` 得 4 条结果 |
| 新建 | UI 新建记忆 `9c90dfe8-498f-4700-83f9-afa9033113ea`，列表 16 → 17 |
| 删除 | UI 删除该测试记忆，列表 17 → 16；该 id 在 DB 中已不存在 |
| Episodes | 14 条情节记录，支持 View details / Delete |
| 自动抽取 | 团队测试会话结束后约 2 分钟（memoryIdleSeconds=120）触发 idle 抽取，生成 episode（source=idle）；删除话题时再次触发 deletion 抽取，共写入 3 条 extraction 记忆（16:52:22），证明「会话 → 情节 → 长期记忆」真实链路可用 |

截图：`2026-10-19-memory-ui.png`、`memory-created.png`、`memory-episodes.png`。

## 清理结果

| 项目 | 结果 |
| --- | --- |
| 测试话题 | 通过 UI 删除；刷新后侧栏不再出现，用户既有话题「你说少了什么」未受影响 |
| 自动抽取副产物 | 删除测试会话产生的 3 条记忆、2 条 episode、1 条 scratchpad、1 条抽取状态；DB 恢复为 16 条记忆 / 14 条 episode / 21 条 scratchpad |
| 测试团队 | 产品无删除动作且 `team_revisions` 触发器为 append-only，故按产品语义归档：rev4、`archived=1`；关联 `team_runs`/`team_events` 审计行保留 |
| 进程与临时文件 | 自建 gateway 与 Chrome 已停止（8765/9333 均关闭）；临时 CDP 脚本目录已删除 |
| 数据库备份 | 清理前对 teams/memory `state.db` 各做一次临时备份，随临时目录一并删除 |

## 未验证项

- 未跑全部 Python/WebUI 测试，仅跑 teams/memory 相关 11 个 WebUI 测试文件。
- 未接入真实外部渠道；团队协作中的成员派工路径本次因队长直接作答未覆盖（`team_tasks=0`）。
- 团队测试数据因 append-only 设计保留归档与审计行，未做物理删除。

## Next

- 验收完成，可刷新现有网站使用；如需提交本次验证产物，按 `git-xywh` + `project.git.md` 走 commit。
- 若需覆盖成员派工路径，可在后续用需要实际检索/执行的多步目标复验，并检查 `team_tasks`/`team_attempts` 增长。
