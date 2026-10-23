---
artifact: spec
route: api-and-interface-design
skills:
  - api-and-interface-design
skills_evidence:
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/api-and-interface-design/SKILL.md
source:
  - .ai-runtime-artifacts/specs/2026-10-15-agent-evaluation-spec.md
  - nanobot/agent/hook.py
  - nanobot/sdk/types.py
  - nanobot/llm_usage/models.py
created_at: 2026-10-15
status: proposed
approved: false
---

# Agent 评估 v1 接口契约

本契约随实施计划一起审阅。它细化已批准 spec，不单独增加产品范围。内部 Python 与 JSON 字段均采用 snake_case，枚举沿用 spec 的小写值；独立前端负责中文显示。金额、分布式调度和任意代码断言不属于 v1。

## 1. 通用约定

- 根路径：/api/eval/v1。所有 JSON 对象拒绝未知字段，响应明确区分 null、0 和缺失。
- ID 为服务端生成的 UUID 字符串；revision 为正整数；时间为 UTC RFC3339，浏览器按本地时区显示；耗时非负毫秒。
- 列表请求 cursor 可选，limit 默认 50、范围 1–200。响应 {data: T[], next_cursor: string|null}。cursor 不透明且绑定过滤条件，非法 cursor 返回 400。
- 普通资源响应为对象；DELETE 成功 204。空列表为 200，不以 404 表示。
- 错误：{error:{code:string,message:string,details:object|null},request_id:string}，message 为面向用户的中文，不包含堆栈、路径或配置内容。
- 400 INVALID_JSON / INVALID_CURSOR；401 UNAUTHENTICATED；403 ORIGIN_REJECTED / CSRF_REJECTED；404 NOT_FOUND；409 REVISION_CONFLICT / STATE_CONFLICT / IDEMPOTENCY_IN_PROGRESS；413 PAYLOAD_TOO_LARGE；422 VALIDATION_ERROR / IDEMPOTENCY_MISMATCH；429 RATE_LIMITED；500 INTERNAL_ERROR；503 PROFILE_UNAVAILABLE。
- 状态改变操作校验当前会话、同源 Origin 与 X-CSRF-Token；关闭通配 CORS。浏览器凭据仅为本站会话。
- 开发时可用 --dev-origin 显式允许 http://127.0.0.1:5174；只接受精确 loopback Origin，并把对应 Host 加入白名单。开发代理保留 Host/Origin，生产不启用此例外；CSRF 仍校验，禁止根据请求头动态扩大白名单。
- 固定排序：用例集/用例按 created_at,id 降序；运行按 created_at,id 降序；尝试按 position,id 升序；Trace 按 seq 升序。每种 cursor 包含所需排序锚点。

## 2. 用例输入

CaseWrite 固定字段：
- name:string，1–120 字；description:string，0–4000 字；tags:string[]，最多 20 项、每项 40 字；enabled:boolean。
- suite_id:UUID；base_revision:int|null，新建 null，编辑必须等于当前 revision。
- turns:[{content:string}]，1–20 项，每项非空且最多 32000 字。
- fixtures:[{path:string,content:string,encoding:"utf-8"}]，最多 50 个，内容合计不超过 2 MiB；只支持文本。
- memory_seed:[{kind:"semantic",content:string}]，最多 20 项、合计 64000 字；由 Worker 经项目 memory API 写入专属记忆库，不直接写任意数据库。
- clock_iso:string|null，用于可控工具的固定时钟，不承诺冻结系统时钟。
- tool_fixtures:[{tool_name:string,arguments_match:object,result:JSONValue,is_error:boolean}]，最多 50 项、合计 1 MiB；只可映射内置受控工具 test_lookup / test_unstable，命中按顺序消费；未命中返回固定受控错误。
- execution:{tool_mode:"fixture"|"live",allowed_tools:string[],max_iterations:int,timeout_s:int}。
- assertions:Assertion[]，1–50 项；rubric:Rubric|null；artifact_paths:string[]，最多 50 项、每项相对 workspace。
- script:[{text:string,json_text:string|null,tool_calls:[{name:string,arguments:object}]}]，最多 20 步；**仅 tool_mode="fixture" 时被受控 provider 消费**，live 模式忽略该字段。用途是让判分链路能离线端到端跑通（spec §验证：离线用受控 provider，与真实 provider 冒烟分开表述）。脚本用尽后回放最后一步；脚本为空时照常按配置构造真实 provider，不静默返回空回答。

