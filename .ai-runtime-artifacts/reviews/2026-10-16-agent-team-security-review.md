# Agent Team 安全审查（独立审查者）

- 审查对象：`feature/agent-team` 分支新增的 Agent Team 功能
- 基准：`.ai-runtime-artifacts/contracts/2026-10-15-agent-team-contract.md`（frozen）§3 / §5 / §7
- 范围：`nanobot/teams/**`、`nanobot/agent/tools/team.py`、`nanobot/webui/{teams_api,teams_routes,inbound_commands,gateway_services,outbound_wire,outbound_projection,settings_routes,session_projection}.py`、`webui/src/lib/teams/**`、`webui/src/hooks/useTeamProgress.ts`、`webui/src/components/thread/Team*.tsx`
- 约束：只读不改；未起服务、未调模型、未读本机密钥/配置；行号均来自本次实际读到的文件

## 结论

**需修改**：2 条高危（运行级读写动作缺少会话归属校验）建议作为合并前必修项；其余为中低危，可跟进。

## 发现清单

### 严重

无。

### 高

- **[H1] `nanobot/teams/runs.py:47-103` + `nanobot/webui/teams_routes.py:252-269`：三个 run 控制动作完全不带调用方会话身份。**
  - 攻击路径：同一 workspace 内任一已认证 WS 连接发 `teamRuns.cancel` / `teamTasks.cancel` / `teamRuns.retry`，只要拿到或从 WS 事件帧里读到别的聊天的 `runId`，即可取消或重试他人的团队运行；`retry_run` 更会用原运行的 `chatId` / `sessionKey` 建新运行（`runs.py:88-96`），等于替别人发起新一轮团队调度。
  - 复现思路：`teams_routes.dispatch_control` 只把 `{runId, taskId, requestId}` 传给 operations；`teams_api.with_team_controller`（`teams_api.py:157-165`）转给 `controller.cancel_run(run_id=...)`；`TeamRunController` 全程没有 session/chat 参数。根因在更上游：`settings_routes._domain_request`（`settings_routes.py:630-645`）构造的 `SettingsRequest` 只有 `query / payload / local_browser`，**不携带 connection 或已认证会话身份**，handler 层没有可比对的对象。
  - 已有的 `CrossSessionForbidden`（`runs.py:27`、`runs.py:137-140`）只校验「task 是否属于该 run」，**不校验 run 是否属于本会话**，挡不住这条路径。
  - 影响面补充（按要求补充）：到达该路径的入口**只有一条** —— WS `requestMutation` 白名单里的三条动作（`ws_http.py:221-223` → `_WEBUI_MUTATION_PATHS`），HTTP 直连被 `ws_http.py:475-479` 与 `settings_routes.py:507-515` 的 405 双重挡死。因此不存在第二条绕过路径，修一处即可闭环；但在「同 workspace 多用户共用一个 gateway」的部署下，可横向影响他人会话。
  - 建议改法：`_domain_request` 增带认证会话身份（chatId / sessionKey），`dispatch_control` 与 `TeamRunController.cancel_run / cancel_task / retry_run` 增加调用方身份参数，与 `repository.get_run(run_id)` 的 `binding.chat_id` / sessionKey 比对，不一致抛 `CrossSessionForbidden` → 403 `CROSS_SESSION_FORBIDDEN`（映射已存在：`teams_routes.py:156`）。

