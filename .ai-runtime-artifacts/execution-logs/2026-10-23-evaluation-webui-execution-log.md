---
artifact: execution-log
route: orchestration
batch: agent-evaluation-webui-group1
created_at: 2026-10-23
status: group2-closed
---

# 执行日志：评估模块 GROUP-1（WU-01~WU-09）

## 1. 编排与并行

- 计划：`.ai-runtime-artifacts/plans/2026-10-23-evaluation-webui-plan.md`
- 调度：`.ai-runtime-artifacts/plans/2026-10-23-evaluation-webui-dispatch.md`
- 追踪：`.ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-23-evaluation-webui.md`

派发 4 个 worker（文件不相交）：

| Worker | WU | 范围 | 结果 |
| --- | --- | --- | --- |
| MW-1 | WU-01/03/04/05 | services/schemas/scheduler/repository/worker/runtime_factory + loop/nanobot/sdk | **中途终止**：WU-01、WU-03 已提交并通过全量回归；WU-04/05 由 Leader 接手完成 |
| PW-1 | WU-06a | `nanobot/evaluation/datasets.py` + 测试 | done（`df8b9f7`，9 passed） |
| PW-2 | WU-07a | `reports.py` 追加 `render_summary_html` + 测试 | done（`1eeb6fb`，23 passed） |
| PW-3 | WU-02 | `nanobot/evaluation/profiles.py` + 测试 | done（`db80283`，3 passed） |

## 2. 偏差与处置

1. **MW-1 终止**：用户判定该 worker 卡死并终止。Leader 复核工作树：WU-01（`e62a330`）与 WU-03（`6ee0407`）的实现完整、测试全绿，予以保留；WU-04/05 由 Leader 直接实现并提交（`a969fa2`）。
2. **MW-1 的 WU-03 实现优于计划**：deadline 从「领取首个 attempt」起算（排队不烧预算）、取消原因区分 timeout/user、收口幂等。计划中的「按 created_at 起算」被替换。
3. **PW-1 纠正断言字段名**：`tool_called` 实际字段是 `name` + 必填 `min_calls`；`tool_forbidden` 是 `names` 列表。计划里的 `tool` 字段名错误，已按源码修正。
4. **PW-3 纠正 bundle 形状**：bundle 必须全 camelCase（与 `config.json` 同形），并补 `modelPreset` 字段——worker 侧选预设读的是 `model_preset`；遍历对象改为 `config.model_presets`（`default` 是保留名，不能进 bundle）。
5. **worker 注入桩兼容**：worker 无条件传 `metadata=` 会打爆测试替身 `StubBot.run`（不收该参数）→ 9 条 `test_worker_orchestration` 变红。改为仅在 Agent 目标时传参。
6. **团队结论口径**：团队路径没有 worker trace 密封语义，直接复用 `_grade` 会把所有结论推进 review_required。给 `_grade` 增加 `trace_complete_override`，团队路径显式传 True。
7. **SameSite 与绑定地址**：评估服务原绑 `127.0.0.1`，而 WebUI 可能用 `localhost` 打开 → 站点不一致会让 Strict 会话 cookie 被浏览器拦。改为绑 `localhost`（覆盖 v4/v6 回环）。

## 3. 验证

见 `.ai-runtime-artifacts/verifications/2026-10-23-evaluation-webui-collective-test.md`：740 passed / 5 既有失败；ruff 全绿；真实冒烟 7 步全过（含 CORS 预检、真实模型 profile、Excel 导入、HTML 报告）。

## 4. GROUP-2（WU-10~WU-12，Leader 直做，2026-10-24）

用户指令「前端 wu10-12 继续实行」。Tier 1 直做，未开工作树，串行三个 WU：

| WU | 内容 | 提交 | 测试 |
| --- | --- | --- | --- |
| WU-10 | `webui/src/lib/evaluation-api.ts`：会话自动登录（session 探测 401 → bootstrap secret 登录）、CSRF 头、401 重置重试一次、Idempotency-Key、错误契约解析、下载/导出 | `ec61802` | 4 passed |
| WU-11 | Settings 评估页：总览指标/最近运行、Runs 表 + 状态筛选、NewRunDialog（目标类型/套件/用例/模型/评审/时限）、RunDetailSheet（尝试/Trace/取消/重跑/导出）、contracts+sidebar+page 注册、十语言文案、状态徽标键 camelCase 化 | `7bec0e4` | 3 passed |
| WU-12 | EvaluationDatasets：模板下载、xlsx 导入（结果 + 行错误）、导出 xlsx、查看用例、HTML 汇总；datasets Tab 接入 | `902e70f` | 5 passed（累计） |

### 偏差与处置

