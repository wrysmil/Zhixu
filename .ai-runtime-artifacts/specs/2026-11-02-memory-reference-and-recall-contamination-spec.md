---
artifact: spec
route: systematic-debugging -> brainstorming
skills: [systematic-debugging, source-driven-development, brainstorming, verification-before-completion]
skills_evidence:
  - .claude/skills/systematic-debugging/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/brainstorming/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - 用户提供的桌面端日志（2026-11-02 21:47—22:22）
  - 用户确认：实际返回/注入的条目 +1（推荐）
  - tests/memory/test_citation_loop.py
  - tests/memory/test_writer.py
  - nanobot/memory/extractor.py
created_at: 2026-11-02
status: approved
approved: true
approval: "用户：按此直接实现，spec/plan无需再次确认（推荐）；直接执行快点"
---

# 引用计数与回忆污染修复

## 已确认问题

1. D11 删除了模型 usefulness 评分到 access_count 的写回，但界面仍显示引用次数；当前无实际访问统计写入。
2. 22:19 的 memory_search 仅命中 episode bb2dd802，不是猫的事实 77b531d5。用户已确认只增加实际返回/注入条目的次数，不级联增加关联事实。
3. 22:21 属于新会话的新批次，不是同批重跑。semantic prompt 包含助手复述和 memory_search 输出；画像候选缺少用户原文证据校验。
4. writer 完全相同内容与属性才幂等；“养/养有”会新增，“无糖咖啡/咖啡不加糖”在同属性下会产生 uncertain 冲突。该现象已在内存 SQLite 复现。

## 推荐方案

### 实际访问统计

- 工具检索只统计最终返回的条目；自动注入只统计最终预算裁剪后进入上下文的条目。
- 每个输出/注入操作按 (kind, record_id) 去重；事实更新 memories，情节更新 episodes。
- 搜索候选、预算丢弃、评测 search_hits、管理页面浏览不计数。
- 使用原子 SQL 自增；工作区归属必须与条目相符；统计失败留 warning，不中断回答。
- 保留 D11：不恢复模型 usefulness 评分，不让访问次数参与 RRF 排序。

### 阻止回忆再次成为新画像

- 画像与任务经验/情节分别处理证据：画像必须有当前批次用户原文中的新事实、明确确认或纠正作为证据。
- 画像抽取输出应携带用户原文证据，程序验证其确实来自当前批次的 user 消息；缺失或伪造证据的画像不入库。
- 检索工具输出、助手复述与工作状态仅作背景，不能单独构成新画像事实。
- “还记得我养的猫吗”不能支持重新记录杭州、职业、咖啡和猫名字；真实的“咪咪现在两岁了”仍可提取。
- 保留任务经验和情节所需的助手/工具步骤，不全局删除对话上下文。
- 此次先修证据来源，保留 D08 对真实不同值的保守冲突策略；不引入字符相似度或否定词终判。

## 备选方案与取舍

1. 推荐：确定性访问统计 + 画像证据校验。计数直接可解释，源头阻断本次重复污染。
2. 恢复旧 usefulness 评分：增加模型调用成本，仍无法覆盖 episode，并违背 D11，拒绝。
3. 对同义内容做相似度自动合并：存在不同时间/条件事实误合并风险，且不能阻断助手虚构画像，暂不采用。

## 实施边界

- 不修改用户的真实记忆数据库，不自动删除或合并现有重复记录。
- 保留当前未提交改动；如使用隔离 worktree，必须携带涉及文件的现有改动，回迁仅应用本任务增量。
- 无新增依赖，不修改模型配置，不调用付费模型。
- 本次不能承诺彻底解决用户再次陈述同义事实的一般语义去重；必须明确报告这一边界。

## 验收

1. 工具返回 memory/episode 后各自次数 +1；未返回项不变。
2. 自动注入仅对最终 included 条目 +1；核心和召回重叠按 kind/id 只计一次。
3. episode 命中不增加其全部 linked_memory_ids 的次数。
4. 注入/工具输出失败、工作区不匹配、不可信 ID、统计数据库异常均有边界验证。
5. 原有四条事实 + 新会话回忆提问 + 工具旧记忆 + 助手复述，idle 后仍为四条事实且无新 conflict。
6. 新用户事实、明确纠正、经验、情节正常入库；伪造/缺失的用户原文证据不能写画像。
7. 保留 D11 与 RRF 测试；先观察新增回归测试失败，再实施修复，运行 memory/agent 相关测试、ruff 和类型检查。

## 已运行验证

`.venv/bin/python -m pytest tests/memory/test_citation_loop.py tests/memory/test_writer.py tests/memory/test_profile_merge_persistence.py tests/memory/retrieval/test_reranker.py -q`

结果：37 passed in 0.63s。该结果证明当前行为及守护边界，不代表修复已经完成。

## 自检

计数口径已由用户确认；实现范围明确；没有依赖旧 usefulness 评分或排序权重；没有将 episode 关联等同于事实实际引用。尚待实施批准，业务代码未修改。

## Next

- 确认本方案并直接实现，可同时明确 spec/plan 无需再次确认；随后补实施计划、拆分任务、测试并修复。
- 需要调整时指出统计或证据边界的修改意见。
