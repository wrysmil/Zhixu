---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md（已读，按 Goal / Architecture / Task 细步 / 自检结构撰写）
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/specs/2026-11-05-memory-injection-redesign-spec.md
  - .ai-runtime-artifacts/specs/2026-11-05-memory-injection-redesign-overview.md
created_at: 2026-11-06
status: draft
approved: false
dispatch: n/a（单线顺序执行，不拆并行）
---

# 知序记忆体系：长期记忆 + 经验库 实施计划

> **For agentic workers:** 按本文件顺序逐 Task 执行；每个 Task 先写失败测试，再实现，再验证，再提交。
> 不在本计划内继续扩展知识图谱、文件/SQLite 双向同步或 SQLite 单主本方案。

**Goal:** 建立两条互不复制的主存储：`MEMORY.md` 作为长期记忆（直接写正文、每轮注入），SQLite 作为经验/失败库（按需检索）；写入按类型分流，不做同步。

**Architecture:** 长期记忆落在全局与项目两级 `MEMORY.md` 内容文件，超过预算的长文拆主题文件并在正文留指针；经验以 `episodes` 表承载（新增 `source=experience`），保留结构化现象/根因/解法/验证字段，检索复用现有 episodes 通道。两条存储之间只允许“经验经用户确认后升级为规则”，不允许内容镜像。

**Tech Stack:** Python 3.11+ / asyncio / SQLite（FTS5 + 现有 retrieval engine）/ Pydantic 配置 / Jinja2 模板 / pytest（`asyncio_mode=auto`）/ React WebUI（Phase 5）。

---

## 1. 总体结构与边界

```text
用户/Agent
   │
   ├─ "以后都成立" ──► MEMORY.md（全局 / 项目，直接正文）
   │                     └─ 超预算 → 同目录独立文件（如 conventions.md）
   │
   └─ "这次发生了什么" ─► SQLite episodes（source=experience）
                          └─ 现象 / 根因 / 解法 / 验证，按需检索

禁止：同一条内容同时写两边；禁止文件↔SQLite 同步层。
允许：经验反复出现 → 用户确认 → 提炼成 MEMORY.md 规则（经验原文保留）。
```

### 1.1 写入路由（唯一判断规则）

| 判断 | 目标 | 例子 |
| --- | --- | --- |
| 以后都成立？ | `MEMORY.md` | 项目约定、构建方式、用户偏好、团队规则 |
| 这次发生过？ | SQLite 经验库 | 任务失败、踩坑、根因、修复步骤、验证结果 |

提示词里必须原样出现这句口诀：**“要规则去记忆，要历史去经验库；同一条内容只写一边。”**

### 1.2 关键设计决策

| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | `MEMORY.md` 直接写正文，不再维护“一行一条”的索引 | 索引维护成本高；规则需要每轮可见，不能依赖模型主动 read_file |
| D2 | 长文超预算才拆同目录独立文件（如 `conventions.md`），`MEMORY.md` 留一行指针 | 保留按需读取能力，同时避免每轮注入无限增长 |
| D3 | 经验复用 `episodes` 表，新增 `EpisodeSource.EXPERIENCE` | 已有检索通道、工作区过滤、时间排序、访问统计；首版不新建表 |
| D4 | 经验不自动过期、不自动归档 | 经验是历史证据，只降权不删除；删除仅用户显式操作 |
| D5 | 长事实从 `memories` 表迁到 `MEMORY.md`，旧行标记归档并保留指针 | 消除两套长期记忆；保留审计与回滚 |
| D6 | Dream 从“记忆整理者”扩为“记忆整理 + 经验复盘”，所有破坏性动作改为提议 | 避免自动删规则、自动升级经验造成不可逆错误 |
| D7 | 主动沉淀询问只在任务结束问一次，先交付结果再询问 | 避免打断主任务和反复骚扰 |
| D8 | 不新增经验库独立开关，沿用 `memory_enabled` | 少一个配置维度；`memory_file_mode=false` 时保持旧路径不动，新工具与新提示词只在文件模式生效 |

---

## 2. 文件结构映射

### 新增

| 文件 | 职责 |
| --- | --- |
| `nanobot/agent/tools/memory_write.py` | 长期记忆写入工具（写 `MEMORY.md` 正文、校验上限） |
| `nanobot/agent/tools/experience.py` | `experience_record` 工具；经验检索复用 `memory_search` |
| `nanobot/memory/experience.py` | 经验记录构造、查询、摘要投影（供工具与 Dream 复用） |
| `tests/memory/test_memory_doc.py` | MEMORY.md 内容读写、上限、分段、注入 |
| `tests/memory/test_experience.py` | 经验写入、检索、工作区隔离、不过期 |
| `tests/agent/test_memory_write_tool.py` | `memory_write` 工具参数与错误 |
| `tests/agent/test_experience_tools.py` | `experience_record` / `experience_search` 工具 |
| `.ai-runtime-artifacts/verifications/2026-11-06-memory-boundary-audit.py` | Phase 0 只读体检脚本 |

### 修改

| 文件 | 改动 |
| --- | --- |
| `nanobot/memory/files.py` | 内容模式读写、按 scope 上限（全局 3000 / 项目 6000 字符）、长文溢出、移除索引注入依赖 |
| `nanobot/agent/context.py` | 注入 `MEMORY.md` 正文而不是索引块 |
| `nanobot/memory/models.py` | `EpisodeSource.EXPERIENCE` |
| `nanobot/memory/repository.py` | `record_experience` / `list_experiences`，episode 检索支持 outcome 过滤 |
| `nanobot/memory/extractor.py` | 经验类产物走 episodes；长期事实只写文件 |
| `nanobot/memory/writer.py` | 停止长期事实写 `memories`（迁移完成后） |
| `nanobot/memory/decay.py` | 明确不处理 episodes；补回归测试 |
| `nanobot/agent/tools/memory_search.py` | 描述改为“经验库检索”，可选 outcome 过滤 |
| `nanobot/agent/tools/memory_admin.py` | 保持文件模式下不注册 `add_memory` / `memory_delete`；长期记忆写入由 `memory_write` 承担 |
| `nanobot/agent/memory.py` | Dream 工具集：只读经验检索，无写入 |
| `nanobot/templates/AGENT.md` | 记忆规范整节重写（长期记忆 + 经验库 + 主动询问） |
| `nanobot/templates/agent/identity.md` | 两套存储说明 |
| `nanobot/templates/agent/dream.md` | 增加经验复盘与“提议不自动执行” |
| `nanobot/cli/gateway_runtime.py` | Dream 经验摘要输入（如需要） |
| `nanobot/webui/memory_api.py` | Phase 5：MEMORY.md 文档读写、经验列表 |
| `webui/src/components/settings/memory/*` | Phase 5：长期记忆 / 经验库两个页签 |
| `docs/memory.md` | 架构说明更新 |

