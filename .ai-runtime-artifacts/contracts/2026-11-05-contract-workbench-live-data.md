---
artifact: contract
route: writing-plans
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/api-and-interface-design/SKILL.md
source:
  - .ai-runtime-artifacts/specs/2026-11-05-workbench-live-data-spec.md
  - .ai-runtime-artifacts/contracts/2026-10-21-contract-session-workbench.md
  - nanobot/webui/session_workbench.py
  - nanobot/agent/tools/context.py
created_at: 2026-11-05
status: proposed
---

# 工作台真实数据契约

## 兼容与身份

保留工作台响应 `schema_version: 1` 与全部现有字段，仅增加可选扩展字段。扩展存在时必须完整符合以下类型；省略表示旧服务端未提供，不等于空列表。snake_case 与小写枚举沿用项目现有协议。

所有端点按完整 session key 做 Bearer 鉴权、WebUI 命名空间、会话存在性检查。只接受当前 session 绑定的项目身份，不能通过请求参数选择另一个项目。无显式项目只见全局资源，不把默认项目误认为用户选中的项目。

```ts
type Scope = "workspace" | "global";
type SkillOrigin = "workspace" | "global" | "plugin" | "builtin";
type Coverage = "turn" | "session" | "unrecorded";
type Phase = "processing" | "tool_running" | "answering" | "waiting_user"
  | "completed" | "failed" | "interrupted" | "unknown";
interface Page<T> {
  items: T[];
  total: number;
  next_cursor: string | null;
  revision: string;
}
interface ResourceRef {
  id: string;
  kind: "note" | "index" | "profile" | "skill";
  scope: Scope | "plugin" | "builtin";
  name: string;
  title: string;
}
interface Observation {
  id: string;
  sequence: number;
  turn_id: string | null;
  call_id: string | null;
  at: string | null;
  kind: "phase" | "tool" | "memory_read" | "memory_write" | "index_maintenance"
    | "context_provided" | "skill_read";
  status: "running" | "succeeded" | "failed" | "cancelled";
  label: string;
  resource: ResourceRef | null;
  extent: "full" | "partial" | "unknown" | null;
  write_kind: "created" | "updated" | "written" | null;
  source: "runtime" | "history" | "extraction";
  coverage: Coverage;
}
interface NoteSummary {
  id: string;
  scope: Scope;
  name: string;
  title: string;
  description: string;
  memory_type: string;
  updated_at: string | null;
  preview_available: boolean;
}
interface SkillSummary {
  id: string;
  name: string;
  description: string;
  scope: SkillOrigin;
  origin: SkillOrigin;
  effective: boolean;
  availability: "available" | "disabled" | "requirements_missing" | "invalid";
  observed_state: "available" | "provided" | "read" | "unrecorded";
  overridden_by: string | null;
  source_label: string;
  requirement_labels: string[];
}
interface WorkbenchScope {
  project_id: string | null;
  project_name: string | null;
  turn_id: string | null;
  coverage: Coverage;
  truncated: boolean;
}
interface WorkbenchActivity {
  phase: Phase;
  current_action: string | null;
  started_at: string | null;
  events: Observation[];
}
interface MemoryContext {
  mode: "file" | "database";
  coverage: Coverage;
  truncated: boolean;
  provided: Observation[];
  reads: Observation[];
  writes: Observation[];
  indexes: Observation[];
  catalogs: { workspace: Page<NoteSummary>; global: Page<NoteSummary> };
  profile: { available: boolean; resource: ResourceRef | null };
}
interface SkillCatalog {
  available_total: number;
  groups: Record<SkillOrigin, Page<SkillSummary>>;
}
interface WorkbenchLiveFields {
  scope?: WorkbenchScope;
  activity?: WorkbenchActivity;
  memory_context?: MemoryContext;
  skill_catalog?: SkillCatalog;
}
interface NotePreview {
  resource: ResourceRef;
  content: string;
  updated_at: string | null;
  truncated: boolean;
}
```

