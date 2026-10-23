---
artifact: spec
route: source-driven-development -> brainstorming -> api-and-interface-design
skills: [source-driven-development, brainstorming, api-and-interface-design]
skills_evidence:
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
  - /Users/mima0000/.agents/skills/brainstorming/SKILL.md
  - harness-kit/.agents/skills/api-and-interface-design/SKILL.md
source:
  - 用户本会话的参考图与设计、前端测试、通过条件要求
  - .ai-runtime-artifacts/research/2026-10-18-subagent-observability-research.md
  - .ai-runtime-artifacts/stack/2026-10-18-stack.md
  - .ai-runtime-artifacts/contracts/2026-10-15-agent-team-contract.md
created_at: 2026-10-18
status: draft
approved: false
---

# 子 Agent 调用状态与执行详情设计

## 1. 要解决的问题

主 Agent 派发任务后，用户应立即知道交给了谁、正在做什么、当前状态和耗时；点击那次调用即可查看真实工具活动、公开结果或错误。无论主 Agent 等待结果还是继续工作，都能观察同一条调用。

成功标准：后台派发返回不显示假完成；运行中有可追踪状态；同名并发不串线；完成可回看；刷新、重连能恢复；聊天、文件预览与工作台布局不被破坏。

首版覆盖 WebUI 会话里的普通 `spawn(wait=false/true)`；非 WebUI 渠道保持既有行为，不伪造可见轮次关联。现有 Agent Team 调度、写作团队及其事件协议不迁移；复用规则和展示习惯，不把普通调用强行注册为团队。既有 TeamProgressPanel 保持可用。单独取消、重试、发消息给子 Agent、改模型、完整 token 流不在首版范围；既有整轮停止仍正常生效，详情反映真实停止结果。

## 2. 方案对比与选择

| 方案 | 收益 | 代价 | 选择 |
| --- | --- | --- | --- |
| 简短状态入口＋右侧详情 | 主对话简洁，适合长任务和多个子 Agent | 需要共享面板槽位与窄屏适配 | 首选 |
| 全量内联 | 无需离开消息即可看过程 | 长日志推走主回答，滚动易受干扰 | 仅供交互比较 |
| 全局任务中心 | 可以汇总跨会话任务 | 本需求只需追踪当前调用，容易失去上下文 | 不采用 |

## 3. 聊天中的入口

每次调用显示一个紧凑按钮，位于对应工具调用的位置，不再同时渲染一条重复的 generic spawn 成功卡。

示例：`调研助手 · 查询近期金价 · 运行中 29 秒  ›`；次行 `正在搜索网页 · 已完成 3 次工具调用`。名称与任务标题优先，工具名用本地化文案。长标题截断但可访问名称保留全文。

状态使用图标＋文字，不只靠颜色。状态来自服务端；“运行中”与“正在调用工具”分层。排队显示等待时长；实际运行计时从 startedAt 开始；终态耗时固定，不再增长。服务器时间校正后计算耗时，本机时钟跳变不能出现负值。暂时断线时另显示“连接已断开，状态待同步”，不把旧运行状态伪装成实时心跳。

同一轮多个调用按 createdAt、invocationId 稳定排序。普通工具批次中不得隐藏活动子任务入口；若现有工具组折叠，在组标题保留“2 个子任务运行中”的入口。父消息完成后，未结束的后台调用仍保留自身状态。

旧聊天没有关联 ID 时继续显示原有工具记录，可标记“暂无执行详情”；禁止按任务名称或 started 文本猜 ID。Agent 档案改名或删除不影响历史调用的展示名快照。

## 4. 右侧执行详情

点击入口才打开，不随新任务自动弹开。打开后分三块：

1. 顶部：Agent 名、任务标题、状态、执行耗时、关闭按钮。
2. 中部：任务说明与公开执行时间线。逐条显示准备、工具开始、工具结束／失败、生成回复、执行终态。工具事件有独立 ID，可区分同名工具的并发调用。
3. 底部：任务结束后显示公开结果摘要或错误原因；长结果显示“内容已截断”。子 Agent 完成不代表主 Agent 已审阅或已汇总，面板不捏造“已验收”。

