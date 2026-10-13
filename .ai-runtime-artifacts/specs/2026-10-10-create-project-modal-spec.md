---
artifact: spec
route: superpowers:brainstorming
skills:
  - brainstorming
skills_evidence:
  - ~/.agents/skills/brainstorming/SKILL.md
  - harness-kit/artifact-templates/spec.harness-overlay.md
source:
  - 本会话用户截图（目标弹窗设计稿 + macOS 文件夹选择面板）与用户指令「方案没意见，开始写 spec 吧」
  - webui/src/components/ChatList.tsx
  - webui/src/components/ui/dialog.tsx
  - webui/src/components/thread/ThreadShell.tsx
  - webui/src/lib/runtime.ts
  - webui/src/lib/api.ts
  - webui/src/lib/workspace.ts
  - webui/src/providers/ClientProvider.tsx
  - webui/src/i18n/locales/zh-CN/common.json
  - nanobot/webui/ws_http.py
  - nanobot/webui/native_folder_picker.py
  - nanobot/webui/workspaces.py
  - desktop/main.cjs
  - desktop/preload.cjs
  - harness-kit/core/routing.md
created_at: 2026-10-10
updated_at: 2026-10-11
status: approved
approved: true
topic: create-project-modal-and-native-folder-picker
---

# 创建项目弹窗与原生文件夹选择器

> 已获用户批准（「方案没意见」+「直接实现吧」）并按 Tier 1 直做完成，收尾口径见 `.ai-runtime-artifacts/verifications/2026-10-11-create-project-modal-verification-lite.md`。本文描述已确认的意图与实现。

## 1. 目标

侧栏「项目」段的「+」当前是一个贴着按钮弹出的 Popover，用户要求改成居中的**弹窗（modal）**，并且弹窗里的「添加」点击后能直接调起**操作系统原生文件夹选择器**选中一个文件夹作为项目源目录。

用户提供的两张截图是**目标设计稿**，不是现状：截图 1 的文案（「创建项目」「源文件夹」「在此电脑上添加文件夹」）在仓库 i18n 中不存在；当前实现文案是「添加项目 / 点击添加文件夹 / 粘贴路径 / 创建」（`webui/src/i18n/locales/zh-CN/common.json:1399`、`:2008-2021`）。

## 2. 现状与根因

### 2.1 弹窗形态

`AddProjectButton`（`webui/src/components/ChatList.tsx:1709-1857`）用 `<Popover>` + `PopoverContent`（:1746、:1768）承载表单，挂在侧栏「项目」段头的「+」上（:718）。Popover 是锚定式浮层，不是模态弹窗。

### 2.2 浏览器场景下原生选择器根本不可用（核心缺陷）

「点击添加文件夹」虚线区的渲染条件是 `pickFolder` 非空（`ChatList.tsx:1779`），而 `pickFolder` 来自 `getRuntimeHost()`（:1719）。`createRuntimeHost` 的解析顺序是 `window.nanobotHost?.pickFolder ?? window.mira?.pickFolder`（`webui/src/lib/runtime.ts:126`）：

| 来源 | 状态 | 依据 |
| --- | --- | --- |
| `window.mira` | 仅 Electron 桌面壳存在，且 `desktop/node_modules` 尚未安装 | `desktop/preload.cjs:5-11` |
| `window.nanobotHost` | 宿主从未注入 | — |
| loopback 桥 `nativeHostPort/nativeHostToken` | **死代码**：URL 参数名定义在 `runtime.ts:67-68`，但 gateway 侧 `nativeHostPort` 零命中（Python 全仓无匹配），`_webui_browser_url`（`nanobot/cli/webui_support.py:257-267`）只拼 `bootstrapSecret` | 已验证 |
| loopback 端点 `POST /v1/pick-folder`（`runtime.ts:232-263` 调用） | **服务端不存在**：`nanobot/` 内 `v1/pick-folder` 零命中 | 已验证 |

