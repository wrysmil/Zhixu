---
artifact: spec
route: source-driven-development -> brainstorming
skills:
  - source-driven-development
  - brainstorming
skills_evidence:
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
  - /Users/mima0000/.agents/skills/brainstorming/SKILL.md
source:
  - 用户 2026-10-11 提供的 Codex 与 Mira 截图及本会话视觉评审
  - 用户要求优化首页、侧边栏图标、浏览器和终端 UI
  - .ai-runtime-artifacts/specs/2026-10-09-codex-parity-ui-workbench-spec.md
  - .ai-runtime-artifacts/specs/2026-10-11-home-dock-pty-terminal-v1.1-spec.md
  - .ai-runtime-artifacts/verifications/assets/2026-10-11-home-dock-pty-v1.1/05-dock-layout-final.png
  - webui/src/components/workspace/WorkspaceNavigation.tsx
  - webui/src/components/dock/StartDock.tsx
  - webui/src/components/workbench/BrowserSection.tsx
  - webui/src/components/workbench/TerminalSurface.tsx
created_at: 2026-10-11
status: approved
approved: true
approval_source: 用户在本会话回复“直接实现吧”（2026-10-11）
skip_plan_reason: 用户在设计稿之后明确要求直接实现；实施期用 dispatch 与 tracking 记录多任务边界
topic: mira-home-sidebar-dock-visual
---

# Mira 首页、左侧导航与右侧 dock 视觉优化

## 目标与范围

把现有聊天首页从“宽大的欢迎页”收敛为清楚、安静的工作台；左侧图标导航、会话列表以及右侧文件/浏览器/终端 dock 使用同一套字阶、间距、图标和状态规则。主要解决用户指出的“不稳重、不可靠”和“浏览器、终端丑”。

本轮改 WebUI 与必要的样式、文案，不改变项目/会话路由、PTY/管道模式、浏览器 URL 白名单、iframe sandbox、权限模式、终端进程生命周期或后端协议。不采购或添加图标包，复用已安装的 `lucide-react`。不将待评审的“知序个人工作空间”设计稿误当作已上线产品；本方案面向当前 Mira 聊天界面。

## 现状证据

| 观察 | 代码或截图依据 | 视觉后果 |
| --- | --- | --- |
| 左侧轨道 64px、会话列 248px，总宽 312px | `App.tsx` 常量；`WorkspaceNavigation.tsx` 使用 `w-16` | 低分辨率桌面上左栏占比过高 |
| 首页标题在大屏为 48px，hero composer 最大 58rem | `ThreadShell.tsx`、`ThreadComposer.tsx` | 标题和输入框同时过强 |
| 首页 dock 为 26rem、XL 为 30rem | `StartDock.tsx` | 三栏同时显示时正文受到挤压 |
| 终端输出区固定 `h-72` | `TerminalSurface.tsx`；v1.1 验收截图 | dock 高度没有被利用，黑框下方留白 |
| 浏览器地址栏/按钮高 24px，文本约 11.5px | `BrowserSection.tsx` | 控件像调试面板，扫描与点击都费力 |
| dock 启动器每个入口都有边框卡片，终端再叠一行 select | `StartDock.tsx` | 卡片层数多，功能入口显碎 |
| 当前存在英文默认文案及重复的 New topic | 用户截图、`en/common.json` | 内容辨识度弱，页面像样例数据 |

当前工作树 `desktop/package-lock.json`、`memory/` 和 drawio 备份存在用户侧改动；实施时必须保留，不覆盖。

## 方案比较

| 方案 | 收益 | 代价 | 结论 |
| --- | --- | --- | --- |
| A. 只换图标与色值 | 改动最小 | 无法解决比例、空白和 dock 内容高度问题 | 不选 |
| **B. 统一视觉系统并收紧布局** | 同时改善首页、双侧栏和工具面板，复用现有组件与行为 | 需多组件联动和截图回归 | **推荐** |
| C. 重做导航信息架构与工具模型 | 可彻底改变产品结构 | 会冲击已验收的 dock/PTY、快捷键和会话逻辑 | 不在本轮 |

