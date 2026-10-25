---
artifact: verification-lite
route: Tier 1 Leader 直做
skills: [source-driven-development, brainstorming, test-driven-development, verification-before-completion]
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
  - /Users/mima0000/.agents/skills/brainstorming/SKILL.md
  - /Users/mima0000/.agents/skills/test-driven-development/SKILL.md
  - /Users/mima0000/.agents/skills/verification-before-completion/SKILL.md
source:
  - harness-kit/core/routing.md
  - harness-kit/project.profile.md
  - harness-kit/context-map.md
  - .ai-runtime-artifacts/plans/2026-10-25-evaluation-observability-plan.md
  - .ai-runtime-artifacts/stack/2026-10-25-evaluation-observability-stack.md
  - https://react.dev/reference/react/useRef#manipulating-the-dom-with-a-ref
created_at: 2026-10-25
---

# 评估页内展示与连接修复

用户授权：“不要做出弹窗，而是做成平铺的页面展现”“直接在页面上优化”；追加反馈：“怎么还报错了”。沿用既有评估接口和观测能力，直接完成局部页面调整。

## 调整范围

- EvaluationSettings：页面标题、四项核心指标、次要计数行、最近运行页内直达。
- EvaluationRuns：新建和详情替换列表内容；返回保留筛选，并恢复键盘焦点；运行进度条。
- NewRunDialog：保留模块名兼容导入，实际为 section + 页内宽版表单，无 Dialog/Portal/遮罩。
- RunDetailSheet：保留模块名兼容导入，实际为 section + 页内详情；展示最新状态，刷新重载详情与观测内容。
- evaluation-api：移除工作区遗留 TEMP-DEMO 端口 8791，恢复实际监听端口 8790。浏览器由“请求失败/离线”恢复为“在线”，真实历史记录可读。
- 保留工作区其他已有修改，不修改后端，不执行新的真实模型评估，不提交或推送。

## 验证证据

- 修改前先调整交互测试：7 个测试中 4 个按预期失败，缺失页内 region 和总览直达入口。
- `npm run test -- --reporter=dot`：149 文件通过、2 文件跳过；1896 测试通过、2 跳过，退出码 0。
- `npm run build`：TypeScript + Vite 成功，退出码 0。
- 对本次 4 个组件、2 个测试、evaluation-api 单独 ESLint：退出码 0。
- 全量 `npm run lint`：3 个既有错误：evaluation-status.tsx 的两个非 hook 命名函数调用 useTranslation；browser-url.ts 的无用赋值。这些文件本轮均未修改。
- 原生子 Agent 独立只读审查发现“详情刷新未生效”和“返回焦点丢失”；两项已修复，并补测试，包含在上述全量通过结果中。
- 浏览器 1280×720：总览显示在线、历史运行可读；总览进入详情，无 dialog；返回运行列表焦点恢复原运行；新建页内表单及取消返回，新建按钮焦点恢复。

## 截图

- `2026-10-25-evaluation-inline-overview.png`
- `2026-10-25-evaluation-inline-detail.png`
- `2026-10-25-evaluation-inline-create.png`

## 限制

未实测移动端和深色模式。全量测试与构建仍输出既有 act/KaTeX、Browserslist、分包体积等警告；不将其表述为零警告。新建请求由自动化测试验证，浏览器只读检查和取消，不新增付费模型调用。
