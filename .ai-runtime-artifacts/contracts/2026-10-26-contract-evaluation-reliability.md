---
artifact: spec
route: api-and-interface-design
skills: [api-and-interface-design]
skills_evidence: [.claude/skills/api-and-interface-design/SKILL.md]
source:
  - .ai-runtime-artifacts/contracts/2026-10-15-contract-agent-evaluation.md
  - .ai-runtime-artifacts/contracts/2026-10-25-contract-evaluation-observability.md
  - .ai-runtime-artifacts/research/2026-10-26-agent-evaluation-project-audit.md
created_at: 2026-10-26
status: proposed
approved: false
---

# 自动化评估可靠性增量契约（计划依据，尚未实现）

沿用 `/api/eval/v1`、snake_case、认证/CSRF/Origin、幂等及 `{error:{code,message}}`。不更改现有四值verdict。
本契约随计划待审；只定义本轮新增接口，不把历史数据重解释为已采齐证据。

## 1. 冻结对象与执行模式

RunCreate保留现有字段，新增：

```python
repetitions: int = 1                 # strict int，1..10
model_mode: Literal["real", "scripted"] = "real"
```

总attempts = case_ids数量 × repetitions，限制 <=200，否则422 ATTEMPT_BUDGET_EXCEEDED。
model_mode与execution.tool_mode正交：fixture表示工具受控，仍可以使用真实模型；scripted只允许fixture且必须有script；real遇到非空script报422 MODEL_MODE_CONFLICT，不静默回放。
老请求缺model_mode：有script且所有case为fixture才推导scripted，其余为real；live+script一律拒绝。快照必须记录推导来源。
rerun继承源model_mode及所有冻结内容，不重新读取现时目标。

`RunManifest`新增于run.snapshot.manifest，version=2：

- dataset_digest：按排序后的case_id/revision/定义摘要形成；不是可变suite名称。
- target_digest：完整目标快照摘要；包括Agent档案/提示词/Skill文件、team revision/Leader/成员档案与依赖技能、默认身份模板摘要。
- environment_digest：fixture、记忆种子、时钟、已解析工具白名单、运行限制；不含随机attempt路径。
- grading_digest：规则版本、量表正文、Judge模型/参数/prompt版本、材料选择策略；不含凭据。
- candidate_digest：被测Agent的目标快照+有效模型/参数；与环境和评分版本分离。
- source_revision、source_dirty_digest：代码版本与相关受控源码差异摘要；只摘要被测代码，不读取敏感文件。
- model_mode、tool_modes、package_versions、snapshot_schema_version；无法获取代码版本则null并标记原因。

团队快照在入队冻结，后续不读共享团队库；成员能力 = 冻结档案 ∩ 用例白名单 ∩ 评估支持集。
team_*编排工具作为独立内部编排能力显式记录，禁止因此打开原生spawn或系统命令。
成员模型默认统一使用run选定profile；团队自带各成员模型的异构比较留后续，不能静默覆盖选择。
有效max_iterations/timeout_s取case与run限制的更严值，写入Worker boot及capability；团队成员另受冻结团队限额约束，任何一层都不能放宽另一层。模型物理请求重试另有计数，不用iteration上限冒充请求预算。

### 1.1 用例环境依赖与工具模式（实施期补充）

CaseWrite 新增两个可选字段：

```python
dependencies: list[str] = []      # 用例必须齐备的输入素材（相对路径）
required_inputs: list[str] = []  # 运行期必须存在、但故意不随用例提供的输入
```

- dependencies：入库即校验，每个路径必须由同用例 fixtures 提供，否则 422
  CASE_DEPENDENCY_MISSING。缺依赖要在入库时明示，不留到运行期。
- required_inputs：入库合法，用于「缺输入」边界用例；Worker setup 阶段检查，
  缺失即 setup 失败并报 CASE_INPUT_MISSING，禁止用空文件假装任务成功。

live 工具集收缩：LIVE_ONLY_TOOLS 不再提供 exec / exec_session / apply_patch，
原生 shell 与补丁执行在 live 模式同样不可选（FORBIDDEN_LIVE_TOOLS）。
理由：这些工具既不在受控白名单内，又属于运行期禁止清单，放进可选集等于允许
用例在入库时申请到原生 shell。要求「入队即拒绝」，所以在模型校验层直接不提供。

## 2. 身份、事件、完整性

