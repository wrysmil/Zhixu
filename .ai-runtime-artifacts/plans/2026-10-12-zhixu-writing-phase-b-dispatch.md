---
artifact: implementation-dispatch
route: writing-plans -> orchestration
batch: zhixu-writing-phase-b
plan: .ai-runtime-artifacts/plans/2026-10-12-zhixu-writing-phase-b-plan.md
spec: .ai-runtime-artifacts/specs/2026-10-10-zhixu-writing-capability-spec.md
contract: .ai-runtime-artifacts/contracts/2026-10-12-contract-writing-collaboration.md
depends_on_batch: zhixu-writing-phase-a
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
worktree: .harness-worktrees/zhixu/wt-writing-phase-b（用户确认后由 Leader 创建，base 见「工作树」节）
---

# 调度：zhixu-writing-phase-b

> 实施步骤以 **plan** 为准；本文件只描述并行 GROUP / WU 与派发。多轮审阅时优先改本文件。
> 阶段 A 的 dispatch（`2026-10-12-zhixu-writing-phase-a-dispatch.md`）是本批的前置：A 未合入前，本批任何 WU 都不启动。

## 并行前提（为什么前端不用等后端）

前端 WU 的测试全部 mock `fetch` 与 `WebUIMutationTransport`，只要**契约 §5（七个动作的 payload）、§6（错误 message）、§7（两种出站帧）**的形状确定就能开发；只有 B5.1 的 `@pytest.mark.live` 冒烟需要 gateway 真跑。因此「后端协作域」支与「前端协作层」支可全程并发。

第二条并发支：B3.1 出厂档案 + B3.2 四个写作 Skill 只碰 `nanobot/agents/catalog.py` 与 `nanobot/skills/article-*/`，与协作域文件零交集，可与 B1 同时启动。

## 执行图

