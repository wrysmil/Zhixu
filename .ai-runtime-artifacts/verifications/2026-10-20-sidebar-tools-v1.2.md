---
artifact: verification
route: orchestration.collective-closeout
skills: [verification-before-completion]
source: [2026-10-20-sidebar-tools-v1.2-plan.md]
created_at: 2026-10-20
status: pass-with-notes
approved: false
plan: 2026-10-20-sidebar-tools-v1.2-plan.md
tier: 2
---

# 侧边栏工具 v1.2 验证记录

## 1. 结论

**PASS WITH NOTES**

- 7 个工作单元的定向测试、类型检查、构建与后端 lint 全部通过。
- 真实浏览器 GUI 验证覆盖文件工具栏/隐藏/搜索/预览/折叠、标签重命名/中键关闭/刷新恢复、浏览器裸域名/非法 scheme、首页与会话 scope 隔离。
- 终端 GUI 启动被当前长期运行的 gateway 阻塞（一直 `Starting terminal…`）；终端前端/后端定向用例通过，未发现代码失败。
- 全量 Vitest 存在与本次改动无关的时序型抖动，且用户已明确要求停止耗时的全量复跑，故不以全量套件作为本次门禁。

## 2. 命令与结果

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| WU-01 | `.venv/Scripts/python.exe -m pytest tests/webui/test_workspace_files.py tests/test_webui_workspace_files.py -q` | `24 passed in 0.84s`，exit 0 |
| WU-01 | `.venv/Scripts/python.exe -m ruff check nanobot/webui/workspace_files.py nanobot/webui/ws_http.py tests/webui/test_workspace_files.py tests/test_webui_workspace_files.py` | `All checks passed!`，exit 0 |
| WU-02 | `.venv/Scripts/python.exe -m pytest tests/webui/test_terminal_scrollback.py tests/test_webui_pty_terminal.py tests/test_webui_terminal.py -q` | `13 passed, 46 skipped in 0.92s`，exit 0；POSIX PTY 用例在 Windows 跳过 |
| WU-02 | `ruff check`（4 个后端模块 + 3 个测试文件） | `All checks passed!`，exit 0 |
| WU-03 | `npm run test -- --cache=false src/tests/dock-file-tab.test.tsx` | `5 passed`，exit 0 |
| WU-03 | `npm run build` + `npx tsc -p tsconfig.build.json --noEmit` | 构建成功；tsc 无输出、exit 0 |
| WU-04 | `npm run test -- --cache=false src/tests/terminal-store.test.ts src/tests/terminal-surface.test.tsx src/tests/zhixu-client.test.ts` | `128 passed`，exit 0 |
| WU-04 | `npx tsc -p tsconfig.build.json --noEmit` | 无输出、exit 0 |
| WU-05 | `npm run test -- --cache=false src/tests/browser-url.test.ts src/tests/browser-section.test.tsx` | `51 passed`，exit 0 |
| WU-05 | `node --test desktop/browser-policy.test.cjs` | `pass 4 / fail 0`，exit 0 |
| WU-05 | `node --check desktop/main.cjs desktop/preload.cjs desktop/browser-policy.cjs` | 全部通过、exit 0 |
| WU-06 | `npm run test -- --cache=false src/tests/dock-store.test.ts src/tests/start-dock.test.tsx` | `26 passed`，exit 0 |
| WU-06 | `npx tsc -p tsconfig.build.json --noEmit` | 无输出、exit 0 |
| WU-07 | `npm run test -- --cache=false src/tests/i18n-dock-parity.test.ts` | `4 passed`，exit 0 |

### 全量 Vitest 说明

- 多次全量运行出现 `5 / 2 / 8` 个非确定性失败，集中在 `add-project-dialog`、`settings-capabilities`、`teams-view`、`preview-thread`、`thread-viewport` 和 `app-layout`。
- `app-layout` 的 `⌘P` 用例确认由本次「标签持久化」跨用例残留导致：已在 `src/tests/app-layout.test.tsx` 的 `beforeEach` 增加 `sessionStorage.clear()`，该文件 `64 passed`。
- 其余失败均为 1 秒级 `waitFor` 超时，单文件复跑仍偶发，且不涉及本次改动文件；用户随后要求停止全量耗时复跑。

