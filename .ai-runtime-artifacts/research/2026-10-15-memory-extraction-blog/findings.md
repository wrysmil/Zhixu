# 调研发现

源码快照：fccafd78429e6424ec1264d518e629b941c315d6，2026-10-15。

- 原有未跟踪文件为 agent-evaluation 任务产物，本次不改动。
- 当前主流水线位于 memory/extractor.py，hook 和 orchestrator 共同调度，必须以实际调用核对触发点，不能只看模块注释。
- ProfileExtractor 一次调用解析 memories 和 experiences；独立 ExperienceExtractor 的存在不代表主流水线必定调用它。
- 现有五期大纲将第二期界定为触发、过滤、候选结构、增量与失败处理。

## 当前接通的主路径

- loop.py:612-758 在开启 memory_extraction_enabled 时注册按轮 hook、删除观察者及 AutoCompact Quick Facts 回调。gateway_runtime.py:535 传 memory_extraction_enabled=True；AgentLoop 构造默认 False。用户侧 memory_enabled 总开关虽然存在，但 _wire_memory_extraction 内通过 locals().get 取 provider，当前该方法没有这个入参，不能据注释保证热开关端到端生效。
- memory_extraction.py:297、331、369：before_run 取消旧计时器；正常 after_run 更新焦点、装备计时器；cancelled/error 由 on_finally 补装备。Hook 默认 120 秒（:197）。同名 config 字段默认 120，但 loop 工厂装配未传 idle_seconds，不能写成配置已端到端接通。
- memory_extraction.py:451 的 _detect_topic_change 直接 return，旧模块文档关于 T5 调模型不可作为当前行为证据。
- 会话删除会调 extract_session(source='deletion')，且需要运行中的事件循环；删除聊天与删除提取出的记忆是不同动作。
- autocompact.py:119-132 在自动压缩成功后调 Quick Facts；仅扫描当前可得 user 文本的规则词，不能保证恢复已经被压缩掉的原文。
- 存在 SessionEndOrchestrator 和 /api/sessions/{session_key}/end 事件发布端点，但当前未找到将事件订阅到编排器的生产调用。on_finally 已明确不调编排器。不能声称关闭窗口必定完整提取。

## 四阶段流水线

- extractor.py:750 extract_session：_system_extract → _llm_extract → _load_existing_memories / _apply_filters → _persist。
- :353 工具节点按 tool_call_id 关联结果，脱敏与截断输入 512 字符、输出 2048 字符；success 仅依赖是否有结果、结果是否包含 error/traceback，为启发式判定。
- :1114 _system_extract 同时读取 user 规则词片段和 scratchpad 快照。:633 转录只渲染 user/assistant 文本，默认 8000 字符预算，超长保留头尾，中间有遗漏；附加工具摘要/规则/快照不在该转录预算中。
- :1134 _llm_extract 用 asyncio.gather(return_exceptions=True) 并发两个调用：ProfileExtractor 同次解析 memories / experiences / 可选 citation_scores；另一调用生成 episode。单路超时 30 秒，错误保存在 failed_tracks。
- :1314 候选过滤：任务请求和 AI 自答正则、type/priority 合法性、content|subject|predicate 精确哈希、同类型历史字符 3-gram Jaccard >0.8。每类型仅预载 500 条历史；相似度对比不是全库语义判断，不能可靠区分改写与事实纠正。
- ProfileExtractor 的 _item_from_dict → _profile_item_to_llm 直接构造候选，没有经过同文件的 _coerce_memory_item；不能宣称其中长度/数值防护覆盖主路径。默认提示词输出也未要求 subject/predicate，不能保证每条都有结构化归属。
- :1525 _persist 写 memories 和 episode，subject/entity 精确字符串匹配建立部分关联；有动作节点时允许保留空摘要 episode。SQLite 写失败写 fallback JSON，database.py:226 支持初始化/构造时重放。该机制用于持久化失败，不能恢复 LLM 未抽出的内容，也不是强事务批次完成或持久化模型任务队列的保证。

## 增量和失败边界

- :915 run_idle_extraction 取 state.last_count，缺失视为 0；current_count <= last_count 无操作。新增 <2 条、或只有一个 user 且 <10 字符无工具调用时跳过且不推进。此长度为字符，不是字节或 token。
- :813 extract_incremental 仅以 session.messages[last_extracted_index:] 构造新 Session 执行完整流水线，成功空结果与吞异常空结果表达能力不足。
- :999-1018 run_idle_extraction 不检查 failed_tracks、持久化结果便 upsert 到 current_count；LLM 全失败也可能推进。tests/memory/test_extractor_incremental.py:359 对该行为有明确断言。该测试文件中 test_failure_does_not_advance_state (:323) 实际模拟 state upsert 失败，不能据名字概括为模型失败不推进。
- 仅消息数组长度作为水位依赖稳定序列；消息前缀删除、重写或压缩需要重置/换代处理。当前未在生产调用处发现 reset_session_extraction_state 接线。
- 重试建议应分清成功无候选、部分成功、失败，并记录区间/分路进度和幂等键；这是文章中的设计建议，不冒充现有实现。

## 验证范围

本轮运行了 `tests/memory` 中 8 个相关测试文件，共 159 passed；未调用真实模型，未做提取质量或性能评测。新增的运行探针只验证源码语义，没有修改业务代码。

