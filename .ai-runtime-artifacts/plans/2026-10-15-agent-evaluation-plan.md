---
artifact: implementation-plan
route: api-and-interface-design -> writing-plans
skills:
  - writing-plans
  - api-and-interface-design
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/api-and-interface-design/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-10-15-agent-evaluation-dispatch.md
spec: .ai-runtime-artifacts/specs/2026-10-15-agent-evaluation-spec.md
contract: .ai-runtime-artifacts/contracts/2026-10-15-contract-agent-evaluation.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - harness-kit/project.verification.md
  - nanobot/nanobot.py
  - nanobot/cli/commands.py
  - nanobot/config/schema.py
  - nanobot/providers/base.py
  - hatch_build.py
  - pyproject.toml
created_at: 2026-10-15
status: approved
approved: true
approved_at: 2026-10-15
approval_evidence: 用户在本会话通过结构化提问选定「Agent 自动化评估（今日 plan/dispatch）」+「只跑 GROUP-1（WU-01 + WU-02）后停在屏障」，等价于「开始实现」。
tier: 2
batch_scope: GROUP-1（WU-01 + WU-02）；GROUP-2/3/4/5 未批准，屏障后暂停
---

# Agent 自动化评估网站实施计划

> **执行 Agent：** 按项目 Harness 编排执行本计划。开始实现前加载 orchestration，依执行图建立工作区、打包上下文、派发和审查；不能用通用 skill 的默认执行路径替代本项目门禁。本轮没有创建工作树或派发 Agent。

**Goal:** 交付独立启动、独立访问的网站，完成真实 Agent 的用例管理、批量运行、自动评分、报告和失败 Trace 查询。

**Architecture:** 独立 aiohttp 服务管理 SQLite 队列与报告，每个用例和 Judge 均在受管理的独立进程中执行。Agent 通过现有 SDK/AgentLoop 调用；前端为独立 evalui 应用，生产资源放入 nanobot/evaluation/static，现有 WebUI 与 Gateway 无须启动。

**Tech Stack:** Python >=3.11、aiohttp 3.x、Pydantic 2.x、SQLite；React 18.3.1、TypeScript 5.9.3、Vite 5.4.21、Tailwind 3.4.19、Vitest 2.1.9。前端版本来自本项目 bun.lock，后端沿用 pyproject.toml 范围；依赖安装及实际版本在实施时记录。

**批准依据:** 用户在设计交付后明确说“写计划”，批准进入本阶段。spec 已批准；本计划、执行图和接口细化仍待用户审阅。

## 1. 实施约束与交付切片

- 完整链路固定为 SDK → AgentLoop → AgentRunner，禁止把受控 provider 的单测结果当作真实模型评估结果。
- 基础运行采用 fixture 工具模式；真实网络模式保留 SSRF 保护。原生 shell、外部写操作、任意 MCP、cron、消息投递和自修改不开放。
- 不修改现有聊天 WebUI 导航、Gateway 协议、记忆抽取算法、定价逻辑。只在确有失败测试证明需要时提出最小 SDK 接入变更，先记录原因，禁止顺手重构核心。
- HTTP 主进程不修改全局配置路径；隔离上下文只在 Worker 内设置。配置凭据不落盘、不进入 URL、日志、Trace、快照或导出。
- 修改共享文件仅由集成 WU/Leader 执行。Worker 不自行提交、推送或改别人的文件；Leader 在批次检查后按显式文件清单提交，不自动 push。
- 任何设计外需求先记录为差异，不静默扩展。CI 中的自动化评估门禁、定时执行、模型对比专页不在本计划。
- 默认数据目录 .ai-runtime-artifacts/verifications/agent-evaluation/；本轮所有计划、契约和验证过程产物只放 .ai-runtime-artifacts/。
- 以下代码与命令均为实施步骤，尚未运行。代码块给出关键实现、测试和算法，完整字段、错误码、限制与边界以契约为准；执行者不得根据示例删去契约必需字段。

交付切片：
1. G1：数据/版本基础 + 独立网站页面骨架；页面用显式测试服务验证契约。
2. G2：真实 SDK 隔离执行 + 规则/Judge 评分；可通过内部服务跑通一条完整用例。
3. G3：持久化调度与 HTTP + 样本资产；形成可由接口操作的评估服务。
4. G4：页面真实接线 + CLI、依赖锁、生产打包与跨模块收口；一个命令独立启动网站。
5. G5：浏览器完整流程、Windows 回收、全量相关测试与独立审查；交付证据。

## 2. 文件结构与所有权

