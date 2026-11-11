---
artifact: implementation-plan
route: superpowers:writing-plans
skills: [writing-plans, api-and-interface-design, source-driven-development, frontend-design-optimizer]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/api-and-interface-design/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - C:/Users/Huangqh/.codex/skills/@user_8ec29dbe/frontend-design-optimizer/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-11-11-knowledge-experience-dispatch.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - harness-kit/project.profile.md
  - harness-kit/context-map.md
  - harness-kit/project.verification.md
  - .ai-runtime-artifacts/research/2026-11-11-knowledge-ui-research.md
  - .ai-runtime-artifacts/research/2026-11-11-knowledge-ui-screenshot-followup.md
  - .ai-runtime-artifacts/stack/2026-11-11-knowledge-ui-stack.md
  - .ai-runtime-artifacts/contracts/2026-11-11-contract-knowledge-experience.md
  - .ai-runtime-artifacts/specs/2026-11-10-knowledge-rag-eval-spec.md
  - 用户要求：不仅整体 UI，还要交互体验、动画；再检查后写计划
created_at: 2026-11-11
status: in-progress
approved: true
approval_evidence: 用户在本会话明确说「开始实现」
tier: 2
---

# 知识库与评测：视觉、交互和动效实施计划

> 执行者：确认本计划后按 Harness 编排执行，每个 WU 独立交付与审查。当前仅计划阶段，不改业务代码、不创建实现 worktree、不派发实现 worker。

**Goal:** 把知识库和检索评测台改为统一、轻盈且反馈清楚的知识工作区，覆盖列表、详情、悬停、表单、异步状态、转场和返回行为。

**Architecture:** 页面容器继续拥有数据与 mutation，拆出按职责分工的展示组件。共享体验层限定在两页内，采用现有 Radix 基础组件和局部 CSS 动画，使用现有 API，不引入后端改造。

**Tech Stack:** 本机 React 18.3.1、TypeScript 5.9.3、Tailwind 3.4.19、Vite 5.4.21、Radix Dialog/Popover/Tooltip、lucide、d3-force；Vitest + Testing Library + 浏览器验证。版本依据已有 stack 文件，实施开始时复核依赖状态。

## 1. 设计依据与本轮取舍

截图确认可观察的是浅中性背景、轻分组、较柔和圆角、悬浮摘要、少量强调色；静态图片不能证明原产品的延迟、曲线、弹性或过渡时长。下面的动效参数是对知序的明确设计建议，不声称复刻截图产品动画。

前面布局稿是视觉探索，不是完整产品原型；本计划补齐交互。以中性灰白为基底，强调色用现有主题 token，截图橙色仅供参考，不强制改变全局品牌色。

范围：知识库首页/库详情/文档片段/检索/图谱，以及评测记录/题集/新建评测/报告/对比/指南。全局侧栏、聊天、系统设置不在本轮范围。没有新增完整 Wiki 编辑器、生成答案评测、跨库全文搜索、服务端名称搜索或未实现的进度接口。

## 2. 页面与交互蓝图

### 2.1 知识库

