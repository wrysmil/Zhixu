---
artifact: spec
route: writing-plans -> api-and-interface-design
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.12/skills/api-and-interface-design/SKILL.md
source:
  - 用户要求：接入现有 WebUI、静态页面、不改后端、整体重构聊天和设置风格
  - webui/src/App.tsx
  - webui/src/main.tsx
  - webui/src/components/settings/contracts.ts
created_at: 2026-10-10
status: proposed
approved: false
---

# 知序 WebUI 静态整合：前端边界契约

## 1. 两种运行入口

- 正常入口：保留已有鉴权、ClientProvider、会话、设置请求和操作；本期只改变表现层，新增工作台与创作页面使用示例数据。
- 预览入口：`?preview=1#/home`，在 `main.tsx` 挂载真实 App 之前分流至 `PreviewApp`。不初始化 loopback runtime、不注册 service worker、不挂载 ClientProvider、ThreadShell、SettingsView 或其他会发起业务请求的容器。
- 预览可以加载同源 JS/CSS、语言包和静态资源；不得请求 `/api`、`/auth`、`/v1`，不得创建 WebSocket 或调用 Electron 宿主业务桥。
- 演示内容标注「示例内容」或「演示建议」；不显示虚假的连接成功、保存到服务器或真实 AI 生成状态。
- 预览状态在内存中维持，刷新重置；正常入口的文章示例状态也仅维持本次访问。原有主题、语言、聊天与设置存储保持既有行为。

## 2. 文件与类型所有权

基础契约由 WU-01 创建，其余 WU 只导入，不并行修改。

`webui/src/components/workspace/contracts.ts`：

```ts
import type { ReactNode } from "react";
import type { SettingsSectionKey } from "@/components/settings/contracts";

export type WorkspaceView =
  | "home" | "chat" | "creative" | "article"
  | "agents" | "skills" | "automations" | "apps" | "settings";

export interface WorkspaceRoute {
  view: WorkspaceView;
  articleId?: string;
  chatKey?: string;
  settingsSection?: SettingsSectionKey;
}

export interface WorkspaceNavigationProps {
  activeView: WorkspaceView;
  collapsed: boolean;
  preview: boolean;
  onNavigate: (route: WorkspaceRoute) => void;
  onToggleCollapsed: () => void;
  chatNavigation?: ReactNode;
  connectionStatus?: ReactNode;
}

export interface WorkspacePageProps {
  preview: boolean;
  onNavigate: (route: WorkspaceRoute) => void;
}
```

`WorkspaceNavigation` 只渲染入口及调用回调，不获取数据。聊天列表由正常入口注入既有 ChatList，预览注入示例列表。非聊天页面不常驻聊天历史。

`webui/src/components/thread/ThreadFrame.tsx`：

```ts
import type { ReactNode } from "react";

export interface ThreadFrameProps {
  header: ReactNode;
  children: ReactNode;
  composer: ReactNode;
  details?: ReactNode;
}
```

纯布局，无 hook 访问运行时；ThreadShell 继续拥有流式、历史、错误恢复、文件预览和会话逻辑。PreviewThread 用 ThreadFrame 和无请求的现有消息组件构成示例。

`SettingsSectionFrame`：`{ title: string; description?: string; actions?: ReactNode; children: ReactNode }`，只渲染标题、说明、操作区、正文。正常 SettingsPage 和 PreviewSettings 共同使用；PreviewSettings 不伪造 SettingsController。

## 3. 文章边界

文章模块只在 `webui/src/components/creative/` 内管理状态。对外提供 `CreativeWorkspace({ route, onNavigate, preview })`，其中 route 为 WorkspaceRoute，其余同 WorkspacePageProps。工作台打开文章只发送 `{ view: "article", articleId }`。

文章模型和版本逻辑只由 WU-05 创建，首页不复制文章 store。首页使用固定示例继续项，注明示例；实际读写文章由 CreativeWorkspace 自己处理。不存在的 articleId 显示「示例文章不存在」并提供返回列表。

`ArticleStatus = "idea" | "draft" | "review" | "done"`；`DemoArticle` 包含 `id/title/status/body/outline/materials/revision`；`DemoSuggestion` 包含 `articleId/baseRevision/before/after/target`；target 为 `{ kind: "body"; start: number; end: number } | { kind: "outline" }`。

修改正文、提纲或影响建议的素材时增加 revision。建议只在 articleId 与 baseRevision 都匹配时可采用；采用后仅在 revision 未再次变化时可撤销。选区 start/end 按 JavaScript 字符串索引保存。AI 操作使用确定性的本地建议，不能调用现有真实发送函数。

## 4. 路由兼容

- 正常空 hash 和 `#/` 展示新工作台；`#/new` 继续新建通用对话。
- 新增 `#/home`、`#/creative`、`#/article/<编码后的 id>`。
- 保留 `#/chat/<key>`、`#/temporary/<id>`、`#/settings?section=...`、`#/apps`、`#/skills`、`#/agents`、`#/automations` 及现有查询参数意义。
- 预览导航保持 `?preview=1`，不能跳回 live App；浏览器前进后退更新当前页面。
- 未识别路径回工作台；非法编码不得抛异常。旧 restart route 恢复与 chat 参数保留行为需回归验证。
- 不新装路由库；复用当前 hash/history 模式。纯解析辅助函数提取至 `workspace/routes.ts`，App 的 restart 副作用不转入纯解析函数。

## 5. 设置与能力覆盖

设置合法 key 维持现有 15 项，不改协议：overview、appearance、models、image、voice、browser、channels、apps、automations、skills、agents、memory、identity、runtime、advanced。

全局直接入口：工作台、助手、创作空间、智能体、技能、自动化、工具与连接、设置。apps 在界面称「工具与连接」，路径及后端字段保持不变。

预览各设置页使用既有字段的静态代表布局；保存按钮仅更新本地示例并提示「演示设置，仅本次预览有效」。OAuth、安装、重启、渠道连接等外部动作在预览禁用并有原因说明。示例密钥字段不接收真实密钥。正常入口所有既有动作、校验及错误状态继续由原 controller 驱动。

## Next

本契约与对应 plan 一起评审；用户说「开始实现」后按 dispatch 执行。没有新增后端端点、协议、数据库或迁移。
