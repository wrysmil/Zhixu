---
artifact: research-report
route: source-driven-development
skills: [source-driven-development]
skills_evidence:
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
source:
  - webui/package.json
  - webui/node_modules/react/package.json
  - webui/node_modules/react-dom/package.json
  - webui/node_modules/vite/package.json
  - webui/node_modules/typescript/package.json
  - webui/node_modules/tailwindcss/package.json
  - webui/node_modules/i18next/package.json
  - webui/node_modules/react-i18next/package.json
created_at: 2026-10-16
status: detected
---

# 保存反馈统一通知：技术栈检测

2026-10-16 直接读取 `webui/node_modules/*/package.json` 核验精确安装版本（`package.json` 里的 `^` 范围不等同于实际安装版本）。

| 依赖 | package.json 声明 | 实际安装 |
| --- | --- | --- |
| react | ^18.3.1 | 18.3.1 |
| react-dom | ^18.3.1 | 18.3.1 |
| vite | ^5.4.11 | 5.4.21 |
| typescript | ^5.7.2 | 5.9.3 |
| tailwindcss | ^3.4.17 | 3.4.19 |
| i18next | ^26.0.6 | 26.0.6 |
| react-i18next | ^17.0.4 | 17.0.4 |

## 关键结论

- **无任何 toast/notification 依赖**：`dependencies` 里没有 sonner / react-hot-toast / `@radix-ui/react-toast`。唯一已装的 Radix 包是 alert-dialog、dialog、dropdown-menu、popover、slot、tooltip。
- **React 18.3.1，且 `main.tsx` 刻意关闭了 `StrictMode`**（注释：dev 双调用会破坏 delta 累加，见 `useNanobotStream`）。因此 Toaster 的订阅/定时器逻辑在 dev 下不会被双挂载，但仍按「可重复挂载安全」写。
- **`crypto.randomUUID` 在 `main.tsx` 已被 shim**（非安全上下文/LAN 场景兜底）。toast id 生成可直接复用，不必再写一套降级。
- **既有跨模块通信约定**：`webui/src/lib/skill-events.ts`、`mcp-preset-events.ts`、`cli-app-events.ts` 统一用 `window.dispatchEvent(new CustomEvent<T>(NAME, { detail }))`，并在函数入口做 `typeof window === "undefined"` 守卫。新通知通道沿用同一约定，不引入 context/provider。
- **i18n 形状强约束**：`webui/src/tests/i18n.test.tsx` 的 `keeps every locale aligned with the English resource shape` 会逐 key 比对 `resources.en.common` 与其余 9 个 locale（es / fr / id / ja / ko / pt-BR / vi / zh-CN / zh-TW），要求 **missing 与 extra 均为空** 且插值变量一致。新增任何 i18n key 必须同时补齐全部 10 个 locale。
- **既有视觉基元可复用**：`App.tsx:3077-3089` 的 `restartToast` 已经给出「顶部居中 / 圆角胶囊 / `floatingSurfaceElevationClassName`」的成品样式，新 Toaster 直接沿用，不新造视觉。

本阶段只做技术栈核验与方案设计，不新增依赖，不改业务代码。
