---
artifact: spec
route: writing-plans + api-and-interface-design
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - .claude/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/api-and-interface-design/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/specs/2026-11-02-memory-evaluation-platform-spec.md
  - .ai-runtime-artifacts/contracts/2026-10-26-contract-evaluation-reliability.md
created_at: 2026-11-02
status: proposed
approved: false
---

# 记忆专项评测增量契约 v1

本契约随实施计划待审；仅定义增量，不表示接口已实现。沿用 `/api/eval/v1`、snake_case、认证/Origin/CSRF、四值verdict、受控artifact路径和现有事务/幂等框架。

## 1. 数据与旧接口兼容

`MemoryScenario`沿用设计示例，schema_version=`memory-scenario/v1`，包含scenario_id、revision、split、template_family、cluster_id、tags、scope、events、probes、gold。

- scenario_id/probe_id/message_id：1..120字符，集合内唯一；revision正整数；split=development/calibration/regression；cluster_id标识原始conversation，缺省scenario_id。
- scope包含subject_id、workspace_id，均为合成测试标识；只映射到本trial新建的作用域，不接收用户真实目录或以标识冒充授权。
- 事件判别type=session/settle/checkpoint/restore/restart/forget/rebuild。session含带时区at、id、role=user/assistant的messages；system/tool消息仅由经审查的外部loader显式映射，不允许用户注入system角色。
- settle.after_session必须引用先前session；默认timeout_s=60，范围1..900且受trial剩余时限约束。真实轮次结束与产品会话结束分别记录，不能把after_run等同USER_CLOSE。
- checkpoint/restore引用已产生的检查点ID；restart关闭并重新构造同一checkpoint对应runtime，必须从持久化文件恢复；不允许保留旧对象伪装重启。
- forget以事实key/服务端解析ID为目标；mode=product_request/admin_delete分报。rebuild要求真实后端具备能力；不支持则capability_missing，不以测试专用强删逻辑替产品补能力。
- probes含id、question、at、fresh_session=true、checkpoint_id（可空，默认最终已settle状态）、scope（可空，继承场景）；一个probe不写回下一probe的起点。
- gold包括required_facts、forbidden_current_values、answerability、expected_absence、allowed_evidence_sets、state_expectations。fact含key/value、subject/scope、valid_from/valid_to（可空）、evidence_message_ids；不得流入执行载荷。
- 检索轨道可提供reference_state：带来源的标准事实、episode、画像/工作记忆及版本；只能用后端真实导入能力，memory_seed文本不冒充结构化状态。
- 未知字段、重复ID、时间倒退、悬空证据、跨split复用template_family、未声明共享范围一律校验拒绝。事件发生时间可不同于有效时间，不能以有效时间变化误判事件乱序。

`MemoryDatasetRevision`：id(UUID)、suite_id(UUID)、dataset_version、status=staging/ready/invalid、split_counts、source={repo_url,revision,sha256,license,original_ids,loader_version,grading_protocol}、definition_digest。来源与gold版本不可变；ready修订后产生新revision。

现有`CaseWrite/CaseRead`增加可选`memory_scenario_ref={dataset_revision_id,scenario_id,revision}`。普通case的turns 1..20、assertions 1..50、必需rule/rubric校验保持；专项case允许turns=[]、assertions=[]，但必须能解析ref且有必需memory指标判据。**该判据以可选字段`memory_required_metrics`（memory指标id列表，缺省空）显式承载**：专项case必须非空，普通case必须为空，二者同时出现即拒绝。拒绝memory_seed、script与专项ref同时提交；首版不支持memory专项混合普通case、Skill/Team目标，返回明确错误。scenario的events不塞进旧turns字段。

执行端`MemoryExecutionInput`由scenario去掉gold、来源许可及评分判据后产生，仅包含scope/events/probes/reference_state、轨道与生效候选。场景原文和gold在评估服务私有存储；Agent工作区只能看到真实产品允许的历史/状态。私有gold不能以artifact路径挂载给Agent工具。

