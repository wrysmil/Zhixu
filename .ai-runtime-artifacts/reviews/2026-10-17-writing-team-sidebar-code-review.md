# 创作空间内置创作团队 —— 尾盘代码审查（五轴）

**审查者**：reviewer（独立只读子 Agent，fresh context，agent id `5fc2d6d8-8861-41ca-b331-16f19bad1882`）
**日期**：2026-10-17
**范围**：`git diff 81b4937..f719d00`（**13 个 commit**，55 files，+14818/−13）及其对应新增文件；**排除**审查时刻仍在途、未提交的 WU-09 传输层改动
**verdict**：**BLOCK**（2 处集成级 correctness 缺陷会让核心作者流程必然失败；修后可复审转 PASS）

**Skills 使用（审查者自述）**：`requesting-code-review@.claude/skills/requesting-code-review/SKILL.md`、`code-review-and-quality@.claude/skills/code-review-and-quality/SKILL.md`、`verification-before-completion@.claude/skills/verification-before-completion/SKILL.md`；按 `reviewer.md` 五轴检查表逐项执行。已跳过 `frontend-ui-engineering` / `api-and-interface-design` / `security-and-hardening` / `performance-optimization`（本次为通用五轴 review，非专项 WU；前端与 API 维度按契约 §6/§8 手工核对）。

---

## 1. blocker（合并前必修）

### B-1 `frozen_preparer()` 漏传四个必填身份参数 → 默认冻结准备 100% TypeError
- **文件**：`nanobot/writing/collab/runner.py:300-306`（调用点）；`nanobot/writing/collab/preparation.py:135-148`（被调方）
- **事实**：`prepare_frozen()` 要求 `workspace_key/article_id/collaboration_id/batch_id` 四个 **kw-only 必填**参数，wrapper 只传了 `loader/recipe/article_snapshot/material_ids/model_id`。实测 `TypeError: prepare_frozen() missing 4 required keyword-only arguments`。
- **影响**：默认冻结准备路径 100% 失败 → `writing.start` 永远起不来。
- **测试盲区**：`runner.py` 单测全部注入 `_FakePreparer`；`grep frozen_preparer tests/` 为 0 命中。
- **修复方向**：wrapper 补传四个身份（从 `batch`/`collaboration` 取），并加「用真实 `frozen_preparer` 走通 start」的集成测试。
- **Leader 处置**：**已修**（commit `bb93858`）。`FrozenPreparer` 协议扩为 7 个具名参数；`_prepare_frozen` 传 `workspace_key=self._workspace_key / article_id / batch_id`（批次 id 在冻结后生成，落库留空、运行期由 `_with_identity` 补齐）；新增 `tests/writing/test_collab_runner.py::test_default_preparer_runs_real_prepare_frozen`，走真实 `prepare_frozen` + 真实 `SkillsLoader` + 真实 `RECIPES`，断言落库 `frozen` 的四个身份与运行期 `FrozenContext` 的正文/`batch_id`。`pytest tests/writing/test_collab_runner.py tests/writing/test_collab_recipe.py -q` → **36 passed**。

### B-2 `handoffVersion()` 取 `Math.max(lastVersion, artifactVersion)` → 提纲确认稳定 409
- **文件**：`webui/src/components/creative/use-writing-collab.ts:468-473`
- **事实**：契约 §10.3 冻结为「整数 `artifact_version` 直接透传」。`lastVersion` = `collaboration.version`（batch 创建 / handoff / artifact 每次 +1，必然 ≫ `artifact_version`），故 `writing.confirm` 稳定 409 `handoff version conflict`。
- **影响**：提纲确认 → 续写初稿这条主链路必然断。
- **测试盲区**：`writing-partner-panel.test.tsx:269` 只断言 `collaborationId/handoffId/outline`（fixture `lastVersion:5 / artifactVersion:2`，正确值应为 2），恰好放过错值。
- **修复方向**：`return handoff.artifactVersion`，并在面板测试补 `expectedHandoffVersion` 断言。
- **Leader 处置**：已派 WU-13（agent id `3219b7e5-7758-4317-ac74-8a889a08fd3c`）。

## 2. major

