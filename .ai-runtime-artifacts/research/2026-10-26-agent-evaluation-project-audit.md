---
artifact: research-report
route: source-driven-development
skills: [source-driven-development, writing-plans, openai-docs]
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/skills/.system/openai-docs/SKILL.md
source:
  - .ai-runtime-artifacts/specs/2026-10-15-agent-evaluation-spec.md
  - .ai-runtime-artifacts/plans/2026-10-23-evaluation-webui-plan.md
  - .ai-runtime-artifacts/contracts/2026-10-25-contract-evaluation-observability.md
  - .ai-runtime-artifacts/verifications/2026-10-26-evaluation-verification-lite.md
created_at: 2026-10-26
status: complete
---

# 知序自动化评估：项目核查与改造依据

## 核查边界

用户要求调研后写计划。本轮只读源码、既有需求和官方文档，写过程文档；不修改业务，不再次运行模型。
运行结果引用同一会话已完成的 2026-10-26 实测，不冒称新一轮运行。当前分支 `harness/agent-evaluation-calibration`。
工作树已有 gateway_runtime.py 的启动修复及其他未跟踪文件，均保留。

## 1. 需求沿革

- 10-15 已批准 spec 要求完整 Agent、独立启动、隔离 Worker、规则+Judge、人工校准、Trace、效率、版本与异常恢复。
- 10-23 后续计划改为 gateway 内嵌 API + 现有 WebUI；Agent/Skill 走 Worker，团队借 gateway 回调。原先独立入口在当前代码未找到。
- 10-25 增加执行画布、工具调用查询、Token 分组及 HTML 报告；契约前半仍写“未实现”，附录与实际代码已落地，不能只读旧状态字样判断能力。
- 本轮用户给出任务完成、效率、决策、安全、多次运行与比较要求；新计划补齐原目标，并提议加入可复算的重复/比较能力。
- 新计划保持现有集成页面，同时补独立 CLI/API 启动；复用同一 UI 构建，不新增一套 evalui。后续若独立模式缺目标来源，必须显式配置 workspace，不能默默退回共享网关。

## 2. 已有资产：应修复和复用

| 资产 | 文件 | 已有行为 |
| --- | --- | --- |
| 数据与版本 | evaluation/database.py、repository.py、schemas.py | 不可变 case revision、运行快照、事务队列、幂等、软删除、review、gold、calibration |
| 执行 | runtime_factory.py、worker.py、processes.py、ipc.py | 完整 SDK/AgentLoop、隔离路径、受控工具、子进程、Trace、取消与清理 |
| 客观判分 | graders/rules.py | 文本/JSON/文件、工具及参数、禁止工具、次数与迭代限制；True/False/None |
| 语义判分 | graders/judge.py、calibration.py、verdict.py | 无工具 Judge、0–4量表、证据引用校验、指纹、gold确认、未校准待复核 |
| 统计展示 | reports.py、observability.py、report_view.py | 分母口径、质量覆盖、延迟、Token、分页、静态 HTML |
| WebUI | components/settings/evaluation/、lib/evaluation-api.ts | 集合导入导出、发起运行、详情、画布、工具表、Token |

不是从零重建。现有工具用例通过率也不能改名成逐次工具选择准确率，两者分母不同。

## 3. 源码发现与证据等级

“静态确认”表示当前函数直接显示该行为；“待复现”表示需要对照测试，不能写成确定根因。

