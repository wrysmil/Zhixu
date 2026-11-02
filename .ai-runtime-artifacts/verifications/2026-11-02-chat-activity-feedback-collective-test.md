---
artifact: collective-test
route: verification-before-completion
skills: [verification-before-completion]
source:
  - .ai-runtime-artifacts/execution-logs/2026-11-02-chat-activity-feedback-execution-log.md
  - .ai-runtime-artifacts/verifications/2026-11-02-chat-activity-feedback-verification-lite.md
created_at: 2026-11-02
status: passed_with_limits
---

# 聊天执行反馈集体测试

## 范围

本轮把四个工作单元合到一个可运行界面上做集体验证：运行标记与可读活动区、回复区上移、文件参数生成可见性、工具动作标题。验收页面使用真实 `ThreadViewport` / `AgentActivityCluster` / `MessageBubble` 组件与内存样例，不连网关、不调用模型。

## 用例与结果

| # | 场景 | 期望 | 结果 |
| --- | --- | --- | --- |
| 1 | 提交后模型尚未产出任何活动 | 持续可见运行指示；回复区起点贴近视口顶部（16px 留白） | PASS |
| 2 | 展开思考详情 | 详情可读（14px、可见溢出）；活动视口高度超过旧 180px 上限；标题展示真实描述 | PASS |
| 3 | 正文暂停、回合未结束 | 正文末尾仍显示本地化运行状态 | PASS |
| 4 | 文件参数尚未生成完 | 显示“正在准备文件编辑…”，且不输出文件内容 | PASS |
| 5 | 长正文 | 继续跟随末尾；用户上翻后不抢滚动位置 | PASS |
| 6 | 整轮完成 | 运行指示与 footer 运行状态同时消失 | PASS |
| 7 | 390px 窄屏 | 不横向溢出，活动详情可见 | PASS |
| 8 | prefers-reduced-motion | 转圈停止旋转但指示保留 | PASS |
| 9 | 等待态（无任何活动条目） | 不再出现空边框大框，改为强调色状态胶囊 | PASS（截图核对） |
| 10 | 深色主题 | 强调色、面板底色、扫光在深色下均可辨识 | PASS（截图核对） |

执行命令：

```bash
node .ai-runtime-artifacts/verifications/2026-11-02-chat-activity-browser.cjs
```

## 视觉判定

- 等待态：`2026-11-02-chat-activity-waiting.png` —— 单行胶囊，转圈与文案同色系强调色，无空框。
- 展开态：`2026-11-02-chat-activity-thinking.png` —— 柔和面板 + 内描边，顶边蓝色扫光，标题 15px 半粗。
- 正文暂停：`2026-11-02-chat-activity-text.png` —— footer 运行状态为强调色，肉眼可辨。
- 折叠态：`2026-11-02-chat-activity-file.png` —— 无边框、无底色，只剩一行标题 + 右侧箭头，不再像输入框。
- 窄屏：`2026-11-02-chat-activity-mobile.png`；深色：`2026-11-02-chat-activity-dark.png`。

## 回归

- `npx vitest run`（WebUI 全量）：2014 通过 / 2 跳过。
- `pytest tests/agent/test_runner_progress_deltas.py tests/agent/test_loop_progress.py tests/tools/test_tool_descriptions.py tests/providers/test_openai_responses.py`：176 通过。
- `npx tsc -p tsconfig.build.json --noEmit`：exit 0；`npx vite build`：成功。
- 触及文件 ESLint `--max-warnings 0`：exit 0；触及 Python 文件 `ruff check`：All checks passed。

## 限制

- 界面验收基于内存样例，不覆盖真实模型回合、真实文件写入与真实 WebSocket 事件时序。
- `pytest tests/agent` 仍有 4 项与本任务无关的失败（记忆/身份模板/MCP 关停时序），详见 verification-lite 的限制章节。
- 未做多浏览器矩阵，仅 Chromium headless。
- 未提交、未推送。