限制：max_iterations 默认 12、范围 1–50；timeout_s 默认 120、范围 5–900。run 的覆盖限制只能收紧用例限制。默认工具白名单 read_file/write_file/edit_file/list_dir/test_lookup/test_unstable；具体名称映射到 ToolRegistry 已注册名，未知名称拒绝。live 模式可额外选 web_search/web_fetch 的现有只读实现，并保留项目网络保护。绝不把自定义工具名作为动态 import。

Assertion 为按 kind 区分的对象，每项有 id:string、required:boolean：
- answer_contains / answer_not_contains：text:string，case_sensitive:boolean。
- answer_json / file_json：schema:SchemaSubset，checks:[{pointer:string,equals:JSONValue}]；file_json 额外有 path:string。
- file_exists：path:string。
- file_contains：path:string、text:string。
- tool_called：name:string、arguments_subset:object、min_calls:int、max_calls:int|null。
- tool_forbidden：names:string[]。
- iteration_limit / tool_call_limit：maximum:int。
- SchemaSubset 支持 type、properties、required、items、enum、additionalProperties、minItems、maxItems；type 仅 object/array/string/number/integer/boolean/null。不支持 $ref、远程引用、正则和代码。整数与布尔值严格区分；JSON Pointer 按 RFC 6901 的 ~0/~1 转义，缺失路径是断言失败。

Rubric={version:string,required:boolean,dimensions:[{id:string,name:string,anchors:[string,string,string,string,string],pass_score:int}]}；维度 1–6 个，pass_score 为 0–4，默认 3。五个锚点分别对应 0–4 分，禁止省略锚点。模型结果 evidence_refs 必须引用实际证据，不能只依据评分文本决定任务成功。

CaseRead=CaseWrite 去掉 base_revision，增加 id、revision、created_at、updated_at、is_deleted。修改产生不可变 revision；删除为软删除，不使历史快照失效。

SuiteWrite={name:string,description:string,tags:string[],base_revision:int|null}；SuiteRead 增加 id、revision、case_count、created_at、updated_at、is_deleted；停用用例通过 CaseWrite.enabled=false 实现。

## 3. 资源 API