上述 `WorkbenchLiveFields` 合并进现有 SessionWorkbenchSnapshot，不替换旧类型。Page 的四个分组即便为空也必须存在；来源暂不可读时保留对应 limitation，不能以空数组隐瞒错误。`available_total` 只计 effective 且 availability=available 的技能。

## HTTP 端点

| 方法与路径 | 请求 | 成功体 |
| --- | --- | --- |
| GET /api/sessions/{key}/workbench | 无新增必填参数 | 原响应 + WorkbenchLiveFields，目录与技能各组首页 limit=50 |
| GET /api/sessions/{key}/workbench/memories | scope=workspace\|global，limit=1..50（默认50），cursor 可选 | Page<NoteSummary> |
| GET /api/sessions/{key}/workbench/skills | scope=workspace\|global\|plugin\|builtin，limit=1..50（默认50），cursor 可选 | Page<SkillSummary> |
| GET /api/sessions/{key}/workbench/resources/{id} | id 是受限资源身份；不能传 path 或 scope 覆盖当前项目 | NotePreview；允许 note/index/profile，拒绝其他种类 |

路径中的 key 与 id 都只 URL 编码一次。id 为服务端编码的固定 kind/scope/name 身份，不接受任意绝对路径；解码后校验枚举、笔记 slug、当前项目可见性，再在允许目录内解析真实路径。技能 id 使用 origin + 规范化真实路径哈希，接口不输出绝对路径。

资源归属项目由当前 session 推导。项目变化后，旧项目资源 id 不能用于读取新项目中同名资源。id 必须绑定生成时 project_id；global 资源也需要当前 session 授权。有效 id 结构与解码辅助函数由 WU-02 定义，HTTP worker 只调用它，不自行拼路径。

排序：笔记以 name 升序；技能以 name、id 升序。cursor 为 base64url 编码的版本、集合类型、scope、session项目身份、revision 与最后排序键；严格校验长度与字段，不包含磁盘路径。超过 2KiB 或错误字段返回400；集合/身份不匹配返回400；目录修订号变化返回409，前端丢弃当前分页并重取首页。revision 从目录条目身份与必要 stat 信息生成，不重新生成目录文件。

各组 total 是当前列表中有效条目总数；next_cursor=null 表示完整。观测截断与目录分页是两个独立概念。目录摘要最多500字符，标签最多300字符，事件id/名称有固定上限；正文只在预览时读取，最多16KiB UTF-8安全截断，超限 truncated=true。返回内容不得包含配置凭证或工具原始参数。

错误沿用当前 session GET 的 `_http_error(status, message)` 文本体，避免引入与既有调用不一致的 JSON 错误格式：400参数错误，401未认证，403不可见，404会话/资源不存在，405方法不支持，409分页失效，503整体服务不可用。错误文本只给安全描述，不暴露物理路径或底层异常。

## 技能内部接口（WU-01 所有）

新增 `nanobot/agent/skill_catalog.py`：不可变 SkillEntry/SkillCatalog 数据模型与 `resolve_skill_catalog(agent_workspace: Path, project_workspace: Path | None, *, disabled_skills: set[str] | None = None) -> SkillCatalog`。SkillEntry 含 id/name/path/origin/description/availability/effective/overridden_by；path 仅服务端使用。

SkillsLoader 增加 `for_project(project_workspace: Path | None) -> SkillsLoader`，返回新的项目绑定实例；不修改共享实例。旧构造及旧 list_skills 调用仍可用。新增 `catalog()` 返回完整清单；旧 list_skills 只投影有效候选，source 兼容 workspace/plugin/builtin。全局与项目的准确区分由新 origin 字段承载，旧 source 不改枚举。

保持 load_skill、常驻技能、显式选择、build_skills_summary 与子 Agent 使用同一个绑定实例。被覆盖的高优先级条目即使缺依赖也不偷偷回落低优先级版本；UI 正确显示缺依赖及覆盖关系。禁用配置按既有名称/别名规则作用于最终清单。