结论：在浏览器里打开 WebUI 时 `pickFolder === undefined`，虚线选择区**不渲染**，用户只剩「粘贴路径」手输框。而 gateway 侧其实早就有可用实现：`POST /api/workspaces/pick-folder`（`nanobot/webui/ws_http.py:1225-1226`、`1264-1286`），经 `pick_native_folder()`（`nanobot/webui/native_folder_picker.py:168-211`）用 `osascript choose folder` 弹 macOS 面板，Linux 走 zenity/kdialog，Windows 走 FolderBrowserDialog。

`ThreadShell` 已经在用这条链路（WS mutation `workspace.pick_folder`，`webui/src/components/thread/ThreadShell.tsx:764-771`，映射表 `ws_http.py:156`），只有侧栏的添加项目表单没接。

### 2.3 一个可选的重构信号

`ChatList.tsx` 已 2113 行，承载侧栏全部渲染逻辑；`Popover` 在该文件中仅被 `AddProjectButton` 一处使用（共 9 处标识符，全属该组件）。把添加项目表单抽成独立组件既满足本次「弹窗化」，也让 `ChatList` 少一块职责，并可单独测试。

## 3. 已确认决策

| # | 决策 | 出处 |
| --- | --- | --- |
| D1 | 删除「粘贴路径」手输框，原生选择器是唯一路径来源 | 用户：「手输路径不保留」 |
| D2 | 选择器不可用时：「添加」置灰 + 说明原因，而非点击后报错、也不是隐藏整段 | 用户选定「禁用按钮 + 说明原因」 |
| D3 | 实现路线 A：抽独立弹窗组件 + 补 gateway 选择器链路；不改 `runtime.ts` | 用户选定 A |

D3 相对最初提案有一处收敛：原计划给 `runtime.ts` 的 `pickFolder` 加 HTTP 回落，但 token 并非全局可取，而是 `useClient()` React context 暴露的 `getToken`（`webui/src/providers/ClientProvider.tsx:36-51`、`App.tsx:960`）。因此选择器回落放在弹窗组件内，`runtime.ts` 保持不动，避免为一个按钮给运行时抽象层塞入 token 依赖。

## 4. 设计

### 4.1 组件边界

新增 `webui/src/components/AddProjectDialog.tsx`：

- **职责**：呈现创建项目弹窗；向宿主取一个源文件夹路径；收集项目名称；提交。
- **接口**：`{ open: boolean; onOpenChange(next: boolean): void; onConfirm(path: string, name: string): void }`。无其他入参，token 与 WS client 由组件内部 `useClient()` 取得。
- **内部状态**：`pickedPath`、`name`、`picking`、`canPick`、`error`。
- **依赖**：`@/components/ui/dialog`（Radix Dialog 封装，`showCloseButton` 默认 true，已带右上 X 与 `modalSurfaceClassName` 石墨灰表面样式，`max-w-lg`）、`@/lib/workspace.{projectNameFromPath,normalizeWorkspacePath}`、`@/lib/runtime.getRuntimeHost`、`@/lib/api.{fetchWorkspaces,ApiError}`、`@/providers/ClientProvider.useClient`。
- **可独立理解**：不需要读 `ChatList` 即可明白它做什么；`ChatList` 也不需要知道它怎么选文件夹。

`ChatList.tsx` 侧：`AddProjectButton` 只保留「+」触发按钮（样式 :1758-1766 不变）与 `<AddProjectDialog>`，删除 Popover 全部标识符（含 import，否则 `npm run lint` 的 `--max-warnings 0` 会挂）；`onAdd(projectPath, projectName)` 签名与向 `onNewChatInProject` 的上抛（`App.tsx:1551-1590`）保持不变，因此项目落盘、sidebar-state 注册、draft scope 等后端与会话流程**零改动**。

一处实现细节：弹窗采用 `mounted` 门控的条件挂载（首次点击「+」才 import 并渲染 `AddProjectDialog`）。原因是组件内部用 `useClient()`，而 `webui/src/tests/chat-list.test.tsx` 直接 `render(<ChatList …/>)`、不包 `ClientProvider`，无条件挂载会在 `useClient()` 处 throw；`mounted` 同时保留 Radix 的关闭动画。

