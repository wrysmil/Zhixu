---
artifact: verification
route: superpowers:verification-before-completion
skills:
  - verification-before-completion
skills_evidence:
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/verifications/2026-11-10-knowledge-rag-eval-verification.md
  - .ai-runtime-artifacts/verifications/2026-11-10-knowledge-recall-thresholds-and-protection-verification.md
branch: feature/desktop-app @ 04d75473
created_at: 2026-11-10
status: completed
---

# 知识全流程浏览器回归（feature/desktop-app @ 04d75473）

> 用户要求：拉取最新代码后「继续测试所有的一个流程，启动项目用浏览器工具测试，截图取证」。
> 本轮在**最新代码**上用无头 Chromium（Playwright，1440×950）走完整用户路径，
> 脚本与截图全部落盘，16 个步骤全通过。

## 1. 环境

| 项 | 值 |
| --- | --- |
| 代码 | `feature/desktop-app`，`git pull --ff-only` 至 `04d75473`（含知识评测台、召回阈值/分块保护/低召回扩展、多库扇出、cross-encoder 精排、分块预览、工作台知识来源） |
| 前端构建 | 拉取后重新 `tsc -b + vite build`（旧 dist 为 01:06 构建，不含新页面） |
| 网关 | `python -m nanobot webui --no-open -y`，WebUI `127.0.0.1:8765`、health `18790`、评测 API `8790` |
| 浏览器 | Playwright 1.64.1 + Chromium 148（`Google Chrome for Testing`，headless） |
| 语料 | `e2e-rollback.md`（633 字符：标题分节 + bash 围栏 + markdown 表格 + 退款/回滚关键词） |

## 2. 结论

- **16/16 步骤通过**，控制台仅 2 条已知探测错误（见 §4），无 JS 异常。
- 新特性在真实浏览器里生效：分块保护（代码块/表格均完整）、三模式检索、聊天内知识引用、
  评测台建集 → 运行 → 报告 → 二配置运行 → 对比。
- 发现 **1 个前端数据展示缺陷**（知识概览「向量」恒为 0），见 §4。

## 3. 步骤与证据

脚本：`run-fullflow.cjs`；报告：`e2e-fullflow-report.json`；截图同目录 `step-*.png`。

| # | 步骤 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 打开 WebUI 首页 | 通过 | `step-01-home.png` |
| 2 | 知识库总览渲染（5 张统计卡） | 通过 | `step-02-knowledge-overview.png` |
| 3 | 新建知识库「E2E 全流程回归知识库」 | 通过 | `step-03-kb-created.png` |
| 4 | 上传 `e2e-rollback.md` → `就绪` | 通过（633 字符 / 4 块） | `step-05-doc-ready.png` |
| 5 | 分块预览：代码围栏、表格未被切开 | 通过（`fenceIntact=true`、`tableIntact=true`，4 块） | `step-06-chunks.png` |
| 6 | 检索 hybrid「线上版本出问题了怎么回滚」 | 通过（4 命中，top1=「## 线上版本回滚」） | `step-07-search-hybrid.png` |
| 7 | 检索 keyword「kubectl rollout undo」 | 通过（1 命中，top1 含命令） | `step-08-search-keyword.png` |
| 8 | 检索 semantic「退款多久到账」 | 通过（4 命中，top1=「## 退款说明」） | `step-09-search-semantic.png` |
| 9 | 入库后统计卡 | 通过（知识库 1 / 文档 1 / 分块 4 / 待处理 0） | `step-09b-knowledge-stats.png` |
| 10 | 聊天「请根据知识库回答：线上版本出问题了怎么回滚？」 | 通过：回答给出 `kubectl rollout undo/status`，标注「来源：知识库《运维手册(E2E 全流程回归语料) > 线上版本回滚》」，工作台「进展」同步知识来源 | `step-10-chat-knowledge.png` |
| 11 | 评测台列表页加载（7 条指标口径下发） | 通过 | `step-11-eval-list.png` |
| 12 | 新建数据集（2 条查询，均关键词标注） | 通过 | `step-12-dataset-created.png` |
| 13 | 首个运行（base: hybrid / limit 8）→ 报告详情 | 通过：准确率 0.125 / 召回率 1 / 命中率 1 / MRR 1 / nDCG 1；逐题 2 行「已评分」且显示查询原文 | `step-13-run-detail.png` |
| 14 | 第二配置运行（base + keyword） | 通过：4 条逐题结果，配置列 `base`/`keyword` | `step-14-run-detail-2.png` |
| 15 | 对比两次运行 | 通过：7 指标 base/candidate/delta 全为 0.000，退化查询 0 条 | `step-15-compare.png` |

