---
artifact: research-report
route: web-investigator -> find-skills -> superpowers:brainstorming
skills: [find-skills, best-practice-research, brainstorming]
skills_evidence:
  - /Users/mima0000/.agents/skills/find-skills/SKILL.md
  - /Users/mima0000/.codex/skills/best-practice-research/SKILL.md
  - /Users/mima0000/.agents/skills/brainstorming/SKILL.md
source:
  - 用户要求查找并下载可用 Skill/提示词，清理作者信息，细化前后端方案
  - .ai-runtime-artifacts/specs/2026-10-17-writing-team-sidebar-spec.md
created_at: 2026-10-17
status: downloaded-and-adapted-for-review
---

# 创作团队可复用素材包

已下载两份纯 Markdown Skill，固定上游 commit，校验清单为 `download-manifest.json`。未执行其中命令、脚本或安装指令，未安装到 Codex/Claude/nanobot 的任何活跃 Skill 目录。

`upstream/` 为来源证据，保留原文与许可；`prepared/` 是去掉平台耦合、个人路径、品牌宣传后的提示词草案，供方案审阅和后续转为产品 Skill。它们尚未经过真实模型效果验收。

## 来源、许可和决定

| 素材 | SkillHub 发现入口 | 已核实上游与许可 | 处理 |
| --- | --- | --- | --- |
| Content Research Writer | [市场副本](https://www.skillhub.club/skills/adcage-awesome-claude-skills-content-research-writer) | [上游](https://github.com/ComposioHQ/awesome-claude-skills/tree/be2a406907dbc61b73e6827ded415c96139d13a2/content-research-writer)；根 README 声明 Apache-2.0；GitHub contents API 确认该目录只有 SKILL.md，无独立 LICENSE/NOTICE | 下载官方原文与许可证；改编为研究写作提示词，移除示例数字、Claude/VS Code 启动说明和 `~/writing/` 文件操作 |
| Humanizer-zh | [市场](https://www.skillhub.club/skills/op7418-humanizer-zh) | [上游固定版本](https://github.com/op7418/Humanizer-zh/tree/f4518a8eab97b8bfebc66a89d34320a89bef6930)；MIT，许可已下载 | 采用 2026-09-23 修订版的保真编辑规则；市场旧缓存不作为执行文本；改编为审校提示词 |
| 公众号排版 Skill | [市场](https://www.skillhub.club/skills/konglong87-methodology-skills-wechat-article-writer) | [上游](https://github.com/konglong87/methodology-skills/tree/main/skills/wechat-article-writer)；MIT | 暂不纳入运行包。实际依赖 Node、配图生成器、Playwright 和相邻文件，单个 SKILL 不是完整排版器 |
| 个性博客写手 | [市场](https://www.skillhub.club/skills/nicknisi-claude-plugins-blog-post-writer) | [上游](https://github.com/nicknisi/claude-plugins/tree/main/plugins/content/skills/blog-post-writer)；MIT | 不整份复制。含个人语料、私人绝对路径、采访流程与附属脚本；仅记录“事实有来源、体验由本人提供”的设计原则 |
| DaisyWriter 公众号写作 | [市场](https://www.skillhub.club/skills/ricky-theseus-daisywriter-wechat-article-writer) | [上游](https://github.com/ricky-theseus/DaisyWriter/tree/master/skills/wechat-article-writer)；GPL-3.0 | 未下载纳入本包；模板/配图/发布导引和分发约束都比当前纯写作链更重 |
| 腾讯 SkillHub 公众号写作台 | [市场](https://skillhub.cn/skills/davtime/wechat-writer-pro) | `skillhub.tencent.com` 重定向至 `skillhub.cn`；本轮未核实该条目的上游与包许可 | 只列发现，不当作已许可素材下载或启用 |

## 清理范围

- 运行提示词不出现原作者署名、社交账号、品牌推广、社群、个人网站或私人目录。
- 保留通用写作概念“作者声音”，它指产品用户，不是第三方署名。
- 版权与许可证、来源、修改说明集中保留在本包，不输出到用户稿件。MIT 的版权/许可文字不能删除；Apache 许可与修改说明随改编内容一起保留。
- Composio 原文无 author 元数据；根 README 含推广，仅作为许可证据归档，不进入运行上下文。Humanizer 的来源 metadata 转入本清单，正文不要求使用或署名特定作者。
- 研究写作原文中的百分比、公司/机构名称和专家引语是示范内容；适配稿全部改用资料引用或明确“待补”，不能作为现成事实。

## 草案文件

- `prepared/writing-editor.prompt.md`：本项目原创主编协调提示词，固定角色、少提问、产物交接。
- `prepared/article-draft.prompt.md`：研究写作方法改编，面向公众号、博客、知识文章。
- `prepared/article-review.prompt.md`：中文审校改编，保留事实、确定性、代码和作者立场。

实施时按产品的七个 Skill 任务边界拆分/引用方法，不把三份草案当成七个 Skill 已实现的证据；也不同时向每位成员灌入全部材料。技能输入输出与文章版本控制由宿主协议提供。
