---
artifact: implementation-dispatch
route: writing-plans -> orchestration
batch: zhixu-writing-phase-a
plan: .ai-runtime-artifacts/plans/2026-10-12-zhixu-writing-phase-a-plan.md
spec: .ai-runtime-artifacts/specs/2026-10-10-zhixu-writing-capability-spec.md
contract: .ai-runtime-artifacts/contracts/2026-10-12-contract-writing-article-store.md
skills:
  - writing-plans
  - orchestration
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
source:
  - harness-kit/core/routing.md
  - harness-kit/core/orchestration/dispatcher-workflow.md
created_at: 2026-10-12
status: draft
approved: false
tier: 2
worktree: .harness-worktrees/zhixu/wt-writing-phase-a（用户确认后由 Leader 创建，base = feature/desktop-app HEAD）
---

# 调度：zhixu-writing-phase-a

> 实施步骤以 **plan** 为准；本文件只描述并行 GROUP / WU 与派发。多轮审阅时优先改本文件。

## 并行前提（为什么前端不用等后端）

前端 WU 的测试全部 mock `fetch` 与 `WebUIMutationTransport`（`webui/src/lib/api.ts:90-156` 的 `request` / `mutation` 是唯一出入口），只要**契约 §4/§5/§6 的形状**确定就能开发；`A4.1` 才需要 gateway 真跑。因此 A1 支与 A2/A3 支可以并发。

## 执行图

```markdown
GROUP-1:
  WU-01: A1.1-A1.3 | 标题: 写作域存储层（database / models / repository） |
    文件: nanobot/writing/{__init__,database,models,repository}.py, tests/writing/* |
    依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu |
    worktree_path: .harness-worktrees/zhixu/wt-writing-phase-a |
    branch: harness/wt-writing-phase-a | wu_skills: source-driven-development, test-driven-development, verification-before-completion

GROUP-2:
  WU-02: A1.4-A1.7 | 标题: 写作域传输层与 gateway 接线 |
    文件: nanobot/webui/writing_{services,api,routes}.py, tests/webui/test_writing_*.py,
          nanobot/webui/{settings_routes,ws_http,gateway_services}.py, nanobot/config/paths.py,
          tests/test_writing_paths.py |
    依赖: WU-01 | wu_type: feature | agent_role: coder | workspace_scope: wu | wu_skills: 同 WU-01 + api-and-interface-design
  WU-03: A2.1 | 标题: 前端类型层英文枚举与模板抽出 |
    文件: webui/src/components/creative/{types.ts,templates.ts,ArticleList.tsx,ArticleEditor.tsx,
          MaterialPanel.tsx,OutlinePanel.tsx,SuggestionPanel.tsx}, 10 个 locales/*/common.json,
          webui/src/tests/{article-model.test.ts,creative-workspace.test.tsx} |
    依赖: 契约 §3/§7（不需要后端） | wu_type: refactor | agent_role: coder | workspace_scope: wu |
    wu_skills: source-driven-development, verification-before-completion, frontend-ui-engineering

GROUP-3:
  WU-04: A2.2-A2.4 | 标题: 前端数据层（writing-api / 恢复稿 / article-model 双计数） |
    文件: webui/src/lib/{writing-api.ts,writing-draft-cache.ts},
          webui/src/components/creative/{article-model.ts,demo-data.ts},
          webui/src/tests/{writing-api.test.ts,writing-draft-cache.test.ts} |
    依赖: WU-03 | wu_type: feature | agent_role: coder | workspace_scope: wu |
    wu_skills: source-driven-development, test-driven-development, api-and-interface-design

GROUP-4:
  WU-05: A3.1-A3.4 | 标题: 创作空间接真实 API + 保存状态机 + 编辑器 + 面板 |
    文件: webui/src/components/creative/{CreativeWorkspace.tsx,use-article-save-flow.ts,ArticleEditor.tsx,
          MaterialPanel.tsx,OutlinePanel.tsx}, webui/src/{App.tsx,preview/PreviewApp.tsx},
          webui/src/tests/{creative-workspace-loading.test.tsx,article-save-flow.test.ts,
          article-editor-ime.test.tsx,creative-panels.test.tsx} |
    依赖: WU-02（接口可用）+ WU-04 | wu_type: feature | agent_role: implementer | workspace_scope: wu |
    wu_skills: source-driven-development, test-driven-development, incremental-implementation, frontend-ui-engineering
  WU-06: A3.5-A3.8 | 标题: 冲突处理 / 恢复 / 导出 / 删除复制防护 |
    文件: webui/src/components/creative/{ConflictDialog.tsx,ArticleRecoveryBanner.tsx,ArticleList.tsx,
          ArticleEditor.tsx,CreativeWorkspace.tsx}, webui/src/lib/writing-export.ts,
          webui/src/tests/{creative-conflict.test.tsx,creative-recovery.test.tsx,writing-export.test.ts,
          creative-workspace.test.tsx}, 10 个 locales/*/common.json |
    依赖: WU-05 完成并 commit（**串行**，与 WU-05 共享 ArticleEditor.tsx / CreativeWorkspace.tsx / locales） |
    wu_type: feature | agent_role: implementer | workspace_scope: wu |
    wu_skills: source-driven-development, test-driven-development, security-and-hardening, frontend-ui-engineering

GROUP-5（尾盘）:
  WU-07: A4.1 集体测试 | 依赖: WU-06 | agent_role: test-engineer | 产物: verifications/*-collective-test.md
  WU-08: A4.2 代码审查 | 依赖: WU-06 | agent_role: reviewer | 产物: reviews/*-code-review.md
  WU-09: A4.2 安全审查 | 依赖: WU-06 | agent_role: security-auditor | 产物: reviews/*-security-review.md
  WU-07 / WU-08 / WU-09 可并发扇出；Leader 汇总后写 execution-log + tracking（A4.3）。
```

