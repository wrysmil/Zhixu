---
artifact: implementation-plan
route: writing-plans
skills:
  - writing-plans (skipped: 未安装；按 plan.harness-overlay.md 的 FM 与结构执行)
skills_evidence:
  - harness-kit/artifact-templates/plan.harness-overlay.md
source:
  - .ai-runtime-artifacts/research/2026-11-04-working-memory-and-goal-anchoring.md §9.6–9.7
  - 用户原话：「工具确实膨胀，那首轮应该注入那些工具呢」「可以的写一个计划吧」
dispatch: n/a
created_at: 2026-11-05
status: draft
approved: false
---

# 工具上下文瘦身实施计划

## Goal

把每轮随请求发送的工具 schema 从 **28,473 字符（≈9.5k tokens）** 压到 **≈2.5–3k tokens**，且不降低可用性：模型仍能发现并使用全部 31 个工具。

三条验收线：

1. 常驻工具（core）schema 总量 ≤ 6,000 字符（≈2k tokens）。
2. 任意会话首次请求的 tools 总量 ≤ 9,000 字符（≈3k tokens）。
3. 工具仍可被模型主动发现：`tool_search` 能在同一轮内激活未常驻工具，并在后续轮次保持。

## 现状基线（实测）

`ToolLoader().discover()` 共 31 个工具类，`{name, description, parameters}` 合计 28,473 字符。单件最贵：`message` 1,947、`cron` 1,759、`grep` 1,614、`exec` 1,544。

按域分包体积见 research §9.7。关键数字：

| 包 | 工具 | 字符 | ≈tokens |
| --- | --- | --- | --- |
| core 六件 | read_file / list_dir / find_files / exec / ask_question / memory_search | 5,815 | 1,938 |
| edit 三件 | apply_patch / edit_file / write_file | 3,184 | 1,061 |
| grep | grep | 1,614 | 538 |
| 其余 18 件 | — | 17,860 | 5,954 |

## Architecture

三层结构，从「全量静态」改为「常驻 + 目录 + 按需激活」：

```
ToolRegistry（全量，装配期构建，不变）
   ├── CORE_TOOLS      常驻，永远进入每轮请求
   ├── TOOL_PACKS      按域分包（edit/grep/web/memory/plan/sessions/cron/subagent/misc）
   └── tool_search     元工具：按 query 或 pack 查找并激活

每轮：effective_tools = registry.subset(CORE | session.metadata["tool_packs"])
每次 LLM 请求：runner 重新 get_definitions() → 本轮新激活的工具立即生效（runner.py:877）
```

**为什么不做意图分类**：判错一次即整轮缺工具；research §9.6 已确认业界主流是 tool search / handoff。本计划只允许「确定性信号预载」（含 URL → 预载 web 包），且只用于预载、不用于排除。

**关键约束（必须在实现中守住）**：

1. `runtime_context_provider` 必须从**全量 registry** 取，不随子集收缩——否则 `create_goal` 不在子集时，Goal Runtime Context 会整块消失（`loop.py:1673` 传的是 `effective_tools`，需改）。
2. 工具顺序稳定、只追加不删除（Manus：mask, don't remove），否则击穿 provider 前缀缓存。
3. 每会话最多 1–2 次激活；激活后不卸载。
4. `enabled(ctx)` 门控与现有 config 装配不变，子集只能在此基础上再收缩。

## 接口契约

```python
# nanobot/agent/tools/catalog.py（新增）
CORE_TOOLS: tuple[str, ...]              # 六件，顺序即注入顺序
TOOL_PACKS: dict[str, tuple[str, ...]]   # pack -> tool names
PACK_SUMMARIES: dict[str, str]           # pack -> 一行说明（进目录）

# ToolRegistry 新增
def subset(self, names: Iterable[str]) -> "ToolRegistry": ...
    """按给定顺序返回新 registry，共享 Tool 实例；缺失名忽略并 warning。"""
def summaries(self) -> list[dict[str, str]]: ...
    """[{"name","pack","summary"}]，供 tool_search 与目录渲染。"""

# tool_search 工具（nanobot/agent/tools/tool_search.py，新增）
参数: query: str = ""      # 关键词；与 pack 二选一
      pack: str | None     # 直接激活整包
      limit: int = 5       # query 命中上限
返回: JSON {"activated": [...], "tools": [{"name","pack","summary"}]}
副作用: registry.register(...) 当前轮生效；session.metadata["tool_packs"] 持久化

# 会话元数据
session.metadata["tool_packs"] = ["edit", "web"]   # 与 goal_state 同级
```

**配置开关**：`tools.catalog_enabled`（默认 `true`），环境变量 `NANOBOT_TOOL_CATALOG=off` 可整体回退到全量注入。

## Task 1 / WU-01：描述瘦身（零架构，可独立上线）

文件：`nanobot/agent/tools/message.py`、`cron.py`、`search.py`（grep）、`generate_image.py`。

- [ ] 先量基线：脚本统计四个工具 schema 字符数，写进测试常量（`tests/agent/test_tool_schema_budget.py`）。
- [ ] 压缩 `message` / `cron` / `grep` / `generate_image` 的 description：保留「何时用/关键约束/返回形状」，删掉示例堆叠与重复说明；参数描述同步精简单句。
- [ ] 运行新增预算测试：四件合计 ≤ 3,000 字符（当前 5,725）。
- [ ] 跑 `tests/agent/` 下与这些工具相关的现有测试，确认无行为断言依赖长描述。
- [ ] Done：全量 schema ≤ 25,000 字符；无测试回归。

