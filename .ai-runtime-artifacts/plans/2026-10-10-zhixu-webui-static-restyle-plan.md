---
artifact: implementation-plan
route: writing-plans -> api-and-interface-design
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.12/skills/api-and-interface-design/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-10-10-zhixu-webui-static-restyle-dispatch.md
source:
  - 用户本轮指令：先接入现有 WebUI，静态页面、不改后端，整体重构设置及聊天页面，写计划
  - 用户上轮约束：通用助手不能丢失 Skill、智能体等能力
  - .ai-runtime-artifacts/specs/2026-10-10-zhixu-workspace-spec.md
  - .ai-runtime-artifacts/research/zhixu-prototype/index.html
  - .ai-runtime-artifacts/stack/2026-10-10-zhixu-workspace-stack.md
  - .ai-runtime-artifacts/contracts/2026-10-10-contract-zhixu-webui-static.md
  - webui/src/App.tsx
  - webui/src/components/settings/SettingsPage.tsx
  - harness-kit/core/routing.md
created_at: 2026-10-10
status: approved
approved: true
approved_quote: "按照这个计划执行任务，不用worktree"（2026-10-10 本会话；worktree 豁免：全部 WU 在主 checkout 实施）
tier: 2
---

# 知序 WebUI 静态接入与整站风格重构实施计划

> 执行者必须按项目 Harness orchestration 流程逐项实施，使用本计划同 stem 的 dispatch 进行编排。当前仅计划，不派 worker、不改业务代码。所有截图、执行日志、验证和审查记录放 `.ai-runtime-artifacts/` 对应目录。

**Goal：** 在现有 React WebUI 内实现知序的整体视觉与页面结构，完成工作台、静态文章创作、聊天、设置和能力管理的统一体验，同时保留通用助手已有能力。

**Architecture：** 演示 HTML 作为视觉与交互参考，迁移为 React 组件；沿用现有主题 token、Radix、hash 路由与业务 controller。正常入口保留既有业务连接，新增文章功能只用内存示例；另在同一 WebUI 提供无后端的预览入口，复用相同导航、布局和基础组件，不再做独立 HTML 或 iframe 页面。

**Tech Stack：** React 18.3.1、TypeScript（声明 ^5.7.2，已记录安装 5.9.3）、Vite（声明 ^5.4.11，已记录安装 5.4.21）、Tailwind 3、Radix、lucide-react、Vitest、Testing Library。实施前重新核对锁文件及安装版本；不升级框架、不增加编辑器或状态管理依赖。

## 1. 本期范围与解释

本期的「静态」指新增工作台/写作页面及整站预览采用本地示例，不接真实文章服务或 AI。已运行的聊天和设置只改表现层，不替换成失去功能的假页面。用户可以直接看静态整站效果，也可以在原入口继续用已有功能。

| 范围 | 本期做法 |
| --- | --- |
| 工作台 | 新页面，示例最近文章/对话、通用任务输入、完整能力入口 |
| 助手 | 重构聊天视觉、空态、标题、输入框、消息与任务详情布局；保留真实对话逻辑 |
| 创作空间 | React 静态文章列表与编辑区，素材/提纲/正文/建议，内存状态与 Markdown 导出 |
| 智能体/技能/自动化/工具与连接 | 保留一级入口及已有管理功能，统一列表、搜索、编辑与确认弹窗 |
| 设置 | 全部现有 15 个 section 的页面外观与分组统一，不删除字段或改变保存逻辑 |
| 全站预览 | `?preview=1#/home` 可离线于后端演示所有页面，显式示例标识，无业务请求 |
| 深色/小窗口 | 保留深色主题；重点交付暖白浅色，同时验收深色可读性及 390/1024/1440 宽度 |
| 后端与数据 | 无 Python、API、WS 协议、配置 schema、身份提示词、数据库、数据迁移改动 |