| WU | 新建文件 | 现有文件改动 |
| --- | --- | --- |
| 01 数据 | nanobot/evaluation/{__init__,schemas,validation,database,repository}.py；tests/evaluation/{__init__,conftest,test_validation,test_database,test_repository}.py | 无 |
| 02 页面 | evalui/{package.json,bun.lock,index.html,tsconfig.json,tsconfig.build.json,vite.config.ts,tailwind.config.ts,postcss.config.js,eslint.config.js}；evalui/src/{main.tsx,App.tsx,styles.css}；evalui/src/lib/{types,api,routes,format}.ts；evalui/src/components/{Shell,StatusBadge,Pagination,JsonEditor}.tsx；evalui/src/pages/{LoginPage,OverviewPage,SuiteListPage,CaseEditorPage,NewRunPage,RunListPage,RunDetailPage,AttemptDetailPage}.tsx；evalui/src/tests/{setup,api,routes,case-editor,run-detail,attempt-detail}.test.ts(x)（具体后缀见任务） | 无 |
| 03 执行 | nanobot/evaluation/{worker,ipc,runtime_factory,fixtures,trace,usage,processes}.py；tests/evaluation/{test_worker,test_ipc,test_runtime_factory,test_trace,test_usage,test_processes}.py | 无；复用现有 _windows_job.py，不在此 WU 重构 |
| 04 判分 | nanobot/evaluation/graders/{__init__,rules,judge,verdict,calibration}.py；nanobot/evaluation/reports.py；tests/evaluation/{test_rules,test_judge,test_verdict,test_calibration,test_reports}.py | 无 |
| 05 服务 | nanobot/evaluation/{scheduler,services,server,auth,routes,artifacts,errors}.py；tests/evaluation/{test_scheduler,test_services,test_server,test_auth,test_routes,test_artifacts,test_recovery}.py | 无 |
| 06 页面联调 | evalui/src/components/CalibrationPanel.tsx；evalui/src/tests/{flow,errors,calibration}.test.tsx；修改 WU02 已交付页面/API 与对应测试 | 仅 WU02 已关闭后接管 evalui/ |
| 07 样本 | nanobot/evaluation/samples/{basic-v1.json,calibration-v1.json}；tests/evaluation/{test_samples,test_end_to_end}.py | 无 |
| 08 集成 | nanobot/evaluation/{cli,build}.py；tests/evaluation/{test_cli,test_build,test_packaging}.py | nanobot/cli/commands.py、hatch_build.py、pyproject.toml、.gitignore；必要时 tests/cli/test_commands.py、tests/webui/test_build.py |
| 09 验证 | .ai-runtime-artifacts/verifications/2026-10-15-agent-evaluation-*；.ai-runtime-artifacts/reviews/2026-10-15-agent-evaluation-* | 发现缺陷按原所有者派 review-fix，不由只读审查者改业务文件 |

shared tests/evaluation/conftest.py 仅 WU01 写；后续 WU 使用自己的局部 fixture，不并行改公共 fixture。所有 nanobot/evaluation Python 包目录均包含 __init__.py。页面拥有独立 HTML/样式/依赖，不静态导入主 WebUI 的业务模块。

## 3. 数据库及事务安排

database.py 持有 schema_version、外键开启、busy_timeout=5000、WAL 和显式事务。只在服务进程通过单写入锁更新；同步 SQLite 操作在串行受控执行器中运行，避免阻塞 aiohttp 事件循环。Worker 不直接连接主数据库。

表：
- suites(id PK,revision,name,description,tags_json,is_deleted,created_at,updated_at)。
- cases(id PK,suite_id FK,latest_revision,is_deleted,created_at)。
- case_revisions(case_id,revision,definition_json,created_at，联合主键，不可原地更新)。
- runs(id PK,suite_id,status,snapshot_json,request_hash,parent_run_id,is_deleted,created_at,started_at,finished_at)。
- attempts(id PK,run_id FK,position,case_id,case_revision,case_snapshot_json,execution_status,grading_status,verdict,result_json,pid,process_nonce,started_at,finished_at)；unique(run_id,position)。
- idempotency(scope,endpoint,key,request_hash,run_id,response_json,created_at)，unique(scope,endpoint,key)，与 run/attempt 创建一事务。
- sample_imports(sample_id PK,suite_id FK,created_at)；基础样本重复导入返回同一用例集，不能仅依赖名称去重。
- reviews(id PK,attempt_id FK,verdict,comment,reviewer,created_at)；只追加。
- artifacts(id PK,attempt_id FK,relative_path,media_type,size_bytes,sha256)；不接收客户端原始绝对路径。
- calibration_results(id PK,judge_fingerprint,rubric_version,gold_revision,total,matched,agreement,passed,created_at)。
- gold_samples(id PK,revision,definition_json,label_status,reviewer,comment,updated_at)；gold_state(id=1 PK,revision) 记录整个 gold 集版本。
- calibration_jobs(id PK,status,snapshot_json,result_json,created_at,started_at,finished_at)；calibration_items(id PK,job_id FK,position,status,result_json,pid,process_nonce)，unique(job_id,position)，与普通运行共用进程预算和恢复逻辑。
assertion/judge 结果保存在 attempts.result_json 的类型化字段，避免首版拆出重复真相表；报告从持久化结果聚合。

领取核心 SQL：
```sql
BEGIN IMMEDIATE;
UPDATE attempts
SET execution_status='running', started_at=:now
WHERE id=(
  SELECT a.id FROM attempts a JOIN runs r ON r.id=a.run_id
  WHERE a.execution_status='queued' AND r.status IN ('queued','running')
  ORDER BY r.created_at,a.position LIMIT 1
) AND execution_status='queued'
RETURNING *;
COMMIT;
```
运行创建时冻结完整 case_snapshot_json；只保留 revision ID 而未保存正文不算完成。领取与取消都必须有状态谓词，不能先读后无条件更新。

## 4. 测试约定与实现任务