## 2. 候选、实验组与分批

`MemoryCandidateInput`：id、name、mode=zhixu/no_persistent_memory、agent_profile_id、extractor_profile_id、memory_settings（白名单Pydantic模型）。不提供任意Python类名、模块、路径、shell或动态导入参数。

`memory_settings`保存真实解析后的功能开关、抽取/合并策略版本、检索通道/权重/阈值/召回上限/注入预算、embedding及索引版本。首版只开放现有后端已支持或本轮接入的可验证字段；无法落到真实配置的参数422 UNSUPPORTED_MEMORY_SETTING，绝不静默忽略。

`MemoryExperimentCreate`：dataset_revision_id、scenario_ids（可选；缺省ready修订所选split全部）、split、track=extraction/retrieval/end_to_end、candidates(1..3)、repetitions(1..10)、model_mode=real/scripted、judge_profile_id（可空）、budgets、primary_metric、policy。

- model_mode只影响模型是否真实；scripted脚本来自平台受控测试fixture，与memory_scenario_ref互斥的旧case.script不是同一入口。脚本模式成绩不能与real模式比较。
- scripted时必须提供静态目录白名单中的script_fixture_id，并冻结其hash及抽取/reader响应序列；拒绝任意文件路径或上传代码。real禁止script_fixture_id。no_persistent_memory只用于end_to_end轨道，不以空抽取结果参与抽取算法排名。
- budgets声明max_trials、max_model_requests、max_input_tokens、max_output_tokens、max_total_cost（可空）、per_trial_timeout_s、group_timeout_s和concurrency。未知单价不允许自动货币预算放行；Token/请求预算仍可执行。
- 默认max_trials=200，核心120×3及多candidate需用户在实验表单明确提高总预算；每个子run仍<=200。分批大小=min(40,floor(200/repetitions))，三候选、120场景、3次共1080 trials；每candidate三批，共9个run。不能只为每批检查预算而漏掉全组总量。
- freeze当前代码与candidate配置；子run复用同一冻结快照。现有调度器领取attempt，实验组协调器只负责创建/取消子run和聚合，不另起执行信号量。
- trial身份为(experiment_id,candidate_id,scenario_id,revision,trial_index)。不同attempt重试不增加planned_trials；成本累计所有attempt。
- 实验组状态queued/running/cancelling/completed/cancelled/interrupted/timed_out，另有measurement_status=complete/incomplete，避免技术完成被当全部指标达标。
- 当天与次日代码比较：直接引用已保存的baseline实验；新代码运行候选后比较。首版不从HTTP启动任意Git checkout，不动态切换服务源码。配置候选可在同一代码版本运行；所有实际源码SHA与dirty摘要均记录。

## 3. 后端adapter与完成屏障

下列类型定义在`nanobot/evaluation/memory/protocol.py`；实现是async，所有结果为严格Pydantic/dataclass，不用无结构dict作跨模块边界。

```python
from typing import Protocol
from nanobot.evaluation.memory.models import (
    MemoryCandidate, MemoryCapabilities, MemoryExecutionInput,
    MemoryProbe, MemorySessionEvent, MemoryStateSnapshot,
    RecallResult, ProbeExecution, SettleResult, ForgetRequest, ForgetResult,
)

class MemoryAdapter(Protocol):
    capabilities: MemoryCapabilities
    async def reset(self, candidate: MemoryCandidate) -> None: ...
    async def ingest_session(self, event: MemorySessionEvent) -> None: ...
    async def settle(self, session_id: str, timeout_s: float) -> SettleResult: ...
    async def snapshot(self, checkpoint_id: str) -> MemoryStateSnapshot: ...
    async def restore(self, state: MemoryStateSnapshot) -> None: ...
    async def restart(self, state: MemoryStateSnapshot) -> None: ...
    async def recall(self, probe: MemoryProbe) -> RecallResult: ...
    async def answer(self, probe: MemoryProbe) -> ProbeExecution: ...
    async def forget(self, request: ForgetRequest) -> ForgetResult: ...
    async def rebuild(self) -> SettleResult: ...
    async def close(self) -> None: ...
```