---

## 3. Phase 0：只读体检与预算测量

**目标：** 在改代码前确认数据规模、分类是否可自动判断、每轮注入成本。

- [ ] **Step 1: 写体检脚本**

创建 `.ai-runtime-artifacts/verifications/2026-11-06-memory-boundary-audit.py`，只读打开 `~/.nanobot/memory/state.db` 与记忆目录，输出 JSON + Markdown。

核心查询：

```python
QUERIES = {
    "memories_by_type": """
        SELECT workspace_id, type, priority, source, COUNT(*) AS n,
               SUM(LENGTH(content)) AS chars
        FROM memories
        GROUP BY workspace_id, type, priority, source
        ORDER BY n DESC
    """,
    "episodes_by_outcome": """
        SELECT workspace_id, source, outcome, COUNT(*) AS n,
               AVG(LENGTH(summary)) AS avg_chars
        FROM episodes
        GROUP BY workspace_id, source, outcome
        ORDER BY n DESC
    """,
    "expiring_memories": """
        SELECT COUNT(*) FROM memories
        WHERE expires_at IS NOT NULL AND datetime(expires_at) < datetime('now')
    """,
}
```

同时统计：

- 每个 workspace 的 `MEMORY.md` 字符数、条目数；
- 当前 `MEMORY_INDEX_MAX_CHARS=2000` 注入块的实际字符数；
- 按 `chars / 1.5` 粗估中文 token；
- 随机抽 30 条 `memories`，人工标注“长期规则 / 经验 / 不确定”。

- [ ] **Step 2: 跑体检并写报告**

```bash
python .ai-runtime-artifacts/verifications/2026-11-06-memory-boundary-audit.py --db ~/.nanobot/memory/state.db --out .ai-runtime-artifacts/verifications/2026-11-06-memory-boundary-audit.md
```

输出到 `.ai-runtime-artifacts/verifications/2026-11-06-memory-boundary-audit.md`。

- [ ] **Step 3: 停止条件**

出现任一条即暂停，先和用户确认，不进入 Phase 1：

- 30 条抽样里“不确定”超过 10 条；
- 单 workspace 的 `MEMORY.md` 预计正文超过 12k 字符；
- `memories` 表中类型为 `experience/error` 的行超过 30%；
- 每轮注入估算超过 8k tokens 且无 prompt caching 条件。

---

## 4. Phase 1：长期记忆改为“正文模式”

**目标：** `MEMORY.md` 只存正文；每轮注入正文；取消索引维护；超预算拒绝写入并提示拆分。

### Task 1.1：文件层内容读写

**Files:**
- Modify: `nanobot/memory/files.py`
- Test: `tests/memory/test_memory_doc.py`

- [ ] **Step 1: 写失败测试**

```python
def test_write_and_read_memory_doc(tmp_path):
    store = MemoryFileStore(tmp_path / "global", tmp_path / "project")
    store.write_memory_doc("global", "# 全局记忆\n\n## 偏好\n- 用中文回复\n")
    assert "用中文回复" in store.read_memory_doc("global")


def test_memory_doc_rejects_over_cap(tmp_path):
    store = MemoryFileStore(tmp_path / "global", tmp_path / "project")
    with pytest.raises(ValueError, match="上限"):
        store.write_memory_doc("global", "x" * 3001)


def test_memory_doc_append_section(tmp_path):
    store = MemoryFileStore(tmp_path / "global", tmp_path / "project")
    store.write_memory_doc("project", "## 构建\n- 用 bun\n")
    store.append_memory_section("project", "模型", "- 默认 doubao\n")
    text = store.read_memory_doc("project")
    assert "## 构建" in text and "## 模型" in text


def test_append_memory_bullet_reuses_section(tmp_path):
    store = MemoryFileStore(tmp_path / "global", tmp_path / "project")
    store.append_memory_bullet("project", "项目事实", "构建用 bun")
    store.append_memory_bullet("project", "项目事实", "端口 8765")
    text = store.read_memory_doc("project")
    assert text.count("## 项目事实") == 1
    assert "构建用 bun" in text and "端口 8765" in text
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/memory/test_memory_doc.py -v
```

Expected: `AttributeError: write_memory_doc`.

- [ ] **Step 3: 实现内容读写**

在 `nanobot/memory/files.py` 增加：

```python
MEMORY_DOC_MAX_CHARS = {"global": 3000, "project": 6000}


def _doc_path(self, scope: str) -> Path:
    return self.dir_for(scope) / "MEMORY.md"


def read_memory_doc(self, scope: str) -> str:
    try:
        return self._doc_path(scope).read_text(encoding="utf-8")
    except OSError:
        return ""


def write_memory_doc(self, scope: str, content: str) -> None:
    text = content.strip()
    limit = MEMORY_DOC_MAX_CHARS.get(scope, MEMORY_DOC_MAX_CHARS["project"])
    if len(text) > limit:
        raise ValueError(
            f"记忆内容超过上限：{len(text)} > {limit} 字符；"
            "请把长文拆到同目录的独立文件（如 conventions.md），并在 MEMORY.md 留一行指针"
        )
    with memory_file_lock():
        path = self._doc_path(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text + "\n", encoding="utf-8")
        tmp.replace(path)


def append_memory_section(self, scope: str, title: str, body: str) -> None:
    current = self.read_memory_doc(scope).rstrip()
    section = f"## {title.strip()}\n{body.strip()}"
    self.write_memory_doc(scope, f"{current}\n\n{section}" if current else f"# 记忆\n\n{section}")


def append_memory_bullet(self, scope: str, section: str, bullet: str) -> None:
    header = f"## {section.strip()}"
    lines = self.read_memory_doc(scope).rstrip().splitlines()
    if header in lines:
        start = lines.index(header)
        insert_at = len(lines)
        for i in range(start + 1, len(lines)):
            if lines[i].startswith("## "):
                insert_at = i
                break
        lines.insert(insert_at, f"- {bullet.strip()}")
    else:
        if lines:
            lines.extend(["", header, f"- {bullet.strip()}"])
        else:
            lines = ["# 记忆", "", header, f"- {bullet.strip()}"]
    self.write_memory_doc(scope, "\n".join(lines))
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/memory/test_memory_doc.py -v
```