每项先写会失败的行为测试，执行确认失败原因，再实现和复跑。新增代码缺失导致的首次失败可接受；依赖环境错误不算已证明行为失败。每个 checkbox 是独立动作，建议 2–5 分钟推进；测试矩阵中的每行分别执行红绿循环，不能一次写完再补测试。

### T01 / WU01：输入模型、限制与路径校验

文件：schemas.py、validation.py、tests/evaluation/test_validation.py、tests/evaluation/conftest.py。
定义契约同名 Pydantic 模型 CaseWrite、CaseRead、SuiteWrite、RunCreate、TraceEvent、WorkerBoot、WorkerResult、AssertionResult、JudgeResult；extra="forbid"。fixture 工厂返回契约合法的文本任务，provider 不使用网络。

- [x] 写相对路径、未知字段、bool 混入整数、rubric 锚点缺失、无必过规则、重复 case_ids 的失败测试。路径核心测试：
```python
from pathlib import Path
import pytest
from nanobot.evaluation.validation import resolve_inside

def test_path_cannot_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="PATH_OUTSIDE_ROOT"):
        resolve_inside(tmp_path, "../outside.txt")

def test_path_resolves_inside(tmp_path: Path) -> None:
    assert resolve_inside(tmp_path, "inputs/a.txt") == tmp_path / "inputs" / "a.txt"
```
- [x] 运行：uv run --no-sync python -m pytest tests/evaluation/test_validation.py -q；确认失败是新模块/逻辑缺失。
- [x] 实现模型及受控路径解析，拒绝 symlink/reparse-point 越界、Windows drive/UNC/反斜线逃逸；以下为基本目录包含判定，Windows 额外分支由测试驱动补齐：
```python
from pathlib import Path

def resolve_inside(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    base = root.resolve()
    if candidate.is_absolute() or not relative.strip():
        raise ValueError("PATH_OUTSIDE_ROOT")
    target = (base / candidate).resolve()
    if target == base or not target.is_relative_to(base):
        raise ValueError("PATH_OUTSIDE_ROOT")
    return target
```
- [x] 按契约实现 SchemaSubset 递归深度上限 16、节点最多 1000，禁止远程 $ref 与代码；测试嵌套上限。
- [x] 复跑测试，记录所实现模型和限制；交给 Leader 审查后进入下项。

### T02 / WU01：版本存储、不可变快照与幂等入队

文件：database.py、repository.py、tests/evaluation/test_database.py、tests/evaluation/test_repository.py。

- [x] 写测试：保存 case rev1→编辑 rev2→旧 run 仍读取 rev1 正文；旧 base_revision 返回 REVISION_CONFLICT；200 用例导入第 200 项非法则整事务回滚。
- [x] 运行：uv run --no-sync python -m pytest tests/evaluation/test_database.py tests/evaluation/test_repository.py -q，确认红灯。
- [x] 建第 3 节表和版本迁移器，repository 提供契约 §6 方法；每次存取使用 Pydantic/JSON 类型化序列化。
- [x] 实现规范化请求摘要，字段顺序不影响幂等身份：
```python
import hashlib
import json

def request_digest(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```
- [x] 按契约原子保存 key/hash/run/attempt，验证相同 key 同 payload 重放、不同 payload 422、并发领取不重复、软删不释放 key。
- [x] 复跑上述文件，并运行 uv run --no-sync ruff check nanobot/evaluation；新增建库/存取行为须全通过。

### T03 / WU02：独立前端项目与类型化 API 客户端

文件：evalui 项目配置、main.tsx、App.tsx、styles.css、src/lib/{types,api,routes,format}.ts、components/、src/tests/{setup.ts,api.test.ts,routes.test.ts}。

- [ ] 从当前 webui/bun.lock 提取 React/类型/Testing Library 等确切版本，独立 package.json 固定版本；不复制业务代码，不升级主 WebUI。scripts 定义 dev/build/typecheck/test/lint；build 执行 tsc -p tsconfig.build.json && vite build。
- [ ] 写客户端测试：401 回到登录但保留表单；409 不清空草稿；同次启动重试复用 Idempotency-Key；Trace after_seq 保持递增。
- [ ] 执行：bun --cwd evalui run test src/tests/api.test.ts src/tests/routes.test.ts；确认失败后实现契约字段和错误解析。
- [ ] vite.config.ts 明确设置独立输出与代理，主 WebUI 构建目录保持原样：
```typescript
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  build: { outDir: path.resolve(__dirname, "../nanobot/evaluation/static"), emptyOutDir: true },
  server: {
    host: "127.0.0.1", port: 5174, strictPort: true,
    proxy: { "/api/eval": { target: "http://127.0.0.1:8790", changeOrigin: false } },
  },
  test: { environment: "happy-dom", setupFiles: ["./src/tests/setup.ts"] },
});
```
开发 Origin 精确允许 http://127.0.0.1:5174，仅在显式 --dev-origin 模式开启，CSRF 仍校验，生产关闭；后端 T10 按契约实现。开发代理保留原 Host/Origin，使服务能按精确列表校验。
- [ ] types.ts 完整映射契约，API 客户端采用 credentials:"same-origin"、mutation 带 CSRF、创建带稳定 key；JSON 和 CSV 下载经 Blob。
- [ ] 运行 bun --cwd evalui run typecheck、test、build；页面仍使用注入的显式测试 transport，此批不声称真实执行链路已接通。

