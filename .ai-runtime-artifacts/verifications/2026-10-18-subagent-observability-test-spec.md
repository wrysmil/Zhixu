---
artifact: verification
route: brainstorming -> verification-before-completion
skills: [brainstorming, verification-before-completion]
skills_evidence:
  - /Users/mima0000/.agents/skills/brainstorming/SKILL.md
  - /Users/mima0000/.agents/skills/verification-before-completion/SKILL.md
source:
  - .ai-runtime-artifacts/specs/2026-10-18-subagent-observability-spec.md
  - .ai-runtime-artifacts/contracts/2026-10-18-contract-subagent-observability.md
  - harness-kit/project.verification.md
  - webui/vite.config.ts
created_at: 2026-10-18
status: baseline-verified-feature-pending
---

# 子 Agent 可见性：前端测试与通过条件

## 1. 验收分层

本轮完成相关旧测试基线及设计；新功能尚未写入业务代码。后续所有新增功能条目当前均为**待实现验收**，不能用旧测试或设计预览替代。

- 解析器/reducer 单测：固定 wire fixtures，验证状态与 seq。
- React 集成测试：真实 parser → reducer → hook → 聊天入口和详情 DOM；只替代网络，不 mock 掉被验收的状态链。
- 浏览器验收：真实布局、键盘焦点、移动端、滚动、颜色主题；happy-dom 不承担这部分结论。
- gateway 端到端：真实 spawn 两模式＋WS＋HTTP 恢复。使用本地确定性 provider/慢工具 fixture，不依赖真实金价或付费模型；至少一次真实浏览器→gateway→Runner 通路，不能只有前端注入假事件。

## 2. P0 用例：全部必须通过

| ID | 操作／输入 | 可判定的通过条件 |
| --- | --- | --- |
| P0-01 | 后台 spawn 派发后工具立即返回 | 显示子任务 queued/running，不能因父工具 done 显示 succeeded |
| P0-02 | wait=true 的慢子任务 | 主 Agent 等待期间，页面仍收到阶段和工具活动；入口可点击 |
| P0-03 | 点击、关闭、再打开详情 | 打开对应 invocation；关闭不发 mutation/取消；重开状态和活动连续 |
| P0-04 | 同一档案同名并发3次 | 3个独立入口；结果和时间线按 ID 对应，乱序完成不串线 |
| P0-05 | 一个工具开始后运行5秒再结束 | 开始即显示“运行中”，结束更新同一行；完成计数只增加一次 |
| P0-06 | 工具先失败，模型换办法后完成 | 单工具显示失败，整个任务仍可 succeeded；不提前终止 |
| P0-07 | 模型不可用、超时、max_iterations、空答案 | 均有真实失败状态和可读原因；不出现成功兜底；原始堆栈/密钥不显示 |
| P0-08 | 用户停止整轮，含排队和运行任务 | 服务端取消后入口/详情均 cancelled；running工具收口；前端不提前伪造终态 |
| P0-09 | 任务 running 时刷新页面 | 从持久快照恢复同一 invocation；补回刷新前活动；不产生第二次 spawn |
| P0-10 | 断线期间任务完成后重连 | 先标待同步，恢复真实终态和结果；恢复后移除断线标记 |
| P0-11 | seq 依次1,3,3,2,4 | 缓存/补拉并去重；最终4条事件逻辑各应用一次，无倒退/重复工具行 |
| P0-12 | succeeded 后迟到running活动/旧快照 | 终态和固定耗时不倒退，结果不消失；时间线允许补齐缺失历史 |
| P0-13 | 列表请求未返回时先收到finished | 晚到列表不覆盖新终态，订阅优先策略无竞态 |
| P0-14 | seq断号＋分页＋resetRequired | 单一补拉链；页读完再续；过期时显示历史缺失提示并收敛，不无限重试 |
| P0-15 | A聊天发请求后切换B，A响应晚到 | B没有A任务、A工具活动或错误；选中项/返回栈清空，旧订阅解除 |
| P0-16 | workspace不匹配、未认证、猜其他调用ID | 客户端不呈现其他作用域内容；服务端401/403/404按现有规则拒绝 |
| P0-17 | 在详情中打开文件再返回 | 同时只有一个右侧详情面板；返回原任务和滚动位置；输入草稿保持 |
| P0-18 | 360/768/1024/1440px与200%缩放 | 无页面水平溢出、按钮遮挡；主聊天宽度不足时切抽屉，关闭可见 |
| P0-19 | 桌面用键盘打开/操作详情 | Enter/Space可打开，面板有可访问名称；聊天可继续操作；更新不抢焦点 |
| P0-20 | 窄屏抽屉完整键盘路径 | 焦点进入抽屉，Tab不离开，Escape关闭，焦点回到原入口；背景不可操作 |
| P0-21 | 状态刷新、秒表更新、活动连发 | 关键状态polite播报；秒表和日志每条不反复播报；无闪烁/假百分比 |
| P0-22 | gateway重启＋旧queued/running记录 | interrupted且有原因；工具活动一并收口，不永久旋转、不自动重跑 |
| P0-23 | 旧gateway/旧历史/未知schema | 普通聊天正常，无新接口请求风暴；旧调用无伪关联、无不可用详情入口 |
| P0-24 | 任务中含私有提示、带token的URL、shell凭据 | wire fixtures与DOM均无敏感哨兵文本；公开摘要来自白名单字段 |
| P0-25 | 临时聊天运行任务后离开 | 磁盘无本次ledger/事件/结果；不误承诺历史恢复 |
| P0-26 | 观察库不可用／无法取得writer锁 | 明确“执行详情不可用”；任务按既有执行语义处理，界面不伪报失败或成功 |
| P0-27 | Agent无工具或档案运行后被删除 | 无工具时仍显示阶段/结果；历史名称快照可读，不依赖档案还在 |
| P0-28 | 超过20条历史，最早任务仍运行 | 活动任务全部可见，历史可分页；首屏limit不误截活动项 |
| P0-29 | 子任务菜单点击／键盘／单任务 | 两行身份与状态可读，当前项有勾；方向键/Home/End导航、Enter/Space选择；Escape先关菜单、焦点回触发器，详情不误关闭；点击外部可关闭；单任务不显示切换器 |

