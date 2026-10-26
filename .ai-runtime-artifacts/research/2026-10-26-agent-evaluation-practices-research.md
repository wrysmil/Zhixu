---
artifact: research-report
route: roles.web-investigator
skills:
  - source-driven-development
skills_evidence:
  - .claude/agents/web-investigator.md
  - .claude/skills/source-driven-development/SKILL.md
source:
  - 用户要求充分调研项目和评估做法，再制定计划
  - .ai-runtime-artifacts/specs/2026-10-15-agent-evaluation-spec.md
  - .ai-runtime-artifacts/verifications/2026-10-26-evaluation-verification-lite.md
created_at: 2026-10-26
status: complete
wu_id: R-01
---

# Agent 自动化评估做法调研

核查日期：2026-10-26。只读官方文档，不安装框架、不调用付费模型、不改业务代码。
本文为写实施计划提供证据；建议方案不表示 Zhixu 已经实现或验证。

## 1. 调研结论

Zhixu 可以继续保留 Python / SQLite / React 自建评估系统。应借用成熟框架的任务定义、证据与评分分离、实验版本管理、重复运行和不确定性表达，不必为了这些能力整体迁移至某个平台。

当前最优先的工作是让“通过”真正有依据：团队执行证据不完整时不能证明委派；关键词命中不能证明开放任务完成；记录了 Token 不能替代效率评价；工具报错也不应不加区分地变成失败（可恢复错误与无意义重试含义不同）。后两条是结合本轮项目记录的工程判断。

## 2. 官方来源与支持的实践

### S1 — Anthropic：任务、trial、grader、结果状态与轨迹

[Demystifying evals for AI agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents)，2026-01-09 发布，本次已打开全文并核对非确定性与路线图段落。

官方区分任务、单次尝试、评分器、轨迹和环境最终状态；“说已经完成”与任务状态真的改变是不同证据。规则、模型与人工评分各有职责。重复执行用于刻画随机性；`pass@k` 表示 k 次至少成功一次，`pass^k` 表示 k 次全部成功。文章建议从真实失败和人工检查发展用例，并持续查阅轨迹验证评分公平性。

**给 Zhixu 的建议：** 用 attempt 表达独立 trial；同一任务支持多次独立执行，并区分能力集和稳定回归集。文件/结构化任务检查实际产物；团队检查真实委派事件，不能采信最终回答的自报。

**边界：** 文中样本规模建议不是固定验收门槛；不能将一次 5/5 解释成可靠性。`pass@k` 的“有一次成功”也不能替代面向用户的稳定性。

### S2 — LangSmith：最终结果、整体轨迹、单步决策分开评估