这里的Protocol省略号是类型签名，不是实现内容。`MemoryStateSnapshot`含version、checkpoint_id、state_digest、受控artifact引用、origin_map、storage_coverage；复制SQLite前完成后台任务并使用backup API，不直接复制活动WAL文件。clone不改变gold。

`SettleResult`含status=completed/skipped_by_policy/failed/timeout、completed_stages、failed_stages、reason_codes、state_version、index_version。异常被产品链路隔离时仍需收集失败轨道，不能因函数返回就completed。

`MemoryCapabilities`逐项声明extraction、semantic_recall、episodes、working_memory、profile、delete、rebuild、restart、clock_coverage、scope_coverage。unsupported与试验失败分开，但缺必需能力使qualification不能passed。no_persistent_memory显式关闭所有持久读写，并清空查询历史，不以“禁止访问检索工具”代替关闭记忆。

端到端输入采用固定历史回放协议：导入原始user/assistant历史并调用与产品相同的抽取入口，query走真实Agent；历史助手回答不是本次模型重新生成。报告标history_mode=replay_transcript，不能声称同时评估了历史对话生成质量。需要真实history生成的轨道单独设计，不混入首版固定数据基准。

完成屏障覆盖实际MemoryExtractionHook的idle/增量任务和Dream/索引任务；优先新增可等待的公共协调入口复用同一产品逻辑，不通过session删除触发抽取，因为它会改变被测状态。不以未接通的SessionEndOrchestrator单独代表产品完整链路。

`MemoryClock`分别提供wall_now（测试日期）、monotonic（策略间隔）和real_deadline（真实执行超时）。真实超时不得随模拟时钟跳跃；retrieval freshness/TTL/默认时间戳/幂等/派生文件刷新覆盖写入capability。默认生产调用无注入时保持现有时间行为，禁止全局monkeypatch。

## 4. Worker、Trace和评分

沿用AgentWorkerBoot role=agent，新增可选memory_execution字段；含ref的case_snapshot本身不带gold。Worker按ref分派到专项runner；普通case继续旧函数。AgentWorkerResult新增可选memory_result；既有final_answer保留，仅为最后probe摘要，不能拿它代替全部probe结果。

TraceEvent闭集增加一个`memory.event`，payload包含kind、scenario_id、probe_id、checkpoint_id、scope、state_version、origin_refs、body/artifact_ref。kind=session_closed/extraction_completed/extraction_skipped/extraction_failed/memory_changed/index_ready/recall_candidates/reranked/injected/probe_answered/state_snapshot/deletion_checked。沿用同一attempt seq/event_id/大小限额/密封；缺任何必需阶段时coverage不可标完整。事件用真实产品observer采集；不解析截断日志，也不从预期gold伪造事件。

`ProbeExecution`保存answer、全部记忆来源与实际上下文摘要、召回排序与来源、实际注入证据、usage、timing、coverage；原文按artifact权限读取。observer记录真实生效配置，启用字段但未生效属于测量失败。

`MemoryMetricResult`：id、version=1、grading_revision、review_digest（可空）、unit=ratio/count/ms/token/bytes/currency、value（可空）、numerator/denominator（可空）、status=scored/not_assessable/not_applicable、scope=probe/scenario/category/dataset、evidence_refs、reasons、coverage、interval（可空）。字段缺失不是0。semantic评分另带grader_fingerprint与calibration_status。

指标ID：extraction_precision/extraction_recall/extraction_f1/unsupported_memory_rate/evidence_recall_at_k/retrieval_precision_returned_at_k/mrr/injection_recall/qa_accuracy/preference_compliance/update_accuracy/stale_use_rate/abstention_accuracy/false_abstention_rate/cross_session_accuracy/restart_accuracy/forget_behavior_success/forget_state_success/scope_leak_rate/memory_lift；加write/index/retrieve/inject时延与Token/嵌入/存储。Precision分母取实际返回数，名称显式区分固定k分母版本。