| 场景 | 用户动作 | 应有反馈与下一步 |
| --- | --- | --- |
| 首页 | 打开知识库 | 标题+主操作，摘要一行，名称搜索/作用域筛选，知识库卡片；初次骨架、失败可重试、空状态有创建入口 |
| 知识库卡片 | 悬停/聚焦 | 轻微底色/边框变化，更多菜单可见且键盘可达；长标题可补充摘要；卡片不抬升、不无限摆动 |
| 进入与返回 | 点击卡片/面包屑返回 | 内容轻淡入，保留首页关键词、筛选、排序和滚动；返回焦点到原卡片，已删除则回列表标题 |
| 文档管理 | 切换状态、翻页、点文件名 | 使用现有分页；后台加载保留旧列表并显示局部忙碌；点名称开片段 Sheet，不让一行所有位置都承担多个冲突动作 |
| 添加资料 | 点添加，选择本地文件或服务器路径 | 默认本地上传；路径作为次级方式；表单标签固定、字段错误就近呈现；提交期间仅阻止重复提交，不冻结整页 |
| 文档处理 | 文件提交成功 | 直接显示真实 pending/processing 状态，转 ready 后更新本行、轻高亮一次；失败提供原因、重试；无真实进度不显示百分比 |
| 片段预览 | 打开、切片段、关闭 | 桌面右侧 Sheet，窄屏全屏；可读长正文，保留文档行上下文；Esc 关闭并恢复焦点；加载只影响详情区 |
| 检索测试 | 输入查询、按 Enter | 立即显示请求中；结果包含来源与可点击片段；区分无结果、查询失败和降级；高级参数折叠，搜索名称与内容检索文案分开 |
| 停用/删除/修复 | 菜单操作 | 有清楚的对象名与后果；保持现有确认；以服务端成功为准再刷新；失败不丢失条目，不做虚假的撤销按钮 |

卡片内主入口用 button/link，更多菜单为同级交互，禁止按钮嵌套。表格操作对键盘/触屏可见，不只在鼠标 hover 时可用。

### 2.2 知识库评测

| 场景 | 用户动作 | 应有反馈与下一步 |
| --- | --- | --- |
| 默认入口 | 打开页面 | 有运行就展示评测记录；无运行但有题集时给开始入口；全空时引导创建/导入。用户主动切换的 Tab 不被轮询强行重置 |
| 题集 | 点测试题集 | 列表显示名称、题数、时间与标注摘要；点开才编辑题目，避免首页把所有标注输入框摊开 |
| 导入/编辑 | 选 CSV、修改标注 | 导入前显示解析错误；编辑保留草稿，提交错误就近呈现；409 保留输入并提示核对；离开未提交表单有舍弃确认 |
| 新建评测 | 点主按钮 | Sheet/对话框先选题集、配置，更多参数折叠；保留隐私确认；没有题集时明确提供创建入口 |
| 运行状态 | 发起、查看、取消 | 防重复提交；排队/运行状态不混淆；实际计数存在才展示进度；取消请求成功后等待服务端终态；失败有可读说明 |
| 运行报告 | 点击运行 | 用题集名+时间识别，ID 为辅助；五指标+评分来源组+逐题表同页出现；切换运行旧报告不得短暂冒充新报告 |
| 单题证据 | 点问题名/详情按钮 | Sheet 中显示查询、配置、标注来源、状态、rank/grade/reason/chunkId；接口没有片段正文时只显示现有证据和可复制 ID，不偷偷添加跨权限读请求 |
| 结果筛选 | 搜索、切未评分/失败 | 即时筛选已加载结果，保留当前排序；无匹配有清空入口；仅凭 scored 状态不标为“通过”，没有阈值就不发明通过率 |
| 两运行比较 | 选择基准与候选 | 先比较可比性；显示真实指标 delta 与退步题；同一运行、缺少一方、降级等给明确提示；点退步定位对应复合行，缺失值不判为改善 |
| 返回记录 | 关闭报告/单题 | 恢复列表筛选、分页、滚动与焦点；不重置题集草稿，不因为动画触发请求 |

题集版本显示只使用当前 revision；不把示意图的 v3 当作已有独立快照版本接口。成本、答案生成进度、回答正确率均不列入首期。

### 2.3 图谱

保持已有节点数/边数上限与布局算法。选择节点使关联项高亮，明确区分“选择”和“展开”；使用现有 onOpenChunk 链接到文档证据。切回文档不丢库选择；动画不重新触发建图/提取。若布局计算已经静态完成，不为追求“活感”额外加入持续漂浮。图谱请求失败在图谱区域重试。

## 3. 动效与反馈规格

目标是动作有回应、层级有来处、异步状态可信。使用 opacity/transform 处理视觉过渡；不对整页 height/width 或每次轮询的数字做连续动画。

