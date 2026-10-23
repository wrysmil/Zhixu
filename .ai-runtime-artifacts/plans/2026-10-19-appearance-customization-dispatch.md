---
artifact: implementation-dispatch
route: orchestration:dispatcher-workflow
plan: skip:plan (用户「直接实现把」，已批准 spec 直接实施)
skills: [orchestration, api-and-interface-design]
skills_evidence: [.claude/skills/orchestration/SKILL.md, C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/api-and-interface-design/SKILL.md]
source: [.ai-runtime-artifacts/specs/2026-10-19-appearance-customization-spec.md, .ai-runtime-artifacts/contracts/2026-10-19-contract-appearance-customization.md]
created_at: 2026-10-19
approved: true
---

# 外观自定义执行图

用户授权：直接实现把。采用已批准方案与 skip:plan 路径，本文件是执行记录，不是请求新一轮计划审批。

worktree_path: C:/Users/Huangqh/.codex/worktrees/appearance-customization/Zhixu

## 执行图

GROUP-1 并行（按共享契约编写，测试在依赖模块到位后运行）：

- WU-01：主题模型、系统模式/保存/迁移、首屏、App 接入；agent_role=coder；wu_type=feature。
- WU-02：参考图式外观编辑页、翻译、隔离预览、组件交互测试；agent_role=coder；wu_type=ui。
- WU-03：全局字体与独立页面适配、代码/diff/终端即时更新；agent_role=coder；wu_type=ui。

GROUP-2：集体测试、构建、真实浏览器冒烟；独立 reviewer/security/performance 审查；修复后复验。

## 上下文包

L1：AGENTS.md、routing.md、project.profile.md（已有上下文；不使用其中过期 Git 同步推断）。

L2：已批准 spec 对应 WU 章节，主 checkout 的绝对路径可读取。

L3：各 WU prompt 的文件列表；参考现有 useTheme、local-preferences、SettingsControls、TerminalSurface。

L4：2026-10-19-contract-appearance-customization.md。

约束：只在 worktree 修改业务文件；禁止碰已有用户未跟踪 teams/、writing/ 与旧验证截图；不提交、不推送；应用源码/过程产物使用 apply_patch，shell 仅查询和验证/环境准备。使用同一已批准设计，无新需求访谈或阶段审批。

## Next

执行实现与验证，不等待额外确认。完成后将指定文件安全同步回主 checkout 供用户直接使用，保留 worktree 便于恢复。
