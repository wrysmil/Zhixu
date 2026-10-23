---
artifact: verification-lite
route: leader-direct
tier: 1
created_at: 2026-10-23
status: passed-with-known-unrelated-test-failures
skills: [source-driven-development, verification-before-completion, agent-browser]
skills_evidence:
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/source-driven-development/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
  - C:/Users/Huangqh/.agents/skills/agent-browser/SKILL.md
source:
  - 用户要求左上角改回纯文字 ZhiXu~
  - 用户要求首页插画换回之前使用的机器人形象
  - 用户修正文字品牌为字号更大、字距更宽、左对齐
---

# 知序文字品牌与首页机器人

## 范围与上下文

已扫描品牌、欢迎页和 rename 相关产物，读取 `project.profile.md`、`context-map.md`、2026-10-22 品牌验证记录及旧版机器人生成记录。

左上角不再使用图形 Logo，改为纯文字 `ZhiXu~`；首页欢迎插画换回旧版蓝白机器人。favicon、桌面图标和页面业务行为未改。机器人运行资产沿用 `webui/public/brand/zhixu-robot.webp`，未覆盖其他页面的 `zhixu-mascot.webp`。

## 变更

- `webui/src/components/workspace/BrandMark.tsx`：改为纯文字 `ZhiXu~`，使用 Century Gothic 600 字体，回退到 Avenir Next、Futura 和 Segoe UI Variable Display；字距设为 `0.015em`。
- `webui/src/components/Sidebar.tsx`：展开侧栏中的文字品牌为 18px，保持 12px 左边距，不做居中偏移。
- `webui/src/components/thread/ThreadWelcome.tsx`：首页插画改用 `brand/zhixu-robot.webp`，原始尺寸为 600 × 450。
- `webui/public/brand/zhixu-robot.webp`：28,038 字节，SHA-256 `F2300F02DC99E075D956BEBD3B721417AA7B2D33BECEA549AE159E53D3F070D6`。

TDD gate: N/A。此次为字体、排版和图片展示替换，没有新增业务逻辑或状态分支。

## 命令与结果

| 检查 | 结果 |
| --- | --- |
| 针对三个改动组件的 ESLint | 通过，0 问题 |
| `npm run build` | 通过，TypeScript 与 Vite 均退出 0 |
| `npm run test -- --no-cache --reporter=dot` | 1,868 通过、7 失败、2 跳过；失败分布在 6 个与本改动无关的既有测试文件 |
| `git diff --check` | 通过，仅有 LF/CRLF 提示 |
| 构建产物校验 | `webui/public/brand/zhixu-robot.webp` 已进入 `nanobot/web/dist/brand/zhixu-robot.webp` |

已知失败涉及 `useSessions`、`start-dock`、`workspace-navigation`、`preview-thread` 等既有测试，不由本次字体和插画替换引入。

## 浏览器证据

使用运行中的 `127.0.0.1:8765` WebUI 检查展开侧栏和首页：

- `ZhiXu~` 实际字号 18px、宽度约 60px、左边距 12px，左侧对齐。
- Century Gothic 字体族生效；字距计算值约 0.27px。
- 首页机器人真实加载为 600 × 450，渲染约 183 × 137。
- 浅色与深色模式均正常，页面横向溢出为 0。

截图：

- `.ai-runtime-artifacts/verifications/2026-10-23-wordmark-left.png`
- `.ai-runtime-artifacts/verifications/2026-10-23-wordmark-robot-home-light-final.png`
- `.ai-runtime-artifacts/verifications/2026-10-23-wordmark-robot-home-dark-final.png`

## Next

刷新已打开的 WebUI 即可查看。没有提交或推送。
