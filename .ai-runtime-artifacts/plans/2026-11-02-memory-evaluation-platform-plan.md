---
artifact: implementation-plan
route: writing-plans + api-and-interface-design
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - .claude/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/api-and-interface-design/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-11-02-memory-evaluation-platform-dispatch.md
contract: .ai-runtime-artifacts/contracts/2026-11-02-contract-memory-evaluation.md
measurement_review: .ai-runtime-artifacts/reviews/2026-11-02-memory-evaluation-measurement-document-review.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - harness-kit/project.profile.md
  - harness-kit/context-map.md
  - .ai-runtime-artifacts/specs/2026-11-02-memory-evaluation-platform-spec.md
  - .ai-runtime-artifacts/research/2026-11-02-memory-evaluation-research.md
  - .ai-runtime-artifacts/stack/2026-11-02-memory-evaluation-stack.md
created_at: 2026-11-02
status: approved
approved: true
design_approval: 用户在本会话说“开始实现”，批准第一阶段按本计划与 dispatch 执行。
---

# 知序记忆评测平台第一阶段 Implementation Plan

> **For agentic workers:** 按Harness使用orchestration逐WU执行，先加载本计划、增量契约与dispatch；用户确认“开始实现”后才创建隔离worktree并委派。writing-plans通用的执行入口由本项目Harness替代。步骤使用复选框跟踪；本轮只编写计划，不启动worker。

**Goal:** 在现有Agent评估页面中完成版本化记忆场景导入、真实跨会话评测、阶段证据、标准指标和历史版本配对比较。

**Architecture:** 保留现有suite/case/run/attempt、Worker、Trace、Judge、校准与review；增加memory专项模块和可等待的产品记忆协调接口。以同一调度器执行分批实验，gold留在评估服务，Worker只接收执行载荷；新增页面复用现有评估入口和认证。

**Tech Stack:** Python≥3.11/asyncio/Pydantic/SQLite/aiohttp、React18/TypeScript/Vite、pytest/Vitest；本机精确版本见同日stack产物。首版不强制引入MemEval/DeepEval/Langfuse新依赖，外部基准先做数据/协议适配。

---

## 0. 当前证据、范围与执行纪律

已核对HEAD ce595d6e14857cea18903d4b2eae688c804c1320，分支feature/desktop-app。计划编写时已跟踪文件无既有差异，原有未跟踪产物全部保留；实施前必须重查。以下是静态证据，不是本轮软件回归结果：

1. worker.py循环case.turns并共用session；fixtures.py::seed_memory直接写文本。专项必须走独立事件流及真实抽取，不能复用它们冒充全链路。
2. runtime_factory.py::create_attempt调用AgentLoop.from_config但未显式传入memory_extraction_enabled、memory_services与retrieval_engine；AgentLoop构造的记忆抽取默认关闭。专项装配必须显式验证所有生效开关，不能假设与gateway一致。
3. MemoryExtractionHook常态抽取走idle任务；on_finally明确不是会话结束。SessionEndOrchestrator有未接通适配，不能直接当完成信号。
4. 多处datetime.now/time.monotonic影响检索、过期和派生文件；现有ControlledClock只控制fixture工具输出。
5. evaluation/database.py声明SCHEMA_VERSION="2"，_MIGRATIONS含"3"。本轮新增存储前先做重复初始化/旧库升级回归并统一到4。
6. 已有RunCreate.repetitions、manifest和comparison，可复用；单run仍限制200 attempts，旧case最多20 turns。

首版完整覆盖已批准spec第一阶段。经验记忆、任意外部后端、自动切换Git checkout、多租户授权改造、物理备份擦除归第二阶段；本轮评测会识别这些能力缺失。若测出产品删除/隔离本身有缺陷，保留失败证据并形成独立修复任务，禁止测试adapter替产品“修到通过”。

每个WU实施时依次写有意义的失败用例、确认预期失败、实现、跑目标测试。代码块提供合同关键实现/可执行测试，其他行为由任务的明确输入、分支和验收矩阵限定；不得按示意数据硬编码返回值。先完成可运行的模型与小闭环，再扩展数据、比较和UI。

开发命令默认仓库根：`uv run --no-sync python -m pytest <测试路径> -q`；单文件时补`::test_name`验证RED。WebUI命令默认webui目录。所有测试临时目录、日志和报告指定在`.ai-runtime-artifacts/verifications/memory-evaluation/`之下。首次依赖同步用项目约定uv，不让外部benchmark锁文件覆盖当前环境。

每个WU开始时在其子进程环境设置`PYTEST_ADDOPTS`，例如WU-01：`--basetemp=.ai-runtime-artifacts/verifications/memory-evaluation/WU-01/tmp -o cache_dir=.ai-runtime-artifacts/verifications/memory-evaluation/WU-01/cache`。各WU使用自身ID以免并行争抢临时目录；退出恢复原环境。文中pytest命令均继承此设置，覆盖报告也定向到同一WU目录；不在项目根创建过程脚本或日志。

### 0.1 M0：环境与已有基线准备（任何编码前执行）

