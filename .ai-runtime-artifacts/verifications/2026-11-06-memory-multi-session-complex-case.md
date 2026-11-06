# 复杂案例：多轮排障记录 + 切换会话跨会话召回

- 状态：待执行
- 日期：2026-11-06
- 目标：验证「多轮对话写入经验库 → 切换新会话 → 用不同措辞召回」整条链路，覆盖
  pitfall / skill / decision 三类沉淀、长期记忆注入、向量语义召回和前端筛选。

## 场景

模拟一个真实项目 `shop-api`（FastAPI + uv + Docker Compose）的上线与排障过程。
连续 3 个错误、2 次修复、1 个约定升级，最后切到全新会话追问。

## 环境

- 独立 HOME / workspace，避免污染真实 `~/.nanobot`。
- `memorySearchBackend=chromadb`，本地模型 `bge-small-zh-v1.5`。
- `memoryPitfallHint=true`（观察项，不强制）。
- WebUI 已登录，模型 MiniMax-M3.2。
- 当前工作区下创建真实子目录 `shop-api/`，让工具调用产生真实报错，而不是纯口述。

## 会话 A：多轮排障（同一会话）

| 轮次 | 用户消息 | 预期 Agent 行为 | 预期落库 |
| --- | --- | --- | --- |
| A1 | 「这是 shop-api 项目：Python 3.11 + FastAPI，依赖用 uv 管理，部署用 Docker Compose，健康检查统一走 `/healthz`。请记住这些项目约定。」 | 调用 `memory_write(scope=project, section=项目约定)`，并读回核实 | `MEMORY.md` 项目正文，不写 `memories` |
| A2 | 「先帮我在 shop-api 下起一个最小 FastAPI 服务，然后跑测试。跑 `uv run pytest` 报 `ModuleNotFoundError: No module named 'httpx'`，我 `pip install httpx` 之后还是报错，为什么？」 | 实际创建文件/执行命令；诊断“装到了系统 Python，不在 uv 的 venv”；修复为 `uv add --dev httpx`；复测通过 | 询问是否沉淀；用户答「记一下」后 `experience_record(kind=pitfall)` |
| A3 | 「docker compose up 之后容器一直 unhealthy，`docker inspect` 显示健康检查失败，日志是 `connection refused`。刚才明明本地能访问。」 | 实际写 compose 并启动（或模拟）；定位健康检查/监听地址问题（如 healthcheck 用 `localhost` 或镜像无 curl）；修复并验证 | `kind=pitfall`，症状/根因/修复/验证字段完整 |
| A4 | 「我们把健康检查统一成 `python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"` 了。以后新服务都这么配，记成技能吧。」 | 提议升级为 skill，确认后写入 capability + prerequisites + steps + verification | `kind=skill`，`metadata.steps` 非空 |
| A5 | 「另外定一下：健康检查端点用 `/healthz` 而不是 `/health`，原因是 k8s 社区约定和网关白名单。记成决策。」 | 写入 decision，包含 context / options / choice / rationale | `kind=decision`，`metadata.choice=/healthz` |
| A6 | 「这次一共踩了哪些坑？按类型列一下。」 | 调用 `memory_search`（同会话）并按 kind 汇总 | 回答包含 A2/A3 两条 pitfall |

## 会话 B：切换新会话后的跨会话召回

点击「新聊天」创建全新会话，不携带 A 的对话上下文，只依赖：
长期记忆注入（项目 MEMORY.md）+ 经验库检索。

| 轮次 | 用户消息 | 预期行为 | 通过标准 |
| --- | --- | --- | --- |
| B1 | 「shop-api 用什么管理依赖？部署方式是什么？」 | 直接读注入的项目长期记忆或 `read_file(MEMORY.md)` | 答出 uv + Docker Compose，不需要检索经验库 |
| B2 | 「新服务容器起来了但健康检查一直失败，提示 connection refused，之前我们怎么处理的？」 | `memory_search(kind="pitfall")`；向量召回 A3，即使措辞不同 | 答出 A3 的根因和修复方式（健康检查命令/地址问题） |
| B3 | 「新服务健康检查应该怎么配？给我标准步骤。」 | `memory_search(kind="skill")` | 返回 A4 的 capability + 编号步骤 + 验证方式 |
| B4 | 「健康检查端点用 /health 还是 /healthz？为什么？」 | `memory_search(kind="decision")` | 返回 /healthz + 理由（k8s 约定/网关白名单） |
| B5 | 「那 A2 那个 httpx 的坑呢？换个说法：测试依赖明明装了，pytest 还说找不到。」 | `memory_search(kind="pitfall")` | 命中 A2，答出 uv venv 隔离与 `uv add --dev httpx` |

## 会话切换与回看

1. 从 B 切回 A：A 的对话历史仍在，问「我们刚才修了什么？」应基于会话上下文回答。
2. 再新建会话 C：只问 B5，验证不依赖任何单次会话缓存，仍能召回。
3. 打开「记忆 → 经验库」，按类型筛选：
   - 踩坑：至少 2 条（A2 / A3）；
   - 技能：1 条（A4）；
   - 决策：1 条（A5）；
   - 搜索「连接被拒绝」「依赖找不到」应能命中对应条目。
4. 打开「长期记忆 → 项目」，确认 `## 项目约定` 含 shop-api 的 uv / Compose / /healthz。

## 数据核对

```sql
-- 1. 类型分布
SELECT json_extract(metadata,'$.kind') AS kind, COUNT(*)
FROM episodes WHERE source='experience' GROUP BY 1;

-- 2. 字段完整性
SELECT id, json_extract(metadata,'$.title') AS title,
       json_extract(metadata,'$.status') AS status
FROM episodes WHERE source='experience' ORDER BY ended_at DESC;

-- 3. 长期记忆没有回写 SQLite
SELECT COUNT(*) FROM memories;
```