复用event_id、seq、execution_id、parent_execution_id、call_id、model_call_id；新增可选target_kind/target_id/skill_id，表示被测对象，与执行Agent身份区分。
Skill以真实隔离执行档案身份执行，skill_id单独记录，不捏造用户已有Agent；未知agent_name保持null。
新增agent.started/completed/failed、team.dispatch/member.started/member.completed事件进入Trace闭集；工具拦截新增tool.denied；所有新事件同步schemas/IPC/读取兼容。
委派边须由team/task/member真实事件与execution_id映射生成，不靠最终文本或时间猜测。单attempt所有成员通过同一单调seq汇聚；多provider实际请求只记一次。

`EvaluationCoverage`：

```json
{"trace":"complete","identity":"complete","usage":"partial",
 "artifacts":"complete","reasons":["PROVIDER_USAGE_MISSING"]}
```

Trace完整来自manifest密封及生命周期检查，不能传override=True。缺usage不会自动把客观任务做错，但效率维度不可判；要求轨迹的维度缺Trace必须不可判。
保留现有trace_complete；v2合格策略按所需证据判断，旧记录标legacy，不能自动生成缺失事件或重写原verdict。

## 3. 结果、维度与合格

新增case可选`evaluation`：

```json
{"version":2,"required_dimensions":["task","constraints","safety"],
 "milestones":[{"id":"artifact","weight":1,"assertion_ids":["file-valid"]}],
 "required_evidence":["final_answer","artifacts"],
 "reference_answer":null,"judge_artifact_paths":[],"budgets":{}}
```

默认新建开放任务要求task维度Judge或可验证里程碑；纯关键词任务允许运行，但评估能力标smoke，只能显示规则通过率，不能作为综合质量门禁。
里程碑引用真实assertion id，权重>0，至少一个；完成率 = 已通过权重/全部权重；任一所需结果未知则task_completion=null，保留已知子项，不能填0或当partial。

attempt.result.evaluation：

- task_outcome=completed/partial/failed/unknown；与execution_status的技术终态分开。
- dimensions：task/constraints/trajectory/efficiency/safety，各为passed/failed/not_assessable/not_applicable，含evidence_refs、reason_codes、score可空。
- qualification=qualified/disqualified/review_required/not_evaluated。
- measurement_errors：环境、采集、评分异常；保留现有errors，二者通过稳定code映射，不重复计数。

确定失败/硬约束违规优先disqualified；所需维度不可评分则review_required；全部必需维度通过才qualified。任务结果完成但效率超明确预算，可显示completed + disqualified。
现有verdict映射qualified→passed，disqualified→failed，review_required→review_required，未执行→not_evaluated。
基础设施失败保留execution_status，task_outcome=unknown，不当成已知Agent能力失败；全集分母仍保留。
不根据team内部partial状态直接推断任务部分完成，必须根据里程碑判定。

## 4. 新规则与Judge材料

扩展声明式规则：answer_exact、answer_length（len即Unicode码点，含标点空白，前后strip可选且固定）、answer_json_block（恰好一个json围栏并校验schema）、tool_trajectory、team_member_executed、usage_limit、duration_limit。
tool_trajectory.steps为{name, arguments_subset}，mode=exact/in_order/any_order；匹配按每次call_id一对一，不让一个调用满足多个步骤。in_order允许中间额外步骤但额外调用仍记录。team_member_executed读取真实member终态事件。
禁止操作分别记录“尝试被拒”与“实际执行违规”；不把拦截成功写成已越权执行。没有完整受控事件时安全结果不可评。
budget缺数据→None；重复参数调用仅为候选冗余，不自动把恢复重试判错。首期不提供任意正则或可执行断言。

JudgeMaterials v2必须包括完整turns、参考/里程碑、最终回答、实际工具及结果、已声明产物的有界脱敏正文、引用索引与缺失清单。
默认总材料64KiB（UTF-8），单个产物正文8KiB；优先预留任务/量表/引用索引，再选声明材料；截断结构化字段记录原长、已保留字节、原因。必需材料被截断则not_assessable，不能硬给低分。
JudgeDimension增加status=scored/not_assessable，后者score=null且写原因；旧响应无status按scored兼容。不得执行材料中的指令，Judge tools=None；保留最多一次格式修复。
Judge参数与校准指纹必须来自实际请求配置。人工确认样本≥10、正反各≥4、一致率≥0.8是原项目最低工程门槛，不宣称统计充分；新增混淆矩阵/假阳性/缺评数，并明确样本量。
prompt、材料策略、rubric、model、generation或gold修订任一变化必须重新校准。

## 5. 指标与重复

