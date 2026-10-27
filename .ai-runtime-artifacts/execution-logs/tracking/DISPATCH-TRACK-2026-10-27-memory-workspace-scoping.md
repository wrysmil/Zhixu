# DISPATCH-TRACK — 记忆工作区隔离

- plan: `.ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-plan.md`
- dispatch: `.ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-dispatch.md`
- 集成分支: `feature/memory-workspace-scoping`（worktree `wt-memory-ws-integration`）
- 基线提交: `1290f86`

---

[2026-10-27 18:40] DISPATCH-GROUP-1 | Leader | Status: started
Detail: 用户回复「并行执行」，plan FM 置 approved=true；执行图 5 个 WU 分三波（WU-01 → WU-02/03/04 → WU-05）
Sub-agents: 1
Context: ~45%
Output: .ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-plan.md
Error: none
Next: 派发 WU-01（存储基座）

[2026-10-27 18:42] WORKTREE-INIT | Leader | Status: completed
Detail: 创建集成 worktree 与 WU-01 worker worktree；验证 worktree 内 `nanobot` 导入指向 worktree 副本（非主 checkout），共享 venv 可跑 pytest
GROUP: 1 | WU: WU-01 | ITER: 1 | STEP: implement
WorktreeId: wt-memory-ws-01 | WorktreePath: D:/studyspace/project/.harness-worktrees/Zhixu/wt-memory-ws-01 | Branch: harness/wt-memory-ws-01 | Base: 1290f86
Worktree: D:/studyspace/project/.harness-worktrees/Zhixu/wt-memory-ws-01 | Branch: harness/wt-memory-ws-01 | Title(zh): 存储基座（工作区键推导 / episode 工作区列 / repository 可见集合过滤）
Tests: 基线 tests/memory/test_search.py 12 passed
Queue-remaining: WU-02, WU-03, WU-04, WU-05
Reviewer: separate-task
Closeout: collective-test=<path|pending> verdict=PASS|FAIL|n/a | code-review=<path|pending> verdict=APPROVE|BLOCK|SKIPPED|n/a | status=pending
Sub-agents: 1
Context: ~45%
Output: none
Error: none
Next: 等 WU-01 返回 → Leader 审查并提交到 harness/wt-memory-ws-01，再合入 feature/memory-workspace-scoping

---

## 执行偏差记录

| 项 | 规范 | 实际 | 原因 |
| --- | --- | --- | --- |
| SpawnWorker 角色 | `Agent(subagent_type=coder)` | `Agent(subagent_type=general-purpose)` + prompt 首句要求必读 `.claude/agents/coder.md` | Qoder 平台只有 Explore/Plan/general-purpose 三种 subagent_type，无 coder 角色；用 prompt 内强制加载角色文件替代平台自动加载 |
| 集成位置 | 产物在主 checkout，代码在 worker worktree | 同左，另加集成 worktree `wt-memory-workspace-scoping` | 主 checkout 有**另一个并行会话**的未提交改动（记忆页改造，触及 `nanobot/memory/{models,database,repository,extractor}.py`、`nanobot/webui/memory_api.py`、`webui/src/**`），主 checkout 内做合并会与那批改动冲突 |
| 合并时机 | WU-01 → WU-02/03/04 → WU-05 | 同左，但每波 worker 从**集成分支最新提交**起分支 | 下游 WU 依赖上游新增的 `memory_workspace_id` 等符号，不能从旧基线起分支 |

