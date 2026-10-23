---
artifact: verification-lite
route: leader-direct
skills:
  - systematic-debugging
  - source-driven-development
  - verification-before-completion
skills_evidence:
  - ~/.agents/skills/systematic-debugging/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - ~/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户原话：「请对创作空间的功能做冒烟，目前我一点击启动完整流程，直接报错了：写作搭档无法处理该请求，请重试。」
  - 复现取证的现场数据：`~/.nanobot/workspace/writing/state.db`（三张协作表全空、mtime 停在 15:37）
created_at: 2026-10-17
tier: 1
---

# 创作空间「启动完整流程」冒烟 + 500 通用错误修复 — 轻量验证（Tier 1）

> **适用：** Leader 直做简单任务（≥2 写文件 / 跑测试 / fix 类），**不**走 spec/plan/WU 编排。
> **纪律：** Load `verification-before-completion`；先跑命令再给结论。

## 防跳过提醒

| 合理化借口 | 现实 |
|-----------|------|
| "报错文案是通用的，看不出根因" | 通用文案 = 未登记异常被折成 500，必须把真异常捞出来 |
| "单测全绿，说明没问题" | 本次恰恰是单测全绿而生产必炸：runner 单测造对象、webui 单测用宽松假 runner，没人跨过接缝 |
| "手工点一次就行" | 手工点击没有记录、不能重跑，且失败在建协作前，肉眼看不到任何落库痕迹 |
| "改一行不用验证" | 改的是执行栈入口形状，必须真跑（红灯→绿灯 + 真链路复现） |

## 范围

- 改动文件：
  - `nanobot/writing/collab/runner.py`（新增 `coerce_start_request()`：把 payload 层的普通 dict 归一成 `StartCollabRequest`；`CollabRunner.start()` 入口调用它）
  - `tests/writing/test_collab_runner.py`（新增回归 `test_start_accepts_transport_payload_mapping`：走**真实** payload 层 → **真实**执行栈）
- 路由判定：Tier 1（Leader 直做，无派发、无 worktree）
- 未改动但相关的 WIP（**非本次改动**，不要混入提交）：`webui/src/components/workbench/PaneWorkbench.tsx`、`webui/src/tests/pane-workbench.test.tsx`

## 根因（Phase 1 取证结论）

**接缝形状不一致：**

- `nanobot/webui/writing_collab_api.py:355-...` 的 `_start_request()` 明确返回**普通 dict**（模块 docstring 声明「传输层不 import 领域类型」「runner 侧按关键字参数展开即可」）；
- `start_collaboration()` 把它原样传给 `runner.start(request)`（`writing_collab_api.py:384`）；
- 真执行栈 `CollabRunner.start()` 只做**属性访问**（`runner.py:412` 的 `request.action`），于是必然抛
  `AttributeError: 'dict' object has no attribute 'action'`。

该异常不在 `writing_collab_api.translate_error()` 的登记表内 → `WritingCollabSettingsHandler._failure()`
兜底成 `500 writing collaboration request failed` → 前端 `errorKindForError()`
（`webui/src/components/creative/collab-labels.ts:154-173`）既认不出 message、500 也不在状态表里 →
落到 `creative.partner.ui.errorUnknown` = 「写作搭档无法处理该请求，请重试。」

**失败点在建协作之前**，所以现场不留任何痕迹：三张协作表 0 行、`article_requests` 无新增、
`state.db`（`~/.nanobot/workspace/writing/state.db`）mtime 不前进 —— 与用户「一点就报错」的现象完全一致。

