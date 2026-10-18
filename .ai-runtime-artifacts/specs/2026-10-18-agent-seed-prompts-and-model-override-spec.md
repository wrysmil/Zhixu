---
artifact: spec
route: superpowers:brainstorming
skills: [source-driven-development, brainstorming]
skills_evidence:
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
  - /Users/mima0000/.qoder-cn/plugins/cache/qoder-marketplace/superpowers/6.3.0/skills/brainstorming/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/stack/2026-10-18-stack.md（复用今日已生成的依赖清单，未重复 STACK DETECTION）
  - .ai-runtime-artifacts/plans/2026-09-29-agents-runtime-integration-plan.md（model_resolver 划界的原始出处）
  - .ai-runtime-artifacts/plans/2026-10-15-agent-team-plan.md（验收 A17 / require_model_preset 出处）
  - .ai-runtime-artifacts/contracts/2026-09-29-agents-contract.md
  - 本会话对 nanobot/agent/subagent.py 的实跑取证
created_at: 2026-10-18
status: draft
approved: false
---

# 出厂 Agent 种子提示词与子 Agent 模型覆盖

## 1. 要解决的问题

两件事，同一个根：档案上写的东西没有被当成运行时的事实。

其一，10 个出厂档案里 4 个 `prompt=""`（`general-assistant` / `code-reviewer` / `researcher` / `ops-runner`）。空提示词不只是缺人设 —— `_build_subagent_prompt`（`nanobot/agent/subagent.py:1188`）遇到空 prompt 时连「你是代码评审」这句身份行都不拼，主回合同样（`nanobot/agent/loop.py:1514`，`turn_role_section = agent.prompt or None`）。选了这四个档案之一，模型感知到的唯一差异是工具变少了，档案名与描述只活在 UI 上。

其二，档案的 `modelId` 在子 Agent 两条路径上都不成立：普通 `spawn agent=<id>` 静默丢弃，团队 `execute_frozen` 必然失败。

成功标准：

- 选中任一出厂档案，子 Agent 的 system prompt 里出现该档案的身份与职责，且承诺与它实际拿到的工具集一致。
- 档案选了模型，子 Agent 真跑那个模型；跑不成就当次失败并说明原因，不再出现「界面显示 A、实际跑 B」。
- 用户自己建的档案即使一个字提示词没写，也拿到身份行。
- 执行面板能核对这次实际用的是哪个模型。

## 2. 现状取证（三条路径口径不同）

| 档案字段 | 主回合（会话选 Agent） | 子 Agent（`spawn agent=<id>`） | 团队成员 |
| --- | --- | --- | --- |
| 工具 `tools` | ✅ `loop.py:1243 _agent_registry` 按白名单重建 registry | ✅ `subagent.py:619` unregister 非白名单 | ✅ 另加 `execution.py:80` 空白名单拒绝起跑（A19） |
| 技能 `skills` | ✅ 三入口共用 exclude（`loop.py:1515`、`context.py:1302`） | ⚠️ 仅裁剪技能摘要（`subagent.py:1168`） | ⚠️ 同左 |
| 提示词 `prompt` | ✅ `loop.py:1514` | ✅ 非空才拼（`subagent.py:1188`） | ✅ |
| 模型 `modelId` | ✅ `loop.py:1190 resolve_preset`，不可解析时软回落 | ❌ 静默沿用父 runtime | ❌ 必然 `model_preset_unavailable` |

模型这一行的取证过程：全仓只有一个 `SubagentManager(...)` 构造点（`loop.py:514`），注释明示「只接 agent_profiles，不接 model_resolver」（`loop.py:525-526`）；`model_resolver` 在 `nanobot/` 内除自身定义与调用外**零注入点**。按同一装配实跑一次 `execute_frozen`，结果：

```
has model_resolver attr: None
>>> execute_frozen outcome : failed
>>> stop_reason            : invalid_profile
>>> error_code             : model_preset_unavailable
>>> content                : Error: 模型 preset 'sonnet-preset' 无法解析，请重新选择可用模型。
>>> _apply_model_override(None resolver) keeps runtime: parent-model
```

两处口径互相矛盾：`teams/runs.py:148 _require_presets` 在**建 run 时**已用 `host.runtime_resolver` 校验过该 preset 存在，于是「校验通过 → 执行必失败」。`tests/` 里 grep `model_preset_unavailable` 零命中，这条路径从未被覆盖。`2026-09-29-agents-runtime-integration-plan.md:1086` 把 resolver 划给「接线方按自己的环境决定」，而 `2026-10-15-agent-team-plan.md` 后补的 A17 要求 fail-closed —— 划界在先、要求在后，中间没人接线。

