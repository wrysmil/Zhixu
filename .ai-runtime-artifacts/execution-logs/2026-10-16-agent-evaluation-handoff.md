---
route: implementation-handoff
batch: agent-evaluation
date: 2026-10-16
worktree: D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation
branch: harness/wt-agent-evaluation
status: 半成品交接（GROUP-4 未批准，用户口头指令范围内）
---

# 交接文档：校准 `evidence_for` 与调度循环（GROUP-3 欠账 D14）

## 0. 一句话状态

GROUP-3（WU-05 T09/T10 + WU-07 T12）**已完成并提交**（`c39e55b`，698 passed）。用户随后要求
「继续补齐校准 evidence_for 和调度循环」——这项**做了一半**：服务层与仓储层已写完并自洽，
**调度器的校准派发循环尚未写，测试一条未写**，工作区有未提交改动。

接手者请**直接接着写**，不要重做已完成部分，也不要以为它已经能跑。

## 1. 已完成且已提交（不要动）

| 提交 | 内容 |
| --- | --- |
| `268f0fe` | T10 HTTP 契约层、认证准入与产物下载 |
| `c39e55b` | T12 20 基础用例 + 10 校准种子 + 离线端到端（含服务层 4 处判分/结论缺陷修复） |

`c39e55b` 的基线数字（下一实例对不上先怀疑自己）：`pytest tests/evaluation -q` → **698 passed**。

## 2. 当前未提交的半成品（`git status` 有 3 个 M）

```
 M nanobot/evaluation/repository.py   (+37)
 M nanobot/evaluation/scheduler.py    (+14)
 M nanobot/evaluation/services.py     (+226/-…)
```

> `webui/package-lock.json` 也是 M，但那是本批之外的既有改动线，**不要一起提交**。

### 2.1 `repository.py` —— 已完成，可直接用

- `list_calibration_items(job_id)`：逐项读出（含 `result.matched`），作业错误诊断与测试靠它。
- `save_calibration_job_result(job_id, result)`：把作业级补充字段（指纹、错误）**合并**进
  `result_json`，**不改** `total/matched/agreement/passed`——那四个数由既有
  `_settle_calibration_if_done` 从逐项结果汇总，是唯一口径。

### 2.2 `services.py` —— 已完成，含一处刻意改动

- **新增 `judge_runner` 注入点**（`__init__(judge_runner=...)`）：attempt 评分与校准项评分
  **共用同一个实现**，默认是评分子进程（`_run_judge`）。这是本轮唯一的测试缝，理由见 §4。
- `judge()` 拆出 `_run_judge(...)`：原 `judge()` 里 spawn 子进程 + 解析 result 的那段整体搬过去，
  `judge()` 只保留「查校准 → 跑评分 → 合并 `calibrated` 标记」。**行为等价**，已有用例全绿。
- **删除 `run_calibration_job`**：它是死代码且语义与仓储冲突（`evidence_for=lambda row: None`
  必抛、且会重复记一次 `calibration_results`）。已确认无任何调用方与测试引用。
- 新增：
  - `_gold_rows(sample_ids)` / `_job_rubric(rows)`：作业内 gold 必须**共用同一份量表**，
    混用量表等于把不同标尺平均成一个数。
  - `_judge_profile(snapshot)`：从 `judge_profile_id` 取 provider/model/bundle（`Profile` 无
    `generation` 字段，故指纹里 generation 为 `None`）。
  - `_calibration_paths(item_id)`：返回 `(证据 workspace 布局, judge 布局)` 二元组，
    都在 `runtime/calibrations/<item_id>` 与 `<item_id>-judge` 下。返回二元组写法略怪，是刻意的。
  - **`calibrate(claimed)`**：单个校准项的完整执行。前置校验顺序＝gold 版本 → 标签是否 confirmed
    → 量表一致 → 还原证据 → 评分 → 与 `expected_verdict` 比对。返回
    `CalibrationItemOutcome(status="completed"|"failed", result={...,"matched":bool,...})`。
  - **`settle_calibration(job_id)`**：把作业结论并进 `calibration_results`（门禁查的表），
    并补指纹/错误。**只有作业 `completed` 才写结论**；取消/中断/任一项失败一律 `passed=null`。
    幂等（指纹已写入直接返回）。

### 2.3 `scheduler.py` —— **只加了骨架，派发循环没写**

