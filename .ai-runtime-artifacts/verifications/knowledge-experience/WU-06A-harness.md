---
artifact: verification
route: verification-before-completion
wu_id: WU-06A
wu_type: e2e
agent_role: test-engineer
created_at: 2026-11-11
status: harness-ready
skills: [browser-testing-with-devtools, agent-browser, verification-before-completion]
skills_evidence:
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/browser-testing-with-devtools/SKILL.md
  - C:/Users/Huangqh/.agents/skills/agent-browser/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - webui/src/providers/ClientProvider.tsx
  - webui/src/lib/api.ts
  - webui/src/lib/knowledge-eval-api.ts
  - webui/src/lib/evaluation-api.ts
  - .ai-runtime-artifacts/contracts/2026-11-11-contract-knowledge-experience.md
---

# WU-06A：隔离浏览器验收夹具交接

本轮仅完成 harness；两页 worker 仍在实现。未执行最终新交互断言，不代表体验改版已验收。

## 实际入口

Vite root 为 webui；必须使用以下 /@fs 地址，不能使用 /.ai-runtime-artifacts/（后者会返回 SPA）。

```text
http://127.0.0.1:5186/@fs/D:/studyspace/project/.harness-worktrees/Zhixu/wt-2026-11-11-knowledge-experience/.ai-runtime-artifacts/verifications/knowledge-experience/fixture.html#/knowledge
http://127.0.0.1:5186/@fs/D:/studyspace/project/.harness-worktrees/Zhixu/wt-2026-11-11-knowledge-experience/.ai-runtime-artifacts/verifications/knowledge-experience/fixture.html#/knowledge-eval
```

实测 document.title 为 `测试数据 · 知识工作区验收夹具`，顶部为 `测试数据 · 隔离浏览器夹具`。提供知识库/评测切换、浅深色切换、刷新重置。真实导入 KnowledgePage、KnowledgeEvalPage、ClientProvider、globals.css、i18n；仅夹具控制栏自建，没有重写业务界面。HTML 自带 React Refresh 前导，解决 /@fs 静态 HTML 未注入前导的问题。

## 数据与隔离

- 3 个知识库，包含长名称、全局/工作区、停用和空库。产品库有 ready/failed/processing 文档；另一库有 ready 文档。
- 文档详情、3 个 chunks、分页/状态筛选；搜索按文本及库匹配，支持空结果。检索分值为固定测试数据，不是真实算法评估。
- 图谱统计、实体列表、类型/名称过滤、3 节点/2 边、实体邻居与 chunk 证据跳转接口。
- 2 个题集（其中一个空题集），2 次 completed 运行；报告具备聚合与逐题结果，expect/judge 独立分组、not_assessable/null 与 failed，7 项指标元数据及公式来自 mock wire response。
- 对比提供 14 行分组指标（含 null）及退化查询。公式与分值为界面测试样本，不作为算法正确性的证据。
- 知识库/文档 mutation 只改当前标签页内存：创建、编辑、删除、重索引、导入占位、图谱抽取/重建、修复响应。题集 CRUD、revision CAS、创建运行和取消均本地模拟。
- 真实 transport/API 不打补丁。评测 /auth/session 总是返回合成 CSRF，避免生产 ensureSession 进入读取保存密码分支；后续请求校验 CSRF。知识库 HTTP 校验合成 token。没有调用 bootstrap、读取配置、建立 ZhixuClient 连接或调用模型。
- 全局 fetch 没有 native fallback，仅认可本地 5186 知识库及 8790 评测的显式路由；其他 origin/port 拒绝，未知路径返回 501。XMLHttpRequest/WebSocket/EventSource 阻断，sendBeacon 返回 false；HTML CSP 额外限制外站资源。Vite 启动时已建立的 HMR 连接不受影响。
- window.__knowledgeFixture.audit 只记录方法和路径，不记 header/body/上传内容。controls 提供 delayMs、failNext（下一次匹配 pathname 子串的请求返回503）、conflictNext（下一次题集 PATCH 返回409）。仅在独立测试浏览器使用，不加载真实凭据。

## 明确限制

