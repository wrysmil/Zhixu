---
artifact: verification-lite
route: superpowers:brainstorming -> leader-direct -> verification-before-completion
skills: [brainstorming, test-driven-development, verification-before-completion]
source:
  - .ai-runtime-artifacts/specs/2026-10-10-create-project-modal-spec.md
  - 用户截图（创建项目弹窗设计稿 + macOS 文件夹选择面板）与指令「直接实现吧」
created_at: 2026-10-11
status: partial
tier: 1
---

# 侧栏创建项目弹窗 + 原生文件夹选择器

## 边界与上下文

Tier 1 Leader 直做（用户明确「直接实现」，无 worktree、无 WU 拆分）。范围限定在 WebUI 侧栏「添加项目」入口的形态与取路径链路：Popover → Radix Dialog，手输路径 → 原生文件夹选择器。**后端零改动**：`/api/workspaces/pick-folder`、`native_folder_picker.py`、`workspaces.py` 均按现状复用；`runtime.ts` 不动（D3）；`ChatList.onAdd → App.onNewChatInProject` 的既有落盘/会话流程不变。

实际栈：React 18.3 + TypeScript 5.7 + Radix Dialog + Tailwind（石墨灰 token）+ i18next 十语种 + Vitest 2.1 / Testing Library（happy-dom）。本机无 `bun`，脚本以 `npm` 等效执行。

## 改动

- 新增 `webui/src/components/AddProjectDialog.tsx`：受控弹窗，接口 `{ open, onOpenChange, onConfirm(path, name) }`；token 与 WS client 由内部 `useClient()` 取得，避免 App → Sidebar → ChatList 三层 prop 穿透。
- 选择器解析顺序：`getRuntimeHost().pickFolder`（Electron `window.mira`）→ 回落 `client.requestMutation("workspace.pick_folder", {}, 300_000)`，超时与服务端 `_PICKER_TIMEOUT_SECONDS = 300` 对齐。
- 能力判定（D2）：`open` 时拉一次 `fetchWorkspaces()` 读 `controls.can_pick_folder`；不可用则整块选择区置灰 + 说明文案，不发请求。403/503 运行时同样退化为置灰；409 内联「已有选择窗口打开中」且不清空已选路径。
- 删除「粘贴路径」手输入口（D1）。`workspace.dialog.manual/usePath/absolutePathRequired/defaultProject` **保留**：`WorkspaceControls.tsx:236,263,265,275` 与 `i18n.test.tsx:211-213` 仍在引用，只是本弹窗不再提供该入口；`clickToAdd` 零引用，已删。
- 提交前路径经 `normalizeWorkspacePath` 去尾斜杠（macOS `POSIX path of` 返回带尾斜杠，侧栏项目段 key 不带，归一避免同名项目两条记录）。
- `ChatList.tsx`：`AddProjectButton` 由 149 行 Popover 实现瘦身为「+」触发钮 + 弹窗；`mounted` 门控条件挂载（`useClient()` 在无 Provider 的 ChatList 测试里会 throw，同时保留 Radix 关闭动画）；Popover/Button/Input/FolderUp/projectNameFromPath import 全部清理。
- i18n 10 份 locale：新增 7 key（title / sourceFolder / addFromThisComputer / add / unavailable / pickFailed / pickerBusy）、删除 1 key（clickToAdd）、改 1 值（create：创建 → 创建项目）。

## 验证

- `cd webui && npm run test`：**95 文件 / 1288 用例全部通过**，exit 0。含新增 `add-project-dialog.test.tsx` 7 项、既有 `i18n.test.tsx` 十语种键集合双向比对、`chat-list.test.tsx` 全量项目段用例无回归。
- 新增用例覆盖：can_pick_folder=false 禁用态与文案、WS 回落断言 `("workspace.pick_folder", {}, 300_000)`、路径回填 + 项目名自动填充 + 去尾斜杠提交、手动改名优先、取消返回 `path:null` 静默、`window.mira` 抛 409 的内联提示与已选路径保持、关闭再打开状态 reset、侧栏「+」→ dialog → 创建项目 → `onNewChatInProject("/Users/me/photos","photos")` 接线。
- `cd webui && npm run lint`（`eslint --max-warnings 0`）：exit 0，无告警。
- `cd webui && npm run build`（tsc + vite）：exit 0，构建成功，产物写入 `nanobot/web/dist`；保留既有 >500kB chunk 提示（`index-BS3uQUWN.js` 787kB，非本次引入）。
- `git diff --check -- webui/src`：clean。（仓库内另有 `.ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-09-15-*.md:242` 的 EOF 空行告警，属既有存档文件，非本次改动。）
- 服务端选择器能力探针（不弹窗）：`uv run --no-sync python -c "from nanobot.webui.native_folder_picker import native_folder_picker_available; print(...)"` → `True`。配合 gateway `/health` = `{"status":"ok","ready":true,"websocket":"running"}`、`127.0.0.1:8765` 返回 200、config `host=127.0.0.1`，`workspace_folder_picker_available()`（`ws_http.py:412-419`）三个条件在本机应同时成立。
- 改动面：13 个源文件（1 新组件 + `ChatList.tsx` + 10 locale + 1 新测试）+ `nanobot/web/dist` 重新构建（该目录未纳入 git 跟踪）。

## 未验证 / 风险

- **浏览器端到端点击未完成**：受控浏览器停在 WebUI 「Password」登录页，登录需要 config 里的本机 bootstrap 口令，未把该口令读入会话。弹窗渲染、Finder 实际弹出、`osascript` 权限表现三项均待用户在已登录浏览器里点一次确认。
- **macOS 自动化权限**：gateway 子进程首次调 `osascript choose folder` 可能被系统「自动化/文件访问」授权拦住并返回非 0，届时时按 § 4.5 走 503 退化路径（内联提示 + 置灰），不会静默失败——但这条路径本机尚未实测。
- **Electron 桌面壳链路**：`window.mira.pickFolder` 分支仅有单测 mock 覆盖，本机 `desktop/node_modules` 未安装，未实跑。
- Windows/Linux 的 zenity/kdialog/PowerShell 分支未在本机验证（仅 macOS）。

## Next

1. 用户在已登录的 WebUI 里走一遍：侧栏「+」→ 居中弹窗 → 「添加」→ macOS 文件夹面板 → 回填 → 「创建项目」出现在侧栏项目段。通过后可把本件 `status` 改为 `complete`。
2. `runtime.ts` 的 `nativeHostPort` / `/v1/pick-folder` 死代码另案清理。
3. 若要实测 Electron 链路：`cd desktop && npm install && npm run smoke`。