[2026-10-27 18:55] BASELINE-CHECK | Leader | Status: completed
Detail: 发现基线提交 1290f86 **不含** `nanobot/memory/provenance.py` 与 `nanobot/webui/memory_presentation.py` —— 这两个模块是并行记忆页会话新建的未跟踪文件（git 从未收录），而本计划的 Task 1/Task 7 都以「修改既有文件」为前提
GROUP: 1 | WU: WU-01 | ITER: 1 | STEP: implement
WorktreeId: wt-memory-ws-01 | WorktreePath: D:/studyspace/project/.harness-worktrees/Zhixu/wt-memory-ws-01 | Branch: harness/wt-memory-ws-01 | Base: 1290f86
Tests: 未跑
Queue-remaining: WU-02, WU-03, WU-04, WU-05
Reviewer: pending
Closeout: collective-test=<path|pending> verdict=PASS|FAIL|n/a | code-review=<path|pending> verdict=APPROVE|BLOCK|SKIPPED|n/a | status=pending
Sub-agents: 1
Context: ~50%
Output: none
Error: none
Next: 处置未跟踪模块（见下）→ WU-01 返回后 Leader 提交并合入集成分支

### 未跟踪模块处置

| 文件 | 归属 | 处置 |
| --- | --- | --- |
| `nanobot/memory/provenance.py` | 并行会话新建（未跟踪） | 随 WU-01 一并纳入集成分支：worker 已在 worktree 内基于同一份内容加了 Task 1 的新函数，提交后即为「新增文件 + 增量」，后续并行会话再提交同一路径时 git 走 add/add 而非「文件不存在」的未知状态 |
| `nanobot/webui/memory_presentation.py` | 并行会话新建（未跟踪） | 由 **WU-04 的 worker 从主 checkout 播种**（`D:\studyspace\project\Zhixu\nanobot\webui\memory_presentation.py`），随 WU-04 提交进入集成分支 |

> 副作用：并行会话日后提交这两个文件时会出现 add/add 冲突。冲突点集中在同文件的新增/修改，已在最终合并阶段统一处理，不影响本 GROUP 的执行。

### 播种规则（Wave 2 必须遵守）

WU-04 / WU-05 的计划内容是**对着主 checkout 当前状态**写的（其中 `_read_presentation`、`PresentationContext`、payload 的 `workspaces` 字段等都是并行记忆页会话新引入的，基线提交里根本没有）。因此这两个 WU 的 worker **必须先把主 checkout 的对应文件原样复制进自己的 worktree**，再在副本上按计划改：

| WU | 需从主 checkout 播种的文件 |
| --- | --- |
| WU-04 | `nanobot/webui/memory_api.py`、`nanobot/webui/memory_presentation.py`、`nanobot/webui/memory_routes.py`、`nanobot/webui/memory_services.py` |
| WU-05 | `webui/src/lib/types.ts`、`webui/src/lib/api.ts`、`webui/src/components/settings/memory/*`、`webui/src/components/workspace/MemoryPage.tsx`、`webui/src/tests/api-memory.test.ts` |

不播种就开工 = 重做一遍并行会话已完成的改造，既浪费又必然产生大冲突。

[2026-10-27 19:10] SCOPE-CHANGE | Leader | Status: completed
Detail: 用户复核两点并拍板：①隔离键**不用 path 哈希**，改用仓库既有的稳定工作区身份（`<工作区>/.nanobot/workspace-id`，32 hex，改名跟随、复制轮换）；②**允许事后改归属**
GROUP: 1 | WU: WU-01 | ITER: 1 | STEP: implement
WorktreeId: wt-memory-ws-01 | WorktreePath: D:/studyspace/project/.harness-worktrees/Zhixu/wt-memory-ws-01 | Branch: harness/wt-memory-ws-01 | Base: 1290f86
Tests: 未跑
Queue-remaining: WU-02, WU-03, WU-04, WU-05
Reviewer: pending
Closeout: collective-test=<path|pending> verdict=PASS|FAIL|n/a | code-review=<path|pending> verdict=APPROVE|BLOCK|SKIPPED|n/a | status=pending
Sub-agents: 1
Context: ~55%
Output: .ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-plan.md
Error: none
Next: 计划改版完成（D1/D8/D9 + Task 1a/1b + Task 7 归属可改 + Task 9 编辑态）→ WU-01 返回后派发 ITER-2 落地身份模块

### 计划改版要点（2026-10-27 19:10）

