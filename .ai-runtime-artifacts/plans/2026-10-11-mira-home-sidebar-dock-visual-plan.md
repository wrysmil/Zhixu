---
artifact: plan
route: writing-plans
batch: mira-home-sidebar-dock-visual
skills:
  - writing-plans
created_at: 2026-10-11
spec: .ai-runtime-artifacts/specs/2026-10-11-mira-home-sidebar-dock-visual-spec.md
contract: 无新增契约（视觉批次，依赖现有 dock-store / TerminalSupervisor / browser-url 白名单均不动）
worktree: .harness-worktrees/zhixu/wt-mira-home-sidebar-dock-visual
branch: harness/wt-mira-home-sidebar-dock-visual
---

# Mira 首页 / 左侧导航 / 右侧 dock 视觉优化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按规格把 Mira 首页从「宽大的欢迎页」收敛为安静、稳重的工作台；左轨道 + 会话列 + 右 dock + 浏览器 + 终端统一字阶、间距、图标与状态规则；不改路由 / PTY / 浏览器白名单 / 后端协议 / i18n 键结构 / 数据契约。

**Architecture:** 视觉批次，三组解耦、共用 token 先行。
- **A（leader直做）**：在 `globals.css` + `tailwind.config.js` + `App.tsx` 暴露 CSS 变量，作为后续两组的唯一宽度 / 字号 / 内边距来源。**不动组件视觉。**
- **B（parallel worker）**：左轨道宽度 + 首页 hero/composer 字号。
- **C（parallel worker）**：右 dock 宽度 / 顶栏高 / 启动器卡片、终端字号与高度、浏览器工具栏 32–36px 控件。
- **D（tail）**：尾盘回归 + 截图 + 审查。

**Tech Stack:** webui React 19 + TS + Tailwind 3.4.19（仅用 token / utility class，无新依赖）；@xterm/xterm 6 + `@xterm/addon-fit`（已就位，不升级）；lucide-react 0.469（已就位）。

**Spec:** `.ai-runtime-artifacts/specs/2026-10-11-mira-home-sidebar-dock-visual-spec.md`（§1–§6 全量落地）
**Hard constraints:**
- 不动现有任何 i18n 键结构与默认值（避免 v1.1 批次新增键以外的扩散）
- 不动 `dock-store.ts` / `TerminalSupervisor` / `browser-url` 白名单 / iframe sandbox / PTY 契约 v2
- 不动 `nanobot/webui/*` 后端；不引入依赖
- 用户未提交工作区改动（`docs/记忆系统/*.drawio.bkp`、`memory/`）保留不覆盖
- 文案调整必须随现有 locale 同步（仅在键值确实需要时改）

## Global Constraints

- 全程中文注释 / 文档；代码标识符英文；产物只落 `.ai-runtime-artifacts/`。
- Worktree：`/Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/zhixu/wt-mira-home-sidebar-dock-visual`（分支 `harness/wt-mira-home-sidebar-dock-visual`，base `feature/desktop-app` HEAD）。
- 三组文件零交集（Group A 完成后 B 与 C 才并发启动；B 与 C 文件集彼此不重叠，详见各 Task）。
- 验证：`cd webui && npm run test` + `cd webui && npm run build` + `cd webui && npm run lint`（本机无 bun，全部 npm）。后端命令不动。
- 每 Task 一个 commit；只 commit 不 push；不覆盖用户未提交文件。
- `globals.css` 修改仅追加 `:root` / `.dark` CSS 变量与少量共用 utility（`.dock-panel`、`.browser-toolbar`）；不重写现有变量。
- i18n：仅在规格 §1 / §3 明确要求调整文案时改对应 locale 的键值；其余复用现有键。

## File Structure

