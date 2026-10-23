---
artifact: implementation-dispatch
route: orchestration:dispatcher-workflow
plan: .ai-runtime-artifacts/plans/2026-10-17-writing-team-sidebar-plan.md
skills:
  - writing-plans
  - orchestration
  - api-and-interface-design
skills_evidence:
  - /Users/mima0000/.agents/skills/writing-plans/SKILL.md
  - .claude/skills/orchestration/SKILL.md
  - .claude/skills/api-and-interface-design/SKILL.md
source:
  - .ai-runtime-artifacts/plans/2026-10-17-writing-team-sidebar-plan.md
  - .ai-runtime-artifacts/specs/2026-10-17-writing-team-sidebar-spec.md
  - harness-kit/core/orchestration/dispatcher-workflow.md
  - harness-kit/core/orchestration/skill-preferences.md
created_at: 2026-10-17
approved: true
approval_evidence: 用户任务开头一次性授权「根据方案完成任务，根据需求完成任务开始吧」（2026-10-17）
---

# 创作空间侧栏内置创作团队 — Harness 执行图

> 实施步骤以 **plan**（`.ai-runtime-artifacts/plans/2026-10-17-writing-team-sidebar-plan.md`）为准；本文件只描述并行 GROUP / WU、依赖与派发。多轮审阅时优先改本文件。

## Git 沙箱

| 项 | 值 |
| --- | --- |
| worktree_id | `wt-writing-team-sidebar` |
| worktree_path | `/Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/zhixu/wt-writing-team-sidebar` |
| branch | `harness/wt-writing-team-sidebar` |
| base | `feature/desktop-app` @ `81b4937` |
| 主 checkout | 只读：Leader 落盘产物；**不写业务代码** |
| 初始化 | `git worktree add -b harness/wt-writing-team-sidebar <worktree_path> feature/desktop-app`（在 WORKTREE-INIT 记入 tracking） |

## 执行图