- **[H2] `nanobot/webui/teams_routes.py:231-246`：`team-runs-list` / `team-run-detail` / `team-run-events` 无会话归属校验（契约 §3 明写「会话访问检查」）。**
  - 攻击路径：`GET /api/team-runs?chatId=<他人 chatId>` 枚举任意聊天的运行清单；拿到 `runId` 后 `GET /api/team-runs/detail?runId=`、`GET /api/team-runs/events?runId=` 可读到该运行的团队名、成员名、任务 title / acceptance、结果摘要与完整事件流（跨聊天横向读）。
  - 复现思路：`dispatch` 把 query 里的 `chatId` 原样透传给 `service.list_runs`；`run_detail` / `run_events` 直接按 `runId` 查（`teams_api.py:118-125`），全链路无任何与请求方会话的比对。契约 §3 原文：`GET /api/team-runs?chatId=...` 标注「会话访问检查」，且「run 查询与 mutation 从认证会话推导 workspaceKey，忽略客户端的 workspaceKey」——目前 `chatId` 100% 来自客户端。
  - 建议改法：三条读动作都要接认证会话身份；`team-runs-list` 强制以会话 chatId 为准（客户端传不一致就 403，或直接忽略），`detail` / `events` 用 run 的 `binding.chat_id` 做归属比对，不一致返回 403 `CROSS_SESSION_FORBIDDEN`。

### 中

- **[M1] `nanobot/teams/scheduler.py:145-148`：写集锁完全由 Leader 模型自行声明，声明为「互不相交」即可绕过 `workspace-write` 全局串行。**
  - 攻击路径：Leader（或被成员输出注入后的 Leader）给两个实际会写同一文件的任务分别声明 `resourceKeys: ["file:a.md"]` 与 `["file:b.md"]`，二者拿不到同一把锁，并发写同一文件导致内容互相覆盖 / 撕裂。
  - 复现思路：`_lock_names` 只要 `declared` 非空就直接用它，并不再并上 `UNDECLARED_WRITE_RESOURCE`（`scheduler.py:147-148`），锁获取点在 `scheduler.py:461-462`。
  - 定性：契约 §7 明确「只有显式声明互不相交写集才放宽」且「该锁是协作约束，不能拦截恶意 shell」，因此**不构成越权漏洞**；但声明方是被注入面很大的 LLM，实际防护力接近零。
  - 建议改法：默认把 `workspace-write` 与声明键合并取锁；或对同批声明做交叉检查（同一 `file:` 前缀出现在两个不同声明中即拒绝该批）。

### 低

- **[L1] `nanobot/agent/tools/team.py:115`：`batchId` 只做 `strip()` 非空校验，无长度上限。**
  - 攻击路径：模型传一个数十 KB 的 `batchId`，落进 dispatch 记录表并参与 `payload_digest` 比对（`scheduler.py:241-247`），重复投递放大存储与内存占用。
  - 复现思路：`TeamTaskInput.from_dict`（`models.py:686-702`）对 `key` / `memberId` / `title` 等都走 `_text(..., max_chars=...)`（`models.py:667-671`），但 `batchId` 是工具级参数、不经过 `from_dict`，绕过了同一套上限。
  - 建议改法：`batchId` 按 `MAX_ID_CHARS`（`models.py:71`，128）截断或拒绝。

### 信息

- **[I1] 用户转述的 `run.started` 投影不一致，本次复核未能复现。**
  - 前端 `webui/src/lib/teams/events.ts:270-275` 明确为 `run.started` 走独立分支，只取 `data.binding` 与 `data.startedAt`；后端 `RunStartedData.to_dict()`（`models.py:1033-1034`）产出的正是 `{binding:{teamId,teamName,teamRevision}, startedAt}`，`parseBinding`（`events.ts:144-151`）要求的三个字段齐备；reducer 也有 `case "run.started"`（`webui/src/lib/teams/reducer.ts:155-161`）与「无快照时唯一入口」的 bootstrap（`reducer.ts:127-136`）。
  - 结论：在当前工作树上这条不成立。若该发现来自更早的 commit，可能是已被修复；标记为「未复现」而非漏洞，也不作为合并阻塞项。
- **[I2] `nanobot/teams/events.py:42-45`：`TASK_WIRE_FIELDS` 含 `acceptance`。**
  - 验收口径是 Leader 显式填的结构化声明，不是模型思考链，故不判为契约 §7 意义上的「私有推理」外泄；仅提示它确实把拆解意图暴露到边缘，供产品确认。

## 已核对、确认安全

