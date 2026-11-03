---
artifact: verification-lite
route: brainstorming -> verification-before-completion
skills:
  - brainstorming
  - verification-before-completion
skills_evidence:
  - /Users/mima0000/.agents/skills/brainstorming/SKILL.md
  - /Users/mima0000/.agents/skills/verification-before-completion/SKILL.md
source:
  - .ai-runtime-artifacts/specs/2026-11-01-memory-eval-spec.md
  - .ai-runtime-artifacts/research/2026-11-01-memory-eval/corpus.json
  - .ai-runtime-artifacts/research/2026-11-01-memory-eval/cases.jsonl
created_at: 2026-11-01
tier: 1
---

# 记忆能力调研与标注集设计核查

## 结论

完成源码调研、40 题合成标注集与本地手测设计。数据结构和证据引用核查通过，相关既有测试 35 项通过。**未进行真实模型质量评测**，没有 Recall/MRR/回答正确率等效果分数。

## 交付范围

- `.ai-runtime-artifacts/specs/2026-11-01-memory-eval-spec.md`：能力证据、40 题速览、启动说明、手测流程、评分与归因。
- `.ai-runtime-artifacts/research/2026-11-01-memory-eval/corpus.json`：8 段历史、26 条用户消息、30 条语义事实及非事实边界。
- `.ai-runtime-artifacts/research/2026-11-01-memory-eval/cases.jsonl`：40 题逐题参考答案、完整性要求、错误条件与证据。
- `.ai-runtime-artifacts/stack/2026-11-01-memory-eval-stack.md`：技术栈核对。
- 本文件：验证证据及限制。

业务源码与配置未修改，未安装依赖，未启动服务，未读取用户模型密钥或个人记忆内容。工作区原有 `memory/state.db` 修改及其他既有产物保持原样，没有进行清理、回滚或提交。

## 源码核查

| 核查项 | 证据 | 结论 |
| --- | --- | --- |
| 默认开关与时间 | `nanobot/config/schema.py` | memoryEnabled=false；memoryIdleSeconds=120；memorySearchBackend=fts5 |
| 写入时机 | `nanobot/agent/hooks/memory_extraction.py`、`nanobot/memory/extractor.py` | idle 定时器＋增量信号门槛；120 秒是开始抽取阈值，不是保证完成时限 |
| 网关接线 | `nanobot/cli/gateway_runtime.py` | 装配 MemoryServices、MemoryStoreAdapter、RetrievalEngine、读写开关 |
| 回答注入 | `nanobot/agent/context.py` | 新自动检索之外还有旧 MEMORY.md/身份上下文，评分须区分来源 |
| 更新边界 | `nanobot/memory/profile_extractor.py`、`extractor.py` | 同 subject/predicate 可覆盖，冲突有保留逻辑；不承诺完整历史版本 |
| 评测中心边界 | `nanobot/evaluation/datasets.py`、`fixtures.py`、`runtime_factory.py` | 普通多轮导入，memory_seed 写旧文件；未等价接线网关新记忆链路 |
| 启动参数 | `nanobot/cli/webui.py::webui` | 支持 --config、--workspace、--port、--gateway-port、--yes |

外部维度参考 LongMemEval 官方仓库：<https://github.com/xiaowu0162/LongMemEval>。没有复用其题目或声称本集是官方基准。

## 已运行验证

### 定向既有单测

```bash
uv run --no-sync pytest tests/memory/test_idle_signal_threshold.py tests/memory/test_hook_idle_timer.py tests/memory/retrieval/test_engine.py tests/memory/test_repository_workspace_filter.py -q
```

实际输出：

```text
...................................                                      [100%]
35 passed in 1.00s
```

该结果验证部分触发、信号门槛、检索编排与工作区过滤的工程行为，不证明真实 LLM 的抽取或回答质量。

### 数据断言

使用 `uv run --no-sync python` 执行内联 JSON/JSONL 结构与关系断言，退出码 0，输出：

```text
PASS: 8 sessions / 26 user turns / 30 facts (27 current + 3 historical)
PASS: 40 unique cases / 32 answerable + 8 unanswerable / 5 categories x 8
PASS: dev 8 / held-out test 32 (26 answerable + 6 unanswerable)
PASS: all fact, evidence, distractor and supersession references resolve
PASS: JSON/JSONL parse; Markdown required front matter and UTF-8 checks
```

断言覆盖：连续唯一 Q01–Q40、每类 8 题、dev/test 数量、事实 ID 与用户消息引用存在、更新目标存在、可回答题有 gold 与证据、无答案题 gold 为空、评分条件非空，以及设计/stack 的必填 front matter。

## 未验证与限制

- 没有真实模型调用，没有执行指南中的启动命令或浏览器操作；启动方法依据当前 CLI 源码。
- 没有验证用户本地凭证、运行端口和实际开关状态。
- 没有进行第二标注员复核；合成集是设计初稿，不是真实用户金标集。
- 没有新增自动跑批程序，JSONL 不是现有评测中心的直接导入格式。
- 没有运行全仓库测试、lint、typecheck 和构建：本次只新增数据与文档，未变更业务代码；针对调研结论运行上述既有测试。
- 手工连问会改变共享记忆状态；只能先作为诊断，正式比较需要按题恢复一致快照。

## 后续

可直接依照 spec 第 6 节手测。若后续要求机器化回归，再为快照回放、真实记忆 ID 映射、按层指标采集制定实施计划。