不改 `nanobot/`、`tui/`、`desktop/`、`packages/client-events/`；不统一重命名 Python 包或覆写已有对话历史。桌面壳只验收现有 HostChrome 在新 WebUI 下的布局。渠道插件位于 Python 目录中的前端文件也不编辑，先通过共享 token 与容器统一风格。

## 2. 视觉与页面规范

- 浅色：背景 `#F7F6F2`，侧栏 `#F0EFE9`，纸面 `#FFFEFB`，正文 `#292D29`，次级文字 `#62685F`，分隔 `#E3E3DB`，强调 `#3F6B58`。转成现有 HSL token；不在各页面重复硬编码色值。
- 全局正文与控件使用现有中文无衬线字体栈；不复制原型的衬线字体到整个界面，不依赖远程字体。代码继续等宽。
- 控件约 8px、面板约 12px、输入主区约 16px 圆角；不把列表、所有按钮变成胶囊。
- 全局导航展开目标 224px，收起 64px；助手上下文列表单独呈现，非助手页不保留聊天列表。小屏使用抽屉，不叠三条侧栏。
- 页面标题 28–32px、UI 正文 14px、文章正文 16–17px；普通内容 960–1120px 上限，文章阅读列约 720px，聊天正文约 800px。多面板会话按实际可用宽度自适应，不强制套文章宽度。
- 设置采用「清晰标题 + 分组说明 + 对齐字段 + 明确操作区」，保留 loading、error、dirty、saving、需重启提示。overview 有价值的使用统计可以折叠，不删除。
- 全站同一套按钮、输入、标签、搜索、空态、错误、弹窗、focus 样式。破坏性动作仍用红色及确认；不是所有状态都改绿色。
- 深色使用低饱和暖深灰纸面与适配的绿色强调；正文、次级文本与焦点必须实际测对比度。普通文本目标至少 4.5:1。

## 3. 页面及能力保留矩阵

| 页面/模块 | 需要变化 | 必须保留的行为 |
| --- | --- | --- |
| 全局导航 | 从图标 rail 改文字导航、完整能力分组 | 折叠、移动端、路由、连接提示、桌面标题栏 |
| 对话列表 | 按助手上下文显示，更轻的列表 | 搜索、项目、置顶、归档、临时聊天、重命名、删除、多面板入口 |
| 聊天 | 暖白画布、正文排版、统一输入和标题 | 发送/停止、流式、附件、模型切换、Skill 提示、智能体入口、引用、错误恢复 |
| 任务详情 | 同风格按需显示，避免工程信息常驻 | 工具状态、子智能体、文件预览、差异、运行信息；需用户响应的卡片仍显眼 |
| 设置 | 分组导航、统一字段/搜索/操作栏 | 15 个 section 路由、全部字段校验与保存、OAuth、重启/错误提示 |
| 能力管理 | 同一套列表、卡片、编辑弹窗 | Skill 安装启停、智能体工具/技能/子智能体配置、自动化管理、MCP/CLI 连接 |
| 记忆与身份 | 文档/列表/详情统一密度 | CRUD、详情、已有开关和 persona 功能 |
| 创作 | 原型搬入 React，三栏可折叠 | 模板、新建/复制/删除、筛选、素材、提纲、编辑/预览、建议采用保护、导出 |

设置导航分组：个人（appearance/memory/identity）；AI 能力（models/image/voice/browser）；连接（channels/apps）；系统（runtime/advanced）；overview 保留总览。agents/skills/automations 为全局独立能力页，旧 settings section 别名继续可访问。

## 4. 文件结构与所有权