Expected: 4 passed.

- [ ] **Step 5: 提交**

```bash
git add nanobot/memory/files.py tests/memory/test_memory_doc.py
git commit -m "feat(memory): MEMORY.md 改为正文读写并限制字数"
```

### Task 1.2：注入改为正文

**Files:**
- Modify: `nanobot/agent/context.py`
- Modify: `nanobot/memory/files.py`
- Modify: `tests/memory/test_memory_injection.py`
- Modify: `tests/memory/test_memory_files.py`
- Test: `tests/memory/test_memory_doc.py`

- [ ] **Step 1: 写失败测试**

```python
def test_injection_uses_memory_doc_body(tmp_path, monkeypatch):
    store = MemoryFileStore(tmp_path / "global", tmp_path / "project")
    store.write_memory_doc("global", "# 全局记忆\n\n## 偏好\n- 用中文回复\n")
    block = render_memory_content_block(store)
    assert "用中文回复" in block
    assert "read_file" not in block  # 不再提示按需读索引
```

- [ ] **Step 2: 实现注入**

在 `files.py` 增加：

```python
def render_memory_content_block(store: MemoryFileStore) -> str:
    parts: list[str] = []
    for scope, title in (("global", "全局长期记忆"), ("project", "项目长期记忆")):
        if scope == "project" and store.project_dir is None:
            continue
        content = store.read_memory_doc(scope).strip()
        if content:
            parts.append(f"## {title}\n目录：{store.dir_for(scope)}\n\n{content}")
    if not parts:
        return ""
    return "# 长期记忆（正文）\n\n" + "\n\n".join(parts)
```

把 `context.py::_load_memory_index_block` 改为调用 `render_memory_content_block(store)`，方法名可保留以避免大范围改动，但返回值是正文；同时把 `build_system_prompt` 里的 `index_block` 变量与注释改成 `memory_doc_block`，避免后续误读。`MEMORY_INDEX_MAX_CHARS` 标记废弃；注入不再二次截断，正文长度已在写入时受 `MEMORY_DOC_MAX_CHARS` 限制。

- [ ] **Step 3: 运行测试**

```bash
pytest tests/memory/test_memory_doc.py tests/memory/test_memory_injection.py tests/memory/test_memory_files.py tests/agent/test_context_builder.py -v
```

Expected: 全绿；原“索引行注入”的断言改为“正文注入”。

- [ ] **Step 4: 提交**

```bash
git add nanobot/agent/context.py nanobot/memory/files.py
git commit -m "feat(memory): 注入 MEMORY.md 正文替代索引"
```

### Task 1.3：新增 `memory_write` 工具

**Files:**
- Create: `nanobot/agent/tools/memory_write.py`
- Test: `tests/agent/test_memory_write_tool.py`

- [ ] **Step 1: 写失败测试**

```python
async def test_memory_write_appends_section(tmp_path):
    tool = MemoryWriteTool(store=MemoryFileStore(tmp_path / "g", tmp_path / "p"))
    result = await tool.execute(scope="global", section="偏好", content="- 用中文回复")
    assert "已写入" in result
    assert "用中文回复" in tool.store.read_memory_doc("global")


async def test_memory_write_rejects_over_cap(tmp_path):
    tool = MemoryWriteTool(store=MemoryFileStore(tmp_path / "g", tmp_path / "p"))
    with pytest.raises(ValueError, match="上限"):
        await tool.execute(scope="global", section="x", content="x" * 3001)
```

- [ ] **Step 2: 实现工具**

```python
class MemoryWriteTool(Tool):
    name = "memory_write"
    description = (
        "写入长期记忆（以后都成立的规则、项目约定、用户偏好）。"
        "直接写入对应 scope 的 MEMORY.md 正文；超过字数上限时拒绝，"
        "请把长文拆到同目录的独立文件（如 conventions.md），并在 MEMORY.md 留指针。"
        "经验、失败、这次任务的经过不要写这里。"
    )

    def __init__(self, store: MemoryFileStore) -> None:
        self.store = store

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["global", "project"]},
                "section": {"type": "string", "description": "小节名，如 项目约定 / 构建与运行"},
                "content": {"type": "string"},
                "mode": {"type": "string", "enum": ["append", "replace"], "default": "append"},
            },
            "required": ["scope", "section", "content"],
        }

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(store=MemoryFileStore.for_project(str(ctx.workspace)))

    async def execute(self, *, scope: str, section: str, content: str, mode: str = "append", **_: Any) -> str:
        if scope not in ("global", "project"):
            raise ValueError(f"scope 必须是 global 或 project，收到 {scope!r}")
        if mode not in ("append", "replace"):
            raise ValueError(f"mode 必须是 append 或 replace，收到 {mode!r}")
        if mode == "replace":
            self.store.write_memory_doc(scope, content)
        else:
            if not section.strip():
                raise ValueError("append 模式必须提供 section")
            self.store.append_memory_section(scope, section, content)
        return f"已写入{scope}长期记忆：{section}"
```

`enabled()` 沿用 `memory_write_enabled` 门控；记忆总开关关闭或 `memory_file_mode=false` 时不注册该工具。

- [ ] **Step 3: 运行测试**

```bash
pytest tests/agent/test_memory_write_tool.py -v
```

- [ ] **Step 4: 提交**