已核对且判定为**设计内**，本次不改：

- 档案 id 拼错 fail-closed 并回列可用 id，兜底空集（`subagent.py:513-572`）；解析失败在裁剪前短路，不会退化成全量工具。
- `extra_tools`（写作工具）绕过白名单：来源是可信运行上下文而非模型可控值（`teams/execution.py:64`）。
- `hidden` 是视图偏好，不参与解析（`store.py:254`）：隐藏档案仍可用显式 id 调用。本工具是单用户自托管，档案不是访问控制单元，改成拦截会把「视图偏好」这个既有承诺变成隐式权限语义。
- 技能 exclude 属提示级约束：子 Agent 模板本就教模型用 `read_file` 去读 SKILL.md（`templates/agent/subagent_system.md`），排除只让它不出现在摘要里。硬沙箱不在本范围。

## 3. 方案对比与选择

| 议题 | 候选 | 选择与理由 |
| --- | --- | --- |
| 出厂提示词覆盖面 | A 只补 4 个空的／B 补 4 个 + 校准已有 6 个 | **B（用户选定）**。但校准结论是「5 个创作团队角色各补一节交付，commander 不动」—— 见 §4.3，不为制造 diff 而改已合格的文本 |
| 空 prompt 的运行时行为 | A 保留跳过／B 空 prompt 也拼身份行 | **B（用户选定）**。身份行成本一行代码，收益是自建档案也有锚点；需要同步翻转 `tests/webui/test_subagent_agent_profile.py:101-107` |
| 老档案拿不到新默认 | A 靠 `reset_profile`／B 读时空字段回落 factory／C 启动迁移写盘 | **A（用户选定）**。`store.py:190` 已有「磁盘上已有的档案一律不覆盖」的承诺，B 会引入字段级回落这层新语义并牵动 `is_customized` 判定，C 动用户数据目录 |
| 模型覆盖接线口径 | A 两条路径统一 fail-closed／B 只补 resolver 让 spawn 静默生效／C 团队软回落对齐 spawn | **A**。B 留着「preset 失效 → 用父模型」的静默降级，正是 A17 要消灭的；C 让团队从显式失败退化成静默换模型。硬失败的成本是用户会突然看到 `model_preset_unavailable`，这是把隐性变显性 |

## 4. 设计

### 4.1 出厂子 Agent 提示词的写法标准

子 Agent 的读者是派活的主 Agent，不是人。四条约束：

1. **四段式**：`你是「X」，<一句话身份>` ／ `# 你要做`（3-4 条，动词开头、可判定）／ `# 回报格式`／ `# 边界`。沿用 commander 与创作团队角色已确立的 house style。
2. **`# 回报格式` 是新增的重点**。子 Agent 的产出会被回注进主 Agent 的上下文，回报结构直接决定主 Agent 能不能核对。统一要求：结论先行、依据带定位（`文件:行` 或真实 URL）、显式列出「没做到 / 没查到」，禁止用推测填空。
3. **不重复 base 模板已有内容**。`templates/agent/subagent_system.md` 与其 `untrusted_content.md` 片段已经承担工作区、历史日志、不可信数据这几条；出厂提示词只写该档案特有的约束，否则同一规则两处口径、改一处漏一处。
4. **边界分两类，只有一类是权威的**：工具白名单是硬边界（registry 裁剪，模型调不到）；提示词里的「不写文件 / 不执行命令」是软告知，作用是让模型少撞墙并如实上报能力受限。写提示词时不许把软告知当成沙箱 —— 因此每条承诺都要与该档案的 `tools` 选择核对。

### 4.2 四个空档案的提示词正文

**`general-assistant`** 工具仅 `read_file, list_dir`。同时修描述：现描述「能力对齐全局配置」与实际最小工具集矛盾，改为与实际一致（`catalog.py:82-86` 已注明默认窄工具集是刻意的最小权限起步，所以修描述不修工具）。

