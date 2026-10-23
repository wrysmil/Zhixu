---
artifact: verification-lite
route: leader-direct
skills:
  - source-driven-development
  - verification-before-completion
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - 用户要求：智能体页默认视图改为卡片；默认主 Agent 不再叫「指挥官」，改取一个专属名字
created_at: 2026-10-20
tier: 1
verdict: PASS
---

# 智能体页默认卡片视图 + 默认主 Agent 改名 — 轻量验证（Tier 1）

## 范围

- `nanobot/agents/catalog.py`：`DEFAULT_MAIN_AGENT_ID`（仍是 `commander`）对应的出厂档案 `name` 由「指挥官」改为「知序指挥官」，prompt 首句同步；id 不变，避免用户已有会话/团队/本地选择里的 `commander` 失效。
- `webui/src/components/settings/agents/AgentsView.tsx`：`view` 初值 `"list"` → `"card"`。
- `webui/src/components/settings/agents/AgentCard.tsx`：卡片容器补 `role="group"`，与 `AgentRow` 同语义，使「默认档案排第一」这类断言不依赖当前视图。
- 测试同步：`tests/webui/test_agents_runtime.py`、`tests/agent/test_turn_agent_profile.py` 的默认档案断言改为「知序指挥官」；前端四个测试文件的默认档案 fixture 同步改名；新增「默认以卡片视图打开」用例。
- 未改 API、数据模型、存储格式；未提交。

## 命名取舍

- 用户点名要「知序指挥官」：前半是产品名（知序），后半是它在工作区里的职务（接住请求、定方向、派活），不再是空泛的「指挥官」。
- 职务细节仍写在 `description` 与 prompt 里（「默认主 Agent」「接住请求、组织工具与子 Agent」）。
- 存储层只补出厂预设、不覆盖磁盘上已有档案（`AgentStore._load_all`），因此本机若曾保存过 `agents/profiles/commander.json`，需要「重置」一次才会看到新名字；本机检查过 `~/.nanobot` 下无该文件。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `.venv\Scripts\python.exe -m pytest tests/webui/test_agents_runtime.py tests/agent/test_turn_agent_profile.py tests/webui/test_agents_catalog.py -q` | PASS，56 passed |
| `vitest run agents-view-integration / thread-composer / message-bubble / thread-shell` | PASS，227/227（含新增默认卡片视图用例） |
| `.venv\Scripts\python.exe -m ruff check nanobot/agents/catalog.py` | PASS |
| `eslint AgentCard.tsx AgentsView.tsx agents-view-integration.test.tsx` | PASS（exit 0） |
| `tsc --noEmit -p webui/tsconfig.json` | 106 个既有错误，均在本次未触碰的行（如 `thread-viewport.test.tsx`、`zhixu-client.test.ts`）；本次改动文件新增 0 个 |

## 环境说明（不是代码问题）

- 沙箱下 pytest 的 `tmp_path` 会因 `mkdir/chmod 0o700` 写的显式 DACL 而全部 setup 失败；本次用一次性 `-p pytest_nochmod` 插件（跑完即删）绕开，测试逻辑未改动。
- vitest 退出时写 `webui/node_modules/.vite/vitest/results.json` 报 EPERM，属沙箱缓存目录权限，测试结果本身有效（进程退出码为 1，但用例全绿）。

## 波动记录

- 首轮分批跑 `thread-shell.test.tsx` 时，`hides the team picker on a temporary chat surface`（`thread-shell.test.tsx:1342`）失败过一次；同一文件随后单独复跑与四文件合跑均通过。该用例落在 `ThreadShell`/`TeamSelector`（当前工作区有他人未提交改动），与本次改名无关，记录为偶发而非阻塞。

## Next

- 若认可「知序指挥官」，无需再动；若想换名字，只改 `nanobot/agents/catalog.py` 的 `name` 与 prompt 首句两处。
- 团队选择器那条偶发失败建议在后续缺陷轮次单独加隔离条件，不并入本次改名。