| 单元 | 新增文件 | 主要修改文件 |
| --- | --- | --- |
| WU-01 基础 | `components/workspace/contracts.ts`、`workspace/routes.ts`、`workspace/WorkspaceNavigation.tsx`、`workspace/PageHeading.tsx`、`workspace/EmptyState.tsx`、`thread/ThreadFrame.tsx`、`settings/shared/SettingsSectionFrame.tsx` | `globals.css`、`tailwind.config.js`、共享 `components/ui/` |
| WU-02 Shell | `components/workspace/WorkspaceHome.tsx`、`preview/PreviewApp.tsx`、`preview/preview-entry.ts`、`preview/fixtures.ts` | `App.tsx`、`main.tsx`、`Sidebar.tsx`、`ChatList.tsx`、`i18n/locales/*/common.json` |
| WU-03 聊天 | `preview/PreviewThread.tsx` | `thread/ThreadShell.tsx`、`ThreadHeader.tsx`、`ThreadViewport.tsx`、`ThreadMessages.tsx`、`ThreadComposer.tsx`、`ThreadStatusBar.tsx`、`MessageBubble.tsx`、`workbench/SessionWorkbench.tsx`、`WorkbenchTabBar.tsx`、`PaneWorkbench.tsx` |
| WU-04 设置与能力 | `preview/PreviewSettings.tsx`、`preview/PreviewCapabilities.tsx` | `settings/SettingsPage.tsx`、`SettingsSidebar.tsx`、`shared/SettingsControls.tsx`、settings 各领域展示组件及编辑弹窗 |
| WU-05 创作 | `creative/types.ts`、`demo-data.ts`、`article-model.ts`、`CreativeWorkspace.tsx`、`ArticleList.tsx`、`ArticleEditor.tsx`、`MaterialPanel.tsx`、`OutlinePanel.tsx`、`SuggestionPanel.tsx` | 无共享文件写权限；接线由 WU-02 负责 |
| WU-06 尾盘 | 有行为变化的 `src/tests/` 测试与验证/审查记录 | 汇总后的冲突修复、遗漏样式和预览接线 |

表内 `components/`、`preview/`、`workspace/`、`creative/` 路径均相对 `webui/src/`；例如 `creative/ArticleEditor.tsx` 的实际位置是 `webui/src/components/creative/ArticleEditor.tsx`，`preview/PreviewApp.tsx` 是 `webui/src/preview/PreviewApp.tsx`。测试的精确路径列于下方 Task。

WU-04 可调整的展示文件相对 `webui/src/components/settings/`，覆盖现有 `overview/OverviewSettings.tsx`、`models/ModelsSettings.tsx`、`models/ProviderSettings.tsx`、`capabilities/{ImageGenerationSettings,TranscriptionSettings,WebSettings,SecuritySettings}.tsx`、`system/{AppsSettings,AutomationsSettings,ChannelsSettings,RuntimeSettings,McpManagementDialog}.tsx`、`channels/{CredentialForm,ChannelSetupParts,ChannelSetupPanel,ChannelQrConnectFlow,ChannelInstancesPanel,ChannelIdentity}.tsx`、`memory/{MemorySection,MemoryListView,MemoryEditDialog,EpisodeListView,EpisodeDetailPanel}.tsx`、`identity/IdentityView.tsx`、`SkillsCatalogSettings.tsx`、`SkillsMarketplace.tsx`、`agents/{AgentsView,AgentCard,AgentRow,AgentTreeView,AgentEditorDialog,shared}.tsx` 及 `agents/parts/` 已有展示组件。不写 `useSettingsController` 和各领域请求/动作 hook。

## 5. 实施 Task（勾选步骤逐项执行）

### Task 0：记录基线与约束

**文件：** 读取现有 spec、prototype、contract、`harness-kit/project.verification.md`、`webui/package.json`；新建 `.ai-runtime-artifacts/execution-logs/2026-10-10-zhixu-webui-static-restyle-execution-log.md`。

- [ ] 记录实际分支、工作区已有改动、依赖版本与可用浏览器工具；保存当前聊天/设置/技能/智能体截图。已有未跟踪原型与用户文件不清理。
- [ ] 在用户批准之后按 Harness 创建隔离 worktree，随后所有生产修改和 worker 都使用其返回的 workspace 路径；本轮计划阶段不创建。
- [ ] 在该 worktree 的 `webui/` 运行 `bun run test`、`bun run build`、`bun run lint`，记录退出码及基线失败。基线失败作为失败，不降级验收要求。
- [ ] 读取下方契约并冻结共享文件所有权；登记执行日志中的 WU 状态。业务实现前核对 React/Radix 相关官方文档；版本与既有实现决定具体 API 用法。

