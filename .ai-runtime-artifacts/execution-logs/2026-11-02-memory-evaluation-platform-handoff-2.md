---
artifact: handoff
route: orchestration
plan: .ai-runtime-artifacts/plans/2026-11-02-memory-evaluation-platform-plan.md
dispatch: .ai-runtime-artifacts/plans/2026-11-02-memory-evaluation-platform-dispatch.md
tracking: .ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-11-02-memory-evaluation-platform.md
contract: .ai-runtime-artifacts/contracts/2026-11-02-contract-memory-evaluation.md
created_at: 2026-11-02
status: handoff
previous_handoff: .ai-runtime-artifacts/execution-logs/2026-11-02-memory-evaluation-platform-handoff.md
---

# 交接文档（第二轮）：知序记忆评测平台第一阶段

> 上一份交接文档是「进入本轮」的入口；本份是「离开本轮」的出口。
> 上一份说：WU-01 未交付、需要重派。本份说：**WU-01 / WU-02 / WU-05 / WU-06a 已交付并通过验证，只剩内置题库一道菜没做完**。

---

## 0. 一句话状态

计划与契约已批准；隔离 worktree 与独立依赖环境就绪。本轮把 **WU-01（模型/协议/仓储 + schema 迁移 4）、WU-02（时钟 + 完成信号）、WU-05（指标与评分）、WU-06a（集合导入器与 LongMemEval loader）** 四个工作单元做完并验证通过（定向测试合计 **564 passed**）；**唯一未完成的是 WU-06b 的三个内置题库 JSONL 文件**——生成器模板已经跑通并产出了 60 条合格数据，剩下 3 个文件待补。WU-03 及之后全部未开始。

---

## 1. 本轮已完成（可直接验收）

| WU | 内容 | 落地文件 | 定向验证（Leader 实测） |
| --- | --- | --- | --- |
| **WU-01** | 记忆专项模型/协议/仓储 + `SCHEMA_VERSION="4"` + migration 4（6 张表）+ `reviews.memory_ref_json` | 新增 `nanobot/evaluation/memory/{__init__,models,protocol,repository}.py`；改 `nanobot/evaluation/{database,schemas,repository}.py` | **289 passed**（test_memory_schemas / test_memory_repository / test_database / test_validation / test_manifests） |
| **WU-02** | `MemoryClock` 三时间轴 + `MemoryOperationRegistry` 完成信号 + `flush_session_memory` 协调入口 | 新增 `nanobot/memory/{clock,signals}.py`；改 `nanobot/agent/hooks/memory_extraction.py`、`nanobot/memory/{extractor,orchestrator,lifecycle}.py`、`nanobot/memory/retrieval/{engine,reranker,store_adapter}.py`、`nanobot/memory/retrieval/channels/{episodes,attachments}.py` | **90 passed**（test_evaluation_clock / test_completion_signals / test_orchestrator / test_lifecycle / test_memory_extraction_hook）；ruff 干净 |
| **WU-05** | 全部记忆指标 ID + `qa_accuracy_scenario_macro` + `grade_memory_trial` + 最大二分匹配 + judge_materials + 通用层新指标 | 新增 `nanobot/evaluation/memory/{metrics,grading,judge_materials}.py`；改 `nanobot/evaluation/{assessment,reports}.py`、`nanobot/evaluation/graders/{judge,calibration}.py` | **144 passed**（test_memory_metrics / test_memory_grading / test_assessment / test_reports / test_metrics_v2 / test_calibration / test_judge_materials）；`ruff check nanobot/evaluation/` → All checks passed |
| **WU-06a** | 流式 JSON/JSONL 解析、staging→ready 原子发布、跨 split 隔离、限额、CAS 编辑、导出登记、LongMemEval loader 与分层子集 | 新增 `nanobot/evaluation/memory/{datasets,longmemeval}.py`；改 `nanobot/evaluation/sample_cases.py` | **41 passed**（test_memory_datasets / test_longmemeval_loader）；`ruff check nanobot/evaluation/memory/` → All checks passed |

### 本轮修掉的真实缺陷（不是测试写错）

