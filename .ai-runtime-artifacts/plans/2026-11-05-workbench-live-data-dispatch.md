---
artifact: implementation-dispatch
route: writing-plans
plan: .ai-runtime-artifacts/plans/2026-11-05-workbench-live-data-plan.md
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/api-and-interface-design/SKILL.md
source:
  - harness-kit/core/routing.md
  - harness-kit/artifact-templates/dispatch.harness-overlay.md
  - .ai-runtime-artifacts/contracts/2026-11-05-contract-workbench-live-data.md
created_at: 2026-11-05
status: needs_review
approved: false
---

# 工作台真实数据接入执行图

## 工作区与批准前限制

主checkout：`D:/studyspace/project/Zhixu`，只存过程产物；保留现有memory/state.db修改。批准实现后由Leader按当时HEAD建立集成工作区 `D:/studyspace/project/Zhixu-workbench-live-data`，branch=`codex/workbench-live-data`。路径已存在时核对归属，不覆盖、不删除，改用带序号的隔离路径并更新execution-log。

下列均为计划中的位置，本阶段不创建。每个WU使用独立worktree和分支，从已整合的依赖提交启动。Leader在集成worktree顺序整合GROUP-1结果，再从集成分支创建GROUP-2工作区；GROUP-2整合后才创建GROUP-3工作区。worker不提交，由Leader在对应工作区复核并提交后整合，禁止共享写目录。

## 执行图

```text
GROUP-1：契约已定后的独立基础与前端
  WU-01 | 标题: 统一项目与全局技能解析
    Task: 1
    依赖: contract
    wu_type: feature | agent_role: coder | workspace_scope: wu
    worktree_path: D:/studyspace/project/Zhixu-workbench-live-data-wu01
    branch: codex/workbench-live-data-wu01
    wu_skills: api-and-interface-design, test-driven-development, source-driven-development
  WU-02 | 标题: 记录工具与记忆文件的真实观测
    Task: 2
    依赖: contract
    wu_type: feature | agent_role: coder | workspace_scope: wu
    worktree_path: D:/studyspace/project/Zhixu-workbench-live-data-wu02
    branch: codex/workbench-live-data-wu02
    wu_skills: api-and-interface-design, test-driven-development, source-driven-development, security-and-hardening
  WU-03 | 标题: 工作台三页签与即时状态接入
    Task: 3,4
    依赖: contract；可先按契约使用测试fixture，真实接口交付由GROUP-3验收
    wu_type: ui | agent_role: coder | workspace_scope: wu
    worktree_path: D:/studyspace/project/Zhixu-workbench-live-data-wu03
    branch: codex/workbench-live-data-wu03
    wu_skills: frontend-ui-engineering, test-driven-development, source-driven-development, Frontend Design Optimizer

GROUP-2：基础完成后的运行时与HTTP接线
  WU-04 | 标题: 接通真实上下文与轮次观测
    Task: 5
    依赖: WU-01, WU-02
    wu_type: feature | agent_role: coder | workspace_scope: wu
    worktree_path: D:/studyspace/project/Zhixu-workbench-live-data-wu04
    branch: codex/workbench-live-data-wu04
    wu_skills: api-and-interface-design, test-driven-development, source-driven-development
  WU-05 | 标题: 提供会话资源分页与安全预览
    Task: 6
    依赖: WU-01, WU-02
    wu_type: feature | agent_role: coder | workspace_scope: wu
    worktree_path: D:/studyspace/project/Zhixu-workbench-live-data-wu05
    branch: codex/workbench-live-data-wu05
    wu_skills: api-and-interface-design, test-driven-development, source-driven-development, security-and-hardening

GROUP-3：真实链路统一验收
  WU-06 | 标题: 验证文件工具到网关与浏览器的完整数据链路
    Task: 7
    依赖: WU-01, WU-02, WU-03, WU-04, WU-05
    wu_type: test | agent_role: test-engineer | workspace_scope: wu
    worktree_path: D:/studyspace/project/Zhixu-workbench-live-data-wu06
    branch: codex/workbench-live-data-wu06
    wu_skills: browser-testing-with-devtools, source-driven-development, security-and-hardening
  Leader尾盘
    Task: 8
    依赖: WU-06与全部修复
    并行只读审查: code-review-and-quality + security-and-hardening；性能审查按风险决定
```

## 派发契约

技能路径由下表固定，派发时只加载所属WU列出的技能；以下是执行期加载清单，本计划阶段没有加载实现技能。

| slug | SKILL.md |
| --- | --- |
| api-and-interface-design | C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/api-and-interface-design/SKILL.md |
| source-driven-development | C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/source-driven-development/SKILL.md |
| test-driven-development | C:/Users/Huangqh/.codex/skills/test-driven-development/SKILL.md |
| security-and-hardening | C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/security-and-hardening/SKILL.md |
| frontend-ui-engineering | C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/frontend-ui-engineering/SKILL.md |
| Frontend Design Optimizer | C:/Users/Huangqh/.codex/skills/@user_8ec29dbe/frontend-design-optimizer/SKILL.md |
| browser-testing-with-devtools | C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/browser-testing-with-devtools/SKILL.md |

- 文件白名单精确继承plan的文件结构表，worker不得跨所有权；GROUP-2文件与GROUP-1互不重叠。
- 所有worker先读取spec、contract、所属Task与目标源码，按本WU技能加载。中文派发与回报；必须包含“### Skills 使用”、修改文件、测试证据、未验证项与wu_status。
- 不改harness-kit，不操作用户配置或真实记忆，不运行付费模型，不提交/push，不改变已冻结字段。契约变更先向Leader提出，由Leader更新文档和所有消费者。
- 单元测试中的fixture只用于逻辑/兼容测试；GROUP-3必须真实文件、工具与gateway。不能用静态草图、拦截接口或成功mock替代数据接入验收。
- 每组完成由Leader整合并复核，再由Leader按语义提交；worker无权更改计划approved状态。运行时接线与API即使并行完成，也必须通过GROUP-3统一验证。
- 失败修复回派原文件所有者；涉及第二个worker文件须明确移交，不让两个worker同时编辑。计划所有工作完成后仍需集体测试和集体审查才能交付。

## Next

用户单独说“开始实现”或“执行”后加载执行阶段编排规则、WORKTREE-INIT并派发；本轮只落盘本执行图，不启动worker。
