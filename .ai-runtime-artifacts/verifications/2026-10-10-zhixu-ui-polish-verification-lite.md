---
artifact: verification-lite
route: leader-direct -> frontend-design -> verification-before-completion
skills: [source-driven-development, frontend-design, verification-before-completion]
source:
  - 用户要求优化整站 UI 细节，并追加桌面版也要优化
  - .ai-runtime-artifacts/specs/2026-10-10-zhixu-workspace-intent.md
  - .ai-runtime-artifacts/contracts/2026-10-10-contract-zhixu-webui-static.md
created_at: 2026-10-10
status: partial
tier: 1
---

# 知序 WebUI / 桌面共用界面细节优化

## 边界与上下文

扫描既有知序产物后读取产品意图、静态接入契约、执行日志、project.profile.md、实际 package.json 和目标源码。本轮为上一版 UI 的有界表现层修整，直接修改两份共用源码：WorkspaceNavigation.tsx、globals.css。不改会话恢复、路由与数据请求逻辑；不修改其他任务的新配色方案产物。

实际栈：React 18、Vite 5、Tailwind 3。纯 JSX 结构与 CSS 修整，未引入框架 API 或依赖。CSS 规则参照 https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Selectors/:focus-visible 和 https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Selectors/:lang 。

## 改动

- 品牌区增大文字并增加留白；导航按钮保持 40px 最小高度，统一 8px 圆角与内侧间距。
- 连接状态从绝对定位改为侧栏正常文档流，修复状态文字遮挡折叠按钮；长状态可换行。
- 短窗口侧栏独立滚动，分组与控件不压缩；桌面原有全局隐藏滚动条规则对导航做局部例外。
- Electron 共用侧栏顶部留白 48px，避开 App.tsx 中 44px 的 HostChrome；保留原有拖拽和非拖拽行为。
- 中文字体优先使用系统中文字体；默认行距定义移到语言规则前，避免覆盖中文正文 1.8 行距。
- 输入区行距 1.75，补充基础键盘焦点，导航焦点向内避免裁切；减少动态效果设置下取消导航过渡。

## 验证

- npm run build：最终版本 exit 0，2927 模块，构建成功；保留既有大于 500kB chunk 警告。
- npm run lint：exit 0（最后改动仅 CSS 状态字号，未再修改 TSX）。
- git diff --check：exit 0。
- 相关回归测试：workspace-navigation、preview-isolation、creative-workspace 共 3 个文件、14 项全部通过（单 worker，exit 0）。
- desktop/npm run smoke：exit 0，验证桌面壳启动；不是桌面原生窗口的视觉验收。
- 全量测试默认并发：5 failed / 1276 passed。减少到 2 个 worker 与 1 个 worker 后均为 1 failed / 1280 passed，总计 1281 项。未将失败降级为成功。
- 未通过项：thread-shell.test.tsx / retries a failed hydration when the page returns to the foreground，historyCalls 期望 2、实际 1。该测试及 ThreadShell 不引用 WorkspaceNavigation；本轮未改会话恢复逻辑，但没有对 HEAD 单独复现，因此不声称已证明为基线失败。
- 实际浏览器工作台：折叠按钮 bottom=668，状态 top=676，不再重叠。
- 900×560 检查：导航 clientHeight=560、scrollHeight=617，可滚动；文档无横向溢出，折叠与展开可操作。检查后恢复默认视口。
- 浏览器 error/warn 日志为空。真实源码预览入口 http://127.0.0.1:5180/?preview=1#/home 。
- 截图：.ai-runtime-artifacts/research/zhixu-ui-polish/home-polished.jpg 。
- TDD：纯表现层结构/CSS修整，无新业务逻辑；复用现有回归测试，未新增镜像样式的单元测试。

## Next

细节修改已落地到正式 WebUI，并由桌面壳共用。全量测试尚有上述失败，不能宣称全部验证通过；桌面原生窗口的视觉与拖拽体验仍需实际打开确认。
