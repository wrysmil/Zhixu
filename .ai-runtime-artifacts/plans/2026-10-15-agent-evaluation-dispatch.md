---
artifact: implementation-dispatch
route: writing-plans
skills:
  - writing-plans
  - api-and-interface-design
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/api-and-interface-design/SKILL.md
plan: .ai-runtime-artifacts/plans/2026-10-15-agent-evaluation-plan.md
spec: .ai-runtime-artifacts/specs/2026-10-15-agent-evaluation-spec.md
contract: .ai-runtime-artifacts/contracts/2026-10-15-contract-agent-evaluation.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - harness-kit/artifact-templates/dispatch.harness-overlay.md
created_at: 2026-10-15
status: approved
approved: true
approved_at: 2026-10-16
approval_evidence: GROUP-1 沿用「本批只跑 GROUP-1 后停在屏障」；GROUP-2 由用户 2026-10-16 在屏障后的结构化提问中选定「推进 GROUP-2（WU-03+WU-04）」，WU-02 因本机无 Bun 继续后置。
tier: 2
batch_scope: GROUP-1（WU-01，已闭环）+ GROUP-2（WU-03 + WU-04，WU-02 待 Bun 后置）
---

# Agent 自动化评估执行图

本文件只描述实施时的分工、依赖与合并屏障。当前没有启动任何子 Agent，也没有创建工作树。用户批准计划后才加载编排阶段 skill；本文件不声称 orchestration 已加载。

## 1. 调度约束

- 最多同时运行 3 个子 Agent，加 Leader 共 4 个实例；各 GROUP 间必须通过集成屏障。
- 所有业务代码在 WORKTREE-INIT 返回并已附着到本会话的 managed worktree 中处理；不是当前主 checkout。先读取已有 artifacts，优先复用合适的活动工作树，不固定不存在的目录。
- 推荐分支 codex/agent-evaluation，最终路径与分支以工具实际返回为准。各 WU 的 worktree_path/branch 在派发前替换为真实结果，不能以本草案中的 n/a 开始编码。
- 派发前为每个 WU 写 ContextPack、依赖产物、源文件和禁止事项。按项目规则落盘 dispatch tracking 与 execution-log。
- Worker 只改自己拥有的文件，不提交、不推送。共享文件变更由 WU08 集成，Leader 负责最终 Git 操作。
- WU01 完成后，其他 WU 不自行改 schemas.py/repository.py；发现缺失将变更请求交 Leader，由原所有者补齐并回归。
- WU02 与 WU06 不同时写 evalui/。WU08 不改前端页面/类型；如需调整 package.json 或 lock，由 WU06 完成后再归并。
- 每个 Agent 汇报必须含“Skills 使用”、实际修改文件、验证命令与结果、未验证项、接口差异；不合规返回不直接整合。

## 2. 执行图

```text
GROUP-1 基础与页面：
  WU-01 数据模型、版本存储与入队 | T01–T02 | 依赖：契约 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a（待 WORKTREE-INIT） | branch: n/a | wu_skills: auto
  WU-02 独立评估页面与契约客户端 | T03–T04 | 依赖：契约 | wu_type: ui | agent_role: coder | workspace_scope: wu | worktree_path: n/a（待 WORKTREE-INIT） | branch: n/a | wu_skills: auto
  屏障：模型/API 字段一致；数据版本与 UI mock 测试通过；独立构建输出无覆盖。

GROUP-2 执行与判分：
  WU-03 SDK 隔离、进程树、Trace 与用量 | T05–T06 | 依赖：WU-01 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a（待 WORKTREE-INIT） | branch: n/a | wu_skills: auto
  WU-04 规则、Judge、校准及指标 | T07–T08 | 依赖：WU-01 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a（待 WORKTREE-INIT） | branch: n/a | wu_skills: auto
  屏障：执行产生的证据可由判分模块读取；无 SDK 核心回归；两种 Token 账本不重复。

GROUP-3 服务与样本：
  WU-05 调度、HTTP、认证、恢复与产物 | T09–T10 | 依赖：WU-01/03/04 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a（待 WORKTREE-INIT） | branch: n/a | wu_skills: auto
  WU-07 基础用例与校准种子 | T12 | 依赖：WU-01/03/04 | wu_type: test | agent_role: test-engineer | workspace_scope: wu | worktree_path: n/a（待 WORKTREE-INIT） | branch: n/a | wu_skills: auto
  屏障：HTTP 返回契约一致，20 用例离线通过；校准样本 proposed/confirmed 不混淆。

GROUP-4 网站联调与集成：
  WU-06 页面真实接线、校准面板与异常恢复 | T11 | 依赖：WU-02/05/07 | wu_type: ui | agent_role: coder | workspace_scope: wu | worktree_path: n/a（待 WORKTREE-INIT） | branch: n/a | wu_skills: auto
  WU-08 CLI、独立构建与打包收口 | T13 | 依赖：WU-02/03/04/05/07 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a（待 WORKTREE-INIT） | branch: n/a | wu_skills: auto
  屏障：入口可独立启动；前端生产入口无测试 transport；wheel/sdist 含独立静态资源及样本。

GROUP-5 集体尾盘：
  WU-09 验证包 | T14 | 依赖：全部 WU | Leader 组织，测试/代码审查/安全审查分别独立实例
  先运行集体测试，再并行只读代码与安全审查；修复回到代码所有者，按影响范围复测。
  Leader 落盘 collective-test、code-review、security-review，更新 execution-log 后判定交付。
```

