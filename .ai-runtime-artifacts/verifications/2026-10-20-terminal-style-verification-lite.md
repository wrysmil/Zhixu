---
artifact: verification-lite
route: leader-direct
skills: [frontend-design, source-driven-development, verification-before-completion]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/frontend-design/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - 用户要求优化 CMD 样式
  - .ai-runtime-artifacts/specs/2026-10-19-appearance-customization-spec.md
  - .ai-runtime-artifacts/specs/2026-10-11-home-dock-pty-terminal-v1.1-spec.md
created_at: 2026-10-20
tier: 1
status: passed
---

# CMD 面板样式优化验证

## 范围与上下文

已先扫描相关项目产物，读取全局外观与终端规格、project.profile.md、context-map.md。本轮为现有终端局部视觉修整，按 Tier 1 直接处理，不扩展终端功能或执行已有多工作单元计划。

本轮只修改 `webui/src/components/workbench/TerminalSurface.tsx`，保留该文件原有未提交的工具栏、剪贴板和主题相关变更。未修改后端、全局主题配置、用户偏好或依赖。

## 变更

- 带轻边框、圆角和内外留白的终端容器。
- 顶部显示 shell 名称、状态色点与截断的工作路径，完整路径保留 title。
- 复制、清屏、粘贴按钮合并为统一尺寸的工具组，补齐可见键盘焦点样式。
- xterm 字号 13px，行高 1.55，输出区增加留白。
- 底部命令栏增加提示符与 Enter 按钮，输入高度增至 36px，保留回车提交与上下键历史。
- 管道模式也观察输出容器尺寸并调用现有 fit；不向管道终端发送 PTY resize 帧。避免固定默认列宽撑出横向滚动条。
- 继续使用现有背景、前景、边框和代码字体语义变量，沿用既有外观更新机制。

## 验证

| 检查 | 结果 |
| --- | --- |
| `npm run test -- --cache=false src/tests/terminal-surface.test.tsx src/tests/start-dock.test.tsx` | 2 文件、49 项通过；存在已有 act 警告。 |
| `npx eslint src/components/workbench/TerminalSurface.tsx --max-warnings 0` | exit 0，无输出。 |
| `npm run build` | TypeScript 与生产构建通过，2986 modules，built in 13.24s。 |
| Chrome GUI | 8765 首页新建 CMD，出现新版顶部路径、工具组和底部命令栏；执行 `echo Terminal ready` 返回 `Terminal ready`，命令输入恢复为空。 |

首次构建因系统临时目录中的 esbuild 文件删除权限失败；将该构建进程的 TEMP/TMP 指向 `.ai-runtime-artifacts/verifications/2026-10-20-terminal-style-temp` 后完成。未调整系统级环境变量。

未新增镜像实现的样式断言测试；运行现有交互回归与真实浏览器检查。未重跑整个 WebUI 测试套件，未另起 Electron；深色主题未做本轮 GUI 切换验证。

截图：[实际终端面板](2026-10-20-terminal-style.png)。

## 参考依据

- [xterm ITerminalOptions](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#lineheight)：fontSize、lineHeight；已核对本地 xterm 6.0.0 类型声明。
- [ResizeObserver](https://developer.mozilla.org/en-US/docs/Web/API/ResizeObserver)：容器尺寸观察与断开监听。

## Next

- 刷新页面即可加载新样式。
- 本轮未提交、未推送。
