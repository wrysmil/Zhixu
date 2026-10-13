---
route: superpowers:writing-plans
date: 2026-10-09
topic: desktop-mira
dispatch: n/a
approved: true
approval_quote: "你想想把，然后webui不能去除保留，然后桌面版也要保留……换一个新的名字"（goal 一次性授权，跨轮持续推进）
spec: ../specs/2026-10-09-desktop-mira-spec.md
---
# 实施计划：Mira 桌面版 + 品牌重塑

单线 Leader 直做（无并行 WU，dispatch: n/a）。分支：feature/desktop-app（基于 feature/memory-system）。

| # | 步骤 | 产物/文件 | 状态 |
| --- | --- | --- | --- |
| T1 | 新品牌资产 | webui/public/brand/mira_*、desktop/assets/icon.png | done |
| T2 | Electron 桌面壳 | desktop/{package.json,main.cjs,preload.cjs,boot.html,scripts/run.cjs,README.md} | done |
| T3 | WebUI 品牌重塑 | i18n×10、index.html、manifest.json、sw.js、Sidebar.tsx；旧 nanobot 图 git rm | done |
| T4 | 测试断言同步 | app-layout/sw/i18n tests | done |
| T5 | 验证 | webui vitest + build；desktop npm i + smoke | in progress |
| T6 | 提交 | feature/desktop-app commit | pending |

## Next
- T5 绿 → T6 提交 → verification-lite 落盘。
