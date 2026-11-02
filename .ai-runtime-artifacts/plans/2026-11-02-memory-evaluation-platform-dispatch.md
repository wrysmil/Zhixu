---
artifact: implementation-dispatch
route: writing-plans + api-and-interface-design
plan: .ai-runtime-artifacts/plans/2026-11-02-memory-evaluation-platform-plan.md
contract: .ai-runtime-artifacts/contracts/2026-11-02-contract-memory-evaluation.md
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - .claude/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/api-and-interface-design/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/specs/2026-11-02-memory-evaluation-platform-spec.md
  - .ai-runtime-artifacts/plans/2026-11-02-memory-evaluation-platform-plan.md
  - .ai-runtime-artifacts/contracts/2026-11-02-contract-memory-evaluation.md
created_at: 2026-11-02
status: approved
approved: true
---

# 记忆评测平台第一阶段：执行图

本文件仅描述获准实现后的调度；没有启动任何worker。业务步骤与RED/GREEN命令见plan Task1–12，字段与错误分母以contract为准。

## 1. 执行前提

- 用户在本会话单独说“开始实现/执行/并行执行”后，Leader加载orchestration与项目Git规则，重新核对HEAD、工作树、文件与依赖。
- 计划采用独立代码worktree：`D:/studyspace/project/Zhixu-memory-evaluation-platform`，分支`codex/memory-evaluation-platform`；本轮不创建。如果已有同名worktree/分支，先只读核验归属，禁止覆盖或删除用户工作，必要时更新dispatch再执行。
- 所有写代码worker在上述隔离worktree内执行，各自仅写plan文件清单；并行GROUP最多3个worker，保留1个Leader槽位。Leader串行集成/检查/提交，worker不得commit/push。
- 过程产物仍写代码worktree的`.ai-runtime-artifacts/`，测试临时根固定`.ai-runtime-artifacts/verifications/memory-evaluation/`；来源数据与gold放评估私有根，绝不挂载日常workspace。
- 执行开始时由Leader建立同stem execution-log与`execution-logs/tracking/DISPATCH-TRACK-2026-11-02-memory-evaluation-platform.md`，记录依赖与真实状态；本轮不伪造执行日志。

## 2. 执行图

所有条目的worktree_path均为`D:/studyspace/project/Zhixu-memory-evaluation-platform`、branch均为`codex/memory-evaluation-platform`。文件缩写只为阅读，完整精确清单以plan各Task的Files为准；workers必须收到展开后的清单。

| GROUP | WU | 标题 | 依赖 | wu_type / agent_role | workspace_scope | 写文件边界 |
| --- | --- | --- | --- | --- | --- | --- |
| G1 | WU-01 | 模型、ref、gold边界与迁移4 | 无 | feature / coder | wu | memory/models/protocol/repository；evaluation/schemas/database/repository；对应测试/fixture |
| G2 | WU-02 | 产品完成信号、时钟与observer | WU-01 | feature / coder | wu | nanobot/memory时钟、signals、提取/派生/检索；agent/hooks/memory_extraction；对应tests/memory |
| G2 | WU-05 | 指标、Judge及通用分母兼容 | WU-01 | feature / coder | wu | evaluation/memory/metrics/grading/judge_materials；assessment/reports/graders；对应测试 |
| G2 | WU-06 | 题库、流式导入和LongMemEval | WU-01 | feature / coder | wu | evaluation/memory/datasets/longmemeval；samples；sample_cases；对应测试 |
| G3 | WU-03 | 隔离adapter与checkpoint恢复 | WU-01、WU-02 | feature / coder | wu | evaluation/memory/zhixu_adapter/checkpoints；对应测试 |
| G4 | WU-04 | Worker三轨与真实产品装配 | WU-01、WU-02、WU-03、WU-05 | feature / coder | wu | memory/runner/worker_bridge；evaluation/schemas/worker/runtime_factory/services/ipc；agent/loop/context；对应测试 |
| G5 | WU-07 | 分批实验、冻结、预算与恢复 | WU-04、WU-05、WU-06 | feature / coder | wu | memory/experiments/manifests；evaluation/scheduler/services/repository；对应测试 |
| G6 | WU-08 | 聚类对比和同快照HTML | WU-05、WU-07 | feature / coder | wu | memory/comparisons/report；对应测试 |
| G7 | WU-09 | HTTP与TS客户端 | WU-06、WU-07、WU-08 | feature / coder | wu | memory/routes；evaluation/server/errors/schemas/services；lib/evaluation-api及memory-evaluation-api；对应测试 |
| G8 | WU-10 | 题库编辑和实验表单 | WU-09 | ui / coder | wu | 4个独立memory UI组件；datasets/form测试；不改入口/翻译/API |
| G9 | WU-11 | 指标、比较、回放、复核入口 | WU-09、WU-10 | ui / coder | wu | 4个独立memory UI组件；EvaluationSettings/Reviews/Calibration；zh-CN/en翻译；report/replay测试 |
| G10 | WU-12 | 软件回归、真实基线和验收 | WU-01至WU-11 | test / test-engineer | wu | 集成/安全/浏览器验证，plan Task12指定测试和过程报告；不自行改业务 |

