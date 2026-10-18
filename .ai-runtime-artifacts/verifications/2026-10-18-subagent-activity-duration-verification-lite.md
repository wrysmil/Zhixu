---
artifact: verification-lite
route: 缺陷调查 -> systematic-debugging -> Tier 1 Leader 直做
skills: [systematic-debugging, test-driven-development, verification-before-completion]
skills_evidence:
  - /Users/mima0000/.qoder-cn/plugins/cache/qoder-marketplace/superpowers/6.3.0/skills/systematic-debugging/SKILL.md
source:
  - webui/src/components/thread/SubagentExecutionActivity.tsx
  - webui/src/tests/subagent-execution-panel.test.tsx
created_at: 2026-10-18
status: verified
tier: 1
---

# 子 Agent 工具活动时长全显示 "0 sec" 修复

## 现象

用户在右侧「Sub-agent execution」详情 EXECUTION 区看到每条工具活动都是 `0 sec`，判断有问题。

## 根因（分层取证）

- 数据层：ledger / 只读 API 实测每条 activity 的 `finishedAt - startedAt` 仅 ~1–2 毫秒
  （`read_file` 读本地文件本就极快；整次 `durationMs`=10665ms，工具批次间隔 ~2s 是模型推理时间，不计入单条工具窗口）。
- 前端层：`SubagentExecutionActivity.tsx::formatDuration` 用 `Math.floor(ms/1000)` 取整到整秒、**无毫秒档**，
  亚秒时长全部塌成 `0 sec`。头部 `6 · sec / 10 · sec` 正常（总时长 >1s）。
- 测试层：既有 fixture 时间戳都是整秒偏移（08:00:05→08:00:08），从未覆盖亚秒，故漏检。

结论：显示粒度缺失，非测量 bug。

## 修复

- `formatDuration` 增加亚秒档：`ms < 1000` 时返回毫秒（`Math.max(1, Math.round(ms))`，避免 `0 ms`），
  ≥1s 仍走秒/分/时；`formatUnit` 扩展 `millisecond` 单位与 `ms` 回退后缀。
- 新增回归用例 `renders sub-second tool activity durations in milliseconds, not 0 sec`
  （420ms→`420 ms`、1.676ms→`2 ms`，并断言不再出现 `0 sec/0秒`）。

## 证据（本轮实跑）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 定向单测（先 RED 后 GREEN） | `vitest run src/tests/subagent-execution-panel.test.tsx` | 16/16 通过（含新增 1 条） |
| 类型 | `tsc -p tsconfig.build.json --noEmit` | 退出 0 |
| Lint | `eslint --config eslint.config.js <两文件> --max-warnings 0` | 退出 0 |
| 构建 | `vite build`（输出 `nanobot/web/dist`） | 退出 0，仅既存 >500kB chunk 提示 |
| 真机复核 | browser-use 硬刷新 :8765，打开 invocation `d3065d7e` | 5 条 `read_file` 活动右侧由 `0 sec` → `2 ms`；控制台 0 error/warning |

## 说明 / 未覆盖

- 单条工具时长语义 = 工具自身执行耗时（本地读文件即 ~ms）。模型每轮 ~2s 推理耗时不归属任一工具行；
  若要在时间线体现「每步墙钟间隔/模型延迟」需另设 phase/model 计时，属独立需求，未在本次范围内。
- 未跑前端全量回归（按用户既往要求，仅定向 + 构建 + 真机）。