原子事实一对一匹配；零相关gold的Recall/MRR为not_applicable，空召回但有相关gold的Recall/MRR为0，precision为not_applicable；有gold且未抽到事实时Recall/F1为0，纯负例无事实输出单独negative_write_success。重复事实记冗余，不增加TP。F1使用2TP/(2TP+FP+FN)，总分母零时null。

确定性事实用标准化key/value/时间/scope规则；自由文本语义对齐用固定版本Judge，无校准时not_assessable或待复核，不把字符串contains当通用语义正确。评分重用父进程已有rules/Judge/reviews/calibration，新增逐probe评分材料，不在Agent子进程自行Judge。

现有ManualReviewCreate/ManualReview新增可选`memory_ref={scenario_id,probe_id,metric_id,grading_revision}`；复用POST `/attempts/{id}/reviews`。服务端验证引用确属该attempt且评分版本未过期，过期409 MEMORY_GRADING_REVISION_CONFLICT。reviews表追加可空memory_ref_json，旧记录保持null；原整attempt的有效verdict只读取null的整体验收review，不能把单probe通过直接当整任务通过。专项评分按review引用产生新grading_revision与review_digest，保留原分数；既有比较快照不回写，重比较生成新记录。

Agent通用指标从既有assessment/reports读取；补task_success_rate_judged、tool_decision_accuracy、tool_argument_accuracy及其annotation_coverage。无人工/声明式决策标注返回not_assessable，不能用工具执行成功率顶替。旧task_success_rate=completed/planned和旧verdict不重定义。

## 5. 对比与冻结

memory_manifest version=1作为现有manifest子结构，冻结dataset/grading/environment/candidate与source状态。实验创建时声明changed_fields的JSON pointer白名单；实际变化必须是其子集。比较输入与评分不变，环境只排除明确声明的被测参数。reader、抽取模型、embedding任意变化必须显式声明；整体memory-stack变更不声称单因素因果。

`MemoryComparisonCreate={baseline_experiment_id,candidate_experiment_id,baseline_candidate_id,candidate_candidate_id,changed_fields,policy}`。双方实验均终态，dataset revision、track、model_mode、repetitions、gold/grader及非被测条件兼容。metadata缺失422，条件不匹配409；未完成不生成“严格完整”比较。

按scenario先聚合trial与probe，再按cluster_id重采样（seed=42、2000轮、95%）；独立cluster<20不输出区间。保留paired/excluded/planned数；主要指标scenario macro，同时显示category macro和probe micro。不同split/官方协议分数不得混合。critical scope泄漏/删除复活优先阻止放行，null或未校准所需指标为review_required。主要增益区间下界>0才显示统计支持提升，预算阈值与非关键容忍度须预先声明；未配置只出诊断。

## 6. HTTP API与分页

沿用`{error:{code,message}}`，分页返回`{data,next_after_key}`，limit默认50、1..200，游标为服务端不透明token，含筛选指纹，稳定(id/seq)排序。写请求都经过原guard；create/import/compare使用Idempotency-Key，按scope+route+key原子唯一claim，同key异体422、处理中409；业务写入和幂等结果同事务，恢复后的重复不会创建额外子run或收费试验。

