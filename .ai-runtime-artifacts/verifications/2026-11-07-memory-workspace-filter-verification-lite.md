---
name: memory-workspace-filter-verification-lite
route: leader-direct
skills:
  - verification-before-completion
skills_evidence:
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
source: 用户反馈「经验库 / 对话片段两个 tab 没有按工作区筛选」
created_at: 2026-11-07
tier: 1
---

# 经验库 / 对话片段按工作区筛选 — 轻量验证（Tier 1）

## 根因（改动前）

1. **写入侧没有归属**：`ExperienceRecordTool` 落库用 `services.workspace_id`，而网关把记忆服务固定在
   `"default"`（`gateway_runtime.py` / `gateway_services.py`）。经验行永远标成全局，项目维度根本不存在。
2. **经验列表没有筛选入口**：`list_experiences_payload` 默认只查 `("default",)`，前端
   `ExperienceListView` 连 `workspace` 参数都不发，也没有下拉。
3. **对话片段口径不一致**：`EpisodeListView` 的 `workspace` 默认 `""`（全部工作区），与长期记忆页
   「默认当前项目」的既有约定相反；前端也拿不到记忆工作区 id（id 写在项目内
   `.nanobot/workspace-id`，只能由后端解析路径）。

## 改动

| 层 | 文件 | 内容 |
| --- | --- | --- |
| 写入 | `nanobot/agent/loop.py` | 新增 `_memory_workspace_id_for_key`（会话归属键，非可见集合），注册为工具属性 `memory_workspace_id` |
| 写入 | `nanobot/agent/tools/experience.py` | `ExperienceRecordTool` 接受 `workspace_id_provider`，落库按会话工作区打标；解析异常回落全局 |
| 读取 | `nanobot/webui/memory_api.py` | `_workspace_filter_id` 把 `workspace_path` 解析成工作区键；`list_episodes_payload` / `list_experiences_payload` 支持 `workspace_path`，经验列表回传 `workspace_options` |
| 读取 | `nanobot/webui/memory_presentation.py` | `filter_result` 新增 `workspace_ids`（后端已解析集合，不做下拉白名单） |
| 路由 | `nanobot/webui/memory_routes.py` | `episode-list` / `experience-list` 透传 `workspace_path` |
| 前端 | `webui/src/components/settings/memory/MemoryPresentation.tsx` | 新增 `CURRENT_WORKSPACE` 哨兵、`useCurrentProject()`、`MemoryFilters` 的「当前项目」入口（并去掉与当前项目重复的选项） |
| 前端 | `ExperienceListView.tsx` / `EpisodeListView.tsx` | 默认选中当前项目；等 `fetchWorkspaces` 落定后再查询；新增/复用工作区下拉；清空筛选回到当前项目 |
| 前端 | `webui/src/lib/api.ts` | `listExperiences` / `listEpisodes` 支持 `workspacePath`（`workspace_path` 查询参数），`ExperienceListResult` 带 `workspace_options` |

筛选语义：`workspace`（下拉 id）走白名单校验；`workspace_path`（当前项目）由后端用
`memory_workspace_id(path)` 推导归属键后**严格等值过滤**。不传参 = 全部工作区（历史经验仍可见）。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `uv run --no-sync pytest tests/webui/test_memory_api.py tests/webui/test_memory_routes.py tests/webui/test_memory_presentation_api.py tests/memory/test_memory_experience_mvp.py -q` | 98 passed（新增 6 条：列表按 `workspace_path` 过滤 ×2、路由透传 ×2、工具归属/回落 ×2） |
| `uv run --no-sync pytest tests/agent/test_memory_visibility.py -q` | 11 passed（新增 2 条：归属键取项目、无作用域回落 default） |
| `uv run --no-sync ruff check <8 个改动/新增测试文件>` | All checks passed |
| `webui: vitest run memory-presentation / memory-long-term-experience / api-memory / memory-workspace-filter` | 39 passed（新增 3 条：经验库默认当前项目并可切回全部、对话片段默认当前项目 + 清空回当前项目、API 传 `workspace_path`） |
| `webui: tsc -p tsconfig.build.json --noEmit` | exit 0 |
| `webui: eslint <7 个改动文件> --max-warnings 0` | 无输出（通过） |
| `webui: vitest run`（全量） | 2020 passed / 18 failed / 2 skipped；失败集中在 `thread-messages`、`appearance-boot`、`i18n`、`message-bubble`、`subagent-execution-panel`，这 5 个文件均未引用记忆模块或 `lib/api`，内容为计时文案、配色、法语资源对齐等，推断为本分支既有失败（本次未改动这些路径） |

## 浏览器实机验证（隔离实例，2026-11-07 19:56 重启后）

环境：`.ai-runtime-artifacts/verifications/2026-11-07-memory-browser-server.py`，网关 8765 / WebUI 18791 /
Vite 5173，workspace 为 `.../2026-11-07-memory-browser/workspace`，数据为 6 条 `default` 经验 + 3 条
项目工作区对话片段。

| 操作 | 观察 |
| --- | --- |
| 经验库默认 | 下拉值 = `workspace`（当前项目名），选项为「当前项目 / All workspaces / Global memory」，空态「还没有经验记录」 |
| 经验库 → Global memory | 请求 `experiences?workspace=default&limit=100`，列出 6 条 |
| 经验库 → All workspaces | 请求 `experiences?limit=100`（不带工作区参数），同样 6 条 |
| 对话片段默认 | 下拉值 = `workspace`，请求 `episodes?order=created&workspace_path=<项目路径>`，3 条均为本项目片段 |
| 对话片段 → Global memory | 6 条（含 `source=experience` 行），「Clear filters」按钮出现 |
| 对话片段 → Clear filters | 回到当前项目，下拉值 `workspace`，3 条 |
| 控制台 | 网关重启前的 bootstrap 500 / WS 断连报错为历史记录；重启后刷新无新增 error/warn，所有 `/api/settings/memory/*` 均 200 |

结论：前端默认当前项目、下拉切换、清空筛选三条路径与后端 `workspace_path` 解析在真实浏览器中一致。
经验库当前项目视图为空，是因为这 6 条经验都写在写入侧修复之前、标的是 `default`——属历史数据缺口，
不是筛选失效（切到 Global memory 即可见）。

## 未验证项

- 未重启用户真实配置的网关（只重启了隔离验证实例），真实 `~/.nanobot/config.json` 下的行为未覆盖。
- 没有重启网关确认「自动抽取的经验行带项目 workspace_id」的端到端落库（依赖真实会话 + 模型抽取）。
- 历史经验行仍标 `default`（写入侧修复前的数据），只能在「全部工作区 / 全局记忆」下看到，未做回填；
  默认视图选当前项目时，老用户会先看到空经验库，需要手动切「全局记忆」。

## Next

- 用真实项目会话让 Agent 记录一条经验，确认 `episodes.workspace_id` 等于该项目工作区键，
  且经验库 tab 默认即可见。
- 老用户默认视图会先看到空经验库：可考虑一次性回填脚本（按 `provenance.session_id` → 会话工作区推导），
  或让经验库在「当前项目无数据且全局有数据」时提示切到全局记忆。本次未做，避免误改既有归属。