### Task 1 / WU-01：统一 token、基础组件与共享契约

**文件：** 使用第4节 WU-01 清单；重点修改 `webui/src/components/ui/{button,input,textarea,dialog,alert-dialog,dropdown-menu}.tsx` 与 `webui/src/components/ui/form-control.ts`。测试：`webui/src/tests/ui-shape-system.test.tsx`、`form-controls.test.tsx`、新增 `workspace-routes.test.ts`。

- [ ] 先按 contract 创建类型与无请求的共享布局。保留现有 props；Button 的 default/secondary/destructive 等变体意义不变。
- [ ] 修改 token 与圆角阶梯，清理基础组件覆盖旧蓝色/灰色的硬编码，使输入、popover、dialog 和焦点跟随主题。
- [ ] 路由解析先写有意义的测试，运行看到失败，再实现。至少覆盖旧聊天、临时聊天、settings 别名、文章、非法 URI 和浏览器 query 不混入 hash。

```ts
// webui/src/tests/workspace-routes.test.ts 的首个新增用例
import { describe, expect, it } from "vitest";
import { parseWorkspaceHash } from "@/workspace/routes";

describe("知序路由兼容", () => {
  it("保留已有聊天 key", () => {
    expect(parseWorkspaceHash("#/chat/websocket%3Aabc"))
      .toMatchObject({ view: "chat", chatKey: "websocket:abc" });
  });
  it("识别文章并容忍非法编码", () => {
    expect(parseWorkspaceHash("#/article/demo-1"))
      .toMatchObject({ view: "article", articleId: "demo-1" });
    expect(parseWorkspaceHash("#/article/%"))
      .toMatchObject({ view: "home" });
  });
});
```

- [ ] 运行 `bun run test -- src/tests/workspace-routes.test.ts src/tests/ui-shape-system.test.tsx src/tests/form-controls.test.tsx`；预期退出0。纯视觉尺寸断言若与已批准新设计冲突，更新目标，但不删除行为断言。
- [ ] 在浅色/深色检查普通文本、次级文本、focus、disabled/error，记录实际对比度。完成后冻结共享基础供其他 WU 使用。

### Task 2 / WU-02：接入工作台、文字导航与静态预览入口

**文件：** 第4节 WU-02 清单；新增 `webui/src/tests/workspace-navigation.test.tsx`、`preview-entry.test.tsx`；更新 `app-layout.test.tsx`、`main-pwa-registration.test.tsx`、`main-randomuuid.test.tsx`、`i18n.test.tsx`。

- [ ] 从 Sidebar 的聊天列表区域提取可复用呈现，使用 WorkspaceNavigation 的 chatNavigation slot；保持 ChatList 原回调与项目/多面板功能。全局入口中文名称完整出现。
- [ ] 空 hash 默认工作台，`#/new` 保持通用对话，新视图分支追加至 App。restart route 与 activeKey 恢复继续用现有逻辑。
- [ ] 新建 WorkspaceHome：一句自由任务输入、示例继续项、创作入口及能力入口。普通输入在正常入口带入通用助手草稿，必须点击发送才提交；预览只产生示例会话，不调 sendMessage。
- [ ] main 启动时先判定 preview，再动态导入对应根组件；把 runtime 初始化与 service worker 注册限制在正常入口。预览先选入口再加载 App，不能在 App 请求失败后才回退。

```ts
// webui/src/preview/preview-entry.ts
export function isPreviewEntry(search: string): boolean {
  return new URLSearchParams(search).get("preview") === "1";
}
// main.tsx 使用规则：isPreviewEntry(window.location.search)
// 为 true 时加载 PreviewApp；为 false 时沿用 App 与现有 runtime 启动。
```