```bash
git add nanobot/agent/tools/memory_write.py tests/agent/test_memory_write_tool.py
git commit -m "feat(memory): 新增 memory_write 长期记忆写入工具"
```

### Task 1.4：提示词改为正文模式

**Files:**
- Modify: `nanobot/templates/AGENT.md`
- Modify: `nanobot/templates/agent/identity.md`
- Modify: `AGENTS.md`（仓库根，补“记忆系统 + 经验库”架构说明）

- [ ] **Step 1: 重写「记忆使用规范」**

把现有“索引 + 按需 read_file + 手动补索引”一段替换为：

```markdown
## 记忆使用规范

你有两套存储，写入前先判断：

- **长期记忆（MEMORY.md）**：以后都成立 → 项目约定、用户偏好、稳定事实、协作规则。
  优先用 `memory_write` 写进对应 scope 的 `MEMORY.md` 正文，用二级标题分小节；不要用 `write_file` 直接改 MEMORY.md。
  超过字数上限时，把长文拆到同目录的独立文件（如 `conventions.md`），在 MEMORY.md 留一行「详见 conventions.md」。
- **经验库（SQLite）**：这次发生过 → 任务失败、踩坑、根因、解法、验证方式。

口诀：**要规则去记忆，要历史去经验库；同一条内容只写一边。**
一条经验反复出现，只有在用户确认后才提炼成 MEMORY.md 里的规则，经验原文继续保留。

写记忆成功后要核实文件内容，再说「已记住」。
```

- [ ] **Step 2: 更新 `identity.md` 的两条记忆说明**

删除“索引每轮注入、按需 read_file”的描述，改为“MEMORY.md 正文每轮注入；用 `memory_write` 写入；长文在同目录独立文件里按需读取”。

- [ ] **Step 3: 更新仓库根 `AGENTS.md`**

在「Key Subsystems」下补一条：

```markdown
- **Memory + Experience** (`nanobot/memory/`): 长期记忆存在全局/项目 `MEMORY.md` 正文，
  每轮注入；经验与失败记录存在 SQLite `episodes`（`source=experience`），按需检索。
  同一条内容只写一处，不做文件/SQLite 同步。
```

- [ ] **Step 4: 验证提示词**

```bash
grep -n "索引" nanobot/templates/AGENT.md nanobot/templates/agent/identity.md
```

Expected: 不再出现“补一行索引 / 按需读索引”的指令。

- [ ] **Step 5: 提交**

```bash
git add nanobot/templates/AGENT.md nanobot/templates/agent/identity.md AGENTS.md
git commit -m "docs(memory): 提示词改为长期记忆正文 + 经验库分流"
```

### Task 1.5：抽取产物写入 MEMORY.md 正文

**Files:**
- Modify: `nanobot/memory/files.py`
- Modify: `nanobot/memory/extract_writer.py`
- Modify: `nanobot/memory/extractor.py`
- Modify: `nanobot/memory/writer.py`
- Test: `tests/memory/test_memory_doc.py`

- [ ] **Step 1: 抽取改为写正文小节**

`write_extracted_memories` 不再调用 `store.write_memory` + 索引，改为：

```python
section = "用户偏好与规则" if scope == GLOBAL_SCOPE else "项目事实"
store.append_memory_bullet(
    scope,
    section,
    f"{memory.content.strip()}（{memory.created_at[:10]}）",
)
written.append(memory_name_for(memory))
```

保留原有 importance 门槛（全局 0.7 / 项目 0.5），低于门槛仍然丢弃。

- [ ] **Step 2: `write_memory` 不再重写索引**

单条文件只作为同目录独立文件的溢出通道；`write_memory` 与 `delete_memory` 落盘后都不再调用 `_sync_index`，`MEMORY.md` 只由 `memory_write`、抽取写入和用户编辑维护。删除溢出文件后，MEMORY.md 里的指针由 Dream 或 `memory_write` 修正。

同时给 `extractor` 的文件模式持久化分支加 guard：不再调用 `add_memory` / `apply_memory_changes` 写 `memories` 表；episodes 与 session 状态照常写。这样从 Phase 1 起就不产生长期事实双写，不用等到 Phase 6。

- [ ] **Step 3: 兼容旧索引文件**

启动时若发现 `MEMORY.md` 仍是旧索引格式（存在 `- [` 行且没有二级标题），把索引行转成正文 bullet（标题 + 钩子 + `详见 file.md`），**不复制文件正文**，原单条文件保留在同目录作为按需读取内容。若 `MEMORY.md` 不存在但目录里有旧单条文件，按 `list_memories` 生成同样的 bullet 指针。若旧 `MEMORY.md` 自身超过上限，先拆出长文到同目录独立文件并在正文留指针，不静默截断。

- [ ] **Step 4: 测试与提交**

```bash
pytest tests/memory/test_memory_doc.py tests/memory/test_memory_files.py -v
git add nanobot/memory/files.py nanobot/memory/extract_writer.py nanobot/memory/extractor.py nanobot/memory/writer.py tests/memory/test_memory_doc.py tests/memory/test_memory_files.py
git commit -m "refactor(memory): 抽取产物写入 MEMORY.md 正文"
```

### Task 1.6：USER.md 与全局 MEMORY.md 去重

**Files:**
- Modify: `nanobot/memory/lifecycle.py`
- Modify: `nanobot/agent/context.py`
- Test: `tests/memory/test_user_profile_projection.py`

- [ ] **Step 1: 定边界**

默认选择：**USER.md 只放“用户是谁 / 偏好画像”，全局 MEMORY.md 只放“协作规则 / 项目约定”**，同一条内容不得两边都写。

- [ ] **Step 2: 停止 USER.md 自动区从全局记忆推导**

文件模式下 `_file_mode_facts()` 不再把全局记忆文件投影成画像（全局记忆正文已经在 MEMORY.md，且可能包含规则）。`refresh_user_md_sync` 在文件模式直接返回 `{"status": "skipped", "reason": "file_mode"}`，保留已有 USER.md 内容，由用户或 Dream 的待确认流程修改。同时把 `MemoryLifecycle.for_workspace` 的缓存键加入 `file_mode`（或在模式变化时清空实例缓存），避免上一轮非文件模式的实例继续写 USER.md。

