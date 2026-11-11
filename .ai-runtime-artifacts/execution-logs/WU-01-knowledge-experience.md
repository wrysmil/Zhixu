---
artifact: execution-log
route: orchestration
wu_id: WU-01
wu_type: ui
agent_role: coder
created_at: 2026-11-11
status: done
scope: 共享体验基础，不代表两页项目验收完成
---

# WU-01 共享体验基础

## 结果与边界

worktree：`D:/studyspace/project/.harness-worktrees/Zhixu/wt-2026-11-11-knowledge-experience`；分支已核对为 `codex/knowledge-experience`。只在此 worktree 写入。用户明确授权组件测试与无提交的 red/green；未 commit/push，未派子 agent。角色文件中的独立 reviewer 委派和 test commit 要求按用户本轮约束跳过，独立审查由 Leader 收口。

已读取 coder 角色、routing、项目 profile/context-map、批准计划 Task 1/动效矩阵、接口契约、既有栈记录、tooltip/sheet/floating-surface/utils 与现有 Radix 测试。没有改页面、locales、API、共享 ui 或测试 config。未覆盖初始存在的文档、依赖链接与 Leader 验证文件。

## 冻结导出

- `experience-types.ts` 原样导出 `KnowledgePreviewProps`、`KnowledgeStatusProps`、`KnowledgeTone`，属性未增删。
- `KnowledgePreview.tsx` 命名导出 `KnowledgePreview`，并重新导出其 props 类型。
- `KnowledgeStatus.tsx` 命名导出 `KnowledgeStatus`，并重新导出其 props 与 tone 类型。
- 两个组件均导入 `experience.css`；消费页亦可直接导入此 CSS。
- Preview 使用现有 Tooltip，300ms 悬停延迟、焦点打开、Esc/激活/失焦关闭、允许移入摘要。复用 Radix 原有 pointer corridor，不自建全局事件或定时器。
- Preview 的单一元素 children 应为可聚焦且能转发 ref/事件的元素；推荐 button/link。文字、Fragment 或多个节点会用可聚焦 span 包装。title/summary/meta 仅提供补充说明，meta 不放交互控件；完整内容由调用方在详情中继续提供。
- Status 仅消费调用方 label/tone/busy，使用 status/polite/atomic、可访问名称与 aria-busy；仅 busy 时渲染隐藏于辅助技术的 spinner。所有状态始终渲染文字，不以颜色或动画代替文字。

## 冻结类名与接入

| 类 | 作用 |
| --- | --- |
| `knowledge-experience` | 两页根作用域、motion token |
| `knowledge-experience-surface` | Portal 浮层作用域、motion token |
| `knowledge-page-header` | 标题与主动作布局、h1 排版 |
| `knowledge-toolbar` | 可换行的筛选/动作工具栏 |
| `knowledge-library-card` | 卡片边框、hover/focus 反馈，无整行缩放 |
| `knowledge-panel` | 通用内容面板 |
| `knowledge-table-scroll` | 表格局部横向滚动 |
| `knowledge-table` | 语义表格间距、分隔线和行反馈 |
| `knowledge-motion-enter` | 160ms 内容淡入与 4px 位移，仅挂载时触发 |
| `knowledge-sheet` | 右侧面板 220ms 进入 / 160ms 退出，窄屏全宽 |
| `knowledge-dialog` | Dialog/Popover 内容 160ms 进入 / 100ms 退出 |
| `knowledge-overlay` | 显式可定制的遮罩 140ms 淡入/淡出 |
| `knowledge-preview` | Tooltip 宽度/换行，140ms 进入 / 100ms 退出 |
| `knowledge-preview-title/summary/meta` | 摘要内部排版 |
| `knowledge-preview-trigger` | 非单元素 children 的可聚焦后备容器 |
| `knowledge-status` | 静态状态文字和 tone |
| `knowledge-status-spinner` | 仅 busy 时 1000ms 线性循环 |
| `knowledge-motion` | 所有动效元素的 reduce-motion 标记 |

Sheet 调用固定为 `className="knowledge-experience-surface knowledge-sheet knowledge-motion"`。默认右侧；左侧需同时传 `side="left" data-side="left"`。不覆盖 top/bottom 方向。内容进入类用 `className="knowledge-motion-enter knowledge-motion"`，不要因轮询改变 key 重挂载。卡片/表格行等含过渡的节点同样加 `knowledge-motion`。

120/160/220/160ms token 唯一定义于 CSS 的两个知识库作用域。减少动画仅影响标记元素，关闭 animation/transition，状态文字保留。没有引入依赖或改变全局 reduce-motion。

现有 SheetContent 不暴露其内置 Overlay 的 className。本 WU 按边界保留其默认行为；`knowledge-overlay` 供能显式传遮罩 class 的组合使用。内置遮罩的局部 140ms/reduce 集成由 WU-05/06 核验，不通过全局选择器或修改共享 ui 解决。

## 变更文件

1. `webui/src/components/workspace/knowledge-shared/experience-types.ts`
2. `webui/src/components/workspace/knowledge-shared/experience.css`
3. `webui/src/components/workspace/knowledge-shared/KnowledgePreview.tsx`
4. `webui/src/components/workspace/knowledge-shared/KnowledgeStatus.tsx`
5. `webui/src/tests/knowledge-experience.test.tsx`
6. 本执行日志。

## 测试先行与命令输出

测试先写，第一次执行时组件文件尚不存在：exit 1，`Failed to resolve import .../KnowledgePreview`，0 个用例执行。此结果只证明文件缺失，不作为行为 red 证据。