| 动作 | Task | 文件 | 职责 |
| --- | --- | --- | --- |
| Modify | A | `webui/src/globals.css` | 新增 CSS 变量 `--rail-w`(54px)、`--chat-col-w`(220px)、`--dock-w`(336px)、`--dock-w-xl`(384px)、`--dock-header-h`(40px)、`--browser-toolbar-h`(36px)、`--terminal-font-size`(12.5px)、`--hero-text-size`(30px)、`--hero-composer-max-w`(44rem)；亮暗主题都覆盖 |
| Modify | A | `webui/tailwind.config.js` | theme.extend 暴露 `width.rail/dock/dockXl/chat`、`height.dockHeader/browserToolbar`、`fontSize.hero/terminal`、`maxWidth.heroComposer` |
| Modify | A | `webui/src/App.tsx` | `SIDEBAR_WIDTH=54`、`SIDEBAR_RAIL_WIDTH=54`、`SIDEBAR_CHAT_WIDTH=220`；`MOBILE_SIDEBAR_WIDTH` 用相加值；注释标「视觉规格 v1.1」 |
| Modify | B | `webui/src/components/workspace/WorkspaceNavigation.tsx` | rail 类名 `w-16` → `w-rail`；轨道内边距略紧 |
| Modify | B | `webui/src/components/thread/ThreadShell.tsx` | `HeroGreeting` h1 `text-[34px] sm:text-[48px]` → `text-hero`；标题最大宽度保持 `max-w-[44rem]` |
| Modify | B | `webui/src/components/thread/ThreadComposer.tsx` | hero `max-w-[58rem]` → `max-w-heroComposer`；`isHero ? "max-w-[58rem]"` 三处统一替换 |
| Modify | C | `webui/src/components/dock/StartDock.tsx` | aside `w-[26rem] xl:w-[30rem]` → `w-dock xl:w-dockXl`；header `h-9` → `h-dockHeader`；启动器卡片改为轻量功能行；删除嵌套 shell select 行 |
| Modify | C | `webui/src/components/workbench/BrowserSection.tsx` | toolbar `h-6` → `h-browserToolbar`；地址框 `text-[11.5px]` → `text-[13px]`；按钮 36×36 |
| Modify | C | `webui/src/components/workbench/TerminalSurface.tsx` | xterm 容器 `h-72 w-full` → `min-h-0 w-full flex-1`；加 `text-terminal` 字号 |
| (Tail) | D | `.ai-runtime-artifacts/verifications/2026-10-11-mira-home-sidebar-dock-visual-*.md` | 截图像素核查 + collective-test + code-review |

---

# Group A — 共用 token 与布局常量（Leader 直做，prereq）

> A 必先完成；完成后 B、C 并发启动。

### Task A.1：globals.css 新增视觉 token

**Files:**
- Modify: `webui/src/globals.css`（仅 `:root` 与 `.dark` 块内追加；不重写现有变量）

**Interfaces:**
- 新增 CSS 变量（亮 / 暗主题都要写）：
  - `--rail-w: 54px;`
  - `--chat-col-w: 220px;`
  - `--dock-w: 336px;`
  - `--dock-w-xl: 384px;`
  - `--dock-header-h: 40px;`
  - `--browser-toolbar-h: 36px;`
  - `--terminal-font-size: 12.5px;`
  - `--hero-text-size: 30px;`
  - `--hero-composer-max-w: 44rem;`

**Steps:**
- [ ] **Step 1**：在 `:root` 块的 `--radius-modal:` 后追加上述 9 个变量。
- [ ] **Step 2**：在 `.dark` 块对应位置追加同名变量（值与亮主题一致）。
- [ ] **Step 3**：`cd webui && npm run lint` 退出码 0。

### Task A.2：tailwind.config.js 暴露给 utility

**Files:**
- Modify: `webui/tailwind.config.js`

**Interfaces:**
- `theme.extend.width`: 新增 `rail: "var(--rail-w)"`、`chat: "var(--chat-col-w)"`、`dock: "var(--dock-w)"`、`dockXl: "var(--dock-w-xl)"`。
- `theme.extend.height`: 新增 `dockHeader: "var(--dock-header-h)"`、`browserToolbar: "var(--browser-toolbar-h)"`。
- `theme.extend.fontSize`: 新增 `hero: ["var(--hero-text-size)", { lineHeight: "1.15" }]`、`terminal: ["var(--terminal-font-size)", { lineHeight: "1.45" }]`。
- `theme.extend.maxWidth`: 新增 `heroComposer: "var(--hero-composer-max-w)"`。

**Steps:**
- [ ] **Step 1**：定位 `theme.extend`，按接口插入 4 块。
- [ ] **Step 2**：`cd webui && npm run build` exit 0；`cd webui && npm run lint` exit 0。

### Task A.3：App.tsx 宽度常量同步

**Files:**
- Modify: `webui/src/App.tsx`（仅改三个常量数值）

