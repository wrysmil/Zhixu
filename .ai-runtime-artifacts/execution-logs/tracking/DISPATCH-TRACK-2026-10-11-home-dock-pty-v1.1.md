# DISPATCH-TRACK：home-dock-pty-terminal v1.1（append-only）

plan: .ai-runtime-artifacts/plans/2026-10-11-home-dock-pty-terminal-v1.1-plan.md
dispatch: .ai-runtime-artifacts/plans/2026-10-11-home-dock-pty-terminal-v1.1-dispatch.md

## INIT-2026-10-11

- 双 worktree 创建于 feature/desktop-app@83ce1ca：
  - wt-home-dock-pty-backend（harness/wt-home-dock-pty-backend）
  - wt-home-dock-pty-webui（harness/wt-home-dock-pty-webui）
- spec 计划期修正三处：§3.3 接管改 sessionStorage+terminal_start 重绑（首页默认 chat 每连接随机 uuid，list 不可见旧终端）；§4.2 增 already_running=接管语义与 shells 端点鉴权收口（check_api_token）；§4.4 本批取 R1 保守形态（restricted 保持管道守卫，PTY 仅 full-access）。
- 派发：Task1（backend worker）∥ Task6（webui worker），后台并行。

## TASK6-DONE-2026-10-11

- worker 完成 Task 6（webui worktree commit 47fecf2）：dock-store.ts 189 行 + 4 组用例全绿（先红后绿）+ addon-fit@^0.11.0 + build 5.54s。
- 编排者抽验：接口名与计划 Interfaces 一致；偏离点= persistedTerminals 返回原始清单（不滤已开 tabs），以测试为契约裁决——Task 9 接管条渲染时需自行排除 snapshot 已存在 tab id（已写入 Task 9 派单要点）。
- 派发 Task 7（TerminalSurface）。

## TASK1-DONE 2026-10-11 (Track1)

- Task 1 PTY 终端桥完成，commit `e977fae`（harness/wt-home-dock-pty-backend）。
- 抽验：`pytest tests/test_webui_pty_terminal.py tests/test_webui_terminal.py -q` 主会话复跑 **39 passed**；ruff 三文件通过；公开接口签名与计划 Interfaces 一致。
- R2 探针结论：`pty.openpty + create_subprocess_exec + loop.add_reader` 在 macOS kqueue 下一转绿，无 flaky。
- worker 偏离 2 处（均已接受）：删未用 `Callable` import；`_finalize_exit` 排水前置 `O_NONBLOCK`（修复计划原码 kill 路径 `os.read` 永久阻塞冻结事件循环的真实死锁，属必要修正，Task 13 安全评审需复核该点）。
- 下一步：Track1 派发 Task 2（`GET /webui/terminal/shells`）。

## TASK2-DONE 2026-10-11 (Track1)

- Task 2 `GET /webui/terminal/shells` 完成，commit `49e976d`（+ws_http.py 10 行、+测试 52 行）。
- 抽验：主会话复跑 shells/workspace_files/PTY 三文件 **21 passed**；ruff 通过；红灯→绿灯过程符合 TDD。
- worker 偏离 2 处（已接受）：计划测试里 `shutil.which` stub 键写错（应为短名 `"zsh"` 而非路径），按计划自带备注以测试为准修正，生产代码零调整；200 用例补 `set(entry)=={"id","label","path"}` 形状断言。
- 下一步：Track1 派发 Task 3（管道桥 terminal_bridge.py 升级：shell 参数/pty:false/接管/宽限元数据/resize no-op）。

## TASK3-DONE 2026-10-11 (Track1)

