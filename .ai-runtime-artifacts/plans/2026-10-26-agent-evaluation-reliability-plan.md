---
artifact: implementation-plan
route: superpowers:writing-plans
skills: [writing-plans, source-driven-development, api-and-interface-design]
skills_evidence:
  - .claude/skills/writing-plans/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/api-and-interface-design/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-10-26-agent-evaluation-reliability-dispatch.md
source:
  - 用户要求充分调研项目和有关评估做法，然后写计划
  - .ai-runtime-artifacts/specs/2026-10-15-agent-evaluation-spec.md
  - .ai-runtime-artifacts/research/2026-10-26-agent-evaluation-project-audit.md
  - .ai-runtime-artifacts/research/2026-10-26-agent-evaluation-practices-research.md
  - .ai-runtime-artifacts/contracts/2026-10-26-contract-evaluation-reliability.md
  - .ai-runtime-artifacts/verifications/2026-10-26-evaluation-verification-lite.md
created_at: 2026-10-26
status: approved
approved: true
---

# 知序 Agent 自动化评估可靠性实施计划

> **For agentic workers:** 实施时按 Harness `orchestration` 与本计划同 stem dispatch 逐任务执行；先写失败测试，再实现，通过后审查。writing-plans通用执行入口由项目Harness绑定替代。本轮只交计划，不实施。

**Goal:** 让评估系统能依据可追溯证据判断 Agent、Skill、团队的任务完成、约束遵循、决策、效率与安全，并用固定数据集的重复试验和配对比较验证迭代效果。

**Architecture:** 复用现有 Python评估服务、SQLite、SDK Worker、规则/Judge/校准和React页面。三类目标统一进入隔离Worker，完整冻结运行输入与目标，采集到统一Trace/物理请求账本；结果评分、过程评分和技术异常分开，报告与比较复用同一读模型。保留gateway内嵌入口，补独立启动和CI入口，不引入外部评估平台。

**Tech Stack:** Python>=3.11（本机3.13.3）、aiohttp 3.14.5、Pydantic 2.14.1、SQLite/asyncio；React18.3.1、TS5.9.3、Vite5.4.21、Vitest2.1.9。详见同日stack文件；本机命令使用npm，无bun。

## 0. 本计划的判断与交付边界

当前基础流程可运行，但不能作为完整Agent质量验收。依据是：团队无Trace却可passed、团队只传末轮并共享运行时、live可能回放脚本、Judge缺原始任务、Skill身份不完整，以及5/5关键词通过遗漏指令和过程问题。

本计划是修复测量可信度和补齐评估闭环，不以换框架或再做一套仪表盘替代后端工作。
已有case版本、幂等、规则、Judge、gold、校准、review、usage、HTML均复用，不重建。
原10-15独立入口需求与10-23内嵌实现并存：保留当前设置页，新增独立命令和同源页面；两者使用同一服务模块和单库独占锁。

首期范围：本地单用户，固定fixture/受控文件工具，真实或脚本模型明确区分；36条分层任务、独立重复、人工校准、双运行比较、CI回归。
不纳入：线上流量A/B、跨用户权限、云部署、自动修改Agent、自主学习、SWE-bench/WebArena整套环境接入、任意shell/MCP、估算人民币金额、多Judge默认投票。不能宣称覆盖所有真实外部集成。

## 1. 研究依据与采用方式

| 官方实践 | 本项目落实 |
| --- | --- |
| Anthropic：任务结果、轨迹、trial分开；能力与回归分开 | 结果/过程维度、case trial、能力集与回归集标签 |
| LangSmith：final/trajectory/single-step、版本、重复和实验比较 | 固定manifest、逐例差异、保留每次结果 |
| Google ADK：exact/in_order/any_order轨迹匹配，效率独立 | 声明式轨迹规则，不要求所有任务走唯一工具路径 |
| Inspect：测量故障独立、Judge材料隔离、聚类统计 | 缺证待复核、受控Judge、按case配对区间 |
| OpenAI：任务特定判据、人工校准、工具选择与参数分开 | 强规则+有依据语义评分，不拿关键词命中当任务完成 |

全部直接链接和适用边界见两份research。外部框架不作为新运行依赖；截图里的通用准确率、成本倍数不作验收标准。

## 2. 最终用户应能完成的流程

1. 选择一个有版本的测试集、Agent/Skill/团队、模型、工具模式和重复次数，看清计划attempt数与限制后启动。
2. 运行时查看任务/试验进度；取消后确认成员和Worker都退出，已发生消耗仍被记录。
3. 查看每例任务完成、硬约束、过程、效率、安全分别怎样，失败定位到真实证据；缺证显示待复核。
4. 对开放任务选择量表和已校准Judge；确认人工样本、查看一致性/假阳性，复核分歧并保留自动结论。
5. 同一测试快照比较baseline/candidate，逐例定位改进与回归，同时比较成本和延迟，不仅比较一个总分。
6. 用CLI在本地/CI跑相同回归，导出JSON、HTML并返回有意义退出码。

