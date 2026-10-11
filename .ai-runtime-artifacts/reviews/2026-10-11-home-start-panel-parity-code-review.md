---
artifact: code-review
route: requesting-code-review
skills: [requesting-code-review, code-review-and-quality]
source:
  - reviewer agent「批次代码审查」（只读，base 5f8195f，worktree 全量未提交 diff）
created_at: 2026-10-11
verdict: BLOCK（4 Major / 9 Minor / 2 Nit；无 Blocker）
leader_ruling: 4 项 Major 全部接受并进入 review-fix；Minor 按「必修/记录」分档见文末处置表
---

# 首页开始面板 +「+」菜单批次 · 代码审查报告

批次整体质量高：契约实现忠实、后端测试确实在「锁住契约行为」、前端懒加载与 store 设计合理。存在 4 项 Important 级缺口（其中 2 项直接违反验收口径 §9.1/§9.5），一轮 review-fix 后可放行。

## Major（继续前修复）

1. **关闭终端 tab 后幽灵 tab 复活竞态** — `webui/src/hooks/useNanobotStream.ts:1637-1653` + `TerminalSection.tsx:263-267`：`closeTerminal` 先发 `terminal_kill` 再 `store.remove(id)`，但后端 `_finalize_exit` 先补发残留 `terminal_output` 再 `terminal_exit`；`applyEvent` 对未知 id 的 output 走「tolerate unknown terminal」upsert，刚关闭的 tab 以 running/exited 重新出现。**修复**：store 维护 removed 墓碑集合，`terminal_output/exit` 命中墓碑则丢弃并清除。
2. **首页文件预览链路缺失** — `HomeStartPanel.tsx:178` + `lib/api.ts`：契约 §2 末条与 plan Task C Step 3 要求 preview `scope` 参数、首页点击文件走 `file-preview?scope=default`（后端已实现并被 `test_webui_workspace_files.py:169-213` 锁住）；前端 `WorkspaceFileTree` 未传 `onOpenFilePreview`，条目全部 disabled。**修复**：api.ts 预览请求透传 scope + HomeStartPanel 接入预览。
3. **8 个终端文案键未写入任何 locale** — `startPanel.{terminalInput,terminalInputPlaceholder,terminalTabs,terminalTab,terminalClose,terminalStarting,terminalExit,terminalExpired}` 仅 `defaultValue` 英文兜底，10 语言 JSON 均缺键，违反 spec §9.5。plan §5 键表本身漏列（计划盲区）。**修复**：按 TerminalSection/BrowserSection 实际消费键补齐 10 locale。
4. **⌘P/⌘T 在活动会话中被吞掉但无效果** — `App.tsx:1933-1937` + `HomeStartPanel.tsx:35`：`resolveStartPanelShortcut(event, view==="chat")` 在整个 chat 视图 preventDefault，而面板仅 `activeKey===null` 挂载；spec §4「仅桌面与全屏态生效」两处未实现。**修复**：keymap 判定加 `activeKey===null`，Web 态收敛。

## Minor（处置：FIX 轮必修=a / 记录后续=b）

| # | 位置 | 内容 | 处置 |
| --- | --- | --- | --- |
| M1 | TerminalSection.tsx:12-13 | 注释引用不存在的 `MAX_TERMINALS_PER_CHAT` | b（注释修正随 FIX-1 顺带） |
| M2 | ComposerPlusMenu.tsx:96/119/182 | `planActive` prop 无供给方，plan 行状态点永不可见（spec §7 半实现） | b（记入契约限制，v1.1 接线） |
| M3 | useNanobotStream.ts:1660-1670 | running 终端的 error 帧被静默丢弃 | a（并入 FIX-1） |
| M4 | TerminalSection.tsx:183-185 | `failureLabel` 比较 token 与 store 实际 message 不匹配，分支永不命中 | a（并入 FIX-1） |
| M5 | ws_http.py:941-955 | `?scope=`（空串）被 `or` 兜底当作 default，与「未知 scope→404」语义有出入 | a（并入 FIX-3，显式注释） |
| M6 | builtin.py:1043 | `/feedback` 空参用法提示中英混排 | b |
| M7 | spec §3/契约 §4 | 浏览器白名单「前后端各一份」后端镜像缺失（当前后端无消费 URL 端点） | b（security 已定级并入其发现） |
| M8 | App.tsx:2749-2886 | 包裹 div 后 PaneWorkbench 块未重排缩进 | a（随 FIX-2 App.tsx 改动顺带） |
| M9 | spec §2.2 | 「点卡片即建首个终端+下拉切换」未实现（现展开后按 + ） | b（spec 偏离记录，非必须） |

