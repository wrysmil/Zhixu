---
artifact: research
route: web-investigator
skills: [source-driven-development, frontend-design]
created_at: 2026-10-15
status: complete
---
# Agent工作台对照与资源

## 对照结论
证据：当前PreviewThread空态为居中问候+输入，WorkspaceNavigation只有图标；PreviewSettings.memory只有两行只读数据。真实MemorySection已有开关、semantic/episode与真实检索列表，不能把预览缺失误认为生产功能缺失。

参考：https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork 与 https://academy.claude.com/tutorials/get-started-in-claude-cowork-in-three-steps 。共同强调可交付任务、工作目录和实际输出。本项目的差距推断：入口与上下文说明弱，可发现性差；本轮修展示与导航，不声称达成外部Agent的执行能力。

补充参考：https://www.dify.ai/workflows ，其执行检查强调节点输出与过程可追溯。本项目已有工具过程/工作台，不将界面清爽误等同于删除过程信息。

|对照维度|当前证据|本轮处理|仍有边界|
|---|---|---|---|
|任务开始|空态只剩居中问候与输入|任务工作台、实际可填草稿的示例、工作目录与模型入口保留|不新增任务执行引擎|
|能力发现|窄rail只显示图标|短文字、tooltip与记忆直达|导航沿用现有路由|
|记忆透明|真实有CRUD但术语多，预览只有统计|普通语言说明、全文列表、筛选与示例演示|真实网关未连接，不能声称真实记忆数据实测|
|执行可见|已有工具过程及dock|保留原过程与操作逻辑，统一阅读空间|无真实运行态时不制造假进度|
|视觉一致|灰底、紫选择色、不同页面层次|暖纸纹理、绿强调、左对齐标题、深色及手机适配|新增文案中英回退，其余语种沿用既有翻译|

## 资源清单
|本地文件|来源|用途|
|---|---|---|
|webui/public/illustrations/agent-workspace.svg|https://cdn.undraw.co/illustration/agentic-network_02v5.svg|任务工作台辅助插画|
|webui/public/illustrations/memory-notes.svg|https://cdn.undraw.co/illustration/notes-list_non4.svg|记忆说明插画|
|webui/public/illustrations/workflow.svg|https://api.iconify.design/lucide/workflow.svg?color=%232c5946|工作流图标|

unDraw https://undraw.co/license 允许在网站内使用和修改；两张插画仅作界面装饰，不用于AI训练、不打包售卖资源。默认紫色#6c63ff替换为#47775b，深色笔画改绿灰。Iconify Lucide采用ISC：https://icon-sets.iconify.design/lucide/workflow/ 。现有lucide-react与Iconify同一图标集，沿用保持一致，无新增依赖。

本轮是工作型页面，真实照片无助于操作，没有接入Pexels；已有合适正式插画，不使用Picsum占位图。所有资源本地serve，不因页面打开增加第三方资源请求。

## 版本与限制
React18.3.1使用现有state/props，参照https://react.dev/reference/react/useState 。没有可用真实网关；业务效果只能组件测试，预览为明确样例。
