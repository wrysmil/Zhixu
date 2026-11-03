# P0 — USER.md 变为记忆投影

> 状态：**已完成**（2026-11-03）｜ 前置：无 ｜ 后续：P1 / P2 / P3 全部依赖本批定的规矩
>
> 末尾「实施记录」一节列出与原始计划的差异和附带发现，先读那节。

## 目标

让 `USER.md` 和 `MEMORY.md` 同构：**由 `memories` 表派生，人工只能改文件内的手写区**。
做完之后，删掉库里一条关于用户的记忆，`USER.md` 会自动少一行。

## 现状与问题

| 事实 | 位置 |
|---|---|
| `MEMORY.md` 已是投影产物，含「自动导出区 + 手工笔记区」两段式 | `nanobot/memory/lifecycle.py` → `MemoryLifecycle.refresh_memory_md_sync` |
| `USER.md` 仍是 Dream 直接改写的自由文本 | `nanobot/agent/memory.py` → `build_dream_tools`，`editable_files = [soul_file, user_file, draft_file]` |
| 两条写手整份覆盖同一文件，格式与语义都不同 | — |
| 出厂模板占位符 `（待填）` 会被当真实内容渲染进 prompt | `nanobot/templates/USER.md` |

编译器侧已经就绪，不需要改：

| 目标 | 源 | 产物 | 上限 | 出厂模板处理 |
|---|---|---|---|---|
| `identity_core` | SOUL.md | identity.core.md | 1200 | 不跳过（人格本就该注入） |
| `agent_behavior` | AGENT.md | agent.behavior.md | 900 | 跳过 |
| `user_profile_core` | USER.md | user.profile.core.md | 600 | 跳过 |

编译主流程 `identity/compiler.py::compile_identity` 已含 `_PLACEHOLDER_MARKERS` 过滤与
`skip_factory_template`，占位符问题在编译层已有护栏。

## 改动清单

### 1. 上锁：USER.md 归 lifecycle 独有

`nanobot/identity/catalog.py`

- `LIFECYCLE_OWNED_FILES` 由 `frozenset({"MEMORY.md"})` 扩为 `{"MEMORY.md", "USER.md"}`。
- `IdentityStore` 对该集合内的文件拒绝直写，行为与 MEMORY.md 完全一致（抛 `IdentityStoreError`）。
- 注意 `_target` 的读取优先级：派生文件先读 `memory/` 再回落 `identity/`。USER.md 的真身将落在
  `identity/USER.md`，因此需要确认该回落顺序不会读到 `identity/` 下的残留旧文件——
  **决定 USER.md 投影写到 `identity/USER.md` 还是 `memory/USER.md`，二者只能选一个**。
  **已定：写 `identity/USER.md`**，`_target` 只对 `MEMORY.md` 走 `memory/` 分支
  （新增常量 `MEMORY_DIR_DERIVED_FILES`）。理由：`ContextBuilder` 读的是
  `identity/USER.md`，落点必须与注入侧一致。

### 2. 投影函数

`nanobot/memory/lifecycle.py` → `MemoryLifecycle`

新增 `refresh_user_md_sync(workspace_id)`，结构照抄 `refresh_memory_md_sync`：

1. 从库里取用户相关记忆。复用 `select_core_facts` 的过滤条件（非 superseded、未过期），
   但**不要**用 `subject = '用户'` 精确等值——openakita 在这个坑里漏掉「用户本人 / 我 / user」。
   建议按 `type IN (PREFERENCE, FACT, RULE)` 且 `subject` 命中一组别名（`用户`、`用户本人`、
   `当前用户`、`user`、`me`，大小写不敏感）来召回，再按 `importance_score DESC` 排序取前 50。
2. 过滤流水账：复用 `memory/filters.py` 的 `is_task_artifact` / `is_ai_self_talk`，
   并追加一个动作词黑名单（打开 / 删除 / 部署 / 运行……）防「[experience] 用户希望删除工作区 py 文件」进档案。
3. 分桶：基本信息（称呼、时区、语言）、协作偏好（语气、长度、详细程度）、工作上下文（角色、项目、工具）。
   兜底只落「协作偏好」，`RULE` / `SKILL` / `ERROR` 类型不进 USER.md。
4. 渲染两段式（见第 4 点），每桶最多 8 条。
5. `_safe_write_with_backup` 落盘，与 MEMORY.md 共用同一个 `_derive_lock`。
6. **写完后必须触发 `identity.compile_identity(workspace)`**，否则 `user.profile.core.md` 不更新，
   模型下一轮看到的还是旧档案。这是本批最容易漏的一步。
7. 内容无变化时直接返回 `{"status": "skipped"}`，不要无意义重写（会刷新 mtime、触发缓存失效）。

统计口径与 MEMORY.md 对齐：刷新时间、触发来源（manual / mutation / scheduled）记入
`MemoryLifecycle` 的类级字典，供 WebUI 展示。

### 3. 存量迁移

新增 `_migrate_legacy_user_md`，参考已有的 `_migrate_legacy_memory_md`：

