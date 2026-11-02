---
artifact: spec
route: source-driven-development -> brainstorming
skills:
  - source-driven-development
  - brainstorming
  - observability-and-instrumentation
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/brainstorming/SKILL.md
  - .claude/skills/observability-and-instrumentation/SKILL.md
source:
  - nanobot/memory/extractor.py
  - nanobot/memory/retrieval/trace.py
  - nanobot/memory/context.py
  - nanobot/agent/context.py
  - nanobot/config/schema.py
  - .ai-runtime-artifacts/specs/2026-11-02-memory-evaluation-platform-spec.md
  - .ai-runtime-artifacts/contracts/2026-11-02-contract-memory-evaluation.md
created_at: 2026-11-02
status: approved
approved: true
approved_at: 2026-11-02
approval_evidence: 用户选择「方案 + 本轮直接实现」并回复「执行」「直接实现快点」。
---

# 记忆链路可观测性与两套测试集方案

## 1. 审查结论

| 环节 | 现状 | 缺口 |
| --- | --- | --- |
| 提取 | `extractor.py` 有 flush/idle/批次/写入计数日志 | 无本批输入（seq 区间、条数）与产出事实明细；无 trace 关联 |
| 检索 | `retrieval/trace.py` 阶段完整（start/recall/dedupe/rank/injection/hybrid） | 开关是模块常量 `ENABLED`；截断硬编码 60 字/8 条；只写 stderr；无结构化事件 |
| 注入 | `memory/context.py::select_memory_context` 算出 included/dropped/hint | **零日志**：最终拼进 system prompt 的块、被去重/预算丢弃的条目均不可见 |
| 落盘 | 仅 stderr；后台 gateway 由进程管理器重定向到 gateway.log | 前台/桌面模式无文件；无按链路检索的专用文件 |
| 配置 | `memory_enabled` 等有 provider 热切换模式 | 无任何日志配置项 |

## 2. 设计

### 2.1 统一观测层

新增 `nanobot/memory/obs.py`：一次 `emit(kind, message, **fields)` 同时产出

- 文本：loguru sink（stderr + 可选 `logs/memory.log`，轮转/保留）；
- 结构化：JSONL（可选 `logs/memory-events.jsonl`，超限轮转 `.1`）。

三个环节只调 `obs.emit`，文本格式留在 `trace.py`，避免两套埋点漂移。日志失败全部吞掉，不影响记忆链路。

### 2.2 事件与字段

事件 kind：`extraction_started/completed/skipped/failed`、`memory_changed`、`retrieval_started`、`recall_candidates`、`retrieval_deduped`、`reranked`、`injected`、`retrieval_skipped`、`retrieval_elapsed`、`retrieval_time_browse`、`retrieval_hybrid`。前 6 个与评测契约 §4 词汇一致；其余为产品排障超集，消费方按已知 kind 过滤。

必备维度：`ts`（带时区）、`kind`、`trace_id`（同轮检索→注入共享）、`session`/`batch_id`、`workspace_id`、输入摘要（query/seq 区间/条数/字符数）、输出摘要（ids/计数/截断正文）、`status|reason|error_code`、`elapsed_ms`、`content_truncated`。

### 2.3 配置（`agents.defaults.memoryLogging`）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | true | 总开关 |
| `consoleLevel` | INFO | 事件级别（DEBUG 需进程日志也为 DEBUG） |
| `includeContent` | true | 关后只记 id/长度 |
| `maxContentChars` | 240 | 单条正文截断（40–2000） |
| `maxItems` | 8 | 每段明细上限（1–50） |
| `fileEnabled` | true | 写 `logs/memory.log` |
| `filePath` | ""（默认 `logs/memory.log`） | 支持 `~` |
| `eventsEnabled` | false | 写 JSONL |
| `eventsPath` | ""（默认 `logs/memory-events.jsonl`） | |
| `rotation` / `retention` | `10 MB` / `7 days` | 文本轮转 |
| `eventsMaxMb` | 20 | 事件单文件上限，超限转 `.1` |

生效：provider 闭包读取当前 config 对象（与 `memory_enabled` 同一模式），同一对象被就地修改时免重启；直接改配置文件后需重启 gateway 才重新加载。sink 参数变化自动重建；未装配场景（库调用/单测）只走 stderr 不落盘。默认路径与 `gateway.log` 同目录（`get_logs_dir()`）。隐私：正文属用户数据，`includeContent=false` 可脱敏，分享日志前需人工检查。

