---
artifact: research-report
route: web-investigator -> source-driven-development -> brainstorming
skills: [source-driven-development, brainstorming]
skills_evidence:
  - .claude/agents/web-investigator.md
  - .claude/skills/source-driven-development/SKILL.md
  - C:/Users/Huangqh/.codex/skills/brainstorming/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/specs/2026-10-15-agent-evaluation-spec.md
  - .ai-runtime-artifacts/research/2026-10-26-agent-evaluation-project-audit.md
created_at: 2026-11-02
status: complete
---

# 知序记忆测试平台：源码与开源项目调研

## 调研结论

推荐复用知序现有 Agent 评估平台，增加记忆场景执行器、阶段证据和专项评分；以 LongMemEval 为首个外部基准，以中文产品专项集覆盖纠正、删除、隔离和异步处理。借鉴 MemEval 的后端适配和公平比较，不整体替换已有平台。

用户已确认首版优先“事实与偏好记忆、跨会话回忆、修改纠正、遗忘与隔离”；经验记忆改善任务执行作为后续阶段。

以下为 2026-11-02 对官方仓库、文档和数据卡的查阅。没有下载或运行完整基准，没有复现任何排行榜，不以 star 数判断方案质量。外部项目事实与本项目建议分开表述。

## 1. 当前仓库：哪些能复用，哪些必须补

| 资产或限制 | 当前源码证据 | 对设计的影响 |
| --- | --- | --- |
| 已有 Agent 评估域 | nanobot/evaluation/ 下 database、schemas、repository、services、worker、graders、reports | 保留现有平台与运行历史，新增记忆专项类型 |
| 已有五维判定 | assessment.py：task / constraints / trajectory / efficiency / safety | 作为 Agent 通用层，增加记忆专项指标；不混成一个模糊分数 |
| 已有重复实验与配对比较 | schemas.py::RunCreate.repetitions；comparisons.py::check_compatibility | 复用场景配对逻辑，扩展记忆配置与状态摘要 |
| 已有运行指纹 | manifests.py：dataset/environment/grading/candidate 摘要 | 需要覆盖抽取模型、检索参数、索引、时钟协议、记忆策略版本 |
| 多轮仍在同一 session | worker.py::run_agent_worker 对 case.turns 循环，始终使用 runtime.session_key | 不能直接把多轮问答通过率当跨会话长期记忆通过率 |
| 种子绕过抽取 | fixtures.py::seed_memory 调 memory.read/write，写入带 eval 标记的文本 | 可作下游参考种子，不可据此评价真实抽取能力；检索专项需要能导入结构化存储并校验可检索性 |
| 当前受控时钟覆盖有限 | fixtures.py::ControlledClock 只影响受控工具输出，不改进程真实时钟 | 时间推理/衰减测试必须明确依赖注入的时钟覆盖，不能只改 clock_iso |
| 已有检索 ID | memory/retrieval/engine.py::retrieve_with_ids 返回注入块和 ID | 可接结构化检索证据，但还要覆盖画像、工作记忆、手动工具召回等其他注入路径 |
| 检索日志主要用于阅读 | memory/retrieval/trace.py 有日志截断和总开关 | 日志不能直接承担完整评测证据，需要独立事件输出 |
| 会话结束编排会跳过/隔离失败 | memory/orchestrator.py::SessionEndOrchestrator.run 有短会话门禁、30 秒幂等窗口及异常捕获 | 完成事件要区分“成功、按策略跳过、失败”；不能等固定秒数就认为写入完成 |
| 现有限额 | schemas.py：最多20 turns/用例、200 attempts/run，repetitions<=10 | 长历史不能硬塞旧 turns；120场景×3次需要分批运行并聚合实验 |

以上是本轮静态核查。10-26 审计文档用于找入口；其中“尚无 repetitions / comparison”的旧结论已被当前源码覆盖。未据此宣称当前软件测试全绿或真实模型有效。

## 2. 公开记忆基准

