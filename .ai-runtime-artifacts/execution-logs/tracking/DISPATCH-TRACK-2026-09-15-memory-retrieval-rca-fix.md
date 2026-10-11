---
artifact: dispatch-track
route: orchestration:dispatcher-workflow
plan: .ai-runtime-artifacts/plans/2026-09-15-memory-retrieval-rca-fix-plan.md
dispatch: .ai-runtime-artifacts/plans/2026-09-15-memory-retrieval-rca-fix-dispatch.md
created_at: 2026-09-15
---

# DISPATCH-TRACK — 记忆检索恒空修复

## Git 沙箱

| 项 | 值 |
| --- | --- |
| WorktreeId | `wt-2026-09-15-memory-retrieval-rca-fix` |
| WorktreePath | `/Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/Agent-With-Memory/wt-2026-09-15-memory-retrieval-rca-fix` |
| Branch | `harness/wt-2026-09-15-memory-retrieval-rca-fix` |
| Base | `feature/memory-system` @ `20e4d94` |

## 追踪条目（append-only）

```text
[2026-09-15 00:00] WORKTREE-INIT | Leader | Status: completed
Detail: git worktree add -b harness/wt-2026-09-15-memory-retrieval-rca-fix 完成，基于 feature/memory-system @ 20e4d94
WorktreeId: wt-2026-09-15-memory-retrieval-rca-fix | WorktreePath: /Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/Agent-With-Memory/wt-2026-09-15-memory-retrieval-rca-fix | Branch: harness/wt-2026-09-15-memory-retrieval-rca-fix | Base: 20e4d94
Sub-agents: 0
Context: n/a
Output: .ai-runtime-artifacts/plans/2026-09-15-memory-retrieval-rca-fix-plan.md, .ai-runtime-artifacts/plans/2026-09-15-memory-retrieval-rca-fix-dispatch.md
Error: none
Next: DISPATCH-GROUP-1 并行派发 WU-01 / WU-02 / WU-03

[2026-09-15 00:00] DISPATCH-GROUP-1 | Leader | Status: started
Detail: 3 路并行派发（文件不相交，同 worktree）
GROUP: 1 | WU: WU-01,WU-02,WU-03 | ITER: 1 | STEP: implement
WorktreeId: wt-2026-09-15-memory-retrieval-rca-fix | WorktreePath: /Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/Agent-With-Memory/wt-2026-09-15-memory-retrieval-rca-fix | Branch: harness/wt-2026-09-15-memory-retrieval-rca-fix | Base: 20e4d94
Queue-remaining: WU-01, WU-02, WU-03
Reviewer: pending
Closeout: collective-test=pending verdict=n/a | code-review=pending verdict=n/a | status=pending
Sub-agents: 3
Context: n/a
Output: none
Error: none
Next: 等 3 个 WU 返回，逐条核对 Done Criteria 与 Skills 使用
```

[2026-09-15 00:00] WU-01-implement | Coder | Status: completed
Detail: store adapter + repository CJK 回退/时间归一 + engine 通道 warning + 装配点；
        另修复计划外缺陷 search_episodes 列名 updated_at → ended_at（schema v1 无此列）
GROUP: 1 | WU: WU-01 | ITER: 1 | STEP: done
Tests: DC1 8 passed；tests/memory/ 671 passed；DC5 全仓唯一装配点已是 adapter
Queue-remaining: none
Reviewer: separate-task（WU-01 自查委派独立 reviewer，verdict PASS）
Output: nanobot/memory/retrieval/store_adapter.py(新), nanobot/memory/repository.py,
        nanobot/memory/retrieval/engine.py, nanobot/cli/gateway_runtime.py,
        tests/memory/retrieval/test_store_adapter.py(新), tests/memory/test_search.py
Error: none
Note: 指出 plan 两处缺陷（Task 5 用例嵌套 connect() 会死锁；边界探针断言数学上不可能），
      以及 RCA §1.3 叙事不可复现。Leader 已实测复核全部成立。
Next: GROUP-1 全部归队 → 尾盘

[2026-09-15 00:00] GIT-COMMIT | Leader | Status: completed
Detail: git-xywh + project.git.md；提交 11 文件（+724/-12），剔除 webui/package-lock.json 与
        无主 memory/ 残留（删前核查为 0 行空库）
Worktree: /Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/Agent-With-Memory/wt-2026-09-15-memory-retrieval-rca-fix | Branch: harness/wt-2026-09-15-memory-retrieval-rca-fix
Output: commit c6f66ac
Error: none
Next: git push（用户已明确确认）

