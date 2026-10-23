# Agent 自动化评估 代码审查（批次 agent-evaluation，GROUP-1 + GROUP-2）

> **审查者身份限制（先说清楚）**：本批 GROUP-2 由 Leader 直接实现（用户否决 worker 派工），因此本文件是**实现者自查**，不满足 dispatch「实现与审查不得同实例」的独立审查要求。独立审查需由后续会话的另一个实例基于同一提交重做，本文件不冒充它。
> 被审代码：`1bfe8d9`（WU-01）+ `11da695`（WU-03/WU-04，26 文件 10695 行）+ `a07c50e`（自查修复）
> 命令口径见 `verifications/2026-10-15-agent-evaluation-collective-test.md`

## 1. 结论

未发现会导致错误判分或数据泄漏的缺陷。3 项已在自查中修掉（见 §3），4 项记为技术债、不阻塞 GROUP-3（见 §4）。

## 2. 判分正确性（最关键面）

| 检查点 | 判定 | 依据 |
| --- | --- | --- |
| verdict 优先级是否等于契约给定实现 | 通过 | `graders/verdict.py` 与 plan T07 给出的 `derive_verdict` 逐分支一致；`test_verdict.py` 覆盖 `judge_state` 六个取值 × 四种执行态 |
| 「不可评估」是否被算成通过 | 通过 | `None in required_rules` 与 `trace_complete=False` 都进 `review_required`；`execution_status != succeeded` 进 `not_evaluated`，聚合分母不含它（`test_reports.py` 零分母 → `None`） |
| 未校准 Judge 是否被当结论 | 通过 | `judge_state="uncalibrated"` → `review_required`；worker 侧固定 `calibrated=False`，真实状态由服务层合并（执行日志 D6） |
| 证据引用是否可伪造 | 通过 | `judge.py` 对每个 `evidence_ref` 调 `evidence.resolve_ref`，解析不到即 `EVIDENCE_REF_UNKNOWN` 拒收；JSON 修复只修语法、不改语义 |
| Token 是否会重复计数 | 通过 | 物理调用是唯一主账本，`round_usages` 只做交叉检查；agent/judge 分桶，各自 `aggregate(role)` |
| seq 是否可能与磁盘脱节 | 通过 | `TraceCollector` 是唯一编号者；触顶或重复 event_id 时 `append` 返回 `None`，该事件既不落盘也不转发 |

## 3. 自查中已修

