---
artifact: verification
route: orchestration:dispatcher-workflow
skills: [orchestration, verification-before-completion]
skills_evidence:
  - harness-kit/.agents/skills/orchestration/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - .ai-runtime-artifacts/plans/2026-10-15-agent-team-plan.md
  - .ai-runtime-artifacts/plans/2026-10-15-agent-team-dispatch.md
  - .ai-runtime-artifacts/contracts/2026-10-15-agent-team-contract.md
  - harness-kit/project.verification.md
  - harness-kit/project.git.md
created_at: 2026-10-15
status: verified
phase: G0
baseline: 690638e
---

# Agent Team G0（Phase 1 / Task 0）环境与接口基线验证

结论：**环境可用，基线已锁定，写入隔离已建立**。可以开始 G1（WU-01）。

本文件与 `execution-logs/tracking/DISPATCH-TRACK-2026-10-15-agent-team.md` 常驻**主 checkout**（`project.git.md` §45：编排产物始终在主 checkout）；业务代码在 worktree `harness/wt-agent-team`。

## 1. 分支与未提交文件

| 项 | 事实 |
| --- | --- |
| 起始分支 | `feature/desktop-app`，基线提交 `690638e`（与 plan front matter `baseline` 一致） |
| 用户未跟踪文件 | `memory/`、`docs/记忆系统/*.drawio.bkp` ×3 —— **本批次不触碰、不提交** |
| 规划产物 | 7 个 Agent Team 产物已提交为 `dc8562c`（`docs(ai-artifacts)`），使 worktree 内 WU 可读取 plan/contract/dispatch |
| 批准后状态 | plan/dispatch `approved: true`（用户原话「根据这个计划，然后执行」）；contract `status: frozen` |

## 2. 运行时与依赖

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 主 checkout Python | `uv run --no-sync python --version` | 3.13.9（≥3.11，满足） |
| worktree Python | 同上（worktree 内） | 3.14.3（uv 为该 worktree 另选解释器；≥3.11 满足） |
| 系统 python3 | —— | 3.9.6，**不用于**项目验证（plan Task 0 已预警） |
| Python 依赖 | `uv sync --extra api --extra dev`（worktree 内） | 成功；无新增依赖、无锁文件变更 |
| `import nanobot` 归属 | `python -c "import nanobot; print(nanobot.__file__)"` | 解析到 **worktree** 内 `nanobot/__init__.py`，未串主 checkout |
| bun | `which bun` | **未安装**（`~/.bun/bin` 不存在）；node v24.18.0、npm/npx 可用 |
| 前端测试入口 | `npx vitest run <files>` | 与 `bun run test` 等价（`package.json` 的 `test` 即 `vitest run`） |
| 新增外部服务 | —— | 无：不需要新环境变量、外部 DB、消息队列、Docker |

**bun 缺失的处理**：不安装第三方运行时（plan §4 禁止无关变更）。前端 WU 一律用 `npx vitest run` / `npm run build` 代替 `bun run test` / `bun run build`，命令语义与 `package.json` script 一致，验证证据按此记录。

## 3. 基线回归（改动前）

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| Python 最近回归 | `pytest tests/webui/test_subagent_profile_fail_closed.py tests/agent/test_subagent_lifecycle.py tests/webui/test_session_projection.py -q` | 主 checkout **56 passed**；worktree **56 passed** |
| 前端最近回归 | `npx vitest run src/tests/agents-view-integration.test.tsx src/tests/nanobot-client.test.ts` | **88 passed**（nanobot-client 83 + agents-view-integration 5） |

已知基线失败清单（`memory/project-known-baseline-failures.md`，2026-10-11 甄别）本轮**未复现**于上述范围：`test_serve_*` 4 例属 `tests/ -k "command or webui or workspace"` 全量选择器，`ruff` 11 处告警集中在 `nanobot/memory/` 与 `nanobot/cli/gateway_runtime.py`。尾盘全量验证时按该清单归因，清单外新失败才追根因。

## 4. 写入隔离（WORKTREE-INIT）

```text
WorktreeId:   wt-agent-team
WorktreePath: /Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/zhixu/wt-agent-team
Branch:       harness/wt-agent-team
Base:         dc8562c（内容来自 690638e + 规划产物提交）
Venv:         该 worktree 独立 .venv（uv sync --extra api --extra dev）
```

主 checkout 在本批次**不写业务代码**。前端 WU 需在 worktree 内单独准备 `webui/node_modules`（worktree 无该目录），执行前由 Leader 处理，禁止顺带升级依赖。

## 5. 接口形状锁定（并行前置）

契约 `2026-10-15-agent-team-contract.md` 已 `status: frozen`，作为 Python/TS 共同依据：

| 形状 | 来源章节 | 版本 |
| --- | --- | --- |
| `TeamBinding {teamId, teamRevision, teamName}` | 契约 §1 | schemaVersion 1 |
| `TeamTaskResult` / `TeamTaskSnapshot` / `TeamRunSnapshot` | 契约 §1 | schemaVersion 1 |
| `TeamEvent`（9 类判别联合 + `data` 固定形状） | 契约 §6 | schemaVersion 1 |
| `SubagentExecutionResult {executionId, outcome, stopReason, finalContent, usage, errorCode}` | 契约 §5 | —— |
| HTTP 读 + 认证 WS mutation 写、`{error:string}` 外层 + 固定错误码前缀 | 契约 §3 | —— |

WU-01 已产出 `tests/fixtures/teams/{definition,event,snapshot}.json` 作为该形状的**可执行锚点**（测试内断言与 dataclass `to_dict()` 往返等价）；fixture 不用真实模型、不发真实网络请求。后续 WU 若需改边界，必须由 Leader 同步 Python/TS 两侧并更新契约。

## 6. 遗留风险

- worktree 解释器 3.14.3 与主 checkout 3.13.9 不一致：本轮基线测试两侧均通过；尾盘 `basedpyright` 结果可能因解释器差异与 CI 略有出入，届时以主 checkout/CI 口径复核。
- 前端 worktree 依赖未就绪：G2 的 WU-08 派发前必须先解决 `node_modules`，否则前端测试无法产出证据。
- `App.tsx`/`ThreadShell.tsx`/`gateway_services.py` 与阶段 A 写作模块共用，WU-05/09/10 须按 dispatch 的「唯一整合者」约束顺序合流。
- 提交规范：plan §5 要求「遵守 AGENTS.md Lore trailers」，但 `AGENTS.md`、`project.git.md` 与近期提交记录中**均无 Lore trailer 规范**，实际口径是 Angular 中文标题 + 可选正文（`project.git.md` §提交格式）。本批次按仓库实际风格执行。

## Next

- G1：WU-01（团队领域类型 + config 路径 + 共同 fixture）。
- 若需改契约边界：先更新 contract 再改 fixture，由 Leader 同步两侧。