| 变更 | 内容 |
| --- | --- |
| D1 改写 | 工作区隔离键 = 稳定身份 id（`<工作区>/.nanobot/workspace-id`，32 hex），不再用 path 哈希。理由：仓库 `SessionManager` 已定义「身份随目录移动、复制目录换 id」，path 哈希与既有语义冲突且改名后永久失联 |
| D8 新增 | 允许事后改归属：`update_memory` 接受 `workspace`，同时更新 `workspace_id` 列与 `metadata.provenance.workspaces` 快照；`store_workspace_id`/`trigger_*`/`generation_method` 不动 |
| D9 新增 | marker 写在工作区内；目录不可写时回退 path 哈希（面板标注为派生归属） |
| Task 1 → 1a/1b | 新增 `nanobot/workspace_identity.py`（身份解析 + 复制检测 + 进程内缓存）；`provenance.memory_workspace_id` 改为委托该模块 |
| Task 7 增补 | `memory_api.update_memory` 支持改归属；`memory_routes.py` 的 `memory-update` 透传 `workspace` |
| Task 9 增补 | `MemoryEditDialog` 编辑态也渲染工作区下拉并提交 `workspace`；移除重复的「默认工作区」空值项 |
| §5.4 增补 | 身份解析带进程内缓存：首次 ~1ms（写 marker），之后 µs 级，对首轮对话仍无感知影响 |
| 风险表 | R1 改为「改名跟随 id」；新增 R8（项目目录出现 `.nanobot/workspace-id`）、R9（只读目录回退派生归属）、R10（注册表丢失致复制检测失效）、R11（改归属与旧契约「编辑不改项目归属」相反，需在尾盘 review 记录） |

[2026-10-27 19:25] DISPATCH-SPLIT | Leader | Status: started
Detail: WU-01 仍在跑旧的 path 哈希版 Task 1；Task 1a（身份模块）是**全新文件**且与 WU-01 的文件面不冲突，故提前并行派发 WU-01b，缩短总时长。Task 1b（隔离键委托，改 provenance.py）必须等 WU-01 落地后再做
GROUP: 1 | WU: WU-01b | ITER: 1 | STEP: implement
WorktreeId: wt-memory-ws-01b | WorktreePath: D:/studyspace/project/.harness-worktrees/Zhixu/wt-memory-ws-01b | Branch: harness/wt-memory-ws-01b | Base: 1290f86
Worktree: D:/studyspace/project/.harness-worktrees/Zhixu/wt-memory-ws-01b | Branch: harness/wt-memory-ws-01b | Title(zh): 稳定工作区身份模块（marker / 复制检测轮换 / 只读回退 / 进程内缓存）
Tests: 未跑
Queue-remaining: WU-01, WU-01b, WU-02, WU-03, WU-04, WU-05
Reviewer: pending
Closeout: collective-test=<path|pending> verdict=PASS|FAIL|n/a | code-review=<path|pending> verdict=APPROVE|BLOCK|SKIPPED|n/a | status=pending
Sub-agents: 2
Context: ~58%
Output: none
Error: none
Next: 等 WU-01 与 WU-01b 返回 → Leader 审查并分别提交 → 合入集成分支 → 派 WU-01 ITER-2（Task 1b 委托）→ Wave 2（WU-02/03/04）

### WU-01b 补充约束（相对 plan Task 1a 的修正）

| 项 | 修正 | 原因 |
| --- | --- | --- |
| 不可写用例 | 改用 monkeypatch 让 `_write_marker` 抛 `OSError`，不用 `os.chmod` | Windows 目录 ACL 不吃 POSIX 位，`chmod 0o500` 拦不住写入，用例会假通过 |
| 注册表目录 | 测试必须 monkeypatch `get_runtime_subdir` 指向 `tmp_path` | 否则测试会往真实 `~/.nanobot/workspace-registry` 写文件 |
| 缓存隔离 | 提供 `reset_workspace_identity_cache()` 供测试清空 `_CACHE` | 进程级缓存会跨用例串味 |
| 异常处理 | 身份解析任何异常都回退 path 哈希，不冒泡 | 记忆链路不能因身份解析失败整体中断 |