- 新建运行保持 running，支持取消；没有模拟模型计算或自动完成。新建运行的 configs/report 目前沿用固定报告样本，只能测试接受/轮询/取消，不可验收任意自定义配置的结果正确性。
- 文档导入只使用提交名称创建 processing 占位，不解析文件、不读取服务端路径。重索引停留 processing，未模拟完成进度。图谱抽取/重建返回成功队列，但保留预置图谱。
- 原生页面轮询行为照常运行；后续页面新增接口时会明确失败，需增补夹具，不会偷偷访问服务端。
- 刷新重置内存；i18n 使用真实模块，只读写语言偏好。请继续使用独立浏览器 profile。
- 替换 WebSocket 后 Vite 断线重连可能被阻断，开发服务重启后整页刷新即可。
- 5186 进程与 esbuild.exe 由 Leader 管理；未改既有 vite.config.ts、依赖及页面文件。并行 worker 的其他修改不属于本 WU。

## 本轮实测

cwd 为该 worktree 根目录：

```powershell
node webui/node_modules/typescript/bin/tsc -p .ai-runtime-artifacts/verifications/knowledge-experience/fixture-tsconfig.json
.ai-runtime-artifacts/verifications/knowledge-experience/esbuild.exe .ai-runtime-artifacts/verifications/knowledge-experience/fixture-check.ts --bundle --platform=node --format=cjs --outfile=.ai-runtime-artifacts/verifications/knowledge-experience/fixture-check.cjs
node .ai-runtime-artifacts/verifications/knowledge-experience/fixture-check.cjs
```

以上命令退出 0。自检覆盖 wire shape、分组/null、局部 mutation、独立实例、CAS 409、503单次失败、外站/8765请求拒绝及未知 mutation 拒绝。TDD gate: N/A（无生产代码变更）。AAA 适用于 transport 自检；只 mock 外部服务边界，无页面/API函数 mock，无 snapshot 断言。

agent-browser 独立会话 wu06a-smoke 实测两个入口首次加载；知识库 3 卡、评测预置题集与测试标记可见，errors 输出为空。只读检查 resource origin 集合为 http://127.0.0.1:5186，评测 auth/session/metrics/datasets/runs 出现在内存 audit 中。最初 wu06a-fixture 会话发现并修复 RefreshSig 缺失；该旧会话 error 缓存不作为修复后证据。未执行报告/图谱/弹层等交互断言。

截图：fixture-knowledge-smoke.png、screenshot-1794388348818.png（评测首次加载）。截图是旧版业务界面的加载证据，不能视为最终改版截图。

## 后续验收清单（未执行，等待 Leader 派发）

1. 桌面/窄屏/浅深色：三卡长名称、空库、列表筛选与返回；文档三状态、详情分块、检索空结果与命中跳转。
2. 上传/新建/删除/重索引/修复确认及取消；只使用自建无敏感测试文件，验证 audit 只走夹具。
3. 图谱选择/展开、邻居、证据回文档与关闭恢复；失败注入后的局部重试。
4. 题集新建/模板导入/编辑/删除；409保留草稿；新建运行付费提示（若有）及取消仅调用本地 mutation/fetch。
5. 两个固定运行报告：5主指标+2辅助指标、来源分组、null不补0、失败与未评分、复合caseId、逐题详情；对比退化与方向。
6. 请求延迟下快速切库/文档/运行，确认旧响应守卫；Esc/Tab/焦点恢复、reduce-motion、刷新/返回无残留遮罩与重复轮询。
7. 最终补录屏、布局截图、console/network、状态矩阵。没有完成这些项前不得宣称最终 E2E 通过。

## 文件列表（本 WU 新增）

- fixture.html：独立入口与网络 CSP。
- fixture.tsx：隔离启动、真实页面/provider/i18n/css、夹具控制栏。
- fixture-data.ts：测试种子与 snake_case 评测响应。
- fixture-transport.ts：fetch/client 内存边界与拒绝出站。
- fixture-check.ts、fixture-check.cjs：transport 自检源文件及可运行产物。
- fixture-tsconfig.json：仅夹具静态类型检查。
- fixture-knowledge-smoke.png、screenshot-1794388348818.png：首次加载证据。
- WU-06A-harness.md：本交接与待执行清单。

## Skills 使用

已读取 test-engineer 角色、browser-testing-with-devtools、agent-browser 及其 CLI core 工作流、verification-before-completion；未发现可用 chrome-devtools MCP，浏览器冒烟使用 agent-browser CLI。e2e_via: cli。最终交互验收按本次派发要求等待后续任务，不调用其他 agent，不 commit/push。