- [ ] PreviewApp 用内存状态与统一导航，保留 preview query，处理 hashchange；将 chat/settings/capabilities/creative 的页面分别接入其他 WU 的交付组件。全局显式「界面预览 · 示例内容」。
- [ ] 所有新增标签加入语言包，遵循当前十种语言 key 一致性测试；中文内容为验收重点，不删除其他语言。
- [ ] 首个预览隔离测试把业务 fetch/WebSocket/runtime bridge 设为抛错，允许静态语言资源；验证入口仍正常渲染。`bun run test -- src/tests/preview-entry.test.tsx src/tests/workspace-navigation.test.tsx src/tests/app-layout.test.tsx src/tests/i18n.test.tsx` 退出0。

### Task 3 / WU-03：聊天页面整体验证与样式重构

**文件：** 第4节 WU-03 清单；测试：现有 `thread-shell.test.tsx`、`thread-composer.test.tsx`、`thread-messages.test.tsx`、`thread-status-bar.test.tsx`、`agent-activity-cluster.test.tsx`、`composer-plus-menu.test.tsx`，新增 `preview-thread.test.tsx`。

- [ ] ThreadShell 只把布局移入 ThreadFrame，继续拥有所有历史和流式 hook；调整标题、阅读列、底部输入、任务详情的样式和响应式，不重写事件投影。
- [ ] 消息内容延续现有 MarkdownText/MessageBubble；用户消息、助手正文、代码块、引用、附件、reasoning/工具状态各自层次清晰。代码、图片、表格有独立溢出处理。
- [ ] 输入框复用 ThreadComposer，保留模型/附件/Skill 补全、智能体相关入口、发送/停止、键盘与权限状态；改为暖白纸面与统一绿色主动作。不得以更简洁为由删除工具入口。
- [ ] 任务详情默认按现有状态逻辑打开或关闭，只优化视觉占位；AskQuestionCard、错误恢复、需确认动作仍直接可见。多面板窄宽度不被固定 max-width 压坏。
- [ ] PreviewThread 用 ThreadFrame 和纯消息呈现，内存中的输入发送追加用户与明确的固定演示回复，展示可折叠示例工具/智能体过程。不得挂载 ThreadShell。
- [ ] 针对实际新增的布局切换/预览发送写行为测试；对仅颜色和间距变化用浏览器验收，不写镜像实现的测试。
- [ ] 运行 `bun run test -- src/tests/thread-shell.test.tsx src/tests/thread-composer.test.tsx src/tests/thread-messages.test.tsx src/tests/thread-status-bar.test.tsx src/tests/agent-activity-cluster.test.tsx src/tests/composer-plus-menu.test.tsx src/tests/preview-thread.test.tsx`，预期退出0。

### Task 4 / WU-04：设置与全部能力页统一风格

**文件：** 第4节 WU-04 清单；测试：`settings-overview.test.tsx`、`settings-providers.test.tsx`、`settings-models.test.tsx`、`settings-system.test.tsx`、`settings-capabilities.test.tsx`、`settings-channels.test.tsx`、`settings-memory-section.test.tsx`、`agents-view-integration.test.tsx`、`skills-marketplace.test.tsx`、新增 `preview-settings.test.tsx`。

- [ ] 重排 SettingsSidebar 展示分组，section key、selectSection 和 deep link 不变；用 SettingsSectionFrame 统一标题、描述、正文宽度与动作栏。
- [ ] 统一 SettingsGroup/SettingsRow 与搜索、开关、下拉、说明、错误提示，继而逐一调整所有15项 section。复杂表单保留足够密度，不用大留白牺牲可操作性。
- [ ] 能力页使用同套列表/卡片、状态标签和编辑弹窗；智能体 prompt/model/tools/skills/subAgents、技能市场、自动化编辑、MCP、记忆详情、身份文档编辑都进入检查清单。
- [ ] 原 controller/actions/provider 请求/保存协议不改。检查 style 修改未破坏 disabled、loading、dirty、error、OAuth、需重启、隐藏/删除等状态。
- [ ] PreviewSettings 覆盖11个普通设置项；PreviewCapabilities 覆盖 apps/agents/skills/automations 共4项。采用相同 SettingsSectionFrame、SettingsControls、基础表单和导航；用内存示例字段，包含代表性详情/弹窗，不用一个空白设置弹窗代替全站。
- [ ] 预览模型密钥展示只读示例；安装、OAuth、重启、连接禁用并说明是预览；本地演示保存有准确反馈，不写真实配置。
- [ ] 运行 `bun run test -- src/tests/settings-overview.test.tsx src/tests/settings-providers.test.tsx src/tests/settings-models.test.tsx src/tests/settings-system.test.tsx src/tests/settings-capabilities.test.tsx src/tests/settings-channels.test.tsx src/tests/settings-memory-section.test.tsx src/tests/agents-view-integration.test.tsx src/tests/skills-marketplace.test.tsx src/tests/preview-settings.test.tsx`，预期退出0。

