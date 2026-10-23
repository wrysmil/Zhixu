---
artifact: verification-lite
route: superpowers:brainstorming -> superpowers:verification-before-completion
skills: [brainstorming, verification-before-completion]
skills_evidence:
  - /Users/mima0000/.agents/skills/brainstorming/SKILL.md
  - /Users/mima0000/.agents/skills/verification-before-completion/SKILL.md
source:
  - harness-kit/project.verification.md
  - .ai-runtime-artifacts/specs/2026-10-17-writing-conversation-team-spec.md
  - .ai-runtime-artifacts/research/2026-10-17-writing-conversation-team-research.md
  - .ai-runtime-artifacts/stack/2026-10-17-writing-conversation-team-stack.md
created_at: 2026-10-17
tier: 1
status: verified-documents-only
---

# 创作空间对话团队方案：文档验证

## 本轮声明

调研与方案已形成草稿，未实施业务功能。用户补充的“文章编辑为主，右侧固定对话”已进入方案正文、来源和验收标准。

## 已执行检查

| 检查 | 命令/方式 | 结果 |
| --- | --- | --- |
| 技术栈 | 项目 Python 的 `importlib.metadata`、Node 读取前端包 `package.json` | 已将精确版本写入 stack 文档 |
| 文档结构 | 项目 Python + PyYAML 解析三份主题文档 front matter | artifact/route/skills/skills_evidence/source/created_at 齐全 |
| 要求覆盖 | Python 检查 U1–U8、A01–A18、四个工具名 | 全部存在 |
| 草稿状态 | 检查 `approved: false`、`## Next` 和已确认布局原文 | 通过 |
| 基础质量 | 检查代码围栏配对、TODO/TBD/FIXME、行尾空格 | 通过 |
| Skill 来源 | 检查 skills_evidence 对应路径存在 | 通过 |
| 现有 diff | `git diff --check` | 退出 0；此命令不含未跟踪文档，因此文档另行逐文件检查 |
| 范围 | `git status --short` 与开始时状态比较 | 本轮仅新增主题过程文档，已有业务改动保留 |

## 内容自审

- 当前实现事实与拟议能力分开标注，外部证据有原始链接。
- 方案没有把固定配方仅换成聊天气泡；明确复用通用团队 Agent Loop。
- 专属工具覆盖名称解析和实际实例化两条链，包含普通聊天/克隆团队隔离要求。
- 已补充 builtin version 升级须复用物理 ID，防止重复种子。
- 已指出当前选区未接线、生产播种接线与非正文撤销缺口。
- 明确并发编辑、IME、取消、重连、幂等、版本冲突与撤销行为。

## 未验证项

未运行应用单测、lint、构建、真实模型或交互式 UI 验证，因为本轮未修改应用源码。本文不证明拟议功能已经可用。接口细化、模型工具调用效果与视觉可用性应在后续实现中验证。
