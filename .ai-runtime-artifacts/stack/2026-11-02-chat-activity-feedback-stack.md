---
artifact: stack
route: source-driven-development
skills: [source-driven-development]
skills_evidence: [.claude/skills/source-driven-development/SKILL.md]
source: [webui/package.json, webui/node_modules/react/package.json, webui/node_modules/vite/package.json, webui/node_modules/typescript/package.json]
created_at: 2026-11-02
---

# 当前栈

实际安装 React 18.3.1、Vite 5.4.21、TypeScript 5.9.3、lucide-react 0.469.0、Vitest 2.1.9。复用现有组件与 useEffect 清理机制，无新增依赖。

参考 React 官方 useEffect 文档 https://react.dev/reference/react/useEffect （副作用与 cleanup）；本次不引入 React 19 API。

本机 bun 不在 PATH，以 node/npm 调用已有包完成同等检查。

## Next

继续集成验证。