| 对象 | 触发与动作 | 时长/节奏 | 降级与中断规则 |
| --- | --- | --- | --- |
| 按钮/菜单行 | hover 改底色，按下主按钮可 scale(.98) | hover 120ms，按下80ms | 禁用态不缩放；触屏不依赖 hover；reduce 时只保留颜色 |
| 标题/卡片/表格行 | hover/focus 有清楚反馈 | 120ms；无整行缩放 | 不挤动列、不改变行高 |
| 补充摘要 | 停留300ms或键盘focus后显示；离开宽限100ms | 入场 opacity + translateY(4px)，140ms | 指针可进入；Esc关闭；移动到其他项撤销前一计时；移动端点开详情 |
| Tab/列表→详情 | 新内容轻淡入，向上4px以内 | 160ms ease-out | 点击即更新选中状态；不等待出场才读数据；不制造中间空白页；快速切换以最后选择为准 |
| Sheet | 遮罩淡入、面板从侧面进入；退出稍快 | 入场220ms，退出160ms，遮罩140ms | 延后卸载到退出完成；焦点恢复不等待不必要的动画；窄屏全屏；reduce直接切换 |
| Dialog/Popover | 淡入+scale(.98→1)或位移4px | 160ms，退出100ms | 不用大幅弹跳；Esc/点击外部遵循组件语义 |
| 折叠高级参数 | 轻淡入；优先直接布局切换配内容淡入 | 160ms | 不将高度动画做成新依赖；提交校验失败自动展开错误所在区 |
| 首次加载 | 先给布局占位，超过150ms再显示骨架 | 无强制最短展示时间 | 快响应不闪骨架；后台刷新不清屏；reduce用静态占位 |
| 运行/上传处理 | 不确定进度小指示+阶段文字；完成后静态状态 | 指示1000ms linear；完成行底色400ms淡回 | 只在真实处理中循环；reduce用静态图标+文字；不假装上传到99% |
| Toast/局部反馈 | 成功提示出现，失败保留可重试 | 入场160ms；成功约3s | 关键错误不自动消失；状态通过aria-live适度通知，不每3秒朗读整个表 |
| 图谱 | 选中/关联边颜色、透明度过渡 | 120ms | 无持续漂浮，无每轮轮询重播布局 |

统一 easing：`cubic-bezier(.2,.8,.2,1)`。大列表不逐项错峰进入；指标数字直接展示真实值，避免滚动计数掩盖 null/0 差异。

`prefers-reduced-motion: reduce` 时停用非必要 scale、slide、脉冲与循环旋转，用文字和静态状态继续传递进度。作用域只覆盖两页，Portal 浮层另加局部类。动画结束事件不作为 mutation 成功、取消请求或数据刷新完成的依据。

## 4. 组件与文件设计

| 位置 | 职责 |
| --- | --- |
| `knowledge-shared/experience-types.ts` | 两页共享摘要、状态展示契约 |
| `knowledge-shared/experience.css` | 局部视觉 token、动效、reduce-motion 与 Portal 样式 |
| `knowledge-shared/KnowledgePreview.tsx` | 复用 Tooltip 的非交互摘要；完整信息在详情可访问 |
| `knowledge-shared/KnowledgeStatus.tsx` | 状态图标、文字和真实 busy 反馈 |
| `KnowledgePage.tsx` | 库、文档请求与选择/返回状态，保留 scope、轮询、确认逻辑 |
| `knowledge/KnowledgeLibraryList.tsx` | 卡片、名称筛选、作用域、排序与空态 |
| `knowledge/KnowledgeDocumentTable.tsx` | 现有分页、行状态与操作入口 |
| `knowledge/KnowledgeDocumentSheet.tsx` | 片段预览、焦点、读取错误 |
| `knowledge/KnowledgeImportSheet.tsx` | 文件/路径表单与阶段反馈 |
| `KnowledgeEvalPage.tsx` | 题集/运行/报告/对比状态与现有数据调用 |
| `knowledge-eval/KnowledgeEvalRunSheet.tsx` | 从 Runs 拆出的新建配置表单 |
| `knowledge-eval/KnowledgeEvalCaseSheet.tsx` | 现有报告中的单题证据 |
| `knowledge-eval/KnowledgeEvalDatasetSheet.tsx` | 题集编辑与草稿保留 |
| 现有 `knowledge-eval/KnowledgeEvalRuns/Datasets/Overview/CaseResults/Compare.tsx` | 分别改为记录、题集摘要、指标、结果表和比较视图 |