[2026-09-15 00:00] GIT-PUSH | Leader | Status: completed
Detail: git push -u origin harness/wt-2026-09-15-memory-retrieval-rca-fix（非受保护分支，未强推）
Output: origin/harness/wt-2026-09-15-memory-retrieval-rca-fix 新建
Error: none
Next: 尾盘 A 集体测试

[2026-09-15 00:00] CLOSEOUT-A | Leader | Status: completed
Detail: 集体测试 PASS（静止态执行）。tests/memory/ 671 passed；真实库端到端冒烟通过，
        RCA 症状 1（答「记忆空」）与症状 2（首轮不检索）双双消除；
        13 全量失败 + 12 ruff 错误经基线 A/B 证明存量、零新增
Tests: PASS
Output: .ai-runtime-artifacts/verifications/2026-09-15-memory-retrieval-rca-fix-collective-test.md
Error: none
Note: 发现 FTS(4 列) vs LIKE 回退(仅 content) 结果集不对称 —— 经逐行定位为 tags 命中，
      非索引漂移（integrity-check OK）；列为遗留项 LI-3
Next: 尾盘 B 并行审查

Detail: preprocessor 门禁放宽（_MEMORY_INTENT_HINTS，_CONTROL_ONLY/_KEEP_SHORT_HINTS 逐字未动）
        + memory_search 会话注入改用 ToolContext.sessions；生产代码净变更 2 文件 +39/-4
GROUP: 1 | WU: WU-02 | ITER: 1 | STEP: done
Tests: RED 6 failed → GREEN 36 passed；tests/memory/ 669 passed；DC4 手工复核与期望逐字一致
Queue-remaining: WU-01
Reviewer: separate-task（WU-02 已自查委派独立 reviewer，verdict PASS，无 Critical）
Context: n/a
Output: nanobot/memory/retrieval/preprocessor.py, nanobot/agent/tools/memory_search.py,
        tests/memory/retrieval/test_preprocessor.py, tests/memory/test_memory_search_tool.py
Error: none
Note: 2 项待 Leader 裁决 —— (a) DC3「ruff check nanobot/ All checks passed」字面未达成；
      (b) _MEMORY_INTENT_HINTS 裸子串匹配会放行「我的天哪」类寒暄（中性流量约 3%）。
Next: 等 WU-01 返回

[2026-09-15 00:00] BASELINE-VERIFY | Leader | Status: completed
Detail: 独立基线 A/B（git archive HEAD 干净导出，不用 git stash）复核 WU-02 的「存量问题」结论
Tests: 基线 ruff = Found 12 errors（与本批同集合）；基线 13 个失败用例名与本批 diff → IDENTICAL
Sub-agents: 1
Context: n/a
Output: /tmp 临时导出（已删除），结论写入本 track
Error: none
Note: 结论 —— 12 lint 错误 + 13 全量失败**均为存量**，本批零新增。
Next: Leader 裁决 DC3 口径

[2026-09-15 00:00] DECISION-DC3 | Leader | Status: completed
Detail: 裁决 —— 本批 lint 验收口径取「**不引入新 lint 错误**」，而非字面 All checks passed。
        依据：基线 HEAD 20e4d94 即为 12 错，且 12 错全落在 WU 允许清单之外
        （agent/context.py、cli/gateway_runtime.py I001、memory/__init__.py F401×6、
        memory/repository.py N806、webui/settings_routes.py）。
        lint 全量清理属独立范围，不在本批；已记入 execution-log 遗留项。
Output: none
Error: none
Next: 等 WU-01 返回后进入尾盘

Detail: identity.md 记忆段两分支均已重写（state.db 三表为主存储 / memory_search 为主检索入口 / 空 MEMORY.md ≠ 记忆为空）
GROUP: 1 | WU: WU-03 | ITER: 1 | STEP: done
WorktreeId: wt-2026-09-15-memory-retrieval-rca-fix | WorktreePath: /Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/Agent-With-Memory/wt-2026-09-15-memory-retrieval-rca-fix | Branch: harness/wt-2026-09-15-memory-retrieval-rca-fix | Base: 20e4d94
Tests: 198 passed, 1 skipped（pytest tests/ -k "identity or prompt"）；Jinja 渲染 state.db OK
Queue-remaining: WU-01, WU-02
Reviewer: pending
Sub-agents: 2
Context: n/a
Output: nanobot/templates/agent/identity.md（worktree 内）
Error: none
Note: WU-03 对 plan Task 7 文本有 1 词偏离（补回 "proactively"）。Leader 已核对
      tests/agent/test_identity_template.py:20 断言 "proactive" in text.lower() → 偏离正确，
      属 plan 文本缺陷，Leader 已回改 plan Task 7 并加「文本约束」注记。
