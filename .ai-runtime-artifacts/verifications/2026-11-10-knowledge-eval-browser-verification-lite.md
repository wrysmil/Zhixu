---
artifact: verification-lite
route: leader-direct
skills:
  - verification-before-completion
skills_evidence:
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户原话：「然后测试一下评测的吧，用浏览器工具测试」
  - .ai-runtime-artifacts/verifications/2026-11-10-knowledge-eval-browser/run-eval-flow.cjs
created_at: 2026-11-10
tier: 1
---

# 检索评测台浏览器回归 — 轻量验证（Tier 1）

## 范围

当前已提交构建（`42469b39` 之上重建 dist）上的评测台全功能路径：
使用指南/模板下载 → 模板导入 → 表单建集 → 标注切换 → 隐私门禁 → 两配置运行 → 报告 → 口径区 → 第二次运行 → 对比 → 删除数据集 → 刷新。

实机环境：WebUI `127.0.0.1:8765`、评测 API `8790`，Playwright Chromium 1440×950。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `node …/run-eval-flow.cjs` | **11/11 通过**；console 仅 1 条 `401 /api/eval/v1/auth/session`（登录前探测，随后自动登录 200） |

## 实机步骤

| # | 步骤 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 评测台列表加载（7 条指标口径） | 通过 | `eval-01-list.png` |
| 2 | 使用指南五步齐全 + 下载模板 | 通过：`knowledge-eval-dataset-template.csv`，129 字节（含 BOM、表头 + 3 行） | `eval-02-manual.png`、`knowledge-eval-dataset-template.csv` |
| 3 | 模板导入成数据集 | 通过：3 条查询，2 条关键词标注 + 1 条模型判定 | `eval-03-imported.png` |
| 4 | 表单建集与标注推断 | 通过：`关键词标注` / `模型判定` 两种徽标同时出现 | `eval-04-datasets.png` |
| 5 | 隐私门禁（judge 用例） | 通过：弹出「模型判定会把命中块正文发给模型」，点取消后**未**发起运行、停留在列表 | `eval-05-privacy.png` |
| 6 | 标注切换（模型判定→关键词标注） | 通过：保存期望关键词后徽标即时翻转 | `eval-06-labeled.png` |
| 7 | 两配置运行（base hybrid + keyword）→ 报告 | 通过：`completed`，2 配置 4 条逐题；指标 命中率 1 / 准确率 0.125 / MRR 1 / nDCG 1 / 召回率 1 / 候选池占比 0.125 / 未评率 0 | `eval-07-report.png` |
| 8 | 指标口径区（后端下发） | 通过：展开后含「相关块数」「未评率」等公式口径 | `eval-08-guide.png` |
| 9 | 第二次运行 + 对比 | 通过：7 行指标 base/candidate/delta 全为 0.000；退化查询 0 条且显示空态文案 | `eval-09-compare.png` |
| 10 | 删除导入数据集 | 通过：列表消失 | `eval-10-deleted.png` |
| 11 | 刷新按钮 | 通过：无错误警报 | — |

后端报告与对比原始响应保存在 `eval-report.json`（`reports` / `compares`）。

## 未验证项

- **judge 真实判定质量**：本轮只验证门禁弹窗与标注切换，取消后把 judge 用例改回关键词标注再跑，未真实调用判定模型。
- **运行取消按钮**：两次运行都在数秒内完成，未抓到 `queued/running` 窗口点取消。
- **退化查询非空呈现**：两次配置指标一致，只验证了空态。
- **10 语言文案逐条校对**：未在本轮范围。

## 测试数据

- 保留：`E2E 评测回归数据集`（2 条查询，2 次运行）、旧数据集 `E2E 全流程数据集`。
- 已删：导入的 `knowledge-eval-dataset-template`（验证删除功能）。

## Next

- 需要真跑 judge 组或验证取消路径时，再单独发一轮；本轮无失败项。