- Task 3 管道桥 v2 完成，commit `1b3a9c2`（terminal_bridge.py +88/-13、test_webui_terminal.py +71）。
- 抽验：主会话复跑 **42 passed**（含 Task 1 PTY 8 项回归）；ruff 两文件通过；既有测试零适配（新参数 keyword-only 可选、返回 dict 仅增 `pty` 键）。
- 接口对齐点：`start(shell=None)`、`"pty": False`、接管 rebind→`already_running`、`_exited` 宽限元数据、`exited_owner/has_live/resize(no-op)`、kill 宽限幂等、`list_visible` 合并 exited 条目。
- worker 偏离 1 处（已接受）：`aclose` 末尾 `_exited.clear()`——对齐 PtyTerminalBridge 行为，防陈旧归属。
- 下一步：Track1 派发 Task 4（TerminalSupervisor 门面 + inbound_commands `_terminal_bridge()` 工厂替换）。

## TASK7-DONE 2026-10-11 (Track2)

- Task 7 TerminalSurface 完成，commit `c32330e`（10 文件，+912/-871；TerminalSection.tsx 及其测试整删）。
- 抽验：主会话复跑 terminal-surface/terminal-store/dock-store 三文件 **38 passed**；worker 全量 vitest 1398 passed + `npm run build` 通过（先红后绿，新测试 23 例）。
- 接口面：`terminalStart(id, sessionKey?, shell?)`、`terminalResize(id, cols, rows)`（500/200 守卫）、`TerminalRecord.pty`、`terminal-shared.ts`（failureLabel/shell_unavailable/defaultGenerateTerminalId/CLEAR_SCREEN）。
- worker 偏离 5 处均已接受（关键两条）：① emulator 创建即全量回放缓存行，pty 翻转重建不丢缓冲；④ 旧容器职责用例（tab strip/tombstone 等）归 Task 9 dock 承接——**Task 9 派发时需带上此承接清单**。
- Task 7/8 间隔短，Task 8（fetchTerminalShells）现派发。

## TASK4-DONE 2026-10-11 (Track1)

- Task 4 TerminalSupervisor 门面完成，commit `e9b8ca2`（3 文件 +299/-6）。
- 抽验：主会话复跑 PTY/管道/shells 三文件 **49 passed**；ruff 通过；工作区干净。
- 路由与计数：restricted 或 Windows→管道桥；full-access POSIX→PTY 桥；`_live_count()` 跨桥统一上限 8；同 id 存活先接管不占额。入站 handler 调用面不变（仅工厂体替换）。
- worker 偏离 3 处均已接受：计划 `_delegate` 恒假死分支修正（行为等价）；超 100 列三元拆行；补 2 个 supervisor 测试（接管透传 + list_visible 双桥合并）。
- Task 5 接线自检结论（worker 报告）：门面消费面齐备（start(shell=)/resize/exited_owner/has_live/kill 宽限透传），FakeBridge 注入路径兼容。
- 下一步：Track1 派发 Task 5（契约接线：outbound pty 字段、入站 shell 校验、terminal_resize handler、contracts 附录 A v2）——Track1 收口任务。

## TASK8-DONE 2026-10-11 (Track2)

- Task 8 fetchTerminalShells 完成，commit `b358cbd`（api.ts +32、api.test.ts +43）。
- 抽验：主会话复跑 api.test.ts **51 passed**；worker 全量 1400 passed（基线 1398+2）无回归，build 通过；红灯原因正确（函数缺失）。
- 对齐确认：URL `/webui/terminal/shells` 无 `/api/` 前缀，与 Task 2 后端路由原样匹配 + vite dev proxy 覆盖 `/webui` 一致；helper 签名 `(token, base="")` 兼容 Task 9 的 `fetchTerminalShells(getToken())` 用法；补 2 条测试（Bearer/载荷 + 401→ApiError，支撑 Task 9 `.catch(()=>setShells(null))` 语义）。
- 下一步：Track2 派发 Task 9（StartDock 标签坞 + DockFileTab + BrowserSection fullHeight + 挂首页 + 删 HomeStartPanel）。

## TASK5-DONE / TRACK1-CLOSED 2026-10-11 (Track1)