| 项目与主源 | 定位、数据与评测 | 可复用部分 | 限制与许可 |
| --- | --- | --- | --- |
| [LongMemEval](https://github.com/xiaowu0162/LongMemEval)；[cleaned 数据卡](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) | 500题，带时间的历史与答案/证据；信息提取、多会话推理、时间推理、知识更新、拒答；官方按类型使用二值 Judge | 首版公开主集；证据 session/turn 标签帮助诊断召回 | 仓库及 cleaned 数据卡标 MIT；锁定具体 revision/hash；oracle 只含证据，不能当正常检索成绩 |
| [LoCoMo](https://github.com/snap-research/locomo)；[官方评分实现](https://github.com/snap-research/locomo/blob/main/task_eval/evaluation.py) | 10个长对话，包含 session 时间、speaker、dia_id、QA类别和证据；官方按类别处理词级 F1，对抗题另有规则 | 长历史、多跳、事件理解补充集 | [LICENSE.txt](https://github.com/snap-research/locomo/blob/main/LICENSE.txt) 为 CC BY-NC 4.0；不是宽松商用许可；第三方 Judge accuracy 不等于官方 F1 |
| [MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench)；[数据卡](https://huggingface.co/datasets/ai-hyz/MemoryAgentBench) | 上下文分块增量写入，覆盖准确检索、测试时学习、长程理解、冲突解决；依任务使用 substring EM、EM、Recall@5、Judge | 后续增量写入与冲突处理回归集 | 代码 MIT，数据卡标 MIT；保留组合数据的原始来源；测试时学习不等于实际 Agent 学会新工作流 |
| [LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2) | 更偏经验记忆，以 web-agent 历史轨迹提供环境与工作流知识，固定 reader 回答 | 第二阶段经验知识评测候选 | 仓库 Apache-2.0；数据另核验；回答经验知识题不等于完成真实任务 |
| [MemoryArena](https://github.com/ZexueHe/MemoryArena)；[官网](https://memoryarena.github.io/) | 多会话 Agent–Memory–Environment，前序动作和反馈影响后序任务 | 第二阶段实际执行收益试点 | README 标 preview；本轮未找到根代码 LICENSE；官网数据 CC BY 4.0 不能替代代码许可 |

公开基准均不能单独证明知序的工作区隔离、请求删除后不复活、Dream完成状态一致或重试幂等。这些需要产品专项集。

## 3. 可复用的评测框架与平台

| 项目 | 能提供什么 | 对知序的取舍 |
| --- | --- | --- |
| [ProsusAI/MemEval](https://github.com/ProsusAI/MemEval) | 记忆后端适配、LoCoMo/LongMemEval loader、统一生成与评分配置、按阶段统计 token；Apache-2.0 | 最有针对性的参考。先写知序 adapter 做一次小规模验证，再决定复用代码范围。它也包含作者自己的 PropMem，公开排名不是独立复现结果 |
| [Langfuse](https://github.com/langfuse/langfuse) | 数据集、实验、Trace、代码/模型/人工评分；核心 MIT，ee 目录例外 | 可选观察与协作后端；现有 pyproject 有 langfuse extra，但这不证明评估已接通；首版不额外强制部署 |
| [DeepEval](https://github.com/confident-ai/deepeval)；[Knowledge Retention](https://deepeval.com/docs/metrics-knowledge-retention) | 对话与 Agent 指标；Knowledge Retention 检查对话事实保持；仓库标 Apache-2.0 | 可选评分插件/量表参考。不能仅用它证明记忆持久化、跨会话或删除正确 |
| [Ragas Context Recall](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/) | 用参考答案的原子声明，衡量检索上下文覆盖 | 借鉴语义证据覆盖；不把静态 RAG 召回指标等同于完整记忆能力 |
| [Arize Phoenix](https://github.com/Arize-ai/phoenix) | 实验、数据集与可观测性 | 备选平台；官方当前为 ELv2，应区分源码可用与 MIT/Apache，首版不引入 |

许可说明只记录主源声明与采用边界，不构成对所有传递依赖或具体用途的许可审查。[MemEval NOTICE](https://github.com/ProsusAI/MemEval/blob/main/NOTICE)明确区分框架与外部数据；其展示的 system token 不包含 embedding 与 Judge，知序应另行记账。

[Langfuse 自托管文档](https://langfuse.com/self-hosting)列出 Web、Worker、Postgres、ClickHouse、Redis/Valkey、对象存储。基于该部署组成及知序已有资产，本轮推断首版整体切换平台收益不足；这是一项项目选型判断，不是项目性能实测。

MemoryBank、Mem0、Letta 等主要是记忆系统或 Agent 运行时，可用作被测对照；不要把更换记忆引擎和建设评测平台混为一项任务。

## 4. 可信比较的共同要求

1. 同数据版本、同 reader/生成模型、同题目、同预算、同评分版本；记忆策略可以是被测变量。
2. 每个 trial 独立重置，背景写入、索引构建和问答全部记账；不能把昨日缓存给某一候选免费复用。
3. 同一长对话的多题相关，区间估计以 conversation/scenario 聚类，不能把全部问答当独立样本。
4. 官方协议分数、内部产品指标、变更回归结论分别报告，不混算总准确率。
5. 固定模型参数与种子不能保证远程模型完全确定；重复运行并报告不确定性。
6. 保留评分材料、判定理由与人工复核；结构化断言优先于 Judge。

## 验证边界

已完成官方来源查阅、当前源码只读核查、设计文档整理；没有安装外部库、运行基准、调用真实模型或证明任一算法优于其他算法。
