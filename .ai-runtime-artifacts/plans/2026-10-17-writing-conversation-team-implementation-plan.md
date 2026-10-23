---
name: writing-conversation-team-implementation-plan
description: 创作空间对话团队改版实施计划（前端会话链路优先，再接团队工具与持久化）
status: approved
approved: true
approval_quote: "我授权按上述方案开始实现。必要的实施计划、接口契约和方案补充可以自行落盘后继续执行，无需再次等待确认。遇到普通、可逆的实现选择，自行判断并继续。"
source_spec: .ai-runtime-artifacts/specs/2026-10-17-writing-conversation-team-spec.md
date: 2026-10-17
---

# 创作空间对话团队改版 — 实施计划

## 0. 口径

- 唯一方案：`specs/2026-10-17-writing-conversation-team-spec.md`；Demo 只作视觉/交互参考。
- 复用既有 `AgentLoop` / `AgentRunner` / `TeamScheduler` / `SubagentManager`，不新增第二套模型循环。
- 先跑通「文章 ↔ 服务端绑定会话 → 右栏真实聊天」，再接写作工具、写入应用服务与保存握手。
- 不自动提交/推送；保留 `M memory/state.db`；过程产物只落 `.ai-runtime-artifacts/`。

## 1. 垂直切片

### WU-1 服务端文章会话绑定（会话链路地基）
- `writing` 库新增 `article_sessions`（workspace_key, article_id, chat_id, session_key, team_id, team_revision, created_at, updated_at）。
- `WritingSessionBinder`：校验文章存在；幂等 get-or-create；创建 `Session`（`websocket:<chatId>`）并写
  `metadata["team_binding"]`（物理团队 ID）与 `metadata["writing_article"]`（article_id）；保存会话。
- 新增 WS 命令 `bind_writing_session`：仅可信 WebUI；返回 attached/session_updated/hydrate 与
  `writing_session` 事件（article_id/chat_id/session_key/team_id/team_revision）。
- 启动时播种内置 `writing-team`：`TeamService.ensure_builtin_team(builtin_team_definition())`，
  allocator 避让用户团队，重复启动幂等；内置 Agent 由 `FACTORY_PROFILES` 提供。

### WU-2 前端右栏真实聊天
- `CreativeWorkspace` 改用服务端返回的 chatId/sessionKey；移除 `${articleId}:partner` 与旧 collab 通道依赖。
- 新 `WritingChatPanel`：`useSessionHistory` + `useNanobotStream` + `ThreadMessages`；历史、流式、连续追问、停止、重连补拉。
- 轻量 composer：`/` 任务菜单、`@` 本团队成员 / 文章内容菜单；只填入不执行；空态四条预设话术。
- 布局：正文主区 + 右侧聊天（默认 380px，可拖拽/记忆/折叠）；素材/提纲计数入口 + 抽屉；移除流程按钮/任务矩阵/阶段面板。
- 全局导航收敛「助手 / 创作 / 更多 + 设置」，保留旧路由与深链。

### WU-3 写作元数据与回合上下文
- 发送帧新增 `writing`：`article_id`、`revision`、`digest`、可选取区 `selection`、可选 `member_id`、可选 `task`。
- 入站层校验：文章存在、会话绑定匹配、revision/digest 与库一致；不合法拒绝，不静默降级。
- 发送前前端 `flush()` 自动保存（含 IME 合并），失败/冲突/离线时禁用发送或给建议卡。
- `TeamLeaderTurns` 从 msg.metadata + session.metadata 生成 `WritingRunExtension`；无绑定返回空。

### WU-4 四个团队专属工具
- `writing_read_article`（主编 + 全体成员）
- `writing_create_material`（主编 / 研究员）
- `writing_create_outline`（主编 / 结构编辑）
- `writing_update_article`（主编 / 写手）
- 工具工厂只在可信运行上下文注入：Leader（`LeaderTurnContext.extra_tools`）与成员
  （`TeamRunHandle.extra_tools_provider → TeamExecutionRequest.extra_tools → execute_frozen → _build_tools`）。
- 校验：内置团队物理 ID、成员角色、文章绑定会话、run 未取消、base revision/digest。
- 普通聊天/克隆团队拿不到工具（工具不进自动发现目录，且绑定校验失败即不注入）。

### WU-5 文章写入应用服务
- `nanobot/writing/apply.py`：CAS + 严格幂等（同键同载荷回原结果、同键异载荷 409，不裸 REPLACE）。
- 字段级变更记录 `writing_article_changes`：before/after、字段、成员、来源消息、请求指纹、结果 revision/digest、撤销状态。
- 支持 body（全文 / 区间）、materials（新增）、outline（替换 / 追加）三类写入与撤销。
- 撤销仅在 revision/digest 匹配时直接还原；否则返回 409 + 恢复建议。
- 取消后晚到写入：运行 handle 标记取消后，工具入口先拒绝。

### WU-6 选区动作与修改对比
- `ArticleEditor` 选区捕获（UTF-16 → 码点），工具条：问 AI / 改写 / 扩写 / 精简 / 更多。
- 只提问不写入；明确改写才携带 selection 与 operation。
- AI 写入后通过事件/刷新同步正文、revision、digest；显示变更卡（before/after + 撤销）。
- 切换文章清空选区引用与建议卡。

### WU-7 持久化同步与冲突
- 素材/提纲抽屉读取真实文章数据；写入成功后刷新 revision/digest 与列表。
- 自动保存、IME、并发编辑、版本冲突复用 `use-article-save-flow`；AI 写入不得覆盖作者新内容。
- 写后回执只在事务提交后返回；前端收到后刷新文章。

### WU-8 验证
- 后端：新增/相关 pytest、`ruff check`、`basedpyright`（存量基线失败单独归因）。
- 前端：`npm run test`、`tsc`、`npm run build`。
- 浏览器：1280/1440/小屏；空文章、连续追问、`/`/`@`、选区改写、停止/重连、切文章。
- 真实模型不可用时记录原因，不以 mock 宣称闭环。

## 2. 接口契约（摘要，详见 contracts/2026-10-17-writing-conversation-team-contract.md）

- WS `bind_writing_session`：`{type, article_id}` → `writing_session` 事件。
- 发送 metadata `writing`（snake_case 传输）。
- WS 动作：`writing.undo_change`（前端撤销）、`article.get` 复用。
- 服务端事件：`writing_changed`（article_id, revision, digest, change, actor, source_message_id）。

## 3. 完成定义

1. 打开文章即获得服务端绑定会话，右栏可真实多轮对话并停止。
2. `/`、`@`、空态话术、素材/提纲抽屉、选区工具条全部可用。
3. 主编可通过团队工具读取文章、创建素材/提纲、更新正文；写入落库并同步编辑器。
4. 普通聊天与克隆团队无法调用写作工具。
5. 撤销不回滚作者后续编辑；冲突/取消后写入不落库。
6. 测试/构建结果与已知缺口如实记录。