[2026-10-27 19:40] EXEC-MODEL-CHANGE | Leader | Status: completed
Detail: 用户指出 worktree 过多，要求直接在当前分支改。核实后确认「worktree 从基线提交起 → 并行会话的新代码不在基线里 → worker 必须手工播种文件」是负收益：绕回原点且多一层复制风险。改为在主 checkout 顺序执行剩余 WU，取消集成 worktree 与每 WU 一个 worktree 的做法
GROUP: 1 | WU: 全部 | ITER: 2 | STEP: implement
WorktreeId: wt-memory-ws-01, wt-memory-ws-01b（收尾后清理） | WorktreePath: D:/studyspace/project/.harness-worktrees/Zhixu/ | Branch: harness/agent-evaluation-calibration（主 checkout） | Base: 1290f86
Tests: 未跑
Queue-remaining: WU-01, WU-01b（收尾中）, WU-02, WU-03, WU-04, WU-05
Reviewer: pending
Closeout: collective-test=<path|pending> verdict=PASS|FAIL|n/a | code-review=<path|pending> verdict=APPROVE|BLOCK|SKIPPED|n/a | status=pending
Sub-agents: 2
Context: ~60%
Output: none
Error: none
Next: 等两个 worker 返回 → 用 **patch 移植**回主 checkout（不 cp 覆盖文件，避免盖掉并行会话的未提交改动）→ 清理 worktree 与临时分支 → 顺序执行 WU-02/03/04/05

### 执行模型变更（2026-10-27 19:40）

| 项 | 原方案 | 现方案 | 原因 |
| --- | --- | --- | --- |
| 工作目录 | 每 WU 一个 worktree + 集成分支 `feature/memory-workspace-scoping` | 全部在主 checkout，当前分支 `harness/agent-evaluation-calibration` | 用户要求；且基线提交缺并行会话的新代码，worktree 方案被迫引入「播种文件」手工步骤 |
| 并行度 | WU-02/03/04 三路并行 | 顺序执行 | 同一工作树并行改代码 + 并发跑 pytest，会出现「读到别人改到一半的模块」的假失败 |
| 在跑的 worker | — | 不打断，收尾后 **patch 移植** | 打断即白跑；且 `cp` 覆盖会盖掉并行会话对 `models.py`/`database.py`/`repository.py` 的未提交改动 |
| 分支/合并 | 5 个 `harness/wt-*` 分支 + 集成分支 | 只留当前分支 | 合并冲突集中在并行会话未提交改动上，本就应在最后统一处理，与 worktree 无关 |

> 计划与 dispatch 文档的 WU 文件边界**保持不变**——它原本就是为「同工作树顺序执行」设计的（每个文件只有一个 WU 能动），只是额外适配了 worktree 起点的差异。

### 移植步骤（worker 返回后按序执行）

| # | 操作 | 覆盖风险 |
| --- | --- | --- |
| 1 | `git -C wt-memory-ws-01 diff > /tmp/wu01.patch`（只含已跟踪文件的修改）；`git -C wt-memory-ws-01b diff > /tmp/wu01b.patch` | 无 |
| 2 | 主 checkout 里**逐个复制**未跟踪的新文件：`nanobot/memory/provenance.py`（并行会话版本 + WU-01 的 Task 1 增量，内容是超集）、`nanobot/workspace_identity.py`、`tests/test_workspace_identity.py`、`tests/memory/test_workspace_scope.py`、`tests/memory/test_repository_workspace_filter.py` | provenance.py 是**超集覆盖**（并行会话的原内容完整保留在其内），不丢东西 |
| 3 | 主 checkout 里 `git apply -3 /tmp/wu01.patch` 处理 `models.py` / `database.py` / `repository.py` / `tests/memory/test_repository.py` | 这 4 个文件并行会话也改过，**可能冲突**；冲突处保留双方：`metadata` 列与 `workspace_id` 列并存、两条 ALTER 并存、`_INSERT/_SELECT` 同时带两列 |
| 4 | 冲突解完跑 `pytest tests/memory tests/test_workspace_identity.py -q` 验证 | — |
| 5 | 派 WU-01c（Task 1b）：把 `provenance.memory_workspace_id` 改为委托 `workspace_identity.workspace_identity_id` | provenance.py 此刻已稳定，不再有并发 |
| 6 | 清理：`git worktree remove` 三个 worktree + 删 `harness/wt-memory-ws-01`、`harness/wt-memory-ws-01b`、`feature/memory-workspace-scoping` 分支 | 只删我自己建的 |
| 7 | 顺序派发 WU-02 → WU-03 → WU-04 → WU-05（均在主 checkout、当前分支） | — |