G8先完成题库与表单组件，G9再接报告与总入口；WU-11消费WU-10确认的导出、props与翻译key。需要修改WU-10文件时由Leader显式转交所有权，不在工具执行中相互覆盖。

G2结束：Leader检查三方types/metrics/source版本一致；G4结束必须有20场景小闭环证据；G7结束先跑HTTP/TS兼容再启UI；每GROUP提交检查点不等于完整交付。

## 3. 每次派发需要的上下文

prompt包含：WU id、中文目标、wu_type、agent_role、完整文件列表、cwd/worktree_path、依赖产物、禁止事项、done criteria、测试命令、本WU Skills与返回格式。禁止把全部外部基准库当上下文灌入worker。

| WU | 本WU必须加载的skills | Done criteria补充 |
| --- | --- | --- |
| 01 | source-driven-development、api-and-interface-design、verification-before-completion | 新旧schema/迁移全部目标回归；gold不进入执行输入 |
| 02/03/04 | source-driven-development、verification-before-completion | 真实完成状态、scope/clock覆盖与三轨证据；无测试专用绕过 |
| 05 | source-driven-development、verification-before-completion | 手算指标、未评分状态、通用旧分母、校准版本一致 |
| 06 | source-driven-development、verification-before-completion | 120分层/20冒烟/独立校准、split防串、流限制、官方来源可追溯 |
| 07/08/09 | source-driven-development、api-and-interface-design、verification-before-completion | 冻结/预算/原子幂等/分页/兼容/聚类比较均有证据 |
| 10/11 | source-driven-development、frontend-ui-engineering、verification-before-completion | 契约字段一致、缺证/失败可见、交互测试、可访问性 |
| 12 | verification-before-completion、browser-testing-with-devtools、security-and-hardening | 软件检查、浏览器证据、真实模型限制、验收矩阵完整 |

skill路径在派发前按项目优先解析：source与verification使用`.claude/skills/`；api/front-end/browser/security使用本机可用agent-skills目录。未安装必需skill时记录真实缺失并由Leader处理，不让worker宣称已加载。skill要求不得覆盖用户已授权范围或输出位置。

返回格式：`wu_status`、`changed_files`、`tests`（命令/结果/日志）、`contract_changes`、`risks_and_limits`、`next_dependencies`及`### Skills 使用`。缺真实命令结果、缺skills证据或突破写文件边界，不直接整合为完成。

## 4. 阻塞与修复规则

- 合同模型缺字段：报告Leader，先修contract/models，再解锁依赖；不得在各worker局部定义同名不兼容dict。
- 发现生产记忆删除/隔离缺陷：保留受控复现和失败评分，单独列修复项；不得在adapter里强删或绕过使结果变绿。
- 软件测试失败：根据本轮原始输出判断是否相关；相关失败修复后重跑，不用旧审计“已知”自动豁免。
- 无凭据/预算/Judge校准：继续不依赖它的软件工作；真实质量验收明确未完成，不能说整体质量达标。
- 运行批次中断：使用既有冻结计划/唯一键恢复，不重复创建试验；取消需确认进程回收再结束。

## 5. 尾盘（G10之后，Leader负责）

