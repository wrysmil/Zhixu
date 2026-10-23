---
artifact: research-report
route: brainstorming + technical-writer
skills: [brainstorming, technical-writer, source-driven-development]
skills_evidence:
  - .claude/skills/brainstorming/SKILL.md
  - C:/Users/Huangqh/.codex/skills/technical-writer/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
source:
  - 用户要求调研并直接改出一版属于知序的 README
  - harness-kit/core/routing.md
  - harness-kit/project.profile.md
  - harness-kit/context-map.md
  - .ai-runtime-artifacts/specs/2026-10-19-zhixu-rename-spec.md
  - .ai-runtime-artifacts/specs/2026-10-17-writing-conversation-team-spec.md
  - .ai-runtime-artifacts/research/2026-10-17-writing-conversation-team-research.md
  - https://github.com/HKUDS/nanobot
  - https://github.com/wrysmil/Zhixu
created_at: 2026-10-19
status: completed
---

# 知序 README 改写调研

## 任务与范围

用户要求“先调研下然后改一版”，交付为直接编辑根 README 的文档稿，不引入产品行为变更，也不以另写 spec/plan 代替正文。沿用 brainstorming 的上下文梳理、路线比较与自查方法；不进入功能设计或编码阶段。

本次读取当前源码、相关方案与近期提交，并访问两个 GitHub 项目主页核对来源身份。事实以本地 `feature/desktop-app` 分支、调研时 HEAD `f935137` 为准；旧文档和历史记忆只作为检索线索。现有未跟踪的 `teams/`、`writing/` 及他人验证产物不属于本次编辑范围。

## 原 README 的问题

- 正文虽然已替换部分展示品牌，首屏仍挂上游语言站点、Stars、PyPI、CI、下载量与社群入口。
- 安装脚本、clone 地址和 PyPI 包指向上游，读者照做会获得原项目。
- Releases、Recent Updates、合作方和大型贡献者头像墙仍代表上游，无法说明知序自己的开发重点。
- 能力介绍偏通用框架，未充分突出当前三层记忆、项目工作台和创作空间。
- 原文件 770 行，大量篇幅用于身份与版本不属于知序的展示信息。

