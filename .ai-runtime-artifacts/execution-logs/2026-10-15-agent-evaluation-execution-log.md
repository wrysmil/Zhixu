# Agent 自动化评估 执行日志（批次 agent-evaluation）

> 批次：agent-evaluation
> 计划：`.ai-runtime-artifacts/plans/2026-10-15-agent-evaluation-plan.md`
> 调度：`.ai-runtime-artifacts/plans/2026-10-15-agent-evaluation-dispatch.md`
> 契约：`.ai-runtime-artifacts/contracts/2026-10-15-contract-agent-evaluation.md`
> Spec：`.ai-runtime-artifacts/specs/2026-10-15-agent-evaluation-spec.md`
> Stack：`.ai-runtime-artifacts/stack/2026-10-15-agent-evaluation-stack.md`
> 工作树：`D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation`（分支 `harness/wt-agent-evaluation`，base `fccafd7`）
> 本批范围：GROUP-1（WU-01）、GROUP-2（WU-03 + WU-04）、GROUP-3（WU-05 T09/T10 + WU-07 T12）；WU-02（前端）等 Bun，GROUP-4/5 未批准

## 1. WU 落地表

| WU | Task | 文件/范围 | 状态 | Leader 抽验 |
| --- | --- | --- | --- | --- |
| WU-01 | T01 输入模型、限制与路径校验 | `nanobot/evaluation/schemas.py`(661) + `validation.py`(60) + `__init__.py`(5) + `tests/evaluation/test_validation.py`(966) + `conftest.py`(239) | done | `pytest tests/evaluation` → 256 passed in 6.27s；`ruff check nanobot/evaluation tests/evaluation` → All checks passed；`basedpyright` → 0 errors 0 warnings 0 notes |
| WU-01 | T02 版本存储、不可变快照与幂等入队 | `nanobot/evaluation/database.py`(330) + `repository.py`(1074) + `tests/evaluation/test_database.py`(214) + `test_repository.py`(812) | done | 同上（同一次门禁运行覆盖 T01+T02）；`git status` 仅 `nanobot/evaluation/`、`tests/evaluation/` 两个新目录 untracked，未改任何既有文件 |
| WU-02 | T03/T04 独立评估页面与契约客户端 | `evalui/`（未创建） | blocked | 本机无 `bun`（PATH 与 `~/.bun` 均无），无法执行 `bun install --frozen-lockfile` / typecheck / vitest / build；不允许用另一套 lock 冒充 frozen 检查 |

## 2. GROUP-1 屏障判定

| 屏障项 | 结论 | 证据 |
| --- | --- | --- |
| 模型/API 字段一致 | 通过（1 处偏差已回写契约） | `test_case_write_field_set_matches_contract`、`test_case_read_field_set_matches_contract`、`test_suite_write_and_read_field_sets_match_contract`、`test_run_create_field_set_matches_contract` 逐字段锁定；读写往返 `test_serialised_case_round_trips_back_to_identical_model` |
| 数据版本测试通过 | 通过 | revision CAS / 快照不可变 / 幂等 / 原子领取 / 导入回滚 / 路径与 schema 限制，见 §3 |
| UI mock 测试通过 | 不适用 | WU-02 未开工（缺 Bun） |
| 独立构建输出无覆盖 | 成立 | 本批无 `nanobot/evaluation/static`、`webui/dist` 等构建目录改动 |

## 3. 完成标准对应的用例（plan §3 WU-01 行）

