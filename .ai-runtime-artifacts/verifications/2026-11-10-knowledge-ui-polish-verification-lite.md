---
artifact: verification-lite
route: leader-direct
skills:
  - verification-before-completion
skills_evidence:
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户反馈：聊天「处理中」蓝色胶囊丑；知识库详情页（上传区 + 文档表）丑
created_at: 2026-11-10
tier: 1
---

# 聊天运行态胶囊 + 知识库详情页视觉调整 — 轻量验证（Tier 1）

## 范围

| 区域 | 改动 |
| --- | --- |
| 聊天运行态 | `ThinkingReasoningShell.tsx`：彻底去掉胶囊外形（无底色/无圆角/无描边），改为行内状态：小 spinner（或完成态对勾）+ 12.5px 灰色文字；展开态顶部扫光由 sky 改为 `via-foreground/25` |
| 过程步骤徽标 | `ActivityStep.tsx`：`active` 档由 sky 改为石墨中性（`border-border/70 bg-muted/55 text-foreground/75`）；成功/失败保留绿/红 |
| 知识库详情 | `KnowledgePage.tsx`：原生文件控件 → 虚线卡片 + 样式化「上传」按钮（input 保留 `aria-label` 并 `sr-only`）；服务器路径独立卡片；文档表头去掉大写+字距、数字列与操作列右对齐、行高加大；「重建」改为图标按钮（带 aria-label） |
| 测试 | `agent-activity-cluster.test.tsx` 同步断言：active kicker 由 `text-sky-700` 改为 `bg-muted/55` |

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `npx vitest run knowledge-page agent-activity-cluster message-bubble` | **115 passed**（16 + 57 + 42）；去胶囊后复跑 `agent-activity-cluster + message-bubble`：**99 passed** |
| `npx tsc -p tsconfig.build.json --noEmit` | 通过 |
| `npx vite build` | `✓ built in 6.23s` |
| 浏览器检查（Playwright 1440×950） | 4/5（最后一项因模型请求挂起超时，与样式无关）；去胶囊后实测运行态行：`{"text":"处理中 1秒","bg":"rgba(0,0,0,0)","radius":"0px","border":"0px"}` |

## 截图

| 文件 | 内容 |
| --- | --- |
| `chat-inline-status.png` | 最终形态：无胶囊，行内 spinner + 「处理中 1秒」灰色文字 |
| `chat-running-after.png` | 中间态：中性灰胶囊（已被行内状态替换） |
| `knowledge-detail-final.png` | 知识库详情：上传/服务器路径双卡片、文档表新排版 |
| `knowledge-detail-after.png` | 表头与操作列调整前的中间态截图 |

## 未验证项

- 深色主题下新中性色的对比度未逐像素核对（沿用现有 token，理论无回归）。
- 运行结束后的完成态截图：本轮模型请求挂起，未能截到；完成态样式未改动（仍为 `bg-muted/60`），由单测覆盖。
- 移动宽度（≤400px）下双卡片堆叠未截图；`lg:grid-cols-2` 保证窄屏单列。

## Next

- 如需继续调色（比如品牌色、圆角、字号），给出目标即可。
- 若确认无误，可说「提交」把向量修复 + 本次视觉调整一起入库。
