---
route: Tier 1 Leader 直做
date: 2026-10-09
topic: hope-restyle
tier: 1
skills_evidence: []
approved: true
approval_quote: "请你使用电脑操作工作打开桌面版的hope Agent……根据他的风格为Agent-with-memory 重新改改这个页面的风格，但是主要功能的ui交互可以不用改。"
spec: ../research/2026-10-09-hope-agent-style.md
---

# 验证（lite）：Mira 前端按 Hope Agent 风格重塑

## 改动面（仅视觉层，未动交互）

| 文件 | 改动 |
| --- | --- |
| `webui/src/globals.css` | `:root` / `.dark` 全量色板换成 Hope 中性灰阶分层；`--radius*` 收到 .375–1rem；`--ring`/`--inline-token-highlight`/`--temporary-*` 由橙改 Hope 蓝；`::selection` 改蓝调；`.host-window-shell`/`.host-sidebar-glass` 去 backdrop-filter 与渐变，改纯色 + 发丝描边；模型徽章 fallback 橙色 → 蓝 |
| `webui/tailwind.config.js` | `fontFamily.sans` 前置 `-apple-system, BlinkMacSystemFont, "Segoe UI"`（Hope 的系统字体栈），CJK 兜底保留 |
| `webui/src/components/thread/ThreadComposer.tsx` | 输入坞由 `bg-muted/30` 灰块改为纯色浮层 + 发丝描边 + `focus-within:border-ring`；thread 态圆角对齐 hero（`rounded-prominent` = 1rem，Hope input-dock） |
| `webui/index.html` / `webui/public/manifest.json` | 预涂底色与 PWA `color_scheme_dark` `#303030` → `#121212`；正文色 `#0a0a0a` → `#121212` |
| `desktop/boot.html` | 启动页文字/按钮对齐新色板，补 `prefers-color-scheme: dark`（`#121212` / `#212121` / `#2b2b2b`） |
| `webui/src/tests/index-html.test.ts`、`tests/thread-composer.test.tsx` | 断言同步（`#121212`、`rounded-prominent`） |

未改：布局结构、组件层级、事件路径、i18n 文案、数据可视化色（`--usage-token` 琥珀）、状态点语义色。

## 证据

### 1. 全量测试（`npx vitest run`，82 文件）

```
Test Files  2 failed | 80 passed (82)
Tests  2 failed | ... passed
```
两个失败均为**基线既有**（见 `memory/project-env-gotchas.md`）：
- `app-layout > preserves the first message when the gateway rejects a project`（`toHaveFocus`，HEAD worktree 同样失败）
- `thread-viewport > renders markdown in prompt rail previews`（负载 flake）→ 隔离复跑 `EXIT=0` 通过

受影响面单独跑：`thread-composer + message-bubble` → `Tests 191 passed`（仅上述 app-layout 失败）。

### 2. 构建

`npm run build` → `✓ built in 9.91s`，`BUILD_EXIT=0`。

### 3. 运行时计算样式（vite dev :5199，真实 Chromium）

| 令牌 | 实测值 | Hope 目标值 |
| --- | --- | --- |
| `--background` | `0 0% 100%` → body `rgb(255,255,255)` | `#fff` ✓ |
| `--foreground` | `0 0% 7%` → `rgb(18,18,18)` | `#121212` ✓ |
| `--border` | `0 0% 89%` → `rgb(227,227,227)` | `#e3e3e3` ✓ |
| `--ring` | `203 100% 42%` | `#006fd6` ✓ |
| `--sidebar` | `225 20% 96%` | `#f3f4f7` ✓ |
| `--radius` / `-control` / `-prominent` | `.5rem / .75rem / 1rem` | panel .5rem、按钮 12px、dock 16px ✓ |
| `--inline-token-highlight` | `#006fd6` | Hope 焦点蓝 ✓ |
| 字体 | `-apple-system, BlinkMacSystemF…` | Hope 系统栈 ✓ |
| 控件 | 按钮/输入 `border-radius:12px`、`font-size:14px`、`font-weight:500` | Hope 一致 ✓ |

暗色（切换 `.dark` 后）：`--background 0 0% 7%`(#121212)、`--card 0 0% 13%`(#212121)、`--primary 0 0% 100%` + `--primary-foreground 0 0% 7%`（反白主按钮，Hope 同款）、`--ring 209 100% 72%`(#70b6ff)、`--sidebar 0 0% 12%`(#1f1f1f)、输入框 `rgb(51,51,51)` 描边。

### 4. 去玻璃化探针（Electron 离屏 + executeJavaScript）

```
light: shell bg rgb(249,250,251)  sidebar rgb(243,244,247) border rgb(225,228,234) backdrop none shadow none
dark:  shell bg rgb(18,18,18)     sidebar rgb(31,31,31)   border rgb(51,51,51)     backdrop none shadow none
```
对应 Hope 的 `--color-surface-app/#f9fafb`、`surface-sidebar/#f3f4f7`、`border-soft/#e1e4ea` 与暗色 `#121212 / #1f1f1f / #333`，`backdrop-filter` 已为 `none`。

### 5. 像素截图

`.ai-runtime-artifacts/research/screenshots/mira-light.png`、`mira-dark.png`
（Electron 离屏 `capturePage()`，1280×820，登录闸口页）：白底 + 近黑 pill 主按钮 / 暗色 `#121212` 底 + 反白主按钮，控件 12px 圆角。

**取证材料**：`.ai-runtime-artifacts/research/hope-index.css`（Hope 前端产物 CSS）、`hope-theme-init.js`、`2026-10-09-hope-agent-style.md`。

## 未覆盖 / 限制

- 认证后的完整界面（侧栏 + 会话列表 + 输入坞）像素级截图未取：webui 需 gateway 口令，未读取用户 `~/.nanobot/config.json` 的 secret。已用「运行时计算样式 + 玻璃探针 + 登录页像素」三条证据替代覆盖同一批令牌。
- `webui/src/components/ChatList.tsx` 的 `#ff8a3d` 状态点（运行中/需关注）与 Python 文件徽标色等语义色按原样保留。

## Next

- 需要的话：提交到 `feature/desktop-app`（当前工作区还含上一轮未提交的品牌资产改动，需分开 commit）。
- 需要像素级复核认证后界面：在 Mira 桌面版窗口内 `Ctrl+R` 重载（gateway :8765 已在服务刚构建好的 `nanobot/web/dist`）。