### 2.4 验收

一次「说事实 → 新会话提问」后，`memory.log` 可见 `extraction_started/completed`、`memory_changed`、`recall_candidates`、`reranked`、`injected`（检索 + 组装两侧）；JSONL 每行可解析且同轮 `trace_id` 一致；`enabled=false` 两文件均不新增。

## 3. 测试集一：桌面客户端手动测试集

> 独立可执行文件：`tests/memory/manual_memory_test_cases_v2.md`（20 条，含步骤/预期/日志断言/记录模板）。

**目标**：真实桌面客户端中人工主动询问，验证「回答对不对」+「日志能否解释为什么」。非目标：统计显著性、多租户。

**前置**：独立 config/workspace；`memoryEnabled=true`、`memoryLogging` 开启且 `eventsEnabled=true`；每轮记录日志行数与记忆条数；等待 `extraction_completed` 而非固定 sleep。

**用例字段**：ID / 前置 / 步骤 / 预期回答 / 日志断言 / 判定。

### A 组：典型对话流程

| ID | 场景 | 预期 | 日志断言 |
| --- | --- | --- | --- |
| A01 | 首次说事实 → 新会话问 | 正确回忆 | `extraction_completed.memories≥1`；`memory_changed.add`；`injected.ids` 含该条 |
| A02 | 偏好设置 → 推荐类提问 | 遵循偏好 | `memory_changed.type=preference`；`injected` 非空 |
| A03 | 事实更新（猫→狗） | 当前答狗 | 第二轮 `change∈{update,superseded}` |
| A04 | 更新后问历史 | 答猫且标明过去 | `injected` 可含旧值，回答有时态 |
| A05 | 关窗重开（跨会话） | 仍记得 | 新 session 的 `injected` 含该条 |
| A06 | 多事实综合 | 不编造缺失项 | `injected` 命中 ≥1，无凭空细节 |
| A07 | 问未说过的事 | 明确不知道 | 无 `memory_changed`，`injected` 空/无关 |
| A08 | 无关闲聊后回问 | 记忆不丢 | `recall_candidates` 有候选，`injected` 不塞无关项 |
| A09 | 删除/遗忘请求（若支持） | 不再暴露 | `memory_changed.change=forgotten`（或明确 capability_missing） |
| A10 | 两个工作区同一问题 | 不串记忆 | `injected.ids` 只含本工作区 id |
| A11 | 关闭记忆开关后提问 | 不注入 | 无 `injected`；重开恢复 |
| A12 | 长会话（20+ 轮）后提问 | 早期事实仍可召回 | `extraction_completed` 多次；`injected` 含目标 id |

### B 组：边界与异常

| ID | 场景 | 预期 | 日志断言 |
| --- | --- | --- | --- |
| B01 | 单条「你好」 | 不触发提取 | `extraction_skipped.reason=signal_threshold` |
| B02 | 超长消息（>10k 字） | 正常回复 | 截断字段 `content_truncated=true`，无超长行 |
| B03 | 纯表情/空白 | 不崩溃 | 无异常栈；跳过原因可读 |
| B04 | 两会话并发说不同事实 | 各自正确 | 两 session 的 batch_id 不交叉 |
| B05 | 提取中关闭应用再打开 | 状态一致 | 重启后 `extraction_completed` 或 replay 可见 |
| B06 | 模型服务不可用 | 明确失败提示 | `extraction_failed.error_code` 非空，不伪装成功 |
| B07 | 记忆文件被占用/只读 | 对话不中断 | warning 一次，`injected` 仍工作 |
| B08 | `includeContent=false` | 日志无正文 | 文本仅 id/长度；JSONL 无 content 字段正文 |

**执行方式**：按 A01→A12、B01→B08 顺序执行；每条记录日志关键行与截图；失败用例保留 `memory.log`/`memory-events.jsonl` 片段。

**通过标准**：A 组 12/12、B 组 8/8 通过；任一「串工作区」「删除后仍暴露」为阻断性失败；日志缺失本身算失败（可观测性未达标）。

## 4. 测试集二：记忆测评平台自动化测试集

> 独立数据文件：`tests/memory/memory-eval-smoke-20.jsonl`（20 条，已用平台 `import_dataset` 实测导入为 ready）。

**目标**：机器可执行、可重复、可比较。判分只看结构化结果与 `memory.event`，不解析人读日志。