## 设计原则

1. **上下文先于装饰。** 项目、设备、会话与工具状态要明确；首页不靠大标题制造存在感。
2. **一个区域只有一个主操作。** 图标、选中态、警告、连接状态各有独立语义；同一权限警告不在多个位置重复争抢注意力。
3. **工具占满工具区。** 浏览器以网页为主，终端以输出为主；工具栏稳定且克制。
4. **保持真实状态。** 不用纯样式伪造运行、连接或安全状态；已有错误、拒嵌、退出、接管提示都继续可见。

## 设计规格

### 1. 整体版式与首页

- 左轨道从 64px 收到 **52–56px**，会话列从 248px 收到 **212–224px**，总宽目标 **264–280px**。保留折叠、键盘焦点和 tooltip。
- 首页 hero 标题从 48px 降到桌面 **28–32px**，字重 medium 或 regular；优先显示具体项目或开始语境，随机问句不再作为唯一上下文。中文 locale 用自然中文，英文 locale 用英文；不强行重命名用户已有会话。
- hero composer 最大宽度从 58rem 收到 **44–46rem**，与项目/本机上下文行同宽。输入区在无右 dock 时保持居中；右 dock 打开时位于剩余正文区中心，不被标题或 dock 顶栏遮挡。
- 页面主内容采用较白的中性底色；左导航和右 dock 用浅一阶的面板底色与单条分隔线。保留现有亮/暗主题 token，靛蓝只用于选中与焦点，橙色只用于真实权限警示，绿色只用于真实运行/连接状态。减少多余投影和卡片套卡片。
- 对于宽度不足以同时保证正文 **≥560px** 和 dock **≥320px** 的窗口，dock 默认折叠为可打开的工具入口，不强挤 composer。窄窗、缩放和多 pane 场景另做响应式验收。

### 2. 左侧导航与会话列

- 全部功能入口复用同一套 Lucide 线框图标，视觉尺寸 **18px**、统一线宽约 **1.75**、点击目标 **36–40px**。建议映射：对话 `MessagesSquare`、创作 `PenLine`、应用 `Blocks`、技能 `Sparkles`、自动化 `CalendarClock`、智能体 `Bot`、设置 `Settings2`；最终以实际图标轮廓在同尺寸下的光学重量校正。品牌标志保留，但缩小周边占位。
- 轨道只给当前页面一个清楚的选中背景；hover、focus、disabled、未读各有单独状态，不用橙点/紫底/绿色点互相代替。图标入口的 tooltip/可访问名称继续显示完整文字。
- 会话列保持“新建 → 项目 → 会话”的阅读顺序，标题与条目用 14/12px 两级字阶；项目标题、列表行与工具按钮使用一致的行高与左右内边距。当前会话的选中背景和左轨道选中态语义相同。
- 多个默认“New topic”在显示层用现有摘要或时间作辅助辨识；没有可用内容时仍保留原会话标题，不写入新标题。统一当前 locale 的系统文案；用户内容原样显示。

### 3. 右侧 dock 共用框架

- 首页 dock 从固定 26/30rem 调为常规 **336–384px**；已有宽度调整/折叠能力继续保留。dock 顶栏高度约 **40px**，文件/浏览器/终端标签用同一规则：图标 + 截断标题 + 活动态 + 关闭按钮；新增按钮与收起按钮有明确 tooltip 和焦点态。
- 空态启动器把三张厚边框卡片改为轻量功能行：**32px 图标容器 + 名称 + 一句说明 + 快捷键**。终端 shell 选择并入“新建终端”的次级菜单或同一功能行的下拉，不额外形成一张嵌套卡片。
- dock 内容区统一 `min-height: 0` 与纵向 flex，使工具面板从顶栏下沿一直延伸到页面底部。文件树、浏览器、终端的空态和加载态都用同一内边距与文字层级。

### 4. 浏览器