- 核对Python/Bun、SQLite/Pydantic/aiohttp/React等实际版本与stack记录；执行`uv sync --all-extras --dev`前先确认本机现有锁文件与CPU向量依赖约定，不用外部benchmark依赖覆盖知序环境。
- 离线就绪验证：`uv run --no-sync python -c "import sqlite3,pydantic,aiohttp; print(sqlite3.sqlite_version,pydantic.__version__,aiohttp.__version__)"`，预期全部可导入；webui目录`bun --version`可返回版本。测试数据根和评估数据库备份路径位于隔离根并可读写；验证仅创建自己命名的临时对象，不清理用户目录。
- 本轮不新增必需环境变量，不创建含真实密钥的.env；模型从现有配置的profile解析，API只传profile_id。离线受控模式不需要外部服务。真实评测另检查reader/extractor/Judge profile、embedding可用性及明确预算，缺失时状态real_evaluation_not_ready，不阻塞离线软件开发也不冒称真实验收完成。
- 在worktree隔离测试根记录现有`tests/evaluation/test_database.py`、`test_worker.py`和`test_reports.py`结果及前端既有评估测试；用于识别新增回归，不将现有失败视为永久豁免。Windows重点验证紧凑路径、文件锁与取消后的进程回收。
- Gate：依赖可导入、隔离路径/备份可用、现有检查结果已记录，才能开始WU-01。真实模型未就绪单列，不伪装配置成功。本项属于执行准备，不新增业务WU。

### 0.2 测量规则基线（本次审查补全）

输入/输出、事实等价、标注完整性、主指标聚合与实际采用判定，以contract§8和measurement_review为准。主指标为`qa_accuracy_scenario_macro`，答对且有必要记忆证据另报`grounded_memory_success`；禁止临时拼综合分。minimum_effect_pp等策略不预设成行业阈值，缺配置时只出诊断。

## 1. 文件结构与接口所有权

新增`nanobot/evaluation/memory/`，每个模块只承担一种职责：

| 文件 | 责任 | 初始所有者 |
| --- | --- | --- |
| __init__.py、models.py、protocol.py、repository.py | 严格模型、adapter签名、专项持久化 | WU-01 |
| clock.py、signals.py（位于nanobot/memory/） | 产品可选时钟、完成状态及结构化observer | WU-02 |
| zhixu_adapter.py、checkpoints.py | 真实装配、隔离、回放、标准种子、备份恢复 | WU-03 |
| runner.py、worker_bridge.py | 场景状态机、Worker输入/输出与主评估流水线 | WU-04 |
| metrics.py、grading.py、judge_materials.py | 指标、事实匹配、逐probe评分与通用指标补充 | WU-05 |
| datasets.py、longmemeval.py；samples/memory-*.jsonl | 集合版本、流式导入与公开基准适配、内置题库 | WU-06 |
| experiments.py、manifests.py | 候选冻结、预算、分批、恢复、取消 | WU-07 |
| comparisons.py、report.py | 聚类配对、门禁、同快照HTML | WU-08 |
| routes.py；webui/src/lib/memory-evaluation-api.ts | HTTP增量与TS客户端 | WU-09 |

前端新增`webui/src/components/settings/evaluation/memory/`，WU-10负责MemoryDatasets、MemoryScenarioEditor、MemoryExperimentForm、MemoryMetricValue；WU-11负责MemoryEvaluation、MemoryExperimentDetail、MemoryComparison、MemoryReplay及现有入口/翻译接线。WU-12只新增集成验收和文档，不并行修改业务接口。

现有schemas.py、database.py、services.py、worker.py、runtime_factory.py、server.py和evaluation-api.ts的修改按任务阶段串行；不得多个worker同时写。models.py/protocol.py在WU-01冻结后是跨WU接口基线，改变字段需先修契约，由Leader安排顺序。

## 2. 里程碑与依赖

| 里程碑 | 完成内容 | 达成时可观察结果 |
| --- | --- | --- |
| M0 环境与已有基线 | §0.1执行准备 | 依赖可导入、隔离根与备份可用、原有检查结果已记录 |
| M1 测量基础 | WU-01、02、05 | 可验证schema、时钟/完成屏障、指标数学与缺证状态 |
| M2 可跑闭环 | WU-03、04、06 | 内置20场景可通过服务层运行三条轨道，并回看阶段证据 |
| M3 版本实验 | WU-07、08、09 | 120×3分批与历史baseline配对，API返回完整报告 |
| M4 页面与交付 | WU-10、11、12 | 页面可导入、运行、比较、回放、复核；离线回归与真实质量证据分开 |

详细GROUP顺序与唯一写者见dispatch。WU-02/05/06在模型契约完成后可并行；前端先完成WU-10，再由WU-11接线，避免未完成的组件影响独立验收。计划阶段不实际委派。

## 3. Task 1 / WU-01：专项模型、版本映射与迁移

**Files:** 新增`nanobot/evaluation/memory/{__init__,models,protocol,repository}.py`、`tests/evaluation/memory_fixtures.py`、`tests/evaluation/test_memory_schemas.py`、`tests/evaluation/test_memory_repository.py`；修改`nanobot/evaluation/{schemas,database,repository}.py`、`tests/evaluation/test_database.py`。

- [ ] **Step 1 — RED：** 给迁移写“空库初始化两次”“原version2但comparisons表已存在”“正常version3升级”“更高版本拒绝”用例；升级前后旧run/result_json逐字一致、外键完整、并发同key不重复写。先运行`tests/evaluation/test_database.py`，记录版本不一致的实际表现；不预设已有测试都会失败。
- [ ] **Step 2 — RED：** 按契约建立MemoryScenario/ExecutionInput/Candidate/Probe/MetricResult/Experiment/Comparison模型，测试duplicate ID、悬空证据、非法split、bool伪装int、time zone缺失、gold进入Worker、case ref混合script/seed；新增模型尚不存在时应明确导入失败。补annotation_scope/extraction_facts、匹配/聚合/区间算法版本、minimum_effect_pp与校准策略字段；旧记录默认targeted，不能自动当完整抽取标注。

共享fixture只返回原始数据，放在独立memory_fixtures.py，禁止改旧conftest的公共语义：

