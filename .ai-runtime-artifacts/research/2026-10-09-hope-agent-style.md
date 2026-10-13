---
name: hope-agent-前端风格取证
date: 2026-10-09
route: Tier 1 Leader 直做
source: 本机 D:\soft\Hope Agent\hope-agent.exe（Tauri + WebView2）+ http://127.0.0.1:8420 运行时
---

# Hope Agent 前端风格取证

## 取证方式

1. 快捷方式 `C:\Users\Public\Desktop\Hope Agent.lnk` → `D:\soft\Hope Agent\hope-agent.exe`（无 resources 目录，非 Electron）。
2. 启动后进程监听 `127.0.0.1:8420`，直接吐出 Vite/rolldown 构建的前端（`<title>Hope Agent</title>`）。
3. 下载产物：`research/hope-index.css`（278 KB，Tailwind v4 + oklch）、`research/hope-theme-init.js`。
4. 用浏览器打开 `http://127.0.0.1:8420/`，读取真实 DOM 的 computed style 交叉验证。

## 设计语言结论

**中性灰阶 + 分层表面 + 蓝色交互强调 + 小圆角 + 无渐变无玻璃。**

### 表面分层（light / dark）

| 层 | light | dark | 用途 |
| --- | --- | --- | --- |
| surface-app | `#f9fafb` | `#121212` | 窗口底 |
| background | `#fff` | `#121212` | 正文画布 |
| surface-panel | `#fcfcfc` | `#1a1a1a` | 侧栏面板（`shadow-panel` + `border-r-border-soft`） |
| card / popover | `#fafafa` | `#212121` | 卡片、弹层 |
| surface-floating | `#fff` | `#212121` | 输入坞（`shadow-floating`） |
| surface-sidebar | `#f3f4f7` | `#1f1f1f` | 76px 图标导航条 |
| surface-subtle | `#f0f1f5` | `#292929` | 更弱的分隔底 |
| secondary / accent | `#ededed` | `#2b2b2b` | 次级按钮、hover |
| muted | `#f2f2f2` | `#262626` | 静置块 |
| border | `#e3e3e3` | `#383838` | 主描边 |
| border-soft | `#e1e4ea` | `#333` | 分层之间的发丝描边 |
| foreground | `#121212` | `#f5f5f5` | 正文 |
| muted-foreground | `#575757` | `#b3b3b3` | 次级文字、markdown h1 |
| primary | `#171717` | `#fff` | 主 CTA（近黑／反白），primary-foreground 反转 |
| destructive | `#ef4343` | `#dc2828` | 危险 |

### 交互强调（唯一彩色）

- `--ha-focus-color: #006fd6`（dark `#70b6ff`），`--ha-focus-halo: #006fd629`（约 16% 透明蓝）
- `--ha-markdown-link: #339cff`（hover `#168af7`；dark `#66b5ff`）
- `--ha-resize-glow: #339cff80`
- 语义色只用于状态点：emerald / amber / red / sky，主色板保持中性

### 形状与排印

- `--radius: .5rem`；`--radius-panel: .5rem`；`--radius-floating: .75rem`；`--radius-input-dock: 1rem`
- 实测：按钮 `border-radius:12px`、输入坞 `16px`、发送按钮为 pill（`9999px`）
- 字体：`-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`；正文 14px、按钮 14px/500、markdown 13px
- 阴影：`shadow-panel` / `shadow-floating` 极弱，无渐变背景、无 backdrop-blur
- 微动效：`--ha-presence-enter-duration:.12s` / `exit .1s`，菜单进出位移 6px
- `body{user-select:none}`（桌面 App 手感），`-webkit-font-smoothing:antialiased`

## 映射到 Mira webui（不改功能交互）

Mira 现有 `webui/src/globals.css` 已是 shadcn HSL token 层，因此改风格集中在 token 层：

| Hope 特征 | Mira 落点 |
| --- | --- |
| 中性灰阶分层 | `:root` / `.dark` 全量色值替换（dark 由 `#303030` 改 `#121212` 系） |
| 小圆角 | `--radius*` 由 1.125–1.75rem 收到 .375–1rem |
| 蓝色焦点/链接 | `--ring`、`--inline-token-highlight`、`--temporary-*`、`::selection` |
| 无玻璃 | `.host-window-shell` / `.host-sidebar-glass` 去 backdrop-filter，改纯色 + 发丝描边 |
| 系统字体栈 | `tailwind.config.js` `fontFamily.sans` 前置 `-apple-system/Segoe UI` |

不改：布局结构、组件层级、事件与交互路径、i18n 文案、`--usage-token`（数据可视化色）。
