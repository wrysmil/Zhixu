---
artifact: verification-lite
route: brainstorming -> systematic-debugging -> verification-before-completion
skills: [brainstorming, systematic-debugging, verification-before-completion, visualize]
skills_evidence:
  - /Users/mima0000/.agents/skills/brainstorming/SKILL.md
  - /Users/mima0000/.agents/skills/systematic-debugging/SKILL.md
  - /Users/mima0000/.agents/skills/verification-before-completion/SKILL.md
  - /Users/mima0000/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/SKILL.md
source:
  - .ai-runtime-artifacts/research/2026-10-18-subagent-observability-preview.html
  - 用户2026-10-18补充的“下拉框优化下”与菜单截图
created_at: 2026-10-18
status: preview-verified-product-pending
tier: 1
---

# 设计预览与下拉菜单检查

## 范围

只修改 `.ai-runtime-artifacts/` 内的研究、设计、契约、测试说明与可交互预览；没有修改 `webui/src` 或 `nanobot` 业务源码。Git已有的 `memory/state.db` 改动及其他会话产物未处理。本预览不连接真实Agent，不能充当功能端到端通过证据。

## 改进

- 原生select改为主题任务菜单：Agent名称、任务标题两行；菜单展示当前状态、选中勾选。
- 方向键与Home/End导航，Enter/Space通过原生button选择；Escape先关闭菜单、恢复焦点。
- 任务切换更新同一个详情面板；关闭菜单不重跑或停止任务。
- 相同状态同步不重建DOM，防止选择后焦点丢失；外部pointerdown只关闭菜单，不提前删除随后click的目标。
- 生产实现写入设计稿：复用项目现有Radix DropdownMenu RadioGroup；单任务不显示切换器。

## 自动交互回归

命令：

```text
node .ai-runtime-artifacts/verifications/2026-10-18-subagent-preview-interactions.mjs
```

该脚本使用当前安装的happy-dom，在独立离线DOM里运行预览的真实脚本；模拟本地状态保存的回显，不模拟真实gateway。

1. 切换子任务，身份同步，菜单关闭。
2. 选择后状态回写不丢失触发器焦点。
3. 菜单展开时点击外部关闭按钮，click目标仍有效。
4. Escape只关闭菜单，保留详情与焦点。
5. 关闭／重开详情保留草稿。
6. 文件预览返回原任务且不新增详情列。
7. 方向键与Home/End定位正确选项。

修复前：5/7，定位出焦点回写与pointerdown删除目标两处问题。先修状态回显后6/7，再修外部按下处理后**7/7，退出码0**。断言直接比较焦点身份，避免展开整个DOM对象产生庞大失败输出。

## 浏览器检查

通过CUA实际打开本地设计预览，观察过1113px桌面分栏与570px窄屏详情；新版任务菜单主要在570px视口检查。

- 实际展开菜单，确认主题背景、两行名称、状态与选中勾可见，没有原生系统蓝色选项块。
- 键盘Down／Return切换到另一个任务，AX状态确认显示对应身份；修复后焦点确认为切换按钮。
- 菜单展开后Escape：详情保留，菜单关闭，焦点回切换按钮。
- 菜单展开时点击详情关闭：返回聊天，焦点回原任务入口。
- 窄屏打开详情：焦点进入关闭按钮；Shift+Tab再Tab后焦点仍在抽屉内。

截图：`2026-10-18-subagent-observability-preview/task-menu-final-narrow.png`。已再次查看保存后的图片，确认展示了最终主题菜单。

未声称完成：360/768/1024/1440px完整浏览器矩阵、200%缩放、屏幕阅读器、真实gateway、断线恢复、真实时间线滚动、性能预算。它们仍由test-spec的产品验收约束。

## 其他检查

- HTML为fragment，无完整html/head/body包装；脚本经Node vm.Script语法校验。
- 当前相关旧前端测试204/204通过、TypeScript noEmit退出0，细节及现有警告见主test-spec。
- 未安装新依赖，未提交Git；设计稿与合同仍为draft/approved=false。

## Next

用户可继续调整设计，或说“写计划”进入实施任务拆分。实际功能完成必须另外满足test-spec的29项P0及回归要求。