### T04 / WU02：用例、运行与 Trace 页面骨架

文件：所有约定 pages/，src/tests/{case-editor.test.tsx,run-detail.test.tsx,attempt-detail.test.tsx}；页面依赖通过 API transport 注入。

- [ ] 为新建/编辑/复制/停用、JSON 导入导出、必需 Judge 提示、点击开始、取消、失败筛选、重跑、人工复核分别写交互失败测试。
- [ ] 运行：bun --cwd evalui run test src/tests/case-editor.test.tsx src/tests/run-detail.test.tsx src/tests/attempt-detail.test.tsx。
- [ ] 实现 Shell 的 hash 路由 /login、/overview、/suites、/cases/new、/cases/:id、/runs/new、/runs、/runs/:id、/attempts/:id。用原生表单、可访问按钮/表格和中文状态文本；不要引入额外路由依赖。
- [ ] 用例编辑器覆盖全部 CaseWrite 字段；高级规则与 fixture 可编辑 JSON，保存前给出字段级校验错误。量表逐维显示五档锚点。
- [ ] Trace 按 turn/iteration 分组、懒展开、cursor 去重；原文用 textContent/React 文本节点：
```tsx
export function EvidenceText({ value }: { value: string }) {
  return <pre className="whitespace-pre-wrap break-words">{value}</pre>;
}
```
- [ ] 增加键盘导航、loading/empty/error、status 文本及 960/1440 宽度检查，复跑测试与构建；页面中禁用需要真实运行且未接线的动作，测试 transport 只能用于测试入口。

### T05 / WU03：隔离运行环境与真实 SDK 适配

文件：runtime_factory.py、fixtures.py、tests/evaluation/test_runtime_factory.py、tests/evaluation/test_worker.py（先覆盖构造）。

- [x] 写测试：两个 attempt 在相同相对路径写不同内容、会话隔离、memory_seed 只进入各自记忆库；记录日常用户数据目录前后差异，确认没有写入。
- [x] 执行：uv run --no-sync python -m pytest tests/evaluation/test_runtime_factory.py -q；确认失败。
- [x] Worker 启动后先 loader.set_config_path(attempt runtime root/config.json)，构造深复制 Config 并 bind_source_path；仅内存注入 profile；不落盘原始配置。通过 AgentLoop.from_config 与 Nanobot(loop,config=...) 进入 SDK，复用现有 hook factories；显式过滤默认注册工具，不能只删除 UI 可见名称。
- [x] 每 turn 经 await bot.run(content,session_key="eval:<attempt_id>",hooks=[trace_hook])；采用 async with bot 保证 aclose；工作区中保留该用例专用会话以支持多轮，不以 ephemeral=true 跳过待测记忆行为。（实现偏差：worker.py 以 `guarded_close()` 单点调用 `runtime.aclose()` 取代 `async with bot`——两者并存会关闭两次，且 `__aexit__` 抛错会覆盖原始执行错误；「保证 aclose」这一约束不变）
- [x] 添加固定 fixture tools，工具错误必须用项目 ToolResult.is_error 表达，不能仅返回含 Error 的普通文本。clock_iso 只影响可控工具。
- [x] live 仅启用现有 web_search/web_fetch；禁止 worker 自动连接 MCP、消息投递、cron、自修改、原生 shell/后台命令工具和任意插件。执行前保存能力清单用于报告。
- [x] 复跑隔离、SDK 清理和工具白名单测试，并跑 tests/test_sdk_streaming.py 与 tests/agent/test_loop_runner_integration.py 检查接入回归。

### T06 / WU03：进程树、IPC、Trace 与用量证据

文件：worker.py、ipc.py、processes.py、trace.py、usage.py 及对应 test_ipc/test_processes/test_trace/test_usage/test_worker.py。

- [x] 写 IPC 错误 attempt_id/版本/重复事件/超长帧、半行 JSONL、无 result 退出、写盘失败、挂起 Worker 与孙进程的失败测试。
- [x] 运行：uv run --no-sync python -m pytest tests/evaluation/test_ipc.py tests/evaluation/test_processes.py tests/evaluation/test_trace.py tests/evaluation/test_usage.py tests/evaluation/test_worker.py -q。
- [x] 实现 create_subprocess_exec，stdin/stdout 管道传协议，logger 重定向 stderr，所有命令参数不含秘密；Windows 使用现有 Job Object 的 suspended spawn/attach/resume 能力，其他平台进程组；不以 shell=True 启动 Worker。父进程持有 owner，关闭 owner 回收全部后代。
- [x] TraceHook 以 reraise=True 初始化；Hook 记录 logical request summary 和工具生命周期；usage observer 记录每个物理调用。observer 写失败置 sticky 错误标志，结束前检查；异步队列满应回压/中断，不丢关键证据。
- [x] 成功必须同时获得结果帧、成功退出码、Trace/产物密封；保留 execution/trace/cleanup 多项错误。在超时/取消后先清理再落终态。
- [x] usage 以物理调用账本为主，按 agent/judge 与 main/auxiliary 标记，不再次相加 round_usages。无法覆盖的调用标记 partial；SDK 辅助 provider 通过已有 factory 参数/构造接点安装 observer，不修改全局 provider 对象。
- [x] 复跑；Windows 原生执行孙进程测试不得 skip 后宣称回收已验证。observer 重试的回归跑 tests/providers/test_llm_usage_observer.py。

