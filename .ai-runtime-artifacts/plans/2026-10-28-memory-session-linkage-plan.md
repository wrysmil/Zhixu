---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
  - harness-kit/.agents/skills/git-xywh/SKILL.md
dispatch: n/a
source:
  - AGENTS.md
  - core/routing.md
  - .ai-runtime-artifacts/verifications/2026-10-28-memory-workspace-e2e-verification-lite.md
  - .ai-runtime-artifacts/verifications/2026-10-28-memory-observability-fix-verification-lite.md
created_at: 2026-10-28
status: implemented
approved: true
verification: .ai-runtime-artifacts/verifications/2026-10-28-memory-session-linkage-verification-lite.md
---

# 记忆 ↔ 会话联动 Implementation Plan

> **实施结果：** 5 个 Task 全部完成，另加真机验证抓出的 2 个修正，共 6 个提交
> （`d6df042` `d2a7667` `e5583e9` `5de8fa7` `ad4a131` `ac83a08`）。
> 计划中「i18n × 10 语言包」一步**未执行**——记忆组件用的是内联双语
> `text(zh, en)` 助手而非 locale JSON，加语言包键是错的方向。
> 详见验证报告。

> **For agentic workers:** REQUIRED SUB-SKILL: 按 WU 顺序在**当前分支顺序执行**（不派子 Agent、不建 worktree），
> 每个 WU 独立提交。步骤用 checkbox（`- [ ]`）跟踪。

**Goal:** 让「记忆从哪次会话产出」在两个方向都可见：工作台能列出本会话抽出的记忆（此前恒为空），记忆面板能显示每条记忆的来源会话。

**Architecture:** 三条独立但互补的改动。① **抽取根因**：`episode.entities` ↔ `memory.subject` 的字符串相等匹配在实践中从未成立（抽取 prompt 根本��要求 `subject`），改为「语义匹配 + 本批次兜底」，并保证 `source_episode_id` 一律回填。② **工作台读侧**：新增按 `metadata.provenance.session_id` 反查记忆的能力（由抽取器直接写入，不依赖 episode 关联，覆盖历史数据），工作台改用它并叠加工作区可见性过滤。③ **面板展示**：记忆 API 顶层补 `session_id` / `session_title`，前端卡片与详情弹窗展示来源会话。

**Tech Stack:** Python 3.11+ / asyncio / loguru / SQLite（FTS5 + JSON1）；React 18 + TypeScript + Vite + vitest + Testing Library；pytest + basedpyright + ruff。

---

## 根因与证据

| 现象 | 证据 |
| --- | --- |
| 工作台「记忆」页签恒为空 | DOM 原文 `No memories linked to this session. Memories from other sessions are excluded.`；该会话实际抽出了 2 条记忆 |
| episode 与记忆无关联 | `episodes.linked_memory_ids = []`、`memories.source_episode_id = None` |
| 关联为何断 | `extractor.py:1669-1676` 用 `episode.entities` 逐个查 `memory_by_subject`（键是记忆 `subject`）；而 `prompts.py` 的记忆 schema **只有** `content/type/priority/importance/tags`，**从不要求 `subject`** → subject 常为空 → 匹配恒不成立 |
| 面板看不出来源会话 | 记忆 API 顶层无 `session_id`；会话 id 只在 `metadata.provenance.session_id`；`MemoryPresentation.tsx` 不渲染 |

## 文件结构

**新建**：无（不新增模块，全部落在既有文件里）。

**修改（后端 6）**
- `nanobot/memory/prompts.py` — 抽取 prompt 补 `subject` / `predicate` 与填写规范
- `nanobot/memory/extractor.py` — 阶段 4b 关联兜底 + `source_episode_id` 一律回填 + 日志
- `nanobot/memory/repository.py` — 新增 `list_memories_by_session()`
- `nanobot/webui/memory_api.py` — 新增 `list_memories_by_session_payload()`；`memory_payload` 补 `session_id` / `session_title`
- `nanobot/webui/memory_presentation.py` — `PresentationContext.session_label()`（会话标题解析 + 缓存）
- `nanobot/webui/memory_routes.py` — 新增动作 `memory-list-by-session`
- `nanobot/webui/session_workbench.py` — 工作台改用会话反查
- `nanobot/webui/gateway_services.py` — 接线新操作

