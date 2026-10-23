---
artifact: research-report
route: web-investigator + source-driven-development
skills: [best-practice-research, source-driven-development]
skills_evidence:
  - /Users/mima0000/.codex/skills/best-practice-research/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/contracts/2026-09-29-agents-contract.md
  - .ai-runtime-artifacts/plans/2026-09-29-agents-runtime-integration-plan.md
  - .ai-runtime-artifacts/research/2026-09-30-openakita-subagent-integration-research.md
created_at: 2026-10-15
status: researched
---

# Agent Team 调研与项目接入结论

## 1. 需求与结论

用户需要：保存包含 Leader 和成员的团队；创建聊天时选择团队；在聊天中看各成员当前任务、活动和进度。用户于本会话明确选择第一版采用「Leader 分派、成员执行、Leader 汇总，并展示实时进度」。

建议建立独立 Team 领域，复用现有 AgentProfile 和 SubagentManager。Leader 使用现有聊天主循环，团队调度工具一次声明一批任务，Python 负责验证依赖、限制并发、执行和保存事件，Leader 根据结构化结果继续安排或汇总。

这属于有持久任务状态的 supervisor 模式。第一版不包含成员互发消息、自主领任务或常驻成员会话；这些是更深协作能力，不能因为 UI 显示了多张 Agent 卡片就声称已经具备。

## 2. 一手资料与可借鉴能力

资料访问日期为 2026-10-15；官方页面会更新，下列是当日读取的事实，不是对未来版本的承诺。