## 3. GUI 验证（真实浏览器）

环境：本机 Chrome（浏览器自动化）+ Vite 5181（临时 `cacheDir` 规避 `node_modules/.vite` 的 EPERM）；gateway 为运行中的 `python -m nanobot gateway --foreground --port 18790`。

| # | 路径 | 操作 | 实际结果 |
| --- | --- | --- | --- |
| 1 | 首页 → Files | 打开 Files；点“显示隐藏文件”；搜索 `AGENTS`；点首条结果；展开 `agents` 后点“全部折叠” | 默认不显示 `.git`/`.env`；显示后出现 `.git`、`.identity-backup-20260929`、`.nanobot`、`.gitignore`；搜索返回名称命中 `AGENTS.md` 与内容命中 `agents/profiles/code-reviewer.json:25`；点击后左侧预览 `AGENTS.md`；折叠后 `profiles` 消失 |
| 2 | 首页 → Terminal | + → New terminal → powershell | 标签、复制/清屏/粘贴工具栏出现；粘贴在启动中为禁用；7 秒后仍为 `Starting terminal…`，未收到 `terminal_started` |
| 3 | 首页 → Browser | 输入 `example.com`；再输入 `file:///etc/passwd` | `example.com` 规范为 `https://example.com/` 并加载；`file:///etc/passwd` 被拒绝并提示 “Only http/https addresses can be opened here.”，原 iframe 保持不变 |
| 4 | 标签 | 双击 Files 改名为“文档”；切到 Terminal 再切回；对终端标签中键关闭；刷新页面 | 改名生效；切回后搜索词 `AGENTS`、隐藏开关、预览仍保留；中键关闭终端标签；刷新后恢复“文档”和 Browser，地址仍为 `https://example.com/`，终端标签不恢复但出现 takeover 横幅 |
| 5 | 会话页 embedded dock | 打开最近会话 → Tools sidebar → 打开 Files；返回首页 | 会话 scope 初始为空启动器，与首页标签隔离；返回首页后“文档”和 Browser 仍在，未泄漏会话标签 |

### 截图

- 已在会话内取得启动器实拍截图（三卡空态、右侧 dock）。
- 尝试用 `take_screenshot(filePath=…)` 落盘时，浏览器返回 `NATIVE_BROWSER_VIEWPORT_UNAVAILABLE`（headless 页面 `visibilityState=hidden`），未能生成 PNG 文件；因此本轮以 DOM 快照与内联截图作为证据。
- 截图目录 `.ai-runtime-artifacts/verifications/2026-10-20-sidebar-tools-v1.2-ui/` 已删除，避免留下空目录造成误导。

## 4. 未覆盖项与原因

1. **终端运行态 GUI**：gateway 为改动前启动的长期进程，`terminal_start` 未返回；未在未获确认的情况下重启共享 gateway。终端前后端行为由定向用例覆盖。
2. **Electron 独立浏览窗口**：浏览器环境无 `window.mira`，`browser-detached` 按设计不显示；未启动 Electron 桌面壳与打包验证。`browser-policy` Node 用例覆盖 URL 策略。
3. **物理中键**：以真实 DOM `auxclick(button=1)` 触发 React `onAuxClick`，未注入 OS 级鼠标事件。
4. **全量 Vitest**：按用户要求停止复跑；见 §2 说明。
5. **POSIX PTY 回放**：Windows 下 46 条用例跳过，`terminal_snapshot` 的接管回放未在 POSIX 实机验证。

## 5. 变更边界说明

- 所有实现均落在计划文件地图内。
- 额外仅新增 `src/tests/app-layout.test.tsx` 的 `sessionStorage.clear()`，用于隔离标签持久化引入的跨用例状态。
- 临时 Vite 配置、隔离缓存与 esbuild 临时目录已清理，未改动 5173/8765 常驻进程。
