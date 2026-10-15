---
artifact: implementation-dispatch
route: writing-plans
plan: .ai-runtime-artifacts/plans/2026-10-15-agent-team-plan.md
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - .claude/skills/writing-plans/SKILL.md
  - .claude/skills/api-and-interface-design/SKILL.md
source:
  - .ai-runtime-artifacts/contracts/2026-10-15-agent-team-contract.md
  - .ai-runtime-artifacts/plans/2026-10-15-agent-team-plan.md
created_at: 2026-10-15
status: approved
approved: true
---

# Agent Team 执行图草案

本文件只规划未来实施，并未派发任何编码任务。当前会话的子 Agent 均为只读调研。实施授权后先加载 orchestration、WORKTREE-INIT，实际分支/worktree 路径由届时原生工具返回，禁止提前虚构路径。

## 执行分组

| GROUP | WU / 对应 Task | 依赖 | 所有权 | 验收 |
| --- | --- | --- | --- | --- |
| G0 | WU-00 / Task 0 环境与接口 | 无 | Leader；契约/基线/fixture 版本 | 环境、已知失败、写入隔离记录 |
| G1 | WU-01 / Task 1 类型与路径 | G0 | executor；teams/models、config/schema/paths、tests/teams/conftest、公共 JSON fixture | 边界/序列化测试 |
| G2 | WU-02 / Task 2 存储与服务 | WU-01 | executor；teams/database/repository/service、对应测试 | CAS/事件原子性/幂等/writer锁 |
| G2 | WU-03 / Task 3 子执行结果 | WU-01 | executor；agent/subagent、agents/runtime、teams/execution、对应测试 | typed result、冻结档案、旧 spawn 回归 |
| G2 | WU-08 / Task 8 管理页 | WU-01 和契约 | executor；前端 teams/types/api、settings/teams、所有 i18n 键、管理页测试 | CRUD/冲突/空态；App 入口补丁交整合者 |
| G3 | WU-04 / Task 4 Leader | WU-02、03 | executor；teams/leader、agent/loop/context、Leader 测试 | 每回合隔离/空集/模型 |
| G3 | WU-05 / Task 5 API与绑定 | WU-02 | executor；teams/binding、teams_api/routes、settings_routes/ws_http、gateway_services、inbound/session_projection/list_index、agents_api | 真实路由/认证/首消息绑定 |
| G3 | WU-09 / Task 9 聊天选择 | WU-08；API可按冻结fixture开发 | executor；TeamSelector、useSessions、App/ThreadShell、lib/types/api/nanobot-client | 首消息和重连；所有共享前端文件唯一整合者 |
| G4 | WU-06 / Task 6 调度 | WU-04、05 | executor；scheduler、agent/tools/team、调度测试；service/loop修改由所属整合者合入 | DAG/并发/取消/幂等 |
| G4 | WU-10 / Task 10 进度 UI | WU-09；冻结 Team frame | executor；共享 team-events、reducer、hook、面板/卡片、进度测试 | seq/归属/终态/历史；client/ThreadShell桥接由WU-09整合者合入 |
| G5 | WU-07 / Task 7 事件与恢复 | WU-06 | executor；teams/events、outbound_projection/wire、websocket/runtime、session/recovery、事件测试 | 补拉、停止、重启不重放 |
| G6 | WU-11 / Task 11 集成尾盘 | 全部 | Leader + 独立 verifier/reviewer/security reviewer | E2E/回归/审查/落盘 |

WU-07 与 WU-03 的 subagent hook 适配不并行写；WU-04 与 WU-06 的 loop 变更顺序合入；WU-05 与 WU-07 共用 run API 注册点，WU-07 在前者结束后补齐。

## 派发约束

- 类型/协议先行：contract + fixture 是 Python/TS 的共同依据；边界签名变更由 Leader 同步两边。
- 每个编码 WU 使用 executor 角色；当前没有 active team/swarm，不用 worker 角色。
- 每个任务 prompt 必含目标、独占文件、只读参考、禁止事项、测试命令和 done criteria，并说明「你不是唯一修改者，不撤销别人改动」。
- worktree_path/branch 在创建后记录到执行日志和 tracking，使用 codex/ 分支前缀；产物始终在 `.ai-runtime-artifacts/`。
- service.py、loop.py、App.tsx、ThreadShell.tsx、nanobot-client.ts 不允许多人同时直接编辑。需要边界修改时，提交窄补丁给指定整合者，或等待其 WU 结束再顺序改。
- 先测试再实现；产出测试证据，不只返回「完成」。子 Agent 不 commit/push，主 Agent 按项目 Git 规范决定提交。
- 使用已有依赖，不启动真实付费模型调用，不读本机配置/密钥，不动未跟踪的 memory/ 和 drawio 备份。

## WU 类型与技能

编码 WU 的 wu_type 为 feature/bugfix，workspace_scope=wu；实际 worktree_path/branch 在执行时登记，不以本草案占位值作为可执行命令。技能至少 source-driven-development、test-driven-development、verification-before-completion，涉及接口再加 api-and-interface-design。返回必须有「Skills 使用」、文件列表、测试结果、剩余风险。

独立审查在编码完成、Leader 集体验证后安排；代码写入者不审自己的 patch。尾盘由 Leader 真实执行和落盘，不宣称会自动触发。

## Next

- 当前计划和契约确认后，用户说「开始实现」或「并行执行」再启动以上工作单元。
- 仅修改接口时同步 contract；仅修改依赖/文件所有权时更新本 dispatch。