### M-1（原发现 3）`_record_request` 的 `INSERT OR REPLACE` 让「同 id 异载荷 → 409」在写路径失效
- **文件**：`nanobot/writing/collab/repository.py:779-786`
- **事实**：`request_id` 固定 + 载荷可变时（`runner.py:485` 的 `confirm-outline` 键不含 `article_id`/`outline`；`:507` 的 `confirm-next` 键不含 outline），第二请求会**覆盖** `article_requests` 行，且第二次 `resolve_handoff` 会重开一个 draft 批次；A 契约 §6 的 `request_id was already used for a different WebUI mutation`(409) 静默失效。
- **修复方向**：写前按 PK 校验 op 一致，冲突抛 `RequestReplayConflict`；幂等键纳入 `article_id` 与实际载荷指纹。

### M-2（原发现 4）`TeamPort`/`ArtifactSink` 全仓零实现 → 契约 §3 的批次执行在生产不可达
- **事实**：`grep start_batch nanobot/` 只命中 Protocol；`on_team_event` 在 `nanobot/` 无生产调用点；`gateway_runtime.py:802` 只挂启动收敛 `converge_collaborations`，`CollabRunner` 本体未装配。即：每次 gateway 重启都会把在途批次标 `interrupted`（收敛已生效、执行没有）。
- **要求**：在 WU-09/WU-07 交付并接线前，该链路**不得**作为已完成项验收。
- **Leader 处置**：已派 WU-07（agent id `de2f026f-cb20-4387-b97d-d24cfb05753f`）补齐 envelope + `TeamPort`/`ArtifactSink` 生产实现与限额强制；装配接线留给收口 WU，缺口已记入 execution-log 的未完成项。

### M-3（原发现 5）状态机关键不变量**无有效断言**（变异探针未捕获）
- **文件**：`tests/writing/test_collab_runner.py`
- **证据**：把 `runner.py:881` 的 `if not ref.live` 改成 `if False`、`:889` 的 `cancel_generation` 校验改成 `if False`，34 个用例**全绿**。`test_cancel_generation_blocks_late_results` 实际被 `_release_handle` 的 `live=False` 拦住，未验证代数闸门；也没有「同 collaboration 换代后旧 run 事件到达」这条真正需要代数的用例。
- **修复方向**：补「取消后重开批次、旧 run_id 事件到达 → 只落 warning 日志且不落产物」。

### M-4（原发现 6）`on_team_event` 在事件循环上同步跑 4+ 个独立 SQLite 事务
- **文件**：`nanobot/writing/collab/runner.py:560-587`（及其调用的 `_gate`/`_ingest_task_result`/`_end_batch_for_author`）
- **事实**：每事务新开连接 + `commit`，仓内无调用方用 `asyncio.to_thread` 包裹，无集成测试锁定该约定。（WU-08 已在 docstring 写明「不跨 await、不持锁等待」。）
- **修复方向**：端口侧包 `to_thread`，或把该约定写入装配契约。
- **Leader 处置**：已写进 WU-07 的必做项 4（要求端口侧保证不阻塞并用测试锁定）。

### M-5（原发现 7）`teams/service.py` 模块级 import 违反既有导入纪律
- **文件**：`nanobot/teams/service.py:39` — 模块级 `from nanobot.writing.builtin import BuiltinAssetRegistry`
- **事实**：`agents/store.py:52-56` 明确写了「本模块不得在导入期 import `nanobot.writing.builtin`，会拉进 `nanobot.config.paths` 导致按名 patch 的既有测试不可达」。实测 `import nanobot.teams.service` 后 `nanobot.config.paths`/`nanobot.writing.database` 均已在 `sys.modules`。
- **Leader 处置**：并入 WU-16（agent id `eb85423a-fade-435f-b0fc-674886ecbbc0`），要求惰引入 + 用实验证明 `tests/webui/test_mcp_presets_api.py` 的 3 个按名 patch 用例恢复。

### M-6（原发现 8）契约 §9 四个限额常量无强制点
- **文件**：`nanobot/writing/collab/recipe.py:87,89,91,95`
- **事实**：`MAX_MEMBER_TASKS_PER_BATCH=8` / `MAX_AUTO_DISPATCH_ROUNDS=2` / `MEMBER_TIMEOUT_SECONDS=300` / `MAX_CONCURRENT_MEMBERS=2` 全仓无强制点（除 `MAX_MEMBERS` 在 `validate_recipe` 自检），即限额目前只是常量。
- **Leader 处置**：已写进 WU-07 必做项 3（发起前真正强制 + 变异探针）。

