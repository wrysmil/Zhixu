---
artifact: spec
route: brainstorming + source-driven-development + web-investigator + frontend-design + imagegen
skills: [brainstorming, source-driven-development, frontend-design, imagegen]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/brainstorming/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/source-driven-development/SKILL.md
  - C:/Users/Huangqh/.codex/skills/frontend-design/SKILL.md
  - C:/Users/Huangqh/.codex/skills/.system/imagegen/SKILL.md
source:
  - 用户确认范围为单个智能体配置页
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/research/2026-10-16-agent-config-reference-research.md
  - .ai-runtime-artifacts/stack/2026-10-16-agent-config-stack.md
  - webui/src/components/settings/agents/AgentEditorDialog.tsx
  - webui/src/components/settings/agents/parts/BasicsSection.tsx
  - webui/src/components/settings/agents/parts/CapabilityPicker.tsx
  - webui/src/components/settings/agents/parts/PromptSection.tsx
  - webui/src/components/settings/agents/parts/AgentRailSummary.tsx
created_at: 2026-10-16
status: approved
approved: true
approval: 用户回复“可以的”，并要求“直接在本分支改，快点”
plan: skip:plan(已批准具体设计，按用户要求直接实施)
---

# 单个智能体配置页：先定义职责，再选择能力

## 目标与范围

用户反馈配置页丑、杂、没有重点；已确认对象是「智能体 → 新建/编辑智能体」，不是团队页。

让用户打开后立即知道：它叫什么、负责什么、应该怎么做；需要调整工具或模型时能直接找到。保留现有草稿与保存机制，不改变后端权限、能力选择协议或既有 Agent 档案。

本文件的设计已获用户确认，进入实施。初始现状判断来自源码与未登录网页；实施验收使用真实编辑器组件与内存样例，不读取认证信息。

## 当前问题与依据

1. `AgentEditorDialog` 的主列依次为基础信息、工具、技能、模型、提示词。职责说明被放到最长的能力列表后面。
2. `BasicsSection` 首项是只读 ID；名称、用途、工作指令不在同一阅读区域。
3. 右栏固定 22rem，再重复列出工具、技能标签；窄屏时仍占最多 45vh，进一步压缩编辑空间。
4. 五个编号分节视觉权重接近，用户难以判断先完成哪一项。