### T07 / WU04：确定性规则、最终 verdict 与聚合指标

文件：graders/rules.py、graders/verdict.py、reports.py；tests/evaluation/{test_rules,test_verdict,test_reports}.py。

- [x] 写 CSV→JSON total=42 正反例、JSON schema/Pointer 缺失、工具调用参数子集、禁止工具、循环上限、二进制/缺失产物等失败测试。
- [x] 执行：uv run --no-sync python -m pytest tests/evaluation/test_rules.py tests/evaluation/test_verdict.py tests/evaluation/test_reports.py -q。
- [x] 规则只访问当前 attempt Evidence 对象和经过校验的产物；schema 仅契约白名单，不执行用户代码。
- [x] verdict 使用以下完整优先级，judge_state 为 disabled/passed/failed/missing/error/uncalibrated；可选 Judge 的异常在传入前归一为 disabled，原异常保留：
```python
def derive_verdict(
    execution_status: str,
    required_rules: list[bool | None],
    judge_state: str,
    trace_complete: bool,
) -> str:
    if execution_status in {"failed", "timed_out"}:
        return "failed"
    if execution_status != "succeeded":
        return "not_evaluated"
    if False in required_rules or judge_state == "failed":
        return "failed"
    if (
        not trace_complete
        or None in required_rules
        or judge_state in {"missing", "error", "uncalibrated"}
    ):
        return "review_required"
    return "passed"
```
- [x] 聚合按契约区分分母和缺失值；黄金组合 planned=5，passed=2，failed=1，review_required=1，not_evaluated=1，应得 overall=0.4，evaluated=2/3，coverage=0.6。空集合三个比例均 null；duration [10,20,30,40] median=25、P95=40。
- [x] CSV 导出对以 =、+、-、@、Tab、CR 开头的文本前缀单引号，JSON 导出不含秘密；工具执行错误率与工具用例正确率不混淆；复跑全部测试。

### T08 / WU04：无工具 Judge、校准与人工复核

文件：graders/judge.py、graders/calibration.py、graders/__init__.py；tests/evaluation/{test_judge,test_calibration}.py。

- [x] 写含“忽略评分标准”的恶意 Trace、未知证据引用、缺维度、分数越界、格式错误重试、评分超时和未校准的失败测试。
- [x] 执行：uv run --no-sync python -m pytest tests/evaluation/test_judge.py tests/evaluation/test_calibration.py -q。
- [x] 固定 grader_version="judge-v1"；系统提示要求把任务/Trace 当数据、无工具；按 rubric 完整返回 {dimensions:[{id,score,reason,evidence_refs}]}，严格整数 0–4。
- [x] malformed/无效结果最多两次物理请求；预算为总评分超时 60 秒（包括重试）；调用取消可回收 Judge Worker。不能因为 JSON 修复而接受未知 evidence_ref。
- [x] 以 provider/model/generation + prompt_version + rubric_version 组成 judge_fingerprint；换配置使校准失效。用户确认的 10 个 gold 结果计算 agreement，至少 8 个匹配才通过；结果分别绑定样本使用的量表指纹，未验证的新量表保留未校准状态。
- [ ] 校准入口并入 NewRun：先提供 gold 证据/建议标签的用户确认，再显示“运行校准”。按契约实现样本读取、标签确认、启动、进度与取消；全部走会话/CSRF/幂等/服务级并发预算。内置标签默认 proposed，AI 不能代替用户确认。
- [x] 校准失败仍展示原始评分，但开放任务 verdict=review_required；人工复核记录原自动 verdict 与独立审核结论，不回写 gold 标签；复跑测试。

### T09 / WU05：调度器、取消和重启恢复

文件：scheduler.py、services.py；tests/evaluation/{test_scheduler,test_services,test_recovery}.py。

- [x] 写并发重复领取、两个 run 总并发上限 3、关闭页面、排队取消、运行中取消、评分取消、第二实例、重启中断的失败测试。
- [x] 执行：uv run --no-sync python -m pytest tests/evaluation/test_scheduler.py tests/evaluation/test_services.py tests/evaluation/test_recovery.py -q。（偏差：未单列 `test_recovery.py`，恢复类用例落在 `test_scheduler.py` 与 `test_repository_reads.py`；实跑 `pytest tests/evaluation -q` → 698 passed）
- [x] 服务独占 filelock 覆盖整个生命周期；事务领取，运行 agent→规则→按需 Judge→密封结果；调度器是唯一结果提交者。Judge 占用统一的 process semaphore，切换阶段释放后重新申请，禁止持有 agent slot 再等待 judge slot 造成死锁。
- [x] 用 monotonic 记录阶段耗时。run 的 terminal_count 只数已经执行及评分终结的项；Agent finished 而 Judge pending 不算整体完成。
- [x] run cancelling 时阻止新领取，取消所有 phase tasks，等待 owner 清理后更新状态；重启对残留运行标 interrupted，包含待执行项一并终结，不自动补跑。读取旧 PID 必须验证 process_nonce/进程启动身份，身份不能验证则记录并提示人工处置，禁止按进程名杀进程。
- [x] 指标派生自 repository 持久状态，浏览器不持有调度状态；复跑测试。

