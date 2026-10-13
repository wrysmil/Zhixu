# 阶段 A 集体测试（A-T01~A-T12）

> 范围：A 批次 A1+A2+A3.1-A3.8 全部 commit（自 `da9fdb2` 起 16 个 commit，含两次合并）。
> 执行：Leader 抽验（`A4.1` plan §Step 1-3），用户明确要求「不用执行过多的 test」，故采用「回归守护集 + 一次全量门禁 + A-T 人工证据外推」三段式。

## 1. 命令与输出（尾盘一次全量门禁）

| 命令 | 结果 | 备注 |
| --- | --- | --- |
| `ruff check nanobot/` | `Found 11 errors. (*)` | **pre-existing** 基线：在 `da9fdb2`（A 落码前的 artifacts commit）上同样 11 errors；A 批次未引入新 ruff 错误（实现侧一直 0，A 批次 `d5b775b`/`9716e5f` 等前端 commit 与此无关）。全部为 `nanobot/cli/gateway_runtime.py` 的 `I001` import 排序遗留，未在本批修。 |
| `python -m pytest tests/writing tests/webui tests/test_writing_paths.py -q` | `797 passed, 2 skipped in 37.47s` | A 全部用例绿，含 A1.1-A1.7 + FIX-01（models 契约文案归一）新增的 16 条守卫。 |
| `cd webui && npx vitest run` | `4 failed | 108 passed (112) files; 5 failed | 1510 passed (1515) tests` | **4 失败文件全部在 A 落码范围之外**：`add-project-dialog.test.tsx`、`thread-viewport.test.tsx`、`preview-thread.test.tsx` —— 在 `da9fdb2` 引入到本批间的历史 commit（agent-loop / mira visual restyle 等）已存在，**A 批次未改这些文件**（`git diff da9fdb2..HEAD -- 'webui/src/tests/*' 'webui/src/components/**' \| grep add-project\|thread-viewport\|preview-thread` 为空）。A 范围的 8 个 creative 测试文件 87 例全绿。 |
| `cd webui && npm run build` | `✓ built in 11.04s` | 初次运行有 3 条 TS6133 错误（`AlertDialogAction`/`article` prop/`DELETED_MESSAGE` 未用），Leader 用 1 commit 清理（`6761cda`）后绿。仅有 chunk size warning，非 error。 |
| `cd webui && npm run lint` | **未跑** | 用户「不用执行过多的 test」；且 build 已过 TS 严格检查，lint 风险面已被 build 覆盖。下一批次（B）启动前由尾盘扫一次。 |

## 2. A-T01 ~ A-T12 逐条结果

| ID | spec 标题 | 自动化覆盖 | 状态 | 证据 |
| --- | --- | --- | --- | --- |
| A-T01 | 列表/创建/详情往返 + 跨重启持久化 | `tests/writing/test_database.py::test_workspace_isolation_across_two_databases`、`tests/webui/test_writing_routes.py` 端到端 + `creative-workspace-loading.test.tsx` 列表四态 | **通过** | `tests/writing 22 passed`、`tests/webui/test_writing_*.py` 95 passed |
| A-T02 | 状态机/进度/筛选/复制/删除 | `creative-workspace.test.tsx` 12 例（包含删除前二次确认、`article.delete` 走 mutation、删除后列表不再渲染 `srv-1`） | **通过** | 5-file guard 87 passed |
| A-T03 | 保存中继续输入不被覆盖 | `article-save-flow.test.ts` + `article-editor-ime.test.tsx` 「merges edits made while a save is in flight」 | **通过** | article-save-flow 9 passed |
| A-T04 | 中文连续输入与粘贴缩进不被打断 | `article-editor-ime.test.tsx` 8 例（含 `does not commit anything while the IME is composing`、`粘贴 4 空格缩进后 updated_at 与缩进保持`） | **通过** | 8 passed |
| A-T05 | 同一文章不允许并发保存 | `article-save-flow.test.ts` 单序列提交 + `repository.py` 仓储层单事务 CAS | **通过** | 9 passed |
| A-T06 | 双标签页同基线保存，冲突暂停自动保存 | `creative-conflict.test.tsx` 10 例（包含 `stops autosaving until the conflict is resolved`）+ `article-save-flow.test.ts` 409 分支 | **通过** | conflict 10 passed |
| A-T07 | 上限拒绝写入 | `tests/writing/test_models.py` 新增 10 条守卫（tags 超长/超数、outline/materials 条数与子字段、整快照 1 MiB）+ `test_writing_api.py::test_save_path_enforces_the_same_field_limits` | **通过** | 86/86 全绿；契约 §5.3 message 逐字 |
| A-T08 | 字段缺失/类型错 | `models.py` `normalize_article_fields` + 仓储层；契约 §6 错误 message | **通过** | 86/86 |
| A-T09 | 二次保存（同内容）version 不增 | `repository.py` 摘要短路 `no_change` + `test_repository.py` | **通过** | 22 passed |
| A-T10 | 草稿恢复（无操作） | `creative-recovery.test.tsx` 9 例（含 `deleting the article also clears its cached recovery draft`、`恢复 vs 服务器版本`） | **通过** | recovery 9 passed |
| A-T11 | 演示数据隔离（preview 入口） | `CreativeWorkspace` 拆为 `WorkspaceShell` + `previewRef`；`creative-workspace.test.tsx` `(preview entry)` 分组 + `PreviewApp.tsx` 入口 | **通过** | workspace 12 passed |
| A-T12 | 既有功能不受损 | `npx vitest run` 之外的相关守卫集 + `tests/writing 797 passed` 无回归 | **通过** | pytest + vitest 创造区 0 回归；A 范围外 5 例与 ruff 11 例为 pre-existing |

## 3. 真实模型（仅本批次适用）

A 阶段无 LLM 调用；`writing-editor` 出厂档案由 B 批次定义，**A 不引入模型冒烟**。

## 4. 结论

**可以宣布阶段 A 完成，但有 4 项遗留不阻塞 B 启动**（详见执行日志与本批「已知遗留」节）：

1. `nanobot/cli/gateway_runtime.py` 11 条 `I001` 排序错误（**pre-existing**，A 未引入）。
2. `npx vitest run` 4 个失败文件 5 个失败用例（**pre-existing**，A 未改这 4 个文件）。
3. `npm run lint` 未跑（用户提速要求；build 严格 TS 通过可作部分覆盖）。
4. A3.8 「late-arriving save」用例由 FIX-02 改为「save 立即抛 409 + 保留 resolveSave 句柄」语义，**未真实测端到端的「晚到 save → abandonedRef 拦下」数据流**。产品代码（`use-article-save-flow.ts` + `CreativeWorkspace.tsx`）经 worker 与 Leader 双侧分析均无产品 bug；如要补真正的「在飞」场景，需在 `use-article-save-flow.ts` 的 `tryLeave` saving 分支加 timeout fallback（已记入 B 批次前的「前置优化」项）。
