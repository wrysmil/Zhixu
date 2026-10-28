---
artifact: verification-lite
route: leader-direct | 实现 + 验证
skills:
  - writing-plans
  - verification-before-completion
  - git-xywh
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
  - harness-kit/.agents/skills/git-xywh/SKILL.md
  - harness-kit/core/routing.md
plan: .ai-runtime-artifacts/plans/2026-10-28-memory-session-linkage-plan.md
source:
  - "工作台现在和记忆能力联动起来了吗 / 记忆从哪个会话中提取能显示到工作区吗 / 会话空闲自动提取记忆能显示在工作台吗 / 按 2+3 做，先出影响面清单 / 123，然后根因也修"
created_at: 2026-10-28
tier: 1
---

# 记忆 ↔ 会话联动 — 实施与验证（Tier 1）

> **纪律：** 先跑命令、再给结论。以下均为 Leader 本机实跑输出。
> 计划见 `.ai-runtime-artifacts/plans/2026-10-28-memory-session-linkage-plan.md`。

## 起点问题

用户问「工作台和记忆能力联动起来了吗」。实测结论：**两个方向都没联动**，且不是 UI 缺失，是链路断裂。

| 问题 | 实测证据 |
| --- | --- |
| 工作台看不到本会话抽取的记忆 | DOM 原文 `No memories linked to this session.`，而该会话确实抽出了 2 条 |
| 面板看不出记忆来自哪次对话 | API 顶层无 `session_id`，会话 id 只埋在 `metadata.provenance.session_id` |
| episode 与记忆无关联 | `episodes.linked_memory_ids = []`、`memories.source_episode_id = None` |

**根因**：`extractor.py` 阶段 4b 只靠 `episode.entities` ↔ `memory.subject` 字符串相等建立关联，
而 `prompts.py` 的记忆 schema **根本没有 `subject` 字段**（只有 content/type/priority/importance/tags），
模型不会自发填写 → 关联几乎不可能建立。

## 改动（6 个提交）

| 提交 | 内容 |
| --- | --- |
| `d6df042` fix(memory) | 关联不再依赖 subject 匹配：prompt 补 subject/predicate + 本批次兜底 + `source_episode_id` 一律回填 |
| `d2a7667` feat(memory) | `repository.list_memories_by_session`（按 provenance.session_id 反查）+ API 出参 + `memory-list-by-session` 动作 + 工作台改用 |
| `e5583e9` feat(webui) | 工作台记忆条目显示归属工作区（后端早就算好，前端类型丢掉了） |
| `5de8fa7` feat(memory) | 面板显示来源会话：`session_id` / `session_title` + `SessionBadge` + 卡片与详情 |
| `ad4a131` fix(memory) | 工作台可见集合改按**会话作用域**计算（真机抓到） |
| `ac83a08` fix(memory) | 会话标题改从 `metadata.title` 取并回退 `session_handle`（真机抓到） |

## 真机验证抓到的两个单测抓不到的问题

这是本批最值得记录的部分——**两个问题都是在真实网关 + 真实库上跑出来的，单元测试全绿时它们存在**：

1. **可见集合口径错**：网关把记忆服务绑在 `workspace_id="default"`（`gateway_services.py:773`），
   而记忆行按会话的项目工作区打标。工作台用 store 作用域算可见集合 → 本会话记忆被自己过滤掉，
   仍返回 0 条。单测里 `services.workspace_id` 恰好就是 `"default"` 且记忆也落在 default，
   所以测不出来。修为按 `memory_visibility_ids(session_memory_workspace_id(session))` 传入，
   与写入侧同源。
2. **会话标题形状错**：`SessionMetadataPayload` 是 `{key, created_at, updated_at, metadata}`，
   标题在 `metadata.title` 而非顶层；且未经自动命名的会话根本没有 `title`，只有
   `session_handle`（聊天头那个 `@kevu`）。单测的桩数据是我按错误假设写的，所以「自证通过」。
   修为 `metadata.title` → `metadata.session_handle` → None。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `pytest tests/memory -q` | **954 passed**（含新增关联兜底用例） |
| `pytest tests/webui tests/memory` | **2113 passed / 3 skipped** |
| `pytest tests/webui/test_session_workbench.py tests/webui/test_memory_presentation_api.py tests/memory -q` | **993 passed** |
| `npx vitest run`（记忆 + 工作台相关） | 记忆 30 passed / 1 skipped；`workbench-memories` 2 passed |
| `npx tsc -p tsconfig.build.json --noEmit` | exit 0 |
| `npx eslint`（改动的前端文件） | 无输出 |
| `ruff check`（改动的 Python 文件） | All checks passed（仓库全量 11 条既有，0 新增） |
| 回归测试「回退即变红」 | `test_episode_links_batch_memories_when_subject_is_empty` 在修复前失败于 `assert ... in []` ✓ |

### 端到端实测（隔离实例，真实网关 + 真实库）

修复前：

```
工作台记忆页签: "No memories linked to this session. Memories from other sessions are excluded."
/api/sessions/.../workbench -> memories: 0
```

修复后：

```
/api/sessions/.../workbench -> memories: 2
  kind=long_term ws=52b68c894e global=False source=session:websocket:8047b950
    用户规定该项目的部署顺序为：先使用 pnpm install 安装依赖，再运行 pnpm …
  kind=long_term ws=52b68c894e global=False source=session:websocket:8047b950
    该项目可按“pnpm install 安装依赖 → pnpm test 执行 Vites…
```

面板出参（`/api/settings/memory/memories`）：

```
session_id=websocket:8047b950-8e43-4ad3-856e-e942e996bff1  title='kevu'
```

浏览器上下文内直接请求工作台接口：`status=200, memories=2`。

## 未验证项

- **工作台前端视觉未截图确认**：内置浏览器与 headless Chrome 的 `document.visibilityState` 都是
  `hidden`，而工作台轮询受 `usePageVisibility` 门控，面板停在「Loading session state…」；
  同一时刻在页面内直接 fetch 工作台接口返回 200 + 2 条。即**数据链路已验证，渲染由
  `workbench-memories.test.tsx` 覆盖，缺一次真人可见窗口的目视确认**。
- `json_extract` 全表扫未在万行库上实测（当前实例 10 行）；按计划该查询只在打开工作台时触发。
- prompt 新增 subject/predicate 后真实模型的抽取质量未做量化对比（仅确认抽取仍成功产出记忆）。
- 未跑 `pytest tests/` 全量（用户指示不再跑全量套件）。

## Next

- 6 个提交在本地，**未 push**（分支领先远端 6）。
- 建议真人开一次可见窗口，目视确认工作台「记忆」页签与面板的来源会话标签。
- 历史数据（修复前抽取的）`linked_memory_ids` 仍为空，但工作台已能靠会话反查显示；
  若别处要消费 `linked_memory_ids`，需单独做一次性回填。
