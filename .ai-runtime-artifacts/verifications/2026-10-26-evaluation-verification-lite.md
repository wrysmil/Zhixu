---
artifact: verification-lite
route: verification-before-completion
skills:
  - verification-before-completion
  - systematic-debugging
  - source-driven-development
  - agent-browser
skills_evidence:
  - .claude/skills/verification-before-completion/SKILL.md
  - .claude/skills/systematic-debugging/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - C:/Users/Huangqh/.agents/skills/agent-browser/SKILL.md
source:
  - 用户要求启动项目，继续测试评估功能，覆盖 Skill 和团队。
  - .ai-runtime-artifacts/verifications/2026-10-25-evaluation-target-testsets.md
created_at: 2026-10-26
tier: 1
status: partial
---

# 项目启动与 Agent / Skill / 团队真实评估

## 环境与改动

- Windows / Python 3.13.3 / Node 20.20.2 / Vite 5.4.21；当前本机评估库初始无集合、无运行，因此没有复用昨日文档中的运行 ID。
- 业务代码只改 `nanobot/cli/gateway_runtime.py:1045`：`channels.webui_gateway()` 改为属性读取 `channels.webui_gateway`。
- 修复前启动稳定报 `TypeError: 'GatewayServices' object is not callable`；定义在 `channels/manager.py` 为 `@property`，同文件既有消费者也采用属性访问。
- 修复后 gateway 8765、health 18790、评估 API 8790 均正常；末次 `/health` 返回 `ready: true`、`websocket: running`。
- 前端以 5173 开发服务运行。旧 `webui/node_modules/.vite` 无写权限，采用 Vite API 临时指定 `.ai-runtime-artifacts/verifications/2026-10-26-vite-cache`，不修改项目配置。
- 后端进程 session 21663；前端进程 session 51312。本次未提交、未推送。

## 真实模型评估

使用当前配置提供的 `model:MiniMax-M3.2` 档案，其 API 返回底层 model 为 `MiniMax-M3`、provider 为 `minimax`；未改模型配置。
所有用例为 live 模式，普通执行器只允许 `read_file`，团队使用现有 `writing-team` 的执行链路。
不启用 Judge，规则结论只代表所声明断言通过，不代表综合质量或性能合格。

| 集合 | Run ID | 用例与规则结果 | Token / 请求 |
| --- | --- | --- | --- |
| 1026回归 · 普通对话 | `55a8d8c8-e7df-45d7-a9e6-cd28e79ff580` | 算术 42、读取 marker.txt 并调用 read_file；2/2 | 7,170 / 3 |
| 1026回归 · Skill 提纲 | `d556f9aa-e14f-4f7d-8d05-6296b0e12d26` | article-outline 的提纲格式、缺素材提示；2/2 | 58,147 / 8 |
| 1026回归 · 团队协作 | `f84b662f-10e4-4f93-b768-ed645c4973ef` | 指定 writing-author 写落叶，返回含金黄；1/1 | 未采集 |

三个运行均 completed。普通用例执行约 4.4s / 5.9s，Skill 约 57.1s / 78.7s，团队约 15.5s（attempt timing）。

## 实测暴露的问题

1. **团队无可观测轨迹和 Token**：execution 返回 0 节点、sealed=false，coverage 为 partial/unavailable；usage 返回空数组，原因「未采集 model.call_completed 事件」。因此不能从评估报告独立核验委派过程或成本。团队回答自报完成了 writing-author 委派，不能拿这段回答替代 Trace 证据。
2. **Skill 身份归属缺失**：画布 agent_id/name 均为空，execution usage_attribution=unavailable；run usage attribution=partial。总 Token 已实报，但无法完整按 Agent 对账。
3. **Skill 效率问题**：提纲格式用例有 6 次模型请求、10 次 read_file 调用，其中 6 次失败；最终输出通过格式关键词断言，不能掩盖中途反复查找文件的代价。两例总计 58,147 Token。
4. **团队输出指令遵循不足**：要求只返回一句话，最终回答却包含派工、回执、缺口与最终句子等约 322 字的过程说明。现有 `answer_contains 金黄` 断言仍判 passed，量表覆盖不足。
5. **Agent 展示信息不完整**：运行完成后 Agent 活动节点仍显示 unknown，名称为空；模型与 read_file 节点可显示 succeeded。Token 按角色展示时 model 栏为 Unavailable，但按请求统计的总量存在。

