---
artifact: implementation-plan
route: writing-plans
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/api-and-interface-design/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-11-05-workbench-live-data-dispatch.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/specs/2026-11-05-workbench-live-data-spec.md
  - .ai-runtime-artifacts/contracts/2026-11-05-contract-workbench-live-data.md
  - .ai-runtime-artifacts/stack/2026-11-05-workbench-stack.md
created_at: 2026-11-05
status: needs_review
approved: false
tier: 2
---

# 工作台真实数据接入实施计划

> 执行者：用户单独确认本计划后，按项目 orchestration 流程逐任务执行；开始前加载 dispatcher-workflow、先建立隔离工作区，再派发有明确文件所有权的 worker。项目规则覆盖 writing-plans 默认的 worktree 时机、产物路径及执行技能选择。

**目标：** 交付以当前对话为中心的进展、记忆、能力工作台；所有状态、读写记录与技能归属连接真实运行数据。

**架构：** 统一项目/全局技能解析；以现有 RequestContext 和工具生命周期记录有界的轮次观测，随会话持久化。工作台 API 以兼容扩展提供观测、目录、分页与受控预览；前端使用即时会话事件与后端快照合并展示。

**技术栈：** Python ≥3.11 / asyncio；React 18.3.1、TypeScript 5.9.3、Vite 5.4.21（当前本地安装）；Tailwind 3；pytest、Vitest。不引入新的业务依赖。

## 执行前提

- spec 已由本轮“写计划然后执行”确认；该组合表达不跳过本计划写入后的项目门禁。
- 当前基线为 feature/desktop-app，计划前 HEAD 为120da2df；正式执行先重新检查 HEAD、脏文件与相关记忆改造是否变化。
- `memory/state.db` 有既有修改，且存在大量无关未跟踪产物。不得 stash、恢复、提交或复制为测试数据。
- 过程产物留在主 checkout 的 .ai-runtime-artifacts/；业务实现只在执行时创建的隔离 worktree 中进行。并行策略见同 stem dispatch。
- 先将本计划和契约纳入本地提交，再从该提交建执行工作区；不得创建尚未确认的业务工作区。

## 文件结构与所有权

| 单元 | 创建 | 修改 | 职责 |
| --- | --- | --- | --- |
| WU-01 | nanobot/agent/skill_catalog.py；tests/agent/test_scoped_skill_catalog.py | nanobot/agent/skills.py；tests/agent/test_skills_loader.py | 统一技能目录、覆盖与兼容加载 |
| WU-02 | nanobot/session/workbench_observations.py；nanobot/agent/hooks/workbench_activity.py；tests/agent/test_workbench_observations.py；tests/agent/test_workbench_activity_hook.py | nanobot/agent/tools/filesystem.py；nanobot/session/manager.py；tests/memory/test_memory_file_tools.py | 有界观测、文件成功边界、fork清理 |
| WU-03 | webui/src/components/workbench/workbench-live-types.ts；workbench-live-model.ts；WorkbenchMemoryPreview.tsx；workbench-memory.tsx；workbench-capabilities.tsx（均在同目录）；webui/src/tests/workbench-live-model.test.ts；workbench-live-api.test.ts；workbench-memory-preview.test.tsx（均在同测试目录） | SessionWorkbench.tsx、session-workbench-api.ts、session-workbench-model.ts、use-session-workbench.ts、workbench-progress.tsx、workbench-context.tsx、workbench-parts.tsx（均在workbench目录）；webui/src/tests/session-workbench.test.tsx、session-workbench-live.test.tsx、workbench-memories.test.tsx | 类型验证、事件合并、三页签、分页、受控预览 |
| WU-04 | tests/agent/test_workbench_runtime_integration.py | nanobot/agent/context.py；nanobot/agent/loop.py；nanobot/agent/subagent.py；nanobot/agent/turn_hooks.py；nanobot/memory/files.py；tests/memory/test_memory_files.py | recorder接线、实际注入清单、项目技能运行时接入、抽取归属 |
| WU-05 | nanobot/webui/workbench_resources.py；nanobot/webui/workbench_live_projection.py；tests/webui/test_workbench_resources.py；tests/webui/test_workbench_live_routes.py | nanobot/webui/session_workbench.py；nanobot/webui/ws_http.py；tests/webui/test_session_workbench.py | 服务端投影、HTTP分页与预览、鉴权与错误 |
| WU-06 | tests/webui/test_workbench_live_e2e.py；.ai-runtime-artifacts/verifications/2026-11-05-workbench-browser.cjs；同目录2026-11-05-workbench-gateway-fixture.py | 仅跨单元集成必需的修复，由Leader分配原所有者处理 | 真实文件/工具/网关/浏览器验收 |

