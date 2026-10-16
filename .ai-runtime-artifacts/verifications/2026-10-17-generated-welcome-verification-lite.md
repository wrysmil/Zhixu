---
artifact: verification-lite
route: leader-direct + imagegen + verification-before-completion
skills: [source-driven-development, brainstorming, imagegen, verification-before-completion]
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
  - /Users/mima0000/.agents/skills/brainstorming/SKILL.md
  - /Users/mima0000/.codex/skills/.system/imagegen/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - 用户：“UI的有些插画你可以考虑通过自己生图的方式来替换”
  - 用户：“可以的你可以自己生图”
  - .ai-runtime-artifacts/research/2026-10-16-agent-config-ui/illustration-prompt.md
  - .ai-runtime-artifacts/research/2026-10-16-agent-team-ui-research.md
  - .ai-runtime-artifacts/verifications/2026-10-10-zhixu-ui-polish-verification-lite.md
  - harness-kit/project.profile.md
  - harness-kit/context-map.md
created_at: 2026-10-17
tier: 1
status: completed
---

# 首页生成插画 — 轻量验证

## 上下文与范围

先扫描既有 specs/plans/decisions/research，读取现有首页视觉规范、Agent 生图提示与历史 UI 验证，再读取项目画像、模块索引和目标组件。现有 Agent 配置与团队插画已经接入，首页仍引用 agent-workspace.svg，因此本次只替换首页欢迎区图片。

采用既有森林绿、米白、纸感陶瓷材质的单 Agent 角色，新场景为整理笔记。用户已经授权自行生成插画，本次属有界素材替换，不新增交互或改布局，不另起需要阶段审批的设计/计划。实际 package.json 栈为 React ^18.3.1、Vite ^5.4.11、TypeScript ^5.7.2、Tailwind ^3.4.17；本次只替换静态 src，不调用新增框架 API，无新依赖。

## 改动文件

- webui/public/illustrations/agent-workspace-generated.webp：内置 image_gen 生成图片，经现有运行时 sharp 缩放与 WebP 编码；640 × 533，56,650 bytes（约 55.3 KiB）。alpha 最小 0、最大 255，isOpaque=false，保留真实透明背景。
- webui/src/components/thread/ThreadWelcome.tsx：只替换欢迎区插画 URL。尺寸、装饰性空 alt 与 aria-hidden、提示按钮及原有响应式规则沿用。
- 本验证记录及同名目录下两张浏览器截图。

原始生成图保留于 /Users/mima0000/.codex/generated_images/01a145b5-050b-7a60-9c14-4b78e7ce8a1c/exec-83318088-4a4b-40e9-9473-a922f13e559f.png。页面只引用仓库内的新 WebP。

## 命令与证据

| 验证 | 结果 |
| --- | --- |
| npm run build（webui） | exit 0；含 TypeScript 检查；2968 模块，Vite 构建 5.48s |
| npm run lint（webui） | exit 0，无输出错误 |
| npm run test -- --maxWorkers=2 --minWorkers=1 src/tests/preview-thread.test.tsx src/tests/preview-isolation.test.tsx src/tests/preview-narrow-shell.test.tsx | exit 0；3 文件，9 测试通过 |
| git diff --check | exit 0 |
| 生产构建图片 | nanobot/web/dist/illustrations/agent-workspace-generated.webp 存在，内容与源资源逐字节相同 |
| sharp metadata/stats | 640 × 533，RGBA，有非不透明 alpha |
| 浏览器实际首页 | 1255 × 960 浅色与深色主题已目视检查；角色与笔记完整，无矩形背景、不遮挡文案 |

截图：
- 2026-10-17-generated-welcome/home-light.png
- 2026-10-17-generated-welcome/home-dark.png

浏览器验证使用本地离线预览 http://127.0.0.1:5180/?preview=1#/home，实际共享 ThreadWelcome 组件。浅/深背景均能辨认脸、手和笔记；现有 190 × 165 CSS 图片区域可容纳素材，无需额外 CSS。未进行下一轮视觉修改。

## 验证边界

- 没有新业务逻辑，素材 URL 替换沿用现有回归测试，未新增镜像实现的测试或进行提交。
- 窄屏由现有测试覆盖侧栏响应；源 CSS 在 767px 以下隐藏整块装饰插画。本轮未在移动端真实浏览器尺寸下截图。
- 未运行全量测试、后端或 Electron 原生窗口验证；本次不涉及相关代码。
- 构建保留 Browserslist 数据过期、Tailwind ease 类歧义、StartDock 静态/动态混合导入、大 chunk 提示；相关测试有 KaTeX quirks mode 与 act 警告，未影响退出状态。
- 工作区先前存在的团队追踪/审查文件和 node_modules 未由本轮改动。

## 生成方式与实际提示词

使用内置 image_gen，transparent_background=true；agent-tools.png 仅作为角色、材质与配色参考。生成一次后压缩，不做二次重绘或抠图。

```text
Use case: stylized-concept.
Asset type: original compact illustration for the welcome screen of Zhixu, a quiet AI productivity workspace. This is an isolated illustration asset, not a UI screenshot.
Primary request: A single capable forest-green autonomous digital Agent calmly arranging loose notes into an orderly small open notebook. Convey turning ideas into clear next steps.
Reference image: the provided existing agent-tools.png is a style and character reference only. Match the matte paper/ceramic material, forest green shell, warm ivory edges, sage accents, tiny restrained amber hinges, dark inset face and two warm ivory eyes. Create a NEW scene and landscape composition rather than reproducing the reference layout.
Subject: one compact assistant with softly faceted modular body, focused friendly face and precise simple hands; one small open notebook and only two loose blank note cards, one subtle curved connection linking the cards. Keep notebook and hands clear at small display sizes.
Composition: compact landscape, approximately 6:5 aspect ratio. Agent on one side and notebook beside it, balanced. All objects fully inside the frame with 6 percent transparent padding. Large clear silhouettes, readable at 190 by 165 CSS pixels.
Style: sophisticated editorial 2.5D illustration, sculptural matte paper and fine-grained ceramic texture, soft warm diffuse lighting, restrained dimensional shading. Match a grown-up productivity application; understated and thoughtful.
Palette: forest green #345C49, warm ivory #EFEADF, sage #A9B6A1, charcoal #28372E, tiny amber #CB985C details.
Background: genuine transparent alpha; no scene, no floor, no enclosing rectangle, no painted checkerboard, no hard shadow.
Text: no words, no letters, no numerals, no branding, no watermark. Blank note pages with at most two simple short embossed lines.
Avoid: extra Agents, stock office workers, neon, purple, holograms, sparkles, glossy plastic toys, large props, tangled cables, sprawling networks, cropping the character or notebook.
```

## Next

本次首页素材替换已完成并验证；无提交、推送或发布动作。
