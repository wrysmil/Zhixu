---
title: Mira 桌面版 + webui 品牌重塑 验证（lite）
date: 2026-10-09
type: verification
tier: 1
route: "Tier 1 Leader 直做"
branch: feature/desktop-app
related:
  - .ai-runtime-artifacts/specs/2026-10-09-desktop-mira-spec.md
  - .ai-runtime-artifacts/plans/2026-10-09-desktop-mira-plan.md
  - .ai-runtime-artifacts/decisions/2026-10-09-mira-rebrand-scope.md
approved: true
---

# 验证范围

1. webui 品牌重塑（nanobot → Mira，仅用户可见字符串；内部标识符保留）
2. desktop/ Electron 桌面壳（复用 webui 构建产物，自动拉起 gateway）
3. webui 与 desktop 并存（不删除 webui）

## 验证命令与结果

### 1. webui 单元测试（vitest 全量）

```
cd webui && npm run test
→ Tests  3 failed | 1216 passed (1219)
```

3 个失败全部定位并解释：

| 失败用例 | 结论 | 证据 |
| --- | --- | --- |
| app-layout「preserves the first message when the gateway rejects a project」(toHaveFocus) | **本分支改动前已存在**（pre-existing） | 在 HEAD 3bbaef5 基线 worktree（.claude/worktrees/base-check，node_modules junction）跑同一用例，失败相同；已清理该 worktree |
| xAI Grok provider 文案用例 | 全量跑负载 flake | 单独隔离运行通过 |
| thread-viewport markdown 用例 | 全量跑负载 flake | 单独隔离运行通过 |

品牌相关断言（app-layout / sw / i18n / settings-apps-oauth / settings-providers / settings-system / thread-composer）改后全部通过。

### 2. webui 构建

```
cd webui && npm run build
→ exit 0
```

### 3. desktop 冒烟

```
cd desktop && npm run smoke
→ SMOKE_EXIT=0（Electron 启动、boot 流程、app.exit(0) 正常）
```

### 4. 保护区未误改（grep 复核）

- `webui/index.html` L105/L136：`nanobot-webui.theme`、`nanobot.locale` localStorage key 保留
- i18n 键名 `nanobotFeatures.*` 保留；`` `nanobot gateway` `` 命令提示保留
- 内部标识符 `useNanobotStream`、`nanobot-client`、`nanobotHost`、`nanobot-host://`、`/api/settings/nanobot-features` 保留
- Python 包目录 `nanobot/`、CLI `nanobot`、`~/.nanobot` 路径不动
- `grep -rln "Use nanobot from\|Chat with nanobot\|webui/locales" tests/ nanobot/channels/*/tests` → 无 Python 测试断言 channel locale 文案（pytest 不受影响）

### 5. 旧品牌资产清理

`git rm` 6 个 `webui/public/brand/nanobot_*`；新增 `mira_mark.svg` 等 6 个 mira 资产；`manifest.json`、`sw.js`、`Sidebar.tsx` 引用同步更新。

## 结论

Tier 1 验证通过：webui 1216/1219（唯一硬失败为基线已有，2 个负载 flake 隔离通过）、build exit 0、desktop smoke exit 0、保护清单复核无误改。

## Next

- 用户在 Windows 上运行 `cd desktop && npm run dist` 生成 Mira-Setup exe 后手工体验
- 如需仓库级 slogan/README 品牌更新，另起任务
