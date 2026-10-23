---
artifact: dispatch
route: orchestration
plan: .ai-runtime-artifacts/plans/2026-10-23-evaluation-webui-plan.md
created_at: 2026-10-23
status: running
approved: true
approval_evidence: 用户在本会话说「开始实现 WU-01~09」+「并行」
---

# DISPATCH：评估模块 GROUP-1 并行实现

## 执行图

GROUP-1（并行）:
  MW-1: WU-01+03+04+05 主链（S7/目标/总时限/Agent+Skill 注入）| 文件: nanobot/evaluation/{services,schemas,scheduler,repository,worker,runtime_factory}.py, nanobot/agent/loop.py, nanobot/nanobot.py, nanobot/sdk/runtime.py, tests/evaluation/{test_services,test_targets,test_timeout}.py | 依赖: 无 | wu_type: feature | agent_role: coder | cwd: wt-agent-evaluation（主工作树）
  PW-1: WU-06a Excel 数据集纯函数 | 文件: nanobot/evaluation/datasets.py, tests/evaluation/test_datasets.py | 依赖: 无 | wu_type: feature | agent_role: coder | cwd: wt-eval-datasets
  PW-2: WU-07a HTML 汇总渲染纯函数 | 文件: nanobot/evaluation/reports.py, tests/evaluation/test_reports.py（只追加） | 依赖: 无 | wu_type: feature | agent_role: coder | cwd: wt-eval-reports
  PW-3: WU-02 profiles 构建（不含 gateway 注入） | 文件: nanobot/evaluation/profiles.py, tests/evaluation/test_profiles.py | 依赖: 无 | wu_type: feature | agent_role: coder | cwd: wt-agent-evaluation

收口（Leader 串行，MW-1/PW 合并后）:
  WU-06b/07b: server.py 路由接线 + repository.get_suite + services.summary_report + create_eval_app(team_runner/profiles) 透传
  WU-08: gateway 装配（profiles + team_runner 注入 `_start_embedded_evaluation`）+ 在途 CORS 改动收口
  WU-09: 全量回归 + verification + collective-test + 审查扇出

## 沙箱与 Git 纪律

- WORKTREE-INIT：`harness/wt-eval-datasets`、`harness/wt-eval-reports`（base=627bc9f）已建。
- 共享 venv 纪律：worker 用 `D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation/.venv/Scripts/python.exe`，cwd 必须是各自 worktree（cwd 优先导入本树 nanobot，已验证）。
- **worker 允许本地 commit（不 push）**：偏离「子 Agent 默认不 commit」的原因——多 worktree 隔离下，未提交改动无法合并；Leader 只 merge 不代抄。commit 用显式路径 `git add <files>`，**禁止 `git add -A` / `git add .`**。
- MW-1 与 PW-3 共享主工作树：**文件不相交**；双方提交前必须 `git status --porcelain` 核对，只 add 自己的文件。在途未提交改动（server.py CORS / gateway_runtime / test_runtime_factory）不属于任何 worker，禁止打包进 commit。

## 验证基线

- `pytest tests/evaluation -q`：702 passed / 5 failed（test_calibration_jobs.py WIP，不算回归）。
- 每 WU 收口命令见 plan 各 WU Step。

## Next

- worker 返回 → Leader 整合、merge、接线、WU-08/09、尾盘（collective-test + reviewer/security-auditor 扇出）。