| 方法 | 相对路径 | 请求与响应 |
| --- | --- | --- |
| GET | /health | 200 {status:"ok"}；免登录，无路径和配置 |
| POST | /auth/login | {passphrase:string} → 200 {csrf_token:string} + HttpOnly/SameSite=Strict cookie |
| GET | /auth/session | 200 {authenticated:true,csrf_token:string}，未登录 401 |
| POST | /auth/logout | CSRF 校验后 204，清除 cookie |
| GET/POST | /suites | 列表 / SuiteWrite → 201 SuiteRead |
| GET/PATCH/DELETE | /suites/{id} | SuiteRead / SuiteWrite → 200；DELETE 要求 ?base_revision=n，软删且历史保留 |
| GET | /cases | 可筛选 suite_id、tag、enabled |
| POST | /cases | CaseWrite → 201 CaseRead |
| GET/PATCH/DELETE | /cases/{id} | 同上；PATCH 提交完整可编辑快照与 base_revision，避免数组局部合并歧义 |
| POST | /cases/{id}/copy | {name:string,suite_id:UUID} → 201 新 CaseRead |
| POST | /suites/import | {schema_version:1,suite:SuiteWrite,cases:CaseWrite[]} → 201 {suite:SuiteRead,case_count:int}；单事务、全部合法才提交；文件最多 5 MiB、最多 200 用例，导入忽略旧 id 并重建关联 |
| GET | /suites/{id}/export | 相同 schema_version 的 JSON，包含当前启用及停用用例，不含密钥 |
| GET | /profiles | {data:[ProfileRead],next_cursor:null}；只返回服务端可选预设 |
| POST | /samples/import | {sample_id:"basic-v1"} → 201 {suite:SuiteRead,case_count:20}；已导入返回原对象 200，不产生副本 |
| GET | /calibration-samples | {gold_revision:int,data:GoldSample[]}；服务端内置 10 条待确认的建议标签 |
| PATCH | /calibration-samples/{id} | GoldReview → 200 GoldSample；确认或修订标签，增加 gold_revision，使旧校准失效 |
| POST | /calibrations | CalibrationCreate + Idempotency-Key → 202 CalibrationRead；未确认足够 gold 样本返回 422 CALIBRATION_GOLD_UNCONFIRMED |
| GET | /calibrations/{id} | 200 CalibrationRead，约 1 秒轮询 |
| POST | /calibrations/{id}/cancel | {} → 202 CalibrationRead，按运行取消与进程回收规则处理 |
| GET | /overview | 最近 10 个 RunSummary、最新非删除运行的 Metrics、状态计数 |
| POST | /runs | RunCreate + Idempotency-Key → 202 RunRead，Location 指向运行 |
| GET | /runs | 可筛选 status、suite_id、parent_run_id |
| GET | /runs/{id} | 200 RunRead，包含进度、指标、脱敏快照 |
| DELETE | /runs/{id} | 仅终态可软删，204 |
| GET | /runs/{id}/attempts | attempt 摘要列表，可筛选 verdict、execution_status、grading_status |
| POST | /runs/{id}/cancel | {} → 202 RunRead；已 cancelling/cancelled 原样返回；其他终态 409 |
| POST | /runs/{id}/rerun | {attempt_ids:UUID[]} + Idempotency-Key → 202 新 RunRead；只能选择同运行终态尝试，输入引用原冻结快照 |
| GET | /attempts/{id} | 200 AttemptRead |
| GET | /attempts/{id}/trace | after_seq 默认 0、limit 1–200 → {data:TraceEvent[],next_after_seq:int,has_more:boolean,sealed:boolean} |
| POST | /attempts/{id}/reviews | {verdict:"passed"|"failed",comment:string} → 201 ManualReview；记录服务端当前本地用户 |
| GET | /artifacts/{id} | 受控 Content-Type、Content-Disposition:attachment、X-Content-Type-Options:nosniff；不解析任意 path |
| GET | /runs/{id}/export | format=json/csv；脱敏摘要，CSV 文本防公式注入 |

登录每来源最多每分钟 10 次尝试；服务启动口令仅显示到本地终端，不进入应用日志。会话有效期 8 小时，进程重启全部失效。localhost HTTP 下不设 Secure，未来 TLS 部署再按实际连接设置。请求体上限 5 MiB，普通变更限制 2 MiB。

ProfileRead={id,name,provider,model,available,unavailable_reason}；字段均为 string，最后一项 string|null；available 为 boolean。不返回 api_base 查询参数、密钥、配置路径、代理凭据。Profile 不允许浏览器直接编辑。

### 3.1 校准样本与校准运行

GoldSample={id:string,task:string,rubric:Rubric,evidence:object,expected_verdict:"passed"|"failed",expected_scores:[{dimension_id:string,score:int}],label_status:"proposed"|"confirmed",reviewer:string|null,comment:string,updated_at:string}。内置标签默认 proposed，不能把 AI 生成标签标成人工已确认。

GoldReview={base_gold_revision:int,expected_verdict:"passed"|"failed",expected_scores:[{dimension_id:string,score:int}],comment:string,confirm:true}；保存与确认由网站当前用户显式操作，reviewer 为服务端本地用户身份，不能由客户端指定。版本过期 409 REVISION_CONFLICT。证据不可在此接口修改。

CalibrationCreate={judge_profile_id:string,gold_revision:int,sample_ids:string[]}；至少 10 个唯一、已 confirmed 的样本，正反例各至少 4 个；首版内置 10 个。服务快照 gold、rubric、judge_fingerprint，走受管理队列，不执行被测 Agent。CalibrationRead={id,status:run_status,judge_fingerprint,gold_revision,total:int,completed:int,matched:int,agreement:number|null,passed:boolean|null,created_at,finished_at,error:string|null}。agreement=matched/total；完成且 >=0.8 才 passed=true，失败/取消/未完成为 null。结果按量表分别绑定指纹，未验证的新量表不能复用旧校准。

内置 10 个 gold 样本共用 general-v1 量表，覆盖任务完成与正确性两个维度，B11/B16 采用同一量表。量表正文与版本纳入指纹；自定义的新量表没有匹配 gold 时显示未校准，不能借用 general-v1 的结果。

