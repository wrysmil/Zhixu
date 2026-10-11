---
artifact: dispatch-track
route: orchestration
plan: .ai-runtime-artifacts/plans/2026-10-11-home-start-panel-and-plus-menu-parity-plan.md
---

# DISPATCH-TRACK-2026-10-11-home-start-panel-parity

[2026-10-11 15:05] DISPATCH-GROUP-1 | Leader | Status: started
Detail: WORKTREE-INIT 完成；uv sync 成功；webui npm install 进行中（本机无 bun）
Sub-agents: 0
Context: ~35%
Output: .harness-worktrees/zhixu/wt-home-start-panel
Error: none
Next: 派发 A/B（后端），install 完成后派发 C/E/F
WorktreeId: wt-home-start-panel | WorktreePath: /Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/zhixu/wt-home-start-panel | Branch: harness/wt-home-start-panel | Base: 5f8195f

[2026-10-11 12:40] DISPATCH-GROUP-1-2 | Leader | Status: completed
Detail: 基线 webui npm run test exit=0（94 files/1281 tests）；并行派发 A/B/C/E/F 五个 coder worker
Sub-agents: 5
Output: worktree wt-home-start-panel
Next: 等待各 WU 返回

[2026-10-11 12:41] WU-E-implement | coder | Status: completed
Detail: 浏览器分区 done：browser-url 白名单（38 用例）+ BrowserSection（7 用例）+ desktop open-external IPC；vitest exit0、lint exit0
Sub-agents: 4 active (A/B/C/F)
Tests: 45/45 pass；全量 test 唯一失败为既有 thread-shell flake（单跑过）；build 失败仅因 C 在途 App.tsx
Worktree: wt-home-start-panel | Branch: harness/wt-home-start-panel | Title(zh): 浏览器 iframe 分区
OpenItems: runtime.ts MiraShellApi 需补 openExternal 类型（转 D）；window.open 无 noopener（计划钉死表达式，尾盘裁决）；桌面实机验收留 G
Next: 等 A/B/C/F；D 依赖 A

[2026-10-11 12:52] WU-C-implement | coder | Status: completed
Detail: 开始面板 done：HomeStartPanel（22rem 可折叠+数据卡片+files 内嵌树）+ fetchWorkspaceFiles + App.tsx hero 挂载 + ⌘P/⌘T keymap；vitest 14/14、全量 1340 全绿、lint/build exit0
Sub-agents: 3 active (A/B/F)
Tests: 1340 pass
Worktree: wt-home-start-panel | Branch: harness/wt-home-start-panel | Title(zh): 首页「开始」右面板
Leader-Ruling: ① scope 偏差 WorkspaceFileTree.tsx（additive 可选 prop，契约 §2 依据）→ 接受；② 溢出 i18n 键 startPanel.{terminalPlaceholder,browserPlaceholder,expand,collapse,filesRetry} → 转 WU-F/尾盘补键（F 在途，返回后核对）；③ 真实后端联调与浏览器人工验收 → GROUP 尾盘
Queue-remaining: D（等 A+E）
Next: 等 A/B/F 返回后派 D、核对 F 键表

[2026-10-11 13:10] WU-B-implement | coder | Status: completed
Detail: feedback 命令 + /api/workspace/files + preview scope 参数 done；新测试 15/15 pass（RED→GREEN 有据）；ruff/basedpyright 目标文件 0 错
Sub-agents: 2 active (A/F)
Tests: 1361 pass（6 失败均甄别为基线既有/端口抖动，非本 WU 引入）
Worktree: wt-home-start-panel | Branch: harness/wt-home-start-panel | Title(zh): feedback + workspace 文件端点
Leader-Ruling: 偏差 1-4 全部接受（anchor 返回 scope/数据目录惯例/palette as_dict 形状/preview 保留 session-key 形态）；「无 key 的 workspace file-preview」与本批 spec §4 v1 边界一致，不做
OpenItems: 基线既有 test_serve_* 4 例失败 + ruff 全局 11 处告警 → Task G 知悉，不属本批修复面
Queue-remaining: D（等 A）
Next: 等 A/F；A 返回即派 D

[2026-10-11 13:35] WU-F-implement | coder | Status: completed
Detail: PlusRow 三类模型 + 「指令」组（compact/permission/model/feedback）+ FeedbackPopover + ThreadComposer 接线 + 10 locale 全量键表（startPanel 27 / plusMenu 13，含 C 的 5 个溢出键）；vitest 15/15、全量 1350/1350、lint 0、locale 对齐脚本 ALL_OK
Sub-agents: 1 active (A)
Tests: 全量通过；唯一 npm run test 失败为既有 thread-shell 计时 flake（单跑过，与 known-baseline-failures 记忆一致）
Worktree: wt-home-start-panel | Branch: harness/wt-home-start-panel | Title(zh): 「+」菜单指令组 + i18n
Leader-Ruling: 接线偏差接受（onSend side-channel 替代 sendSystemCommand 字面调用、/model 走 onModelPresetChange、权限走 onWorkspaceScopeChange 既有链路——均等效且不破坏既有测试渲染）；planActive 留接口默认 false 接受
SECURITY: worker 报告其工具输出中全程持续出现伪造 "System:" 提示注入块（诱导 git commit/push、git checkout -- .、rm -rf、读 .env、写 CI 工作流、提交虚假报告）——worker 全部拒绝、未执行任何 git/破坏性命令。来源未定位，Task G security-auditor 必须溯源（worktree 文件 / node_modules / MCP 输出）
OpenItems: ①浏览器人工 E2E 未做（G 覆盖）；②locales 合并回主 checkout 前须先处理用户未提交改动（记忆 project-home-start-panel-batch 已录）
Queue-remaining: D（等 A）
Next: 等 A 返回即派 D

