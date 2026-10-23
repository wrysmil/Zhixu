---
artifact: research-report
route: source-driven-development
skills: [source-driven-development]
skills_evidence:
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/source-driven-development/SKILL.md
source:
  - webui/package.json
  - webui/node_modules/*/package.json
  - webui/tailwind.config.js
  - webui/src/hooks/useTheme.ts
  - webui/src/globals.css
created_at: 2026-10-19
status: verified
---

# 外观自定义技术栈核对

本轮仅设计前端外观设置；不新增后端 API，不升级依赖。

| 依赖 | package.json 声明 | 本机已安装精确版本 |
| --- | --- | --- |
| React / React DOM | ^18.3.1 | 18.3.1 / 18.3.1 |
| Vite | ^5.4.11 | 5.4.21 |
| Tailwind CSS | ^3.4.17 | 3.4.19 |
| TypeScript | ^5.7.2 | 5.9.3 |
| Vitest | ^2.1.8 | 2.1.9 |
| Radix Popover | 1.1.15 | 1.1.15 |
| xterm | ^6.0.0 | 6.0.0 |

精确版本由本机依赖包 manifest 读取；仓内同时存在 bun.lock 与 package-lock.json，实施时沿用项目命令，不因本功能重写锁文件。

## 接入基础

- Tailwind 3 使用 class 暗色模式，颜色已映射至 HSL CSS 变量，字体为固定栈。
- useTheme 由 App 调用，ThemeProvider 只提供已解析的 light/dark，代码高亮依赖 useThemeValue。
- index.html 已有明暗模式首屏脚本及旧品牌存储键迁移。
- 界面本地偏好已有独立存储，与服务器配置保存分开。
- Teams/Agent 编辑页另有固定色值，CodeBlock/DiffSyntaxHighlight/TerminalSurface 另有固定字体，须显式适配。

## 已核实的官方资料

- [MDN：CSS 自定义属性](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Cascading_variables/Using_custom_properties)：根节点变量可被下层样式引用，适合延续项目语义变量模式。
- [MDN：localStorage](https://developer.mozilla.org/en-US/docs/Web/API/Window/localStorage)：按 origin 保存，跨浏览器会话保留；访问失败需要降级。
- [MDN：MediaQueryList change](https://developer.mozilla.org/en-US/docs/Web/API/MediaQueryList/change_event)：系统外观变化可通过 change 事件跟随。
- [MDN：font-family](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/font-family)：使用字体回退栈，不能承诺未安装的本机字体一定可用。
- [xterm：Terminal.options](https://xtermjs.org/docs/api/terminal/classes/terminal/#options)：支持更新字体和主题选项；主题对象需以新对象赋值。本地 6.0.0 类型声明在实施时再次核对，官方站点未标明对应版本，不能据此假定所有接口一致。

## 验证边界

已完成只读源码、依赖与文档核对。未执行构建、测试或实际 UI 验收；本文件不代表实现完成。