- [ ] **Step 3: 备选方案（用户选择“并入”时）**

删除 USER.md bootstrap 注入，把现有 USER.md 内容迁移到全局 MEMORY.md 的「用户画像」小节，并同步更新 `identity.md`。本计划默认不执行此步。

- [ ] **Step 4: 回归测试**

```python
def test_file_mode_skips_user_md_derivation(tmp_ws, services):
    MemoryLifecycle._instances.clear()
    lifecycle = MemoryLifecycle("test-ws", services, file_mode=True)
    result = lifecycle.refresh_user_md_sync("test-ws")
    assert result == {"status": "skipped", "reason": "file_mode"}
```

- [ ] **Step 5: 提交**

```bash
git add nanobot/memory/lifecycle.py nanobot/agent/context.py tests/memory/test_user_profile_projection.py
git commit -m "refactor(memory): USER.md 与全局记忆正文去重"
```

---

## 5. Phase 2：SQLite 经验库

**目标：** 经验写入 episodes（`source=experience`），按需检索；经验不过期、不参与日常衰减。

**时间策略：** 经验不设 `expires_at`，也不新增 `archived` 列；冷数据只通过 `ended_at` 与 `access_count` 影响排序，仍然可被检索到。需要“归档”时先写 `metadata.cold=true`，不删行。

### Task 2.1：新增经验来源

**Files:**
- Modify: `nanobot/memory/models.py`
- Modify: `nanobot/memory/extractor.py`
- Test: `tests/memory/test_experience.py`

- [ ] **Step 1: 失败测试**

```python
def test_experience_source_value(tmp_path):
    episode = Episode(
        id="e1", session_id="s1", summary="构建失败 → 缺依赖 → 安装后通过",
        started_at="2026-11-06T00:00:00Z", ended_at="2026-11-06T00:01:00Z",
        outcome=EpisodeOutcome.FAILED, source=EpisodeSource.EXPERIENCE,
        workspace_id="default",
    )
    assert episode.source == EpisodeSource.EXPERIENCE
```

- [ ] **Step 2: 实现**

```python
class EpisodeSource(str, Enum):
    MANUAL = "manual"
    SESSION_END = "session_end"
    CONTEXT_COMPRESS = "context_compress"
    TOPIC_CHANGE = "topic_change"
    DAILY_CONSOLIDATION = "daily_consolidation"
    DELETION = "deletion"
    IDLE = "idle"
    EXPERIENCE = "experience"
```

在 `extractor.py` 的 source 映射加入 `"experience": EpisodeSource.EXPERIENCE`。

- [ ] **Step 3: 测试与提交**

```bash
pytest tests/memory/test_experience.py -v
git add nanobot/memory/models.py nanobot/memory/extractor.py tests/memory/test_experience.py
git commit -m "feat(memory): 新增 experience 经验来源"
```

### Task 2.2：经验记录与查询

**Files:**
- Create: `nanobot/memory/experience.py`
- Modify: `nanobot/memory/repository.py`
- Test: `tests/memory/test_experience.py`

- [ ] **Step 1: 失败测试**

```python
def test_record_and_search_experience(tmp_path):
    db = MemoryDatabase(tmp_path, db_path=tmp_path / "state.db")
    exp = record_experience(
        db, workspace_id="default", session_id="s1",
        title="bun 构建失败", situation="bun run build 报模块缺失",
        action="直接重跑", outcome="failed",
        root_cause="依赖未安装", solution="bun install 后重跑",
        verification="bun run build 通过", tags=["bun", "build"],
    )
    rows = list_experiences(db, workspace_ids=["default"], query="bun")
    assert any(row.id == exp.id for row in rows)


def test_experience_not_visible_in_other_workspace(tmp_path):
    db = MemoryDatabase(tmp_path, db_path=tmp_path / "state.db")
    record_experience(
        db, workspace_id="proj-a", session_id="s1",
        title="bun 构建失败", situation="bun run build 报模块缺失",
        action="直接重跑", outcome="failed",
        root_cause="依赖未安装", solution="bun install 后重跑",
        verification="bun run build 通过", tags=["bun"],
    )
    assert list_experiences(db, workspace_ids=["proj-b"], query="bun") == []
```

- [ ] **Step 2: 实现**

`experience.py` 负责：

```python
def record_experience(
    db: MemoryDatabase, *, workspace_id: str, session_id: str,
    title: str, situation: str, action: str, outcome: str,
    root_cause: str = "", solution: str = "", verification: str = "",
    tags: Sequence[str] = (),
) -> Episode:
    now = datetime.now(timezone.utc).isoformat()
    db.ensure_schema()
    summary = f"{situation.strip()}\n根因：{root_cause.strip()}\n解法：{solution.strip()}"
    episode = Episode(
        id=uuid4().hex,
        session_id=session_id,
        summary=summary,
        goal=situation.strip(),
        outcome=EpisodeOutcome(outcome),
        source=EpisodeSource.EXPERIENCE,
        started_at=now,
        ended_at=now,
        entities=list(dict.fromkeys([*tags, title])),
        tools_used=[],
        tags=["experience", outcome, *tags],
        importance_score=0.6,
        workspace_id=workspace_id,
        metadata={
            "title": title,
            "action": action,
            "root_cause": root_cause,
            "solution": solution,
            "verification": verification,
        },
    )
    with db.connect() as conn:
        add_episode(conn, episode)
    return episode
```

`list_experiences` 复用 `repository.search_episodes`，条件追加 `source='experience'` 与可选 `outcome`；按 `ended_at DESC` 排序，限制条数。查询词按 `channels/episodes.py` 的确定性投影拆成多个 term，逐个调用 `search_episodes` 后按 id 去重，避免 `"bun build"` 匹配不到 `"bun run build"`。`query` 为空时不做 LIKE，直接按 `ended_at DESC` 列出最近经验，供 Dream 复盘使用。

具体做法：给 `search_episodes` 增加可选参数 `source: str | None = None`、`outcome: str | None = None`，SQL 追加 `AND source = ?` / `AND outcome = ?`；现有 episodes 通道调用不传这两个参数，行为不变。

