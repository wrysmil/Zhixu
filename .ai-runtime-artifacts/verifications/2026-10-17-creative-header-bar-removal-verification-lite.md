---
artifact: verification-lite
route: leader-direct | 小改动直做
skills:
  - verification-before-completion
skills_evidence:
  - ~/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户截图（创作视图顶部栏被红框圈出）+ 原话「把上面这一栏去掉」
created_at: 2026-10-17
tier: 1
---

# 创作视图顶部栏移除 — 轻量验证（Tier 1）

> **适用：** Leader 直做简单任务（跑测试 / 有 UI 行为变更），**不**走 spec/plan/WU 编排。
> **纪律：** Load `verification-before-completion`；先跑命令再给结论。

## 防跳过提醒

| 合理化借口 | 现实 |
|-----------|------|
| "只是删了一个 header" | 删 header 会连带 `PanelLeftOpen` 变成未使用导入，`noUnusedLocals: true` 直接让构建失败 |
| "看起来没问题" | 看起来 ≠ 验证过 |

## 范围

- 改动文件：`webui/src/App.tsx`（唯一写文件）
- 移除对象：`view === "creative" | "article"` 分支里 `h-14` 的顶栏（`创作空间` 文案 + 主题切换按钮 + 移动端 `lg:hidden` 侧栏开关）
- 顺带处理：删除因此不再被引用的 `PanelLeftOpen` 导入（`tsconfig.json` 开启 `noUnusedLocals`）
- 布局补偿：容器从 `absolute inset-0 overflow-y-auto bg-background` 改为按 `showHostChrome` 追加 `pt-10`，与 memory/identity 视图的既有写法一致，避免原生宿主 chrome 下内容被顶栏遮挡
- 路由判定：Tier 1（Leader 直做）

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `cd webui && ./node_modules/.bin/tsc -p tsconfig.build.json` | `TSC_EXIT=0`（无类型错误） |
| `cd webui && ./node_modules/.bin/vitest run src/tests/creative-workspace.test.tsx src/tests/creative-panels.test.tsx src/tests/writing-partner-panel.test.tsx` | `Test Files 3 passed (3)` / `Tests 41 passed (41)` |
| `cd webui && ./node_modules/.bin/vitest run src/tests/app-layout.test.tsx` | `Test Files 1 passed (1)` / `Tests 69 passed (69)` |
| `cd webui && ./node_modules/.bin/vite build` | `✓ built in 6.45s`，`VITE_EXIT=0`，产物落 `nanobot/web/dist` |
| `curl -s -i http://127.0.0.1:8765/` | `HTTP/1.1 200 OK`、`Cache-Control: no-cache`、`index.html` mtime 15:47（新构建已被网关托管） |

## 未验证项

- 未做浏览器像素级截图比对：本次为纯删除 + 容器 padding 调整，未新增交互；真实渲染由用户刷新页面后确认。
- 移动端（`<lg`）回归：原顶栏内的 `lg:hidden` 侧栏开关随顶栏一并移除，`creative/article` 视图在窄屏下不再有侧栏入口（与 memory/identity 视图现状一致）。桌面端（`lg+`）侧栏为常驻，不受影响。

## Next

- 任务完成 → 无需暂停（除非用户要求 commit/MR）
- 范围扩大 → 补 spec/plan 或升级 Tier 2 编排
