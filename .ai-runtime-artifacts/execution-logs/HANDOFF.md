---
artifact: handoff
route: orchestration:dispatcher-workflow
plan: .ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-plan.md
dispatch: .ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-dispatch.md
tracking: .ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-27-memory-workspace-scoping.md
created_at: 2026-10-27
status: completed
branch: harness/agent-evaluation-calibration
closed_at: 2026-10-27
---

# 交接文档 — 记忆工作区隔离改造

## 一句话现状

**全批已完成并全绿：后端 10256 passed / 前端 1931 passed；写入打标、检索过滤、面板归属、前端四个 WU 全部落地。**
尾盘三层审查提出 1 个代码 P1（生产 idle 提取丢失工作区 → 跨工作区泄漏）与 1 个性能 P1
（工作区过滤把 FTS5 查询计划换成索引驱动 → 5 万行单工作区 25ms 退化到 4867ms），**均已修复并各配会变红的回归测试**。
收口明细见本文件末尾「§8 收口记录」，验证与审查产物见
`verifications/2026-10-27-memory-workspace-scoping-{verification-lite,collective-test}.md`
与 `reviews/2026-10-27-memory-workspace-scoping-*.md`。

---

## 1. 已完成

| WU | 内容 | 产物 |
| --- | --- | --- |
| WU-01b | 稳定工作区身份模块 | `nanobot/workspace_identity.py`（230 行）、`tests/test_workspace_identity.py`（156 行，8 用例） |
| WU-01c | 记忆隔离键委托身份模块 | `nanobot/memory/provenance.py` 增量追加 4 个函数、`tests/memory/test_workspace_scope.py`（重写为 10 用例）、`tests/memory/test_provenance.py` |
| WU-01d | episode 工作区列 + repository 可见集合过滤 | `nanobot/memory/models.py`、`database.py`、`repository.py` + `tests/memory/test_repository_workspace_filter.py`、`test_repository.py`、`test_database.py` |

### 关键代码位置

| 位置 | 内容 |
| --- | --- |
| `nanobot/workspace_identity.py` | `workspace_identity_id(path)`：marker `<工作区>/.nanobot/workspace-id`（32 hex）→ 复制检测轮换 → 只读回退 path 哈希（24 hex）；进程内 `_CACHE` + `reset_workspace_identity_cache()` |
| `nanobot/memory/provenance.py:109` | `memory_workspace_id(project_path)` → 委托 `workspace_identity_id`；非法输入/异常一律 `'default'` |
| `nanobot/memory/provenance.py:136` | `session_memory_workspace_id(session)` → 读 `session.metadata.workspace_scope.project_path` |
| `nanobot/memory/provenance.py:153` | `memory_visibility_ids(key)` → `('default',)` 或 `('default', key)` |
| `nanobot/memory/models.py:71` | `Episode.workspace_id: str = 'default'` |
| `nanobot/memory/database.py:20,95,246` | `_SCHEMA_VERSION = '3'`；`episodes.workspace_id` 列；`idx_episodes_workspace`；幂等 `ALTER` |
| `nanobot/memory/repository.py:960` | `known_workspace_ids(conn)` → 库中已出现的 workspace_id 集合 |
| `nanobot/memory/repository.py` | `list_memories` / `list_episodes` / `search_memories` / `_search_memories_like` / `search_semantic_scored` / `query_semantic` / `search_episodes` 均支持 `workspace_ids`（`None`=不过滤，空元组=恒假 `0 = 1`） |

---

## 2. 验证证据

```bash
cd D:/studyspace/project/Zhixu
.venv/Scripts/python.exe -m pytest tests/memory tests/test_workspace_identity.py -q
# → 925 passed in 19.92s
```

**基线噪声（不是本轮引入，别去修）**：
- `ruff check nanobot/` 11 条既有报错，全在并行会话未提交改动的文件（`nanobot/cli/commands.py:43` I001、`nanobot/memory/__init__.py` 7×F401、`nanobot/memory/lifecycle.py:333` N806、`nanobot/memory/repository.py:534` N806）。
- `basedpyright` 仓库整体 2593 条诊断，集中在并行会话正在改的文件；本轮新增/修改的 3 个文件均为 0 error。