| 缺陷 | 位置 | 性质 |
| --- | --- | --- |
| 迁移 4 不幂等 → `duplicate column name: memory_ref_json` | `database.py` | 真实产品缺陷；改为 `_MIGRATIONS` 条目支持 `Callable[[sqlite3.Connection], None]`，ALTER 前先 `PRAGMA table_info` 判存在 |
| `_init_at_version` 造出「版本号是 3、结构却是 4」的假旧库 | `tests/evaluation/test_database.py` | 测试 helper 缺陷；改为 monkeypatch `_MIGRATIONS` 子集 |
| JSON 数组导入**从不消费元素间的 `,`** → 任何 ≥2 元素数组全失败 | `memory/datasets.py::_read_json_array` | 真实产品缺陷（普通 `BytesIO` 同样复现） |
| 导出摘要与落盘字节不一致（Windows 文本模式 `\n`→`\r\n`） | `memory/datasets.py::export_dataset_revision` | 真实缺陷；改为 `write_bytes` + 对落盘字节求 sha256 |
| 二值 Judge 对短答案题要求完全相等 →「你最喜欢乌龙茶。」判错 | `memory/longmemeval.py` | 口径缺陷；新增 `answer-phrase/v1` 版本化适配器（协议 hash 随之变更） |
| `CaseWrite` 新增字段后旧字段集枚举测试失败 | `tests/evaluation/test_validation.py` | 契约 §1 要求的合法变更；已授权 WU-01 同步该测试 |
| `test_manifests.py` 钉死 `SCHEMA_VERSION == 2` 的过期断言 | `tests/evaluation/test_manifests.py` | Leader 直接修（契约 §7 要求升到 4） |

### 基线对照证据（重要，别重复踩）

`tests/evaluation/` 全量在 WU-01 完成后为 **5 failed / 986 passed**。Leader 用 `git stash` 把 6 个已跟踪改动退回基线后重跑**同样 5 条用例 → 同样 5 条失败**，证明是既有环境缺陷（Windows 深路径写 session checkpoint 临时文件 `FileNotFoundError`），**与本轮改动无关**。既有失败清单与根因见 `.ai-runtime-artifacts/verifications/2026-10-26-evaluation-verification-lite.md:70-71`。

---

## 2. 唯一未完成项：三个内置题库 JSONL

**状态**：`nanobot/evaluation/samples/` 下 **三个文件全部不存在**：

| 文件 | 需要 | split | 缺口 |
| --- | --- | --- | --- |
| `memory-core-v1.jsonl` | **120 条**，10 类各 12 条 | `regression` | **全部缺** |
| `memory-smoke-v1.jsonl` | **20 条**，10 类都出现 | `development` | **全部缺** |
| `memory-calibration-v1.jsonl` | **≥20 条**，10 类各含正负例 | `calibration` | **全部缺** |

因此 `tests/evaluation/test_memory_sample_quality.py` 当前为 **8 failed / 1 passed / 19 errors**（全部因文件缺失）。该测试文件本身**已写好且是权威口径**，不需要重写。

### 2.1 已经产出的 60 条（可直接复用）

`.ai-runtime-artifacts/verifications/memory-evaluation/WU-06/parts/core-b.jsonl` —— **60 条**（`multi-session` / `abstention` / `distractor` / `forget` / `isolation` 各 12 条）。

Leader 已实测校验：**60 行全部通过 `MemoryScenario.model_validate`**；`scenario_id` / `cluster_id` / `subject_id` 各 60 唯一；**历史文本 60 条互不重复**；`forget` 事件 12 条、`rebuild` 4 条、`shared-allowed` 3 条、无 gold 的 unanswerable probe 33 条。

### 2.2 已跑通并可复制的生成器模板（**接手后照这个做**）

`.ai-runtime-artifacts/verifications/memory-evaluation/WU-06/parts/gen_core_b.py`（1321 行，46 KB）——这是**唯一被证明有效的做法**：内容表 + 通用构造器，**不要**手写完整 JSON。

