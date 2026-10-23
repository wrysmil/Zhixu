---
artifact: review
route: orchestration:dispatcher-workflow
skills: [requesting-code-review, code-review-and-quality]
group: 3
scope: [WU-04, WU-05, WU-09]
head: 40ea493
base: 2700025
created_at: 2026-10-16
status: reviewed
---

# G3 代码审查（WU-04 / WU-05 / WU-09）

## 0. 审查方式与局限（先读这段）

- 审查由 **Leader 分离式自审**完成：实现结束后重新独立通读源码取证，不复用实现时的判断；
  发现项一律**先写测试见红再改实现见绿**（本轮 3 项全部如此）。
- **没有做独立子代理审查**。用户在本会话明确拒绝了一次 `Agent` 派发调用，且 `orchestration`
  skill 禁止项含「派发子 Agent 做全项目编排」。`requesting-code-review` 要求的「代码写入者不
  审自己的 patch」这一条**未满足**，本产物不能被视为独立审查证据。
- 补救要求：**WU-11 必须派真正的独立 reviewer + security reviewer**，并把本文件与
  `verifications/2026-10-16-agent-team-g3-collective-test.md` 一并交给它作为待验清单。

## 1. 正确性（本轮发现并已修）

| 编号 | 严重度 | 问题 | 修复与证据 | commit |
| --- | --- | --- | --- | --- |
| C1 | **High（安全相关）** | 首条消息的团队绑定 `claim` 无条件执行：任何能进入 `_dispatch_message` 的帧（例如别的通道转投、或 `webui: true` 由客户端自称但连接未过 WebUI 握手）都能**消费服务端暂存并拿到绑定 metadata**。绑定本身仍需 WU-06 才生效，但「谁能触发绑定」不该由客户端决定。 | claim 收进 `trusted_webui` 分支（`metadata["webui"] is True and connection in self._webui_connections`）。新增 `test_only_a_webui_authenticated_connection_can_take_the_staging`：未受信连接取不到、暂存原封不动、受信连接随后正常取到。**反证已实测**：临时把守卫改成 `if True:` 后该用例失败（`team_binding` 出现在未受信连接的入站 metadata 里），改回即通过。 | `40ea493` |
| C2 | Medium | 英雄页「下一条是临时聊天」开关打开时，团队选择器仍然渲染。那条发送走 `new_temporary_chat`、**根本不带 `team_id`**，用户会以为选了团队实际建的是普通临时聊天。 | `teamPickerVisibility` 增加 `temporaryRequested`；`ThreadShell` 传 `temporaryChatEnabled`。纯函数用例 + `thread-shell.test.tsx::hides the team picker when the next chat would be temporary`。 | `40ea493` |
| C3 | Medium（测试忠实性） | `tests/webui/test_teams_chat_binding.py` 的 `media` 替身缺 `augment_transcript_user_media`，一旦消息帧带上 `webui: true`（真实客户端始终带）就 `AttributeError`，把「受信/非受信」这条真实差异掩盖成替身崩溃。 | 补齐忠实实现（空进空出）。顺带说明：**此前的测试替身不够忠实**——真实 WebUI 每条消息都带 `webui: true`，而用例不带，所以 C1 的注入面在原测试下不可见。 | `40ea493` |

## 2. 判定为「设计权衡」而非缺陷的项（需要产品口径确认）

1. **`TEAM_BINDING_EXPIRED` 只报一次。** `claim` 与 `expired_binding` 都在报错后清掉记录，
   因此同一条消息第二次发送会安静地按普通聊天走。契约 §4 的字面要求是「不能静默退回普通模式」，
   当前实现在**第一次**满足了，第二次没有。
   - 若要严格满足字面口径：保留「已声明但过期」的墓碑直到重新绑定或聊天删除，每次发送都报错。
     代价是没有「就用普通聊天发送」的确认动作时，这条聊天会卡死（本版没有该 UI）。
   - 现状取舍：报错一次 + 允许后续按普通聊天发送，等价于把「确认降级」隐式交给用户。
   - **需要用户/产品定夺**；已列入 WU-11 待议清单。建议方向：过期后 composer 显示「团队绑定已过期，
     点击重新选择或按普通聊天发送」，两个动作都走服务端。