## 3. WU 上下文、边界与完成标准

| WU | 必需上下文与参考 | 写入所有权 | 禁止事项 | 完成标准 |
| --- | --- | --- | --- | --- |
| 01 | spec §6/7/9；契约全部；nanobot/config/schema.py；现有 SQLite repository 模式 | 计划文件表中的数据/validation/test fixture 文件 | 不改 Agent、UI、调度；不为自己放宽契约 | 版本 CAS、快照不变、幂等、原子领取、导入回滚、路径与 schema 限制通过 |
| 02 | spec §5；契约全部；现有 webui/bun.lock/vite.config.ts 仅作技术参考 | evalui 初始目录，见 T03/T04 | 不修改主 WebUI；不声称 mock 已接真实 Agent | 页面测试、独立 typecheck/build/lint、关键错误 UI；输出目录独立 |
| 03 | spec §3/4/8/9；契约 §5/6；SDK、Hook、Config、usage observer、_windows_job | 执行/Trace/IPC/process/usage 模块及局部测试 | 不更改 provider 全局行为；不向主服务进程绑定配置；不启用未批准工具 | 两个用例全数据隔离，资源回收，证据完整性、物理调用账本及 SDK 回归通过 |
| 04 | spec §7/11；契约 §2/4/5/6；LLMProvider.chat_with_retry | graders/、reports.py 及局部测试 | 不接受无效证据引用；不将未校准当通过；不调用任意用例代码 | 规则正反例、模型重试上限、量表校准失效、指标黄金组合、脱敏导出通过 |
| 05 | spec §4/8/9；契约全部；WU03/04 交付接口 | 服务/调度/路由/认证/产物及局部测试 | 不并行改公共 models/repository；禁止 in-memory-only 队列；禁止通配 Origin | 全资源 API、gold 标签确认、校准、并发、取消、重启、独占锁、敏感数据测试通过 |
| 06 | WU02 页面、WU05 HTTP、WU07 样本；契约 | 接管 evalui 页面/API/测试；新增 CalibrationPanel | 不回退为假数据；不把页面关闭等同取消运行 | 真实服务登录至报告/复核完整走通；轮询晚返回/401/409 恢复，校准标签状态准确 |
| 07 | spec §6/11；契约 CaseWrite/GoldSample；WU03/04 接口 | 两个样本 JSON、test_samples/test_end_to_end | 不写 runtime 根目录；不把 AI 建议标签标成人工确认 | 20 例合法且可判分、关键值翻转必失败、10 gold 建议标签与锚点可复核 |
| 08 | T13；CLI entry/commands；hatch_build.py；pyproject；现有 WebUI build tests | 计划列明的共享入口/构建文件及新的 evaluation/cli.py/build.py | 不覆盖现有 dist；不修改其他 WU 页面；不全局解除 lock 忽略策略 | CLI 独立启动、依赖提示、包内资源/样本、旧入口/构建回归通过 |
| 09 | 全部交付与测试证据；project.verification.md | 仅过程产物；缺陷测试需经 Leader 指定文件所有权 | 审查者不修业务代码；不能以 skipped 标成功 | 集体测试/独立审查落盘，失败或未验证项明确，execution-log 真实关闭 |

WU 技能按派发时实际规则解析：数据/API 用 api-and-interface-design；执行器采用 TDD 与项目安全边界；前端采用 frontend-ui-engineering、browser-testing-with-devtools；验证采用 verification-before-completion；尾盘采用 requesting-code-review + code-review-and-quality + security-and-hardening。路径在执行时验证，缺失先查项目级 skill。这里是拟用技能，不是本轮加载记录。

## 4. 集成与变更处理

1. 每个屏障由 Leader 检查 git diff、接口一致性、测试输出与边界；通过后再启动依赖 GROUP。
2. 各 GROUP 共享同一受管理实现工作树，但按文件所有权隔离。若平台采用每 WU 独立工作树，Leader 记录准确目录/分支并按依赖归并；不得默认并行修改共享文件。
3. 接口变化先改契约，经 Leader 检查同批消费者；对所有依赖 WU 发中文变更说明后再更新实现。
4. Judge 评分校准、运行并发、进程回收出现跨模块差异时，先集体复现最早失败，再分派修复；不得用汇总错误覆盖原始错误。
5. 计划文件与执行图有冲突时先改文档并说明；写文件清单是派发边界，不是允许忽略规格的理由。

## 5. 尾盘产物

- .ai-runtime-artifacts/execution-logs/2026-10-15-agent-evaluation-execution-log.md
- .ai-runtime-artifacts/verifications/2026-10-15-agent-evaluation-collective-test.md
- .ai-runtime-artifacts/reviews/2026-10-15-agent-evaluation-code-review.md
- .ai-runtime-artifacts/reviews/2026-10-15-agent-evaluation-security-review.md

当前只列出未来路径，不提前建立“已完成”证据文件。运行数据和截图位于 .ai-runtime-artifacts/verifications/agent-evaluation/ 与同级的具体验证子目录，不能把凭据、用户隐私或模型原始敏感内容提交。

## Next

- 确认计划/执行图 → 回复“开始实现”或“并行执行”。
- 只改顺序/分工 → 修改本文件并由 Leader 复核依赖。
- 改产品范围 → 先更新 spec，再同步计划和契约。