## 3. 文件结构与责任

所有下列业务改动均是**待实施**，本轮不创建这些源码文件。

| 文件/模块 | 责任 |
| --- | --- |
| `nanobot/evaluation/schemas.py`、`database.py`、`repository.py` | v2类型、兼容migration、trial唯一键与比较记录 |
| 新 `nanobot/evaluation/manifests.py` | 冻结摘要、有效配置和比较兼容性 |
| `runtime_factory.py`、`worker.py`、`ipc.py`、`trace.py`、`usage.py` | 隔离执行、模式、生命周期、物理调用与覆盖 |
| 新 `nanobot/evaluation/team_runtime.py` | 在attempt内装配现有团队service/turn/scheduler，禁止借共享gateway host |
| `nanobot/teams/turn.py`、`execution.py`、`scheduler.py`；`nanobot/agent/subagent.py` | 仅增加scope observer/有效白名单/父子身份传递所需扩展；保留正常团队行为 |
| 新 `nanobot/evaluation/assessment.py` | 里程碑与五维度、合格策略、原因码 |
| `graders/rules.py`、`judge.py`、`verdict.py`、`calibration.py` | 强规则、有界材料、不可评分、校准与旧API兼容 |
| 新 `nanobot/evaluation/judge_materials.py` | 原始任务、声明产物正文、裁剪与引用索引 |
| `reports.py`、`observability.py`、`report_view.py` | 统一v2统计、Trace图、同源报告 |
| 新 `nanobot/evaluation/comparisons.py` | 配对、差值、统计区间与不可比原因 |
| `services.py`、`server.py` | 编排以上能力，逐步移除团队特殊判分旁路 |
| `webui/src/lib/evaluation-api.ts`、`components/settings/evaluation/` | 已有视图升级，新维度/校准/复核/比较组件 |
| 新 `nanobot/cli/eval_commands.py`、现有`commands.py`；新`webui/eval.html`、`src/evaluation-entry.tsx`、`vite.config.ts` | 独立启动、同源评估页面、无交互CI入口 |
| `nanobot/evaluation/samples/`、`tests/evaluation/`、`tests/webui/`、`webui/src/tests/` | 产品样例、评分器反例、端到端/迁移/界面验证 |

共享类型由T02集中建立；后续WU新增字段先回Leader更新契约，不能各自发明结构。服务层、repository等共享文件按dispatch串行所有权。

## 4. 指标与判定原则

完整定义以增量契约为准：

- task_outcome区分completed/partial/failed/unknown；execution_status不再被当任务结果。
- 页面同时显示任务成功率、部分完成率、综合合格率与评估覆盖率；旧passed仍保留原义，旧记录标legacy。
- 过程、安全、效率独立；安全硬违规和必过规则失败不能被Judge高分抵消；缺必需证据进入待复核。
- 读文件失败不自动等于冗余；工具执行成功不自动等于选择正确；被拒绝的越权尝试不等于越权已执行。
- Token覆盖主调用、辅助、成员与Judge，重试新物理请求独立计；报表去重，不把父execution总量与子项再次相加。
- 迭代数、模型物理请求数、墙钟耗时分开；均值/中位数/P95都有有效样本数，未知不填0。
- 一个case重复r次不是r个独立任务。比较按case配对，重复结果先在case内聚合；样本不足不宣称显著提升。

## 5. 任务与实施步骤

下面的测试代码是要新增的契约级断言；fixture需在对应测试模块显式构造，不能依赖用户配置或私有workspace。每个任务分红测→最小实现→针对性验证→审查后小范围提交；提交由Leader执行，不能自动push。

### T01：建立可复现的软件基线，修复已有测量故障

**Files:** 修改`tests/evaluation/test_calibration_jobs.py`、`test_services.py`、`test_scheduler.py`、`nanobot/evaluation/services.py`、`scheduler.py`、`repository.py`、`runtime_factory.py`；必要路径修复定位`nanobot/session/manager.py`及其测试。

- [ ] 记录当前811通过/8失败的精确原因；先单跑5个校准失败，检查测试profile是否显式注册，不让mock judge依赖不存在的judge-1。
- [ ] 用短/长两种Windows路径测试同一checkpoint原子写，检查字节/字符、目录是否存在；证明是路径还是竞态后再修，不修改系统长路径策略。
- [ ] 写并运行调度器反例：run A已经满额，检查run B时不得再领取A的attempt。保留服务全局worker与run两级上限。