```
你是「通用助理」，接一件边界清楚的小事。你看不到派活方的对话，一切以本次任务说明为准。

# 你要做
1. 先判断任务真正要的结果，含糊处按最保守的理解做，并把用到的假设写进回报；
2. 只用读文件与列目录查证，不靠印象回答；
3. 做完就回报，不扩范围：任务没要求的改动一律不做。

# 回报格式
- 第一行给结论（是 / 否 / 是什么 / 够不够）；
- 依据逐条列，每条带出处（文件路径:行号）；
- 结尾写「没查到的」和「需要对方拍板的」，不用推测补位。

# 边界
- 你没有写文件、执行命令、联网检索的能力；需要时就明说受限并停手，不要绕路；
- 不臆造文件内容或行号，读不到就写读不到。
```

**`code-reviewer`** 工具 `read_file, list_dir, grep, find_files`。

```
你是「代码评审」，只读地审查交给你的改动，指出缺陷与风险。你不改代码。

# 你要做
1. 先定位被审对象与它的调用点，不看上下文不下结论；
2. 按「会出错的」排序而不是「看不惯的」：正确性 → 边界与错误处理 → 并发与资源 → 安全（注入、越权、凭据泄漏）→ 可读性；
3. 每条问题给三件事：位置（文件:行）、为什么会出错、最小改法的一句话描述，不写补丁代码；
4. 区分「缺陷」与「偏好」，后者单独列在末尾，不混进问题清单。

# 回报格式
- 首句总体结论：可以合入 / 有阻塞问题 / 证据不足；
- 问题按严重度排列，没发现问题的小节明写「未发现问题」，不留空；
- 单列「我没看到的部分」：哪些文件没读到、哪些行为无法静态确认。

# 边界
- 只读：不修改文件、不执行命令、不跑测试；只能靠运行验证的结论标注「需实测」；
- 不臆造行号与文件名，引用前先确认读到过；
- 不替作者决定功能取舍，需求层面的问题只指出、不拍板。
```

**`researcher`** 工具 `read_file, list_dir, web_search, web_fetch`。

```
你是「资料研究」，把一个待确认的问题查成有出处的结论。

# 你要做
1. 先把问题拆成可检索的子问题，逐条查证，不做一次性泛读；
2. 关键事实至少两个来源交叉验证；说法冲突时并列写出，不替读者选边；
3. 优先一手来源（官方文档、原始代码、发布说明），其次才是转述；
4. 记下检索时间 —— 版本、价格、限额这类信息会过期。

# 回报格式
- 第一行给结论；
- 「依据」逐条列：事实 + 出处链接 + 来源层级（官方 / 一手 / 社区转述）；
- 「已排除」列出见过但不可信的说法及其来源，免得对方重复踩；
- 「未解决」明确写「未找到」，不用推测补齐。

# 边界
- 只读与检索：不写文件、不执行命令；
- 链接与引文必须是你真实读到的，找不到证据就写没找到；
- 不越过被指派的问题替对方做结论。
```

**`ops-runner`** 工具 `read_file, list_dir, exec` —— 四个里唯一带高风险工具（`catalog.py:69-71` 把 `exec` 提级为 high），所以它的边界段最实质。

```
你是「运维执行」，在给定工作区里执行命令并如实回报。

# 你要做
1. 执行前先确认对象：用读文件与列目录核清路径与现状，命令只作用在核清过的东西上；
2. 一条命令一次执行，不把多个不相关操作串成一行 —— 出错时定位不到是哪一步；
3. 回报必带退出码与关键输出，失败时原样贴出错误，不概括成「执行失败」；
4. 命令改动了文件时，逐条列「改了哪些、从什么改成什么」。

# 回报格式
- 第一行：目标达成 / 部分达成 / 未达成；
- 「执行了什么」按顺序列出命令与参数；
- 「观察到什么」列退出码、关键输出、文件变更；
- 「建议下一步」只写有依据的，不给通用运维建议。

# 边界
- 只做被指派的那条命令。安装依赖、删数据、改配置、推远端这类有外部影响的动作，任务说明里明确要求才做；没要求就先回报「需要授权」并停手；
- 不可逆操作（删除、覆盖、重置）先说明影响范围，没授权就停住；
- 不读凭据与密钥，不为绕过权限限制改写命令；
- 白名单里没有写文件与派子 Agent 的入口，别试。
```

### 4.3 已有 6 个的校准结论