```python
def make_memory_scenario() -> dict:
    return {
        "schema_version": "memory-scenario/v1", "scenario_id": "fact-001",
        "revision": 1, "split": "regression", "template_family": "fact-city-a",
        "cluster_id": "conversation-a", "tags": ["fact", "cross_session"],
        "scope": {"subject_id": "synthetic-u1", "workspace_id": "synthetic-w1"},
        "events": [
            {"type": "session", "id": "s1", "at": "2026-11-01T09:00:00+08:00",
             "messages": [{"id": "m1", "role": "user", "content": "请记住，我的虚构测试城市叫青岚城。"},
                          {"id": "m2", "role": "assistant", "content": "收到这条信息。"},
                          {"id": "m3", "role": "user", "content": "之后问居住地时请参考。"}]},
            {"type": "settle", "after_session": "s1"}
        ],
        "probes": [{"id": "q1", "question": "我住在哪个城市？", "fresh_session": True,
                    "at": "2026-11-02T09:00:00+08:00"}],
        "gold": {"q1": {"required_facts": [{"key": "city", "value": "青岚城",
                     "evidence_message_ids": ["m1"]}], "answerability": "answerable"}}
    }
```

- [ ] **Step 3 — 实现：** 以契约§1/7为唯一结构，普通case旧校验不变，专项ref可用空turns/assertions且必须解析到可评分场景。追加migration4和6张专项表、reviews.memory_ref_json可空列；SCHEMA_VERSION改4；原迁移正文保持。ManualReview/Create增加可选memory_ref，append_review原调用兼容；整体验收有效verdict排除单probe review。MemoryRepository提供`import_revision`、`get_scenario`、`get_execution_input`、`get_gold`、`create_experiment_plan`、`get_experiment`、`save_comparison`，均通过EvaluationDatabase事务，不嵌套connect。
- [ ] **Step 4 — GREEN：** 运行`pytest tests/evaluation/test_memory_schemas.py tests/evaluation/test_memory_repository.py tests/evaluation/test_database.py tests/evaluation/test_validation.py -q`（使用本计划统一uv前缀）。期望全部通过，migration4重复执行无副作用；任何原有失败另存实际日志，不能凭旧文档豁免。
- [ ] **Step 5 — 检查点：** Leader只检查本WU文件与迁移测试；提交仅按后续获准的Git流程执行，示例主题`feat(evaluation): 增加记忆评测模型与存储`，不自动push。

## 4. Task 2 / WU-02：产品链路的完成信号、时钟和证据

**Files:** 新增`nanobot/memory/clock.py`、`nanobot/memory/signals.py`；修改`nanobot/agent/hooks/memory_extraction.py`、`nanobot/memory/{extractor,orchestrator,lifecycle}.py`、`nanobot/memory/retrieval/{engine,reranker,store_adapter}.py`、`nanobot/memory/retrieval/channels/{episodes,attachments}.py`；新增`tests/memory/test_evaluation_clock.py`、`tests/memory/test_completion_signals.py`。

- [ ] **Step 1 — RED：** 针对吞异常、部分failed_tracks、短会话skip、索引未可见、idle与关闭并发、幂等窗口写测试；订阅者必须收到真实completed/failed/skipped，不能从空结果推断成功。测策略时钟前进一天但真实超时不改变，未覆盖时钟路径报告缺失。
- [ ] **Step 2 — 实现clock：** MemoryClock提供`wall_now()`、`monotonic()`、`real_deadline()`及仅冻结模式可用的`advance(seconds)`；构造支持`frozen_at`与`monotonic_at`，生产缺省用真实时间。日期跳跃只改变wall/策略monotonic；执行超时始终用asyncio真实deadline。替换文件清单内TTL、新鲜度、默认时间戳、派生刷新和幂等相关时间读取。

```python
from datetime import datetime, timedelta, timezone
from nanobot.memory.clock import MemoryClock

def test_virtual_day_keeps_real_deadline():
    clock = MemoryClock(frozen_at=datetime(2026, 11, 1, tzinfo=timezone.utc), monotonic_at=0)
    deadline = clock.real_deadline()
    clock.advance(86400)
    assert clock.wall_now() == datetime(2026, 11, 2, tzinfo=timezone.utc)
    assert clock.monotonic() == 86400
    assert 0 <= clock.real_deadline() - deadline < 5
```

- [ ] **Step 3 — 实现signals：** 提供`MemoryOperationRegistry.start/finish/wait/snapshot`和可选event sink；key包含runtime namespace/session/op_id，支持多轨完成、错误、取消与index_ready。公共`flush_session_memory`协调入口复用idle提取的同一提取逻辑，等待pending任务或触发真实会话结束，不借删除会话触发，不重复抽取；observer不携带gold。
- [ ] **Step 4 — GREEN：** `pytest tests/memory/test_evaluation_clock.py tests/memory/test_completion_signals.py tests/memory/test_orchestrator.py tests/memory/test_lifecycle.py tests/memory/test_memory_extraction_hook.py -q`，期望缺省生产行为兼容、冻结场景确定、observer异常不破坏主流程但让评测证据标partial。类级singleton/pending任务按namespace隔离，close后无残留。
- [ ] **Step 5 — 检查点：** Leader核对所有修改仅增加可选注入，不更改抽取质量算法；主题`feat(memory): 增加可等待处理状态与测试时钟`。

## 5. Task 3 / WU-03：知序adapter、状态快照与新会话

**Files:** 新增`nanobot/evaluation/memory/zhixu_adapter.py`、`nanobot/evaluation/memory/checkpoints.py`、`tests/evaluation/test_memory_adapter.py`、`tests/evaluation/test_memory_checkpoints.py`；局部适配测试fixture只写各自测试文件。

