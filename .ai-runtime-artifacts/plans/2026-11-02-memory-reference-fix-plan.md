---
artifact: implementation-plan
route: writing-plans -> orchestration
skills: [writing-plans, orchestration, test-driven-development]
skills_evidence: [.claude/skills/writing-plans/SKILL.md, .claude/skills/orchestration/SKILL.md]
source: [.ai-runtime-artifacts/specs/2026-11-02-memory-reference-and-recall-contamination-spec.md]
approved: true
approval: "用户：按此直接实现，spec/plan无需再次确认（推荐）；直接执行快点"
dispatch: 2026-11-02-memory-reference-fix-dispatch.md
created_at: 2026-11-02
---

# 记忆引用与回忆污染修复实施计划

Goal：实际输出/注入的事实及情节计数 +1；阻止旧检索内容再次成为画像。
Architecture：计数在最终选择之后经 adapter 原子写入；画像候选携带并校验本批 user 原文证据，experience/episode 上下文保留。
Tech Stack：Python 3.13.9、SQLite 3.52.0、pytest 9.1.1、Pydantic 2.13.5；无新增依赖。

## Task 1 / WU-01：计数

文件：nanobot/memory/retrieval/store_adapter.py、engine.py、nanobot/agent/context.py，相关 retrieval 测试或新增 tests/memory/test_reference_tracking.py。

- [x] 先写失败测试：最终工具返回中事实/情节 +1；search_hits 和预算丢弃不变；重复条目只计一次；跨工作区不能写；统计错误不影响回答。
- [x] 执行 .venv/bin/python -m pytest tests/memory/test_reference_tracking.py -q，观察失败。
- [x] adapter 接收最终 MemoryHit 序列；按 kind/id 去重，使用 UPDATE memories/episodes SET access_count=access_count+1 WHERE id=? AND workspace_id=?。事实更新 last_accessed_at；不更新 updated_at。
- [x] engine 在最终 top 选择之后统计；context 在最终 selection.included 后统计。search_hits 不写。
- [x] 跑新测试与 retrieval/context 现有测试。

## Task 2 / WU-02：画像来源证据

文件：nanobot/memory/profile_extractor.py、extractor.py、prompts.py，相关 extractor 测试。

- [x] 先写失败集成测试：四条旧事实；新批 user 只问猫，工具与 assistant 复述旧画像；模型尝试返回旧事实；原四条不增、不产生 conflict。
- [x] 增加正向与边界测试：真实本批用户原文有效；伪造/缺失证据拒绝；experience 和 episode 可继续记录。
- [x] 画像候选增加 evidence 字符串，语义 prompt 明确只能引用当前批 user 原文；解析/过滤阶段验证来源。
- [x] 模型输出无证据不提升画像，不影响独立 experiences。更新原有合理 fixture 使其携带真实用户证据；不放宽新校验以迎合旧 fixture。
- [x] 跑新测试与 memory 提取相关测试。

## Task 3：集成、审查、交付

- [x] 全量 tests/memory/，相关 agent tests，ruff 与 basedpyright。失败由对应 WU 继续修。
- [x] 独立 reviewer/security 只读审查；修复实际发现。
- [x] 将 worktree 相对本次起始快照的增量应用回主 checkout，不带回或覆盖别人的改动。
- [x] 主 checkout 重跑必要检查；落盘 collective-test、review、execution-log，不提交不推送。