### 4.2 选择器链路与能力判定

点击「添加」（或整块圆角框）时的解析顺序：

1. `getRuntimeHost().pickFolder`（Electron：`window.mira.pickFolder` → `ipcRenderer.invoke("mira:pick-folder")` → `dialog.showOpenDialog({ properties: ["openDirectory","createDirectory"] })`，`desktop/main.cjs:239-246`，已正确带 `openDirectory`）
2. 回落：`client.requestMutation<{ path: unknown }>("workspace.pick_folder", {}, 300_000)`，超时口径与 `ThreadShell.tsx:764-771` 一致，且与服务端 `_PICKER_TIMEOUT_SECONDS = 300`（`native_folder_picker.py:13`）对齐

两条都返回绝对路径字符串或 `null`（取消），UI 不区分自己跑在哪个宿主。

能力判定（落实 D2）：弹窗 `open` 变为 true 时调用一次 `fetchWorkspaces(getToken())`，读 `payload.controls.can_pick_folder`。该字段由后端 `workspaces.py:176` 输出，取值条件为 `workspace_folder_picker_available()`（`ws_http.py:412-419`）：gateway 监听 loopback **且** 请求来自本机浏览器 **且** 本机存在 osascript/zenity/kdialog/PowerShell。

- `can_pick_folder === false`：「添加」禁用，框内文案替换为不可用说明（见 § 4.4 K6）。
- 该判定在 Electron 下同样成立：桌面壳 `window.mira.pickFolder` 优先命中，能力位仅用于兜底提示。
- 权衡：`App.tsx` 已持有 `workspaces` 状态（:1032、:1256），弹窗再拉一次属于重复请求。选择它是因为替代方案要给 App → Sidebar → ChatList 三层加 prop；弹窗打开是低频用户动作，一次 GET 换组件解耦更划算。

### 4.3 数据流

```
「+」 → AddProjectDialog(open)
         ├─ open 时: GET /api/workspaces → controls.can_pick_folder
         ├─ 点「添加」: window.mira.pickFolder | WS workspace.pick_folder
         │                → gateway osascript → Finder → 绝对路径
         ├─ 回填: pickedPath; name 为空时 = projectNameFromPath(pickedPath)
         └─ 提交: onConfirm(normalizeWorkspacePath(pickedPath),
                          name.trim() || projectNameFromPath(path))
                    → ChatList.onAdd → App.onNewChatInProject → 既有流程
```

提交前路径经 `normalizeWorkspacePath`（`webui/src/lib/workspace.ts:45-48`）去掉尾部斜杠：macOS `osascript` 的 `POSIX path of` 返回值带尾斜杠（`/Users/me/Photos/`），而侧栏项目段的 group key 与既有项目注册表按无尾斜杠路径索引，不归一会出现同名项目的两条记录。

### 4.4 视觉与文案

布局按截图 1，落在既有石墨灰 token 上（`modalSurfaceClassName` / `rounded-modal`）：

1. `DialogHeader` + `DialogTitle`「创建项目」；右上 X 由 `DialogContent` 自带。
2. 「项目名称」输入框，左侧文件夹图标；选中目录后自动填为目录名（用户可覆盖），placeholder 仅在空值时提示「项目名称」。
3. 段标签「源文件夹」。
4. 圆角可点框：未选时居中「在此电脑上添加文件夹」+「添加」胶囊按钮；选中后显示完整路径（`truncate` + `title` 悬浮全文），文案换成「更换文件夹」。
5. `DialogFooter`：取消（ghost）/ 创建项目（主按钮，`disabled = !pickedPath.trim()`）。

i18n（`workspace.dialog` 段）。最终落地：**新增 7 key、删除 1 key、改 1 值**（`git diff` 10 个 locale 各 +8/-2 行）：

