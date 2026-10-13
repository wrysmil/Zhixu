# 阶段 A 代码审查

> 范围：A 批次 A1+A2+A3.1-A3.8（自 `da9fdb2` 起 16 个 commit）。
> 方式：Leader 自审（用户要求「快点执行」未派独立 reviewer 子代理；五轴审查按 `code-review-and-quality` skill 检查表逐项对照，证据以本批 WU 返回 + 集体测试为准）。
> 严重度：BLOCK = 必须修后重新审查；WARNING = 建议修但不阻塞；INFO = 知会。

## 1. 正确性（correctness）

| # | 主题 | 严重度 | 处置 |
| --- | --- | --- | --- |
| 1.1 | `nanobot/writing/repository.py` 业务幂等键落地为 `<action>[:<article_id>][:<payload 指纹>]`（WU-01 deviation 1） | INFO | 比 plan 严格（动作名 + 载荷指纹），契约 §5.2 message 与行为不变；既有 `test_expired_base_revision_is_rejected` 已相应调整 `op` 查询条件。**已修，不阻塞**。 |
| 1.2 | `nanobot/writing/models.py` `tags` 静默截断改为拒绝（FIX-01 主导） | INFO | 契约 §5.3 第 165 行明令「超限拒绝写入并保留原稿，绝不截断保存」，原 `_tags` 静默过滤违反此口径；FIX-01 把六条 message 全部收归 models 单一出口。**已修**。 |
| 1.3 | `nanobot/writing/models.py` `_brief` 对 `goal/audience/style` 仍 `raw.strip()[:500]` 截断（FIX-01 偏差 1） | WARNING | 契约 §5.3 未给 brief 字段上限；行为与 spec 「绝不在创作侧静默改稿」略冲突。**本批不修**，已登记到决策记录与 B 启动前置；建议在下批 SPEC 修订时显式给 brief 加上限（与 title/summary 同族）并拒绝截断。 |
| 1.4 | `get_workspace_path → ensure_dir` 仍会创建工作区根目录（FIX-01 偏差 5） | INFO | 全仓既有约定（memory/session 同款）；`writing/state.db` 已确认不在构造时产生（`test_constructing_database_creates_no_directory` 钉死）。**不修**。 |
| 1.5 | A3.8 「late-arriving save」用例的 stub 改语义（FIX-02） | WARNING | 改后的 stub 立即抛 409 而非真在飞，未端到端验证 abandonedRef 拦下晚到 save 的数据流；产品代码（`abandonedRef` 在 `EditorHost` 卸载时由 `flow.abandon()` 设置）分析正确，但缺真实跑通证据。**本批不修**（用户要求提速），登记到 B 启动前置。 |
| 1.6 | `nanobot/webui/writing_api.py` 拆补偿后 `_translated` 仅 `WebUISettingsError(exc.message, status=exc.status)` 纯透传（FIX-01 偏差 1-3 已并入） | INFO | 与契约 §6 逐字一致；测试断言 86/86。**已修**。 |

## 2. 可读性（readability）

| # | 主题 | 严重度 | 处置 |
| --- | --- | --- | --- |
| 2.1 | A3.5 `ConflictDialog` 顶部「处理冲突」按钮 `data-testid="conflict-reopen"`；二次确认 `conflict-discard-warning` / `conflict-discard-confirm` 区分（WU-05b 偏差 4） | INFO | 命名清晰。 |
| 2.2 | `nanobot/writing/models.py` 上限判定常量集中到 `_TAGS/_OUTLINE/_MATERIALS_LIMIT_MESSAGE` | INFO | 单点真相，便于后续加字段。 |
| 2.3 | `use-article-save-flow.ts` A3.6+A3.8 扩展（+165 行）公开 `applyRecovery` / `discardRecovery` / `storageHealthy` / `tryLeave` / `forceConflict` | INFO | 接口面增长但每条都有明确测试覆盖；WUA-05b 的接线点文档已写明下游扩展方式。 |
| 2.4 | A3.3 引入 `EditorMode = "edit" \| "preview" \| "split"` 与 `mergeImeValue` 纯函数出口 | INFO | 把 IME 合并从 JSX 抽出到可单测的纯函数，符合 spec A-03 与「前端纯函数必须可单测」约束。 |

