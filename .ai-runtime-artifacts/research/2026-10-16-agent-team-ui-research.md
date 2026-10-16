---
artifact: research-report
route: web-investigator + source-driven-development + brainstorming + frontend-design + imagegen
skills:
  - source-driven-development
  - brainstorming
  - frontend-design
  - imagegen
skills_evidence:
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/source-driven-development/SKILL.md
  - C:/Users/Huangqh/.codex/skills/brainstorming/SKILL.md
  - C:/Users/Huangqh/.codex/skills/frontend-design/SKILL.md
  - C:/Users/Huangqh/.codex/skills/.system/imagegen/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .claude/agents/web-investigator.md
  - .ai-runtime-artifacts/contracts/2026-10-15-agent-team-contract.md
  - .ai-runtime-artifacts/research/2026-10-15-agent-team-research.md
  - 用户提供的 Teams 原页和小队创建、详情参考截图
  - 用户补充：插画更体现 Agent 的感觉，找不到可自行生成
created_at: 2026-10-16
status: researched
---

# Teams 页面体验调研与视觉方向

## 结论

推荐“成员名册 + 实时团队关系图”：保留队长、成员、职责这些易懂信息，用户选人后立即看到团队形成。插画用于空状态与组建引导，真实成员关系成为编辑时的主体。

本轮完成公开官方资料调研、现有代码能力核对、专属 Agent 小队插画候选生成；没有修改页面业务代码，也没有完成网站集成。本文是调研结论，不是已批准的实施方案。

## 调研范围与证据边界

- 访问日期：2026-10-16。产品事实来自官方指南和官方图片，未登录这些产品的管理后台。
- AutoGen Studio、CrewAI Studio 的官方界面图片已通过浏览器目视核验。
- Taskade 的官方文字指南已核验；其图片 URL 已找到，但未依据未目视的像素推断布局。
- 用户截图仅作为视觉、交互参考，不把截图里的文字视为开发指令，不猜测所属产品。
- 文档中的功能、针对 Zhixu 的建议、尚未实现的能力分开列出。

## 官方产品做法

