---
artifact: research-report
route: web-investigator -> brainstorming
skills: [brainstorming, frontend-design, source-driven-development]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/brainstorming/SKILL.md
  - C:/Users/Huangqh/.codex/skills/frontend-design/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/source-driven-development/SKILL.md
source:
  - 用户提供的知序与商汤小浣熊截图
  - webui/src/globals.css
  - webui/src/components/thread/ThreadWelcome.tsx
  - https://www.xiaohuanxiong.com/
  - https://xiaohuanxiong.com/work/home
  - https://www.kimi.com/
  - https://blog.duolingo.com/core-tabs-redesign/
created_at: 2026-10-20
status: direction-proposed
approved: false
---

# 知序 AI 助手视觉方向调研

## 用户诉求与范围

用户觉得当前配色、页面第一印象不像 AI 助手，希望参考卡通风与网上产品。此次完成原因分析、官方来源调研与可比较的方向建议；尚未确定最终设计，不进入实现。

## 现状证据

| 观察 | 依据 | 对第一印象的影响（设计判断） |
| --- | --- | --- |
| 暖米白、森林绿、纸张纹理一起使用 | globals.css 的 :root 与全局 background-image | 更接近手帐、阅读或写作空间 |
| 大标题使用宋体/衬线字体，尺寸最高 3.25rem | --font-display 与 .thread-welcome-title | 编辑出版感强，任务输入的视觉优先级偏低 |
| 机器人与输入框之间隔着引导区 | 用户截图与 ThreadWelcome.tsx 结构 | 机器人像装饰插画，缺少接收任务的关系 |
| 截图中的部分标签、图标呈近白色，背景也浅 | 用户截图 | 对比度偏弱、页面显得发虚；本轮未定位运行时样式根因，不能断言具体 token 缺陷 |
| 引导只展示整理笔记、拆解目标 | ThreadWelcome.tsx prompts | 第一眼可见的能力偏窄，无法表现通用个人助手定位 |

## 官方参考与证据边界

1. 商汤小浣熊：https://www.xiaohuanxiong.com/ 。官网文字明确定位任务理解、工具调用与办公成果交付。实际应用入口是 https://xiaohuanxiong.com/work/home 。角色趴在输入框上的布局观察来自用户提供的截图，不声称已核验当前登录后布局。可借鉴角色、任务输入、具体能力入口的组合，不照搬紫色主题、促销区或其品牌角色。
2. Kimi：https://www.kimi.com/ 。此次网页读取可确认 New chat、Projects、任务输入提示、Select project 与 Plugins 的入口文字；无法据此确认全部视觉样式。可借鉴任务入口与上下文选择放在同一操作链上，不照搬具体配色。
3. Duolingo 官方改版复盘：https://blog.duolingo.com/core-tabs-redesign/ 。文章给出多种方向及前后对比，明确讨论插画是否有用途、简洁是否损害清晰度，并统一字体、间距与标题层级。可借鉴角色系统与功能层级同时成立，避免每页都放大角色。其学习激励与强游戏化不直接适用于知序。

检索时 Duolingo 旧 illustration/characters 与 illustration/shape-language 页面均重定向设计博客栏目，因此不将旧规范当作目前仍可访问的原文。豆包动态页未取得足够内容，不据此描述现状。

## 可比较的方向

| 方向 | 画面与配色 | 收益 | 代价 |
| --- | --- | --- | --- |
| A. 清爽卡通助手（推荐） | 云白、蓝色主操作、少量杏黄；有明确轮廓的简洁角色 | 与当前暖纸感拉开差异，角色亲切且长期工作较清爽 | 机器人材质与局部配色需一起调整 |
| B. 暖色桌面伙伴 | 干净白底、杏橙主色、深灰文字，柔和立体角色 | 亲切、有个人桌面感 | 奶油色或纹理过多容易回到当前观感 |
| C. 轻漫画工具箱 | 白底、深色细描边、少量彩色标签、扁平角色 | 辨识度高，卡通感更直接 | 密集聊天、代码、设置页面更难控制视觉噪声 |

推荐 A 的起始色板（设计提案，未验收）：画布 #F7F9FC、输入表面 #FFFFFF、文字 #253047、主操作 #4E83F1、辅助点缀 #F4BD6A、边界 #E1E7EF。杏黄只作为角色和少量装饰，不作为白底小文字颜色。最终需按实际字号、状态验证对比度。

## 推荐首页构成

- 标题缩为 28–32px 无衬线字体，用直接称呼表达助手身份，例如“我是知序，今天想一起做什么？”
- 角色靠近输入框上缘或左上角，与输入区域形成一个整体；发送消息后收为头像，避免占据持续工作的空间。
- 输入框是最大、最清楚的可交互表面。提示覆盖问题、资料与目标，而非只写模糊的“问任何问题”。
- 输入框下方保留少量轻量任务入口，例如整理资料、写作、研究、拆解任务；只映射已有能力，不虚构一键成果流程。
- 最近工作使用简短列表，便于继续已有进度；不另加重型数据仪表盘。
- 模型、权限、设备、项目选择仍真实可见，遵循现有行为；使用较低视觉层级，权限风险状态不得伪装或隐藏。
- 清理近白文字、图标与浅背景的搭配；常规正文、次级标签、禁用态分别定义，不靠全局压低透明度制造柔和感。
- 首页、聊天、侧栏、设置共用色值、圆角、字体与图标规范；角色主要出现在首页、空状态和少量有含义的反馈里。

## Next

- 用户选定角色主导、整体卡通化或保留机器人微调的程度，再形成可比较的首页设计稿与正式 spec。
- 需要调整方向时，明确希望保留或排除的色彩与角色特征。
- 当前研究不代表最终设计批准，也不代表页面已经改动。