- 识别当前 `USER.md` 是否含 `NOTE:BEGIN` 标记；没有 → 视为旧稿。
- 旧稿若是出厂模板（`_is_template_content`）→ 直接丢弃，不迁移。
- 旧稿是用户手写内容 → 整体作为一条 `core-note` 风格的记录存入库（`subject=用户`，
  `type=PREFERENCE`，`importance=0.7`，标记 `needs_review=True`），
  **不要静默判定为事实**，也不要整份覆盖。
- 迁移只在第一次执行，跑完写标记，幂等。

### 4. 模板改两段式

`nanobot/templates/USER.md`，与 MEMORY.md 的手工区约定保持一致：

实际落地的模板（比计划少一层「字段名」，理由见实施记录）：

```markdown
# User Profile

帮助知序了解你，以便用你舒服的方式协作。

<!-- zhixu:user-auto:begin ... 请勿手改 -->
（暂无记录：对话中了解到的信息会自动出现在这里）
<!-- zhixu:user-auto:end -->

<!-- zhixu:user-note:begin ... 可自由编辑 -->
## 特别说明

（暂无手写笔记：任何希望 AI 长期记住的禁忌、习惯或偏好，写在这里）
<!-- zhixu:user-note:end -->
```

有内容时自动区按 `## 基本信息 / ## 工作上下文 / ## 协作偏好` 分节，每节若干条
`- 正文`；空分区不渲染。**不再**写 `（待填）` 这类占位符——它们会被模型当真实值。

同步更新 `nanobot/identity/bootstrap.py::ensure_identity_templates` 的 seed 逻辑，
让新 workspace 直接用新模板。

### 5. 收回 Dream 的写权限

`nanobot/agent/memory.py::build_dream_tools`

- `editable_files` 去掉 `self.user_file`，只留 `soul_file` 与 `draft_file`。
- `_DREAM_CONTENT_PATHS` 去掉 `"USER.md"`，否则 `/dream-log` 仍会把 USER.md 的变化记成 Dream 的改动，
  git 审计和实际权限不一致。

> SOUL.md 的写权限本批**不动**，留到 P3 连同 dream.md 一起收。一次改两处出问题说不清是谁的锅。

### 6. WebUI 提示

身份编辑器里给 USER.md 加一句「自动区由记忆系统生成，手改会在下次整理时被覆盖」，
与 MEMORY.md 现有的提示保持一致。不需要新接口。

## 明确不做

- 不做 `profile_state.json` + 白名单那套（openakita 的方案，与现有库结构重复）
- 不动 Dream 的工具白名单、不删 draft（P1 的事）
- 不加任何新工具
- 不引入 LLM 参与分类（纯规则，保持 MEMORY.md 同样的零 LLM 成本）

## 实施记录（2026-11-03）

### 与计划的差异

| 计划 | 实际 | 原因 |
|---|---|---|
| 自动区渲染成 `- **称呼**：` 字段 | 渲染成 `- 正文` 条目 | 记忆是整句而非键值对；硬拆字段要靠猜，猜错比不拆更糟。分区已经提供了结构 |
| 手写区复用 core-note | 新增 `user-note:` 前缀的独立记录 | 复用会让两个文件互相覆盖手写区 |
| 预算：先放笔记再砍事实（照抄 MEMORY.md） | 自动区优先，笔记拿剩余 | 实测：一份 1100 字旧稿把 11 条画像全挤成「暂无记录」 |
| 只做 USER.md | 附带修了核心视图的 scope 回归 | 见下 |

### 附带发现：核心视图是一条死代码

`select_core_facts` 一直按 `scope="user"` 过滤，但 D01-D13 之后记忆的
`scope` 默认 `global`、抽取链路从不写 `user`——真实库里 17 条记忆全是
`global`。后果有两层：

1. `MEMORY.md` 的自动区永远为空，只剩手工笔记；
2. 注入侧 `agent/context.py::_load_core_hits` 调的是同一个函数，**每轮注入的核心事实也是空的**。

USER.md 若照抄这套筛选，会继承同一条死路。因此 P0 一并改了
`select_core_facts`：去掉 scope 过滤，改用 workspace + 有效性 + 重要性，
并排除 `is_manual_note_record`；同时把三条钉住旧行为的测试改成钉住新契约。

**这条修复会让 MEMORY.md 和核心注入从「空」变成「有内容」**——所以又补了
`_is_meta_observation`，把「用户的 USER.md 仍是空白模板」这类 AI 对配置文件的
观察挡在核心视图外，否则它们会立刻进入每轮 prompt。

### 附带发现：旧版英文模板会污染迁移

真实 workspace 的 `USER.md` 还是上一代英文模板（`(your name)` 那一版），
逐字比对认不出来，会被整份当成「用户手稿」搬进手写区。现已支持按占位标记
识别，并把旧模板占位符加进编译器的 `_PLACEHOLDER_MARKERS`。

另外编译器现在会丢弃 `<!-- ... -->` 行——区块标记只对人有用。

