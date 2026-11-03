---
artifact: verification-lite
route: leader-direct | 拉取后回归 + 浏览器前端验证
skills:
  - verification-before-completion
skills_evidence:
  - ~/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户原话「拉取项目」→「继续进行测试，用浏览器进行前端测试」
created_at: 2026-10-26
tier: 1
---

# 评估可靠性拉取回归 + 浏览器前端验证（Tier 1）

## 范围

- 拉取：分支 `harness/agent-evaluation-calibration`，`2fb9e5d` → `21ddf78`（13 个提交，fast-forward）。
- 本次改动文件：
  - `webui/src/i18n/locales/{en,es,fr,id,ja,ko,pt-BR,vi,zh-CN,zh-TW}/common.json`（各 +4 键）
  - `webui/src/tests/evaluation-i18n-parity.test.ts`（新增回归用例）
  - `tests/evaluation/test_execution_modes.py`、`tests/evaluation/test_processes.py`、`tests/agent/test_auto_compact.py`（跨平台用例健壮化）
- 路由判定：Tier 1（Leader 直做）；未派子 Agent（单一执行面，串行依赖强）。
- 验证环境：gateway（`.venv/bin/nanobot gateway`，:8765 WS + :8790 评估服务 + :18790 health）+ Vite dev server :5173 + Chrome 155（Playwright `channel=chrome`，无需下载浏览器）。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `git fetch --all --prune` + `git pull --ff-only` | ✅ 快进 13 提交，与远端 0/0 |
| `.venv/bin/pytest tests/evaluation tests/cli/test_eval_commands.py -q` | ✅ **926 passed**（修前 924 passed / 2 failed） |
| `.venv/bin/pytest tests/evaluation tests/cli/test_eval_commands.py tests/agent/test_auto_compact.py -q` | ✅ **979 passed**（含 ③ 的健壮化用例） |
| `cd webui && ./node_modules/.bin/vitest run` | ✅ **152 files / 1912 passed / 2 skipped**（修前 151 / 1909） |
| `cd webui && ./node_modules/.bin/tsc -p tsconfig.build.json --noEmit` | ✅ 0 error |
| `node browser-e2e/browser-test.js`（Playwright + Chrome，复现说明见同目录 README.md） | ✅ **24 步 / FAIL=0**（修前 FAIL=11） |
| `.venv/bin/pytest -q --tb=no -rf`（全量） | ⚠️ **10080 passed / 40 failed / 21 skipped**（失败全部为分支既有红灯，见下） |
| `cd webui && eslint src --max-warnings 0` | ⚠️ 7 error（全部位于本次未改动文件：`IdentityView.tsx`、`useFileSaver.ts`、`useZhixuStream.ts`、`browser-url.ts`） |

## 浏览器验证明细（`browser-e2e/browser-test.js`，截图与报告同目录）

| 步骤 | 结果 |
| --- | --- |
| 首页加载 + bootstrap 自动认证（`#/?bootstrapSecret=`） | PASS 无密码页直接进应用 |
| Settings → Evaluation 分区 | PASS 渲染 1935 字符 |
| 评估 Tab：Overview / Runs / Datasets | PASS×3，无新增 console 错误 |
| 运行详情抽屉打开 | PASS（命中真实运行「团队写作回归 · Team」） |
| 运行详情内 Reliability scorecard / Manual review | PASS×2 |
| 评估 API：login / session / health / profiles / overview / suites / runs / calibration-samples / datasets-template | PASS×9（全 200） |
| 评估 API 错误路径 `/runs/does-not-exist` | PASS 404 + `{"error":{"code":"RECORD_NOT_FOUND"}}` 契约正确 |
| 窄视口 390px 横向溢出 | PASS（scrollWidth == clientWidth） |
| 控制台错误 | PASS error=0（已排除自查请求的 401/404） |
| i18n 裸键外泄扫描 | 修前 **FAIL** → 修后 **PASS 未发现裸键** |

## 本次修复

### ① 评估 UI 4 个 i18n 键缺失 → 页面漏出裸键（真实缺陷）