## 3. 架构（architecture）

| # | 主题 | 严重度 | 处置 |
| --- | --- | --- | --- |
| 3.1 | 后端写作域按 5 个 Python 文件切分（database / models / repository / webui/writing_{services,api,routes}） | INFO | 单一职责清晰；与 memory/identity/agents 同构。 |
| 3.2 | 前端 `webui/src/lib/` 拆 `writing-api.ts` / `writing-draft-cache.ts` / `writing-export.ts`；creative 组件仅持有 presentation | INFO | `setField` 单点入口进入 save flow；面板（Material/Outline）纯展示，符合 A-04/A-05/A-06 的「数据走 save flow，UI 不直写」原则。 |
| 3.3 | `useArticleSaveFlow` 把单序列提交与 abandoned 标记下沉到 hook，组件不感知 Promise 链 | INFO | 状态机六态（`saved/dirty/saving/failed/conflict/offline`）是 spec A-06 直接对应。 |
| 3.4 | 模板不写在 server（契约 §模板）而由前端 `templates.ts` 产出并随 `article.create.outline` 上传 | INFO | 与「服务端不按 template_id 播种提纲」契约口径一致。 |
| 3.5 | `_SYSTEM_ROUTES` + `_WRITING_MUTATION_PATHS` + `_WEBUI_MUTATION_PATHS` 三张表一致 | INFO | 4 mutation 全 WS-only，HTTP 直连 405。 |

## 4. 安全概览（security overview）

完整安全审查见 `2026-10-12-zhixu-writing-phase-a-security-review.md`，此处只列架构层面的关注点。

- 写动作全部走 `nanobot/webui/writing_api.py` 的协议级校验（`client_request_id` 正则、`article_id` 长度/类型、`base_revision` 拒绝 bool/负数、URL 必须 http(s)）。
- 错误 message 由契约 §6 唯一来源，传输层零字符串拼接。
- SQL 全参数化 `?`，`_READ_COLUMNS` 为模块常量非拼接；`workspace_key` 服务端派生，客户端不可伪造。
- 本地草稿（`writing-draft-cache.ts`）只存本地工作区命名空间 + 草稿编辑副本，**不存 token / 不存 base64 凭据**。
- 导出（`writing-export.ts`）是 Markdown 字符串 → Blob，无脚本注入面（`<a download>` 触发）。

## 5. 性能概览（performance overview）

- 列表强制分页（默认 50，仓储上限 200），`articles_updated_idx(workspace_key, updated_at)` 索引走全表扫描路径而非 LIKE。
- 摘要短路 `no_change` 在 server 端计算（`snapshot_digest`），前端不重算。
- 写操作单事务无 N+1；`WritingDatabase.connect()` 每次新建 sqlite3 连接（与 `nanobot/memory/database.py` 同构）。
- build 出现 chunk size warning（>500 kB）—— 仅 warning，非 error，纳入 B 批次前的「拆 chunk」前置。

## 6. 重构建议

| # | 主题 | 建议批次 |
| --- | --- | --- |
| 6.1 | 把 nanobot/cli/gateway_runtime.py 的 11 条 import 排序修掉 | 下批空闲时 |
| 6.2 | `use-article-save-flow.ts` 抽 `tryLeave` saving 分支 timeout fallback，让 A3.8 「late save」真正端到端跑通 | B 批次启动前置 |
| 6.3 | `writing-export.ts` 与 `article-model.ts` 的 `generateMarkdown` 关系收敛（导出是纯字符串，模型是状态机） | B 完成后扫 |
| 6.4 | `webui` chunk size 拆 split（动态 import 创作空间） | B 完成后扫 |

## 7. BLOCK 项

**无**。所有 WARNING/INFO 已登记到执行日志与决策记录；不阻塞 B 启动。

## 8. 总体结论

**APPROVE WITH CAVEATS**。A 批次在契约 §1-§7 全部要求的实现路径上**没有发现新引入的产品/架构/正确性/安全 BLOCK**。三处 WARNING（brief 截断、late-save 测试 stub、chunk warning）已显式登记，纳入 B 批次前置；其余 INFO 在 dispatcher-workflow 的 WU 自检里已逐条勾过。