这些问题既来自本地 README 检查，也可与 [上游项目](https://github.com/HKUDS/nanobot) 首页结构相互核对。本仓库当前远程地址为 [wrysmil/Zhixu](https://github.com/wrysmil/Zhixu)。

## 定位与结构取舍

| 路线 | 优点 | 问题 | 选择 |
| --- | --- | --- | --- |
| 产品入口为主，开发信息靠后 | 新读者先知道用途，再知道怎么运行；能体现自己的方向 | 需要收紧能力承诺并核对启动方式 | 采用 |
| 延续超轻量 Agent 框架介绍 | 改动较少，覆盖面广 | 仍然接近上游，记忆与创作差异不突出 | 不采用 |
| 只讲三层记忆 | 差异明确，适合技术专题 | 无法覆盖项目工作区、文章与桌面入口 | 不采用 |

面向愿意本地运行、配置模型的中文用户与开发者。标题定位为“有记忆、能执行的个人 AI 工作台”。正文顺序：定位 → 使用场景 → 记忆 → 项目工作 → 创作 → 安装 → 数据与模型 → 扩展 → 参与 → 致谢与许可。

## 关键事实与源码依据

| 事实 | 本轮核对依据 | README 表述边界 |
| --- | --- | --- |
| 品牌与 Python 引擎改名进度不同 | `pyproject.toml` 的 scripts/name；`nanobot/cli/entry.py`；rename spec 与近期 Phase 1/2 提交 | 保留真实的 `nanobot` 命令和 `~/.nanobot`，不虚构 `zhixu` CLI 或已发布包 |
| 三层记忆已存在于当前分支 | `nanobot/memory/models.py`；`nanobot/agent/loop.py` 的提取 hook 装配；`nanobot/agent/hooks/memory_extraction.py`；`nanobot/agent/context.py`；`nanobot/agent/tools/memory_search.py` | 不把 profile 中“历史分支工作”的旧概述当作当前事实；不承诺每次都能准确记住 |
| 记忆默认关闭 | `nanobot/config/schema.py` 的 `memory_enabled=False`，默认全文检索；`MemorySection.tsx` 的设置开关 | 明确首次开启步骤与额外模型调用 |
| 记忆管理页面与接口已存在 | `MemoryPage.tsx`；`MemorySection.tsx`；`webui/src/lib/api.ts`；`nanobot/webui/memory_routes.py` | 区分语义条目编辑和情节浏览；不把三层都描述为相同的 CRUD 页面 |
| 创作已有实际团队对话和文章工具 | `CreativeWorkspace.tsx`；`WritingChatPanel.tsx`；`ArticleEditor.tsx`；`nanobot/writing/run_extension.py`；`nanobot/writing/tools.py` | 介绍当前保存、选区、文章工具与对话，不仅照抄 10 月 17 日设计中的“尚未接线”结论；不声称本轮验证真实模型质量 |
| WebUI 源码需构建 | `hatch_build.py` 的 editable 跳过构建；`webui/package.json` | 给出显式前端构建，使用可选 api 依赖提供 aiohttp；不复制上游一键安装脚本 |
| 桌面版是 Electron 壳 | `desktop/package.json`；`desktop/main.cjs`；`desktop/scripts/run.cjs` | 先配置构建 WebUI，再启动桌面；明确需要 Python 后端，不宣称独立完整安装包 |
| 登录密钥来自现有配置 | AGENTS.md 登录说明；`desktop/main.cjs`；`nanobot/cli/webui.py` | 只说明字段位置与回退，不读取或展示本机实际密钥 |
| 上游和版权须清楚保留 | `LICENSE`；`THIRD_PARTY_NOTICES.md`；上游主页 | 保留来源段与 MIT 链接，移除上游宣传入口，不修改许可 |

## 具体编辑

- 根 README 重写为 215 行中文文档，使用项目现有 Logo，不使用上游界面截图冒充知序。
- clone、Issue 和 PR 入口改为当前仓库，源码安装指定当前主线分支。
- 删除上游下载量、版本、伙伴、贡献者头像与访客统计展示。
- 在开发入口中保留本地文档链接，并提示详细指南仍可能带有上游安装与社区说明。
- 不修改 Python 包、配置数据、许可证、详细指南或运行中服务。

## 验证边界

已做 Markdown 结构、路径、Logo、旧身份残留与 CLI 帮助检查。没有在全新环境执行安装，没有调用真实模型，也没有验证全部能力的端到端质量；源码事实核对不等于功能验收。结果见同日 README verification-lite。

## 追加：参考同类项目后的图文版

用户进一步要求“一些图片，或者说明，参考下别人的项目”。本次读取官方仓库首页与 README，参考信息组织方式，不复制第三方截图和素材：

| 参考项目 | 实际观察 | 本稿采用的方式 |
| --- | --- | --- |
| [Cherry Studio](https://github.com/CherryHQ/cherry-studio) | 首屏品牌后有产品图片，之后展开使用入口与能力 | 在介绍后直接展示知序首页，先建立界面印象 |
| [Flowise](https://github.com/FlowiseAI/Flowise) | README 使用大幅 Agentflow GIF 展示可视化操作，再接 Quick Start | 把文章编辑器放到对应功能段，用图注解释用途；本轮用静态截图，不制作执行演示 GIF |
| [Mem0](https://github.com/mem0ai/mem0) | 使用品牌横幅，结合能力说明和 Basic Usage 解释记忆产品 | 为知序加入记忆流程示意与可尝试的任务示例；不引用其性能或效果数字 |
| [Dify](https://github.com/langgenius/dify) | 功能说明与 Quick Start 分层组织 | 保持项目能力和安装步骤的层次，不把竞品介绍写入知序正文 |

官方 README 原文亦通过 `raw.githubusercontent.com/<owner>/<repo>/main/README.md` 只读访问，确认图片、GIF 和段落位置；没有下载第三方媒体。

### 资产来源与选择

全部新增图片保存到 `images/readme/`，来自本轮浏览器实际截图，未合成或改写界面：

| 文件 | 页面与状态 | 用途 |
| --- | --- | --- |
| `zhixu-home.jpg` | 本机 `/?preview=1#/home`，简体中文 | 介绍后展示任务入口、项目选择与话题 |
| `zhixu-creative.jpg` | 本机 `/?preview=1#/creative`，未编辑的内置示例文章列表 | 放在折叠区，展示模板、草稿与文章列表 |
| `zhixu-editor.jpg` | 从内置示例文章进入编辑器，选择“分屏” | 展示 Markdown 编辑与渲染预览 |

截图使用浏览器默认视口，三张均为 1057×956。README 正文和图注明确其为“界面预览 / 示例内容”，文章编辑图注明确不展示真实团队执行。没有发送模型请求，没有写入真实文章或记忆。

检查过现有 `images/zhixu-workbench.png`，它仍是上游样式；历史文章截图含旧 AI 占位面板，均不采用。现有 memory-ui 截图含真实个人记忆，本轮不复制到公开文档。当前 `#/memory` 预览入口展示占位页，因此改用机制图解释记忆，不为补图修改产品代码。

### 新增说明

- 三个任务表达示例：读项目并整理文档、围绕已有文章调整提纲、表达并核对长期偏好。
- 一张 Mermaid 图解释对话、工作记忆、语义 / 情节提取、检索与后续上下文之间的用途关系；明确不是每轮必定发生的流水线。
- 将原纯文本架构示意改为 Mermaid，区分网关入口、渠道消息总线与经典终端 / SDK 路径。
- 验证限定于截图、文档结构和 Mermaid 语法；没有新增产品功能或端到端效果承诺。
