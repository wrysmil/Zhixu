---
artifact: implementation-dispatch
route: writing-plans
plan: .ai-runtime-artifacts/plans/2026-10-10-zhixu-webui-static-restyle-plan.md
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.12/skills/api-and-interface-design/SKILL.md
source:
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/contracts/2026-10-10-contract-zhixu-webui-static.md
created_at: 2026-10-10
status: approved
approved: true
worktree: n/a（用户指令「不用worktree」，主 checkout 直接实施）
---

# 知序 WebUI 静态整合执行图

这是拟执行图；本轮没有激活 orchestration、创建 worktree 或派发 worker。实施须在用户批准后由 Leader 读取 dispatcher-workflow 并创建隔离 worktree；下面 worker 的 cwd 均为该工具返回的 workspace 路径，禁止在主 checkout 写业务代码。

## 执行组

| GROUP | WU | 角色/类型 | 依赖 | 文件范围 | 完成标准 |
| --- | --- | --- | --- | --- | --- |
| 0 | 基线 | Leader / investigate | 用户批准 | 只读源文件与执行日志 | 已记录原功能、已有改动及测试/构建基线 |
| 1 | WU-01 基础体系 | coder / ui | 基线 | plan §4基础清单、globals/ui/token、routes及对应测试 | 类型冻结、主题与基础布局可用、路由兼容测试通过 |
| 2 | WU-02A 入口和Shell | coder / ui | WU-01 | App/main/Sidebar/ChatList、workspace home、preview root、locales及对应测试 | 静态入口隔离、文字导航和工作台可运行；导出回调契约冻结 |
| 3 | WU-03 聊天 | coder / ui | WU-02A | plan §4聊天清单、PreviewThread、聊天测试 | 统一聊天视觉、原交互保留、纯预览可运行 |
| 3 | WU-04 设置和能力 | coder / ui | WU-02A | plan §4设置清单、PreviewSettings/Capabilities、设置测试 | 15项设置和4类能力页覆盖、controller未改 |
| 3 | WU-05 文章 | coder / ui | WU-02A | creative目录及文章测试 | 静态完整流程、版本保护、导出可用 |
| 4 | WU-02B 总接线 | coder / ui | WU-03/04/05 | WU-02所有文件、preview-navigation测试 | live与preview入口共享布局，所有入口可达 |
| 5 | WU-06 集中验证 | test-engineer / test | WU-02B | 浏览器证据、集体测试记录与必要测试 | 全量前端检查、离线预览、视口/主题验收完成 |
| 6 | 独立review | reviewer / review | WU-06 | readonly：最终diff | 行为保持、样式覆盖及维护性审查 |
| 6 | 独立security | security-auditor / security | WU-06 | readonly：预览/文章/共享组件diff | 业务请求隔离、链接与HTML处理、示例凭据检查 |
| 7 | 修复和收尾 | Leader协调原WU / review-fix | review/security | 发现项对应文件及日志 | 修复已验证、两类审查和collective-test落盘 |

GROUP-3最多三个worker同时运行，Leader统筹，不额外同时占用第四worker名额。GROUP-6两个独立审查实例，不用原编码实例代替审查。

## Skill派发要求（实施时逐项加载）

- 所有编码WU：source-driven-development（文档优先及版本核对）、frontend-design（统一设计稿）、incremental-implementation、verification-before-completion。
- 新路由/状态/隔离行为WU：按有意义的行为测试加载 test-driven-development；纯样式调整不增加镜像测试。
- WU-06：browser-testing-with-devtools、verification-before-completion；若DevTools工具不可用，使用已提供浏览器工具等效验收并写明限制。
- review：requesting-code-review、code-review-and-quality。
- security：security-and-hardening。
- Leader：实施入口加载 orchestration；Git操作时加载git-xywh及project.git.md。
- 每个worker返回中文 `### Skills 使用`、修改文件、检查命令/退出码、未实测项目与剩余风险；不自行commit/push。

## 共享文件约束

1. WU-01交付后globals/ui/routes/contracts视为冻结；变更提给Leader安排原WU顺序修改。
2. WU-02独占App/main/Sidebar/ChatList/locales/PreviewApp/fixtures；其他WU通过已定义props交付页面，不直接接线。
3. WU-03不改hooks/useNanobotStream、会话数据模型与事件协议。
4. WU-04不改controller、请求/动作hook、API客户端与设置协议；先通过共享token再调整展示组件。
5. WU-05只写creative及对应测试，不导入真实API、客户端或业务provider。
6. 后端目录、TUI、desktop与shared events全体禁止写；已有用户文件不删改。

## Next

用户说「开始实现」后激活编排。可修改任务优先级或静态预览范围；计划正文与此执行图一起评审。
