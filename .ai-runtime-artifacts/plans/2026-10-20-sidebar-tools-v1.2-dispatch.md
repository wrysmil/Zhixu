---
artifact: dispatch
route: orchestration.dispatch
skills: [test-driven-development, subagent-driven-development]
source: [2026-10-20-sidebar-tools-v1.2-plan.md]
created_at: 2026-10-20
status: done
approved: true
plan: 2026-10-20-sidebar-tools-v1.2-plan.md
tier: 2
---

# 侧边栏工具 v1.2 调度单

对应计划：[2026-10-20-sidebar-tools-v1.2-plan.md](./2026-10-20-sidebar-tools-v1.2-plan.md)

## 1. 调度原则

- 每个 WU 派发一个全新子 Agent，只读对应 Task 的完整章节，禁止跨任务“顺手优化”。
- 严格 TDD：先跑 RED（预期失败），再写实现，再跑 GREEN；禁止先写实现后补测试，禁止弱化断言换通过。
- 子 Agent 一律中文汇报；代码标识符、路径、命令、API 名保留英文。
- 子 Agent 只改自己边界内的文件。若发现必须改边界外文件，停止并回报，由主 Agent 决定并入哪个 WU。
- 每个 WU 完成后回报：改动文件、RED 证据、GREEN 证据、遗留风险。主 Agent 复核实际 diff 后再放行下一波。
- 不执行计划外的 `git push`、分支操作、依赖升级、格式化全仓。
- 测试环境固定：
  - Python：`.venv/Scripts/python.exe`（在仓库根目录执行）。
  - 前端：`cd webui && npm run test -- <paths>`（本机无 `bun`）。
  - 桌面：`node --test desktop/browser-policy.test.cjs`。

## 2. 执行图

```
Wave 1（并行）      Wave 2        Wave 3              Wave 4        Wave 5
WU-01 文件后端 ─┐
WU-02 终端后端 ─┼─> WU-03 文件前端 ─> WU-04 终端前端 ─┐
WU-05 浏览器/桌面 ┘                                  ├─> WU-06 dock 外壳 ─> WU-07 i18n/集体验收
                                                     ┘
```

| 波次 | 工作单元 | 前置 | 说明 |
| --- | --- | --- | --- |
| Wave 1 | WU-01、WU-02、WU-05 | 无 | 三者文件集不相交，可真正并行 |
| Wave 2 | WU-03 | WU-01 GREEN | 依赖后端 `show_hidden` 与 `/files/search` 契约落地 |
| Wave 3 | WU-04 | WU-02、WU-03 GREEN | `types.ts` 串行；依赖 WU-03 先写文件搜索类型 |
| Wave 4 | WU-06 | WU-04、WU-05 GREEN | 依赖 `TerminalSurface.active` 与 `BrowserSection.scope` |
| Wave 5 | WU-07 | 全部 GREEN | 统一全量验收 + 真实 GUI 冒烟 |

## 3. 写入边界

| 文件 | 归属 | 规则 |
| --- | --- | --- |
| `nanobot/webui/workspace_files.py` | WU-01 | 独占 |
| `nanobot/webui/ws_http.py` | WU-01 | 独占；只加 `show_hidden` 解析、搜索路由与 handler |
| `nanobot/webui/terminal_scrollback.py` | WU-02 | 新建，独占 |
| `nanobot/webui/outbound_wire.py` | WU-02 | 独占；只加 `terminal_snapshot` 编码 |
| `nanobot/webui/pty_terminal.py` / `terminal_bridge.py` | WU-02 | 独占 |
| `webui/src/lib/types.ts` | WU-03 → WU-04 | 串行：WU-03 先加文件搜索类型；WU-04 再加 `terminal_snapshot` 与 `TerminalRecord` 字段 |
| `webui/src/lib/api.ts` | WU-03 | 独占 |
| `webui/src/components/workbench/WorkspaceFileTree.tsx` | WU-03 | 独占 |
| `webui/src/components/dock/DockFileTab.tsx` / `DockFileSearch.tsx` | WU-03 | 独占 |
| `webui/src/lib/zhixu-client.ts` | WU-04 | 独占；必须把 `terminal_snapshot` 加入终端帧白名单 |
| `webui/src/hooks/useZhixuStream.ts` | WU-04 | 独占 |
| `webui/src/components/workbench/TerminalSurface.tsx` | WU-04 | 独占；`active` prop 由 WU-06 使用 |
| `webui/src/lib/clipboard.ts` | WU-04 | 独占 |
| `webui/src/lib/browser-url.ts` / `runtime.ts` | WU-05 | 独占 |
| `webui/src/components/workbench/BrowserSection.tsx` | WU-05 | 独占；`scope` prop 由 WU-06 传入 |
| `desktop/browser-policy.cjs` / `.test.cjs` | WU-05 | 新建，独占 |
| `desktop/preload.cjs` / `main.cjs` / `package.json` | WU-05 | 独占 |
| `webui/src/lib/dock-store.ts` | WU-06 | 独占 |
| `webui/src/components/dock/StartDock.tsx` | WU-06 | 独占；不得在 WU-03/04 阶段改动 |
| `webui/src/i18n/locales/*/common.json` | WU-07 | 独占 |
| `webui/src/tests/i18n-dock-parity.test.ts` | WU-07 | 独占 |

