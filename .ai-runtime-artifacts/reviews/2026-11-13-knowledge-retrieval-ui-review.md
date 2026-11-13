# 知识库「检索预览」页面审查与去 AI 味整改

- 日期：2026-11-13
- 范围：`webui/src/components/workspace/KnowledgePage.tsx` 中 `KnowledgeRetrievalPage` 入口及其子组件（检索表单、检索范围、检索历史、检索链路画布、检索结果、阶段详情弹窗）
- 输入材料：真实运行实例（gateway `127.0.0.1:8765` + 已构建的前端产物）、Playwright/Chromium 真实截图（1440×900、1024×800、390×844）、源码
- 数据状态：示例知识库「示例·知识库产品与技术」，3 篇文档、12 分块；查询「记忆工作区隔离」，混合模式、8 条
- 现有组件与令牌：React 18.3 + Tailwind + Radix Dialog 1.1.4 + lucide-react 0.469；项目自封装 `Button`（CVA，`rounded-control`）、`Input`、`KnowledgeDialogContent`、`SegmentedControl`；令牌 `--radius-panel`、`--radius-control`、`--muted`、`--border`、`--primary`；字体为系统 UI 栈 `--font-ui`，等宽为 `--font-code`
- 限制：未做读屏测试、未做 200% 缩放验证；性能数字来自 trace 自身计量，未做前端渲染 profiling

截图依据（改造前，真实实例）：

- `proposal/assets/before-idle.png`（1440×900 初始态）
- `proposal/assets/before-results.png`（1440×900 检索后）
- `proposal/assets/before-stage.png`（阶段详情弹窗）
- `proposal/assets/before-narrow.png`（1024×800）
- `proposal/assets/before-mobile.png`（390×844）

## 总体判词

**这个页面把「检索结果」放在了「检索过程」的后面，而过程用了 10 张一模一样的卡片——用户来是为了看检索到了什么，页面先让用户读 719px 的仪表盘。** 其余问题（整行浅紫大胶囊按钮、每步 2–4 个描边小 pill、全角圆角卡片堆叠、`hybrid`/`16398ms` 原始值直接进中文界面、详情弹窗铺满 1400px 只放两个空盒子）都是同一根因：按「有什么字段就摆什么块」拼页面，而不是按「用户判断什么」组织信息。

保留的部分：左查询 / 右链路的整体骨架符合任务；链路每个阶段可点开看分数，是同类产品里少见的有效设计；检索历史作为可重跑的记录有实际价值；页面对错误、降级、无结果都有状态处理。

## 问题清单

