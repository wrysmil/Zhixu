---
artifact: dispatch
route: orchestration.dispatch
skills: [test-driven-development, subagent-driven-development]
source: [2026-10-18-subagent-observability-plan.md]
created_at: 2026-10-18
status: ready
approved: true
plan: 2026-10-18-subagent-observability-plan.md
---

# 子 Agent 执行详情调度

## 执行图

```text
WU-01 ledger/models ─┬─> WU-02 runtime observer ─> WU-03 gateway
                     └─> WU-04 frontend state ────> WU-05 UI surface
                                                        │
                                                        └─> Leader E2E/review
```

## 工作单元与写入边界

- WU-01：只写 `nanobot/subagent_executions/**` 与 `tests/subagent_executions/**`；先产出 RED 测试，再实现 ledger。
- WU-02：只写 `nanobot/agent/subagent.py`、`nanobot/agent/tools/spawn.py`、`nanobot/agent/tools/execution.py` 与指定 runtime tests；等待 WU-01 public types/repository 固定。
- WU-03：只写 `nanobot/webui/subagent_execution_routes.py`、settings/outbound adapter 与指定 WebUI Python tests；等待 WU-01/02 event API 固定。
- WU-04：只写 `webui/src/features/subagent-execution/**` 与三份前端状态测试；可在 WU-03 wire contract 固定后开始。
- WU-05：只写 ThreadShell、generic tool model、两个新增详情组件和 UI tests；等待 WU-04 hook API 固定。
- Leader：只做集成、冲突消解、真实浏览器、测试证据、审查与 Lore commit；不覆盖 WU 文件。

## 串并行与验收门

1. WU-01 GREEN 后广播领域签名。
2. WU-02 与 WU-04 可各自准备 RED；WU-04 的 wire parser 以正式 contract fixture 为准，不能自造字段。
3. WU-03 必须在 WU-02 运行时 event API GREEN 后接入。
4. WU-05 必须通过 WU-04 flow tests 后接入 `ThreadShell`，避免 surface 状态二次实现。
5. 每个 WU 必须回报 RED 命令、GREEN 命令、改动文件和遗留风险；任何跨边界修改先交给 Leader。
6. GROUP 结束后必须跑 Task 6 集体测试、独立审查和真实浏览器证据，才可报告完成。
