# 评估分支前端测试修复验证（2026-10-23）

分支：`harness/agent-evaluation-calibration`（2eb4cd5）

## 背景

切到评估分支后全量 `vitest run` 出现 5 个失败（1870/1877 通过）。根因均为桌面端视觉重构合入后测试断言未跟上，非功能回归；其中 app-layout 一条实为竞态（composer 惰性挂载自动聚焦）。

## 修复内容

| 文件 | 修复 |
| --- | --- |
| `webui/src/tests/i18n-dock-parity.test.ts` | `DOCK_KEYS` 清单补 `openTerminal`、`terminalTheme`（10 个 locale 实际已同步，是硬编码清单过期；两键被 `App.tsx` / `TerminalSurface.tsx` 真实消费） |
| `webui/src/tests/useSessions.test.tsx` | 低信息兜底标题期望改为 `New topic · 4/16`（`deriveTitle` 有意为含糊标题追加 updatedAt 短日期） |
| `webui/src/tests/workspace-navigation.test.tsx` | 聊天视图展开聊天栏时 rail 不再渲染收起按钮（入口移交顶部品牌行）；断言改为「chat 视图无该按钮 + creative 视图点击生效」 |
| `webui/src/tests/preview-isolation.test.tsx` | 品牌文案随欢迎页改版按语言切换，改用语言无关的 `hero-greeting` testid |
| `webui/src/tests/app-layout.test.tsx` | 竞态修复：工作台线程惰性挂载时会自动聚焦 composer（`dock.composerAria` = `Message {{title}}`），焦点离开 Radix 菜单内容会立即触发 `onOpenChange(false)` 关闭菜单。开菜单前先 `waitFor` composer 聚焦完成 |

## 排查记录（app-layout）

- 探针确认：点击 pane 菜单 Delete 后 `getSessionAutomations` 从未被调用；`findByRole` 命中的菜单项在返回时已 `isConnected === false`（触发器与侧栏仍连接，无重挂载）。
- 在 `handleActionMenuOpenChange` 打点 + document capture 监听：菜单打开后焦点序列 `menu content → Message Alpha → Message New topic`，Radix 因 focus outside 连发两次 `onOpenChange(false)`。

## 结果

```
Test Files  145 passed | 2 skipped (147)
     Tests  1875 passed | 2 skipped (1877)
```

2 个 skipped 为既有行为（全量基线一致），无新增跳过。涉及源码文件 `ChatList.tsx` 仅临时打点、已还原，无净改动。
