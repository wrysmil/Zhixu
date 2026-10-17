---
artifact: research-report
route: source-driven-development -> superpowers:brainstorming
skills: [source-driven-development, brainstorming]
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
  - /Users/mima0000/.agents/skills/brainstorming/SKILL.md
source:
  - 用户本轮八项要求与截图
  - 用户本轮补充：文章编辑为主，右侧固定对话
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/specs/2026-10-17-writing-team-sidebar-spec.md
  - .ai-runtime-artifacts/specs/2026-10-17-writing-partner-model-selection-spec.md
  - .ai-runtime-artifacts/contracts/2026-10-17-contract-writing-team-collab.md
  - .ai-runtime-artifacts/stack/2026-10-17-writing-conversation-team-stack.md
created_at: 2026-10-17
status: researched
---

# 创作空间对话团队：调研依据

## 1. 结论

改版应保留文章作为主工作区，在右侧提供完整的团队对话。核心改动是以普通消息进入既有 Agent Loop，由主编判断回答、派工和调用文章工具；固定动作配方不再决定每次请求的执行路径。

已有团队执行、文章存储、保存恢复等能力可复用。命令菜单、成员提及、文章作用域工具、团队默认播种和编辑器选区仍有真实接线工作，不能只改右栏外观。

以下“已存在”均指本轮静态源码核对；没有把历史文档里的测试结果当成本轮运行结果，也没有进行真实模型验证。

## 2. 截图与代码所反映的问题

截图显示：左侧素材/提纲常驻，中间正文，右侧“写作搭档”包含重复的“启动完整流程”、任务按钮、状态、结果与协作过程。右侧输入框缺乏聊天的视觉主次，作者必须理解任务/阶段才能操作。

| 发现 | 当前证据 | 设计影响 |
| --- | --- | --- |
| 右栏以动作和状态为中心 | `WritingPartnerPanel.tsx:92` 的 `ACTION_BY_STATUS`；`:112` 的固定快捷动作；`:731` 起独立输入框 | 改为消息时间线、输入框、消息中的产物卡 |
| 所谓回复仍启动动作批次 | `collab/runner.py:482` 的 `reply()` 通过 `_launch_batch(action=REPLY)` 发起批次 | 正常对话进入既有会话消息链，不能用 `writing.reply` 冒充聊天 |
| 任务拆解由代码配方决定 | `collab/team_port.py:348` 的 `plan_members()`，`:360` 调 `recipe_for(action)`，`start_full` 设置顺序依赖 | 普通对话由主编 Loop 决策，Scheduler 保留通用依赖能力 |
| 通用团队对话已存在 | `teams/turn.py:145` 按 session metadata 建立 Leader 回合；`teams/execution.py` 调 `execute_frozen()` | 优先接入已有团队会话，避免第二套 LLM Loop |
| 命令和提及有可复用 UI | `ThreadComposer.tsx:1129` 命令检索；`:1320` 提及候选 | 补写作动作与成员候选，不能认为现有 `@` 已支持成员 |
| 实际选区尚未贯通 | `ArticleEditor.tsx:220` 的正文 textarea 未绑定选区回调；`CreativeWorkspace.tsx:504` 未给搭档传 `selection` | 浮动问 AI 需要补采集、快照、上下文、提交完整链路 |
| 内置团队已有定义和保护 | `writing/builtin.py:47`；`teams/service.py:189` 的 `ensure_builtin_team()` | 复用播种方法，补初始化与升级接线 |
| 当前生产路径仍偏惰性建团队 | `collab/team_port.py:275` 的 `resolve_team()` / `:291` allocator；本轮检索 `ensure_builtin_team` 未发现生产调用 | 首次进入团队管理就应看见种子团队，不依赖启动写作 |
| 管理页缺内置来源展示闭环 | `TeamService.list_teams()` 直接返回 repository；`TeamsView`、前端 TeamDefinition 无对应内置展示字段 | 用资产登记投影来源、删除保护和详情入口 |
| 工具白名单不会自动实例化工具 | `teams/leader.py` 的 `build_leader_registry()` 只从已有 base 取工具；`subagent.py:430` 用 ToolLoader 新建成员 registry | 需同时扩展可用工具目录与实际执行时的作用域注入 |
| 文章写入保护可复用 | `writing/repository.py`；`collab/apply.py:1` 定义采纳/撤销事务 | 所有 Agent 写入走文章服务、revision 与幂等，不能写磁盘绕过 |
| 左侧导航负担较大 | `WorkspaceNavigation.tsx:51` 起同时陈列主入口、能力项、记忆、身份、团队 | 一级保留常用入口，管理能力聚合至“更多” |

