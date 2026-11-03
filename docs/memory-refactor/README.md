# 记忆系统改造计划（P0–P3）

把 OpenAkita 的双轨记忆机制（SQLite 全量库 + md 投影）落到知序，同时收编上游遗留的 Dream。

## 为什么改

现在有两套并存的记忆生产链路，互不知情：

```
链路 A：history.jsonl ──Dream(每2h,LLM带文件工具)──> SOUL.md / USER.md / MEMORY.md.draft
链路 B：session turns ──extractor(会话结束,LLM)──> memories 表 ──> MEMORY.md
```

问题：Dream 的 `MEMORY.md.draft` 没有任何消费入口；Dream 越权写 SOUL.md / USER.md；
两条链路对同一段对话重复消耗 LLM；库里 `decay_rate` 等字段齐全但无衰减算法；
主 Agent 只有一个只读的 `memory_search`，想记东西无处可写。

核心思路：**Dream 保留脑子（提示词），换掉手（工具）；USER.md 变成和 MEMORY.md 一样的投影；
库是唯一真相源，md 全是海报。**

## 批次与依赖

```
P0  USER.md 投影 ──┬──> P1  工具 + Dream 收编 + 删 draft ──> P2  日盘账 + 衰减 + 预算
                   └──> P3  提示词 + 页面可见性
```

| 批次 | 状态 | 文件 | 一句话 | 能否单独上线 |
|---|---|---|---|---|
| P0 | **已完成** | [P0-user-md-projection.md](./P0-user-md-projection.md) | USER.md 变成投影，Dream 交出写权限 | 能，且风险最低 |
| P1 | **已完成** | [P1-memory-tools-and-dream.md](./P1-memory-tools-and-dream.md) | 补 4 个记忆工具，Dream 改产出入库，删 draft | 依赖 P0 |
| P2 | 待开工 | [P2-consolidation-and-decay.md](./P2-consolidation-and-decay.md) | 每日盘账 job + 衰减 + 注入预算 + 全局桶 | 依赖 P1 |
| P3 | 待开工 | [P3-prompts-and-visibility.md](./P3-prompts-and-visibility.md) | 改 dream.md、补 AGENT.md、页面可见 | 依赖 P1 |

P0 与 P3 无相互依赖，可并行；P3 的 dream.md 深度改造必须在 P1 工具就位之后（P1 已做最小改写，P3 补成长循环与预算意识），
否则提示词让模型调 `add_memory` 而它手上只有文件工具，会退化成写文件。

## 每批的共同验收底线

1. `pytest tests/` 全绿，且每批新增用例数达标（P0≥6 / P1≥8 / P2≥8 / P3≥5）。
2. 有一条**人眼验证的闭环**，见各文件「验收标准 → 手动」第一条。
   P0 的闭环是：删一条记忆，USER.md 少一行。这条不过，后面全是空中楼阁。
3. 无新增绕过库直写 md 的路径。

## 贯穿全程的三条纪律

1. **模型永远不直写 MEMORY.md / USER.md。** 它们是投影，写了也会被刷新覆盖。
   唯一的例外是文件内的手写区，且必须走 `NOTE:BEGIN` 标记。
2. **写入口只有一个。** 所有记忆变更最终都经过 `writer.apply_memory_changes`，
   无论是主 Agent、Dream 还是定时任务。去重、superseded 链、审计只在这一处实现。
3. **删除必须二次确认。** 用户感知到的「AI 忘了我说的话」几乎都是误删，
   `memory_delete` 的 `dry_run` 预览不可跳过。

## 不在本计划内

- 向量检索的降级策略与 embedding 选型
- 多用户 / IM 场景的租户隔离（当前单 workspace 够用）
- `profile_state.json` + 白名单方案（与现有库结构重复，已否决）
- SOUL.md 的气质与篇幅重写