```markdown
GROUP-0（前置，不派 worker）：
  Leader 确认阶段 A 已合入 base 分支，且 `nanobot/writing/{database,models,repository}.py` 存在、`tests/writing -q` 绿。
  不满足即阻塞整批——B1.1 的 schema v2 是在 A1.1 的 `DATABASE_VERSION` 之上加表，不是从零建库。

GROUP-1:
  WU-01: B1.1-B1.5 | 标题: 协作域纯逻辑（schema v2 / 模型投影 / 偏移 / 解析 / 上下文拼装） |
    文件: nanobot/writing/database.py, nanobot/writing/collab/{__init__,models,offsets,parser,context_builder}.py,
          tests/writing/{test_collab_schema,test_collab_models,test_collab_offsets,test_collab_parser,test_collab_context}.py |
    依赖: GROUP-0 | wu_type: feature | agent_role: coder | workspace_scope: wu |
    worktree_path: .harness-worktrees/zhixu/wt-writing-phase-b |
    branch: harness/wt-writing-phase-b |
    wu_skills: source-driven-development, test-driven-development, verification-before-completion
  WU-02: B3.1-B3.2 | 标题: writing-editor 出厂档案 + 四个内置写作 Skill |
    文件: nanobot/agents/catalog.py, tests/webui/test_agents_catalog.py,
          nanobot/skills/article-{rewrite,expand,outline,review}/SKILL.md, tests/agent/test_writing_skills.py |
    依赖: GROUP-0（与 WU-01 并发，文件零交集） | wu_type: feature | agent_role: coder | workspace_scope: wu |
    wu_skills: source-driven-development, test-driven-development, verification-before-completion

GROUP-2:
  WU-03: B1.6 | 标题: 协作仓储（任务/建议/撤销/绑定 + 在跑闸门 + 采纳撤销事务） |
    文件: nanobot/writing/repository.py（纯重构：`save_snapshot` 拆出 `save_snapshot_on` / `replay_response`）,
          nanobot/writing/collab/repository.py, tests/writing/test_collab_repository.py |
    依赖: WU-01 | wu_type: feature | agent_role: coder | workspace_scope: wu |
    wu_skills: 同 WU-01 + systematic-debugging（CAS/事务边界易错）
  WU-04: B2.1 | 标题: Skill 门与正文内联（四态） |
    文件: nanobot/writing/collab/skill_gate.py, tests/writing/test_collab_skill_gate.py |
    依赖: WU-01（`CollabLimitError` / `SkillContext`）；与 WU-02 联调留到 B5.1 |
    wu_type: feature | agent_role: coder | workspace_scope: wu | wu_skills: 同 WU-01

GROUP-3:
  WU-05: B2.2 | 标题: WritingCollabRunner（执行 / 状态跃迁 / 停止 / 重启收敛） |
    文件: nanobot/writing/collab/runner.py, tests/writing/test_collab_runner.py |
    依赖: WU-03 + WU-04 | wu_type: feature | agent_role: implementer | workspace_scope: wu |
    wu_skills: source-driven-development, test-driven-development, verification-before-completion, systematic-debugging
  WU-06: B4.1-B4.2 | 标题: 前端协作数据层与两个出站事件分支 |
    文件: webui/src/components/creative/{collab-types.ts,collab-labels.ts}, webui/src/lib/writing-collab-api.ts,
          webui/src/lib/{types.ts,nanobot-client.ts},
          webui/src/tests/{writing-collab-api.test.ts,writing-collab-events.test.ts} |
    依赖: 契约 §5/§6/§7（不需要后端）；可与 WU-05 并发 | wu_type: feature | agent_role: coder | workspace_scope: wu |
    wu_skills: source-driven-development, api-and-interface-design, verification-before-completion

GROUP-4:
  WU-07: B2.3 | 标题: 出站事件通道（bus → projector → WebSocket）+ runner 顶部 import 收敛 |
    文件: nanobot/bus/outbound_events.py, nanobot/webui/outbound_projection.py,
          nanobot/channels/websocket/runtime.py, nanobot/writing/collab/runner.py（仅两处函数级 import 上移）,
          tests/webui/test_writing_events.py |
    依赖: WU-05（**串行**，与 WU-05 共享 runner.py） | wu_type: feature | agent_role: coder | workspace_scope: wu |
    wu_skills: 同 WU-05
  WU-08: B4.3 | 标题: useWritingCollaboration 任务状态机与刷新恢复 |
    文件: webui/src/components/creative/use-writing-collaboration.ts, webui/src/tests/writing-collaboration-flow.test.tsx |
    依赖: WU-06 | wu_type: feature | agent_role: implementer | workspace_scope: wu |
    wu_skills: source-driven-development, test-driven-development, frontend-ui-engineering
  WU-09: B4.5 | 标题: 本地差异预览 + 建议卡片 + SuggestionPanel 重写 |
    文件: webui/src/components/creative/{writing-diff.ts,SuggestionCard.tsx,SuggestionPanel.tsx},
          webui/src/tests/{writing-diff.test.ts,suggestion-panel.test.tsx} |
    依赖: WU-06；与 WU-08 并发（文件零交集） | wu_type: feature | agent_role: coder | workspace_scope: wu |
    wu_skills: source-driven-development, test-driven-development, frontend-ui-engineering

GROUP-5:
  WU-10: B2.4 + B2.6 | 标题: 协作 payload/handler（七个动作，含 collaborate.stop）+ 会话打标与判据 |
    文件: nanobot/writing/repository.py（`record_request_on`）, nanobot/writing/collab/repository.py（`reject_suggestion` / `is_bound_session`）,
          nanobot/webui/{writing_collab_api,writing_collab_routes}.py, nanobot/session/collab_mark.py,
          tests/writing/test_collab_reject.py, tests/webui/test_writing_collab_routes.py, tests/session/test_collab_mark.py |
    依赖: WU-07（runner 的 `start`/`stop` 与事件已可用）+ WU-03；与 WU-08/09 并发 |
    wu_type: feature | agent_role: implementer | workspace_scope: wu |
    wu_skills: source-driven-development, test-driven-development, api-and-interface-design, security-and-hardening, verification-before-completion
  WU-11: B4.4 | 标题: CollabPanel 协作发起面板（动作选择 / 档案与 Skill / 素材勾选 / 停止） |
    文件: webui/src/components/creative/CollabPanel.tsx, webui/src/tests/collab-panel.test.tsx |
    依赖: WU-08；与 WU-10 并发 | wu_type: feature | agent_role: implementer | workspace_scope: wu |
    wu_skills: source-driven-development, test-driven-development, frontend-ui-engineering

GROUP-6:
  WU-12: B2.5 | 标题: 路由表 / WS-only 门禁 / gateway 装配与启动收敛 |
    文件: nanobot/webui/{settings_routes,ws_http,gateway_services}.py, tests/webui/test_writing_collab_wiring.py |
    依赖: WU-10 | wu_type: feature | agent_role: coder | workspace_scope: wu |
    wu_skills: source-driven-development, api-and-interface-design, verification-before-completion
  WU-13: B3.3 | 标题: 四道全局记忆门 + 判据注入上移 |
    文件: nanobot/agent/loop.py, nanobot/agent/autocompact.py, nanobot/agent/hooks/memory_extraction.py,
          nanobot/cli/gateway_runtime.py, tests/memory/test_collab_memory_gates.py |
    依赖: WU-10（`collab_gate_predicate`）；与 WU-12 并发（`gateway_runtime.py` ≠ `gateway_services.py`） |
    wu_type: feature | agent_role: implementer | workspace_scope: wu |
    wu_skills: source-driven-development, test-driven-development, systematic-debugging, verification-before-completion
  WU-14: B4.6 | 标题: 选区上报 + 建议动作接线 + 脏保护对话框 + 工作区组合 |
    文件: webui/src/components/creative/{ArticleEditor.tsx,use-suggestion-actions.ts,ConflictDialog.tsx,
          use-article-save-flow.ts,CreativeWorkspace.tsx},
          webui/src/tests/{editor-selection.test.tsx,suggestion-actions.test.ts,collab-conflict-dialog.test.tsx} |
    依赖: WU-10 + WU-11 + WU-09（**串行汇聚点**：五个 creative 组件与阶段 A 产物同文件） |
    wu_type: feature | agent_role: implementer | workspace_scope: wu |
    wu_skills: source-driven-development, test-driven-development, incremental-implementation, frontend-ui-engineering, security-and-hardening

GROUP-7:
  WU-15: B4.7 | 标题: App 挂载会话跳转 + 10 个 locale 全量同步 |
    文件: webui/src/App.tsx, webui/src/components/creative/CreativeWorkspace.tsx（props 增 `onOpenChat`）,
          10 个 webui/src/i18n/locales/*/common.json, webui/src/tests/collab-open-chat.test.tsx |
    依赖: WU-14 完成并 commit（**串行**，共享 CreativeWorkspace.tsx） |
    wu_type: feature | agent_role: coder | workspace_scope: wu |
    wu_skills: source-driven-development, verification-before-completion, frontend-ui-engineering
  WU-16: B3.4 | 标题: B-T12 兼容回归记录 |
    文件: .ai-runtime-artifacts/verifications/2026-10-12-zhixu-writing-phase-b-bt12-regression.md |
    依赖: WU-12 + WU-13 | wu_type: test | agent_role: test-engineer | workspace_scope: wu |
    wu_skills: verification-before-completion, systematic-debugging

GROUP-8（尾盘，扇出并发）:
  WU-17: B5.1 集体测试 + 真实模型冒烟 | 依赖: WU-14/WU-15/WU-16 | agent_role: test-engineer |
    文件: tests/writing/test_collab_real_model_smoke.py,
          .ai-runtime-artifacts/verifications/2026-10-12-zhixu-writing-phase-b-collective-test.md
  WU-18: B5.2 代码审查 | 依赖: WU-15 | agent_role: reviewer |
    产物: .ai-runtime-artifacts/reviews/2026-10-12-zhixu-writing-phase-b-code-review.md
  WU-19: B5.2 安全审查 | 依赖: WU-15 | agent_role: security-auditor |
    产物: .ai-runtime-artifacts/reviews/2026-10-12-zhixu-writing-phase-b-security-review.md
  WU-17 / WU-18 / WU-19 可并发；Leader 汇总后写 B5.3（execution-log + decisions + tracking）。
```