上述问题已记录，本轮没有扩展修改团队 Trace、Skill 加载或评分体系。

## 自动化验证

| 命令 | 实际结果 |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest tests/cli/test_gateway_runtime.py tests/webui/test_eval_team_runner.py tests/evaluation/test_targets.py tests/evaluation/test_observability.py tests/evaluation/test_observability_routes.py -q --basetemp=.ai-runtime-artifacts/verifications/2026-10-26-pytest` | 65 passed |
| `webui: npm run test -- --no-cache src/tests/evaluation-api.test.ts src/tests/evaluation-settings.test.tsx src/tests/evaluation-observability.test.tsx` | 17 passed，exit 0 |
| `.venv/Scripts/python.exe -m ruff check nanobot/cli/gateway_runtime.py` | All checks passed |
| `.venv/Scripts/python.exe -m pytest tests/evaluation tests/webui/test_eval_team_runner.py -q --basetemp=.ai-runtime-artifacts/verifications/2026-10-26-eval-pytest` | 811 passed、8 failed；不能判全量通过 |

第一次前端测试 17 个断言通过，但写旧缓存 results.json 出现 EPERM、退出 1；关闭测试缓存后重跑成功。

扩大回归的 8 个失败：

- `test_calibration_jobs.py` 5 个：`test_job_completes_and_records_a_passing_gate`、`test_over_generous_judge_completes_but_fails_the_gate`、`test_cancelling_a_job_leaves_agreement_null`、`test_item_evidence_is_the_frozen_gold_payload`、`test_http_created_job_runs_to_completed`。输出包含 `PROFILE_NOT_FOUND: judge-1`，结果取消或缺少结果。昨日验证文档也记录此文件 5 个失败，但本轮没有回退 HEAD 做独立基线复现，故只报告本次实际失败。
- `test_services.py` 3 个：`test_pipeline_grades_and_passes_a_good_case`、`test_failing_required_assertion_yields_failed_not_error`、`test_run_read_and_attempt_read_match_contract_shapes`。读取首个失败测试的 evaluation.db 证实 worker result 为 `FileNotFoundError`，失败路径为深层 session checkpoint 临时文件（Windows 长路径问题的线索，尚未通过短路径对照验证）。后两例是否完全同因尚未独立确认。

本轮启动修复有修复前崩溃、修复后健康服务的直接证据；没有新增测试提交，也未满足技能要求的 test-first Git 提交证明，不声称完成严格 TDD。
未跑全仓 Python、类型检查或生产构建。

## 浏览器与报告证据

评估入口：`http://127.0.0.1:5173/#/settings?section=eval`。
真实页面验证了运行列表、完成进度、用例切换、普通 Agent 画布、read_file 工具表、Skill 画布和团队降级空态。
浏览器 errors 列表为空。首次使用窄视口验证后切换 1440×1000 检查桌面视图。

证据目录：`.ai-runtime-artifacts/verifications/2026-10-26-eval-results/`。

- `agent-canvas.png`、`tool-calls.png`、`skill-canvas.png`、`team-canvas.png`。
- 三个 `<run-id>.html` 为评估 API 导出的 HTML 报告；三个 `<run-id>.details` 为运行、attempt、usage、execution 的原始 API 结果。
- `2026-10-26-eval-check.py` 为本轮验证脚本。口令由项目 helper 在内存中传递，不写入脚本或产物。

## Next

- 本轮测试完成，服务保持运行；全量回归存在失败，不能视为整体交付通过。
- 后续优先修复团队 Trace / Token 采集和 Skill 归属，再增强断言与检查 Skill 读取失败原因。