每个创建文件的父目录在表中明确。禁止worker越过所有权改ThreadShell、公共协议包、翻译大文件或其他worker文件。界面当前已有useWorkbenchText，继续使用其中英文文案策略；需要组件拆分时仅在所属workbench目录内新增文件并回报。

## Task 1 / WU-01：统一技能根与同名覆盖

- [ ] 1. 在 test_scoped_skill_catalog.py 添加下面的失败用例；测试 helper 只在tmp_path生成真实技能，不读取用户技能。

```python
from pathlib import Path
from nanobot.agent.skills import SkillsLoader
from nanobot.agent.skill_catalog import resolve_skill_catalog

def install_skill(root: Path, name: str, body: str) -> Path:
    path = root / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path.resolve()

def test_runtime_and_catalog_use_same_project_override(tmp_path):
    agent, project = tmp_path / "agent", tmp_path / "project"
    global_path = install_skill(agent, "writer", "GLOBAL_MARKER")
    project_path = install_skill(project, "writer", "PROJECT_MARKER")
    catalog = resolve_skill_catalog(agent, project)
    candidates = [entry for entry in catalog.entries if entry.name == "writer"]
    winner = next(entry for entry in candidates if entry.effective)
    loser = next(entry for entry in candidates if not entry.effective)
    assert winner.path == project_path
    assert winner.origin == "workspace"
    assert loser.path == global_path
    assert loser.overridden_by == winner.id
    content = SkillsLoader(agent).for_project(project).load_skill("writer")
    assert content is not None and "PROJECT_MARKER" in content
    assert "GLOBAL_MARKER" not in content
```

- [ ] 2. 执行 `uv run --no-sync python -m pytest tests/agent/test_scoped_skill_catalog.py -q`，确认新模块缺失导致失败。
- [ ] 3. 实现契约中的不可变SkillEntry/SkillCatalog（entries为tuple）、resolve_skill_catalog、for_project、catalog。技能身份基于规范化真实路径，origin决定项目→全局→插件→内置优先级。按entry自身metadata判断描述和依赖，不能再次按名字读到其他来源。
- [ ] 4. 更新list_skills/load_skill/get_always_skills/load_skills_for_context/build_skills_summary/显式选择逻辑，全部走同一绑定目录。保持旧list_skills响应shape与source值兼容。覆盖条目保留，禁用/缺依赖不自动回落低优先级。
- [ ] 5. 添加并验证：同根去重、两个项目并发实例、禁用别名、缺CLI依赖、软链接越界与Windows大小写。执行 `uv run --no-sync python -m pytest tests/agent/test_scoped_skill_catalog.py tests/agent/test_skills_loader.py -q`，期望全部通过。
- [ ] 6. Worker提交文件清单、测试证据、契约偏差与风险给Leader；Leader复核后独立提交 `feat(skills): 统一项目与全局技能加载目录`。

## Task 2 / WU-02：真实事件与有界持久观测