```python
async def test_claim_does_not_bypass_full_run(scheduler_case):
    await scheduler_case.start_two_runs(per_run_limit=1)
    await scheduler_case.dispatch_until_blocked()
    assert scheduler_case.peak_active_for_each_run() == [1, 1]
```

- [ ] 最小修改profile装配、claim的eligible_run_ids过滤与短session根方案；已有错误归因与结果状态不得吞掉。
- [ ] 验证：`.venv/Scripts/python.exe -m pytest tests/evaluation/test_calibration_jobs.py tests/evaluation/test_services.py tests/evaluation/test_scheduler.py -q --basetemp=.ai-runtime-artifacts/verifications/eval-base`。期望全绿；Windows长路径用例另外在保留的长路径fixture执行。失败不得标“基线通过”。

### T02：确定v2类型、运行manifest和兼容migration

**Files:** 修改`schemas.py`、`database.py`、`repository.py`、`services.py`；新增`manifests.py`、`tests/evaluation/test_manifests.py`；修改`test_database.py`、`test_repository.py`、`test_validation.py`。

- [ ] 为契约的model_mode/repetitions/evaluation/coverage/trial/比较对象写类型和边界红测，包括bool不能当int、总attempts>200拒绝。
- [ ] 建立完整目标冻结入口：Agent/Skill保留已有文件快照，团队冻结revision+Leader+成员档案/提示词/所需技能；禁止源路径穿越、链接越界、任意二进制文本化。

```python
def test_manifest_changes_when_skill_changes(manifest_case):
    a = manifest_case.freeze(skill_text="版本一")
    b = manifest_case.freeze(skill_text="版本二")
    assert a["target_digest"] != b["target_digest"]
    assert a["environment_digest"] == b["environment_digest"]
```

- [ ] 追加v2 migration，旧attempt trial_index=0，比较表和唯一键；事务回滚/重复启动/旧JSON兼容测试。旧v1 DDL和历史verdict不改。
- [ ] 实现manifest摘要白名单，去掉凭据/本地绝对路径；实际模型、generation、代码脏摘要必须与展示一致，无法获取明确为null。
- [ ] 验证：`.venv/Scripts/python.exe -m pytest tests/evaluation/test_manifests.py tests/evaluation/test_database.py tests/evaluation/test_repository.py tests/evaluation/test_validation.py -q`。期望旧库迁移/新库/回放全通过。

### T03：补齐执行生命周期与统一证据采集

**Files:** 修改`worker.py`、`trace.py`、`ipc.py`、`usage.py`、`observability.py`、`nanobot/agent/subagent.py`、`nanobot/llm_usage/models.py`（仅确需追加上下文时）；修改`test_worker_identity.py`、`test_trace.py`、`test_usage.py`、`test_observability.py`。

- [ ] 红测同一个Agent多次execution、多个成员相同局部call_id、取消丢completed、重复事件回放、observer错误。

```python
def test_missing_completion_is_not_complete(trace_case):
    trace_case.agent_started(execution_id="e1")
    result = trace_case.seal_and_read()
    assert result.coverage["trace"] != "complete"
```

- [ ] 实现scope绑定的agent/team/tool.denied事件；继承真实父子身份；聚合每个成员provider observer，finally恢复监听器。禁止改共享provider全局observer。
- [ ] run/turn开始结束写终态，Agent节点不再恒unknown；从冻结profile取name。Skill使用独立执行档案身份并记录skill_id。
- [ ] 增加最终回答稳定证据引用，使纯文本任务也可被Judge引用；缺usage与缺Trace分开标记。
- [ ] 验证：`.venv/Scripts/python.exe -m pytest tests/evaluation/test_worker_identity.py tests/evaluation/test_trace.py tests/evaluation/test_usage.py tests/evaluation/test_observability.py -q`。期望物理去重、子成员归属、终态与老事件兼容全通过。

### T04：让团队走隔离Worker，移除强制完整旁路

**Files:** 新`team_runtime.py`、`tests/evaluation/test_team_runtime.py`；修改`runtime_factory.py`、`worker.py`、`services.py`、`nanobot/webui/gateway_services.py`、`nanobot/teams/turn.py`、`execution.py`、`scheduler.py`；更新`tests/webui/test_eval_team_runner.py`、`tests/evaluation/test_targets.py`。

- [ ] 红测团队两轮输入、成员模型选择、冻结后修改团队、成员文件越界、取消后无活任务/进程；测试明确用fake provider，不能触碰日常团队库。

