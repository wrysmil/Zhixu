---
artifact: spec
route: superpowers:brainstorming
skills:
  - brainstorming
skills_evidence:
  - harness-kit/.agents/skills/brainstorming/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - C:/Users/Huangqh/Downloads/推理过程展现.md
  - Codex 桌面截图 + learn.chatgpt.com/docs/changelog
created_at: 2026-10-09
status: draft
approved: false
topic: codex-parity-ui-workbench
---

# 对标 Codex 的页面 / 对话 / 工作台重塑方案（spec）

## 0. 目标与决策

把 Mira（nanobot-webui）的**整体页面设计**、**对话中的展现**对标 Codex，并新增一个**单会话工作台右栏**——融合 Codex 的「Sources / 变更审阅 / 任务态」与 Qoder 图二红框的「环境信息 / 技能与 MCP / 产出 / 来源」聚合面板。

已确认的四个决策（来自本轮澄清）：

| 维度 | 决策 |
| --- | --- |
| 改造范围 | **三层全改**（页面 + 对话 + 工作台），但**分阶段落地**（一步步来） |
| 右栏形态 | **常驻可折叠右栏**，可**展开为全宽**做 diff 审阅；窄屏自动降为浮层 |
| 与多分屏关系 | **并存**：`PaneWorkbench` 仍负责多会话并排；右栏是「当前活动 pane 的单会话工作台」叠加层 |
| 数据边界 | **可加只读后端 API**；但 v1 先用前端已有数据派生，v2 再接后端补 git/工作区 diff |

> 本文件仅到「方案」层，不含实现。写完暂停，等确认后再「写计划 / 直接实现」。

---

## 1. 调研取证

### 1.1 Codex（图一 + 官方 changelog）
- **顶栏**：会话以 **tab** 呈现（`你好`、`Curie`、`+`），右侧有筛选/分屏/审阅面板开关。
- **侧栏**：产品切换器 + `新聊天` + `项目`（把会话按项目分组）+ 通知/搜索。
- **对话展现**：
  - `已处理 1分钟 19秒 ⌄` —— 可折叠的**耗时头**，展开看步骤。
  - `🌐 已搜索网页` —— **单行步骤**，图标 + 动词短语。
  - 正文是**结构化答案**：`1. 标题` + `日期 / 摘要 / 为什么重要 / 来源`，来源是带 favicon 的链接。
  - 悬浮 `⋯` 操作（复制/引用/重试）。
- **composer**：`与 Codex 协作` + `+` + `⚠ 完全访问`（权限/审批态）+ 模型 `GPT-5.5` + 推理强度 + 停止。changelog 提到「reasoning effort 紧凑拨盘」「approval picker with clearer permission choices」「Full access 警告弹窗」。
- **Sources 面板**：会话内引用聚合到一个专用区（`conversation's Sources panel`）；变更在**统一工作区**跨目录审阅、可就地 accept/reject；侧栏可切到 **activity view**；并行 **side chats**。

### 1.2 文档内其它产品的「推理过程展现」
- **OpenAkita**（doc OCR）：`已处理(11.7S)` + 逐条 `调用 DELEGATE_TO_AGENT(...)` 步骤 + **错误行**（`✗ NO ACTIVE SESSION…`）+ 网页搜索行带 `查看详情` / `搜索完成(3761 字符)`。→ 启示：**步骤可展开看详情**、**失败态显性**。
- **mul / qoder（图二）**：qoder 对话里 `执行工具 9 次` 折叠 + 正文；**右侧红框工作台**：`更新于 15:53` + 摘要段 + `环境信息`(diff `+106 -0` / `本地` / `feature/desktop-app` / `提交或推送`) + `技能与 MCP`(`3条建议` + 列表) + `产出`(文件) + `来源`。→ 这是**单会话工作台**的标杆形态。

