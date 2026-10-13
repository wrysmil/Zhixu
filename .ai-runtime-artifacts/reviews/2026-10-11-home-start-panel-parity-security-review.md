---
artifact: security-review
route: security-and-hardening
skills: [security-and-hardening]
source:
  - security-auditor agent「批次安全审查+注入溯源」（只读，base 5f8195f，worktree 全量未提交 diff）
created_at: 2026-10-11
verdict: BLOCK（0 Critical / 1 High / 3 Medium / 5 Low / 3 Info）
leader_ruling: HIGH 与 sandbox 相关 2 项 MEDIUM 必修后复审；输出配额与 LOW 项按处置表分档
---

# 首页开始面板 +「+」菜单批次 · 安全审查报告

## [HIGH] iframe sandbox 组合 + 白名单仅提交时校验 → 「重定向/DNS rebinding→同源→父页 token→终端帧→任意命令执行」放大链

- **位置**：`webui/src/components/workbench/BrowserSection.tsx:187`（`sandbox="allow-scripts allow-same-origin allow-forms allow-popups"`）+ `webui/src/lib/browser-url.ts:44,60`（loopback 放行）
- **说明**：`allow-scripts` 与 `allow-same-origin` 同开时，iframe 内文档一旦与父页同源，sandbox 形同虚设。白名单只在点击 Go 时校验一次：公网 302 重定向、DNS rebinding 均可把 iframe 送进网关自身 origin；若该 origin 下存在可承载攻击者脚本的路径（如 media 预览），iframe 脚本可读父页 token/sessionStorage 并直接发 `terminal_start/terminal_write` 帧——本批终端通道随即成为 RCE 通道。
- **修复（FIX-1）**：移除 `allow-same-origin`；`window.open` 加 `noopener`。已知代价：loopback 开发站内需要同源存储的页面在 iframe 中功能降级——用户驱动、可接受；桌面壳走 `mira:open-external` 不受影响。
- **缓解记录（b 档后续）**：确认 media/预览响应 `Content-Disposition`/`frame-ancestors` 收敛，逐跳重定向校验需宿主端 URL 代理，超 v1 范围，记入 v1.1。

## [MEDIUM] restricted guard 逐行独立校验，shell 续行/引号拼接可绕过（违反契约 §1.3）

