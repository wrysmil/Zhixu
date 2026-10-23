# 创作空间对话团队改版 — 冒烟验证（2026-10-17）

## 环境

- 仓库：`/Users/mima0000/Documents/学习-001/do-project/zhixu`
- Gateway：`nanobot gateway --background`，启动于本地 19:41（UTC 11:41），控制端口 18790 `/health` = 200，WebUI `127.0.0.1:8765` = 200。
- WebUI：`npm run build` 产物，浏览器 `http://127.0.0.1:8765`（bootstrap 自动登录）。
- 模型：真实 `deepseek-flash`（全局默认 preset 回退），非 mock。
- 数据：工作区现有 4 篇文章（`Redis的原理` / `e w q` / `232` / `hgh`）。

## 已通过（真实浏览器 + 真实模型）

| 项 | 证据 |
| --- | --- |
| 创作列表 | `#/creative` 加载 4 篇真实文章，状态/版本/时间正常 |
| 文章主区 + 右侧团队对话 | 打开文章后右栏固定展开（窄屏为浮层），标题「创作团队」+「内置」标识 |
| 会话绑定 | 服务端返回 `chat_id/session_key/team_id`；右栏模型 chip 显示 `deepseek-flash · Medium · 全局默认`，无自拼 `${articleId}:partner` |
| 空态预设 | 按文章状态排序（有提纲时「写作初稿」置顶），文案使用《Redis的原理》标题；点击只填输入 |
| `/` 任务菜单 | 6 项（构思/素材/提纲/写作/改写/审校）；选「提纲」后输入框变为模板文案，出现「结构编辑 + 提纲」芯片，未自动发送 |
| `@` 成员菜单 | 5 名成员（主编/资料研究员/结构编辑/写手/审校）+ 文章上下文（全文/提纲；无选区与素材时对应项禁用） |
| 发送与落库 | 发送 /提纲 后：主编读取文章（工具 trace「Completed Writing read article」）→ 派结构编辑 → 提纲 3→7 条写入 → 前端收到 `writing_changed`，横幅「团队修改了提纲（writing-architect）」并自动载入 |
| 连续追问/历史 | 关闭右栏、切到别的文章再切回，原用户消息与完整回复从服务端历史恢复 |
| 前后对比 | 「查看对比」展示修改前（3 条）与修改后（7 条） |
| 撤销 | 「撤销这次修改」后提纲 7→3，保存时间更新，横幅关闭 |
| 划词工具条 | 选中正文出现 问 AI/改写/扩写/精简/更多；点「改写」自动展开右栏并填入请求 + 选区芯片（不自动发送） |
| 素材/提纲按需展开 | 工具栏「素材 / 提纲 3」打开左侧抽屉，抽屉内可切换素材与提纲；无任务矩阵/阶段面板 |
| 全局导航 | 一级仅 助手 / 创作 / 更多 / 设置；「更多」菜单分组为 工作区（Apps/Skills/Automations）、AI 配置（Agents/Teams）、个人（Memory/Identity），旧路由均可达 |
| 团队种子 | `#/teams` 可见「创作团队」v2：Leader 创作团队主编 + 资料研究员/结构编辑/写手/审校，来源为内置 |

## 冒烟中发现并已修复

- **划词动作不打开右栏**：`ArticleEditor.dispatchSelectionAction` 只写入种子；右栏关闭时用户看不到请求。已在派发动作时 `setChatOpen(true)`，补单测「划词动作会自动打开收起的创作团队右栏」，重新构建后在浏览器复验通过。

## 冒烟中发现、已修复

1. **同一轮内成员第二次写入必冲突（P0）**
   - 现象：结构编辑第一次写提纲成功（revision 2→3）后，同轮再做格式清理，两次都返回 `writing_conflict current_revision=3`，而文章 revision/digest/条目均未变。
   - 根因：`writing` 元数据在 `run_extension` 的 `_WritingSeed` 中按 run_id 缓存，构造的 `WritingToolRuntime` 是 frozen；`apply.py::apply_change` 成功后没有回写 runtime 基线，读工具返回的新 revision 也没有成为后续写入的基线。
   - 修复：
     - `apply.py::apply_change`：当 revision/digest 与开工快照不一致时，若「该文章最后一次变更」恰好由同一 run、同一成员产生且 result_revision/digest 等于当前值，则把有效基线提升为当前版本；其他成员、其他 run、作者直改仍按原规则冲突。
     - `tools.py::WritingToolRuntime` 改为可变，`writing_read_article` 读到最新版本后刷新 `base_revision/base_digest`，落实方案 §6.4「读新版本 → 重试」。
     - 工具描述同步为「其他人改过才拒绝」。
   - 回归测试：`test_same_run_member_can_keep_writing_after_its_own_change`、`test_other_member_in_same_run_still_conflicts`、`test_external_edit_between_writes_is_still_a_conflict`、`test_read_article_refreshes_the_write_base`。
2. **写入事件 actor 显示原始 id（P2）**
   - 修复：`run_extension.on_change` 用团队修订里的 `leader/member profile.name` 补 `actor_name`（保留 actor id）；前端 `WritingChangedEvent` 增加可选字段，`CreativeWorkspace` 优先显示 `actor_name`。回归测试 `test_change_event_carries_actor_display_name` 与前端 `creative-team-sync` 的 actor 断言。

## 自动化验证

- 前端：`npm run test` 132 文件通过 / 2 skipped，**1718 passed**；`tsc -p tsconfig.build.json --noEmit`、`npm run lint`、`npm run build` 全部通过。
- 后端：`pytest tests/writing` **217 passed**；`pytest tests/webui/test_writing_routes.py test_writing_wiring.py test_writing_collab_wiring.py test_teams_e2e.py` 127 passed，1 个 `test_cancel_from_a_foreign_session_is_refused` 失败（已在 `/tmp/zhixu-head-baseline` 复现，属分支既有失败）。
- 静态检查：`ruff check nanobot/writing nanobot/bus tests/writing` 通过；`basedpyright` 针对本次改动的三个文件 0 错误（全仓库 1896 条为 `writing/collab` 等既有问题，已用 HEAD worktree 对照确认非本次引入）。
- 修复后重新构建并重启 gateway，`:18790/health` 与 `:8765` 均 200，创作页与会话历史正常加载。
- 新增/改写用例：`writing-chat-panel`(9)、`creative-selection-toolbar`(5)、`creative-team-sync`(4)、`workspace-navigation`(22)，并同步 `app-layout`/`preview-isolation`/`creative-panels`/`creative-partner-integration` 的导航与抽屉口径。

## 说明

- 冒烟期间对 `Redis的原理` 正文临时写入示例文字后已清空并等待自动保存，最终 `0 字`、revision 3。
- `e w q` 的提纲经「写入→撤销」后回到 3 条，revision 4。
- 同一轮多写的修复由确定性测试覆盖（同 run 同成员连续写 → 通过；其他成员/作者直改 → 仍冲突；读新版后重试 → 通过），未再消耗一次真实模型回合复跑该场景。
- 未做 live 验证：作者与团队同时编辑的竞态、断网重连补拉、小屏浮层拖拽宽度的多设备矩阵（对应单测/集成测试已覆盖主要分支）。
- Gateway 当前保持运行（`--background`），可直接打开 `http://127.0.0.1:8765` 体验。
