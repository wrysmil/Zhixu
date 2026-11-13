---
artifact: verification-lite
route: leader-direct | 小改动直做
skills:
  - verification-before-completion
skills_evidence:
  - ~/.agents/skills/verification-before-completion/SKILL.md
source:
  - "合并到desktop / 继续合并"
created_at: 2026-11-13
tier: 1
---

# 代码分支合流：codex/knowledge-experience → feature/desktop-app — 轻量验证（Tier 1）

## 范围

- 改动文件：合流提交 `acfa57c2`（37 个文件）；测试修订 3 个文件
  - `webui/src/App.tsx`
  - `webui/src/components/workspace/KnowledgePage.tsx`
  - `webui/src/components/workspace/knowledge/**`
  - `webui/src/components/workspace/knowledge-shared/experience.css`
  - `webui/src/i18n/locales/*/common.json`
  - `webui/src/tests/{knowledge-graph,knowledge-graph-view,app-layout,thread-shell,preview-isolation}.test.tsx`
- 路由判定：Tier 1（Leader 直做，Git 合流 + 过期断言修订）

## 冲突处理口径

| 文件族 | 决策 | 理由 |
| --- | --- | --- |
| 知识库页面 / 图谱 / 导入弹框 | 采用 `codex/knowledge-experience`（较晚，11-12） | 拖拽导入、流向式 ForceGraph、引用来源卡、实体胶囊为用户确认后的形态 |
| `App.tsx` 会话栏显隐 | 采用主线规则 `view === "chat"` | 身份配置、记忆库等页面均只保留图标栏，符合「身份配置还有会话栏」的反馈 |
| 上传上限 | 主线 20 MB + 对应文案回填 i18n | 主线已放宽上限，知识库分支仍为 6 MB |
| i18n（10 个语种） | 取并集：知识库分支新键 + 主线上限文案 | 两侧文案不冲突，仅大小数字不同 |

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `git merge --no-ff codex/knowledge-experience` | 20 个冲突全部解决并登记 |
| `npx tsc -p tsconfig.build.json --noEmit` | 0 错误 |
| `npx vitest run`（全量 172 个测试文件） | 170 passed / 2 skipped，2133 tests passed，0 failed |

## 未验证项

- 浏览器实机视觉复核未在本轮执行（合并的是已验收的知识库分支形态）。
- `node_modules/.vite/vitest/results.json` 写入 EPERM 为 Windows 文件占用，不影响用例结果。

## Next

- 任务完成 → 推送 `feature/desktop-app` 与 `codex/knowledge-experience`
