---
artifact: handoff
route: orchestration
plan: .ai-runtime-artifacts/plans/2026-11-02-memory-evaluation-platform-plan.md
dispatch: .ai-runtime-artifacts/plans/2026-11-02-memory-evaluation-platform-dispatch.md
tracking: .ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-11-02-memory-evaluation-platform.md
created_at: 2026-11-02
status: handoff
---

# 交接文档：知序记忆评测平台第一阶段

> 接手人先读这一页，再读 plan 与 contract。本文档只描述"已经发生了什么"和"从哪继续"，不重复设计内容。

## 1. 一句话状态

计划与契约已批准，隔离 worktree 与依赖环境已就绪，既有测试基线已记录；**GROUP-1 的 WU-01 尚未产出任何代码，需要重新派发**。其余 11 个工作单元全部未开始。

## 2. 已完成

| 事项 | 证据 | 位置 |
| --- | --- | --- |
| 方案/契约/前端设计定稿 | 用户已确认"先事实与偏好记忆"并批准实现 | `.ai-runtime-artifacts/specs/2026-11-02-memory-evaluation-platform-spec.md`、`-frontend-spec.md`、`contracts/2026-11-02-contract-memory-evaluation.md` |
| 计划与调度批准 | plan/dispatch 的 `status: approved`、`approved: true` | `.ai-runtime-artifacts/plans/2026-11-02-memory-evaluation-platform-{plan,dispatch}.md` |
| 隔离 worktree | 分支 `codex/memory-evaluation-platform`，基点 `ce595d6` | `D:/studyspace/project/Zhixu-memory-evaluation-platform` |
| 依赖环境 | worktree 内独立 `.venv`（`uv sync --all-extras --dev` 完成，Python 3.13.3） | worktree `.venv` |
| 既有基线 | 评估模块 `test_database + test_worker + test_reports` = **54 passed** | `.ai-runtime-artifacts/verifications/memory-evaluation/M0/baseline.md` |
| 过程追踪 | 已建 execution-log 与 DISPATCH-TRACK | `.ai-runtime-artifacts/execution-logs/…` |

## 3. 未完成（含必须知道的事实）

- **WU-01 未交付**：派发后约 30 分钟，worktree 内没有产生任何业务文件（`nanobot/evaluation/memory/`、`tests/evaluation/test_memory_*.py` 均为空），`git diff` 为空。该 worker 中途偏离任务，改为调查"多模态记忆链路"，并把结果写到了主 checkout 的 `.ai-runtime-artifacts/verifications/2026-11-02-multimodal-memory-review-verification.md`——**与本计划无关，属于偏航产物，请自行决定保留或删除**。
- **WU-02 ~ WU-12 全部未开始**，前端（WU-10 / WU-11）同样未开工。
- 主 checkout 与 worktree 的**已跟踪文件均无改动**；除 `.ai-runtime-artifacts/` 下的未跟踪过程产物外，没有任何代码落地。

## 4. 环境与命令（Windows / PowerShell）

worktree 根：`D:/studyspace/project/Zhixu-memory-evaluation-platform`

```powershell
cd D:/studyspace/project/Zhixu-memory-evaluation-platform
$env:PYTEST_ADDOPTS='--basetemp=.ai-runtime-artifacts/verifications/memory-evaluation/<WU-ID>/tmp -o cache_dir=.ai-runtime-artifacts/verifications/memory-evaluation/<WU-ID>/cache'
uv run --no-sync python -m pytest tests/evaluation/... -q
uv run --no-sync ruff check nanobot/
```

坑（接手人务必知道）：

1. **不要复用主 checkout 的 `.venv`**。它是 editable 安装且指向主目录，复用会让测试导入主目录代码，测出来"看似通过"其实跑的不是 worktree。worktree 必须用自己的 `.venv`（已建好）。
2. 每个 WU 用自己的 `PYTEST_ADDOPTS` 目录，避免并行 worker 争抢临时目录。
3. 临时目录与报告只落 `.ai-runtime-artifacts/verifications/memory-evaluation/<WU-ID>/`，不要在仓库根留垃圾。
4. 用 PowerShell，不要走 bash/WSL。
5. 子 Agent 一律不 commit、不 push；提交与推送属 Leader 且需用户另行确认。

## 5. 文档地图（先读这四份）