公开参考：
- [Coze 官方入门](https://www.coze.com/open/docs/guides/agent_quick_start)：Persona & Prompt、Skills、Preview&Debug 有明确分区。
- [Dify Build an Agent](https://docs.dify.ai/en/cloud/use-dify/build/new-agent/build)：模型、提示词、技能、文件、工具分别说明，高级设置另置。文档顺序不等于产品像素顺序。
- [Relevance AI Prompt](https://relevanceai.com/docs/build/agents/build-your-agent/prompt) 及其[官方界面图](https://relevanceai.com/docs/images/_opt/agent/build_overview.webp)：侧边分区，中间大面积指令，右侧资源。

以下布局是结合本项目的设计取舍，不宣称三家使用同一布局，不引入它们独有的运行能力。

## 三种方向与选择

| 方向 | 优点 | 取舍 |
| --- | --- | --- |
| 保留长表单，仅改顺序与样式 | 改动较少 | 工具与技能仍挤占大量空间，编辑长指令时仍需频繁滚动 |
| 分区编辑工作区（推荐） | 默认突出职责；已有用户可直接跳到工具或模型；草稿连续 | 需要处理切换、键盘操作和小屏摘要 |
| 强制分步创建向导 | 新建时引导明确 | 每次修改都要前后切换，不适合频繁编辑已有智能体 |

采用分区工作区，保留弹窗入口与退出习惯，不新增强制步骤。

## 布局与视觉

桌面保留较宽 Dialog，最大约 1180px、高度不超过 90dvh。左分区导航约 156px，中间编辑区占剩余主要空间，右侧摘要约 240px；左右栏不等宽。标题和内容左对齐，按钮靠右。配置区域使用同一个滚动主体，避免三列各自出现长滚动条。

布局示意（非页面截图）：

```text
编辑智能体 · 资料整理助手                           关闭
──────────────────────────────────────────────────────
职责与身份       名称                           配置摘要
工具与技能       一句话说明                     小幅 Agent 插画
模型与更多       工作指令（主要编辑区）          名称 / 用途
                职责、做事规则、输出要求         模型
                展开编辑 · 字数                 工具数量 / 技能数量
──────────────────────────────────────────────────────
有未保存的修改                            取消   保存配置
```

- 色彩：暖白纸面、低对比绿灰，森林绿主按钮，琥珀只用于提醒。背景叠轻微颗粒与暖白到浅鼠尾草的渐变；不使用紫色或靛蓝渐变。
- 层级：页标题约 20px，分区标题 16px，正文与输入 14px，辅助信息 12px。指令编辑区约 320px 起；单行字段使用统一高度与边框。
- 图标：从 Iconify 核验并本地导出少量 Lucide SVG，保持与现有 lucide-react 一致；导航带短文字，不用 Emoji 作为导航或按钮图标。既有用户档案的头像值保留，不能为了视觉改版静默重写数据。
- 不使用 Hero 加三张卡、居中宣传文案、整屏大图或无用途的装饰卡片。
- 交互过渡使用约 140–180ms 的 cubic-bezier(.2,.8,.2,1)，只用于选择和切换，遵守 prefers-reduced-motion；不使用 ease-in-out 或持续漂浮。
- 深色使用墨绿灰表面与低对比纹理，保持文本与边框对比；不简单反色插画。

## 三个分区

### 1. 职责与身份（默认）

依次为「名称」「一句话说明」「工作指令」。三项在同一编辑区域，名称紧凑，指令占主要空间。指令辅助文案为「写清它负责什么、怎样完成任务，以及结果怎么交付。」

保留当前 Markdown 纯文本、字数、5000 字上限和展开编辑功能；不增加 AI 生成提示词按钮，不把示例自动写入真实配置。名称为必填，说明与指令保留当前可空语义，不能新增未获授权的必填限制。

### 2. 工具与技能

页内用「工具 / 技能」切换两个目录，一次只看一类。保留搜索、分类、批量选择、已启用数量、风险与不可用原因；已选项优先可见，全部目录按分类逐步展开，不把上百项默认铺开。

工具解释为「允许它执行的操作，比如读文件、搜索网页」；技能解释为「完成某类任务时可参考的做事方法」。不把技能等同于权限，也不暗示选中某项就已经实际执行。

`all / include / exclude` 继续按现有协议保存与解析，文案使用「全部可用 / 只用所选 / 排除所选」。系统锁定项不能取消，当前不可用项不可选，摘要排除 blocked 工具。新建仍保持当前最小默认能力（读文件、列目录），不因为设计改版自动开启全部工具。

分区切换只改变视图；选择模式切换沿用当前明确的行为，并用短提示说明其影响，不能悄悄改变模式或扩大权限。

### 3. 模型与更多

模型放顶部，清楚展示「跟随全局模型」及实际名称，可改为指定模型。保留原健康状态说明；已保存 modelId 不在目录时显示「原模型当前不可用」，保留原值，不把它伪装成跟随全局。

分类、档案外观、只读 ID、档案类型与更新时间集中到后续信息区；ID不再占据首页首项。系统预设的重置入口保留，位置与保存区分，不提升为主按钮。

本次不新增温度、Token 配额、自动保存、MCP 单独授权、子 Agent 编排等没有现成编辑能力的配置；`subAgents` 等现有但未显示的字段必须原样保留。

## 右侧摘要与插画

摘要只显示名称、用途、当前模型、有效工具与技能数量，以及最多三个已选项示意；其余显示「等 N 项」并可跳到对应分区，不重复完整标签墙。没有配置时如实显示「尚未填写名称」「未添加技能」，不出现编造的在线、就绪、通过检查状态。

单 Agent 插画放在摘要开头或新建引导，桌面宽约 180–220px、高不超过 210px，object-fit: contain，不截掉角色和工具。已有配置密集时进一步缩小，小屏隐藏。插画中的三枚工具图形仅为示意，不能当作实际授权结果。

候选素材：`../research/2026-10-16-agent-config-ui/agent-tools-v1.png`，1122×1402、RGBA、alpha 范围 0–255，1,351,186 字节。已目视确认单 Agent、工具连接、森林绿与纸质感，无文字。原始尺寸较大，实施时需导出适合网页的透明 WebP 并检查细边和小尺寸清晰度；正式素材放 `webui/public/illustrations/`，不能让生产页面引用 `.ai-runtime-artifacts/`。

生成方式、完整提示词见 `../research/2026-10-16-agent-config-ui/illustration-prompt.md`。没有把 unDraw 素材输入图像生成模型。

unDraw 的 AI Agent 已查看，主体为显示器与大脑，未选用；Agentic Network 偏网络关系，本页改用原创单 Agent。Iconify 的 `lucide:bot` 已核验，工具和模型导航候选在实施前逐个核验并本地集成。此工具配置页没有真实照片或临时照片用途，不插入 Pexels / Picsum。

## 状态、保存与适配

- 父编辑器持有同一个完整 draft，分区切换不重置输入。现有 onSave、保存中禁用、错误反馈、取消与放弃修改确认保留。
- 保存成功按现有父页面处理；失败保留草稿。只有真实成功响应后展示成功，不在点击时提前提示。
- 900–1199px 将左侧导航改为顶部三个按钮，主区与摘要保持合理比例；900px 以下隐藏持续侧栏，提供一行摘要展开入口。手机用接近全屏的编辑面板，单列阅读，导航可换行，不横向溢出。
- 360px 宽屏、200% 缩放和软键盘下，名称、指令和保存按钮可操作；底栏可换行，不能沿用固定提示宽度挤出按钮。
- 分区用语义正确的 tablist/tab/tabpanel，方向键可切换，键盘焦点清楚；关闭后返回原入口。错误需与字段关联，跨分区保存校验失败时自动定位错误。
- 指令展开编辑时保留 Dialog 语义和焦点管理；Esc 先关闭展开编辑，再按现有流程处理整个草稿退出。

## 验收与边界

1. 首屏无需滚过工具列表即可编辑名称、说明和工作指令。
2. 三分区切换、工具/技能切换不丢输入；仅调整名称后保存不能改变 tools、skills、subAgents 或 modelId。
3. locked、blocked、all/include/exclude 与有效数量一致，新建默认权限不变。
4. 保存中、保存失败、关闭确认、系统重置、长指令上限和目录空态仍可用。
5. 检查桌面、360px、深色、200% 缩放与减少动效；图标和插画本地加载，无第三方资源依赖。
6. 后续实现运行受影响的编辑器行为测试、TypeScript、相关 lint 与构建；纯视觉不编写镜像样式的测试。未连接真实网关时明确说明验证限制。

## Spec 自检

范围明确为单 Agent 编辑器；已列方案取舍、字段映射、保存边界、缺失数据、窄屏与素材用途。没有把调研资料当成运行能力，没有自动开启权限，没有引入假调试。当前不包含实施计划，也不代表页面已经交付。

## Next

- 认可该设计且希望直接改页面：说「直接实现」或「直接做」。
- 希望先细拆任务：说「写计划」。
- 希望调整布局或插画：直接说修改意见。

按 `harness-kit/core/routing.md` 的阶段门禁，本方案写入后暂停，待用户确认再进入实现。