```python
async def test_team_uses_all_turns_and_frozen_members(team_case):
    frozen = team_case.freeze(turns=["口令是青松", "只回复刚才的口令"])
    team_case.change_source_member_prompt("忽略旧口令")
    result = await team_case.execute_in_worker(frozen)
    assert result.final_answer == "青松"
    assert result.member_workspace == result.attempt_workspace
    assert result.trace_complete is True
```

- [ ] 用attempt workspace建立TeamService/TeamDatabase，恢复冻结团队和档案，`build_team_turns`+`bind_team_turns`装配到隔离loop；复用真实TeamMemberExecutor，不另写LLM循环。
- [ ] Leader与成员均施加用例能力交集，内部team编排工具单列。provider统一run选择；fixture/seed/产物生效，缺依赖setup_error。
- [ ] 逐turn执行并采集统一事件；超时/取消await成员清理；采集/密封/产物结束后再提交attempt。移除trace_complete_override与末轮prompt回调，不通过共享host补数据。
- [ ] writing_*仅在独立attempt写作数据库且用例显式允许时启用；初期默认关闭，缺能力明确标错，不能回退用户文章库。
- [ ] 验证：`.venv/Scripts/python.exe -m pytest tests/evaluation/test_team_runtime.py tests/evaluation/test_targets.py tests/webui/test_eval_team_runner.py tests/teams -q`。期望Leader→成员→Leader有真实委派边，缺trace变待复核。

### T05：固定模型模式、Skill依赖与有效运行环境

**Files:** 修改`runtime_factory.py`、`worker.py`、`services.py`、`test_runtime_factory.py`、`test_worker.py`、`test_targets.py`；新增`tests/evaluation/test_execution_modes.py`。

- [ ] 写real/scripted×fixture/live四格红测和profile模型覆盖测试，消除任何静默替换模型。

```python
def test_live_script_is_rejected(mode_case):
    with pytest.raises(ValueError, match="MODEL_MODE_CONFLICT"):
        mode_case.validate(model_mode="real", tool_mode="live", script=[{"text":"假结果"}])
```

- [ ] case环境显式提供brief、素材、身份默认模板、可选文章/记忆种子；缺依赖入队或setup时明示。Skill内部读取哪些缺文件仍保留证据，不用空文件假装任务成功。
- [ ] 仅物化冻结且可用的Skill；载入清单、hash、是否执行read_file、有效白名单进capability。限制Skill目标只加载指定技能及明确声明依赖。
- [ ] 核对现有live工具支持集与FORBIDDEN_TOOLS冲突，入队即拒绝不支持的exec/exec_session/apply_patch；不为测试Skill而开放shell。
- [ ] 将case/run取更严值后的iteration/timeout实际下发到Worker配置，团队各成员也应用能力和限额交集；测试不能只看快照字段，必须确认达到限制后真实停止。
- [ ] 验证：`.venv/Scripts/python.exe -m pytest tests/evaluation/test_execution_modes.py tests/evaluation/test_runtime_factory.py tests/evaluation/test_worker.py tests/evaluation/test_targets.py -q`。期望报告能证明真实模型/回放与工具模式各是什么。

### T06：增加任务里程碑、强规则与五维度合格策略

**Files:** 新`assessment.py`、`tests/evaluation/test_assessment.py`；修改`graders/rules.py`、`graders/verdict.py`、`services.py`；修改`test_rules.py`、`test_verdict.py`。

- [ ] 写当前假阳性反例：322字团队回复违反40字限制，关键词齐但没有最终JSON/真实产物/委派不能通过。

```python
def test_keyword_cannot_override_length_failure(assessment_case):
    result = assessment_case.grade(answer="金黄" + "说明" * 160, max_chars=40)
    assert result.dimensions["constraints"].status == "failed"
    assert result.qualification == "disqualified"
```

- [ ] 实现契约中的exact、length、唯一JSON块、轨迹匹配、成员执行、usage/duration预算；工具步骤一对一匹配，不错罚合法替代路径。
- [ ] 里程碑权重解析真实assertion结果，生成completed/partial/failed/unknown；未知不按0分。新任务没有充分任务判据标smoke，不输出“综合质量通过”。
- [ ] 五维度与旧verdict映射，保留规则False优先和Judge不得翻案；基础设施失败任务结果unknown，缺必要证据review_required。
- [ ] 安全规则区分尝试被拒、实际违规；注入内容只是数据；正当恢复错误不自动判冗余。
- [ ] 验证：`.venv/Scripts/python.exe -m pytest tests/evaluation/test_assessment.py tests/evaluation/test_rules.py tests/evaluation/test_verdict.py -q`。期望人为构造的成功/部分/失败/缺证四组结果精确一致。

### T07：恢复可用Judge、人工校准与材料证据闭环

