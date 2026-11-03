# P3 — 提示词改造与页面可见性

> 状态：待开工 ｜ 前置：P1（工具已就位，dream.md 才能改）

## 目标

工具发齐了，还得让模型知道什么时候用。本批做三件事：改 dream.md、补 AGENT.md 行为段、
把记忆链路在界面上摊开。

## 现状与问题

| 事实 | 位置 |
|---|---|
| `templates/agent/dream.md` 139 行，路由表仍指向 SOUL.md / USER.md / draft | `nanobot/templates/agent/dream.md` |
| AGENT.md 编译上限 900 字符，已有「任务执行 / 工具与环境 / 沟通契约」三节 | `nanobot/identity/compiler.py::COMPILE_TARGETS` |
| SOUL.md 18 行，无「我会记住」类承诺 | `nanobot/templates/SOUL.md` |
| `provenance` 已有来源标记，无页面入口 | `nanobot/memory/provenance.py` |
| identity 有 mtime 变更检测，但 system prompt 带缓存 | `identity/compiler.py` + `core/_agent_runtime` |

`dream.md` 是**整个记忆系统里质量最高的一份资产**，本批只改三段，不要重写。

## 改动清单

### 1. dream.md：删 25 行，改 3 处，加 15 行

**删掉：**

- 第 11 行（`~~MEMORY.md~~` 划掉那行）——不再提文件，只提记忆类型。
- 第 28-48 行整段 `MEMORY.md.draft generation rules`。
- 第 130 行 `Edit those files directly` 的指令。

**改：**

| 现在 | 改成 |
|---|---|
| 路由表四个文件出口 | 路由到记忆类型：`rule` / `preference` / `fact` / `skill`，并给出与库 `type` 枚举的对应关系 |
| `read_file memory/MEMORY.md` 看真相 | 由 prompt 直接注入「当前核心记忆摘要」，不再让它自己读文件 |
| 删掉 `SOUL.md` / `USER.md` 两个出口 | 价值观与档案不再由 Dream 写 |

**加（三段，合计约 15 行）：**

1. **定级指引**——什么情况给 `importance=0.9+`（用户明确要求永久记住、反复强调的身份事实）、
   什么给 `0.4`（一次性上下文）。现在它完全没有概念，导致所有记忆同权重，衰减无从谈起。
2. **预算意识**——「核心记忆总字符有上限，你提交的条目如果总量超限，优先保留规则类」。
3. **汇报要求**——「最后输出：新增 N 条、合并 M 条、删除 K 条、跳过 J 条，逐条一句话理由」。
   审计表现在只有计数，没有理由，用户问「为什么删我这条」时答不上来。

**原样保留的部分**（这些都是精华，别动）：

- 第 85 行「能靠一次搜索查到的公知信息不要记」
- 第 106-110 行衰减规则（但要改成调用库的字段语义，而不是自己维护文件）
- 第 133-134 行验证纪律（只汇报工具调用证实的修改）
- 第 138 行中文「禁止声称已写入 MEMORY.md」→ 改为「禁止声称已写入任何文件」

### 2. AGENT.md：加一节「成长循环」

在 `templates/AGENT.md` 增加一节，标题必须是 `成长循环`——
`compiler.py` 的 `owned_markers` 已经包含这个词，不加这个标题编译产物收不到它。

参考 openakita 的 Growth Loops，压到 5 行以内（编译上限 900 字符，现有内容已占大头）：

```markdown
## 成长循环

- 同类操作第 2 次出现：在记忆里标记。第 3 次出现：主动问要不要固化成技能。
- 任务失败学到教训：立刻记一条（类型 error）。用户纠正错误：记成规则（类型 rule）。
- 发现高效的工具组合：记成经验（类型 experience）。
- 每轮对话留意 1-2 个值得了解的细节，不要为了记而记。
```

**绝对不要抄 openakita 的这两句：**

