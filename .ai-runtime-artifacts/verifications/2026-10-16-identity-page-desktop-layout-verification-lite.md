---
artifact: verification-lite
route: leader-direct
skills:
  - ui-ux-pro-max
  - verification-before-completion
skills_evidence:
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
  - C:/Users/Huangqh/.qoder/skills/ui-ux-pro-max/SKILL.md
source: 用户报「身份页面在桌面版被挤压、变形，没有适配桌面版」→ 追加「表单不要堆在下面，用按钮切换分页」「保存时给个已保存提示」「记忆/身份页顶部那条独立操作栏去掉，和其他页面对齐」
created_at: 2026-10-16
tier: 1
---

# 身份页桌面版布局适配 + 分页编辑 + 保存通知 — 轻量验证（Tier 1）

## 范围

- 改动文件
  - `webui/src/components/workspace/IdentityPage.tsx`（栅格重排、分页编辑、保存提示）
  - `webui/src/App.tsx`（记忆/身份视图移除顶部独立操作栏）
  - `webui/src/tests/identity-page-editor.test.tsx`（新增：分页切换 + 保存提示）
- 路由判定：Tier 1（Leader 直做）；`webui/` 变更按 `harness-kit/project.verification.md` 以 webui 测试为最小验证。

## 一、根因（实测）

桌面壳默认窗口 1440×900，左侧栏 274px（收起 54px）后被 `max-w-[1180px]` 兜住，**左列实际只有 708px**；而 `PersonaGallery` 用**视口断点** `xl:grid-cols-5` + 首卡 `col-span-2 row-span-2`：

| 指标（修复前，视口 1426×864，Electron 壳实测） | 值 |
| --- | --- |
| 左列宽度 | 708px |
| 栅格轨道 | `132px ×5`（含首卡跨 2 列 ≈ 276px） |
| 每张卡片外框溢出（`scrollWidth > clientWidth`，被 `overflow-hidden` 裁切） | 5/5 张均为 `true` |
| 首卡高度（`row-span-2`） | 703px（内容只占顶部，下方大片空洞） |

即：卡片被压到 132px 宽 + 内容横向裁切，首卡撑成 703px 高空洞。

## 二、变更摘要

| 项 | 修复前 | 修复后 |
| --- | --- | --- |
| 性格卡片栅格 | `md:grid-cols-2 xl:grid-cols-5` + 首卡 `xl:col-span-2 xl:row-span-2` + 尾卡 `xl:mt-6` | `sm:grid-cols-2 xl:grid-cols-3`，首卡 `sm:col-span-2`（5 张卡片恰好铺满整行，无空洞） |
| 主区左右分栏 | `lg:grid-cols-[minmax(0,1fr)_360px]`（1024 视口时左列仅 ~390px） | `xl:` 起分栏；`2xl:max-w-[1400px]` 放宽大屏 |
| 表单 | 称呼/职责/语气/语言/底线/备注 6 项一路向下堆叠 | 4 步按钮切换（性格卡片 / 称呼与语气 / 底线与备注 / 高级 Markdown），配 上一步·下一步 |
| 语气选择 | `sm:grid-cols-4`（在半宽单元格里压到 ~62px） | `grid-cols-2`（2×2） |
| 保存反馈 | 仅按钮文案切「已保存」 | 顶部浮出通知（复用 `floatingSurfaceElevationClassName`，`role="status"`，2.6s 自动消失） |
| 记忆/身份页顶部 | `h-14` 独立操作栏（「记忆/身份」文字 + 月亮主题按钮 + `lg:hidden` 侧栏按钮） | 整条移除；原生标题栏留白改为容器 `showHostChrome && pt-10` |

## 三、验证命令与输出

### 1. Electron 桌面壳实测（真实窗口 1426×864，视口即桌面默认）

方法：`MIRA_DESKTOP_DEV=1` 起 Electron shell（独立 `--user-data-dir`）+ `--remote-debugging-port`，经 CDP `Runtime.evaluate` 量测；经 `Emulation.setDeviceMetricsOverride` 复现多档视口。

| 视口 | 左列 | 栅格轨道 | 卡片宽度 | 整页横向溢出 |
| --- | --- | --- | --- | --- |
| 1426×864 | 708px | `348px 348px` | 348px（首卡 708px） | 0 |
| 1280×800 | 708px | `348px 348px` | 348px | 0 |
| 1024×768 | 879px（单列） | `433.4px 433.4px` | 433px | 0 |
| 900×700 | 825px（单列） | `406.4px 406.4px` | 406px | 0 |

修复前同口径：`132px ×5`、5/5 卡片溢出、首卡 703px。

分页（1426×864，逐页点击量测）：

| 分页 | 面板高度 | 整页是否需滚动 |
| --- | --- | --- |
| 1 性格卡片 | 543px | 否 |
| 2 称呼与语气 | 387px | 否 |
| 3 底线与备注 | 256px | 否 |
| 4 高级 Markdown | 84px（折叠态） | 否 |

顶部栏移除后终态（1426×864）：`themeToggleButtons=0`、`chromeBars(h-14 header)=0`、`scrollerOverflow=0`、Save 按钮下沿距视口底 52px（修复前为 −9px，即被裁切）。

### 2. WebUI 测试

```bash
cd webui && npx vitest run
# Test Files  4 failed | 118 passed | 2 skipped (124)
# Tests       5 failed | 1619 passed | 2 skipped (1626)
```

失败项与本改动无关（已逐项归因）：

| 失败 | 归因 |
| --- | --- |
| `i18n.test.tsx > keeps every locale aligned...` | zh-CN 缺 `settings.teams.rosterCount_one/_other`（teams 特性遗留，`webui/src/i18n/` 工作树干净） |
| `add-project-dialog` ×2、`preview-thread`、`thread-shell` | 全量并发下的顺序/全局态抖动；单文件运行 3/4 通过 |
| （首轮）`app-layout > deletes a custom skill...` | 既有失败：临时还原 `App.tsx` 改动后单跑仍 `1 failed` |

新增用例：

```bash
cd webui && npx vitest run src/tests/identity-page-editor.test.tsx
# ✓ IdentityPage paged editor > shows one page at a time and switches through the step bar
# ✓ IdentityPage paged editor > confirms a save with a transient status notice
# Test Files 1 passed (1) | Tests 2 passed (2)
```

## 四、未验证项

- `tsc` / `vite build` 未在本机执行（沿用用户「构建由自己在环境里做」的约定）；`vitest` 已完成 TS 转译，未发现语法/类型阻塞性错误。
- 截图：CDP `Page.captureScreenshot` 在 Electron 窗口被遮挡时超时，最终态以几何量测 + 用户侧截图确认，未留存自动化截图。
- 桌面壳实测中为验证曾临时启动 `nanobot gateway`（8765）；如不再需要可自行结束该进程。
- 记忆页顶部栏与身份页走同一分支，量测在身份页完成；记忆页为同一 `view === "memory" || view === "identity"` 分支，未单独量测。

## Next

- 任务完成 → 无需暂停（用户已授权 commit + push）
- 范围扩大 → 补 spec/plan 或升级 Tier 2 编排
