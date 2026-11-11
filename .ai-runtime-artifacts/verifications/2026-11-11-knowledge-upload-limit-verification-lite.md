---
artifact: verification-lite
route: leader-direct | 小改动直做
skills:
  - verification-before-completion
skills_evidence:
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户反馈：知识库上传被 6 MB 限制，要求放宽到 20 MB；上传失败后无法重新上传
  - 用户反馈：上传界面与侧边抽屉太丑，最多用居中弹框
created_at: 2026-11-11
tier: 1
---

# 知识库上传 20 MB、失败重试与弹框 UI — 轻量验证（Tier 1）

## 范围

- 上限与重试：
  - `webui/src/components/workspace/KnowledgePage.tsx`：`MAX_UPLOAD_BYTES` 6 MB → 20 MB；新增 `uploadRetryable` / `uploadError`；选择文件时清空旧错误。
  - `nanobot/webui/knowledge_api.py`：`_MAX_UPLOAD_BYTES` 6 MB → 20 MB（仍与 `knowledge.chunking.max_file_mb` 取小，默认 50 MB）。
  - `nanobot/webui/knowledge_services.py`、`docs/knowledge.md`：口径注释/文档同步。
- UI：
  - `webui/src/components/workspace/knowledge/KnowledgeImportSheet.tsx`：右侧 48rem 抽屉 → 居中 544px 弹框；拖拽/点击上传区、文件卡片（名称 + 大小 + 移除）、行内错误、重试按钮、分段 tab。
  - `webui/src/components/workspace/knowledge/KnowledgeDocumentSheet.tsx`：分块预览右侧抽屉 → 居中 768px 弹框，内容区独立滚动。
  - `webui/src/components/workspace/knowledge-shared/experience.css`：新增 `knowledge-dialog-compact` / `knowledge-dialog-wide`（原有 `.knowledge-dialog` 的 `max-width` 会覆盖 Tailwind 类）。
  - `webui/src/components/workspace/KnowledgePage.tsx`：「添加资料」加图标；弹框打开时隐藏页面级错误横幅；上传错误只留在弹框内。
  - `webui/src/i18n/locales/*/common.json`：`uploadHint` / `uploadTooLarge` 6 MB → 20 MB；新增 `chooseFile` / `dropHint` / `removeFile`（10 个语言）。
- 测试：`webui/src/tests/knowledge-page.test.tsx`（新增 3 条）、`tests/webui/test_knowledge_api.py`（新增 1 条）。

## 根因

- 6 MB 是 v1 知识库上传沿用的聊天附件入口语义上限，前后端各自硬编码。
- 「无法重新上传」：超限是前端本地拦截，`重试上传` 用同一个超限文件再次本地拦截，错误字符串不变 → React 状态无变化、无反馈，像按钮失效。
- 弹框宽度失控：`.knowledge-experience-surface.knowledge-dialog { max-width: calc(100vw - 2rem) }` 特异性高于 Tailwind 的 `max-w-[540px]`。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `cd webui && ./node_modules/.bin/vitest run src/tests/knowledge-page.test.tsx src/tests/knowledge-experience.test.tsx` | 56 passed |
| `cd webui && ./node_modules/.bin/vitest run src/tests/knowledge-eval-page.test.tsx src/tests/knowledge-graph-i18n-parity.test.ts src/tests/evaluation-i18n-parity.test.ts src/tests/knowledge-graph-view.test.tsx` | 28 passed |
| `cd webui && ./node_modules/.bin/tsc -p tsconfig.build.json --noEmit` | exit 0 |
| `npx eslint --config webui/eslint.config.js ...（4 个改动前端文件）` | exit 0 |
| `uv run --no-sync pytest tests/webui/test_knowledge_api.py -q` | 45 passed |

## Red-Green 证据

| 回退项 | 期望失败用例 | 实测 |
| --- | --- | --- |
| 前端 `MAX_UPLOAD_BYTES` 临时改回 6 MB | `accepts uploads up to the 20 MB webui limit` | 1 failed |
| `KnowledgeImportSheet` 临时去掉 `retryable` 条件 | `rejects files over 20 MB without offering a retry` | 1 failed |
| 后端 `_MAX_UPLOAD_BYTES` 临时改回 6 MB | `test_webui_upload_limit_defaults_to_20mb` | 1 failed（`assert 6291456 == 20971520`） |

三次回退均已恢复，恢复后上表命令全绿。UI 样式本身无自动化断言。

## 浏览器实机检查（headless Playwright，vite dev + 当前 8765 网关，1440×950）

- 打开知识库 → 文档 → 添加资料：弹框实测 `544 × 342`、水平居中（x=448），控制台 0 error。
- 选择 21 MB `.docx`：文件卡片显示 `big-test.docx · 21.0 MB`，行内红色提示「文件超过 WebUI 的 20 MB 上限，请改用 CLI 导入。」，无「重试上传」，有「移除文件」；未发出上传请求。
- 分块预览：弹框 `768 × 711`，内容区滚动正常，控制台 0 error；关闭后页面无残留错误横幅。
- 截图：`/tmp/upload-dialog-default.png`、`/tmp/upload-dialog-error.png`、`/tmp/chunk-preview-dialog.png`。

## 未验证项

- 深色模式、拖拽悬停高亮、真实文件走完整上传链路（需要重建 WebUI 并重启 8765 网关后回归）。
- 知识评测页的「新建评测 / 用例 / 数据集」仍是右侧抽屉，本次未改。
- 20 MB 经 base64 后约 26.7 MiB，仍在默认 36 MB WS 帧上限内；若把 `channels.websocket.maxMessageBytes` 调到 28 MB 以下，大文件会先被帧上限拦截。
- 历史 spec（`.ai-runtime-artifacts/specs/2026-11-09-knowledge-center-spec.md` 等）中的 6 MB 口径未回改，以本产物和 `docs/knowledge.md` 为新口径。

## Next

- 任务完成 → 无需暂停（除非用户要求 commit/MR）
- 需要真机验证 → 重建 WebUI、重启网关后回归
- 范围扩大 → 补 spec/plan 或升级 Tier 2 编排