| API（前缀/api/eval/v1） | 请求 | 响应 |
| --- | --- | --- |
| GET /memory/datasets | after_key,limit,split | DatasetRevision列表，不带全部历史/gold |
| POST /memory/datasets/import | multipart metadata+file；JSON/JSONL/longmemeval；文件hash与来源必填 | 202 `{import_id,status,dataset_revision_id}` |
| GET /memory/imports/{id} | after_key,limit（错误分页） | `{id,status,counts,errors,next_after_key,dataset_revision_id}`；errors分页 |
| GET /memory/datasets/{id}/scenarios | after_key,limit | `{data:ScenarioSummary[],next_after_key}` |
| GET /memory/datasets/{id}/scenarios/{scenario_id} | revision | `{execution,gold,revision}`；只在评估授权管理面展示 |
| POST /memory/datasets/{id}/revisions | `{base_revision,changes:MemoryScenario[]}` | 201 DatasetRevision；CAS防覆盖，ready原revision不变 |
| GET /memory/capabilities | 无 | 支持轨道、设置白名单、候选profile及缺失能力 |
| POST /memory/experiments | MemoryExperimentCreate | 202 `{id,status,planned_trials,child_runs}` |
| GET /memory/experiments | after_key,limit,dataset_revision_id | 实验摘要分页 |
| GET /memory/experiments/{id} | 无 | frozen manifest、budget、measurement_status、coverage、进度 |
| POST /memory/experiments/{id}/cancel | `{}` | 202，已终态重复取消200并返回终态 |
| GET /memory/experiments/{id}/metrics | 无 | `{metrics_version:1,metrics,scope,coverage}`，全实验范围 |
| GET /memory/attempts/{id}/probes | after_key,limit | probe执行/评分摘要，不含私有gold文件路径 |
| GET /memory/attempts/{id}/events | after_seq,limit | memory.event投影，沿用Trace权限与artifact引用 |
| POST /memory/comparisons | MemoryComparisonCreate | 201 MemoryComparison摘要 |
| GET /memory/comparisons/{id} | after_key,limit,status | 全量统计+当前页配对；不是分页后重算统计 |
| GET /memory/comparisons/{id}/report | 无 | 同一快照的自包含HTML |

内置集合使用`POST /samples/import`新增sample_id=memory-smoke-v1/memory-core-v1，内部进入同一版本化导入器；返回suite_id和memory_dataset_revision_id。旧sample_id和xlsx路径不改变。首版新增集合JSON下载，通过已登记的artifact读取；不把外部基准数据重新打包发布到代码仓库。

导入流限制：单文件最多2GiB、单scenario最多32MiB、最多2000场景、单场景最多100000消息；服务端存储配额更严者优先。JSONL按行增量解析，JSON数组用标准库增量raw_decode；单条缓冲受限。临时上传仅评估服务可读，invalid不暴露部分ready集合；取消/错误后清理临时文件并保留诊断摘要。LongMemEval只读上传文件，不接受远程URL/可执行评分脚本。

错误：404 MEMORY_DATASET_NOT_FOUND/MEMORY_SCENARIO_NOT_FOUND/MEMORY_EXPERIMENT_NOT_FOUND/MEMORY_IMPORT_NOT_FOUND；409 MEMORY_DATASET_NOT_READY/MEMORY_EXPERIMENT_NOT_TERMINAL/COMPARISON_INCOMPATIBLE/REVISION_CONFLICT；413 MEMORY_IMPORT_TOO_LARGE；422 MEMORY_SCHEMA_INVALID/MEMORY_EVIDENCE_INVALID/MEMORY_BUDGET_EXCEEDED/MEMORY_MODE_CONFLICT/MEMORY_TARGET_UNSUPPORTED/UNSUPPORTED_MEMORY_SETTING/MEMORY_CAPABILITY_REQUIRED/COMPARISON_METADATA_MISSING。内部处理失败使用500且不返回路径、异常正文或凭据。

## 7. 存储与迁移

复用suite/case/run/attempt与gold/review/artifact的现有关系；不再建第二套trial执行数据库。追加memory_dataset_revisions、memory_scenarios、memory_import_jobs、memory_experiments、memory_experiment_runs、memory_comparisons表；metric结果进入attempt.result.memory，snapshot正文为受控artifact。

同一migration4追加reviews.memory_ref_json可空字段；读取旧review不要求该字段非空，专项引用与整体验收分开，不迁移旧评分的语义。

