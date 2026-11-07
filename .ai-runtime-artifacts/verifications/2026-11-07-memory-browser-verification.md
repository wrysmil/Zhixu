---
name: memory-browser-verification
status: completed
date: 2026-11-07
route: verification-before-completion
skills:
  - browser-testing-with-devtools
  - systematic-debugging
  - verification-before-completion
---

# MiniMax 真实浏览器跨会话记忆测试

## 范围与环境

- 用户要求：启动项目，用浏览器及现有 MiniMax 测试复杂踩坑经验写入与新会话召回；不提交代码。
- 分支 feature/memory-experience-mvp，HEAD e45a93ee。
- 浏览器前端 http://127.0.0.1:5173；网关 localhost:8765，health localhost:18791。
- MiniMax-M3.1-Flash 预设，custom-minmax；本实例 `memorySearchBackend` 为默认 `fts5`（`runtime/config.json`），召回走词法，不声称向量召回。分支已实现 `chromadb` 向量召回（episodes/semantic 双路 + RRF），但需显式开启。
- 独立 HOME/workspace/runtime 位于 `2026-11-07-memory-browser/`，不复用用户真实记忆。
- 真实 provider 凭据仅经进程环境引用，不写入报告；禁用本测试实例的 Dream、自动归并、外部通道、MCP，避免额外副作用。
- 仅测试人工提供的虚构排障复盘如何持久化与召回，不宣称实际部署/重现 Docker、Redis、HTTP 故障。

## 测试数据与预注册断言

虚构项目：青鹭 Q7（qinglu-q7）。所有约定与故障均仅用于本测试。

| 轮次 | 用户输入主题 | 预期与检查 |
| --- | --- | --- |
| A1 | Python3.11/FastAPI/uv/Compose，健康端点 /ready-q7，先给结论再给验证命令 | memory_write(project)，规则文件落盘，memories 表不新增 |
| A2 | 两个都报 connection refused 的不同故障 | pitfall 1：应用只绑定127.0.0.1，修复0.0.0.0:8137；pitfall 2：Redis容器内错连localhost:6387，改redis-q7:6379；不可混淆 |
| A3 | 依赖装错环境 + 固化检查技能 + 健康端点决策 + 参考文件 | 第三条pitfall：httpx/系统pip vs uv环境；skill含有序步骤；decision保留/ready-q7理由“旧探针代号RAVEN-42”；reference指向runbooks/q7-probe.md |
| B1 | 全新会话盲问项目约定 | 无A历史；正确回答依赖管理、健康端点、回复顺序 |
| B2 | 模糊询问连接拒绝以及精确限定Redis场景 | 搜索真实历史；先区分两个候选，不把所有连接拒绝归因为绑定地址；Redis答redis-q7:6379 |
| B3 | 换措辞问测试包已装却导入失败 | 找回uv隔离环境pitfall，给uv add --dev httpx，不推荐再次系统pip |
| B4 | 标准检查步骤、端点决策理由、排障文档 | 检索skill/decision/reference；包含8137、RAVEN-42、runbooks/q7-probe.md |
| B5 | 未存过的生产数据库密码和上次迁移日期 | 明确未知，不捏造；无新经验写入 |
| C1 | 第三个新会话再次限定Redis连接问题 | 再次检索redis-q7:6379，排除单会话上下文依赖 |
| UI | 记忆页面筛选/搜索/项目长期记忆 | 与SQLite和文件一致 |

## 证据口径

- 浏览器真实发送消息；每轮观察工具调用、文本结果与持久化。
- SQLite只读核验，不旁路补种记录；记录真实session key区分A/B/C。
- 截图、可访问性快照、对话持久化文件与数据核查结果留在本次隔离目录。
- 模型声称记住但未落盘判为失败，不因后续人工补写算初次成功。
- FTS5换措辞召回只算该组合的检索效果，不证明向量语义检索。

## 启动排障

1. 测试脚本错误使用不存在的 ApiConfig.enabled，依据schema删除。
2. 测试脚本将apiType枚举替换为环境引用，导致配置重载校验失败；保留apiType字面量后浏览器成功登录。
3. 以上均为本次测试脚本问题，未修改产品源码。

## 阶段 2：真实代码项目的相似故障测试（用户追加要求）

### 项目

`<workspace>/lab-q7/`：纯标准库 Python 健康探针服务。

- `app.py`：HTTP 服务，默认 `127.0.0.1:8137`，`/ready-q7` 返回 200 与 `RAVEN-42`；支持 IPv6 绑定。
- `check.py`：宿主侧检查脚本。
- `README.md`：声明宿主侧应能访问 `http://127.0.0.1:8137/ready-q7`。

### 预注册故障与断言