结构：
- 助手函数：`u()` / `a()`（消息）、`ses()`（会话事件）、`forget()` / `rebuild()` / `checkpoint()` / `restart()`（事件）、`fact()`（事实）、`probe()`（探针+gold）、`gone()`（删除后的状态期望）。
- 每类一张内容表：`MULTI_SESSION` / `ABSTENTION` / `DISTRACTOR` / `FORGET` / `ISOLATION`，每张表 12 项子型。
- 通用构造器 `build_row()` 负责：ID 命名、split、时间轴（`BASE = datetime(2026,3,1,9,0,0,tzinfo=TZ)`，每场景 `SCENARIO_SPACING = 5 天`）、**证据 id 自动绑定到真实 message id**、gold/probe 一致性。
- `build_all()` + `main()` 写盘。
- 顶部常量：`SUBJECT_FIRST = 61`（本 worker 负责 061..120；**core-a 必须用 001..060**）、`OUT_PATH`。

**补齐剩余三份的做法**：复制 `gen_core_b.py` → 改 `SUBJECT_FIRST` / `OUT_PATH` / 类别表 → 产出分片 → 合并成最终文件。
- `core-a`：`stable-fact` / `preference` / `paraphrase` / `temporal` / `correction` 各 12 条 → `SUBJECT_FIRST=1`，输出 `parts/core-a.jsonl`。
- `smoke`：20 条、10 类都出现 → `scenario_id` 前缀 `smoke-`、`template_family` 前缀 `tf-smoke-`、`subject_id` 前缀 `synthetic-smoke-`、`split="development"`，输出 `parts/memory-smoke-v1.jsonl`。
- `calibration`：≥20 条 → 前缀 `calib-` / `tf-calib-` / `synthetic-calib-`、`split="calibration"`、**每条 tags 必须含 `gold:proposed` 且不得含 `gold:confirmed`**、每类正负例齐全，输出 `parts/memory-calibration-v1.jsonl`。
- 合并：`core-a.jsonl` + `core-b.jsonl` → `nanobot/evaluation/samples/memory-core-v1.jsonl`（**一行一个 JSON**，`ensure_ascii=False`）。

### 2.3 题库必须满足的硬约束（`tests/evaluation/test_memory_sample_quality.py` 是权威口径，共 27 个用例）

- **形状**：core 120 条 / 10 类各 12 条 / scenario_id 唯一；smoke 20 条且 10 类都出现；calibration ≥20 条、10 类都出现且每类正负例齐全。
- **split 隔离**：`template_family` 与 `scope.subject_id` **不得跨 split 复用**（core / smoke / calibration 互不相交）——用上面规定的前缀即可保证。
- **命名**：`scope.subject_id` 与 `workspace_id` 必须以 `synthetic-` 开头。
- **可校验**：每行都能被 `MemoryScenario.model_validate` 通过；`gold` 的键（除保留键 `annotation_scope` / `extraction_facts` / `state_expectations`）**等于** `probes` 的 id 集合；每个 `required_facts` / `forbidden_current_values` 的 `evidence_message_ids` 非空且指向**真实存在的 message id**。
- **唯一性**：core + smoke 的 `cluster_id` 合计不重复；core 的历史文本互不重复。
- **覆盖面**：`preference` ≥3 软偏好（无 `forbidden_current_values`）+ ≥3 明确禁止；`isolation` ≥1 条 `tags` 含 `shared-allowed` 且首个 probe 有 `required_facts`；`temporal` ≥6 条带 `valid_from`/`valid_to`；`forget` ≥6 条含 `forget` 事件、≥3 条含 `rebuild` 事件；`abstention` ≥6 条 `answerability="unanswerable"` 且无 `required_facts`；≥2 条 `tags` 含 `short-session` 且首个 session 事件消息数 ≤2；core 的 tags **不得**出现 `gold:confirmed`。
- **来源**：`memory_sample_source()` 要给出真实 sha256 / loader_version / grading_protocol / license（`sample_cases.py` 已实现，读文件算 sha256）。

---

## 3. Leader 已拍板的两处口径（不要再讨论）

1. **WU-06a 的 D4 语义变更**：临时上传的落盘错误改为「**块末抛**」（`MemoryDatasetError(MEMORY_IMPORT_DISK_FULL)`），失败瞬间即删除半截文件。→ **保留**。WU-09 的 API 层把该码映射为 422/507 即可，不要改回 fail-fast（会与已通过的断言冲突）。
2. **WU-06a 的 D8 近似 Judge**：新增 `answer-phrase/v1` 适配器。→ **保留**，前提是它与通用判分层严格隔离：独立 metric id `longmemeval_binary_judge`、`official=False`、协议 hash 冻结并随规则变化、报告中必须标注「平台近似判定，非官方分」。契约 §8.2「不用字符串 contains 当通用语义正确」约束的是通用记忆判分层（`grading.py` 用事实抽取 + 规范化匹配，**没有**用 contains），二者不冲突。

