---
artifact: verification
route: verification-before-completion
plan: .ai-runtime-artifacts/plans/2026-10-25-evaluation-observability-plan.md
created_at: 2026-10-25
status: complete
scope: skill / team / 普通对话 三类目标的真实评估回归
---

# 三类目标测试集合与实测结果

用户要求：为 skill、团队、普通对话各设计一个测试集合，用真实评估功能跑一遍，并以截图验收。
本轮**调用了真实模型**（`model:MiniMax-M3.1-Flash` / custom-minmax），数据落在用户本机评估库
`~/.nanobot/evaluation`，通过 WebUI 与离线 HTML 报告双路核对。

## 1. 设计的三个测试集合

| 集合 | 目标 | suite id | run id | 用例 |
| --- | --- | --- | --- | --- |
| 基础对话回归 · 普通对话 | `agent:general-assistant` | `943eb08f-8c9c-45fa-8d2b-1fee56312349` | `bbc4196a-f618-44fb-9d22-f95313b4857f` | 读取固定标识 / 算术与排除 / 固定口令复述 |
| 技能口令回归 · Skill | `skill:eval-smoke` | `4f9d2dc1-d782-4fc1-a432-4541bf4144c4` | `6c4d7934-3b49-4d76-99aa-f3a883adf7bc` | 触发口令 / 未触发不泄露 |
| 团队写作回归 · Team | `team:writing-team` | `563351f9-2e09-4047-9087-7c5a735de528` | `df2a2d67-ecfb-46e5-95e0-65135b5f6331` | 一句话写落叶 / 提纲含指定词 |

用例断言全部是**确定性规则**（`answer_contains` / `answer_not_contains` / `tool_called`），
不挂 Judge：结论可复算，不依赖模型打分。普通对话的「读取固定标识」还要求真的调用 `read_file`，
让画布上有真实工具节点。

## 2. 实测结果

| 集合 | 状态 | 通过 / 失败 | Token（input/out/total） | 物理请求 | 轨迹 |
| --- | --- | --- | --- | --- | --- |
| 普通对话 | completed | 3 / 0 | 13,137 / 95 / 13,232 | 4 | 4 节点 6 边，coverage 全 complete |
| 技能 | completed | 2 / 0 | 10,237 / 139 / 10,376 | 3 | 4 节点 6 边，usage 归属 unavailable |
| 团队 | completed | 2 / 0 | — | — | **0 节点，无轨迹** |

离线 HTML 报告三份均生成成功且自包含（无 `<script>`、无外链）：
`report-agent.html` 22 KB、`report-skill.html` 20 KB、`report-team.html` 15 KB。

## 3. 截图证据

| 文件 | 内容 |
| --- | --- |
| `01-overview.png` | 评估总览：三套集合都已在系统里，指标带来源标注 |
| `02-runs-list.png` | 运行列表 |
| `03-agent-run-detail.png` | 普通对话运行详情（3/3 通过） |
| `04-agent-canvas.png` | 普通对话执行画布：general-assistant / MiniMax-M3.1-Flash / read_file 节点与边 |
| `05-node-inspector.png` | 节点检查器：真实 agent_id、execution_id、call_id、耗时、证据事件 ID |
| `06-tool-calls.png` | 工具调用表：搜索、状态下拉、分页计数「1 loaded of 1 total」 |
| `07-skill-canvas.png` | 技能画布 + 覆盖度降级提示 |
| `08-team-canvas.png` | 团队目标：无轨迹时的诚实空态 |
| `09-report-*.png` | 三份离线 HTML 报告渲染截图 |

## 4. 本轮暴露的真实缺口

1. **团队目标没有轨迹**：`_execute_team` 由 gateway 注入的 runner 在**进程内**执行，
   不起 worker 子进程，因此不写 Trace（attempt 目录里 `trace/` 为空、无 manifest、无 worker.log）。
   后果：团队运行的画布、工具、Token 全部为空。页面没有假装有数据，而是显式降级
   （`Trace coverage: Partial` / `Identity coverage: Unavailable` /
   `Coverage notes: identity_unavailable, usage_attribution_unavailable, trace_not_sealed`）。
   这是**既有架构限制**，不是本次改动引入；要让团队也可观测，需要在团队 runner 上挂 TraceCollector。
2. **技能运行缺智能体归属**：skill 目标的事件里 `agent_id` 为空，所以
   `usage_attribution` 降级为 `unavailable`（Token 总量仍由物理请求账本给出）。
3. **节点检查器的 Agent 名称为空**：`general-assistant` 只有 id，没有 name，
   检查器显示 `Agent: —`、`Agent ID: general-assistant`。
4. **团队运行的 Token 未进观测账本**：团队 runner 的 outcome 里带 `usage`，但只写进 attempt result，
   轨迹派生的 Token 主账本看不到它。

## 5. 复现方式

```bash
# 三个集合已在评估系统里；重跑同名集合即可复现
curl -s -X POST http://127.0.0.1:8790/api/eval/v1/auth/login \
  -H 'Content-Type: application/json' -d '{"passphrase":"<webui bootstrap secret>"}' -c /tmp/eval-cookie
# 之后可 GET /api/eval/v1/runs/{run_id}、/attempts/{id}/execution、/runs/{id}/usage、/runs/{id}/report
```

本轮为不干扰用户正在运行的 gateway，观测接口用一个**只读旁路**进程（复用同一评估库、
不启调度器）提供；旁路与 `EVAL_PORT` 临时改端口均已还原（`EVAL_PORT` 已回到 8790，与 HEAD 一致）。