依赖链：`WU-01 → WU-02`；`WU-03 → WU-04 → WU-05 → WU-06 → {WU-07 ∥ WU-08 ∥ WU-09}`。
两支起点 `WU-01` 与 `WU-03` 可同时启动（文件零交集：Python 侧 vs webui 侧）。

## 审查门

- **WU-01 → WU-02 放行条件**：`python -m pytest tests/writing -v` 全绿 + `ruff check nanobot/writing` 零错。repository 的 CAS/幂等语义是后端口径源头，未绿不许向上接。
- **WU-02 完成后 Leader 抽验**：对照契约 §4/§5/§6 逐条核对 `_SYSTEM_ROUTES`、`_WRITING_MUTATION_PATHS`、`_WEBUI_MUTATION_PATHS` 三张表的路径与 action 名**完全一致**；跑 `uv run --no-sync basedpyright nanobot/webui nanobot/writing`。
- **WU-03 完成后 Leader 抽验**：`npm run test`（含 `i18n.test.tsx:584` 的 locale 一致性）+ `npm run build`。这一步会改 5 个 creative 组件，回归面最大，必须单独把关。
- **WU-05 的 A-T03/A-T04 是硬门**：保存中继续输入不被覆盖、中文组合输入不被打断——两条测试不绿不许进 WU-06。
- **GROUP-5 启动前提**：WU-01~WU-06 全部 commit；任一失败阻塞整批。
- 批次收尾：collective-test + code-review + security-review 全 PASS 才允许合流（`routing.md` § 交付完成）。

## 文件冲突控制

1. `nanobot/writing/` + `tests/writing/` 仅 WU-01 写；`nanobot/webui/writing_*` 与三处接线文件仅 WU-02 写。
2. `webui/src/components/creative/*` 在 WU-03（类型迁移）与 WU-05/WU-06（功能）之间**串行**，不并发。
3. 10 个 `locales/*/common.json`：WU-03、WU-06 都会改，按上面的串行顺序执行；任何 worker 不得改他人 Task 的键。
4. `ArticleList.tsx`：WU-03（字面量）→ WU-06（A3.8），串行安全。
5. 冻结文件：`nanobot/memory/*`、`nanobot/session/manager.py`、`nanobot/webui/transcript.py`、`webui/src/lib/nanobot-client.ts`、`webui/src/lib/api.ts`（只 import，不改）——worker 需要动其中之一时**停下上报**，不自行修改。
6. `package.json` / `pyproject.toml`：本批次零新增依赖，出现改动需求即为偏差，上报。

## Skill 派发要求（worker prompt 内列「本 WU Skills」，必 Load）

- 全部编码 worker：`source-driven-development`（Step 0 先扫 `.ai-runtime-artifacts/`：本 spec + contract + plan 为真相源）、`verification-before-completion`、`incremental-implementation`；返回须含 `### Skills 使用`。
- WU-02 / WU-04：`api-and-interface-design`（契约对齐，不自行扩形状）。
- WU-01 / WU-05：`test-driven-development`（CAS 与保存状态机是纯逻辑，红-绿必须先于实现）。
- WU-03 / WU-05 / WU-06：`frontend-ui-engineering`（受控/非受控输入、焦点与键盘可达）。
- WU-06 + 尾盘 WU-09：`security-and-hardening`（Markdown 不可信渲染、URL 校验、作用域隔离、日志不落正文）。
- Leader：`orchestration`；提交按 `git-xywh` + `harness-kit/project.git.md`；尾盘按 `routing.md` § 批次收尾扇出。

## 共享文件约束

1. worker **不 commit / 不 push**（Leader 按 plan 各 Task 末尾的 message 统一提交）。
2. worker 的 cwd = `worktree_path`；主 checkout 不写业务代码（`routing.md` § 实现阶段）。
3. 不改 `.ai-runtime-artifacts/` 中非本 WU 产物；发现契约与代码现状冲突时写进返回的 `### 偏差`，不自行改契约。

## 工作树

- 路径：`.harness-worktrees/zhixu/wt-writing-phase-a`；分支：`harness/wt-writing-phase-a`；base：`feature/desktop-app` HEAD。
- 该目录当前不存在，且未确认是否已在 `.gitignore`；Leader 在 WORKTREE-INIT 时先核对 `.gitignore`，缺失则**单独**提一个 chore commit 补忽略项（不与业务 commit 混）。
- 主 checkout 存在用户未提交改动时，Leader 先处理（stash 需带唯一 tag 并可恢复），再派发。

## 变更记录

| 轮次 | 日期 | 变更摘要 |
| --- | --- | --- |
| 1 | 2026-10-12 | 初稿：A1~A4 拆为 9 个 WU，前端支与后端支并发 |

## Next

- 执行图确认 → 说「开始实现」或「并行执行」
- 只改 plan 任务、不改并行策略 → 仅改 `*-plan.md`
- 只改 WU 拆分 / 依赖 → 改本文件并告知 Leader 审阅
