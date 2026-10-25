---
artifact: spec
route: api-and-interface-design
skills: [api-and-interface-design]
skills_evidence: [.claude/skills/api-and-interface-design/SKILL.md]
source:
  - .ai-runtime-artifacts/contracts/2026-10-15-contract-agent-evaluation.md
  - nanobot/evaluation/schemas.py
  - nanobot/evaluation/worker.py
  - nanobot/evaluation/usage.py
  - nanobot/evaluation/evidence.py
  - nanobot/llm_usage/models.py
created_at: 2026-10-25
status: proposed
approved: false
---

# 评估执行观测与 Token 接口增量契约

本契约是待批准实施计划的接口依据，尚未实现。沿用 `/api/eval/v1`、snake_case、既有鉴权、错误结构和 Trace 分页；不另建账户体系。设计与演示数据不证明现有服务已经支持这些字段。

## 1. 标识与真实归属

| 字段 | 规则 |
| --- | --- |
| agent_id / agent_name | 被实际加载的档案身份与冻结名称；未知为 null。不能从模型名或工具名反推 |
| execution_id | 每次智能体执行的唯一 ID；同一 agent 可有多个 execution，不能按 agent_id 合并执行 |
| parent_execution_id | 来自真实 spawn / delegate 的显式关系；根执行为 null。历史记录无法还原时为 null，并标记关系缺失 |
| activity_id | 一次可展示的活动标识；同一 execution 可以先计划、后汇总，画布不把两次活动计为两个 agent |
| call_id | 一次物理工具或模型请求唯一标识；重试必须生成新 ID，并可携带 retry_of_call_id |
| model_call_id | 模型物理请求 ID；不能以模型名称、时间戳、Token 数值去重 |
| event_id / seq | 沿用 Trace；分页回放同一 event_id 不能产生新调用 |
| role / scope | role=agent/judge；scope=main/auxiliary/unknown；与智能体身份分开 |
| usage_source | reported/estimated/mixed/unavailable。与现有 LLMCallRecord.source=user/api/cron/dream/system 是不同概念，不混用 |

新增身份优先作为可选 Trace payload 字段，读取旧事件仍合法。若新增 `agent.started` / `agent.completed` / `agent.failed` 等事件，必须同步修改闭集校验、IPC 和历史兼容测试。observer 只在当前 execution 范围生效，不能覆盖全局 provider 监听器。

## 2. 规范化节点、边与覆盖

```typescript
type Coverage = "complete" | "partial" | "unavailable";
type ExecutionNode = {
  id: string;
  kind: "agent_activity" | "tool_call" | "model_call" | "judge" | "output";
  agent_id: string | null;
  agent_name: string | null;
  execution_id: string | null;
  parent_execution_id: string | null;
  call_id: string | null;
  model: string | null;
  status: "running" | "succeeded" | "failed" | "cancelled" | "unknown";
  started_at: string | null;
  duration_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  usage_source: "reported" | "estimated" | "mixed" | "unavailable";
  evidence_refs: string[];
};
type ExecutionEdge = {
  id: string;
  from: string;
  to: string;
  kind: "delegation" | "invocation" | "sequence" | "return";
};
type ExecutionPage = {
  data: { nodes: ExecutionNode[]; edges: ExecutionEdge[] };
  next_after_seq: number;
  has_more: boolean;
  sealed: boolean;
  coverage: {
    trace: Coverage;
    identity: Coverage;
    usage_attribution: Coverage;
    reasons: string[];
  };
};
```

节点 ID 稳定，分页后的同 ID 节点是更新而不是重复添加。`sequence` 只能用于同 execution 内有事件依据的顺序；跨 execution 不凭时间创建调用、委派或返回边。孤立节点归入“关系未采集”区域。缺父记录不创建虚构父节点。工具本体 Token=null，不把工具返回长度重复计为模型用量。

## 3. HTTP 与分页

| 方法 / 路径 | 输入 | 输出 / 行为 |
| --- | --- | --- |
| GET `/attempts/{id}/execution` | `after_seq` 默认 0，`limit` 默认 200、范围 1–1000 | ExecutionPage；首次响应不声称未读完的图是完整图；共享现有 Trace 可访问性检查 |
| GET `/runs/{id}/usage` | `group_by=role/agent/model`，默认 role；`after_key` 可选不透明游标；`limit` 默认 50、范围 1–200 | `{data, totals, next_after_key, coverage}`；totals 为该 run 的唯一物理请求主账本，不是当前页求和 |
| GET `/runs/{id}/report` | 无任意文件路径参数 | 当前运行的自包含 HTML；content-disposition 固定安全文件名 |
| GET `/reports/summary?suite_id=...` | 保留已有入口 | 数据集历史汇总；明确选取范围，列出 selected_run_count、total_run_count 和截断情况 |