NewRun 页面的“校准”面板提供 10 条证据及标签复核，再启动校准、显示进度与取消。未确认标签或未获得有效校准时允许继续执行用例，但开放任务只显示原始模型分数并进入 review_required。离线测试使用明确标记的 gold fixture，不能将其作为人工已确认的产品数据导入。

## 4. 运行、状态与指标

RunCreate={suite_id:UUID,case_ids:UUID[],agent_profile_id:string,judge_profile_id:string|null,limits:{concurrency:int,max_iterations:int,timeout_s:int}}。
case_ids 非空且不可重复，最多 200；全部属于 suite；concurrency 默认 1、范围 1–3。若所选用例 rubric.required=true，则 judge_profile_id 必填。服务冻结 suite/case revision、模型与量表快照，服务端并发总上限 3。

Idempotency-Key：客户端每次用户发起操作生成一次，网络重试复用；服务以 session-independent 本地账户标识 + endpoint + key 唯一约束，与 run 创建同一事务，保存 canonical request hash。相同 payload 已完成返回原 202 对象；未完成入队事务返回 409 IDEMPOTENCY_IN_PROGRESS；不同 payload 返回 422 IDEMPOTENCY_MISMATCH。记录与运行保留同样长，软删运行不释放 key。

execution_status=queued|running|succeeded|failed|timed_out|cancelled|interrupted|setup_error。
grading_status=pending|running|completed|judge_error|not_applicable。
verdict=passed|failed|review_required|not_evaluated。
run_status=queued|running|cancelling|completed|cancelled|interrupted。

执行 failed/timed_out 表示已开始执行且失败，自动 verdict=failed。setup_error/cancelled/interrupted 为 not_evaluated。必过规则失败→failed；必需 Judge 有效、已校准且任一维度未达标→failed；必需 Judge 缺失/失败/未校准或证据不完整→review_required；其他必过条件全部满足→passed。可选 Judge 失败不改变有效规则结论，但显示 judge_error。非必过规则用于展示，不参与 passed；每个用例至少有一个必过规则或必需 rubric。

RunSummary={id,suite_id,suite_name,status,created_at,started_at,finished_at,parent_run_id,planned_count,terminal_count}；三个时间除 created_at 可 null，parent_run_id 可 null。RunRead 增加 snapshot、counts、metrics、attempts_url；snapshot 包含冻结 case_revision 引用与 profile_id/provider/model/generation 参数、rules_version、grader_version、tool_mode，不含秘密。

AttemptRead={id,run_id,position,case_id,case_revision,case_name,execution_status,grading_status,verdict,started_at,finished_at,final_answer,stop_reason,errors,assertions,judge,usage,timing,trace_complete,artifacts,reviews}。
- errors:[{phase:"setup"|"execution"|"trace"|"grading"|"cleanup",code,message}]，按发生顺序追加，不能用后发生的错误替换原错误。
- assertions:[{id,passed:boolean|null,required:boolean,message,evidence_refs:string[]}]；无法读取证据为 null。
- judge:null|{status,model,version,dimensions:[{id,score:int,reason,evidence_refs:string[]}],calibrated:boolean,error:string|null}。
- artifacts:[{id,name,media_type,size_bytes,sha256}]；reviews 为追加记录，含 id、verdict、comment、reviewer、created_at。
- timing={queue_ms,agent_ms,grading_ms,total_ms}，均 number|null；total 从入队到完成，统计所用单调时钟时段，UTC 时间仅用于显示。
- usage={agent:UsageAggregate,judge:UsageAggregate}。
- UsageAggregate={input_tokens:int|null,output_tokens:int|null,total_tokens:int|null,source:"reported"|"estimated"|"mixed"|"unavailable",coverage:"complete"|"partial"|"unavailable",physical_requests:int,iterations:int,missing_usage_calls:int}。实际 provider call 为主账本；round_usages 仅用于交叉检查，禁止重复相加；未知量不补零；辅助调用缺失令 coverage=partial。

