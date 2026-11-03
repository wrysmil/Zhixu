---
artifact: research
route: systematic-debugging
skills: [systematic-debugging]
source:
  - https://docs.mem0.ai/core-concepts/how-it-works
  - https://docs.mem0.ai/platform/features/custom-instructions
  - https://docs.mem0.ai/core-concepts/memory-operations/add
  - https://langchain-ai.github.io/langmem/concepts/conceptual_guide/
  - https://docs.letta.com/v1-sdk/memory/memory-blocks
created_at: 2026-11-03
---

# 记忆抽取提示词：外部实践调研与本仓库落点

## 外部结论（四个来源）

### Mem0（How it works / Custom instructions / Add）

- 记忆与消息分层：`add()` 默认走「抽取 → 追加存储 → 检索」三段式；`infer=False` 才存原文。
- 追加式写入：新记忆不覆盖旧记忆，重复/更新由检索侧与下游决策处理。
- **Custom Instructions 是产品级「该抽什么/不该抽什么」开关**：官方模板结构为
  `Your Task → Information to Extract（分类清单）→ Exclude（排除清单）`。
- 明确区分**用户范围**与**助手/agent 范围**两套抽取规则：用户偏好/目标/约束 vs.
  工具失败、重试策略、环境怪癖，并要求相互排除。

### LangMem（Conceptual guide）

- 记忆分三类且各有归属：**语义**（事实/知识）、**情节**（成功经验与推理过程）、
  **程序性**（系统行为规则）。用户画像偏好 **profile**（单份当前状态文档），
  事实集合偏好 **collection**（可检索条目）。
- 明确警告**过度抽取**会降低检索精度、**抽取不足**会降低召回；两者要靠提示词调节平衡。
- collection 需要 reconcile（删除/失效/更新/合并）新信息与旧信念。
- 形成时机分「主动（会话内）」与「后台（交互之间）」，对应本仓库的 `memory_save`
  与空闲抽取。

### Letta（Memory blocks）

- 用**带描述的命名块**区分记忆用途：`human`（人）、`persona`（角色）、
  `scratchpad`（工作状态）、`reference`（只读规范）。描述决定 agent 何时读/写哪个块。
- 块默认可写可读；只读块用于不会被 agent 破坏的规范。
- 这与本仓库的 `memories`（用户画像）/ `experiences`（经验）/ `scratchpad`（工作状态）/
  core-note（用户手写）分层一致，缺的是**写入入口的显式化**。

## 落点到本仓库

| 外部做法 | 本仓库现状 | 本次改动 |
| --- | --- | --- |
| Mem0：抽取规则用「Extract 清单 + Exclude 清单」 | 提示词有分类与过滤，但缺「问题前提/回忆请求」判据 | 提示词重写为四类输入 + 8 条抽取边界 + 显式 Exclude 清单 |
| Mem0：区分用户范围与 agent 经验范围 | 提示词混在一份输出里 | `memories`（用户/项目事实）与 `experiences`（经验）双轨，经验必须有工具结果证据 |
| LangMem：过度抽取伤精度 | 6 条测试问题被抽成画像 | 程序化过滤：问题/回忆/任务/疑问句不作为画像证据；经验必须带证据 |
| LangMem：profile vs collection | 只有 collection | 保留 collection；用户手写内容走 core-note（profile 性质） |
| Letta：显式可写记忆块 | 只有 `memory_search`，没有保存工具 | 新增 `memory_save` 工具 + `save_manual_memory` 写入路径 |
| Mem0/LangMem：未验证声明要保留归属 | 抽取内容写成客观事实 | 提示词要求未验证的用户陈述写成「用户报告/用户表示」 |

## 仍然存在的边界

- 提示词只能约束模型倾向，程序化过滤只能挡住可识别的句式；语义层面的「这是陈述还是提问」
  仍可能误判，因此保留「宁缺毋滥 + 证据必须逐字匹配」两条硬约束。
- 经验现在必须有工具结果或用户确认作为证据；历史数据里没有证据的旧经验不会被自动补证。