- [ ] 1. 在test_workbench_observations.py添加失败用例：构造两个recorder，向一个加入相同call_id的start/end重复事件；断言另一个为空、成功终态不回退、按id幂等、超过200事件标truncated、超过20轮裁剪。事件对象按契约全部字段构造，不使用真实会话。
- [ ] 2. 执行 `uv run --no-sync python -m pytest tests/agent/test_workbench_observations.py -q`，期望缺模块失败。
- [ ] 3. 实现TurnObservationRecorder、稳定资源id编解码、事件投影。snapshot返回脱离内部可变状态的字典：events、truncated、turn_id、coverage；persist只修改传入session的观测键。record输入经过统一边界校验，不接收原始tool result正文。
- [ ] 4. 生命周期hook按原before/after/error接口实现；文件工具在路径真正解析成功且操作成功的位置记录元信息。使用current_tool_call_id与current_request_context定位recorder，失败不可误记成功。读取范围按offset/limit与实际总行数判断full/partial，无法证明完整则unknown。
- [ ] 5. 在文件工具访问策略内按当前RequestContext.workspace解析项目记忆根，允许全局与当前项目两处记忆根；每次解析受既有权限约束，不扩展为整个用户目录。普通文件行为及返回文本保持兼容。
- [ ] 6. 将workbench_observations加入session fork易变键排除集合。持久化时使用现有元数据更新链路，不用detached快照save覆盖会话历史；后台并发观测使用同一session的串行合并入口，测试两个追加均保留。
- [ ] 7. 添加真实read/write/edit成功与失败、MEMORY.md维护、同名跨项目、绝对/相对路径、fork/reload和取消用例。执行 `uv run --no-sync python -m pytest tests/agent/test_workbench_observations.py tests/agent/test_workbench_activity_hook.py tests/memory/test_memory_file_tools.py tests/tools/test_filesystem_tools.py tests/agent/test_session_manager_history.py -q`，期望通过。
- [ ] 8. Leader复核后提交 `feat(workbench): 记录真实工具与记忆文件事件`。

## Task 3 / WU-03：前端契约、即时状态与加载隔离

- [ ] 1. 创建workbench-live-types.ts，逐项翻译契约，不另起不同字段名。在workbench-live-api.test.ts添加旧响应可用、新字段错形报错、未知时间保持null用例。在workbench-live-model.test.ts添加“tool end先到，旧snapshot后到”的回退防护用例。
- [ ] 2. 执行（webui目录）`bun run test src/tests/workbench-live-model.test.ts src/tests/workbench-live-api.test.ts`，期望新增函数缺失失败。
- [ ] 3. 在workbench-live-model.ts集中实现纯事件合并，签名为 `mergeWorkbenchActivity(snapshot: WorkbenchActivity | null, events: Observation[], turnId: string | null): WorkbenchActivity | null`；同轮终态优于旧running，异轮不合并，按sequence排序；没有turn_id不能输出turn coverage。只从已有会话事件投影，不解析助手自然语言推测工具状态。

```ts
const terminal = new Set(["succeeded", "failed", "cancelled"]);
// 同一事件身份的合并核心，调用方先完成 turn/session 身份校验。
function mergeEvent(previous: Observation | undefined, incoming: Observation): Observation {
  if (!previous) return incoming;
  if (terminal.has(previous.status) && incoming.status === "running") return previous;
  return incoming.sequence >= previous.sequence ? incoming : previous;
}
```

- [ ] 4. session-workbench-model.ts从messages的既有turn_id/toolEvents派生即时活动；保留产出、来源和CLI计数兼容。扩展SessionWorkbenchModel的新增属性为可选，保持现有ThreadShell调用不变；无已知started_at不生成假计时。
- [ ] 5. session-workbench-api.ts增加契约端点fetch与严格可选扩展校验，错误解析兼容当前文本体；use-session-workbench订阅当前chat的start/end/error、answer/complete、连接状态，即时本地更新；HTTP刷新防抖200–350ms。不逐token刷新，也不新建第二套协议。
- [ ] 6. 切换session/project立即清空旧分页/预览/观测并取消旧请求；token变化同时失效。刷新同会话失败保留快照并标stale；重连与未知状态不保留确定运行的旧工具。页面隐藏与卸载清理按现有行为保留。
- [ ] 7. 执行（webui目录）`bun run test src/tests/workbench-live-model.test.ts src/tests/workbench-live-api.test.ts src/tests/session-workbench-live.test.tsx`，期望通过；测试覆盖请求竞态、无计划工具结束、断线、旧版本兼容与409分页重载。