1. **十语言 i18n 从 WU-12 前移到 WU-11**：`i18n.test.tsx` 形状测试要求所有 locale 与 en 键完全一致，en 单独加键会红。WU-12 因此只剩数据集 Tab，提交信息相应调整（原计划标题含「与十语言文案」）。
2. **SettingsPage 总闸问题**：计划写「eval case 提到 `if (!settings)` 之前」，但 `renderSection()` 外层还有 `settings ? … : null` 总闸（SettingsPage 734 行附近），只改 switch 不生效。评估页改为在总闸外渲染（`activeSection === "eval"` 分支），不依赖 settings 载荷。
3. **PreviewApp 预览面**：`?preview=1` 无后端，`eval` 与 capability 页同样映射到 overview，避免预览页出现「评估服务离线」横幅。
4. **状态徽标 i18n 键 camelCase 化**：`evaluation-status.tsx` 原直接拼 snake_case 后端值；新增 `evaluationStatusKey()`（snake → camel）对齐词条表（timedOut/reviewRequired/notEvaluated）。
5. **删除 WU-10 遗留 `requestBlob`**（未使用，`tsc` noUnusedLocals 报 TS6133）；PreviewApp 同步补 `eval` 分支后 tsc 全绿。
6. **EvaluationDatasets 下载按钮改 async/await 处理器**：`.then` 链在测试替身返回 undefined 时崩（Cannot read properties of undefined），await 写法对真实/替身实现都稳。
7. **浏览器验证时发现的非问题**：首页 hash 直跳 `#/settings?section=eval` 偶发落在 overview（路由同步竞态），点侧栏 Evaluation 正常；`/auth/session` 401 探测是自动登录预期第一步。

### 验证

- `vitest run`（全量）：**1884 passed / 2 skipped**（149 文件，含 5 条评估组件测试 + 4 条 API 客户端测试 + i18n 形状测试）。
- `tsc -p tsconfig.build.json --noEmit`：干净。
- 真实浏览器（Vite 5173 + gateway 8765 + 内嵌评估 8790）：见 `.ai-runtime-artifacts/verifications/2026-10-23-evaluation-webui-wu10-12-verification.md`。

## 5. WU-13（桌面端端到端，2026-10-25 完成）

用户切换模型为 `custom-minmax · MiniMax-M3.1-Flash` 后执行。真实浏览器（browser-use）走通：自动登录 → 评估页 → UI 新建运行（Dataset 目标 + basic-v1 + MiniMax-M3.1-Flash）→ 真实跑分 Completed 1/1（verdict passed，final answer「2」，3294/29 tokens）→ 详情 verdict/Trace（8 事件）→ 数据集模板下载 + UI 导入回环（含行错误展示）→ Export xlsx / HTML summary。

- **发现并修复白屏缺陷**：`/runs/{id}/attempts` 轻量结构与 `Attempt` 类型不符，RunDetailSheet 渲染 `attempt.errors.length` 崩溃卸载整树；`listAttempts` 归一化 + 补 succeeded/setup_error 词条（10 locale），提交 `a0f1d53`。
- 复验：全量 vitest 1884 passed / 2 skipped；tsc 干净。
- 量测与观察见 `.ai-runtime-artifacts/verifications/2026-10-23-evaluation-webui-collective-test.md` §WU-13。

## 6. 三类目标评估（Agent / Skill / Team，2026-10-25）

按用户要求为普通对话、技能、团队各建一个数据集，走真实模型评估并导出 HTML 报告。

- 结果：Agent 2/2 passed（suite `40a9f87f`，run `3136610f`）、Skill 1/1 passed（suite `af939825`，run `5129b18b`）、Team 1/1 passed（suite `c1317940`，最新 run `fe7246d6`，约 124s）。
- 修复三处后端缺陷：
  1. `build_team_eval_runner` 建 run 但不跑 Leader turn → 注入 binding/turn id 驱动 `host.process_direct`，非终态收口为 failed；
  2. Leader 名册缺 `memberId` 导致 `team_dispatch` 反复被拒超时 → 名册渲染 memberId；
  3. `summary_report` 用最旧运行覆盖最新结论 → 改 first-wins。
- 回归：`tests/evaluation` + `tests/webui/test_eval_team_runner.py` + `tests/teams` 共 438 passed；ruff 干净。
- 报告与细节：`.ai-runtime-artifacts/verifications/2026-10-25-three-target-eval.md`（HTML 在 `2026-10-25-eval-reports/`）。

## 7. 下一步

- 提交未 push，等用户确认。三类目标评估的修复同样未提交。
- 可选小任务：设置页 React error boundary（组件崩溃不再白屏）。

