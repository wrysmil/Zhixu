---
artifact: verification-lite
route: leader-direct
skills:
  - verification-before-completion
source:
  - 用户 2026-10-21 设计评审交接（欢迎页 5 条收敛规则 + 共享页头 3 条）
  - .ai-runtime-artifacts/research/2026-10-20-ai-assistant-visual-research-report.md
created_at: 2026-10-21
tier: 1
---

# 欢迎页与共享页头视觉收敛 — 轻量验证（Tier 1）

## 范围

- 改动文件：`webui/src/globals.css`（仅样式，无组件结构、无逻辑改动）
- 欢迎页 `.thread-welcome-*`：
  - H1 改 `--text-strong` 近黑、27px；品牌蓝只留给 eyebrow 6px 圆点与发送键。
  - eyebrow 紫色改 `--muted-foreground`，去掉紫色光晕。
  - 字号归一到 11 / 13 / 15 / 27：描述 12.5 → 15px，chips / 标签 / 列表标题 → 13px，列表摘要 → 11px。
  - 去盒子化：chips 边框与底色置透明、最近会话去掉逐行分隔线与图标底板、页脚标语隐藏；composer 保留唯一实体边框。
  - 吉祥物 186 → 96px、去掉光晕，响应式同步收敛。
- 共享页头 `.assistant-page-*`：
  - kicker 紫色改 `--muted-foreground` 并去掉光晕点，字号 10.5 → 11px。
  - 标题 `--text-display` → `--text-strong`。
  - 插画尺寸 128/104/176 → 96/80/128，900px 与 767px 断点同步下调。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `webui/node_modules/.bin/vitest run`（全量） | 142 passed / 3 failed files / 4 failed tests（见下「基线归因」） |
| `git stash push -- webui/src/globals.css` 后重跑 3 个失败文件 | 同样 4 failed / 67 passed，确认与本次 CSS 无关；已 `git stash pop` 恢复 |
| `cd webui && npm run build` | exit 0，`✓ built in 5.95s`，产物落到 `nanobot/web/dist` |
| `node .ai-runtime-artifacts/verifications/2026-10-21-welcome-converge-check.cjs` | 全部断言通过，`consoleErrors: []`；Chrome headless + 系统 Chrome |

### 基线归因（HEAD `2ccf7ea` 已存在，非本批引入）

- `src/tests/app-layout.test.tsx` — keeps a named group and its remaining pane active after deleting a pane
- `src/tests/appearance-adapters.test.tsx` — xterm wrapper root colors/font（PTY=true / PTY=false 两例）
- `src/tests/i18n-dock-parity.test.ts` — dock.* key set across 10 locales

### 浏览器核验要点（1440×960，Chrome）

| 检查项 | 实测 |
| --- | --- |
| H1 颜色 / 字号 | `rgb(26,33,50)`（--text-strong）/ 27px |
| eyebrow 颜色 / 圆点 | `rgb(98,108,127)` 灰 / `rgb(47,88,177)` 6×6px |
| 描述 / chips / 列表标题字号 | 15px / 13px / 13px |
| chip 边框与底色 | `rgba(0,0,0,0)`，composer 仍是唯一实体 |
| 最近会话分隔线 / 图标底板 | `0px` / 透明 |
| 页脚标语 | `display: none` |
| 吉祥物 | 96×96px，`halo display: none` |
| 共享页头（`#/memory`） | kicker 灰、标题近黑、compact 插画 80px |
| 窄屏 390px | 标题 24px、描述 13px、chips 13px、吉祥物隐藏 |

截图：

- `.ai-runtime-artifacts/verifications/2026-10-21-welcome-converge/welcome-1440.png`
- `.ai-runtime-artifacts/verifications/2026-10-21-welcome-converge/memory-1440.png`
- `.ai-runtime-artifacts/verifications/2026-10-21-welcome-converge/welcome-390.png`

## TDD 门禁

N/A（CSS-only，无生产逻辑变更）。视觉断言由上面的浏览器脚本以计算样式执行，非快照式测试。

## 未验证项

- 深色主题未单独截图；改动全部走语义 token（`--text-strong` / `--muted-foreground` / `--primary`），dark 变量已在 `:root.dark` 定义，但未做像素级核对。
- 仅验证 Chrome；Safari / Firefox 未跑。
- 视觉主观判断（是否达到 Qoder 式克制）需用户确认。

## Next

- 认可效果 → 如需提交，按 `git-xywh` 单独提交 `webui/src/globals.css` + 本验证产物。
- 继续推进 → 第二批可评估 Settings 内部卡片密度，或 Creative/Article 编辑器字号体系（不在本次范围）。
