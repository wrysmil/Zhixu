---
artifact: verification-lite
route: leader-direct
skills: [technical-writer, source-driven-development, verification-before-completion]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/technical-writer/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - 用户要求调研并重写属于知序的 README
  - .ai-runtime-artifacts/research/2026-10-19-zhixu-readme-research.md
created_at: 2026-10-19
tier: 1
---

# 知序 README 改写验证

## 范围

- 用户交付：根 `README.md`。
- 过程产物：本验证与同日 README 调研记录，均位于 `.ai-runtime-artifacts/`。
- 文档任务由主 Agent 直接完成；未改运行时代码、配置与用户数据。正文交付后，用户追加要求 commit，按下方限定范围提交；未推送。
- TDD gate: N/A (no production code)。

## 命令与结果

| 检查 | 本轮结果 |
| --- | --- |
| `git diff --check` | 通过，退出码 0 |
| `git diff --stat` | 用户文档改动仅 README，127 insertions / 682 deletions；770 行改为 215 行 |
| `.venv/Scripts/python.exe -m nanobot --version` | 退出码 0，`nanobot v0.3.0`，证实实际命令名仍是 nanobot |
| `.venv/Scripts/python.exe -m nanobot webui --help` | 退出码 0，确认 WebUI 入口与 `--dev` |
| `.venv/Scripts/python.exe -m nanobot agent --help` | 退出码 0，确认 `--classic`、`-m` |
| `.venv/Scripts/python.exe -m nanobot gateway --help` | 退出码 0，确认 `--background` |
| 只读 Python 检查本地链接与图片 | 21 个目标，0 缺失 |
| 只读 Python 检查首屏页内导航 | 4 个锚点，0 缺失 |
| 只读 Python 检查编码与代码围栏 | UTF-8 无 BOM，无替换字符，代码围栏成对 |
| 只读 Python 检查旧宣传残留 | 无 nanobot.wiki、上游 raw 安装脚本、上游 Stars 徽章、贡献者墙标记、Latest release 或访客统计 |
| package.json 检查 | WebUI build/dev 与 desktop start 脚本存在 |
| Logo 文件与人工查看 | 1254×1254 PNG，使用现有知序图像 |

只读检查使用标准库 `pathlib/re/json/struct`，读取 README 与 package.json，解析本地 Markdown 引用、HTML img、标题锚点和 PNG 尺寸；不写脚本或缓存文件。

## 未验证项

- 没有在干净机器重新安装依赖、构建客户端或启动网关；帮助命令通过不代表完整启动验收。
- 没有发送真实模型请求或验证记忆准确率、写作质量、渠道接入效果。
- 没有运行业务测试套件；本次只修改文档，验证限定于文档与其中命令的有效性。
- 详细指南中的上游安装与社区信息尚未逐篇迁移；根 README 已明确适用范围。

## Next

- 文档初稿已可供用户审阅，无需另行批准后才能查看正文。
- 若后续调整定位、截图或安装发布方式，基于本稿继续编辑；引擎改名与独立发行另按对应任务处理。

## 追加验证：图文增强

- 新增三个最终交付截图：`images/readme/zhixu-home.jpg`、`zhixu-creative.jpg`、`zhixu-editor.jpg`；README 已实际引用，不依赖 `.ai-runtime-artifacts/` 中的历史图片。
- 浏览器只在 `?preview=1` 中切换语言、导航和选择分屏。截图均人工查看，页面带“界面预览 · 示例内容”，无真实用户数据、凭据或真实模型输出。
- 图片校验：Pillow `Image.verify()` 通过，均为 1057×956 JPEG；大小依次为 46,689 / 67,032 / 124,030 字节（首页 / 创作 / 编辑）。
- 本地引用校验：25 个 Markdown 链接与 HTML 图片路径全部存在；代码围栏成对，包含 2 个 Mermaid 图。
- Mermaid 语法校验：使用项目现有 `mermaid` 与 `happy-dom` 依赖解析 README 的两个图，输出 `Mermaid 1: syntax valid`、`Mermaid 2: syntax valid`，退出码 0。
- 解析器首次在无 DOM 的 Node 环境中因 DOMPurify 缺少浏览器对象失败，补用已有 happy-dom 后通过；未修改图语义、依赖或产品代码来绕过检查。
- 三个截图来自实际浏览器默认视口，无图像合成与后处理。没有重新安装、构建或启动业务服务，使用既有本地服务。
- 最终仍未进行真实模型执行验证，也未验证 GitHub 服务端 Mermaid 的具体排版；本轮校验为语法、文件有效性与截图视觉检查。

## 用户授权提交

- 用户原话：`comit`。
- 提交范围：`README.md`、三张 `images/readme/zhixu-*.jpg`、同日 README 调研记录与本验证记录，共 6 个文件。
- 提交前重新检查 `git diff --check`、25 个本地引用、3 张图片有效性，均通过。
- 工作区其他团队 / 写作数据、截图和缓存保留原状，不加入本次提交；不执行 push。