Next: 等 WU-01 / WU-02 返回

[2026-09-15 00:00] GIT-COMMIT | Leader | Status: completed
Detail: git-xywh + project.git.md；提交 11 文件（+724/-12），剔除 webui/package-lock.json 与
        无主 memory/ 残留（删前核查为 0 行空库）
Worktree: /Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/Agent-With-Memory/wt-2026-09-15-memory-retrieval-rca-fix | Branch: harness/wt-2026-09-15-memory-retrieval-rca-fix
Output: commit c6f66ac
Error: none
Next: git push（用户已明确确认）

[2026-09-15 00:00] GIT-PUSH | Leader | Status: completed
Detail: git push -u origin harness/wt-2026-09-15-memory-retrieval-rca-fix（非受保护分支，未强推）
Output: origin/harness/wt-2026-09-15-memory-retrieval-rca-fix 新建
Error: none
Next: 尾盘 A 集体测试

[2026-09-15 00:00] CLOSEOUT-A | Leader | Status: completed
Detail: 集体测试 PASS（静止态执行）。tests/memory/ 671 passed；真实库端到端冒烟通过，
        RCA 症状 1（答「记忆空」）与症状 2（首轮不检索）双双消除；
        13 全量失败 + 12 ruff 错误经基线 A/B 证明存量、零新增
Tests: PASS
Output: .ai-runtime-artifacts/verifications/2026-09-15-memory-retrieval-rca-fix-collective-test.md
Error: none
Note: 发现 FTS(4 列) vs LIKE 回退(仅 content) 结果集不对称 —— 经逐行定位为 tags 命中，
      非索引漂移（integrity-check OK）；列为遗留项 LI-3
Next: 尾盘 B 并行审查

[2026-09-15 00:00] CLOSEOUT-B1 | Security-Auditor | Status: completed
Detail: 独立安全审查完成，verdict=APPROVE（无 Critical）。
        I-1 二阶提示词注入（本批**激活**的 sink）/ I-2 检索无 user-workspace 过滤 / M-1~M-8
Reviewer: separate-task（独立只读实例，与 coder 不同实例）
Output: .ai-runtime-artifacts/reviews/2026-09-15-memory-retrieval-rca-fix-security-review.md
Error: none
Note: Leader 独立复核后 —— I-1 属实（已复现）；I-2 **降级**为潜在缺口：实测
      MemoryExtractor.user_id 默认 'default' 且全仓库无覆写点，真实库 8/8 行均为
      ('default','default')，生产代码造不出跨 user 的行；配置仅启用 websocket 单渠道。
Next: 修 I-1 + M-1/M-2/M-3/M-4

[2026-09-15 00:00] SECURITY-FIX | Leader | Status: completed
Detail: 闭合 I-1（记忆内容入 system prompt 前清洗：clean_query + 孤立注入标签剥离 +
        markdown 标题剥离 + 换行折叠 + 500 字截断 + 不可信数据前导声明）；
        M-1 except 收窄（语法类 debug / 库级故障 warning，no such column 归 warning）；
        M-2 不再记 query 原文；M-3 {}→{!r}；M-4 search_episodes LIKE 转义
Tests: tests/memory/ 681 passed（671 + 10 新增安全用例）；真实库冒烟零回归；
       ruff 存量 12 错不变（零新增）
Output: commit d726dfa（engine.py / repository.py / test_injection_block_safety.py）
Error: none
Note: 「孤立 </memory> 逃逸」缺口由本批新增用例自己抓到，已补 _INJECTION_TAG_RE
Next: 等 B2 代码审查

[2026-09-15 00:00] ARTIFACT-INCIDENT | Leader | Status: resolved
Detail: 22:55:21 有 agent 在主 checkout 执行 git stash -u（stash@{0}
        "baseline-check-2026-09-15"），把**主 checkout 全部未提交工作**（含本批 4 个
        AI 产物 + 并行会话的 6 个业务文件改动 + 3 个新文件）一并 stash 走。
        恢复后 Leader 逐一校验：plan 1033 行（含 §1.3 更正段）/ collective-test 186 行 /
        tracking 134 行 —— 内容完整无损。