| 编号 | 优先级 | 位置与问题 | 证据类型 | 用户影响 | 修复方向 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| P1-1 | P1 | 链路画布高 719px，检索结果 `top=812`（900px 视口只剩 88px；1024×800 视口完全不可见） | 实测几何（`canvasH=719`、`resultTop=812`）+ 截图 | 用户检索后必须先滚动才能看到命中的内容，主任务被过程信息压到首屏之外 | 链路改为紧凑流水线（≤2 行），结果紧随其下；节点点击看详情 | 已修复并验证 |
| P2-1 | P2 | 10 个阶段各一张圆角卡片，每张内嵌 2–4 个描边 pill，10px 字号；「关键词召回 7ms」与「模型改写 1121ms」权重相同 | 截图 `before-results.png`；源码 `RetrievalTraceCanvas.tsx:164-224` | 满屏重复形状，看不出哪一步值得关注；小字号 pill 在窄屏上换行成噪音 | 流水线 chip（状态点 + 名称 + 时长），次要指标移入详情；头部补「最慢」提示 | 已修复并验证 |
| P2-2 | P2 | 阶段详情弹窗因 `.knowledge-dialog` 的 `max-width` 覆盖了 `max-w-lg`，实测宽度≈1408px；两个指标盒占不足 1/3，其余空白 | 截图 `before-stage.png`；`experience.css:185-190` | 阅读路径被拉长，信息密度极低 | 固定 44rem，指标改为无边框键值网格，补阶段解释与实际分词/实体/标签值 | 已修复并验证 |
| P2-3 | P2 | 中文界面直接出现 `hybrid`、`fts`、`vector`、`model_unavailable`、`16398ms`、`11-13 10:33`（ISO 字符串切片） | 截图 + `KnowledgeRetrievalPage.tsx:386-390`、`KnowledgeSearchResults.tsx:22-32` | 用户需要自己翻译模式和路径；16.4 秒的慢查询没有任何提示 | 模式/路径/状态本地化；时长格式化（`16.4s`）；日期用本地短格式 | 已修复并验证 |
| P2-4 | P2 | 检索按钮为 306×36 的整行胶囊；禁用态是 50% 透明的主色，呈浅紫大色块 | 实测 `searchBtn={w:306,h:36}` + 截图 | 禁用、加载、可点击三种状态难以区分；与知识库页右上角的小主按钮也不一致 | 与「条数」同排、固定宽度；禁用态用中性灰；加载态保留 spinner | 已修复并验证 |
| P2-5 | P2 | 左栏被拆成「查询 / 检索范围 / 检索历史」三张等权卡片，三套边框、三套 padding | 截图 `before-idle.png` | 表单与设置被切成三个互不相干的模块，扫读成本高；页面像模板拼装 | 合并为单一控制面板，用 hairline 分段 | 已修复并验证 |
| P2-6 | P2 | 首次进入同时出现两条空态：「检索后在这里查看每个关键步骤」+「输入查询以预览检索结果」 | 截图 `before-idle.png` | 空页面像没做完；两条提示说的是同一件事 | 未检索时不渲染链路面板，只在结果区给一条可操作提示 | 已修复并验证 |
| P2-7 | P2 | 检索页底部仍显示知识库列表的统计条（1 知识库 / 3 文档 / 12 分块 …） | 截图 `before-idle.png` 底部；`KnowledgePage.tsx:751` 只判断 `!detailOpen` | 页脚数字与当前任务无关，滚动到底会误以为还在列表页 | 检索页同样隐藏统计条 | 已修复并验证 |
| P3-1 | P3 | 链路头部 `GitMerge` 主色气泡、7×7 圆形折叠按钮（图标像“关闭”）、结果区 pill 行 | 截图 + `RetrievalTraceCanvas.tsx:125-149` | 装饰抢注意力，折叠图标语义不清 | 去掉气泡；折叠改为「收起/展开」文字 + chevron | 已修复并验证 |
| P3-2 | P3 | 命中行带左色条、阴影、评分主色胶囊、`定位到文档` 描边胶囊，8 条结果纵向占满 | 截图 + `KnowledgeCitation.tsx:57-115` | 每条命中都像一张营销卡，比较分数与内容反而更慢 | 紧凑列表行：等宽分数 + 文档/章节/页码 + 截断正文，操作降为文字按钮 | 已修复并验证 |
| P3-3 | P3 | 阶段详情里出现未翻译的字段名 `modeUsed`、`outcome` 和大写原始 key 作为分组标题 | 源码 `KnowledgeRetrievalPage.tsx:515, 626-631` | 开发者字段泄漏到界面，术语不一致 | 映射为「实际模式 / 结果 / 模型 / 原因」等本地化标签 | 已修复并验证 |

## 关键问题取证

### P1-1 结果被链路挤出首屏

- 复现：1440×900 打开知识库 → 检索预览 → 查询「记忆工作区隔离」→ 混合模式检索。
- 实测：链路画布 `height=719px`，结果面板 `top=812px`，视口 900px，结果仅剩 88px 可见；1024×800 时结果完全在视口外。
- 根因：链路每个 stage 渲染成 64px 高的卡片并纵向排列（10 步），结果面板在其下方，二者在同一列。
- 验收：同一查询、同视口下，检索结果标题进入首屏（`top < 视口高度 - 240px`），链路自身高度 ≤ 180px；点击任一节点仍可查看完整明细。

### P2-2 详情弹窗宽度失控

- 证据：`KnowledgeRetrievalPage.tsx:444` 只传 `max-h-[80vh]`，`experience.css:185-190` 的 `.knowledge-experience-surface.knowledge-dialog { max-width: calc(100vw - 2rem) }` 覆盖了组件默认 `max-w-lg`。
- 验收：1440 视口下弹窗宽度 ≤ 720px，标题、状态、指标、明细一屏内可见；Esc 关闭后焦点回到触发节点。

### P2-3 原始值直出

- 位置：`KnowledgeSearchResults.tsx:22`（`modeUsed`）、同文件 26（`routes`）、`KnowledgeRetrievalPage.tsx:386-390`（`historyMeta` 与 ISO 切片）。
- 验收：中文界面中不再出现 `hybrid`、`vector`、`16398ms`、`11-13 10:33`；慢查询以 `16.4s` 呈现且与使用同一格式的链路总耗时一致。

## 十类覆盖与未验证项

