# 评估分支前端 UI 浏览器验证（2026-10-23）

分支：`harness/agent-evaluation-calibration`（2eb4cd5 + 本地修复）
环境：gateway（`.venv/bin/nanobot gateway`，:8765 + 内嵌评估服务 :8790）+ `webui` Vite dev server :5173，Qoder 内置浏览器（`#/?bootstrapSecret=...` 自动认证）。

## 目的

按用户要求「在前端 UI 进行测试」：把应用跑起来在真实浏览器里冒烟验证，重点是评估模块 UI。基线事实：本分支前端评估部分只落地了 `EvaluationStatusBadge` 组件（2eb4cd5，83 行），无消费方、无 `settings.evaluation.status.*` i18n 键、无 Settings 评估页——计划文档 WU-10~12（API 客户端 / Settings 评估页 / 数据集 Tab）尚未实现。

## 验证步骤与结果

| # | 项目 | 结果 |
| --- | --- | --- |
| 1 | gateway 启动 | 首启崩溃 → 修复 ① 后正常；:8765、:8790 均监听，评估服务随 gateway 启停 |
| 2 | 应用加载 / 自动认证 | 欢迎页渲染（英文 locale：hero、快捷任务、composer、模型 deepseek-flash、agent 知序指挥官） |
| 3 | 侧栏 | 导航项齐全（Assistant/Creative/Agents/Memory/More/Settings）；项目（zhixu、图片-001）与 Topics 列表从 gateway 实时加载 |
| 4 | Settings 总览 | Token 用量图（含真实历史数据）、AI/Capabilities/System/About 各区块正常，Gateway Ready |
| 5 | 评估服务连通性（页面上下文 fetch） | `/health` 200；`/auth/login` 200 → 会话 cookie；`/auth/session`、`/profiles`（model:deepseek-flash + 受控执行器/法官）、`/overview`、`/suites`、`/datasets/template`（真实 xlsx 5703B）全部 200 |
| 6 | 评估 API 错误路径 | 缺 `suite_id` 422、不存在 404；修复 ② 前浏览器报 `Failed to fetch`（无 CORS 头），修复后错误 JSON 可读 |
| 7 | 聊天链路（真实发送） | 发送「reply with OK only」→ WS → 记忆检索注入 7 条 → LLM 调用 → deepseek key 欠费报错 → UI 渲染「Model provider quota is unavailable」横幅 + 会话标题自动更新。链路全通，回复内容受账号额度限制 |
| 8 | 控制台 | 修复后无新增报错；历史 CORS/500 条目均对应修复前/重启间隙 |

## 修复内容

### ① gateway 启动崩溃（阻塞级）

`nanobot/cli/gateway_runtime.py:1045`：`channels.webui_gateway()` → `channels.webui_gateway`。
`ChannelManager.webui_gateway` 是 `@property`（`nanobot/channels/manager.py:158`），356c22f 引入的评估装配代码把它当方法调用，gateway 一启动即 `TypeError: 'GatewayServices' object is not callable` 崩溃。

### ② 评估服务错误响应缺 CORS 头

`nanobot/evaluation/server.py` `_cors_middleware`：`guard` 对 `HTTPException` 选择重新抛出（aiohttp 协议层转响应，这是刻意的，见 guard 内注释），异常不经过中间件的 response 分支，导致所有 4xx/5xx 错误响应都没有 `Access-Control-Allow-Origin`，浏览器把契约错误 JSON 当网络错误（`TypeError: Failed to fetch`），前端永远读不到 `VALIDATION_ERROR`/`RECORD_NOT_FOUND` 等错误体。
修复：CORS 中间件补 `except web.HTTPException`，在异常的 `headers` 上追加 CORS 头后原样 raise。curl 复验 422/404 均带 `Access-Control-Allow-Origin: http://127.0.0.1:5173` + `Allow-Credentials: true`；浏览器端 401 错误体可读。
回归：`pytest tests/evaluation/test_server.py test_routes.py test_auth.py -q` → **82 passed**。

## 缺口与观察（非本次修复）

1. **评估 UI 未上线**：Settings 无「评估」入口，徽标组件无消费方、i18n 键未补（fallback 英文文案）。WU-10~12 待实施后才有可点的评估界面。
2. **deepseek key 欠费**（账号问题，非代码）：LLM 调用返回 quota 错误，gateway 已正确把错误映射为 UI 横幅；充值后聊天与评估运行才能出真实回复。
3. **`/api/sessions` 首次请求耗时 126s**（gateway 慢路由日志，最终 200）：疑似启动期与记忆系统初始化争用，单次出现，建议后续单独排查。
4. 浏览器为 Qoder 内置隐藏视口，指针交互不可用，验证通过 hash 导航 + a11y 快照 + `evaluate_script` 驱动完成；截图能力不可用与本仓库无关。