---

## 4. 本轮对契约与 dispatch 的修订（已生效）

- **契约 §1**：专项 case 的「必需 memory 指标判据」由可选字段 **`memory_required_metrics`**（指标 id 列表，缺省空）承载——专项 case 必须非空、普通 case 必须为空。
- **dispatch §7**：
  - §7.1 WU-01 文件所有权扩展到 `tests/evaluation/test_validation.py`（只改字段集枚举）。
  - §7.2 上述 `memory_required_metrics` 契约增量。
  - §7.3 **`plan_batches` 归属**：实现在 `nanobot/evaluation/memory/repository.py`；**WU-07 不得重复定义**，应 `from nanobot.evaluation.memory.repository import plan_batches` 再导出。
  - §7.4 迁移 4 幂等要求（已实现）。
- 上述修订在**主 checkout 与 worktree 两份副本**都已同步（两份内容一致）。

---

## 5. 环境与命令（Windows / PowerShell）

worktree 根：`D:/studyspace/project/Zhixu-memory-evaluation-platform`（分支 `codex/memory-evaluation-platform`，基点 `ce595d6`）

```powershell
cd D:/studyspace/project/Zhixu-memory-evaluation-platform
$env:PYTEST_ADDOPTS='--basetemp=.ai-runtime-artifacts/verifications/memory-evaluation/<WU-ID>/tmp -o cache_dir=.ai-runtime-artifacts/verifications/memory-evaluation/<WU-ID>/cache'
uv run --no-sync python -m pytest <本 WU 自己的测试文件> -q
uv run --no-sync ruff check <本 WU 改过的路径>
```

坑（务必知道）：

1. **不要复用主 checkout 的 `.venv`**——它是 editable 安装且指向主目录，复用会让测试导入主目录代码。worktree 必须用自己的 `.venv`（已建好，Python 3.13.9 / uv 0.12.9）。
2. **不要跑全量 `tests/` 或 `tests/evaluation/`**（用户 2026-11-02 明确要求）：只跑本 WU 改动的测试文件。Leader 的全量基线对照已做过，结论见 §1。
3. 每个 WU 用自己的 `PYTEST_ADDOPTS` 目录，避免并行 worker 争抢临时目录。
4. 临时目录与报告只落 `.ai-runtime-artifacts/verifications/memory-evaluation/<WU-ID>/`。
5. 用 PowerShell，不要 bash/WSL。
6. 子 Agent 一律**不 commit、不 push**；提交与推送属 Leader 且需用户另行确认。
7. **子 Agent 会因上下文耗尽被中断**：本轮有 3 个 worker 死在「生成大量中文内容」上。派发内容生成类任务时，**必须在 prompt 里要求用「生成脚本 + 紧凑内容表」**，并明确禁止把成品 JSON 打印进对话。

---

## 6. 文档地图（先读这四份）

| 文件 | 作用 |
| --- | --- |
| `.ai-runtime-artifacts/plans/2026-11-02-memory-evaluation-platform-plan.md` | 12 个 WU 的逐步任务、RED/GREEN 命令、文件所有权；**§3 = WU-01、§4 = WU-02、§7 = WU-05、§8 = WU-06** |
| `.ai-runtime-artifacts/contracts/2026-11-02-contract-memory-evaluation.md` | 字段与分母的唯一真相源：§1 数据结构、§3 adapter 协议与时钟、§4 指标、§6 HTTP/导入限制、§7 存储与迁移、§8 测量算法 |
| `.ai-runtime-artifacts/specs/2026-11-02-memory-evaluation-platform-spec.md` | 方案正文（页面、数据集、指标、比较、验收）；§5 是题库的 10 类定义 |
| `.ai-runtime-artifacts/specs/2026-11-02-memory-evaluation-frontend-spec.md` | 前端六视图设计 |

