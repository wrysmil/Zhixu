# 进度

- 已读取写作技能及规范、项目入口、routing、现有系列大纲；开始按入口追踪源码。
- 初次读取完整长源码导致输出截断，改用按函数定位和分段读取。
- brainstorming 仅用于文章选题和结构推敲，本任务不涉及代码实现、产品方案或实施门禁。
- 使用 planning-with-files 管理调研，文件按项目 AGENTS.md 放入 .ai-runtime-artifacts/research/。
- 已静态追踪 loop → hook → extractor → filters/profile/persistence，并核对实际会话结束端点、自动压缩及对应单测断言。
- 事实审阅重点：T5 禁用、会话结束编排未发现订阅接线、idle 配置未透传、模型失败也推进水位、主路径未复用完整候选 coercion。
- 正文以可迁移的提取设计为主，不把调研记录和缺陷清单直接改写成博客；失败语义在相关章节说明。
- 编辑自审采用 blog-writer 的六个阅读关口与 Markdown 排版规范。代码按已读源码裁剪；假设输出和建议改法单独标注，无虚构实测结论。
- 根据用户追加的参考结构要求，通过浏览器读取已发表原文，将正文改为五个编号章节加前言；继续在对话中交付，不自动发布或创建 Page。
- 用户反馈调研深度不足后，完成第二轮源码追踪：补齐 active retrieval→citation scoring→access_count→rerank 闭环、两套 idle、Prompt/merge 契约、事务/fallback、用户隔离、开关与配置透传、结束/删除路径。
- 运行 8 个记忆测试文件：159 passed。运行 4 个探针确认：整批回滚与返回 ID 不一致、跨用户同主体查找命中首条、字符串 tags 被拆字符、episode fallback 的 summary/goal 未脱敏。
- 发现工作区存在其他任务的大量未提交 WebUI 改动，本任务未触碰；只更新本调研目录 3 个 Markdown 文件。