2. **取走暂存后消息发布失败会丢绑定。** claim 在 `metadata` 组装完成后、`try/accepted` 之前；
   若 `webui_dispatch_message` 抛错，暂存已被消费，重试即成普通聊天。与既有「排队回合」在同一
   窗口内的行为一致（`register_queued_websocket_turn_if_idle` 会被 `finally` 回收，绑定不会），
   属已知取舍，若要收紧需引入「未确认前不消费」的两阶段取走。
3. **`_purge_expired` 的宽限窗口 = 一个 TTL。** 过期记录多留 `ttl` 秒用于报错，之后随任何一次
   `stage` 扫掉；另有 256 条 LRU 上限。内存有界，判定不依赖 sleep。

## 3. 可读性与架构

- `teamPickerVisibility` 抽成纯函数是本轮最好的一个决定：把「临时聊天不显示 / 已绑定只读 /
  有消息不能改」三条契约口径变成 6 行可断言的表，避免为每条规则养一套 ThreadShell 集成替身。
- `ThreadComposer` 用 `teamPickerSlot?: ReactNode` 而非 5 个新 props：composer 已经是全站参数最
  多的组件之一，slot 让「什么时候能选团队」的决策留在 `ThreadShell`，composer 只负责摆放位置。
- `TeamSelector` **不在组件内取数**：`useClient()` 在没有 Provider 的渲染里会抛，而
  `thread-composer.test.tsx` 等既有用例正是裸渲染 composer。因此取数放 `useTeamOptions(token)`
  （App 侧）再以 props 传入。代价是 App 每次启动多一个 `GET /api/settings/teams`，见 §5-R6。
- `loop` 侧坚持「绑定随入站消息进来 → 回合内落盘 → 才装配 Leader」的单写者顺序，没有让 WebUI 层
  直接写会话文件；这与 `persist_message_scope` 同构，读者不需要新的心智模型。

## 4. 测试质量

- 后端本批新增/扩展：`tests/webui/test_teams_chat_binding.py` 22 例、`test_teams_binding.py` 14 例、
  `test_teams_routes.py` 26 例、`tests/teams/test_leader_context.py` 20 例。
- 前端本批新增：`team-chat-creation.test.tsx` 11 例、`team-selector.test.tsx` 8 例，另在
  `chat-list` / `thread-shell` / `useSessions` / `app-layout` 各补忠实替身与断言。
- 形状好的地方：断言的是**帧与 metadata**（`sent` 里逐字比 `{"type":"new_chat","team_id":...}`），
  不是实现细节；plan 锚点（空 registry 不回退、绑定不被覆盖）都逐字保留。
- 弱点：
  - 没有 e2e/真实浏览器验证（见集体测试 §8）；
  - `test_reconnecting_a_bound_chat_reports_the_persisted_binding` 用替身 reader，不是真会话文件；
  - 契约 §4「fork 继承团队修订」只在 `SessionManager` 层证明，没有过 WebUI fork 入口。

## 5. 遗留清单（按建议归属）

| 编号 | 项 | 建议归属 |
| --- | --- | --- |
| R1 | 创建撞 id 的文案复用「已被其他改动更新」（应为 `TEAM_ID_TAKEN`） | WU-11 文案批 |
| R2 | 冲突后对话框没有「载入最新」动作 | WU-11 |
| R3 | `useTeamOptions` 用 `listTeams(limit:100)` 且忽略 `nextCursor`，第 101 个团队起选不到 | WU-11（或加搜索） |
| R4 | 团队 limit 区间在 TS/Python 各写一遍，缺跨语言锚点测试 | WU-11 |
| R5 | `prune_activity_events` 仍无调用方（G2 的 L4 未消化） | WU-07 |
| R6 | 团队关闭（503）时 App 仍会请求一次团队列表 | 可接受；要优化则改成首次打开时取数 |
| R7 | `teamBindingByChatId` 不随聊天删除清理 | 低危，标签页生命周期内有界 |
| R8 | `loop.team_context_provider` 未接线，团队聊天尚未真的按 Leader 跑 | **WU-06（阻塞功能）** |
| R9 | `TEAM_BINDING_EXPIRED` 只报一次的口径需产品定夺（§2-1） | 需用户确认 |

## 6. 结论

G3 三个 WU 在其自身范围内**可用且可证**，门禁全绿（后端 2777 passed / 1 项基线失败，前端 1574 passed、
`tsc` 无输出、`npm run build` 与 eslint 通过）。功能完整性上最大的洞是 R8——它属于 WU-06 的所有权，
不是 WU-04/05/09 的偷工，但**必须在 WU-11 之前闭掉**，否则端到端跑不通。