时间线只收公开活动与经过白名单投影的摘要，不展示私有推理、系统 prompt、完整工具参数、原始 shell 输出或凭据。初版活动文案可以只到“搜索网页／读取文件／运行命令”；查询词、文件路径只有经过专门允许的摘要投影后才显示，不直接复制参数。

2026-10-18 用户预览反馈：优化本轮子任务下拉。采用主题一致的任务切换菜单：触发器两行显示Agent与任务标题，菜单每项显示Agent、当前状态、任务标题与选中勾选；选中与悬停使用柔和主题色。生产实现复用项目现有Radix DropdownMenu的RadioGroup，不新增select依赖。键盘支持方向键、Home/End、Enter/Space；Escape先关菜单并回到触发器，第二次Escape才关详情；点击外部关闭菜单。弹层不超过容器可用宽度，不能覆盖侧栏关闭入口。只有一个任务时显示静态身份，省去无意义下拉。

数据加载有 loading、empty、error、not-found 状态；失败有“重新加载”，只重试读取，不重跑子 Agent。模型不可用、工具失败、任务超时、用户停止、gateway 重启分别呈现。单个工具失败后任务仍可恢复成功，不能直接把一次 tool.failed 映射成整个调用 failed。

## 5. 面板槽位与响应式

在 ThreadShell 内用一个明确的 `rightSurface` 选择：工作台、文件预览、子 Agent 详情、工具面板。沿用已有面板组件和打开动作，以局部仲裁替代新增常驻列，不重做整个工作台。

- 桌面（可用内容区 ≥960px）：非模态右栏，默认 440px；主聊天至少 420px，宽度不满足即使用抽屉。保留现有文件预览调宽能力，不为本需求新增拖拽系统。
- 窄屏：同一内容使用模态抽屉，满可用宽度、最大 560px；有遮罩、关闭按钮与 Escape；背景不可操作，焦点在抽屉内循环，关闭返回原入口。
- 桌面详情不锁主聊天焦点。自动状态更新不移动焦点、不清空输入草稿。
- 打开子 Agent 详情时临时隐藏其他右侧内容，保存之前的视图与滚动位置；关闭后还原。若详情中打开文件，文件预览提供“返回执行详情”，不会同时增加第三列。跨 chat 切换清空当前选择与返回栈，防止看到上一个 chat 的任务。
- 时间线独立滚动；用户在底部时跟随新增活动，向上阅读时不拉回底部，显示“有新活动”。主聊天滚动位置不因后台事件强制变化。
- 支持 360/768/1024/1440px、浅色／深色、中文／英文、200% 缩放、reduced-motion。窄屏关闭和返回点击区域至少 44px。

## 6. 状态与父子关系

生命周期：`queued → running → succeeded | failed | cancelled | interrupted`；运行阶段另外记录 preparing、model、tools、finalizing。临时断线属于连接状态，不是调用终态。

准入或模型准备失败允许 queued 直接进入 failed；运行超时为 failed，stopReason=timeout。`max_iterations` 或空最终答案为 failed，不能沿用旧字符串路径把兜底文案显示为成功。工具失败本身不自动结束任务。整轮停止应覆盖排队与运行任务，取消传播后记录 cancelled。进程异常重启后遗留非终态记录为 interrupted；重启不自动重跑任务。

调用身份是完整 invocationId；agentId 是档案身份，parentToolCallId 是主 Agent 这次工具调用身份；三者不混用。parentTurnId 使用 WebUI 的可见轮次 ID，不使用 loop 内部 turn_id 冒充。异步结果唤醒的后续父回合不能改写该调用的原始归属。

父 Agent “正在等待”仅在服务端能确认其被 wait=true 阻塞时显示；普通后台调用不让界面擅自把父 Agent 改成等待或完成。

## 7. 后端设计与数据流

沿用 SubagentManager 与 AgentRunner，增加普通调用的观察记录。新记录仓库建议放 `nanobot/agent/executions/`，读接口放 WebUI 原有路由层；具体文件拆分留给实施计划，不创建通用工作流框架。