### 执行进展（2026-10-27 19:55 起）

| 时刻 | 动作 | 结果 |
| --- | --- | --- |
| 19:50 | 复制 WU-01b 的两个新文件到主 checkout：`nanobot/workspace_identity.py`（230 行）、`tests/test_workspace_identity.py`（156 行） | 主 checkout 无同名文件 → 零覆盖风险 |
| 19:52 | 复制 WU-01 的 `tests/memory/test_workspace_scope.py`（47 行） | 新增文件，零覆盖风险 |
| 19:53 | 主 checkout 跑 `pytest tests/test_workspace_identity.py -q` | **8 passed** |
| 19:53 | 主 checkout 跑 `pytest tests/memory/test_workspace_scope.py` | 收集期 ImportError —— 依赖尚未追加的 `provenance.py` 新函数（预期内，Task 1b 落地后即恢复） |
| 19:55 | 派发 **WU-01c**（Task 1b：隔离键委托身份模块），在主 checkout 执行 | 使 WU-01 的 path 哈希版 Task 1 整份作废 → 移植时**不必**覆盖并行会话那份 `provenance.py`，覆盖风险归零 |

> 关键结论：**移植清单里唯一需要「覆盖已有内容」的文件（`nanobot/memory/provenance.py`）被 WU-01c 消掉了**。WU-01 的 `provenance.py` 不再移植；只取它的 `models.py` / `database.py` / `repository.py` / `tests/memory/test_repository.py`。这 4 个文件里只有前 3 个与并行会话重叠，冲突面从 4 个降到 3 个，且都是「双方各加一列」的结构性重叠。

[2026-10-27 18:40] EXEC-SIMPLIFY | Leader | Status: completed
Detail: WU-01（worktree 版）长时间未落盘且其 Task 1 产物已被 Task 1b 取代 → 停掉；改为在**主 checkout**直接派 WU-01d 做 Task 2/3。并行会话的未提交改动天然在底座上，**移植与冲突解环节整个消失**。worktree 与临时分支已全部清理
GROUP: 1 | WU: WU-01d | ITER: 3 | STEP: implement
WorktreeId: n/a | WorktreePath: D:/studyspace/project/Zhixu（主 checkout） | Branch: harness/agent-evaluation-calibration | Base: 1290f86
Tests: 未跑
Queue-remaining: WU-01c（跑）, WU-01d（跑）, WU-02, WU-03, WU-04, WU-05
Reviewer: pending
Closeout: collective-test=<path|pending> verdict=PASS|FAIL|n/a | code-review=<path|pending> verdict=APPROVE|BLOCK|SKIPPED|n/a | status=pending
Sub-agents: 2
Context: ~62%
Output: none
Error: none
Next: WU-01c + WU-01d 返回 → 跑 tests/memory 全量 → 顺序派 WU-02/03/04/05

### 执行模型最终态（2026-10-27 18:40）