| 完成标准 | 用例 |
| --- | --- |
| 版本 CAS | `test_stale_base_revision_is_rejected`、`test_update_suite_rejects_stale_base_revision`、`test_delete_suite_rejects_stale_base_revision`、`test_save_case_without_case_id_requires_null_base_revision` |
| 快照不变 | `test_run_freezes_case_body_before_a_later_edit`、`test_run_snapshot_contains_the_whole_case_definition`、`test_case_revision_body_is_immutable_even_via_repository`、`test_case_revisions_cannot_be_updated_in_place`（绕开仓储直改也被拒） |
| 幂等 | `test_request_digest_ignores_key_order`、`test_same_key_same_payload_replays_original_run`、`test_same_key_different_payload_is_a_mismatch`、`test_incomplete_reservation_reports_in_progress`、`test_soft_deleting_a_run_does_not_release_the_key`、`test_different_scope_may_reuse_the_same_key`、`test_idempotency_key_is_unique_per_scope_and_endpoint` |
| 原子领取 | `test_claim_returns_queued_attempt_in_position_order`、`test_concurrent_claim_never_hands_out_the_same_attempt`、`test_claim_skips_attempts_of_a_deleted_run`、`test_finish_attempt_requires_a_claimable_state`、`test_cancel_queued_run_stops_further_claims` |
| 导入回滚 | `test_import_rolls_back_when_a_later_case_is_invalid`、`test_import_accepts_two_hundred_valid_cases`、`test_import_ignores_incoming_case_ids`、`test_repeated_sample_import_returns_the_original_suite` |
| 路径与 schema 限制 | `test_path_cannot_escape*`、`test_absolute_drive_and_unc_paths_are_rejected`、`test_backslash_paths_are_rejected`、`test_junction_pointing_outside_root_is_rejected`、`test_schema_subset_{accepts_maximum_nesting,rejects_nesting_beyond_depth_limit,accepts_exactly_one_thousand_nodes,rejects_more_than_one_thousand_nodes,rejects_reference_and_code_keywords,rejects_unknown_type}` |

## 4. 偏差与处理

| # | WU | 偏差 | 处置 |
| --- | --- | --- | --- |
| D1 | WU-01 | `JudgeWorkerBoot` 比契约 §5 正文多要求 `runtime_root`/`workspace`（评分子进程需要受控 scratch 目录做强制进程回收与产物落盘） | 按「接口变化先改契约」回写契约 §5 一句话；实现保持现状，WU-03 构造 Judge boot 时必须带这两个字段 |
| D2 | WU-01 | `schemas.py` 未定义 `RunSummary/RunRead/AttemptRead/GoldSample/GoldReview/CalibrationCreate/CalibrationRead/ProfileRead`，`repository` 对应方法返回存储形状 dict（缺 `suite_name`、`terminal_count`、`counts`、`metrics`、`attempts_url`） | 属 WU-05（HTTP/资源）与 WU-04（`aggregate_run`）所有；plan T01 只要求 9 个模型，WU-01 不自扩范围。已登记为交接缺口，禁止后续 WU 绕过契约自行放宽 |
| D3 | 批次 | WU-02 缺 Bun 工具链 | 保持阻塞；GROUP-1 屏障以「UI mock 测试不适用」判定，不声称前端已交付 |

## 5. 红绿证据口径（如实记录）

- T01 的首轮红灯有落盘证据：ITER=1 中断时 `schemas.py` 从未创建、`tests/evaluation/test_validation.py` 第 316–320 行字典字面量缺 `}]` 导致 pytest 收集期 `SyntaxError`（见 DISPATCH-TRACK 的 RESUME-CHECK 条目）。
- 本会话 Leader 复核到的是**全绿**（256 passed）；worker 在 ITER=2 期间的逐项红灯输出未落盘，因此不声称「每个 checkbox 各自跑过红灯」。按 plan §4 的口径，这一条记为证据缺口，若要求补齐需对单个 checkbox 做定向红绿复现。
- 未跑全仓测试套件：WU-01 只新增两个目录、零改动既有文件，回归面为空；`nanobot/evaluation` 尚未被任何运行入口 import。

## 6. GROUP-2（WU-03 + WU-04）落地

派发方式变更（如实记录）：用户在本会话否决了 worker 派工（两次 `Agent` 调用被 deny），随后指示「继续」+「完成屏障与产物落盘后自动收尾，不再等待确认」。因此 GROUP-2 由 Leader 直接在受管理 worktree 内实现，**本 GROUP 无任何 sub-agent**；dispatch 的「实现与审查不得同实例」约束无法由派工满足，改为把审查降级成独立落盘产物（见 §9），不假装已做独立审查。

| WU | Task | 文件（行数） | 状态 |
| --- | --- | --- | --- |
| WU-03 | T05 隔离运行环境与真实 SDK 适配 | `runtime_factory.py`(327) + `fixtures.py`(182) + `tests/evaluation/test_runtime_factory.py`(423) | done |
| WU-03 | T06 进程树、IPC、Trace 与用量证据 | `ipc.py`(215) + `processes.py`(521) + `trace.py`(465) + `usage.py`(275) + `evidence.py`(278) + `worker.py`(935) + `test_ipc.py`(280) + `test_processes.py`(260) + `test_trace.py`(241) + `test_usage.py`(350) + `test_worker.py`(326) + `test_worker_orchestration.py`(987) | done |
| WU-04 | T07 规则、verdict 与聚合指标 | `graders/rules.py`(545) + `graders/verdict.py`(127) + `reports.py`(473) + `test_rules.py`(788) + `test_verdict.py`(231) + `test_reports.py`(478) | done |
| WU-04 | T08 无工具 Judge 与校准 | `graders/judge.py`(419) + `graders/calibration.py`(329) + `graders/__init__.py`(7) + `test_judge.py`(501) + `test_calibration.py`(732) | done（库侧）；T08「校准入口并入 NewRun」一项属 UI/HTTP，随 WU-02/WU-05 后置，plan checkbox 保持未勾 |

