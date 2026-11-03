---
artifact: collective-test
route: orchestration.collective-closeout
skills: [verification-before-completion, test-driven-development]
skills_evidence: [.claude/skills/verification-before-completion/SKILL.md, .claude/skills/test-driven-development/SKILL.md]
source: [.ai-runtime-artifacts/plans/2026-11-02-memory-reference-fix-plan.md]
created_at: 2026-11-02
status: passed
---

# 引用计数与画像来源：集体验证

最终变更已从隔离 worktree 相对起始快照增量应用回主 checkout，保留原有未提交代码和真实记忆数据；无提交、推送。

## 主目录最终验证

`.venv/bin/python -m pytest tests/memory/ tests/agent/test_consolidator.py tests/agent/test_context_builder.py tests/agent/test_loop_working_memory.py tests/agent/test_memory_visibility.py tests/agent/test_memory_store.py tests/agent/tools/test_memory_search_tool.py tests/evaluation/test_memory_adapter.py tests/evaluation/test_memory_worker.py tests/evaluation/test_memory_checkpoints.py tests/evaluation/test_memory_end_to_end.py -q`

结果：**1250 passed, 21 warnings in 50.27s**。包含记忆全套、Consolidator/context 构建、记忆工具、评测 adapter/worker/checkpoint 和真实服务/子进程端到端场景。警告为已有 aiohttp AppKey/RequestKey 与向量模型接口 FutureWarning。

本任务 21 个改动文件的 ruff：All checks passed；git diff --check 通过。

## 静态检查的既有问题

- 全仓库 ruff：5 个既有问题，位于 cli/commands.py、memory/__init__.py、memory/repository.py，修复前后相同；未越界清理。
- 改动相关7个生产文件 basedpyright：修复前102 errors，修复后99 errors；按文件、规则和消息多重集合比较，新增诊断0。agent/memory.py 单独0 errors。
- 因存在基线问题，不宣称全仓库 lint/typecheck 通过。

## 红绿证据

- 引用统计最初7 failed/1 passed：数据库计数实际0，期望1；修后真实工具、情节、原子自增与最终预算统计通过。
- 回忆污染最初9 failed/3 passed：四条旧事实被重复写入或标冲突；修后拒绝无证据/工具/助手/回忆问句来源。
- token-probe 副作用：只估算也使计数变为(1,1,0)，修后状态探测0次，真实构建仍+1。
- experiences 类型绕过：4个坏类型入库回归先失败，限制为 EXPERIENCE/SKILL/ERROR 后通过。
- “记得以后推荐咖啡不要加糖”正向回归先失败，收窄裸“记得”回忆判定后通过。
- 新增回归共32条（引用11、画像21），独立审查员复跑32 passed。

## 边界

实际返回/注入次数不是模型 usefulness 评分；不恢复排序权重；情节不级联关联事实；用户原文证据校验不承担任意语义蕴含验证。一般用户重复陈述同义事实的自动合并不在本次范围。既有重复记录保留。

本次未重启用户正在运行的桌面/网关进程、未跑付费模型；运行中进程需重新加载才能采用源码变更。全仓库完整 tests/ 未执行。
