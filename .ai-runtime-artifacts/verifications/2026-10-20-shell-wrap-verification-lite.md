---
artifact: verification-lite
route: leader-direct
skills: [ui-ux-pro-max, source-driven-development, verification-before-completion]
skills_evidence:
  - harness-kit/.agents/skills/ui-ux-pro-max/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - 用户要求：模仿图 2 的主内容"包裹层"；左侧栏同样做圆角包裹；侧边栏按钮去重；右侧坞选项放大并参考图 4 背景；新增"打开终端面板"按钮（图 5）
  - .ai-runtime-artifacts/specs/2026-10-19-appearance-customization-spec.md
  - .ai-runtime-artifacts/plans/2026-10-20-sidebar-tools-v1.2-plan.md
created_at: 2026-10-20
tier: 1
status: passed
---

# WebUI 外壳包裹层、侧栏去重与右侧坞启动器改造验证

## 范围与上下文

已先扫描 `.ai-runtime-artifacts/` 中外观定制规格、侧栏 v1.2 计划与验证记录，读取 `project.profile.md`、`context-map.md`。本轮只改外壳视觉与侧栏入口，不扩展侧栏功能，不改后端协议。

## 变更

| 文件 | 变更 |
| --- | --- |
| `webui/src/globals.css` | 新增 `--app-canvas` 明暗 token；新增 `.app-shell-canvas`、`.app-shell-card`、`.app-shell-panel`、`.dock-launcher-surface`（细点阵 + 顶部柔光）；坞宽 336/384 → 360/420；移除 `host-sidebar-glass` 的右边框避免与卡片边框重叠。 |
| `webui/src/App.tsx` | 外层画布 + 左侧栏与主内容各自内嵌为 16px 圆角卡片（8px 外边距、6px 栏间缝）；新增顶栏终端按钮 `start-dock-terminal-toggle`；⌘J/Ctrl+J 打开终端面板；首页不再向 StartDock 传 `onCollapse`。 |
| `webui/src/components/dock/StartDock.tsx` | 空态启动器改为 76px 大卡片（44px 图标块、标题/描述、快捷键 kbd），卡片列表在坞内**垂直居中**（`m-auto`，内容超高时让位滚动）；终端卡片快捷键 ⌘J；订阅并消费"打开终端面板"意图，挂载前登记的请求不会丢。 |
| `webui/src/components/thread/ThreadHeader.tsx` | 侧栏开关仅在移动端渲染（`lg:hidden`），桌面端由左栏底部开关独占；删除已无意义的 `hideSidebarToggleForHostChrome`。 |
| `webui/src/components/thread/ThreadShell.tsx` | 同步移除 `hideSidebarToggleForHostChrome` 透传；会话覆盖层继续向 StartDock 传 `onCollapse`。 |
| `webui/src/lib/dock-store.ts`、`dock-shortcuts.ts` | 新增一次性终端面板意图（登记/订阅/消费）；快捷键解析新增 `j → terminal`。 |
| 10 个 locale | 新增 `dock.openTerminal`。 |
| `webui/src/tests/*` | 新增终端意图与 ⌘J 用例；侧栏开关去重后补首页无重复收起按钮的断言。 |

## 验证证据

| 检查 | 结果 |
| --- | --- |
| `npm run test -- --cache=false src/tests/start-dock.test.tsx src/tests/dock-shortcuts.test.ts` | 2 文件、26 项通过。 |
| `npm run test -- --cache=false src/tests/app-layout.test.tsx src/tests/i18n.test.tsx` | 2 文件、91 项通过。 |
| `npx eslint <9 个改动 TS 文件> --max-warnings 0` | exit 0。 |
| `npm run build` | `tsc -p tsconfig.build.json` 通过；vite 产物 9.64s 完成（esbuild 临时目录指向 `.ai-runtime-artifacts` 后成功）。 |
| 1440×900 实测 | 画布 `rgb(242,244,247)`；左栏 274×884 @ (8,8)，主卡片 1144×884 @ (288,8)，圆角 16px，栏间缝 6px。 |
| 右侧坞 | 坞宽 420；启动器卡片 395×76；点阵+柔光背景生效。 |
| 终端按钮 | 点击 `start-dock-terminal-toggle` 后生成 1 个终端标签、启动器消失；页面无 error。 |
| 暗色 | `html.dark`：画布 `rgb(11,14,19)`、卡片 `rgb(16,19,25)`，两卡片层次可见。 |

截图：[亮色外壳](2026-10-20-shell-wrap-light.png)、[右侧坞启动器](2026-10-20-dock-launcher.png)、[终端面板](2026-10-20-dock-terminal.png)、[暗色外壳](2026-10-20-shell-wrap-dark-2.png)、[暗色启动器](2026-10-20-dock-launcher-dark.png)。

## 运行环境说明

- 沙箱对 `C:\Users\Huangqh\.nanobot\sessions` 下**新建**锁文件返回 WinError 5，真实配置的 gateway 无法在本会话启动。为做视觉验证，改用工作区内临时实例：`.ai-runtime-artifacts/verifications/nanobot-ui-check/config.json`（测试密钥 `zhixu-ui-check-2026`、独立 workspace），监听 8765。
- 临时实例因此看不到真实会话。真实数据未受影响：`~/.nanobot/sessions` 仍有 21 个命名空间，主命名空间 `378bba77…` 下有 31 个会话文件。验证结束后已停止临时 gateway，8765 端口已释放，用户自行运行 `nanobot gateway` 即可恢复真实数据。
- 共享工作树的 `webui/node_modules/.vite` 被其他会话进程占用，vite 用 `vite-ui-check.config.mts` 指向独立缓存目录启动（5173），仍在运行。
- `memory/state.db` 曾被早期使用真实配置启动 gateway 的尝试改动，已还原并清理备份，无未提交数据变化。

## 未覆盖

- 未重跑整个 WebUI 测试套件，未重新打包 Electron，未在真实数据实例上回归。
- 未提交、未推送。
