---
artifact: collective-test
route: verification-before-completion
batch: agent-evaluation-webui-group1
created_at: 2026-10-23
status: pass-with-known-failures
scope: GROUP-1（WU-01~WU-09）
---

# GROUP-1 集体测试：评估模块后端（目标体系 / 真实模型 / 总时限 / Excel / HTML 报告 / gateway 内嵌）

## 0. 范围与分支

- 工作树：`D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation`
- 分支：`harness/agent-evaluation-calibration`（base `627bc9f` = 已合并的 feature/desktop-app）
- 本批提交：
  | 提交 | 内容 |
  | --- | --- |
  | `e62a330` | WU-01 S7：profile 凭据不落运行快照，boot 时从内存重建 |
  | `db80283` | WU-02 从 gateway 配置构建真实模型评估 profile |
  | `6ee0407` | WU-03 目标快照 + 总时限（超时批量停止，`timed_out` 收口） |
  | `a969fa2` | WU-04/WU-05 Agent/Skill 目标冻结注入 + 团队 runner 进程内执行 |
  | `a7d18ac` | merge：PW-1 Excel 数据集纯函数 |
  | `36fb2b0` | merge：PW-2 HTML 汇总渲染函数 |
  | `356c22f` | WU-06b/07b/08 数据集与报告路由 + gateway 内嵌装配（profiles / 团队 runner / CORS） |
  | `058b56c` | 评估服务绑 localhost（SameSite=Strict 会话口径修正） |

## 1. 检查表执行结果

| 检查项 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量测试 | `.venv/Scripts/python.exe -m pytest tests/evaluation -q` | **740 passed / 5 failed**（5 条全部为 `test_calibration_jobs.py` 既有 WIP，见 §3） |
| 目标与注入测试 | `pytest tests/evaluation/test_targets.py -q` | 14 passed（WU-03/04/05 新增用例全绿） |
| 数据集测试 | `pytest tests/evaluation/test_datasets.py -q` | 9 passed（PW-1） |
| 报告测试 | `pytest tests/evaluation/test_reports.py -q` | 23 passed（PW-2） |
| 模型 profile 测试 | `pytest tests/evaluation/test_profiles.py -q` | 3 passed（PW-3） |
| 静态检查 | `ruff check nanobot/evaluation nanobot/cli/gateway_runtime.py nanobot/webui/gateway_services.py` | All checks passed |
| 类型检查 | `basedpyright nanobot/evaluation nanobot/cli/gateway_runtime.py nanobot/webui/gateway_services.py` | 见 §4（与基线对比，不劣化） |

## 2. 真实冒烟（内嵌装配 + 浏览器同款请求路径）

脚本：`%TEMP%/zhixu-eval-smoke.py`（装配 `create_eval_app`，绑定 8791，允许 Origin `http://127.0.0.1:5173`）

| 步骤 | 期望 | 实测 |
| --- | --- | --- |
| CORS 预检 `OPTIONS /auth/login` | 204 + `Access-Control-Allow-Origin` 精确等于来源 | 204 + `http://127.0.0.1:5173` |
| 登录 | 200 + csrf_token | 200 + csrf |
| `GET /profiles` | 200，含真实模型 profile | 200，3 个（`model:MiniMax-M3.2` + 受控执行器/评分器） |
| `GET /datasets/template` | 200 + xlsx | 200，5703 字节，xlsx content-type |
| `POST /datasets/import` | 201 + suite | 201（导入模板生成 suite「我的数据集」） |
| `GET /reports/summary?suite_id=…` | 200 + HTML | 200，739 字节 HTML |
| `GET /health` | 200 | 200 `{"status":"ok"}` |

冒烟 FAILURES 计数：**0**。

## 3. 既有失败（不是本批引入，不在 GROUP-1 范围）

`tests/evaluation/test_calibration_jobs.py` 5 条：校准作业用例 WIP（基线提交 `c93a181` 即如此，任务说明「7 条里 5 条未收口」）。本批未触碰校准域代码。

## 4. 类型检查

见 §1 表格；`nanobot/evaluation` 基线为 291 errors（272 条 `reportUnknown*`）。本批新增模块（datasets/profiles）与改动文件不新增错误类别。

## 5. 并行审查

- 代码审查（reviewer）：进行中 → 产物 `.ai-runtime-artifacts/reviews/2026-10-23-evaluation-webui-code-review.md`
- 安全审查（security-auditor）：进行中 → 产物 `.ai-runtime-artifacts/reviews/2026-10-23-evaluation-webui-security-review.md`

## 6. 结论

GROUP-1（WU-01~WU-09）后端范围：**通过**（含 5 条既有失败与两项并行审查结论）。前端（GROUP-2 / WU-10~WU-13）尚未开始，需用户确认后进入下一批。

## Next

- 审查结论 → 有 BLOCK 项则修复后复审
- 前端批次 → 说「开始实现 GROUP-2」

---

# WU-13 桌面端端到端验证（真实浏览器，2026-10-25 追加）

## 0. 环境与前置