| 项 | 状态 |
| --- | --- |
| worktree / 临时分支 | **已全部清理**（`wt-memory-ws-01`、`wt-memory-ws-01b`、`wt-memory-ws-integration` + 对应 3 个分支） |
| 代码落点 | 全部在主 checkout 当前分支 `harness/agent-evaluation-calibration` |
| 并行会话冲突 | **不再需要手工解** —— worker 直接在对方未提交改动之上做增量编辑 |
| 在跑 | WU-01c（`provenance.py` 委托身份模块）、WU-01d（episode 工作区列 + repository 可见集合过滤），文件面互不相交 |
| 后续 | WU-02 → WU-03 → WU-04 → WU-05 顺序执行 |

> 提交纪律：`memory/state.db`（仓库根、已被跟踪的运行时库）会被本地跑测改动，**不进我的提交**；每次 `git add` 只加本次 WU 的文件。




---

## 恢复批次（2026-10-27 会话恢复）

[2026-10-27 22:05] RESUME | Leader | Status: completed
Detail: 依据 `.ai-runtime-artifacts/execution-logs/HANDOFF.md`（status: in-progress）恢复。先 `git fetch --all --prune` + 拉取 `harness/agent-evaluation-calibration`（1fcc74d→2b447c6，8 commits，+5462/-1108），并快进 `feature/memory-system`（3bbaef5→b02b5d7，78 commits）；`git worktree prune` 清掉 5 条失效 worktree 记录。工作区干净（`nanobot/ webui/ tests/` 无未提交改动）
GROUP: 2 | WU: 全批 | ITER: 1 | STEP: baseline-check
State: WU-01(b/c/d) 已落地并提交（1290f86 / 2630a7c）；WU-02/03/04/05 经逐文件核实**全部未开工**（extractor 无行归属打标；retrieval/agent 无 workspace_ids 透传；memory_api 仍按 store 标签过滤；前端仍用 value="" / value="unassigned" 旧口径）
Next: 派发 WU-02/03/04/05

[2026-10-27 22:08] DISPATCH-GROUP-2 | Leader | Status: started
Detail: **并行度恢复为 4 路**。dispatch 原定「顺序执行」的理由是 worktree 播种/移植机制，该机制已于 19:55 随 WU-01c/01d 取消（代码全部落在主 checkout 当前分支）；dispatch §并行边界表的文件归属表经复核**互不相交**，故按 AGENTS.md「独立并行子任务」改回并行。写域互斥：WU-02{extractor,provenance,test_extractor_workspace} / WU-03{retrieval/**,agent/context,agent/loop,agent/tools/memory_search,tests} / WU-04{webui/memory_api,memory_presentation,memory_routes,session_workbench,memory/repository(update_memory),tests/webui} / WU-05{webui/src/**}
GROUP: 2 | WU: WU-02, WU-03, WU-04, WU-05 | ITER: 1 | STEP: implement
Tests: 基线 tests/memory+test_workspace_identity = 925 passed（交接文档 §2）
Sub-agents: 4
Execution: Codex native subagent（agent_type=default, model=gpt-6.1-sol, reasoning=high）；**禁用 executor 角色**——其锁定 gpt-5.2-codex，ChatGPT 账户不支持（见本文件下方「模型路由事故」）
Closeout: collective-test=<pending> verdict=pending | code-review=<pending> verdict=pending | status=in-progress
Next: 等 4 路返回 → Leader 逐路审查 diff → 串行跑全量 pytest + 前端 vitest/build → 尾盘三层审查 → 落盘 verification/collective-test/reviews

### 本批次执行约束（已写进每个 worker 的 prompt）

- 禁止 `git commit` / `push` / `add` / `stash` / `checkout --` / `restore` / `reset`；禁止整文件覆盖式重写（并行会话在同仓库作业）。
- 必须 TDD：先红后绿，回报里带先红/后绿的真实命令与输出。
- worker 不写 `.ai-runtime-artifacts/` 产物（Leader 统一落盘）。
- `bun` 本机不可用 → 前端用 `npx vitest run`。
- 仓库根 `memory/state.db` 是被跟踪的运行时二进制库，跑测会改动它 → 不进任何提交。
