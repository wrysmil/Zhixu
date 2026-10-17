---
artifact: verification-lite
route: leader-direct
skills:
  - systematic-debugging
  - verification-before-completion
skills_evidence:
  - ~/.agents/skills/systematic-debugging/SKILL.md
  - ~/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户原话：「有问题啊一聊天页面变成这样了」＋「聊天框直接被盖住了」（附 WebUI 会话页截图）
  - 截图逐像素取证：`verifications/2026-10-17-chat-page-cover/`
created_at: 2026-10-17
tier: 1
---

# 会话页工作台 rail 压住输入区 — 轻量验证（Tier 1）

> **适用：** Leader 直做简单任务（≥2 写文件 / 跑测试 / fix 类），**不**走 spec/plan/WU 编排。
> **纪律：** Load `verification-before-completion`；先跑命令再给结论。

## 防跳过提醒

| 合理化借口 | 现实 |
|-----------|------|
| "只改了一行，不用验证" | 这次恰恰是「删掉一个 class」的行为修复，必须真机量盒模型 |
| "上次跑过了" | 修复前后各跑一次，红灯/绿灯都必须有输出 |
| "看起来没问题" | jsdom 不做布局，必须用真实浏览器量 rect |
| "太简单了，省掉吧" | 该 bug 正是因为「测试只断言 style 字符串」才漏掉 |
| "时间紧，跳过这次" | 跳过 2 分钟验证 = 后续 2 小时排查同类错位 |

## 范围

- 改动文件：
  - `webui/src/components/workbench/PaneWorkbench.tsx`（输入区宿主：去掉 `w-full`，让 rail 让位真正生效；<768px 不白让位）
  - `webui/src/tests/pane-workbench.test.tsx`（补回归断言：宽度不得钉成 100%；窄屏不施加让位）
- 路由判定：Tier 1（Leader 直做，无派发、无 worktree）
- Skill 缺失说明：routing.md 要求的 `source-driven-development` 本机未安装（`~/.agents/skills`、`~/.claude/skills` 均无），按其备用路径改读 `harness-kit/project.verification.md` + 相关产物（见下）

## 根因（Phase 1/2 取证结论）

会话页的输入区是 `createPortal` 到 `PaneWorkbench` 的 `<footer>` 里（`data-testid="workbench-composer-host"`）；
而「会话工作台 rail」是 `ThreadShell` 内部的 `w-[19rem] shrink-0` 常驻列（`hidden md:flex`，见 `ThreadShell.tsx:1893-1898`）。
两者不在同一层，所以 rail 的宽度必须由 `composerRailInset` 在宿主的右边距上补回来。

修复前宿主写的是 `mx-auto w-full max-w-[58rem]` + 内联 `margin-right: 19rem`：
`w-full` 把宽度钉成 `width:100%`，与 `margin-right` 构成**过约束**，
CSS 规范下 auto 边距退化为 0、右外边距被丢弃 → 19rem 让位失效。
真机实测（修复前）：宿主 `[290,1218]`（928 宽）vs rail `[1016,1320]` → **横向重叠 202px**，
错误横幅与其关闭按钮、输入卡片右半部都落进 rail 底下 → 用户看到的「聊天框直接被盖住了」。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `vitest run src/tests/pane-workbench.test.tsx`（**修复前**，TDD 红灯） | `2 failed \| 10 passed`：`expected '19rem' to be ''`（窄屏仍让位）、`className` 含 `w-full` |
| `vitest run src/tests/pane-workbench.test.tsx`（**修复后**） | `12 passed`，exit 0 |
| `vitest run`（webui 全量） | `Test Files 128 passed \| 2 skipped (130)`、`Tests 1685 passed \| 2 skipped (1687)`，exit 0 |
| `tsc -p tsconfig.build.json --noEmit` | exit 0（无输出） |
| `vite build` | `✓ built in 5.82s`，exit 0，产出 `../nanobot/web/dist`（8765 侧同源产物也已带上修复） |
| `eslint --config webui/eslint.config.js <touched> --max-warnings 0` | exit 0 |
| 真机 CDP 实测（桌面 1320×510，`probe-verify-fix.mjs`） | 宿主 `[290,1000]` w=710；rail `[1016,1320]`；**横向重叠 0、间隙 16px**；`scrollWidth==clientWidth==710` |
| 真机 CDP 实测（窄屏 600，`Emulation.setDeviceMetricsOverride`） | rail `0×0`（`hidden md:flex` 生效）；宿主 `inlineStyle=""`、宽 576 → 不再白让 19rem |

修复前后真机截图：`2026-10-17-chat-page-cover/repro-newsend.png`（前，rail 压住横幅与关闭按钮）、`verify-fix-wide.png` / `verify-fix-narrow.png`（后）。

> 注：本机 PATH 无 `bun`，上表以 `/usr/local/bin/node` 直跑 `vitest` / `tsc` / `vite build`，与 `bun run test` / `bun run build` 等价（均为同一 `node_modules/.bin` 脚本）。

## 未验证项

- **「左侧内容被横向裁掉」（横幅只剩正文尾巴、输入区「此计算机 / 访问权限」被切在会话列左边界）**：已确认是运行时 `scrollLeft ≠ 0`（用户确认「刷新后恢复正常」），但**尚未复现触发路径**。已排除：斜杠面板 + 方向键的 `scrollIntoView` 路径（实测全程 `scrollLeft` 为 0）。候选容器：`overflow-x: hidden` 但横向溢出 **1841px** 的消息行 `span.block.w-fit.max-w-full`（`overflow` 隐藏态用户无法手动滚回，程序化滚动后无法自愈）。需用户复现一次或另开专项。
- **错误横幅本身不是布局假报**：复现里模型请求真实失败（`Model provider request failed`），属 provider 配置/服务状态问题，与本次布局修复无关。
- 未做 Safari / Firefox 真机核对（仅 Chrome 1320×510 与 600 两档）。

## Next

- 任务完成 → 无需暂停（除非用户要求 commit/MR）
- 范围扩大 → 继续追「左侧被裁」触发路径时补 spec/plan 或升级 Tier 2 编排