- **位置**：`nanobot/webui/terminal_bridge.py:291-309`（`write` 按 `\n` 切行）→ `381-393`（`_submit_line` 仅对单行调 `_guard_command`）
- **说明**：交互 shell 会把未闭合引号/`\` 续行/多行结构拼为一条命令执行，deny 正则与路径逃逸检查逐行独立跑可被拆行规避。
- **修复（FIX-3）**：restricted 会话检测引号不闭合/续行符时缓冲续行，对最终完整命令串整段 guard，fail closed。

## [MEDIUM] sandbox 包裹失败 fail-open

- **位置**：`terminal_bridge.py:240-241`（`except Exception` 后以裸命令启动）
- **修复（FIX-3)**：wrap 失败返回 `terminal_started status=error`，fail closed。

## [MEDIUM→Low] 输出事件流无速率上限；断线后 shell 存活 1800s 可被同 chat 连接接管

- **位置**：`terminal_bridge.py:419-444`；归属校验复用 attach 集（单用户模型可接受）
- **处置**：字节/帧配额 a 档由 FIX-3 以最小实现落地（每终端每秒排空预算，超限合批延迟，不丢数据）；若实测复杂度超预期则降级 b 档记录（Leader 按返回甄别）。「断线存活 1800s、重连接管」语义补入契约文档。

## [LOW] 处置表

| # | 位置 | 内容 | 处置 |
| --- | --- | --- | --- |
| L1 | builtin.py:1043-1061 | `/feedback` jsonl 无单条长度/条目上限与轮转 | a（FIX-3：单条 4000 字符上限；轮转 b 档） |
| L2 | webui/package-lock.json:3245 | `@xterm/xterm` 唯一一条 resolved 走 npmmirror 镜像（integrity 与官方一致，无夹带、无钩子、无依赖） | a（Leader 复审通过后改回 registry.npmjs.org 并验证 lock 解析） |
| L3 | inbound_commands.py | `terminal_write/kill/list` 未同 `terminal_start` 校验 `_webui_connections` 首门 | a（FIX-3 统一口径） |
| L4 | desktop/main.cjs:252-256 | `mira:open-external` 仅 scheme 白名单 | b（补 `new URL()` 解析校验随 FIX-1 顺带；sender frame 检查记 v1.1） |

## [INFO / 正面观察]

- 终端 ANSI 输出仅可伪造显示文本（xterm `disableStdin`，无输入劫持、无 addon 攻击面）。
- workspace files 端点与 session 版共用 `_resolve_directory_path` 严格路径收敛，先 `check_api_token`，403→400 `invalid_path` 符合契约。
- 前端 store 200KB/4000 行裁剪；64K 入帧与 terminal_id 正则双端镜像一致；错误外泄收敛；`_feedback` 用 `json.dumps` 免疫 JSONL 行注入；Electron 保持 contextIsolation/sandbox。

## 注入溯源结论（单独成节）

**判定：worktree 与主 checkout 的代码/配置/数据中均未定位伪造 "System:" 注入块来源；该注入不来自仓库内容。**

排查面（全只读）：diff+untracked 2035 行新增无诱导文本（命中的均为防御性文档/测试毒料，如 `tests/memory/retrieval/test_injection_block_safety.py:28` 系 2026-09-15 审查 I-1 回归样本，合法）；git hooks 仅 `.sample`、无 `core.hooksPath`；npm scripts 无生命周期钩子，新增包 integrity 与官方源一致且无 install 脚本/网络调用；CI 未改动；`~/.nanobot/*` 与 `memory/state.db` strings 检索无触发串；`.ai-runtime-artifacts/` 中「用户已确认」仅出现在合法决策文档标题；Qoder 知识库无生成条目。

**处置**：来源应为仓库之外的运行通道（LLM provider 响应、编排/渲染层或某 MCP server 输出），属环境级风险。保留各 worker 拒绝记录；**建议向用户/平台侧上报注入样本原文以便定位通道**；不据此清理任何仓库文件。

## 复审口径

FIX 完成后需对 FIX-1（BrowserSection sandbox/noopener 触及文件）与 FIX-3（terminal_bridge/inbound_commands 触及文件）做定向安全复审。

---

## 复审结果（RE-SECURITY，2026-10-11 尾盘第二轮）

**verdict: APPROVE**。逐项确认：
- HIGH 已缓解：sandbox 无 allow-same-origin → 内嵌文档恒为 opaque origin，重定向/DNS rebinding 至网关源也无法读父页 token；无 allow-top-navigation；弹窗不逃逸 sandbox；网关认证走 header token（SameSite 下子帧不带凭据）。chain 断言成立。
- MEDIUM 多行绕过已缓解：`analyze_restricted_segment` 状态机 + 拼接视图整段 guard；残留形态（行尾悬空 `|`/`>`/`&&` 运算符续接、`$'…'` 混淆）定级 LOW——默认 deny 集为 token 型模式不可利用，v1.1 加固。
- MEDIUM sandbox wrap fail-open 已缓解（status=error，不 spawn 裸 shell）；win32+sandbox 跳过 wrap 记 LOW 平台平价项。
- LOW 组全部落地（连接首门、feedback 4000 上限、main.cjs URL 解析、package-lock resolved 已回官方源）。
- 输出配额降级 v1.1 确认可接受：入站 64k/出站 8KB/MAX_OUTPUT_CHARS/并发 8/idle 回收已有硬界，单用户自托管威胁模型下属自伤面。

新发现均 LOW：运算符悬空续接、win32 wrap 跳过、main.cjs:192 setWindowOpenHandler 校验口径与 IPC 不一致（建议统一走 URL 解析）。全程未发现注入块，只读执行。