- 工具栏控件升到 **32–36px** 点击目标，地址框文本约 **13px**；后退/前进/刷新/外部打开使用已有 Lucide 图标，禁用时保持可识别轮廓。地址框是工具栏主元素，展示当前 URL，可键盘提交。
- iframe/网页视口占尽工具栏以下可用高度；空态显示图标、简短说明和聚焦地址栏的动作。加载中显示局部进度而不让整面板闪烁。
- 非法 URL 错误贴近地址栏；拒嵌状态保留“在外部浏览器打开”的主要操作，文字说明网站限制，不暗示 Mira 故障。安全过滤和 iframe sandbox 逻辑原样保留。

### 5. 终端

- 终端输出面替换固定 `h-72`，使用剩余 dock 高度；输出面单一深石墨背景、清楚的边界和 **12–16px** 内边距。保持 xterm 字体栈，字号目标 **12.5–13px**，行高/字符间距以实际 PTY 渲染截图校正。
- 顶部标签说明 shell；若现有状态数据可用，运行/退出/失败以文字和少量语义色呈现。退出码、错误与重连接管提示保留。PTY 模式不额外显示命令输入框；管道降级模式的输入框与输出面连续排列、按钮和字号与浏览器一致。
- xterm 继续负责文本、选择和滚动；视觉层不得破坏 `FitAddon` 的容器测量、键盘输入、窗口 resize、复制粘贴或输出流。禁止伪造 cwd、命令执行成功等未收到的数据。

### 6. 交互与可访问性

- 同时验收 hover、focus-visible、active、disabled、loading、empty、error、exited、blocked 状态；亮暗主题均可辨识。图标按钮保留文本可访问名称与键盘路径。
- 既有 `⌘P`/`⌘T`、tab 开关、shell 选择、终端接管、浏览器外开行为保持；动效只用于状态转换，尊重 reduced-motion。

## 实施边界与候选文件

优先修改 `webui/src/components/workspace/WorkspaceNavigation.tsx`、`webui/src/components/Sidebar.tsx`、`webui/src/components/dock/StartDock.tsx`、`webui/src/components/workbench/BrowserSection.tsx`、`webui/src/components/workbench/TerminalSurface.tsx`、`webui/src/components/thread/ThreadShell.tsx`、`webui/src/components/thread/ThreadComposer.tsx`、`webui/src/App.tsx` 和 `webui/src/globals.css`。仅在实际文案变更时改对应 locale；复用现有图标包，无新依赖。实施计划按共用 token/布局、左栏首页、右 dock 三组界面确定文件归属，避免并行修改同一文件。

## 验收口径

1. 以 **1280×800、1440×900、1024×768** 做首页截图：标题、composer、左栏、右 dock 无遮挡或横向滚动；dock 打开时正文仍可输入。
2. 左侧各功能入口的图标线宽、尺寸、选中与焦点样式一致；折叠和 tooltip 可用；项目/会话长标题正常截断。
3. dock 空态、文件、浏览器与终端标签样式统一；浏览器地址栏可读、网页视口占满余高；拒嵌外开路径可见。
4. PTY 终端在 dock 中占满余高，输入、滚动、resize、关闭与接管正常；管道降级仍可提交命令。运行/退出/失败状态清楚。
5. 亮色/暗色、键盘焦点、窄窗和多 pane 视觉检查；前端目标测试、全量 `bun run test`、`bun run lint` 与 `bun run build` 通过。视觉截图与验证记录写入 `.ai-runtime-artifacts/verifications/`。

## 方案自检

- 范围锁定为现有 Mira 聊天工作台视觉；不会混入待评审的知序个人工作空间产品方案。
- 布局、图标、浏览器、终端都有可量化验收；不改已验收的后端与安全契约。
- 用户当前未提交工作树改动被列为保留约束。

## Next

本设计稿写入后依 `harness-kit/core/routing.md` 阶段门禁暂停，业务代码保持未改。

- 认可方案：说“写计划”制定实施计划，或说“直接实现”按项目实施路由继续。
- 需要调整：直接指出想改的范围、风格或优先级。