- [ ] **Step 3: 测试与提交**

```bash
pytest tests/memory/test_experience.py -v
git add nanobot/memory/experience.py nanobot/memory/repository.py tests/memory/test_experience.py
git commit -m "feat(memory): 新增经验记录与检索"
```

### Task 2.3：经验工具

**Files:**
- Create: `nanobot/agent/tools/experience.py`
- Modify: `nanobot/agent/tools/memory_search.py`
- Test: `tests/agent/test_experience_tools.py`

- [ ] **Step 1: 失败测试**

```python
def _make_tool(tmp_path):
    db = MemoryDatabase(tmp_path, db_path=tmp_path / "state.db")
    return ExperienceRecordTool(
        db=db, workspace_id="default", session_id="s1"
    ), db


async def test_experience_record_writes_episode(tmp_path):
    tool, db = _make_tool(tmp_path)
    result = await tool.execute(
        title="bun 构建失败", situation="bun run build 报模块缺失",
        action="直接重跑", outcome="failed",
        root_cause="依赖未安装", solution="bun install 后重跑",
        verification="bun run build 通过", tags=["bun"],
    )
    assert "已记录" in result
    rows = list_experiences(db, workspace_ids=["default"], query="bun")
    assert rows
```

- [ ] **Step 2: 实现工具**

`experience_record` 参数：

```python
{
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "situation": {"type": "string"},
        "action": {"type": "string"},
        "outcome": {"type": "string", "enum": ["failed", "partial", "completed"]},
        "root_cause": {"type": "string"},
        "solution": {"type": "string"},
        "verification": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "situation", "outcome"],
}
```

`memory_search` 保留工具名以兼容提示词，但描述改为：

```text
从经验库检索过去做过的任务、失败、根因与解法。
长期规则和项目约定不在经验库，直接读 MEMORY.md。
```

并增加可选 `outcome` 过滤。实现上：传 `outcome` 时通过 `ctx.attributes["memory_services"]` 取 DB，直接走 `list_experiences(..., outcome=...)`；拿不到 services 或未传 outcome 时继续走现有 retrieval engine（保持语义召回与自动注入一致）。

`enabled()` 门控：`experience_record` 沿用 `memory_write_enabled`；`memory_search` 沿用现有 `retrieval_engine` 判断。

- [ ] **Step 3: 运行测试**

```bash
pytest tests/agent/test_experience_tools.py tests/memory/test_experience.py -v
```

- [ ] **Step 4: 提交**

```bash
git add nanobot/agent/tools/experience.py nanobot/agent/tools/memory_search.py tests/agent/test_experience_tools.py
git commit -m "feat(memory): 新增经验库读写工具"
```

### Task 2.4：经验不过期、不参与衰减

**Files:**
- Modify: `nanobot/memory/decay.py`（仅在发现 episodes 被处理时）
- Test: `tests/memory/test_experience.py`

- [ ] **Step 1: 写回归测试**

```python
def _db_with_experience(tmp_path):
    db = MemoryDatabase(tmp_path, db_path=tmp_path / "state.db")
    record_experience(
        db, workspace_id="default", session_id="s1",
        title="bun 构建失败", situation="bun run build 报模块缺失",
        action="直接重跑", outcome="failed",
    )
    return db


def test_consolidation_does_not_touch_experiences(tmp_path):
    db = _db_with_experience(tmp_path)
    run_consolidation(db, workspace_id="default")
    with db.connect() as conn:
        row = conn.execute("SELECT COUNT(*) FROM episodes WHERE source='experience'").fetchone()
    assert row[0] == 1
```

- [ ] **Step 2: 验证并修正**

当前 `_decay_pass` 只处理 `memories` 表；如果测试发现 episodes 被删除，在 `decay.py` 增加 `source != 'experience'` 条件。

- [ ] **Step 3: 提交**

```bash
git add nanobot/memory/decay.py tests/memory/test_experience.py
git commit -m "test(memory): 经验记录不参与自动衰减"
```

---

## 6. Phase 3：主动沉淀引导

**目标：** 任务结束（失败/修复/用户纠正/可复用方法）时，AI 主动问一次是否沉淀，并正确分流到经验库或长期记忆。这套提示词只在 `memory_file_mode=true` 时启用；非文件模式保持旧规范。

**Files:**
- Modify: `nanobot/templates/AGENT.md`
- Modify: `nanobot/templates/agent/identity.md`
- Test: 手工场景验证 + `tests/agent/test_experience_tools.py` 覆盖工具调用

- [ ] **Step 1: 提示词加入策略**

```markdown
## 沉淀询问

以下情况在**交付结果之后**问一次，不要打断主任务：
- 本次出现失败或重试；
- 用户纠正了你的做法；
- 解决了非显而易见的问题；
- 发现了可复用的方法。

问法三选一：
「这次的失败和解决方式，要不要沉淀到经验库？也可以记成以后都遵守的规则，或者不用。」

- 选“经验” → `experience_record`
- 选“规则” → `memory_write`
- 选“不用” → 不再追问同一话题
- 一次任务最多问一次；简单问答不要问。
```

- [ ] **Step 2: 手工验证三个场景**

1. 构建失败 → 修复成功 → 询问 → 选经验 → 下次 `memory_search` 命中。
2. 普通问答 → 不询问。
3. 询问后选“不用” → 同一会话不再问。

验证记录写入 `.ai-runtime-artifacts/verifications/2026-11-06-experience-prompt-verification.md`。

- [ ] **Step 3: 提交**

```bash
git add nanobot/templates/AGENT.md nanobot/templates/agent/identity.md
git commit -m "docs(memory): 增加任务经验主动沉淀规则"
```

---

## 7. Phase 4：Dream 职责调整

**目标：** Dream 负责长期记忆整理 + 经验复盘；所有删除/升级动作只提议，不自动执行。

### Task 4.1：Dream 提示词

**Files:**
- Modify: `nanobot/templates/agent/dream.md`
- Test: `tests/agent/test_dream.py`

- [ ] **Step 1: 重写职责段**

