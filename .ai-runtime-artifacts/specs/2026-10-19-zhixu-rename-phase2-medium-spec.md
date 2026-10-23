---
artifact: spec
route: superpowers:brainstorming
skills: [brainstorming, source-driven-development, api-and-interface-design]
skills_evidence:
  - .claude/skills/brainstorming/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/api-and-interface-design/SKILL.md
source:
  - specs/2026-10-19-zhixu-rename-spec.md
  - contracts/2026-10-19-contract-zhixu-rename-mapping.md
  - stack/2026-10-19-zhixu-rename-stack.md
created_at: 2026-10-19
status: draft
approved: false
---

# Phase 2 方案：前端与协议（🟠 中风险）

## 0. 定位

第二期。**改前后端之间的接口面**：TypeScript 符号名、HTTP 头、HTTP 路由、WebSocket payload key、浏览器持久化键、npm 包名。

风险等级中等的来源不是「改起来难」，而是**改错了会静默失效**：前端符号名改一半，TypeScript 编译会报错（可发现）；但 HTTP 路由改了没重建 dist、或 localStorage 键改了没做迁移，会表现为「功能消失 / 主题重置」（不易发现）。

> ⚠️ **执行前置**：`stack/2026-10-19-zhixu-rename-stack.md` 已标注 **bun 不在 PATH**。本期验收需要重建 webui dist，执行前必须确认 bun 可用，或明确改用 `npm run build`（`webui/package.json` 的 `build` 脚本为 `tsc -p tsconfig.build.json && vite build`，与包管理器无关，npm 可跑）。

## 1. 本期边界

| 做 | 不做（留给 Phase 3） |
| --- | --- |
| 前端 TS 符号与文件名 | Python 包目录 `nanobot/` → `zhixu/` |
| HTTP 头、HTTP 路由、WS payload key | `NANOBOT_*` 环境变量 |
| localStorage / SW 缓存键 | `~/.nanobot` 数据目录 |
| npm 包名与 scope | CLI 命令 `nanobot` |
| Vite 插件名与 HMR 路径 | 构建五连（`outDir` / `hatch_build.py` / `pyproject.toml` / `Dockerfile` / `.dockerignore`） |
| i18n key `nanobotFeatures.*` | 渠道前端 glob `nanobot/channels/*`（依赖包目录名） |
| SKILL.md frontmatter `metadata.nanobot` | 前端 glob / `registry.ts` 正则 |

## 2. 改动清单

### 2.1 前端符号与文件（契约 §2.3）

| 旧 | 新 | 文件 |
| --- | --- | --- |
| `NanobotClient` | `ZhixuClient` | `webui/src/lib/nanobot-client.ts` → **`zhixu-client.ts`**（`git mv`） |
| `useNanobotStream` | `useZhixuStream` | `webui/src/hooks/useNanobotStream.ts` → **`useZhixuStream.ts`**（`git mv`） |
| `getTerminalStore` / `terminalStoresByClient` | 类型参数同步改 | 同上（`:1730,1732`） |
| `NanobotFeatureInfo` / `NanobotFeaturesPayload` | `ZhixuFeatureInfo` / `ZhixuFeaturesPayload` | `lib/api.ts:21`、`channel-plugins/types.ts:5,6,11,16,21,24` |
| `fetchNanobotFeatures` / `enableNanobotFeature` / `disableNanobotFeature` | `fetchZhixuFeatures` / `enableZhixuFeature` / `disableZhixuFeature` | `lib/api.ts:618,657,670` |
| — | — | `webui/src/tests/nanobot-client.test.ts` → `zhixu-client.test.ts`（`git mv`） |
| — | — | `webui/src/tests/useNanobotStream.test.tsx` → `useZhixuStream.test.tsx`（`git mv`） |
| — | — | `webui/src/App.tsx:79,122,131,791,835`、`components/dock/StartDock.tsx:27`、`components/ChatList.tsx`、`main.tsx:44`（注释） |

**执行顺序**：`git mv` 文件 → 改文件内符号 → 改全部 import 引用点。TypeScript 编译器会兜住漏改（`bun run build` 先跑 `tsc -p tsconfig.build.json`）。

### 2.2 Python 侧（**只改文件/符号名，不动包目录**）

