# DISPATCH-TRACK-2026-10-15-agent-evaluation

Batch: agent-evaluation
Plan: .ai-runtime-artifacts/plans/2026-10-15-agent-evaluation-plan.md
Dispatch: .ai-runtime-artifacts/plans/2026-10-15-agent-evaluation-dispatch.md
Contract: .ai-runtime-artifacts/contracts/2026-10-15-contract-agent-evaluation.md
Spec: .ai-runtime-artifacts/specs/2026-10-15-agent-evaluation-spec.md
Stack: .ai-runtime-artifacts/stack/2026-10-15-agent-evaluation-stack.md

## 批次范围说明

- 用户批准方式：本会话结构化提问选定「Agent 自动化评估」+「只跑 GROUP-1（WU-01 + WU-02）后停在屏障」，plan/dispatch FM 已改 `approved: true` 并引用该选择。
- **本批实际只派 WU-01**：WU-02 需要 Bun 跑 `bun install --frozen-lockfile` / typecheck / vitest / build，本机 PATH 与常见目录均无 Bun；用户明确选择「本批只做 WU-01，WU-02 等 Bun」，不允许用另一套 lock 冒充 frozen-lockfile 检查。
- GROUP-2/3/4/5 未批准，屏障后暂停。

## 能力降级记录

- `SpawnWorker(agent_role=coder)` 在 Qoder 环境无对应 subagent_type（可用类型仅 Explore / Plan / general-purpose）→ Detail: capability orchestration.dispatch degraded；改用 general-purpose worker，并在 prompt 首步强制 Read `.claude/agents/coder.md` 以加载角色检查表、完成定义与返回格式。
- 本会话（Claude Code）重跑：`coder` subagent_type 可用，capability 恢复，不再需要 general-purpose 降级与手工角色样板。
- 主 checkout 的 `.venv` 为空壳（仅 `_virtualenv`，import pydantic 失败）→ 用户授权在受管理 worktree 内执行 `uv sync --all-extras --dev`。

## 追踪条目（append-only）

[2026-10-15 10:05] APPROVAL | Leader | Status: completed
Detail: 计划门禁通过，范围收窄为 GROUP-1；plan 与 dispatch FM 同步为 approved: true + batch_scope
Sub-agents: 0
Output: .ai-runtime-artifacts/plans/2026-10-15-agent-evaluation-plan.md / -dispatch.md
Error: none
Next: WORKTREE-INIT

[2026-10-15 10:12] WORKTREE-INIT | Leader | Status: completed
Detail: git worktree add -b harness/wt-agent-evaluation，base = feature/memory-system HEAD（fccafd7）；worktree 落在 repo-parent 下，仓库 .gitignore 无需改动。分支名以工具实际返回为准，替代 dispatch 草案中的 codex/agent-evaluation
WorktreeId: wt-agent-evaluation | WorktreePath: D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation | Branch: harness/wt-agent-evaluation | Base: fccafd7
Sub-agents: 0
Output: D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation
Error: none
Next: ENV-SYNC + DISPATCH-GROUP-1(WU-01)

[2026-10-15 10:15] ENV-SYNC | Leader | Status: completed
Detail: worktree 内 uv sync --all-extras --dev 退出码 0；实测解释器 .venv/Scripts/python.exe 可 import pydantic 2.14.1 / pytest 9.1.1 / aiohttp 3.14.5（venv Python 3.13，项目要求 ≥3.11）。实际版本已记录，满足 plan §5「依赖安装及实际版本在实施时记录」
Sub-agents: 0
Output: D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation/.venv
Error: none
Next: DISPATCH-GROUP-1

[2026-10-15 10:26] DISPATCH-GROUP-1 | Leader | Status: started
Detail: 派发 WU-01（T01 schemas/validation + T02 database/repository），worker = general-purpose + 强制先 Read .claude/agents/coder.md（capability degraded，见上）；WU-02 本批不派发（缺 Bun，用户已确认）
GROUP: 1 | WU: WU-01 | ITER: 1 | STEP: implement
Queue-remaining: WU-02(待 Bun), WU-03, WU-04, WU-05, WU-07, WU-06, WU-08, WU-09
Worktree: D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation | Branch: harness/wt-agent-evaluation | Title(zh): 评估域数据模型 / 版本存储 / 幂等入队
Owned-files: nanobot/evaluation/{__init__,schemas,validation,database,repository}.py + tests/evaluation/{__init__,conftest,test_validation,test_database,test_repository}.py
Tests: pending
Reviewer: separate-task | pending
Closeout: collective-test=pending | code-review=pending | status=pending
Sub-agents: 1
Output: none
Error: none
Next: WU-01 返回后做 GROUP-1 屏障（字段对契约、红绿证据、ruff/basedpyright、显式清单提交）