- **commander**：已符合标准（有「你要做 / 派活纪律 / 边界」，且它面向用户、回报格式即「先给结论再给依据」已写在第 3 条）。**本次不动**，避免无意义 diff 和 `is_customized` 漂移。
- **5 个创作团队角色**：结构与职责已合格，缺的是与主编汇总对接的交付结构。各补一节 `# 交付`（3 行内，规定该角色回执该带什么：研究员=出处+检索时间+存疑项，结构编辑=提纲+取舍理由+待拍板分歧，写手=改动小节与理由+待核实断言，审校=问题/定位/建议三件套，主编=派了什么、收了什么、缺口在哪）。其余文本、`tools`、`skills` 一律不动 —— `catalog.py:176-177` 已注明技能配套按 `logical_id` 认领，改一个字只会静默失效。
- 创作团队 5 个是 `protected` 内置档案（`writing/builtin.py:44`），内容源就是 catalog，改出厂值属允许范围；但 `WRITING_BUILTIN_VERSION`（`builtin.py:27`）参与内置登记表的键（`builtin.py:113/132/140`），**bump 等于新增一行登记而不是迁移旧行**。本次按「不 bump」处理：提示词是内容字段，不参与资产身份；实施时若发现必须 bump，停下来回到本 spec 复议。

### 4.4 空 prompt 也拼身份行

`subagent.py:_build_subagent_prompt` 改为 `agent is None` 才返回纯模板；非空时恒拼身份行，prompt 段仅在有内容时追加。`loop.py:1514` 主回合同样用 `name + description` 兜底，让 `context.py:244` 的 `role_section` 非空。身份行措辞与现状一致（有描述用「你是X，Y。」，无描述用「你是X。」）。

主回合不会出现「两段身份打架」：`context.py:243-247` 本就是 `identity.md`（Runtime/Workspace/Memory 这类事实段，不声明人格）之后紧跟 role_section，而 commander 已经在用这个位置写「你是「指挥官」，这个工作区的主 Agent…」。兜底只是把同一位置从「空」变成「一句身份」，走的是已验证过的路径。

改动会翻转两条既有断言（它们锁的正是我们要废除的行为）：`tests/webui/test_subagent_agent_profile.py:101-107`（空 prompt → 不拼身份）改为断言出现「你是通用助理」。

### 4.5 模型覆盖接通（统一 fail-closed）

1. `loop.py:514` 构造点补 `model_resolver`，把 `runtime_resolver.resolve_preset` 的 `KeyError/ValueError` 收敛成 `None`（沿用 `loop.py:1196` 已有的捕获口径），删掉 `loop.py:525-526` 那条现已失效的注释。
2. `subagent.py` 去掉 `require_model_preset` 参数与 `_apply_model_override`：档案指定了 `model_id` 就只有一条路 —— 解析出来用它，解析不出就 `_typed("failed", "invalid_profile", ..., error_code="model_preset_unavailable")`。两条路径共用同一段，`_execute_admitted` 不再有静默分支。
3. `teams/execution.py` 与 scheduler 不改：`request.runtime` 继续作为「档案未指定模型时的继承值」（`scheduler.py:565` 传的是全局 runtime）。修好后 Leader runtime（`loop.py:1184`）与成员 runtime 各按各的档案生效，这正是 A17 的原意。
4. 主回合的软回落（`loop.py:1190-1203`）**保留**，口径不对称是刻意的：主回合有人在现场、能立刻察觉回答不像那个模型，且硬失败会直接拦掉用户这一轮；子 Agent 的结果只回注给主 Agent，静默降级没人看得见。
5. 会被这个改动显性化的存量：凡是档案存了 `modelId` 而该 preset 已被删除，之前静默跑父模型 / 团队报 `model_preset_unavailable`，之后 spawn 也报同一个错。UI 侧已有对应认知（`AgentRailSummary.tsx:20` 的「原模型当前不可用」）。

### 4.6 执行可见性（可切出的一包）

`ExecutionSnapshot`（`subagent_executions/models.py:31`）没有模型字段，即便修好 §4.5 也核对不了「这次跑的哪个模型」。加 `model`（实际解析出的 `runtime.model`）与 `model_source`（`profile` / `session`，说明这个模型来自档案还是继承）两个字段。`executions` 表把整个快照存成 `payload` JSON（`repository.py:107-113`），**不需要 SQL 迁移**；改动面是 dataclass、`repository.create/start` 落字段、WS 投影，以及前端详情头一行。

这一包与提示词无依赖关系，实施中若要控范围可整包切出到下一批。

## 5. 兼容与数据影响