**修改（前端 6 + i18n 10）**
- `webui/src/lib/types.ts` — `MemoryPresentation` 补 `session_id` / `session_title`
- `webui/src/components/workbench/session-workbench-api.ts` — 工作台记忆条目类型与校验器补 `workspace_id` / `is_global` / `session_id`
- `webui/src/components/workbench/workbench-context.tsx` — 渲染「来源会话」「归属工作区」标签
- `webui/src/components/settings/memory/MemoryPresentation.tsx` — 新增 `SessionBadge`
- `webui/src/components/settings/memory/MemoryListView.tsx` — 卡片信息行 + 详情弹窗加来源会话
- `webui/src/i18n/locales/{en,es,fr,id,ja,ko,pt-BR,vi,zh-CN,zh-TW}/common.json` — 新增文案键（**10 语言必须齐全**，否则 `i18n.test.tsx` 的对齐断言变红）

**测试（6）**
- `tests/memory/test_extractor_workspace.py` — 关联兜底
- `tests/memory/test_provenance.py` — 仓库层按会话反查
- `tests/webui/test_session_workbench.py` — 工作台列出本会话记忆
- `tests/webui/test_memory_api.py` — 反查动作 + 可见性过滤
- `tests/webui/test_memory_presentation_api.py` — `session_id` / `session_title` 出参
- `webui/src/tests/memory-presentation.test.tsx` — 来源会话渲染

---

## 已拍板的三项决策

1. 工作台反查**加工作区可见性过滤**（顺手堵上「工作台能显示别的 workspace 记忆」这个既有洞；当前链接为空、实际显示 0 条，**无可见回归**）。
2. 来源会话**标题优先、session key 兜底**（key 放 tooltip）。
3. 工作台的归属标签**一并补**（后端已算 `workspace_id` / `is_global`，前端类型把它们丢掉了）。

---

### Task 1: 抽取根因修复（关联兜底 + subject 回填）

**Files:**
- Modify: `nanobot/memory/prompts.py`（记忆 schema 段）
- Modify: `nanobot/memory/extractor.py:1669-1676`（阶段 4b）
- Test: `tests/memory/test_extractor_workspace.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/memory/test_extractor_workspace.py`：

```python
def test_episode_links_batch_memories_when_subject_is_empty() -> None:
    """抽取 prompt 不要求 subject，subject 为空时关联也必须建立。"""
    # 构造：一条 subject 为空的记忆 + 一个带 entities 的 episode
    # 期望：episode.linked_memory_ids 含该记忆 id，且 memory.source_episode_id 被回填
```