- [ ] **Step 1 — RED：** 写真实SQLite/WAL备份恢复、两个probe状态隔离、画像/working-memory旁路、scope共享正例、无记忆模式全持久读写关闭、重启重建对象、gold文件不可读测试。向量后端缺失时应capability_missing，不回退为假向量成绩。
- [ ] **Step 2 — 实现checkpoints：** `MemoryCheckpointStore.capture/restore`在完成屏障后用SQLite backup保存，不直接copy活动DB。原子保存manifest：状态文件hash、scope映射、origin_refs、store版本、后台任务终态；恢复在独立probe子目录，读sqlite和向量索引版本一致后开放查询。

SQLite备份叶函数采用完整、可单测的标准库行为：

```python
import sqlite3
from pathlib import Path

def backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
```

- [ ] **Step 3 — 实现adapter：** 实现契约MemoryAdapter全部签名。构造明确的MemoryServices/Extractor/RetrievalEngine/画像和工作记忆路径，记录实际能力。ingest固定transcript；settle调用WU-02公共入口；snapshot/restore/restart不保留隐藏Python缓存；recall按标准state导入且验证可检索；answer用新session真实Agent。subject/session映射跟产品相同规则，不靠每次随机session伪造跨用户授权。
- [ ] **Step 4 — GREEN：** `pytest tests/evaluation/test_memory_adapter.py tests/evaluation/test_memory_checkpoints.py tests/memory/test_workspace_scope.py tests/memory/test_repository_scope_filter.py -q`。明确forget(product_request/admin_delete)与rebuild是否真支持；不支持返回不可测，支持就走真实产品操作并验证旁路/再派生，不写测试专用清库逻辑。
- [ ] **Step 5 — 检查点：** 所有路径解析限制在trial评估根；快照中的原始历史不能成为query额外输入。主题`feat(evaluation): 接入知序记忆隔离适配器`。

## 6. Task 4 / WU-04：专项Worker与三轨执行闭环

**Files:** 新增`nanobot/evaluation/memory/runner.py`、`worker_bridge.py`、`tests/evaluation/test_memory_runner.py`、`test_memory_worker.py`；修改`nanobot/evaluation/{schemas,worker,runtime_factory,services,ipc}.py`、`nanobot/agent/{loop,context}.py`。

- [ ] **Step 1 — RED：** 在受控provider的真实子进程上测试“写历史→settle→新会话query”，断言工具上下文没有gold、session中没有旧对话，注入事件匹配真实system上下文。分测extraction/retrieval/end_to_end，断言memory_seed未被调用。普通Agent旧case走原路径。
- [ ] **Step 2 — 实现输入分离：** services在父进程解析immutable ref并读取execution/gold两份；AgentWorkerBoot仅增加memory_execution，父评分端保存gold。专项启动前在runtime_factory显式传`memory_extraction_enabled`、MemoryServices、retrieval_engine及WU-02信号；失败就setup失败，不降级普通聊天。

父进程构造Worker载荷的核心数据选择固定为：

```python
def execution_fields(scenario: dict) -> dict:
    keys = ("schema_version", "scenario_id", "revision", "scope", "events", "probes", "reference_state")
    return {key: scenario[key] for key in keys if key in scenario}

def test_execution_payload_excludes_gold():
    from tests.evaluation.memory_fixtures import make_memory_scenario
    payload = execution_fields(make_memory_scenario())
    assert "gold" not in payload
    assert "events" in payload and "probes" in payload
```

- [ ] **Step 3 — 实现状态机：** `run_memory_scenario`顺序处理契约全部event；每个checkpoint sealed后才允许probe clone。extraction只导出state评分；retrieval使用reference_state并禁用历史抽取；end_to_end走真实查询。统一memory.event进入已有seq/IPC/Trace，正文超限进入受控artifact。每个probe分别输出coverage/usage/answer，绝不只保留最后回答；finally关闭probe/base runtime和任务。
- [ ] **Step 4 — GREEN：** `pytest tests/evaluation/test_memory_runner.py tests/evaluation/test_memory_worker.py tests/evaluation/test_worker.py tests/evaluation/test_ipc.py tests/evaluation/test_trace.py tests/evaluation/test_runtime_factory.py -q`。包含模型服务错、settle timeout、取消、observer损坏、skip策略应导致能力失败而非剔除、Windows紧凑路径；失败账目保留。
- [ ] **Step 5 — 检查点：** 从boot→实际产品调用→probe→评分全链审计gold与生效开关；主题`feat(evaluation): 增加跨会话记忆专项执行`。

## 7. Task 5 / WU-05：指标、评分、校准与通用Agent口径

**Files:** 新增`nanobot/evaluation/memory/{metrics,grading,judge_materials}.py`、`tests/evaluation/test_memory_metrics.py`、`test_memory_grading.py`；修改`nanobot/evaluation/{assessment,reports}.py`、`nanobot/evaluation/graders/{judge,calibration}.py`。

- [ ] **Step 1 — RED：** 用手算数据验证TP/FP/FN、空分母、重复事实、作用域/时态不符、等价证据组、多跳集齐、空召回、返回少于k、缺注入事件、只拒答刷分、旧值仅被提到不等于误用。指标输出必须带num/denom/status/coverage和version。增加targeted额外合法事实不计FP、未校准等价关系为unknown、最大匹配优于顺序贪心、答案正确但无记忆证据、scene-macro与probe-micro不同等用例。

F1使用计数直接计算，避免P/R为null时把漏记藏起来：

```python
def extraction_prf(tp: int, fp: int, fn: int) -> tuple[float | None, float | None, float | None]:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    return precision, recall, f1

def test_missing_all_expected_facts_is_zero_recall():
    assert extraction_prf(0, 0, 2) == (None, 0, 0)
```

