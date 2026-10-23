---
artifact: verification-lite
route: 缺陷调查 -> Tier 1 Leader 直做（fix）
skills:
  - source-driven-development
  - test-driven-development
  - verification-before-completion
source:
  - 用户截图：首页输入框 `@commander` 时光标压在末字符上（"为什么这个光标有些问题"）
  - webui/src/components/thread/ThreadComposer.tsx
  - webui/src/components/CliAppMentionText.tsx
  - webui/src/components/ui/agent-glyph.tsx
  - .ai-runtime-artifacts/verifications/2026-10-18-agent-mention-verification.md
created_at: 2026-10-18
tier: 1
---

# Composer Agent mention 光标漂移 — 轻量验证（Tier 1）

## 根因

Composer 输入区是两层叠出来的：上层是文字被 `text-transparent` 抹掉的 `<textarea>`（原生光标由它绘制，
`ThreadComposer.tsx:2541-2571`），下层是画橙色 mention 的 `ComposerCliMentionOverlay`（`ThreadComposer.tsx:2531, 2951`）。
两层逐字符等宽时，光标才会贴着可见文字。

`AgentMentionToken` 的 composer 变体在覆盖层里额外插了一个行内图标
（旧实现 `CliAppMentionText.tsx:192-195`：`inline-flex items-center gap-0.5` + `AgentGlyph 0.74~0.82em`），
而 textarea 里只有 `@commander` 这 10 个字符 → 可见文字整体右移，光标留在原位，看起来压在 `r` 上。

同文件的 CLI / MCP token 没有这个问题，因为它们把 logo 绝对定位盖在 `@` 上、`@` 转 `text-transparent`
（`CliAppMentionText.tsx:261-286`），保留了 `@` 的 advance width。

## 改动

- `webui/src/components/CliAppMentionText.tsx`：`AgentMentionToken` 的 composer 变体改为复用 CLI/MCP 的
  「`@` 槽位 + 绝对定位图标」写法，覆盖层宽度与 textarea 纯文本严格一致；message 变体保持原来的行内图标不变。
- `webui/src/tests/thread-composer.test.tsx`：
  - 新增用例 `keeps the agent mention glyph inside the @ slot so the text cursor stays aligned`
    （断言 glyph 容器为 `absolute left-1/2 top-1/2`、token 文本仍为 `@commander`、无 `mx-` 位移）。
  - 调整既有用例 `opens the agent mention palette and sends the selected agent id`：emoji 图标现在占据 `@` 槽位，
    `textContent` 变成 `@🛠code-helper`，故改为断言名字片段 `code-helper` + glyph testId 存在；完整 `@name` 由 lucide 用例覆盖。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `vitest run src/tests/thread-composer.test.tsx -t "agent mention glyph"`（改前） | 1 failed（红：无 `composer-agent-mention-glyph-commander`，DOM 为 `inline-flex … gap-0.5`） |
| `vitest run src/tests/thread-composer.test.tsx src/tests/message-bubble.test.tsx src/tests/thread-shell.test.tsx`（改后） | 209 passed |
| `vitest run`（webui 全量） | 1779 passed, 2 skipped, 0 failed（139 files passed, 2 skipped） |
| `tsc -p tsconfig.build.json --noEmit` | exit 0 |
| `eslint --config webui/eslint.config.js` 两个改动文件 `--max-warnings 0` | exit 0 |

## 浏览器实测（vite dev :5175 → gateway :8765，首页 hero composer）

用与 textarea 同字体的镜像 span 复算光标 x，与覆盖层可见文字右边缘比对：

| 输入 | 覆盖层 token 宽 | textarea 纯文本宽 | 光标 vs 可见文字末端 |
| --- | --- | --- | --- |
| `@commander`（caret 在末位） | 101.0px | 101.0px | **0.0px** |
| `@commander `（选完候选词的真实状态，多一个空格） | 101.0px | 105.2px | +4.2px（正好一个空格宽） |

结构侧：`@` 槽位宽 14.4px，内部图标盒 11.8×11.8px（hero `0.74em`），绝对定位居中，不参与行内排版。

修复前的漂移量按同一字体推算为「图标 11.8px + gap 2px ≈ 13.8px」（未回滚复测，属算术推导），
与截图里光标压在 `r` 上的表现一致。

## 未验证项

- 视觉截图未取：in-app Browser 面板当时不可见，`take_screenshot` 返回
  `NATIVE_BROWSER_VIEWPORT_UNAVAILABLE`；上述结论以 DOM 几何数值为准。
- emoji 图标 Agent 的实机观感未验证（当前 gateway 的 Agent 列表里没有 emoji 图标档案），只有单测覆盖其结构。
- 非 hero（会话页）composer 未单独量测；其字号同为 16px、仅 line-height 由 24px 变 20px，宽度逻辑一致。

## 附带结论（非 bug）

- 光标比字高一截是浏览器行为：hero 为 `text-[16px] leading-6`（`ThreadComposer.tsx:2359`），
  原生光标高度跟行盒走（≈24px），16px 字的大写高只有 ≈11px。
- 选完候选词后光标与文字之间的那点间隙来自自动补的空格（`ThreadComposer.tsx:1721-1728`），属预期。

## Next

- 修复已完成并通过验证；未提交，等用户要求再走 git。
- 若仍想保留「图标在 `@` 左边」的观感，只能换 contenteditable 富文本输入（超出本次 bug 范围）。
