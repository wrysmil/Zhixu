---
route: Tier 1 Leader 直做
artifact: verification-lite
date: 2026-10-22
topic: landing-density
status: passed
---

# 欢迎页 / 侧栏视觉密度收敛 — 验证

## 改动

| 级别 | 项 | 落点 |
| --- | --- | --- |
| P0 | 欢迎页删除重复的「继续手头的工作」，只保留侧栏一处会话列表 | `webui/src/components/thread/ThreadWelcome.tsx`、`ThreadShell.tsx`（移除 `onOpenChat`）、`App.tsx` |
| P0 | 会话标题规范化：去 markdown/标点、折叠空白、上限 24 字；纯数字或 ≤3 字符拉丁标题回退为「新对话 · 月/日」 | `webui/src/lib/format.ts`、`chat-groups.ts`、`useSessions.ts`、`SessionSearchDialog.tsx` |
| P0 | 侧栏 recovery 三角默认中性灰，行 hover 才变橙 | `webui/src/components/ChatList.tsx` |
| P1 | 删除「你好，我是知序」eyebrow 与标题的语义重复 | `ThreadWelcome.tsx`、`globals.css` |
| P1 | 字号收敛：辅助文本 12px，交互 chip 13px；intro 最小高度 11rem→8.5rem | `globals.css` |
| P1 | composer 边框 `border/80`→`border/60`；完全访问 badge 由橙色改为中性灰 | `ThreadComposer.tsx`、`WorkspaceControls.tsx` |
| P2 | 文件页不再上抛外置预览，改为树+预览同列内嵌（四列→三列） | `ThreadShell.tsx` |
| P2 | 欢迎列宽 `clamp(43rem,46vw,52rem)` → `min(44rem,100%)`，跟随容器 | `globals.css` |

## 验证

```
npx tsc -p tsconfig.build.json --noEmit          # 通过，无输出
npx vitest run thread-shell / thread-viewport / thread-display-compat / thread-composer
npx vitest run chat-list / format.i18n / dock-file-tab / start-dock / pane-workbench / session-workbench
npx vite build                                   # ✓ built in 10.70s
```

测试结果：326 passed，0 failed（vitest 退出后写 `node_modules/.vite/vitest/results.json` 报 EPERM，属本机缓存权限，不影响结果）。

真实 GUI 核对（headless Edge，1600×900，带 bootstrapSecret 连本地 gateway）：

- eyebrow 节点数 0；recents 节点数 0
- `.thread-welcome` 系容器与 composer 的 max-width 计算值均为 `min(704px, 100%)`
- `--hero-composer-max-w: min(44rem, 100%)`
- 权限按钮计算色 `rgb(98,108,127)`（中性），不再为橙色
- 侧栏 `wew` → `New topic · 10/15`，`454` → `New topic · 9/30`

截图：`verifications/2026-10-22-landing-density.png`

## 追加轮（2026-10-22，修两条已知限制）

**组内标题去重**：`ChatList` 在分组结果上生成 `uniqueTitles`，同组内重复标题追加 ` · 2`、` · 3`。
截图可见 `New topic · 9/30` 与 `New topic · 9/30 · 2`、`打招呼` 与 `打招呼 · 2`。

**文件面板真实布局验证**：CDP 驱动 headless Edge（1600×900），依次点击会话行 → 打开 dock → 打开 Files → 点击文件，实测：

```json
{
  "externalPreviewPanels": 0,
  "inlinePreviews": 1,
  "dockWidth": 480,
  "fileTree": { "w": 209, "x": 1370 },
  "inlinePreview": { "w": 234, "x": 1124 },
  "chatColumn": { "w": 822, "x": 289 }
}
```

外置预览列为 0，文件树与预览同处一条 480px 的 dock rail（内部 209 + 234，可拖拽）——主布局由四列降为三列。截图：`verifications/2026-10-22-dock-inline-preview.png`。

补充验证：`tsc --noEmit` 通过；`vitest` 144 passed（chat-list / thread-shell / format / dock-file-tab / start-dock）；`vite build` ✓ 12.14s。

## 未覆盖

- 同一分钟创建的两条低信息会话仍可能同名（序号按组内出现顺序分配，排序变化时序号会跟随重排）。