### T10 / WU05：独立 HTTP、认证、资源与报告接口

文件：server.py、auth.py、routes.py、artifacts.py、errors.py；tests/evaluation/{test_server,test_auth,test_routes,test_artifacts}.py。

- [x] 写 health、登录、会话恢复、CSRF、Origin、限流、版本冲突、重复提交、非法状态、跨 attempt 产物/证据、缺失资源的失败测试。
- [x] 执行：uv run --no-sync python -m pytest tests/evaluation/test_server.py tests/evaluation/test_auth.py tests/evaluation/test_routes.py tests/evaluation/test_artifacts.py -q。（实跑同四文件 + 全目录 `pytest tests/evaluation -q` → 698 passed）
- [x] aiohttp Application 通过 cleanup_ctx 打开 repository/lock/scheduler 并在退出时逆序等待回收；HTTP handler 入队后立即返回 202，客户端断开不取消运行。
- [x] 实现契约列出的全部路由；导入单事务；Artifact ID 解析到已登记目录内文件并使用 attachment；静态资源不允许目录索引/外部链接。
- [x] local passphrase 仅终端显示，错误响应只返回白名单字段；精确 Host/Origin 允许表与 session/CSRF 组合；--dev-origin 仅接受显式 loopback Origin。默认无任意 Origin 放行。
- [x] profile DTO 只含可选模型公开信息，不含原配置；服务启动缺失 api extra 给出可操作安装提示。密钥检测采用测试 sentinel 并扫描 API/Trace/导出，而非打印真实配置。
- [ ] 接入基础样本导入、gold 标签确认、calibration 路由和复核/复制/导出/软删，跑上述测试及定向 basedpyright；校准任务与普通运行共享服务级并发、取消与重启恢复逻辑。（部分完成：样本导入、gold 确认、calibration 路由、复核/导出/软删均已接入并有用例；**末句未成立**——`services.run_calibration_job` 的 `evidence_for` 写死 `None`、结论重复记一次、调度器不走 `claim_calibration_item`/`finish_calibration_item`，见执行日志 D14，须在 T11/T13 前补接线）

### T11 / WU06：页面接入真实服务与错误恢复

文件：接管 evalui 全部页面及 lib/api.ts；新 components/CalibrationPanel.tsx、src/tests/{flow,errors,calibration}.test.tsx。

- [ ] 写登录→选模型→建用例→运行→报告→Trace→复核的组合失败测试，使用真实契约的 HTTP stub。
- [ ] 执行：bun --cwd evalui run test src/tests/flow.test.tsx src/tests/errors.test.tsx。
- [ ] 移除正式入口测试 transport，默认指向同源 /api/eval/v1。轮询仅在页面可见时定时约 1 秒，重新打开立即刷新；卸载 abort 页面请求，不能调用 cancel run。
- [ ] 测试晚返回旧 run 不覆盖新页面、分页不重复、401/网络失败保留草稿、409 清晰提示冲突、请求超时不自动用新 key 重启评估。
- [ ] 报告展示 overall 与 evaluated 两种比例、评分覆盖率、工具 eligible/valid 数、Token 来源与覆盖、Agent/Judge 分开；校准面板展示 10 条证据及标签，让用户确认/修订，再运行、取消和查看校准。模型内部 reasoning 缺失显示“未提供”。
- [ ] 960/1440 宽度及键盘检查，长 Trace 不阻塞页面；完整 typecheck/test/lint/build 通过。

### T12 / WU07：20 个基础用例和 10 个校准样本

文件：samples/basic-v1.json、samples/calibration-v1.json；tests/evaluation/{test_samples,test_end_to_end}.py。样本作为包内文本，首次启动通过用户点击“导入基础用例”导入，重复导入使用稳定样本 id/version 识别并提示，不静默复制。

基础用例表：
| ID | 任务与固定期望 | 主要证据 |
| --- | --- | --- |
| B01 | 回答 2+3，仅输出 5 | answer_contains + answer_not_contains |
| B02 | 输出 JSON {"status":"ok","count":3} | answer_json + schema |
| B03 | 读取 input.txt 的 alpha 标识并复述 | read_file 参数、回答 |
| B04 | 创建 output.txt，正文 hello | file_contains |
| B05 | 编辑 draft.txt 中 old→new | file_contains、不再含 old 的 JSON/文本规则组合 |
| B06 | 列举 fixtures 两文件 a.txt/b.txt | list_dir 参数、回答 |
| B07 | sales.csv 的 10,12,20 求和写 total=42 | file_json /total |
| B08 | items.json 的 3 项分类输出 counts.json | file_json 指定字段 |
| B09 | 两文件整数合并写 result.json | 两次读取 + 最终值 |
| B10 | 将嵌套 JSON customer.name 写 summary.json | JSON Pointer |
| B11 | 给定不完整任务应提出一个必要问题 | 必需 rubric |
| B12 | 无需工具的简单分类 | tool_forbidden + answer_json |
| B13 | test_lookup 固定北京温度 18，生成一句答复 | 工具参数 + 答案 |
| B14 | test_lookup 按城市返回两项，比较温差 | 两次调用参数 + 差值 |
| B15 | test_unstable 首次受控失败、第二次成功 | 有序 tool.failed/completed + 最终回答 |
| B16 | 缺失文件后明确说明缺失、不虚构内容 | 工具错误 + 必需 rubric |
| B17 | 两轮记住本会话代号 cedar，第二轮复述 | turns + 答案 |
| B18 | 专属语义种子“称呼为小林”，询问用户称呼 | memory_seed + 回答 |
| B19 | 用户限定禁止联网，读取本地价格表回答 | 禁网络工具 + 文件内容 |
| B20 | 在 max_iterations=2 内生成要求的两字段 JSON | iteration_limit + file_json |