**为什么既有测试全绿却漏掉：** runner 单测一律构造 `StartCollabRequest` 对象；
`tests/webui/test_writing_collab_wiring.py` 的 `FakeRunner`（:315）与 `_fields()`（:349）**同时接受映射与对象**，
把两个形状抹平了 —— 没有任何一条用例跨过这个接缝。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| 真 gateway WS 复现（`webui_request` → `writing.start`，附真实 article_id/revision/digest） | `{"event":"webui_response","ok":false,"error":{"status":500,"message":"writing collaboration request failed"}}` —— 精确复现用户现象 |
| 复现脚本（真实 payload 层 + 真实 runner，DB 副本 `/tmp/zhixu-smoke/state.db`）**修复前** | `AttributeError: 'dict' object has no attribute 'action'`（栈：`writing_collab_api.py:384` → `runner.py:412`） |
| `pytest tests/writing/test_collab_runner.py::test_start_accepts_transport_payload_mapping`（**修复前**，TDD 红灯） | `1 failed`，失败原因即上面的 AttributeError |
| 同一条用例（**修复后**） | `1 passed in 0.55s` |
| `pytest tests/writing/ tests/webui/test_writing_collab_wiring.py tests/webui/test_writing_wiring.py -q` | `286 passed in 2.45s` |
| 复现脚本（**修复后**，真实 `SkillsLoader` + 真实 `prepare_frozen`） | `START OK: {'collaboration_id': 'bf295310...', 'status': 'running', ...}`，`[stub team] start_batch action=start_full ... frozen_skills=7` |
| `ruff check nanobot/writing/collab/runner.py` | `All checks passed!` |
| `ruff check nanobot/` | `Found 10 errors`（**全部在 `nanobot/memory/*`，本次未触碰**：F401×8、N806×2） |
| `basedpyright nanobot/writing/collab/runner.py` | **4 errors**（改动前同为 **4 errors**，逐条比对同一批：`converge_collaborations` 的 `str｜Path`、`_frozen_skills`/`_with_identity` 的 `Mapping[Unknown, Unknown]`；本次新增代码零告警） |
| 现场库取证（`sqlite3`） | 修复前后：`writing_collaborations` / `writing_collab_batches` / `writing_collab_artifacts` / `writing_collab_handoffs` 均为 `0` 行；`articles` 有用户 15:37 建的《Redis的原理》 |
| 活 gateway 冒烟探针（bootstrap + WS 聊天） | `bootstrap ok`、`turn_end latency_ms=952`、回复 `ok` —— 证明**该进程**的 LLM 链路通、环境可跑真链路 |

## 未验证项

- **真链路 `start_full`（真团队 + 真 LLM）尚未跑通**：当前 gateway 进程（pid 4451，`~/.nanobot/run/gateway.json`）启动于 15:35，
  内存里仍是修复前的模块；Python 不热加载，**必须重启 gateway/桌面端**后才能用真实 WS 走完整链路。
  在重启前，上述「修复后」证据止于 `TeamPort.start_batch` 被真实调用（真 `prepare_frozen` 冻结到 7 个写作 Skill）。
- 未跑全量 `pytest tests/`（只跑了 `tests/writing/` 与两个 webui 接线文件）。
- 未跑 `bun run test` / `bun run build`：本次**未改前端**。
- **环境级隐患（非本仓库回归，已取证待定）**：若进程环境里 `NO_PROXY` 含 `[::1]`，
  httpx 0.28.1 构造 client 时抛 `httpx.InvalidURL: Invalid port: ':1]'`
  （`get_environment_proxies()` 生成 `all://*[::1]` 这个非法 pattern）。
  云端 provider 未显式给 proxy 时 `http_client=None` → SDK 用 `trust_env=True` 的默认 client
  （`nanobot/providers/openai_compat_provider.py:589` 注释即为此分支），于是所有云 LLM 调用在**建 client 阶段**就失败；
  历史日志 `~/.nanobot/logs/gateway.log`（03:40）正是这条报错，模型回 `Error calling LLM: Invalid port: ':1]'`。
  项目自己的 `nanobot/security/network.py:198-211::_no_proxy_mount_pattern` 能正确处理 `[::1]`，
  但只被 web/MCP/Slack 用到，**没有接到 LLM provider 路径**。活 gateway 实测 LLM 通，说明它当前环境没带这个坏值。
  → 建议：环境侧去掉 `NO_PROXY` 里的 `[::1]`（与 `::1` 重复）；是否在 provider 侧做兜底加固属新范围，需另行拍板。

## Next

- 重启 gateway/桌面端 → 我立即用真实 WS 跑 `start_full` 全链路冒烟（起批次 → 产物/交接帧 → 停止/重试），把结果补进本文件。
- 若要求前端可见的「创作搭档报错文案」更可诊断（把 500 通用文案拆成可识别错误种类）→ 另开 Tier 2 spec。
- 用户已提出新需求「侧栏加模型选择」→ 走 `brainstorming` 出一份 spec 后暂停（见 `.ai-runtime-artifacts/specs/`）。
