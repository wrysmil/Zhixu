---
artifact: verification-lite
route: leader-direct
skills: []
source:
  - 用户请求：装 impeccable 扫 webui 看看问题
  - 用户请求：直接修（两个 P1）
  - https://github.com/pbakaus/impeccable （v4.8.2，CLI 通过 npx 使用）
created_at: 2026-11-13
tier: 1
---

# WebUI × Impeccable 检测报告（含 P1 修复与复扫）

## 环境

- 构建：`webui` 重新 `tsc + vite build` → `nanobot/web/dist`
- 服务：`.venv/bin/nanobot webui --no-open -y`（127.0.0.1:8765）
- 扫描：`npx impeccable@latest detect --json --no-advisory --viewport 1440x950`，13 条路由；另 4 条路由 390×844
- 证据：同目录 `assets/2026-11-13-impeccable-scan/`（每条路由 JSON + 4 张截图）

## 结果（1440×950，共 84 条）

| 规则 | 数量 | 判定 |
| --- | --- | --- |
| text-occlusion | 29 | 全部集中在 knowledge / knowledge-eval → 双层渲染副产物 |
| low-contrast | 21 | 真实 |
| undersized-ui-text | 14 | 真实（creative 13、identity 1） |
| nested-cards | 11 | 真实（结构性） |
| line-length | 4 | 真实（memory 3、skills 1） |
| kicker-above-heading | 1 | 真实（knowledge-eval） |
| skipped-heading | 1 | 真实（memory，h1→h4） |
| repeated-container-text | 1 | 真实（automations "heartbeat"×3） |
| tiny-text | 1 | 真实（identity 11.5px 正文） |
| pulsing-dot | 1 | 风格项，已支持 prefers-reduced-motion |

## P1 — knowledge / knowledge-eval 双层整页渲染

`isSettingsLikeView`（`webui/src/App.tsx:2925`）把 `knowledge`、`knowledge-eval` 也算作 settings-like，而下方
`view === "memory" || "identity" || "knowledge" || "knowledge-eval"` 的层（`App.tsx:3264`）同时渲染。

实测（#/knowledge-eval，等待 3s 稳定后）：

- `main` 下 4 个 `absolute inset-0` 层；其中 `absolute inset-0 flex flex-col` 是完整的 SettingsView「概览」页
  （Text 用量图表、模型、能力…，**518 个 DOM 节点**），被上层 `bg-surface-raised` 的白底整页盖住
- 该隐藏层无 `aria-hidden` / 无 `inert`，**8 个可 Tab 聚焦控件**（如「返回聊天」）
- 29 条 text-occlusion 全部来自这一层被遮挡的文字，非可见缺陷，但说明重复渲染确实存在

修复方向：settings-like 层与 memory/knowledge 层互斥（或对隐藏层加 `inert` + `aria-hidden`）。

## P1 — 正文对比度不足

`--muted-foreground`（light）为 `hsl(240 3.83% 46.078%)` ≈ `#71717a`，白底仅 **4.4:1**，已低于 AA 4.5:1；
代码里大量用 alpha 变体进一步压低（54 个文件、约 180 处 `text-muted-foreground/NN`）：

| 实际色 | 对比度 | 出现处 |
| --- | --- | --- |
| `#b1b1b6` / 白 | 2.1:1 | 侧栏 `li.px-1.5.py-1`（多页） |
| `#a3a3a9` / 白 | 2.5:1 | 侧栏 `span.px-2`（多页） |
| `#9c9ca2` / 白 | 2.7:1 | 输入框 placeholder（new / settings） |
| `#9b9ba1` / `#fcfcfc` | 2.7:1 | 设置搜索 placeholder |
| `#aaaaaf` / 白 | 2.3:1 | skills 计数 `text-[11px]` |
| `#7f7f87` / 白 | 4.0:1 | new 页 | 
| `#8d8d95` / 白 | 3.3:1 | new 页 |

建议：`--muted-foreground` 提到 ≈ `40%` 亮度，并给 alpha 设下限（正文不低于 `/85`，次要标签不低于 `/70`）。

## P2 — 10px 功能文字

- `webui/src/components/workspace/CreativeWorkspace*` 系列：模板卡片描述「把复杂概念，解释清楚」等、时间戳「4周前」、版本号「版本 3」、提示「模板仅初始化提纲」均为 `text-[10px]`
- identity 页「当前」徽章 `text-[10px]`
- identity 页还有一处 11.5px 正文

## P2 — 卡片套卡片（11 处）

agents（9 张卡再嵌套）、skills、apps、automations、settings-appearance、settings-models、knowledge、
knowledge-eval、creative（表单内嵌套）、new（composer 内嵌套）。

## P3 — 其他

- **kicker 无标题**：`MemoryPage.tsx:40` 把 11px 大写 kicker 直接写成 `<h1>`，下面没有真标题 → 触发 skipped-heading；同时 knowledge-eval 的「知识中心 / 检索评测台」是 kicker-above-heading 模式，与其他页面（无 kicker）不一致
- **长行**：memory 记忆条目 119–152 字符/行（3 处），skills 描述 1 处
- **automations**：`heartbeat` 文本在同一面板出现 3 次
- **装饰脉冲点**：`.thread-welcome-eyebrow-dot` 无限脉冲（`globals.css:1675`），已做 reduced-motion 兜底

