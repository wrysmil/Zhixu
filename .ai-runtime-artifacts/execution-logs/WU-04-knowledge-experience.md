---
artifact: execution-log
route: orchestration.dispatch
wu_id: WU-04
wu_type: ui
agent_role: coder
created_at: 2026-11-11
status: done
skills: [source-driven-development, frontend-ui-engineering, test-driven-development, verification-before-completion, performance-optimization]
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/frontend-ui-engineering/SKILL.md
  - C:/Users/Huangqh/.codex/skills/test-driven-development/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/performance-optimization/SKILL.md
---

# WU-04 图谱交互与证据入口

## 执行边界

worktree：`D:/studyspace/project/.harness-worktrees/Zhixu/wt-2026-11-11-knowledge-experience`；当前分支已核对为 `codex/knowledge-experience`。

只修改以下四个业务/测试文件及本日志，没有修改父页、共享 CSS、locales、API、依赖与测试配置；保留其他 worker 的已有修改。没有 commit/push，没有派发 agent。按用户明确要求执行组件测试；角色中的不做组件测试、独立 reviewer 和 skill 中 test commit 证明要求，由本轮更具体的“指定测试、不 commit、不派 agent”覆盖。无提交的测试先行证据见下文；独立审查与整页浏览器验收交 Leader 收口。

实现依据：已读 coder 角色、routing、project.profile/context-map、project.verification、批准计划 Task4 与动效矩阵、知识体验接口合同、既有栈记录以及四个目标文件。沿用 React 18.3.1、d3-force 3.0.1、现有 UI 组件，没有引入新框架 API。

## 变更

- `webui/src/components/workspace/knowledge/GraphPanel.tsx`：GraphPanelProps 与 onOpenChunk(docId, chunkId) 原样保留。列表打开实体仍加载实体详情和初始子图；图内选择只读取详情，显式“展开关联”和跳数切换读取子图。实体与子图请求分别计序号，快速切换、关闭、库/transport 切换或卸载后旧结果与旧错误不能覆盖当前状态；统计请求也有旧响应守卫。详情和子图的错误分别显示并原位重试，互不清空已成功区域，失败不刷新列表/统计。
- 详情加载时保留现有图谱实例，因此图节点焦点与缩放不丢失。关闭时优先回到当前实体卡片，否则回最初打开的卡片，原卡片已过滤掉则回搜索框。原证据按钮继续通过父页已有回调打开来源；无回调时明确禁用。详情名称/正文、证据标题可换行。
- `webui/src/components/workspace/knowledge/GraphView.tsx`：SVG 改用可容纳交互子项的 group 语义；每个节点可 Tab、Enter、Space 选择，有 aria-pressed、可见焦点环、静态选中大小与描边。关联节点/边使用中性主题色、透明度与线宽突出，底部显示完整选中名称/类型及显式展开按钮。移除仅鼠标可达的双击展开入口，统一走显式按钮。
- 仍为 50 节点/100 边，原 d3 力参数、180 次同步 tick、stop() 静态布局与滚轮缩放逻辑不变；没有原生平移实现，未新造平移。仅对已经截断的图谱内容生成布局签名，相同刷新内容不重新布局/重置 viewBox。无持续漂浮、无轮询/新增 endpoint。
- 标题 14/16、辅助信息 12、实体/证据正文 14；使用现有背景/边框/前景 token。选中关联以 120ms opacity/描边过渡，motion-reduce 关闭过渡与原有加载旋转，仍保留文字、静态选中与可用按钮。没有改共享动画 token 或全局规则。
- `webui/src/tests/knowledge-graph.test.tsx`：原 4 用例保留，增加 8 用例覆盖节点选择与展开请求边界、证据回调、旧实体/旧子图响应、库切换、局部重试、关闭旧响应与两种焦点恢复。
- `webui/src/tests/knowledge-graph-view.test.tsx`：原 2 用例保留，增加 2 用例覆盖键盘选择/显式展开/关联标记与相同刷新内容的缩放和节点身份保持。

## 测试先行与验证证据

