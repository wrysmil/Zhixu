---
artifact: dispatch
route: orchestration.dispatch
skills: [orchestration, test-driven-development, frontend-design, imagegen]
source: [2026-10-17-creative-home-refresh-plan.md]
created_at: 2026-10-17
status: complete
approved: true
plan: 2026-10-17-creative-home-refresh-plan.md
---
# 创作空间首页调度
GROUP-1：
- WU-02：测试与本地化；文件 webui/src/i18n/locales/*/common.json、webui/src/tests/article-list-home.test.tsx、仅首页定位器相关既有测试；先测试 RED 后通知 WU-01。
- WU-01：首页实现；文件 webui/src/components/creative/ArticleList.tsx；依赖 WU-02 RED 信号。
- WU-03：插画；文件 webui/src/assets/creative-writing-desk.png；独立生成。
GROUP-2：主 Agent 集成验证；独立 reviewer 和 security reviewer。
所有代码 cwd=/Users/mima0000/.codex/worktrees/creative-home-refresh/zhixu。过程产物在原工作区 .ai-runtime-artifacts/。