## 修复（2026-11-13）

### 1. 双层渲染

`webui/src/App.tsx:2939` 新增 `rendersSettingsSurface`（只含 settings / apps / automations / skills / agents / teams），
SettingsView 层改用它（`App.tsx:3255`）；`isSettingsLikeView` 保留原语义，继续负责「只留图标栏」的布局判定。

复测：`#/knowledge-eval` 稳定后 `main` 下只剩 3 层（chat 隐藏层 + 视口 + 目标页），518 节点的 SettingsView 不再挂载。

### 2. 对比度

- `webui/src/globals.css:110` light `--muted-foreground`：`46.078%` → `42%`（白底 4.9:1 → 5.9:1，`#f4f4f5` 底也能过 AA）
- 脚本批量移除 `text-muted-foreground/NN`（NN ≥ 50）的 alpha 降级，**170 处 / 53 文件**；保留 10 处刻意淡出的装饰/禁用态：
  面包屑 chevron（`/35`）、拖拽把手（`/45`、`/40`）、空态图标（`/20`）、diff 标记（`/45`）、`disabled:`（`/50`）
- `AutomationsSettings.tsx`：筛选 chip 的 `text-sky-600`→`700`、`text-amber-600`→`700`、`text-rose-600`→`700`，
  计数徽章 `text-orange-800/70`→`text-orange-800`

### 复扫结果

| 指标 | 修复前 | 第一轮 | 第二轮 |
| --- | --- | --- | --- |
| 1440×950 合计 | 84 | 33 | **8** |
| text-occlusion | 29 | 0 | 0 |
| low-contrast | 21 | 0 | 0 |
| undersized-ui-text / tiny-text | 15 | 15 | 0 |
| line-length | 4 | 4 | 0 |
| kicker / skipped-heading | 2 | 2 | 0 |
| repeated-container-text | 1 | 1 | 0 |
| pulsing-dot | 1 | 1 | 0 |
| nested-cards | 11 | 10 | 8 |
| 390×844（4 条路由） | 21 | 14 | **0** |

### 第二轮修复

- **字号**：`ArticleList.tsx` 全部 `text-[10px]` → `text-[11px]`（模板描述、时间戳、版本号、提示语）；`IdentityPage` 的「当前」徽章 10→11px、卡片内黄色提示条 11.5→12px
- **标题语义**：`MemoryPage` 的 kicker 原本直接写成 `<h1>` 且页面无真标题 → 改为 `assistant-page-heading` 真标题；
  `MemoryCharts` h4/h5 → h2/h3，`MemoryGuide` h3 → h2，`EpisodeDetailPanel` h4 → h3（视觉不变，只修大纲）
- **kicker**：`KnowledgeEvalPage` 去掉 `kicker="知识中心"`（标题已自解释，避免 kicker-above-heading）
- **长行**：记忆条目 / episode 摘要 `max-w-[68ch]`，技能描述 `max-w-[64ch]`
- **重复文案**：automations 的 `heartbeat` 是主从视图（列表行 + 详情标题同一条记录），非缺陷 →
  `.impeccable/config.json` 里以 `repeated-container-text=*` + `*automations*` + reason 精确抑制
- **脉冲点**：删除 `.thread-welcome-eyebrow-dot` 的无限 `box-shadow` 动画与 `welcome-dot-pulse` keyframes
- **卡中卡**：
  - `AgentCard` 的「默认主 Agent / 系统预设 / 分类」三枚描边 pill → 一行内联文字（`★ 默认主 Agent · 系统预设 · 通用`）
  - `ArticleList` 模板选项卡去掉描边，选中态改用底色
  - `IdentityPage` 卡片内两条提示条去掉描边，保留色底

### 剩余 8 条（nested-cards，判定为误报）

`agents` / `automations` / `creative` / `knowledge-eval` / `new` / `settings-appearance` / `settings-models` / `skills` 各 1 条。

逐页核对 DOM：被指向的容器之上没有任何描边/圆角祖先（例如 `#/agents` 的卡片直接铺在 `bg-settings-canvas` 上，
祖先全是圆角 0、无描边的布局 div；`#/agents` 在去掉 pill 后配对从 9 降到 1）。剩下的配对对象是
主题预览缩略图（appearance）、composer 内的权限/模型控件（new）、面板内的输入框与徽章（skills / knowledge-eval）——
都属于「控件/缩略图放在容器里」，不是视觉上的卡中卡，因此未再改动结构。
另：`apps` 在未改代码的情况下自行清零，说明该规则对加载/动画时序也有一定敏感性。

### 验证

- `tsc -p tsconfig.build.json` + `vite build` 通过
- `vitest run`：2131 passed，3 failed（`appearance-boot.test.ts`，在未改动的干净工作区同样失败，属既有问题）
- 截图：`assets/2026-11-13-impeccable-scan/after/`（第一轮）、`after-round2/`（第二轮：agents / creative / identity / memory + JSON）

## 未覆盖

- 暗色主题（detect 无主题切换参数；app 的 appearance 系统会覆盖 `.dark` 类，手动切类无效）
- 需要登录/真实数据的页面状态、对话流、评测跑批过程
- `--preview=1` 静态预览壳与真实壳的差异