1. 先写 6 个新行为用例，再运行用户指定的完整双文件命令：**6 failed / 6 passed**，失败分别为旧实体覆盖、缺失详情重试、缺失图谱重试、库切换旧响应、节点没有 button 语义、相同刷新测试缺少交互图谱 group 语义。之后实现生产代码。
2. 修正测试中旧翻译的实际可访问名称为 Neighborhood（原测试误写 Subgraph），实现后双文件 **12/12 通过**。
3. 增加既有行为的守卫覆盖（图内选择不重新请求子图、旧子图响应、关闭与焦点），双文件 **15/15 通过**。
4. 自查补“选择图内独有节点后关闭”的焦点用例，先运行单例：**1 failed / 11 skipped**，实际焦点落到 body。增加原触发器回退后双文件及现有 i18n parity：**17/17 通过**。
5. 最终重跑用户原命令（cwd `webui`）：

```text
node node_modules/vitest/vitest.mjs run --config ../.ai-runtime-artifacts/verifications/knowledge-experience/vite.config.ts src/tests/knowledge-graph.test.tsx src/tests/knowledge-graph-view.test.tsx
2 files passed; 16 tests passed; exit 0; 2026-11-11 17:43:34 开始，8.34s。
```

附加验证：相同命令追加 `src/tests/knowledge-graph-i18n-parity.test.ts`：3 files / 17 tests passed，exit 0。四文件 ESLint `--max-warnings 0`：exit 0。限定四文件的 `git diff --check`：exit 0。Vite 有现存 CJS Node API 弃用提示，无测试失败；未删除/降低原断言。

全项目生产类型检查：`node node_modules/typescript/bin/tsc -p tsconfig.build.json --noEmit`，首轮与最终复核均 exit 0。未运行整套 WebUI 回归/生产打包，未声称真实后端、浏览器视觉/320–1440px、reduce-mode 实际媒体设置或父页跳转整链路已经验收；这些由 Leader 的集体验证完成。

## 自查

- self_check: PASS（本 WU 代码与指定组件测试范围）；wu_status: done；done_criteria_met: 是。
- 性能：布局输入有界，未提高节点/边上限；仿真仍 stop，无长期动画、轮询、事件监听器或新计时器。选择不重算布局，不因 UI 状态额外获取统计/实体列表；未做无证据的性能提升声明。
- 日志/安全：未新增生产遥测或记录用户资料；React 文本输出，未引入 HTML 注入、跨库读取或新权限路径。DOM 焦点查询限定于当前面板，实体 ID 经 CSS.escape。
- 异步：详情、图谱各自局部 busy/error/retry，旧响应检查也覆盖 catch/finally；关闭后不重新弹出详情。
- 无障碍：节点与展开按钮键盘可用；选中通过 aria-pressed、尺寸/线宽、文字共同表达；状态 status、错误 alert；焦点返回含图内独有实体回退。
- 保留风险/后续：真实浏览器焦点环、reduce-motion、窄屏及父页文档返回由 Leader/WU-06 验收；WU-05 接管下方翻译键。当前浏览器验收未执行，无阻塞。

## WU-05 文案键表

所有新文案均采用 `t('knowledge.experience.graphX', { defaultValue: '中文' })`，暂未改任何语言文件。

| 键（前缀 knowledge.experience.） | 中文 defaultValue |
| --- | --- |
| graphSelected | 已选择 |
| graphSelectHint | 选择节点查看详情，展开按钮加载关联 |
| graphExpand | 展开关联 |
| graphDetailLoading | 正在读取实体详情… |
| graphRetryDetail | 重试实体详情 |
| graphLoading | 正在加载关联图谱… |
| graphRetryGraph | 重试关联图谱 |
| graphNoRelations | 暂无关联节点，仍可查看下方文档证据 |

## Skills 使用与资料

五项必读/角色要求 skills 均已加载：source-driven-development（先合同/计划、再官方资料）、frontend-ui-engineering（语义/键盘/状态/视觉）、test-driven-development（两轮先红后绿）、verification-before-completion（新鲜命令证据）、performance-optimization（有界输入与请求/布局自查，没有无测量优化宣称）。未加载会派 agent 的流程，遵守本轮禁止派发要求。

参考官方资料：[React useEffect 请求清理与竞态防护](https://react.dev/reference/react/useEffect#fetching-data-with-effects)、[WAI-ARIA Button 的 Enter/Space、aria-pressed 与焦点约定](https://www.w3.org/WAI/ARIA/apg/patterns/button/)。只采用 React 18 已有的 effect/ref/state API。