- [ ] **Step 2 — 实现规则评分：** metrics.py定义契约全部ID；`grade_memory_trial(execution,gold,policy)`按contract§8做版本化标准化、否定/时间/scope检查、等价边与确定性增广路最大二分匹配，并形成probe/scenario/category/dataset结果。closed_world才计算完整抽取P/R/F1；targeted只计算已标目标覆盖。qa_correct按必需事实全满足且无关键矛盾二值判定；部分完成另列。增加grounded_memory_success与qa_accuracy_scenario_macro，按先probe→trial→scene等权的固定公式聚合；缺后端state时只计算行为，不生成伪状态分数。
- [ ] **Step 3 — 实现语义与通用层：** judge_materials生成逐probe问题/时点/scope/参考/实际证据，无工具Judge、匿名A/B，材料截断或未校准所需语义项待复核。复用reviews与calibration记录指纹，按memory_ref重算新grading_revision/review_digest，保留原分数且不回写已冻结比较。通用层补`task_success_rate_judged`及工具选择/参数标注计分；无标注null+coverage，旧task_success_rate分母不变。分阶段usage含抽取/派生/embedding/reader/Judge，unknown费用null。
- [ ] **Step 4 — GREEN：** `pytest tests/evaluation/test_memory_metrics.py tests/evaluation/test_memory_grading.py tests/evaluation/test_assessment.py tests/evaluation/test_reports.py tests/evaluation/test_metrics_v2.py tests/evaluation/test_calibration.py tests/evaluation/test_judge_materials.py -q`。手工标注至少覆盖正反例；自动生成gold仅proposed，不伪称人工确认。
- [ ] **Step 5 — 检查点：** Leader复算小样本分子/分母、聚合权重及四值verdict映射；主题`feat(evaluation): 增加记忆专项指标与证据评分`。

## 8. Task 6 / WU-06：内置集合、版本编辑与LongMemEval导入

**Files:** 新增`nanobot/evaluation/memory/{datasets,longmemeval}.py`、`nanobot/evaluation/samples/memory-{smoke,core,calibration}-v1.jsonl`、`tests/evaluation/test_memory_datasets.py`、`test_longmemeval_loader.py`、`test_memory_sample_quality.py`；修改`nanobot/evaluation/sample_cases.py`。

- [ ] **Step 1 — RED：** 写JSON/JSONL流式分块、UTF-8跨块、畸形尾部、单场景32MiB上限、ID重复、证据悬空、来源hash不符、数据集编辑CAS、重复导入幂等测试。任一条失败集合不进入ready，错误不回显本机路径。
- [ ] **Step 2 — 实现数据流：** `read_scenario_stream(stream,format)`增量解码；JSON数组用JSONDecoder.raw_decode逐条消费，缓冲受限；JSONL受限逐行。`import_dataset`写staging→原子发布ready，取消/磁盘配额不足清理上传。编辑生成新revision并保持旧gold及引用不变，导出复用artifact登记。
- [ ] **Step 3 — 建题：** 核心120场景10类×12，必须逐条人工可审核历史与证据；开发冒烟20为另一个development集合，不把regression同模板直接复制为调参题。calibration使用独立模板/人物且正反例齐全，gold默认proposed。加入历史时点、偏好禁忌/软偏好、共享正例、无gold负例、删除后重建、短会话策略等，记录template_family/cluster_id并自动检查跨split重叠。
- [ ] **Step 4 — LongMemEval：** `load_longmemeval`读取本地上传原格式，保留question_id/type、时间、answer/evidence_sessions/has_answer，生成场景与私有gold；不会把has_answer提示给Worker。锁定cleaned revision/hash、500题原始ID清单，分层子集显示subset。官方二值Judge按题型建立版本化内置适配，记录协议hash；不执行上传评分脚本，不把内部F1冒充官方分数。

数据质量的可执行验收例：

```python
from collections import Counter
from pathlib import Path
import json

def test_core_covers_ten_categories():
    path = Path("nanobot/evaluation/samples/memory-core-v1.jsonl")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    counts = Counter(row["tags"][0] for row in rows)
    assert len(rows) == 120
    assert len(counts) == 10 and set(counts.values()) == {12}
    assert len({row["scenario_id"] for row in rows}) == 120
```

- [ ] **Step 5 — GREEN/检查点：** `pytest tests/evaluation/test_memory_datasets.py tests/evaluation/test_longmemeval_loader.py tests/evaluation/test_memory_sample_quality.py tests/evaluation/test_samples.py -q`。测试只用自建微型外部格式fixture；完整公开数据不进代码库、不在测试里联网下载。主题`feat(evaluation): 增加中文记忆题库与基准导入`。

## 9. Task 7 / WU-07：候选冻结、分批实验、预算与恢复

**Files:** 新增`nanobot/evaluation/memory/{experiments,manifests}.py`、`tests/evaluation/test_memory_experiments.py`、`test_memory_manifests.py`；修改`nanobot/evaluation/{scheduler,services,repository}.py`。

- [ ] **Step 1 — RED：** 测120×3×3生成1080 planned trials/9个子run、跨批总预算拒绝、相同幂等key不重复、进程重启不重建已入队run、取消后停止新批且回收现有进程；子run共享原slots并发闸。缺配置实际值/代码来源时禁止严格比较。
- [ ] **Step 2 — 实现分批：** 以固定scenario顺序和contract上限计算批次；创建子run时统一scene ref→case id/revision映射，写experiment_runs唯一关系；通过同一EvaluationRepository事务/计划恢复，绝不在HTTP await模型。