**Interfaces:**
- `SIDEBAR_WIDTH = 54`（旧 64）
- `SIDEBAR_RAIL_WIDTH = 54`（旧 64）
- `SIDEBAR_CHAT_WIDTH = 220`（旧 248）
- `MOBILE_SIDEBAR_WIDTH = min(${SIDEBAR_WIDTH + SIDEBAR_CHAT_WIDTH}px, calc(100vw - 0.75rem))`（保持模板，仅数值自动加和）
- 注释：「视觉规格 v1.1：54 / 220 替换 64 / 248」。

**Steps:**
- [ ] **Step 1**：替换 3 个常量；保留 `MOBILE_SIDEBAR_WIDTH` 模板。
- [ ] **Step 2**：`cd webui && npm run test` exit 0。

---

# Group B — 左轨道 + 首页（与 C 并发，文件零交集）

> B 启动前提：A.1–A.3 已 commit。
> B 文件集：`WorkspaceNavigation.tsx`、`ThreadShell.tsx`、`ThreadComposer.tsx`。

### Task B.1：左轨道宽度收紧

**Files:**
- Modify: `webui/src/components/workspace/WorkspaceNavigation.tsx`

**Steps:**
- [ ] **Step 1**：定位 line ~158 处的 `workspace-navigation-rail flex w-16 min-h-0 shrink-0 ...`，把 `w-16` 改为 `w-rail`。
- [ ] **Step 2**：`cd webui && npm run lint` exit 0。

### Task B.2：首页 hero 字号

**Files:**
- Modify: `webui/src/components/thread/ThreadShell.tsx`（仅 `HeroGreeting` 组件内的 h1 类名）

**Steps:**
- [ ] **Step 1**：h1 类名 `select-none whitespace-nowrap text-[34px] font-normal leading-[1.08] tracking-normal text-foreground sm:text-[48px] sm:leading-tight` → `select-none whitespace-nowrap text-hero font-normal leading-tight tracking-normal text-foreground`。
- [ ] **Step 2**：`cd webui && npm run test` 通过。

### Task B.3：Composer hero 容器宽度

**Files:**
- Modify: `webui/src/components/thread/ThreadComposer.tsx`

**Steps:**
- [ ] **Step 1**：grep `max-w-\[58rem\]` 在 `ThreadComposer.tsx`，逐处替换 hero 分支为 `max-w-heroComposer`；保留 thread 分支 `max-w-[49.5rem]`。
- [ ] **Step 2**：`cd webui && npm run test` 通过；`npm run lint` exit 0。

---

# Group C — 右 dock / 浏览器 / 终端（与 B 并发，文件零交集）

> C 启动前提：A.1–A.3 已 commit。
> C 文件集：`StartDock.tsx`、`BrowserSection.tsx`、`TerminalSurface.tsx`。

### Task C.1：StartDock 宽度 + 顶栏 + 启动器

**Files:**
- Modify: `webui/src/components/dock/StartDock.tsx`

**Steps:**
- [ ] **Step 1**：aside `hidden w-[26rem] shrink-0 ... md:flex xl:w-[30rem]` → `hidden w-dock shrink-0 ... md:flex xl:w-dockXl`。
- [ ] **Step 2**：header `h-9` → `h-dockHeader`。
- [ ] **Step 3**：启动器空态三张卡片改为「轻量功能行」：去掉圆角边框与背景，保留 hover 提示；按钮 `gap-2.5 px-3 py-2.5` → `gap-3 px-2 py-2`。
- [ ] **Step 4**：删除 shell select 行（`<label>` 与 `<select>` 整段移除；+ 菜单里 shell 子菜单保留作为唯一入口）。
- [ ] **Step 5**：`cd webui && npm run test -- StartDock` 通过；`npm run lint` exit 0。

### Task C.2：BrowserSection 工具栏升级到 32–36px

**Files:**
- Modify: `webui/src/components/workbench/BrowserSection.tsx`

**Steps:**
- [ ] **Step 1**：`buttonClass` `grid h-6 w-6 ...` → `grid h-browserToolbar w-9 ...`（按钮 36×36 命中目标）。
- [ ] **Step 2**：地址框 `h-6 ... text-[11.5px]` → `h-browserToolbar ... text-[13px]`。
- [ ] **Step 3**：form `gap-1` → `gap-1.5`；错误文案 `text-[11.5px]` → `text-[12px]`。
- [ ] **Step 4**：`cd webui && npm run test` 通过；`npm run lint` exit 0。

