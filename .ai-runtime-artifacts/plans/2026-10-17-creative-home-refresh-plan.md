---
artifact: plan
route: writing-plans
skills: [writing-plans, source-driven-development, using-git-worktrees]
skills_evidence:
  - .claude/skills/writing-plans/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - /Users/mima0000/.agents/skills/using-git-worktrees/SKILL.md
source:
  - 用户已审阅上一轮首页分析并要求「创建一个workTree然后修复吧」
  - webui/src/components/creative/ArticleList.tsx
  - .ai-runtime-artifacts/specs/2026-10-10-zhixu-writing-capability-spec.md
created_at: 2026-10-17
status: complete
approved: true
approval_evidence: 用户上一轮要求分析，本轮明确要求创建 worktree 然后修复；继续执行已讨论的首页方案。
dispatch: 2026-10-17-creative-home-refresh-dispatch.md
tier: 2
---

# 创作空间首页实施计划

**Goal:** 紧凑、清楚地承接创建草稿与继续写作，保持已有编辑器和文章接口。
**Architecture:** 仅调整 ArticleList 表现和首页交互、locale、行为测试和一张本地插画。不变更组件 Props、服务端接口与文章编辑器。
**Tech Stack:** React 18.3.1、TypeScript 5.9.3、Vite 5.4.21、Tailwind 3.4.19、Vitest 2.1.9（沿用既有安装，无新增依赖）。

## 工作区与基线

- worktree: /Users/mima0000/.codex/worktrees/creative-home-refresh/zhixu
- branch: codex/creative-home-refresh
- HEAD: 2d7513fecf29fa3f5157103d9f793278e3d04589
- 从原工作区复制全部前端未提交文件与四个未跟踪前端文件，原文件保留。
- 前端复制后、任务改动前 Git tree: d0c501ec07a735f621d9dfb3e865f86eda60934b；后续以此区分用户已有工作与本次改动。
- 复用原工作区 node_modules 的符号链接；仅启动 worktree 的独立 Vite 预览，不部署。

## 设计与边界

- 页首保持轻量欢迎语，小型纸艺插画仅装饰，移动端隐藏、深色适配。
- 创作表单：可见的文章标题标签、单行输入、Enter 提交（中文组合输入除外）、创建草稿/创建中状态，保留失败内容。
- 四个模板合入创建卡片，紧凑网格，显示已选/空白写作；选模板只选择且聚焦标题，仍然由创建按钮提交。
- 明确提示模板只初始化提纲、素材在编辑器中添加，不暗示首页已上传或生成。
- 文章列表紧接表单，保留搜索/筛选/复制/删除/打开与空态、加载、错误态；文章卡片减少无效高度，补清晰焦点。
- 首页中英完整本地化，其余语言补缺失键，保留其既有文本。
- 本轮不扩展分页后端或文件上传；50 条列表边界另记为已知限制，避免扩大首页视觉任务。

## Task 1: 回归与本地化（WU-02）

- [x] 先新增 ArticleList 独立行为测试：显式标题、Enter 创建、组合输入、模板选择、创建等待防重、错误保留，以及中英首页无硬编码串。
- [x] 运行新测试观察预期失败，再完善各 locale 的首页键。
- [x] 将旧交互测试中按 placeholder 定位标题改为按可访问标题标签定位，保留原有断言含义。

```tsx
fireEvent.change(screen.getByRole("textbox", { name: "文章标题" }), { target: { value: "新的理解" } });
fireEvent.submit(screen.getByTestId("create-article-form"));
expect(onCreate).toHaveBeenCalledWith("新的理解", undefined);
```

## Task 2: 首页 UI（WU-01，等待 RED）

- [x] 使用现有 Input 替换大 textarea，ref 为 HTMLInputElement；input 标签关联可见 label。
- [x] 使用原 onCreate、onOpen 等 Props，保持错误、删除确认与未保存提示。
- [x] 与 locale 契约保持一致；采用下面的标准表单结构和输入法保护。

```tsx
<form data-testid="create-article-form" onSubmit={(event) => { event.preventDefault(); void handleCreate(); }}>
  <label htmlFor="new-article-title">{t("creative.topic_label")}</label>
  <Input id="new-article-title" ref={titleRef} value={newTitle}
    onKeyDown={(event) => {
      if (event.key === "Enter" && event.nativeEvent.isComposing) event.preventDefault();
    }} />
</form>
```

## Task 3: 插画（WU-03）

- [x] 内置 imagegen 生成透明纸艺写作桌：米白纸张、墨绿书签、少量暖灰；无文字、无金属强光。
- [x] 落盘 webui/src/assets/creative-writing-desk.png；校验透明、尺寸、体积，引用仅此本地文件。

## Task 4: 集成验收与审查

- [x] targeted creative tests、前端构建（含 tsc）、改动文件 ESLint。
- [x] 全 WebUI 测试；若失败与基线对照，不改无关模块。
- [x] 浏览器预览 desktop/mobile/dark；首页标题/表单/文章区不横溢，桌面首屏可见文章入口；创建/模板/筛选/搜索回归。
- [x] 独立只读代码审查与安全审查；记录 collective-test、review、execution-log。

## Next

本轮按用户已给出的修复指令持续执行至验证完成，保留 worktree 供检查，不自动合并或推送。
