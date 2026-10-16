---
artifact: dispatch
route: orchestration:dispatcher-workflow
status: executing
approved: true
source: .ai-runtime-artifacts/specs/2026-10-16-agent-config-spec.md
---

# 单个智能体配置页实施

用户已批准设计，并明确要求跳过工作树，直接当前分支修改。覆盖 WORKTREE-INIT；保护 AGENTS.md 用户改动，不提交、不推送。独立文件分工，同一目录实施。

- editor_layout：主弹窗、身份/更多信息、右摘要、独立样式；不改能力控件及翻译。
- editor_controls：工作指令、工具选择器、模型选择器；不改主弹窗。
- editor_validation：中英文文案及专项测试；与组件作者对齐键名。
- Leader：素材接入、真实组件预览、构建检查、尾盘审查。

契约：AgentProfile、onSave 和能力 all/include/exclude 协议保持；新增 tab 仅控制显示，不拆草稿；subAgents 保存原值。未知 modelId 保留并显示不可用。所有新增翻译位于 settings.agents.workspace。图片固定本地地址 /illustrations/agent-tools.png，Iconify 图标 /icons/agent-bot.svg。

完成条件：真实组件桌面和手机可操作，无横向溢出；专项回归、TypeScript 和生产构建通过，相关 lint 无新增问题。预览使用内存样例，不能声称已跑真实网关保存。