| 类别 | 结论 |
| --- | --- |
| 1 设计一致性与品牌 | 已检查（P2-4、P2-5、P3-1、P3-2）。与知识库列表页的中性面板、hairline 分隔不一致 |
| 2 可用性与交互 | 已检查（P1-1、P2-6、P2-7）。加载/错误/无结果状态存在；重跑历史项会回填并立即检索，行为正确 |
| 3 响应式与适配 | 已检查（1024、390 真实截图）。窄屏下历史先于结果、链路在结果前展开，顺序不合任务 |
| 4 可访问性 | 部分检查：原生 label/aria-label、焦点轮廓、Radix 弹窗聚焦与 Esc 由组件保证；未做读屏与 200% 缩放 |
| 5 代码与维护 | 已检查：未新增组件库；沿用 Button/Input/Radix Dialog；去掉了重复的空态分支 |
| 6 状态与工程 | 已检查：`seq`/`busy` 防竞态、切换范围清空结果、历史按库加载；改动未触及这些路径 |
| 7 性能与兼容 | 不适用：短列表，未做虚拟化；链路节点动画尊重 `prefers-reduced-motion` |
| 8 国际化与内容 | 已检查（P2-3）。补 10 个语言资源；`fts` 等诊断码保留原文并统一标注「路径」 |
| 9 安全与业务约束 | 不适用：只读预览，无写操作；未展示密钥 |
| 10 测试与验收 | 已检查：更新 `knowledge-page.test.tsx` 的检索断言；真实实例前后对照截图 |

可选审美建议（未列入缺陷）：结果区可考虑增加“按分数/按文档分组”，当前数据量下收益不足，未实施。

## 优化执行计划

| 顺序 | 改动位置 | 复用 | 预期效果 | 验证 |
| --- | --- | --- | --- | --- |
| 1 | 左栏三卡 → 单面板 + 分段 | `rounded-panel`、`divide` | 扫读成本下降，页面不再像模板拼接 | 截图 + 表单可用性 |
| 2 | 检索按钮同行固定宽 | `Button` default + 中性禁用态 | 三种状态可辨 | 键盘 Tab + 禁用/加载截图 |
| 3 | 链路卡片列表 → 流水线 chip | `lucide-react`、现有状态色 | 首屏出现结果 | 实测 `resultTop` |
| 4 | 命中行 → 紧凑列表 | 现有 `KnowledgeCitation` 重写（唯一调用方） | 一屏可比较 3–4 条命中 | 展开/定位可用 |
| 5 | 阶段详情 | `KnowledgeDialogContent` + 新 `.knowledge-stage-dialog` | 信息密度与可读性 | 1440/1024/390 |
| 6 | 本地化与格式化 | i18n 资源 + `formatDuration` | 中文界面无原始枚举 | 10 语言 shape 测试 |
| 7 | 隐藏检索页统计条 | `KnowledgePage.tsx` 条件 | 页脚不再串页面 | 截图 |

## 组件选型与逐元素 HTML 方案

- 不新增组件库。主库沿用项目自封装组件：`webui/src/components/ui/button.tsx`（CVA，`default`/`ghost`/`outline`，`sm`）、`webui/src/components/ui/input.tsx`、`KnowledgeDialogContent`（`@radix-ui/react-dialog@1.1.4`）、`lucide-react@0.469.0`。
- 未选用 Ant Design / MUI 等原因：本页只用到按钮、输入、原生下拉与弹窗，项目已有封装且样式令牌齐全；为一个预览页引入整套组件库会造成按钮、表单、弹窗风格分裂与包体积上升。
- 官方文档：Radix Dialog <https://www.radix-ui.com/primitives/docs/components/dialog>（访问 2026-11-13）；Lucide <https://lucide.dev/icons/>（访问 2026-11-13）。
- HTML 方案入口：`proposal/index.html`（本地打开，无联网请求；图像为真实实例截图）。逐元素编号见页面内「逐元素改造账本」。
- 原生组件展台：本次未切换组件库，不渲染假的“官方默认样例”；方案中的全部界面即为项目真实组件在真实实例中的截图。

## 字体与视觉素材决定

- 基线：`--font-ui` 系统栈（`-apple-system, "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans SC" …`），数字统一 `tabular-nums`，ID 使用 `--font-code`。实测 1440 页面 `computedFont` 即为该栈。
- 候选：A 中性产品（沿用系统栈，推荐）；B 标题衬线（Songti/SimSun）——不适合以数据与操作为主的工具页；C 数字等宽（Cascadia Code/Consolas 只用于分数与耗时）——会与链路当前 `tabular-nums` 表现接近，但增加字形混排。
- 决定：**沿用现有系统栈**（无网络字体、无加载成本、与聊天/记忆页一致）；试排入口 `proposal/font-picker.html`，可按真实文案切换标题/正文/数字角色。状态：按项目现有品牌基线采用，未新增字体文件。
- 图片：本页不需要解释性图像或装饰图；未引入图库与生成素材。

## 实施与验收记录

### 改动文件