## Task 2 / WU-02：目录与子集（不含激活）

文件：`nanobot/agent/tools/catalog.py`（新增）、`registry.py`、`loop.py:1777`（effective_tools 计算处）、`tests/agent/test_tool_registry_subset.py`（新增）。

- [ ] 新增 `catalog.py`：定义 `CORE_TOOLS`、`TOOL_PACKS`、`PACK_SUMMARIES`，与实测工具名逐一对应；加测试断言每个名字都存在于 `ToolLoader().discover()`。
- [ ] `ToolRegistry.subset(names)`：按 names 顺序建新 registry，实例共享，缺失名 warning 不抛。
- [ ] `loop.py` 计算 `effective_tools` 时：`enabled` 关闭或 catalog 关闭 → 维持全量；否则 `self.tools.subset(CORE | packs_for(session))`。
- [ ] 修正 runtime context providers 取全量：`_resolve_runtime_context_for_request` 的 `tools` 参数改传 `self.tools`，schema 仍用 `effective_tools`。
- [ ] 测试：subset 顺序稳定；缺失名不影响；`create_goal` 不在 subset 时 goal block 仍出现。
- [ ] Done：默认配置下首轮 tools 总量 ≤ 9,000 字符；现有测试全绿。

## Task 3 / WU-03：`tool_search` 与会话级激活

文件：`nanobot/agent/tools/tool_search.py`（新增）、`registry.py`、`loop.py`（会话元数据读写）、`tests/agent/test_tool_search.py`（新增）。

- [ ] 实现 `ToolSearchTool`：`query` 走「名字 + 一行摘要」的关键词打分（大小写不敏感、支持中文同义词表）；`pack` 直接整包激活；返回前先 `register` 到当前 registry，再写 `session.metadata["tool_packs"]`。
- [ ] `loop.py` 读取 `tool_packs` 构建子集；会话恢复/重启后从 metadata 重建（复用 session manager 现成持久化）。
- [ ] 激活上限：单会话 `tool_packs` 最多 2 个包（可配置），超出时返回提示而非静默失败。
- [ ] 未加载工具的调用错误提示：在 registry 的 unknown-tool 分支追加「可用 tool_search 查找」。
- [ ] 测试：激活后同一轮第二次请求 `get_definitions()` 已含新工具；下一轮仍在；超限提示；pack 名非法时 400 风格错误。
- [ ] Done：新增测试全绿；手动跑一条「读文件 → 搜索 → 编辑」链路，确认编辑工具在需要时出现。

## Task 4 / WU-04：观测、预算与回归

文件：`nanobot/agent/loop.py`（日志）、`tests/agent/test_tool_schema_budget.py`、`docs/` 无改动。

- [ ] 每轮 DEBUG 日志：`tools.count`、`tools.chars`、`tool_packs`，便于线上核对。
- [ ] 预算测试三条：core ≤ 6,000 字符；首轮 ≤ 9,000；两轮之间仅追加不重排（比较 names 序列）。
- [ ] 全量回归：`pytest tests/agent tests/memory tests/webui -q`、`ruff check nanobot/`、`basedpyright`（按项目现有严格口径）。
- [ ] 落盘 `verifications/*-verification.md` 与 execution-log；不提交、不推送。
- [ ] Done：三条预算断言通过，回归无新增失败。

## 测试矩阵

| 场景 | 断言 |
| --- | --- |
| 默认编码会话首轮 | tools 总量 ≤ 9,000 字符；含 core + edit + grep |
| IM 聊天会话首轮 | 仅 core + tool_search；无 edit |
| 调 `tool_search(pack="web")` | 下一请求含 web_search/web_fetch；metadata 记录 `web` |
| 会话重启 | 从 `tool_packs` 重建，工具仍在 |
| `catalog_enabled=false` | 与改造前完全一致（31 件全量） |
| create_goal 不在子集 | Goal Runtime Context 仍注入 |
| 重复激活 | 幂等，不重复 register，不改变顺序 |

## 风险与回滚

| 风险 | 处置 |
| --- | --- |
| 子集导致模型不知道某工具存在 | 目录常驻 + `tool_search` + unknown-tool 提示三处兜底 |
| 前缀缓存击穿 | 只追加不重排；每会话激活上限 2 包 |
| runtime context 丢失 | providers 固定取全量 registry（WU-02 已含） |
| 子代理能力被削弱 | `spawn` 仍从 `self.tools` 取全量；子代理不继承 packs |
| 线上异常 | `NANOBOT_TOOL_CATALOG=off` 一键回退，无需回滚代码 |

## 不在范围内

- 不做每轮意图分类（research §9.5–9.6 结论）。
- 不接 Anthropic 服务端 tool search（provider 不支持，且会与本地 catalog 重复）。
- 不改 WebUI 工具管理界面；如需展示「已加载/未加载」，另开计划。
- 不动 MCP 动态工具发现逻辑；MCP 工具整体归入 `mcp` 包（默认未激活）。

## Next

**（写入后暂停，等确认再实现）**

- 计划确认 → 说「开始实现」或「执行」
- 只想先拿收益 → 说「只做 WU-01」（描述瘦身，零架构、可独立上线）
- 需要调整 → 直接说修改意见（如换 core 清单、改激活上限、加 MCP 包策略）