| 旧 | 新 | 文件 |
| --- | --- | --- |
| `nanobot_features_api.py` | `zhixu_features_api.py` | `nanobot/webui/`（`git mv`，import 路径随之改） |
| `nanobot_features_payload()` | `zhixu_features_payload()` | 同上 `:19` |
| `nanobot_feature_instance_target()` | `zhixu_feature_instance_target()` | 同上 `:28` |
| `nanobot_features_action()` | `zhixu_features_action()` | 同上 `:36` |
| `operations.nanobot_features_payload` | `operations.zhixu_features_payload` | `webui/settings_system.py:48,501,505` |
| `operations.nanobot_features_action` | `operations.zhixu_features_action` | `webui/settings_system.py:49,507,516,530` |
| `payload["nanobot_features"]` | `payload["zhixu_features"]` | `webui/settings_system.py:685,693` |
| `nanobot_features_action: SettingsOperation` | `zhixu_features_action` | `webui/settings_capabilities.py:61,732` |
| `payload.nanobot_features`（前端插件） | `payload.zhixu_features` | `channels/weixin/webui/WeixinPanel.tsx:149` |

### 2.3 HTTP 路由与 WS 协议（**无兼容，同仓同版本发布**）

| 旧 | 新 | 位置 |
| --- | --- | --- |
| `/api/settings/nanobot-features` | `/api/settings/zhixu-features` | `webui/ws_http.py:190`；`lib/api.ts:622` |
| `/api/settings/nanobot-features/enable` | `/api/settings/zhixu-features/enable` | `webui/ws_http.py:191`；`lib/api.ts:662` |
| `/api/settings/nanobot-features/disable` | `/api/settings/zhixu-features/disable` | `lib/api.ts:675` |
| 测试 mock | 同改 | `webui/src/tests/app-layout.test.tsx:1064`、`api.test.ts:801` |

**为什么可以不兼容**：webui 的生产产物 `nanobot/web/dist/` 由 **gateway 自己的 wheel 携带**（`pyproject.toml` `artifacts`、`Dockerfile` COPY），前端与后端来自同一次构建，不存在版本错配。唯一例外是 dev 场景（`bun run dev` 代理到独立运行的 gateway）——此时两者必须同分支，已是既有约定。

### 2.4 HTTP 头 — **双读**

| 旧 | 新 | 位置 |
| --- | --- | --- |
| `X-Nanobot-Auth` | `X-Zhixu-Auth` | 客户端：`webui/src/lib/bootstrap.ts`、`tui/src/protocol.ts` |
| `x-nanobot-session-key` | `x-zhixu-session-key` | 客户端：`webui/src/lib/session-drag.ts` |
| — | — | 服务端：`webui/http_utils.py`（`issue_route_secret_matches`）、`channels/websocket/runtime.py:184` |

**设计**：客户端**只发新头**；服务端**新旧都收，新优先**。约 3 行。

```python
# webui/http_utils.py（示意）
for header in ("X-Zhixu-Auth", "X-Nanobot-Auth"):   # 后者保留 ≥1 个大版本
    if (value := headers.get(header)) is not None:
        return secrets.compare_digest(value, expected)
return False
```

**为什么客户端不必双发**：与 §2.3 同理，dist 由 gateway 自身携带，错配不成立。若用户 `bun run dev` 代理到未升级的 gateway，登录失败——属预期，文档提示升级 gateway 即可。

兼容期 ≥1 个大版本后，连同服务端旧头分支一并删除。

### 2.5 浏览器持久化与缓存

| 旧键 | 新键 | 位置 |
| --- | --- | --- |
| `nanobot-webui.theme` | `zhixu-webui.theme` | `index.html:104` |
| `nanobot-webui.sidebar` | `zhixu-webui.sidebar` | `App.tsx:131` |
| `nanobot-webui.sidebar.session-updates.v1` | 同前缀换名 | `App.tsx:132` |
| `nanobot-webui.sidebar.completed-runs.v1` | 同前缀换名 | `App.tsx:133` |
| `nanobot-webui.restartStartedAt` | 同前缀换名 | `App.tsx:134` |
| `nanobot-webui.restartRoute` | 同前缀换名 | `App.tsx:135` |
| `nanobot-webui.collapsed-pane-groups.v1` | 同前缀换名 | `ChatList.tsx:86` |
| `nanobot.locale` | `zhixu.locale` | `i18n/config.ts:1`、`index.html:135` |
| `nanobot-static-`（SW 缓存前缀） | `zhixu-static-` | `public/sw.js:1` |