usage 行字段：`key, agent_id, agent_name, role, provider, model, input_tokens, output_tokens, total_tokens, physical_requests, missing_usage_calls, source, coverage`。按 agent 分组时保留 Judge 单独分组与未归属组；历史归属不明的数据不会丢弃或全算给主智能体。混合来源不改称实报；分组表缺失值仍为 null。Agent 与 Judge 不共享去重桶，跨 observer 重发依据稳定调用 ID 去重；真实重试的新 ID 正常累加。

同一查询分页使用固定排序，游标绑定 run_id 和 group_by；非法游标返回 400，参数格式错误 422，不存在 404，未登录 401、无权访问 403。所有错误沿用当前服务器 `{error:{code,message}}` 及中间件返回的扩展字段，不临时另定格式。

## 4. 大量工具的归并与查询

- 分组键为 `(attempt_id, execution_id, phase_id, tool_name)`。没有 phase_id 时仅合并同 execution 的连续同类调用块；不能把相隔很远、执行次序不同的同名工具强行揉在一起。
- 分组是派生展示视图，组 ID 与所有子 call_id 可关联；`count/succeeded/failed/running/unknown` 按真实调用计数。
- 默认超过 40 个工具节点启用分组，默认画布最多展示 80 个节点。用户展开一个分组不会连带展开全部分组。接近上限时进入该组局部子图，保留面包屑。
- 分组明细通过 `GET /attempts/{id}/tool-calls?execution_id=...&phase_id=...&name=...&status=...&q=...&after_key=...&limit=50` 分页查询；响应 `{data,next_after_key,total_count}`，limit 范围 1–200。`q` 只匹配工具名、call_id、智能体名称，不默认搜索敏感原始参数。
- 未加载的错误不会被当作零；服务端分组统计覆盖全部可用事件，响应同时标注 trace coverage。UI“只看异常”可定位所在分组，再打开真实失败调用，不把失败被重试后的成功覆盖掉。
- 请求完成分页合并和取消均有测试；10,000 次调用用于局部读取与节点上限测试，不将全量大 JSON 一次塞入浏览器。

## 5. 导出边界

报告与系统消费同一派生读模型。单运行和数据集历史标题、范围、分母不可混淆。套件最新记录按 `case_id` 关联，固定按 created_at 与 run_id 排序，保留 revision，不按名称合并；历史失败可另列“历史出现失败”，不能篡改最新结论。

报告包含：范围配置、摘要、分组流程图、智能体表、工具分组统计、异常调用表、Token 分账、用例结果、口径说明。每个用例的流程图单独标范围，不能把一个用例的调用数冒充全轮。

少量调用使用完整 SVG；大量调用使用分组 SVG，逐次明细放在其后分区表中，按每个表最多 200 行分段。10,000 行以上报告仍有明确已导出行数、总行数与截断说明；不无声删除明细。默认导出不携带任意大原始 payload 或用户日常会话全文，调用参数/结果按字段白名单和当前脱敏规则生成摘要。

报告无 CDN、远程字体、运行时脚本和网络请求；静态 SVG 不含 foreignObject、外链、事件属性。文本、SVG 标签和属性均转义。打印重复表头，避免一行拆页；原生 details 可辅助阅读，但关键证据、失败和指标不得仅隐藏在 details 中。

---

# 附录 A：实施对齐的规范形状（2026-10-25 落地版）

本附录是实施阶段的唯一对齐口径，与上面 §2/§3 的类型定义一致；实现必须产出这里列出的字段名，
前端 `webui/src/lib/evaluation-api.ts` 的类型已经按此实现，不再改动字段名。

## A.1 GET /attempts/{id}/execution

响应体即 `ExecutionPage`：

```json
{
  "data": {
    "nodes": [
      {
        "id": "tool:<attempt_id>:<call_id>",
        "kind": "agent_activity | tool_call | model_call | judge | output",
        "agent_id": null,
        "agent_name": null,
        "execution_id": null,
        "parent_execution_id": null,
        "call_id": null,
        "model": null,
        "status": "running | succeeded | failed | cancelled | unknown",
        "started_at": null,
        "duration_ms": null,
        "input_tokens": null,
        "output_tokens": null,
        "total_tokens": null,
        "usage_source": "reported | estimated | mixed | unavailable",
        "evidence_refs": [],
        "label": null,
        "tool_name": null,
        "phase_id": null,
        "input_summary": null,
        "output_summary": null
      }
    ],
    "edges": [{"id": "...", "from": "...", "to": "...", "kind": "delegation | invocation | sequence | return"}]
  },
  "next_after_seq": 0,
  "has_more": false,
  "sealed": false,
  "coverage": {"trace": "complete | partial | unavailable", "identity": "...", "usage_attribution": "...", "reasons": []}
}
```

