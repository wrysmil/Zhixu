---
artifact: verification
route: bugfix/ui
batch: workspace-panel-fixes
work_unit: WU-02
created_at: 2026-10-15
skills:
  - systematic-debugging
  - source-driven-development
  - test-driven-development
  - frontend-ui-engineering
  - performance-optimization
  - verification-before-completion
source:
  - 用户 2026-10-15 截图与修复要求
  - .ai-runtime-artifacts/specs/2026-10-11-home-dock-pty-terminal-v1.1-spec.md
worktree: D:/studyspace/project/Zhixu/.harness-worktrees/workspace-panel-fixes
wu_status: done
---

# WU-02：工作区、侧栏工具和文件预览布局修复

## 根因

- 共享输入框宿主同时使用 `w-full` 和右侧 `marginRight`，整体宽度超过父列；ThreadShell 最外层 flex 项缺少 `min-w-0`，内容最小宽度进一步阻碍收缩。
- SessionWorkbench 把摘要固定在最大 45% 高度，再把 StartDock 挂在下面，工作区信息和工具上下争抢同一列。
- 会话文件树把预览回调上抛宿主，绕过 DockFileTab 自带预览；原内联预览又采用上下排列。
- 首页默认收起工具后，原快捷键仅修改标签 store，没有明确展开意图，导致标签已打开但界面仍收起。

## 变更

- `SessionWorkbench.tsx`：默认显示完整工作区摘要，点击“展开侧栏工具”切换全高工具模式；第一次展开后保留同一工具实例，返回工作区不会关闭终端。工具宽度 36rem、摘要 19rem，并受可用宽度约束；上报实际占位宽度，覆盖模式上报 0。
- `ThreadShell.tsx`：补齐 flex 收缩约束；观察实际 pane 宽度，小于 720px 时侧栏覆盖显示；桌面首次访问默认摘要打开，窄窗口首次访问默认关闭，保存过的选择仍生效。按当前会话过滤工具展开事件。
- `PaneWorkbench.tsx`、`App.tsx`：通过受实际侧栏宽度约束的可用区域容纳共享输入框，移除造成越界的外边距；关闭和覆盖模式归零。
- `DockFileTab.tsx`：文件预览在左、文件树在右，内容分别滚动；长路径和代码保留在各自边界内。会话工具直接使用内部预览。
- `StartDock.tsx` 公共布局：新增 `defaultCollapsed`，首页默认收起；按 scope 响应显式展开事件，重复打开已存在标签仍可展开。文件组件按 scope 重置。本文件终端逻辑改动属于 WU-01。
- `dock-shortcuts.ts`：定义 `DOCK_OPEN_EVENT` 和 scope 信息，快捷键打开标签时同时表达展开意图。
- en / zh-CN / zh-TW：新增工作区/工具切换文案；最终 locale 合并由 Leader 统一处理。

## 测试资产和红绿证据

- `session-workbench.test.tsx`：默认摘要、显式工具模式、保留同一终端实例且不 kill、宽度回传、覆盖模式不预留输入框宽度、会话隔离、左预览右文件树。
- `pane-workbench.test.tsx`：输入框可用区域扣除侧栏宽度，宿主不再叠加右外边距。
- `start-dock.test.tsx`：工具初始收起、显式展开；终端列表隔离用例由 WU-01 添加。
- `app-layout.test.tsx`：首页快捷键打开并展开工具、再次收起后重复快捷键仍展开、会话 scope 隔离、实测宽度 304→576→304→0 对应输入框可用区域。
- `thread-shell.test.tsx`：桌面/窄窗和保存偏好的四种组合、pane 变窄时切换覆盖模式；旧聊天生命周期用例显式收起摘要，避免聊天文本和摘要镜像文本产生重复匹配。
- 首轮两文件目标测试在实现前为 7 failed / 11 passed，失败点为缺少工具展开入口与输入框可用区域；首页默认收起用例实现前失败；快捷键显式展开用例实现前失败；窄窗首次默认关闭用例实现前失败。对应修复后目标回归通过。
- 未创建 test/code commit：Leader 明确要求不 commit/push，红绿顺序以本次工具执行记录为证。

## 验证结果

| 命令 / 范围 | 结果 |
| --- | --- |
| SessionWorkbench + PaneWorkbench + StartDock，单 worker | 38 / 38 通过（8 + 12 + 18） |
| App + ThreadShell 布局、默认态、快捷键专项，单 worker | 9 / 9 通过 |
| `npm test -- src/tests/thread-shell.test.tsx --maxWorkers=1 --minWorkers=1 --silent`，最后一次 16:58 | 73 / 73 通过，exit 0 |
| `npx tsc -p tsconfig.build.json` | exit 0 |
| 定向 ESLint：App、上述组件、dock-shortcuts 与五个改动测试文件 | exit 0 |
| `git diff --check` | exit 0 |

ThreadShell 完整文件之前暴露了历史恢复测试的等待竞态：mock 在请求入口就递增 `historyCalls`，测试随即发 hidden→visible，此时失败响应还未提交、`loading` 仍为 true。Leader 授权后只修改该测试，等待 `HERO_GREETING_PATTERN` 可见，即首轮失败已结束加载，再模拟返回页面；不改变生产重试语义。最后完整文件 73 条全部通过。

四文件整套中的一次旧 skill 删除用例失败，专项重跑失败阶段不同；Leader 冻结全量 1531/1533 的剩余失败仅为 locale 形状和上述 hydration 竞态，skill 删除用例在该冻结全量通过。完整批次最终结果以 Leader 集体测试报告为准，不将局部结果表述为全量通过。

## 轻量审查与开发者自检

- 独立 reviewer：`/root/security_review` 只读复查布局，无可执行阻断项。已确认模式互斥、保留工具实例、宽度清理归零、事件 scope 校验、预览左/树右和收缩约束贯通。
- 性能：工具首次使用才挂载；观察器和事件监听均清理，无额外循环请求。返回摘要复用工具实例。
- 安全：文件预览仍走既有 API；工具展开事件只影响当前 scope。终端协议与终端列表归属由 WU-01 负责。
- 无障碍：保留 button 语义，工具切换具备可访问名称及 aria-expanded，隐藏模式不可见。
- `code_review: PASS`；`self_check: PASS`；未执行 commit/push。

## Skills 使用

已加载 Leader 指定的 systematic-debugging、source-driven-development、test-driven-development，并按 coder 的 ui 规则加载 frontend-ui-engineering、performance-optimization。完成前读取仓库 `.claude/skills/verification-before-completion/SKILL.md` 与 `harness-kit/project.verification.md`。未做无证据的性能优化，未新增方案或计划门禁。

官方依据：[MDN min-width](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/min-width) 对 flex 最小内容宽度的说明；[React 状态保留](https://react.dev/learn/preserving-and-resetting-state) 对保留相同位置组件状态的说明。

## 未验证项

真实浏览器的最终像素边界、720px 临界点和分栏视觉未实测：Leader 的浏览器 origin 权限被拒绝，未绕过限制。自动测试覆盖模式、实际测量值传递和响应式分支，但不替代屏幕验收。批次全量构建与最终测试由 Leader 冻结变更后统一执行。