依赖链（主干）：`GROUP-0 → WU-01 → WU-03 → WU-05 → WU-07 → WU-10 → {WU-12 ∥ WU-13}`；
前端支：`WU-06 → {WU-08 → WU-11 ∥ WU-09} → WU-14 → WU-15`；
档案支：`WU-02`（独立，最终由 WU-17 联调）。
三支可同时启动：`WU-01`、`WU-02`、`WU-06` 文件零交集。

## 审查门

- **WU-01 → WU-03 放行条件**：`python -m pytest tests/writing -q` 全绿 + `ruff check nanobot/writing` 零错。`offsets.py` 的定位口径（B-05）和 `parser.py` 的校验（B-T10）是建议能否落对位置的唯一源，未绿不许向上接。
- **WU-03 完成后 Leader 抽验**：`python -m pytest tests/writing -q` 里阶段 A 的 `test_repository*.py` **一条不许变红**（B1.6 声称是纯重构，用 diff 与测试结果双向取证，不采信 worker 自述）。
- **WU-05 的 B-T01/B-T05/B-T11 是硬门**：在跑闸门（同文章第二个任务 409）、状态跃迁、重启收敛（`reconcile_on_boot`）三条不绿不许进 WU-07。
- **WU-07 完成后 Leader 抽验**：出站帧形状逐字对照契约 §7（`body` 中 `None` 键不下发）；`uv run --no-sync basedpyright nanobot/bus nanobot/webui nanobot/channels/websocket`。
- **WU-10 完成后 Leader 抽验**：`WRITING_COLLAB_ACTION_NAMES` 七个动作与契约 §5 逐字一致；`writing.collaborate` 与 `writing.collaborate.stop` 两个循环绑定动作在 sync 入口 `handle()` 必须返回 500，且 `handle_async()` 里没有对它们的 `asyncio.to_thread` 包裹。这是本批最容易改错的一处（改成 `to_thread` 会让 `create_task` 在工作线程 RuntimeError）。
- **WU-10 → WU-13 放行条件**：`collab_gate_predicate` 自身容错（异常返回 `False`），四个调用点**裸调用**；出现调用点再包 `try/except` 即打回。
- **WU-14 前 Leader 确认**：`to_view_dict()` 字段与 `collab-types.ts` 逐字一致（grep 对照取证）。
- **WU-15 完成后**：`cd webui && bun run test`（含 `i18n.test.tsx` 的 locale parity：missing / extra / interpolationMismatches 三项全空）+ `bun run build`。
- **GROUP-8 启动前提**：WU-01~WU-16 全部 commit；任一失败阻塞整批。
- 批次收尾：collective-test + code-review + security-review 全 PASS 才允许合流（`routing.md` § 交付完成）。B5.1 至少跑成一次真实模型调用，否则 B-03/B-04 只能记「未验证」。

