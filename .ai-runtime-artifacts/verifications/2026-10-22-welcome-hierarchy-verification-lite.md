---
artifact: verification-lite
route: leader-direct
tier: 1
created_at: 2026-10-22
status: passed
skills: [ui-ux-pro-max, source-driven-development, verification-before-completion]
skills_evidence:
  - harness-kit/.agents/skills/ui-ux-pro-max/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - 用户对比两张首屏截图后确认「图 2 骨架 + 图 1 层级」方向，要求试改一版
  - 对齐 2026-10-11-mira-home-sidebar-dock-visual-spec.md：首页标题保持在 28–32px，不回到 48px
---

# 欢迎页视觉层级调整

## 范围与上下文

Step 0 扫描 `.ai-runtime-artifacts/`，读取：

- `specs/2026-10-11-mira-home-sidebar-dock-visual-spec.md`：标题 28–32px、hero composer 44–46rem，理由是「标题和输入框同时过强」。
- `verifications/2026-10-22-zhixu-cute-brand-verification-lite.md`：欢迎页与品牌插画为共享组件，现有猫头鹰素材保留。
- 近期提交 `style(theme)`：默认石墨灰阶、主区纯白，因此不引入蓝紫渐变或重投影。

STACK：React 18.3.1 + Vite 5.4.11 + Tailwind 3.4.17 + TypeScript 5.7.2。纯布局/样式改动，未引入依赖或框架 API。

改动：

- `webui/src/components/thread/ThreadWelcome.tsx`：标题上方新增 `你好，我是知序 / HI, I'M ZHIXU` eyebrow（含中性圆点）。
- `webui/src/globals.css`：
  - eyebrow 样式（11px / 600 / 0.14em 字距）；
  - 标题改为 `clamp(1.65rem, 1.45rem + 0.5vw, 2rem)`，收字距、收行高，桌面不超 32px；
  - 说明文案 14 → 15px；
  - 快捷项从无边框文字链改为 4 列等宽细描边卡片，hover 用边框加深 + 1px 上浮 + 极轻投影；
  - 900px 以下卡片转 2 列，560px 保持 2 列，移动端隐藏插画的既有规则不变。

没有新增渐变、彩色强调或重投影；所有新颜色走 `--card` / `--border` / `--text-body` / `--text-strong` token，暗色由 token 自动适配。

## 命令与结果

| 检查 | 结果 |
| --- | --- |
| `npx vitest run --no-cache src/tests/thread-shell.test.tsx src/tests/preview-thread.test.tsx` | 2 个文件、81 个用例全部通过 |
| `git diff --check` | 通过（仅 LF/CRLF 提示） |
| 无头 Chrome + CDP，1440×900 / 1024×768 / 480×900 | 三种视口均渲染正常 |
| 中文 / 英文 locale | 中英文标题、eyebrow、卡片均不溢出；英文长标签自动换行且同行卡片等高 |

TDD gate: N/A（视觉样式与静态文案，无新增业务逻辑；既有用例回归通过）。

## 浏览器证据与边界

- `2026-10-22-welcome-zh-light.png`（1440×900，中文）
- `2026-10-22-welcome-en-light.png`（1440×900，英文）
- `2026-10-22-welcome-zh-1024.png`（1024×768，中文）
- `2026-10-22-welcome-zh-480.png`（480×900，中文，2×2 卡片）

使用 `?preview=1` 预览壳验证。预览壳以 `persist:false` 启动主题与语言，截图内切暗色无效，因此**未做暗色目视验证**；新增颜色全部来自既有 token，暗色理论上随 `appearance` 内联变量生效。未在已认证 Electron 窗口中复验，登录态下的实际数据差异（会话列表、模型选择器）不影响本区块。

## Next

改动未提交。可继续：在真实桌面壳中看暗色、把输入区工具条降噪（`完全访问权限` 收进 `+`）、或补顶部面包屑。