### 1.3 现状架构（已读代码，作为改造基线）
- `App.tsx` `Shell`：`Sidebar`(272px/56px rail) + `main` → `PaneWorkbench`（多会话分屏：columns/rows/grid/BSP/main-stack，拖拽/缩放）或 `SettingsView`。
- `Sidebar.tsx`：品牌 + `新聊天/搜索/Apps/Skills/Automations/Agents` + `ChatList`（按项目/工作区分组）+ 底部设置/连接。**已接近 Codex 侧栏**。
- `ThreadShell.tsx`：`ThreadHeader` + `ThreadViewport`(消息) + `ThreadComposer` + `FilePreviewPanel`（右侧单文件预览，可拖宽）。
- `AgentActivityCluster.tsx`：**已实现 Codex 式「Worked for Xs」折叠**，内含 `ReasoningRow / ActivityStep / WebSearchRun / WebActivityRow / CliRunRow / McpRunRow / FileEditGroup`，标签即 `Working/Worked for {{duration}}`。
- `MessageBubble` + `MarkdownText`：最终答案渲染。
- composer：`ModelPresetBadge`（模型/预设）+ `WorkspaceControls`（`access_mode` full/restricted ≈「完全访问/受限」）+ `ComposerUsagePopover`（token 用量）。

---

## 2. 差距分析（Gap）

### 2.1 整体页面
| Codex 有 | 现状 | 差距 |
| --- | --- | --- |
| 顶部会话 **tab 条** | 分屏 tab 藏在侧栏 `ChatList` 分组 | 缺顶部常驻 tab 条 + `+` 新建 tab |
| 居中定宽正文列 + 大量留白 | 正文列 `max-w` 已有但节奏偏紧 | 间距/层级需 Codex 化 |
| composer 权限/审批态显眼 | `access_mode` 在 WorkspaceControls 里偏弱 | 权限 chip 需前置 + Full access 警告 |

### 2.2 对话展现
| Codex/OpenAkita 有 | 现状 | 差距 |
| --- | --- | --- |
| `已处理 X` 折叠 | ✅ `AgentActivityCluster` | 视觉/文案打磨（图标、节奏、错误态） |
| 步骤 `查看详情` 展开 | 部分（`GenericToolRun`/`WebSearchRun`） | 统一「查看详情」抽屉/内联展开 |
| 失败步骤显性（红 ✗） | 有 `tone=error` | 需对齐 OpenAkita 的「错误行 + 原因」 |
| 结构化答案（编号/日期/来源） | `MarkdownText` 通用 | 答案排版强化 + **行内引用角标** |
| 来源聚合 Sources | ❌ 仅零散 web 行 | **缺**（进工作台右栏） |

### 2.3 工作台（核心缺口）
| Qoder 红框分区 | 现状 | 差距 |
| --- | --- | --- |
| 更新于 + 会话摘要 | ❌ | **缺** |
| 环境信息（diff 统计/本地/分支/提交） | `workspaceScope` 有 path/name/access；**无 git 分支/工作区 diff 汇总/提交动作** | **缺**（v1 前端派生 diff，v2 后端 git） |
| 技能与 MCP（+ 建议） | `skills/mcpPresets/cliApps` 已知，未按会话聚合用量 | **缺**聚合 |
| 产出（文件） | `FileEditGroup` 在对话内 | 未在工作台聚合；`FilePreviewPanel` 仅单文件 |
| 来源 | ❌ | **缺** |
| 全宽 diff 审阅 + accept/reject | `DiffPair` 有 diff，无统一审阅面 | **缺** |

**结论**：页面与对话是「打磨 + 补齐」，工作台是「新增」。工作台右栏是本次价值最高、也是唯一需要新数据面的部分。

---

## 3. 设计方案

### 3.1 布局总览（三层如何拼合）