---

## 3. 未完成（按依赖顺序恢复）

| 顺序 | WU | 依赖 | 内容 | 文件 |
| --- | --- | --- | --- | --- |
| 1 | **WU-02** | 已有 `provenance.py` | 提取落库按会话工作区打标：`Memory`/`Episode` 带 `workspace_id`；`extraction_provenance` 加 `workspace_id` 关键字参数（`'default'` 时不写 `workspaces` 快照）；画像合并按工作区隔离（`_load_existing_memories(candidates, *, workspace_id)`、`_merge_into_existing(..., workspace_id=...)`）；3 处调用点用同一隔离键 | `nanobot/memory/extractor.py`、`nanobot/memory/provenance.py`（仅改 `extraction_provenance`）、`tests/memory/test_extractor_workspace.py`（新建） |
| 2 | **WU-03** | 已有 repository 的 `workspace_ids` | 检索链路可见集合透传：`store_adapter` → `channels/{semantic,recent,episodes}` → `engine.retrieve_with_ids` → `context._build_memory_section_with_ids` → `loop._compute_retrieval_section`；`memory_search` 工具用 `get_cached` 会话解析（**不新增磁盘读**） | `nanobot/memory/retrieval/**`、`nanobot/agent/context.py`、`nanobot/agent/loop.py`、`nanobot/agent/tools/memory_search.py` |
| 3 | **WU-04** | 已有 `known_workspace_ids` | 面板读写不再按 store 标签过滤；`create_memory` 按归属写列；**`update_memory` 支持改归属**（同步 `provenance.workspaces` 快照，`store_workspace_id`/`trigger_*` 不动）；`memory_routes` 透传 `workspace`；`workspace_options`/`filter_result` 以 `workspace_id` 为准（含「全局记忆」项、取消「默认工作区」项）；工作台记忆项带归属 | `nanobot/webui/memory_api.py`、`memory_presentation.py`、`memory_routes.py`、`session_workbench.py` |
| 4 | **WU-05** | WU-04 的 payload 契约 | 前端：类型 + `workspace_id`；筛选下拉含「全局记忆」；**`MemoryEditDialog` 编辑态也渲染工作区下拉并提交归属**；移除重复的「默认工作区」空值项与 `unassigned` 项 | `webui/src/lib/types.ts`、`webui/src/lib/api.ts`、`webui/src/components/settings/memory/**` |

### WU-03 关键实现约束（易错）

- **不要用 Chroma 的 `where` 过滤**：实测 chromadb 1.5.9 下 `where={"workspace_id":{"$in":[...]}}` 有 +1~18ms/查询的固定开销（10k 文档）。向量候选改为**回表读 SQLite 行后判定可见性**（`store_adapter._is_visible`），FTS5 路由 SQL 过滤。
- `nanobot/agent/context.py` 已从 typing 导入 `Sequence`；`nanobot/agent/loop.py` 需要给 `from collections.abc import ...` 补 `Sequence`；`nanobot/memory/retrieval/*` 各文件需要新增 `from collections.abc import Sequence`。
- 每轮可见集合 = `memory_visibility_ids(memory_workspace_id(scope.project_path))`。

---

## 4. 关键决策（已定，不要重新讨论）

| 编号 | 决策 |
| --- | --- |
| D1 | 隔离键 = **稳定工作区身份 id**（`<工作区>/.nanobot/workspace-id`，32 hex），**不用 path 哈希**（改名会永久失联且不可改归属） |
| D2 | **取消「默认工作区」概念**：任何工作区按自己的身份 id 归属；`'default'` = 全局记忆 |
| D3 | 可见集合 = `('default', 当前工作区键)`；无工作区的会话（渠道侧）只见全局 |
| D4 | `workspace_id` 列与 `provenance.workspaces` 快照**同源**，由 `session_memory_workspace_id` 统一推导 |
| D5 | `services.workspace_id`（恒 `'default'`）降级为 **store 标签**，不再作为记忆行过滤条件 |
| D6 | episodes 同步加 `workspace_id` 列 |
| D7 | 手动新增记忆可选全局或某工作区，省略则归全局 |
| D8 | **允许事后改归属**：`update_memory` 接受 `workspace`，同时改列与 provenance 快照（不动 `store_workspace_id`/`trigger_*`） |
| D9 | marker 写在工作区内；不可写时回退 path 哈希（面板标注为派生归属） |