| # | 位置 | 问题 | 修法 |
| --- | --- | --- | --- |
| C1 | [worker.py `run_agent_worker`](file:///D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation/nanobot/evaluation/worker.py#L582) | `async with runtime.bot` 与 `finally: await runtime.aclose()` 并存 → SDK 关两次；`__aexit__` 抛错会**覆盖**原始执行错误（直接违反 dispatch §4） | 引入 `guarded_close()`：清理错误自捕获后追加 `phase="cleanup"`，异常处理排在其内侧，使 `errors` 顺序等于发生顺序。用例 `test_cleanup_failure_is_kept_alongside_execution_error` 锁死顺序 |
| C2 | [worker.py `WorkerChannel`](file:///D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation/nanobot/evaluation/worker.py#L133) | 发过的每一帧都攒在 `sent` 列表里，20 MiB Trace 预算等于在堆上再复制一份，且无任何生产代码读它 | 只保留 `last_frame` 与 `frame_count` |
| C3 | [worker.py `_index_file`](file:///D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation/nanobot/evaluation/worker.py#L445) | `read_bytes()` 整文件进内存算 sha256，大产物直接顶内存 | 1 MiB 分块流式哈希，`size_bytes` 由累计块长得出 |

## 4. 记为技术债（不阻塞）

1. **`graders/rules.py` 545 行 / `reports.py` 473 行**：断言种类（`answer_contains`、`tool_called`、`json_schema`、`json_pointer`、`artifact_*`、`loop_limit`）都在同一文件里按 kind 分派。当前每种都有独立用例、可读性尚可，再加两类以上时应拆成规则注册表。现在拆属于自扩范围，不动。
2. **`redact_json` 每帧一次 `json.dumps` 往返**：脱敏代价随事件体积线性增长，最坏情况是 16 KiB 正文 × 大量事件。正确性优先，保持现状；若 live 冒烟显示 worker CPU 成为瓶颈，再改成逐字段替换。
3. **`basedpyright` strict 仍有 164 条 `reportUnknown*`**：源于 `Mapping[str, Any]` 与递归 `JsonValue` 的逐层 `.get()`。真缺陷（协议只读性、结果泛型、`Literal` 收窄、`None` 比较）已清零。收敛方式是给 JSON 负载加一层类型化访问器，工作量大且与全仓 2269 errors 的既有基线同源，登记到 T14。
4. **`evidence_from_payload` 手写逐字段校验**：契约 `extra="forbid"` 的模型本可以直接 `model_validate`，这里手动解析是为了把字段缺失归一成 `JUDGE_BOOT_EVIDENCE_INVALID` 而不是 pydantic 的长错误串。可读性稍差，但错误码口径更稳，保留。

## 5. 复核建议（给独立审查者）

按风险从高到低：`graders/verdict.py` + `graders/judge.py` 的拒收路径 → `worker.py` 的终态与错误累积 → `evidence.py` 的三个读取入口 → `reports.py` 的分母与缺失值 → `processes.py` 的 owner 回收。前三处都有直接对应用例，后两处建议独立审查时补跑一次「故意留半段 Trace 目录」与「kill 掉 worker 主进程」的手工复现。

---

# GROUP-3 追加审查（WU-05 T09/T10 + WU-07 T12）

> 同样是**实现者自查**（同实例），审查范围是 GROUP-3 的实现改动 + 新增样本/端到端测试：`nanobot/evaluation/{services,server,runtime_factory}.py`、`nanobot/evaluation/samples/{basic-v1,calibration-v1}.json`、`tests/evaluation/{test_samples,test_end_to_end}.py`。
> 命令口径见 collective-test §6。

## 6. 结论

未发现会导致错误判分、结论串台或标签状态错乱的新增缺陷。**5 项已在自查中修掉**（§7），**3 项记为技术债/已知欠账**（§8），其中 C6 是真实功能缺口（校准作业编排未接线），必须在 T11/T13 前补。

## 7. 自查中已修

| # | 位置 | 问题 | 修法 |
| --- | --- | --- | --- |
| C4 | [services.py `_iterations_of`](file:///D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation/nanobot/evaluation/services.py#L881) | `iterations` 读 `result.get("iterations")`，但 worker 结果帧把它放在 `usage` 下 → 取值恒为 0，**`iteration_limit` 断言恒过**（判分侧失去一道门禁） | 改为从 `result["usage"]["iterations"]` 取，并加布尔排除（`bool` 是 `int` 子类）。B20 的 `iteration-cap` 用例与翻转用例双向钉死 |
| C5 | [services.py `_find_calibration`](file:///D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation/nanobot/evaluation/services.py#L540) | 查校准结论时漏传 `gold_revision` → 人工改完 gold 标签后，**旧标签下的校准结论仍被采信** | 补 `gold_revision=self._repository.gold_revision()`；`_find_calibration` 外层 `contextlib.suppress(Exception)` 保持「查不到就是未校准」的保守语义 |
| C6 | [services.py `_evidence_payload`](file:///D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation/nanobot/evaluation/services.py#L939) | Judge boot 的证据负载缺 `workspace` → `evidence_from_payload` 侧按契约要求 `JUDGE_BOOT_EVIDENCE_INVALID` 拒收，**服务层评分永远 setup_error** | 补 `"workspace": str(evidence.workspace)`，指向**被测 attempt** 的工作区（judge 自己的 scratch 目录只做进程隔离，不承载证据） |
| C7 | [services.py `_artifacts`](file:///D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation/nanobot/evaluation/services.py#L417) | 旧实现 `artifact_dir.rglob("*")` 扫目录，而该目录里只有索引本身；产物证据实际来自 workspace → **登记的是空集**，所有 `file_*` 断言都会走「未登记」判失败 | 改为只读 Worker 密封的 `manifest.json`，且必须 `kind=="workspace"`、`resolve_inside(root)` 通过、目标在工作区内。扫目录的写法同时会把未声明的中间文件误当证据 |
| C8 | [services.py `_judge_paths`](file:///D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation/nanobot/evaluation/services.py#L709) | Judge 与 Agent 共用 `runtime_root/workspace/trace/artifacts` → 评分事件追加到**已密封的 Trace 段**，污染被测证据且 judge 自己 seal 失败；并发 attempt 之间也会互相覆盖索引 | 每个 attempt 独占 `runtime/`、`workspace/`、`trace/`、`artifacts/`，judge 用 `<attempt>-judge` 前缀的第二套目录；会话存储因 Windows 260 上限外插到 attempt 目录之外并显式注入 `SessionManager` |

## 8. 技术债与已知欠账

1. **`run_calibration_job` 接线缺失（C6 同级，非本批已修）**：`services.py:557` 的 `evidence_for=lambda row: None` 必抛 `GOLD_SAMPLE_EVIDENCE_MISSING`；`run_calibration` 内部与函数尾部各记一次 `record_calibration_result`；调度器不走 `claim_calibration_item`/`finish_calibration_item`。判分与门禁本身已被离线用例证明可用，缺的是 HTTP 触发的作业编排。**不要**在此处临时塞一个 `evidence_for` 就当修好——证据来源必须是 gold 样本里冻结的那份，否则校准就成了自证。
2. **`seed_gold_samples` 是 `INSERT OR REPLACE`**：语义上能把 `confirmed` 打回 `proposed`。服务层用「空表才 seed」兜住，并有三条断言钉死（幂等返回 0 / 确认后仍 0 / 状态不倒挂）。建议后续把仓储改成 upsert-only-when-absent，从根上消除这个陷阱。
3. **291 条 basedpyright strict error**：272 条 `reportUnknown*` 家族 + 19 条已逐条核对为非缺陷（详见 collective-test §9.2）。与 GROUP-2 的 D7 同口径，不谎称达标。

## 9. 判分正确性复核（GROUP-3 新增面）

| 检查点 | 判定 | 依据 |
| --- | --- | --- |
| 样本能否让规则「恒过」 | 通过 | 8 条关键值翻转逐条断言 `passed is False`，且执行仍 `succeeded` |
| 端到端是否绕过生产路径 | 通过 | `test_end_to_end.py` 零 `monkeypatch`；`create_attempt`/`AgentLoop`/`ToolRegistry`/`seal_attempt_artifacts`/`evaluate_rules` 全为真实调用 |
| 标签状态能否被程序冒充人工 | 通过 | 测试内确认走 `review_gold_sample` 并强制 `comment` 自报「离线测试夹具」；样本 JSON 本身不含 `label_status`/`reviewer`，seed 一律写 `proposed` |
| 校准一致率能否被调用方抬高 | 通过 | `run_calibration` 的判定只取仓储 gold 行，调用方传入的 `samples` 仅用于匹配 id 提供证据对象 |
| 校准结论能否跨标签版本复用 | 通过 | 指纹 + 量表版本 + `gold_revision` 三元组（C5 已修） |

## 10. 给独立审查者的补充复核顺序

1. `run_calibration_job` 的接线缺口：确认 T11 补齐时 gold 证据取自样本冻结内容，而不是重新跑一遍 attempt。
2. `_registered_artifacts`（测试侧）对 `services._artifacts` 的镜像是否会在后续演进中漂移——当前测试只保证口径一致，不保证代码同源。
3. B15（受控工具首次失败）这类「工具失败后回合继续」的样本，其价值依赖 `test_unstable` 的 fixture 语义；若将来改为真实不稳定工具，用例结论不可直接复用。
4. `samples/*.json` 会被 T13 打进 wheel：请确认 `_sample_path` 用 `Path(__file__).parent` 在非 editable 安装下仍然成立。