### Task 5 / WU-05：静态文章创作搬入 React

**文件：** 第4节 WU-05 清单；测试：新增 `webui/src/tests/article-model.test.ts`、`creative-workspace.test.tsx`。

- [ ] 使用 article-model 纯函数与局部 React state 管理示例文章；新建文章为空正文、构思中，可选技术解析/源码阅读/实践复盘/学习总结模板。空白主题显示错误，不新建。
- [ ] 文章列表支持标题/摘要搜索、状态过滤、复制、删除确认、空态与示例恢复。整个创作区统一状态所有权，切换列表/编辑页不丢草稿。
- [ ] 编辑区左素材/提纲、中正文、右AI建议；正文用 textarea 与已有 MarkdownText，默认不添加富文本库。小窗口支持侧栏折叠/切换，正文保持中心地位。
- [ ] 素材支持用户观点、文本、代码、链接；只把链接记录为素材，不抓网页。拒绝 javascript 等危险链接；渲染使用 React/现有 MarkdownText，不移植 HTML 字符串插入方式。
- [ ] 使用本地确定性建议；采用前显示差异，正文/提纲/素材版本变化后旧建议失效；撤销仅允许在采用后未再修改时执行。

```ts
// webui/src/components/creative/article-model.ts 的建议门控规则
export function canAcceptSuggestion(
  article: { id: string; revision: number },
  suggestion: { articleId: string; baseRevision: number },
): boolean {
  return article.id === suggestion.articleId
    && article.revision === suggestion.baseRevision;
}
// article-model.test.ts 必须覆盖不同文章、旧 revision 和匹配 revision 三种结果。
```

- [ ] 导出真实 Markdown 文件，清理文件名无效字符；界面提示「已发起下载」，刷新前提示本期内容仅在本次访问内保留。
- [ ] 测试覆盖新建校验、筛选空结果、切换保留草稿、过期建议拒绝、撤销冲突；运行 `bun run test -- src/tests/article-model.test.ts src/tests/creative-workspace.test.tsx`，预期退出0。

### Task 6 / WU-06：统一接线、浏览器验收与尾盘

**文件：** `webui/src/tests/preview-navigation.test.tsx`、前述失败测试的必要修复；过程产物使用第7节路径。