### M-7 内置团队保护在生产完全失效（与安全审查 F1 同一根因，交叉确认）
- **文件**：`nanobot/webui/teams_api.py::build_service` 不传 `builtin_registry` → `_require_team_writable` 永远短路。
- **Leader 处置**：WU-16 的 1 号必做项。

## 3. minor

| # | 位置 | 事实 | 处置 |
| --- | --- | --- | --- |
| m-1 | `runner.py:453-515` | `confirm` 非原子：先 `save_snapshot`（独立事务）后 `resolve_handoff`，中间崩溃会留下「文章已改、交接仍 pending」 | 记录为已知风险；建议把交接 CAS 放在文章写之前或合并事务 |
| m-2 | `collab-labels.ts:18-30,127-132` | `STAGE_LABEL_KEY` 把阶段映射成状态文案；`stageLabelKey` 除本文件外无调用点（死代码）；`STATUS_ERROR_KIND` 把一切 400 译成 `error.selectionRequired` | 400 映射并入 WU-13 必做项 5；`stageLabelKey` 死代码随 `SuggestionPanel.tsx` 一同留待后续清理 |
| m-3 | `apply.py:95-177` | `apply_batch` 落库 `snapshot_digest` 但采纳时只用 `base_revision` 比较，从未校验 `suggestion.snapshot_digest` 与当前文章摘要 | 记录为已知缺口（契约未冻结该比较语义） |
| m-4 | 前端 | `WritingPartnerPanel`/`use-writing-collab`/`ArtifactCard` 除测试外零引用（`ArticleEditor.tsx:413-426` 替换点未改） | WU-13 必做项 1 |
| m-5 | `apply.py:357` | `_record_response` 同样 `INSERT OR REPLACE`，覆盖 `save_snapshot` 自己的幂等行（语义相容，但删掉保存流的幂等记录） | 与 M-1 同批处理（WU-16/WU-09 之外单列） |
| m-6 | `collab/models.py:401` | `Handoff` docstring 写「`status` 自身充当交接版本」与冻结口径（整数 `artifact_version`）矛盾 | 记录为文档订正项 |

## 4. nit

| # | 位置 | 事实 |
| --- | --- | --- |
| n-1 | `tests/writing/test_collab_store.py:191` | `test_same_request_id_replays_without_second_batch` 未覆盖「同 `request_id` + 异载荷 → 409」（即 M-1 的空洞） |
| n-2 | 契约 §8 | 扩键段落写「共 38 个键」，逐项列举实为 37；实现 37，无误，契约文字需订正 |
| n-3 | `collab/database.py:139` | `ensure_collab_schema` 每次调用执行 8 条 DDL（`CollabRepository.__init__` 每次都跑）；建表后可用 `_schema_meta` 短路 |

## 5. 契约一致性核对表（审查者原表）

| 项 | 结果 |
| --- | --- |
| §2 DDL 列/索引（六表）逐字一致；`SCHEMA_VERSION="2"`；只新增表 | pass |
| §1 枚举（collaboration/batch/artifact/handoff/suggestion/action/target/frozen 形状） | pass |
| §3 动作名（8 个 action 常量 + `ACTIONS` 白名单） | pass |
| §4 错误 message 逐字 | pass（`CollaborationNotFound` 带 `<id>` 后缀，属可接受的更详细形态） |
| §5 事件名投影（`writing_collab/artifact/handoff`、`send_writing_event`） | pass（代码在 WU-09 在途区，另行归因） |
| §6 前端类型（4 个 union + `CollabSnapshot` 字段 + snake_case 边界） | pass |
| §6 `utf16RangeToCodepoints`（emoji 代理对向下取整、CRLF 保原样） | pass |
| §6/§10.3 `expected_handoff_version` = 整数 `artifact_version` | **fail**（B-2） |
| §7 内置清单 13 项 + Skill→角色配套（对照 `catalog.py:163/235/259`） | pass |
| §7 allocator 协议（空串=被用户占用 → `-builtin`/`-builtin-N` 重试 ≤8，保留用户对象） | pass（`builtin.py:144-158`） |
| §7 管理投影字段 `ownerModule/origin/builtinVersion/deletable/protectionReason` | pass |
| §7 内置团队保护 | **fail**（M-7） |
| §8 i18n 键（10 locale） | pass（脚本复算：`creative.partner.*` 每 locale **88** 个 = 51 非 ui + 37 `ui.*`，10 个 locale 键集完全一致、无缺失、无越界扩键、无空值；TS 侧引用无缺失；`settings.teams.rosterCount_one/_other` 已补） |
| §9 批次与限额 | **fail**（M-6） |
| §10 裁决 1（协作表自管 schema） | pass（`repository.py:226-227` 幂等调用两处） |
| §10 裁决 6（提纲撤销不还原 outline，需登记为已知缺口） | pass（`apply.py:212-216` 仅还原 body） |