**Files:** 新`judge_materials.py`、`tests/evaluation/test_judge_materials.py`；修改`graders/judge.py`、`calibration.py`、`verdict.py`、`worker.py`、`services.py`；更新`test_judge.py`、`test_calibration.py`、`test_calibration_jobs.py`。

- [ ] 红测原任务turns必须在材料中、产物声明正文可读、无工具、缺材料不可评分、伪引用拒收、分隔符注入不改变结构、截断不丢引用索引。

```python
def test_judge_material_keeps_task_before_large_trace(material_case):
    material = material_case.build(turns=["只返回一句话"], tool_result="x" * 100000)
    assert material.turns == [{"content":"只返回一句话"}]
    assert material.truncated is True
    assert material.required_evidence_missing == ["tool_result_body"]
```

- [ ] 实现64KiB材料预算/8KiB产物正文/选择记录和脱敏；scored与not_assessable分开，不能要求缺证给低分。
- [ ] 确认实际Judge generation传入请求并参与指纹；prompt/material策略改版失效旧校准。沿用最多一次格式修复，不默认增加三次Judge调用。
- [ ] 修通人工proposed→confirmed、校准作业、原始自动结果+append-only人工复核；≥10且正反各≥4、agreement≥0.8是现有最低门槛，补混淆矩阵、假阳性和覆盖。建议逐步扩充到30条真实人工标注，不能由AI代确认。
- [ ] 验证：`.venv/Scripts/python.exe -m pytest tests/evaluation/test_judge_materials.py tests/evaluation/test_judge.py tests/evaluation/test_calibration.py tests/evaluation/test_calibration_jobs.py -q`。期望所有故障变清晰状态；真实Judge验收在T14、人工标签确认后进行。

### T08：让指标、页面与HTML使用一致的统计口径

**Files:** 修改`reports.py`、`observability.py`、`report_view.py`、`services.py`；更新`test_reports.py`、`test_observability.py`、`test_report_view.py`、`test_services.py`。

- [ ] 用固定6条样本构造1完成合格、1完成预算违规、1部分、1任务失败、1采集故障、1取消，测试所有分母和null。

```python
def test_task_success_is_not_qualification_rate(metrics_case):
    metrics = metrics_case.aggregate_six_cases()
    assert metrics.task_success_rate == 2 / 6
    assert metrics.qualification_rate == 1 / 6
    assert metrics.partial_completion_rate == 1 / 6
    assert metrics.planned == 6
```

- [ ] 加迭代/物理请求/端到端时延统计、失败代价、覆盖数；团队并行时间用墙钟，Token使用唯一请求账本。
- [ ] 保留工具用例通过率原名；补执行错误率、明确标注判据覆盖的调用准确率；不生成无法复算的冗余率/安全率。
- [ ] HTML与API/UI同一snapshot，显示五维度、原始判据、证据链接、缺失和范围；旧记录legacy提示，不倒改历史评分。
- [ ] 验证：`.venv/Scripts/python.exe -m pytest tests/evaluation/test_reports.py tests/evaluation/test_observability.py tests/evaluation/test_report_view.py tests/evaluation/test_services.py -q`。期望JSON/HTML数字逐项同和。

### T09：支持独立重复试验与可靠性报告

**Files:** 修改`repository.py`、`scheduler.py`、`services.py`、`reports.py`；新增`tests/evaluation/test_repetitions.py`；更新`test_repository.py`、`test_scheduler.py`。

- [ ] 红测2case×3重复=6个attempt、trial隔离、取消保留、幂等重放不翻倍、重跑保留源快照。

```python
def test_repeated_attempts_are_distinct(repetition_case):
    run = repetition_case.create(cases=2, repetitions=3)
    rows = repetition_case.attempts(run)
    assert len(rows) == 6
    assert len({row["id"] for row in rows}) == 6
    assert sorted(row["trial_index"] for row in rows) == [0,0,1,1,2,2]
```

- [ ] 每次独立workspace/session/runtime/provider，线程/进程限制不因重复扩大；按case/trial统计，不与rerun/模型请求重试混淆。
- [ ] 报告逐case分布、波动、r次至少一次/全部成功经验比例与覆盖；全部可判case不足时不填虚假稳定性。
- [ ] 不后台自动增加真实重复次数；默认1，上限10且总attempts<=200；UI提前展示planned。
- [ ] 验证：`.venv/Scripts/python.exe -m pytest tests/evaluation/test_repetitions.py tests/evaluation/test_scheduler.py tests/evaluation/test_repository.py -q`。期望所有唯一键/恢复/取消行为一致。

### T10：固定版本的配对比较与不确定性

**Files:** 新`comparisons.py`、`tests/evaluation/test_comparisons.py`、`test_comparison_routes.py`；修改`server.py`、`services.py`、`repository.py`、`report_view.py`。