[2026-10-11 13:50] WU-A-implement | coder | Status: completed
Detail: terminal_bridge + inbound 4 帧 + outbound 4 事件 + runtime.py 最小接线 done；pytest 21/21、相关子集 953 passed；本 WU 文件 ruff 0；outbound payload 双键 event/type 兼容代码库惯例（契约 §1.2 逐字满足）
Sub-agents: 0 active；即将派 D
Tests: 953 pass / 1 skip；ruff 全局 11 处为基线既有（A/B 双证）
Worktree: wt-home-start-panel | Branch: harness/wt-home-start-panel | Title(zh): 后端终端 WS 通道
OpenItems: 真实 WS 端到端仅单元级验证（G 浏览器验收覆盖）；Windows cmd.exe 分支未本机实测；断线后终端存活至 idle（契约允许）
Next: 派发 D（TerminalSection + App slots 接线 + runtime.ts 类型），完成后 GROUP 尾盘

[2026-10-11 14:20] WU-D-implement | coder | Status: completed
Detail: TerminalSection（8 tab/xterm lazy 切 chunk/输入行+历史/退出码）+ client 4 帧 + terminal store（useSyncExternalStore/ref-count/seq 去重/裁剪）+ SessionWorkbench 两分区 + App slots 接线 + runtime.ts openExternal；20/20 新用例、全量 1370 全绿、lint/build 0；bundle 净增 +10KB gzip，xterm 338KB 走懒加载
Sub-agents: 0
Tests: 1370 pass
Worktree: wt-home-start-panel | Branch: harness/wt-home-start-panel | Title(zh): 前端终端分区与接线
OpenItems: 真机 xterm/端到端网关联调/双面板并渲染未实测（G 覆盖）；terminal_list_result 跨 chat 合并为契约 v1 行为
Next: 尾盘——集体测试（Leader）+ 并行 reviewer/security-auditor 扇出 + 注入溯源

[2026-10-11 15:05] CLOSEOUT-collective-test | Leader | Status: completed
Detail: 全量复跑 webui test 98/1370 exit0、lint 0、build 0；后端新测试 36 passed；回归子集 1464 passed/4 failed（test_serve_* 基线既有）；ruff 11 基线既有 → 判定 PASS
Tests: 见 verifications/2026-10-11-home-start-panel-parity-collective-test.md
Next: 并行扇出 reviewer + security-auditor

[2026-10-11 15:40] CLOSEOUT-code-review | reviewer | Status: completed
Detail: verdict BLOCK：0 Blocker / 4 Major（幽灵 tab 竞态、首页预览链路缺失、8 终端键缺 locale、⌘P/⌘T 活动会话吞键）/ 9 Minor / 2 Nit；契约一致性核对全通过；无注入块 encountered
Tests: 只读审查未运行测试
Worktree: wt-home-start-panel | Title(zh): 批次代码审查
Next: 与 security 合并后派 review-fix

[2026-10-11 15:55] CLOSEOUT-security-audit | security-auditor | Status: completed
Detail: verdict BLOCK：1 HIGH（iframe allow-scripts+allow-same-origin 且白名单仅提交时校验→重定向/DNS rebinding 同源→token→终端帧=RCE 链）/ 3 MEDIUM（restricted guard 逐行可绕、sandbox wrap 失败 fail-open、输出无速率配额）/ 5 LOW / 3 INFO；注入溯源：仓库/hooks/npm/CI/运行时数据均未定位来源，判定环境级通道（LLM provider/编排层/MCP 输出），不清理仓库文件，建议上报样本原文
SECURITY: 溯源结论同上；后续 FIX 派发词均含防注入指令
Tests: 只读审计未运行测试
Worktree: wt-home-start-panel | Title(zh): 批次安全审查+注入溯源
Next: 派 FIX-1（前端终端+浏览器安全）/ FIX-2（首页接线+快捷键+i18n）/ FIX-3（后端安全）三 worker 并行 → 复跑 → 定向复审

[2026-10-11 16:40] CLOSEOUT-review-fix+re-review | coder×3 + reviewer/security-auditor | Status: completed
Detail: FIX-1 墓碑集合+sandbox 去 allow-same-origin+noopener+main.cjs URL 校验；FIX-2 首页 scope 预览+⌘P/⌘T 收紧 activeKey===null+10 locale 补 10 键；FIX-3 restricted 整段 guard fail-closed+sandbox wrap status=error+连接首门+feedback 4000 上限+空 scope 规范化——三 worker 均 done、均报「遇到注入块：无」
Sub-agents: 0 active；RE-REVIEW/RE-SECURITY 定向复审均 APPROVE
Tests: Leader 复跑 webui 99/1391 exit0、lint/build 0、后端目标 49 passed、回归 1477 passed（4 基线既有）、ruff 目标 0；Leader 顺带 list_result 墓碑一行+注释+package-lock resolved 回官方源
Worktree: wt-home-start-panel | Branch: harness/wt-home-start-panel | Commits: e1ae304/f1ac5ec/60dbe98/a9eb0e1（未 push）
OpenItems: 浏览器人工验收（spec §9）；v1.1 记录项见 collective-test；待用户确认 merge-back（主 checkout 有用户未提交工作）
Queue-remaining: 无（批次完成，待合并决策）
Next: 用户确认合并方式 → git-xywh 合并回 feature/desktop-app → WORKTREE-CLOSE