## 验收标准

### 自动

1. `pytest tests/` 全绿，新增用例不少于 6 条：
   - 删一条用户记忆 → `refresh_user_md_sync` 后文件中该行消失
   - 手写区内容在两次刷新之间保持不变
   - 出厂模板原稿被 `_is_template_content` 识别且不迁移
   - 旧稿非模板 → 生成一条 `needs_review=True` 的记忆且原文件未被静默改写
   - `subject='用户本人'` 的记忆能被召回（防精确等值回归）
   - 含「删除 / 部署」等动作词的流水账不进 USER.md
2. `refresh_user_md_sync` 返回 `skipped` 时文件 mtime 不变。

### 手动（核心闭环，必须人眼过一遍）

1. 启动后在对话里说「记住我不吃香菜」→ 记忆面板出现该条 → 触发一次整理 →
   `identity/USER.md` 的「协作偏好」下出现该行，且 `identity/runtime/user.profile.core.md` 同步更新。
2. 在面板删除该条 → 再触发一次整理 → USER.md 里该行消失。**这一条不过，P0 不算完成。**
3. 手动编辑 USER.md 的 `NOTE:BEGIN` 区 → 触发整理 → 手写内容保留。
4. 手动编辑 `AUTO:BEGIN` 区 → 触发整理 → 手改被覆盖，且有备份文件生成。
5. 跑一次 Dream → `git diff` 确认 USER.md 无变化。

### 回归风险点

- `agent/context.py` 的 `_load_bootstrap_files` 会同时找 `identity/USER.md` 和 workspace 根的
  `USER.md`。投影写到 `identity/` 之后，**必须清理 workspace 根的残留文件**，
  否则老位置优先命中，注入的永远是旧内容。
- `identity/bootstrap.py::migrate_legacy_identity_files` 会把根目录的 SOUL/USER 搬进 identity/，
  与投影写入口可能打架，需要确认执行顺序。

## 回滚

`LIFECYCLE_OWNED_FILES` 退回 `{"MEMORY.md"}` + 模板退回原版 + `_target` 分支退回。
`USER.md` 内容由迁移时生成的备份恢复，无数据丢失。

## 验证结果（2026-11-03）

### 自动

`tests/memory/test_user_profile_projection.py` 20 条全绿；`tests/memory` +
`tests/identity` + Dream + refresh 路由合计 1114 passed / 1 skipped。

覆盖的断言：

| 用例 | 钉住什么 |
|---|---|
| `test_delete_memory_removes_the_line` | **P0 核心闭环** |
| `test_second_refresh_skips_and_keeps_mtime` | 无变化不重写、不刷 mtime |
| `test_note_area_survives_refresh` | 手写区两次刷新后不变 |
| `test_write_user_md_saves_note_only` | 自动区手改被覆盖、手写区入库 |
| `test_factory_template_not_migrated` | 出厂模板不迁移 |
| `test_legacy_handwritten_content_becomes_review_note` | 旧稿 → `needs_review` 笔记，内容不丢 |
| `test_own_export_is_not_treated_as_legacy` | 二次刷新不回吞自己的导出 |
| `test_subject_alias_is_recalled` | `用户本人 / 我 / User` 都能召回 |
| `test_action_flow_is_excluded` | 动作词流水账不进画像 |
| `test_meta_observation_kept_out_of_core_view` | AI 对配置文件的观察不进核心视图 |
| `test_long_note_does_not_starve_facts` | 长笔记不挤掉画像 |
| `test_compiled_profile_updated` | 派生后 `user.profile.core.md` 同步，且无注释/空态泄漏 |

### 真数据干跑

拿真实 workspace（`~/.nanobot/workspace`，17 条记忆、全部 `scope=global`、
`subject` 16/17 为空）的副本跑了一遍：

- 候选命中 11 条，六条被正确剔除（3 条 AI 对配置文件的观察、2 条 SKILL、1 条 ERROR）；
- 渲染 698 字符，分节正确；二次刷新 `skipped`；
- 删除库里那条「游泳」→ 走真实 mutation 钩子 → 文件里该行消失；
- 通过 `identity_write_file` 写入手写区 → 入库、`.bak` 生成、自动区被重算。

### 仍未手工验证（需要你启动应用过一遍）

1. WebUI 身份编辑器里 USER.md 显示橙色「自动重新生成」徽标、且仍可编辑保存；
2. 对话里说「记住我不吃香菜」→ 面板出现该条 → USER.md「协作偏好」出现该行
   （依赖 P1 的 `add_memory`，**当前模型还没有写工具，这一步暂时做不到**）；
3. 跑一次 Dream → `git diff` 确认 USER.md 无变化（`editable_files` 已移除该项，
   用例 `test_dream_tools_excludes_derived_files` 已覆盖，但真实一次运行未跑）。

> 注意：老 workspace 根的 `USER.md` 不会被删除（那里可能有你的手写内容）。
> 注入侧已优先读 `identity/USER.md`，根目录那份自此只是残留副本。