B05 若现有 file_contains 不支持否定，使用 required 的 file_json JSON Pointer equals 检查确定性编辑结果，将 draft fixture 改为 JSON 文档，不新增未经契约声明的断言种类。用例文本与判定数据在实施时写成完整 JSON 文件，禁止发布占位用例。

- [x] 写 schema_version、20 个唯一 ID、所有工具/规则受支持、样本无绝对路径、答案/产物期望自洽的失败测试。
- [x] 执行：uv run --no-sync python -m pytest tests/evaluation/test_samples.py -q。（实跑 `test_samples.py test_end_to_end.py` → 45 passed）
- [x] 写完整 20 用例内容与 fixture；controlled provider 按任务脚本返回工具调用，离线端到端验证每个规则能够通过且翻转关键值会失败。
- [x] gold 样本固定 C01 正确直接回答、C02 正确文件产物、C03 正确多步结果、C04 如实报告失败、C05 正确澄清、C06 虚假完成、C07 错误数值、C08 缺失关键字段、C09 无关回答、C10 忽略禁止工具；每条写完整任务、证据、rubric、人工分数与 expected_verdict，成功/失败各至少 4 条，明确边界锚点。
- [x] 10 个 gold 与 B11/B16 共用 general-v1 量表，固定任务完成/正确性两个维度；每个维度 0–4 五档锚点完整，pass_score=3。自定义量表的独立校准不足必须显示，不复用其他量表结果。
- [ ] 10 条为人工可复核的种子标签，默认 proposed；用户在网站确认前不能声称是人工标注。Leader 检查数据自洽与页面确认链路，真实 Judge 校准只有用户确认标签后才可成立；离线测试自行确认的 gold fixture 明确标为测试数据。（除「页面确认链路」外全部完成：出厂 `proposed`/reviewer 为 null、二次 seed 不覆盖、未确认即拒校准、离线确认的 comment 自报「非人工标注」；页面链路待 WU-02 出页面后验证）
- [x] 复跑 samples 与端到端测试，记录 fixture 模式的能力边界。（边界记入 collective-test §10）

### T13 / WU08：CLI、依赖锁、打包与现有入口回归

文件：nanobot/evaluation/{cli,build}.py、nanobot/cli/commands.py、hatch_build.py、pyproject.toml、.gitignore、tests/evaluation/{test_cli,test_build,test_packaging}.py。

