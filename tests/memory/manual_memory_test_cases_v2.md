# 知序记忆功能手动测试集 v2（桌面客户端）

> 适用：桌面版（Electron 壳 + 本地 gateway + WebUI）。目的：人工主动询问，验证记忆的**提取、检索、注入**三环节，以及日志能否解释「为什么记得 / 为什么没记住」。
> 配套：日志实现见 `.ai-runtime-artifacts/specs/2026-11-02-memory-observability-and-test-suites-spec.md`；自动化测试集见同目录 `memory-eval-smoke-20.jsonl`。

## 一、开测前准备

| 项 | 要求 |
| --- | --- |
| 实例隔离 | 用独立 `--config` / `--workspace` 启动，避免污染日常记忆 |
| 记忆开关 | `agents.defaults.memoryEnabled = true` |
| 日志开关 | `agents.defaults.memoryLogging`：`enabled=true`、`fileEnabled=true`、`eventsEnabled=true`、`maxContentChars=240` |
| 启动 | 改完配置重启 gateway / 桌面端；确认 `<data_dir>/logs/memory.log` 与 `memory-events.jsonl` 已生成 |
| 清场（可选） | 每轮前清空测试库：`sqlite3 <workspace>/memory/state.db "delete from memories; delete from episodes; delete from scratchpad;"` |
| 等待方式 | 不要固定 sleep。等 `memory.log` 出现 `【记忆提取·完成】`，或记忆面板出现条目，再进行下一问 |

### 触发与等待（重要）

- **idle 触发**：默认**空闲 120 秒**（`agents.defaults.memoryIdleSeconds`）后自动提取；从最后一条回复结束开始计时，期间发新消息会重置。建议测试环境改成 **15–20 秒**，否则每条用例都要干等两分钟。
- **会话结束触发**：关闭/删除会话会立即 flush，不等 idle；但桌面端关窗口会停掉 gateway，flush 可能来不及——**结束会话后保持应用开着**等完成。
- **提取耗时**：语义 + 情节两路 LLM 并发，正常几秒到 60 秒；模型慢或重试时可能 1–2 分钟（批次租约 120 秒）。
- **判定完成**：以日志 `【记忆提取·完成】` / `extraction_completed` 或面板出现条目为准；超过 **2 分钟**仍无完成事件，按失败排查（看 `extraction_failed` / `extraction_skipped`，不要继续等）。
- **不触发的情况**：新增消息不足 2 条，且只有一条短于 10 字的用户消息、又没有工具调用 → `extraction_skipped(reason=signal_threshold)`，这是产品策略，不是 bug。
| 记录 | 每条用例记录：实际回答、日志关键行（kind + id）、截图（面板类）、通过/失败 |

**日志位置**：`~/.nanobot/logs/memory.log`（文本）、`~/.nanobot/logs/memory-events.jsonl`（结构化，开启后）。
**断言用词**：`extraction_completed` / `memory_changed(change=...)` / `recall_candidates` / `reranked` / `injected(ids, dropped, hint)`。

## 二、A 组：典型对话流程（12 条）

### A01 基础事实记忆
- 步骤：新会话说「我住在杭州，做后端开发」→ 等待提取完成 → 新开会话问「我住哪？」
- 预期回答：杭州。
- 日志断言：`extraction_completed.memories ≥ 1`；`memory_changed.change=added`；`injected.ids` 含该条。

### A02 偏好设置与遵循
- 步骤：说「推荐咖啡时不要加糖，我喝无糖的」→ 新会话问「帮我点杯咖啡」。
- 预期回答：推荐无糖/不加糖的选项。
- 日志断言：`memory_changed.type=preference`；`injected` 非空且含偏好条目。

### A03 知识更新（当前状态）
- 步骤：说「我养了一只猫，叫咪咪」→ 等提取 → 说「猫送人了，现在养狗，叫豆豆」→ 等提取 → 问「我现在养什么宠物？」
- 预期回答：狗（豆豆），不提猫是当前宠物。
- 日志断言：第二轮 `memory_changed.change ∈ {updated, superseded}`；`injected.ids` 不含旧猫条目。

### A04 历史与当前区分
- 步骤：承接 A03，问「我以前养什么宠物？」
- 预期回答：猫（咪咪），并明确是过去。
- 日志断言：`injected` 可含旧值；回答不得把旧值说成当前。

### A05 跨会话回忆
- 步骤：A01 后关闭会话窗口再重开（或新建会话），问「我做什么工作？」
- 预期回答：后端开发。
- 日志断言：新 session 的 `injected.ids` 仍含该条；`recall_candidates` 有候选。

### A06 多事实综合
- 步骤：说「下周三去上海出差，住静安区」→ 新会话问「我出差住哪？」
- 预期回答：静安区（可带上海）。
- 日志断言：`injected` 命中 ≥1 条；回答不编造酒店名/具体地址。

### A07 拒答（无信息）
- 步骤：新会话问「我生日是哪天？」
- 预期回答：明确说没有这项信息，不编造。
- 日志断言：无 `memory_changed`；`injected` 为空或与生日无关。

### A08 无关信息不干扰
- 步骤：先闲聊天气、电影 5 轮，再问 A01 的居住城市。
- 预期回答：杭州。
- 日志断言：`recall_candidates` 有候选；`injected` 不被闲聊内容占满。

