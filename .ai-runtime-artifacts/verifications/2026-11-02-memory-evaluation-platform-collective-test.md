---
artifact: collective-test
plan: .ai-runtime-artifacts/plans/2026-11-02-memory-evaluation-platform-plan.md
branch: codex/memory-evaluation-platform
worktree: D:/studyspace/project/Zhixu-memory-evaluation-platform
base: ce595d6
created_at: 2026-11-02
status: offline-accepted
---

# 记忆评测平台第一阶段 集体验收（2026-11-02）

## 1. 交付矩阵

| WU | 交付 | 独立复核 |
| --- | --- | --- |
| WU-01 | 专项模型、协议、仓储、migration 4 | 289 passed |
| WU-02 | MemoryClock 与完成信号 | 90 passed |
| WU-03 | zhixu adapter、checkpoint 快照/恢复 | 57 passed |
| WU-04 | 三轨 Worker、runner、worker_bridge | 29 passed + 旧链路 87 passed |
| WU-05 | 21 项记忆指标、评分、Judge 材料 | 144 passed |
| WU-06 | LongMemEval 导入器、core 120 / smoke 20 / calibration 20 | 28 passed + 41 passed |
| WU-07 | 分批实验、预算、幂等恢复、冻结清单 | 79 passed |
| WU-08 | 历史配对、cluster 区间、报告 | 89 passed |
| WU-09 | 17 条记忆路由、错误码、TS 客户端 | 115 passed + TS 17 passed |
| WU-10 | 题库、场景编辑、实验表单、指标值组件 | TS 13 passed + tsc/eslint |
| WU-11 | 记忆专项入口、详情、比较、回放、复核 | TS 60 passed + build |
| WU-12 | 离线端到端、安全边界 | 19 passed |

## 2. 软件检查

- 后端：`pytest tests/evaluation/ tests/memory/ -q` → **2345 passed**（初次 2344 passed / 1 failed 为 `test_validation` 字段集未同步 `memory_track`，已修正）。
- 前端：`npx vitest run` → **1980 passed / 2 skipped / 1 failed**；唯一失败 `settings-providers` 为全量并发下的偶发，隔离复跑 **8 passed**，与本改动无关。
- 构建：WU-11 执行 `tsc -p tsconfig.build` + `vite build` + eslint 改动文件 → 通过。
- ruff：本次新增/改动文件 **All checks passed**；`ruff check nanobot/` 余 11 条均在未改动基线文件（cli/commands.py、memory/__init__.py、memory/repository.py、lifecycle.py:359 的既有 N806）。

## 3. 验收要点

- 120×3×3 → 1080 planned trials / 9 子 run，单批 120 trials ≤ 200 上限；组预算、取消回收、重启不重建已入队 run。
- 三轨（extraction / retrieval / end_to_end）均走真实服务、DB 与子进程；smoke 20 场景通过；四种受控故障（抽取漏项、召回漏项、注入截断、回答误用）可分别定位。
- gold 不进入执行载荷；历史注入指令不能控制 Judge；导入路径/大小受限；报告 XSS 转义；namespace 缓存与 singleton 隔离；删除不复活、共享正例可用。
- 统计方向与采用建议分离；cluster<20 不出区间；关键泄漏/删除复活压过均分收益。

## 4. 未完成 / 明确缺口

1. **真实模型质量验收未完成**：未使用用户 profile 与预算跑 20 场景三基线、核心 120×3 与 LongMemEval 全量；不得据此宣称质量达标。
2. **basedpyright 与全仓 `pytest tests/` 未执行**；本 worktree 只跑了评估与记忆相关目录。
3. **浏览器人工实测截图**（导入→运行→对比→回放→复核）未采集。
4. `negative_write_success` 在 closed_world + 空 extraction_facts 下仍不可评（契约缺口）。
5. 真实向量链路待嵌入模型联调；`clock_coverage` 对 since_days SQL 窗口仅部分覆盖。
6. WU-12 计划中的前端集成测试 `memory-evaluation-integration.test.tsx` 未单独新增，现有 report/replay/settings 测试已覆盖主要交互。
7. 独立 security-audit / perf-audit 未执行；WU-12 安全用例为工程内断言。

## 5. 提交状态

所有改动保留在 worktree 工作区，**未 commit / 未 push / 未合并**。提交范围与 PR 需用户另行授权。