提交：`11da695 feat(evaluation): 隔离运行环境、Worker 编排与判分链路（WU-03 + WU-04 / T05-T08）`，显式 26 个文件清单，未修改任何既有文件（`git status` 在提交前仅这两批新文件 untracked）。GROUP-2 合计新增 10695 行（实现 5098 / 测试 5597）。

## 7. GROUP-2 屏障判定

门禁命令与输出（全部在 worktree 内用 `.venv/Scripts/python.exe` 实跑）：

- `pytest tests/evaluation -q` → **496 passed in 15.84s**（GROUP-1 256 + GROUP-2 240）。
- `pytest tests/evaluation/test_worker_orchestration.py -q` → **19 passed in 0.86s**。
- `pytest tests/evaluation/test_processes.py -q -rs` → **11 passed，无 skip**（Windows Job Object 真实回收孙进程，满足 plan T06「不得 skip 后宣称回收已验证」）。
- `pytest tests/test_sdk_streaming.py tests/agent/test_loop_runner_integration.py tests/providers/test_llm_usage_observer.py -q` → **25 passed**（接入回归，无核心行为改动）。
- `ruff check nanobot/evaluation tests/evaluation` → **All checks passed**。
- `basedpyright nanobot/evaluation`（CI 同口径 strict）→ **175 errors**；见 D7 口径说明。

屏障四项：

1. **证据可被判分读取**：通过。`test_sealed_trace_feeds_rules_and_final_verdict` 用 worker 真实密封出的 Trace 目录走 `read_trace` → `build_attempt_evidence` → `evaluate_rules` → `derive_verdict`，得到 `passed`，并按 `trace:<attempt_id>:<seq>` 真实解析到工具事件。转发帧与磁盘事件的 `seq`/`event_id` 一一对应（`TraceCollector` 是唯一编号者，触顶即不转发）。
2. **两种 Token 账本不重复**：通过。agent 与 judge 各自 `UsageLedger` 分桶；judge 用例断言 `model.call_completed` 事件的 role 集合恰为 `{"judge"}`、两次物理请求只进 judge 账本（total 20）；agent 用例断言 2 次物理调用 → total 24 且 `iterations=2`，`round_usages` 不参与数值。
3. **无 SDK 核心回归**：通过（上述 25 passed；本批对 `nanobot/` 非 evaluation 目录零改动）。
4. **完成标准（dispatch §3 WU-03/WU-04 行）**：通过 —— 两用例全数据隔离与凭据不落盘（`test_runtime_factory` 全目录扫描 + worker 帧/盘双侧扫描）、资源回收（owner 关闭回收后代）、证据完整性（`trace_complete` 触顶/写坏即 false）、物理调用账本、规则正反例、Judge 重试上限与证据引用拒收、量表指纹失效、指标黄金组合（planned=5 → overall 0.4 / evaluated 2/3 / coverage 0.6；`[10,20,30,40]` median 25、P95 40）、CSV 公式注入前缀脱敏。

## 8. 偏差与处理（GROUP-2 新增）

