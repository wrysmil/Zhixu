---
artifact: research-report
route: web-investigator + source-driven-development
skills: [source-driven-development]
skills_evidence:
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/source-driven-development/SKILL.md
source:
  - 用户要求调研并优化单个智能体配置页
  - .claude/agents/web-investigator.md
  - webui/src/components/settings/agents/AgentEditorDialog.tsx
created_at: 2026-10-16
status: researched
---

# 单个智能体配置页参考调研

调研日期：2026-10-16（Asia/Shanghai）。范围：官方资料、公开截图及资源库。未登录修改任何外部平台，未修改业务代码。

## 已核实的官方事实

| 来源 | 职责、模型、工具及测试的组织方式 | 证据边界 |
| --- | --- | --- |
| [Dify：Build an Agent](https://docs.dify.ai/en/cloud/use-dify/build/new-agent/build) | 创建时填写名称，可选角色和描述；Configure 文档依次介绍 Model、Prompt、Skills、Files、Tools；环境变量归 Advanced Settings。右侧 Build / Preview 切换，Preview 不会修改配置。 | 页面注明新 Agent 为 beta，最后修改日期 2026-08-27。此顺序是文档章节顺序，不能宣称就是完整 UI 像素顺序。旧 `/en/guides/application-orchestrate/agent` 已跳转 Quick Start，勿引用旧路径作为当前界面证据。 |
| [Coze：Build your first AI assistant agent](https://www.coze.com/open/docs/guides/agent_quick_start) | 官方指南明确左侧 Persona & Prompt、中间 Skills、右侧 Preview&Debug；先写职责指令，按需加插件等能力，然后调试、发布。 | 搜索索引可读完整指南，直接读页工具遇到客户端渲染空白；此处布局来自官方文字说明，未声称查看当前登录态产品。 |
| [Relevance AI：Prompt](https://relevanceai.com/docs/build/agents/build-your-agent/prompt) | 文档分 Agent Profile、Model Selection、Prompt Instructions，并允许在指令中引用工具。 | 公开文档含界面图，已实际查看下方官方原图。 |
| [Relevance AI：Language Model](https://relevanceai.com/docs/build/agents/build-your-agent/agent-settings/language-model) | 详细模型设置位于 Advanced settings → Language Model；包括输出限制、温度、推理设置等。 | 这里只借鉴逐步展开的组织方式，不引入本项目没有的参数。 |
| [Relevance AI：Tools](https://relevanceai.com/docs/build/agents/build-your-agent/tools) | 工具有独立配置区；添加后再配置使用参数。 | 不把各家 Tools 与 Skills 视为完全等价术语。 |

Relevance 官方界面图：[Agent Builder overview](https://relevanceai.com/docs/images/_opt/agent/build_overview.webp)。已通过浏览器目视：左侧以 Prompt、Tools、Knowledge 等导航分区，中间突出名称、模型和大面积指令编辑区；右侧是资源区，顶部 Build / Run 切换。此图是文档截图，不能保证与今日登录态产品逐像素一致；其紫色选中态与背景不属于推荐配色。

## 针对本项目的建议（设计推断）

现有顺序为基础信息（ID 最先）→ 工具 → 技能 → 模型 → 提示词，右侧重复列出全部能力。用户首先想回答“这个智能体负责什么”，长列表和技术标识抢占了首屏。

建议采用可直接跳转的三个分区：

1. **职责与身份**：名称、简短说明、工作指令；默认打开，给指令足够空间。
2. **工具与技能**：分别选择，展示已选数量及必要摘要，不再旁边重复所有标签。
3. **模型与更多**：模型与 ID 等较少修改的字段集中；保留默认值和清楚的影响说明。

右侧只放单个 Agent 插画和精简配置摘要。已有配置区不应做成有强制前后顺序的向导；切换分区须保留输入。没有真实测试接入时不添加假调试框。以上是结合本项目约束的取舍，不能表述为“三家都这样做”。

视觉采用左对齐、明确主次和非等宽区域；冷暖浅渐变或轻纹理提供底色层次。遵守用户全部禁止项：不使用紫/靛蓝渐变、纯平背景、Hero 加三卡片、完美居中、术语空话、Emoji 功能图标、ease-in-out。

## 插画与图标

- [unDraw intelligence 搜索](https://undraw.co/search/intelligence)：已确认 AI Agent、Agentic Network、AI Research Assistant 等真实候选；已目视 AI Agent 弹窗，内容为显示器和大脑图形，对“单个 Agent 调工具”的表达偏弱。Agentic Network 更偏多节点关系，是否采用须看实际构图。主 Agent 已选择生成更贴合的插画，本报告不生图、不把 unDraw 原图交给模型改绘。
- [unDraw 许可](https://undraw.co/license)：允许免费商业使用及修改，但禁止用于 AI/ML 训练、开发和验证等用途；本轮仅作 UI 插画候选研究。
- [Iconify Lucide](https://icon-sets.iconify.design/lucide/)：已通过浏览器确认官方列表及 ISC 标识；已实际搜索确认 `lucide:bot`、`lucide:bot-message-square` 存在，可用于身份/试聊语义。
- 待实现时核验的导航候选：`lucide:wrench`（工具）、`lucide:sliders-horizontal`（模型与更多）、`lucide:brain-circuit`（技能）。本轮未逐个打开，不能声称均已下载核验。建议使用同一套线条风格、18–20 px，附带文字标签。
- [Iconify SVG 官方用法](https://iconify.design/docs/usage/svg/no-code/)与[SVG API](https://iconify.design/docs/api/svg.html)：可以取得 SVG。固定导航适合把核验后的少量图标作为本地资源，避免运行时依赖第三方网络；这是本项目交付建议。
- 此配置工具界面没有合适的真实照片或占位照片用途，不为凑资源插入 Pexels / Picsum 图片。

## 限制

未访问三家登录后的实际编辑器，未验证实时交互和响应式断点。Relevance 截图及 unDraw 弹窗已目视，但未保存本地截图文件；上文保留可访问官方原图。报告未授权引入新后端配置、自动保存或调试功能。

### Skills 使用

- `.claude/agents/web-investigator.md`：已加载；只读调研，产物写入规定 research 目录。
- `source-driven-development@C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/source-driven-development/SKILL.md`：已加载；仅引用官方来源，区分已核实事实与本项目设计推断。
