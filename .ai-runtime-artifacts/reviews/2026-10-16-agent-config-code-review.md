---
artifact: code-review
route: requesting-code-review + code-review-and-quality
created_at: 2026-10-16
status: passed
source: .ai-runtime-artifacts/specs/2026-10-16-agent-config-spec.md
---

# 单智能体配置页代码审查

审查范围：AgentEditorDialog、BasicsSection、AgentRailSummary、PromptSection、CapabilityPicker、ModelPicker、agent-editor.css 及两组相关测试；仅静态审查当前未提交改动，未运行广泛测试。

## 发现与复核

- 已解决 P2：跨分区后保存失败原因不可见。最终改动使用 validationError 在底栏持续显示具体原因，点击切换到对应分区；validationFocus 在分区提交后聚焦字段。保存按钮以 aria-describedby 关联错误原因。新增集成测试覆盖名称清空后跨分区、错误可见、点击返回、字段获得焦点且不提交。
- 审查途中发现 PromptSection 重复 `aria-describedby` 属性；最新文件已去重，问题已解决。
- 最新 PromptSection 保留 Radix DialogDescription 默认 ID，另用内部 span 为 textarea 说明建立引用；子弹窗语义与指令说明引用均完整。

## 已确认的边界

- draft 仍由父编辑器统一持有，分区切换不重建 AgentProfile；保存使用完整 draft，隐藏的 subAgents、能力模式与原 modelId 保留。
- locked 项不能取消；blocked 项不可操作且不计入有效数量；exclude 的批量操作保留被屏蔽条目的原配置。
- 展开指令编辑改用嵌套 Radix Dialog，Trigger/Close 提供焦点恢复，Esc 先关闭子编辑器；相关测试覆盖草稿保留。
- CSS 在 900px 以下采用单列与折叠摘要，窄屏底栏可换行；实际浏览器视觉与软键盘仍应由主 Agent 验收。

## 结论

最终复核无未解决的 P1/P2；未发现数据丢失、权限扩大或保存协议回归。通过本轮代码审查。主 Agent 已报告新增聚焦集成测试通过，本 reviewer 未重复执行测试。
