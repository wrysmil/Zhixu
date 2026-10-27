---
artifact: stack
route: source-driven-development
skills:
  - source-driven-development
source:
  - webui/package.json
  - webui/bun.lock
created_at: 2026-10-27
---

# 记忆页面技术栈核对

| 依赖 | package.json 声明 | bun.lock 锁定 |
| --- | --- | --- |
| React | ^18.3.1 | 18.3.1 |
| Vite | ^5.4.11 | 5.4.21 |
| TypeScript | ^5.7.2 | 5.9.3 |
| Tailwind CSS | ^3.4.17 | 3.4.19 |
| Vitest | ^2.1.8 | 2.1.9 |
| lucide-react | ^0.469.0 | 0.469.0 |

以当前组件、原生日期格式化和既有图标库完成设计，不需要依赖升级。
此次为方案核查，未执行安装、构建或测试。