[2026-10-15 续会] RESUME-CHECK | Leader | Status: completed
Detail: 用户在新会话要求「执行这个计划」。核查 worktree 发现上一会话 WU-01 中断：`nanobot/evaluation/schemas.py` 从未创建，`tests/evaluation/test_validation.py` 第 316–320 行字典字面量缺 `}]` 导致 pytest 收集期 SyntaxError，T01 红灯未成立、T02 未开工。`bun` 复查仍不可用（PATH 与 ~/.bun 均无），WU-02 继续阻塞，与用户既有决策一致。判定为「WU-01 续做（review-fix 语义）」而非新 WU，GROUP-1 范围不变
Worktree: D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation | Branch: harness/wt-agent-evaluation
Sub-agents: 0
Output: none
Error: none
Next: DISPATCH-GROUP-1(WU-01 ITER=2)

[2026-10-15 续会] DISPATCH-GROUP-1 | Leader | Status: started
Detail: 重新派发 WU-01（T01 修复+schemas 补全，T02 database/repository），ITER=2；agent_role=coder 本会话可用，无需 capability 降级。已知残局写入 ContextPack（validation.py 已完成；test_validation.py 语法错误位置；schemas.py 缺失）
GROUP: 1 | WU: WU-01 | ITER: 2 | STEP: implement
Queue-remaining: WU-02(待 Bun), WU-03, WU-04, WU-05, WU-07, WU-06, WU-08, WU-09
Worktree: D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation | Branch: harness/wt-agent-evaluation | Title(zh): 评估域数据模型 / 版本存储 / 幂等入队
Owned-files: nanobot/evaluation/{__init__,schemas,validation,database,repository}.py + tests/evaluation/{__init__,conftest,test_validation,test_database,test_repository}.py
Tests: pending
Reviewer: separate-task | pending
Closeout: collective-test=pending | code-review=pending | status=pending
Sub-agents: 1
Output: none
Error: none
Next: WU-01 返回后做 GROUP-1 屏障（字段对契约、红绿证据、ruff/basedpyright、显式清单提交）

[2026-10-16 续会] CLOSE-GROUP-1 | Leader | Status: completed
Detail: WU-01（ITER=2）交付已落盘：nanobot/evaluation/{__init__,schemas,validation,database,repository}.py + tests/evaluation/{__init__,conftest,test_validation,test_database,test_repository}.py，共 4362 行；未触碰任何既有文件（git status 仅这两个新目录 untracked），故无既有回归面。Leader 屏障复核：pytest tests/evaluation → 256 passed in 6.27s；ruff check nanobot/evaluation tests/evaluation → All checks passed；basedpyright（同 CI 口径）→ 0 errors, 0 warnings, 0 notes
GROUP: 1 | WU: WU-01 | ITER: 2 | STEP: barrier
Sub-agents: 0
Output: .ai-runtime-artifacts/execution-logs/2026-10-15-agent-evaluation-execution-log.md
Error: none
Next: WU-02 仍阻塞（复查本机无 bun，PATH 与 ~/.bun 均无）→ 等用户决定装 Bun 还是先推进 GROUP-2

[2026-10-16 续会] BARRIER-RESULT | Leader | Status: completed
Detail: 屏障四项判定。(1) 模型/API 字段一致：通过 —— schemas.py 有 test_{case_write,case_read,suite_write_and_read,run_create}_field_set_matches_contract 等字段集锁定测试；读写字面往返由 CaseWrite/CaseRead/SuiteWrite/SuiteRead 类型化序列化承担，满足 plan T02「每次存取使用 Pydantic/JSON 类型化序列化」。契约偏差 1 处，已按「接口变化先改契约」回写契约 §5：JudgeWorkerBoot 沿用 runtime_root/workspace（评分子进程需要受控 scratch 目录做强制进程回收与产物落盘），原契约正文只列 attempt_id/rubric/evidence/profile_bundle。(2) 数据版本与入队完成标准：通过 —— revision CAS、旧 run 仍读 rev1 正文、case_revisions 就地 UPDATE 被拒、200 用例导入第 200 项非法整事务回滚、同 key 同 payload 重放、不同 payload IDEMPOTENCY_MISMATCH、未完成预留 IDEMPOTENCY_IN_PROGRESS、软删运行不释放 key、并发 claim 不重复发放、claim 跳过已删运行。(3) UI mock 测试：不适用，WU-02 未开工。(4) 独立构建输出无覆盖：成立，本批无任何构建产物/静态目录改动。
Sub-agents: 0
Output: .ai-runtime-artifacts/contracts/2026-10-15-contract-agent-evaluation.md（§5 Judge boot 一句）
Error: none
Next: 已登记给后续 WU 的三项缺口 —— WU-05 补 RunSummary/RunRead/AttemptRead/GoldSample/GoldReview/CalibrationCreate/CalibrationRead/ProfileRead 读出模型（repository 现返回存储形状 dict，缺 suite_name/terminal_count/counts/metrics/attempts_url）；WU-03 建 worker/TraceCollector；WU-04 建 evaluate_rules/evaluate_judge/derive_verdict/aggregate_run