- Task 5 契约 v2 接线完成，commit `88cde1d`（4 文件 +214：outbound `pty` 字段、入站 shell 白名单校验、`terminal_resize` handler、kill 宽限兜底、契约附录 A）。
- 抽验：主会话复跑三终端测试文件 **54 passed**；worker 另验回归 114 passed（workspace_files + websocket http routes）、ruff 通过、先红（6 failed）后绿。
- worker 偏离 4 处均已接受：FakeBridge 草图 bug 修正（owner_chat 恒 None 与其用例矛盾，按计划授权以 fixture 实际为准）；chat id 沿用既有 `"chat1"`；`test_encode_terminal_started_exact_fields` 随 v2 契约补 `pty` 必要连带更新。
- **前后端调用面逐字核对通过**（worker 报告，Task 7 vs Task 5）：terminalStart shell 仅 truthy 携带/后端 None 跳过校验 ✓；terminalResize 界值 500/200 单一真相源 + 前端预过滤/后端纵深 ✓；`event.pty===true` 对 v1 网关 optional 兼容 ✓。
- Track1（Task 1-5）收口：e977fae → 49e976d → 1b3a9c2 → e9b8ca2 → 88cde1d，工作树无未提交改动（`?? memory/` 为运行时产物，不纳入）。
- 待尾盘复核项继承：O_NONBLOCK 排水（T1）、R1 保守 restricted 形态、接管同 gate 信任面、resize 界值——Task 13 安全评审清单。

## TASK9-DONE 2026-10-11 (Track2)

- Task 9 StartDock 完成，commit `0c6d613`（8 文件 +1124/-736；HomeStartPanel.tsx 及其测试整删）。
- 抽验：主会话复跑 start-dock/dock-store/app-layout/terminal-surface 四文件 **107 passed**；worker 全量 1397 passed（1400−18 旧用例+15 新，对账一致）+ build + eslint 全绿。
- testid 清单 11 项全部落实；三条裁定约束逐条完成（横幅自过滤专测✓ / 容器职责 a-d 承接含 9 开 8 拒、tombstone 不复活专测✓ / dock 外层零 pty 分叉✓）。
- worker 偏离 6 处均已接受，要点转记：
  1. **恢复不重绑**：terminal_list_result 自动恢复仅开标签不发 start；persisted 接管候选跳过自动恢复、留给横幅显式点击——Task 13 验收第 9 条按此语义查。
  2. **新 i18n 键 `dock.platformDefaultShell`**（不在 Task 12 键表）——**Task 12 派发时必须补进 10 语言表**。
  3. app-layout.test.tsx 的 ⌘P 拦截用例改写为「挂载 start-dock」断言，Task 11 恢复拦截时需再改回。
  4. 旧 home-start-panel 测试的 fetch URL 形态契约断言随删除失覆盖——Task 13 集体测试如涉文件预览链路需补 mock 级以外的真实 URL 验证。
- 下一步：Track2 派发 Task 10（SessionWorkbench embedded 宿主）。

## TASK10-DONE 2026-10-11 (Track2)

- Task 10 SessionWorkbench embedded 宿主完成，commit `4ce8836`（4 文件 +288/-47，新建 session-workbench.test 5 用例）。
- 抽验：主会话复跑 session-workbench/start-dock/thread-shell **88 passed**；worker 全量 1402 passed（1397+5）+ build 绿。
- scope 隔离/接管持久化两专测通过（`nanobot.dock.terminals.websocket:chat-a` 键形正确）。
- worker 偏离 5 处均已接受，重点两条转记：
  1. **dock 渲染位置改为 aside 底部 flex-1 区 + 滚动区 `max-h-[45%]`**（计划原位夹在滚动区内无法给 xterm/fullHeight 确定高度）——**Task 13 浏览器验收需目测该布局比例是否协调**；
  2. thread-shell 共享假 client 补 terminal API 属必要基建。
- i18n 无新键；`workbench.fileTree` 键在本组件弃用，留待 Task 12 统一清理核对。
- 下一步：Track2 派发 Task 11（dock 快捷键 resolveDockShortcut + App 接线，含恢复 app-layout 的 ⌘P 拦截用例语义）。

## TASK11-DONE / TRACK2-CLOSED 2026-10-11 (Track2)