```python
def plan_batches(ids: list[str], repetitions: int) -> list[list[str]]:
    if not 1 <= repetitions <= 10:
        raise ValueError("MEMORY_SCHEMA_INVALID")
    size = min(40, 200 // repetitions)
    return [ids[start:start + size] for start in range(0, len(ids), size)]

def test_core_batches_preserve_all_trials():
    batches = plan_batches([f"s{i}" for i in range(120)], 3)
    assert [len(batch) for batch in batches] == [40, 40, 40]
    assert sum(len(batch) * 3 for batch in batches) == 360
    assert all(len(batch) * 3 <= 200 for batch in batches)
```

- [ ] **Step 3 — 实现冻结与预算：** 入队冻结数据/gold/策略/真实模型/检索与索引参数、代码SHA+相关dirty hash、clock/能力、评分指纹；不读.env。实际请求前预留组预算，结算失败/重试全成本，组并发不能越界；能精确知道的Token/金额才硬结算，未知不当0。货币预算缺单价则拒绝自动放行。
- [ ] **Step 4 — GREEN：** `pytest tests/evaluation/test_memory_experiments.py tests/evaluation/test_memory_manifests.py tests/evaluation/test_scheduler.py tests/evaluation/test_services.py tests/evaluation/test_manifests.py -q`。旧baseline结果可直接保留到升级后比较；服务不动态checkout旧Git源码。试验未全部可评分时measurement_status=incomplete。
- [ ] **Step 5 — 检查点：** 总计划数、取消/重试账目与数据库实际attempt数能对账；主题`feat(evaluation): 增加记忆实验分批与配置冻结`。

## 10. Task 8 / WU-08：历史配对、聚类区间与报告

**Files:** 新增`nanobot/evaluation/memory/{comparisons,report}.py`、`tests/evaluation/test_memory_comparisons.py`、`test_memory_report.py`；复用现有comparisons.py思想，不改变普通比较规则。

- [ ] **Step 1 — RED：** 同场景不同revision拒绝、未声明模型变化拒绝、允许的检索参数变化可比、缺一trial为incomplete、同conversation多题按cluster聚类、20以下不输出区间、分页不影响全量统计、0违规不等于安全保证、关键泄漏压过均分收益。
- [ ] **Step 2 — 实现配对：** `compare_memory_experiments`先检查终态/冻结摘要/changed_fields，再逐scenario聚合trial与probe；cluster级bootstrap固定seed42/2000轮；输出paired/excluded/coverage和题级micro/场景macro/类别macro。官方协议另区，统计分母不能因分页或未判定变化。
- [ ] **Step 3 — 实现结论：** `classify_change`只返回statistical_direction（统计支持提升/尚不能确认/退化）；另实现adoption_decision，检查minimum_effect_pp、分项退化容忍度、成本/延迟预算和关键门禁。按contract§8.5使用cluster整体配对重采样、type7分位数；缺题/缺重复/未校准时主指标不完整，不能以子集提升自动采用。关键泄漏、删除复活直接阻止放行。HTML与API使用同一不可变比较快照，escape所有用户文本，不含外部脚本与私有gold路径。

```python
def classify_change(interval: tuple[float, float] | None) -> str:
    if interval is None:
        return "inconclusive"
    low, high = interval
    if low > 0:
        return "improved"
    if high < 0:
        return "regressed"
    return "inconclusive"

def test_interval_crossing_zero_is_not_proven_unchanged():
    assert classify_change((-0.02, 0.05)) == "inconclusive"
```

- [ ] **Step 4 — GREEN：** `pytest tests/evaluation/test_memory_comparisons.py tests/evaluation/test_memory_report.py tests/evaluation/test_comparisons.py tests/evaluation/test_report_view.py -q`。固定合成数据手算差值、cluster数和成本，恶意HTML被转义。
- [ ] **Step 5 — 检查点：** 报告完整列出改善/退化/未知，成本拆写入/查询/Judge，未知分项不填0；主题`feat(evaluation): 增加记忆版本配对报告`。

## 11. Task 9 / WU-09：HTTP API、幂等与TS客户端

**Files:** 新增`nanobot/evaluation/memory/routes.py`、`webui/src/lib/memory-evaluation-api.ts`、`tests/evaluation/test_memory_routes.py`、`webui/src/tests/memory-evaluation-api.test.ts`；修改`nanobot/evaluation/{server,errors,schemas,services}.py`、`webui/src/lib/evaluation-api.ts`。

- [ ] **Step 1 — RED：** 按契约§6逐路由覆盖未登录、坏CSRF/Origin、413、404、无能力422、异体同key422、并发处理中409、limit越界、游标筛选不匹配、未知内部异常不泄路径；读gold和artifact也须鉴权。写完整矩阵，不能只测happy path。
- [ ] **Step 2 — 实现路由：** routes.py返回RouteDef清单，由现有server._routes挂载到同前缀并纳入guard/method表；schema边界解析一次，service内部用严格模型。上传采用流式multipart，不提高全站默认正文限制；202后GET状态，不用长请求跑评测。原add_review转交可选memory_ref并校验attempt归属/评分revision，过期409 MEMORY_GRADING_REVISION_CONFLICT；旧无ref请求保持原形状。错误码全部注册到errors.py，禁止自行拼错误响应。
- [ ] **Step 3 — 实现客户端：** 从evaluation-api.ts抽出并复用其会话/CSRF/Idempotency-Key请求函数，保留原API签名。memory-evaluation-api.ts的请求/响应逐项对应契约，AbortSignal支持取消轮询，流上传显示进度；认证重试沿用同一key，不能重复计费。样本导入结果增量memory_dataset_revision_id保持旧类型兼容。