## Nit

- browser-url.test.ts:101 拼写 `%js`→`%s`；inbound_commands.py:578 `_validate_terminal_id` 的 `connection` 形参未使用。

## 契约一致性核对（通过项）

- 4 入站帧/4 出站事件与契约 §1.1/§1.2 逐字对齐；`event`/`type` 双键 encode 层统一注入；chat 管线旁路正确。
- `terminal_id` 正则、64_000 write 上限双端镜像一致；`limit_reached/already_running/error`、restricted→stderr+`exit_code:-1`、idle→expired+`exit_code:null` 均按契约实现且有测试。
- workspace files 端点、`resolve_workspace_anchor`、404/400 映射一致；`/feedback` jsonl+metadata+palette 一致。
- YAGNI：spec §8 不做清单逐项未越界；「+」菜单既有机会行为保持且测试断言行为等价。
- 测试锁约质量抽查高（精确字段集、seq 单调、跨 chat 归属、行为断言非快照）。

## 已知遗留项影响验证

- `terminal_list_result` 跨 chat 合并：确认影响，home 面板无 sessionKey 时多开 tab 混排；与契约 v1 声明一致。
- `window.open` 无 noopener（BrowserSection.tsx:22）：移交 security 定级（已并入其报告）。
- 基线 flake/失败：本批新代码无叠加影响；真实浏览器端到端未做。

## 五轴摘要

| 轴 | 状态 | 关键问题 |
| --- | --- | --- |
| 正确性 | ⚠️ | 幽灵 tab(#1)、首页预览缺口(#2)、快捷键吞键(#4) |
| 可读性 | ✅ | App.tsx 缩进、注释漂移 |
| 架构 | ✅ | useNanobotStream.ts 增至 1720 行，建议后续拆分终端 store |
| 安全（概览） | ⚠️ | 移交 security-auditor |
| 性能（概览） | ✅ | lazy chunk、8KB 分片、store 裁剪 |

---

## 复审结果（RE-REVIEW，2026-10-11 尾盘第二轮）

FIX-1/FIX-2/FIX-3 交付后定向复审 **verdict: APPROVE**：4 项 Major 全部确认已修复（墓碑集合对偶 _finalize_exit 时序、scope 预览链路含后端自洽性、10 locale 34+ 消费键全齐且插值一致、⌘P/⌘T 收紧 activeKey===null 且有行为测试）。Leader 顺带落地复审 Minor：`terminal_list_result` 补查墓碑（+测试）、DEFAULT_MAX_TERMINALS 注释形态修正。

遗留记录（不阻塞）：
- spec §4「快捷键仅桌面/全屏态生效」Web 态未收敛——按 Leader 裁决范围仅做 activeKey 收紧，偏离决策留档避免下批重提。
- Minor M2（planActive 无供给方）/M6/M9 维持 b 档；Nit「terminal_limit_reached 死分支」留后续简化。

## 处置汇总

review-fix 轮全量复跑：webui 99 files / 1391 tests exit0、lint 0、build 0；后端目标 49 passed、回归子集 1477 passed（4 例 test_serve_* 基线既有）、改动文件 ruff 0。批次按 4 个逻辑 commit 提交至 harness/wt-home-start-panel（e1ae304 / f1ac5ec / 60dbe98 / a9eb0e1），未 push。