1. Leader执行集体回归并保存collective-test，核对所有WUs与spec覆盖。
2. 只读reviewer与security-auditor独立审查已集成代码；有并发/索引性能疑点时另派perf-auditor。此时最多3名审查者，不复用实现实例作为独立审查。
3. 审查问题由Leader安排串行修复或文件不冲突的修复WU，再跑受影响验证。
4. Leader写`reviews/2026-11-02-memory-evaluation-platform-code-review.md`、同stem security-review、`verifications/2026-11-02-memory-evaluation-platform-collective-test.md`并关闭execution-log。
5. 最终报告分别列软件交付状态、真实模型评测状态、支持/不支持能力、已知产品失败与未完成基准，不把受控脚本结果称为真实记忆提升。

## Next

- 认可顺序与边界 → 说“开始实现”或“并行执行”。
- 只改任务细节 → 调整plan；只改调度/所有权 → 调整本文件；字段与分母变更 → 三份文档同步。
- 计划阶段结束于文档交付，不执行上述GROUP。

## 6. 本次测量审查增量

- 所有编码前先执行plan§0.1 M0环境准备；无需新建业务WU。
- WU-01纳入annotation_scope、算法版本与决策policy字段；WU-05实现contract§8的最大事实匹配、完整/部分gold、明确聚合公式及grounded_memory_success。
- WU-08区分statistical_direction与adoption_decision；WU-11对应显示统计支持与实际采用限制，不把分数略高或区间跨0解释成确定结论。
- WU数量、依赖和文件所有权保持；审查报告为`.ai-runtime-artifacts/reviews/2026-11-02-memory-evaluation-measurement-document-review.md`，执行者必须读取。

## 7. 执行期修订（Leader 记录，随本文件生效）

### 7.1 WU-01 文件所有权扩展（2026-11-02 ITER 2 复审后）

契约 §1 要求 `CaseWrite/CaseRead` 增加 `memory_scenario_ref`，这使 `tests/evaluation/test_validation.py::test_case_write_field_set_matches_contract`（枚举 CaseWrite 精确字段集）必然失败。该文件原不在 WU-01 白名单内，**现扩展给 WU-01**：只允许改这一处字段集枚举（加入 `memory_scenario_ref`、`memory_required_metrics`），不得改该文件其他用例。

### 7.2 契约增量：`memory_required_metrics`（2026-11-02）

契约 §1 已补：专项 case 的「必需 memory 指标判据」由可选字段 `memory_required_metrics`（memory 指标 id 列表，缺省空）承载。专项 case 必须非空、普通 case 必须为空，二者同时出现即拒绝。下游 WU（04/05/09/10/11）一律使用该字段名，不要另起同义字段。

### 7.3 `plan_batches` 归属（2026-11-02）

分批公式 `min(40, floor(200/repetitions))` 的实现落在 `nanobot/evaluation/memory/repository.py::plan_batches`（WU-01 已产出并被 `create_experiment_plan` 使用）。**WU-07 不得重复定义**：WU-07 在 `nanobot/evaluation/memory/experiments.py` 中 `from nanobot.evaluation.memory.repository import plan_batches` 并在模块 `__all__` 中再导出，保持单一真相源。

### 7.4 迁移 4 幂等要求（2026-11-02）

契约 §7 与 plan §3 Step 4 要求「migration4 重复执行无副作用」。`ALTER TABLE reviews ADD COLUMN memory_ref_json` 在库版本号落后于实际结构时不可重复执行，WU-01 必须让迁移 4 幂等（可用 `PRAGMA table_info` 判存在后跳过，或让 `_MIGRATIONS` 条目支持可调用语句），且迁移 1/2/3 正文逐字不变。


### 7.5 指标 ID 落档（2026-11-02 ITER3）

`longmemeval_binary_judge`（WU-06a D8 近似 Judge，official=False，协议 hash 冻结）与 `repeat_stability`（spec §6 重复稳定性）已补入契约 §9 执行期修订。下游 WU（04/07/08/09/10/11）直接引用这两个 id，不得另起同义名。WU-08 报告展示 longmemeval 分数时必须标注「平台近似判定，非官方分」。
