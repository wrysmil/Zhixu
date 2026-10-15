---
artifact: review
route: orchestration.collective-closeout
created_at: 2026-10-15
batch_id: workspace-panel-fixes
work_unit: WU-S
agent_role: security-auditor
reviewer_instance: /root/security_review
worktree_path: D:/studyspace/project/Zhixu/.harness-worktrees/workspace-panel-fixes
base_sha: fccafd7
verdict: APPROVE
skills:
  - security-and-hardening
skills_evidence:
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/security-and-hardening/SKILL.md
  - .claude/agents/security-auditor.md
source:
  - .ai-runtime-artifacts/verifications/2026-10-15-workspace-panel-fixes-wu01-terminal.md
---

# 工作区侧栏与终端修复独立安全审查

## Summary

- 结论：**APPROVE**。
- Critical：0。
- High：0。
- Medium：0（审查中发现的 1 项会话归属问题已修复并复查）。
- Low：0。

范围为冻结后的工作树相对 `fccafd7` 的最终差异，重点检查终端启动、错误回报、待启动队列、会话工具状态和文件预览。审查者未参与实现；仅按 Leader 最后指令写入本报告，没有修改源码、读取真实配置或密钥，也没有联网审计或升级依赖。

## Findings

当前没有未解决的安全发现。

### 已关闭：终端清单跨会话自动注入

- 初审证据：原 `StartDock` 对连接级 `terminal_list_result` 的全部运行条目调用 `dockStore.openTerminal(scope, ...)`；服务端清单覆盖该连接订阅的多个会话，条目没有归属。多个已挂载或隐藏的工具区会把其他会话终端放入自己的标签表，并通过全局终端 store 展示已有输出。
- 最终修复：pipe 与 PTY 的运行及 grace 退出条目均回报保存的 `session_key`；wire 和 TS 类型兼容旧端缺字段，store 保留归属。`StartDock.tsx:152-158` 以服务器归属优先，只接纳当前会话；旧端缺字段时仅使用已有 store 归属，未知条目不自动打开。首页在回调时读取当前默认会话，持久化候选继续保留显式接管流程。
- 复查证据：`tests/test_webui_terminal.py` 覆盖无 PTY 的运行和退出归属；`webui/src/tests/start-dock.test.tsx:302` 覆盖同连接 A/B 清单隔离，`:319` 覆盖 legacy 已知归属恢复与未知条目拒绝；`terminal-store.test.ts:106` 覆盖归属回填。实现代理的验证记录报告上述测试通过。

## Positive Observations

- **终端鉴权与限制保留。** `inbound_commands.py:617-643` 的连接授权、会话订阅检查、工作区解析与 shell 名称校验仍在启动逻辑之前。Supervisor 修复仅跳过不存在的桥；restricted 仍走管道及原命令守卫，没有切换到未受限 PTY。
- **异常响应不泄露子进程细节。** `inbound_commands.py:654-675` 在服务端记录启动异常，对客户端只发送关联原 `terminal_id` 的固定 `terminal_start_failed`。新增异常测试用私有错误文本断言最终回报不含该文本；界面将失败、超时、断线映射为固定文案。
- **过期的待启动请求不会在重连后再启动。** `nanobot-client.ts:1089-1096` 建立超时，`:1164-1197` 在断线或超时后删除对应未发送的 `terminal_start` 并清理计时器；关闭终端也清理待启动帧。对应测试覆盖未发送请求超时后重新打开连接不再发送，以及成功回报后不再超时。此次没有新增命令输入重试或自动重放机制。
- **文件预览保留原权限路径。** `DockFileTab.tsx:52` 继续调用带 token 和 session key 的原预览接口，会话预览不覆盖为默认工作区。服务端 `ws_http.py:943-979` 的 token 检查、会话命名空间和 scope 解析未修改；`file_preview.py:82-108` 继续使用 `resolve_allowed_path` 及 restricted 工作区边界。内容通过现有 `CodeBlock` 文本渲染，没有新增原始 HTML 注入。
- **隐藏状态与会话切换有明确归属。** `ThreadShell.tsx:1895` 与 `StartDock.tsx:438` 分别以会话和 scope 重置组件；工具展开事件在 `ThreadShell.tsx:822`、`StartDock.tsx:120` 校验 scope。侧栏宽度、默认收起和预览排列修改没有引入新的文件、网络或执行权限。
- **依赖范围未扩大。** 最终差异没有清单或锁文件变更，也没有新增外部服务、鉴权流程、CORS、上传或敏感数据存储类别。

## 证据与未验证项

- 独立执行：最终 `git diff --check fccafd7` 通过；逐项阅读上述实现、调用端、原服务端边界及相关回归测试。
- 阅读实现代理验证记录：后端 14 passed、44 skipped；终端相关前端 145 passed；定向 Ruff 与 TypeScript 检查通过。此处引用其记录，不表述为审查者独立重跑。
- 当前环境为 Windows，POSIX PTY 实机运行未独立验证；依赖未变且本轮不是发布审计，没有运行联网依赖 audit。结论适用于本轮变更范围，不代表全仓渗透测试或部署环境认证。

## Skills 使用

- 已加载：`security-and-hardening`，执行信任边界、认证授权、数据保护、输入输出与异常回报检查；已阅读其安全检查表。
- 已加载角色：`.claude/agents/security-auditor.md`；保持实现独立性，最终源码冻结后再次复查。
- 已跳过：联网依赖审计与依赖升级；本轮没有依赖变更，且派发明确不授权此类操作。其他与本轮差异无关的安全域未扩展审计。

## Next

安全审查通过；Leader 可结合批次测试和实际浏览器布局验收继续交付。