### A09 删除/遗忘（产品支持时）
- 步骤：在记忆面板删除 A01 的城市条目（或发「忘记我住在哪」）→ 新会话问「我住哪？」
- 预期回答：不再暴露杭州。
- 日志断言：`memory_changed.change=forgotten`（若产品未提供该能力，记录 `capability_missing` 并跳过，不算失败）。

### A10 工作区隔离
- 步骤：工作区 W1 说「我们团队用 GitLab」→ 切到工作区 W2 问「我们团队用什么代码托管？」
- 预期回答：W2 不知道，不暴露 GitLab。
- 日志断言：W2 的 `injected.ids` 不含 W1 条目；`recall_candidates` 的 candidate_ids 不出现该 id。

### A11 记忆总开关
- 步骤：设置里关闭 `memoryEnabled` → 新会话问 A01 的城市 → 再打开 → 重问。
- 预期回答：关闭时不注入（回答不知道/通用），打开后恢复杭州。
- 日志断言：关闭期间无 `injected`（或 `hint=false` 且 ids 为空）；打开后 `injected.ids` 恢复。

### A12 长会话后回忆
- 步骤：连续 20+ 轮讨论不同话题，其中第 3 轮说「我对花生过敏」→ 结束会话 → 新会话问「点餐要注意什么？」
- 预期回答：避免花生。
- 日志断言：期间出现多次 `extraction_completed`；最终 `injected.ids` 含过敏条目。

## 三、B 组：边界与异常（8 条）

### B01 单条寒暄不触发提取
- 步骤：只发「你好」。
- 预期：正常回复，不产生记忆。
- 日志断言：`extraction_skipped.reason=signal_threshold`（或 `no_new_messages`），无 `memory_changed`。

### B02 超长消息
- 步骤：粘贴一段 >10,000 字的内容，其中埋一句「我的工号是 A123」。
- 预期：正常回复，不崩溃。
- 日志断言：日志中 `content` 被截断（`…`），单行长度受 `maxContentChars` 约束；不出现整段原文。

### B03 纯表情/空白
- 步骤：发「🙂🙂🙂」和只有空格的空消息（若前端允许）。
- 预期：不崩溃；空消息被拒或忽略。
- 日志断言：无异常栈；跳过原因可读。

### B04 两会话并发
- 步骤：同时开两个会话，分别说「我喜欢喝美式」「我喜欢喝拿铁」→ 各自提问。
- 预期：各自回答自己的偏好，不串。
- 日志断言：两 session 的 `batch_id` 不同；`injected.ids` 不交叉。

### B05 提取中重启客户端
- 步骤：说完事实后立刻关闭桌面端（提取未完成）→ 重开 → 等提取 → 提问。
- 预期：状态一致，记忆不丢或可重试成功。
- 日志断言：重启后可见 `extraction_completed` 或 `replay`；不得出现「批次永久 pending」。

### B06 模型服务不可用
- 步骤：临时改错 API key / 断网，触发提取。
- 预期：对话仍可用，明确提示提取失败。
- 日志断言：`extraction_failed.error_code` 非空；不伪装成 `completed`。

### B07 日志文件不可写
- 步骤：把 `memory.log` 所在目录设为只读（或把 `filePath` 指向不可写路径）后启动。
- 预期：对话不中断；记忆功能正常。
- 日志断言：仅一条 sink 不可用告警；stderr 仍有记忆日志；`injected` 正常。

### B08 关闭正文记录
- 步骤：设 `includeContent=false` 重启，触发一次记忆与一次提问。
- 预期：功能正常。
- 日志断言：日志只出现 id/长度（`<omitted N chars>`），不出现正文；`memory-events.jsonl` 中 `content` 同理。

## 四、通过标准

| 范围 | 标准 |
| --- | --- |
| A 组 | 12/12 通过 |
| B 组 | 8/8 通过（A09 若产品未提供遗忘能力，按声明跳过） |
| 阻断性失败 | 任一「跨工作区泄漏」「删除后仍暴露」即整体不通过 |
| 可观测性 | 每条用例的日志断言必须成立；日志缺失按失败计（说明观测层未覆盖） |

## 五、记录模板

```
用例 ID：
开始时间：
实际回答：
日志关键行：
  - extraction_completed: memories=? skipped=?
  - memory_changed: change=? memory_id=?
  - injected: ids=? dropped=? hint=?
截图：
判定：PASS / FAIL（原因）
```

## 六、常见排查

| 现象 | 先看什么 |
| --- | --- |
| 提问没回忆 | `recall_candidates` 是否有候选 → 有候选但 `injected` 为空，看 `dropped`/`hint`（预算或去重） |
| 记不住新事实 | `extraction_started` 是否出现 → 没有则看 `extraction_skipped.reason` |
| 记住但用错旧值 | `memory_changed.change` 是否为 `updated/superseded`；`injected` 是否混入旧条目 |
| 串工作区 | `injected.ids` 的来源 workspace；`recall_candidates` 是否命中不可见集合 |
| 日志文件为空 | 配置 `memoryLogging.enabled/fileEnabled`；sink 告警；`filePath` 是否可写 |
