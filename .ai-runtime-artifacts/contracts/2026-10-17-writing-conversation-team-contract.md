---
name: writing-conversation-team-contract
description: 创作空间对话团队改版接口契约（会话绑定、写作元数据、团队工具、变更与撤销）
status: approved
approved: true
approval_quote: "我授权按上述方案开始实现。必要的实施计划、接口契约和方案补充可以自行落盘后继续执行，无需再次等待确认。"
date: 2026-10-17
---

# 接口契约 — 创作空间对话团队改版

## 1. 会话绑定

### 1.1 存储

```sql
CREATE TABLE IF NOT EXISTS article_sessions (
  workspace_key TEXT NOT NULL,
  article_id    TEXT NOT NULL,
  chat_id       TEXT NOT NULL,
  session_key   TEXT NOT NULL,
  team_id       TEXT NOT NULL,
  team_revision INTEGER NOT NULL,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (workspace_key, article_id)
);
```

- 一篇一绑定；`chat_id` 由服务端生成 UUID，客户端不可指定。
- 团队物理 ID 来自 `BuiltinAssetRegistry.physical_id("team", "writing-team")`；
  未登记时启动播种会先创建。
- `session.metadata["team_binding"] = {team_id, teamRevision, teamName}`；
  `session.metadata["writing_article"] = {articleId, workspaceKey}`。

### 1.2 WS 命令

```jsonc
// C→S
{"type": "bind_writing_session", "article_id": "<id>"}
// S→C（随后会发 attached / session_updated，并 hydrate）
{"event": "writing_session", "article_id": "<id>", "chat_id": "<uuid>",
 "session_key": "websocket:<uuid>", "team_id": "<physical>",
 "team_revision": 1, "team_name": "创作团队",
 "revision": 3, "digest": "<sha256>"}
```

- 仅可信 WebUI 连接；文章不存在 → `error: article not found`。
- 幂等：同一文章重复调用返回同一 `chat_id`；已存在会话时补齐缺失 metadata。
- 客户端不得拼 `${articleId}:partner`。

## 2. 发送写作元数据

```jsonc
// C→S message frame 附加
"writing": {
  "article_id": "<id>",
  "revision": 3,
  "digest": "<sha256>",
  "member_id": "writing-author",        // 可选，必须属于本团队
  "task": "rewrite",                    // 可选：ask|rewrite|expand|condense|outline|material|draft
  "selection": {                        // 可选
    "text": "原文",
    "start": 12, "end": 30,             // Python 码点
    "prefix": "前 32 字", "suffix": "后 32 字"
  }
}
```

- 服务端校验：会话已绑定同 article、article 存在、revision/digest 与库当前值一致、
  member 属于冻结团队名册；任一不满足整条消息拒绝（`error` 事件）。
- metadata 落在 `InboundMessage.metadata["writing_context"]`，仅本轮可见。

## 3. 团队写作工具

工具名固定；只有 `writing-team` 内置团队、且会话绑定文章时注入。

| 工具 | 允许角色 | 输入 | 输出 |
| --- | --- | --- | --- |
| `writing_read_article` | 全部 | `{}` | 文章完整快照 + `revision`/`digest` |
| `writing_create_material` | editor / researcher | `type,title,content,url?,source?,note?` | `{article_id, revision, digest, material_id, change_id}` |
| `writing_create_outline` | editor / architect | `items:[{text}], mode:"replace"|"append"` | `{article_id, revision, digest, change_id}` |
| `writing_update_article` | editor / author | `content, mode:"append"|"replace_range"|"replace_body", start?, end?, summary?` | `{article_id, revision, digest, change_id}` |

共同前置：
- 运行未取消；工具上下文由宿主构造（workspace/article/chat/session/team/run/turn/member 均由服务端给出）。
- 写入要求 `base_revision`/`base_digest` 等于当前库值；不等 → `ToolResult.error("writing_conflict ...")`，不写。
- 区间以 Python 码点计，超界/空内容 → 参数错误，不写。
- 写入经 `writing.apply` 单事务完成并记录 change；返回结果仅含提交后的 revision/digest。

## 4. 写入应用服务

```python
apply_change(
    db, *, article_id, base_revision, base_digest, change,
    actor: WritingActor, request_fingerprint: str, client_request_id: str,
) -> dict
```

- `change` 形如 `{"kind":"body","mode":"append","content":"..."}` /
  `{"kind":"material","material":{...}}` / `{"kind":"outline","items":[...],"mode":"replace"}`。
- 幂等键 = `(workspace_key, client_request_id)`；同键同指纹回原响应，同键异指纹 409，
  不允许 `INSERT OR REPLACE` 顶掉既有记录。
- `writing_article_changes`：

```sql
CREATE TABLE IF NOT EXISTS writing_article_changes (
  id            TEXT NOT NULL,
  workspace_key TEXT NOT NULL,
  article_id    TEXT NOT NULL,
  kind          TEXT NOT NULL,            -- body|material|outline
  before_json   TEXT NOT NULL,
  after_json    TEXT NOT NULL,
  actor_member  TEXT NOT NULL,
  source_run_id TEXT NOT NULL DEFAULT '',
  source_turn_id TEXT NOT NULL DEFAULT '',
  source_message_id TEXT NOT NULL DEFAULT '',
  request_fingerprint TEXT NOT NULL,
  result_revision INTEGER NOT NULL,
  result_digest TEXT NOT NULL,
  reverted      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  reverted_at   TEXT,
  PRIMARY KEY (workspace_key, id)
);
```

- 撤销（`writing.undo_change`）：仅当当前 `revision`/`digest` 等于该 change 的
  `result_revision/result_digest` 时，把 before 字段写回并产生 `revision + 1`；
  否则 409 `undo blocked by newer edits`，不回滚作者后续修改。
- 取消：run handle `cancelled/cancelling` 后工具调用一律拒绝（`writing_run_cancelled`）。

## 5. 前端动作

- `writing.undo_change`（WS mutation）：`{client_request_id, change_id, base_revision}` →
  `{article_id, revision, digest, change_id}`；前端成功后刷新文章。
- 已有 `article.get` 用于刷新正文/素材/提纲/revision/digest。
- 事件 `writing_changed`：`{article_id, revision, digest, change_id, kind, actor}`；
  前端按 article_id 过滤后刷新。

## 6. 错误口径

| 场景 | 传输 | message |
| --- | --- | --- |
| 文章不存在 | 404 | `article not found` |
| 版本冲突 | 409 | `article_conflict current_revision=<n> article_id=<id>` |
| 撤销被后续编辑挡住 | 409 | `undo blocked by newer edits` |
| 同请求异载荷 | 409 | `request_id was already used for a different writing mutation` |
| 运行已取消 | 工具错误 | `writing_run_cancelled` |
| 非团队成员 / 无权限 | 工具错误 | `writing_permission_denied` |
| 未绑定文章 | 工具错误 | `writing_article_unbound` |