## 参考已发表文章调整结构

用户追加要求参考 https://juejin.cn/post/7696080841777020966 的结构。普通网页读取失败后，已通过 Codex 内置浏览器读到已发表全文，标题为《聊聊Agent的记忆系统如何设计》，作者 AI晓华。

原文结构为：前言 → 为什么 Agent 需要记忆 → 记忆的种类 → 记忆的作用 → 记忆系统的存储设计 → 答疑工作记忆。讲解顺序是具体开发场景、简短概念、作用示例、结构与代码、边界答疑；不能将本地草稿当作本次网页正文。

第二期据此调整为：前言 → 为什么需要记忆提取 → 记忆提取的时机 → 三类记忆分别怎么提取 → 记忆提取的流程设计 → 答疑（摘要与提取、模型失败与重试）。保留已核对的源码事实，减少调研报告式的逐项边界罗列，限制放回对应机制旁。当前轮不新增图片生成。

## 第二轮深挖：可进入正文的项目细节

### 1. 提取和召回已经形成反馈闭环

- AgentLoop 在构建本轮 Prompt 时调用 `_build_memory_section_with_ids`，得到注入块和真正进入 Prompt 的 memory id 列表。
- id 列表经 AgentTurnHookSpec 传给当轮 MemoryExtractionHook；空闲计时器触发提取时，再把 `cited_memories` 交给 ProfileExtractor。
- 同一路语义模型调用追加 citation scoring 段，要求逐条判断 `useful`；`_persist` 只接受本次注入集合内的 id，`useful=true` 才 `bump_access_count`。
- 检索重排的 access 分量是 `log1p(access_count)/5`，权重 0.20。“被用过且被判定有用”的记忆会影响后续排序。

### 2. 项目里有两套 idle

- 记忆提取 hook：默认 120 秒，按 `session_extraction_state.last_count` 做增量提取。
- 自动压缩：默认 15 分钟（`idleCompactAfterMinutes`），压缩会话上下文；压缩成功后才跑 Quick Facts。
- Quick Facts 只用正则扫描用户规则片段，候选类型固定为 RULE，复用过滤和落库，不调 LLM、不产情节。

### 3. 默认 Prompt 与合并契约存在落差

- 语义 Prompt 的输出示例不要求 subject/predicate；但合并、精确哈希和 episode↔memory 关联依赖这些字段。
- 单测 fake provider 主动返回 subject/predicate，掩盖了默认 Prompt 不一定提供它们的事实。
- 当前按同一主体同一属性更新旧记忆、按 entity 建立来源关联，更像可选能力。

### 4. 主路径解析与校验脆弱

- `_item_from_dict` 直接 `float(importance)`；非法字符串会让语义分路整体失败。
- `tags` 若错误返回字符串，`list("abc")` 会得到 `['a','b','c']`；运行探针已确认。
- extractor.py 中更完整的 `_coerce_memory_item` 没有被 ProfileExtractor 主路径复用。

### 5. 写入失败的事务语义与设计文档不一致

- `_persist` 用一个连接包住整批写入，单条失败时 `conn.rollback()`。
- 运行探针：第一条成功、第二条失败后，数据库最终 0 行，但返回结果仍含第一条 id；fallback 只有第二条。
- 设计文档要求“单条失败跳过该条，已写入 memory 不回滚”，当前实现相反。
- 向量 best-effort 索引发生在整批 commit 之前；后续回滚可能留下“向量有、SQLite 无”的暂态，启动对账可清理 stale id。

### 6. fallback 是写入补偿

- fallback 在 DB 写失败时保存候选；MemoryDatabase 和 MemoryExtractor 初始化都会触发 replay。
- 脱敏仅覆盖 `content/subject/predicate`；episode 的 `summary/goal` 不脱敏，运行探针已确认。
- 它不能补回模型失败、解析失败或水位已前移的内容。

### 7. 用户/工作区隔离只完成了一部分

- Scratchpad 从 `channel:chat_id` 派生 user_id，并以 (user_id, workspace_id) 为主键。
- memories 写入保存 user_id/workspace_id；但 `list_memories` 和 `find_memory_by_subject_predicate` 只按 workspace_id。
- 运行探针：同一 workspace 下两个用户的相同 subject/predicate，查找返回先写入的记录。
- FTS/语义检索接口也没有 user/workspace 参数；当前主要靠每个 workspace 独立 state.db 形成物理隔离。

### 8. 开关与配置没有完整透传

- 上下文构建会读取 `memory_enabled_provider`，关闭后可以不注入记忆。
- `_wire_memory_extraction` 用 `locals().get("memory_enabled_provider")` 取值，但该方法没有这个参数，extraction hook 实际拿不到热开关。
- `memory_idle_seconds` 有配置字段和单测，但 loop 未把 `idle_seconds` 传给 factory；生产阈值仍是类属性 120 秒。

### 9. 结束与删除路径

- 删除会话时先从缓存抓 transcript，文件删除后异步完整提取；缓存未命中则跳过，也不会删除已形成的 memories。
- SessionEndOrchestrator 的 episode 步骤是真实实现，但 user_profile/experience 当前是 TODO 占位。
- 设计原定语义+情节+scratchpad 三路并发，当前 idle 主路径是两路，scratchpad 深度格式化未接入。
