# 经验库类型扩展 + Episodes 向量召回：实施验证

- 日期：2026-11-06
- 计划：`.ai-runtime-artifacts/plans/2026-11-06-experience-kinds-vector-retrieval-plan.md`
- 分支：`feature/memory-experience-mvp`

## 已实现

### 1. 五类沉淀（不新增表）

- 类型只写入 `episodes.metadata.kind`：`experience` / `skill` / `pitfall` / `decision` / `reference`。
- `metadata.status`：`active` / `deprecated` / `superseded`；旧行缺省按 `experience` / `active`。
- `record_experience` 按 kind 校验必填项并组装 summary：
  - experience：situation + 根因 + 解法
  - skill：capability + 前置 + steps + 验证
  - pitfall：symptom + 根因 + fix + 验证
  - decision：背景 + 选项 + choice + 理由
  - reference：用途 + pointer
- `experience_record` 工具新增 `kind` 与类型专有参数；`experience_query` 支持 kind/status。
- `episodes.metadata` 表达式索引：`idx_episodes_kind`（放在 ALTER 补列之后，兼容老库）。

### 2. 检索过滤与标签

- `list_experiences` / `repository.search_episodes` / `get_episodes_by_ids` 支持 kind/status/outcome。
- `RetrievalEngine.retrieve / search_hits / retrieve_with_ids` 新增过滤参数；带过滤时只走 episodes 通道。
- `memory_search` 收敛掉独立 outcome 分支，统一走引擎；结果与注入块带 `[踩坑]` / `[技能·已弃用]` 标签。
- WebUI 经验库新增类型/状态筛选与按 kind 渲染的详情（步骤、症状/修复、结论/理由、指针）。

### 3. Episodes 向量

- `VectorStore` 新增 `episodes` 集合（与 `memories` 共用 client/模型/index path）：
  `upsert_episode` / `remove_episode` / `search_episodes` / `list_episode_ids` / `delete_episode_ids`。
- `EpisodeVectorIndexer`：写入 best-effort、删除同步、启动回填（集合 id 与 SQLite 差集，不落新表）。
- episodes 通道：词法 ∪ 向量 → RRF → 回表按 workspace/kind/status/outcome 过滤 → 去重。
- 向量不可用/模型未装/ChromaDB 异常时自动退化为纯词法。

### 4. 踩坑主动提示（默认关闭）

- 配置 `agents.defaults.memoryPitfallHint`（默认 `false`）。
- 工具报错时检索 `kind=pitfall,status=active` top-1，向量优先、LIKE 兜底，低分不提示。
- 提示以“经验库提示 · 仅供参考”追加到对应 tool message；每轮最多 2 条、同错误只提示一次。

### 5. Dream 与提示词

- Dream 复盘按 kind：同类型合并、经验升级技能、踩坑升级规则（待确认）、技能弃用、决策 supersede。
- `AGENT.md` / `identity.md` / `dream.md` 增加五类分流与主动沉淀边界；identity 仍在 4000 字符限制内。

### 6. 文档

- `docs/memory.md`：五类字段、写入分工、过滤语义、episodes 向量集合与降级、开关。
- `AGENTS.md`：Experience Library 段落更新。

## 验证结果

| 范围 | 结果 |
| --- | --- |
| 后端（memory + webui memory api + Dream + identity + memory_search tool） | **1200 passed** |
| 新增/更新测试 | `test_memory_experience_kinds.py`、`test_episode_indexer.py`、`test_episode_vector_recall.py`、`test_pitfall_hint.py`、engine/formatter/UI 用例 |
| WebUI 类型检查 | `tsc -p tsconfig.build.json --noEmit` 通过 |
| WebUI 组件测试 | 2 个文件 / **15 passed** |
| ruff | 所有本次改动文件通过 |

### 已知的非本次失败（基线问题）

- `tests/memory/vector/test_model_hub.py::test_sync_endpoint_writes_both_env_and_constants`：
  测试用 `sys.modules` 伪造 `huggingface_hub.constants`，当前依赖版本下 `from ... import` 不读该替身；`model_hub.py` 未被本次修改。
- `tests/agent/test_session_atomic.py` / `tests/webui/test_session_list_index.py` 的 preview 预算用例：
  provider_state 预览返回空串，与记忆链路无关。
- `tests/agent/test_turn_agent_profile.py` 断言默认身份里的“知序指挥官”不应出现，属该用例与当前模板的基线差异。

## 真实向量 + 前端联测（2026-11-06 19:00）

环境：隔离 HOME/workspace，`memorySearchBackend=chromadb`，`memoryPitfallHint=true`，
本地模型 `bge-small-zh-v1.5`（512 维），`chromadb 1.5.9` / `sentence-transformers 6.2.0`。

- 启动回填：gateway 日志 `vector store ready`，`episode vector startup sync: indexed=6`；
  ChromaDB `episodes` 集合 `count=6`，`memories` 集合 `count=0`。
- 语义召回（直接查 `episodes` 集合）：
  查询“前端服务起不来，提示地址已被占用”命中
  `症状：Vite 启动报 EADDRINUSE 5173`（score 0.579），
  说明换一种说法也能召回同一条踩坑。
- 浏览器（Playwright，视觉验证）：
  - 经验库同时显示五类徽标，按类型筛选“踩坑/技能”只返回对应条目；
  - 技能卡片展示 capability + 编号步骤，决策卡片展示结论/理由/选项，参考卡片展示用途/指针；
  - 搜索“端口”命中“Vite 端口被占用”；
  - 长期记忆“项目”下拉列出 `workspace` 与 `beta 项目`，切换后正确加载 beta 的 `MEMORY.md`。
- 对话链路：MiniMax 在“端口被占用怎么处理”问题中调用 `memory_search(kind=pitfall)`，
  回答引用了经验库中的 taskkill / 5174 方案。

截图目录：`.ai-runtime-artifacts/verifications/memory-ui/`

| 截图 | 内容 |
| --- | --- |
| `10-experience-kinds-all.png` | 五类经验库总览 |
| `11-experience-pitfall-filter.png` | 按“踩坑”筛选 |
| `12-experience-skill.png` | 技能卡片（步骤） |
| `15-experience-search-port.png` | 搜索“端口”命中 |
| `13-longterm-project-dropdown.png` | 长期记忆项目下拉 |
| `14-longterm-beta-project.png` | 切换到 beta 项目后的 MEMORY.md |
| `16-vector-chat-recall.png` | MiniMax 通过 memory_search 召回踩坑 |

## 未覆盖

- `memoryPitfallHint` 的真实工具报错触发链路尚未在浏览器里制造一次工具失败来验证；
  hook 与检索已有单测，建议后续用一个必然失败的命令复测。
- `vector-sync` 端点目前同时同步 memories + episodes，尚未提供独立 `target=episodes` 参数。

## 回滚

- `memorySearchBackend=fts5`：关闭向量，episodes 回到纯词法。
- `memoryPitfallHint=false`：关闭主动提示。
- 忽略 `metadata.kind`：读取逻辑等价于全部 experience；无需数据迁移。
- 删除 ChromaDB `episodes` 集合：下次启动按 SQLite 重建。

## Next

1. 安装 `chromadb`、`sentence-transformers` 后跑真实向量回填与检索。
2. 用 MiniMax 复测“踩坑换措辞召回”“技能升级”“决策 supersede”三条场景。
3. 视使用情况再决定是否把 `vector-sync` 拆出 `target=episodes` 的独立入口。