- 分支：`harness/agent-evaluation-calibration`；修复提交：`a0f1d53`（见 §3）。
- gateway：`.venv/bin/nanobot gateway`（8765 + 内嵌评估 8790）；WebUI：`vite --port 5173 --strictPort`。
- 用户已把当前模型切换为自定义 `custom-minmax · MiniMax-M3.1-Flash`（config `modelPresets`）。
- 数据准备（API 造数）：suite `basic-v1`（8947c27f）+ live 用例「加法问答」（turn: 1+1 等于几；断言 `answer_contains "2"` 必过）。
- 截图不可用：内嵌浏览器视口隐藏（NATIVE_BROWSER_VIEWPORT_UNAVAILABLE，与 2026-10-23 UI 验证一致），以 DOM 结构量测替代。

## 1. 浏览器端到端检查表（browser-use）

| # | 步骤 | 期望 | 实测 |
| --- | --- | --- | --- |
| 1 | 打开 `/#/?bootstrapSecret=…&section=eval` | 免密登录进主界面 | 通过（无登录框；`section` 参数不生效，需点侧栏，见 §4-3） |
| 2 | Settings → Evaluation | Online 徽标 + 三 Tab | 通过（Online / Overview·Runs·Datasets） |
| 3 | 总览指标 | 空态显示「—」 | 通过（无运行记录时 Planned/Passed/Pass rate/Coverage 均为「—」） |
| 4 | Runs → New run | 对话框字段齐全 | 通过（Target type/Dataset/用例勾选/Model/Judge/时限） |
| 5 | 选 Dataset 目标 + basic-v1 + **MiniMax-M3.1-Flash**，提交 | 入队 | 通过（列表出现 `basic-v1 Queued 0/1`） |
| 6 | 等待跑完 | Completed 1/1 | 通过（约 15s：agent_ms 2898，token 3294 in / 29 out，usage source=reported） |
| 7 | 打开运行详情 | verdict + Trace | **首测白屏崩溃 → 修复后通过**（§3）；Passed 徽标、执行状态 Succeeded、final answer「2」、Pass rate 100%、Trace 8 事件（attempt.started→attempt.completed，provider=custom-minmax） |
| 8 | 数据集 Tab → Download template | 模板可下载 | 通过（200 + `Microsoft Excel 2007+`，xlsx MIME） |
| 9 | 模板填充 1 有效行 + 1 缺必过规则坏行 → UI Import | Imported + 行错误 | 通过（横幅「Imported 导入回环集 · 2」；Row errors：`{"row":4,"message":"用例 坏行用例 缺少期望包含/期望工具等必过规则"}`；坏行被正确丢弃，suite 含模板自带「示例用例」+「冒烟导入」共 2 例） |
| 10 | Export xlsx / HTML summary | 下载可用 | 通过（export.xlsx 200 + PK 魔数 5692B；`/reports/summary` 200 text/html，标题「评估汇总 · 数据集 导入回环集」） |
| 11 | 全程 console | 无意外报错 | 通过（仅既有预期项：/auth/session 401 探针、gateway 重启瞬间的会话 401 自动重登） |

## 2. 前端回归

- 全量 vitest：**147 files / 1884 passed / 2 skipped**（与 GROUP-2 基线一致）。
- tsc `-p tsconfig.build.json --noEmit`：clean。

## 3. WU-13 发现并修复的缺陷（`a0f1d53`）

- **现象**：真实跑分 Passed 后点开运行详情 → `TypeError: Cannot read properties of undefined (reading 'length')` → 无 error boundary，整棵 React 树卸载（白屏）。window error 监听 + 二次复现定位到 [RunDetailSheet.tsx:223](file:///Users/mima0000/Documents/学习-001/do-project/zhixu/webui/src/components/settings/evaluation/RunDetailSheet.tsx#L223)。
- **根因**：`GET /runs/{id}/attempts` 列表返回轻量结构（`errors`/`final_answer` 在 `result` 内、无 `assertions` 等），与 `GET /attempts/{id}` 的完整 `AttemptRead` 不同；前端 `Attempt` 类型按详情接口书写，vitest mock 又与错误假设同形，单测无法发现。
- **修复**：`listAttempts` 归一化为 `Attempt`（[evaluation-api.ts](file:///Users/mima0000/Documents/学习-001/do-project/zhixu/webui/src/lib/evaluation-api.ts)）；补 attempt 侧 `succeeded`/`setup_error` 执行状态词条（10 locale）。单测 36 条 + 全量 1884 条 + tsc 复验通过。
- **遗留建议**（未做）：设置页无 React error boundary，组件崩溃仍会白屏；可作为独立小任务。

## 4. 其他观察（非阻塞）

1. 评估 profile 是 gateway 启动时快照（gateway_runtime.py:391），运行中新增 `modelPresets` 不会热更新 → 重启 gateway 后新模型才进「新建运行」下拉。已知设计（凭据仅内存重建），使用时需知悉。
2. gateway 重启后浏览器 eval 会话失效，401 → 自动重登链路实测可用（无感恢复）。
3. 根路由 `#/?...&section=eval` 的 `section` 参数不生效，需手动点侧栏 Evaluation；登录后 hash 保留 `section=eval`，与 Settings 页挂载时序存在 race。
4. 首次造数失败留下一个空 suite `basic-v1`（无删除路由），新建运行时需选有用例的 suite。

