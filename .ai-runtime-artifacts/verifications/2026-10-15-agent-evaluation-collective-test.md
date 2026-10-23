# Agent 自动化评估 集体测试记录（批次 agent-evaluation）

> 范围：**GROUP-1（WU-01）、GROUP-2（WU-03 + WU-04）、GROUP-3（WU-05 T09/T10 + WU-07 T12）**。WU-02（evalui 前端）、WU-08/WU-09（CLI 与打包，GROUP-4）、GROUP-5 未开工，dispatch §5 要求的「全批集体测试」尚未成立，不得据此文件宣布批次完成。
> 工作树：`D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation`（分支 `harness/wt-agent-evaluation`，base `fccafd7`，最新提交 `268f0fe` + 本批未提交工作区改动）
> 解释器：worktree 内 `.venv/Scripts/python.exe`（Python 3.13.3，pydantic 2.14.1 / pytest 9.1.1 / aiohttp 3.14.5）
> 执行者：Leader 本人（GROUP-2/3 用户均否决派工，无 sub-agent）

## 1. 命令与真实输出

| 命令 | 输出 |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest tests/evaluation -q` | `496 passed in 15.84s` |
| `... -m pytest tests/evaluation/test_worker_orchestration.py -q` | `19 passed in 0.86s` |
| `... -m pytest tests/evaluation/test_runtime_factory.py -q` | `16 passed` |
| `... -m pytest tests/evaluation/test_processes.py -q -rs` | `11 passed`，**无 skip 报告** |
| `... -m pytest tests/test_sdk_streaming.py tests/agent/test_loop_runner_integration.py tests/providers/test_llm_usage_observer.py -q` | `25 passed` |
| `... -m ruff check nanobot/evaluation tests/evaluation` | `All checks passed!` |
| `... -m basedpyright nanobot/evaluation` | `175 errors`（164 条为 `reportUnknown*`，见执行日志 D7） |

`-rs` 用于暴露 skip 原因：`test_processes.py` 没有任何 skip，因此 Windows Job Object 的「挂起 spawn → attach → resume → 强制回收」路径与孙进程回收是在本机真实执行过的，不是以 skip 冒充验证。

## 2. 跨模块接缝合测（GROUP-1 × GROUP-2 × GROUP-2 内部）

这几条是「单模块各自绿、接缝仍可能断」的高危面，逐条有对应用例：

| 接缝 | 用例 | 结论 |
| --- | --- | --- |
| WU-03 产证据 → WU-04 消费 | `test_sealed_trace_feeds_rules_and_final_verdict` | worker 真实密封的 Trace 目录被 `read_trace` 读回，`build_attempt_evidence` 构建后 `evaluate_rules` 全通过，`derive_verdict(..., judge_state="disabled", trace_complete=True)` 判 `passed` |
| IPC 帧 seq ↔ 磁盘 seq | `test_every_forwarded_event_exists_on_disk_with_same_seq` | 转发帧与磁盘事件的 `seq`/`event_id` 列表逐项相等，首尾恰为 `attempt.started`/`attempt.completed`。父进程 `trace:<attempt>:<seq>` 引用不可能指向缺行 |
| 凭据边界 | `test_artifacts_and_frames_never_carry_the_api_key` | 密钥同时放进 turn 文本与工具结果；帧、result、磁盘 Trace 文件三处均只出现 `***`，全目录扫描无泄漏 |
| agent / judge 账本 | `test_physical_calls_are_the_only_usage_source` + `test_judge_worker_retries_once_and_only_counts_judge_usage` | agent 2 次物理调用 → 24 tokens；judge 2 次物理请求 → 20 tokens 且事件 role 集合为 `{"judge"}`，两本账不互串 |
| Worker 终态 ↔ 父进程判定 | `test_ready_then_traces_then_result_and_parent_accepts_all` | `ParentIpc` 逐帧 accept 全通过且 `can_seal_succeeded(exit_code=0)` 为真 |
| 判分优先级 ↔ 聚合 | `test_verdict.py` + `test_reports.py` | 契约黄金组合 planned=5 → overall 0.4、evaluated 2/3、coverage 0.6；空集合三个比例为 `None`；`[10,20,30,40]` median 25 / P95 40 |

## 3. 契约 §5 硬数字覆盖

| 硬数字 | 实现常量 | 用例 |
| --- | --- | --- |
| 帧上限 3 MiB | `ipc.MAX_FRAME_BYTES = 3 * Mib` | `test_ipc.py` 超长帧拒收 |
| 事件正文 16 KiB | `trace.MAX_EVENT_BODY_BYTES = 16 * 1024` | `test_trace.py` 正文截断/拒收与 `trace_complete` 压低 |
| 分段 1 MiB | `trace.MAX_SEGMENT_BYTES = 1024 * 1024` | `test_trace.py` 多段滚动与 `read_trace` 合流 |
| 单 attempt Trace 20 MiB | `trace.MAX_TRACE_BYTES = 20 * 1024 * 1024` | 触顶后不再分配 seq、不再转发帧 |
| seq 从 1 严格递增 | `TraceCollector.next_seq` 唯一来源 | 上述合流用例 |
| 重复 event_id 不重复写入 | `collector.append` 返回 `None` | `test_trace.py` 幂等用例；返回 `None` 时父进程也收不到 |
| Judge 最多两次物理请求 | `graders/judge.py` 重试预算 | `test_judge.py` + worker 侧 `provider._calls == 2` |
| 评分总预算 60 秒 | `judge.DEFAULT_TIMEOUT_S` | `test_judge.py` 超时用例（含重试计入同一预算） |

## 4. 未覆盖 / 待补（不得省略）

1. **真实 provider 冒烟**：本文件全部结论来自受控 provider 与桩 SDK。离线绿 ≠ 真机跑通模型；live 冒烟（`web_search`/`web_fetch` 白名单、真实 token 上报）尚未执行，属 GROUP-3 之后的独立验证项。
2. **端到端**：`test_end_to_end.py` 与 20 个基础用例（WU-07 / T12）不存在，因为没有服务与 CLI 入口（WU-05 / WU-08 未开工）。
3. **前端**：WU-02（evalui）缺 Bun，页面测试与独立构建输出完全未验证。
4. **全仓测试套件**：未跑。本批对 `nanobot/` 非 evaluation 目录零改动，SDK 接入面以定向回归 25 passed 代替；全仓套件留到 T14 执行并记录真实耗时与失败清单。
5. **basedpyright strict 未达标**：175 errors（真缺陷已清零，剩余为 `Any` 遍历噪声）。全仓 strict 基线本身为 2269 errors，本批不声称达标，收敛项登记在 T14。
6. **红绿证据不完整**：`worker.py` 是先实现后补测试（执行日志 §9），逐 checkbox 红灯未落盘。

## 5. 结论

GROUP-1 与 GROUP-2 的完成标准在**离线口径**下全部成立，接缝合测与硬数字覆盖有真实命令输出支撑；live 冒烟、端到端、前端与全仓回归四类明确未验证。批次保持「GROUP-2 屏障通过、等待 GROUP-3 批准」状态。

---

# GROUP-3 追加（WU-05 T09/T10 + WU-07 T12）

> 批准口径：用户「批准并执行 GROUP-3」= WU-05（T09 调度器/服务层 + T10 HTTP/认证/资源/产物）+ WU-07（T12 基础用例与校准种子）。
> 屏障口径：**HTTP 返回契约一致，20 用例离线通过；校准样本 proposed/confirmed 不混淆。**
> 审查者身份：同 GROUP-2，实现与审查同实例（用户两次否决派工），已在两份 review 首段声明。

## 6. GROUP-3 命令与真实输出

| 命令 | 输出 |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest tests/evaluation -q` | `698 passed, 63 warnings in 63.53s`（GROUP-2 496 + 本批 202） |
| `... -m pytest tests/evaluation/test_samples.py tests/evaluation/test_end_to_end.py -q` | `45 passed in 11.91s`（12 结构自洽 + 33 端到端） |
| `... -m pytest tests/evaluation/test_end_to_end.py -q` | `33 passed in 12.17s` |
| `... -m pytest tests/evaluation/test_end_to_end.py -q -k basic_case_passes` | `20 passed in 15.21s`（B01–B20 全绿） |
| `... -m pytest tests/evaluation/test_end_to_end.py -q -k flipped` | `8 passed in 3.30s`（关键值翻转） |
| `... -m ruff check tests/evaluation/ nanobot/evaluation/` | `All checks passed!` |
| `... -m basedpyright tests/evaluation/test_end_to_end.py` | `0 errors, 0 warnings, 0 notes`（新文件 strict 全绿） |
| `... -m basedpyright nanobot/evaluation` | `291 errors`（109 `reportUnknownMemberType` / 97 `reportUnknownVariableType` / 62 `reportUnknownArgumentType`，其余 23 条逐条核对见 §9） |
| `... -m pytest tests/cli tests/webui/test_build.py tests/config -q` | `4 failed, 446 passed, 3 skipped in 59.65s`——4 条失败全在 `tests/cli/test_commands.py::test_serve_*`，与本批零文件交集（见 §9.1） |