随后加入仅返回 children/label 的最小占位实现，执行同一命令取得行为 red，再实现正式组件。没有把占位实现作为交付，也未修改原行为断言来迁就正式实现。

cwd 为 worktree 的 `webui`：

```text
node node_modules/vitest/vitest.mjs run --config ../.ai-runtime-artifacts/verifications/knowledge-experience/vite.config.ts src/tests/knowledge-experience.test.tsx

RED，17:02:34，exit 1：
❯ src/tests/knowledge-experience.test.tsx (10 tests | 10 failed) 20907ms
Unable to find role="tooltip"（4 项 Preview 用例）
Unable to find an accessible element with the role "status"（6 项 Status 用例）
Test Files  1 failed (1)
Tests       10 failed (10)
Duration    22.91s

GREEN，17:04:18，exit 0：
✓ src/tests/knowledge-experience.test.tsx (10 tests) 3306ms
Test Files  1 passed (1)
Tests       10 passed (10)
Duration    7.68s
```

覆盖焦点打开/摘要可访问描述、Esc 后焦点保留、悬停及移入摘要、原有点击动作保留、文字 children 和缺省 summary/meta、忙碌→就绪、四种 tone 静态文字及错误消息 HTML 转义。没有 mock Tooltip、颜色字符串断言或 CSS 快照。hover 单测不能替代浏览器的几何 corridor/连续动效验证。

```text
node node_modules/typescript/bin/tsc --noEmit -p tsconfig.build.json
cwd: webui
exit 0，无诊断输出

node webui/node_modules/eslint/bin/eslint.js --config webui/eslint.config.js webui/src/components/workspace/knowledge-shared/experience-types.ts webui/src/components/workspace/knowledge-shared/KnowledgePreview.tsx webui/src/components/workspace/knowledge-shared/KnowledgeStatus.tsx webui/src/tests/knowledge-experience.test.tsx --max-warnings 0
cwd: worktree
exit 0，无诊断输出

git diff --check
exit 0，无输出（新增文件仍未跟踪，此命令不能代替新增文件审查）
```

首次 lint 误用根 node_modules 路径，得到 MODULE_NOT_FOUND；已改为上面 webui 依赖路径并成功执行。Vite 提示 CJS API deprecated、Browserslist 数据较旧，未改依赖和配置消除历史提示。全量测试/build 与两页浏览器验收按计划由 Leader/WU-06 负责，不把本次定向通过称为全量通过。

## 局部浏览器预览尝试与遗留

尝试用本次源码建立局部 Preview/Status/Sheet fixture。使用现有配置启动 :5187 时，esbuild 预构建缓存文件写入报 `Access is denied`；再次以程序参数指定独立 `WU-01-cache` 仍有相同错误。没有修改现有 config。已停止两个本次启动的服务，移除未成功验收的临时 fixture 源文件；未清理可能仍由其他任务使用的缓存。

因此未声称截图、录屏或浏览器动效通过。WU-05/06 非阻断待验：真实指针移入摘要持续可读、CSS 退出等待 animationend、Sheet 焦点恢复、原生遮罩的局部 motion、窄屏/深色/reduce、轮询不重播。已向 Leader 通知环境限制，契约和类名冻结可供下组继续。无业务/类型阻断。

## 自查

- self_check: PASS（限定 WU-01 共享基础交付；浏览器限制如上）
- wu_status: done
- 性能：无请求/轮询、无自建 timer/listener、无布局尺寸动画；循环 spinner 仅真实 busy。
- 安全：无 HTML 注入、路径/凭据/网络读取；字符串经 React 转义。
- 日志：纯展示层无请求与业务异常入口，不额外输出用户摘要或状态日志。
- 无障碍：使用 Radix 焦点与 Esc 语义，状态有文字及名称、装饰图标隐藏；完整信息/详情入口责任保留在调用方。
- 轻量审查：逐文件自查完成；独立审查未运行，按用户禁止派 agent 的约束交 Leader 收口。
- open_items: 上述浏览器动态验收与默认 Overlay 局部 motion（WU-05/06）。

## Skills 使用

- 已加载：`.claude/skills/source-driven-development/SKILL.md`（先核对计划/契约与既有代码，再核对官方依据）。
- 已加载：`C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/frontend-ui-engineering/SKILL.md`（作用域、布局、语义、焦点与静态反馈）。
- 已加载：`C:/Users/Huangqh/.codex/skills/test-driven-development/SKILL.md`（先写测试，实际行为 red 后实现 green）。
- 已加载：`.claude/skills/verification-before-completion/SKILL.md`（记录实际命令、退出码、失败及限制；按用户指示不创建 test commit）。
- 已加载：`.claude/skills/performance-optimization/SKILL.md`（ui 角色要求，检查计时器/重复渲染/动画边界，不宣称未测量的性能提升）。
- 预览时加载：`agent-browser`、`browser-testing-with-devtools` 指南；DevTools MCP 未提供，预览服务缓存失败，未运行浏览器验收。
- 已跳过：设计/重规划/独立 reviewer 派发，全量两页实现与集体验证均不在 WU-01 授权范围。

官方依据：[Radix Tooltip](https://www.radix-ui.com/primitives/docs/components/tooltip)、[Radix CSS Animation](https://www.radix-ui.com/primitives/docs/guides/animation)、[MDN prefers-reduced-motion](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/prefers-reduced-motion)。已同时核对安装版本的 Tooltip 类型；官方网页版本可能更新，使用的 Provider/Root/asChild API 已在本地组件测试中执行。