- 现象：评估页直接渲染 `settings.evaluation.reliability.calibration.unknown`、`.scorecard.taskCompletionHint`、`.scorecard.qualificationHint`、`.reviews.failed`。
- 根因：4 个键在 **10 个语言包中全部缺失**；`i18n-dock-parity.test.ts` 只校验 `dock.*`，未覆盖 `settings.evaluation.*`，所以没被拦住。
- 修复：10 语言包各补 4 键（40 行新增，无重排）；新增 `webui/src/tests/evaluation-i18n-parity.test.ts`（消费键存在性 + `settings.evaluation` 键集跨语言一致 + 4 个回归键定点校验）。
- 证据：临时删除 `en` 的 `calibration.unknown` → 新用例 3 条断言全部失败；还原后全绿。浏览器复测裸键 0 处。

### ② 拉取带入的 2 个跨平台用例缺陷（其一会挂 Linux CI）

- `tests/evaluation/test_execution_modes.py::test_session_checkpoint_path_fits_windows_limit`：用 `"D:/..."` 构造路径，POSIX 下 `Path.is_absolute()` 为 False → `attempt_paths` 抛 `RUNTIME_ROOT_NOT_ABSOLUTE`。**CI 矩阵含 ubuntu-latest，该用例在 Linux 上同样必挂**。修复：改用等长（138 字符）POSIX 绝对路径跑同一套字符数断言，并额外用 `PureWindowsPath` 复核真 Windows 场景的阈值结论。
- `tests/evaluation/test_processes.py::test_grandchild_is_recycled_with_the_job_tree`：`_posix_process_group` 依赖 `/proc`（Linux 专有），macOS 无 `/proc` → 枚举为空。修复：枚举断言按平台收紧（`HAS_PROC`），保留真正契约断言「孙进程必须随进程组一起死」（macOS 走 `killpg`，实测通过）。

### ③ 分支既有的脆弱用例（与本次拉取无关，但已红）

- `tests/agent/test_auto_compact.py::TestIdleScanThrottling::test_configured_idle_scan_interval_throttles_checks`：用固定 4 元素 tick 迭代器桩 `time.monotonic`，而该桩改的是**全局 `time` 模块对象**，`AgentLoop.__init__` 间接触发的 `SubagentExecutionRepository.prune()`（`nanobot/subagent_executions/repository.py:392`）也会消耗 tick → 序列错位 → `StopIteration`。修复：改为可控游标（`clock["now"]`），测试意图（首次放行 / 同刻节流 / 未到间隔节流 / 到点再放行）逐条保留且不再与调用次数耦合。`tests/agent/test_auto_compact.py` 现 53 passed。

## 未验证项 / 遗留风险

- **全量套件 40 条失败为分支既有红灯，与本次拉取和本次改动无关**，证据：在拉取前提交 `2fb9e5d` 的临时 worktree 上复跑同批用例仍失败（websocket 5+10+1、tools 7、teams e2e 1、cli 14 条）。分布：`nanobot/channels/websocket/tests/*`（16）、`tests/tools/test_{mcp_probe,mcp_tool,web_fetch_security}.py`（7）、`tests/cli/test_commands.py`（14，CI 矩阵已 `--ignore`）、`tests/cli/test_tui_launcher.py`、`tests/agent/tools/test_subagent_tools.py`、`tests/channels/test_websocket_application_boundary.py`、`tests/webui/test_teams_e2e.py`。典型原因：websocket 通道现在会先推 `subagent_execution_snapshot` 事件，旧断言只认 `message`。**未修**——超出本次拉取范围，建议单独开 spec。
- eslint 7 条报错位于本次未改动文件（react-hooks 规则缺失 + `no-useless-assignment`），属既有问题，未处理。
- 浏览器验证未覆盖：真实 LLM 回合（`deepseek` key 额度不足，评估运行无法产出真实回复）、多浏览器、触屏交互。
- 评估服务走内存内嵌模式，未验证独立进程部署形态。

## Next

- 任务完成 → 无需暂停（除非用户要求 commit/MR）。
- 分支既有 40 条红灯 → 建议另起一轮专项（先 spec 再动手）。