63 条 warning 是 aiohttp `NotAppKeyWarning`（`app["eval_context"]` / `request["session"]` 字符串键），沿用 WU-05 既有写法，不影响行为；T13 若引入 `web.AppKey` 可一并消掉。

## 7. 屏障项一：20 用例离线通过（真链路，无 monkeypatch）

`tests/evaluation/test_end_to_end.py` 的执行段**不打桩**：`create_attempt` 真实构造 `AgentLoop`/`Nanobot`/受控工具注册表，只有 LLM 被换成 `ControlledProvider`（回放用例 `script`）。Trace 由 `TraceCollector` 落盘、`seal_attempt_artifacts` 密封，产物按 `services._artifacts` 同口径（`kind=="workspace"` → `resolve_inside` → 工作区包含判定）登记，判分走真实 `evaluate_rules`。

| 用例 | 断言覆盖点 | 结果 |
| --- | --- | --- |
| B01/B02 | `answer_contains` / `answer_not_contains` / `answer_json`（SchemaSubset + JSON Pointer） | 全绿 |
| B03/B04/B05 | `tool_called`（含 `arguments_subset`）/ `file_exists` / `file_contains` / `file_json` 编辑后取值 | 全绿 |
| B06/B07/B08/B09/B10 | `list_dir`、CSV 求和、分类计数、两文件合并、嵌套 JSON Pointer（`/customer/name`） | 全绿 |
| B11/B16 | 必需 rubric + `required:false` 的文本断言（澄清提问 / 如实报缺失） | 全绿 |
| B12 | `tool_forbidden`（本回合一次工具都不许调） | 全绿 |
| B13/B14/B15 | 受控工具 `test_lookup` / `test_unstable`（首次受控失败、第二次成功）、两次调用计数 | 全绿 |
| B17/B18 | 两轮会话记忆 / `memory_seed` 语义记忆 | 全绿 |
| B19 | 禁网络工具 + 只读本地价格表 | 全绿 |
| B20 | `iteration_limit`（`max_iterations=2` 下两次模型调用）+ `tool_call_limit` | 全绿 |