| 编号 | 发现 | 源码入口 / 证据 | 等级 |
| --- | --- | --- | --- |
| F01 | 团队没有 worker Trace，却强制判分 Trace 完整 | services.py::_execute_team、_grade(trace_complete_override=True) | 静态确认 + 实测零节点仍passed |
| F02 | 团队在用户共享host/workspace执行，不应用case白名单/fixture/记忆种子，模型选择不经过普通Worker配置链 | webui/gateway_services.py::build_team_eval_runner；services.py仅传team_id/prompt/timeout | 静态确认；副作用风险，未做破坏性验证 |
| F03 | 团队只冻结id，执行时重读当前team；多轮只传case.turns[-1] | services.py::_freeze_target、_execute_team；build_team_eval_runner | 静态确认 |
| F04 | live且script非空仍可能被ControlledProvider替换 | runtime_factory.py::create_attempt只判断case.script | 静态确认；需模式交叉回归 |
| F05 | Judge材料列用例名称/说明、回答、工具、产物元数据，但漏完整turns；产物正文从不读，整体尾截断可能丢引用 | graders/judge.py::_material、_known_refs | 静态确认；会限制开放任务判分 |
| F06 | Skill执行有execution但无目标执行Agent身份；Agent名称从target.name取，而快照档案名称嵌套在agent.profile | worker.py::run_agent_worker；services.py::_freeze_agent_target | 静态确认 + 实测归属缺失 |
| F07 | Skill读失败涉及USER.md、SOUL.md、memory、article.md | 本轮details与tool节点；内置article-outline期望brief/素材输入 | 实测确认；不能把6次失败一概归因Skill路径错误或冗余 |
| F08 | Judge校准已有实现，但5项作业测试失败；不是“没有校准模块” | calibration.py、server.py；本轮811 passed/8 failed | 实测确认；fixture profile缺失线索 |
| F09 | 单run并发检查后调用全队claim_attempt，领取可能是另一个已满额run | scheduler.py::_dispatch_once；repository.py::claim_attempt | 静态疑点，且本轮limit1有两attempt相近时间启动；需重现调度器与物理进程双层限制 |
| F10 | Worker会话checkpoint仍可能超Windows长路径限制 | 本轮test_services首个失败result，FileNotFoundError临时路径 | 原因待短路径对照；不能无证据调整系统注册表 |
| F11 | 校准/人工复核已有后端接口，但WebUI只显示reviews类型，无对应完整操作入口 | server.py::_routes；evaluation-api.ts；evaluation组件目录 | 静态确认当前UI缺口 |
| F12 | 无repetitions、manifest稳定版本比较或逐case配对页 | RunCreate、repository.create_run、reports.py、评估UI | 静态核查未见实现 |

## 4. 本轮结果不能证明什么

5/5只证明所选弱断言命中；团队约322字违反一句话要求仍通过；Skill最终格式命中但有6次读取失败。
团队成本未知；Skill两例58147 Token是本轮观测，不是所有Skill的代表值。
811 passed/8 failed说明软件回归未全绿，不能因为失败曾被文档称为“基线”就永久豁免。

## 5. 官方方法的补充核对

外部研究主报告为同日 `agent-evaluation-practices-research.md`，涵盖10份官方来源。
Leader另打开OpenAI以下原文，仅借鉴方法，不接托管API：

- [Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)：任务特定目标、代表性数据、人工校准、比较与持续补例；工具名选择和参数准确性需要分别检查。
- [Trace grading](https://developers.openai.com/api/docs/guides/trace-grading)：过程证据用于定位质量与正确性问题；仅看最终输出不能定位错误。
- [Graders](https://developers.openai.com/api/docs/guides/graders)：规则与模型评分可组合；当前页面提示相关托管工作流弃用，因此本计划不依赖该产品接口或特定模型。

不采用截图里无明确条件的Judge准确率/便宜倍数，也不将外部文档示例分数当本项目门槛。

## 6. 应坚持的实施原则

1. 先修测量，再扩大样本；有缺证时明确待复核。
2. 任务结果、规则遵循、轨迹质量、效率、安全分别保存；综合合格必须满足声明的硬条件。
3. 规则能准确判断的交给规则；开放语义挂校准后的Judge；缺材料允许不可评分，不能强迫低分。
4. 每次运行固定数据、目标、模型配置和评分口径；重复尝试隔离，比较保留case配对。
5. 先交付本地受控回归；真实网络、shell、任意MCP、线上A/B不能混入同一可信等级。
6. 报告同时展示样本数、分母、缺失范围与已知限制，不能把界面完整等同评估完整。