组件继续使用已安装的 Dialog/Sheet/Popover/Tooltip/DropdownMenu。第一版不新增 Motion/GSAP、整套 UI 库或图表库：目前所需转场 CSS 已能覆盖，结果表保留现有语义表格，控制筛选/排序/分页。后续若出现复杂列拖拽/虚拟化需求再单独评估 TanStack，不把“允许外部组件”当作必须加依赖。

不改共享基础组件默认样式；当前 Sheet 300ms 可以在局部调用覆盖到上述220/160ms，不全局牵连其他功能。

## 5. 分阶段任务

### Task 0：实现前基线（Leader）

- [ ] 检查当前分支、已有修改和未跟踪文件；记录知识库/图谱正在进行的修改，禁止覆盖。按调度文件在获准后建立隔离 worktree；不把其他未提交工作自动搬走。
- [ ] 重读两页和契约，确认现有图谱工作是否已进入实现基线。记录原有页面和关键流程截图到 `verifications/2026-11-11-knowledge-experience/`。
- [ ] 在 `webui` 跑相关现有测试，记录基线失败；明确当前开发服务对应源码或构建版本。不能把旧构建的截图当本次改版截图。

```powershell
bun run test src/tests/knowledge-page.test.tsx src/tests/knowledge-eval-page.test.tsx src/tests/knowledge-eval-api.test.ts src/tests/knowledge-eval-template.test.ts src/tests/knowledge-stats-api.test.ts src/tests/knowledge-graph.test.tsx src/tests/knowledge-graph-view.test.tsx
```

预期：记录实际结果；历史失败写明，不能降低断言或删除测试来变绿。

### Task 1 / WU-01：共享体验基础

文件：新增 `knowledge-shared/experience-types.ts`、`experience.css`、`KnowledgePreview.tsx`、`KnowledgeStatus.tsx`；新增 `src/tests/knowledge-experience.test.tsx`。不修改页面及共享 ui 基础组件。

- [ ] 根据契约实现两个共享组件；先用行为测试证明焦点进入摘要、Esc关闭、忙碌状态有名称；不测试颜色字符串或抄CSS快照。
- [ ] 定义下面的 motion token 与对应类，含动画退出和 Portal 作用域；状态文案由调用方提供，不在共享组件写死业务语言。

```css
.knowledge-experience,
.knowledge-experience-surface {
  --knowledge-motion-fast: 120ms;
  --knowledge-motion-content: 160ms;
  --knowledge-motion-sheet-in: 220ms;
  --knowledge-motion-sheet-out: 160ms;
  --knowledge-motion-ease: cubic-bezier(.2,.8,.2,1);
}
@media (prefers-reduced-motion: reduce) {
  .knowledge-experience .knowledge-motion,
  .knowledge-experience-surface.knowledge-motion,
  .knowledge-experience-surface .knowledge-motion {
    animation: none !important;
    transition: none !important;
  }
}
```

- [ ] 对 active spinner 同样加 `knowledge-motion`；保证 reduce 后仍有文字。用浏览器检查进入/退出过程中都没有闪烁或被突然卸载。
- [ ] 跑 `bun run test src/tests/knowledge-experience.test.tsx`，预期行为用例通过，类型导出固定后交给两页消费。

### Task 2 / WU-02：知识库完整路径

