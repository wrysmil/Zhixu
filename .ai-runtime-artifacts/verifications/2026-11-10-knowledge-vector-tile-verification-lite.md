---
artifact: verification-lite
route: leader-direct
skills:
  - verification-before-completion
skills_evidence:
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户原话：「先修好向量显示的缺陷」
  - .ai-runtime-artifacts/verifications/2026-11-10-knowledge-fullflow-browser-verification.md §4
created_at: 2026-11-10
tier: 1
---

# 知识概览「向量」统计恒为 0 — 轻量验证（Tier 1）

## 范围

- 缺陷：后端 `stats.vector` 是对象（`status/backend/model/ok_docs/failed_docs/pending_docs`），
  前端 `fetchKnowledgeStats` 只接受 `number`，其余置 `null`，页面 `?? 0` 后恒显示 0。
- 改动文件：
  - `webui/src/lib/api.ts`：新增 `vectorCount()`，对象口径取 `ok_docs`，保留裸数字兼容；形状不认识仍返回 `null`（不伪造 0）。
  - `webui/src/tests/knowledge-stats-api.test.ts`（新增）：对象口径 / 裸数字 / 不可识别三种用例。
- 路由判定：Tier 1（Leader 直做），无行为面变更，仅数据归一化。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `npx vitest run src/tests/knowledge-stats-api.test.ts src/tests/knowledge-page.test.tsx` | **19 passed**（3 + 16） |
| `npx tsc -p tsconfig.build.json --noEmit` | 通过（无输出） |
| `npx vite build` | `✓ built in 6.40s` |
| 浏览器实机（Playwright，1440×950）读取统计卡 | `{"kbs":"知识库 1","docs":"文档 1","chunks":"分块 4","pending":"待处理 0","vectors":"向量 1"}` |

实机前提：知识库 `E2E 全流程回归知识库` 1 篇文档、4 块，`documents.vector_status=ok`。
截图：`2026-11-10-knowledge-fullflow-browser/step-16-vector-tile-fixed.png`。

## 未验证项

- 向量后端不可用（`status=disabled`）时统计卡显示 0：与 `ok_docs=0` 视觉一致，尚未在禁用向量的实例上实机核对。
- `failed_docs > 0` 的展示（例如"1 失败"）：当前统计卡是单一数字口径，未加副标题，属后续产品决策。

## Next

- 任务完成 → 无需暂停；如要入库请说「提交」。
- 若需要区分 ready/disabled/failed 的状态语义 → 需拆 spec（统计卡加状态徽标），不要在本修复上继续加逻辑。