---

## 5. 性能基线（已实测，勿重复压测）

| 规模 | 无过滤 | 有 `workspace_id` 过滤 | 差值 |
| --- | --- | --- | --- |
| 1k | 0.66ms | 0.71ms | +0.05 |
| 10k | 5.27ms | 3.31ms | −1.96 |
| 50k | 27.47ms | 16.34ms | −11.13 |

- FTS5 侧过滤**零成本**（多数场景更快：命中集变小，排序更小）。脚本 `.ai-runtime-artifacts/research/2026-10-27-memory-recall-bench.py`。
- 身份解析带进程内缓存：首次 ~1ms（写 marker），之后每轮 µs 级。
- **对首轮对话无可感知影响**。

---

## 6. 坑与风险（接手前必读）

| # | 事项 |
| --- | --- |
| R1 | **并行会话仍在主 checkout 工作**：`nanobot/memory/{extractor,models,database,repository}.py`、`nanobot/webui/*`、`webui/src/**` 里有他们的未提交改动。worker 只能**增量**编辑，禁止整文件覆盖、禁止 `git checkout --` / `git restore` / `git stash` |
| R2 | `nanobot/memory/provenance.py` 与 `nanobot/webui/memory_presentation.py` 在 git 里**长期未被跟踪**（并行会话新建）。provenance.py 已被本轮提交收录；memory_presentation.py 仍未跟踪 → WU-04 的 worker 需先把它复制进工作树再改 |
| R3 | `nanobot/memory/extractor.py` 的 provenance 落库改动属并行会话，**本轮未提交**（WU-02 会补 `workspace_id`，届时一并落地） |
| R4 | 用户项目目录会出现 `.nanobot/workspace-id`（git 里显示 untracked）——用户已知情选择 |
| R5 | 只读工作区回退 path 哈希 → 派生归属，改名会失联；用户可在编辑弹窗改归属迁走 |
| R6 | `2026-10-27-contract-memory-presentation.md` 写的「编辑不改项目归属」与 D8 相反 → 尾盘 review 要显式记录该偏离 |
| R7 | 已知缺口（不在本轮范围）：`nanobot/memory/lifecycle.py:116` 的 MEMORY.md 派生过滤 `scope="user"`，而生产写入全是 `scope="global"` → MEMORY.md 恒空（实测文件仅 3 字节） |
| R8 | 仓库根 `memory/state.db` 是**被 git 跟踪的运行时二进制库**，本地跑测会改动它 → 提交时排除 |

---

## 7. 恢复指令

1. 读本文件 + `.ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-plan.md`（Task 4/5/6/7/9 有逐字实现代码）。
2. 确认主 checkout 状态：`git status --short -- nanobot/ webui/ tests/`（并行会话可能又改了文件）。
3. 顺序派发 WU-02 → WU-03 → WU-04 → WU-05，**均在主 checkout 当前分支顺序执行**（用户明确要求：不要每任务一个 worktree + 集成分支）。
4. 每个 WU 的 prompt 要点：必读 `.claude/agents/coder.md`（角色检查表 + 返回格式，含 `### Skills 使用`）；必 Load `test-driven-development` / `verification-before-completion` / `incremental-implementation`；禁 Load `brainstorming` / `writing-plans` / `git-xywh` / `orchestration`；worker 不 commit/push。
5. 尾盘：集体测试（`pytest tests/memory tests/webui tests/agent` + `cd webui && bun run test && bun run build`）→ 三层审查（reviewer / security-auditor / perf-auditor）→ 落盘 `verifications/*-collective-test.md` 与 `reviews/*`。
6. Qoder 平台无 `coder` 子代理类型 → 用 `general-purpose` + prompt 强制加载 `.claude/agents/coder.md` 替代平台自动加载。

