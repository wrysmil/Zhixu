---
artifact: contract
route: api-and-interface-design
skills: [api-and-interface-design]
created_at: 2026-10-15
status: approved
---
# WU边界
- WU01独占globals.css、WorkspaceNavigation.tsx、BrandMark.tsx、ThreadShell.tsx、PreviewThread.tsx、ThreadFrame.tsx、可新增共享ThreadWelcome组件及对应测试；仅为导航接线必要可修改PreviewApp.tsx。不改PreviewSettings和memory模块。
- WU02独占MemorySection.tsx、SettingsSectionFrame.tsx、PreviewSettings.tsx、可新增MemoryGuide/PreviewMemory组件及测试；不改globals.css。
- Leader独占静态资源与过程产物、最终整合。语言资源按需要单独整合，子Agent优先使用useTranslation的现有key，新增文案以zh判断中英fallback，避免批量改十个语言包。
- 设计token由WU01定义，主色森林绿，暖纸背景，细边框，展示标题Georgia/中文宋体；WU02使用现有bg-card/text-primary/border-border等语义token。
- 插画路径：/illustrations/agent-workspace.svg、/illustrations/memory-notes.svg。Leader下载unDraw并将默认紫色替换为森林绿（许可允许自定义）。
- 图标沿用lucide-react，即Iconify的Lucide图标集；如需图标资源可引用Leader提供的本地SVG。无新增运行时图标请求和依赖。
- 新对话示例仅填入composer，使用现有draftSeed/受控输入机制；不自动发消息。PreviewMemory所有交互仅在内存中，禁止调用生产client。
- 新增记忆导航调用onNavigate({view:"settings",settingsSection:"memory"})。PreviewApp如要转发initialSection时用settingsSection，切换时key刷新或效果同步，生产App已有映射需验证。
