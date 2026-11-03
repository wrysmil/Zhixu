# 2026-11-03 下午提交总结：记忆系统 P0–P3 落地

> 范围：`feature/desktop-app` 上 11:31–17:43 的 4 个提交
> 作者：creatorhqh-commits（本机 00:04 的两个 WebUI 提交不计入）
> 主线：把 OpenAkita 的双轨记忆机制落进知序——SQLite 是唯一真相源，`MEMORY.md` /
> `USER.md` 全部降级为投影，Dream 从「改文件」改为「写库 + 调工具」。

## 一、总览

| 时间 | 提交 | 主题 | 文件 | 增删 |
| --- | --- | --- | --- | --- |
| 11:31 | `1abee595` | style(webui): 统一记忆主题文案与黑白灰视觉 | 32 | +680 / −408 |
| 16:37 | `4abd8364` | feat(memory): USER.md 变投影 + 记忆写入工具 + Dream 收编入库 | 33 | +3848 / −574 |
| 17:42 | `a71f2cdf` | feat(memory): 每日全库盘账 + 衰减归档 + 注入预算 | 11 | +1208 / −10 |
| 17:43 | `b9bd8487` | feat(prompt): 提示词改造 + 记忆链路页面可见 | 16 | +376 / −18 |

依赖关系：`P0（USER.md 投影）→ P1（工具 + Dream 收编）→ P2（盘账/衰减）`；
`P3（提示词与页面可见性）`在 P1 之后即可做，本次与 P2 同日收口。

---

## 二、逐个提交

### 1. `1abee595` — 记忆主题的文案与视觉统一

**动机**：记忆相关页面在改造前文案口径不一致，浅色/深色主题、启动页、PWA
配色各自为政。

**改动**
- 首页与记忆页面文案统一，10 个语言包同步更新（i18n 结构对齐）。
- `globals.css` 黑白灰体系重排，浅/深色主题、启动页与 PWA `manifest` 配色收口。
- 替换助手菜单图标，同步已有测试断言。

**验证**：50 项相关测试、生产构建、三档宽度浏览器验证通过；全量检查中此前已存在的
评测与多语言问题单独记录，未混入本提交。

---

### 2. `4abd8364` — P0 + P1：USER.md 变投影，补齐记忆写入工具

**动机**：此前存在两条互不知情的记忆生产链路——Dream 用文件工具改
`SOUL.md` / `USER.md` / `MEMORY.md.draft`，extractor 写 `memories` 表再生成
`MEMORY.md`。结果是 Dream 越权、draft 无人消费、同一段对话重复消耗 LLM，
而主 Agent 只有一个只读的 `memory_search`，想记东西无处可写。

**P0：USER.md 投影**
- `USER.md` 加入 `LIFECYCLE_OWNED_FILES`，与 `MEMORY.md` 同构：自动导出区 + 手写区。
- 新增 `refresh_user_md_sync` / `write_user_md` / `select_user_facts`，写入统一走 lifecycle。
- Dream 的 `editable_files` 移除 `user_file`；旧英文模板按占位标记识别，不误迁成手稿。
- 顺带修掉 `select_core_facts` 按 `scope="user"` 过滤导致核心注入恒空的死代码。

**P1：记忆工具 + Dream 收编**
- 新增 `add_memory` / `memory_delete` / `memory_stats` / `consolidate_memory`
  （`nanobot/agent/tools/memory_admin.py`），只写库、不写 md，投影由 lifecycle 重算。
- `memory_delete` 两段确认：`dry_run=True` 拿候选与 `confirm_token`，候选集变化则 token 失效。
- 新增 `memory_dream_runs` 审计表：Dream 的写入计数取自 writer 真实决策，不是模型自述；
  主 Agent 的写入不计入。
- Dream 只剩 `skills/` 文件写与记忆工具（`source=consolidation`），
  `SOUL.md` / `USER.md` / `MEMORY.md` 全部拒写。
- 删除 `MEMORY.md.draft` 共 6 处，遗留 draft 归档到 `memory/legacy/`。
- `/dream-log` 改读审计表；`/dream-restore` 改为按条删除说明（git diff 已不能反映记忆变更）。
- 注入预算：compiler 丢弃 HTML 注释行，并屏蔽「暂无」与旧英文模板占位符。

**关键取舍**
- 否决「让 Dream 直接写 `MEMORY.md`」——会被 refresh 整份覆盖。
- 否决 `scope` 三态（auto/global/session）——本仓 `scope` 恒为 `global`，
  改为用 `retention` 映射已有的 `priority` + `expires_at`。
- 否决保留 Dream 对 `SOUL.md` 的写权限——价值观不该由定时任务自己改。

**验证**：全量 `tests/` 8236 passed / 11 failed（11 条在改动前 HEAD 上均可复现）；
新增 35 条用例（22 工具 + 13 审计）；真库副本完成写入/幂等/两段删除/审计/投影全链路验证。

