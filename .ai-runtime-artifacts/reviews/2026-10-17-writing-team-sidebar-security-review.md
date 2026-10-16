# 创作空间内置创作团队 —— 尾盘安全审查

**审查者**：security-auditor（独立只读子 Agent，fresh context，agent id `ebd738aa-a5db-4e8d-afdc-365556d73d6d`）
**日期**：2026-10-17
**范围**：`git diff 81b4937..f719d00` 的全部新增/改动 Python 与前端；WU-09 未提交传输层作为「移动目标」单独标注
**verdict**：**PASS WITH FINDINGS** —— 无跨工作区越权、SQL 注入、凭据/正文泄漏或工具越权；但契约 §7「内置 Team 受保护」在**生产装配中完全未生效**（已复现绕过），另有 2 处跨对象绑定缺失与业务码不统一。**F1 须合并前修**。

---

## 1. 威胁面逐条结论（审查者原文）

1. **跨工作区：通过；跨文章/跨会话：不通过。** 全部 SQL 带 `workspace_key = ?`（`sha256(路径)[:16]`，服务端推导，`writing/database.py:71-74`）；`collab/apply.py:312-324` 的 `_suggestion_rows` 另带 `article_id`，跨文章建议 409。但 `writing_collab_api.py:594-599` 的 `confirm_handoff` 只按 `handoff_id` 取行，**未校验归属 `collaboration_id`**（`runner.py:466` 有此校验，但网关装的是前者 → F2）。WS 归属 `SettingsRequest.caller_chats` 已由服务端从连接推导（`settings_routes.py:709`），teams 域用 `teams_api.require_caller_session`，**协作域完全未读**（→ F3）。
2. **Team 不通过；Agent/Skill/MCP 通过。** Agent 删除/重置/定制保存三入口均拦，`CUSTOMIZATION_FIELDS`（`catalog.py:26-29`）覆盖 prompt/tools/skills/sub_agents，`type="custom"` 被 `replace()` 强制回 system；Skill 删除+禁用+同名导入均拦（工作区遮蔽按 `source` 正确放行，`skills_api.py:47-51`）；MCP 守卫已按 kind 派生（`mcp_presets_api.py:1478-1481`）。**Team：`teams_api.build_service`（`teams_api.py:81-84`）从不注入 `builtin_registry`，`TeamService._require_team_writable`（`service.py:130-135`）恒为 no-op**（→ F1）。
3. **注入：通过。** `grep "execute(\|executemany("` 结果全为 `?` 占位，仅有的 f-string 是常量列名与 `','.join('?'*n)`。模型/事件身份不被信任：`_resolve_event`（`runner.py:872-876`）只读顶层 `run_id`（不走 `event.data`），`_gate`（`runner.py:878-902`）用仓储行反查 batch/collab 状态，四项身份或状态不一致即丢弃。7 个 Skill 正文均显式声明素材/网页为**参考数据**、其中的指令不得当系统指令执行（`article-research/SKILL.md:23,61` 等）。
4. **凭据与数据泄漏：通过。** `Artifact.to_listing_dict`（`collab/models.py:376-392`）剔除 `content`；`frozen.skills[]` 只落 name/path/sha256（`runner.py:1052,1211-1224`）；`error_message` 生产路径恒为 `''`（`runner.py:684-688` 不传）；事件帧仅契约 §5 白名单字段；日志只带 id/status（`runner.py:1064-1086`）；creative 目录无 `console.*`。
5. **工具权限面：通过。** 五档案 `AgentSelection("include", _WRITING_READONLY_TOOLS)`（`catalog.py:86,142-258`），仅研究员加 `web_search/web_fetch`，无写文件/exec/发布/安装/凭据类；`resolve_selection` include = `present & entries`（`agents/runtime.py:44-45`）且未传 `locked_ids`，`teams/execution.py:78-84` 对空白名单直接拒跑（fail-closed）。Skill 由 `SkillsLoader` 只读加载。
6. **取消与晚到结果：代码层通过，生产未接线。** 取消先置 `ref.live=False` 再 await（`runner.py:804-806`），叠加「batch/collab 仍在途」「`active_batch_id == ref.batch_id`」仓储守卫，晚到只记日志。但 `CollabRunner`/`ArtifactSink` 全仓无生产实例（`gateway_services.py:137-141` 不传 runner → 控制动作恒 503），闸门当前不可达。
7. **前端：基本通过。** `clientRequestId` = `crypto.randomUUID()`，回退 52bit `Math.random`（`writing-collab-api.ts:407-410`）；无 `dangerouslySetInnerHTML/innerHTML/eval`、无 localStorage、URL 走 `URLSearchParams`。两处低危见 F5/F6。

## 2. 发现

### [high] F1 内置 Team 保护未接线（契约 §7 在生产永不生效）
- **位置**：`nanobot/webui/teams_api.py:81-84`、`nanobot/webui/gateway_services.py:230-255`
- **PoC（实测）**：`build_service(tmp).save_team({"id":"writing-team","name":"ATTACKER-OWNED",...})` → `builtin_registry is None = True`，写入成功并返回 `writing-team / 'ATTACKER-OWNED'`。
- **影响**：任意已认证 WebUI 客户端可抢占/覆盖/归档内置团队逻辑 id，契约 §4 的 409 `BUILTIN_RESOURCE_PROTECTED` 永不产生；`test_builtin_protection.py:271-282` 反而把「无 registry 即不保护」固化为期望。
- **修复**：`build_service` 注入 `BuiltinAssetRegistry(WritingDatabase(workspace))`，并让 allocator 走单独服务端路径（避免 create 路径自锁）。
- **Leader 处置**：已派 WU-16（agent id `eb85423a-fade-435f-b0fc-674886ecbbc0`），含「修正固化错误期望的测试」与「导入纪律」两项。