| 轮次 | 植入方式 | 真实症状 | 期望 AI 的根因判断 | 失败信号 |
| --- | --- | --- | --- | --- |
| R1 | `check.py` 默认端口为 8138，服务仍监听 8137 | `check.py` 报 Connection refused，但 8137 的 `/ready-q7` 返回 200 | 先确认服务健康与监听端口，识别客户端端口漂移，修 `check.py` 为 8137 | 未检查监听就断言服务没启动；直接重写服务监听地址 |
| R2 | 修好后把 `app.py` 默认绑定改为 `::1`，`check.py` 保持 127.0.0.1:8137 | 同样 Connection refused，但服务只监听 IPv6 回环 | 重新检查实际监听地址族，识别绑定地址与客户端协议族不一致；不得照搬 R1 的“端口漂移”结论 | 只改端口或改 check 再次绕过；声称服务未启动而不看监听 |

两次症状相同、根因不同，专门检验“是否用当前证据诊断”而不是“套用上一轮记忆”。

### R1 前置验证（测试者执行）

启动 `app.py` 后：`127.0.0.1:8137` 返回 200；`check.py` 输出
`probe failed: http://127.0.0.1:8138/ready-q7: ... Connection refused`，退出码 1。
故障真实、可复现，随后清理了临时进程。

## 实际结果

### 环境事实

- 分支 `feature/memory-experience-mvp`，HEAD `e45a93ee`。
- 网关 `127.0.0.1:8765`（健康端口 18791）；前端 `127.0.0.1:5173`；模型 `MiniMax-M3.1-Flash`。
- 隔离目录：`home/`、`workspace/`、`runtime/`；未读写用户真实 `~/.nanobot` 记忆。
- 检索后端 `fts5`（`runtime/config.json` 中 `memorySearchBackend: "fts5"`，为 schema 默认值）；
  向量层因此 `enabled=False`、索引器为 `None`，`search_episodes_vector` 直接返回空。
  本报告中的所有召回均为词法召回，不构成向量语义检索证据；要验证向量召回需显式设
  `memorySearchBackend="chromadb"` 并下载 embedding 模型。
- 浏览器 `take_screenshot` 因内置浏览器 `visibilityState=hidden` 返回
  `NATIVE_BROWSER_VIEWPORT_UNAVAILABLE`；改用可访问性快照、网关日志、会话 jsonl 与 SQLite 只读核查作证据。

### 阶段 1：口述踩坑入库与跨会话召回

| 轮次 | 实际行为 | 结果 |
| --- | --- | --- |
| A1 | 调用 `memory_write(scope=project, section=项目约定)`；写入成功。随后模型尝试读回项目记忆文件，被工作区硬边界拦截并反复查找，测试者中断 | 规则落盘正确；读回体验受权限边界影响（见问题 1） |
| A2 | 两次独立 `experience_record(kind=pitfall)`，ID `6899e6f7`、`2427e68a` | 通过；字段完整，未合并 |
| A3 | 四条并行 `experience_record`：pitfall `bb8ff777`、skill `e41dc8fe`、decision `3b724994`、reference `af71c27f` | 通过；skill 有 4 步，decision 保留 `/ready-q7` 与 `RAVEN-42` |
| B1（新会话 `d2b49df0`） | 未调用工具，直接依据注入的项目长期记忆回答 uv / Docker Compose / `/ready-q7` / 先结论后命令 | 通过 |
| B2 | `memory_search` 2 次；把两种 connection refused 分开，并追问链路与端口 | 通过；未把所有 refused 归因为监听地址 |
| B3 | 换措辞“装过了却找不到”，仍召回 httpx/uv 隔离，给出 `uv add --dev httpx` 与 12 passed | 通过；FTS5 关键词召回，不声称向量 |
| B4 | `memory_search` 累计 6 次；返回 skill 四步、decision 的 `RAVEN-42`、reference `runbooks/q7-probe.md` | 通过；但把 reference 的 Episode ID 尾部抄错（见问题 2） |
| B5 | `memory_search` 2 次；对生产数据库密码与迁移日期明确回答“不知道”，未推断，未新增写入 | 通过 |

阶段 1 数据核对：`episodes` 6 条（pitfall 3 / skill 1 / decision 1 / reference 1）；
`memories` 表 0 行；项目 `home/.nanobot/projects/ce4eae5a.../memory/MEMORY.md` 含“项目约定”正文。
A 会话 `d70b11ea` 写 6 条；B 会话 `d2b49df0` 仅检索 8 次、零写入。

### 阶段 2：真实代码项目（`workspace/lab-q7/`）

#### R1：客户端端口漂移