```
┌─ Sidebar ─┬──────────────── main (PaneWorkbench：1..N 会话分屏) ───────────────┐
│ (基本保留) │  ┌─ tab 条：会话1 | 会话2 | + ─────────────────────────── 视图开关 ─┐ │
│           │  ├─ active pane ────────────────────────────────────────────────┤ │
│           │  │  ┌ 对话列(居中定宽) ┐        ┌ 工作台右栏(常驻可折叠) ┐        │ │
│           │  │  │ header           │        │ 概览: 更新于+摘要      │        │ │
│           │  │  │ Worked-for 折叠   │  ←→    │ 环境: +106 -0 分支 提交 │        │ │
│           │  │  │ 步骤/来源角标     │  展开   │ 技能与MCP: 用量+建议    │        │ │
│           │  │  │ 结构化答案        │  全宽   │ 产出: 文件/diff         │        │ │
│           │  │  │ composer          │        │ 来源: 引用聚合           │        │ │
│           │  │  └──────────────────┘        └─────────────────────────┘        │ │
│           │  └─────────────────────────────────────────────────────────────────┘ │
└───────────┴──────────────────────────────────────────────────────────────────────┘
```

- **右栏属于 active pane**：`ThreadShell` 内新增 `SessionWorkbench` 作为对话列的兄弟节点（与现 `FilePreviewPanel` 同一 `<section>` flex 容器）。分屏切活动格时，右栏跟随该格会话。
- **折叠/展开**：右栏三态——`rail`（常驻窄栏，分区手风琴）→ `expanded`（点「审阅变更」或某产出，右栏铺满 pane 宽度做全宽 diff/文件）→ `collapsed`（收成竖排图标条）。`FilePreviewPanel` 吸收为 `expanded` 态下的文件/diff 面，不再独立存在。
- **响应式**：`< lg` 或分屏格过窄时，右栏默认 `collapsed`，点击以浮层（sheet）覆盖出现。

### 3.2 Layer A — 整体页面（对标 Codex）
1. **顶部会话 tab 条**：把 `PaneWorkbench` 现有 header host 升级为常驻 tab 条（会话名 + 活动/未读点 + `+` 新 tab + 右侧「视图/工作台」开关组）。复用 `workbench-model` 的 tab 状态，不新建模型。
2. **侧栏**：保持结构；仅调 `新聊天` 与 `项目分组` 的间距/字阶贴近 Codex；`access_mode` 入口收敛到 composer（见 3.4）。
3. **正文列节奏**：统一 `max-w-[46rem]` 居中列、区块垂直间距、hover 层级，向 Codex 留白靠拢（纯 token/class 层，不动数据流）。
4. **composer 内联推理档**：模型名后直接跟推理强度（Codex `GPT-5.5 中`、Qoder `Qwen3.8-Flash 中`），provider 支持时以轻量下拉切换，而非独立拨盘弹层。
5. **底部状态栏**（Qoder 图二底部实证）：pane 底缘常驻一条 `工作区名 · 本地/远程 · 分支 · 上下文用量%`，把现在散在 WorkspaceControls/`ComposerUsagePopover` 的信息收敛为常驻只读条；与工作台「环境信息」同源。
6. **主题**：沿用现有 shadcn 浅色体系（Hope 重塑已铺色板/圆角/去玻璃），本层只做密度与对齐微调，避免与已提交的 `30a585c` 视觉重塑冲突。

### 3.3 Layer B — 对话展现（对标 Codex + OpenAkita）
1. **Worked-for 折叠打磨**：保留 `AgentActivityCluster`/`ThinkingReasoningShell`；文案对齐 Codex（`已处理 1分19秒`），头图标与 chevron 统一；活动态 sheen 保留。
2. **步骤行统一「查看详情」**：`ActivityStep`/`WebSearchRun`/`GenericToolRun` 增加一致的右侧 `查看详情`，展开内联详情（参数/结果片段/字符数，学 OpenAkita `搜索完成(3761 字符)`）。
3. **失败态显性**：错误步骤用红 ✗ + 一句原因（对齐 OpenAkita 的 `✗ NO ACTIVE SESSION…`），复用现有 `tone="error"`。
4. **结构化答案排版**：`MarkdownText` 增加「答案模式」样式——编号小节、`日期/摘要/来源` 键值行的字阶与缩进，贴近 Codex 新闻检索结果版式。
5. **行内引用角标**：正文里的来源渲染为 `[n]` 角标 + favicon，点击滚动/高亮到工作台「来源」区（与 3.4 打通）。

