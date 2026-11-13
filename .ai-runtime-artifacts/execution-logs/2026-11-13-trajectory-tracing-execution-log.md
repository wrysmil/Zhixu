---
artifact: execution-log
route: implementation (Tier 2)
source:
  - .ai-runtime-artifacts/specs/2026-11-13-trajectory-tracing-spec.md
  - .ai-runtime-artifacts/contracts/2026-11-13-trajectory-tracing-contract.md
  - .ai-runtime-artifacts/research/2026-11-13-deepseek-harness-tracing-research.md
branch: feature/trajectory-tracing
base_branch: feature/desktop-app
created_at: 2026-11-13
approved: true
approval_quote: 用户原话「根据这个方案执行任务把」「这个用一个新的分支实现」
---

# 执行轨迹 V1 执行日志

## 1. 分支与范围

从 `feature/desktop-app` 新建 `feature/trajectory-tracing`。按方案 A→B 纵向切片实现：
先让「输入 → 逐模型尝试 → 逐工具 → 输出 → 历史查询」闭环可用，再接截图主视图。

## 2. 批次 A：事实闭环（后端）

新增 `nanobot/trajectory/`：

| 文件 | 作用 |
| --- | --- |
| `models.py` | 事件词汇、ContentRef、Coverage、记录/事件 DTO |
| `policy.py` | 凭据脱敏、内联媒体剔除、UTF-8 截断、正文预算 |
| `store.py` | 单 writer SQLite（WAL + writer.lock）、事件与投影同事务、尾页/补页/正文分片 |
| `recorder.py` | 每次 run 的记录器，delta 合并、预算降级、fail-open |
| `capture.py` | runner/provider/tool 依赖的采集门面与 attempt 边界 |
| `context.py` | ContextVar 绑定 recorder 与 logical request |
| `service.py` | 按 workspace 管理写入 store、内存 store（临时聊天）与只读查询 |

采集点：

- `agent/loop.py`：可见轮次边界创建 recorder，绑定 ContextVar，记录用户输入；记忆/工作记忆注入记为 `context.injected`；轮次结束按结果写 `run.finished`。
- `agent/runner.py`：每个 iteration 记 `step.started`；`context_governor.prepare_request` 之后保存规范化请求快照（system/messages/tools/generation），生成 `logicalRequestId`。
- `providers/base.py`：`_run_with_retry` 每次物理调用生成 `modelCallId`，记录 started / 流式 delta / finished，重试前写 `model.retry.scheduled`；usage 仍走原 observer，不覆盖。
- `providers/fallback_provider.py`：fallback 时写 `model.route.changed`，不额外计模型请求。
- `agent/tools/execution.py`：调度前为全部调用写 `tool.requested`（含未启动项）；执行边界写 started/finished（succeeded/failed/denied/cancelled）；取消时未启动项标 skipped。
- `config/schema.py`：新增 `trajectory` 配置（enabled / mode / retentionDays / maxBodyMb / maxRunBodyMb）。
- `gateway_runtime.py`、`AgentLoop.from_config`：按配置初始化轨迹服务。

## 3. 批次 A2：读取与前端数据源

- `webui/trajectory_routes.py`：只读 GET 摘要 / records / events / spans / bodies；`caller_chats` 越权按 404 隐藏存在性；错误码 `TRAJECTORY_*`。
- `webui/settings_routes.py`：注册路由、GET-only 门禁、能力位 `trajectoryViewV1` + `trajectoryEventsV1`。
- `webui/gateway_services.py`：按 session → workspace 解析后绑定轨迹服务，并把提交批次转成 WS 帧。
- WebSocket 增量：
  - `trajectory_subscribe` / `trajectory_unsubscribe` 命令，绑定连接当前 chat。
  - 提交监听 → `TrajectoryBatchWireEvent` → `trajectory_batch` 帧（from/to/durable seq、recordUpdates）。
  - 客户端按 `appliedSeq` 应用；断线重连自动重订阅；批次不连续时走 HTTP 补拉；5s 轮询兜底。

## 4. 批次 B：轨迹主视图

- `webui/src/features/trajectory/`：types / parser / client / projection / useTrajectory。
- `webui/src/components/thread/trajectory/`：Toolbar（时长/轮次/调用/搜索/已加载计数）、三泳道时间带（点击定位、拖选闭区间）、按轮次分组的紧凑台账、右侧检查器（内容/输入/Schema/JSON、懒加载正文、复制）。
- `ThreadHeader` 增加中列 `viewTabs`；`ThreadShell` 增加对话/轨迹标签，轨迹视图隐藏时停止轮询并保留已加载状态；轨迹视图复用会话输入框。
- 10 个 locale 增加 `thread.trajectory.*` 文案。

## 5. 明确偏差与未完成项

1. **浏览器像素级验收未执行**：本轮完成类型检查、构建与组件/纯函数测试，未启动 gateway 在真实浏览器对照 1600×1000 截图。
2. **真实模型端到端未执行**：重试/fallback/工具/子 Agent/压缩均有单测或接线，但未在用户真实 provider 配置下跑一次完整会话。
3. **SDK 隐式重试不可见**：provider 内部 SDK 的额外网络重试仍标记为 `provider_call` 可见性，未宣称 `transport_attempt`。
4. **切换标签会重建输入框组件**：轨迹视图使用同一 composer 节点，但两视图切换时组件重新挂载，未保存半截草稿。

## 6. Next

- 在真实 gateway + 用户模型配置下做一次轨迹端到端验收与截图对照。
- 验证断线重连、慢消费者与 retention 前缀清理在真实数据量下的表现。