**关键值翻转 8 条**（`FLIPS` 表）验证「规则真的在判，而不是恒过」：B01 回答改 8、B02 `count` 改 4、B04 正文改 goodbye、B07 `total` 改 41、B09 调用上限改 2、B12 脚本前置 `read_file`、B13 回答改 25°C、B20 迭代上限改 1 —— 每条都断言**指定那条**断言 `passed is False`，且执行本身仍 `succeeded`（断言失败 ≠ 执行失败）。20 条用例同时覆盖契约 §2 全部 10 种断言。

## 8. 屏障项二：校准样本 proposed/confirmed 不混淆

| 检查 | 用例 | 结论 |
| --- | --- | --- |
| 首启 seed 只写 `proposed`，`reviewer` 全为 `null` | `test_gold_seed_starts_as_proposed_and_never_resets` | 10 条全 `proposed` |
| 二次 seed 幂等 | 同上 | 返回 0，不重复写 |
| 人工确认后 seed **不重置** | 同上 | `confirmed`/`local-user` 保持不变，仍返回 0 |
| HTTP 首启读出只有 `proposed`，且不泄露 workspace | `test_http_first_boot_exposes_only_proposed_samples` | 真 `TestServer` + 登录 + `GET /calibration-samples` → 10 条 `proposed` |
| 未确认标签不得启动校准 | `test_calibration_refuses_unconfirmed_gold` | 抛 `GOLD_NOT_CONFIRMED`，且**不写** `calibration_results` |
| 一致率达标/不达标两条路 | `test_calibration_passes_when_judge_matches_confirmed_gold` / `..._fails_for_over_generous_judge` | 10/10 → agreement 1.0 `passed=True`；一律打 4 分 → 5/10 → agreement 0.5 `passed=False` |
| 同指纹可查回结论 | 同上 | `lookup_calibration` 按 `judge_fingerprint + rubric_version + gold_revision` 命中 |

校准段的 Judge 也走真实 `evaluate_judge`（严格 JSON 解析、`evidence_refs` 逐条校验），只把 provider 换成回放脚本；引用取 `trace:<attempt_id>:1`，seq 1 在样本证据里确实存在。测试代码自行确认的 gold 在 `comment` 里写明「离线测试夹具：由测试代码批量确认，非人工标注」——**样本出厂状态永远是 `proposed`，真实确认只能由用户在页面做**。

## 9. GROUP-3 未覆盖 / 已知缺口（不得省略）

