---
batch: home-dock-pty-terminal-v1.1
type: verification-lite
date: 2026-10-11
verdict: PASS (12/12 acceptance + 4/4 security review)
---

# 验收一句话总览 · home-dock-pty-terminal v1.1

**整体结论：PASS**。

- **12 项 CDP 实机验收**：PASS 12/12。详见 [`2026-10-11-home-dock-pty-v1.1-collective-test.md`](./2026-10-11-home-dock-pty-v1.1-collective-test.md)
- **4 项安全评审**：PASS 4/4（O_NONBLOCK 排水 / restricted 保守 / 接管同 gate / resize 500/200 单一真相源）。详见 [`2026-10-11-home-dock-pty-v1.1-security-review.md`](./2026-10-11-home-dock-pty-v1.1-security-review.md)
- **Code review（plan task 偏离点影响面裁决）**：PASS（6 处偏离全部「不影响产品行为、不引入新攻击面」）。详见 [`2026-10-11-home-dock-pty-v1.1-code-review.md`](./2026-10-11-home-dock-pty-v1.1-code-review.md)

## 关键产物

| 文件 | 说明 |
| --- | --- |
| `2026-10-11-home-dock-pty-v1.1-collective-test.md` | 12 项 + 4 评审结论矩阵 + 截图索引 + 集体测试三轨道结果 |
| `2026-10-11-home-dock-pty-v1.1-code-review.md` | plan task 偏离点影响面裁决（verification-before-completion 视角） |
| `2026-10-11-home-dock-pty-v1.1-security-review.md` | 4 项安全点逐项评审（O_NONBLOCK 与接管 gate 重点） |
| `assets/2026-10-11-home-dock-pty-v1.1/01..05.png` | CDP 截屏（终端启动 / xterm / 接管条 / kill 后 / 终态布局） |
| `assets/2026-10-11-home-dock-pty-v1.1/_verification-summary.json` | 12 项步骤结果 + WS 帧证据（所有 token 已重写为 `<TOKEN_REDACTED>`） |

## 一句话

首页/会话右栏升级为标签式 dock、PTY 真终端在 full-access 可用（xterm 直通 + bash 提示符 + ResizeObserver 自动 resize）、文件/浏览器 tab 同框并存、⌘P/⌘T 全场景直达、60s 宽限 kill 幂等、接管重绑走同 gate 信任面、resize 500/200 前后端单一真相源——零产品代码改动，可合并回主分支。

---

**token 保密合规**：所有产物均遵守「禁明文 token」要求；本文件与 `_verification-summary.json` 仅引用 tokenMeta 元数据（len=48、prefix=nbwt）。