### 3.4 Layer C — 单会话工作台右栏（新增，核心）
新组件 `webui/src/components/workbench/SessionWorkbench.tsx`（+ 子分区组件）。分区与数据：

| 分区 | 内容 | v1 数据来源（前端派生） | v2（后端只读 API） |
| --- | --- | --- | --- |
| **概览** | `更新于 HH:MM` + 会话目标/结果摘要 | 末条 assistant 摘要 + `session.updatedAt` | 后端精炼 summary |
| **环境信息** | diff `+A -D`、`本地/远程`、分支、`提交或推送` | 汇总 `message.fileEdits` 的 added/deleted；`workspaceScope.{project_path,project_name,access_mode}` | git `branch/remote/dirty/base` + 工作区真实 diff（`GET /api/sessions/{key}/workbench`） |
| **技能与 MCP** | 本次用到的 skills/mcp/cli + `N 条建议` | 从 `toolEvents` 聚合 `mcp_*`/`run_cli_app`；`skills`/`mcpPresets`/`cliApps` 已知 | 建议：后端按上下文推荐 |
| **产出** | 文件/图片/文档列表，点开进 `expanded` diff/预览 | `fileEdits` + `media` + `FilePreviewAvailability` | 工作区新增文件枚举 |
| **来源** | 网页/文档引用聚合（title/host/favicon/url） | 从 `WebSearchRun`/`WebActivityRow` 的 toolEvents 去重聚合 | 后端统一 citation 表 |

- **交互**：分区为手风琴（默认展开 概览+环境）；`产出`/`来源` 项可点击 → 切 `expanded` 全宽审阅；`提交或推送` 复用 `git-xywh` 流程且**必须二次确认**（对齐 Codex Full access 警告），只读 API 阶段先禁用/占位。
- **与 composer 打通**：把 `access_mode`（完全访问/受限）前置为 composer 的权限 chip，并加推理强度拨盘（provider 支持时）。

### 3.5 数据契约（v2 只读 API 草案，供后续 contract 细化）
`GET /api/sessions/{key}/workbench` →
```json
{ "updated_at": 0, "summary": "…",
  "env": { "project_path": "…", "project_name": "…", "access_mode": "full|restricted",
           "git": { "branch": "…", "remote": "…", "dirty": true, "base": "…" },
           "diff": { "files": 4, "added": 106, "deleted": 0 } },
  "skills_used": ["…"], "mcp_used": ["…"], "cli_used": ["…"],
  "outputs": [{ "path": "…", "kind": "file|image|doc" }],
  "sources": [{ "title": "…", "url": "…", "host": "…", "favicon": "…" }] }
```
只读、无副作用；写操作（提交/推送）单独走既有受控命令，不进此接口。

---

## 4. 分阶段落地（对应「一步步来」）

- **阶段 0 · 视觉基线**（Layer A，纯样式）：顶栏 tab 条 + 正文列节奏 + composer 权限 chip。风险低、可独立验收。
- **阶段 1 · 对话展现**（Layer B）：Worked-for 打磨 + 查看详情 + 失败态 + 答案排版 + 行内引用角标。
- **阶段 2 · 工作台 v1**（Layer C，前端派生）：`SessionWorkbench` 右栏（概览/环境/技能MCP/产出/来源）+ 全宽 diff 审阅 + 吸收 `FilePreviewPanel`。
- **阶段 3 · 工作台 v2**（后端只读 API）：git 环境 + 工作区 diff 汇总 + 统一来源；`提交或推送` 受控接线。

每阶段一个 spec→plan→实现→尾盘循环；阶段间可暂停回归确认。

