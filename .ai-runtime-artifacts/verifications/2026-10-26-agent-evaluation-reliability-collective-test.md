---
artifact: collective-test
route: orchestration.dispatch
plan: .ai-runtime-artifacts/plans/2026-10-26-agent-evaluation-reliability-plan.md
contract: .ai-runtime-artifacts/contracts/2026-10-26-contract-evaluation-reliability.md
created_at: 2026-10-26
status: partial
blockers: [team-runtime-wiring, real-model-smoke, gold-human-confirmation]
---

# 评估可靠性集体测试（2026-10-26）

## 1. 结论摘要

- 软件回归：后端 1332 passed / 0 failed；前端 1902 passed / 7 failed / 2 skipped，失败集合与同 HEAD 基线完全一致（都在评估域之外）；tsc 通过；ruff（nanobot/evaluation）通过。
- 判错能力：14 条评分器反例全部未判合格（12 disqualified + 2 review_required）；36 条样本的错误回放 34 failed + 2 setup_error，**没有一条错误回放被判 passed**。
- 待完成（阻断完整交付）：
  1. 团队生产执行栈未接线：EvaluationService 只有注入 team_runtime_factory 才能跑团队目标；gateway 尚未注入，团队目标会 setup 失败（不会假通过）。
  2. 真实模型验收未执行：本机未提供可用凭据，6 attempt 真实冒烟未跑。
  3. gold 未人工确认：校准保持「待人工确认」，未运行真实 Judge 校准。
  4. Skill 目标：本次回放统一走 Agent 执行路径，Skill 目标的技能物化与技能隔离只做了单元级验证，未在 36 条回放中按 skill 目标执行。

## 2. 验证矩阵

| 域 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | pytest tests/evaluation tests/teams tests/webui/test_eval_team_runner.py tests/cli/test_eval_commands.py tests/cli/test_gateway_runtime.py -q | 1332 passed / 0 failed（185s） |
| 后端 lint | ruff check nanobot/evaluation | All checks passed |
| 前端类型 | npx tsc -p tsconfig.build.json --noEmit | 退出码 0 |
| 前端全量 | npm run test -- --no-cache | 1902 passed / 7 failed / 2 skipped（153 文件） |
| 前端构建 | npm run build（TEMP/TMP 指向可写目录） | 退出码 0，built in 13.61s |
| 样本定义与反例 | pytest test_reliability_samples.py test_samples.py test_datasets.py | 41 passed |
| T05 执行模式 | pytest test_execution_modes.py | 13 passed |
| 用例判据端点 | pytest test_routes.py | 40 passed |
| 前端集成 | vitest evaluation-integration.test.tsx | 4 passed |
| 前端评估域 | vitest reliability + settings + observability + api | 26 passed |

前端失败集合（与基线一致，非评估域）：useSessions、thread-viewport、add-project-dialog（2 条）、workspace-navigation、preview-isolation、i18n-dock-parity、preview-thread。

## 3. 36 条样本受控回放（框架验证，不是模型能力）

- 脚本：.ai-runtime-artifacts/verifications/2026-10-26-run-reliability-samples.py
- 结果：.ai-runtime-artifacts/verifications/2026-10-26-reliability-sample-run.json

| 回放 | passed | review_required | failed | setup_error |
| --- | --- | --- | --- | --- |
| 正确回放 | 7 | 6 | 21 | 2 |
| 错误回放 | 0 | 0 | 34 | 2 |

- **错误回放 0 次通过**：这是本批最重要的底线证据。
- setup_error 的 2 条（R2-A06 / R2-S05）是「故意缺输入」边界用例（required_inputs），按设计在 setup 阶段报缺，不是缺陷。
- 正确回放仍有 21 failed 的原因：多数样本要求真实工具调用、产物文件或团队委派证据。受控回放只补了可从断言推导出的工具调用，无法凭空造出团队事件与完整产物，因此判失败或待复核——这正是「缺证不能通过」的预期行为，不是判分器把正确结果判错。
- 回放统一使用 Agent 执行路径：团队与 Skill 的目标语义未在回放中启用（见 §6）。

## 4. 评分器反例

- grader-counterexamples-v2：14 条，全部 qualification != qualified（12 disqualified + 2 review_required）。
- 反向对照：GE-02 / GE-03 / GE-07 的正确输出被判 qualified，说明判分器不是「全判失败」。
- 覆盖的典型错误：团队一句话自报完成、无产物自报已生成、关键词冒充 JSON、伪造 usage、缺 Trace、缺 usage、工具参数错误、注入越界、团队越权工具、超预算、里程碑部分通过。

## 5. UI / API / HTML / JSON 一致性抽查

- GET /runs/{id}/metrics 返回 {metrics_version:2, metrics, coverage, scope}；运行详情页用同一响应渲染记分卡。
- 未知分母：组件显示 Unknown，不补 0（evaluation-reliability.test.tsx 覆盖）。
- 缺 Trace / 缺 usage：分别有独立提示（trace-notice / usage-notice）；证据不全时不渲染综合质量结论（no-quality-conclusion）。
- 用例判据保存：PATCH /cases/{id} 走 base_revision CAS；集成测试验证 base_revision=2 与 evaluation.version=2 被写入。
- 配对比较：运行页创建 baseline/candidate 比较，逐 case 显示 improved/regressed/unchanged/incomparable 与原因（集成测试覆盖）。
- HTML/JSON 导出沿用既有报告读模型，本轮未改格式。

## 6. 团队抽核（未完成，明确阻塞）

- 团队目标必须注入 team_runtime_factory；gateway 未接线，团队 attempt 会 setup_error（TEAM_RUNTIME_UNAVAILABLE），不会假通过。
- 因此「Leader 与成员 Trace / 工具与物理请求 / usage 同和 / attempt 之外写入为零 / 取消后全部退出」本轮无法实测。
- 这是 T14 的阻断项，不能写成「已通过」。

## 7. 真实模型与人工校准（未完成）

- 真实模型 6 attempt 冒烟未执行（无凭据）。
- gold 未人工确认 → 校准保持 review_required；不得由 AI 代签。
- 36×3=108 attempt 与双版本 216 attempt 的预算未启动。

## 8. 结论

软件回归与「错误必须被判错」的底线已通过；完整交付仍被三项阻塞：团队执行栈接线、真实模型验收、人工 gold 确认。在上述阻塞解除前，不得宣称「完整评估系统已交付」。