**用例结构**（`memory-scenario/v1`，字段以契约 §1 为准）：

```json
{
  "schema_version": "memory-scenario/v1",
  "scenario_id": "pref-update-001",
  "revision": 1,
  "split": "regression",
  "cluster_id": "conv-001",
  "tags": ["preference", "update"],
  "scope": {"subject_id": "u01", "workspace_id": "w01"},
  "events": [
    {"type": "session", "id": "s1", "at": "2026-11-01T09:00:00+08:00", "messages": [
      {"id": "m1", "role": "user", "content": "我喝拿铁，加糖"},
      {"id": "m2", "role": "assistant", "content": "记住了"}]},
    {"type": "settle", "after_session": "s1"}
  ],
  "probes": [{"id": "q1", "fresh_session": true, "at": "2026-11-02T10:00:00+08:00", "question": "帮我点杯咖啡"}],
  "gold": {"q1": {"required_facts": [{"key": "coffee", "value": "拿铁", "evidence_message_ids": ["m1"]}],
                   "forbidden_current_values": [], "answerability": "answerable"}}
}
```

**集合构成**：冒烟 20 场景（开发时单次）；核心回归 120 场景（10 类 × 12，发布候选 ×3）；LongMemEval 固定分层子集（里程服用全量 500）。10 类：稳定事实、偏好约束、同义问法、时间有效性、知识纠正、多会话综合、信息不足拒答、无关干扰、明确遗忘、工作区隔离。

**输入/输出规范**：

| 方向 | 内容 |
| --- | --- |
| 输入（执行端） | scenario 去掉 gold/split/许可后的 execution 视图：scope/events/probes/reference_state + track + candidate |
| 输入（评分端） | 私有 gold：required_facts/forbidden_current_values/answerability/expected_absence/evidence |
| 输出（每 probe） | answer、召回排序与来源、实际注入上下文、usage/timing、指标值+分子分母+status、证据引用 |
| 输出（事件） | `memory.event`：session_closed、extraction_completed/skipped/failed、memory_changed、recall_candidates、reranked、injected、probe_answered 等 |

**预期结果与通过标准**：

| 轨道 | 主判据 | 通过标准 |
| --- | --- | --- |
| extraction | 原子事实一对一匹配；P/R/F1 | closed_world 下 F1 ≥ 基线且无退化；targeted 只报覆盖，不报全量 F1 |
| retrieval | Evidence Recall@k、MRR、注入覆盖率 | 注入覆盖率按**最终上下文**重算，不沿用召回值；零 gold 记 not_applicable |
| end_to_end | `qa_accuracy`（规则优先，Judge 需校准）、`grounded_memory_success` | 必需事实全覆盖、无 forbidden 当前值、无 scope 违反；关键失败（泄漏/删除复活）直接 blocked |
| 通用 | 稳定性 `repeat_stability`、覆盖 `measurement_status` | 同场景 K 次全过才算稳定；技术缺失记 incomplete，不填 0 |

聚合：场景 macro 为主，类别 macro 与题级 micro 辅助；区间用 cluster bootstrap（seed=42、2000 轮、95%），独立 cluster<20 不展示区间。

**执行方式**：`POST /api/eval/v1/memory/experiments`（或 CLI）→ 每 trial 独立数据根/session/workspace → `settle` 完成屏障（completed/skipped_by_policy/failed/timeout 四分）→ 新 session probe → 评分；`model_mode=real|scripted` 不混算；120×3 分批（每批 ≤40 场景）聚合，不缩重复次数。

**与日志的关系**：`injected`/`recall_candidates`/`memory_changed` 事件是注入覆盖率与阶段定位的证据源；人读 `memory.log` 只用于失败回放，不参与判分。

## 5. 本轮实现边界

- 新增 `nanobot/memory/obs.py`；改 `config/schema.py`、`retrieval/trace.py`、`memory/extractor.py`、`agent/context.py`、`gateway_runtime.py`、`cli/agent.py`、`cli/commands.py`（serve）、`evaluation/runtime_factory.py`（评估关闭落盘）；新增 `tests/test_memory_obs.py`。
- 不改记忆算法、不改数据库 schema、不改 WebUI。

## Next

- 实现完成 → 跑 `pytest tests/test_memory_obs.py` + 相关记忆测试 → 出 `verification-lite`。
- 需要调整事件字段/默认值 → 直接说修改意见。