Error: RCA（specs/2026-09-15-memory-retrieval-rca.md）的 +28 行「实施期更正」段
      未随恢复回到工作树，仍留在 stash@{0}。Leader **未回填**以免与并行会话
      agent-with-memory-99（正工作于该文件）争抢。
Next: 需用户裁决是否回填 RCA 更正段；更正内容已另有落点（plan §1.3 + collective-test LI-7）

[2026-09-15 00:00] NOTE-STASH-RESIDUE | Leader | Status: blocked
Detail: stash@{0} 仍存在，内含并行会话 agent-with-memory-99 的在途改动
        （nanobot/memory/extractor.py / agent/loop.py / hooks/memory_extraction.py 等 6 个
        已跟踪文件的修改，及 llm_error.py / labels.py 等新文件）。当前工作树中这些
        已跟踪文件的修改**不在**（仅新文件回来了）。
Next: **不建议**由本会话 drop 该 stash；请并行会话或用户确认后处置

[2026-09-15 00:00] CLOSEOUT-B2 | Reviewer | Status: completed
Detail: 独立代码审查 verdict=**BLOCK**。C-1（审查目标被后续提交取代 + 工作树污染）/
        C-2（头号验收测试不守住其声称的根因 5）/ C-3（语义通道对自然中文 query 仍空）/
        I-1（门禁 over+under-trigger）/ I-2 / I-3 + M 系列
Reviewer: separate-task（独立只读，与 coder 不同实例）
Output: .ai-runtime-artifacts/reviews/2026-09-15-memory-retrieval-rca-fix-code-review.md
Error: none
Note: Leader 逐条实测复核 —— C-2 / C-3 / I-1 **全部成立**；I-3 已在 d726dfa 修复。
      C-1 Leader 认责：审查运行期间向同一 worktree 提交。
Next: 修 C-2 + I-1

[2026-09-15 00:00] REVIEW-FIX-ROUND1 | Leader | Status: completed
Detail: C-2 —— 新增判别性用例 test_semantic_channel_alone_recovers_chinese_substring
        （记忆置 30 天前超出 recent 窗口，四路仅 semantic 可能命中），并更正原用例
        docstring「覆盖根因 1 + 5」的过头表述；
        I-1 —— **删除** short_without_context 分支（其前提「首轮无历史」恒真），
        _MEMORY_INTENT_HINTS 收窄为强锚点且只作用于 ≤3 字路径
Tests: 变异实验自证判别力：原样 ids=['old1'] / 禁 LIKE 回退 ids=[]  ✅
       门禁复测：'项目进度如何' 等此前 SKIP 的输入全部 INJECT（under-trigger 消除）
       tests/memory/ 689 passed；全量 13 失败与基线一致（存量）；ruff 变更文件全绿
Output: commit e7bd551
Error: none
Note: over-trigger（'我的天哪' 仍检索）为删除分支的必然代价，且由 reranker 冷启动
      豁免放大（既有设计）→ 登记 follow-up；C-3/I-2 判定为已知限制 + follow-up，不按已完成收口
Next: 第 2 轮审查（冻结快照 e7bd551）

[2026-09-15 00:00] CLOSEOUT-B2-RETRY | Reviewer | Status: started
Detail: 按门禁「BLOCK → 修复后重新审查」派 WU-REVIEW-02。为规避 C-1 的并发污染，
        改为在 git archive e7bd551 生成的**冻结快照** /tmp/rca-fix-review-e7bd551 上进行，
        prompt 要求审查者先自证 nanobot.__file__ 指向快照再下结论
Reviewer: separate-task（新实例）
Output: pending
Error: none
Next: 等 APPROVE 后方可声称交付完成

[2026-09-15 00:00] PERMISSION-BLOCK | Leader | Status: blocked
Detail: 在最终提交 e7bd551 上复跑真实库冒烟（读 ~/.nanobot/workspace/memory/state.db）
        被 Claude Code 权限分类器以 PII 处置策略拒绝。Leader **未尝试绕过**。
Error: 真实库冒烟仅在 c6f66ac 时点执行过一次（输出已落盘 collective-test §3）；
       e7bd551 的门禁改动使首轮行为变化，该时点证据对首轮路径不再完全等价
Next: 需用户裁决是否授予读权限复跑，或接受「以 tests/memory 结果为准」