| 影响面 | 结论 |
| --- | --- |
| 已存盘的老档案 | 不迁移、不覆盖（`store.py:190`）。出厂 prompt 由 `""` 变有值后，这些档案会被 `is_customized`（`catalog.py:327`）判成「已定制」，点重置才拿到新默认（`store.reset_profile`）。这是用户选定口径，UI 上「已定制」标记属可接受的既有语义 |
| 空 prompt 语义变更 | 自建档案此前无身份行、此后有 → 子 Agent 与主回合的 system prompt 变长一行。无 API、无存储变更 |
| 模型 fail-closed | 存量档案里失效 preset 从「静默沿用父模型」变成「显式失败」。属把隐性变显性，需写进本批次变更说明 |
| 测试翻转 | `test_subagent_agent_profile.py:101-107`（身份行）、`:121-131`（两条 `model_resolver` 缺失时保持 runtime 的断言，锁的正是要废除的静默降级） |

## 6. 测试与验收口径

新增结构断言（参数化覆盖 10 个出厂档案）：

1. 出厂 prompt 非空、长度 ≤ `PROMPT_MAX_LENGTH`，且按组分节断言（不要求 10 个档案同构）：
   - 4 个新写的（`general-assistant` / `code-reviewer` / `researcher` / `ops-runner`）：含 `# 你要做`、`# 回报格式`、`# 边界`；
   - 5 个创作团队角色：含 `# 输入`、`# 你要做`、`# 交付`、`# 边界`；
   - commander：含 `# 你要做`、`# 派活纪律`、`# 边界`（本次文本不动，断言只锁结构不被后续批次误删）。
2. 每个出厂档案的 `tools` 选择与其 prompt 承诺不矛盾：断言 `code-reviewer` / `researcher` / 创作团队角色的白名单里不含 `write_file`、`edit_file`、`exec`；`ops-runner` 不含 `write_file`、`edit_file`；`general-assistant` 只有 `read_file, list_dir`。这条是「§4.1 第 4 点」的可执行化 —— 核对白名单，不去解析提示词文本。
3. 空 prompt 也拼身份行：出厂与自建档案各一条，主回合同步。
4. 装配级回归锁（本次缺陷的直接防复发）：用既有已装配 `AgentLoop` 夹具（参考 `tests/agent/tools/test_self_tool_runtime_sync.py:29` 的用法）断言 `loop.subagents._model_resolver is not None`。
5. 模型覆盖：spawn 路径 preset 可解析 → runtime 被替换；不可解析 → `error_code="model_preset_unavailable"`，且不再返回父 runtime。团队路径同两条断言。
6. 若做 §4.6：快照带 `model` 与 `model_source`，投影与前端测试各一条。

验收口径：出厂档案任选一个 spawn，回报文本里能看出它按该档案的回报格式在说话；给子 Agent 档案选一个与全局不同的模型，执行结果与面板显示的模型一致；把该 preset 删掉再跑，得到的是明确失败而不是悄悄换模型。

## 7. 范围外

- 技能硬沙箱（exclude 目前只到摘要级，见 §2 末条）。
- `hidden` 改成访问控制。
- 跨档案的能力继承、档案版本化与自动迁移。
- `extra_tools` 绕过白名单这条既有设计的调整。

## 8. 落点清单

| 包 | 文件 |
| --- | --- |
| A 提示词 | `nanobot/agents/catalog.py`（4 个新写 + 5 个补 `# 交付` + `general-assistant` 描述） |
| B 身份行 | `nanobot/agent/subagent.py:1188`、`nanobot/agent/loop.py:1514`、`tests/webui/test_subagent_agent_profile.py` |
| C 模型覆盖 | `nanobot/agent/loop.py:514`、`nanobot/agent/subagent.py:574-582 / 987-1001`、`tests/`（新增装配锁与两条路径断言） |
| D 可见性（可切出） | `nanobot/subagent_executions/{models,repository}.py`、`nanobot/webui/subagent_execution_routes.py`、`webui/src/features/subagent-execution/`、前端测试 |

## Next

**（已暂停，等确认 —— 见 `harness-kit/core/routing.md` § 阶段门禁）**

- 确认方案无误 → 说「写计划」或「制定实施计划」
- 认可但想省掉计划 → 说「直接实现」或「直接做」（A/B/C 三包合计约 5 个写文件，会走 Tier 1 直做 + verification-lite；若 D 也一起做则建议升级为编排）
- 要调整方案 → 直接说修改意见，特别是这几处需要拍板的：§4.2 四段正文的措辞、§4.3「commander 不动」的结论、§4.5 主回合保留软回落、§4.6 是否留在本批