---

## 8. 收口记录（2026-10-27）

### 8.1 已完成的 WU

| WU | 落地 | 产物 |
| --- | --- | --- |
| WU-02 | 提取落库按会话工作区打标；画像合并按工作区隔离；`extraction_provenance` 增 `workspace_id` 形参 | `nanobot/memory/extractor.py`、`nanobot/memory/provenance.py`、`tests/memory/test_extractor_workspace.py` |
| WU-03 | 检索链路透传可见集合；每轮计算可见集合并注入；`memory_search` 工具侧解析 | `nanobot/memory/retrieval/**`、`nanobot/agent/{context,loop}.py`、`nanobot/agent/tools/memory_search.py`、`tests/agent/test_memory_visibility.py` |
| WU-04 | 面板读写不再按 store 标签过滤；`create_memory` 可选归属；`update_memory` 可改归属；工作台记忆项带归属 | `nanobot/webui/memory_{api,presentation,routes}.py`、`session_workbench.py`、`nanobot/memory/repository.py` |
| WU-05 | 前端筛选下拉含「全局记忆」；编辑态可改归属 | `webui/src/lib/{types,api}.ts`、`webui/src/components/settings/memory/**`、`webui/src/tests/memory-workspace-filter.test.tsx` |

### 8.2 Leader 补修（尾盘审查发现）

1. **P1（跨工作区泄漏）** `nanobot/agent/hooks/memory_extraction.py`：idle / 增量提取重建 `Session` 时丢失
   `workspace_scope` → 记忆落成全局 → 其它工作区可召回。已修：hook 携带归属；loop 侧抽出
   `_memory_workspace_project_path` 作为读写唯一真相源。回归 `tests/memory/test_hook_workspace_scoping.py`。
2. **P1（性能）** `nanobot/memory/repository.py`：工作区过滤把 FTS5 查询计划换成「索引驱动 + 逐行全扫 FTS5」，
   5 万行单工作区 25ms → 4867ms。已修：两处 FTS 查询改 `CROSS JOIN` 强制 FTS5 驱动 → 9.2ms。
   回归 `tests/memory/test_fts_workspace_filter_plan.py`。
3. **P2（口径不同源）** `nanobot/webui/memory_presentation.py`：面板归属改为以行 `workspace_id` 列为准，
   快照只补名称；`nanobot/agent/loop.py`：无显式工作区一律只见全局（D3）。
4. **P2（既有缺陷）** `nanobot/workspace_identity.py`：为**不存在**的工作区写 marker 会凭空创建目录树
   （POSIX 上 `C:\Work\A` 这类路径被 `pathlib` 当相对目录，直接在仓库根长出同名目录）。已修：不存在的
   工作区退化为 path 哈希。回归 `tests/test_workspace_identity.py::test_missing_workspace_is_not_created_on_disk`。

### 8.3 契约变更（需要后续知悉）

- **跨工作区不再合并画像**：同一 subject+predicate 在不同工作区各自成行（原契约是合并成一行并累积项目快照）。
  `tests/memory/test_provenance.py` 已按新契约拆分。
- **编辑可改归属（D8）**：与 `contracts/2026-10-27-contract-memory-presentation.md` 的「编辑不改项目归属」相反，
  按用户复核意见改为**只改归属**（不动 `store_workspace_id` / `trigger_*` / `generation_method` / `origin_kind`）。
- **取消「默认工作区」概念（D2）**：`workspace_id='default'` 即全局记忆。

### 8.4 遗留项

- 真机场景验证未做（需用户参与）：两个会话切到不同项目 → 互不可见；面板工作区筛选；工作台归属标签。
- `nanobot/memory/lifecycle.py:116` 的 MEMORY.md 派生过滤 `scope="user"` 恒空 —— 独立缺陷（plan §6 R5）。
- 既有红灯 `tests/agent/test_mcp_reconnect_crash.py::test_mcp_reconnect_during_shutdown_does_not_crash`
  —— 已在 HEAD `2b447c6` 的独立 worktree 复现，与本批无关。