```markdown
GROUP-0（Leader 直做，不委派）:
  WU-00: 契约落盘 contracts/2026-10-17-contract-writing-team-collab.md | 文件: .ai-runtime-artifacts/contracts/*.md | 依赖: 无 | wu_type: docs | agent_role: implementer | wu_skills: api-and-interface-design | worktree_path: n/a

GROUP-1（并行 5，文件互斥）:
  WU-01: 七个内置写作 Skill 正文 | 文件: nanobot/skills/article-{brief,research,outline,draft,rewrite,expand,review}/SKILL.md, tests/writing/test_builtin_skills.py | 依赖: WU-00 | wu_type: feature | agent_role: coder | wu_skills: source-driven-development,incremental-implementation,test-driven-development,verification-before-completion | worktree_path: <wt>
  WU-02: 五个写作出厂档案 | 文件: nanobot/agents/catalog.py, tests/webui/test_agents_catalog.py | 依赖: WU-00 | wu_type: feature | agent_role: coder | wu_skills: source-driven-development,incremental-implementation,test-driven-development,verification-before-completion | worktree_path: <wt>
  WU-03: 内置资产清单与幂等 preparation | 文件: nanobot/writing/builtin.py, nanobot/writing/database.py, tests/writing/test_writing_builtin.py | 依赖: WU-00 | wu_type: api | agent_role: coder | wu_skills: api-and-interface-design,source-driven-development,incremental-implementation,test-driven-development,verification-before-completion | worktree_path: <wt>
  WU-04: 协作持久化 store | 文件: nanobot/writing/collab/{__init__,database,models,repository}.py, tests/writing/test_collab_store.py | 依赖: WU-00 | wu_type: api | agent_role: coder | wu_skills: api-and-interface-design,incremental-implementation,test-driven-development,verification-before-completion | worktree_path: <wt>
  WU-05: 定位、采纳与撤销事务 | 文件: nanobot/writing/collab/{offsets,apply}.py, tests/writing/test_collab_offsets.py, tests/writing/test_collab_apply.py | 依赖: WU-00（与 WU-04 同包不同文件） | wu_type: feature | agent_role: coder | wu_skills: incremental-implementation,test-driven-development,verification-before-completion | worktree_path: <wt>

GROUP-2a（并行 3）:
  WU-06: 任务配方与冻结准备 | 文件: nanobot/writing/collab/{recipe,preparation}.py, tests/writing/test_collab_recipe.py | 依赖: WU-01, WU-02, WU-03 | wu_type: feature | agent_role: coder | wu_skills: source-driven-development,incremental-implementation,test-driven-development,verification-before-completion | worktree_path: <wt>
  WU-07: envelope 与完整长稿落库 + Team 侧最小适配 | 文件: nanobot/writing/collab/envelope.py, nanobot/teams/repository.py, nanobot/teams/scheduler.py, nanobot/agent/subagent.py, tests/writing/test_collab_envelope.py, tests/webui/test_writing_adapter.py | 依赖: WU-04, WU-06 | wu_type: feature | agent_role: coder | wu_skills: source-driven-development,incremental-implementation,test-driven-development,verification-before-completion | worktree_path: <wt>
  WU-10: 内置归属投影与受保护写入口 | 文件: nanobot/agents/store.py, nanobot/webui/agents_api.py, nanobot/webui/skills_api.py, nanobot/webui/skills_marketplace.py, nanobot/teams/service.py, nanobot/webui/teams_routes.py, nanobot/webui/mcp_presets_api.py, tests/webui/test_builtin_protection.py | 依赖: WU-02, WU-03 | wu_type: feature | agent_role: coder | wu_skills: security-and-hardening,incremental-implementation,test-driven-development,verification-before-completion | worktree_path: <wt>

GROUP-2b（串行，独占装配文件）:
  WU-08: 协作批次运行器 + 装配 + .enabled 修复 | 文件: nanobot/writing/collab/runner.py, nanobot/cli/gateway_runtime.py, nanobot/webui/gateway_services.py, tests/writing/test_collab_runner.py | 依赖: WU-04, WU-06, WU-07, WU-10 | wu_type: feature | agent_role: coder | wu_skills: incremental-implementation,observability-and-instrumentation,test-driven-development,verification-before-completion | worktree_path: <wt>

GROUP-2c（串行）:
  WU-09: WS 动作、事件与读接口 | 文件: nanobot/webui/writing_collab_{api,routes}.py, nanobot/webui/settings_routes.py, nanobot/webui/ws_http.py, nanobot/webui/gateway_services.py, nanobot/bus/outbound_events.py, nanobot/webui/outbound_projection.py, nanobot/channels/websocket/runtime.py, tests/webui/test_writing_collab_{api,routes,wiring}.py, tests/webui/test_writing_wiring.py | 依赖: WU-08 | wu_type: api | agent_role: coder | wu_skills: api-and-interface-design,incremental-implementation,observability-and-instrumentation,test-driven-development,verification-before-completion | worktree_path: <wt>

GROUP-3a（并行 2）:
  WU-11: 前端协作 API 与事件归并 | 文件: webui/src/lib/writing-collab-api.ts, webui/src/tests/writing-collab-api.test.ts | 依赖: WU-09 | wu_type: ui | agent_role: coder | wu_skills: frontend-ui-engineering,incremental-implementation,test-driven-development,verification-before-completion | worktree_path: <wt>
  WU-14: i18n 全量同步与既有失败修复 | 文件: webui/src/i18n/locales/<10 locales>/common.json | 依赖: WU-00（键名已冻结） | wu_type: ui | agent_role: implementer | wu_skills: verification-before-completion | worktree_path: <wt>

GROUP-3b（串行）:
  WU-12: 创作搭档侧栏与产物卡 | 文件: webui/src/components/creative/{use-writing-collab.ts,WritingPartnerPanel.tsx,ArtifactCard.tsx,collab-labels.ts}, webui/src/tests/writing-partner-panel.test.tsx, webui/src/tests/writing-artifact-card.test.tsx | 依赖: WU-11, WU-14 | wu_type: ui | agent_role: coder | wu_skills: ui-ux-pro-max,frontend-design,frontend-ui-engineering,test-driven-development,verification-before-completion | worktree_path: <wt>

GROUP-3c（串行）:
  WU-13: 编辑器/工作区集成 + B01 预览重复修复 | 文件: webui/src/components/creative/ArticleEditor.tsx, webui/src/components/creative/CreativeWorkspace.tsx, webui/src/tests/creative-partner-integration.test.tsx, webui/src/tests/creative-workspace.test.tsx | 依赖: WU-12 | wu_type: ui | agent_role: coder | wu_skills: ui-ux-pro-max,frontend-ui-engineering,test-driven-development,verification-before-completion | worktree_path: <wt>

GROUP-4（尾盘，Leader + 并行审查）:
  WU-15a: 集体测试（Python + Vitest + lint + build + gateway 冒烟） | agent_role: test-engineer | wu_type: test | wu_skills: test-driven-development,verification-before-completion
  WU-15b: 代码审查（五轴） | agent_role: reviewer | wu_type: review | wu_skills: requesting-code-review,code-review-and-quality,verification-before-completion
  WU-15c: 安全审查 | agent_role: security-auditor | wu_type: review | wu_skills: security-and-hardening,verification-before-completion
  WU-15d: 浏览器验收 390/768/1280/1440 + 真实模型 W15 全流程 | agent_role: smoke-tester | wu_type: smoke | wu_skills: browser-testing-with-devtools,verification-before-completion
  WU-15e: Leader 落盘 collective-test / code-review / security-review / execution-log
```

