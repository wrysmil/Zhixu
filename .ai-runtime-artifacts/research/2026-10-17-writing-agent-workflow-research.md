---
artifact: research-report
route: web-investigator -> best-practice-research
skills: [best-practice-research, source-driven-development]
skills_evidence:
  - /Users/mima0000/.codex/skills/best-practice-research/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
source:
  - 用户要求联网调研、集思广议并完善创作空间
  - 用户确认优先服务公众号、博客、知识类文章
  - 用户补充专属内置 Skill/Agent/可选 MCP、管理页标识与不可删除
  - 用户最终纠正为固定内置创作团队，直接应用到侧边栏，不需要用户指定团队或配置工作流
  - .claude/agents/web-investigator.md
  - .ai-runtime-artifacts/specs/2026-10-10-zhixu-writing-capability-spec.md
  - .ai-runtime-artifacts/plans/2026-10-12-zhixu-writing-phase-b-plan.md
created_at: 2026-10-17
status: complete
updated_at: 2026-10-17
---

# 创作空间：同类产品与本地能力调研

## 1. 结论与证据边界

推荐把创作空间设计为“开箱即用的内置创作团队”：文章侧边栏提供写作任务入口，主编 Agent 自动协调固定成员，专属 Skill 定义方法与产物，工具/可选 MCP 提供资料能力，文章库负责正式内容与版本。

用户已选定公众号、博客、知识类文章，并明确要求创作空间的专属内置资源在对应管理页标识、不可删除。用户随后明确：固定团队直接出现在创作侧边栏，无需用户指定团队；先前“可编辑工作流设计器”的理解已撤销。流程是内置团队的执行方式，首版不提供节点编辑、团队选择或 Skill/MCP 配置向导。

本次真实联网检索并打开官方文档与上游 README，另由只读子代理核对仓库。**未登录试用竞品，未实际运行其模型，不把官方功能说明当作效果评测。** 下文“借鉴”均为结合本项目提出的设计判断。价格不参与本次方案选择。

## 2. 外部产品与项目

