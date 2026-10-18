---
artifact: verification
route: 功能实现 -> @ 选择 Agent + 默认指挥官档案 + 图标体系
skills: [source-driven-development, test-driven-development, verification-before-completion]
source:
  - webui/src/components/thread/ThreadComposer.tsx
  - webui/src/components/CliAppMentionText.tsx
  - webui/src/lib/nanobot-client.ts
  - webui/src/lib/agents/icons.ts
  - webui/src/components/ui/agent-glyph.tsx
  - webui/src/components/ui/agent-icon-picker.tsx
  - nanobot/agents/catalog.py
  - nanobot/agents/runtime.py
  - nanobot/webui/inbound_commands.py
  - nanobot/agent/loop.py
created_at: 2026-10-18
status: verified
tier: 1
---

# @ 指定 Agent + 默认「指挥官」档案 + Agent 图标体系

## 结论

三项均已完成并通过验证：

1. 聊天输入框输入 `@` 会弹出 Agent 候选，选中后以 `agent: "<profile-id>"` 随消息发送；
   后端仅对受信 WebUI 连接生效，校验后写入 metadata `agent_id`，由 `AgentLoop` 以该档案的
   提示词 / 技能 / 工具 / 模型接管本轮。
2. 原默认系统提示词已抽象为系统预设「指挥官」（id `commander`），作为未显式 `@` 时的
   默认主 Agent，并出现在 Agent 管理列表中。
3. Agent 图标改为本地 Lucide 精选图标选择器（`lucide:<name>` token），保留 Emoji / 图片路径兼容。

## 关键实现

### 后端

- `nanobot/agents/catalog.py`：`DEFAULT_MAIN_AGENT_ID = "commander"`；FACTORY_PROFILES 首位新增
  「指挥官」系统预设（分类 General、图标 `lucide:compass`、颜色 `#D97706`、
  原默认系统提示词全文、工具与技能全量）。
- `nanobot/agents/runtime.py`：`AGENT_PROFILE_METADATA_KEY = "agent_id"`；
  `resolve(agent_id, ...)` 支持按 profile 解析提示词、工具白名单、技能排除与模型。
- `nanobot/webui/inbound_commands.py`：`normalize_agent_selection()` 校验 id，仅受信连接接受
  `envelope["agent"]`；非法/不存在时返回 `invalid_agent` / `agent_not_found` 且不派发。
- `nanobot/agent/loop.py`：`TurnContext.agent`、`_resolve_turn_agent()`；团队回合优先于
  Agent，SYSTEM / 子 Agent 回合不解析；`_run_agent_loop(agent=...)` 注入角色段与技能排除。

### 前端

- `ThreadComposer.tsx`：`@` 候选中 Agent 优先级最高并置顶 `commander`；隐藏 Agent 不参与候选；
  文本中最后一个有效 `@agent` 决定本轮接管者；排队草稿恢复时同样解析。
- `CliAppMentionText.tsx`：mention 分段新增 `agent` 类型与 token 渲染（AgentGlyph + 名称）。
- `nanobot-client.ts` / `useNanobotStream.ts` / `types.ts`：`SendOptions.agentId` 透传，
  出站帧按需附加 `agent` 字段。
- `lib/agents/icons.ts` + `ui/agent-glyph.tsx` + `ui/agent-icon-picker.tsx`：
  36 个精选 Lucide 图标、5 个分组、搜索与 Emoji 双标签；`AgentGlyph` 统一渲染
  lucide token / 图片 URL / data URI / Emoji / 默认图标，替换了原 EmojiPicker 与各处零散渲染。

## 证据（本轮实跑）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| TypeScript | `webui/node_modules/.bin/tsc -p tsconfig.build.json` | 通过 |
| 前端构建 | `webui/node_modules/.bin/vite build` | 通过 |
| 前端目标用例 | `vitest run` 会话 / client / plus-menu / 图标集合 | 180 passed |
| plus-menu 回归 | 单独复跑 `composer-plus-menu.test.tsx` | 15 passed |
| 后端聚焦用例 | `uv run --no-sync pytest tests/agent/test_turn_agent_profile.py tests/webui/test_agent_selection.py tests/webui/test_agents_runtime.py tests/webui/test_agents_wiring.py` | 66 passed |
| 后端全量相关 | `uv run --no-sync pytest tests/agent tests/webui -q` | 2734 passed, 4 failed（基线） |

### 基线失败归因

4 个失败（auto_compact idle scan、mcp reconnect、run_inline structured error、teams e2e ordering）
在 pristine HEAD 临时 worktree 中复跑同样失败，确认与本次改动无关。

### ESLint

唯一报错为 `react-hooks/exhaustive-deps rule not found`（`useNanobotStream.ts:714` 既有
eslint-disable 注释），该行本次未改动，属既有配置问题。

## 浏览器端到端（http://127.0.0.1:8765）

1. 首页输入 `@`：出现 Mentions 面板，第一条为「指挥官 @commander · 由 @commander 接管本轮 Agent」
   且默认选中；code-reviewer / general-assistant / 会话 / CLI / MCP 依次排列。
2. 选择后输入 `@commander 你好，请用一句话介绍你自己，并说明你现在能使用哪些工具。` 并发送：
   本轮由指挥官接管，回复以「我是『指挥官』——这个工作区的主 Agent」开头，并列出实际可用工具。
3. `#/agents` Agent 管理页：列表可见「指挥官 · System preset · General ·
   默认主 Agent：接住用户请求，组织工具与子 Agent，并对最终交付负责。」
4. 打开指挥官档案 → Model & more → Pick an icon：默认「图标」标签，`compass` 处于选中态，
   分组（指挥与目标 / 思考与创作 / 工程与工具 / 探索与系统 / 运行与度量）与 Emoji 标签均正常。

## 运维注意

Agent catalog 在 gateway 进程内缓存；改动 `catalog.py` 后必须重启 gateway 才能看到新预设。
浏览器首次未看到 commander 即因旧进程仍在运行，重启后恢复正常。

## 图标来源调研

- Lucide：https://lucide.dev/
- Iconify（跨图标集检索）：https://icon-sets.iconify.design/
- Tabler Icons：https://tabler.io/icons
- Phosphor Icons：https://phosphoricons.com/
- DiceBear（头像生成）：https://www.dicebear.com/
- OpenMoji：https://openmoji.org/
- Fluent Emoji：https://github.com/microsoft/fluentui-emoji
- Twemoji：https://github.com/twitter/twemoji

选型结论：Agent 图标采用本地 Lucide 精选注册表（不动态导入全部图标），保证离线可用、
风格统一、体积可控；Emoji 作为次要选项保留。