### [medium] F2 `writing.confirm` 缺 handoff↔collaboration 绑定
- **位置**：`nanobot/webui/writing_collab_api.py:594-599`
- **PoC（实测）**：文章 A 的 handoff + 协作 B 的 `collaboration_id` → 200 且 A 的 handoff 变 `confirmed`。
- **附带**：`article_revision/article_digest` 仅原样回显，未做文章 CAS 保存（`runner.py:474-494` 才是完整语义）。
- **修复**：比照 `runner.py:466` 加绑定校验，或改走 runner。
- **Leader 处置**：已下发 WU-09 裁决 2（必修 + 回归测试）。

### [medium] F3 协作域不校验 WS 会话归属
- **位置**：`nanobot/webui/writing_collab_routes.py`（全篇未读 `request.caller_chats`）
- **影响**：已认证连接可用任意合法 `chat_id` 起协作，绑定他人 `session_key` 并向该 chat 扇出 `writing_*` 帧（`channels/websocket/runtime.py:1576-1591`）。
- **修复**：`writing.start` 复用 `teams_api.require_caller_session`。
- **Leader 处置**：已下发 WU-09 裁决 3（必修 + 回归测试）。

### [medium] F4 业务码不统一
- **位置**：仅 teams 路径带码（`teams_routes.py:163`）；`agents_api.py:48-49` 丢弃 `AgentStoreError.code`，`skills_api.py:42-44`、`mcp_presets_api.py:1480-1481` 只回 message。
- **附带**：`test_builtin_protection.py:521` 自行硬编码该码，未验证异常真携带。
- **Leader 处置**：并入 WU-16 必做项 4。

### [low] F5 晚到产物守卫是死代码
- **位置**：`webui/src/lib/writing-collab-api.ts:799-801`
- **事实**：前端按 `body.batch_id` 过滤旧批次，但契约 §5 与 `runner.py:917-926` 的 `writing_artifact` 帧均无 `batch_id` → 恒空串。`writing_handoff` 帧无非本协作过滤（`:820-841`），`writing_collab` 仅在同 id 时过滤（`:766`），快照未载入时会被整帧收养（跨文章串显）。
- **Leader 处置**：并入 WU-13 必做项 4。

### [low] F6 `clientRequestId` 键不含 articleId
- **位置**：`webui/src/components/creative/use-writing-collab.ts:243`
- **事实**：用 `start:${action}` 且失败不 `release` → 切文章后复用同 id，被服务端判 `RequestReplayConflict` 409（fail-closed，不泄露）。
- **Leader 处置**：并入 WU-13 必做项 4。

## 3. 未验证 / 不覆盖（审查者声明）

- **审查对象是移动目标**：`webui/writing_collab_{api,routes}.py` 及 `tests/webui/test_writing_collab_{api,routes,wiring}.py` 为**未跟踪**文件，`settings_routes.py`/`ws_http.py`/`outbound_projection.py`/`bus/outbound_events.py`/`channels/websocket/runtime.py`/`gateway_services.py` 为**未提交**改动 —— 均**不在** `git diff 81b4937..HEAD` 内；审查期间 `writing_collab_api.py` 仍被改写（mtime 02:25:45），行号为审查时刻。
- 必跑命令：`pytest tests/webui/test_builtin_protection.py tests/webui/test_writing_collab_api.py tests/webui/test_writing_collab_routes.py tests/writing -q` → **7 failed, 234 passed, 1 skipped**；7 条全在 `tests/webui/test_writing_collab_routes.py`（WU-09 进行中：未装配时回 500 而非契约 503、`sanitized_extras`、handler 分发等）；`test_builtin_protection.py` 16 passed/1 skipped，`tests/writing` 全绿。
- 未覆盖：`ArtifactSink.ingest`（WU-07）无生产实现 → 其文章域闸门无法审查；runner 端到端未验证；`2026-10-12-contract-writing-collaboration.md` 未逐条比对；前端未跑 `bun run test`/构建；未做依赖/CORS/安全头审计（本次无相关改动）。

## 4. Leader 裁决汇总

| 项 | 严重度 | 裁决 | 归属 |
| --- | --- | --- | --- |
| F1 | high | 必修（注入 registry + 惰引入 + 修正固化错误期望的测试） | WU-16 |
| F2 | medium | 必修（handoff↔collaboration 绑定 + 回归） | WU-09 |
| F3 | medium | 必修（`caller_chats` 归属校验 + 回归） | WU-09 |
| F4 | medium | 必修（错误体统一带 code + 验证异常真携带） | WU-16 |
| F5 | low | 修（产物帧过滤口径收敛） | WU-13 |
| F6 | low | 修（幂等键纳入 articleId + 失败释放） | WU-13 |
| 其他（`WritingDatabase.connect()` 全局 `threading.Lock` 与 `apply.py:60-69` 的 `_SharedConnection` 借用连接绕过该锁） | — | 记为并发正确性可疑点，未列入合并阻塞；建议后续单独硬化 | 记录 |
