---
artifact: execution-log
route: orchestration
batch: agent-evaluation-webui-group1
created_at: 2026-10-23
status: group1-closed
---

# 执行日志：评估模块 GROUP-1（WU-01~WU-09）

## 1. 编排与并行

- 计划：`.ai-runtime-artifacts/plans/2026-10-23-evaluation-webui-plan.md`
- 调度：`.ai-runtime-artifacts/plans/2026-10-23-evaluation-webui-dispatch.md`
- 追踪：`.ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-23-evaluation-webui.md`

派发 4 个 worker（文件不相交）：

| Worker | WU | 范围 | 结果 |
| --- | --- | --- | --- |
| MW-1 | WU-01/03/04/05 | services/schemas/scheduler/repository/worker/runtime_factory + loop/nanobot/sdk | **中途终止**：WU-01、WU-03 已提交并通过全量回归；WU-04/05 由 Leader 接手完成 |
| PW-1 | WU-06a | `nanobot/evaluation/datasets.py` + 测试 | done（`df8b9f7`，9 passed） |
| PW-2 | WU-07a | `reports.py` 追加 `render_summary_html` + 测试 | done（`1eeb6fb`，23 passed） |
| PW-3 | WU-02 | `nanobot/evaluation/profiles.py` + 测试 | done（`db80283`，3 passed） |

## 2. 偏差与处置

1. **MW-1 终止**：用户判定该 worker 卡死并终止。Leader 复核工作树：WU-01（`e62a330`）与 WU-03（`6ee0407`）的实现完整、测试全绿，予以保留；WU-04/05 由 Leader 直接实现并提交（`a969fa2`）。
2. **MW-1 的 WU-03 实现优于计划**：deadline 从「领取首个 attempt」起算（排队不烧预算）、取消原因区分 timeout/user、收口幂等。计划中的「按 created_at 起算」被替换。
3. **PW-1 纠正断言字段名**：`tool_called` 实际字段是 `name` + 必填 `min_calls`；`tool_forbidden` 是 `names` 列表。计划里的 `tool` 字段名错误，已按源码修正。
4. **PW-3 纠正 bundle 形状**：bundle 必须全 camelCase（与 `config.json` 同形），并补 `modelPreset` 字段——worker 侧选预设读的是 `model_preset`；遍历对象改为 `config.model_presets`（`default` 是保留名，不能进 bundle）。
5. **worker 注入桩兼容**：worker 无条件传 `metadata=` 会打爆测试替身 `StubBot.run`（不收该参数）→ 9 条 `test_worker_orchestration` 变红。改为仅在 Agent 目标时传参。
6. **团队结论口径**：团队路径没有 worker trace 密封语义，直接复用 `_grade` 会把所有结论推进 review_required。给 `_grade` 增加 `trace_complete_override`，团队路径显式传 True。
7. **SameSite 与绑定地址**：评估服务原绑 `127.0.0.1`，而 WebUI 可能用 `localhost` 打开 → 站点不一致会让 Strict 会话 cookie 被浏览器拦。改为绑 `localhost`（覆盖 v4/v6 回环）。

## 3. 验证

见 `.ai-runtime-artifacts/verifications/2026-10-23-evaluation-webui-collective-test.md`：740 passed / 5 既有失败；ruff 全绿；真实冒烟 7 步全过（含 CORS 预检、真实模型 profile、Excel 导入、HTML 报告）。

## 4. 下一步

- 并行审查（reviewer / security-auditor）结论落 `.ai-runtime-artifacts/reviews/` 后按需返工。
- 前端批次 GROUP-2（WU-10~WU-13）待用户确认后开始。
