---
artifact: stack
route: source-driven-development
created_at: 2026-11-01
status: completed
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
---

# 目标页技术栈

- 目标：`/Users/mima0000/Documents/学习-001/do-project/dontforget/design-demo/index.html`。
- 单文件 HTML5 + CSS + 浏览器原生 JavaScript；无框架、无版本化前端依赖、无 package.json、无构建步骤。
- 原版请求 Google Fonts（Fraunces / Inter / JetBrains Mono），本轮改成系统字体并移除字体网络请求。
- 验证环境 Node.js v24.13.0；使用本机已缓存 Playwright 和 Chromium headless shell（cache revision 1223）。未新增或安装项目依赖。
- zhixu 的 React / Vite / Electron 依赖不用于这个静态页面，不把相邻项目运行测试当作此原型验收。
