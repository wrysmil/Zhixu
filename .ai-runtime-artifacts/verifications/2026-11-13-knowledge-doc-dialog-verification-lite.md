---
artifact: verification-lite
route: leader-direct
skills:
  - source-driven-development
  - verification-before-completion
source:
  - 用户原话：「这个不要侧边栏，用弹窗表示，然后我希望可以支持看原文，原文可以支持markdown，pdf，word文档的一个查看，预览」
  - 用户追加：「按这四项继续」（服务端分页 10/页、分段搜索、展开/收起、原文页独立）
created_at: 2026-11-13
tier: 1
---

# 知识文档「分块详情」弹窗 + 原文预览 — 轻量验证（Tier 1）

## 范围

### 后端

- `nanobot/knowledge/preview.py`（新增）：按源文件类型生成原文预览 —— md 原文、纯文本、PDF 逐页文本、DOCX 段落/标题/表格；字符与条目上限 200k / 100 页 / 2000 块，错误信息不含文件系统路径。
- `nanobot/webui/knowledge_routes.py`：新增读动作 `knowledge-doc-content`（`/api/settings/knowledge/doc/content`）。
- `nanobot/webui/knowledge_api.py`：`_doc_content` 处理器；`knowledge-doc-get` 增加 `chunkOffset` / `chunkQuery`，返回 `chunkTotal`；`chunkLimit` 上限 20 → 50。
- `nanobot/knowledge/repository.py` / `service.py`：`list_chunks` 支持 `query`（LIKE 通配符转义）与偏移分页；新增 `count_chunks`。

### 前端

- `KnowledgeDocumentSheet.tsx` → `KnowledgeDocumentDialog.tsx`：右侧抽屉改为居中弹窗（固定头尾、中间滚动），分块/原文两个标签。
- `KnowledgeSourcePreview.tsx`（新增）：markdown 渲染、PDF 分页文本、Word 段落与表格、纯文本。
- 分块页对齐 Dify：服务端分页 10/页、内容搜索（300ms 防抖）、展开/收起正文、空搜索态。
- `KnowledgePage.tsx`、`lib/api.ts`、`experience.css`、10 个 locale 文案、`knowledge-page.test.tsx`。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `uv run --no-sync python -m pytest tests/webui/test_knowledge_api.py tests/knowledge/storage/test_repository.py -q` | 113 passed（含原文预览 4 例、分页/搜索 1 例、LIKE 转义 1 例） |
| `uv run --no-sync ruff check nanobot/knowledge nanobot/webui/knowledge_api.py` | All checks passed |
| `webui/node_modules/.bin/vitest run src/tests/knowledge-page.test.tsx` | 26 passed（含弹窗原文页、分页/搜索/收起 2 例） |
| `webui/node_modules/.bin/tsc -p tsconfig.build.json --noEmit` | exit 0 |
| `webui/node_modules/.bin/eslint --config webui/eslint.config.js <touched files>` | exit 0 |
| 隔离实例 curl：`/api/settings/knowledge/doc?id=…&chunkLimit=2&chunkOffset=1&chunkQuery=记忆` | `chunkCount=4 chunkTotal=3 offset=1 limit=2 chunks=[1,3]` |
| 隔离实例 curl：`/api/settings/knowledge/doc/content?id=…` | `kind=markdown name=记忆与协作示例.md len=270`，正文与源文件一致 |

## 未验证项

- **浏览器实机视觉未完成**：隔离实例（gateway 18821 / vite 5183）启动成功，但共享 `webui/node_modules/.vite/deps` 被并行会话的 dev server 占用，页面停在「正在加载 知序…」并报 `504 Outdated Optimize Dep`；停止验证进程、未对弹窗做截图/几何量测。需要独占 cacheDir 或重启 dev server 后补验。
- 原文预览对扫描版 PDF 只显示「无文本」提示，不含 OCR；PDF 仅文本预览，不做页面栅格化（pymupdf 非默认依赖）。

## Next

- 待补：浏览器实机确认弹窗宽度（68rem）、双标签与分页在真实 gateway 下的表现。
- 范围扩大（分段编辑/启停、命中次数）→ 需新写动作与 schema 变更，另开 spec。