## 文件冲突控制

1. `nanobot/writing/collab/` 整目录按 WU-01 → WU-03 → WU-04 → WU-05 → WU-07 → WU-10 **串行**，任一 worker 不得改前序 WU 已 commit 的函数签名；需要改时写进 `### 偏差` 上报。
2. `nanobot/writing/repository.py` 被 WU-03（B1.6 重构）与 WU-10（`record_request_on`）两次修改，按上面的串行顺序执行，不并发。
3. `webui/src/components/creative/CreativeWorkspace.tsx` 被 WU-14 与 WU-15 修改，串行；`ArticleEditor.tsx` 只 WU-14 写（阶段 A 的 WU-05/06 已合入，本批不再并发改它）。
4. 10 个 `webui/src/i18n/locales/*/common.json`：本批**只有 WU-15** 写，其余前端 WU 需要的文案在自己的组件里用 `defaultValue` 兜底，不得提前改 locale。
5. 冻结文件（只 import，不改；需要改即停下上报）：`nanobot/memory/*`、`nanobot/session/manager.py`（B2.6 只新建 `collab_mark.py`，不碰 manager）、`nanobot/webui/settings_contracts.py`、`webui/src/lib/api.ts`、`nanobot/config/paths.py`、A 批的 `webui/src/lib/writing-api.ts`。
   例外说明：`webui/src/lib/nanobot-client.ts` 与 `webui/src/lib/types.ts` 在 A 批是冻结的，本批由 **WU-06 独占**新增两个事件分支——WU-06 之后任何 worker 想再改它们，停下上报。