- **认证与门禁**：6 条新路径全部登记在 `_SYSTEM_ROUTES`（`settings_routes.py:195-197`），经 `_route()` 后统一走 `_authorized`（`settings_routes.py:530-531`）；3 条写路径在 `_TEAMS_MUTATION_PATHS`（`settings_routes.py:246-249`），HTTP 直连被 `settings_routes.py:507-515` 与 `ws_http.py:475-479` 双重 405 挡死，必须来自已认证 WS 的 `dispatch_webui_mutation`（`ws_http.py:485-515`）。**未发现 GET 改状态**。WS 动作名白名单收紧为 `[a-zA-Z][a-zA-Z0-9_.]{0,127}`（`inbound_commands.py:76-78`），未知动作仍 404。
- **模型不能自带身份**：`TeamTaskInput.from_dict` 用 `_reject_unknown_keys` 拒绝 `runId` / `sessionKey` / `workspaceScope` 等任何未声明键（`models.py:689-693`）；`TeamDispatchTool.execute` 只暴露 `batchId` 与 `tasks`（`team.py:41-75`）；`run/session/权限` 一律由宿主经 `TeamExecutionRequest` 注入（`execution.py:47-63`、`scheduler.py:497-511`）。
- **memberId 只能来自冻结名册**：`scheduler.py:346-350` 在 `_validate` 中逐条比对 `self._slots`（由冻结修订的 `revision.members` 构造，`scheduler.py:188`），且校验先于 `_persist_batch`（`scheduler.py:239 → 254`），未知成员整批拒绝且不落库、不起任务。
- **跨批依赖与环**：`scheduler.py:351-358` 拒绝引用本批以外的 key 与自依赖，`_toposort`（`scheduler.py:361-382`）用 Kahn 排不完即判环；两者都在持久化与起任务之前抛出 `TeamValidationError`，被 `team.py:129-132` 转成工具错误回给模型，不起任何孩子。
- **成员无法被指令诱导去起别的成员**：`TeamDispatchTool._plugin_discoverable = False`（`team.py:80`），不进普通聊天自动发现；成员工具目录为 `scope="subagent"` 的真实子工具集（`turn.py:204-206`、`turn.py:227`），并与冻结档案求交；档案解析不出 tool allow-list 时直接 `invalid_profile` 拒绝起跑（`execution.py:78-84`），不做「空白名单 = 全量放行」的扩权解释。
- **工作区边界**：团队库路径只由 workspace 推导、调用方不可指定（`teams_api.py:61-72`）；`workspace_scope` 由宿主一路透传到 `execute_frozen`（`scheduler.py:508` → `execution.py:102`），团队层不新增任何写入路径。
- **数据边界（§7）**：`EVENT_DATA_FIELDS`（`events.py:30-40`）+ `TASK_WIRE_FIELDS` / `TASK_RESULT_WIRE_FIELDS` / `RUN_WIRE_FIELDS`（`events.py:42-53`）构成显式白名单，`project_team_event` 只挑白名单键（`events.py:102-123`）；完整 `fullResult`、原始工具参数、shell 输出均不在任何投影字段里。`lastAction` 唯一来源是 `render_activity_action`，只从 `{toolName, toolStatus, iteration}` 拼一句话并截断到 240 字（`events.py:147-157`）。
- **前端无 XSS 汇**：`webui/src/components/thread/Team*.tsx`、`webui/src/lib/teams/**`、`useTeamProgress.ts` 中 `dangerouslySetInnerHTML` / `innerHTML` 全量 grep 无命中；`summary` / `lastAction` / 成员名均走普通 JSX 文本节点。
- **temporary 聊天控制按钮是真禁用**：`ThreadShell.tsx:1617` 传 `canControl={!temporary}`，`TeamProgressPanel.tsx:96-107`（成员停止按钮）与 `:109`（底部停止/重试区）在 `canControl` 为假时整块不渲染，不是仅 CSS 隐藏。
- **DoS 边界**：`limit` 1..500（`repository.py:1033-1035`）、列表 1..100（`repository.py:1395-1400`）、`bool` 显式排除；`afterSeq < 0` 触发 `resetRequired`（`repository.py:1047-1051`），seq 断号同样置位；前端 `asSequence` 拒绝负数 / 非整数（`events.ts:104-106`）；活动事件 4KiB 上限 + 削字段 / 丢弃（`events.py:126-144`）+ 每任务 0.25s 节流（`events.py:25,175-184`）+ 每 run 5000 条上限（`repository.py:865`）；`maxConcurrency` 信号量 + 同成员互斥锁 + `maxTasks` / `maxDispatchRounds` / 任务与运行超时（`scheduler.py:189-190,248-252,338-341,466-469`）。
- **多进程与重启**：writer 用 `filelock.FileLock`（`database.py:24,311-332`），是真正的跨进程文件锁；抢不到时 `writer_owned=False`（`teams_api.py:80-84`），`prepare_new_run` 直接抛 `StorageUnavailable`（`service.py:240-242`）→ 503 `STORAGE_UNAVAILABLE`（`teams_routes.py:159`），**第二个 gateway 确实无法派活**。`recover_after_restart` 只在拿到 writer 时执行（`service.py:278-283`），且 `mark_interrupted_runs` 只把遗留 `running/cancelling` 落成 `interrupted`（`repository.py:570-580`），不重放任何任务，**重启不会重复副作用**；`prune_activity_events` 还会按 `workspace_key` 回表限定删除范围（`repository.py:1066-1074`），不会误删别的工作空间。

