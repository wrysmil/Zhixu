---
artifact: verification
route: orchestration.dispatch
source:
  - .ai-runtime-artifacts/plans/2026-10-26-agent-evaluation-reliability-plan.md
  - .ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-26-agent-evaluation-reliability.md
created_at: 2026-10-26
status: passed
---

# 评估可靠性 WU-01 基线验证

## 修复的业务问题

- 校准作业引用 `agent-1`、`judge-1`，而受控默认档案实际使用 `controlled-agent`、`controlled-judge`，导致 `PROFILE_NOT_FOUND`。服务仅在直接查找失败时作兼容映射。
- 调度器在检查某个运行的并发额度后从全局 FIFO 领取 attempt，可能领取属于另一个已满额运行的任务。领取现在在同一原子查询中按 `run_id` 限定。

## 环境结论

深层隔离worktree与pytest默认临时目录组合会触发 Windows `WinError 206`。以短绝对 `--basetemp` 重跑后服务测试通过，因此没有将该现象归因为业务执行或模型能力失败。

## 复跑结果

在主工作区、使用短绝对临时目录执行：

```powershell
& .venv\Scripts\python.exe -m pytest tests/evaluation/test_calibration_jobs.py -q --basetemp=D:\studyspace\project\z_wu01_main_calibration
& .venv\Scripts\python.exe -m pytest tests/evaluation/test_scheduler.py -q --basetemp=D:\studyspace\project\z_wu01_main_scheduler
& .venv\Scripts\python.exe -m pytest tests/evaluation/test_services.py -q --basetemp=D:\studyspace\project\z_wu01_main_services
& .venv\Scripts\python.exe -m ruff check nanobot/evaluation/services.py nanobot/evaluation/scheduler.py nanobot/evaluation/repository.py tests/evaluation/test_calibration_jobs.py tests/evaluation/test_scheduler.py
```

- calibration jobs：7 passed（2条既有aiohttp AppKey建议告警）
- scheduler：18 passed
- services：17 passed
- ruff：passed
- `git diff --check`：passed

## 未覆盖项

本切片没有实现v2数据迁移、Trace身份、团队隔离或Judge材料升级；这些在后续WU中处理。