- memory_scenarios：PK(dataset_revision_id,scenario_id,revision)，unique(case_id,case_revision)，execution_json与gold_json分列；case快照只存ref。
- memory_experiment_runs：unique(experiment_id,candidate_id,batch_index)，run_id唯一；实验与所有子run在同一事务冻结/入队，或恢复时用唯一键补齐既有计划，不能重复创建。
- imports状态staging/running/ready/invalid/cancelled，expected_count与observed_count校验后原子发布ready。
- comparisons存结果及引用快照，禁止读当前可变case重算历史报告。

当前源码SCHEMA_VERSION="2"但_MIGRATIONS包含"3"。先加迁移回归验证既有库，再统一升级至4并追加本轮表。保留历史1/2/3迁移正文；版本4仅代表本轮新DDL，不能重置已存在库版本。迁移先在复制库验证，备份落评估私有根；回退为恢复旧库并用匹配代码，禁止旧程序写version4库。

## 8. 测量算法补充（本轮计划审查修订）

本节细化§4/5，不改原有指标ID。匹配、分母、聚合和决策规则分别固定版本；评价算法与被测记忆算法分开记录，不能改了评分器后把分数变化归因于记忆改进。

### 8.1 输入与输出的最小闭环

执行输入：场景历史、session边界、时间与scope、更新/遗忘/重启事件、probe问题；检索专项另有reference_state。评分输入：私有gold、可替代证据、完整性标注、评分/校准策略。候选输入：生效模型、prompt、记忆算法/参数、代码摘要。实验输入：轨道、重复次数、预算、changed_fields、主要指标与容忍度。

每probe输出：实际回答、原子事实/来源、状态变化、召回排序、实际注入上下文、时延/usage、指标值/分子分母、score_status、证据及未知原因；实验输出：全量计数、场景/类别统计、严格场景通过率、覆盖率、运行失败和成本；比较输出：paired差值、cluster区间、关键失败、新旧逐题变化、statistical_direction与adoption_decision。

### 8.2 原子事实匹配与标注完整性

`gold.annotation_scope`新增closed_world/targeted，缺省targeted兼容旧示例；gold.extraction_facts为抽取轨道的完整目标集合，和仅针对某题的required_facts分开。closed_world须由标注者确认目标集合与允许的抽取范围完整；公开问答答案不自动满足此条件。完整性和确认状态进入grading_digest。

匹配v1步骤：

1. 将预测和gold拆成原子事实，保留(subject,key,value,scope,valid_from,valid_to,origin_refs)。同义key映射及value别名来自版本化词表；Unicode使用NFKC，统一空白/确定的数字日期格式。不得去掉“不/禁止”、比较符、时间限定或作用域。
2. 主体/key/scope/查询时有效区间一致后，canonical value相同可建立确定性等价边；自由文本需固定grader判定equivalent/contradictory/unrelated/unknown，并保留证据。只以已校准的equivalent建立边；unknown的必需关系使对应指标not_assessable，不能当不相等。
3. 对同一预测事实的重复先去重，重复率单列；在预测事实与gold事实间做最大基数二分图匹配（确定性增广路算法，节点排序固定）。不能贪心按出现顺序匹配，一个事实不能重复充当多个TP。
4. 对closed_world的抽取集合G与去重预测P，TP=匹配数，FP=|P|-TP，FN=|G|-TP；Precision=TP/|P|，Recall=TP/|G|，F1=2TP/(2TP+FP+FN)。FP中“源会话支持但不属于本场景存储目标”和“源会话无依据”分别诊断，不都叫幻觉。
5. targeted标注只支持已标目标的覆盖率，不能把额外记住的合法事实全部计FP。完整extraction_precision/F1返回not_assessable；只有显式确认的抽取全集才输出全量P/R/F1。无依据记忆率需要对每条输出和源会话做支持性判定，缺材料同样未知。

事实匹配中所用等价判定与答案Judge的角色分开，均记录grader指纹；不依赖某个embedding相似度阈值直接认定事实相同。向量相似度可筛选待比对候选，不替代否定/时间/scope核验。

### 8.3 单题记忆分与检索分