## 6. 测试与命令证据（审查者原始输出）

| 命令 | 结果 |
| --- | --- |
| `pytest tests/writing tests/webui/test_builtin_protection.py -q` | **228 passed, 1 skipped**（skip = `test_builtin_protection.py:359` 显式注明「首批无内置 MCP 预设」） |
| 提交范围（clean HEAD）`ruff check nanobot/` | **10 errors，全部 `nanobot/memory/*` 既有 F401/N806，本次变更零新增** |
| 工作区（含 WU-09 未提交）`ruff check nanobot/` | 12 errors：上述 10 + `gateway_services.py I001` + `outbound_projection.py:188 F821 Undefined name 'conns'`（在途缺陷，已转 WU-09；另注：`:188` 在 `conns` 赋值 `:191` 之前引用，`WritingEvent` 一进来就 `NameError`） |
| `npx vitest run src/tests/writing-{collab-api,artifact-card,partner-panel}` | **3 files / 32 tests passed** |
| 全量必跑命令（含 WU-09 文件） | **18 failed, 314 passed, 1 skipped**；18 个失败全部落在 `test_writing_collab_{wiring,routes,api}.py` + `test_writing_wiring.py` 的 2 条 collab 条目（`WRITING_COLLAB_ACTION_NAMES` 与路由路径集比较口径不一致），均属 WU-09 在途，已单独归因 |
| 变异探针（临时改后 `finally` 还原，`git diff` 已确认复原） | 捕获：`repo_active_batch`、`apply_pending_guard`、`repo_idempotent_replay`、`converge_interrupted`；**未捕获：`runner_gate`、`runner_generation`、`repo_handoff_cas`（经 runner 路径）** → M-3 |
| TDD 证据 | 每 commit 内测试与生产代码同 commit，无法从历史区分 test-first；测试含 happy path + 大部分错误路径，但存在 B-1 与 M-3 两处关键盲区 |

## 7. 未覆盖 / 未验证（审查者声明）

- WU-09 传输层（`writing_collab_{api,routes}.py`、`outbound_projection.py`、`outbound_events.py`、`ws_http.py`、`settings_routes.py`、`gateway_services.py` 未提交部分）及 `tests/webui/test_writing_collab_*` 的 18 个失败：进行中，不计入本次结论。
- 未跑 basedpyright（计划要求 CI 严格类型检查）；未跑前端 `tsc --noEmit` / `npm run build` / 全量 vitest（仅 3 个新测试文件）；未做浏览器端手工验证。
- 未验证真实模型端到端（plan §0 明确 W15 允许记录缺口）。
- `TeamPort` 无实现，故批次执行、事件收口、`cancel_run` 真实语义、`on_team_event` 的事件形状兼容性均**无法验证**。
- 安全专项仅标注可疑点，完整威胁建模由另一名安全审查员负责。

## 8. Leader 裁决汇总

| 项 | 裁决 | 归属 |
| --- | --- | --- |
| B-1 | 已修 + 已加真实准备器集成测试 | Leader（`bb93858`） |
| B-2 | 必修 | WU-13 |
| M-1 | 必修（幂等键/写前校验） | 待派（WU-04 命名空间） |
| M-2 | 必修（生产适配器 + 装配） | WU-07 + 收口装配 |
| M-3 | 必修（补代数闸门用例） | 待派 |
| M-4 | 必修（端口侧 `to_thread` + 测试锁定） | WU-07 |
| M-5 | 必修（惰引入 + 实验证明） | WU-16 |
| M-6 | 必修（限额真正强制 + 变异探针） | WU-07 |
| M-7 | 必修（注入 registry） | WU-16 |
| m-2 | 400 映射修正 | WU-13 |
| 其余 minor/nit | 记录为已知缺口或随死代码清理 | 不作合并阻塞 |
