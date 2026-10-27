---
artifact: verification
route: verification-before-completion
created_at: 2026-10-27
status: passed
---

# 全局记忆文案修正

将记忆行工作区标签、工作区筛选选项、新增记忆工作区选项三处“工作区未记录 / Workspace not recorded”统一为“全局记忆 / Global memory”。保持内部值 `unassigned` 与查询/写入语义不变。

修改文件：`webui/src/components/settings/memory/MemoryPresentation.tsx`、`MemoryEditDialog.tsx`。

- 相关组件与 API 测试：`npm run test -- src/tests/memory-presentation.test.tsx src/tests/api-memory.test.ts --reporter=dot --cache=false`，退出码 0，19/19 通过。
- 两文件 ESLint：退出码 0，无错误或警告。
- 定向搜索确认两组件内无旧文案。未新增无必要的纯文案测试；既有测试无对应旧文案断言。
- 后续为刷新实际静态资源重新生产构建：沿用 `2026-10-27-esbuild-memory-input.cjs` 的临时 NODE_OPTIONS 方案运行完整 `npm run build`，退出码 0，3015 模块转换，`built in 11.93s`。
- 新资源 `nanobot/web/dist/assets/MemoryPage-DxxquY0X.js` 为 32.26 kB/gzip 10.14 kB；定向检查确认包含“全局记忆 / Global memory”，不包含旧文案。
- 未提交或推送。