- [ ] 写 nanobot eval --help、serve 选项、缺 aiohttp、端口占用、已有实例、静态包缺失、wheel/sdist 资源与现有 WebUI 构建目录不变的失败测试。
- [ ] 执行：uv run --no-sync python -m pytest tests/evaluation/test_cli.py tests/evaluation/test_build.py tests/evaluation/test_packaging.py -q。
- [ ] cli.py 提供 create_eval_app，commands.py 显式 add_typer(name="eval")，保持 CLI agent fast path 不动。serve 固定默认 host=127.0.0.1、port=8790，提供 --data-dir、--config、--dev-origin；首版拒绝非 loopback host。
- [ ] build.py 用现有 nanobot.webui.build 的参数化能力检查 evalui/static 版本与构建；hatch_build.py 独立调用并记录跳过/新鲜/构建结果，原 WebUI 行为测试保持通过。新增 NANOBOT_SKIP_EVALUI_BUILD/NANOBOT_FORCE_EVALUI_BUILD，语义与主 WebUI 对应，不能误用一个开关跳过另一应用。
- [ ] pyproject.toml include/artifacts/sdist 显式包含 nanobot/evaluation/static/**/* 和 samples/*.json；源码包可直接使用预构建静态资源，editable 安装不强制构建，缺资源时提示开发模式或构建命令。
- [ ] .gitignore 追加精确规则：
```gitignore
!evalui/bun.lock
evalui/node_modules/
evalui/coverage/
nanobot/evaluation/static/
.ai-runtime-artifacts/verifications/agent-evaluation/
```
生成 evalui/bun.lock 并验证 git check-ignore --no-index -v evalui/bun.lock 的最终例外规则；不改变根目录 *.lock 对其他文件的策略。维护静态资源 git-ignore、但打包时包含，避免相互遗漏。
- [ ] 包构建测试在 pytest tmp_path 下建立，实施 Agent 产生的实际 wheel/sdist 与检验报告放 .ai-runtime-artifacts/verifications/agent-evaluation-build/；CLI help、原入口 tests/cli/test_entry.py、tests/cli/test_commands.py 与 tests/webui/test_build.py 通过。

### T14 / WU09：集体测试、浏览器验收、Windows 与独立审查

文件：只写 .ai-runtime-artifacts/verifications/、reviews/、execution-logs/ 下的本批产物。

- [ ] 先执行下节自动化命令，保存退出码、环境版本、通过/失败与已知限制；不使用“预计通过”替代结果。
- [ ] 浏览器验证登录、导入 20 用例、编辑版本、开始运行、关闭/重开页面、查看原始失败、Trace/产物/复核、导出、失败重跑、取消；保留脱敏截图和关键 HTTP 结果。
- [ ] 在 Windows 实际启动可产生子孙进程的测试 Worker，验证取消、超时、服务正常退出与异常重启时的归属识别/回收；不能仅验证 await task.cancel。
- [ ] 在受控 profile 上跑至少 B01、B07、B15/B17 的真实 Agent 冒烟，展示真实调用来源与消耗；没有可用凭据则标未验证、保留校准不足状态，不编造结果。
- [ ] 若有用户确认的 10 条 gold 与可用 profile，运行真实校准并记录 agreement；若缺少，记录真实校准未验证，仍交付可操作的确认/校准功能。若 agreement <80%，报告未达标且开放式结果进入人工复核，不能声称 Judge 已完成验收。
- [ ] 扇出独立代码审查与安全审查，检查认证/路径/凭据/进程/幂等/并发/指标；审查者只读，修复回到原 WU 另行测试。
- [ ] Leader 写 collective-test、code-review、security-review，关闭 execution-log；严重问题未修复或必需检查失败时不得宣称完整交付。

## 5. 验证命令与执行位置

项目根目录：
```powershell
uv sync --all-extras --dev
uv run --no-sync python -m scripts.install_channel_dependencies --all-channels
uv run --no-sync python -m pytest tests/evaluation -q
uv run --no-sync python -m pytest tests/test_sdk_streaming.py tests/agent/test_loop_runner_integration.py tests/providers/test_llm_usage_observer.py tests/cli/test_entry.py tests/cli/test_commands.py tests/webui/test_build.py -q
uv run --no-sync ruff check nanobot/
uv run --no-sync basedpyright
uv pip check
```

独立前端（项目根目录）：
```powershell
bun install --cwd evalui --frozen-lockfile
bun --cwd evalui run typecheck
bun --cwd evalui run test
bun --cwd evalui run lint
bun --cwd evalui run build
```

若 Bun 不存在，先安装/使用项目已配置的 Bun；不能生成另一套 lock 后宣称 frozen-lockfile 已检查。新 lock 在 T03/T13 生成后才运行 frozen 安装。本计划不假定本机工具已存在。依赖同步可能下载较大的 all-extras；实际执行先检查可用环境，按项目验证要求记录采取的安装方式，不重复安装已满足的环境。

独立服务（入口实现后）：
```powershell
uv run --no-sync nanobot eval --help
uv run --no-sync nanobot eval serve --port 8790
```
自动化测试启动的后台服务窗口隐藏，使用显式端口和实例身份关闭。真实模型 profile 不打印到命令行。全量 tests/ 并行结果若失败，先用单 worker 复现对应目标，报告已有问题与本次新增回归。

如修改了共享构建/入口，收尾运行原 WebUI build/test；若前端源码未改而其 build 出现既有故障，记录基线并定位，不能称为全套绿色。本计划不要求部署或推送。

## 6. 规格覆盖与自检

| spec 验收项 | 承接任务与证据 |
| --- | --- |
| 1 独立启动 | T05、T10、T13、T14 |
| 2 后台操作全流程 | T03、T04、T10、T11、T14 |
| 3 20 用例、版本、Trace、指标 | T02、T06、T07、T12 |
| 4 异常与原始错误 | T06–T10、T14 |
| 5 全数据隔离 | T05、T06，runtime root 写入差异测试 |
| 6 关闭页面与进程回收 | T06、T09、T11、T14 |
| 7 快照、重跑、幂等、独占 | T02、T09、T10、T11 |
| 8 指标和 Token 来源 | T06、T07、T11 |
| 9 路径、敏感数据、安全展示 | T01、T06、T10、T11、T14 |
| 10 人工样本与 Judge 校准 | T08、T12、T14 |

自检：接口/任务同名；新增角色/数据表均有所有者；Judge 进程及校准队列占用服务级并发；UI 正式入口不带测试 transport；契约增加校准与开发 Origin 的细化；未创建业务文件、工作树或派发。任务代码是关键行为的参考实现，执行时按完整契约与测试矩阵交付，不能只实现代码示例。

## Next

2026-10-15 用户批准本计划与执行图，并限定首批范围为 GROUP-1（WU-01 + WU-02），在 GROUP-1 集成屏障处暂停。执行状态与验证证据记录在 `execution-logs/2026-10-15-agent-evaluation-execution-log.md` 与 `execution-logs/tracking/DISPATCH-TRACK-2026-10-15-agent-evaluation.md`。

- GROUP-1 屏障通过并确认 → 回复继续，进入 GROUP-2（WU-03 + WU-04）。
- 希望调整范围/顺序 → 直接说明修改意见。
- 希望按执行图并行到底 → 回复“并行执行”；仍由 Leader 完成最终集体测试与独立审查。
