---
artifact: contract
route: brainstorming -> api-and-interface-design
skills:
  - api-and-interface-design
created_at: 2026-10-11
topic: home-start-panel-and-plus-menu-parity
spec: .ai-runtime-artifacts/specs/2026-10-11-home-start-panel-and-plus-menu-parity-spec.md
---

# 契约：终端 WS 通道 / workspace 文件端点 / feedback 命令

WU 间边界契约。前后端 WU 并行开发以本文件为唯一真相源；任何一侧改签名须先改本文件。

## 1. 终端 WS 通道

信封沿用 `nanobot/channels/websocket/runtime.py` JSON envelope（`type` 分发），会话归属校验沿用 attach 机制。

### 1.1 入站帧（client → host）

```jsonc
// type: "terminal_start"  — 幂等：同 terminal_id 重复调用返回当前状态
{ "type": "terminal_start", "terminal_id": "<uuid v4, 客户端生成>", "session_key": "<可选，缺省=当前 attach 会话>" }
// type: "terminal_write"  — 一行命令（含结尾 \n 时视为提交）；服务端不回显
{ "type": "terminal_write", "terminal_id": "…", "data": "ls -la\n" }
// type: "terminal_kill"
{ "type": "terminal_kill", "terminal_id": "…" }
// type: "terminal_list"   — 请求回推当前可见终端
{ "type": "terminal_list" }
```

### 1.2 出站事件（host → client，outbound_wire 扩展）

```jsonc
{ "type": "terminal_started",  "terminal_id": "…", "shell": "/bin/bash", "cwd": "…", "status": "ok|already_running|limit_reached|error", "message": "<错误时>" }
{ "type": "terminal_output",   "terminal_id": "…", "seq": 12, "stream": "stdout|stderr", "data": "<≤8KB 分片>" }
{ "type": "terminal_exit",     "terminal_id": "…", "exit_code": 0 }
{ "type": "terminal_list_result", "terminals": [ { "terminal_id": "…", "status": "running|exited", "shell": "…", "cwd": "…" } ] }
```

### 1.3 语义约束

- `terminal_id` 全局唯一由客户端生成；服务端映射到 `ExecSessionManager` 会话。
- 并发上限 8（后端既有），超限 `terminal_started.status=limit_reached`。
- cwd = 会话 workspace 根（无会话锚时 = default_scope 根）；`restricted` access_mode 下命令过 ExecTool guard，被拒命令输出走 `terminal_output(stderr)` + 立即 `terminal_exit(exit_code=-1)`。
- idle 回收沿用 1800s；回收后补发 `terminal_exit {exit_code: null}`（经 `terminal_output` 附「session expired」文本）。
- 错误格式：不引入新错误协议，失败一律 `status != ok` + `message`。

## 2. workspace 维度文件端点（只读）

```
GET /api/workspace/files?path=<relative>&scope=default
→ 200 { …与 GET /api/sessions/{key}/files 同构（workspace_files.py::workspace_directory_payload）… }
→ 404 { "error": "no_default_workspace" }   // 无默认工作区
→ 400 { "error": "invalid_path" }            // 路径逃逸校验失败
```

- 实现 = 复用 `workspace_directory_payload`，锚点从 sessionKey 换为 `config.workspaces.default_scope.project_path`。
- 分页/截断字段（`truncated`、`parent_path`）语义不变；前端 `WorkspaceFileTree.tsx` 仅换 fetcher。
- 文件预览：v1 首页面板点击文件 → 若存在默认工作区，`file-preview` 以 `?scope=default` 同规则扩展（实现同一 handler 的锚点解析函数）。

## 3. `/feedback` 命令

- 注册：`nanobot/command/builtin.py`，`/feedback <text>`；空参数返回用法。
- 存储：追加 `~/.nanobot/data/feedback.jsonl`，行 schema：
  `{ "ts": <epoch float>, "session_key": "…", "text": "…" }`
- 副作用：session metadata `feedbacks` 追加同构条目；返回确认文本「已记录反馈，谢谢！」（进入正常回合消息流，前端无需专用渲染）。
- 命令面板：`builtin_command_palette()` 增加条目 `{name:"feedback", description:"发送关于当前会话的反馈"}`，`/` 菜单自动可见。
- 前端提交：`sendSystemCommand("/feedback " + text.replace(/\n/g," ⏎ "))`。

## 4. 前端内部接口（WU-C/D/E/F 共享）

- `nanobot-client.ts` 新增：`terminalStart(id, sessionKey?)`、`terminalWrite(id, data)`、`terminalKill(id)`、`terminalList()`；事件回调注册形如 `onTerminalEvent(cb: (e: TerminalEvent) => void)`，类型定义放 `webui/src/types.ts`。
- `ComposerPlusMenu.tsx` 行模型扩展：
  `type PlusRow = { kind: "insert-token", … } | { kind: "action", run: () => void, disabled?: boolean } | { kind: "popup-select", options: {id,label,current}[], onSelect(id) }`
- 浏览器白名单：`lib/browser-url.ts::normalizeBrowserUrl(raw): {ok:true,url} | {ok:false,reason:"scheme|private|malformed"}`（前后端各一份实现，规则一致：仅 http/https；loopback 允许；其余私网/保留地址拒绝）。

## 5. 版本与兼容

- 全部为增量：新帧类型对旧客户端不可见即忽略（gateway dispatch default 分支已有 unknown-type 容错则复用；否则加日志跳过）。
- 不改任何既有帧/端点签名。