```markdown
你是记忆管理员 + 经验复盘员。

长期记忆（MEMORY.md）：
- 合并重复小节、修正错误指针、把超长内容拆到同目录独立文件；合并/拆分必须保留原文或可回溯。
- 删除用户规则、删除项目约定必须列为“待确认”，不能直接删。
- 保持全局/项目归属正确，不把同一条内容复制到两边。

经验库（SQLite）：
- 只读检索，不写经验。
- 找出重复出现的失败模式，提出“合并为一条经验/提炼为规则”的建议。
- 经验不按时间删除，只建议标记冷数据（`metadata.cold=true`），不删行。
```

- [ ] **Step 2: 工具集**

`nanobot/agent/memory.py::build_dream_tools`：

- 保留文件读写工具；
- 增加只读 `experience_search`（直接读 DB，不依赖 retrieval engine；统计由 Dream 在同一次结果上聚合）；
- 不注册 `experience_record`、`memory_write`、`memory_delete`。

- [ ] **Step 3: 测试**

```bash
pytest tests/agent/test_dream.py -v
```

Expected: Dream 工具列表不含写入工具；提示词包含“待确认”。

- [ ] **Step 4: 提交**

```bash
git add nanobot/templates/agent/dream.md nanobot/agent/memory.py tests/agent/test_dream.py
git commit -m "feat(memory): Dream 增加经验复盘并改为提议制"
```

### Task 4.2：复盘结果落点

**Files:**
- Modify: `nanobot/agent/memory.py`
- Modify: `nanobot/cli/gateway_runtime.py`
- Modify: `nanobot/memory/database.py`

- [ ] **Step 1: 给 `memory_dream_runs` 补 metadata 列**

当前表没有 `metadata` 列，先加列（新库建表语句 + 旧库 ALTER 都要）：

```python
# _SCHEMA_STATEMENTS 的 memory_dream_runs 表定义增加
metadata TEXT NOT NULL DEFAULT '{}',

# init_schema 中补列
dream_columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_dream_runs)")}
if "metadata" not in dream_columns:
    conn.execute(
        "ALTER TABLE memory_dream_runs ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"
    )
```

Dream 的复盘建议写入 `memory_dream_runs.metadata`（JSON），字段：

```json
{
  "experience_review": {
    "repeated_patterns": [{"key": "bun-build-missing-deps", "count": 3, "suggestion": "bun install 后再构建"}],
    "promote_candidates": [{"experience_ids": ["exp-1", "exp-2"], "proposed_section": "构建与运行"}]
  }
}
```

不做独立 review 表；WebUI 在 Phase 5 读取该字段展示。

写入方式：Dream 结束时执行 `UPDATE memory_dream_runs SET metadata = ? WHERE run_id = ?`；读取端按 JSON 解析，字段缺失时返回空列表。

- [ ] **Step 2: 测试与提交**

```bash
pytest tests/agent/test_dream.py -v
git add nanobot/agent/memory.py nanobot/cli/gateway_runtime.py nanobot/memory/database.py
git commit -m "feat(memory): 记录 Dream 经验复盘建议"
```

---

## 8. Phase 5：WebUI 分栏

**目标：** 设置页“记忆”拆成“长期记忆”和“经验库”。

**Files:**
- Modify: `nanobot/webui/memory_api.py`
- Modify: `nanobot/webui/settings_routes.py`
- Modify: `webui/src/components/settings/memory/MemorySection.tsx`
- Modify: `webui/src/components/settings/memory/MemoryListView.tsx`
- Create: `webui/src/components/settings/memory/ExperienceListView.tsx`
- Test: `webui/src/components/settings/memory/*.test.tsx`（如已有测试则扩展）

- [ ] **Step 1: API**

按现有 `/api/settings/memory/...` 路由约定新增（并在 `settings_routes.py` 注册）：

```text
GET  /api/settings/memory/docs?scope=global|project   → { scope, content, chars, limit }
POST /api/settings/memory/docs/save                    → 保存正文；超限 400
GET  /api/settings/memory/experiences?workspace=&outcome=&q= → 列表
```

`POST .../docs/save` 需要携带 `expected_updated_at` 或内容哈希，冲突返回 409，避免覆盖并发编辑。保存直接调用 `MemoryFileStore.write_memory_doc`，**不要**走 `MemoryLifecycle.write_memory_md`（后者是 SQLite 投影路径，文件模式下会写错来源）。

- [ ] **Step 2: 前端**

- “长期记忆”页签：Markdown 文本框 + 字数/上限提示；保存失败显示“超限，请拆分到同目录独立文件”。
- “经验库”页签：列表（标题、结果、时间、工作区）、按失败/成功筛选、搜索框、详情展开。
- 顶部计数分开：长期记忆条数（按小节/文件）、经验条数。

- [ ] **Step 3: 验证**

```bash
cd webui && bun run test
```

Expected: 测试通过；`bun run build` 由用户环境验证，未经同意不执行。

- [ ] **Step 4: 提交**

```bash
git add nanobot/webui/memory_api.py nanobot/webui/settings_routes.py webui/src/components/settings/memory
git commit -m "feat(webui): 记忆页拆分为长期记忆与经验库"
```

---

## 9. Phase 6：迁移、清理与文档

**目标：** 把旧 `memories` 中的长期事实迁到 `MEMORY.md`，保留审计；停止双写。

### Task 6.1：只读迁移预演

**Files:**
- Create: `nanobot/memory/migrate_memories.py`（可复用 Phase 0 逻辑，提供 `main()` 与 `if __name__ == "__main__"`）
- Test: `tests/memory/test_memory_migration.py`

- [ ] **Step 1: 分类规则**

| 条件 | 去向 |
| --- | --- |
| `type in (preference, rule, fact)` 且 `priority=long_term` | MEMORY.md 候选 |
| `type in (experience, error)` | 经验库候选（转 episode 或保留归档） |
| `type=skill` | 不迁入 MEMORY.md；报告后由人工决定进 `skills/` 或经验库 |
| `expires_at` 已过期 / `priority=short_term` | 不迁移，标记归档 |
| 无法判断 | 写入报告，人工确认 |