## 3. P1 与性能预算

| ID | 验收条件 |
| --- | --- |
| P1-01 | 中文／英文、浅色／深色状态均可读，文本不依赖颜色；reduced-motion无持续装饰动画 |
| P1-02 | 用服务器时间偏移＋假计时验证排队/运行/终态计时：不为负，终态推进60秒仍不增长 |
| P1-03 | 8个并发调用、每个500条活动fixture，浏览器收到事件至DOM可见的本地p95≤250ms；点击打开p95≤200ms（详情数据已在本地） |
| P1-04 | 本地确定性gateway fixture：服务端提交关键事件至DOM可见≤1秒，不以真实LLM耗时作为此指标 |
| P1-05 | 用户向上读时间线时不拉回底部；底部自动跟随；“有新活动”能回到底部；主聊天不被抢滚动 |
| P1-06 | 重复挂载/卸载10次后无残留订阅、计时器、重复请求；切换状态不丢草稿 |
| P1-07 | 长任务标题、长错误、超长结果和过期记录都有可读回退；结果截断提示正确 |

上述为设计预算，尚未实测；浏览器记录设备、版本、fixture规模和测量方法。若P1不达标，必须记录影响及明确豁免，不可直接写“全部通过”。

## 4. 测试组织与执行

建议新增测试文件（未创建）：subagent-execution-events、subagent-execution-reducer、subagent-execution-flow、subagent-execution-panel、right-surface。复用现有 team-flow 的 wire fixture方法，但不能只测文案或复制实现逻辑。

计时测试使用Vitest2假计时，user-event配置advanceTimers；用act包裹状态推进；清理pending timers后恢复真实计时。请求竞态使用deferred promise，不增加任意sleep以碰运气。浏览器必须补测happy-dom证明不了的布局、焦点、滚动。

标准命令：`cd webui && bun run test`、`bun run lint`、`bun run build`。环境没有Bun时用当前安装的Node入口等效执行，不临时下载依赖。涉及后端后，补目标pytest＋ruff＋basedpyright，并做gateway启动与端到端冒烟。

合入条件：P0 29项全部通过；P1通过或有具体豁免；新增目标用例、现有相关回归、前端全量测试/类型检查/lint/build通过；Python接口与runtime相关检查通过；控制台无新增error，无新增未处理promise拒绝；存档截图和真实帧证据。截图包含桌面运行、完成、失败、窄屏、刷新恢复。

覆盖率沿用项目已有阈值statements/lines85%、branches/functions80%；不为本功能下调阈值，也不把总覆盖率当作关键场景通过的替代物。

## 5. 本轮实际基线结果

在当前安装环境执行：

```text
工作目录：/Users/mima0000/Documents/学习-001/do-project/zhixu/webui
node node_modules/vitest/vitest.mjs run src/tests/team-events.test.ts src/tests/team-reducer.test.ts src/tests/team-flow.test.tsx src/tests/team-progress.test.tsx src/tests/file-preview-panel.test.tsx src/tests/thread-shell.test.tsx src/tests/generic-tool-model.test.ts src/tests/agent-activity-cluster.test.tsx
退出码：0
Test Files  8 passed (8)
Tests       204 passed (204)
Duration    4.51s

node node_modules/typescript/bin/tsc -p tsconfig.build.json --noEmit
退出码：0；无诊断输出
```

现有警告：Browserslist数据过期、KaTeX quirks mode、ThreadShell若干act包装警告。以上是本轮读到的基线输出；没有以更新依赖或无关改动清掉它们。

本轮未跑全量lint/build/Python测试：未改业务代码，build会写现有bundled dist；当前验证目标是设计和相关测试基线。新增功能P0/P1及真实gateway端到端仍为待实现验收。

## 6. 设计预览验证

设计预览的7项交互回归已通过，并做了实际浏览器菜单、键盘、焦点与关闭检查。详见 `2026-10-18-subagent-observability-preview-verification.md` 与截图目录 `2026-10-18-subagent-observability-preview/`；这些结果仅证明设计交互，不证明产品功能已接通。