文件：修改 `KnowledgePage.tsx`、`src/tests/knowledge-page.test.tsx`；新增表中四个知识库组件。禁止改 GraphPanel/GraphView、locales、API。

- [ ] 先补行为用例：名称筛选/清空、点击卡片后返回保持筛选、文档旧响应不能覆盖新文档、上传失败保留可重试入口。
- [ ] 拆分现有大页面时原样保留 mutation 和 scope 参数；首页采用一行摘要、卡片和明确主动作，不显示未经接口支持的跨库最近文档。
- [ ] 把上传与路径导入移入同一 Sheet 的两种方式；沿用已有文件编码和服务端能力，文件读取/提交失败分别显示，禁止编造取消上传服务端接口。
- [ ] 把现有片段弹窗整理为 DocumentSheet，分页按 API 既有 limit/offset；没有更新日期时显示“—”，不生成虚构时间。
- [ ] 检索在独立 Tab；保留模式、降级理由、来源定位；请求失败与空结果有不同状态。
- [ ] 通过事件处理器保存/恢复离开列表时的滚动与焦点；后台轮询只更新行数据，不重复入场。
- [ ] 跑 `bun run test src/tests/knowledge-page.test.tsx src/tests/knowledge-stats-api.test.ts`，预期上传/删除/修复/检索和新增行为全部通过。

行为测试写法以现有 mock 与 React Testing Library 为准，关键断言例如：

```tsx
await user.click(screen.getByRole("button", { name: "Product manual" }));
await user.click(screen.getByRole("button", { name: "返回知识库" }));
expect(screen.getByRole("textbox", { name: "搜索知识库名称" })).toHaveValue("Product");
expect(screen.getByRole("button", { name: "Product manual" })).toHaveFocus();
```

这里的无障碍名是本次拟定的新文案；实施时在统一文案任务登记，测试中使用固定 zh-CN 环境与现有 KBS fixture。

### Task 3 / WU-03：评测记录、配置、报告与对比

文件：修改 `KnowledgeEvalPage.tsx`、上述五个现有展示组件及 `KnowledgeEvalMetricGuide.tsx`，新增三种 Sheet；修改 `src/tests/knowledge-eval-page.test.tsx`。禁止改数据 transport、API 和 locales。

- [ ] 先补默认入口、隐私确认、防重复提交、取消、终态拉取报告、未评分/0分、expect/judge 分组、409保留草稿与旧报告响应守卫用例。
- [ ] 默认入口仅在首次载入时推导，用户切到题集后轮询不能自动跳回运行。新建配置从记录列表拆到 Sheet，所有原白名单字段与隐私确认保留。
- [ ] 题集首页展示摘要，点开编辑题目；Sheet关闭前检测 dirty，表单错误时不关闭；CSV解析继续复用 dataset-template.ts。
- [ ] 报告保留五指标和逐题表同页；公式移为可展开解释而非挤满指标卡。查询原文、配置标识、评分来源始终可核对。
- [ ] CaseSheet只显示 `KnowledgeEvalCaseResult.hits` 的既有字段；rank/grade/reason明确排版，null显示未评分；不把 chunkId 当正文，不隐式调用跨作用域知识库API。
- [ ] 对比基准/候选明确命名；逐指标方向来自API；退步清单来自comparison，不额外猜测缺失题的升降；不同组不混算。
- [ ] 跑 `bun run test src/tests/knowledge-eval-page.test.tsx src/tests/knowledge-eval-api.test.ts src/tests/knowledge-eval-template.test.ts`，预期原有业务约束和新增交互通过。

关键状态断言保留在现有 fixture 体系，例如：

```tsx
const callsBefore = api.createRun.mock.calls.length;
await user.dblClick(screen.getByRole("button", { name: "发起运行" }));
expect(api.createRun.mock.calls.length - callsBefore).toBeLessThanOrEqual(1);
```