预演还要输出：每个候选 Workspace 的 MEMORY.md 预计字符数；超过 3k/6k 上限的条目拆到同目录独立文件并留指针，不强行塞入正文。

- [ ] **Step 2: 预演**

```bash
python -m nanobot.memory.migrate_memories --dry-run --workspace default
```

输出：将写入的 MEMORY.md 文本、将归档的行数、无法判断的条目。**不写库、不写文件。**

### Task 6.2：执行迁移

- [ ] **Step 1: 用户确认后执行**

```bash
python -m nanobot.memory.migrate_memories --apply --workspace default
```

- 写入 MEMORY.md（按类型/主题分小节；超上限拆同目录独立文件并留指针）；
- 旧行设置 `expires_at='1970-01-01T00:00:00Z'`（过去时间，确保立即失效），`metadata.migrated_to_file` 指向文件与小节；
- 不物理删除旧行，保留回滚与审计。

- [ ] **Step 2: 验证注入不再出现旧 SQLite 核心事实**

```bash
pytest tests/agent/test_context_builder.py tests/memory/test_memory_doc.py -v
```

- [ ] **Step 3: 清理向量索引**

迁移后对已归档的 `memories` 行调用 `remove_memory_best_effort`，或对 workspace 执行一次 `reindex_vector`；确保语义通道不再召回旧长期事实。经验走 episodes，不依赖 `memories` 向量。

### Task 6.3：完成经验/错误路由

**Files:**
- Modify: `nanobot/memory/extractor.py`
- Modify: `nanobot/memory/writer.py`
- Modify: `nanobot/memory/extract_writer.py`

- [ ] **Step 1: 路由写入**

Phase 1 已停止长期事实写 `memories`；这里补齐 experience/error 分支：

- `preference` / `rule` / `fact` → `MemoryFileStore.append_memory_bullet`（追加到对应小节）
- `experience` / `error` → `record_experience`；缺少根因/解法时，`situation` 取 `content`，其余字段留空
- 不再同时写 `memories` 和文件。

- [ ] **Step 2: 测试**

```bash
pytest tests/memory/ tests/agent/test_memory_admin_tools.py -v
```

Expected: 抽取后同一内容只出现在一个存储。

### Task 6.4：文档与配置

- [ ] 更新 `docs/memory.md`：长期记忆与经验库两条链路、写入路由、Dream 职责。
- [ ] `memory_file_mode` 保留为回滚开关；在文档标注“关闭时回到旧 SQLite 路径，不推荐长期使用”。
- [ ] 提交：

```bash
git add docs/memory.md nanobot/memory/extractor.py nanobot/memory/writer.py nanobot/memory/extract_writer.py
git commit -m "refactor(memory): 长期记忆与经验按类型分流"
```

---

## 10. 验收标准

1. `MEMORY.md` 正文每轮注入；提示词不再要求维护索引；无索引行。
2. 超过上限的写入被拒绝，并提示拆分到同目录独立文件。
3. 用户说“记住…”后，下一次会话能看到该规则。
4. 一次失败任务记录后，下一次 `memory_search` 能召回，且不写入 `MEMORY.md`。
5. 经验记录不因时间/低重要性自动删除或归档。
6. Dream 不自动删除用户规则，不自动把经验升级为规则；只产出提议。
7. 全局与项目记忆、经验按 `workspace_id` 隔离。
8. 同一条内容只出现在一个存储；抽取后不再双写。
9. 迁移后旧 `memories` 行标记归档且带指针，可回滚。
10. Phase 0 报告记录每轮注入字符/token 估算，且不超过约定预算。

---

## 11. 风险与待确认

| # | 风险/待确认 | 处理 |
| --- | --- | --- |
| R1 | MEMORY.md 正文每轮注入成本 | Phase 0 实测；上限 3k+6k；支持 prompt caching 时成本更低 |
| R2 | 旧 `memories` 分类不确定 | Phase 0 抽样；不确定条目进报告，不自动迁移 |
| R3 | `episodes` 承载经验后与会话摘要混在一起 | 用 `source='experience'` 与 UI 过滤；量大再拆表 |
| R4 | 模型仍把经验写进 MEMORY.md | 提示词口诀 + 工具描述 + Dream 检测；必要时加 `experience_record` 必填字段约束 |
| R5 | 主动询问变成骚扰 | 只问一次、先交付再问、拒绝即停；必要时 Phase 3 增加抑制表 |
| R6 | `memory_search` 名称仍带“记忆” | 首版保留名称，描述改为经验检索；后续可改名 `experience_search` |
| R7 | USER.md 与全局 MEMORY.md 内容重复 | 由 Phase 1 Task 1.6 处理：USER.md 只放画像，MEMORY.md 只放规则/约定 |
| R8 | Dream 复盘没有展示入口 | Phase 4 先写 `memory_dream_runs.metadata`，Phase 5 WebUI 展示 |

### 待用户确认

1. `memory_search` 是否改名 `experience_search`（首版保留名称）。
2. USER.md 是否继续保留独立画像，还是并入全局 MEMORY.md（默认保留独立，走 Task 1.6 Step 2；选并入则走 Step 3）。
3. Dream 复盘结果是否必须有 WebUI 入口才发布，还是先写审计字段。
4. 旧 `memories` 迁移时，`experience/error` 类型条目是转经验库还是直接归档。

---

## 12. 执行顺序与停止点

```text
Phase 0 体检 → 用户确认 → Phase 1 长期记忆正文 → Phase 2 经验库
→ Phase 3 主动询问 → Phase 4 Dream → Phase 5 WebUI → Phase 6 迁移清理
```

- Phase 0 停止条件触发时暂停；
- Phase 1 完成后先做一次真实对话验证（写入 + 下一轮注入）；
- **发布门禁：Phase 1 不能单独发布。** 必须与 Phase 5 的 MEMORY.md 编辑入口一起上线，或临时关闭 WebUI 旧长期记忆的 create/update/delete，否则 WebUI 仍会写 SQLite，形成两个编辑入口；
- Phase 2 完成后先做一次“失败 → 记录 → 检索命中”验证；
- Phase 6 的 `--apply` 必须单独获得用户确认，不做自动迁移。
