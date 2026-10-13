---
artifact: implementation-dispatch
route: writing-plans
plan: .ai-runtime-artifacts/plans/2026-10-11-home-start-panel-and-plus-menu-parity-plan.md
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
  - harness-kit/.agents/skills/api-and-interface-design/SKILL.md
source:
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/contracts/2026-10-11-contract-home-start-panel.md
created_at: 2026-10-11
status: draft
approved: false
worktree: 默认 WORKTREE-INIT（委派 worker 前创建）；用户明示豁免时主 checkout
---

# 首页开始面板 + 「+」指令组 执行图

拟执行图；本轮未激活 orchestration、未创建 worktree、未派发 worker。用户说「开始实现」后由 Leader 读取 `dispatcher-workflow.md` 执行 §0。

## 执行组

| GROUP | WU | 角色/类型 | 依赖 | 文件范围（plan §2） | 完成标准 |
| --- | --- | --- | --- | --- | --- |
| 0 | 基线 | Leader / investigate | 用户批准 | 只读 + execution-log | 基线测试/构建/lint 退出码已记录；worktree 就绪 |
| 1 | A 后端终端 | coder / backend | 基线 | terminal_bridge、inbound/outbound、test_webui_terminal | 契约 §1 四帧四事件 pytest 全绿 |
| 1 | B 后端 feedback+files | coder / backend | 基线 | builtin.py、ws_http.py、workspace_files.py、两测试文件 | 契约 §2/§3 pytest 全绿 |
| 2 | C 前端开始面板 | coder / frontend | 契约（与 A/B 并行安全） | HomeStartPanel、App.tsx、api.ts、测试 | 三卡片+空态+⌘P/T vitest 全绿 |
| 2 | E 前端浏览器 | coder / frontend | 契约 | BrowserSection、browser-url、desktop IPC、测试 | 白名单用例表全过；拒嵌兜底可测 |
| 2 | F 前端「+」菜单 | coder / frontend | 契约 | ComposerPlusMenu、ThreadComposer、FeedbackPopover、9×locales | 指令组四行行为 vitest 全绿；locales 键表齐 |
| 3 | D 前端终端 | coder / frontend | A 完成 + E 组件存在 | TerminalSection、nanobot-client、types、useNanobotStream、SessionWorkbench | 多开/流式输出/退出码/上限 UI 全绿；build 体积记录 |
| 4 | G 尾盘 | Leader + reviewer + security-auditor | A-F | 全量验证 + 审查产物 | plan §4 命令全绿；spec §9 验收；两份 review + collective-test 落盘 |

GROUP-1 两 worker 并行；GROUP-2 三 worker 并行（文件互斥见 plan §2 冲突控制）；D 串行于 A、E 之后（需真实桥与 Browser 组件挂载）。

## Skill 派发要求（worker prompt 内列「本 WU Skills」，必 Load）

- 全部编码 WU：`source-driven-development`（契约文件为真相源）、`incremental-implementation`、`verification-before-completion`；返回须含 `### Skills 使用`。
- A/B/D/F：`test-driven-development`（先失败测试后实现，plan 各 Task Step 1-2 即 TDD 序）。
- C/E/F：`frontend-ui-engineering`；E 另加 `security-and-hardening`（URL 白名单）。
- A：`security-and-hardening`（新增执行面）。
- Leader：`orchestration`；提交时 `git-xywh` + `harness-kit/project.git.md`；尾盘按 routing § 批次收尾扇出。

## 共享文件约束

1. `App.tsx` 仅 C 写；`SessionWorkbench.tsx` 仅 D 写；`nanobot-client.ts`/`types.ts`/`useNanobotStream.ts` 仅 D 写；`i18n/locales/*/common.json` 仅 F 写（键表 plan §5，C/D/E 只引用键名）。
2. 契约（`contracts/2026-10-11-contract-home-start-panel.md`）冻结：任何签名改动先改契约并经 Leader 广播。
3. worker 不 commit/push（Leader 统一提交，按 plan 各 Task 末尾 commit message）；不改他人文件范围；不动 `.ai-runtime-artifacts/` 中非本 WU 产物。
4. 既有未提交工作区改动（ChatList、AddProjectDialog 等）不属于本批，不清理不覆盖。

## Next

用户说「开始实现」后激活编排；也可指定裁剪（如终端后置为第二批）。