辅助：`.ai-runtime-artifacts/reviews/2026-11-02-memory-evaluation-measurement-document-review.md`、`research/2026-11-02-memory-evaluation-{research,guide}.md`、`stack/2026-11-02-memory-evaluation-stack.md`。

---

## 7. 剩余执行图（依赖顺序）

| GROUP | WU | 内容 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| G1 | WU-01 | 模型/协议/仓储 + 迁移 4 | 无 | ✅ **完成** |
| G2 | WU-02 | 时钟与完成信号 | WU-01 | ✅ **完成** |
| G2 | WU-05 | 指标与评分 | WU-01 | ✅ **完成** |
| G2 | WU-06 | 题库与 LongMemEval | WU-01 | ⚠️ **a 完成（导入器），b 未完成（题库）** |
| G3 | WU-03 | 知序 adapter、checkpoint 快照与新会话 | WU-01、WU-02 | ⬜ 未开始 |
| G4 | WU-04 | 专项 Worker 三轨执行闭环 | WU-01/02/03/05 | ⬜ 未开始 |
| G5 | WU-07 | 候选冻结、分批实验、预算与恢复 | WU-04/05/06 | ⬜ 未开始 |
| G6 | WU-08 | 聚类配对比较与同快照 HTML 报告 | WU-05、WU-07 | ⬜ 未开始 |
| G7 | WU-09 | HTTP 增量接口与 TS 客户端 | WU-06/07/08 | ⬜ 未开始 |
| G8 | WU-10 | 前端：题库管理与实验创建界面 | WU-09 | ⬜ 未开始 |
| G9 | WU-11 | 前端：报告、回放、复核与入口接线 | WU-09、WU-10 | ⬜ 未开始 |
| G10 | WU-12 | 集成/安全/浏览器验收与真实基线 | 全部 | ⬜ 未开始 |

**依赖提醒**：
- WU-03 只依赖 WU-01 + WU-02，**两者已完成 → 现在就能派**，不必等题库。
- WU-04 依赖 WU-05（已完成）+ WU-03；WU-07 依赖 WU-06 → **题库没补完前，WU-04 之后的链路不能真正收口**。
- 前端链最长：WU-09 → WU-10 → WU-11，不要提前手写前端组件去猜接口。

### WU-03 派发时必须带上的 4 条风险（来自 WU-02 的自检）

1. `flush_session_memory` 等 pending idle 任务受产品 idle 阈值（默认 120s）约束 → adapter 需调小 `idle_seconds` 或用 `wait_pending=False` 直接触发，否则**如实记 `timeout`，不得伪装成功**。
2. registry 的 `partial` 状态与契约 §3 `SettleResult.status` 闭集（completed/skipped_by_policy/failed/timeout）的**映射在 WU-03 适配层**做，契约不改。
3. `repository.query_semantic` 的 `since_days` SQL 窗口仍走真实时间（`nanobot/memory/repository.py` 不在 WU-02 白名单）→ 评测的 **clock_coverage 必须标「部分覆盖」**，不能声称全链路虚拟时钟。
4. `MemoryLifecycle._last_refresh_at` 是**类级** dict → 评测必须用独立 `workspace_id`，否则生产与评测实例的时间轴混用。

### WU-04 派发时必须带上的 3 条接口约定（来自 WU-05 的自检）

1. **WU-04 的 runner 必须向 `grade_memory_trial` 提供** `MemoryTrialExecution` 的这些字段：`asserted_facts` / `answer_facts_parsed` / `forgotten_fact_keys` / `scope_observable` / `source_supported_facts` / `state_observations`。缺 `answer_facts_parsed` 时**二值判定为未知（not_assessable）而不是 0**。
2. `extraction_precision` / `extraction_f1` 在 `targeted` 标注下按契约返回 `not_assessable`；`forget_state_success` 在后端不导出状态时返回 `not_assessable`（**不得推断成功**）；`estimated_cost` 单价未知时为 `null`。
3. 确定性判定**只认逐字相等**，同义改写记未命中（保守偏低估召回）；中文数字只在能确定时转换（"两三"不转、"三天"转）；语义判定路径已实现但**未经校准**，默认走确定性路径。

### 一个待裁决的契约缺口（WU-05 自检发现）