对可评分probe，`qa_correct`为0/1：所有required_facts均覆盖，且无被断言为当前事实的forbidden值、关键矛盾或scope违反；部分正确记录事实覆盖分，不能四舍五入成成功。无答案题以正确承认信息不足记1，编造或泄漏记0；有答案却拒答记0。

语义0–4量表是诊断辅助，不直接当二值正确率。先做确定规则和事实核验；自由文本判断只能在所需校准通过后用于二值判定，否则qa_correct=null。必需材料缺失为null，不能用低分替代；已经有确定违规证据的题可以判0，不因另一项未知而掩盖违规。

`grounded_memory_success`新增：qa_correct=1，且全部必要证据在该probe实际可用的合法记忆上下文中。qa_correct与该指标分别报告：答对但未观察到记忆证据，只证明答对，不能归因为记忆使用；产品不要求回答显式引用时不要求输出引用。该指标证明证据可用与答案受支持，不证明模型内部真正采用了某条记忆；净收益另看无记忆配对对照。

设gold证据单元Gq，top-k结果映射/去重后覆盖Cq，Recall@k=|Cq∩Gq|/|Gq|；MRR=首条相关结果rank的倒数，多题均值。实际返回precision=相关返回条数/实际返回条数，并显示返回数。多跳需要“全部必要证据集齐全”另列，不能命中一条就算整题证据足够；等价证据组只需完整满足任一合法组。实际注入覆盖按最终模型上下文重算，不沿用召回值。

targeted题的Recall明确为相对已标必要证据的覆盖；返回precision需要完整相关性标注或逐结果经确认的相关性判定，不能把未标返回项默认为不相关。判定不全时precision为not_assessable，保留已知命中数和相关性覆盖。

定向更新、偏好、重启题以对应probe的qa_correct聚合；stale_use_rate与scope_leak_rate按确定的违规事件/题计算，越低越好。forget_behavior_success要求所有删除后probe不暴露目标；forget_state_success还需声明范围内存储/索引/派生/重建检查全部通过。不支持state检查时不能推断state成功。

### 8.4 聚合、未知与重复

主指标明确命名`qa_accuracy_scenario_macro`（新增ID）：设场景i有Qi题、K次重复，全部所需q(i,k,j)可判，s_i=(1/K)Σk[(1/Qi)Σj q(i,k,j)]，S=(1/N)Σi s_i。类别指标在各类别内部同样按scene平均；题级micro作为补充。严格场景通过率单独定义为：每次trial所有必需probe与硬条件均通过的比例；重复稳定性再要求同场景K次全部通过，三者不能互换。

默认probe等权、重复等权、scene等权；自定义权重必须在实验前声明并改变grading_digest。没有题的extraction轨道不算qa_accuracy，使用自己的抽取指标；不同轨道不混合总分。

未知不填0：展示各metric已评分/planned覆盖、技术失败分布，以及成功/planned的交付口径。完整主指标需要所有预定scene、trial、probe具有该指标判定；缺失时可以展示明确标注的已评分子集描述值，但measurement_status=incomplete，不能据该子集宣布总体提升。已有确定失败不因未知而被删除。

区分三类情况：没有执行/模型服务异常是技术未知；产品按策略跳过抽取后仍能正常问答时按真实回答计分；产品明确违反结果/边界是已知失败。重试不能选最好一次，trial执行前固定结果选择规则，全部物理请求费用照计。

### 8.5 比较、区间与采用判定

