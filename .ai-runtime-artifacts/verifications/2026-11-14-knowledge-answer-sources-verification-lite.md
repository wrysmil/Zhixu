---
artifact: verification-lite
route: leader-direct | 小改动直做
skills:
  - verification-before-completion
skills_evidence:
  - ~/.agents/skills/verification-before-completion/SKILL.md
source: 用户原话「我希望如果调用知识库回答的问题，在AI回复之后能在底部增加那种知识来源的展现，然后点击可以查看到原文。原文可以在右边侧边栏展现，覆盖侧边栏，然后点击叉叉可以关闭，回归到工作台」
created_at: 2026-11-14
tier: 1
---

# 知识库回答底部的来源文档 — 轻量验证（Tier 1）

## 范围

- `webui/src/lib/knowledge-sources.ts`（新增）：解析 `knowledge_search` 结果 XML，按 `turnId` 汇总去重来源
- `webui/src/components/thread/KnowledgeSources.tsx`（新增）：回复底部来源条（默认 4 条，可展开全部）
- `webui/src/components/thread/KnowledgeSourcePanel.tsx`（新增）：右侧原文面板（本次引用 + 原文）
- `webui/src/components/thread/SubagentExecutionPanel.tsx`：`RightSurface` 增加 `knowledge`
- `webui/src/components/thread/ThreadShell.tsx`：来源开合、覆盖工作台、隐藏工作台按钮
- `webui/src/components/thread/ThreadViewport.tsx` / `ThreadMessages.tsx`：回调透传与按回合渲染
- `webui/src/i18n/locales/*/common.json`：`message.knowledgeSources.*`（10 个语言）
- `webui/src/tests/knowledge-sources.test.ts`（新增）、`webui/src/tests/thread-messages.test.tsx`（新增用例）

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `npx vitest run` | 173 文件通过 / 2 skipped；2156 passed / 2 skipped |
| `npx tsc -p tsconfig.build.json` | 无输出（通过） |
| `eslint`（改动文件） | 无输出（通过） |
| `npx vite build` | ✓ built；chunk 体积预警与改动前一致（SettingsView 544.13 kB） |
| 浏览器（Playwright + 真实网关 8765，会话 `websocket:393da1fb…`） | 来源条 1 个、9 条来源；折叠 4 / 展开 9；点击打开面板 → 工作台与工具按钮为 0；Escape 关闭、X 关闭均恢复工作台；console error 0 |

## 未验证项

- 移动端（<768px）Dialog 形态只做了代码路径覆盖，未在窄视口实机点击验证。
- 未新发一轮真实 LLM 调用；来源条验证基于历史回放（同一 turn 的 trace 行携带 `knowledge_search` 结果），实时流式路径与回放共用同一 `toolEvents` 结构。

## 补充：思考块折叠态只显示一行

- 改动：`ActivityStep` 新增 `labelSingleLine`（折叠态 `truncate`）；`ReasoningRow` 折叠标签截到 200 字并启用单行。
- 验证（Playwright + `websocket:a7f66732…` 会话）：折叠态 label `height=24px`、`white-space: nowrap`、`text-overflow: ellipsis`、`scrollWidth 1340 > clientWidth 594`（省略号生效）；点「查看详情」后 detail 出现（高 104px）。
- 测试：`message-bubble.test.tsx` 增加 `toHaveClass("truncate")` 断言；全量 2156 通过。