## Task 4 / WU-03：三页签布局与受控预览

- [ ] 1. 在session-workbench.test.tsx和workbench-memories.test.tsx添加失败断言：同计划只出现一次、无子Agent不展示独立空块、已提供目录不能呈现为已读取全文、scope分组与真实计数准确。
- [ ] 2. 拆分workbench-memory.tsx与workbench-capabilities.tsx；workbench-context.tsx仅保留来源限制解释与兼容导出，避免该文件继续叠加职责。SessionWorkbench保留页签、键盘切换与宿主宽度控制。
- [ ] 3. 进展采用状态卡→唯一计划→协作→产出/来源布局；近期默认5条展开20条。计划比例仅取completed/steps，空计划不显示百分比；输出的file preview与执行详情回调继续可用。
- [ ] 4. 记忆采用已读/写入/目录提供三类动态，下面放workspace/global目录、画像与旧工作上下文。读写汇总按resource.id去重，失败事件单独标记；历史无数据显示未记录而非0次。
- [ ] 5. 能力按workspace/global/plugin/builtin分组；读取状态和可用性分开，显示覆盖关系；只计真正effective+available技能。CLI/MCP延续现有真实统计，不填未知成功率。
- [ ] 6. WorkbenchMemoryPreview调用受控资源端点，在工作台内提供可关闭的只读预览面板；不将记忆路径传到通用任意文件预览。加载、无权限、已删除、16KiB截断均有清楚文本。分页More按钮使用group.next_cursor，409重置对应组首页。
- [ ] 7. 添加workbench-memory-preview.test.tsx：验证global笔记响应可显示、403/404可读、切会话关闭旧预览、旧请求无法覆盖新内容。继续用useWorkbenchText中英文策略，遵守减少动效、焦点管理与280px宽度。
- [ ] 8. 执行（webui目录）`bun run test src/tests/session-workbench.test.tsx src/tests/session-workbench-live.test.tsx src/tests/workbench-memories.test.tsx src/tests/workbench-memory-preview.test.tsx src/tests/workbench-live-model.test.ts src/tests/workbench-live-api.test.ts` 和 `bun run build`，期望通过；Leader提交 `feat(workbench): 接入实时状态与记忆技能分组界面`。

## Task 5 / WU-04：把观测与技能接入真正运行路径

