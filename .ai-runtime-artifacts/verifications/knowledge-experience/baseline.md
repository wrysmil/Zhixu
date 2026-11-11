---
artifact: verification-baseline
route: orchestration:dispatcher-workflow
skills: [verification-before-completion]
skills_evidence: [.claude/skills/verification-before-completion/SKILL.md]
source: [webui/src/tests]
created_at: 2026-11-11
---

# 知识工作区改版前基线

隔离工作区基于 8d475728。主工作区有其他后端和图谱未提交工作，本次未带入或修改。

执行环境无Bun，使用已安装 Node 20.20.2 直接执行 Vitest 2.1.9。通过artifact局部Vite配置使用独立缓存；初始模块路径配置失败后修正，最终测试命令退出0。

cwd: `D:/studyspace/project/.harness-worktrees/Zhixu/wt-2026-11-11-knowledge-experience/webui`

```text
node node_modules/vitest/vitest.mjs run --config ../.ai-runtime-artifacts/verifications/knowledge-experience/vite.config.ts src/tests/knowledge-page.test.tsx src/tests/knowledge-eval-page.test.tsx src/tests/knowledge-eval-api.test.ts src/tests/knowledge-eval-template.test.ts src/tests/knowledge-stats-api.test.ts src/tests/knowledge-graph.test.tsx src/tests/knowledge-graph-view.test.tsx
Test Files 7 passed (7)
Tests 53 passed (53)
Duration 9.22s
```

基线只证明这7组测试在改版前通过；未执行全量测试/构建/浏览器动态验收。
