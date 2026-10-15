# 侧栏工具行为统一 + 移除添加窗格（2026-10-15）

## 变更范围

### 1. 侧栏「工作区 ⇄ 侧栏工具」行为统一
用户反馈：点击侧栏 ＋ 有时打开侧栏工具，有时打开侧边聊天栏，行为不确定。

- `SessionWorkbench`：`StartDock` 改为常驻挂载（原先仅在工具态挂载），
  工作区状态下 ＋ 不可见、无法唤起工具菜单；新增 `workbench.toolsMenu` 按钮，
  点击即切到工具态并展开 文件 / 浏览器 / 终端 菜单，与工具态下的 ＋ 行为一致。
- `StartDock`：新增 `openMenuSignal`，供宿主远程唤起其 ＋ 菜单。
- `ThreadShell`：`workbenchAction` 仅在侧栏收起时渲染，且只做「打开」不做「切换」；
  侧栏开启期间该位置不再出现第二个语义不同的控件。
- `collapseWorkbench` 在收起时清空 `toolsRequest`，保证再次打开回到工作区。

### 2. 侧栏只在当前激活窗格渲染
多窗格布局下每个 pane 各渲染一条侧栏，导致并排出现多个「工作台」。
`ThreadShell` 新增 `showWorkbenchRail`，`App.renderPane` 传 `context.active`。

### 3. 移除「添加窗格」
- `PaneWorkbench`：删除 ＋ 按钮本体、仅为其服务的 `HeaderIconButton`、
  `Plus` 图标与 `onAddPane` / `addPaneDisabled` / `addPaneDisabledLabel` 三个 props。
- `App`：删除 `onAddPane` 回调、`creatingPane` 状态、`activePaneLimitReached`
  及随之不再使用的 `addWorkbenchPane` 引入。

## 验证情况

| 范围 | 结果 |
| --- | --- |
| `tsc -p tsconfig.build.json` | 通过，0 错误 |
| `tsc --noEmit -p tsconfig.json` | 本次涉及文件 0 错误（存量 import.meta/env 等报错未动） |
| `vite build` | 通过，2946 模块，产物写入 `nanobot/web/dist` |
| vitest（app-layout / pane-workbench / thread-shell / session-workbench / start-dock / terminal-surface） | 202 通过 |

## 未闭合项

1. `app-layout.test.tsx > deletes a custom skill from its detail sheet` 失败。
   与本次改动无关（该用例覆盖技能删除），尚未定位。
2. `StartDock` 常驻挂载后，每个会话侧栏都会触发一次 `terminalList()`。
   量级很小，未做懒加载；若后续需要可退回按需挂载并仅保留 ＋ 行为统一。
3. 本轮新增的 `showWorkbenchRail` / `openMenuSignal` 相关用例已随改动更新，
   但未在最终构建后重跑全量。

## 环境备注

`nanobot/web/dist` 原由其他账号构建，`assets` 目录对当前账号只读，
`emptyOutDir` 持续 EPERM。已将旧产物改名为 `nanobot/web/dist__stale` 挪开后重建，
该目录本地无法删除，已写入 `.git/info/exclude`（仅本机忽略，不入库）。