`negative_write_success`（纯负例「不该记却记了」）目前**恒为 `not_applicable`/`not_assessable`**：冻结的 `MemoryGold` 拒绝 `closed_world` + 空 `extraction_facts`（`CLOSED_WORLD_WITHOUT_FACTS`），因此**没有表达「已确认的抽取目标集为空」的方式**。契约 §4 明确要求这个指标单列。→ **接手人需要裁决并先改契约再改代码**（例如允许 `closed_world` + 显式的「目标集已确认为空」标记），不要在 WU-05 里私自放宽模型校验。

---

## 8. 验收口径（必须分开说）

- **软件交付**：离线可跑的评测闭环、页面流程、测试与回归证据。
- **真实质量**：需要真实模型 profile、embedding 与明确预算，属 WU-12；缺资源时写「真实质量验收未完成」，**不得用受控脚本结果冒充记忆能力提升**。
- 不得把「分数略高」直接说成「记忆变好」：需要 `statistical_direction`（统计支持）与 `adoption_decision`（采用建议）分开呈现。

---

## 9. 风险清单

| 风险 | 说明 | 应对 |
| --- | --- | --- |
| **子 Agent 上下文耗尽** | 本轮 3 个 worker（WU-01 首轮、WU-06b、WU-06b-A/B）死在长上下文上 | 内容生成类任务强制「脚本 + 内容表」；prompt 里明令禁止把成品 JSON 打进对话；派发 prompt 已是缺陷驱动式而非「读完全部文档再动手」 |
| **题库内容重复充数** | 复制粘贴会让回归分数看起来稳定 | `test_memory_sample_quality.py` 已锁唯一性与覆盖面；生成器必须每类 12 个**不同子型** |
| **共享文件并发写** | `schemas.py` / `database.py` / `services.py` / `worker.py` / `server.py` / `evaluation-api.ts` 多 WU 先后改 | 同一时刻只允许一个 WU 写同一文件；dispatch 已排串行次序；本轮 WU-05/WU-06a 并行时文件集不相交（已核） |
| **接口漂移** | `models.py`/`protocol.py` 是跨 WU 基线 | 冻结后要改字段，先改 contract 再改代码，由 Leader 安排顺序 |
| **迁移风险** | 现网 `SCHEMA_VERSION` 已升到 `"4"` | 回退＝恢复旧库并用匹配代码，禁止旧程序写 version4 库；迁移 4 已幂等 |
| **gold 泄漏** | 评分判据不得进入执行载荷或 artifact 路径 | WU-01 的 `build_execution_input` 是**唯一构造点**（gold 根本不在入参里）；WU-02 的 observer 事件递归剔除 gold 类键 |
| **前端先于接口** | 页面猜接口会返工 | 严格等 WU-09 → WU-10 → WU-11 |
| **真实评测资源** | profile/embedding/预算未核验 | 离线开发照做，质量验收单列 |

---

## 10. 恢复话术

从 `DISPATCH-TRACK-2026-11-02-memory-evaluation-platform.md` 恢复。两条路可并行：

**路线 A（补题库，约 1～2 个 worker）**
1. 复制 `.ai-runtime-artifacts/verifications/memory-evaluation/WU-06/parts/gen_core_b.py`；
2. 产 `core-a`（5 类 × 12 条，`SUBJECT_FIRST=1`）、`smoke`（20 条）、`calibration`（≥20 条）三个分片；
3. 合并为 `nanobot/evaluation/samples/memory-{core,smoke,calibration}-v1.jsonl`；
4. 跑 `pytest tests/evaluation/test_memory_sample_quality.py -q` 直到 27 条全绿。

**路线 B（推进主线，约 1 个 worker）**
5. 派发 **WU-03**（知序 adapter + checkpoint 快照 + 新会话），依赖已满足；prompt 里带上 §7 的 4 条风险。

**收口要求**
6. GROUP-2 收尾时 Leader 串行复核：各 WU 自己的测试文件 + `ruff` + **文件边界** + **交叉一致性**（指标 ID 是否与 contract §4/§8.4 一致、`MemoryMetricResult` 字段是否与 WU-01 冻结的 models 一致、`plan_batches` 归属是否唯一）。
7. WU-06b 完成后，`test_memory_sample_quality.py` 的 27 个用例必须全绿——这是题库唯一的验收口径。