（实现时按该文件既有的 fixture 写法补全构造代码；断言点固定为
`episode.linked_memory_ids` 含新记忆 id、`memory.source_episode_id == episode.id`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/memory/test_extractor_workspace.py -q -k episode_links`
Expected: FAIL（`linked_memory_ids` 为空）

- [ ] **Step 3: 改抽取 prompt，补 subject / predicate**

`nanobot/memory/prompts.py` 记忆 schema 段：

```python
  {
    "memories": [
      {"content": "用户有使用命令行处理文件的习惯", "type": "PREFERENCE", "priority": "long_term", "importance": 0.6, "tags": ["cli"],
       "subject": "用户", "predicate": "有使用命令行处理文件的习惯"}
    ],
```

并在撰写规范里补一条：

```
- **subject**：这条记忆的**主语短语**（如「用户」「项目 alpha-app」「部署流程」），
  尽量与情节记忆的 entities 用同一说法，便于两者互相关联；拿不准时给最贴近的主语，不要留空
- **predicate**：主语与记忆内容之间的关系短语（如「使用」「位于」「偏好」）
```

- [ ] **Step 4: 实现关联兜底**

`nanobot/memory/extractor.py` 阶段 4b，替换为：

```python
            # 阶段 4b：反向关联
            episode = filtered.episode
            linked_ids: list[str] = []
            semantic_ids: list[str] = []
            if episode is not None:
                # 语义关联：episode.entities 命中本批次记忆的 subject
                for entity in episode.entities:
                    memory_id = memory_by_subject.get(entity)
                    if memory_id is not None and memory_id not in semantic_ids:
                        semantic_ids.append(memory_id)
                # 兜底：本批次产出的记忆一律挂到本情节上。
                # 此前只靠 entities ↔ subject 的字符串相等，而抽取 prompt 从不要求
                # subject → subject 常为空 → 关联整条断裂，工作台与 Episodes 页签恒为 0 条。
                for memory_id in saved_memory_ids:
                    if memory_id not in linked_ids:
                        linked_ids.append(memory_id)
                for memory_id in semantic_ids:
                    if memory_id not in linked_ids:
                        linked_ids.append(memory_id)
                logger.info(
                    "memory episode linkage: session={} episode_semantic={} episode_batch={}",
                    session.key,
                    len(semantic_ids),
                    len(linked_ids),
                )
```

并把 `Episode.linked_memory_ids` 的语义写进 docstring：「本情节产出的记忆 + 语义相关的记忆」。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/memory -q`
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add nanobot/memory/prompts.py nanobot/memory/extractor.py tests/memory/test_extractor_workspace.py
git commit -m "fix(memory): 情节与记忆的关联不再依赖 subject 字符串匹配"
```

---

### Task 2: 后端——按会话反查记忆 + 工作台改用

**Files:**
- Modify: `nanobot/memory/repository.py`（新增函数）
- Modify: `nanobot/webui/memory_api.py`（新增 payload 函数）
- Modify: `nanobot/webui/memory_routes.py`（新增动作）
- Modify: `nanobot/webui/gateway_services.py`（接线）
- Modify: `nanobot/webui/session_workbench.py`（工作台改用）
- Test: `tests/memory/test_provenance.py`、`tests/webui/test_session_workbench.py`

- [ ] **Step 1: 写失败测试（仓库层）**

`tests/memory/test_provenance.py`：

```python
def test_list_memories_by_session_filters_by_provenance_and_workspace() -> None:
    """按 provenance.session_id 反查，且受可见集合约束。"""
```

断言：属于 session-a 的记忆能查到；同属 session-a 但 workspace_id 不在可见集合内的查不到。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/memory/test_provenance.py -q -k by_session`
Expected: FAIL（`list_memories_by_session` 不存在）

- [ ] **Step 3: 实现 repository 函数**

`nanobot/memory/repository.py`：

```python
def list_memories_by_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    workspace_ids: Sequence[str] | None = None,
    limit: int = 20,
) -> list[Memory]:
    """按抽取来源会话反查记忆（``metadata.provenance.session_id``）。

    与 episode 的 ``linked_memory_ids`` 互补：那条链路依赖 LLM 是否填了
    subject，早期数据普遍为空；``provenance.session_id`` 由抽取器直接写入，
    是「这条记忆由哪次会话产出」的可靠依据。

    代价：``metadata`` 上的 JSON 路径无索引 → 全表扫。只在打开工作台时
    触发一次（不是每轮），memories 量级下可接受；上万行需加表达式索引。
    """
    condition, condition_params = _workspace_ids_condition("workspace_id", workspace_ids)
    clauses = ["json_extract(metadata, '$.provenance.session_id') = ?"]
    params: list[Any] = [session_id]
    if condition:
        clauses.append(condition)
        params.extend(condition_params)
    flat = " ".join(_SELECT_MEMORY_COLUMNS.split())
    sql = (
        f"SELECT {flat} FROM memories WHERE {' AND '.join(clauses)} "
        "ORDER BY created_at DESC LIMIT ?"
    )
    rows = conn.execute(sql, [*params, int(limit)]).fetchall()
    return [_row_to_memory(r) for r in rows]
```

- [ ] **Step 4: 实现 API payload**

`nanobot/webui/memory_api.py`：

```python
def list_memories_by_session_payload(
    services: MemoryServices, *, session_id: str, limit: int = 20,
) -> dict[str, Any]:
    """本会话产出的记忆（按 provenance.session_id 反查 + 工作区可见性过滤）。"""
    visible = memory_visibility_ids(services.workspace_id)
    with services.database.connect() as conn:
        rows = list_memories_by_session(
            conn, session_id, workspace_ids=visible, limit=limit
        )
    _, _, context = _read_presentation(services)
    return {"items": [memory_payload(row, context) for row in rows]}
```

（`memory_visibility_ids` 从 `nanobot.memory.provenance` 导入。）

- [ ] **Step 5: 路由与接线**

`nanobot/webui/memory_routes.py`：
- `MemorySettingsOperations` 增加 `list_memories_by_session: Callable[..., dict[str, Any]]`
- `MEMORY_ACTION_NAMES` 增加 `"memory-list-by-session"`
- `dispatch` 增加：

```python
    if action == "memory-list-by-session":
        session_id = (query_first(query, "session_id") or "").strip()
        if not session_id:
            raise WebUISettingsError("session_id is required")
        return operations.list_memories_by_session(
            session_id=session_id,
            limit=_maybe_int(query_first(query, "limit")) or 20,
        )
```

`nanobot/webui/gateway_services.py::build_memory_operations` 增加绑定：
`"list_memories_by_session": lambda **kw: memory_api.list_memories_by_session_payload(services, **kw)`

- [ ] **Step 6: 工作台改用会话反查**

`nanobot/webui/session_workbench.py`：把「阶段 4c」的 episode→linked 反查改成
「先按 session_id 反查，episode 链接作为兜底」：

```python
        try:
            session_memories = memory_operations.list_memories_by_session(
                session_id=session.key, limit=20
            ).get("items", [])
        except Exception:
            unavailable("session_memories")
            session_memories = []
        for row in _rows(session_memories, 20):
            content = _text(row.get("content"))
            if not content:
                continue
            workspace_id = _text(row.get("workspace_id"), 64) or "default"
            memories.append({
                "id": _text(row.get("id"), 100),
                "kind": "long_term",
                "title": content[:80],
                "content": content,
                "source": "session:" + session.key,
                "updated_at": _text(row.get("updated_at"), 80) or None,
                "persisted": True,
                "workspace_id": workspace_id,
                "is_global": workspace_id == "default",
            })
```

保留原 episode→linked 分支作为**兜底**（历史数据里 provenance 无 session_id 时仍能命中），
并用 `seen` 集合按记忆 id 去重、总量上限 20。

- [ ] **Step 7: 跑测试**

Run: `uv run --no-sync pytest tests/memory tests/webui/test_session_workbench.py tests/webui/test_memory_api.py -q`
Expected: 全绿

- [ ] **Step 8: 提交**

```bash
git add nanobot/memory/repository.py nanobot/webui/memory_api.py nanobot/webui/memory_routes.py \
        nanobot/webui/gateway_services.py nanobot/webui/session_workbench.py \
        tests/memory/test_provenance.py tests/webui/test_session_workbench.py tests/webui/test_memory_api.py
git commit -m "feat(memory): 工作台按来源会话反查本会话的记忆"
```

---

### Task 3: 前端——工作台记忆页签显示来源会话与归属

**Files:**
- Modify: `webui/src/components/workbench/session-workbench-api.ts:19,44`
- Modify: `webui/src/components/workbench/workbench-context.tsx:20-32`
- Test: `webui/src/tests/memory-presentation.test.tsx`（或新增 workbench 测试文件）

- [ ] **Step 1: 补类型与校验器**

`session-workbench-api.ts`：

```ts
memories: { id: string; kind: "working" | "long_term"; title: string; content: string; source: string; updated_at: string | null; persisted: boolean; workspace_id?: string; is_global?: boolean; session_id?: string | null }[];
```

校验器（`:44` 附近）同步接受这三个可选字段。

- [ ] **Step 2: 渲染归属与来源标签**

`workbench-context.tsx` 的记忆条目里，在「来源」一行后追加：

```tsx
      {memory.workspace_id && (
        <span className="mt-1 block text-[10px] text-muted-foreground">
          {memory.is_global
            ? text("归属：全局记忆", "Ownership: global memory")
            : text(`归属：工作区 ${memory.workspace_id.slice(0, 8)}`, `Ownership: workspace ${memory.workspace_id.slice(0, 8)}`)}
        </span>
      )}
```

- [ ] **Step 3: 跑测试**

Run: `cd webui && npx vitest run src/tests/memory-presentation.test.tsx`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add webui/src/components/workbench/session-workbench-api.ts webui/src/components/workbench/workbench-context.tsx
git commit -m "feat(webui): 工作台记忆条目显示归属工作区"
```

---

### Task 4: 前端 + API——记忆面板显示来源会话

**Files:**
- Modify: `nanobot/webui/memory_presentation.py`（`PresentationContext.session_label`）
- Modify: `nanobot/webui/memory_api.py`（`memory_payload` 补字段）
- Modify: `webui/src/lib/types.ts`（`MemoryPresentation` 补字段）
- Modify: `webui/src/components/settings/memory/MemoryPresentation.tsx`（`SessionBadge`）
- Modify: `webui/src/components/settings/memory/MemoryListView.tsx`（卡片 + 详情）
- Modify: `webui/src/i18n/locales/*/common.json`（10 语言）
- Test: `tests/webui/test_memory_presentation_api.py`、`webui/src/tests/memory-presentation.test.tsx`

- [ ] **Step 1: 写失败测试（后端出参）**

`tests/webui/test_memory_presentation_api.py`：

```python
def test_memory_payload_exposes_source_session() -> None:
    """记忆条目顶层带 session_id 与可读标题。"""
```

断言：`payload["session_id"] == "websocket:abc"`、`payload["session_title"] == "部署流程讨论"`。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/webui/test_memory_presentation_api.py -q -k source_session`
Expected: FAIL（无 `session_id` 顶层字段）

- [ ] **Step 3: 实现会话标题解析**

`nanobot/webui/memory_presentation.py` 的 `PresentationContext` 增加：

```python
        self._session_titles: dict[str, str | None] = {}

    def session_label(self, session_id: str | None) -> str | None:
        """会话 id → 可读标题；取不到返回 ``None``（调用方降级显示 key）。"""
        if not session_id or self.services is None or self.services.session_metadata is None:
            return None
        if session_id in self._session_titles:
            return self._session_titles[session_id]
        try:
            metadata = self.services.session_metadata(session_id) or {}
        except Exception:
            metadata = {}
        raw = metadata.get("title")
        label = raw.strip() if isinstance(raw, str) and raw.strip() else None
        self._session_titles[session_id] = label
        return label
```

- [ ] **Step 4: `memory_payload` 补字段**

`nanobot/webui/memory_api.py`：

```python
    provenance = m.metadata.get("provenance") if isinstance(m.metadata, dict) else None
    raw_session = provenance.get("session_id") if isinstance(provenance, dict) else None
    session_id = raw_session if isinstance(raw_session, str) and raw_session else None
    ...
    return {
        ...
        "session_id": session_id,
        "session_title": context.session_label(session_id) if (context is not None and session_id) else None,
    }
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/webui/test_memory_presentation_api.py -q`
Expected: PASS

- [ ] **Step 6: 前端类型 + 组件**

`webui/src/lib/types.ts` 的 `MemoryPresentation`：

```ts
export interface MemoryPresentation {
  origin_kind?: MemoryOrigin;
  generation_method?: MemoryOrigin;
  trigger_mode?: "automatic" | "manual" | "unknown";
  trigger_reason?: string;
  workspaces?: MemoryWorkspace[];
  session_id?: string | null;
  session_title?: string | null;
}
```

`MemoryPresentation.tsx` 新增：

```tsx
export function SessionBadge({ record }: { record: { session_id?: string | null; session_title?: string | null } }) {
  if (!record.session_id) return <span className="text-muted-foreground">{text("来源会话未记录", "Source session not recorded")}</span>;
  const label = record.session_title || record.session_id.slice(0, 18);
  return <span title={record.session_id} className="max-w-56 truncate text-muted-foreground">{text(`会话：${label}`, `Session: ${label}`)}</span>;
}
```

`MemoryListView.tsx` 卡片信息行加 `<SessionBadge record={memory} />`；
`MemoryDetailFields` 的 `DetailFields` 数组加一行：

```tsx
    [text("来源会话", "Source session"), <SessionBadge record={memory} />],
```

- [ ] **Step 7: i18n × 10**

在**每个** `webui/src/i18n/locales/<locale>/common.json` 的 `settings.memory` 下新增：

```json
"sourceSession": "来源会话",
"sourceSessionUnknown": "来源会话未记录"
```

（en: `"Source session"` / `"Source session not recorded"`；其余语言按语义翻译，
**10 个语言必须齐全**，否则 `i18n.test.tsx` 的 locale 对齐断言会红。）

- [ ] **Step 8: 跑前端测试**

Run: `cd webui && npx vitest run src/tests/memory-presentation.test.tsx src/tests/i18n.test.tsx`
Expected: PASS（`i18n.test.tsx` 的既有 zh-CN `rosterCount` 失败项属既有问题，不在本次范围）

- [ ] **Step 9: 提交**

```bash
git add nanobot/webui/memory_presentation.py nanobot/webui/memory_api.py \
        webui/src/lib/types.ts webui/src/components/settings/memory/MemoryPresentation.tsx \
        webui/src/components/settings/memory/MemoryListView.tsx webui/src/i18n/locales/*/common.json \
        tests/webui/test_memory_presentation_api.py webui/src/tests/memory-presentation.test.tsx
git commit -m "feat(memory): 记忆面板显示来源会话"
```

---

### Task 5: 验证与落盘

**Files:**
- Create: `.ai-runtime-artifacts/verifications/2026-10-28-memory-session-linkage-verification-lite.md`
- Create: `.ai-runtime-artifacts/verifications/2026-10-28-memory-session-linkage/`（截图）

- [ ] **Step 1: 跑定向测试**

```bash
uv run --no-sync pytest tests/memory tests/webui tests/agent -q
cd webui && npx vitest run
```

- [ ] **Step 2: 静态检查**

```bash
uv run --no-sync ruff check nanobot/
uv run --no-sync basedpyright <本批改动的 Python 文件>
```

基线：ruff 11 条既有、basedpyright 新增诊断 0。**失败用例必须 stash 掉本批改动复跑归因**，
确认非本批引入才可接受。

- [ ] **Step 3: 真机验证**

复用隔离实例（网关 18771 / 前端 5173）：起会话 → 空闲抽取 → 打开工作台「记忆」页签
应列出本会话记忆且带归属标签；打开记忆中心卡片应显示来源会话。

- [ ] **Step 4: 截图**

用 CDP 脚本（本会话已有 `cdp_shots.py` / `cdp_workbench_shot.py`）截三张：
① 记忆中心卡片带来源会话 ② 工作台记忆页签有本会话记忆 ③ 记忆详情弹窗的来源会话字段。

- [ ] **Step 5: 落盘报告**

按 `harness-kit/artifact-templates/verification-lite.md` 写
`.ai-runtime-artifacts/verifications/2026-10-28-memory-session-linkage-verification-lite.md`，
含命令表、结果、失败归因、截图引用。

- [ ] **Step 6: 提交**

```bash
git add .ai-runtime-artifacts/verifications/2026-10-28-memory-session-linkage-verification-lite.md \
        .ai-runtime-artifacts/verifications/2026-10-28-memory-session-linkage/
git commit -m "docs(artifacts): 落盘记忆会话联动的实测与验证报告"
```

---

## 自检

**范围覆盖**：三个决策项 → 决策 1 落在 Task 2 Step 6（可见性过滤）；决策 2 落在 Task 4 Step 4/6（标题优先、key 兜底）；决策 3 落在 Task 3（工作台归属标签）。根因修复 → Task 1。方案 2 → Task 2。方案 3 → Task 4。无遗漏。

**类型一致性**：`list_memories_by_session` 在 repository / memory_api / memory_routes / gateway_services 四处签名一致（`session_id` + `limit`）；`PresentationContext.session_label` 在 `memory_payload` 中调用；TS 侧 `session_id` / `session_title` / `workspace_id` / `is_global` 命名与后端出参一致。

**占位符扫描**：Task 1 Step 1 与 Task 4 Step 1 的测试代码标注了「按该文件既有 fixture 写法补全构造代码」——这是唯一未给出完整代码的步骤，实现时必须先读该文件既有 fixture 再写，禁止凭想象编造构造方式。其余步骤均给出可直接落地的代码。

**已知遗留（不在本批范围）**
- `json_extract` 无索引 → 工作台打开会话时一次全表扫；上万行记忆需加表达式索引。
- `linked_memory_ids` 语义由「仅语义相关」扩为「本批次产出 + 语义相关」，四个消费方（`memory_presentation.py:53`、工作台、`EpisodeDetailPanel`、`EpisodeListView` 计数）随之变化；Episodes 页签的「N 条关联记忆」会从 0 变成实际条数（属预期改善）。
- 抽取 prompt 改动会影响 LLM 输出形状，需在真机验证时确认抽取质量未退化。

## Next

（写入后须暂停 — 即使用户句末含「然后执行」）

- 计划确认 → 说「开始实现」或「执行」
- 需要调整 → 直接说修改意见