- [ ] Leader 合并 WU 后接线 PreviewApp/App，检查导航、标题、侧栏与各页面组件无不同版本；清理重复临时样式和旧品牌前端文案。只改用户可见 WebUI 品牌，不改包名、API、历史文本。
- [ ] 全量执行 `bun run test`、`bun run build`、`bun run lint`（cwd=`webui/`）；退出0是目标。完整记录基线失败与本次失败，不能以「大部分通过」作为完成。
- [ ] 在真实浏览器停用或不启动后端，打开 `?preview=1#/home`；点击所有8个全局入口、所有15项设置、文章列表与编辑器。核对没有 API/auth/v1/WS 请求，所有预览动作不改真实存储。
- [ ] 浅色1440×900记录工作台、助手、创作、编辑、智能体、技能、自动化、工具与连接、全部设置分区截图；1024×768和390×844检查核心流程，深色至少检查聊天、文章、设置与能力弹窗。
- [ ] 浏览器验证前进后退、刷新、键盘Tab/Escape、弹窗焦点恢复、抽屉、长代码/表格、状态/错误/空列表、不存在文章；无横向全页溢出。
- [ ] 正常入口使用已有可用环境回归真实聊天、停止、附件、Skill、智能体相关入口、会话与设置；不为本期新增后端。无可用后端时注明真实业务未实测，不能声称全功能实测通过；组件测试与静态预览完成证据分别列出。
- [ ] 检查 `git diff --name-only` 无 Python、TUI、desktop、协议、配置 schema 变更；构建生成的 `nanobot/web/dist` 为现有构建产物，不手改，不作为后端逻辑变更；避免把它混入源代码提交。
- [ ] 按 Harness 扇出独立代码审查与安全审查（重点：预览隔离、XSS、链接、示例凭据、原控制逻辑保留），修复发现项后只重复受影响检查；性能审查按新包体/渲染问题触发。
- [ ] 汇总 collective-test、code-review、security-review 与 execution-log，交付真实预览地址和截图。commit/push/MR 按后续用户指令及 Git规范执行，本计划不自动授权发布。

## 6. 依赖与完成标准

执行链：Task0 → WU-01 → WU-02基础接入 → WU-03/WU-04/WU-05并行 → WU-02统一接线 → WU-06尾盘。WU-02中间先交付能运行的导航/工作台，未完成视图不得显示为已完成交付。

最终完成须满足：

1. 同一现有 WebUI 中能直接进入新工作台与创作页面，全站风格覆盖聊天和设置，独立 HTML 不再是主要交付物。
2. 无后端时可以预览完整页面；业务网络隔离有测试与浏览器证据，示例/真实模式不混淆。
3. 智能体、技能、自动化、工具与连接直接可达；既有功能未因视觉改造删除。
4. 所有现有设置 section、输入/校验/保存状态存在；完整页面检查表无遗漏。
5. 文章只用示例和内存状态，无新增文章/AI请求；建议和撤销有版本保护。
6. 桌面/小窗口/深色/键盘操作通过验收；文字、focus 对比度达标。
7. WebUI测试、构建、lint通过；真实业务未实测的部分明确列出；独立审查发现项已处理。

## 7. 验证与审查产物

- `.ai-runtime-artifacts/execution-logs/2026-10-10-zhixu-webui-static-restyle-execution-log.md`
- `.ai-runtime-artifacts/verifications/2026-10-10-zhixu-webui-static-restyle-collective-test.md`
- `.ai-runtime-artifacts/reviews/2026-10-10-zhixu-webui-static-restyle-code-review.md`
- `.ai-runtime-artifacts/reviews/2026-10-10-zhixu-webui-static-restyle-security-review.md`
- `.ai-runtime-artifacts/research/zhixu-webui-static-restyle/`：页面截图、视口和浏览器证据。

## 8. 计划自检

- 范围覆盖：从独立原型扩展到现有 WebUI；聊天、设置、能力页、文章、主题与导航均有任务。
- 约束覆盖：通用定位、无后端改动、静态新增功能、可离线预览、旧功能/路由/保存保留。
- 契约一致：WorkspaceRoute、导航slot、ThreadFrame、SettingsSectionFrame及文章版本在对应contract定义；preview隔离在入口建立。
- 文件所有权：globals/ui由WU-01独占；App/main/Sidebar/locales/预览总接线由WU-02独占；其余WU不争抢共享文件。
- 测试策略：只为新路由、状态、预览隔离与文章版本行为新增测试；纯视觉使用实际浏览器证据。
- 阶段边界：本文件未授权立即实现；当前没有运行应用检查，不声称实现或构建通过。

## Next

- 认可范围和顺序：说「开始实现」，进入 Harness 编排实施。
- 要调整：直接指出页面范围、导航、预览方式或优先级。
- 本轮只写计划与接口契约，未修改 WebUI 或后端代码。