错误映射的最小可执行验收：

```python
from nanobot.evaluation.errors import status_for

def test_memory_error_statuses():
    assert status_for("MEMORY_DATASET_NOT_FOUND") == 404
    assert status_for("MEMORY_DATASET_NOT_READY") == 409
    assert status_for("MEMORY_BUDGET_EXCEEDED") == 422
    assert status_for("MEMORY_IMPORT_TOO_LARGE") == 413
```

- [ ] **Step 4 — GREEN：** `pytest tests/evaluation/test_memory_routes.py tests/evaluation/test_auth.py tests/evaluation/test_routes.py -q`；webui执行`bun run test src/tests/memory-evaluation-api.test.ts src/tests/evaluation-api.test.ts`，期望认证兼容、分页准确、异体key拒绝、未知状态不误显示成功。
- [ ] **Step 5 — 检查点：** API测试与TS类型字段逐项对照；主题`feat(evaluation): 开放记忆评测接口`。

## 12. Task 10 / WU-10：题库管理与实验创建界面

**Files:** 新增`webui/src/components/settings/evaluation/memory/{MemoryDatasets,MemoryScenarioEditor,MemoryExperimentForm,MemoryMetricValue}.tsx`、`webui/src/tests/memory-evaluation-datasets.test.tsx`、`memory-evaluation-form.test.tsx`；不改现有总入口与翻译文件（由WU-11统一接线）。

- [ ] **Step 1 — RED：** 测内置20/120导入、JSON/JSONL错误定位、历史/证据编辑后生成新revision、staging不可运行、轨道能力缺失提示、候选预算总量随重复/候选数变化、空Judge时语义项待复核。表单拒绝1080 trials超默认预算，必须显式修改。
- [ ] **Step 2 — 实现题库：** MemoryDatasets显示来源/许可/split/覆盖与版本；MemoryScenarioEditor展示会话事件、时间、证据与gold管理面，只修改白名单字段，不提供任意脚本编辑；导出引用后端artifact。旧Agent/xlsx集合入口继续可用。
- [ ] **Step 3 — 实现创建：** MemoryExperimentForm选择三轨、candidate设置、repetitions、预算和冻结条件；历史baseline通过已保存实验选择，不提供Git命令框；仅展示后端capabilities支持的参数，不能提供不生效旋钮。无判据、无校准或不支持删除/时钟的影响在提交前清楚显示。

共同指标显示组件约定props为`{value:number|null, unit:string, status:string}`，不可测显示“未评分”，而不是0%：

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryMetricValue } from "@/components/settings/evaluation/memory/MemoryMetricValue";