规则：
- `kind` 只允许上述 5 个值；`tool_group` 只存在于前端投影层，不出现在服务端响应里。
- 工具节点的 Token 三项恒为 `null`；模型节点的 Token 来自真实 usage，未知为 `null`。
- `status` 不靠颜色单独表达；缺失父执行时 `parent_execution_id` 为 `null` 且 coverage.identity 降级。
- 分页：同一 `after_seq` 请求返回严格递增事件对应的节点；`next_after_seq` 是下一次请求的入参。
- `sealed` 表示 Trace 已完整（不再有后续事件），来自 Trace 存储的真实状态。

## A.2 GET /attempts/{id}/tool-calls

查询参数：`execution_id, phase_id, name, status, q, after_key, limit(1..200, 默认 50)`。

响应行字段（**服务端必须产出这些名字**）：

```json
{
  "call_id": "tool-call-19",
  "attempt_id": "attempt-1",
  "execution_id": null,
  "phase_id": null,
  "tool_name": "web_fetch",
  "agent_id": null,
  "agent_name": null,
  "status": "running | succeeded | failed | cancelled | unknown",
  "started_at": null,
  "duration_ms": null,
  "input_summary": null,
  "output_summary": null,
  "error": null,
  "retry_of_call_id": null
}
```

响应体：`{"data": [...], "next_after_key": null, "total_count": 0, "coverage": "complete | partial | unavailable"}`。

- `input_summary` / `output_summary` 是白名单字段的**摘要文本**（截断 + 不含密钥原文），不是完整 payload。
- `q` 只匹配 `tool_name`、`call_id`、`agent_name`。
- `total_count` 是过滤后全量条数，与当前页长度无关。

## A.3 GET /runs/{id}/usage

查询参数：`group_by=role|agent|model`（默认 role）、`after_key`、`limit(1..200, 默认 50)`。

响应体：

```json
{
  "data": [
    {"key": "...", "agent_id": null, "agent_name": null, "role": "agent | judge",
     "provider": null, "model": null,
     "input_tokens": null, "output_tokens": null, "total_tokens": null,
     "physical_requests": 0, "missing_usage_calls": 0,
     "source": "reported | estimated | mixed | unavailable", "coverage": "complete | partial | unavailable"}
  ],
  "totals": {"input_tokens": null, "output_tokens": null, "total_tokens": null,
             "physical_requests": 0, "missing_usage_calls": 0},
  "next_after_key": null,
  "coverage": {"usage": "...", "attribution": "...", "reasons": []}
}
```

- `totals` 覆盖整轮唯一物理请求主账本，不是当前页求和；`null` 表示未知，0 表示真实为零。
- Agent 与 Judge 不共享去重桶；重放同一 `model_call_id` 不重复计数。
- `group_by=agent` 时 `key` 是 agent_id（缺失为 `unassigned`）；Judge 行单独成组，
  `key` 形如 `judge:<agent_id>`，不与执行智能体合并成一行。

## A.4 GET /runs/{id}/report

- 响应：`text/html; charset=utf-8`，`content-disposition: attachment; filename="<run_id>-report.html"`，
  文件名只允许 `[A-Za-z0-9._-]`，禁止来自用户输入的任意路径。
- 正文是**自包含静态 HTML**：无脚本、无外链、无 CDN、无远程字体、无网络请求；
  不含 `foreignObject`、事件属性、`javascript:` 链接；文本与属性全部转义。
- 报告与系统页面消费同一读模型快照；口径说明必须写明范围、用例数、工具调用数、
  物理请求数、Token 合计与未归属量，且不得把一个用例的调用数冒充全轮。
- 大规模：工具节点 > 40 时输出分组概览 + 异常表 + 分段明细（每表 ≤ 200 行）；
  达到导出预算时必须显式写明「已导出 N / 总 M」与缺失范围，不谎称完整。
- 打印 CSS：表头跨页重复、行不跨页拆分；关键指标、失败与口径说明不得只藏在折叠区。

## A.5 报告渲染模块输入

`nanobot/evaluation/report_view.py` 提供纯函数：

```python
def render_run_report(snapshot: Mapping[str, Any]) -> str
```

`snapshot` 是普通 dict（只含 JSON 可序列化值），键：

| 键 | 内容 |
| --- | --- |
| `run` | `{"id","suite_id","suite_name","status","model","mode","created_at","started_at","finished_at","planned_count","terminal_count"}` |
| `attempts` | 列表，每项 `{"id","position","case_id","case_revision","case_name","execution_status","grading_status","verdict","errors","judge_summary","usage","timing","trace_complete"}` |
| `execution` | 列表，每项 `{"attempt_id","nodes":[A.1 节点],"edges":[A.1 边],"coverage":{...}}` |
| `tool_calls` | 列表，每项字段同 A.2 |
| `usage` | `{"totals": {...}, "rows": [A.3 行]}` |
| `options` | `{"max_tool_rows_per_table": 200, "max_total_tool_rows": 10000, "generated_at": "..."}` |

渲染器不访问数据库、不读环境变量、不发网络请求；所有数字缺失显示 `—`。