| key | 处置 | zh-CN 值 |
| --- | --- | --- |
| `title` | 新增 | 创建项目 |
| `sourceFolder` | 新增 | 源文件夹 |
| `addFromThisComputer` | 新增 | 在此电脑上添加文件夹 |
| `add` | 新增 | 添加 |
| `unavailable` | 新增 | 请在网关所在电脑打开，或使用桌面客户端 |
| `pickFailed` | 新增 | 未能打开文件夹选择器 |
| `pickerBusy` | 新增 | 已有选择窗口打开中 |
| `create` | 改值 | 创建 → 创建项目 |
| `clickToAdd` | 删除 | 落实 D1/D2；全仓 grep 后确认零引用 |

保留复用：`projectName`、`cancel`、`changeFolder`、`picking`。`manual` / `manualPlaceholder` / `usePath` / `absolutePathRequired` / `defaultProject` **保留**：`WorkspaceControls.tsx:236,263,265,275` 与 `i18n.test.tsx:211-213` 仍在引用，本次只从新弹窗移除用法。10 个 locale（en / es / fr / id / ja / ko / pt-BR / vi / zh-CN / zh-TW）均补齐新 key，不依赖 `fallbackLng`；`i18n.test.tsx` 的十语种键集合双向比对用例已通过。

### 4.5 错误处理

| 情况 | 表现 |
| --- | --- |
| 用户在 Finder 取消 | 后端返回 `path: null`（`native_folder_picker.py:197-207`）→ 保持原状态，无提示 |
| 选择中 | 「添加」禁用，文案「正在选择…」（复用 `picking`），最长 300s |
| 409「picker already open」（`ws_http.py:1273-1274`） | 框下内联一行提示：已有选择窗口打开中 |
| 403 / 503（picker 不可用或 osascript 失败） | 内联一行提示 + 把 `canPickFolder` 置 false，退化为 D2 的禁用态 |
| 请求异常 / WS 断开 | 内联一行提示，路径保持为空 |
| 提交时路径已不存在 | 不做额外校验：路径由选择器当场给出，服务端 `pick_native_folder` 已校验 `is_absolute() && is_dir()`（:209-210） |

内联提示而非 toast：弹窗是模态的，toast 会被遮罩层压住视线。新增 key `pickFailed`、`pickerBusy`。

### 4.6 测试

`webui/src/tests/add-project-dialog.test.tsx`（Vitest + Testing Library，happy-dom，默认 locale `en`，`src/tests/setup.ts` 已全局装 i18n）。mock 方式：`vi.hoisted` 暴露 `requestMutation` / `fetchWorkspaces`，`vi.mock("@/providers/ClientProvider")` 替换 `useClient`，`vi.mock("@/lib/api", importOriginal)` 保留 `ApiError` 只换 `fetchWorkspaces`。7 条用例（均已通过）：

1. `can_pick_folder=false` → 选择区禁用、`unavailable` 文案出现、无「添加」胶囊、点击不发 mutation、创建按钮禁用且 `onConfirm` 不触发。
2. 无 `window.mira` → 点选择区断言 `requestMutation("workspace.pick_folder", {}, 300_000)`；回填路径、项目名自动填为目录名、提交得到去掉尾斜杠的 `("/Users/me/Photos", "Photos")`。
3. 手动改项目名 → 覆盖推断名，提交 `("/Users/me/photos", "Family")`。
4. 选择器返回 `path: null`（取消）→ 路径保持空、无错误提示、创建按钮仍禁用。
5. `window.mira.pickFolder` 抛 `status=409` → 内联「已有选择窗口打开中」，已选路径不被清空，且不再走 WS 回落。
6. 关闭再打开 → `pickedPath` / `name` reset，创建按钮回到禁用态。
7. 接线：`ChatList` 点「Add project」→ 出现 `role=dialog`「创建项目」→ 选目录 → 点「创建项目」→ `onNewChatInProject("/Users/me/photos", "photos")`。

原计划第 7 条放在 `chat-list.test.tsx`，实际放在本文件的 `describe("sidebar add-project entry")`：`chat-list.test.tsx` 顶部整体 `vi.mock` 掉 `ClientProvider` 会波及该文件 1200 行的其他用例，而接线断言本来就依赖同一套 mock 骨架。

## 5. 明确不做