- ChromaDB `episodes` 集合数量应 ≥ 新增条数；`episodes` 中每条都能按标题找到。
- `memories` 表应保持 0 行（文件模式）。

## 截图清单

保存到 `.ai-runtime-artifacts/verifications/memory-ui/multiturn/`：

| 文件 | 内容 |
| --- | --- |
| `a1-memory-write.png` | 项目约定写入并核实 |
| `a2-pitfall-httpx.png` | httpx 踩坑与修复 |
| `a3-pitfall-healthcheck.png` | 健康检查踩坑 |
| `a4-skill.png` | 技能沉淀 |
| `a5-decision.png` | 决策沉淀 |
| `b1-memory-inject.png` | 新会话直接答出项目约定 |
| `b2-cross-session-pitfall.png` | 新会话召回 A3 |
| `b3-skill-steps.png` | 新会话召回技能步骤 |
| `b4-decision.png` | 新会话召回决策理由 |
| `b5-paraphrase.png` | 换措辞召回 A2 |
| `ui-kind-filters.png` | 经验库按类型筛选 |
| `ui-project-memory.png` | 项目长期记忆含 shop-api 约定 |

## 通过标准

1. A2/A3 至少两条 pitfall 落库，症状/根因/修复/验证齐全。
2. A4 skill 的 steps 至少 2 步；A5 decision 的 choice 与 rationale 可读。
3. 新会话 B 不依赖旧会话上下文，B2/B3/B4/B5 全部命中正确类型与内容。
4. 长期记忆在 B1 直接生效，且 `memories` 表仍为 0。
5. 前端筛选、搜索、项目记忆展示与数据一致。
6. 至少一次「换措辞」召回（B5 或直接查向量）证明语义召回生效。

## 失败判定

- 新会话找不到 A 的记录 → 检查 `memory_visibility_ids` 与 workspace_id 是否一致。
- 只命中关键词完全相同的记录、换措辞失败 → 检查向量集合是否回填、模型是否 ready。
- 经验被写进 `MEMORY.md` 或规则被写进 episodes → 分流错误。
- UI 筛选与 SQLite 类型不一致 → 检查 `memory-doc` / `experience-list` 参数传递。

## 执行方式

- 手动：按上表逐轮在 WebUI 发送，每轮截图；结束后跑数据核对。
- 自动：Playwright 脚本逐轮发送并等待 `停止响应` 消失；SQL/Chroma 用 Python 脚本核对。

## 执行结果（2026-11-06 19:15–19:38）

模型 MiniMax-M3.2，真实工具执行（uv、pytest、docker compose），每轮截图后由旁路校验器
读取 SQLite 与 ChromaDB，并写 `<截图>.verify.json`。

| 轮次 | 截图后 kinds（pitfall/skill/decision/experience/reference） | 总数 | 向量 | 本轮增量 |
| --- | --- | --- | --- | --- |
| A1 | 1/1/1/2/1 | 6 | 6 | 仅写项目 MEMORY.md |
| A2 | 2/1/1/2/1 | 7 | 7 | +1 pitfall（httpx / uv 环境） |
| A3 | 2/1/1/2/1 | 7 | 7 | 0（本轮未落库） |
| A4 | 3/2/1/2/1 | 9 | 9 | +1 pitfall（A3 补记）+1 skill |
| A5 | 3/2/2/2/1 | 10 | 10 | +1 decision |
| A6 | 3/2/2/2/1 | 10 | 10 | 0（只检索） |
| B1–B5 | 3/2/2/2/1 | 10 | 10 | 0（只读检索） |
| C1 | 3/2/2/2/1 | 10 | 10 | 0（只读检索） |

### 证据

- A2 pitfall：`af2b0b0542b8`，根因是 uv / pip / PATH 三套解释器不一致，修复用 `uv add --dev`。
- A3 pitfall：`3431809008bc`，症状 Connection refused，区分 slim 镜像缺 curl 与应用未监听。
- A4 skill：`aac0b34d8130`，含 capability、前置条件、urllib 探活步骤、验证方式。
- A5 decision：`404718c1`，/healthz，理由是 K8s 约定与网关白名单。
- B2 命中 `3431809008bc`；B3 命中 `aac0b34d8130`；B4 命中 `404718c1`；
  B5 换措辞“依赖装了 pytest 还说找不到”，仍命中 A2 的 `af2b0b0542b8`。
- C1（第三个会话）再次命中 `af2b0b0542b8`，说明不依赖单次会话缓存。
- `memories` 表保持 0 行；项目 `MEMORY.md` 写入 shop-api 的 uv / Compose / healthcheck 约定。
- ChromaDB `episodes` 最终 10 条，与 SQLite 一致。

### 观察到的问题

1. **A3 当轮未落库**：截图时 pitfall 仍为 2；A4 的计划里模型自己发现“上一轮 pitfall 未落库”
   并补记。最终无数据丢失，但沉淀被推迟了一轮，说明“沉淀”仍可能被主任务挤掉。
2. A1 首次写入出现重复的 `## 项目约定` 标题，模型自行用 replace 修复；`memory_write` 的
   section 合并策略仍值得后续加固。
3. B 脚本最后“切回会话 A”的定位器因话题标题被省略号截断而超时；产品侧会话切换本身正常，
   已用 regex 重新验证并截图 `switch-back-a.png`。

### 截图

`.ai-runtime-artifacts/verifications/memory-ui/multiturn/`：`a1`–`a6`、`b1`–`b5`、
`c1-third-session.png`、`switch-back-a.png`，每条截图旁有同名 `.verify.json`。