it("does not display missing evidence as zero accuracy", () => {
  render(<MemoryMetricValue value={null} unit="ratio" status="not_assessable" />);
  expect(screen.getByText("未评分")).toBeInTheDocument();
  expect(screen.queryByText("0%")).not.toBeInTheDocument();
});
```

- [ ] **Step 4 — GREEN：** `bun run test src/tests/memory-evaluation-datasets.test.tsx src/tests/memory-evaluation-form.test.tsx`，组件交互能产生契约合法请求；键盘、表单label、上传失败重试、mobile滚动可用。翻译key清单交给WU-11，不写硬编码技术实现术语到产品主流程。
- [ ] **Step 5 — 检查点：** 只新增约定组件与测试，确认export/props/翻译key清单后交给WU-11；主题`feat(webui): 增加记忆题库与实验表单`。

## 13. Task 11 / WU-11：报告、回放、复核与现有入口接线

**Files:** 新增`webui/src/components/settings/evaluation/memory/{MemoryEvaluation,MemoryExperimentDetail,MemoryComparison,MemoryReplay}.tsx`、`webui/src/tests/memory-evaluation-report.test.tsx`、`memory-evaluation-replay.test.tsx`；修改`webui/src/components/settings/evaluation/{EvaluationSettings,EvaluationReviews,EvaluationCalibration}.tsx`、`webui/src/i18n/locales/{zh-CN,en}/common.json`。

- [ ] **Step 1 — RED：** 测完整/不完整分离、改善与退化列表、条件不匹配拒比、差值百分点、少于20cluster无区间、关键违规盖过平均提升、缺事件显示缺证、历史baseline与当前候选的SHA可见。重用现有review/calibration状态，不能用“样本数量足够”假装Judge已校准；分开显示统计方向与采用建议，targeted标注无抽取Precision时显示具体原因，答对无证据不当记忆使用成功。
- [ ] **Step 2 — 实现主入口：** EvaluationSettings新增“记忆专项”tab并保持原overview/runs/datasets；MemoryEvaluation负责列表和导航，MemoryExperimentDetail展示分批进度/预算/各轨指标及取消；创建和编辑组件由WU-10导出，不复制其逻辑。
- [ ] **Step 3 — 实现回放/复核：** MemoryReplay按真实event seq展示历史→抽取→状态变化→候选→重排→实际注入→回答，标记failed/skipped/unknown。MemoryComparison展示固定条件、变化项、全量统计与分页case；允许逐probe复核，复用旧review接口的memory_ref标明scenario/probe/metric/grading_revision。修改人工判定保留旧评分及fingerprint，版本过期提示刷新，旧比较保持快照不变。

差值显示必须明确百分点单位，叶函数契约为：

```typescript
export function memoryDeltaLabel(delta: number | null): string {
  if (delta === null) return "证据不足";
  const value = delta * 100;
  return `${value > 0 ? "+" : ""}${value.toFixed(1)} 个百分点`;
}
```

- [ ] **Step 4 — GREEN：** `bun run test src/tests/memory-evaluation-report.test.tsx src/tests/memory-evaluation-replay.test.tsx src/tests/evaluation-settings.test.tsx src/tests/evaluation-reliability.test.tsx src/tests/i18n.test.tsx`。zh-CN/en新增key一致，其他语言按既有fallback；UI显示已知失败和未知，不发明分数。
- [ ] **Step 5 — 检查点：** Leader确认WU-10已完成再验收本WU接线，新增组件label与翻译一致，再执行`bun run build`；主题`feat(webui): 增加记忆比较与证据回放`。

## 14. Task 12 / WU-12：集体验收、真实基线与尾盘

**Files:** 新增`tests/evaluation/test_memory_end_to_end.py`、`test_memory_security.py`、`webui/src/tests/memory-evaluation-integration.test.tsx`；新增过程文档仅在`.ai-runtime-artifacts/verifications/`、`reviews/`、`execution-logs/`。不添加测试专用业务分支把失败变绿。

- [ ] **Step 1 — 离线集成：** 真实服务/DB/子进程+受控provider跑20场景；三轨、重启、重复、取消、异常恢复及120×3分批都覆盖。注入四种受控故障：抽取漏项/召回漏项/注入预算截断/回答误用，要求报告分别定位。允许断言受控故障产生失败，但不能据此声称真实模型质量达标。
- [ ] **Step 2 — 安全与数据边界：** 测原工作区不可读、gold不可见、历史中的注入指令不控制Judge、导入路径/上传大小受限、报告XSS转义、不同namespace的缓存和singleton无泄漏、删除不复活与允许共享正例。黑盒/多租户未支持项必须显示缺失，实际产品缺陷输出独立问题记录。
- [ ] **Step 3 — 软件质量：** 根目录按项目要求运行`uv run --no-sync python -m pytest tests/evaluation/ tests/memory/ -q`、`uv run --no-sync ruff check nanobot/`、`uv run --no-sync basedpyright`、`uv run --no-sync python -m pytest tests/ -q`；webui执行`bun run test`、`bun run lint`、`bun run build`。记录每条结果与失败归属；相关必需检查未通过不得宣布软件验收完成。浏览器实测导入→运行→对比→回放→复核，保留截图与请求证据。
- [ ] **Step 4 — 真实模型质量验收：** 使用用户现有已授权profile，运行前显示并锁定预算；profile/额度不足时明确“真实质量验收未完成”，离线交付不冒充全量质量验收。先20场景三基线（无持久记忆/当前/候选），再显式预算下跑核心120×3；同代码无策略差异的A/A作稳定性检查。LongMemEval先固定分层小样本验证映射与官方协议，再按预算全量500；完整集未跑时只报subset。人工确认gold与Judge指纹，未校准语义分数不自动放行。
- [ ] **Step 5 — 尾盘：** 编排reviewer与security-auditor只读独立审查，必要时perf-auditor验证并发/索引成本；修复后仅重跑受影响检查。Leader写collective-test、code-review、security-review与execution-log，收齐软件检查、质量证据/缺口、逐项验收与提交范围。提交/push/PR遵循另行授权及项目Git规则，不能由worker自动执行。

## 15. 设计覆盖与验收追踪

| 已批准spec章节 | 交付任务 | 关键验收 |
| --- | --- | --- |
| §3三条轨道、§4执行协议 | 02/03/04 | 新会话、真实记忆写入、可等待状态、时钟、隔离clone、重启 |
| §5数据集/导入/版本 | 01/06/09/10 | 20/120、JSON/JSONL、LongMemEval、split/证据、历史revision |
| §6通用Agent指标 | 05/08/11 | 旧分母兼容，任务/工具/稳定性/成本/覆盖均可核对 |
| §7专项指标 | 02/03/05/11 | 全部指标ID、删除/泄漏阶段证据、能力缺失不通过 |
| §8版本比较 | 07/08/11 | 候选冻结、历史baseline、批次预算、cluster区间、关键门禁 |
| §9评分可信度 | 04/05/09/11 | gold分离、截断缺证、校准、review、匿名与无工具Judge |
| §10页面与数据 | 01/07/09/10/11 | 同一现有入口、分页、CAS、幂等、可回放、HTML |
| §11验收与后续范围 | 12 | 软件健康与真实质量分别给证据，第二阶段不混交付 |

## 16. 自检、回退与交接

- [ ] 核对plan/contract/dispatch所有WU、字段、路径与依赖一致，模型先于消费者，所有共享文件只有一个同时写者。
- [ ] 代码示例中的MemoryClock、MemoryMetricValue、extraction_prf、plan_batches、classify_change均有对应创建任务；Protocol类型全部在WU-01定义。
- [ ] 不把同一天三候选误写成任意Git版本并行执行；旧SHA通过历史冻结结果比较，未来外部runner另立设计。
- [ ] 已覆盖200/run与全实验预算、gold隔离、短会话策略、部分后台失败、异步索引、时钟双轨和重复probe污染。
- [ ] 数据迁移前私有备份；关闭memory专项入口可停止新实验，旧普通评估继续工作。已升级库不能由旧代码写，真正回退恢复对应备份。进行中实验先取消并回收进程，禁止直接删除运行目录。
- [ ] 计划本身只做文档验证，不预报未来测试通过数，也不承诺真实算法分数。

## Next

- 确认计划 → 说“开始实现”或“执行”；按dispatch从WU-01开始，完成全部第一阶段。
- 希望并行 → 审阅同stem dispatch后说“并行执行”；保持模型契约及共享文件依赖顺序。
- 需要调整 → 直接给出范围、指标或预算意见，先修改本计划与契约。
- 当前status=draft、approved=false；本轮不改业务代码、不建worktree、不启动子Agent。