6. `package.json` / `pyproject.toml`：本批次零新增依赖，出现改动需求即为偏差，上报。
7. `pytest.ini` / pyproject 的 markers：B5.1 需要 `live` marker。若当前没有注册，Leader 单独提一个 chore commit 补 `markers = ["live: ..."]`，不与业务 commit 混。

## Skill 派发要求（worker prompt 内列「本 WU Skills」，必 Load）

- 全部 worker：`source-driven-development`（Step 0 先扫 `.ai-runtime-artifacts/`：本 spec + B 契约 + B plan + A plan 的接口小节为真相源）、`verification-before-completion`、`incremental-implementation`；返回须含 `### Skills 使用`。
- WU-01 / WU-03 / WU-05 / WU-08 / WU-10 / WU-14：`test-driven-development`（红-绿先于实现；CAS、在跑闸门、状态机三处无捷径）。
- WU-06 / WU-10 / WU-12：`api-and-interface-design`（契约 §5/§6/§7 对齐，不自行扩形状、不新增动作名）。
- WU-08 / WU-09 / WU-11 / WU-14 / WU-15：`frontend-ui-engineering`（受控 textarea 与选区、IME、焦点可达、按钮禁用态）。
- WU-10 / WU-14 + 尾盘 WU-19：`security-and-hardening`（不可信网页正文与素材注入声明、`article_id`/`chat_id`/`client_request_id` 校验覆盖到 stop 动作、协作档案 `tools` 封闭、日志与产物不落正文/key）。
- WU-05 / WU-13：`systematic-debugging`（异步取消与记忆门是最容易「看起来过了」的两处）。
- Leader：`orchestration`；提交按 `git-xywh` + `harness-kit/project.git.md`；尾盘按 `routing.md` § 批次收尾扇出。

## 共享文件约束

1. worker **不 commit / 不 push**（Leader 按 plan 各 Task 末尾的 message 统一提交）。
2. worker 的 cwd = `worktree_path`；主 checkout 不写业务代码（`routing.md` § 实现阶段）。
3. 不改 `.ai-runtime-artifacts/` 中非本 WU 产物；发现契约与代码现状冲突时写进返回的 `### 偏差`，不自行改契约。
4. 每个 WU 返回须带「跑了哪条命令 / 输出尾部 / 未跑的原因」；无证据不得声称完成。

## 工作树

- 路径：`.harness-worktrees/zhixu/wt-writing-phase-b`；分支：`harness/wt-writing-phase-b`。
- base：优先阶段 A 合入后的分支 HEAD；A 尚未合入时，base = `harness/wt-writing-phase-a` HEAD（B1.1 的 schema v2、B4.6 的 `use-article-save-flow.ts` 都直接依赖 A 的产物，base 选错会全程冲突）。
- 该目录当前不存在，且未确认是否已在 `.gitignore`；Leader 在 WORKTREE-INIT 时先核对 `.gitignore`，缺失则**单独**提一个 chore commit 补忽略项。
- 主 checkout 存在用户未提交改动时，Leader 先处理（stash 需带唯一 tag 并可恢复），再派发。
- A、B 两批若同时在跑，**不允许**共用一个工作树或一个分支。

## 变更记录

| 轮次 | 日期 | 变更摘要 |
| --- | --- | --- |
| 1 | 2026-10-12 | 初稿：B1~B5 拆为 19 个 WU；后端协作支 / 前端协作支 / 档案与 Skill 支三支并发；B3.4 提前到 GROUP-7 |

## Next

- 执行图确认 → 说「开始实现」或「并行执行」（前提是阶段 A 已合入）
- 只改 plan 任务、不改并行策略 → 仅改 `2026-10-12-zhixu-writing-phase-b-plan.md`
- 只改 WU 拆分 / 依赖 → 改本文件并告知 Leader 审阅
