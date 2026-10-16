---
artifact: verification
route: verification-before-completion -> systematic-debugging
skills: [verification-before-completion, systematic-debugging]
skills_evidence:
  - /Users/mima0000/.agents/skills/verification-before-completion/SKILL.md
  - /Users/mima0000/.agents/skills/systematic-debugging/SKILL.md
source:
  - .ai-runtime-artifacts/specs/2026-10-17-writing-team-sidebar-spec.md
  - webui/src/preview/PreviewApp.tsx
  - webui/src/components/creative/CreativeWorkspace.tsx
  - webui/src/components/creative/ArticleEditor.tsx
created_at: 2026-10-17
status: tested-with-preview-defect
---

# 创作空间浏览器测试 v2

## 环境和边界

使用真实 Codex 内置浏览器及 cua_repl，通过独立启动的 Vite 5.4.21 开发服务测试 `http://127.0.0.1:5173/?preview=1#/creative`。起初视口约 1280×720；截图随浏览器面板布局存在高度变化。本轮未做移动设备尺寸仿真。

该入口明确显示“界面预览 · 示例内容”，使用内存 transport，不连接 gateway，不发送正式文章 mutation，不调用模型。本轮测试数据是一次性预览内容，未读取本机密钥或用户真实文章。

因此这里的“保存”仅指预览会话的模拟保存；不能当作后端跨重启持久化、真实 Team 或 AI 协作已验收的证据。

## 实际操作

| 项目 | 操作与观察 | 结果 |
| --- | --- | --- |
| 列表与入口 | 打开创作空间，看到三个示例文章、模板、搜索和状态筛选 | 页面可进入；没有配置团队的前置步骤 |
| 新建进入编辑器 | 填中文标题、点击一次新建，进入空正文编辑器 | 可进入；返回列表后发现重复项，见缺陷 B01 |
| 中文与 emoji | 输入中文段落及 `✍️`，包含标题、列表和引用 | 输入和预览保留内容；未进行真实 IME 候选串交互，IME 由自动化基线覆盖 |
| Markdown 预览 | 从编辑切换预览，核对标题、段落、列表和引用 | 正确显示 |
| 链接素材 | 新增“链接”类型，填写标题、URL、摘录并添加 | 展示链接，默认“本次协作可用”勾选；未把保存链接声称为已抓取 |
| 提纲编辑 | 新增一个提纲项并确认 | 展示章节及上/下移、删除入口；单项时排序按钮禁用 |
| 分屏与保存状态 | 切换分屏，观察未保存→已保存，点击保存后按钮禁用 | 预览会话内状态可更新，不代表真实 API 持久化 |
| AI 区 | 查看编辑器右侧 | 仍为阶段 B 占位，不能执行新创作团队 |
| 返回列表 | 返回后可见新稿，但同一次创建出现两条同名记录 | 缺陷 B01，已重载后再次复现 |

## B01：预览模式单次新建出现重复文章

**复现两次。** 第二次从 reload 后干净的三篇示例开始：输入“单次创建重复问题复现”，点击一次新建，返回列表；总数为 5，出现两张标题和创建时间完全相同的文章卡，期望总数为 4。

静态数据流依据：

1. `CreativeWorkspace.tsx:414` 的 preview transport 调用 `applyPreviewMutation`，随后 `writePreview(outcome.articles)` 已更新集合。
2. `article.create` 的 preview mutation 已将创建对象加入结果集合（`CreativeWorkspace.tsx:114` 起）。
3. `handleCreate` 取得结果后在 `CreativeWorkspace.tsx:548` 再执行 `writePreview([article, ...previewRef.current])`，又加入同一对象。

观察与数据流相符：**模拟 transport 和创建回调都拥有列表插入责任**。这是预览分支缺陷，不外推为真实服务端也会重复创建。本轮没有改业务代码或测试；实施前应增加“一次新建→返回列表→只有一张对应卡”的回归断言，固定唯一的预览列表更新责任。既有 `creative-workspace.test.tsx` 的新建用例仅验证进入编辑器，未覆盖返回后的唯一性。

## 截图证据

- `2026-10-17-writing-browser-v2/article-material-preview.jpg`：正文预览与链接素材。
- `2026-10-17-writing-browser-v2/article-outline-split.jpg`：中文、emoji、提纲和分屏，右侧为真实占位状态。
- `2026-10-17-writing-browser-v2/preview-duplicate-article.jpg`：第一次返回后的列表状态。
- `2026-10-17-writing-browser-v2/preview-duplicate-reproduced.jpg`：清空预览后再次单次创建，重复记录同屏可见。

截图 API 实际返回 JPEG 字节，已按文件签名保存为 `.jpg`，没有转换或编辑图像。临时浏览器测试页与本轮启动的 Vite 服务已关闭；模拟数据不保留为正式文章。

## 设计影响与后续测试

新协作侧栏不能同时挤压素材、正文编辑、预览四个长列；中等宽度下应折叠素材或使用切换面板。侧栏需要可见的产物、下一步操作与保存状态，而非把文章正文变成多人聊天记录。

实施后需补 390/768/1280/1440 宽度、键盘与焦点、真实 gateway/WS、阶段交接、长初稿完整性、选区采纳/冲突、停止后的晚到结果、断线重开、内置保护与真实模型全流程。当前截图与自动化通过项不能替代这些新增验收。
