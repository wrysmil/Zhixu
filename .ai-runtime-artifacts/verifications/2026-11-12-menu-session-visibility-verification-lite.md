---
artifact: verification-lite
route: leader-direct
skills:
  - source-driven-development
  - verification-before-completion
skills_evidence:
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/source-driven-development/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - 用户要求正式应用点击左侧菜单后不要默认显示会话栏
  - .ai-runtime-artifacts/specs/2026-10-28-navigation-transition-spec.md
  - harness-kit/project.profile.md
  - harness-kit/context-map.md
  - webui/src/App.tsx
created_at: 2026-11-12
tier: 1
---

# 左侧菜单会话栏显示 — 轻量验证

## 范围与上下文

- 正式应用桌面侧栏仅在 `view === "chat"` 且用户展开偏好为真时显示会话列。
- 记忆、画像及其他功能页保持 54px 图标导航；回到助手恢复原会话和收起偏好。
- 会话列保持挂载，隐藏时沿用现有 `aria-hidden`、`inert` 和焦点处理。
- 不改变移动端抽屉、路由、会话数据与全局导航组件的受控接口。
- 修改 `webui/src/App.tsx` 和既有 `app-layout.test.tsx`；其他会话同时修改的智能体页面未涉及。
- 本次为现有布尔显示条件修复，未引入框架 API 或依赖。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `npm run test -- src/tests/app-layout.test.tsx -t "hides sessions on" --no-cache`（修复前） | Memory / Identity 两项失败：期望 54px，实际 264px，复现问题 |
| `npm run test -- src/tests/app-layout.test.tsx src/tests/workspace-navigation.test.tsx --no-cache`（最终） | 2 文件、91 项通过 |
| 指定 App.tsx 和 app-layout.test.tsx 的 ESLint，`--max-warnings 0` | 通过 |
| `git diff --check -- webui/src/App.tsx webui/src/tests/app-layout.test.tsx` | 通过 |
| `npm run build` | 首次遇到 Windows 临时文件访问拒绝；将 TEMP/TMP 指向 `.ai-runtime-artifacts/verifications` 后重试通过，3071 模块 |
| `npm run test -- --no-cache --maxWorkers 2 --minWorkers 1 --reporter dot` | 172 文件：168 通过、2 跳过、2 失败；2135 项：2127 通过、2 跳过、6 失败。此轮在既有断言修正前启动：3 项 App 布局旧断言已在最终 91 项回归中通过；另 3 项为 appearance-boot.test.ts 调色板恢复断言失败，未涉及本次显示条件 |

既有溢出会话行测试改为验证隐藏元素仍挂载，返回助手后仍为同一节点；画像路由测试明确查询 Identity files 标题，避免与保持挂载的助手标题冲突。

## 未验证项

- 未进行真实浏览器交互验收，不声称视觉动效验收通过。
- 全量套件未全绿，保留上述外观主题失败；未扩大本次范围处理主题逻辑。没有在修改测试断言后重跑全量套件，已重跑完整受影响测试文件。

## Next

- 用户随后以 `commit` 授权本地提交本次源码、测试与验证记录；不推送。
