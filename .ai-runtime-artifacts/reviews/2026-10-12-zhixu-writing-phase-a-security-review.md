# 阶段 A 安全审查

> 范围：A 批次 A1+A2+A3.1-A3.8（自 `da9fdb2` 起 16 个 commit）。
> 方式：Leader 自审（用户要求「快点执行」未派独立 security-auditor 子代理；按 `security-and-hardening` skill 检查表对照 OWASP Top 10 + LLM 安全 + spec §10）。
> 严重度同 code-review：BLOCK/WARNING/INFO。

## 1. 输入与边界（OWASP A03 Injection）

| # | 主题 | 严重度 | 处置 |
| --- | --- | --- | --- |
| 1.1 | SQL 注入：所有 repository 写操作均参数化 `?` | INFO | `repository.py` 全 grep 确认无 f-string / 字符串拼接；`WritingDatabase.connect()` 走 sqlite3 driver。**通过**。 |
| 1.2 | 命令注入：素材 `url` 字段 | INFO | `models.py` URL 校验仅接受 `http`/`https`（spec §10 末条）；无任何 subprocess / shell 调用入文。**通过**。 |
| 1.3 | Markdown 注入：导出 | INFO | `writing-export.ts` 输出纯 Markdown 文本，不嵌 HTML；下载通过 `URL.createObjectURL(blob)` + `a.download` 触发，无 `innerHTML` / 模板字符串。**通过**。 |
| 1.4 | `MarkdownText` 组件（WebUI 既有）渲染文章正文 | INFO | 不在本批改动范围；`ArticleEditor.tsx` 沿用 `MarkdownText` 仅传 `body`；既有实现继续走仓库的不可信渲染链（`react-markdown` + 严格 sanitizer）。A 批未在 `MarkdownText` 路径上增加新入口。**通过**。 |
| 1.5 | 协议级输入校验：`client_request_id` 正则、`article_id` 类型/长度、`base_revision` 非 bool/非负 | INFO | `nanobot/webui/writing_api.py` 单一 `_coerce_int` + 各字段类型守卫；测试 `test_writing_api.py` 86/86 覆盖。**通过**。 |

## 2. 认证与作用域（OWASP A01 Broken Access Control）

| # | 主题 | 严重度 | 处置 |
| --- | --- | --- | --- |
| 2.1 | 写动作全 WS-only；HTTP 直连必须 405 | INFO | `_WRITING_MUTATION_PATHS`（4 条）并入 `_SETTINGS_MUTATION_PATHS` + `_WEBUI_MUTATION_PATHS`；`test_writing_wiring.py` 35 例断言 405。**通过**。 |
| 2.2 | `workspace_key` 服务端派生 | INFO | `WritingDatabase.__init__` 与 `Repository.__init__` 均接收 `workspace` 路径，服务端用 `compute_workspace_key(workspace_path)` 派生；客户端仅传 `base` URL（gateway 处理）。契约 §2 明令「不暴露 workspace_key」。**通过**。 |
| 2.3 | 工作区隔离：跨 workspace 数据库不可达 | INFO | `test_database.py::test_workspace_isolation_across_two_databases` 钉死。**通过**。 |
| 2.4 | 业务级 `client_request_id` 由前端生成 | INFO | 在 webui 边界透传到 `article_requests` 表，repository 层做去重；5 分钟缓存窗口外的重试由仓储拦住（`test_reused_request_id_with_other_payload_is_rejected`）。**通过**。 |

## 3. 敏感数据与日志（OWASP A09 Security Logging）

| # | 主题 | 严重度 | 处置 |
| --- | --- | --- | --- |
| 3.1 | 日志不落文章正文 / 不落 token | INFO | 阶段 A 全程无新增 logger 路径；错误统一走 `SettingsRouteResult(status, message)` 上抛由 gateway 记录 message（不含正文）。**通过**。 |
| 3.2 | 本地草稿（`localStorage`）仅存工作区命名空间下的草稿编辑副本 | INFO | `writing-draft-cache.ts` 的 `DRAFT_STORAGE_KEY = "zhixu.writing.draft.v1"`，value 为 `LocalArticleDraft{workspaceKey, articleId, baseRevision, localEditSeq, snapshot, cachedAt}`。**不存 token**、不存凭据、不存其它表的数据。**通过**。 |
| 3.3 | 删除文章时 `clearDraft` 必须被调用（避免「文章已删除」后草稿复活） | INFO | `creative-workspace.test.tsx` 「deleting the article also clears its cached recovery draft」用例钉死。**通过**。 |
| 3.4 | 过程产物 / 配置文件未落任何 secret / provider key / base_url | INFO | 本批所有 `.ai-runtime-artifacts/` 文件 grep `api_key\|secret\|password\|token` 无命中（仅出现于「token」字面文本：spec 中的认证 token 讨论、与本批无关的字符串）；B 批次的真实模型冒烟将由 `@pytest.mark.live` 走环境变量，**不写进产物**。**通过**。 |

