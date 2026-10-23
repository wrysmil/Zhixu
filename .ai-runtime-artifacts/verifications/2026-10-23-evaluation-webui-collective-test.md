---
artifact: collective-test
route: verification-before-completion
batch: agent-evaluation-webui-group1
created_at: 2026-10-23
status: pass-with-known-failures
scope: GROUP-1（WU-01~WU-09）
---

# GROUP-1 集体测试：评估模块后端（目标体系 / 真实模型 / 总时限 / Excel / HTML 报告 / gateway 内嵌）

## 0. 范围与分支

- 工作树：`D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation`
- 分支：`harness/agent-evaluation-calibration`（base `627bc9f` = 已合并的 feature/desktop-app）
- 本批提交：
  | 提交 | 内容 |
  | --- | --- |
  | `e62a330` | WU-01 S7：profile 凭据不落运行快照，boot 时从内存重建 |
  | `db80283` | WU-02 从 gateway 配置构建真实模型评估 profile |
  | `6ee0407` | WU-03 目标快照 + 总时限（超时批量停止，`timed_out` 收口） |
  | `a969fa2` | WU-04/WU-05 Agent/Skill 目标冻结注入 + 团队 runner 进程内执行 |
  | `a7d18ac` | merge：PW-1 Excel 数据集纯函数 |
  | `36fb2b0` | merge：PW-2 HTML 汇总渲染函数 |
  | `356c22f` | WU-06b/07b/08 数据集与报告路由 + gateway 内嵌装配（profiles / 团队 runner / CORS） |
  | `058b56c` | 评估服务绑 localhost（SameSite=Strict 会话口径修正） |

## 1. 检查表执行结果

| 检查项 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量测试 | `.venv/Scripts/python.exe -m pytest tests/evaluation -q` | **740 passed / 5 failed**（5 条全部为 `test_calibration_jobs.py` 既有 WIP，见 §3） |
| 目标与注入测试 | `pytest tests/evaluation/test_targets.py -q` | 14 passed（WU-03/04/05 新增用例全绿） |
| 数据集测试 | `pytest tests/evaluation/test_datasets.py -q` | 9 passed（PW-1） |
| 报告测试 | `pytest tests/evaluation/test_reports.py -q` | 23 passed（PW-2） |
| 模型 profile 测试 | `pytest tests/evaluation/test_profiles.py -q` | 3 passed（PW-3） |
| 静态检查 | `ruff check nanobot/evaluation nanobot/cli/gateway_runtime.py nanobot/webui/gateway_services.py` | All checks passed |
| 类型检查 | `basedpyright nanobot/evaluation nanobot/cli/gateway_runtime.py nanobot/webui/gateway_services.py` | 见 §4（与基线对比，不劣化） |

## 2. 真实冒烟（内嵌装配 + 浏览器同款请求路径）

脚本：`%TEMP%/zhixu-eval-smoke.py`（装配 `create_eval_app`，绑定 8791，允许 Origin `http://127.0.0.1:5173`）

| 步骤 | 期望 | 实测 |
| --- | --- | --- |
| CORS 预检 `OPTIONS /auth/login` | 204 + `Access-Control-Allow-Origin` 精确等于来源 | 204 + `http://127.0.0.1:5173` |
| 登录 | 200 + csrf_token | 200 + csrf |
| `GET /profiles` | 200，含真实模型 profile | 200，3 个（`model:MiniMax-M3.2` + 受控执行器/评分器） |
| `GET /datasets/template` | 200 + xlsx | 200，5703 字节，xlsx content-type |
| `POST /datasets/import` | 201 + suite | 201（导入模板生成 suite「我的数据集」） |
| `GET /reports/summary?suite_id=…` | 200 + HTML | 200，739 字节 HTML |
| `GET /health` | 200 | 200 `{"status":"ok"}` |

冒烟 FAILURES 计数：**0**。

## 3. 既有失败（不是本批引入，不在 GROUP-1 范围）

`tests/evaluation/test_calibration_jobs.py` 5 条：校准作业用例 WIP（基线提交 `c93a181` 即如此，任务说明「7 条里 5 条未收口」）。本批未触碰校准域代码。

## 4. 类型检查

见 §1 表格；`nanobot/evaluation` 基线为 291 errors（272 条 `reportUnknown*`）。本批新增模块（datasets/profiles）与改动文件不新增错误类别。

## 5. 并行审查

- 代码审查（reviewer）：进行中 → 产物 `.ai-runtime-artifacts/reviews/2026-10-23-evaluation-webui-code-review.md`
- 安全审查（security-auditor）：进行中 → 产物 `.ai-runtime-artifacts/reviews/2026-10-23-evaluation-webui-security-review.md`

## 6. 结论

GROUP-1（WU-01~WU-09）后端范围：**通过**（含 5 条既有失败与两项并行审查结论）。前端（GROUP-2 / WU-10~WU-13）尚未开始，需用户确认后进入下一批。

## Next

- 审查结论 → 有 BLOCK 项则修复后复审
- 前端批次 → 说「开始实现 GROUP-2」
