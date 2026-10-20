---
artifact: verification-lite
route: leader-direct
skills:
  - source-driven-development
  - verification-before-completion
  - frontend-design
  - frontend-ui-engineering
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
  - C:/Users/Huangqh/.codex/skills/frontend-design/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/frontend-ui-engineering/SKILL.md
source:
  - 用户要求将重要菜单固定显示，低频入口收进 More
created_at: 2026-10-20
tier: 1
verdict: PASS_WITH_KNOWN_FAILURE
---

# 工作区导航优先级调整 — 轻量验证（Tier 1）

## 范围

- `WorkspaceNavigation.tsx`：助手、创作、智能体、记忆固定显示；应用、技能、自动任务、团队、身份设置收进 More。
- `workspace-navigation.test.tsx`：覆盖固定入口、More 分组、当前态、折叠态与路由回调。
- `app-layout.test.tsx`：记忆入口改为从固定导航打开。
- 未修改任何业务 API、数据模型和路由协议。

## TDD 证据

- 红灯：修改导航测试后，`workspace-navigation.test.tsx` 20 项中 4 项失败，明确缺少 `Agents` / `Memory` 固定入口。
- 绿灯：实现菜单重排后，同一文件 20/20 通过。
- 本批次没有提交，无法提供 test-first commit hash；仅保留本轮命令级红绿证据。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `npm test -- --run src/tests/workspace-navigation.test.tsx --cache=false` | PASS，20/20 |
| `npm test -- --run src/tests/workspace-navigation.test.tsx src/tests/app-layout.test.tsx --cache=false` | 88/89；导航测试全部通过，既有 `opens Automations from the main sidebar` 仍失败 |
| `npm test -- --run src/tests/app-layout.test.tsx -t "opens Automations from the main sidebar" --cache=false` | FAIL，单独复现，未进入 Automations 页面；与本次固定/More 分组无关 |
| `npm run build` | PASS，Vite 构建成功；保留既有大 chunk 警告 |
| `npm run lint` | PASS |
| `git diff --check` | PASS；仅显示其他既有文件的 CRLF 提示 |

## 浏览器验收

- 入口：`http://127.0.0.1:4173/?preview=1#/home`。
- 固定栏可见：Assistant、Creative、Agents、Memory。
- More 展开可见：Apps、Skills、Automations、Teams、Identity，并按 Workspace / AI configuration / Personal 分组。
- 点击 Memory 后 hash 为 `#/memory`，Memory 的 `aria-current=page`，More 不高亮。
- 从 More 点击 Teams 后 hash 为 `#/teams`，More 的 `aria-current=page`。
- 页面 `scrollWidth === clientWidth`（1014），无横向溢出；浏览器 console 0 error / 0 warn。

## 已知限制

- 离线 PreviewApp 的 Memory / Teams 页面仍显示通用“设置（示例）”占位内容，这是上一轮已记录的预览接线缺口，不影响本次导航层级和路由当前态验收。
- 全量 App layout 仍有 Automations 导航测试失败，因此本批次不声明全量测试通过。

## Next

- 当前导航改动可继续保留。
- 后续单独修复 PreviewApp 页面接线和 Automations 布局测试，不在本次菜单重排中扩大范围。
