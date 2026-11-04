---
artifact: verification-lite
route: leader-direct | Tier 1 Leader 直做
skills:
  - verification-before-completion
skills_evidence:
  - ~/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户原话：「现在项目中的内置的 SOUL 和 Agent.md 还是和 openakita 差别好大……你看比如说这种就可以参考啊」
  - 决策：AskUserQuestion 三问（出厂 AGENT.md 是否注入 / 篇幅 / 老 workspace 升级路径）
created_at: 2026-11-04
tier: 1
---

# 内置 SOUL / AGENT 出厂模板改版 — 轻量验证（Tier 1）

> **适用：** Leader 直做（≥2 写文件 / 跑测试 / 行为变更类），**不**走 spec/plan/WU 编排。
> **纪律：** 先跑命令再给结论。

## 范围

改动文件：

| 文件 | 改动 |
| --- | --- |
| `nanobot/templates/SOUL.md` | 重写：本体层，对齐 openakita 骨架（优先级排序 / Being Helpful / Being Honest / Avoiding Harm / 大局安全 / 内在驱动 / 自主能动性 / 身份认知），占位符换知序实际 |
| `nanobot/templates/AGENT.md` | 重写：方法论层（Ralph Wiggum / 场景判别 / 任务执行流 / 每轮自检 / 成长循环 / 自我修复 / 能力扩展 / 工具优先序 / 验证 / 记忆规范 / 子 Agent / 禁止行为），工具名全部换知序实际 |
| `nanobot/templates/personas/{balanced,mentor,creative,companion}.md` | 去掉 SOUL 副本式结构，改成表现层（tech_expert 已是此形态） |
| `nanobot/templates/legacy/{SOUL-cn.md,AGENT.md}` | 新增：历史出厂版本副本，供自动升级比对 |
| `nanobot/agent/context.py` | `_SKIPPABLE_DEFAULTS` 去掉 `AGENT.md` → 出厂行为准则也进系统提示 |
| `nanobot/identity/compiler.py` | 出厂标记扩容覆盖新章节；预算 1200/900 → 6000；收窄改为「无损才用」（丢内容就收全文）；跳过目标时清掉过期产物 |
| `nanobot/identity/catalog.py` | 新增 `char_limit_for()`：SOUL/AGENT 上限 8000，其余仍 1500 |
| `nanobot/identity/store.py` | 写入校验与清单按文件取上限 |
| `nanobot/identity/bootstrap.py` | 新增 `upgrade_legacy_factory_templates()`：未定制的老出厂副本自动升级 |
| `webui/src/components/workspace/IdentityPage.tsx` / `webui/src/lib/types.ts` | 字符上限按文件取；字符计数对所有文件显示 |
| `tests/identity/*`、`tests/agent/test_context_builder.py`、`tests/webui/test_identity_routes.py` | 断言更新 + 新增用例 |

路由判定：Tier 1（Leader 直做）。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `pytest tests/identity/ tests/agent/ tests/webui/ -q` | 2886 passed / 3 failed / 4 skipped；3 个失败在 HEAD 基线（临时 worktree 复现）同样失败，与本次改动无关 |
| `pytest tests/identity/ -q` | 97 passed, 1 skipped |
| `ruff check`（本次改动的 8 个 py 文件） | All checks passed |
| `basedpyright nanobot/identity/ nanobot/agent/context.py` | 1 error（`store.py:188` `isinstance` 冗余，未改动的既有行） |
| `webui: tsc -p tsconfig.build.json` | 通过（首轮报 `IdentityFileEntry.charLimit` 缺失，已补类型） |
| `webui: vite build` | ✓ built（首次失败为 esbuild 临时目录 EPERM，换 TMPDIR 后通过） |
| `webui: vitest run src/tests/identity-page-editor.test.tsx` | 5 passed |

### 行为验证（实机渲染系统提示）

新 workspace 播种 + 编译后 `_load_bootstrap_files()`：

```
prompt chars = 9272
'## SOUL.md'  -> True      '## AGENT.md' -> True
'Being Helpful' -> True   'Ralph Wiggum Mode' -> True
'资源穷尽原则' -> True     'Self-Healing Protocol' -> True
'Capability Expansion Protocol' -> True    'Tool Priority' -> True
```

| 场景 | 结果 |
| --- | --- |
| 出厂 SOUL 编译产物 | 4324 字符（改版前预算 1200，会被腰斩） |
| 用户改过的 AGENT.md 编译产物 | 3827 字符，保留用户自定义章节与正文 |
| 老 workspace 未定制文件 | 自动升级到新出厂模板（SOUL + AGENT 均验证） |
| 用户自己写过的文件 | 不被升级（内容逐字比对） |

### 真实客户端验证（重启网关后）

网关 PID 53292（17:24 启动，早于本次改动）重启后（PID 26440），后端按文件下发上限：

```
SOUL.md  charLimit=8000     USER.md  charLimit=1500
AGENT.md charLimit=8000     MEMORY.md / policies.md / 5 个 persona = 1500
```

身份页实机截图（Playwright + msedge headless）：
`.ai-runtime-artifacts/verifications/identity-page-after-restart.png`
—— AGENT.md 显示 `4921 / 8000 字符`，无超限告警，内容为新出厂模板。

顺带修掉两处文案/行为不准：

1. 字符计数改为对所有文件显示后，原文案「保存后将被自动截断」只对 MEMORY.md 成立
   （记忆生命周期会截断），其余身份文件是 `IdentityStore` 直接 400 拒绝。拆成
   `charCountExceeded`（截断）与 `charCountRejected`（拒绝）两个 key，10 个语言同步。
2. 上限按文件下发后前端 textarea 与后端校验同源，不再出现「出厂模板存不回来」。


### 顺带修掉的一个静默丢规则缺陷

改版前实测：用户在 `identity/AGENT.md` 追加 `## 随手记`，编译后**整段消失**
（`随手记` / `pnpm` 两条断言均为 False）——章节收窄按 marker 挑章节，用户自加的
标题不命中就丢正文。出厂模板变长后这会成为常态。

改法：收窄只在**无损**时采用，一旦会丢正文就收全文（仍受字符预算封顶）。
复验：`## 随手记` 与 `pnpm` 规则在编译产物与系统提示中均保留。

## 未验证项

- 未在真实桌面端跑一轮对话看行为差异（只验证了系统提示的渲染内容）
- `nanobot/web/dist` 已由 `vite build` 重新生成，未提交（工作区既有产物）
- 工作区里 `nanobot/cli/*`、`nanobot/config/schema.py` 等改动为本次任务之外的既有未提交内容，未触碰

## Next

- 任务完成 → 无需暂停（除非用户要求 commit/MR）
- 范围扩大 → 补 spec/plan 或升级 Tier 2 编排