- [ ] 红测同case不同revision、fixture/规则/模型模式不一致拒比较；candidate配置变化允许；未知manifest拒“严格比较”。

```python
def test_comparison_pairs_case_id_and_revision(comparison_case):
    result = comparison_case.compare_same_manifest()
    assert result.improved_case_ids == ["case-b"]
    assert result.regressed_case_ids == ["case-a"]
    assert result.intervals is None  # 此fixture只有2个任务
```

- [ ] 实现按case聚合trial后配对，展示新通过/新失败/不变/不可比与完整原始值；不简单拿两份均值相减后宣称有效。
- [ ] 有效配对case>=20时启用固定seed42/2000次case级bootstrap；小于20仅描述；策略版本进入比较快照。禁止把重复trial当独立任务扩大样本。
- [ ] 比较成功/合格/效率/安全/覆盖的差异，配对缺失列原因；不自动生成单个“综合赢家”。新增鉴权、CSRF、幂等、分页与导出测试。
- [ ] 验证：`.venv/Scripts/python.exe -m pytest tests/evaluation/test_comparisons.py tests/evaluation/test_comparison_routes.py -q`。期望固定数据重算结果稳定、非法比较不产生记录。

### T11：升级真实评估工作流界面

**Files:** 修改`webui/src/lib/evaluation-api.ts`和评估目录现有组件；新`EvaluationScorecard.tsx`、`EvaluationCalibration.tsx`、`EvaluationReviews.tsx`、`EvaluationComparison.tsx`、`CaseEvaluationEditor.tsx`；修改locale及`src/tests/evaluation-*.test.tsx`。

- [ ] 红测“综合合格率”不冒充任务成功率、smoke/legacy显式标记、缺Trace和缺usage各自提示。

```tsx
it("keeps missing evidence visible", async () => {
  render(<EvaluationScorecard evaluation={missingTraceFixture} />);
  expect(screen.getByText("待复核")).toBeVisible();
  expect(screen.queryByText("综合质量通过")).toBeNull();
});
```

- [ ] 数据集页面提供用例判据编辑，展示任务/环境/里程碑/硬约束/量表，保存形成revision；保留Excel导入导出且往返不丢v2字段（复杂字段以版本化JSON列承载，明确校验）。
- [ ] 发起运行分开选择真实模型/回放、工具模式、重复次数和预算；显式说明团队模型策略；不能把dataset当第四种执行器（dataset是输入集合）。
- [ ] 详情展示任务结果+五维度+证据，工具错误/疑似重复可筛选；校准页确认gold并运行作业；复核页保留原自动结论；比较页逐case跳到双方证据。
- [ ] i18n全部现有语言同形；不向普通用户暴露内部Harness/WU/凭据；满足键盘/窄屏，保留现有聊天与设置导航。
- [ ] 验证（cwd=webui）：`npm run test -- --no-cache src/tests/evaluation-api.test.ts src/tests/evaluation-settings.test.tsx src/tests/evaluation-observability.test.tsx src/tests/evaluation-reliability.test.tsx`、`npx tsc -p tsconfig.build.json --noEmit`、`npm run build`。再做真实浏览器路径验收。

### T12：建立36条任务与评分器反例集

**Files:** 新`nanobot/evaluation/samples/reliability-v2.json`、`rubrics-v2.json`、`grader-counterexamples-v2.json`、`tests/evaluation/test_reliability_samples.py`；修改`datasets.py`、`test_datasets.py`、`test_samples.py`。

- [ ] 固定36条，三目标各12条：4正常、4边界、2恢复、2安全；回归/能力/保留集使用tags，不把真实网络样本混入fixture基线。
- [ ] Agent：CSV→JSON状态检查、真实读写、工具参数、关键顺序、多轮修正、缺输入、空数据、失败恢复、只读保护、注入诱导越界。
- [ ] Skill：指定技能/依赖、结构化产物、素材来源、缺brief/空素材、多轮约束、禁止正文写入、错误工具结果、注入资料、预算边界。
- [ ] Team：真实成员委派/汇总、双轮上下文、只返回一句话、冻结版本、多成员并行、错误memberId/成员失败/超时取消、共享数据隔离、禁止越权工具。不得仅用最终回答含成员名证明委派。
- [ ] 每例写清reference或产物期望、工具/成员判据、失败定义、可接受替代路径、证据需要；fixture变异同时给错误输出，验证grader确实能判错。

```python
def test_samples_cover_targets_and_known_false_positives(sample_catalog):
    assert sample_catalog.counts_by_target() == {"agent":12,"skill":12,"team":12}
    assert sample_catalog.grade_counterexample("team-verbose-keyword").qualification == "disqualified"
    assert sample_catalog.grade_counterexample("claims-artifact-without-file").qualification != "qualified"
```