| 对象 | 已核实的能力与来源 | 写手收益 | 对本项目的借鉴与局限 |
| --- | --- | --- | --- |
| Lex | [Chat](https://lex.page/read/60e61da1-8579-4674-901e-689f65347330) 使用当前文档上下文；[Knowledge Bases](https://lex.page/read/d70b2ea4-00ff-4abb-a7d3-097a1140a7f0) 复用文件、公开 URL 等资料；[Checks](https://lex.page/read/c563b895-d226-40c6-988f-33add2ff1dac) 在文档中提出可处理的建议 | 减少复制文章，反馈和原文保持关联 | 采用文章内的来源包和逐项审阅；资料已加入不代表事实已核验，长资料仍需上下文预算 |
| Novelcrafter | [Codex Tracking](https://www.novelcrafter.com/help/docs/codex/codex-tracking) 控制设定是否进入上下文；[多场景聊天](https://www.novelcrafter.com/help/faq/chat/chat-multiple-scenes) 可选择章节/场景范围 | 作者知道 AI 本次参考了什么 | 映射为本次文章、选区、勾选素材与风格样例；小说设定检测不直接搬到知识写作 |
| Sudowrite | [Story Bible](https://docs.sudowrite.com/using-sudowrite/1ow1qkGqof9rtcyGnrWUBS/what-is-story-bible/jmWepHcQdJetNrE991fjJC) 把创作信息传递给后续步骤；[Scenes & Draft](https://docs.sudowrite.com/using-sudowrite/1ow1qkGqof9rtcyGnrWUBS/scenes--chapter-prose/49p5MTVxTKkVFEC5rVUzpY) 支持生成前检查与作者处理 | 先整理结构，再展开正文 | 借鉴阶段依赖、生成前检查；其小说流程与章节篇幅机制不直接作为公众号模板 |
| Dify（已排除的产品形态） | [内容工作流教程](https://docs.dify.ai/en/quick-start) 展示节点、输入输出引用和运行；[Human Input](https://www.dify.ai/blog/the-human-input-node-bringing-human-judgment-into-automated-workflows) 让流程等待人工编辑或判断 | 可参考人工交接点 | 用户明确排除配置型体验；不做画布、节点编辑或团队选择，不引入其依赖 |
| STORM / Co-STORM（开源） | [上游 README](https://github.com/stanford-oval/storm/blob/main/README.md) 区分资料搜集/提纲与正文生成，并描述专家、主持人与用户协作 | 写前形成资料和结构，用户可改变探索方向 | 借鉴来源先行与有限角色协作；上游明确输出仍需要编辑，不把自动生成等同于可发表 |
| Open Deep Research（开源历史参考） | [上游 README](https://github.com/langchain-ai/open_deep_research/blob/main/README.md) 的 Legacy 节列出人工参与规划与主管/研究员两种实现 | 对照人工介入与并行研究的取舍 | 仅作模式参考；页面标明 2026-08-21 已归档，且上述部分属于 legacy，不能声称为当前维护中的主实现 |

## 3. 现有项目事实

| 事实 | 可核对位置 | 判断 |
| --- | --- | --- |
| 文章与素材、提纲可真实保存，存在版本检查和请求去重 | `nanobot/writing/database.py:14`、`repository.py:178`、`webui/src/lib/writing-api.ts:305` | 保留并复用，不重做文章库 |
| 自动保存和中文组合输入已有保护 | `webui/src/components/creative/use-article-save-flow.ts:29`、`ArticleEditor.tsx:129` | AI 更新必须接入既有保存队列，不能强制回写 textarea |
| AI 区仍是阶段 B 占位；正文未追踪选择范围 | `ArticleEditor.tsx:210`、`:413` | 真实任务、选区、建议应用均待接通 |
| `SuggestionPanel` 是未接入生产的本地组件 | `SuggestionPanel.tsx:23`，全仓引用检索 | 不能把演示建议作为 AI 链已完成的证据 |
| Phase B 计划为 draft/approved:false，collab 目录及写作 Skill 尚不存在 | 2026-10-12 phase-b-plan、`nanobot/writing/`、`nanobot/skills/` | 旧计划可借用版本保护等约束，但需按新工作流与 Team 事实修订 |
| Team 已有依赖、并发、超时、冻结档案和结构化成员结果 | `nanobot/teams/scheduler.py:450`、`execution.py:94`、`nanobot/agent/subagent.py:601` | 复用执行基础；人工暂停与文章产物不是现成能力，需写作层补齐 |
| 系统 Agent 删除已有 409 保护，保存时按工厂 ID 重算类型 | `nanobot/agents/store.py:163`、`:178` | 增加创作归属和稳定资产身份，避免仅靠可变 type 判定 |
| builtin Skill 已有不可删保护与管理分组 | `nanobot/webui/skills_api.py:94`、`:168`、`SkillsCatalogSettings.tsx:58` | 复用现有行为，并显示“创作空间内置” |
| workspace 同名 Skill 会遮蔽 builtin | `nanobot/agent/skills.py:102` | 工作流须按来源与固定身份解析，不可只绑定名称 |
| MCP preset 连接可被移除，custom/import 可同名覆盖 | `nanobot/webui/mcp_presets_api.py:1457`、`:1564` | 内置定义、连接状态和凭据应分开；补删除与覆盖校验 |

## 4. 多角度综合

- **作者视角：** 进入文章后能知道下一步、得到可用产物；“补一个例子”“找依据”“删去重复”比泛聊天入口更贴近任务。
- **主编视角：** 不同角色的输出要共享目标与已确认提纲；审稿问题必须定位到段落，避免多位 Agent 分别写出彼此冲突的全文。
- **研究视角：** 来源要随资料卡和建议传递；有链接但没读正文，应显示为未读取，不能称已验证。
- **产品视角：** 侧栏直接发起“构思、找资料、列提纲、写初稿、改写、审稿”；主编选择成员，用户只表达写作意图。局部改写无须启动所有角色。
- **运行时视角：** Team 解决执行资源，写作层解决固定步骤、人工交接、来源、文章快照和采纳。无需建设通用工作流引擎。
- **管理视角：** “创作空间内置”是服务端权威事实；禁止删除与禁止关闭不是一回事。可选 MCP 保留内置入口，但连接和授权必须由用户控制。

## 5. 方案比较

| 方案 | 收益 | 代价/缺口 | 结论 |
| --- | --- | --- | --- |
| A：局部按钮直接调用单 Agent | 最快形成局部 AI 能力 | 缺少资料、结构、写作和审稿之间的协调 | 不作为最终交付 |
| B：侧栏固定内置创作团队，主编协调，复用 Team | 打开文章即可用；从目标到建议形成连续产物 | 需固定任务配方、文章适配和内置归属保护 | 推荐，符合用户最终要求 |
| C：用户配置工作流或选择团队 | 灵活度高 | 把组队和配置负担交给写手 | 用户明确排除 |

## 6. 优先级与交接

首批需形成完整闭环：打开文章 → 使用侧栏内置团队 → 主编自动派工 → 带来源产物 → 作者确认 → 服务端版本校验采纳 → 重开恢复。现有全局模型配置直接复用，不新增必填配置。MCP 是团队的可选资料能力，默认写作不依赖额外账号；第三方服务只引用已配置连接，不自动安装或授权。

后续可增加本地只读资料 MCP、作者主动选入的风格样例和渠道适配。它们不能用于替代首批真实协作的完成标准。

配套方案：`.ai-runtime-artifacts/specs/2026-10-17-writing-team-sidebar-spec.md`。本调研不构成旧 Phase B 计划已获批准或已经实现的证据。

## 7. v2 补充：可复用素材与实测

已从 SkillHub 发现入口追到官方上游，下载 Content Research Writer 与 Humanizer-zh，两份原始 Skill 的精确 commit、SHA-256、许可和清理说明位于 `.ai-runtime-artifacts/research/writing-skill-assets-v2/README.md`。另有三份适配提示词草案。没有把市场旧缓存或来源不明包当作内置能力激活。

本次进一步核实：Team 现成能力不包括内置 factory、跨批作者交接、完整长稿下游传递和 Skill 正文冻结；“沿用 Team”需要具体文章适配，而非只绑定一个团队 ID。相关边界已写入 spec v2 §13。

新鲜前端全量为 1636 通过、1 失败、2 跳过；lint/build 通过。真实浏览器验证了编辑、素材、提纲和分屏，并两次复现预览新建重复记录。测试证据见 `2026-10-17-writing-frontend-baseline-v2.md` 与 `2026-10-17-writing-browser-v2.md`；不据此宣称尚未实现的固定团队已可用。