```
- 每次迭代保存进度到 MEMORY.md
- 每次迭代从文件读取状态（fresh context）
```

那是 Ralph 续跑机制的需求，而且它允许模型直写 MEMORY.md。你的 MEMORY.md 是投影产物，
模型照着做会反复写入失败、重试、烧 token，还会污染对话。它的
`Task Execution Flow` 里「更新 MEMORY.md 记录进度和经验」同样要改成「调用 `add_memory`」。

### 3. SOUL.md：只做一件事

检查有没有「我会记住你说的一切」这类过度承诺。现在这 18 行没有，**可以不动**。
如果以后要加，只能写成「重要的偏好我会写进记忆，并告诉你写了什么」——
承诺必须和机制能力对齐，否则用户发现记不住时信任直接归零。

### 4. 页面：记忆链路可见

| 位置 | 加什么 |
|---|---|
| 设置 → 自动化 | 日整理 job 的上次运行结果（增 / 合 / 衰 / 归条数）——数据 P2 已入审计表 |
| 记忆面板 | 每条记忆点开显示「来源：某年某月某日某会话」「置信度 0.82」「被访问 7 次」 |
| 身份编辑器 | SOUL / AGENT 显示「上次编译时间」与「有未编译改动」提示 |

第三条对应 openakita 那个已知坑：改了文件但 prompt 缓存没清，下一轮不生效。
生效链路是 `保存 → identity.reload() → compile_all() → 失效 system prompt 缓存`，
绕过 UI 直接改文件的人需要看到一个明确的「未编译」标记。

### 5. 测试与文案

- `tests/` 补 dream.md 的路由断言：给定一句「以后都用 pnpm」，断言产出 `preference` 类型记忆
  而不是一条文件写入指令。
- `docs/memory.md` 的 Dream 章节同步更新（第 57 行仍写着 Dream 读当前 MEMORY.md）。
- `docs/automations.md` 补 `memory_consolidate` job 说明。

## 明确不做

- 不重写 SOUL.md 的气质与篇幅。openakita 那份是 Claude 宪法式长文，Zhixu 是 694 字节的
  「跑在你自己设备上的猫」。**参考它的章节分层，不要参考它的文字量**，编译上限也不允许。
- 不把 dream.md 翻成中文。LLM 对英文指令的遵循度通常更高，且现有内容质量稳定，
  没有充分理由动语言。
- 不加新工具。

## 验收标准

### 自动

1. `pytest tests/` 全绿，新增用例不少于 5 条：
   - dream.md 不含 `write_file` / `edit_file` 指向 SOUL.md / USER.md / draft 的指令
   - dream.md 含定级指引、预算意识、汇报要求三段
   - AGENT.md 编译产物包含「成长循环」一节
   - AGENT.md 编译产物不含「MEMORY.md」字样（防 Ralph 那句混进来）
   - 编译产物总字符不超过 900
2. `identity/compiler.py` 的 `owned_markers` 与新章节标题一致。

### 手动

1. 连续三次让 AI「用 pnpm 装依赖」→ 第三次它主动说「要不要我固定用 pnpm」。
2. 改一次 SOUL.md → 身份编辑器显示「有未编译改动」→ 点编译 → 下一轮对话行为变化。
3. 触发一次 Dream → 审计表有记录，页面上能看到它自己汇报的逐条理由。
4. 在记忆面板点开任意一条 → 能看到来源会话与时间。

### 回归风险点

- AGENT.md 加内容后编译可能超 900 字符被 `_enforce_char_budget` 截断，
  而截断是按行切的，可能正好把新加的「成长循环」整节切掉。**加完必须实测产物。**
- dream.md 与工具白名单必须同批上线。只改一边会出现「提示词让它调 `add_memory`，
  但它手上只有文件工具」，它会退化成写文件，白改。

## 回滚

`templates/` 三个文件恢复原版即可，无数据迁移。`compiler.py` 的 `owned_markers` 若加过词需同步退回。
