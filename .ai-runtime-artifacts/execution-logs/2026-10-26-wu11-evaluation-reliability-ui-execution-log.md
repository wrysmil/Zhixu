# WU-11 执行日志：评估可靠性前端界面（T11）

- 日期：2026-10-26
- 分支：harness/agent-evaluation-calibration
- 范围：仅 webui 前端（5 个新组件 + 1 个新测试 + 10 份 locale common.json）
- 纪律：未修改 evaluation-api.ts、既有评估组件、Python 后端；未 commit、未 push

## 1. 变更清单

| 文件 | 说明 |
| --- | --- |
| webui/src/components/settings/evaluation/EvaluationScorecard.tsx | 任务完成度与综合合格率两个口径分栏展示；显式 full/smoke/legacy/未知能力标记；缺 Trace 与缺 usage 给出不同提示；证据不全时不输出综合质量结论；未知分母显示 Unknown 而非 0% |
| webui/src/components/settings/evaluation/EvaluationCalibration.tsx | Judge 校准状态、校准指纹、人工确认 gold 进度、运行校准入口；未达人工确认门槛时按钮禁用且提示待人工确认；无自动代签入口 |
| webui/src/components/settings/evaluation/EvaluationReviews.tsx | 自动结论与人工结论并排展示，人工结论为追加项；同时显示保留自动结论的说明 |
| webui/src/components/settings/evaluation/EvaluationComparison.tsx | 逐 case 展示 improved/regressed/unchanged/incomparable 与原因，显示比较整体原因，可跳转双方证据 |
| webui/src/components/settings/evaluation/CaseEvaluationEditor.tsx | 编辑任务/环境/里程碑/硬约束/量表/预算判据，保存形成 revision；校验里程碑权重与未知断言 id；提示 Excel 导入导出流程不变 |
| webui/src/tests/evaluation-reliability.test.tsx | 9 项测试，覆盖两个口径不互相冒充、未知分母、smoke/legacy 标记、缺 Trace 与缺 usage 分别提示、缺证不出结论、比较页逐 case 状态与原因、评审页保留自动结论、校准需人工确认、判据保存为新 revision |
| webui/src/i18n/locales/{en,es,fr,id,ja,ko,pt-BR,vi,zh-CN,zh-TW}/common.json | 在 settings.evaluation 下新增 reliability 子块，113 个键，10 语言同形，文本级锚点插入，未整体重序列化 |

## 2. 红→绿证据

- 红：首次运行 npm run test -- --no-cache src/tests/evaluation-reliability.test.tsx → Tests 6 failed | 3 passed（locale 键未落地，文案断言全部落空）。
- 另一次红：把 10 份 locale 恢复到 HEAD 后再跑全量，evaluation-reliability.test.tsx 出现在失败文件列表中（numFailedTests 14），确认该测试确实依赖新键而非空转。
- 绿：补齐 10 语言并统一缩进后，同一命令 → Tests 9 passed (9)，Test Files 1 passed。

## 3. 验证结果

| 命令 | 结果 |
| --- | --- |
| npm run test -- --no-cache src/tests/evaluation-reliability.test.tsx | 9 passed / 0 failed |
| npm run test -- --no-cache evaluation-reliability + i18n + i18n-dock-parity + evaluation-settings + evaluation-observability + evaluation-api | 56 passed / 1 failed（i18n-dock-parity 属既有问题，见下） |
| npx tsc -p tsconfig.build.json --noEmit | 退出码 0 |
| npm run build | 退出码 0（需先把 TEMP/TMP 指向可写目录，见风险项） |
| npm run test -- --no-cache（全量） | 1894 passed / 11 failed / 2 skipped；失败文件与同 HEAD 基线一致 |
| npx eslint（仅本次新增目录与测试文件） | 0 错误（evaluation-status.tsx 的 2 处既有 react-hooks 错误不在本次范围） |

## 4. 基线对比方法

- 备份 10 份 locale 后 git checkout 恢复，跑全量 vitest JSON 报告作为基线，再恢复改动重跑对比。
- 基线失败文件（除本轮新增测试外）：add-project-dialog、app-layout、i18n-dock-parity、preview-isolation、preview-thread、thread-viewport、useSessions、workspace-navigation —— 与改动后失败集合一致。
- start-dock、thread-shell 仅在改动后全量中出现失败，单独复跑均通过（109 passed），判定为并行负载导致的既有 flaky，与本轮改动无关。
- i18n-dock-parity 失败原因是 dock 键集比用例中的 DOCK_KEYS 多了 terminalTheme，属于既有的键表未收口，与 evaluation.reliability 无关。

## 5. 风险与未完成项

1. 五个新组件尚未挂进 EvaluationSettings/EvaluationRuns/EvaluationDatasets：这三个既有组件在本轮写入禁区，需由 Leader 集成，否则页面不可见。
2. evaluation-api.ts 没有用例判据保存、校准作业、复核提交、比较创建的读取封装之外的方法：CaseEvaluationEditor 的保存、校准作业、复核提交均以可选回调（onSave/onRunCalibration/onConfirmGold/onSubmitReview）形式暴露，未接后端前保存按钮会提示未接入。
3. EvaluationScorecard 的 coverage 入参是结构化 EvaluationCoverage，而 GET /runs/{id}/metrics 返回的 coverage 是 Record<string, unknown>，集成时需要一次字段映射。
4. RunMetricsView.coverage 与新 reliability 指标在旧服务上不存在，集成时须做空值兜底，不得用 0 代替未知。
5. 构建在本机需要 TEMP/TMP 指向可写目录（默认 C:\\Users\\Huangqh\\AppData\\Local\\Temp 下 esbuild 临时文件删除被拒），这是环境问题而非代码问题。
6. 未做真实浏览器路径验收：组件未挂载，浏览器内没有可访问入口。