- 不改后端：`/api/workspaces/pick-folder`、`native_folder_picker.py`、`workspaces.py` 现状已够用。
- 不动 `runtime.ts`，不复活 `/v1/pick-folder` 与 `nativeHostPort` 死代码（既不实现也不清理，另案处理）。
- 不给 `ThreadShell` 的工作区选择改形态（它已能用同一条 WS 链路，本次只让新弹窗复用同一 mutation）。
- 不做拖拽文件夹进弹窗、不做最近使用目录、不做多目录一次添加。
- 不引入「手输路径」的任何替代入口（D1）。

## 6. 改动面

| 文件 | 动作 |
| --- | --- |
| `webui/src/components/AddProjectDialog.tsx` | 新增 |
| `webui/src/components/ChatList.tsx` | 改：`AddProjectButton` 瘦身、删 Popover 与手输框、清 import |
| `webui/src/i18n/locales/*/common.json` × 10 | 改：新增 7 key、删 1 key、改 1 值 |
| `webui/src/tests/add-project-dialog.test.tsx` | 新增（含侧栏接线断言） |
| `nanobot/web/dist/**` | 由 `npm run build` 重新产出（此前为 9-30 陈旧构建） |

实际合计 13 个源文件（其中 10 个为机械 i18n）+ 构建产物。`webui/src/tests/chat-list.test.tsx` 未改动（接线断言改放在新测试文件）。按 `harness-kit/core/routing.md` § WU 编排硬触发本属 Tier 2，用户明确要求「直接实现」，故按 **Tier 1 Leader 直做**（无 worktree），产物为 `verification-lite`。

## 7. 验证口径

```bash
cd webui && npm run test        # vitest run，新增用例全绿且既有用例无回归
cd webui && npm run lint        # eslint --max-warnings 0
cd webui && npm run build       # tsc -p tsconfig.build.json && vite build
```

手工验收（本机 macOS + 浏览器，gateway 已在 `127.0.0.1:8765` 运行）：侧栏「+」→ 居中弹窗 → 点「添加」→ 弹出 macOS 文件夹面板 → 选中后路径与项目名回填 → 「创建项目」后项目出现在侧栏项目段。

注：本机无 `bun`，`package.json` 脚本用 `npm` 等效执行。

## 8. 风险

| 风险 | 应对 | 状态 |
| --- | --- | --- |
| macOS 首次由 gateway 子进程调 `osascript` 可能被系统权限（自动化/文件访问）拦住，返回非 0 | 已归入 § 4.5 的 503 退化路径：内联提示 + 禁用态 | **未验**：需 § 7 手工验收实测一次并记录 |
| 删除 `manual` / `manualPlaceholder` 可能有其他引用点 | 实现前全仓 grep | **已确认**：`WorkspaceControls.tsx:236,263,265,275` 与 `i18n.test.tsx:211-213` 在引用，故 key 全部保留，仅新弹窗不再提供手输入口；`clickToAdd` 零引用，已删 |
| 弹窗内重复拉 `/api/workspaces` | 仅 `open` 时拉一次，不轮询；失败按 `canPick=false` 保守处理 | 已实现 |
| Electron 与浏览器两条链路行为差异（Electron 可新建文件夹，`osascript choose folder` 亦可） | 两条均返回绝对路径，UI 一致；测试分别 mock（用例 2 走 WS、用例 5 走 `window.mira`） | 已实现 |
| 原生选择器返回值带尾斜杠，与侧栏项目 key 不一致 | 提交前 `normalizeWorkspacePath` | 已实现（用例 2 断言） |

## Next

实现与自动化验证已完成（`npm run test` 95 文件 / 1288 用例全绿、`npm run lint` 无告警、`npm run build` 成功）。余下：

1. § 7 本机手工验收（浏览器点「+」→ 弹窗 → 添加 → Finder → 回填 → 创建项目出现在侧栏），结果记入 `verification-lite`。
2. Electron 桌面壳链路未实测（本机 `desktop/node_modules` 未安装）。
3. `runtime.ts` 的 `nativeHostPort` / `/v1/pick-folder` 死代码清理另案。