- `webui/src/components/workspace/knowledge/KnowledgeRetrievalPage.tsx`：头部、两段左栏、流水线上方结果区、阶段详情弹窗与 4 个子组件重写；`knowledgeCollapsed` 初始按视口宽度 <1024 收起。
- `webui/src/components/workspace/knowledge/RetrievalTraceCanvas.tsx`：卡片列表 → 流水线节点；新增最慢步骤、状态词、`docFilter` 图标；无 trace 时不渲染。
- `webui/src/components/workspace/knowledge/KnowledgeSearchResults.tsx`：pill 行 → 模式/路径/降级说明行；命中列表改分隔线。
- `webui/src/components/workspace/knowledge/KnowledgeCitation.tsx`：重写为紧凑三列列表（序号 / 内容 / 分数），去掉阴影、左色条与胶囊。
- `webui/src/components/workspace/knowledge/knowledge-labels.ts`（新增）：模式与召回路径的本地化映射。
- `webui/src/components/workspace/KnowledgePage.tsx`：检索页隐藏统计条与知识库列表。
- `webui/src/lib/format.ts`：新增 `formatCompactDuration`（<1000ms 保留毫秒，否则秒）。
- `webui/src/components/workspace/knowledge-shared/experience.css`：新增 `.knowledge-stage-dialog`（44rem）；`prefers-reduced-motion` 覆盖链路节点。
- 10 个语言 `common.json`：新增 25 个检索键、移除 2 个不再使用的键、`historyMeta`/`canvasTotal` 改用格式化时长。
- `webui/src/tests/knowledge-page.test.tsx`：同步模式与路径的本地化断言。

### 逐项结果

| 编号 | 结果 | 证据 |
| --- | --- | --- |
| P1-1 | 已修复并验证 | 1440×900：链路 `height=175`、结果 `top=282`（改造前 719 / 812）；1024×800 首屏可见 |
| P2-1 | 已修复并验证 | 流水线节点 10 个，头部「共 13s 最慢：向量召回 6.2s」；截图 `assets/after-3-retrieval-results.png` |
| P2-2 | 已修复并验证 | 弹窗实测 704×250；Esc 关闭、焦点由 Radix Dialog 管理 |
| P2-3 | 已修复并验证 | 中文界面显示「混合 / 向量 · 关键词 · 图谱 / 1.7s / 2 分钟前」；诊断码仅出现在详情 |
| P2-4 | 已修复并验证 | 按钮实测 76×36；禁用态为 `bg-muted`（截图 `assets/after-2-retrieval-idle.png`） |
| P2-5 | 已修复并验证 | 左栏两段面板：控制（查询 + 范围）与历史，hairline 分隔 |
| P2-6 | 已修复并验证 | 初始态仅一条空态；链路未运行时不渲染 |
| P2-7 | 已修复并验证 | 检索页 `knowledge-stats` 与 `knowledge-kb-list` 均不存在（`statsVisible=false`） |
| P3-1 | 已修复并验证 | 头部无装饰气泡；折叠为「收起 / 展开」+ chevron，aria-label 保留 |
| P3-2 | 已修复并验证 | 命中行一屏 3–4 条，可展开正文、定位分块、查看 id |
| P3-3 | 已修复并验证 | 详情标签为「实际模式 / 结果 / 模型 / 原因」；值列表按分词、实体、标签、路径分组 |

### 验证命令

| 命令 | 结果 |
| --- | --- |
| `npx tsc -p tsconfig.build.json --noEmit`（webui） | 通过，无输出 |
| `npx vitest run src/tests/knowledge-page.test.tsx` | 27/27 通过 |
| `npx vitest run src/tests/i18n.test.tsx` | 27/27 通过（含 10 语言 shape 对齐） |
| `npx eslint --config webui/eslint.config.js` 8 个改动文件 | 无输出 |
| `npx vite build` | 成功（仅既有 chunk 体积提示） |
| 真实实例 Playwright 取证 | 1440×900 / 1024×800 / 390×844；控制台 0 error |

HTML 方案已在本机 Chromium 打开验证：8 张截图均加载（`naturalWidth>0`）、前后切换与账本过滤可用、字体试排 iframe 加载 4 套搭配，控制台无错误。

### 剩余与限制

- 未做读屏软件与 200% 缩放验证；键盘仅检查 Tab 顺序与 Esc。
- 移动端链路「默认收起」按进入页面时的视口判断（已在 390×844 全新加载验证：无节点、头显示「展开链路」）；运行中缩放窗口不会自动折叠。
- 命中片段按原始文本展示，Markdown 标题 `##`、加粗标记会原样出现；未做渲染或清洗，避免改变被引用内容。
- 文档范围下拉一次最多加载 200 篇；超出需服务端检索，本期未做。
- 未提交、未推送：改动仍在 `feature/desktop-app` 相关工作树中，提交范围需用户确认。
