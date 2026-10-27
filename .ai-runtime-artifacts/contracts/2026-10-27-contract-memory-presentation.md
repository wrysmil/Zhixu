---
artifact: contract
route: api-and-interface-design
skills: [api-and-interface-design]
created_at: 2026-10-27
status: active
source: [.ai-runtime-artifacts/specs/2026-10-27-memory-page-presentation-spec.md]
---

# 记忆展示跨前后端契约

本轮“直接执行”批准已确认 spec，跳过另一个待批准 plan。当前只查询 gateway 已绑定的记忆库；项目工作区来自该库记录的可信归属，不扫描其他路径数据库。

## JSON 追加字段（原字段不删）

Memory 与 Episode 返回追加：

```ts
interface MemoryWorkspace { id: string; name: string; path: string }
interface MemoryPresentation {
  origin_kind: 'ai' | 'manual' | 'unknown';
  generation_method: 'ai' | 'manual' | 'unknown';
  trigger_mode: 'automatic' | 'manual' | 'unknown';
  trigger_reason: string;
  workspaces: MemoryWorkspace[];
}
interface MemoryListResult<T> {
  items: T[];
  total: number; // 来源过滤后、limit前
  source_counts: { all: number; ai: number; manual: number; unknown: number };
  workspace_options: MemoryWorkspace[]; // 当前库可识别归属，独立于来源和关键词
  scope: 'current_store';
}
```

字段扁平添加到每条 payload；TypeScript 可选以兼容旧 fixture/服务，但新后端必须给出。`origin_kind=manual` 包含人工新增和明确手动触发的 AI 提取；旧 extraction 没有 trigger 时依然可识别 AI，trigger_mode=unknown。未知来源不自动归为人工。

## 查询与写入

- 现有 memories、memories/search、episodes 查询追加 `origin=ai|manual|unknown`、`workspace=<id>|unassigned`、`q`；空或省略表示全部。
- memories 追加 `priority=short_term|long_term`；保留 type、order、limit。新增 `order=updated`，created 按创建时间；episode 保留 session_id，q 搜索目标/摘要/标签。
- 工作区可选项取全部基础记录归属及已知默认归属；无效 origin/priority/order/workspace → 400，未知记录 → 404，保持现有 `{error: ...}` 格式。
- source_counts 按 q/type/priority/workspace 生效、origin 生效前计算。total 为 origin 生效后计数，limit 只在最后应用。
- 手动新增记忆可传可选 `workspace=<id>|unassigned`，仅接受已知默认或记录中可验证的工作区，不接受任意 path。省略时绑定当前库默认工作区。编辑不改原始来源与项目归属。
- 详情与修改/删除必须限制在服务绑定的数据范围；不能只凭另一个 workspace_id 的记录 ID 访问。
- 新来源存 `Memory.metadata.provenance` 和新增 `Episode.metadata.provenance`；Episode metadata 数据库列采用幂等追加迁移，旧 fallback JSON 缺 metadata 时读 `{}`。
- 公共持久化 helper：`nanobot.memory.provenance.workspace_from_path(path)` 返回 MemoryWorkspace；`extraction_provenance(session, source)` 返回 provenance dict（以上展示字段＋session_id）；metadata 外层由调用处包装 `{provenance: ...}`。
- 新提取持久化会话 `metadata.workspace_scope.project_path` 快照；来源 session_end/idle/context_compress/topic_change/daily_consolidation 显式自动，manual 显式手动，其他无法确定时 unknown。
- 查询侧从可信快照优先恢复工作区，其次明确关联 episode.session_id / provenance.session_id 的会话元数据；无快照且会话未保存 workspace_scope 时为 unassigned，不能用 resolver 默认值伪造归属。
- MemoryServices 可追加可选 `session_metadata` 回调，gateway 注入 `sessions.read_session_metadata`。查询使用请求内缓存避免重复读取同会话元数据。

## 分工边界

- WU-01 持久化：`nanobot/memory/{models,database,repository,extractor,provenance}.py` 与 tests/memory 新测试。
- WU-02 查询 API：`nanobot/webui/{memory_api,memory_routes,memory_services,memory_presentation,gateway_services}.py` 与 tests/webui 测试；不修改 WU-01 文件。
- WU-03 前端：`webui/src/components/settings/memory/`、`webui/src/lib/{api,types}.ts`、locale 与相关 tests；不改 Python。
- 全部使用工作树 `C:/Users/Huangqh/.codex/worktrees/memory-page-presentation/Zhixu`，不提交、不推送。Leader 整合并做真实验证。