## 4. 各 WU 派发摘要

### WU-01 文件后端：隐藏开关与有界搜索

- 必读：计划 Task 1 全节。
- 交付：
  - `workspace_directory_payload(..., show_hidden=False)`，条目附 `hidden`。
  - `workspace_search_payload(...)`：查询 ≤ 200 字符、结果 ≤ 100、扫描 ≤ 2000 文件、单文件 ≤ 256 KiB、每文件 ≤ 5 处、2.0s 截止；跳过二进制/符号链接/构建目录。
  - 路由 `/api/sessions/{key}/files/search` 与 `/api/workspace/files/search`。
- 验收：

```bash
.venv/Scripts/python.exe -m pytest tests/webui/test_workspace_files.py tests/test_webui_workspace_files.py -q
.venv/Scripts/python.exe -m ruff check nanobot/webui/workspace_files.py nanobot/webui/ws_http.py tests/webui/test_workspace_files.py tests/test_webui_workspace_files.py
```

- 门禁：两个 pytest 文件全 PASS，ruff `All checks passed!`。

### WU-02 终端后端：有界回放快照

- 必读：计划 Task 2 全节。
- 交付：
  - 新模块 `terminal_scrollback.py`：64 KiB / 32768 字符双上限，UTF-8 边界安全截断。
  - `outbound_wire.py` 新增 `terminal_snapshot` 编码。
  - PTY 与管道桥接管时，在 publisher 重绑后、`terminal_started` 前发送快照。
- 验收：

```bash
.venv/Scripts/python.exe -m pytest tests/webui/test_terminal_scrollback.py tests/test_webui_pty_terminal.py tests/test_webui_terminal.py -q
.venv/Scripts/python.exe -m ruff check nanobot/webui/terminal_scrollback.py nanobot/webui/outbound_wire.py nanobot/webui/pty_terminal.py nanobot/webui/terminal_bridge.py tests/webui/test_terminal_scrollback.py tests/test_webui_pty_terminal.py tests/test_webui_terminal.py
```

- 门禁：全 PASS；Windows 下 POSIX PTY 用例 skip 属正常，不得改成假通过。

### WU-03 文件前端：工具栏、隐藏、搜索

- 必读：计划 Task 3 全节。
- 前置：WU-01 GREEN。
- 交付：
  - `types.ts` 文件搜索类型与 `hidden` 字段；`api.ts` 两个搜索调用与 `showHidden` 参数。
  - `WorkspaceFileTree` 支持 `showHidden` / `refreshToken` / `collapseToken`，隐藏项弱化显示。
  - `DockFileTab` 工具栏：刷新、显示隐藏、全部折叠、搜索；新组件 `DockFileSearch`（250ms 防抖）。
- 验收：

```bash
cd webui && npm run test -- src/tests/dock-file-tab.test.tsx
```

- 门禁：定向用例全 PASS，且不修改 `StartDock.tsx`。

### WU-04 终端前端：快照回放、复制/清屏/粘贴、活动态

- 必读：计划 Task 4 全节。
- 前置：WU-02、WU-03 GREEN。
- 交付：
  - `zhixu-client.ts` 白名单加入 `terminal_snapshot`（漏加会导致整帧被丢弃）。
  - `TerminalRecord` 增加 `viewEpoch` / `historyTruncated`；store 支持 `clear` 与快照整帧替换（`event.seq < lastSeq` 丢弃）。
  - `TerminalSurface`：`active` 控制 PTY 交互与 fit，快照全量重放，工具栏复制/清屏/粘贴，截断提示。
- 验收：

```bash
cd webui && npm run test -- src/tests/terminal-store.test.ts src/tests/terminal-surface.test.tsx src/tests/zhixu-client.test.ts
```

- 门禁：定向用例全 PASS；`npm run build` 类型检查无错。

### WU-05 浏览器与桌面独立窗口

- 必读：计划 Task 5 全节。
- 交付：
  - 裸域名补全：公网主机补 `https://`，localhost/127/::1 补 `http://`，单标签与非法 scheme 维持原拒绝行为。
  - 浏览器历史按 scope 持久化到 sessionStorage，读取时重新过 `normalizeBrowserUrl`。
  - `desktop/browser-policy.cjs` + Node 测试；`main.cjs` 独立窗口加固与 IPC `mira:open-browser-window`。
  - 保留 iframe `sandbox="allow-scripts allow-forms allow-popups"`，不得加 `allow-same-origin`。
