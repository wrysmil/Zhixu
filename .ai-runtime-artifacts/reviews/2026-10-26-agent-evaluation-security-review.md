---
artifact: security-review
route: orchestration.dispatch
plan: .ai-runtime-artifacts/plans/2026-10-26-agent-evaluation-reliability-plan.md
created_at: 2026-10-26
reviewer: leader-self-review
independent_review: not-satisfied
status: completed-with-blockers
---

> 说明：本报告是 **Leader 自查**。原计划的独立审查单元两次派发均因服务高负载失败（详见执行日志），
> 因此本文不冒充「独立审查」结论；「独立审查」这一验收项仍记为**未满足**。

# 评估可靠性安全审查（自查）

## 1. 审查范围与方法

- 范围：git diff 2fb9e5d..HEAD，重点 runtime_factory.py、worker.py、team_runtime.py、sample_cases.py、server.py、schemas.py。
- 方法：读代码路径 + 跑定向命令验证边界行为。
- 实际执行的命令与结果：
  - `pytest tests/evaluation/test_execution_modes.py -q`：14 passed（live 越权工具、路径依赖、限额、长路径）。
  - `pytest tests/evaluation/test_targets.py tests/evaluation/test_validation.py -q`：覆盖工具白名单与路径校验。
  - 手工验证：团队 TargetRef 在无快照提供者时返回 TEAM_NOT_FOUND（入队拒绝，不执行）。

## 2. 问题清单

| 编号 | 严重度 | 位置 | 失效路径 | 影响 | 建议 |
| --- | --- | --- | --- | --- | --- |
| SR-01 | 中 | nanobot/evaluation/services.py:682-686；nanobot/webui/gateway_services.py:821-828 | gateway 只传旧 team_runner，执行段只认 team_runtime_factory，团队路径不可达（不会回退共享 host，已加测试锁定） | 团队隔离、成员权限交集、取消回收均未实测，不能作为安全验收依据 | 接线隔离 team_runtime_factory 后补团队安全抽核 |
| SR-02 | 低 | nanobot/evaluation/runtime_factory.py:314 | _missing_required_inputs 用 exists()，未展开符号链接 | 仅存在性判断（不读取），据以拒绝/放行的风险有限 | 改用 resolve_inside |
| SR-03 | 低 | nanobot/evaluation/sample_cases.py:100 | 样本 environment/notes 文本写入 case description | description 仅展示、不进 prompt；React 转义渲染，无 XSS | 保持现状；若未来 description 进 prompt 需重新评估 |
| SR-04 | 低 | webui EvaluationRuns.tsx | 同一 run 可自比 | 非安全问题，结果误导 | 前端禁用同值 + 后端 422 |
| SR-05 | 未发现 | nanobot/evaluation/runtime_factory.py:245, 309 | 原生 shell | exec 永久关闭；注册表中出现 FORBIDDEN_TOOLS 或 mcp_ 前缀即 SANDBOX_VIOLATION | — |
| SR-06 | 未发现 | nanobot/evaluation/schemas.py:419-426 | live 越权工具 | exec/exec_session/apply_patch 不在可选集，入库即 TOOL_NOT_ALLOWED | — |
| SR-07 | 未发现 | nanobot/evaluation/validation.py:validate_relative_path | 路径逃逸 | 拒绝绝对路径、盘符、UNC、反斜线、. 与 .. 段；运行期再 resolve_inside / is_relative_to | — |
| SR-08 | 未发现 | nanobot/evaluation/worker.py:132, 1110 | 凭据泄漏 | profile bundle 只在内存；快照不含 bundle；worker 帧按 secret 值 redact | — |
| SR-09 | 未发现 | nanobot/evaluation/server.py:51, 259 | 请求体 DoS | MAX_BODY_BYTES=5MiB，client_max_size 同步；错误体走白名单映射 | — |
| SR-10 | 未发现 | package.json / pyproject | 供应链 | 本轮未新增第三方依赖（仅新增 JSON 数据与 TSX/测试） | — |

## 3. 越权与隔离边界核对

| 能力 | 评估用例能否打开 | 证据位置 |
| --- | --- | --- |
| 原生 shell（exec/exec_session/apply_patch） | 否 | schemas.py:419-426；runtime_factory.py:245 |
| MCP 自动连接 | 否 | runtime_factory.py DISABLED_SUBSYSTEMS + 不构造 MCPProvider |
| 子 Agent / spawn | 否 | FORBIDDEN_TOOLS 注销 + spawn 在 removed_tools |
| 消息投递 / 渠道 | 否 | channels overlay sendProgress=False，DISABLED_SUBSYSTEMS channel_delivery |
| cron / goal / 自修改 | 否 | DISABLED_SUBSYSTEMS + goal 工具注销 |
| 工作区外文件读写 | 否 | restrict_to_workspace + 路径校验 + resolve_inside |
| 用户团队库 | 未实测 | team_runtime 物化到 attempt workspace（代码层），生产未接线 |
| 凭据外泄 | 否 | 快照不含 bundle；redact_json 覆盖 worker 帧 |

## 4. 未覆盖与残余风险

- 团队目标不可达，团队隔离的实际行为未验证（SR-01）。
- 真实模型下的工具调用行为未验证（受控 provider 只回放脚本）。
- 未做渗透式测试（恶意样本文件、超长字段、并发取消风暴）。
- 未验证 Windows 符号链接/junction 在 workspace 内的绕过（validation 只做语法层 + 运行期 resolve；本轮未构造 junction 用例）。

## 5. 结论

现有代码路径下，原生 shell/MCP/投递/子 Agent 等越权能力均已关闭，凭据不入快照，路径校验多层。但团队隔离与真实模型行为未实测，安全验收不完整。必须修复项：SR-01（接线后补团队安全抽核）；SR-02 建议修。
