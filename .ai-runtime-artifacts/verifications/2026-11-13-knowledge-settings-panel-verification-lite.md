# 知识库检索设置面板 · 轻量验证

- 日期：2026-11-13
- 范围：检索/精排参数读写与热应用 + WebUI「检索设置」页签
- Route：Tier 1 Leader 直做

## 变更清单

| 文件 | 内容 |
| --- | --- |
| `nanobot/knowledge/service.py` | 新增 `apply_retrieval_settings()`：只重建检索器，已入库块与向量不变 |
| `nanobot/webui/knowledge_routes.py` | 新增读动作 `knowledge-settings-get`、写动作 `knowledge.settings.update` 及路径 |
| `nanobot/webui/knowledge_services.py` | 新增 `config_writer` 注入缝、`save_config()`、`live_service` |
| `nanobot/webui/knowledge_api.py` | 参数 payload 转换、范围校验、跨字段校验、落盘 + 热应用 |
| `webui/src/lib/api.ts` | `KnowledgeRunSettings` 类型 + `fetchKnowledgeSettings` / `updateKnowledgeSettings` |
| `webui/src/components/workspace/knowledge/KnowledgeSettingsPanel.tsx` | 设置面板（权重/RRF k/条数/阈值/MMR/精排），分段参数只读展示 |
| `webui/src/components/workspace/KnowledgePage.tsx` | 详情页新增「检索设置」页签 |

## 验证命令与结果

- `python -m pytest tests/webui/test_knowledge_settings_api.py -q` → **12 passed**
- `python -m pytest tests/webui/test_knowledge_api.py tests/webui/test_knowledge_settings_api.py -q` → **63 passed**
- `python -m ruff check`（5 个改动文件）→ **All checks passed**
- `cd webui && npx tsc -p tsconfig.build.json` → **无输出（通过）**
- `cd webui && npx vitest run src/tests/knowledge-settings-panel.test.tsx src/tests/knowledge-page.test.tsx` → **30 passed**（面板 4 + 页面 26）
- `npx eslint`（4 个改动前端文件）→ **无输出（通过）**
- 实机渲染：浏览器打开知识库详情，可见「检索设置」页签；面板进入错误态时显示后端返回的 `API route not found`，未白屏（见 `research/knowledge-demo/live-settings-tab.png`）

## 未闭环 / 已知限制

1. **运行中的 gateway 未重启**：8765 上的进程早于本次改动，`GET /api/settings/knowledge/settings` 返回 404，因此实机截图停留在错误态。重启 gateway 后即可看到真实参数。
2. 分段参数目前只读展示；分块试跑预览接口未做（下一步）。
3. 检索结果未透出 `candidates_in/kept` 与精排 outcome（后端已有字段，API 未映射）。
4. 召回测试「存为评测用例」未接。
5. i18n 仅用 `defaultValue` 兜底中文，未补 10 语言词条。

## 环境备注

- `webui/node_modules/.vite` 不可写（EPERM），dev server 依赖优化会失败；本次用临时 `vite.live.config.ts` 把 `cacheDir` 指到仓库内可写目录完成实机渲染，验证后已删除临时文件与缓存目录。