1. **`run_calibration_job` 接线缺失（阻塞 UI 校准）**：`services.py:557` 写死 `evidence_for=lambda row: None`，调用必抛 `GOLD_SAMPLE_EVIDENCE_MISSING`；同一次调用还会在 `run_calibration` 内部与函数尾部**各记一次** `record_calibration_result`；调度器也不走校准项循环（`claim_calibration_item` / `finish_calibration_item` 无生产调用方）。校准的**判分与门禁链路**已被 §8 用例证明可用，缺的是「HTTP 触发的作业编排」这一段接线，属本屏障外欠账，须在 T11/T13 前补。
2. **`basedpyright` strict 未达标**：291 errors，272 条为 `reportUnknown*` 家族；余下 19 条逐条核对结果为——`reportUnnecessaryIsInstance` 6 条（防御性 isinstance，删掉会削弱运行时兜底）、`reportInvalidTypeForm` 2 条（`schemas.py:86-87` 的 `TypeAliasType` 递归别名，运行时正确）、`reportOptionalMemberAccess` 2 条（`services.py:533-534`，控制流上 `payload` 必非 `None`，类型收窄器看不出）、`reportAbstractUsage` 1 条（受控工具基类未实现 `parameters`）、`reportArgumentType` 7 条（`Literal` 与 UUID 构造的既有收窄写法）。**无新增真缺陷**，但本批不声称 strict 达标（与 D7 同口径，登记到 T14 收敛）。
3. **live provider 冒烟仍未执行**：本文件全部结论来自受控 provider。真实模型的 `web_search`/`web_fetch` 白名单、真实 token 上报、真实失败重试未验证。
4. **CLI 入口不存在**：`nanobot eval` 属 T13/WU-08（GROUP-4，未批准）。因此「导入基础用例 → 开始运行」的完整用户路径当前**没有命令行入口**，只有 HTTP 与服务层 API。
5. **前端完全未验证**：WU-02 仍因缺 Bun 阻塞，页面与浏览器验收零覆盖。
6. **4 条 CLI 回归失败（既有，非本批引入）**：`tests/cli/test_commands.py` 的 4 个 `test_serve_*` 失败于 `_fake_create_app() got an unexpected keyword argument 'session_manager'`——`nanobot/cli/commands.py` 与 `tests/cli` 均不在本批 diff 内（`git diff HEAD --stat` 只含 `nanobot/evaluation/*` 与 `tests/evaluation/*`），属分支上已有的 serve 改动与测试桩不同步，留给 CLI 相关批次处理。
7. **红绿证据**：`test_end_to_end.py` 首轮即绿（实现先于测试补齐，与 GROUP-2 §9 同性质的证据缺口，已如实记录）。

## 10. fixture 模式的能力边界（T12 收口项）

离线通过 ≠ 真实可用。当前 fixture 模式的边界，逐条如下：

| 维度 | 边界 |
| --- | --- |
| 工具白名单 | 只有 6 个：`read_file` / `write_file` / `edit_file` / `list_dir` / `test_lookup` / `test_unstable`；`web_search`/`web_fetch` 仅 `tool_mode="live"` 允许，其余（含 exec、MCP、投递、自修改、goal）一律 `SANDBOX_VIOLATION` |
| 模型 | `ControlledProvider` 回放 `script`，**不是模型能力评测**；它证明的是判分/证据/门禁链路，不是回答质量 |
| 脚本形态 | 每步 = 一次物理模型调用并上报 usage；脚本**必须以 `text`/`json_text` 收尾**，否则回放工具步会顶到 `max_iterations`（B20 就是这条约束的正例） |
| 脚本耗尽 | 重复回放**最后一步**，不报错。用例作者须自行保证脚本长度，否则会出现「回答复读」这种看起来正常的假象 |
| 时间 | `ControlledClock` 固定时钟，不依赖真实时间；工具 fixture 的时间字段因此可复现 |
| 网络/MCP | 不构造 `MCPProvider`，不自动连任何外部 MCP，`capability.external_dependencies` 仅 live 为真 |
| 目录布局 | 会话存储外插到 attempt 目录**之外**（Windows 260 字符上限，实测 attempt 内嵌套已达 281 字符）；隔离靠会话键前缀 `eval:` + 工作区哈希命名空间 |
| 判分 | 规则判分与 Judge 校准离线全链路可跑并已验证；live 只影响模型/网络两处 |
| 产物 | 只登记 `artifact_paths` 声明且密封进 `manifest.json` 的文件；工作区里的中间文件不会被当成证据 |

结论：**fixture 模式能证明「评估系统自己是对的」，不能证明「Agent 是对的」。** 后者需要 live 冒烟 + 真实模型，属 GROUP-5 独立验证项。

## 11. GROUP-3 结论

屏障两项口径在离线口径下全部成立：20 条基础用例在真链路上全部通过、8 条关键值翻转全部按预期失败；校准样本出厂 `proposed`、服务与 HTTP 两级都不与 `confirmed` 混淆、未确认即拒绝校准、一致率门禁双向可判。已知缺口 7 条（其中第 1 条 `run_calibration_job` 接线缺失为真实功能欠账）已逐条登记，批次状态为「GROUP-3 屏障通过（离线口径），等待 Bun / GROUP-4 / GROUP-5」。