**一次性迁移（推荐，成本约 10 行）**：在 `index.html` 已有的一次性 boot 脚本里，读新键失败时回退旧键并回写新键。用户的主题与语言不重置。

```js
// index.html boot 段（示意，放 index.html:104 附近）
var THEME = "zhixu-webui.theme", LEGACY_THEME = "nanobot-webui.theme";
stored = localStorage.getItem(THEME) || (function () {
  var old = localStorage.getItem(LEGACY_THEME);
  if (old) { localStorage.setItem(THEME, old); localStorage.removeItem(LEGACY_THEME); }
  return old;
})();
```

侧栏折叠类键（`sidebar` / `collapsed-pane-groups`）**不做迁移**——重置代价极低（用户重新折叠一次），迁移收益不抵复杂度。

SW 缓存前缀换名后旧缓存自然失效并被清理，**无需迁移**（这正是换名的目的）。

### 2.6 npm 包名与构建配置

| 旧 | 新 | 文件 |
| --- | --- | --- |
| `"name": "nanobot-webui"` | `"name": "zhixu-webui"` | `webui/package.json:2` |
| 根包名两处 | 同步 | `webui/package-lock.json:2,8` |
| 根包名 | 同步 | `webui/bun.lock` |
| `"name": "@nanobot/tui"` | `"name": "@zhixu/tui"` | `tui/package.json:2` |
| 根包名 | 同步 | `tui/bun.lock` |
| 插件名 `nanobot-guard-webui-entry-chunk` | `zhixu-…` | `webui/vite.config.ts:19` |
| 插件名 `nanobot-gzip-webui-assets` | `zhixu-…` | `webui/vite.config.ts:149` 附近的 `:49` |
| HMR 路径 `/__nanobot_vite_hmr` | `/__zhixu_vite_hmr` | `webui/vite.config.ts:112` |

> **锁文件处理**：**手工改根包名的 1–2 行，不重新生成锁文件**。重新生成会引入与本次改名无关的依赖漂移，违反 `project.profile.md`「禁止在本功能内顺便更新依赖」。

`webui/package.json:13` 的 lint 脚本、`eslint.config.js:16`、`tailwind.config.js:10`、`tsconfig.json:37-38` 里的 `nanobot/channels/*/webui/**` glob **本期不动**（依赖 Phase 3 的包目录改名）。

### 2.7 i18n key

10 个语言文件的 `nanobotFeatures` 顶层 key → `zhixuFeatures`（`webui/src/i18n/locales/{en,zh-CN,zh-TW,ja,ko,fr,es,pt-BR,vi,id}/common.json` 各 1 处）。

**value 本期不动**（Phase 1 已处理品牌文案；Phase 3 处理命令字面量）。改 key 名需同步 `i18n/config.ts` 与所有 `t('settings.nanobotFeatures.…')` 调用点。

### 2.8 SKILL.md frontmatter

| 旧 | 新 | 文件 |
| --- | --- | --- |
| `metadata: {"nanobot":{...}}` | `metadata: {"zhixu":{...}}` | `skills/github`、`skills/weather`、`skills/summarize`、`skills/tmux`、`skills/image-generation`、`skills/clawhub`、`skills/cron` |

**解析端双回退**（照抄 `agent/skills.py:345` 现有 `openclaw` 模式，加一层）：

```python
# nanobot/agent/skills.py:345 与 nanobot/webui/skills_api.py:260 同步
payload = (data_object.get("zhixu")
           or data_object.get("nanobot")     # 外部 skill 兼容，保留 ≥1 个大版本
           or data_object.get("openclaw", {}))
```

### 2.9 文档与配置

| 文件 | 改动 |
| --- | --- |
| `docs/websocket.md:267` | `X-Nanobot-Auth` → 「`X-Zhixu-Auth`（旧名 `X-Nanobot-Auth` 兼容）」 |
| `AGENTS.md:122` | 同上 |
| `webui/README.md` | 随前端标识符同步的部分 |
| `.claude/settings.json:103,104,107` | `curl -H 'X-Nanobot-Auth: …'` → `X-Zhixu-Auth` |