---

### 3. `a71f2cdf` — P2：每日全库盘账、衰减归档与注入预算

**动机**：记忆库「只进不出」，`decay_rate` / `expires_at` / `importance_score`
字段齐全但全仓没有衰减计算；Dream 只做增量，不做全库去重；`MEMORY.md` 没有硬上限。

**改动**
- 新增 `nanobot/memory/decay.py`：规则级去重（内容哈希 + 向量近重复）、
  衰减归档（`effective = importance - decay_rate × 距上次访问天数`）、dry-run 预演、
  checkpoint 续跑。
- 新增 `memory_consolidate_runs` 审计表与 `agents.defaults.consolidate` 配置
  （默认每天 03:00、预算 1500s、归档阈值 0.15）。
- gateway 注册受保护系统 job `memory_consolidate`，与 Dream 完全独立，零 LLM 调用。
- 检索命中刷新 `last_accessed_at` / `access_count`：补上
  `MemoryStoreAdapter.record_accesses`——此前注入侧每轮静默 `AttributeError`，
  等于从未记录访问，衰减也就无从算起。
- `MEMORY.md` 超过 1500 字符时按类型优先级整条裁剪，规则类最后被砍。

**关键取舍**
- 归档不删除，只设置 `expires_at`；行与归档原因保留，清掉 `expires_at` 即可还原。
- 否决引入 `scope` 三态与全局桶；否决让 LLM 逐条审查（成本高，本批只做规则级去重 + 衰减）。

**验证**：`tests/memory/test_decay.py` 新增 10 条；`tests/memory` + `tests/webui`
2134 passed（2 条失败在未改动模块）；临时库端到端验证 60 天未访问事实被正确归档。

---

### 4. `b9bd8487` — P3：提示词改造与记忆链路页面可见

**动机**：工具已经发齐，但模型不知道什么时候用；衰减、盘账结果和「有未编译改动」
这类状态对用户不可见。

**改动**
- `dream.md` 新增三段：定级指引（0.9+ 与 0.4 的判定）、预算意识、逐条理由汇报；
  衰减规则改为库字段语义；不再提 `SOUL`/`USER` 文件出口；禁止声称写入任何文件。
- `AGENT.md` 新增「成长循环」四条（不引入 Ralph 式 `MEMORY.md` 读写）。
- `SOUL.md` 按用户确认补三节价值取向（真正的帮助 / 诚实 / 自主与边界），
  源码 604 字符、编译产物 592/1200，与 `personas/balanced.md` 同源。
- 自动化页：`memory_consolidate` 显示上次盘账的处理/合并/衰减/归档与 dry-run。
- 身份编辑器：显示上次编译时间与「有未编译改动」提示，保存/重载后刷新。

**关键取舍**
- 否决照搬 openakita 的 `SOUL` 全文——其中 Ralph / fresh-context / install_skill
  等机制本仓未实现，抄进来会变成无法兑现的承诺。
- 行为类条款仍只放 `AGENT.md`，`SOUL.md` 只承载价值取向。

**验证**：`tests/identity` 与 `tests/agent/test_dream.py` 新增 11 条断言；
WebUI `tsc --noEmit` / `eslint` / `vitest` 通过；`SOUL` 编译产物实测 592/1200。

---

## 三、贯穿四条纪律

1. **模型永远不直写 `MEMORY.md` / `USER.md`。** 它们是投影，写了也会被刷新覆盖；
   唯一例外是文件内手写区，且必须走 `NOTE:BEGIN` 标记。
2. **写入口只有一个。** 主 Agent、Dream、定时任务全部经过
   `writer.apply_memory_changes`，去重、superseded 链、审计只在这一处实现。
3. **删除必须二次确认。** `memory_delete` 的 dry-run 预览不可跳过。
4. **归档不等于删除。** 衰减只拨 `expires_at`，保留行与原因，可逆。

## 四、验证状态与遗留

**已验证**
- 后端全量与新增用例、WebUI 类型检查/lint/单测、`SOUL` 编译体积、衰减端到端。

**未验证（提交中自述 Not-tested）**
- 自动化页的盘账统计与身份编辑器，未在浏览器实际点击验证。
- WebUI 设置页「上次整理」一行未在浏览器实际验证。

**文档与基线问题**
- `P2` / `P3` 文档引用的
  `.ai-runtime-artifacts/verifications/2026-11-03-memory-refactor-p2-p3-verification-lite.md`
  在仓库与各 worktree 中均不存在，属于断链，需要补写或改引用。
- `4abd8364` 记录的 11 条失败为改动前基线，均位于未改动模块，尚未清理。
- P2「全局桶」按 P1 决策未实施，`scope` 保持全库恒 `global`。
