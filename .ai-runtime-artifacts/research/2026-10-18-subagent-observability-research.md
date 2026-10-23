---
artifact: research
route: source-driven-development -> best-practice-research -> brainstorming
skills: [source-driven-development, best-practice-research, brainstorming]
skills_evidence:
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
  - /Users/mima0000/.codex/skills/best-practice-research/SKILL.md
  - /Users/mima0000/.agents/skills/brainstorming/SKILL.md
source:
  - .ai-runtime-artifacts/contracts/2026-10-15-agent-team-contract.md
  - .ai-runtime-artifacts/research/2026-10-15-agent-team-research.md
  - .ai-runtime-artifacts/research/2026-10-16-agent-team-ui-research.md
  - 用户本会话提供的四张交互参考图与研究、设计、前端测试要求
created_at: 2026-10-18
status: complete
---

# 子 Agent 执行可见性调研

## 1. 目标与范围

用户要在主聊天看见子 Agent 状态，点击后查看执行过程；比较全量内联与右侧详情，形成可实现的设计和前端验收条件。本轮交付设计与测试规范，不把已有单测通过当作新功能已实现。

## 2. 项目内部证据

| 位置（本轮读取） | 直接事实 | 设计影响 |
| --- | --- | --- |
| `nanobot/agent/tools/spawn.py:123` | `wait=false` 默认后台，`wait=true` 调 `run_inline` | 保留两种执行语义，均提供进度 |
| `nanobot/agent/subagent.py:464,530` | 两路径均创建 asyncio task；同步路径 await 该 task | 等待结果不意味着阻塞整个事件循环 |
| `nanobot/agent/subagent.py:123,517,587` | 内存记录 phase/tool_events/usage，结束后移除 | 仅轮询内存无法满足完成后回看和刷新恢复 |
| `nanobot/agent/subagent.py:160,734,820` | checkpoint 更新阶段；工具公开活动主要在 after_iteration 汇总 | 要显示正在执行的工具，需接入逐工具开始/结束 hook |
| `nanobot/agent/tools/execution.py:146,155,173,192` | 已有逐工具开始、异常、返回错误、正常结束 hook | 扩展观察者，无需另写 LLM 循环 |
| `nanobot/agent/tools/context.py:31` | RequestContext 有 turn_id，无显式 tool_call_id | 调用归属必须新增按工具调用绑定的上下文，不用全局可变字段 |
| `nanobot/teams/events.py:24` | 团队已有公开白名单事件、seq、限流与事务后投递 | 复用规则；普通 spawn 不伪装成 TeamRun |
| `webui/src/components/thread/activity/generic-tool-model.ts:227` | spawn 被当作通用“委派任务” | 派发完成与子任务完成必须分开 |
| `webui/src/hooks/useTeamProgress.ts:56` | 团队已有快照、补拉、逐 chat 订阅 | 作为新 hook 的参考，避免改坏现有团队契约 |
| `webui/src/components/thread/ThreadShell.tsx:1881` | 文件预览、SessionWorkbench、StartDock 已占右侧 | 执行详情必须有统一槽位仲裁，不新增常驻列 |

已有 Team 契约只允许公开活动摘要，不下发完整工具参数、原始输出或私有推理；本设计沿用此边界，不承诺复制参考图中的完整思考文本。

## 3. 官方／上游资料

查阅日期：2026-10-18。下面“事实”来自原站；“应用”是本项目设计判断。

| 来源 | 已确证事实（概述） | 本项目应用 |
| --- | --- | --- |
| [Claude Code：foreground/background subagents](https://code.claude.com/docs/en/sub-agents#run-subagents-in-foreground-or-background) | 支持前台等待与后台并发，后台完成通知、任务 transcript 入口 | 状态入口＋详情合理；右侧位置是本项目选择 |
| [LangGraph：subgraph streaming](https://docs.langchain.com/oss/python/langgraph/streaming#subgraph-outputs) | 子图流可带调用路径和 task ID namespace | 事件要有稳定父子归属；不据此推断本项目已有重连持久性 |
| [W3C APG：modal dialog](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/) | 模态需内部焦点、Tab 循环、Escape、关闭后焦点恢复 | 窄屏抽屉按模态验收，桌面非模态面板不锁住主聊天 |
| [WAI-ARIA 1.2：status](https://www.w3.org/TR/wai-aria-1.2/#status) | status 隐含 polite live region，不应抢焦点 | 只播报关键状态变化，耗时每秒更新不播报 |
| [Testing Library：fake timers](https://testing-library.com/docs/using-fake-timers/) | 假计时需清理，user-event 需正确配置 advanceTimers | 耗时、重连退避测试确定化，测试后无遗留计时器 |
| [Vitest v2：vi timer API](https://v2.vitest.dev/api/vi#vi-advancetimersbytimeasync) | 提供异步推进计时器；vi.waitFor 的自动推进有专属语义 | 不把该保证套用于 Testing Library 的 waitFor |

版本边界：Claude 与 LangGraph 为滚动文档；LangGraph 的 v2 流格式针对其 1.1+，只借鉴事件来源表示，不增加该依赖。ARIA 1.2 为 2023-06-06 Recommendation；Vitest 文档为 2.x。资料不能代替本仓库运行验证。

## 4. 方案排序

1. **聊天简短状态＋点击右侧详情：推荐。** 主对话稳定，多任务可区分，过程与结果按需查看。
2. 全部内联展开：小任务直观，但多个长任务会推走回答、干扰滚动；作为交互比较，不作为首版默认。
3. 只加全局任务列表：缺少主对话与具体调用的关联，用户仍可能感到卡住。

## 5. 调研结论与限制

问题主要是普通 spawn 的可观测链路缺失，不能由截图证明全部使用阻塞模式。仅增加右栏不能满足生命周期、回看、重连要求。需要普通调用记录＋公开事件＋只读查询，再让聊天入口和右栏消费同一份状态。

已实际跑当前相关 8 个前端测试文件，204/204 通过；TypeScript 无输出检查退出 0。具体警告、命令与新功能待验收项见对应 test-spec。未真实重放截图中那一次调用。

研究过程一位固定 researcher 模型因账号不支持失败，随后改用继承模型的只读资料代理完成调研；该现象支持“模型配置失败需要独立呈现”，不作为本项目运行错误的证据。
