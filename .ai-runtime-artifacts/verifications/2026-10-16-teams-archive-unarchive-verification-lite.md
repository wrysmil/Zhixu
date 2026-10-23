---
route: Tier 1 Leader 直做
artifact: verification-lite
date: 2026-10-16
topic: 团队归档可取消 + 团队页说明文案
commit: f104bc5
---

# 验证（lite）：团队归档生命周期打通 + 团队页说明文案

## 改动范围

| 层 | 文件 | 内容 |
| --- | --- | --- |
| 后端 | `nanobot/teams/service.py` | `save_team` 对已归档团队只放行「取消归档」（payload 显式 `archived=false`），其余编辑继续抛 `TeamArchived`；`prepare_new_run` 的新运行拦截不变 |
| 前端 | `webui/src/components/settings/teams/TeamsView.tsx` | 归档团队默认不进列表；工具栏新增「显示归档/隐藏归档」开关（带归档数）；归档卡片给「取消归档」+ 确认框，并撤掉「编辑」；头部新增 `workspaceSubtitle` 说明 |
| 样式 | `webui/src/components/settings/teams/teams.css` | `.teams-toolbar-subtitle` |
| 文案 | `webui/src/i18n/locales/*/common.json`（10 语言） | `showArchived` / `hideArchived` / `unarchive` / `unarchiveTitle` / `unarchiveBody` / `allArchived` / `workspaceSubtitle` |
| 测试 | `tests/teams/test_service.py`、`tests/webui/test_teams_routes.py`、`webui/src/tests/teams-view.test.tsx` | 见下 |

## 证据

```
# 后端（含新用例 test_archived_team_only_accepts_unarchive）
python -m pytest tests/teams tests/webui/test_teams_routes.py tests/webui/test_teams_binding.py tests/webui/test_teams_chat_binding.py -q
→ 450 passed in 27.04s

# 前端
cd webui && npx vitest run src/tests/teams-view.test.tsx --reporter=basic
→ Tests  17 passed (17)
```

已覆盖的关键行为：

- 归档态下 `archived=true` 的保存 → 404 `TEAM_NOT_FOUND`（路由级，`test_teams_routes.py`）。
- 归档态下 `archived=false` 的保存 → 200，修订 +1，且 `prepare_new_run` 随后放行（`test_service.py`）。
- 归档后卡片从列表消失、只剩「显示归档」入口；开关打开后可见「已归档」徽章；取消归档后徽章消失（`teams-view.test.tsx`）。
- 归档卡片没有「编辑」，只有「复制」与「取消归档」（同上）。
- 10 个语言包 JSON 合法且 key 齐全；`i18n.test.tsx` 报出的 `extra: []`、`interpolationMismatches: []` 证明新增 key 各语言形状一致。

## 未验证 / 已知失败

- **未在桌面壳里做视觉验证**：起了 vite(5173) 准备走 Electron+CDP，被随后的提交指令打断，未截图确认 `workspaceSubtitle` 的实际排版（行宽/换行是否压迫工具栏）。单测只证明文案会渲染，不代表排版达标。
- `webui/src/tests/i18n.test.tsx > keeps every locale aligned with the English resource shape`：既有失败，原因是 zh-CN 缺 `settings.teams.rosterCount_one` / `rosterCount_other`（与本次改动无关，本次新增 key 未出现在 missing/extra 中）。
- `webui/src/tests/teams-view.test.tsx:194` 断言内联 "Saved" 曾失败（18:14 基线）；18:36 并行会话改完 `webui/src/components/ui/toaster.tsx` 后转为通过，非本次改动引入，也不再是失败项。

## 结论

后端闸门、前端交互与测试三侧一致，Python 450 例与 teams-view 17 例全绿；剩余差异只有上面的视觉排版未复核。