| 文件 | 作用 |
| --- | --- |
| `.ai-runtime-artifacts/plans/2026-11-02-memory-evaluation-platform-plan.md` | 12 个 WU 的逐步任务、RED/GREEN 命令、文件所有权；**§3 是 WU-01 的全部指令** |
| `.ai-runtime-artifacts/contracts/2026-11-02-contract-memory-evaluation.md` | 字段与分母的唯一真相源：§1 数据结构、§7 存储与迁移、§8 测量算法 |
| `.ai-runtime-artifacts/specs/2026-11-02-memory-evaluation-platform-spec.md` | 方案正文（页面、数据集、指标、比较、验收） |
| `.ai-runtime-artifacts/specs/2026-11-02-memory-evaluation-frontend-spec.md` | 前端六视图设计（实验记录 / 测试集 / 创建实验 / 实验详情 / 版本比较 / 场景回放） |

辅助：`.ai-runtime-artifacts/reviews/2026-11-02-memory-evaluation-measurement-document-review.md`（测量审查，契约 §8 的依据）、`research/2026-11-02-memory-evaluation-{research,guide}.md`（开源基准调研与用户向说明）、`stack/2026-11-02-memory-evaluation-stack.md`（依赖版本）。

plan 章节行号速查：§0 证据纪律 37 / §0.1 M0 56 / §1 文件所有权 68 / §2 里程碑 88 / Task1 WU-01 100 / Task2 134 / Task3 158 / Task4 187 / Task5 212 / Task6 236 / Task7 263 / Task8 288 / Task9 314 / Task10 337 / Task11 361 / Task12 382 / §15 覆盖追踪 392 / §16 自检 405。

## 6. 剩余执行图（依赖顺序）

| GROUP | WU | 内容 | 依赖 |
| --- | --- | --- | --- |
| G1 | WU-01 | 记忆专项模型/协议/仓储 + schema 迁移到 version 4 | 无 ← **从这里开始** |
| G2 | WU-02 / WU-05 / WU-06 | ① 产品完成信号与可注入时钟 ② 记忆指标与评分 ③ 内置题库与 LongMemEval 导入 | WU-01（可并行） |
| G3 | WU-03 | 知序 adapter、checkpoint 快照与新会话 | WU-01、WU-02 |
| G4 | WU-04 | 专项 Worker 三轨执行闭环 | WU-01/02/03/05 |
| G5 | WU-07 | 候选冻结、分批实验、预算与恢复 | WU-04/05/06 |
| G6 | WU-08 | 聚类配对比较与同快照 HTML 报告 | WU-05、WU-07 |
| G7 | WU-09 | HTTP 增量接口与 TS 客户端 | WU-06/07/08 |
| G8 | WU-10 | 前端：题库管理与实验创建界面 | WU-09 |
| G9 | WU-11 | 前端：报告、回放、复核与现有入口接线 | WU-09、WU-10 |
| G10 | WU-12 | 集成/安全/浏览器验收与真实基线 | 全部 |

前端依赖链最长：WU-09 接口完成后 WU-10 先出题库与表单，WU-11 再接报告与入口。不要提前手写前端组件去猜接口。

## 7. 接手后的第一件事：重新派发 WU-01

直接复用下面这段派发内容（角色文件、允许文件、契约指针、测试命令都已备好）：

