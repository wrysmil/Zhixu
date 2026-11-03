---
artifact: verification
route: superpowers:writing-plans -> verification-before-completion
skills: [writing-plans, verification-before-completion]
skills_evidence:
  - /Users/mima0000/.agents/skills/writing-plans/SKILL.md
  - /Users/mima0000/.agents/skills/verification-before-completion/SKILL.md
source:
  - .ai-runtime-artifacts/plans/2026-10-25-evaluation-observability-plan.md
  - .ai-runtime-artifacts/research/2026-10-25-evaluation-observability-demo.html
  - .ai-runtime-artifacts/research/2026-10-25-evaluation-run-report-demo.html
  - .ai-runtime-artifacts/research/2026-10-25-evaluation-160-calls-report-demo.html
created_at: 2026-10-25
status: demo-verified-with-project-check-gaps
---

# 评估观测台设计 Demo 验证

## 范围

本轮仅交付 `.ai-runtime-artifacts/` 中的设计演示、接口草案、技术栈和计划。没有改动 `nanobot/` 或 `webui/src/`，未执行真实模型请求、工具、评估或团队任务。测试数字全部来自标注的模拟数据。

工作树有其他会话的在途改动，核对时又出现 team/gateway 与测试文件的新变更；未撤销或纳入本次成果。计划的源码事实来自本轮只读快照，正式执行前重新取得基线。

## 数字与脚本校验

通过 Node 的 `vm.Script` 编译 Demo 中的内联脚本，并用独立 context 对声明的数据做断言；不是对真实业务系统的运行验证。

| 项目 | 实测 |
| --- | --- |
| JavaScript 语法 | PASS |
| 用例数量 | 20 |
| Agent Token 用例和 | 40,680 |
| Judge Token 用例和 | 7,640 |
| 总量 | 48,320 |
| 用例 #01 智能体表合计 | 6,240 |
| 用例 #01 物理请求表合计 | 6,240 |
| 压力场景调用数 / 唯一 ID | 160 / 160 |
| 压力场景失败数 | 6 |
| 压力场景工具分组 | 4 |

末次命令摘要：`node -e` 读取 Demo，提取 `<script>` 后执行 `new vm.Script(js)`；对 cases、agents、calls、stressCalls 数组求和及唯一性断言。所有上述断言 PASS。

## 浏览器实测（CUA / 本地 IAB）

本地预览地址：`http://127.0.0.1:8891/2026-10-25-evaluation-observability-demo.html`，仅回环绑定，服务目录是 research。页面不连接评估 API。

| 步骤 | 实际结果 |
| --- | --- |
| 点击资料研究员节点 | 检查器显示 agent_research、parent=agent_main、demo-fast、m_02、1,480 Token 与输入/输出 |
| 用例结果筛选失败 | 显示唯一 #19 引用证据校验，Agent 1,800、Judge 360 |
| 从失败用例查看流程 | 显示“未采集到完整执行关系”，未绘制虚构图 |
| Token Tab | Agent 24 请求 / 40,680；Judge 20 请求 / 7,640；合计44请求 / 48,320 |
| 160 调用压力场景 | 显示4个工具分组，未挂载160个独立工具节点 |
| web_fetch 分组翻页 | 第1页 t_fetch_001–008；第2页 t_fetch_009–016；40条、5页 |
| 分组筛选失败 | 仅 t_fetch_012、025、038，计3条 |
| 搜索012并查看 | 定位 t_fetch_012，显示 agent_research、参数 source_012、DEMO_TIMEOUT、663ms、Trace seq103–104 |
| 宽度390px | DOM innerWidth=390、document.scrollWidth=375；无整页水平溢出；截图确认双列指标、画布、下置检查器和独立表格滚动 |
| 视口恢复 | 已 reset 临时 viewport，不保留测试尺寸 |

## HTML 实际下载与重开

从 Demo 按钮实际下载，保存到 research 中作为可独立阅读的交付文件：

| 文件 | 验证 |
| --- | --- |
| evaluation-run-report-demo.html | 7张表、11节点静态图、20条用例结果；浏览器重新打开可读，无脚本 |
| evaluation-160-calls-report-demo.html | 7张表、分组静态图、全部160条调用明细、全部6条异常；实际下载成功，无脚本 |

报告不含 `<script>`、inline onload/onclick、外部 http/https 资源引用。主要数据、错误、图和调用明细均在初始 HTML 中；原生 details 只承载额外的已脱敏证据。金额未推算，压力场景 Token 未采集时显示缺口，不混入主运行总量。

## 视觉判断记录

- 当前默认桌面宽度：指标/Tab/画布层级清晰；中等宽度检查器移到图下，展开画布时使用独立侧栏。工具节点蓝色、智能体绿色、Judge紫色；有文字状态。
- 160调用：默认分组避免密集铺图；失败分组有异常数和感叹号，右侧可只看失败。全图概览用于辨认路径，单次详情仍通过检查器阅读。
- 窄屏：保留可操作的检查器和表格；全图文字在适应模式较小，需要缩放或选择节点阅读，不能把移动端概览当作逐节点全文视图。
- 静态报告：报告版心、摘要、流程、表格依次展示，数值右对齐，范围与演示标识可见。

截图：`2026-10-25-evaluation-stress-canvas.jpg`、`2026-10-25-evaluation-demo-mobile.jpg`、`2026-10-25-evaluation-report-preview.jpg`。

## 项目检查与未验证项

- `bash harness-kit/scripts/harness-check.sh` 返回1：既有 source 布局缺少 `platform/cursor/.cursor/rules/ai-entry.mdc` 和 `cursor-subagent-routing.mdc`，没有改动 Harness 以掩盖失败。
- `git diff --check` 提示两份既有10月23日 execution-log/collective-test 的 EOF空行；未调整他人修改。自身新增文件另做空白/元数据检查。
- 不声称本次已通过真实系统的 pytest / Vitest / typecheck / build：本次没有业务实现，验证针对原型。
- print CSS 已提供，但本轮没有打印预览或PDF分页实测；正式实现 WU-05/06 必须执行。
- 160调用交互已实测；10,000调用的服务端分页、性能、取消与完整观察覆盖属于待实施验收，不能把原型演示当作已经支持。
- Demo 使用内存数组分页；正式系统按接口契约改为服务端查询与按需读取。