| # | WU | 偏差 | 处置 |
| --- | --- | --- | --- |
| D4 | WU-03 | plan T05 写「采用 `async with bot` 保证 aclose」，但 worker 还需要 `finally` 落终态。两者并存导致 SDK 关闭两次，且 `__aexit__` 抛错会**覆盖**原始执行错误（违反 dispatch §4「不得用汇总错误覆盖原始错误」） | 改为 `guarded_close()` 异步上下文管理器：`finally` 内自捕获清理异常并追加 `phase="cleanup"` 错误，关闭只走 `runtime.aclose()` 一处；异常处理排在 `guarded_close` 内侧，使 errors 顺序等于发生顺序。用例 `test_cleanup_failure_is_kept_alongside_execution_error` 锁住该顺序 |
| D5 | WU-03 | 契约 §5 只明确「管道里不得多写敏感串」，原实现的脱敏只覆盖 IPC 帧，Trace 落盘仍会写入凭据原文 | 强化为落盘前脱敏：`redact_json()` 抽成一份实现，`TraceRecorder.emit` 先脱敏再交给 `TraceCollector`。用例把密钥同时放进 turn 文本与工具结果，断言帧、磁盘、result 三处都只有 `***` |
| D6 | WU-03 | `JudgeWorkerBoot` 无 `calibrated` 字段（契约 `extra="forbid"`，不允许自行加字段） | Worker 侧固定 `calibrated=False`，真实校准状态由 WU-05 服务侧从 calibration store 合并后进 verdict；已在 `run_judge_worker` 注释与代码口径写明，避免下游误把 Worker 输出当最终结论 |
| D7 | WU-03/04 | `basedpyright nanobot/evaluation` 报 175 errors，其中 164 条是 `reportUnknown*` 家族（`Mapping[str, Any]` 与递归 `JsonValue` 的逐层 `.get()`）；GROUP-1 当时为 0 是因为那几个模块几乎不做任意 JSON 遍历 | 全仓 strict 基线本就是红的（`basedpyright` 整仓 2269 errors），这批不谎称达标。本轮已把**真**类型缺陷清零：`AttemptEvidenceView` 三个协议成员改成只读 `@property`（可变属性协议会把只读实现判成不匹配）、`_emit` 改 `TypeVar` 保留具体结果类型、`guarded_close` 返回 `AsyncGenerator`、`_source()` 返回 `Literal`、校准缺分不再拿 `None` 比大小、`_median/_p95_nearest_rank` 收 `Sequence[float]`。剩余 `reportUnknown*` 记为 T14 尾盘统一收敛项（要么加 JSON 值访问器、要么按仓库既有做法局部 `pyright: ignore`），不在本批悄悄扩面 |
| D8 | 批次 | WU-02 仍缺 Bun；T08 的「校准入口并入 NewRun」依赖 WU-02/WU-05 | 保持阻塞并保留 plan checkbox 未勾；不用另一套 lock 冒充 frozen-lockfile 检查 |

## 9. 红绿证据口径（GROUP-2 如实记录）

- `worker.py` 与 `test_worker_orchestration.py` 是 Leader 在同一会话内**先实现后补测试**，不是逐 checkbox 红灯先行；首轮 19 条用例即 10 failed（`LLMUsage` 的 `reported+estimated==total` 不变式、协议只读性、清理顺序），修完实现与桩后置绿。这一段红灯是「实现与真实 SDK 契约不符」暴露出来的，但不是 TDD 意义上的失败测试先行，记为证据缺口。
- GROUP-2 其余模块（`evidence.py`、`graders/*`、`reports.py`、`trace/usage/ipc/processes`）在上一段会话按红灯先行推进，逐项红灯输出未逐条落盘。
- 未跑全仓 `pytest`：本批对 `nanobot/` 非 evaluation 目录零改动，且 SDK 接入面已用定向回归（25 passed）覆盖；全仓套件留到 T14「现有入口回归」执行并记录真实耗时。
- 尾盘产物：`.ai-runtime-artifacts/verifications/2026-10-15-agent-evaluation-collective-test.md`、`.ai-runtime-artifacts/reviews/2026-10-15-agent-evaluation-code-review.md`、`.ai-runtime-artifacts/reviews/2026-10-15-agent-evaluation-security-review.md`。因本 GROUP 实现与审查同实例，两份 review 明确标注为 Leader 自查、不构成 dispatch 要求的独立审查，独立审查需由后续会话的另一个实例重做。

## 10. 下一步（等用户指令）

1. 装 Bun（或指定已有 Bun 路径）→ 补派 WU-02（T03/T04），把 GROUP-1 的 UI 屏障补齐。
2. 批准 GROUP-3（WU-05：调度器/HTTP/认证/产物）→ 消费本批交付接口：`read_boot_line`/`run_worker`/`WorkerChannel`/`ProcessOwner`/`evaluate_rules`/`evaluate_judge`/`derive_verdict`/`aggregate_run`，并把 D6 的 `calibrated` 合并进 verdict 口径。
3. WU-07 样本（20 用例 + 10 gold）可在 GROUP-3 之后并行准备，但 gold 标签必须留 `proposed`，等人工确认。
4. 主 checkout 的 `.ai-runtime-artifacts/` 全部产物仍未纳入 git（本批未擅自提交过程产物），是否入库等用户决定。