后端与检索的原始响应（报表、检索命中）保存在 `e2e-fullflow-report.json` 的 `reports` / `searches` 字段，
例如首轮聚合：`hit_at_k=1, precision_at_k=0.125, mrr=1, ndcg_at_k=1, recall_at_k=1, pool_coverage=0.25, unscored_rate=0`。

## 4. 发现

| # | 问题 | 级别 | 状态 |
| --- | --- | --- | --- |
| 1 | 知识概览「向量」统计恒为 0：后端 `stats.vector` 是对象（`status/backend/model/ok_docs/...`，实测 `vector_status=ok`、4 块已向量化），前端 `webui/src/lib/api.ts:1833` 仅接受 `number`，其余一律置 `null` | P2（展示） | **已修**：`vectorCount()` 取 `ok_docs`，单测 + 实机 `向量 1`（见 `2026-11-10-knowledge-vector-tile-verification-lite.md`） |
| 2 | 控制台 `404 /api/sessions/<新会话key>/workbench` | 无害 | 仅发生在**尚未创建**的新会话首次加载；打开历史会话/产生消息后不再出现（探针复测 0 条 4xx） |
| 3 | 控制台 `401 /api/eval/v1/auth/session` | 预期 | 评测 API 登录前探测，随后自动登录成功 |

## 5. 脚本侧修正（非产品缺陷）

首轮脚本失败是**测试脚本选错元素**，非应用缺陷，已修正后全绿：

1. 页面上有 2 个 `input[type=file]`（聊天侧栏隐藏附件输入在前），上传改为限定 `[data-testid="knowledge-page"]` 内。
2. 评测详情返回按钮与左侧导航同名「检索评测台」，改为限定页面内面包屑点击，避免导航重置分区。
3. 对比下拉第一项为空占位，改用 option value 选择，并等待「对比」按钮可用。

## 6. 复现

```bash
# 1) 网关（默认实例）
cd /Users/mima0000/Documents/学习-001/do-project/zhixu
.venv/bin/python -m nanobot webui --no-open -y

# 2) 前端构建（拉新代码后必须）
cd webui && npx tsc -p tsconfig.build.json --noEmit && npx vite build

# 3) 浏览器回归
node .ai-runtime-artifacts/verifications/2026-11-10-knowledge-fullflow-browser/run-fullflow.cjs
```

脚本可重复执行：会先清理同名知识库/数据集，再重建。

## 7. 本轮留下的测试数据

| 对象 | 名称 | 说明 |
| --- | --- | --- |
| 知识库 | `E2E 全流程回归知识库` | 1 篇文档、4 块、向量 ok |
| 评测数据集 | `E2E 全流程数据集` | 2 条查询（关键词标注） |
| 评测运行 | 多次 base / keyword 运行 | 可在「对比」分区查看 |

如需清理：知识库页删除该库；评测台「数据集」分区删除该数据集（运行记录会保留引用）。

## 8. 未覆盖

1. **LLM 判定组（judge）**：本轮两条查询都带期望关键词，只覆盖 `expect` 标注；judge 链路仍需真实判定跑一次。
2. **退化查询非空的浏览器呈现**：两次配置指标相同，退化区只验证了空态文案。
3. **桌面端（Electron）**：本轮只验证 Web 浏览器。
4. **文档删除/服务器路径入库**：未在本轮脚本内再跑（上轮巡检已覆盖删除，CORS DELETE 修复已并入本分支）。