行号是本轮源码快照定位，后续实现可能变化。

## 3. 外部产品与标准

检索日期：2026-10-17。只将可直接读取的官方文档或作者一手说明作为依据；不根据搜索摘要推断未展示的功能。

### Notion：对话与文档局部编辑并存

官方文档区分面向任务的 Agent 对话、选中文字后的 AI 编辑、空白处起草。文档也描述了接受、丢弃、重试生成结果的交互。[Notion 官方使用指南](https://www.notion.com/help/guides/notion-ai-for-docs)、[Notion AI 帮助](https://www.notion.com/help/notion-ai-faqs)。

本项目推论：文章右侧的持续对话与正文局部提问可以共用上下文，用户无需在独立流程页间跳转；明确区分“提问”与“修改”。这属于本项目设计选择，并非照搬 Notion 的权限或数据模型。

### Lex：围绕草稿交流，选段可发起讨论

Lex 创始人的产品说明明确介绍 Ask Lex 可围绕草稿聊天，也可选中一段发起针对该段的对话；Checks 在文档中显示修改建议及理由。该来源发表于 2024-07-25，仅用作已公开的交互模式证据，不据此声称当前所有界面细节保持不变。[Lex 一手产品说明](https://buttondown.com/lex/archive/announcing-lex-for-teams/)。

本项目推论：主编的文字回复与“已添加素材 / 已更新正文 / 查看修改”等轻量产物卡应在同一消息流中；整篇审校宜先展示定位建议。

### VS Code：命令缩短表达，局部操作接续现有会话

官方聊天文档描述 `/` 可列出常用提示词/技能入口；内联聊天文档描述选区限定请求范围、显示修改对比，以及在活跃编辑会话中将提问送回该会话。[VS Code 聊天交互](https://code.visualstudio.com/docs/chat/chat-overview)、[VS Code 内联与快速聊天](https://code.visualstudio.com/docs/chat/inline-chat)。

本项目推论：`/` 应表达“做什么”，`@` 应表达“谁来做”；划词提问接续本篇同一个对话。`@` 的成员路由语义由本项目定义，不能由竞品对上下文的提及直接推导。

### 原生 textarea 已能提供可靠选区起点

`selectionStart` / `selectionEnd` 可读写 textarea 选区。HTML 标准使用 UTF-16 code unit 表示相关文本位置；项目已存在前端到 Python 码点的转换函数。[MDN 选区 API](https://developer.mozilla.org/en-US/docs/Web/API/HTMLTextAreaElement/selectionStart)、[HTML 文本选区标准](https://html.spec.whatwg.org/multipage/form-control-infrastructure.html#textFieldSelection)。

本项目推论：第一版不必更换编辑器。直接读取 textarea 选区，保留现有 Unicode 转换；预览 DOM 与 Markdown 源文映射暂不扩展为可写选区能力。

## 4. 方案比较

| 方案 | 收益 | 成本/限制 | 结论 |
| --- | --- | --- | --- |
| 文章主区 + 固定右侧团队聊天 + 作用域工具 | 符合用户最新布局选择，复用团队 Loop，能连续追问与实际落文 | 需要会话、工具注入和保存冲突接线 | 推荐 |
| 保留固定配方，只把按钮和结果画成聊天气泡 | 表面改动小 | 执行仍由固定动作决定，提问容易意外启动批次 | 不采用 |
| 聊天全屏 + 按需文章画布 | 空白开场更像聊天产品 | 与用户明确的“文章编辑为主，右侧固定对话”冲突 | 不采用 |

## 5. 边界与可信度

- 高置信度：当前固定动作配方、选区未接线、团队执行路径、工具实例化机制、已有播种方法，均有直接源码依据。
- 中置信度：通过局部宿主扩展即可完成作用域工具注入；仍需实施计划阶段冻结具体接口并验证异步任务传播和取消行为。
- 未验证：实际模型能否稳定选择正确工具、长对话质量、真实来源检索效果、改版布局的用户测试结果。
- 已确认：默认布局为文章编辑主区、右侧固定对话。
- 本轮交付是产品与技术方案，没有业务实现或功能通过声明。