已加：
- `CalibrationItemOutcome` dataclass（`status` + `result`）。
- `AttemptPipeline` Protocol 增加 `calibrate()` 与 `settle_calibration()` 两个方法。

**没写（下一个实例的全部工作）**：`_dispatch_once()` 里没有任何校准分支，仓储的
`claim_calibration_item` / `finish_calibration_item` / `open_calibrations` 仍然**没有生产调用方**。

## 3. 下一个实例的施工顺序

### 步骤 1：调度器校准循环（`scheduler.py`）

在 `_dispatch_once()` 末尾加一次 `self._dispatch_calibration()`，规则与 attempt 派发同构：

```python
async def _dispatch_calibration(self) -> None:
    if not self._repository.open_calibrations():
        return
    if self._inflight_calibrations() >= self._config.concurrency:
        return
    claimed = self._repository.claim_calibration_item()   # 跨作业原子领取
    if claimed is None:
        return
    task = asyncio.create_task(
        self._run_calibration_item(claimed),
        name=f"calibration:{claimed['job_id']}:{claimed['id']}",
    )
    self._tasks[task.get_name()] = task          # stop() 与 _reap_done() 靠它统一管理
```

`_run_calibration_item` 的形状（**必须照抄的不变量**）：

1. `async with self._slots:` —— 校准项与 attempt **共用同一个服务级信号量**（契约 §3.1/§4）。
2. 执行前后各查一次作业状态；`cancelled` 时不提交通过结论。
3. 成功后 `finish_calibration_item(item_id, status=outcome.status, result=outcome.result)`，
   **紧接着** `self._pipeline.settle_calibration(job_id)`（只有最后一项才会触发作业收口）。
4. `asyncio.CancelledError` → 该项落 `cancelled` 后重新抛出。
5. `CalibrationError`（前置不满足：未确认 / 量表不匹配 / gold 版本变了）与其它异常 → 该项落
   `failed` 并把错误文本写进 `result["error"]`，**不要让任务异常终止派发循环**。
6. `finish_calibration_item` 在作业已被取消时可能抛 `StateConflict`（项已是 `cancelled`）→
   用 `contextlib.suppress` 静默，**这不是错误**。

不需要额外写取消/恢复代码：`cancel_calibration()` 已把排队项置 `cancelled`，
`claim_calibration_item` 只领取 `queued|running` 作业的项，`_recover()` 已有
`mark_calibration_interrupted`。取消的收口语义由仓储的 `_settle_calibration_if_done` 保证
（任一项非 completed → `agreement=None`、`status="cancelled"`）。

### 步骤 2：修 `tests/evaluation/test_scheduler.py` 的假流水线

`_FakePipeline` 必须补 `calibrate()` 与 `settle_calibration()`，否则**第一次派发到校准项就
`AttributeError`**（Protocol 只在静态检查生效，运行时是普通属性查找）。
最小实现：记 `entered`/`settled`，返回 `CalibrationItemOutcome("completed", {"matched": True})`。

### 步骤 3：新写 `tests/evaluation/test_calibration_jobs.py`

建议至少这 6 条（全部离线，不起子进程——注入 `judge_runner`）：

1. **跑通并写门禁行**：seed 10 条 gold → 全部 `review_gold_sample` 确认 → 建作业 → 调度器跑到终态
   → 作业 `completed`、`agreement=1.0`、`passed=True`，
   `find_calibration_result(fingerprint, "general-v1", gold_revision)` 命中（**这条是门禁真正
   接上的证据**，不是作业自己好看）。
2. **坏 Judge 不通过**：`judge_runner` 一律给 4 分 → `matched` 只对上正例 → `passed=False`。
3. **未确认标签不跑**：seed 后不确认 → 作业收口为非 passed，`calibration_results` **没有新行**。
4. **gold 版本漂移拒跑**：建作业后改一条标签（`gold_revision` +1）→ 该项 failed，错误含
   `GOLD_REVISION_STALE`。
5. **取消**：跑到一半 `cancel_calibration` → 作业 `cancelled`、`passed is None`、不写门禁行。
6. **证据确实来自 gold 冻结内容**：断言注入的 `judge_runner` 收到的 `evidence.attempt_id` 等于
   gold 行里的 attempt_id，`evidence.workspace` 是校准项自己的工作区（不是别处）。

