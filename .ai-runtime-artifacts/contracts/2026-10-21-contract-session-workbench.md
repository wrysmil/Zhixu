---
artifact: contract
route: orchestration:dispatcher-workflow
skills: [api-and-interface-design, source-driven-development]
created_at: 2026-10-21
---

# 会话工作台契约 v1

## 请求与错误

`GET /api/sessions/{encodeURIComponent(fullSessionKey)}/workbench`，沿用其他 session GET 的 Bearer 认证、WebUI namespace、既有错误 JSON/status、存在性检查。禁止读请求创建会话或写入任何计划/记忆。SQLite/磁盘查询在线程中完成。数据缺少返回空数组/null；局部模块不可用通过 limitations 标记；401/403/404/5xx 不能伪装成功空态。

## 响应（白名单投影，不包含配置凭证）

```ts
type TaskStatus = 'pending'|'in_progress'|'completed'|'blocked'|'cancelled';
interface WorkbenchTask { id:string; title:string; status:TaskStatus; source:string; detail?:string; }
interface SessionWorkbenchSnapshot {
  schema_version:1;
  session_key:string;
  updated_at:string|null;
  goal:{objective:string;status:string;ui_summary?:string;recap?:string}|null;
  tasks:WorkbenchTask[];
  plan:{explanation:string;steps:WorkbenchTask[];updated_at:string|null}|null;
  agents:{id:string;name:string;status:string;detail:string;invocation_id:string}[];
  memories:{id:string;kind:'working'|'long_term';title:string;content:string;source:string;updated_at:string|null;persisted:boolean}[];
  skills:{name:string;status:'available'|'always_loaded'|'used';source:string}[];
  mcp:{name:string;status:string;calls:number;successes:number|null;failures:number|null}[];
  limitations:string[];
}
```

tasks 优先本会话持久化 plan.steps；可再展示真实持续目标等来源，去重并明确 source。agents 复用既有执行仓库，每项 invocation_id 用于既有详情面板。工作记忆按 ScratchpadWriter.user_id_for_key(fullSessionKey) 查询；长期记忆只取本会话 episodes 的 linked_memory_ids。所有集合有界（任务最多50，agents20，记忆20，能力合理上限）；content 限长。未跟踪调用结果时 successes/failures 为 null，不能填0假装已知。

## 计划维护

新增自动发现的 `update_plan` Tool。参数为 explanation（可选短说明）和 steps（有界完整替换列表；title 非空；status 为上述枚举，id 由服务端稳定生成或校验）。只能修改 current_request_context.session_key 的 session.metadata.workbench_plan。保存失败回滚内存，返回明确错误；没有请求上下文不得写入。普通计划不创建或变更持续目标，不触发后台自动执行授权。

## 前端模块边界

- SessionWorkbench 增加可选 `sessionKey?:string|null`、`token?:string`、`client?:ZhixuClient`、`onOpenExecution?:(invocationId:string)=>void`；保持已有 model/onOpenFilePreview 调用兼容。
- SessionWorkbench 宽度改由宿主控制，根为 w-full；导出 SESSION_WORKBENCH_RAIL_WIDTH 仍默认19rem。
- ThreadShell 传真实 historyKey/token/client；布局 worker 负责该文件与可拖宽宿主。侧栏 worker 不修改 ThreadShell/App/PaneWorkbench。
- 侧栏 worker 在新文件定义本契约 TS类型、fetch和hook，不修改 lib/api.ts/lib/types.ts 避免交叉。请求通过既有API base/runtime工具与token；会话切换立刻清除旧快照并取消/丢弃旧请求。
- 有限轮询+事件失效刷新，卸载清理；后台/未连接时暂停；失败展示重试，不显示旧会话数据。当前消息派生的摘要/产出/来源保留，任务/记忆/能力不得从示例生成。

## 验收

认证GET、计划更新工具->持久化->GET、跨会话隔离、真实scratchpad/episode关联、MCP/Skill来源区别，以及UI三页签/加载/错误/空态均须有聚焦验证。