## 未能验证的部分（还差什么证据）

1. **`nanobot/agent/loop.py`、`nanobot/agent/subagent.py`、`nanobot/channels/websocket/runtime.py`、`nanobot/channels/manager.py`、`nanobot/cli/gateway_runtime.py` 的改动不在本次范围内，未审。** 契约 §5 明确要求「Team 模式移除普通 spawn，避免绕过团队成员限制」——这条无法确认是否落地。缺证据：需单独审 `loop.py` 中 Team 模式下的工具白名单装配 diff。
2. **子执行活动 payload 的实际内容未验证。** `execution.py:91-92` 只是把 `on_activity` 的 payload 原样转发给 `EventSink`，真正产生 payload 的 `SubagentManager.execute_frozen` 在 `agent/subagent.py`（范围外）。缺证据：需确认该 payload 只含 `toolName / toolStatus / iteration`，不含工具参数与输出——这直接决定 §7 是否成立。
3. **无任何运行时验证**：未起服务、未跑测试、未调模型、未起第二个 gateway。H1/H2 的「可横向影响他人会话」结论是静态代码推演，缺一条端到端复现（两个 WS 连接 + 交叉 runId 的集成测试）。
4. **`repository.retry_run` 的子图复制细节未逐行读完**（`runs.py:88-96` 只看到入参 `chat_id` / `session_key` 来自 `run_context`）。缺证据：需确认它只复制 failed/interrupted 及其下游 blocked、成功任务不重跑、且新 run 仍走原固定修订。
5. **`webui/src/lib/teams/api.ts` 的传输通道未逐行确认**：`cancelTeamRun` 等三函数调用 `transport` 的 WS mutation（`api.ts:256-290`），但未确认该 transport 在 `temporary` 客户端上不会另开 HTTP 旁路。缺证据：需看 `WebUIMutationTransport` 的实现与 `temporary` 客户端的装配。
6. **`inbound_commands.py` 的 camelCase 放行是否放宽了别的域**：`is_webui_mutation_action` 是全 WS mutation 共用的正则（`inbound_commands.py:76-78`），新增了 `A-Z`。已确认真正能执行的路径仍受 `_WEBUI_MUTATION_PATHS` 精确匹配约束（`ws_http.py:539-540`），但未穷举全部既有动作名的回归影响。缺证据：跑一遍 `tests/webui/test_inbound_commands.py` 全量。