- [ ] gold建议仍proposed；构造测试用confirmed只能在测试库，不能写入用户真实库。至少10条人工确认后方可真实校准；开发/保留样本分开，不用调过Prompt的集充当泛化证明。
- [ ] 验证：`.venv/Scripts/python.exe -m pytest tests/evaluation/test_reliability_samples.py tests/evaluation/test_samples.py tests/evaluation/test_datasets.py -q`。期望36条定义全合法，全部已知反例被检出，不能用“样本全部通过”替代能力测量。

### T13：补独立启动、无交互回归和CI

**Files:** 新`nanobot/cli/eval_commands.py`、`tests/cli/test_eval_commands.py`、`webui/eval.html`、`webui/src/evaluation-entry.tsx`、`.github/workflows/evaluation.yml`；修改`cli/commands.py`、`evaluation/server.py`、`webui/vite.config.ts`、打包资源配置。

- [ ] 红测无gateway时eval serve可提供API/页面、目标发现与团队执行；同库第二实例拒绝；退出await清理；默认bind回环地址。

```python
def test_cli_separates_failure_and_measurement_error(cli_case):
    assert cli_case.run_controlled(outcome="qualified").exit_code == 0
    assert cli_case.run_controlled(outcome="disqualified").exit_code == 1
    assert cli_case.run_controlled(outcome="review_required").exit_code == 2
```

- [ ] 独立入口复用Eval API/目标冻结与profile构建、同源独立评估entry；内嵌页保持可用。通过结构化配置发现端口，移除前端硬编码8790的单一假设。
- [ ] 登录复用现有cookie/CSRF语义；独立口令用受控本机存储/输入，不复制上轮临时302桥为产品机制；普通链接登录错误要有可操作说明。
- [ ] 实现契约中的eval run参数、退出码、固定导出；CI只跑scripted fixture，无私密凭据和付费模型；真实回归通过显式本地触发，不PR自动调用。
- [ ] README只补产品命令/验证边界，过程报告仍写.ai-runtime-artifacts；构建产物含eval entry并验证wheel实际资源。
- [ ] 验证：`.venv/Scripts/python.exe -m pytest tests/cli/test_eval_commands.py tests/evaluation/test_server.py -q`；运行独立serve与controlled CLI一条成功/失败/缺证；验证服务停止后无Worker。

### T14：集成验收、真实评估和独立审查

**Files:** 只写`.ai-runtime-artifacts/verifications/2026-10-26-agent-evaluation-reliability-collective-test.md`、`reviews/*-code-review.md`、`reviews/*-security-review.md`、`execution-logs/*-execution-log.md`；缺陷修复另有受控WU。

- [ ] 先跑全评估、相关teams/CLI/WebUI，所有失败逐项修复或提出明确阻塞；不得把8个旧失败标通过。
- [ ] 用36条受控任务验证执行与判分，再用grader反例验证“错误能被抓住”。这验证框架，不证明真实模型能力。
- [ ] 人工确认gold后运行真实Judge校准；未确认时交付必须写“校准待人工确认”，不能AI代签。如果校准不达标，界面/CI保持review_required。
- [ ] 真实模型先每目标2条×1次=6attempt，其中包含已知团队一句话和Skill材料缺口；用户预算支持后再36×3=108attempt/variant。双版本完整比较为216attempt加Judge，须提前显示预算；不默认无限重跑。
- [ ] 抽核团队：原始输入全量、冻结profile、Leader和成员Trace、工具/物理请求、usage同和、attempt之外写入为零、取消全部退出。
- [ ] 核对UI、API、HTML、JSON同一快照数字一致；做登录/刷新/重启/独立入口、旧库迁移、完整/缺证/失败状态和窄屏截图。
- [ ] 发起独立code reviewer与security auditor（只在实施阶段），检查冻结/隔离/注入/副作用/资源回收/统计分母；根据结果修复，更新执行日志才交付。

## 6. 批次与验收关口

| 批次 | 任务 | 批次结束必须证明 |
| --- | --- | --- |
| A 基线与契约 | T01→T02 | 当前失败可解释并修复，v1迁移和v2边界可靠 |
| B 可信执行 | T03→T04→T05 | 三目标隔离、完整turns/冻结目标/真实模式，缺Trace不再passed |
| C 有效判分 | T06→T07 | 已知假阳性被抓住，Judge材料完整且可弃权，校准与复核闭环 |
| D 可比较指标 | T08→T09→T10 | 分母可复算、重复独立、严格配对比较和缺失说明 |
| E 用户与自动化 | T11 / T12 / T13 | 页面可操作、36条样本、独立命令和无付费CI；文件互斥后可并行 |
| F 完整验收 | T14 | 真实小样本、gold校准、软件回归、审查证据齐全 |