保留overall_pass_rate=passed/planned，界面名称改“综合合格率”；新增task_success_rate=completed/planned、partial_completion_rate=partial/planned、task_assessment_coverage=已知任务结果/planned。旧记录不新增伪任务结果。
返回均值/中位数/P95与有效样本数：iteration、model_requests、input/output/total_tokens、queue/agent/grading/end_to_end_ms。失败/重试的开销照计；主执行/辅助/Judge分别记账；并行成员耗时不相加冒充墙钟时间。
保留tool_case_pass_rate名字；tool执行错误率=错误完成调用/有终态调用。逐调用选择/参数准确率仅覆盖有人工或声明式判据的调用，并展示覆盖数。
attempt新增trial_index（0..repetitions-1）；DB唯一(run_id,case_id,trial_index)，rerun另run且有source_attempt_id。
可靠性先返回每case的successes/trials及“r次至少一次成功”“r次全部成功”经验比例，r必须相同且完整可判，同时展示符合条件case数和总case数；不把这些经验数当独立性假设下的p^r估计。

## 6. API增量

| API | 请求/输出 |
| --- | --- |
| GET `/runs/{id}` | 增加manifest、repetitions、model_mode、评估能力smoke/full/legacy与coverage；保留现有字段 |
| GET `/runs/{id}/metrics` | `{metrics_version:2,metrics,coverage,scope}`；scope含run_id、case_count、planned_trials、valid_trials |
| POST `/comparisons` | `{baseline_run_id,candidate_run_id,mode:"strict"}`；Idempotency-Key；201已生成读模型，非模型执行 |
| GET `/comparisons/{id}` | `{id,compatibility,pairs,metrics,intervals,excluded,reasons,next_after_key}`；pairs按case_id/revision对齐，query为after_key和limit（默认50上限200）；统计覆盖全比较快照，不是当前页 |
| GET `/comparisons/{id}/report` | 自包含HTML，与UI同一比较快照 |
| 既有校准/复核API | 复用/calibration-samples、/calibrations、/attempts/{id}/reviews，不另建重复接口 |

strict要求终态、相同dataset/environment/grading摘要、repetitions/model_mode/tool模式；candidate和source代码可以不同，作为被测变量显式列出。任一必需摘要未知，422 COMPARISON_METADATA_MISSING；不兼容409 COMPARISON_INCOMPATIBLE。初版不做自动交集比较。
比较展示改进/退步/不变/不可比，缺失trial不按失败替代。每case先聚合trial，再按case配对bootstrap，固定seed=42、2000次；有效配对case<20仅描述差值，不输出显著性结论。此20为本项目显示策略，不是统计学保证。
不自动宣布综合优胜；展示任务成功率差、合格率差、成本/耗时差、安全失败及覆盖变化。正向收益不能抵消安全硬失败。

### 6.1 内置样本登记（实施期补充）

- 内置样本 id 扩展为 basic-v1 与 reliability-v2；SampleImportRequest.sample_id 同步。
- reliability-v2（36 条分层样本）带判据元数据（case_id/target/tier/失败定义/
  可接受路径/所需环境/gold），不是 CaseWrite 形状：经 sample_cases 适配器转换后
  才能入库。元数据进 tags 与描述，判据原样保留。
- 适配器映射规则：environment.required 里普通 fixture 依赖转 dependencies；
  标注「故意缺失」的 fixture 依赖转 required_inputs；tool/skill/team/member 类
  依赖只进描述。
- gold 不随样本入库：人工确认走校准通道，样本内 gold 一律 proposed。

## 7. 存储、兼容与启动

追加migration v2，不改已发布v1 DDL：attempts增加trial_index/source_attempt_id，旧行回填trial_index=0；增加comparisons表（固定run refs/查询摘要/统计策略版本）；已有run保留v1快照。
所需新增评估字段优先进入不可变case定义/结果JSON，避免把每个维度拆成冗余状态表。manifest随run冻结。
独立`nanobot eval serve`从显式config/workspace构建目标与模型目录；服务独占锁覆盖共享数据目录，不能与内嵌实例并发写同一库。提供同源静态评估页面，WebUI嵌入模式保留。
CLI `nanobot eval run --suite-id ID --profile ID --target-kind agent|skill|team --target-id ID --repetitions N --output PATH`为待实现接口；输出路径在运行时用户授权位置，CI默认`.ai-runtime-artifacts/verifications/agent-evaluation/`。退出0=全部qualified，1=存在已知不合格，2=运行/判分/缺证/配置导致未完成或待复核（混合时2优先）。
凭据仅从服务配置进入内存与私有IPC，不放manifest/命令行/报告；旧记录不回填凭据或虚构归属。