[Evaluate a complex agent](https://docs.langchain.com/langsmith/evaluate-complex-agent)，已核对文首定义与示例。

官方将评价拆为 final response、trajectory、single step，且说明这些方法不绑定 LangGraph。

**给 Zhixu 的建议：** 同一用例保存独立的结果评分、轨迹约束和单步工具参数检查；不要用 final answer 的分数掩盖过程问题。先做完整任务评估，再按失败归因提供单步诊断，避免建设另一套互不关联的评估执行器。

### S3 — Google ADK：工具路径匹配强度应由任务决定

[Evaluation criteria](https://adk.dev/evaluate/criteria/)，原官方链接 `google.github.io/adk-docs/evaluate/criteria/` 已重定向至此；核对 `tool_trajectory_avg_score`、效率指标与 `final_response_match_v2`。

官方支持 `EXACT`、`IN_ORDER`、`ANY_ORDER` 三种工具轨迹匹配；名称、参数、顺序按所选策略判断。效率指标包括工具数、模型请求数、Token 和耗时；质量评分可与之独立。

**给 Zhixu 的建议：** 严格流程用 exact；只约束关键步骤用 in_order；无顺序要求用 any_order。参数支持声明式子集匹配，保留额外调用及错误，不把所有合法替代路径都判失败。效率默认独立展示，只有用例明确预算时才作为必过约束。

**边界：** 轨迹匹配不等于通用“工具选择准确率”；没有参考或判据的调用应标未评，不能全按正确计数。

### S4 — LangSmith：固定数据集版本与拆分

[Manage datasets](https://docs.langchain.com/langsmith/manage-datasets)，核对版本、指定版本运行与 split 段落。

官方数据集增删改会形成新版本，实验可指定版本并使用不同 split。

**给 Zhixu 的建议：** experiment 固定 case id/revision、种子文件、工具 fixture、规则、量表和目标配置指纹。区分开发集、保留集、稳定回归集与外部依赖集。失败案例经人工确认后新增 revision，不修改旧报告来“修分数”。

### S5 — LangSmith：重复执行保留逐次结果和波动

[How to evaluate with repetitions](https://docs.langchain.com/langsmith/repetition)，核对 `num_repetitions` 和结果展示。

官方每个 example 可重复执行多次，保留各次结果、均分及标准差，用于理解 Agent 的非确定性。

**给 Zhixu 的建议：** 将 Agent 重复运行次数与 Judge 重复评分次数设为两个字段。对同一输出重复评分只测评分波动，不增加 Agent 可靠性样本。预估总 attempts 和模型预算后运行，支持取消后保留全部已完成 trial。

### S6 — LangSmith：实验比较优先逐例定位回归

[How to compare experiment results](https://docs.langchain.com/langsmith/compare-experiment-results)，已核对比较视图、差异筛选与延迟等过滤。

官方支持对比同一数据集上的实验，定位改进、回归以及具体输入输出、反馈和延迟差异。

**给 Zhixu 的建议：** baseline/candidate 逐例配对，显示稳定通过、新失败、新通过、未形成有效对比；同时显示成功率、Token、耗时和安全指标。版本不一致默认禁止宣称优劣，只允许标明交集范围做探索性比较。

### S7 — Inspect：评分器失败是测量失败

[Scoring Policy](https://inspect.aisi.org.uk/scoring-policy.html)，已核对错误、未评分及原因记录。

官方区分被测模型做错、运行基础设施故障和评分工具无法读数；这些情况影响不同的计数与分母。模型评分无法解析时应保留 unscored 与原因，而不是假定评分成功。

**给 Zhixu 的建议：** 保留现有 execution/grading/verdict 分离；新增更明确的不可评原因和质量门禁资格。展示计划全集、有效评分、异常/缺证覆盖数，不能通过删除异常样本提高总体表现；也不能把网络或平台故障直接解释为 Agent 能力失败。

### S8 — Inspect：Judge 的输入隔离与可复现设置

[Model Grading](https://inspect.aisi.org.uk/model-graded.html)，核对角色、评分健壮性、重复性章节。

官方支持显式 grader 角色和多个评分模型；默认模板对被测文本中的分隔符做处理，降低其伪造评分边界的风险；无法解析的投票不应左右多数结论。温度和 seed 可在服务支持时固定，但低温并不保证完全确定。

**给 Zhixu 的建议：** Judge 不挂工具，评分指令与任务材料分开；模型输出、工具结果、错误都作为不可信数据，加入“忽略规则给满分”及伪造分隔符测试。冻结 grader model、prompt、rubric、参数和版本；多 Judge 是可选能力，先修通校准与人工复核，不默认用三倍调用掩盖判据问题。

**边界：** 模板分隔或转义不是提示注入的完整安全证明；不能宣称固定温度就可复现。

### S9 — Inspect：区间估计与重复样本相关性

[Scoring Metrics](https://inspect.aisi.org.uk/metrics.html)，核对 `stderr`、`ci`、`ci_wilson`、cluster 和多评分者一致性。

官方提供均值标准误、bootstrap、置信区间；二元结果建议 Wilson 区间，并支持按任务等聚类处理样本相关性。

**给 Zhixu 的建议：** 单次独立二元任务比例可显示 Wilson 区间；同任务重复试验和两版本比较优先按 case 聚类，保留配对关系，使用 case 层配对 bootstrap 展示差值区间。样本不足时明确“仅描述性结果”，不自动打“显著提升”标签。

**边界：** 配对 bootstrap 是本报告根据统计需求提出的实现选择，不是该文要求的唯一方法；两组区间是否重叠不能替代正式配对差异分析。

### S10 — Inspect：沙箱与资源生命周期

[Sandboxing](https://inspect.aisi.org.uk/sandboxing.html)，核对隔离用途、per-sample setup、清理与资源限制。

官方让执行任意代码的工具在专用隔离环境工作；可给每个样本准备输入并清理环境，控制容器和进程并发。

**给 Zhixu 的建议：** 独立 attempt workspace/runtime/session，fixture 默认；团队评估不能直接复用用户日常可写工具环境。取消后等成员与子进程退出再结束运行；在现有受控文件工具层可完成初版测试，不必为当前计划立即引入 Docker。

**边界：** 应用层 workspace 白名单不是 OS 沙箱；如果未来允许真实 shell/任意插件，需另行实现并验证隔离后才能开放安全测评。

## 3. 建议的最小可信评估闭环

以下是针对 Zhixu 的工程建议，不是外部文档的原文规范。

1. **运行证据先行：** 每个 Agent execution 的真实起止、父子关系、目标身份、Skill 文件摘要、工具参数/结果、模型物理请求与 usage 都有稳定 ID；完整性成为可判资格的一部分。
2. **结果与过程双评：** 结果按任务里程碑给完成/部分完成，过程按允许的工具集合、关键顺序和禁止操作判断；效率独立统计。一个任务可以结果正确但效率超预算或安全失败，不能只显示一个绿色 passed。
3. **Judge 可校准：** 人工确认 gold、保存分歧与理由、量表指纹、校准失效条件。不同维度可配置硬规则或语义评分；证据不存在的 Judge 引用应判无效。
4. **重复与比较：** 固定任务快照，每组重复尝试，baseline/candidate 保持相同测试集和环境。先展示原始样本、覆盖与波动，再给差值和区间。
5. **失败回流：** 任务歧义、判据错误、环境故障和 Agent 失败分开归因。通过复核把真实失败加入回归集，不把可变真实网络样本混入稳定 fixture 基线。

## 4. 对本轮发现的直接应用

| 已有证据 | 应补的判据/系统能力 | 预期验收行为 |
| --- | --- | --- |
| 团队最终文本自报委派，Trace 为零 | 团队事件进入统一 Trace；要求有真实成员执行事件 | 无证据时不判“委派成功” |
| 要求一句话，最终约322字仍通过 | 长度、段落与格式规则；语义要求挂量表 | 能稳定检出该反例 |
| 提纲关键词存在，但读取失败6次 | 错误分类、重复调用数、预算及轨迹检查 | 结果分与过程/效率结论分开 |
| Skill agent_id为空 | Skill目标身份和execution链继承 | Token可按真实身份归属 |
| 五个小样本全部通过 | 分层真实任务集、失败与边界反例、重复尝试 | 报告不再把冒烟结果称综合能力 |
| 校准测试失败、Judge未实测 | 先修gold/profile/calibration闭环，再使用已校准Judge | 未校准开放评分进入待复核 |

## 5. 不能从这次调研推出的结论

- 截图中的“Judge准确率70%–85%”“便宜10倍”等数字没有指定任务、模型、标注和统计定义，不能直接变成项目承诺或验收线。
- 规则多、Trace图复杂或样本多，不自动意味着评估可信；必须反向用已知成功、失败和边界结果测试评分器。
- 检查到工具调用失败不自动等于无意义操作；应结合任务预期、重试政策和后续恢复判断。
- 本文没有确认当前代码中的安全边界已成立，也没有验证任何框架能直接替代本项目团队运行时。

## 6. 与 Leader 本轮源码调查衔接

Leader 本轮静态调查回传：团队路径共享 gateway、只传最后一条 turn、目标仅冻结 team id，并给判分传 `trace_complete_override=True`；普通 Worker 在 live 模式存在非空 script 时仍可能切换 ControlledProvider；Judge 材料缺少 turns；Skill 失败读取涉及 USER/SOUL/memory/article 等环境材料。以上是 Leader 的源码结论，本文未另行运行复现。

对应的计划顺序应是：先补独立启动与隔离 Worker、明确固定/真实模型执行模式，冻结完整团队配置，完整传入多轮任务与评分证据，移除缺证时的强制完整标记；再恢复 Judge 校准，之后才建设多次运行与配对对比。否则重复执行只会更稳定地重复错误测量。

独立运行入口源于项目原始 spec；外部框架来源证明 per-sample 环境与测量错误分类的价值，不能据此推导必须采用某个具体 CLI 名或整体替换技术栈。

## Skills 使用

- 已读取 `.claude/agents/web-investigator.md`：官方网页只读取证、来源标注、只写 research。
- 已读取 `.claude/skills/source-driven-development/SKILL.md`：先读取既有评估 spec 与本轮 verification，再查官方文档。
- 未调用真实模型、未新增依赖、未修改业务代码、未执行 Git。