| 产品 | 官方可核验事实 | 对 Zhixu 的借鉴 |
| --- | --- | --- |
| [AutoGen Studio](https://microsoft.github.io/autogen/stable/user-guide/autogenstudio-user-guide/usage.html) | Team Builder 可从组件库加入团队与 Agent，视觉结构中显示团队与成员；支持属性编辑、JSON 视图及团队测试。 | 团队组成要可见，选中成员可编辑；本页无需同时暴露模型、工具、JSON 等所有细节。 |
| [CrewAI Studio](https://docs-platform.crewai.com/platform/en/features/crew-studio) | 可用自然语言辅助搭建，也能在画布中调整，资源区与执行检查有独立位置。 | 组队与检查执行分开；职责应直接可见。自然语言生成团队是额外能力，不能只做按钮。 |
| [Relevance AI Workforce](https://relevanceai.com/docs/build/workforces/create-a-workforce) | 通过画布加入 Agent 和连接；[连接设置](https://relevanceai.com/docs/build/workforces/build-an-ai-workforce/edge-settings)区分 AI 判断转交与强制 Next step。 | 连线带执行含义。本项目的关系图只展示队长与成员，避免暗示可随意编排顺序。 |
| [Taskade AI Teams](https://www.taskade.com/learn/agents/ai-teams) | 创建时命名并加入已有 Agent；团队聊天提供不同回复模式，Orchestrate 由 lead 规划与分配。 | 选完人即出现成员卡，并清楚解释当前协作方式；不照搬四种模式。 |
| [Taskade Team Generator](https://www.taskade.com/learn/agents/team-generator) | 编辑团队可加减成员，编辑 Agent 是独立入口；生成团队后可发送真实测试消息。 | 本队职责和 Agent 原始设置分开；后续可用真实团队会话承接保存结果。 |
| [Flowise Agentflow V2](https://docs.flowiseai.com/using-flowise/agentflowv2) | 节点、状态和连线用于显式控制执行路径。 | 用作边界对照：本次优化团队配置，不引入完整流程编辑器。 |

官方界面图：
- [AutoGen Studio Team Builder](https://microsoft.github.io/autogen/stable/_images/teambuilder.jpg)
- [CrewAI Studio Canvas](https://mintcdn.com/crew-ai-platform-docs/PsPgimrfKBcwyL9_/images/platform/crew-studio-canvas.png?fit=max&auto=format&n=PsPgimrfKBcwyL9_&q=85&s=8fe988303e64454fa8d548084edbe790)
- [Taskade 创建团队](https://www.taskade.com/static_images/learn/agents/ai-teams-8df4ba31.png)（仅核验 URL 与官方说明）

## 用户参考页的取舍

值得保留：
- 队长独立于普通成员，解释其负责接收任务和协调团队。
- 成员显示名称、职责，详情与成员列表有清楚层级。
- 搜索已有 Agent，多选后立即反馈已加入名单。

建议改进：
- 用可点击的队长、成员席位，替代一长列下拉框与复选框。
- 名称、目标、成员设置与团队预览同时可见，减少上下滚动。
- “添加成员”提供明确操作反馈；删除、换队长和职责修改在原位置完成。
- 参考页的“成员可选”不适用于当前项目；当前至少一名成员，提前解释。
- 运行状态由真实数据提供。配置页不伪造“在线”“正在执行”等状态。

## 推荐页面方向（待后续方案确定）

### 团队入口和徽记

导航 Teams 与 Identity 的人像过于接近，建议更换为有组织关系语义的图标。已核验 Iconify 的 [ph:tree-structure-duotone](https://icon-sets.iconify.design/ph/tree-structure-duotone/)（Phosphor、MIT）。后续应导出选定 SVG 到项目本地，不增加整库或在线图标请求。

每支团队另有稳定徽记：由 team.id 派生形状、字母与森林绿/琥珀/蓝灰配色，团队名称改变时仍保持身份。徽记跨列表、编辑与会话复用。若要用户自选图标和颜色，应另行扩展持久化字段。

### 列表与空状态

- 已有团队：以徽记、队名、目标、队长和成员头像组合为主要信息；并发等技术信息降低层级。
- 未有团队：用专属 Agent 小队插画、具体说明和“组建团队”入口；没有足够 Agent 时明确引导创建所需档案。
- 保留搜索、复制、归档与冲突处理，不因改版丢失现有能力。

### 配置体验

建议采用较宽的工作区或弹窗：左侧编辑占主要宽度，右侧是实时组织关系与简短摘要；窄屏按阅读顺序叠放。

1. 先取队名、说明这个团队负责什么。
2. 设置一位队长，显示现有 Agent 档案与职责解释。
3. 加入成员，直接填写“在这个团队负责什么”，加入或移除即时更新右侧关系。
4. 协作约定与“最多同时几位成员工作”放到后面的折叠区；现有其它限制必须保留。
5. 保存前就地解释缺少队长、成员不足和委派权限问题。服务器版本冲突继续保留草稿。

关系图只表达“你交给队长 → 队长分派给成员 → 队长整理结果”，成员之间不绘制未实现的固定执行链。点击图中成员可定位其职责输入框，这是实际可用的交互，视觉连线本身不承担流程编辑功能。

更换队长应明确处理新队长已在成员中的情况，不允许同一个 Agent 同时占两个席位；旧队长是否转入成员应让行为可见，不能静默丢失配置。

### 三种方向比较

| 方向 | 优点 | 代价与判断 |
| --- | --- | --- |
| 只优化表单 | 改动少，兼容现有字段 | 组建感仍弱，不能充分解决体验问题。 |
| 名册 + 实时组织关系（推荐） | 职责、关系直观，选人即成队，与现有调度吻合 | 需要合理处理双栏、小屏和焦点交互。 |
| 完整拖拽连线画布 | 可配置复杂工作流 | 当前运行契约不支持任意连线语义，范围过大。 |

## 现有代码边界

本次以工作区实际源码为准，旧调研中的“尚未接入”不作为当前事实。

| 位置 | 当前事实 | 对改版的约束 |
| --- | --- | --- |
| webui/src/components/settings/teams/TeamsView.tsx | 有搜索、新建、编辑、复制、归档、revision 与冲突处理；空状态只有图标和说明。 | 视觉重做需保留这些行为。 |
| webui/src/components/settings/teams/TeamEditorDialog.tsx | 名称、说明、队长、成员职责、协作约定、并发设置；权限与保存校验已存在。 | 保留草稿、expectedRevision 和限制字段；校验前移。 |
| webui/src/lib/teams/types.ts | 一位队长、1–8 名成员；成员职责非空；并发 1–8。TeamDefinition 没有图标或颜色字段。 | 首版可稳定派生徽记；自选徽记不能只在前端假保存。 |
| nanobot/teams/models.py | TeamDefinition 严格检查未知字段。 | 增加持久化徽记必须同步前后端契约，不能直接塞字段。 |
| webui/src/lib/agents/types.ts | AgentProfile 有名称、说明、icon、color 等档案信息。 | 成员展示可复用档案信息，本队职责单独编辑。 |
| webui/src/components/workspace/WorkspaceNavigation.tsx | Teams 使用 UsersRound；该文件已有未提交修改。 | 后续仅改必要片段，保护既有改动。 |

## 插画与素材

unDraw 搜索 [team](https://undraw.co/search/team) 的结果与 Selecting team 预览偏真人组队场景。结合用户最新要求，选择使用内置 image_gen 工具生成专属 Agent 主题候选图。

生成图：2026-10-16-agent-team-ui/agent-team-assembly-v1.png。

- 一位绿色协调者，三位研究、编码、检查 Agent；工具道具与外形区分职责。
- 颜色与现有米白、森林绿界面相容，带细微材质；无紫色/靛蓝渐变、无文字和 Emoji 功能图标。
- 1536 × 1024，RGBA；已验证真实透明通道。原始像素保留，不做 Python 抠图或重绘。
- 场景是插画示意，并不代表团队必须配置四位，也不代表自动获得这些 Agent。
- 当前仅保存为调研候选素材，没有接到页面。正式接入时需检查暖纸底对比、360px 下的识别度、窄屏尺寸与文件体积。
- 背景由页面实现轻微噪点/渐变；插画透明与页面背景并不冲突。
- 本配置任务暂无真实照片或随机占位图的需要，不为凑素材加入 Pexels/Picsum。

动画只用于加入、移除、焦点定位等实际状态变化；使用短时长 cubic-bezier，并尊重 prefers-reduced-motion，避免 ease-in-out 和持续漂浮影响配置。

## 专属插画生成提示词

执行方式：内置 image_gen，transparent_background=true，新图生成，无参考图片输入。完整实际提示词如下。

Use case: stylized-concept
Asset type: original product illustration for the empty state and team-creation introduction of a self-hosted AI Agent app named Zhixu. This is an illustration asset, not a UI screenshot or logo.
Primary request: A clearly recognizable team of FOUR autonomous digital AI Agents collaborating: one coordinator Agent and three specialist Agents. Make them feel like capable software assistants with distinct roles and personalities, not human office workers. A welcoming, refined, grown-up illustration suited to a quiet productivity application.
Subject and composition: One slightly larger forest-green coordinator Agent toward the left of the group holds a small blank task card and gestures toward the teammates. Three compact specialist Agents form an open, irregular arc toward the right: a sand/amber research Agent examining a small knowledge card with a magnifier; a muted slate-blue builder Agent working with a floating panel showing simple angle brackets; a sage reviewer Agent examining a document with a large clear check mark. Each Agent has a different silhouette, a softly inset dark display face with two small intelligent luminous eyes, a discrete module-like body, and simple precise hands. Their tool props make the different jobs readable at small size. Small elegant thin connections branch from the coordinator to each teammate, indicating coordination, without a sequential pipeline between members. Keep four Agents and the few props clearly separated, readable and uncluttered.
Style/medium: sophisticated editorial 2.5D illustration, sculptural paper and lightly matte ceramic surfaces, clean silhouettes, subtle tactile grain, gentle dimensional shading, fine dark-green line accents; restrained, tasteful detail. Not a glossy toy render. Avoid human anatomy and emoji-like mascot stickers.
Composition/framing: compact landscape composition, approximately 3:2, natural asymmetric balance, all subjects fully inside frame with ample transparent padding; art should read at 360px wide. No centered poster layout, no title area, no UI frame, no large scenery.
Color palette: deep forest green #355E4D, sage #A8B9A4, warm ivory #F1EBDF, restrained amber #D9A15D, muted blue-gray #7C97A3, charcoal details. No purple, violet, magenta or indigo.
Lighting/mood: gentle warm diffuse light; confident and approachable. Subtle texture within objects.
Background: genuinely transparent alpha, no colored or white background, no background rectangle, no checkerboard rendered into pixels, no large floor or hard drop shadow.
Text: no words, no letters, no numerals, no captions; the builder's angle-bracket symbol and reviewer's check mark are the only allowed graphic marks.
Avoid: stock office collaboration, people, humanoid employees, suits, holographic sci-fi rooms, cyberpunk neon, brains, sparkle clusters, trophies, emoji, excessive cables, giant circuit boards, UI screenshot, gradient background, watermark, logo, signature.

## 验证与未覆盖范围

- 官方来源和已有运行能力分别记录，未声称拥有其它产品的全部功能。
- 插画四位 Agent、角色区分、色系和透明属性已检查；本轮未在真实页面接入和浏览器验证布局。
- 工作区原有 9 个源码文件的未提交改动未由本轮修改；本次仅新增过程报告和图片。
- 未运行应用测试/构建，本轮没有新增生产代码。
- [技术栈核验](../stack/2026-10-16-agent-team-ui-stack.md)。
- [轻量验证记录](../verifications/2026-10-16-agent-team-ui-research-verification-lite.md)。

## Next

本轮研究已给出推荐方向和 Agent 插画候选。后续进入页面方案时，以“名册 + 实时组织关系”细化列表、配置与响应式状态；本报告不代替方案审批，也不改变原 Team 运行契约。