HTTP 层再补一条：`POST /calibrations` → 202 → 轮询 `GET /calibrations/{id}` 到 `completed`
且 `judge_fingerprint` 非空。`http_app.build_app()` 需要能注入带假 `judge_runner` 的 service
（可能要给它加一个可选参数，或在测试里自己拼 `create_app`）。

### 步骤 4：门禁 + 产物

```bash
cd /d/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation
.venv/Scripts/python.exe -m pytest tests/evaluation -q            # 基线 698 passed
.venv/Scripts/python.exe -m ruff check nanobot/evaluation tests/evaluation
.venv/Scripts/python.exe -m basedpyright nanobot/evaluation      # 基线 291 errors（272 条 reportUnknown*）
```

产物更新（都在**主 checkout** `D:/studyspace/project/Zhixu/.ai-runtime-artifacts/`，不在 worktree）：
collective-test 追加 GROUP-4 小节、code-review 追加本轮审查、security-review 追加
`judge_runner` 注入点与校准证据目录的安全结论、execution-log §17+、DISPATCH-TRACK 追加两条、
plan T10 末条 checkbox 视完成情况勾选（**只能在真的接通后勾**）。

## 4. 设计决策与红线（别改回去）

1. **校准证据只能来自 gold 行冻结的 `evidence`**：不重跑 attempt、不接受调用方递进来的证据对象。
   否则一致率就是自己给自己打分。`evidence_from_payload(row["evidence"], workspace=校准项自己的
   workspace)`，gold 里的 artifact 只有 sha256/大小等**元数据**，磁盘上没有对应文件——这是
   刻意的，judge 只读元数据不读正文。
2. **`agreement`/`passed` 只有仓储一个口径**：`_settle_calibration_if_done` 汇总，
   `record_calibration_result` 落库。服务层与调度层都**不许**自己写 `>= 0.8` 阈值。
3. **失败/取消/中断一律 `passed=null`**，不允许用 0 或部分汇总冒充「跑完了但一致率低」。
4. **`judge_runner` 是唯一测试缝**：生产默认必须是评分子进程，不要因为测试方便改成别的默认。
5. 校准项占服务级信号量，与 attempt 共享（契约要求「共享服务级并发」）。
6. 凭据边界照旧：`bundle` 只进内存里的 boot，不落库、不进命令行、不进 Trace。

## 5. 已知阻塞 / 本轮不做

- **WU-02（evalui 前端）仍缺 Bun**：不得用另一套 lock 冒充 `--frozen-lockfile`。页面确认链路
  因此仍未验证。
- **live provider 冒烟未做**：全部结论来自受控 provider；`judge_runner` 默认的子进程路径在离线
  测试里**没有被执行过**（既有 `test_services.py` 也没有真正跑过 judge 子进程）——这是一个
  已知的覆盖缺口，接手者若能造出「受控 profile 在子进程里可跑」的方案，值得单独记一笔。
- **S7 未处置**：`create_run` 仍把 `Profile.bundle` 整块写进 `runs.snapshot_json`（当前 bundle
  恒为 `None` 故无实际泄漏；API 与导出是字段白名单）。处置需先定「凭据注入」契约，不在本轮范围。
- `tests/cli` 有 4 条 `test_serve_*` 既有失败（`session_manager` 桩不同步），与本批零交集，
  不计入本批欠账。

## 6. 引用

- 计划：`.ai-runtime-artifacts/plans/2026-10-15-agent-evaluation-plan.md`（T09/T10/T12 已勾，
  T10 末条因 D14 保持未勾）
- 契约：`D:/studyspace/project/Zhixu/.ai-runtime-artifacts/contracts/2026-10-15-contract-agent-evaluation.md`
- 执行日志：`D:/studyspace/project/Zhixu/.ai-runtime-artifacts/execution-logs/2026-10-15-agent-evaluation-execution-log.md`（D14 在 §13）
- 集体测试：`D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-10-15-agent-evaluation-collective-test.md`（§9 第 1 条即本轮欠账）
- 代码/安全审查：`D:/studyspace/project/Zhixu/.ai-runtime-artifacts/reviews/2026-10-15-agent-evaluation-{code,security}-review.md`
- 追踪：`D:/studyspace/project/Zhixu/.ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-15-agent-evaluation.md`