此用例必须先按原隐私确认流程允许创建，并使用未完成 Promise 固定 busy 状态，最后断言恰有一次创建；否则只测到禁用按钮不能证明防重复提交。报告另用 failed/not_assessable/scored-null 三种 fixture 验证“错误”“未评分”“0.000”不混淆。

### Task 4 / WU-04：图谱与证据切换

依赖 WU-02。修改 `knowledge/GraphPanel.tsx`、`GraphView.tsx` 与 `knowledge-graph.test.tsx`、`knowledge-graph-view.test.tsx`；不写 KnowledgePage.tsx。

- [ ] 核对现有选中/展开/缩放/平移的行为，先补选择节点→证据入口、请求失败重试、切换实体旧响应守卫；存在的测试直接复用。
- [ ] 按现有 GraphPanelProps 接口接入 WU-02 的预览动作，保持 node/edge cap 和抽取确认；颜色与透明度给出选中反馈，不添加持续物理动画。
- [ ] 减少动画设置下选择/展开/来源定位仍可用；焦点返回清楚；若已有布局静态计算，保持静态。
- [ ] 跑 `bun run test src/tests/knowledge-graph.test.tsx src/tests/knowledge-graph-view.test.tsx src/tests/knowledge-graph-i18n-parity.test.ts`。

### Task 5 / WU-05：统一文案、动效集成与异常收口

依赖 WU-02/03/04。单一owner修改 `src/i18n/locales/*/common.json`、共享experience层、两页少量集成修正，以及 `knowledge-experience.test.tsx`；禁止无关页面重构。

- [ ] 合并两页新文案键，先补 zh-CN/en，按项目现有策略补齐全部语言键；共用的“返回、重试、加载、关闭”复用 existing keys。页面worker将新增键表交给此owner，避免并行写同一JSON。
- [ ] 检查 hover/focus/Esc、Portal层级、外部点击、草稿舍弃与焦点恢复；将字符串状态/默认入口不一致统一到契约。
- [ ] 检查初次加载与后台刷新：150ms延迟骨架计时器正确清理，快速切换无旧计时器闪现，快速返回不触发第二次mutation。
- [ ] 所有新循环动画受reduce限制，滑动/缩放退出后没有不可点击遮罩；不修改共享ui默认类和全局reduce规则。
- [ ] 运行 `bun run test src/tests/knowledge-experience.test.tsx src/tests/i18n.test.tsx src/tests/knowledge-graph-i18n-parity.test.ts`，再进入整体验证。

### Task 6 / WU-06：浏览器验收与集体收尾

执行者：Leader协调 test-engineer、reviewer、security-auditor；审查与实现使用不同实例。输出仅放 `.ai-runtime-artifacts/verifications/`、`reviews/`、`execution-logs/`。

- [ ] 用本次源码启动开发服务并核对页面；测试资料与题集使用可识别的隔离 fixture，不修改用户真实知识库，不使用付费模型来证明纯交互改版。
- [ ] 完整走：创建库→上传→处理终态→片段→检索→图谱证据→返回；导入题集→编辑→新建评测/隐私确认→运行中→取消/完成→报告→单题→双运行对比→返回。
- [ ] 对业务响应可模拟，但报告明确标注；实际传输失败和超时至少各验证一条；实时模型/生成质量不作为本次UI验收结论。
- [ ] 检查 1440×900、1280×800、390×844，浅/深色，正常/减少动画，纯键盘。表格可局部横滚，页面不得横溢；长中文/英文题目、无日期、0/null、空/错/处理中都可读。
- [ ] 在浏览器录制一段约20–40秒的关键交互：摘要出现/消失、进入返回、Sheet开合、运行状态变化；以录屏验证连续性，不用静态截图声称“动画已验证”。录制用测试fixture，不包含凭据或真实私人内容。
- [ ] DevTools检查连续打开/关闭20次无遗留遮罩/异常焦点，10次快速换Tab无串页；Performance轨迹检查本次motion没有持续大面积布局/绘制。若记录出现>50ms长任务，定位与本改动关系并记录，不对所有设备承诺60fps。
- [ ] 在 `webui` 顺序执行下列最终检查，记录真实通过/失败和未解决项。