- 验收：

```bash
node --test desktop/browser-policy.test.cjs
cd webui && npm run test -- src/tests/browser-url.test.ts src/tests/browser-section.test.tsx
```

- 门禁：Node 与 Vitest 全 PASS；桌面窗口参数逐项符合计划 §3.4。

### WU-06 dock 外壳：标签持久化、重命名、keep-alive

- 必读：计划 Task 6 全节。
- 前置：WU-04、WU-05 GREEN。
- 交付：
  - `dock-store.ts`：`customTitle`、`renameTab`、file/browser 标签持久化（`nanobot.dock.tabs.<scope>`），容错读取。
  - `StartDock.tsx`：按 kind 显示图标、双击重命名、中键关闭、keep-alive 叠层面板（`absolute inset-0` + `invisible pointer-events-none` + `aria-hidden`），向终端传 `active`、向浏览器传 `scope`。
- 验收：

```bash
cd webui && npm run test -- src/tests/dock-store.test.ts src/tests/start-dock.test.tsx
```

- 门禁：定向用例全 PASS；切标签不重建终端、不丢文件树状态。

### WU-07 i18n 与集体验收

- 必读：计划 Task 7 全节。
- 前置：WU-01 ~ WU-06 全部 GREEN。
- 交付：
  - 17 个新键 × 10 语言；`i18n-dock-parity.test.ts` 覆盖新键。
  - 全量前端测试 + build、全量后端测试 + ruff + basedpyright、桌面 Node 测试。
  - 真实 GUI 冒烟（5 条路径）与截图，写入 `.ai-runtime-artifacts/verifications/2026-10-20-sidebar-tools-v1.2.md`。
- 验收：

```bash
cd webui && npm run test
cd webui && npm run build
.venv/Scripts/python.exe -m pytest tests/webui/test_workspace_files.py tests/test_webui_workspace_files.py tests/webui/test_terminal_scrollback.py tests/test_webui_pty_terminal.py tests/test_webui_terminal.py -q
.venv/Scripts/python.exe -m ruff check nanobot/webui/ tests/webui/test_workspace_files.py tests/webui/test_terminal_scrollback.py tests/test_webui_workspace_files.py tests/test_webui_pty_terminal.py tests/test_webui_terminal.py
.venv/Scripts/python.exe -m basedpyright nanobot/webui/workspace_files.py nanobot/webui/terminal_scrollback.py nanobot/webui/pty_terminal.py nanobot/webui/terminal_bridge.py nanobot/webui/outbound_wire.py
node --test desktop/browser-policy.test.cjs
```

- 门禁：结论只能是 `PASS` / `PASS WITH NOTES` / `FAIL`；有 FAIL 不得提交。

## 5. 串行点与集成门禁

1. **WU-03 启动前**：确认 WU-01 的 `show_hidden` 参数名、搜索响应字段与计划 §3.1 完全一致；不一致先改 WU-01，不在前端做兼容层。
2. **WU-04 启动前**：确认 WU-03 已合入 `types.ts`；WU-04 只追加终端类型，不回改文件搜索类型。
3. **WU-06 启动前**：确认 `TerminalSurface` 已接受 `active`、`BrowserSection` 已接受 `scope`；否则先补依赖 WU。
4. **WU-07 启动前**：`git status` 确认无越界改动；任何未预期的文件改动先查明来源。

## 6. 失败处理

- 同一 RED/GREEN 最多重试 2 次；仍失败则停止，附完整命令输出与已尝试方案，回报主 Agent。
- 禁止通过放宽断言、`skip`、`xfail`、`any` 强转来“通过”。
- 发现计划本身有误（契约冲突、缺依赖）时，先停并回报；由主 Agent 更新计划与本调度单后再继续。

## 7. 子 Agent 派发模板

```text
「Harness：orchestration.dispatch」
Skills: test-driven-development@harness-kit/.agents/skills/test-driven-development/SKILL.md loaded

任务：执行 <WU-0x>，依据 D:\studyspace\project\Zhixu\.ai-runtime-artifacts\plans\2026-10-20-sidebar-tools-v1.2-plan.md 的 Task <N> 全节。
工作目录：D:\studyspace\project\Zhixu
前置：<依赖 WU 已 GREEN / 无>
写入边界：仅允许修改计划 Task <N> 的 Files 列表内文件；边界外一律不动。
要求：
1. 严格按 Task 内步骤执行 RED → GREEN，保存关键命令输出摘要。
2. 不做计划外重构、不升级依赖、不跑全仓格式化。
3. 完成后用中文回报：改动文件、RED 证据、GREEN 证据、偏差与风险。
4. 不执行 git push；提交动作按 Task 内说明，若计划未要求提交则不要提交。
验收命令：<本调度单该 WU 的验收命令>
```

---

当前状态：`status: ready`，`approved: false`。等待用户批准后按 Wave 1 派发。