- Task 11 dock 快捷键完成，commit `3fdc79d`（4 文件 +111/-5：dock-shortcuts.ts 新模块 + App 接线 + app-layout 承接 + 4 新用例）。
- 抽验：主会话复跑 dock-shortcuts/app-layout **69 passed**；worker 全量 1406 passed（1402+4）+ build 绿；首跑 1 例波动两次复跑未复现（记录待集体测试观察）。
- 承接项完成：⌘P 拦截语义恢复且保留挂载断言；「does not swallow ⌘P」按 spec B2 反转成新语义（会话页也拦截、HOME scope 不误开）。
- ⌘T preventDefault 冲突处理归 Task 13 实机清单第 8 项复核（worker 转记）。
- Track2（Task 6-11）收口：47fecf2 → c32330e → b358cbd → 0c6d613 → 4ce8836 → 3fdc79d，工作树干净。
- 下一步：派发 Task 12（i18n dock.* ×10 语言，含补 `dock.platformDefaultShell`、核对 `workbench.fileTree` 弃用清理）；Task 13（合并+实机验收）在其后。

## TASK12-DONE 2026-10-11 (尾盘)

- Task 12 i18n 完成，commit `2e1033c`（10 locale ×+14/-1 + parity 测试 84 行）。
- 抽验：主会话复跑 parity **4 passed**；en/zh-CN/ja 抽查 dock 命名空间 10 键齐备（含补键 platformDefaultShell）、workbench.fileTree 已删且有「不回流」断言。
- worker 全量 1410 passed + build 绿；`startPanel.shellUnavailable` 原 10 locale 缺失已补。
- 下一步：Task 13——主仓合并两分支 → 集体测试 → build+重启 gateway → CDP 12 项实机验收 → 安全评审 4 转记点 → verifications 落盘。

## TASK13-DONE / BATCH-CLOSED 2026-10-11 (尾盘)

- 合并：backend 5 commits → webui 7 commits 双轨 `--no-ff` 入 feature/desktop-app，无冲突；主仓 ruff（pty/bridge/ws_http/inbound/outbound）+ 后端 pytest **65 passed** + 前端 vitest **1410 passed** + build 绿；仅补一次主仓 `npm install`（worktree 装过 addon-fit）。
- gateway 已重启：旧 52927 → 新 85262（:8765 主端口 + 18790 辅助），合并代码生效。
- 12 项 CDP 实机验收 + 4 项安全评审 **全部 PASS**。产物落盘 4 个 .md（collective-test 88 / code-review 138 / security-review 236 / verification-lite 31 行）+ 5 张 PNG + 1 个 `_verification-summary.json`，路径 `…/verifications/assets/2026-10-11-home-dock-pty-v1.1/`。
- 关键证据：xterm 真渲染 bash-3.2$（pty=true）、接管横幅「Running terminal: >. bash — Take over / Press Enter to redraw」、kill 后 60s 宽限无错误帧、⌘P/⌘T preventDefault 拦截、aside 宽 480 高 813（max-h-[45%] 协调）。
- token 全程合规：用户 secret 经 `jq -r` + shell var 流转；28+ 字符 token 落盘一律 `<TOKEN_REDACTED>`；报告只引用 `ws_len=48 / api_len=48 / tokenPrefix4="nbwt"` 元数据。
- 已知非阻塞差异（已落 verifications）：headless 进程 `$PATH` 无 zsh → shells 端点空 → 卡片回退到 `/bin/bash`（带「default interactive shell is now zsh」迁移提示）；⌘P/⌘T 合成走 `window.dispatchEvent`（CDP modifiers 视错位方法学回退，product 行为无误判）；PTY 字符回环仅测试侧断言、CDP 实测只到 `terminal_resize`+bash 提示符（需更严验证时后续补 emulator.write("echo PROBE\n") 轮询）。
- **批次完结**：所有 13 个任务 + 4 项验收 + 4 项安全评审 PASS；合并仅本地、未 push（push 需用户明确确认）。