```text
你是 WU-01，wu_type=feature，agent_role=coder。先读 D:/studyspace/project/Zhixu-memory-evaluation-platform/.claude/agents/coder.md 并按其检查表与返回格式执行。

工作目录（只在此目录工作，主 checkout 只读）：
D:/studyspace/project/Zhixu-memory-evaluation-platform（分支 codex/memory-evaluation-platform，基点 ce595d6）

目标：实现 plan §3 Task 1 / WU-01——记忆专项模型、协议、仓储与 schema 迁移到 version 4。
Done criteria：
1) 新增 nanobot/evaluation/memory/{__init__,models,protocol,repository}.py，落地 contract §1/§7 结构；
2) nanobot/evaluation/database.py 的 SCHEMA_VERSION 改 "4"，追加 migration 4 与 6 张记忆专项表，reviews 表加可空 memory_ref_json 列，既有 1/2/3 迁移正文不变；
3) schemas.py / repository.py 增加记忆专项读写与 memory_scenario_ref 可选字段，普通 case 旧校验语义不变，专项 case 允许空 turns/assertions 但必须可解析 ref；
4) MemoryRepository 提供 import_revision / get_scenario / get_execution_input / get_gold / create_experiment_plan / get_experiment / save_comparison，走 EvaluationDatabase 事务且不嵌套 connect；
5) get_execution_input 必须剥离 gold、来源许可与评分判据。

允许文件：nanobot/evaluation/memory/{__init__,models,protocol,repository}.py、tests/evaluation/{memory_fixtures,test_memory_schemas,test_memory_repository}.py；可改 nanobot/evaluation/{schemas,database,repository}.py、tests/evaluation/test_database.py。其他一律禁止；禁止 commit/push。

必读：.ai-runtime-artifacts/plans/2026-11-02-memory-evaluation-platform-plan.md（§0、§0.1、§0.2、§1、§3 全文精读）、contracts/2026-11-02-contract-memory-evaluation.md（§1、§7、§8.1–8.3）、reviews/2026-11-02-memory-evaluation-measurement-document-review.md、nanobot/evaluation/{schemas,database,repository}.py、tests/evaluation/{test_database,test_validation}.py。

Skills：source-driven-development、api-and-interface-design、verification-before-completion（均在 .claude/skills/）。

纪律：先写会失败的测试并实际跑到 RED，再实现，再 GREEN。迁移需覆盖空库两次幂等、version2 已有 comparisons、version3 正常升级、高版本拒绝启动、旧数据逐字一致、外键完整、并发同 key 不重复写。契约有歧义不要卡住也不要自行发明字段：按字面实现并把问题写进 risks_and_limits。

命令：$env:PYTEST_ADDOPTS='--basetemp=.ai-runtime-artifacts/verifications/memory-evaluation/WU-01/tmp -o cache_dir=.ai-runtime-artifacts/verifications/memory-evaluation/WU-01/cache'；uv run --no-sync python -m pytest tests/evaluation/test_memory_schemas.py tests/evaluation/test_memory_repository.py tests/evaluation/test_database.py tests/evaluation/test_validation.py -q；uv run --no-sync ruff check nanobot/evaluation/

返回：严格按 coder.md 的返回格式（变更摘要/检查表/测试资产/验证/完成状态/开发者自检/Skills 使用/阻塞项），必须含真实命令结果。
```

派发要点（避免重蹈覆辙）：

- 给 worker 的第一条消息必须包含"允许文件 + 契约指针 + 测试命令"三件套，并要求它**先在磁盘上产出 RED 测试文件**再继续；
- 中途用 `send_message` 催办时，要求它回一句当前处于哪一步、已落盘哪些文件，而不是让它重新理解任务；
- worker 一旦偏航（改了任务范围、或开始回答别的问题），立刻中断并重派，不要等它自己回来。

## 8. 验收口径（必须分开说）

- **软件交付**：离线可跑的评测闭环、页面流程、测试与回归证据。
- **真实质量**：需要真实模型 profile、embedding 与明确预算，属 WU-12；缺资源时写"真实质量验收未完成"，不得用受控脚本结果冒充记忆能力提升。
- 不得把"分数略高"直接说成"记忆变好"：需要统计方向（statistical_direction）与采用建议（adoption_decision）分开呈现。

## 9. 风险清单

| 风险 | 说明 | 应对 |
| --- | --- | --- |
| 共享文件并发写 | `schemas.py`、`database.py`、`services.py`、`worker.py`、`server.py`、`evaluation-api.ts` 由多个 WU 先后修改 | 同一时刻只允许一个 WU 写同一文件；dispatch 已排好串行次序 |
| 接口漂移 | `models.py`/`protocol.py` 是跨 WU 基线 | 冻结后要改字段，先改 contract 再改代码，由 Leader 安排顺序 |
| 迁移风险 | 现网 `SCHEMA_VERSION="2"` 但 `_MIGRATIONS` 含 "3"，本轮升到 "4" | 先在复制库验证；回退＝恢复旧库并用匹配代码，禁止旧程序写 version4 库 |
| gold 泄漏 | 评分判据不得进入执行载荷或 artifact 路径 | WU-01 的 `get_execution_input` 必须剥离 gold；后续 WU 同样守住 |
| 前端先于接口 | 页面猜接口会返工 | 严格等 WU-09 → WU-10 → WU-11 |
| 真实评测资源 | profile/embedding/预算未核验 | 离线开发照做，质量验收单列 |

## 10. 恢复话术

从 `DISPATCH-TRACK-2026-11-02-memory-evaluation-platform.md` 恢复：最后完成的是 M0 基线，GROUP-1 的 WU-01 未产出代码，需要重派。
继续 GROUP-1 中 WU-01 的 implement 步骤（见 §7 派发内容），拿到模型契约后并行派 GROUP-2（WU-02 / WU-05 / WU-06）。