## 派发规则（本批补充约束）

1. **cwd 一律为 `worktree_path`**；主 checkout 只由 Leader 写 `.ai-runtime-artifacts/`。
2. 每个 WU 的 prompt 必须包含：身份（`WU-xx` + `wu_type` + `agent_role`）、Goal、Done criteria、允许/禁止文件、Skill slug + 路径、验证命令、L1–L5 上下文块（L2 = plan 对应 Task 节 + contract §2，L3 ≤5 个源文件，L4 = contract，L5 = 失败输出）。
3. 返回必须含 `### Skills 使用` 与 `### References 检查`；任一 `fail` → `wu_status: blocked`，Leader 不整合。
4. 子 Agent **不** `git commit` / `push`；由 Leader 在每批结束后统一提交（Angular 风格）。
5. 禁止事项（所有 WU 通用）：不改 `harness-kit/`；不写 `docs/`；不提交密钥/凭据；不装依赖；不把构建输出写入 `nanobot/web/dist`；不改 `webui/src/components/creative/SuggestionPanel.tsx`（已确认死代码，留给后续清理）。
6. 文件冲突保护（派发后修正，避免同批写同一文件）：`nanobot/writing/database.py` **只允许 WU-03** 修改（追加 `writing_builtin_assets` + `SCHEMA_VERSION="2"`）；协作表 DDL 由 WU-04 自管的 `nanobot/writing/collab/database.py::ensure_collab_schema` 建立，WU-04 **不改** `nanobot/writing/database.py`。`nanobot/webui/gateway_services.py` 只允许 WU-08（先）与 WU-09（后）依次修改；`nanobot/writing/collab/__init__.py` 只允许 WU-04 创建/修改（WU-05 不建不改）。

## 批次门槛

| 批次 | 进入条件 | 退出条件 |
| --- | --- | --- |
| GROUP-0 | 计划已批准 | 契约落盘且与 plan §2 一致 |
| GROUP-1 | WU-00 完成 + WORKTREE-INIT | 5 个 WU 返回；`.venv/bin/python -m pytest tests/writing tests/webui/test_writing_wiring.py -q` 不劣化 |
| GROUP-2a/2b/2c | 上批整合完成 | 协作层单测全绿；接线测试覆盖 7 个新动作 |
| GROUP-3a/3b/3c | action/事件/类型冻结 | Vitest 全绿（含 i18n）+ lint + build |
| GROUP-4 | 全部 WU 返回 | 集体测试 + 双审查 + 浏览器验收 + execution-log 落盘 |

## 变更记录

| 轮次 | 日期 | 变更摘要 |
| --- | --- | --- |
| 1 | 2026-10-17 | 初稿：15 个 WU 分 5 批；据只读侦察修正 Team 接入点、前端落点与 `.enabled` 缺陷归属（WU-08） |
| 2 | 2026-10-17 | 修正文件冲突：`nanobot/writing/database.py` 只归 WU-03；协作表 DDL 由 WU-04 自管 `collab/database.py` |
| 3 | 2026-10-17 | **调度提前**：WU-14（i18n，键名已冻结）与 WU-11（前端协作 API，接口已冻结在契约 §3/§6）提前到与 GROUP-1 并行派发；WU-11 的运行时联调仍在 WU-09 之后 |

## Next

- 执行入口：Leader 按 GROUP-0 → GROUP-1 … 顺序派发；worktree 初始化完成后在 `tracking/DISPATCH-TRACK-2026-10-17-writing-team-sidebar.md` 逐步记录。
- 只改 WU 拆分/依赖 → 改本文件；只改 Task 内容 → 改 plan；改接口 → 先改契约再同步 plan 与本文件。
- 真实模型（W15）在无可用模型时可记录缺口，不作为其余 WU 的阻塞项。