---

# GROUP-3 追加（WU-05 T09/T10 + WU-07 T12）

> 批准口径：用户「批准并执行 GROUP-3」+「继续把」。范围＝WU-05（T09 调度器与服务层、T10 HTTP/认证/资源/产物/错误）与 WU-07（T12 基础用例与校准种子）。
> 屏障口径：**HTTP 返回契约一致，20 用例离线通过；校准样本 proposed/confirmed 不混淆。**
> 实施方式：Leader 在同一 worktree 直写（用户此前连续两次否决子 Agent 派工，本批未再派工）。

## 11. GROUP-3 落地表

| WU | Task | 文件/范围 | 状态 | Leader 抽验 |
| --- | --- | --- | --- | --- |
| WU-05 | T09 调度器与服务层 | `scheduler.py`(438) + `services.py`(993) + `test_scheduler.py`(516) + `test_services.py`(356) + `test_repository_reads.py`(392) | done | 见 §12 门禁输出 |
| WU-05 | T10 HTTP、认证、资源与产物接口 | `server.py`(633) + `auth.py`(191) + `artifacts.py`(91) + `test_server.py`(382) + `test_auth.py`(254) + `test_routes.py`(379) + `test_artifacts.py`(251) | done | 同上；产物按 id + `attachment` 下载，Host/Origin/CSRF 组合有负向用例 |
| WU-07 | T12 基础用例与校准种子 | `samples/basic-v1.json`(629) + `samples/calibration-v1.json`(638) + `test_samples.py`(288) + `test_end_to_end.py`(513) | done | `test_samples` + `test_end_to_end` → 45 passed；20 用例真链路全绿、8 条翻转全红 |

GROUP-3 合计：实现 2346 行 + 样本 1267 行 + 测试 3108 行。

## 12. GROUP-3 屏障判定

门禁命令与真实输出（worktree 内 `.venv/Scripts/python.exe`，Python 3.13.3 / pydantic 2.14.1 / pytest 9.1.1 / aiohttp 3.14.5）：

- `pytest tests/evaluation -q` → **698 passed, 63 warnings in 63.53s**（GROUP-2 496 + 本批 202）。
- `pytest tests/evaluation/test_samples.py tests/evaluation/test_end_to_end.py -q` → **45 passed in 11.91s**。
- `pytest tests/evaluation/test_end_to_end.py -q` → **33 passed in 12.17s**（含 20 用例全链路 + 8 翻转 + 5 条 gold/校准）。
- `ruff check tests/evaluation/ nanobot/evaluation/` → **All checks passed!**
- `basedpyright tests/evaluation/test_end_to_end.py` → **0 errors**（新文件 strict 全绿）。
- `basedpyright nanobot/evaluation` → **291 errors**（272 条 `reportUnknown*` 家族 + 19 条逐条核对为非缺陷，见 collective-test §9.2）。
- `pytest tests/cli tests/webui/test_build.py tests/config -q` → **4 failed, 446 passed, 3 skipped**；4 条失败全在 `tests/cli/test_commands.py::test_serve_*`，与本批 diff 零交集（既有失败）。

屏障两项：

1. **20 用例离线通过**：通过。`test_end_to_end.py` 执行段零 monkeypatch——真 `create_attempt`/`AgentLoop`/`ToolRegistry`/受控工具/Trace 密封/`evaluate_rules`，只有 LLM 换成 `ControlledProvider`。20 条用例覆盖契约 §2 全部 10 种断言；8 条关键值翻转逐条断言指定断言 `passed is False` 且执行仍 `succeeded`。
2. **校准样本 proposed/confirmed 不混淆**：通过。首启 seed 只写 `proposed`/`reviewer=null`；二次 seed 幂等返回 0；人工确认后 seed 仍返回 0 且状态不倒挂；HTTP 真起 `TestServer` 读出 10 条全 `proposed` 且不泄露 `workspace`；未确认即拒绝校准且不写 `calibration_results`；一致率 1.0 → `passed=True`、0.5 → `passed=False`，同指纹可查回。

## 13. 偏差与处理（GROUP-3 新增）