## 观测内部接口（WU-02 所有，WU-04 接线）

新增 `nanobot/session/workbench_observations.py`，定义 WorkbenchObservation、资源编码器与 `TurnObservationRecorder`。构造参数 session_key、turn_id（允许缺失但 coverage 不得为 turn）、project_path、agent_workspace；支持 `record(event)` 幂等追加、`snapshot()` 返回不可变投影、`persist(session)` 写入 session.metadata.workbench_observations。磁盘保存由既有 SessionManager.save 完成，观测模块不另建数据库。

每轮最多200事件，最近20轮；追加按事件id替换同一阶段状态，不重复计数，sequence单调递增。超过上限保留最新事件并设置truncated。新轮次启动/工具边界持久化；session fork 的易变元数据排除本观测键，不能让新会话继承“本轮已读取”。模型提供者消息白名单不包含观测元数据。

通过 RequestContext.attributes['workbench_observer'] 传递 recorder（服务端对象，不序列化到请求），利用已有 ContextVar 与工具call上下文保持并发隔离。新增 WorkbenchActivityHook 复用 before_execute_tool/after_execute_tool/on_execute_tool_error 与运行结束hook。需要工具实际解析后的路径与读取范围时，通过文件工具成功边界调用 recorder.record_file_access；只记录标准化元信息，不改变工具返回文本。

文件记录入口签名：`record_file_access(*, path: Path, call_id: str, operation: Literal['read','write','edit'], at: str | None, extent: Literal['full','partial','unknown'] = 'unknown', write_kind: Literal['created','updated','written'] | None = None) -> None`。路径归类与资源id由该入口统一处理；非记忆/技能资源直接忽略。`record_context_resources(resources: Sequence[Mapping[str, object]]) -> None`接收实际manifest，按当前turn生成context_provided事件。`record(event: Mapping[str, object]) -> None`和`snapshot() -> dict[str, Any]`使用固定投影字段；snapshot包含events、truncated、turn_id、coverage，返回深拷贝。

后台追加通过同一进程内按session_key的串行合并入口执行，合并最新metadata后调用既有SessionManager.update_session_metadata，仅更新观测键并同步cache，不覆盖history。禁止两个后台任务各自read→append→set同一个旧数组。持久化失败记录limitation且不宣称可重载恢复。

上下文构建器调用 recorder.record_context_resources，传入最终组装成功的索引/画像/常驻技能事实；只有真实包含的条目记为 provided。目录预算截断时 extent=partial，不能将磁盘总数当作已提供数量。后台写入必须明确传 session_key 与可验证的源 turn_id；没有 turn_id 的写入 coverage=session，不合并到本轮计数。

## 更新与状态所有权

后端快照负责持久事实、资源目录与历史；前端现有 onChat 工具事件负责即时运行状态，不能等待 HTTP 轮询才展示 start/end。frontend 合并按 turn_id + call_id，以终态覆盖运行态；在同轮活跃事件存在时，较旧快照不能把新状态回退。新轮次消息立刻清除上轮即时状态。

phase 由现有 lifecycle/accepted turn/result 事件产生；没有来源时 unknown。断线时前端单独标 stale，显示连接恢复中，不重写持久 phase，也不继续确定显示旧工具运行。没有开始时间返回null；客户端仅用已知时间计时，不以请求完成时间当工具开始时间。

复用已有 session update 通知，200–350ms合并HTTP刷新；不新增第二套WebSocket业务协议。轮询仍限制在活跃10秒/空闲30秒，页面隐藏暂停，卸载取消。每次会话/项目身份变化使旧请求与资源分页结果失效。

## 契约验收

兼容旧响应；新增字段错形有可读错误；400/401/403/404/405/409/503遵守上述语义。以同名跨scope笔记、同名不同路径技能、分片读取、写入失败、目录修订与会话切换为必测用例。API查询无写入副作用；即时事件与旧快照竞争不回退；截图与完整链路验证在真实gateway上完成。
