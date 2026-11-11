---
artifact: interface-contract
route: superpowers:writing-plans
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/api-and-interface-design/SKILL.md
source:
  - webui/src/lib/api.ts
  - webui/src/lib/knowledge-eval-api.ts
  - webui/src/components/workspace/knowledge/GraphPanel.tsx
created_at: 2026-11-11
status: proposed
---

# 知识工作区体验改版：并行边界契约

## 1. 范围与数据真源

本轮只调整 `#/knowledge`、`#/knowledge-eval` 及其内部详情。维持这两个外部路由和现有鉴权；不要求修改全局导航、聊天、设置、后端或数据库。界面里的数据一律来自现有读模型；示意图里的最近更新聚合、固定 K、题集版本名不作为已有接口能力。

| 领域 | 真源与约定 |
| --- | --- |
| 知识库读写 | `lib/api.ts`，继续用 token 读取、WebUIMutationTransport 修改；scope 原样传递 |
| 文档列表 | `fetchKnowledgeDocs(token, {kbId,status,limit,offset,...scope})`，现有 offset/limit 分页；禁止把当前页过滤描述为全库内容搜索 |
| 文档详情 | `fetchKnowledgeDoc` 的 document/chunks/chunkCount；列表来源仅 originalName/mime，不暴露服务端路径 |
| 评测 | `knowledge-eval-api.ts`，继续复用 evaluationTransport，不重写鉴权、幂等、CSRF |
| 指标 | 五项主指标全部保留；poolCoverage/unscoredRate 保留辅助入口；公式、方向及比较值由后端决定，null 不转 0 |
| 评测行身份 | 使用完整复合 caseId（查询×配置）作为行 key；显示查询原文，不能把不同配置合并为一题 |
| 评分来源 | expect/judge 分组，不跨组聚合或比较；降级、失败、未评分分别呈现 |
| 图谱 | GraphPanelProps 和 onOpenChunk(docId,chunkId) 保持兼容；不更换布局引擎或提高节点上限 |

## 2. 页面内组件接口

以下是拟新增共享契约，实施时放入 `webui/src/components/workspace/knowledge-shared/experience-types.ts`：

```ts
import type { ReactNode } from "react";

export interface KnowledgePreviewProps {
  title: string;
  summary?: string;
  meta?: ReactNode;
  children: ReactNode;
}

export type KnowledgeTone = "neutral" | "pending" | "success" | "error";

export interface KnowledgeStatusProps {
  label: string;
  tone: KnowledgeTone;
  busy?: boolean;
}
```

KnowledgePreview 是非交互的补充摘要：用现有 Tooltip 组合，悬停/焦点可触发、Esc 可关闭，指针进入摘要不消失；信息在点击后的详情也能找到。需要复制或操作的内容使用现有 Popover 或 Sheet，不往 tooltip 塞按钮。不得为长名称、评分理由等唯一信息只提供 hover。

两个页面根节点添加 `knowledge-experience` 类，所有视觉与 motion 规则限定在该类内。浮层通过调用点添加 `knowledge-experience-surface` 类；不得修改共享 Sheet/Tooltip 默认行为来影响其他页面。CSS token 在 `knowledge-shared/experience.css` 唯一定义。

页面容器保留请求、scope、mutation、轮询所有权；展示组件用受控 props 回调请求操作，不能重复轮询。评测报告可以通过完整 caseId 打开单题 Sheet；显示 run 名时优先用题集名+时间，ID 为可复制辅助信息，不新增后端 name 字段。

## 3. 异步、分页与错误

- 初次加载可用骨架；后台刷新保留已展示数据、滚动、选中项和筛选。错误显示在对应区域，并有重试操作。
- 用户切换库/文档/运行后，旧请求结果不得覆盖当前选择；使用请求序号或取消标志。关闭弹层不取消服务端已接受的任务。
- 上传为「读取文件 → 提交 → 排队/处理 → 就绪/失败」；没有上传字节进度接口就使用阶段文案和不确定进度，不绘制虚假百分比。
- 评测只展示 progress 中确实存在且含义明确的计数；缺字段时只显示排队/运行中。3 秒轮询沿用当前逻辑，终态停止，不因动画重复发请求。
- 保持现有错误映射；题集更新 409 时保留本地草稿，提示刷新后核对，禁止静默覆盖。
- 删除/重建/修复继续使用当前确认和服务端回读。不能为了退出动画先从数据集中永久删除。
- 文档默认每页 20 条，走现有服务端分页；名称筛选若只作用于已加载页，明确写「筛选本页」。题集与运行当前完整返回，可在前端搜索/排序/每页20条；不伪造服务器分页参数。

## 4. 并行写入边界

- WU-01 独占 knowledge-shared，冻结后供两页消费。
- WU-02 独占 KnowledgePage.tsx、knowledge/ 新拆分组件及 knowledge-page.test.tsx。
- WU-03 独占 KnowledgeEvalPage.tsx、knowledge-eval/ 及 knowledge-eval-page.test.tsx。
- WU-04 在 WU-02 完成后独占 knowledge/GraphPanel.tsx、GraphView.tsx 与对应图谱测试；WU-02 不修改这两文件。
- WU-05 在页面合并后独占 locales、必要的共享样式修正和交互测试。
- 共享 UI 基础组件、lib/api.ts、knowledge-eval-api.ts、workspace/routes.ts 原则上不写。确需变更时先由 Leader 调整本契约并转交单一 owner。

## 5. 本契约的验证

静态对照现有接口类型；并行前检查导出一致。合并后用行为测试验证 scope、旧响应守卫、未评分、分组与焦点恢复。此次仅写契约，没有执行上述未来实现检查。