## 5. 涉及文件（预估，非实现）
- 改：`App.tsx`（tab 条/右栏挂载）、`ThreadShell.tsx`（右栏槽位、FilePreview 合并）、`AgentActivityCluster.tsx` 及 `activity/*`（查看详情/错误态）、`MarkdownText.tsx`（答案版式/角标）、`ThreadComposer`（权限 chip/推理拨盘）、`Sidebar.tsx`（间距）。
- 新：`components/workbench/SessionWorkbench.tsx` + 分区子组件 + `workbench-model` 派生选择器；阶段 3 新增 `nanobot` 侧只读 handler。
- 复用：`DiffPair`、`FileEditRow`、`WebSearchRun`、`workspaceScope`、`fileEdits`、`toolEvents`。

## 6. 风险与约束
- **不与已提交视觉重塑冲突**：`30a585c`（Hope 色板/圆角/去玻璃）为基线，本方案在其上做布局/信息架构，不重开配色。
- **桌面壳**：`window.mira` 桌面检测下顶栏/侧栏行为需回归（见项目记忆）。
- **后端边界**：v1 严格前端派生，不阻塞；v2 才加只读 API，遵守 `.agent/security.md` 与只读无副作用原则。
- **多分屏性能**：每 pane 各自右栏的数据聚合需 memo，避免 N 格重复扫描。

## 7. Spec 自检
- 无 TBD/占位；三层与四决策一一覆盖；分阶段与「一步步来」一致；数据边界（v1 前端/v2 只读 API）明确；写操作受控。

## Next

**（已写入 spec，暂停等确认 —— 见 `harness-kit/core/routing.md` § 阶段门禁）**

- 认可方案 → 说「写计划 / 制定实施计划」（我按阶段 0 起出 `writing-plans` 计划）
- 想先做某一层 → 指定「先做阶段 2 工作台」等
- 需要调整 → 直接给修改意见（如右栏分区增删、tab 条是否要、提交动作是否本轮纳入）

## 8. 证据修订（2026-10-09 二轮，用户提供 Codex 桌面端一手截图 ×4）

用户实机截图（主界面 / 账号菜单 / 设置-常规 / 设置-语音）证伪并修正两条：

1. **Layer A-1 顶部 tab 条语义修正**：Codex 主界面**没有**全局会话 tab 条；顶栏只在「聊天与内容被同时打开在标签页栏」时出现（设置项「默认采用完整视图：开始新任务时，在同一标签页栏中显示聊天和内容」+ 右上角 ⊞ 分屏入口佐证）。此前"你好/Curie/+"截图是**已打开多个会话**的状态。
   → 实现修正：tab 条只渲染**显式分组**（`explicit` 或多 pane 的 workbench tab），无分组时整条隐藏；不再把全部会话水平复制一遍（第一轮实现踩坑，侧栏已有全量列表）。
2. **Layer A-4 推理档数据源确认（撤销"暂缓"）**：`SettingsPayload.model_presets[].reasoning_effort`（low/medium/high/xhigh/max/adaptive/none）已随 settings 下发到前端，此前"缺 per-session 数据源"判断有误。
   → 实现修正：composer 模型 pill 名称后内联档位（Codex `GPT-6.1 Sol 轻度` 同款节奏），模型切换下拉每项同步显示；zh-CN 文案 低/中/高/超高/极限/自适应，其余语言回退英文原值；`none` 与空值不显示。
3. 佐证不受影响的条目：composer 权限 chip（`⚠ 完全访问` 橙色，位于 `+` 右侧底行）与 Mira 现状一致；Codex composer 顶部上下文条（`📁 项目 | 💻 此计算机 | ⚙`）对应 Mira 的「选择项目」行 + 阶段0 底部状态栏（Qoder 式），位置差异保留 Qoder 方案。
4. **Layer A-3 正文列节奏落地口径**：居中列宽维持现有已统一的 `max-w-[49.5rem]`（消息列 / composer / 通知条同宽，改 46rem 会破坏对齐且收益微小）；「向 Codex 留白靠拢」通过垂直节奏一档放宽实现：消息单元间距 `mt-5/mt-4/mt-2 → mt-6/mt-5/mt-3`，日期分隔线 `my-5 → my-6`（纯 class 层，不动数据流）。