## 3. 执行顺序

1. Python 侧文件 `git mv` + 符号改名（§2.2）—— 先改后端，供前端对齐。
2. HTTP 路由 / WS key（§2.3）—— 后端先行，前端随后。
3. 前端 `git mv` + 符号改名（§2.1）。
4. HTTP 头双读（§2.4）。
5. localStorage 迁移 + SW 前缀（§2.5）。
6. npm 名 / Vite 配置（§2.6）。
7. i18n key（§2.7）、SKILL frontmatter + 双回退（§2.8）。
8. 文档与 `.claude/settings.json`（§2.9）。
9. **重建 webui dist**（`bun run build` 或 `npm run build`）。

## 4. 明确不做

- ❌ 不动 Python 包目录、不动 Python import。
- ❌ 不动 `NANOBOT_*`、`~/.nanobot`、CLI 命令。
- ❌ 不动构建五连（`outDir` 等）—— 属 Phase 3。
- ❌ 不重新生成 lock 文件。
- ❌ 不动渠道前端 glob（`nanobot/channels/*`）—— 属 Phase 3。

## 5. 验收

```powershell
# 1. Python 无回归
uv run --no-sync pytest -q
uv run --no-sync ruff check nanobot tests conftest.py
uv run --no-sync basedpyright

# 2. WebUI 编译 + 测试 + 构建（bun 不可用时用 npx/npm）
cd webui; npx tsc -p tsconfig.build.json
cd webui; npx vitest run
cd webui; npm run build

# 3. TUI 无回归
cd tui; npx tsc --noEmit        # bun 不可用时的等价检查

# 4. 协议一致性断言：后端路由与前端调用必须同改
rg -n "nanobot-features|nanobot_features" webui/src nanobot --glob '!**/node_modules/**'
# 期望：仅剩 skills.py / skills_api.py 的 frontmatter 回退分支（§2.8）

# 5. 头名双读在位
rg -n "X-Zhixu-Auth" nanobot/webui/http_utils.py nanobot/channels/websocket/runtime.py webui/src/lib/bootstrap.ts tui/src/protocol.ts

# 6. 持久化键迁移在位
rg -n "nanobot-webui\.theme|zhixu-webui\.theme" webui/index.html
```

**行为验收**：

1. 升级后打开 WebUI → **主题与语言未被重置**（验证 §2.5 迁移生效）。
2. 用 devtools 手工发一个带 `X-Nanobot-Auth` 的请求 → **仍然认证通过**（验证双读）。
3. 切换 channel 的 feature 开关 → WebUI 与渠道面板同步（验证路由 + WS key）。
4. `docker build` 后镜像内 `nanobot/web/dist/` 存在且可访问（验证 dist 重建进了 wheel）。

## 6. 风险

| 风险 | 缓解 |
| --- | --- |
| 改了源码但没重建 dist → 线上跑的还是旧路由 | §3 第 9 步 + 验收行为项 4 |
| 双头删除过早，旧 dist 连新 gateway 失败 | 兼容期 ≥1 个大版本，三期兼容代码同版本下线 |
| localStorage 迁移脚本写错 → 主题丢失 | 迁移只做「读旧→写新→删旧」单向；验收行为项 1 实测 |
| i18n key 改名漏改调用点 → 功能项消失 | TypeScript 编译拦截；`rg "settings.nanobotFeatures"` 应为空 |
| lock 文件重新生成引入依赖漂移 | §2.6 明确手工改，不重新生成 |
| bun 缺失导致无法验收 | 执行前置检查，见 §0 |

## 7. Spec 自检

- **占位符**：无。行号与文件名已核对到调研时的实测值。
- **内部一致**：§1 边界与 §4 明确不做一致；与契约 §5「Phase 2 改前端符号 + 协议路由 + 构建路径，不改 Python 包目录」一致。
- **歧义**：「双读」已给出具体代码形态与「客户端只发新头」的理由；localStorage 迁移已区分「迁移」与「不迁移」的键。
- **范围**：可拆为 2 个并行 WU（前端符号 / 后端协议），但两者通过路由契约强耦合，**建议单 WU 顺序执行**以避免 dist 重建竞态。

## Next

- 本期方案无误 → 说「写计划」或「制定实施计划」
- 需要调整 → 直接说修改意见