数据流：工具执行上下文绑定 parentToolCallId → SpawnTool 捕获可见 turn/chat/workspace → 生成调用记录 → 档案预检与 SubagentManager 执行 → 逐工具 hook／checkpoint 更新记录与事件 → 事务提交后经既有 MessageBus/WS 投影 → 前端统一 store → 聊天入口与右栏。调用记录在预检之前创建；预检拒绝记录 queued→failed，不能只有一条无法打开的错误字符串。结果在结构化 SubagentExecutionResult 层采集，不能从旧接口返回的字符串猜成功。取消观察覆盖排队、初始化、运行三处，不只依靠已进入 Runner 的 hook。

逐工具 ContextVar 只绑定该次工具执行，并在 finally reset；不能在共享可变 RequestContext 上就地赋值，防止 asyncio.gather 下串 ID。父调用上下文先捕获，再生成子 Runner 的上下文。

普通调用持久化使用 workspace 派生的 `<agent-workspace>/runtime/agent-executions.db`，SQLite＋既有 filelock 模式；独立于 Team 开关和 memory DB。只读查询不创建空库。通过 owner/writer 锁确认该进程有恢复权后，才把旧非终态任务改成 interrupted；抢不到锁不能改写其他进程任务。首版 WebUI 观察服务不支持多 writer，无法取得锁时明确报告观察服务不可用，不能静默抢占。

记录最少包括父子关联、展示名快照、status/phase、created/started/finishedAt、lastAction、lastSeq、公开结果和 errorCode/stopReason。事件与快照在同事务写入；不逐 token 写库，SQLite 工作离开事件循环长阻塞路径。生命周期和逐工具开始／结束不能因活动节流丢失。

观察存储不可用时不偷偷改变已授权 spawn 的执行语义：仍执行原任务，结构化告知本次“执行详情不可用”，前端保留 generic 工具状态但不得误报子任务成功。该降级不能算完整验收通过；网络推送失败不回滚已执行任务，恢复靠快照。具体数据形状、查询和恢复算法见同日 contract。

终态快照保留 30 天，终态活动事件保留 7 天；活动任务不按时长清理。7 天后仍能回看结果，较早时间线明确提示已过期；30 天后旧入口显示“执行记录已过期”。临时聊天不写本次 ledger，使用连接期内存状态，离开后不承诺历史恢复；验证既有临时聊天不落盘约束。

## 8. 前端模块与兼容

新增普通执行的 types/parser/reducer/API 与 useSubagentExecutions hook；复用 Team 的 seq/补拉设计，不修改其 wire 字段。组件分为聊天入口、执行详情、活动时间线；它们读取同一份 execution store，不各自维护一份生命周期。

ThreadShell 只负责选择 executionRef 和右侧槽位；不继续把解析、重连和日志渲染塞进这个大组件。主消息关联靠 chat＋可见 turn＋parentToolCallId，历史回放也走同一关联。

旧 gateway 未声明 `subagentExecutionEventsV1` 能力时保留旧工具显示，不建立失败请求循环。未知事件版本忽略并保留最后可信快照；跨 chat/workspace 的帧与过期请求结果不得更新当前视图。

## 9. 测试与验收

逐项测试、性能预算、mock 与真实 gateway 的区别、现有 204 条测试基线见 `.ai-runtime-artifacts/verifications/2026-10-18-subagent-observability-test-spec.md`。所有 P0 必须通过，产品端到端验收不能由静态原型或假事件流代替。

设计预览提供侧栏与内联两种布局，以及可切换的示例运行／完成／失败状态；该预览不连接真实 Agent。预览验证结果单独记载，不能声称本功能已实现。

## 10. 自检

- 已比较三种方案；首版范围是普通 spawn 的可观测性，不改调度语义。
- 已区分调用终态、单工具失败、父 Agent 汇总与连接中断。
- 已定义调用身份、恢复、历史、暂存降级、旧版本与临时聊天行为。
- 已处理现有工作台／文件预览冲突，桌面非模态与窄屏模态分开验收。
- 已提供独立合同和可执行验收矩阵；没有用未实现的功能冒充已通过测试。

## Next

- 认可方案：说“写计划”或“制定实施计划”，再拆分实现与验证任务。
- 需要调整：直接指出范围、交互或验收条件。
- 本文为设计稿，尚未获批；本轮不改业务代码、不提交 Git、不进入实现。