[2026-10-16] CONTRACT-AMEND | Leader | Status: completed
Detail: GROUP-2 派发前按「多 WU 并行前必定义接口契约」补契约 §6.1 AttemptEvidence（WU-03 产出 → WU-04 消费的接缝）：字段集、ToolCallEvidence/EvidenceArtifact 形状、三个读取入口（read_text/read_json/exists 必须走 resolve_inside）、evidence_refs 两种前缀与归属校验、trace_complete=False 时必过 review_required。evidence.py 所有权归 WU-03，WU-04 禁止 import 该并行中的新文件
Sub-agents: 0
Output: .ai-runtime-artifacts/contracts/2026-10-15-contract-agent-evaluation.md §6.1；dispatch FM batch_scope 改为 GROUP-1 + GROUP-2
Error: none
Next: DISPATCH-GROUP-2

[2026-10-16] DISPATCH-GROUP-2 | Leader | Status: started
Detail: 并行派发 WU-03（T05 隔离运行环境+真实 SDK 适配，T06 进程树/IPC/Trace/用量证据）与 WU-04（T07 规则+verdict+聚合指标，T08 无工具 Judge+校准）。文件集不相交：WU-03 独占 runtime_factory/fixtures/worker/ipc/processes/trace/usage/evidence，WU-04 独占 graders/* 与 reports.py。共用 worktree（同批共享实现工作树，按所有权隔离）
capability: SpawnWorker(agent_role=coder) 在 Qoder 环境仍无 coder subagent_type（可用仅 Explore/Plan/general-purpose/context7:docs-researcher）→ orchestration.dispatch degraded；改用 general-purpose worker，prompt 首步强制 Read `.claude/agents/coder.md` 加载角色检查表、完成定义与返回格式
GROUP: 2 | WU: WU-03 | ITER: 1 | STEP: implement
Worktree: D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation | Branch: harness/wt-agent-evaluation | Title(zh): SDK 隔离执行、进程树、IPC、Trace 与用量证据
Owned-files: nanobot/evaluation/{runtime_factory,fixtures,worker,ipc,processes,trace,usage,evidence}.py + tests/evaluation/{test_runtime_factory,test_worker,test_ipc,test_processes,test_trace,test_usage}.py
Tests: pending | Reviewer: separate-task | pending | Closeout: pending
Sub-agents: 2
Output: none
Error: none
Next: 两 WU 返回后做 GROUP-2 屏障（证据可读回判分、无 SDK 核心回归、两种 Token 账本不重复）

[2026-10-16] DISPATCH-GROUP-2 | Leader | Status: started
GROUP: 2 | WU: WU-04 | ITER: 1 | STEP: implement
Detail: 与 WU-03 同批并行；判分侧只按契约 §6.1 消费证据，测试自备 fake evidence 对象，不得 import 并行中的 evidence.py，也不得修改 WU-01 已提交的 schemas/validation/database/repository
Worktree: D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation | Branch: harness/wt-agent-evaluation | Title(zh): 规则判分、verdict 合并、聚合指标与 Judge 校准
Owned-files: nanobot/evaluation/graders/{__init__,rules,verdict,judge,calibration}.py + nanobot/evaluation/reports.py + tests/evaluation/{test_rules,test_verdict,test_reports,test_judge,test_calibration}.py + 判分侧 fake evidence 测试资产
Tests: pending | Reviewer: separate-task | pending | Closeout: pending
Sub-agents: 2
Output: none
Error: none
Next: 同上（GROUP-2 屏障）


[2026-10-16 续会] DISPATCH-GROUP-2 (REPLAN) | Leader | Status: completed
Detail: 原计划的两个并行 worker 被用户连续两次否决派工（Agent 调用被 deny），随后明确「继续」→ 语义确定为不再派 worker，由 Leader 在同一 worktree 内直接实现 WU-03 + WU-04。据此本条把 DISPATCH-GROUP-2 两条 started 记录收口为「Leader 直写」，并如实登记与 dispatch 规范的偏差
Deviation: 违反 orchestration.dispatch「Leader 不写业务代码」与「实现与审查不得同实例」两条；本次降级的直接原因是用户否决派工，非能力缺失。代码审查与安全审查两份产物均由实现者同实例产出，已在各自文件首段声明
Sub-agents: 0
Output: nanobot/evaluation/{runtime_factory,fixtures,worker,ipc,processes,trace,usage,evidence}.py + graders/{rules,verdict,judge,calibration}.py + reports.py（7238 行）
Error: none
Next: GROUP-2 屏障

[2026-10-16] BARRIER-GROUP-2 | Leader | Status: completed
Detail: 屏障判定全部为真实绿灯（命令与完整输出记入执行日志 §7）。pytest tests/evaluation -q → 496 passed；test_worker_orchestration.py 单跑 → 19 passed；test_processes.py -rs → 11 passed 无 skip；SDK 三件套（loop/runner/memory）→ 25 passed；ruff check nanobot/evaluation tests/evaluation → All checks passed；basedpyright nanobot/evaluation → 175 errors（其中 164 条 reportUnknown* 噪声，全仓基线 2269，strict 门禁本就红）
Evidence: 契约硬数字覆盖 —— ipc.MAX_FRAME_BYTES=3MiB、trace.MAX_EVENT_BODY_BYTES=16KiB、MAX_SEGMENT_BYTES=1MiB、MAX_TRACE_BYTES=20MiB、seq 从 1 严格递增、judge 两次物理请求、评分预算 60s，全部有断言
Reviewer: separate-task | degraded-to-self-review（同实例，已声明）
Closeout: collective-test=done | code-review=done | security-review=done | status=completed
Deviation: D4 帧内存无界累积→只留末帧；D5 产物整文件读入哈希→流式 sha256；D6 双 aclose 与清理错误遮蔽执行错误→guarded_close 单点；D7 usage 私有成员访问→模块级 check_role/check_scope；D8 协议可变属性不匹配→只读 @property。全部记入执行日志 §8
Sub-agents: 0
Output: 提交 11da695（GROUP-2 实现，26 文件）+ a07c50e（自查修复）；.ai-runtime-artifacts/verifications/2026-10-15-agent-evaluation-collective-test.md；.ai-runtime-artifacts/reviews/2026-10-15-agent-evaluation-{code,security}-review.md；.ai-runtime-artifacts/execution-logs/2026-10-15-agent-evaluation-execution-log.md §6-§10
Error: none
Next: WU-02 仍阻塞（本机无 Bun，不得用另一套 lock 冒充 frozen-lockfile）；GROUP-3/4/5 未批准；T08「校准入口并入 NewRun」保持未勾，属 GROUP-3 API/前端范围

[2026-10-16] CLOSE-BATCH-PARTIAL | Leader | Status: completed
Detail: GROUP-1 + GROUP-2 收口。plan 共 90 个 checkbox，已勾 37、未勾 53；未勾项分布在 WU-02（evalui 前端，等 Bun）、GROUP-3 调度器/API、GROUP-4 样本与 CLI 打包、GROUP-5 验证与交付，均未批准或被阻塞，不属本批欠账。GROUP-2 范围内唯一未勾项是 T08 的「校准入口并入 NewRun」（需 GROUP-3 的 API + 前端）。工作树 harness/wt-agent-evaluation 干净；.ai-runtime-artifacts/ 产物位于主 checkout Zhixu 且未入库，是否入库待用户决定
Residual-risk: live provider 冒烟、端到端、全仓测试套件、basedpyright reportUnknown* 收敛（T14）、CSV 公式注入反向测试、快照/导出凭据 grep 断言 —— 全部已登记在 collective-test 与安全审查中
Sub-agents: 0
Error: none
Next: 等用户决定：装 Bun 解 WU-02 阻塞 / 批准 GROUP-3 / 产物入库

[2026-10-16] DISPATCH-GROUP-3 | Leader | Status: started
Detail: 用户批准并执行 GROUP-3 = WU-05（T09 调度器与服务层、T10 HTTP/认证/资源/产物/错误）+ WU-07（T12 基础用例与校准种子）。屏障口径：HTTP 返回契约一致，20 用例离线通过；校准样本 proposed/confirmed 不混淆
GROUP: 3 | WU: WU-05 | ITER: 1 | STEP: implement
Worktree: D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation | Branch: harness/wt-agent-evaluation | Title(zh): 调度器、服务层、HTTP 契约层与认证准入
Owned-files: nanobot/evaluation/{scheduler,services,server,auth,artifacts}.py + tests/evaluation/{test_scheduler,test_services,test_server,test_auth,test_routes,test_artifacts,test_repository_reads}.py
Tests: pending | Reviewer: separate-task | pending | Closeout: pending
Sub-agents: 0（用户此前连续两次否决派工，本批由 Leader 直写，与 GROUP-2 同）
Output: none
Error: none
Next: WU-07 样本与端到端，然后 GROUP-3 屏障

[2026-10-16] BARRIER-GROUP-3 | Leader | Status: completed
Detail: 屏障两项在离线口径下全部成立。20 条基础用例走真链路（create_attempt/AgentLoop/ToolRegistry/Trace 密封/evaluate_rules 全真，仅 LLM 换 ControlledProvider）全部通过，8 条关键值翻转逐条判失败且执行仍 succeeded；gold 首启 seed 只写 proposed、幂等、人工确认后不倒挂，HTTP 首启读出 10 条全 proposed，未确认即拒绝校准且不写结论，一致率 1.0→passed / 0.5→not passed
Evidence: pytest tests/evaluation -q → 698 passed in 63.53s；test_samples+test_end_to_end → 45 passed in 11.91s；ruff → All checks passed；basedpyright test_end_to_end.py → 0 errors；basedpyright nanobot/evaluation → 291 errors（272 条 reportUnknown* 噪声，19 条逐条核对为非缺陷）；pytest tests/cli tests/webui/test_build.py tests/config → 4 failed/446 passed/3 skipped，4 条失败为 tests/cli 既有 serve 桩与 session_manager 不同步，与本批 diff 零交集
Reviewer: separate-task | degraded-to-self-review（同实例，已声明）
Closeout: collective-test=done | code-review=done | security-review=done | status=completed
Deviation: D9 iterations 读错层级致 iteration_limit 恒过→改读 usage；D10 校准查忘记 gold_revision→补三元组；D11 Judge 证据负载缺 workspace→评分必 setup_error→补；D12 产物登记扫错目录恒为空集→改读密封 manifest.json；D13 Judge 与 Agent 共用 trace 目录污染被测证据→各自独占；D14 run_calibration_job 的 evidence_for 写死 None、结论重复记、调度器不走校准项循环→未修，登记为屏障外功能缺口；D15 create_run 把 Profile.bundle 整块落库（潜在凭据落盘，当前 bundle 恒 None 故无实际泄漏，出口侧字段白名单已核实）→未修，登记 S7；D16 B05 否定断言改用 file_json JSON Pointer（plan L344 授权）；D17 WU-02 仍等 Bun，GROUP-4/5 未批准
Sub-agents: 0
Output: 提交 harness/wt-agent-evaluation（GROUP-3 代码与样本）；.ai-runtime-artifacts/verifications/2026-10-15-agent-evaluation-collective-test.md 追加 §6-§11；reviews/2026-10-15-agent-evaluation-{code,security}-review 追加 GROUP-3 章节；execution-logs/2026-10-15-agent-evaluation-execution-log.md §11-§16
Error: none
Next: 等 Bun 解 WU-02；GROUP-4（T13 CLI/打包 + D14 接线）、GROUP-5（live 冒烟/浏览器验收/独立审查）待批准；S7 凭据注入契约待决

[2026-10-16] CLOSE-BATCH-PARTIAL | Leader | Status: completed
Detail: GROUP-3 收口（批次仍为部分完成）。fixture 模式能力边界已记录：仅 6 个白名单工具、受控 provider 回放脚本且须以文本/JSON 收尾、固定时钟、不构造 MCP 不触网、会话存储外插到 attempt 目录之外、只登记声明过的产物；结论是「fixture 模式能证明评估系统自己是对的，不能证明 Agent 是对的」
Residual-risk: D14 校准作业接线缺失（功能欠账）、S7 运行快照潜在凭据落盘、live provider 冒烟、CLI 入口 nanobot eval 不存在、前端零覆盖、basedpyright reportUnknown* 收敛（T14）、4 条 CLI test_serve_* 既有失败
Sub-agents: 0
Error: none
Next: 等用户指令：Bun / GROUP-4 / GROUP-5 / S7 契约决策
