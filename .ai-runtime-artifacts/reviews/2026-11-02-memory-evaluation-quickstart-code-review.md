---
artifact: code-review
route: requesting-code-review + code-review-and-quality
skills: [requesting-code-review, code-review-and-quality, security-and-hardening]
source:
  - .ai-runtime-artifacts/verifications/2026-11-02-memory-evaluation-quickstart-collective-test.md
created_at: 2026-11-02
status: completed_with_limits
---

# 记忆评测接线与简化入口复核

## 结论

Leader在实现后以独立阅读轮次检查测试、差异和实际默认路径，未发现本次差异中的阻断问题。完整独立 Agent 审查未完成：安全审查 Agent在核对 adapter/FTS/clock/显式覆盖一致性后因工作区额度耗尽退出；不得将其标为完整审查通过。

## 核对项

- capability 工厂由真实 adapter 共用；没有为解灰而虚构向量支持。
- 默认服务、创建准入与冻结 coordinator 共用能力声明，显式 unsupported 覆盖没有被替换。
- 测试运行实际默认 create_eval_app 生命周期、认证、数据库、导入、入队；仅禁止测试派发外部模型。选中 profile bundle 确实进入 boot、配置模型解析正确，gold 不进入执行载荷。
- 前端高级折叠仅控制展示，不删除预算/轨道/候选状态；折叠后校验及提交状态仍保留。
- 默认真实模型过滤 controlled；独立抽取模型禁用，避免未生效设置。
- capability 请求失败不再吞成全灰表单，显示重试路径。
- 指标按轨道限制；3种默认指标与后端 experiments 常量相同。
- 新增20个语言键已经补齐10个语言包；未改动其他任务新增的 parity 用例。
- 未引入依赖、SQL、任意文件执行、HTML注入或配置凭据输出。

## 剩余限制

严格 Python 类型检查当前两文件254条诊断未通过；真实模型质量与向量检索不属于本次离线验收。桌面进程未强制重启。代码和构建已整合原 checkout，其他任务改动保留，无提交或推送。