Metrics={planned:int,passed:int,failed:int,review_required:int,not_evaluated:int,overall_pass_rate:number|null,evaluated_pass_rate:number|null,evaluation_coverage:number|null,tool_case_pass_rate:number|null,tool_eligible_cases:int,tool_valid_cases:int,quality:[{dimension_id,mean:number|null,valid_count:int,eligible_count:int}],agent_duration:{median_ms:number|null,p95_ms:number|null,count:int},usage:{agent:UsageAggregate,judge:UsageAggregate}}。
overall_pass_rate=passed/planned；evaluated_pass_rate=passed/(passed+failed)；evaluation_coverage=(passed+failed)/planned；零分母为 null。tool_case_pass_rate 分母只含该用例全部 required 工具断言已获得有效判分的用例；同时展示 tool_valid_cases/tool_eligible_cases。P95 采用 nearest-rank 排序第 ceil(0.95*n) 项；中位数偶数取中间两项平均。不合并不同工具模式/模型的历史报告为同一基线。

## 5. Trace 与 Worker IPC

TraceEvent={schema_version:1,attempt_id:UUID,seq:int,event_id:UUID,type:string,turn:int|null,iteration:int|null,call_id:string|null,ts:string,elapsed_ms:number,payload:object}。
seq 从 1 严格递增；重发同 event_id 不重复写入。evidence_refs 格式为 trace:<attempt_id>:<seq> 或 artifact:<uuid>；校验必须属于当前尝试。

type 闭集：attempt.started、turn.started、iteration.started、model.request_summary、model.call_completed、tool.started、tool.completed、tool.failed、iteration.completed、turn.completed、attempt.completed、attempt.failed、trace.incomplete。
- tool.* payload 包含 name、arguments、result、is_error、duration_ms；开始时未知项为 null。
- model.request_summary 记录 SDK/Hook 实际可见的 messages 摘要与模型参数，不把逻辑摘要冒充全部物理请求原文。
- model.call_completed 映射项目 LLMCallRecord，追加 role=agent|judge 与 scope=main|auxiliary|unknown；不保存 api_key 等配置。
- 事件正文最多 16 KiB，超出时写受控文本 artifact 并附 truncated=true、artifact_id；单 attempt Trace 上限 20 MiB，超过后停止执行，保留 trace.incomplete。
- 分段 JSONL 最大每段 1 MiB；活动段按完整行读取；完成时 flush、fsync、原子生成 manifest。截断行忽略但必须 trace_complete=false，不能判通过。
- hook 采集器显式 reraise=true；provider usage observer 现为 fail-open，因此另设错误标志在结束时检查，不能假设抛出会中止执行。

Worker 通过 python -m nanobot.evaluation.worker 启动，参数只含 attempt_id；stdin/stdout 为 UTF-8 NDJSON 私有管道，日志只进 stderr 并脱敏，行上限 3 MiB。
- 父→子 Agent：一次 {type:"boot",role:"agent",protocol_version:1,attempt_id,case_snapshot,workspace,runtime_root,profile_bundle}。profile_bundle 只在内存与管道存在，不写原始 boot。stdin 随即关闭；取消走父进程拥有的进程树。
- 子→父：{type:"ready",protocol_version:1,attempt_id}；{type:"trace",event:TraceEvent}；{type:"result",attempt_id,execution_status,final_answer,stop_reason,usage,timing,errors,trace_complete}。
- 父进程只接受当前 attempt_id/版本/状态合法的事件；成功终态必须同时满足 result 已接收、进程退出成功、Trace 及 artifact manifest 已密封。无 result 的退出视为 interrupted 或 setup_error，不能默认为 succeeded。
- boot/profile_bundle 只由服务端构造，浏览器没有入口直接提交；Worker 清理 SDK、临时 credential 对象和 provider 连接。
- Judge 使用单独受调度器管理的评分子进程，沿用同一 IPC 与进程树管理，boot.role="judge"，输入为已校验的评分材料与量表；agent 进程执行期间的用量只计 agent，Judge 及其重试只计 judge。这样评分超时与取消同样能被强制回收。
- Judge boot={type:"boot",role:"judge",protocol_version:1,attempt_id,rubric,evidence,profile_bundle}，并沿用与 Agent boot 相同的 runtime_root/workspace 绝对路径字段（评分子进程同样需要受控scratch目录做进程回收与产物落盘）；除这两个路径字段外不包含 workspace 工具配置；共享 ready/trace/result 外层格式。Judge 的 result 有 execution_status、judge:JudgeResult|null、usage、timing、errors、trace_complete，final_answer/stop_reason 固定为 null；数据库评分终态由服务合并到原 attempt，不能覆盖 Agent execution_status。校准任务使用独立 UUID 作为 attempt_id，并写入校准任务索引。

