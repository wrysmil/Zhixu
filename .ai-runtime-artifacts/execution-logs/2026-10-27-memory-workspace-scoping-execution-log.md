---
artifact: execution-log
route: orchestration:dispatcher-workflow
plan: .ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-plan.md
dispatch: .ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-dispatch.md
created_at: 2026-10-27
status: in-progress
branch: harness/agent-evaluation-calibration
---

# 执行日志 — 记忆工作区隔离改造

## 批次 GROUP-1

| WU | 状态 | 结果 |
| --- | --- | --- |
| WU-01b 稳定工作区身份模块 | done | `nanobot/workspace_identity.py` + 8 用例全绿 |
| WU-01c 隔离键委托身份模块 | done | `provenance.py` 增量追加 4 函数 + 10 用例重写；18 passed |
| WU-01d episode 列与查询过滤 | done（worker 被会话中断终止，产物已落盘且全量绿） | `_SCHEMA_VERSION='3'`、`idx_episodes_workspace`、repository 7 处 `workspace_ids` 过滤、`known_workspace_ids` |
| WU-02 提取写入按工作区打标 | 未开工 | worker 在落盘前被中断，`extractor.py` 保持原样，无半成品 |
| WU-03 检索链路可见集合 | 未派发 | 依赖 WU-01d（已满足） |
| WU-04 面板与工作台归属口径 | 未派发 | 依赖 WU-01d（已满足） |
| WU-05 前端筛选与编辑态改归属 | 未派发 | 依赖 WU-04 |

## 时间线

| 时刻 | 事件 |
| --- | --- |
| 18:10 | plan/dispatch 落盘并提交（`1290f86`）；WORKTREE-INIT 建 3 个 worktree |
| 18:15 | 派 WU-01（存储基座）；用户复核隔离键 → 改为稳定 id，改写 plan D1/D8/D9 |
| 18:25 | 派 WU-01b（身份模块，与 WU-01 文件面不冲突） |
| 18:40 | 用户指出 worktree 过多 → 停 WU-01，改为**主 checkout 顺序执行**，取消集成 worktree；清理 3 个 worktree + 3 个临时分支 |
| 18:42 | 派 WU-01c（Task 1b，主 checkout）；派 WU-01d（Task 2/3，主 checkout） |
| 18:48 | WU-01c 完成（18 passed）；`tests/memory` 全量 **925 passed** |
| 18:55 | 用户要求写交接文档并提交 → 落盘 HANDOFF.md 与本执行日志 |

## 关键变更史

| 决策点 | 变更 | 触发 |
| --- | --- | --- |
| 隔离键 | path 哈希 → 稳定工作区身份 id（marker 文件） | 用户复核：仓库已有同构机制（`SessionManager`），path 哈希在改名后永久失联 |
| 归属可改 | 新增 `update_memory(workspace=...)` | 用户拍板：允许事后改归属 |
| 执行模型 | 每 WU 一个 worktree → 主 checkout 顺序执行 | 用户指出过度工程；且基线提交缺并行会话的新代码，worktree 方案被迫引入手工「播种文件」步骤 |
| 移植 | 7 步 patch 移植 → **取消** | worker 直接在主 checkout 增量编辑后，移植与冲突解环节整个消失 |

## 验证记录

```bash
.venv/Scripts/python.exe -m pytest tests/memory tests/test_workspace_identity.py -q
# → 925 passed in 19.92s
```

- `ruff check nanobot/`：11 条**既有**报错（全在并行会话未提交改动的文件，非本轮引入）
- `basedpyright`：`provenance.py` / `workspace_identity.py` / `models.py` 等本轮文件 0 error；仓库整体 2593 条既有诊断

## 性能实测（脚本见 `.ai-runtime-artifacts/research/`）

FTS5 加 `workspace_id` 过滤：1k +0.05ms / 10k −1.96ms / 50k −11.13ms → **零成本**。
Chroma `where` 过滤：+1~18ms/查询 → **弃用**，改在 SQLite 侧判定可见性。
身份解析：首次 ~1ms（写 marker），之后 µs 级（进程内缓存）。**对首轮对话无可感知影响。**

## 待办

见 `.ai-runtime-artifacts/execution-logs/HANDOFF.md` §3（WU-02 → 03 → 04 → 05）与 §6（8 条坑与风险）。
