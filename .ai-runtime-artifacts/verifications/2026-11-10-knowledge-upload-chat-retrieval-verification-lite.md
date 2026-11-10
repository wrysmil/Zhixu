---
artifact: verification-lite
route: leader-direct
skills:
  - verification-before-completion
skills_evidence:
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户原话：「修完之后记得继续完成测试，完成一个文档上传 到最后入库解析，最后在聊天页面问这个文档相关内容测试是否能检索的出来」
  - .ai-runtime-artifacts/verifications/2026-11-10-knowledge-vector-tile-verification-lite.md（向量统计修复）
created_at: 2026-11-10
tier: 1
---

# 文档上传 → 入库解析 → 聊天检索 — 轻量验证（Tier 1）

## 范围

- 修复后构建（`vectorCount` 已生效，`vite build` 重新打包）上的聚焦链路：
  上传新文档 → 解析/索引 `ready` → 分块核对 → 聊天提问检索。
- 语料：`e2e-release.md`（530 字符，《发布值班手册 2026 Q4》，含灰度比例、P1 电话、发布窗口、bash 围栏、表格）。
- 目标知识库：`E2E 全流程回归知识库`（已有 1 篇 `e2e-rollback.md`，本轮新增第 2 篇）。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `node …/run-upload-chat.cjs`（Playwright Chromium 1440×950） | **4/4 通过**，console error 仅 1 条 404（新会话 workbench 探测） |
| 知识库 DB 核对（`~/.nanobot/knowledge/state.db`） | `e2e-release`：`ready / vector_status=ok / 4 chunks / 530 chars / norm1:tok2:chunk1` |

## 实机步骤

| # | 步骤 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 上传 `e2e-release.md`，行出现 | 通过 | `step-01-uploaded.png` |
| 2 | 等待解析入库 | 通过：`就绪 530 字符 4 块` | `step-02-ready.png` |
| 3 | 分块预览核对关键事实 | 通过：4 块，`5%`、`15 分钟`、`021-6688-9900`、`10:00–16:00` 全部在块内 | `step-03-chunks.png` |
| 4 | 聊天提问「第一批放多少流量/观察多久？P1 升级给谁、电话？」 | 通过：回答引用 `doc://doc_9c79bad03e9c`，命中 5%、15 分钟、值班二号、021-6688-9900，并补出发布窗口 | `step-04-chat-answer.png` |
| 5 | 二次追问「发布窗口允许的时间段」 | 通过：回答命中 `10:00–16:00` | `step-05-chat-window.png` |

工作台「进展」同步显示知识来源（《发布值班手册(2026 Q4)》），说明检索链路进入了会话投影。

## 未验证项

- 无（本轮目标链路全绿）。
- 观察：新会话首次加载仍会出现一条 `/api/sessions/<新key>/workbench` 404，属既有探测行为，不影响页面与检索。

## Next

- 已完成；测试数据保留（知识库 2 篇文档、评测数据集 1 个，可按需清理）。
- 如需把「向量」修复与本次证据一起入库，请说「提交」。