| # | WU | 偏差 | 处置 |
| --- | --- | --- | --- |
| D9 | WU-05 | 服务层从 worker 结果帧取 `iterations` 时读了顶层，实际字段在 `usage` 下 → `iteration_limit` 断言恒过 | 改为 `_iterations_of()` 从 `result["usage"]["iterations"]` 取（含 bool 排除）；B20 正反用例双向钉死 |
| D10 | WU-05 | `_find_calibration` 漏传 `gold_revision` → 人工改完 gold 后旧校准结论仍被采信 | 补 `gold_revision=repository.gold_revision()`，指纹判定升为三元组 |
| D11 | WU-05 | Judge boot 证据负载缺 `workspace` → 服务层评分必 `setup_error`（`evidence_from_payload` 按契约拒收） | 补 `workspace`，指向被测 attempt 的工作区；judge 自己的目录只做进程隔离 |
| D12 | WU-05 | 原 `_artifacts` 扫 `artifact_dir` 目录，而产物实际在 workspace → 登记恒为空集，所有 `file_*` 断言会走「未登记」判失败 | 改为只读 Worker 密封的 `manifest.json`，且必须 `kind=="workspace"` + `resolve_inside` + 工作区包含判定 |
| D13 | WU-05 | Judge 与 Agent 共用 runtime/trace/artifact 目录 → 评分事件污染已密封的被测 Trace，judge 自身 seal 也失败 | 每 attempt 独占目录，judge 用 `<attempt>-judge` 第二套；会话存储因 Windows 260 上限外插并显式注入 `SessionManager` |
| D14 | WU-05 | `run_calibration_job` 的 `evidence_for` 写死 `lambda row: None`，且 `record_calibration_result` 被记两次，调度器不走校准项循环 | **未修，登记为屏障外功能缺口**（见 §14 与 code-review C6）。判分与门禁本身已被离线用例证明可用，缺的是 HTTP 触发的作业编排 |
| D15 | WU-05 | `create_run` 把 `Profile.bundle` 整块写进 `runs.snapshot_json`，理论上可把 `apiKey` 落进 SQLite（与「凭据不进数据库快照」冲突） | **未修，登记为中危潜在缺口**（安全审查 S7）。当前所有生产 profile 的 `bundle` 恒为 `None` 故无实际泄漏；API 读出与导出均为字段白名单，出口侧已核实安全。处置需先定「凭据注入」契约，不在本批擅自扩面 |
| D16 | WU-07 | plan T12 要求「翻转关键值必须失败」，但 B05 原本想用「不含 old」的否定断言，而 `file_contains` 无否定形态 | 按 plan L344 授权改用 `file_json` 的 JSON Pointer equals 断言，fixture 改为 JSON 文档，不新增契约外的断言种类 |
| D17 | 批次 | WU-02 仍缺 Bun；T13（CLI/打包）、GROUP-5（浏览器验收）未批准 | 保持阻塞与未批准状态，不冒充完成；`nanobot eval` 入口当前不存在，属预期 |

## 14. T12 收口：fixture 模式能力边界（详见 collective-test §10）

一句话口径：**fixture 模式能证明「评估系统自己是对的」，不能证明「Agent 是对的」。** 边界包括：仅 6 个白名单工具；受控 provider 只回放脚本（脚本须以文本/JSON 收尾，用尽后重复末步）；固定时钟；不构造 MCP、不触网；会话存储外插到 attempt 目录之外；只登记声明过的产物。

## 15. GROUP-3 红绿证据口径

- `test_end_to_end.py` 首轮即绿（实现先于测试补齐），与 GROUP-2 §9 同性质的 TDD 证据缺口，已如实记录，未美化。
- `test_samples.py` 12 条亦为一次对齐即绿。
- CLI 4 条 `test_serve_*` 失败为分支既有状态，本批未触碰 `nanobot/cli` 与 `tests/cli`，故不计入本批欠账，但登记待处理。
- 尾盘产物：collective-test（GROUP-3 追加 §6–§11）、code-review（追加 §6–§10）、security-review（追加 §6–§10）、本执行日志 §11–§15、DISPATCH-TRACK 三条记录。两份 review 仍标注为 Leader 自查，不构成 dispatch 要求的独立审查。

## 16. 下一步（等用户指令）

1. 装 Bun → WU-02（T03/T04）+ T11 页面接入真实服务。
2. 批准 GROUP-4：T13 CLI/依赖锁/打包（`nanobot eval serve`）、以及补 D14 的校准作业接线。
3. 批准 GROUP-5：live provider 冒烟、浏览器验收、Windows 与独立审查。
4. 待决：S7 的凭据注入契约；4 条 CLI `test_serve_*` 既有失败的归属批次；`.ai-runtime-artifacts/` 是否入库。
