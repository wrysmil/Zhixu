---
artifact: code-review
route: orchestration.dispatch
plan: .ai-runtime-artifacts/plans/2026-10-26-agent-evaluation-reliability-plan.md
created_at: 2026-10-26
reviewer: leader-self-review
independent_review: not-satisfied
status: completed-with-blockers
---

> 说明：本报告是 **Leader 自查**。原计划的独立审查单元两次派发均因服务高负载失败（详见执行日志），
> 因此本文不冒充「独立审查」结论；「独立审查」这一验收项仍记为**未满足**。

# 评估可靠性代码审查（自查）

## 1. 审查范围与方法

- 范围：git diff 2fb9e5d..HEAD（9 个提交），重点 nanobot/evaluation/**、samples/**、webui 评估组件与 API。
- 方法：逐文件阅读 diff；对关键路径跑真实命令验证，不凭印象。
- 实际执行的命令与结果：
  - `pytest tests/evaluation tests/teams tests/cli/test_eval_commands.py -q`：1317 passed / 3 failed（3 条失败经定位为 Windows 260 路径上限，非代码逻辑错误；修复后见 §2-2）。
  - `pytest tests/evaluation tests/teams tests/webui/test_eval_team_runner.py tests/cli/test_eval_commands.py tests/cli/test_gateway_runtime.py -q`：1332 passed / 0 failed。
  - `ruff check nanobot/evaluation`：All checks passed。
  - `npx tsc -p tsconfig.build.json --noEmit`：退出码 0。
  - 36 条样本受控回放（.ai-runtime-artifacts/verifications/2026-10-26-run-reliability-samples.py）：错误回放 0 通过。

## 2. 问题清单

| 编号 | 严重度 | 位置 | 问题 | 影响 | 建议 |
| --- | --- | --- | --- | --- | --- |
| CR-01 | 阻断（已记录） | nanobot/evaluation/services.py:176-177, 682-686 | 全仓没有注入 team_snapshot_provider / team_runtime_factory；团队目标在入队即 TEAM_NOT_FOUND，即使入队成功也会 TEAM_RUNTIME_UNAVAILABLE | 团队目标不可用，T14 团队抽核无法进行 | 接入生产团队执行栈（复用 nanobot/teams 的 Leader 回合与成员执行），并用真实团队跑抽核 |
| CR-02 | 已修复 | nanobot/evaluation/runtime_factory.py:152（sessions_root）、:420（session_key） | 会话 checkpoint 用 base64(完整 UUID) 命名，数据目录一深就超过 Windows 260，写盘报 FileNotFoundError（实测路径 276 字符） | 长路径下 attempt 执行失败，看起来像「Agent 能力失败」 | 已改为紧凑会话键 + runs/s 目录，实测同一目录 242 字符通过，并加了回归测试 |
| CR-03 | 已修复 | nanobot/evaluation/worker.py:792 | capability（技能清单/哈希、生效白名单、生效限额）只在内存，未写进 Trace | 报告侧无法回答「这次在什么能力边界下跑的」 | 已写进 attempt.started，并加测试断言 |
| CR-04 | 阻断（待验收） | — | 真实模型 6 attempt 冒烟未执行；gold 未人工确认 | 无法证明真实模型与 Judge 校准成立 | 配凭据后跑冒烟；人工确认 ≥10 条 gold |
| CR-05 | 重要 | .ai-runtime-artifacts/verifications/2026-10-26-run-reliability-samples.py | 回放统一走 Agent 执行路径，Skill/团队目标语义未端到端覆盖 | 36 条样本只验证了判据与证据链路，不证明三目标隔离 | 为 Skill 目标冻结技能、为团队目标提供快照后各跑抽样 |
| CR-06 | 建议 | nanobot/evaluation/runtime_factory.py:314 | _missing_required_inputs 只用 exists()，未做 resolve_inside | 符号链接指向 workspace 外时会被判定为「存在」；但不读内容，风险低 | 改用 resolve_inside 判定 |
| CR-07 | 建议 | nanobot/evaluation/services.py:1832 | 旧快照（无 explicit_limits）按 RunLimits 默认 12/120 收紧 | 旧运行已终态；rerun 走 explicit_limits，影响面小 | 给旧快照补 explicit_limits 或明确 legacy 标记 |
| CR-08 | 建议 | webui EvaluationRuns.tsx 比较区 | baseline 与 candidate 可选同一个 run，后端未拒绝 | 自比结果显示全 unchanged，可能误导 | 前端禁用同值，后端返回 422 |
| CR-09 | 建议 | webui EvaluationReviews.tsx + 后端 ManualReviewCreate | 人工复核只接受 passed/failed；前端对 review_required/not_evaluated 静默忽略 | 用户点击无反馈 | 禁用不可提交项并给提示 |
| CR-10 | 未发现 | — | 任务完成率与综合合格率混用 | — | 页面分栏、API 分字段，指标测试覆盖 |
| CR-11 | 未发现 | — | 未知分母补 0 | — | null → Unknown，evaluation-reliability 测试覆盖 |
| CR-12 | 未发现 | — | 缺证仍判合格 | — | Scorecard evidenceComplete 与 assessment 门槛一致；缺 Trace → review_required |

## 3. 三条底线判定

| 底线 | 判定 | 证据 |
| --- | --- | --- |
| 缺证不能通过 | 通过 | 36 条样本错误回放 34 failed + 2 setup_error，0 通过；缺 Trace/usage 分别提示且不渲染综合质量结论 |
| 关键词不能代替任务完成 | 通过 | 断言 + 里程碑 + 证据引用；反例 GE-03（关键词冒充 JSON）判 disqualified |
| 评分异常不混成 Agent 能力失败 | 基本通过 | measurement_errors 与 qualification 分离；not_evaluated / review_required 区分；**真实模型未验收，无法证明 Judge 校准在真实数据成立** |

## 4. 未覆盖与残余风险

- 团队执行栈：代码层有隔离实现，但生产路径不可达，团队安全与资源回收未实测。
- 真实模型：全部结论基于受控 provider 与 fixture。
- 前端：既有 7 条失败与本轮无关，未修复；本轮新增页面只做了组件级与集成级测试，未做真实浏览器端到端截图。
- 样本回放：仅 Agent 路径。

## 5. 结论

框架与回归可用，三条底线中前两条已通过、第三条受真实模型验收限制。完整交付被 CR-01（团队装配）、CR-04（真实模型与 gold）阻塞；这两项解除前不得宣称完整评估系统已交付。必须修复项：CR-01、CR-04。