## 6. Python 模块边界

这些为新建接口，不假装仓库已有：
- EvaluationRepository：create_suite / save_case / create_run / claim_attempt / finish_attempt / get_run / append_review；事务归 repository，数据库连接归 database。
- EvaluationRuntimeFactory.build(boot)：建立 worker 局部配置上下文、tools 白名单、SDK 和 usage observers。禁止在 HTTP 主进程调用 loader.set_config_path。
- TraceCollector.append(event) / seal()：持久化路径只由服务生成；同步 observer 只将记录交入受监控队列。
- run_worker(boot)：执行有序 turns、SDK context manager 清理并产生 WorkerResult。
- evaluate_rules(case, evidence)：返回 AssertionResult 列表；不访问外网或执行用例代码。
- evaluate_judge(rubric, evidence, provider)：无工具调用，验证完整维度和 evidence_refs；最多两次物理评分请求。
- derive_verdict(execution_status, required_rules, judge_state, trace_complete)：只负责确定性合并，单元测试覆盖全部组合。
- aggregate_run(attempts, planned_count)：按第 4 节聚合，不依赖 UI。

### 6.1 AttemptEvidence（WU-03 产出 → WU-04 消费）

跨 WU 接缝，先于实现锁定。`nanobot/evaluation/evidence.py` 由 **WU-03 拥有**，WU-04 只按本节字段消费、不得改该文件。它只在服务进程内传递，不进 IPC、不进数据库。

- `AttemptEvidence`（frozen dataclass）字段：`attempt_id:str`、`case_snapshot:dict`（仓储冻结的 CaseRead 形状，判分前已过一次 `CaseWrite.model_validate`）、`final_answer:str|null`、`stop_reason:str|null`、`iterations:int`、`trace_events:tuple[TraceEvent,...]`（按 seq 严格升序且全部属于当前 attempt）、`tool_calls:tuple[ToolCallEvidence,...]`、`artifacts:tuple[EvidenceArtifact,...]`、`workspace:Path`（该 attempt 专属工作区，已 resolve）、`trace_complete:bool`、`execution_status:ExecutionStatus`。
- `ToolCallEvidence`：`name:str`、`arguments:JsonObject`、`result:JSONValue|null`、`is_error:bool`、`seq:int`、`turn:int|null`、`iteration:int|null`。只从 `tool.started/tool.completed/tool.failed` 事件派生；`is_error` 取项目 `ToolResult.is_error`，不得用文本前缀猜测。
- `EvidenceArtifact`：`relative_path:str`（相对 workspace，已通过 `resolve_inside`）、`media_type:str`、`size_bytes:int`、`sha256:str`、`artifact_id:UUID|null`；未登记的产物 `artifact_id=None` 且只能判失败，不能读取内容。
- 读取入口只有三个：`read_text(relative_path) -> str`、`read_json(relative_path) -> JSONValue`、`exists(relative_path) -> bool`。三者都必须先把路径解析到本 attempt workspace 内并在越界时抛 `PATH_OUTSIDE_ROOT`；不接受绝对路径、符号链接外指或 `artifact_id=None` 的条目。判分模块不得自行 `open()`。
- `evidence_refs` 只允许 `trace:<attempt_id>:<seq>` 与 `artifact:<artifact_id>` 两种前缀，且 `attempt_id`/`artifact_id` 必须属于当前对象；`resolve_ref(ref)` 命中返回证据、未命中返回 `None`。规则与 Judge 都把 `None` 当作「证据不完整」，不得当作通过。
- 判分可见范围即本对象持有的证据：Trace 截断（`trace_complete=False`）时，规则可继续判必过项，但 `derive_verdict` 必须给 `review_required`；未采集到的字段保持 `None`，不补零、不猜测。

## Next

随 2026-10-15-agent-evaluation-plan.md 一起审阅；批准计划后实施，接口变化先更新本契约并通知各 WU。
