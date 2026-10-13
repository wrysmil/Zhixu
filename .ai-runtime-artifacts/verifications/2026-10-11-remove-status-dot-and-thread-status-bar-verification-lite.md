---
artifact: verification-lite
route: leader-direct -> verification-before-completion
skills: [verification-before-completion]
source:
  - 用户截图（侧栏底部绿点 + 底部「📁 workspace ⚠ Full」两处红框）与指令「这两个去除掉」
created_at: 2026-10-11
status: complete
tier: 1
---

# 移除侧栏连接绿点与底部工作区状态栏

## 边界与上下文

纯移除，无新功能。用户框定两个元素：① 侧栏底部孤立的绿色圆点；② pane 底部 `📁 workspace ⚠ Full` 一体胶囊。经确认②按「整条状态栏删掉」处理（连同右端上下文占用 % 条），因为该状态栏是只读展示层，工作区切换与权限切换另有入口。

## 改动

- `webui/src/components/Sidebar.tsx`：删除底部 `<div className="… py-3"><ConnectionBadge /></div>` 行。`connectionStatus` 插槽（:158-162 → `WorkspaceNavigation.tsx:198-202` 的 `sr-only` 容器）保留，读屏仍有连接状态播报；`ConnectionBadge` 组件本身保留。
- `webui/src/components/thread/ThreadShell.tsx`：删除两处 `<ThreadStatusBar />` 挂载（非 portal 分支、composer portal 分支）与其 import。`workspaceScope`、`composerContextUsage` 仍被其他子组件使用，未变成 dead prop（lint `--max-warnings 0` 已证实）。
- 删除 `webui/src/components/thread/ThreadStatusBar.tsx` 与 `webui/src/tests/thread-status-bar.test.tsx`（全仓仅这两处挂载 + 该测试引用，确认零残留引用）。
- i18n 未动：`thread.composer.workspace.fullShort` / `defaultShort` 仍被 `WorkspaceControls.tsx:319` 使用（composer 的权限菜单），删键会破坏十语种 parity 断言。

## 验证

- `cd webui && npm run lint`：exit 0，无告警（含未用变量、未用 import 检查）。
- `cd webui && npm run test`：**94 文件 / 1285 用例全部通过**，exit 0。相比上一轮 95/1288 恰好少 1 文件 3 用例，即被删除的 `thread-status-bar.test.tsx`；无其他回归。
- `cd webui && npm run build`：exit 0，新 bundle `assets/index-zoqloY5w.js` 785.39 kB（原 787.69 kB），CSS `index-MrFQbKbW.css`。
- gateway 直接读盘 serve，`http://127.0.0.1:8765/` 已返回新 hash；Mira 桌面壳窗口 Cmd+R（`desktop/main.cjs:208-209` 的 reload/forceReload）即可重载，无需重启进程。
- 未做：桌面窗口内的视觉复核（需用户在 Mira 窗口按一次 Cmd+R 确认两处已消失）。

## Next

若还要收掉 composer 内的项目名/权限入口（`WorkspaceProjectPicker`、`WorkspaceAccessMenu`），那是另一层改动，会牵动 `ThreadComposer.tsx` 的 `showContextTabs` 链与 `App.tsx` 的 `workspaceControls` 传递，需单独确认。