```powershell
bun run test
bun run build
bun run lint
```

- [ ] 已有失败先与Task0基线比对，所有新增失败必须修复；不得删除原测试来适应新布局。未触及Python/WS则不启动全后端大回归；若因契约变更触及后端则重估范围与验证。
- [ ] reviewer检查交互/竞态/数据不变量，security-auditor检查路径与内容呈现、文件导入和mutation鉴权，必要时追加性能审查。
- [ ] Leader写 `verifications/2026-11-11-knowledge-experience-collective-test.md`、`reviews/2026-11-11-knowledge-experience-code-review.md`、security review与execution-log，记录完成范围、录屏、截图及限制。完成不等于发布，不自动push/合并。

## 6. 关键失败场景与通过标准

| 必验场景 | 通过标准 |
| --- | --- |
| 指针从触发器移到摘要 | 摘要保持，不闪烁；Esc关闭，关键内容仍可点进详情查看 |
| 快速 A→B→A | 最后选择一致；B的迟到响应不覆盖A；没有额外mutation |
| Sheet退出/再开 | 不残留遮罩或滚动锁；焦点进入标题/首个控件，关闭回原入口 |
| 表单提交失败或题集冲突 | 草稿保留，错误字段可见，可修改再试，不误报成功 |
| 处理中/降级/未评分 | 各状态明确可读，停用动画仍能理解，null不转0 |
| 轮询刷新 | 不闪整页骨架，不重播卡片进场，不打断阅读/输入；终态停止轮询 |
| 小屏、长内容与深色 | 操作不溢出/裁切，文字可读，详情可滚，焦点可见 |
| 评测完整性 | 五项指标、逐题结果、标注来源、退步证据都能找到，无无依据“通过率” |

## 7. 官方依据与证据边界

- [Radix Animation](https://www.radix-ui.com/primitives/docs/guides/animation)：支持CSS控制入场/退出及退出阶段卸载处理；因此本期不必先引入JS动效库。
- [Radix Hover Card](https://www.radix-ui.com/primitives/docs/components/hover-card)：用于视力用户的链接预览；不能把必需信息只藏在此类预览内。本期复用现有Tooltip/Popover/详情作为等价入口。
- [W3C Hover or Focus](https://www.w3.org/WAI/WCAG22/Understanding/content-on-hover-or-focus.html)：补充内容应可关闭、可移入、保持可读。
- [MDN prefers-reduced-motion](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/prefers-reduced-motion)：根据用户设置减少非必要运动。

具体时长、配色、布局和分阶段策略为本计划的设计判断；这些来源不证明截图原产品有相同参数，也不证明当前知序已通过验收。

## 8. 计划自检

- 覆盖：两页视觉、hover/click/focus、导航返回、Sheet/Dialog、上传、评测状态、图谱、异常、暗色/窄屏/reduce、测试与动态验收均有明确任务。
- 类型：复用 KnowledgeKb/Doc/Chunk、KnowledgeEvalReport/CaseResult/Comparison 和 GraphPanelProps；共享新契约有唯一文件与owner。
- 并行：先冻结共享体验层，知识库与评测并行；图谱在知识库之后；locales与集成单一owner收口。无同一文件并行写。
- 依赖：无需新增运行时依赖；没有把外部组件升级、未实现的后端字段或图谱算法替换作为隐含前提。
- 本轮验证：只完成文档与代码接口核对、官方文档调研；本计划中的实现、测试和录屏均待执行，未声称已通过。

## Next

- 计划确认：说「开始实现」或「执行」。
- 需要调整：直接指出范围、交互或动效修改意见。
- 采用并行实施：按同stem的dispatch执行；本轮到计划交付为止。