1. 优先检查评分完整、gold可信确认状态、必要语义校准、输入/非被测配置一致、重复完成和关键边界；不满足则incomplete/incomparable/review_required，不给整体改善结论。公开集确认依据可为锁定的已发布标注及审查过的导入协议，产品集由标注者确认，自动生成标注不得直接标confirmed。
2. 主要比例指标差值Δ=100×(S_candidate-S_baseline)，单位百分点。错误率先按direction=-1转为“正值代表改善”的差；延迟/费用保留自身单位并给相对变化，不与正确率加权成总分。
3. 对相同scene求d_i=s_candidate_i-s_baseline_i。按原conversation/共享事实模板的cluster_id整体抽样，C个cluster每轮有放回抽C次，把各被抽cluster中全部配对scene的d_i串联后求均值。不是各cluster均值再无条件等权，也不把重复trial当新增独立scene。
4. 固定seed42、2000轮，取重采样均值2.5%和97.5%的线性插值分位数（type7），得到paired_cluster_percentile_v1区间。C<20不显示区间；20只是显示门槛。数据量、cluster独立性与稀疏/退化分布同时提示；区间不提供确定性保证。A/A参考同样固定此规则；两组别分别独立bootstrap后比较区间重叠不能替代配对差值区间。
5. statistical_direction：区间下界>0为improved，上界<0为regressed，其余inconclusive。不说“跨0就是没有变化”。
6. adoption_decision另看预先声明`minimum_effect_pp`、`max_regression_pp_by_metric`、`max_cost_ratio`、`max_p95_latency_ratio`和关键门禁。minimum_effect_pp无默认行业值，缺省仅出诊断、不自动采用。主指标改善区间下界超过minimum_effect_pp，所有必需分项满足容忍度、无关键边界失败且预算通过，才能显示adoptable。置信区间上界小于-minimum_effect_pp可称有实际意义的退化；其他组合显示证据不足或权衡。
7. 明确隔离泄漏/删除复活的已知失败可直接blocked，即使主要平均分上升。关键必测项缺证为review_required。分项退化检查用其差值区间下界对容忍度评估，样本不足则不能自动通过；零容忍场景按已知事件门禁，不以“未显著”豁免。
8. 只对预先指定主指标作主要结论，次要指标是诊断/门禁；大规模选参须独立保留集复验，不用同一数据反复筛选后的区间充当无选择偏差证明。模型服务版本发生变化时，旧结果只能按新变化项解释或重跑基线，不能全归因于记忆算法。

判分策略新增`fact_matching_version`、`metric_aggregation_version`、`bootstrap_version`、`minimum_effect_pp`与校准策略（min_samples/min_per_class/min_agreement/max_false_pass_rate）。这些均入grading_digest；新旧历史记录若不具备相同评分证据，需用同一评分版本复算双方并形成新记录，否则标不可比。

## Next

本契约与同日memory-evaluation-platform-plan.md一并评审；用户说“开始实现”后才进入编码。接口或指标变更先改本契约再调整任务，禁止worker自行分叉命名/分母。

## 9. 执行期修订（Leader 记录，随本文件生效）

### 9.1 `longmemeval_binary_judge` 指标 ID 落档（2026-11-02 ITER3）

WU-06a 按 Leader 对 D8 的拍板新增 `answer-phrase/v1` 近似 Judge，其指标 id 定为 `longmemeval_binary_judge`（实现在 `nanobot/evaluation/memory/longmemeval.py::LONGMEMEVAL_JUDGE_METRIC_ID`）。本节补档其口径：

- 独立 metric id，不属于 §4/§8 的通用记忆判分层；`official=False`。
- 判定规则版本化为 `answer-phrase/v1`，协议 hash 随规则变化冻结，写入 grading 证据。
- 报告展示时必须标注「平台近似判定，非官方分」，不得与 LongMemEval 官方口径混算。
- §8.2「不用字符串 contains 当通用语义正确」的约束对象是通用记忆判分层（`grading.py`），与本近似 Judge 不冲突。

### 9.2 `repeat_stability` 指标 ID 对应关系（2026-11-02 ITER3）

spec §6「重复稳定性」的实现指标 id 为 `repeat_stability`（实现在 `nanobot/evaluation/memory/metrics.py::REPEAT_STABILITY`）。契约 §4 声明「不改原有指标ID」，spec 指标字典中的中文指标名与本仓库实现的英文 id 一一对应，本条仅落档对应关系，无口径变更。