### Task C.3：TerminalSurface 占满 dock 余高 + 字号

**Files:**
- Modify: `webui/src/components/workbench/TerminalSurface.tsx`

**Steps:**
- [ ] **Step 1**：xterm 容器 `h-72 w-full ... [&_.xterm]:h-full [&_.xterm]:w-full` → `min-h-0 w-full flex-1 ... text-terminal [&_.xterm]:h-full [&_.xterm]:w-full`。
- [ ] **Step 2**：status 文案 `text-[11.5px]` → `text-[12px]`。
- [ ] **Step 3**：`cd webui && npm run test` 通过；`npm run lint` exit 0。
- [ ] **Step 4**：实机回归（worktree 内 `npm run dev` + 启动 dock terminal 标签）：PTY 模式输入 / 输出 / 滚动 / resize 正常；行式降级可提交。

---

# Group D — 尾盘（Leader + verifier + reviewer）

> D 启动前提：B 与 C 全部 commit。

### Task D.1：截图像素核查

**Files:**
- Create: `.ai-runtime-artifacts/verifications/2026-10-11-mira-home-sidebar-dock-visual-screenshots.md`
- Create: `.ai-runtime-artifacts/verifications/assets/2026-10-11-mira-home-sidebar-dock-visual/*.png`（3 分辨率 × 2 主题 = 6 张；启动页 + dock + browser + terminal 各场景）

**Steps:**
- [ ] **Step 1**：worktree 内 `npm run dev`，手工跑三个浏览器尺寸（1280×800 / 1440×900 / 1024×768），切亮 / 暗主题。
- [ ] **Step 2**：截图 6 张落盘；markdown 报告列出每张图的命中点（rail 宽 54、dock 宽 336/384、hero 字号、composer 宽 44rem、浏览器工具栏 36、终端占满余高）。
- [ ] **Step 3**：对照规格 §验收口径 1–6 写出 PASS / FAIL。

### Task D.2：collective-test + code-review + security-review

**Files:**
- Create: `.ai-runtime-artifacts/verifications/2026-10-11-mira-home-sidebar-dock-visual-collective-test.md`
- Create: `.ai-runtime-artifacts/reviews/2026-10-11-mira-home-sidebar-dock-visual-code-review.md`
- Create: `.ai-runtime-artifacts/reviews/2026-10-11-mira-home-sidebar-dock-visual-security-review.md`

**Steps:**
- [ ] **Step 1**：collective-test：`npm run test / lint / build` 全量结果；v1.1 PTY / dock store / browser URL 白名单最小回归。
- [ ] **Step 2**：code-review：视觉 vs 行为（不应破坏：路由、PTY 状态、键盘 ⌘P / ⌘T、tab 关闭、shell 选择、拒嵌外开、ResizeObserver 上报、TerminalSupervisor 上限 8）。
- [ ] **Step 3**：security-review：仅视觉、iframe sandbox 不变、URL 白名单不动。

### Task D.3：合流与 artifacts 落盘

**Files:**
- Modify: `.ai-runtime-artifacts/execution-logs/2026-10-11-mira-home-sidebar-dock-visual-execution-log.md`
- Modify: `.ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-11-mira-home-sidebar-dock-visual.md`
- Git: 合流 `harness/wt-mira-home-sidebar-dock-visual` → `feature/desktop-app`

**Steps:**
- [ ] **Step 1**：worktree 内 git log --oneline 列出本批 commit；处理主 checkout 未提交改动（与用户确认提交 / stash / 精确恢复）。
- [ ] **Step 2**：Leader 执行合流（按 `git-xywh` + `project.git.md`；用户确认 push 与否）。
- [ ] **Step 3**：执行日志与 tracking 文件 final 更新；列出 changed files / simplifications / remaining risks。

---

## Next

批准后由 Leader：① 创建 worktree + 初始化分支基线；② Group A 串行执行；③ A 完成后并发派发 Group B / C worker；④ 收口 Group D。
取消：`$cancel`。
裁剪：若用户认为视觉批次需收紧范围，可只跑 Group A + B 或 A + C。