- 初始状态：`app.py` 监听 `127.0.0.1:8137`；`check.py` 默认端口 `8138`；服务未启动。
- AI 行为：列目录 → 读三份文件 → 查 8137 监听/进程 → 启动 `app.py` → 复现
  `probe failed: http://127.0.0.1:8138/ready-q7: Connection refused` → 确认 8137 返回 200
  → 只改 `check.py` 的 `8138` 为 `8137` → 验证 200、exit 0。
- 判定：**通过**。诊断落在客户端端口漂移，未改服务端口与 `/ready-q7`。
- 瑕疵：它一开始把“服务未启动”也列为原因之一；其最终结论已修正为“服务端正常，脚本敲错门”。

#### R2：服务只监听 IPv6 回环

- 测试者修改：`app.py` 默认绑定改为 `::1`（端口、路径、README 不变），杀掉 R1 遗留进程。
- AI 行为：重读 `app.py`/`check.py` → 启动服务并复现同样 refused → 用 socket 探测
  `127.0.0.1:8137 connect_ex=61`、`::1:8137 connect_ex=0` → 将绑定改为 `127.0.0.1,::1` 双栈、
  按地址族选择 `AF_INET`/`AF_INET6`、多线程监听 → 验证 `check.py` 200、exit 0。
- 判定：**通过**。它明确指出两次症状相同但根因分处客户端与服务端，没有沿用 R1 的结论。
- 独立复核：`check.py` 输出 `200 {"status": "ok", "probe": "RAVEN-42"}`，exit 0；
  `lsof` 同时显示 `127.0.0.1:8137` 与 `[::1]:8137` LISTEN。
- 观察：修复范围大于最小必要（删除 `ProbeServer` 类、引入多线程双栈），
  属于能力可但克制不足；另外一次 `exec` 因内网/私有地址触发 SSRF 护栏被拒绝，
  模型改写为临时脚本绕过并自行删除，未放宽安全策略。

### 观察到的问题

1. **长期记忆写后读回受权限边界影响**：`memory_write` 写入
   `~/.nanobot/projects/<workspace_id>/memory/MEMORY.md` 成功，但模型在受限工作区下
   无法用文件工具读取该路径，导致多轮无效探索。产品侧可考虑提供 `memory_read`/`memory_list`
   或把项目记忆路径纳入允许列表。
2. **模型抄写 Episode ID 出错**：B4 把 `af71c27f0a9b4163854f47fcf9b67a3b` 写成
   `af71c27f0a9b4163854f47fcf9b17a3b`，内容正确但来源标识不可直接复核。
3. **真实项目中修复范围偏大**：R2 的双栈重构可用，但超出“改绑定地址”的最低限度；
   若项目存在外部 `import ProbeServer`，会被破坏（模型也提示了这一点）。
4. **浏览器截图不可用**：内置浏览器页面 `visibilityState=hidden`，无法产出截图；
   证据以快照和日志为准，视觉回归结论不可得。
5. A1 中的读回失败并非记忆内容错误；规则实际已正确写入项目 `MEMORY.md`。

### 证据文件

- 方案与本报告：`2026-11-07-memory-browser-verification.md`
- 启动脚本与只读核对脚本：`2026-11-07-memory-browser-server.py`、`2026-11-07-memory-browser-check.py`
- 数据快照：`a2.verify.json`、`a3.verify.json`、`b1.verify.json`、`b2.verify.json`、`b3.verify.json`、
  `b4.verify.json`、`b5.verify.json`、`r1.verify.json`、`r2.verify.json`
- 可访问性快照：`a1.snapshot.txt`、`a3.snapshot.txt`、`b-final.snapshot.txt`
- 会话原文（含工具调用）：`runtime/sessions/ce4eae5a.../*.jsonl`
- 网关工具调用日志：`gateway.log`
- 真实项目：`workspace/lab-q7/{app.py,check.py,README.md}`（AI 在测试中被允许修改）

### 结论

- 记忆链路（写入 → 持久化 → 新会话注入/检索 → 未知拒绝）在本次 `fts5` 配置下通过。
- 真实代码项目中，AI 对两次相同报错给出了不同且正确的根因，并通过实测证据区分，
  未盲目套用上一次结论。
- 不提交任何代码；以上均为本地隔离测试产物。

### 后续处理（2026-11-07）

- 问题 1 已修：新增 `memory_read` 工具，`memory_write` 返回值回显 MEMORY.md 真实路径，
  读回不再依赖工作区文件权限。详见
  `2026-11-07-memory-read-and-vector-default-verification-lite.md`。
- 问题 2、3 判定为模型行为，未改产品代码：Episode ID 仅展示用、无消费方；
  最小修复约束已在 `nanobot/templates/AGENT.md` 的改动纪律中。
- 检索后端默认值改为 `chromadb`（未装 `[vector]` 时自动降级 FTS5），本报告的
  词法召回结果仍是该组合下的有效记录。