不把批次B或C单独称作完整评估系统。每批提交可独立回滚，后续类型读字段必须容忍旧记录。

## 7. 最终验收清单

- [ ] 当前团队322字/一句话、无产物自报完成、无委派自报成员、缺Trace等反例全部按预期判失败或待复核。
- [ ] 正常三目标均有可追溯完整输入/目标/执行身份/委派/工具/usage；缺失字段精确降级，不补0、不伪造。
- [ ] 模型与工具模式不可混淆，团队有效模型与用户选择相同，所有执行限额实际生效。
- [ ] 冻结后改源目标、用例或Skill不改变已入队/重跑的内容；数据/环境/评分不同不能严格比较。
- [ ] 同case重复不共享状态；并发、超时、取消、重启与Windows长路径测试通过。
- [ ] 任务完成、部分完成、综合合格、效率、决策、安全都有清晰定义、覆盖数与证据；旧记录不冒充新评分。
- [ ] Judge材料含原始任务，分数有依据，校准失败/缺证/未确认gold不会放行；人工结果不覆盖原自动结果。
- [ ] 36条分层任务和反例、重复与baseline/candidate比较、独立启动/CLI/CI可运行。
- [ ] 所有本轮相关自动化测试通过，软件正确性与模型能力测试分别报告；不承诺真实模型36条全过。
- [ ] 无生产workspace/文章/团队库污染，无真实MCP/原生shell越权，导出无凭据/秘密/未脱敏全文。

## 8. 验证命令与证据目录

后端（repo根）：

```powershell
.venv/Scripts/python.exe -m pytest tests/evaluation tests/teams tests/webui/test_eval_team_runner.py tests/cli/test_eval_commands.py tests/cli/test_gateway_runtime.py -q --basetemp=.ai-runtime-artifacts/verifications/eval-final
.venv/Scripts/python.exe -m ruff check nanobot/evaluation nanobot/teams nanobot/cli/eval_commands.py nanobot/cli/gateway_runtime.py
.venv/Scripts/python.exe -m basedpyright
```

前端（webui目录）：

```powershell
npm run test -- --no-cache
npx tsc -p tsconfig.build.json --noEmit
npm run lint
npm run build
```

类型/lint若有全仓既有失败，先给同HEAD可复现差异与本次触及文件结论，不隐瞒、不宣称全绿。Vite缓存若受旧文件权限影响，使用过程目录中的专用缓存，不删除未知归属目录。
每批验证产物写`.ai-runtime-artifacts/verifications/`；截图/JSON/HTML标记fixture或真实模型、run_id、sample_count、版本与缺口。

## 9. 风险与停止条件

| 风险 | 控制与停止条件 |
| --- | --- |
| 团队装配依赖共享服务 | 单独team_runtime适配现有栈；未隔离前不开真实团队安全测评、不回退共享host |
| 采集改变运行语义 | observer作用域/finally恢复；正常团队和subagent回归；不得用评估专用假循环替代 |
| gold人工环节未完成 | 保持proposed和待复核；交付明确未验收，不自动确认 |
| 样本规模与费用 | 先6条真实冒烟，完整36×3需显式展示总量；Judge预算分列 |
| 测量标准越改越宽 | 冻结grader反例与规则版本，旧失败不能通过删断言变绿 |
| 统计过度解读 | case聚类、配对、缺证范围；小样本只描述，不承诺显著性或泛化 |
| 老数据迁移 | additive migration、历史只读、legacy标识、备份与回滚演练 |

## 10. 计划自检

| 用户/原始需求 | 对应任务 |
| --- | --- |
| 完整Agent自动化执行和独立访问 | T03–T05、T13 |
| 任务完成、部分完成、开放任务 | T06、T07 |
| 迭代/Token/延迟/工具质量/安全 | T03、T06、T08 |
| Trace、产物和失败可追溯 | T03、T04、T07、T11 |
| 稳定性、多次运行、版本对比 | T02、T09、T10 |
| 真实代表性用例/边界/恢复/对抗 | T12、T14 |
| 校准、人工复核与持续回归 | T07、T11、T13 |

接口以同日contract为唯一增量真相源；本计划中的新模块均在T02/T03等先建立再使用。研究与计划只写过程文件，未启动实施、未提交。

## Next

- 计划确认 → 在本会话说「开始实现」或「执行」。
- 需要调整 → 直接给修改意见。
- 想并行 → 先审同stem dispatch，再说「并行执行」；隔离、判分等前置批次不可跳过。
