---
artifact: verification-lite
route: leader-direct | 小改动直做
skills:
  - verification-before-completion
  - agent-browser
skills_evidence:
  - ~/.agents/skills/verification-before-completion/SKILL.md
  - ~/.agents/skills/agent-browser/SKILL.md
source:
  - "可以的（同意起服务做实机视觉复核）"
created_at: 2026-11-13
tier: 1
---

# 合流后实机视觉复核：聊天首页 / 知识库 / 记忆库 / 身份配置 — 轻量验证（Tier 1）

## 范围

- 被验对象：`feature/desktop-app`（含 `acfa57c2` 合流 + `ce61cbd4` 测试同步）
- 运行方式：主仓 `webui` vite dev（`--port 5180`，`vite.local.dev.config.ts`），网关复用已在跑的 `nanobot gateway`（127.0.0.1:8765）
- 浏览器：agent-browser（CDP），真实网关数据
- 截图：`.ai-runtime-artifacts/verifications/merge-2026-11-13/`

## 逐页结论

| 页面 | 结果 | 关键观察 |
| --- | --- | --- |
| 聊天首页（空白） | 通过 | 白底 + 图标栏 + 会话栏都在；输入框居中，示例卡片按「不要大标题」的要求已移除 |
| 聊天会话页 | 通过 | 左侧会话栏、中间消息流、右侧工作台面板、底部输入框均正常 |
| 知识库列表 | 通过 | 1 知识库 / 3 文档 / 12 分块 / 3 向量；卡片式库列表 + 面包屑 |
| 知识库详情 | 通过 | 文档 / 检索预览 / 图谱 三个标签页可切换；文档表带分块预览入口 |
| 检索预览（引用 UI） | 通过 | 命中卡展示：序号、分数 0.828、文档标题 + 路径（记忆与协作示例 › 记忆）、章节标题（## 记忆）、命中正文、定位信息，并提供「定位到文档 / 展开」动作；检索路径显示 vector, keyword, graph（hybrid） |
| 知识图谱 | 通过 | Neo4j 就绪；实体 44 / 关系 62；实体改为胶囊选择器（Agent 8、知识库 8…，可「展开 44 个」）；子图为 d3 力导向图，节点带标签、可拖拽、有向边带箭头与流向虚线动画，底部有「展开关联」与邻居/提及列表 |
| 记忆库 | 通过 | 长期信息 / 对话片段 / 关系视图三标签；关系视图由真实 206 条记忆生成 19 个节点的有向力导向图（我、项目、React、前端开发、职业…），不再是无标签的放射状团 |
| 身份配置 | 通过 | 该页只保留图标栏，没有会话栏（此前反馈的问题已消失）；源文件树 + 人格分栏 + 编辑器正常 |

## 遗留观察（未改，供决策）

1. 聊天首页的欢迎文案与起步 chips 在真实浏览器里被样式隐藏：`.thread-welcome-extras-block` 为 `display:none`，`.thread-welcome-intro` 计算尺寸 0×0。测试里断言的是「欢迎块已挂载」，不代表可见；首页因此只剩输入框。
2. 身份页「源文件」列表与右侧编辑器各自保留独立滚动条。
3. 对枢纽实体（如 Agent）的 1 跳邻域天然呈星形辐射，但节点可拖拽、边有流向动画。
4. 聊天欢迎语仍是「你好，我是 nanobot 🦊」，身份文件里的旧称谓未随本次合流调整。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `npx vite --port 5180 --config vite.local.dev.config.ts` | dev server ready（127.0.0.1:5180） |
| agent-browser 打开 `#/?bootstrapSecret=…` | 8 个页面/标签逐一截图，无控制台阻断 |
| `npx vitest run`（合流前一轮） | 170 files / 2133 tests passed，0 failed |

## 未验证项

- 未做暗色主题、窄屏（移动端）与键盘可达性回归。
- 知识图谱「重建 / 构建图谱」按钮未触发（会重跑抽取，耗时且改动数据）。

## Next

- 任务完成 → 如需继续调页面，可基于本轮截图直接给改造点；服务保持在 5180 端口供查看。

## 追加：顶部入口图标与登录页改版（2026-11-13 晚）

用户在实机上指出的两处问题，已在同一个 dev server（127.0.0.1:5180）复核：

| 问题 | 处理 | 截图 |
| --- | --- | --- |
| 顶部两个入口图标难认：终端是「圆角方块+小尖括号」，右坞是「实心竖条」——后者会被读成滚动条（此前已反馈过一次） | 终端换成 lucide `Terminal`（尖括号+下划线），右坞换成 lucide `PanelRight`（带分隔线的侧栏），并给右坞补上 `aria-pressed` 的按压态样式 | `merge-2026-11-13/20-dock-icons.png` → `22-dock-icons-new.png` |
| 登录页是裸表单：没有品牌、没有说明、不跟随应用外观 | 改成品牌卡片：点阵底纹 + 品牌字标 + 「本地工作空间」徽标 + 标题/副标题 + 密码位 + 密码来源提示；并让 App 在 Shell 之前也持有一份外观控制器，使登录页跟随主题与强调色 | `merge-2026-11-13/21-auth-new.png` |

新增 i18n 键：`app.auth.local` / `app.auth.title` / `app.auth.subtitle` / `app.auth.hint`（10 个语种齐备）。

验证：

| 命令 | 结果 |
| --- | --- |
| `npx tsc -p tsconfig.build.json --noEmit` | 0 错误 |
| `npx vitest run`（全量） | 170 files / 2133 tests passed，0 failed |

> 注：`preview-thread.test.tsx > renders the seeded demo conversation` 在满载并发跑过一次偶发超时，重跑即通过，判定为既有的时序抖动，未因本次改动产生回归。
