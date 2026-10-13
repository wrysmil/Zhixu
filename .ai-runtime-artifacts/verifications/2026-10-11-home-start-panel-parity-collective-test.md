---
artifact: collective-test
route: verification-before-completion
skills: [verification-before-completion]
source:
  - .ai-runtime-artifacts/plans/2026-10-11-home-start-panel-and-plus-menu-parity-plan.md
  - .ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-11-home-start-panel-parity.md
created_at: 2026-10-11
worktree: /Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/zhixu/wt-home-start-panel
branch: harness/wt-home-start-panel
base: 5f8195f
verdict: PASS（测试/构建/lint 全绿；审查 BLOCK→review-fix 轮后双复审 APPROVE）
---

# 首页开始面板 +「+」菜单批次 · 集体测试记录

## 检查表执行结果

| 检查项 | 命令（cwd=worktree） | 退出码 | 结果 |
| --- | --- | --- | --- |
| 前端全量测试 | `webui: npm run test` | 0 | 98 files / 1370 tests 全过（含本批新增 home-start-panel 14、browser-url 38、browser-section 7、terminal-section 20、composer-plus-menu 9 新用例） |
| 前端 lint | `webui: npm run lint` | 0 | 无告警 |
| 前端构建 | `webui: npm run build` | 0 | xterm 独立 lazy chunk，主包无回退 |
| 后端新测试 | `uv run --no-sync python -m pytest tests/test_webui_terminal.py tests/test_command_feedback.py tests/test_webui_workspace_files.py -v` | 0 | 36 passed（21+5+10），RED→GREEN 有据 |
| 后端回归子集 | `uv run --no-sync python -m pytest tests/ -k "webui or terminal or exec_session or command or workspace" -q` | 1 | 1464 passed / 4 failed；4 例失败均为 `test_serve_*` 基线既有（见记忆 project-known-baseline-failures），本批未触及相应路径 |
| ruff | `uv run --no-sync ruff check nanobot/` | 1 | 11 处告警均为基线既有（gateway_runtime、memory/*），本批新增文件 0 告警 |
| basedpyright（目标文件） | 本批改动 Python 文件 | 0 | 0 错 |

## 并行批次期间已知瞬时态（已甄别，非缺陷）

- WU-E 期间一次 `npm run build` exit=2：WU-C 在途 App.tsx JSX 未闭合所致；C 完成后全量 build 恢复 exit=0。
- `thread-shell.test.tsx` 全量偶发计时 flake（单跑必过）：与基线既有清单一致，归因为基线。

## 未实测面（交接给审查与人工验收）

- 真实浏览器端到端未做：xterm 渲染、iframe 拒载 4s 启发、桌面 `mira:open-external` IPC 实机行为。
- spec §9 五条人工验收口径待 gateway 启动后走查（见记忆 project-startup-zhixu）。

## 结论

测试/构建/静态检查维度 **PASS**。尾盘审查门禁：code-review 与 security-auditor 均判 BLOCK（详见 reviews/ 同批两产物），按 dispatcher-workflow「任一审查 BLOCK → 对应 WU 修复后重新审查」进入 review-fix 轮。

## review-fix 轮复跑（2026-10-11 第二轮）

FIX-1（终端 store 墓碑/browser sandbox/desktop IPC）+ FIX-2（首页预览 scope/⌘P⌘T 收紧/10 locale 补键）+ FIX-3（restricted 整段 guard/sandbox fail-closed/连接首门/feedback 上限/空 scope）交付后 Leader 复跑：

| 检查项 | 退出码 | 结果 |
| --- | --- | --- |
| `npm run test`（全量） | 0 | 99 files / 1391 tests 全过（含 terminal-store 新增 11 用例） |
| `npm run lint` / `npm run build` | 0 / 0 | 全绿 |
| 后端目标 3 文件 pytest | 0 | 49 passed |
| 后端回归子集 | 1 | 1477 passed / 4 failed（均 test_serve_* 基线既有） |
| ruff（本批改动 7 py 文件） | 0 | All checks passed |

定向复审：RE-REVIEW **APPROVE**、RE-SECURITY **APPROVE**（残留均 LOW，记 v1.1）。Leader 顺带落地 list_result 墓碑一行修复 + 注释修正 + package-lock resolved 回官方源。

批次已按 4 个逻辑 commit 提交至 `harness/wt-home-start-panel`（e1ae304 后端终端通道 / f1ac5ec feedback+files 端点 / 60dbe98 首页面板+终端+浏览器+desktop / a9eb0e1「+」菜单+feedback UI+i18n），未 push。

## 仍未实测面（人工验收）

真实浏览器端到端五口径（spec §9）：gateway 启动后首页面板文件/终端/浏览器、「+」菜单指令组、feedback 落盘。桌面 IPC 与 iframe 实机行为。