- [ ] 1. 添加test_workbench_runtime_integration.py，用真实SessionManager、ContextBuilder、文件工具与临时目录，测试目录manifest和实际prompt一致，索引截断后不宣称所有条目已注入。
- [ ] 2. 执行 `uv run --no-sync python -m pytest tests/agent/test_workbench_runtime_integration.py -q`，期望尚无观测接线而失败。
- [ ] 3. memory/files.py增加 `render_index_block_with_manifest(store) -> tuple[str, list[dict[str, object]]]`；原render_index_block委托并只返回text。manifest直接从最终保留的行生成，不二次扫描目录猜测。分离真实目录路径（模型使用）与HTTP资源身份（UI使用）。
- [ ] 4. context.py按当前workspace调用self.skills.for_project(root)，以该实例加载常驻、显式与摘要；实际上下文组装成功后向recorder提交索引/画像/常驻技能清单。include_memory/file_mode关闭时按真实路径处理，不记录不存在的注入。
- [ ] 5. loop.py在真实turn_id、session_key确定后创建recorder，放入该轮RequestContext.attributes，并把同一recorder传给turn hook factory。turn_hooks.py注册WorkbenchActivityHook，不影响已有进展/file edit hook；短暂执行或无session上下文不写持久会话。
- [ ] 6. subagent.py的技能解析使用它实际绑定的项目scope，不通过变更共享loader切换。嵌套工具事件仅在明确parent/session身份下关联，独立子Agent不污染主轮计数。
- [ ] 7. loop.py已有_memory_file_writer在抽取成功后按写入文件生成source=extraction事件；有可信turn_id才coverage=turn，否则coverage=session。使用SessionManager.update_session_metadata的既有原子更新路径，先合并最新观测、不保存旧快照覆盖history；发出既有session update通知，不改变抽取周期或Dream。
- [ ] 8. 测试真实读取、写入、刷新后恢复；两个项目并发不混技能或笔记；tool结束到answer阶段状态正确；抽取无turn_id不计本轮。执行 `uv run --no-sync python -m pytest tests/agent/test_workbench_runtime_integration.py tests/agent/test_context_builder.py tests/agent/test_loop_runner_integration.py tests/agent/test_loop_concurrency.py tests/agent/test_subagent.py tests/memory/test_memory_files.py tests/memory/test_scheduled_extraction.py -q`，期望通过。
- [ ] 9. Leader复核后提交 `feat(workbench): 接通上下文注入与会话观测链路`。

## Task 6 / WU-05：服务端投影、分页与资源预览

- [ ] 1. test_workbench_live_routes.py按现有test_session_workbench.py的HTTP测试夹具增加无认证401、未知会话404、workspace可见范围、旧字段保留、GET无磁盘写入断言；test_workbench_resources.py添加跨项目同名笔记、畸形/越界id、分页409与16KiB预览用例。
- [ ] 2. 执行 `uv run --no-sync python -m pytest tests/webui/test_workbench_live_routes.py tests/webui/test_workbench_resources.py -q`，期望路由或字段缺失失败。
- [ ] 3. workbench_resources.py负责session作用域资源查询与预览；只对固定memory根读当前分页，索引列表使用有界frontmatter读取，禁止列表页加载全部笔记正文。按name排序，逐组total准确，revision含文件身份/stat，不重建MEMORY.md。
- [ ] 4. workbench_live_projection.py负责把recorder历史、MemoryFileStore摘要、统一SkillCatalog映射到契约；缺模块用limitation，未知time/null，旧历史仅恢复明确成功的调用。成功读取技能必须精确匹配真实路径，不能只按父目录名字。
- [ ] 5. session_workbench.py保留原字段，扩展新投影；ws_http.py的workbench路由从session metadata解析项目，用SkillsLoader.for_project保持runtime一致。将新分页/预览路由放在精确匹配位置，GET与鉴权统一封装，文件IO使用asyncio.to_thread。
- [ ] 6. 错误状态严格按契约文本体返回；无项目不误读其他项目，global资源在项目外也可受控读取。symlink真实路径必须留在允许根，客户端不得用path参数扩大范围。GET不创建session、目录、索引，响应no-store。
- [ ] 7. 添加资源项目绑定、分页修订、禁用/缺依赖技能、源码敏感信息不泄露和部分服务失败测试。执行 `uv run --no-sync python -m pytest tests/webui/test_session_workbench.py tests/webui/test_workbench_live_routes.py tests/webui/test_workbench_resources.py tests/webui/test_skills_api.py tests/webui/test_file_preview.py -q`，期望通过。
- [ ] 8. Leader复核后提交 `feat(workbench): 提供真实观测目录与安全预览接口`。

## Task 7 / WU-06：真实数据集成与浏览器检查