| 来源 | 文档事实 | 本项目建议 |
| --- | --- | --- |
| [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) | Lead、成员、任务列表、mailbox 分工；独立上下文；任务依赖和领取互斥。功能仍标注实验性质，存在恢复、协调和关闭限制。 | 区分配置与运行；任务终态来自执行器；把取消和重启行为写进契约。第一版不复制 CLI 会话/tmux 架构。 |
| [LangChain Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents) | Supervisor 经工具调用专门 Agent，收结果后综合；可并行调用，默认调用间不保留子 Agent 记忆。 | 与现有 `run_inline` 最契合；任务给必要摘要和材料引用，不复制整个父聊天。 |
| [CrewAI Hierarchical Process](https://docs.crewai.com/v1.15.28/en/learn/hierarchical-process) | Manager 负责任务分派与结果检查，可配置 manager 和迭代/速率约束。 | Leader 决定工作内容，运行时执行权限和预算约束；结果可要求返工，但不能无限循环。 |
| [LangGraph Event streaming](https://docs.langchain.com/oss/python/langgraph/event-streaming) | 事件区分生命周期、状态、工具和消息，携带关联信息和顺序标识。 | 为团队事件使用运行 ID、任务 ID、尝试 ID 和递增 seq；不用时间戳判断事件先后。 |
| [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | 执行持久化与线程/状态相关，内存和持久保存有不同生命周期。 | 聊天历史和任务执行记录分开；React 内存及 asyncio.Task 均不是持久化事实源。 |
| [LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) | 检查点可保存执行进度，重放需要考虑模型和外部调用的再次执行。 | 第一版重启标记 interrupted；手动重试创建新运行，不承诺任意外部工具恰好执行一次。 |

补充辨析：当前 Claude Code 比较表也允许部分具名 subagent 相互通信，因此不能仅用「能否通信」定义团队。共享任务协调、生命周期、上下文边界才是更稳定的区分。该差异不改变本项目首版范围。

## 3. 仓库已实现的能力

下列行号对应本次基线，后续提交可能移动。

| 能力 | 证据 | 结论 |
| --- | --- | --- |
| Agent 定义、持久化和管理页 | `nanobot/agents/models.py:103`、`store.py:61`、`webui/src/components/settings/agents/AgentsView.tsx:39` | 可直接引用档案，不再新建另一套 Agent 编辑器。 |
| 指定档案运行 | `nanobot/agent/tools/spawn.py:123`、`nanobot/agent/subagent.py:368,434,530` | prompt、tools、skills 已消费；支持后台/同步路径。 |
| 按预设选模型 | `nanobot/agent/model_runtime.py:117`、`nanobot/webui/agents_api.py:200` | 档案 `modelId` 清单实际为 preset 名；SubagentManager 宿主尚未接通 resolver。 |
| 会话扩展点 | `nanobot/session/manager.py:284`、`nanobot/webui/inbound_commands.py:307` | metadata 能持久化；空聊天现在先暂存 workspace，首消息才落盘。 |
| 子任务状态采集 | `nanobot/agent/subagent.py:74,90,187` | 有内存状态，供 runtime_control 使用；完成后删除，没有历史状态。 |
| 聊天实时通道 | `nanobot/events.py:42`、`nanobot/bus/outbound_events.py` | typed event + EventSink 可扩展；应通过边缘投影接入 WebUI。 |
| 活动区与重连 | `AgentActivityCluster.tsx:145`、`nanobot-client.ts:1169`、`outbound_projection.py:129` | 可复用显示风格和 attach；缺团队事件与快照。 |

旧产物必须结合代码读取：9/29 计划写过「无效档案回退默认」，9/30 修复后已经是显式档案无效即拒绝；旧 OpenAkita 调研对递归风险的早期判断也被后续计划更正。子 Agent 现有 scope 已排除 spawn，不需要首版增加递归框架。

## 4. 真实缺口及优先级

1. **P0：团队与会话绑定不存在。** `AgentProfile.subAgents` 是能力选择集合，未消费为运行权限，也不是团队实体。
2. **P0：Leader 档案装配必须按回合。** Loop 的 context、tools 是共享对象；Team A 的白名单和 prompt 不能污染普通聊天/Team B。子档案解析器只生成 subagent scope，不能直接用于 Leader。
3. **P0：空工具集合会回退全量。** `loop.py:1077,1454,2101` 存在 `tools or self.tools`；ToolRegistry 定义 `__len__`，合法空集会被误判。实施时先加回归测试再改 `is None`。
4. **P0：执行结果不是可靠结构。** 迭代耗尽、无效档案及错误可能是普通字符串；不能扫描「Error」决定终态。新增 typed result，保留 stop_reason、usage 和执行 ID，同时保留旧字符串 API 兼容层。
5. **P0：进度无持久出口。** SubagentStatus 缺 Agent/会话/父轮次归属，完成即删；需新的任务尝试和事件表。
6. **P1：恢复只有主回合恢复。** 子任务不会随进程重启恢复；首版须阻止主回合恢复逻辑重放带副作用的团队工具。
7. **P1：现有 `agent_ui` 只有透传，没有成员消费者和完整历史投影。** 不能当作已建好的团队 UI。本方案用独立 typed frame 和团队快照，避免混进普通 trace 合并。
8. **P1：并发共享 workspace。** 子 Agent 并无自动工作目录隔离。独立读任务可并行；写相同资源的任务必须以资源锁或依赖串行，不宣称应用层锁等同进程沙箱。

## 5. 三种接入路径

| 路径 | 优点 | 代价 | 选择 |
| --- | --- | --- | --- |
| 在档案 `subAgents` 上直接加 UI | 改动少 | 混淆角色档案与可复用团队，无运行和恢复边界 | 不采用 |
| 独立 Team 域，复用 Loop/Runner/SubagentManager | 顺应已有结构，可小步验收，无额外运行服务 | 需要补结构化结果、状态持久化和边缘协议 | 推荐 |
| 引入通用多 Agent 框架替代现有执行链 | 可获得现成图调度和扩展机制 | 双运行时、双状态/取消/记忆，依赖和迁移面大 | 当前不采用 |

## 6. 交接与证据边界

详见同日 `agent-team-plan.md`、`agent-team-contract.md` 和 dispatch。研究结论足以编写计划；本轮只做源码阅读、官方资料核对和文档工作，未证明应用测试/构建通过。规模、超时、UI 和数据模型均是本项目建议值，不是上游产品统一标准。
