---
artifact: research
route: brainstorming + web-investigator
created_at: 2026-11-01
status: completed
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
  - /Users/mima0000/.agents/skills/brainstorming/SKILL.md
  - .claude/agents/web-investigator.md
---

# Dont Forget 桌面工作台调研与本轮改动

用户要求：查看 `design-demo/index.html`，认为原页面结构、画面风格偏概念展示，要求上网参考桌面 AI 工作台并优化。

## 定位与上下文

- 当前仓库是 zhixu，目标文件实际位于相邻项目 `/Users/mima0000/Documents/学习-001/do-project/dontforget/design-demo/index.html`；已通过文件系统检索确认。
- dontforget 当前只有这个静态原型，没有 package.json、应用后端或独立 AGENTS.md。本轮没有执行项目抽离。
- 已扫描 zhixu 的 specs/plans/decisions/contracts/research，并阅读 `2026-11-01-dontforget-extraction-plan.md`、project.profile.md 与 context-map.md。抽离计划未批准，其技术迁移不属于本轮。
- 本轮只修改目标 HTML；所有调研、验证、备份与截图存放于 zhixu `.ai-runtime-artifacts/`。

## 官方参考

1. [Cherry Studio · Chat Interface](https://www.cherryai.com/docs/en/cherrystudio/preview/chat/)
   官方文档及截图定义左侧助手与话题、顶部当前助手与模型、底部输入区域。采用会话主线和就近输入工具；不照搬其全部功能入口。
2. [AnythingLLM · Memories & Personalization](https://docs.anythingllm.com/features/memories)
   官方文档介绍通过聊天设置打开 Memories 侧栏。采用按需展开记忆详情，而非首页常驻统计。
3. [Jan · Desktop Quickstart](https://docs.jan.ai/desktop/quickstart)
   官方区分聊天模型选择器、Hub 模型下载与 Settings 中的模型服务配置。采用高频使用和低频配置分离。
4. [Jan 官方界面示例](https://www.jan.ai/post/run-ai-models-locally)
   图像检索返回的官方桌面截图呈现最近会话侧栏、中央对话、底部输入与模型选择。用于视觉层级参考。

以上为产品文档和界面参考，不构成经过用户测试的普遍可用性结论。

## 设计判断

原版同时展示一级模块、记忆卡片、数据统计、宣传式标题和全局运行数据。用户第一眼需要理解过多产品能力，而不能直接开始或继续任务。暖纸色、衬线标题与阴影使功能原型带有编辑展示页的气质。

考虑过三个方向：

- **会话优先（采用）**：左侧会话、中央读写、右侧按需详情。最符合本轮日常使用目标。
- 记忆仪表盘：记忆检视效率高，但继续放大原版首页统计和卡片问题。
- IDE 式工作台：适合代码与文件任务，但为一般个人助理增加不必要的面板与导航。

## 实际落地

- 232px 左栏集中新对话、搜索、记忆库、自动化与按时间分组的历史会话。
- 助手、技能、个人偏好、开发工具集中到工具区；MCP 进入设置，原路由仍可访问。
- 新对话页面只保留轻量引导、输入、三个可编辑建议及最近会话。
- 对话输入固定底部；记忆和助手详情按需展开，窄屏有明确关闭入口。
- 中性白/灰底、系统无衬线字体、少量绿色强调、轻边框、无装饰渐变；删除远程字体请求和全局统计栏。
- 补齐消息键盘发送与 IME 保护、草稿保留、稳定会话索引和活动排序、搜索、附件名称演示、主题切换、键盘开关。
- 明确“交互原型”“演示数据”，不伪装模型调用、保存、编译、连接或文件上传。

## 用户补充后的首页文案（2026-11-01）

用户要求首页突出记忆系统，已在静态原型更新：

- 主标题：聊过的事，不必从头说。
- 说明：记住你的偏好、决定与经历，在需要时为你找回。
- 输入提示：告诉我需要记住的事，或找回之前聊过的内容…
- 快捷建议：记住一个新偏好 / 回顾之前的决定 / 找回聊过的事。
- 最近对话标题：从上次的对话继续。

结构和交互保持不变，最新截图与浏览器报告已重新生成。正式接入任务及 Logo 应用要求同步写入 `../plans/2026-11-01-dontforget-extraction-plan.md` 的 Task 5b / 5c；业务迁移仍未开始。

## 范围与验收

后续用户提供 WorkBuddy 截图并明确工作空间必备。当前原型已增加空间分组/创建/切换/折叠、输入区下方空间选择，首页采用居中标题和记忆任务切换、框上建议、框下空间栏；侧栏约252px。输入区右上沿的平面标志已按用户要求换为内置生图工具生成的立体小象举便签插画（`design-demo/assets/dontforget-elephant-mascot-v1.png`），品牌位仍使用 Logo。最新证据见 `.ai-runtime-artifacts/verifications/2026-11-01-workspace-ui/`，正式接入约束已同步到抽离计划 Task 5b / 5c。

不新增依赖，不接后端，不实现项目抽离。刷新会清空演示会话，主题偏好保存在本地浏览器。原有部分配置、编辑动作仍为占位。

验收证据：`../verifications/2026-11-01-dontforget-ui/browser-report.json` 与同目录截图。原文件备份：`2026-11-01-dontforget-ui/index-before.html`。