- [ ] 1. 创建test_workbench_live_e2e.py：使用真实临时笔记/技能文件、SessionManager、工具执行及HTTP handler，控制性推进start/read/write/end事件，不把snapshot替换成固定mock成功响应。
- [ ] 2. 测试路径为：建全局+A/B项目资源→A上下文构建→读取全局笔记→新增A笔记→读取项目同名技能→保存/重载session→鉴权GET→分页/预览。断言A/B隔离、读写证据、实际加载内容与能力一致、GET无副作用。
- [ ] 3. 执行 `uv run --no-sync python -m pytest tests/webui/test_workbench_live_e2e.py -q`，期望完整闭环通过；此项可以控制provider返回，不代表已做真实模型验收。
- [ ] 4. verifications下创建gateway-fixture与browser脚本，所有临时数据留在该目录对应子目录；启动真实本地gateway和当前WebUI。fixture只能生成测试会话与真实文件、通过现有工具与hook推动状态，不得向浏览器拦截伪造工作台接口。
- [ ] 5. 浏览器记录进展start/end/失败/回复完成，记忆三分组与global预览、技能分组与override，页面刷新后恢复、切项目不串、断线过期标记。检查rail 280/304/420px与移动端面板，截图存verifications/2026-11-05-workbench-browser/。
- [ ] 6. 页面检查使用当前可用的真实浏览器连接；若内嵌浏览器超时改用项目已有浏览器测试方式。浏览器连接始终不可用时，报告为未完成的验收，不以静态HTML或截图猜测通过。

## Task 8 / Leader：尾盘与交付

- [ ] 1. 运行所有WU列出的聚焦测试合集一次；无新失败且无新代码改动时不无意义重复。失败由原文件所有者修复并重跑受影响检查。
- [ ] 2. 执行 `uv run --no-sync ruff check nanobot/`、`uv run --no-sync basedpyright`。WebUI目录执行 `bun run test`、`bun run build`、`bun run lint`。验证真实gateway可以启动；本次不自动升级依赖。
- [ ] 3. 记录基线失败的同命令输出与受影响范围；禁止用skip、放宽类型/断言或排除失败用例获得全绿。完整Python全量回归若未运行必须在报告说明，不把聚焦测试称为全回归。
- [ ] 4. 并行安排只读code-review、安全审查与按需性能审查。重点复核资源路径/项目隔离、同名技能运行时一致、事件持久化竞争、分页避免全量正文读取、WebSocket与HTTP竞态。
- [ ] 5. Leader落盘collective-test、code-review、security-review与execution-log，包含实际命令/结果、浏览器证据和真实模型验证边界。完成问题修复后才标全部完成。
- [ ] 6. 本地提交分组清晰；用户未要求push/PR，不自动推送。最终交付列出真实数据闭环、测试结果、限制与变更位置。

## 需求覆盖自检

| spec要求 | 负责任务 |
| --- | --- |
| 当前任务、计划去重、真实运行态、无计划也可用 | Task2/3/4/5 |
| 目录提供与完整/分片读取分开、真实写入、未知历史 | Task2/4/5/6 |
| 全局/项目文件库、画像、分页、受限预览 | Task4/5/6 |
| 项目/全局/插件/内置、覆盖规则、真实加载一致 | Task1/4/5/6 |
| 200事件×20轮、幂等、fork/reload、后台来源 | Task2/5 |
| 实时事件、旧快照竞争、断线与会话切换 | Task3/7 |
| 三页签、窄栏、减少动态、键盘与焦点 | Task4/7 |
| 旧API兼容、纯GET、认证/越界/错形/局部失败 | Task3/6/7 |
| 真实文件/网关/浏览器、非mock验收与验证边界 | Task7/8 |

自检结果：接口名称与字段以contract为准；依赖顺序见dispatch；不存在跨并行WU的文件重叠。真实模型测试不是默认要求，确定性集成测试与真实浏览器必须完成。主checkout的既有memory/state.db始终不在文件清单中。

## Next

计划与契约已准备审阅。按仓库组合指令门禁，本轮不创建业务worktree、不派发实现、不改业务代码。用户单独回复“开始实现”或“执行”后，按dispatch开展隔离并行实现；无需重复选择布局或再次确认真实数据范围。