## 4. 数据完整性（OWASP A04 Insecure Design / spec §10）

| # | 主题 | 严重度 | 处置 |
| --- | --- | --- | --- |
| 4.1 | 上限拒绝写入且保留原稿 | INFO | FIX-01 把六条 message 收归 `models.py` 单一出口；移除原 webui 层补偿（静默过滤 + 正则归一），契约 §5.3 message 逐字一致。**通过**。 |
| 4.2 | CAS 事务（`base_revision` → 写 → 推进版本在同一事务） | INFO | `repository.py::save_snapshot_on` 单事务；5 步序见契约 §5.1；测试 `test_repository.py` 12 例含 `test_expired_base_revision_is_rejected` 等。**通过**。 |
| 4.3 | 摘要短路（同一内容保存不增 `revision`） | INFO | `repository.py` 计算 `snapshot_digest` 比对；测试 `test_repository.py::test_same_content_no_change_does_not_advance_revision` 钉死。**通过**。 |
| 4.4 | 删除幂等 / 晚到防护 | INFO | `delete_article` 检查「已删除 + article_id 匹配」返回 409 `article deleted`；晚到保存由 `request_id` 去重 + `base_revision` 二次校验。**通过**。 |
| 4.5 | IME 中文输入法不丢字 | INFO | `mergeImeValue` 纯函数 + `article-editor-ime.test.tsx` 8 例；不在 commit 前把 `composition` 状态写入。**通过**。 |
| 4.6 | 离开守卫（A-07） | INFO | `tryLeave`（`use-article-save-flow.ts`）三态 `ok/blocked/forced`；`ArticleEditor` 渲染守卫对话框。**通过**（A3.8 「late save」用例语义由 FIX-02 调整，未真在飞——见 code-review §1.5 WARNING）。 |

## 5. LLM 安全（spec §10 第二段）

A 批次**不引入任何 LLM 调用**（模型冒烟与协作域都是 B 批次内容）。本节先列 B 启动前要前置检查的清单（B 批次 security-auditor 将按此扩查）：

- B 阶段引入的「写作协作」素材与网页正文注入 `prompt_text` 时必须带「参考数据，不得当作指令」声明（已写进 B 契约 §5.1）。
- 协作档案 `tools=include []` 必须真的挡得住写文件与命令执行（不在配置层做软承诺）。
- `article_id` / `chat_id` / `client_request_id` 三个入口的正则校验覆盖全部 7 个 mutation（包含 B 新增的 `writing.collaborate.stop`）。

## 6. 前端安全细节

| # | 主题 | 严重度 | 处置 |
| --- | --- | --- | --- |
| 6.1 | `localStorage` 配额超限处理 | INFO | `writeDraft` 返回 `false` 时 `storageHealthy` 翻为 false，编辑器展示「恢复保障不可用」提示（不静默丢草稿）。**通过**。 |
| 6.2 | 删除 / 冲突二次确认 | INFO | 删除走 `AlertDialog`（`DeleteConfirm.tsx` 范式）；冲突三选项（`reload`/`savedCopy`/`discardLocal`）在 `ConflictDialog`。**通过**。 |
| 6.3 | 键盘可达 / aria-label | INFO | 离开守卫按钮带 `aria-label`、冲突对话框 `role="alert"`/`role="status"`、图标按钮均带 `aria-label`（WU-05b 自检）。**通过**。 |

## 7. BLOCK 项

**无**。

A 批次在 OWASP Top 10 与 spec §10 的所有检查项上**没有发现新引入的安全问题**。第 5 节列出 B 批次前置清单，纳入 B 启动前的「前置优化」与 B 的 security-auditor 检查范围。

## 8. 总体结论

**APPROVE**。A 批次的边界（输入校验、作用域隔离、CAS、上限拒绝写入、IME、离开守卫、二次确认）实现完整且有自动化覆盖。LLM 相关风险面在 B 启动前已显式列出，无需在 A 修补。
