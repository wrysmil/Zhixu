---
artifact: verification
route: tier1-leader-direct
scope: evaluation-webui-wu10-12
created_at: 2026-10-24
status: passed
---

# 验证记录：评估 WebUI WU-10~WU-12

## 提交

| WU | 提交 | 内容 |
| --- | --- | --- |
| WU-10 | `ec61802` | `webui/src/lib/evaluation-api.ts` + 4 条 API 客户端测试 |
| WU-11 | `7bec0e4` | Settings 评估页（总览/运行/新建/详情/Trace）+ contracts/sidebar/page 注册 + 十语言文案 + 徽标键 camelCase |
| WU-12 | `902e70f` | 数据集 Tab（模板/xlsx 导入导出/用例/HTML 汇总）+ 2 条数据集测试 |

## 命令证据

```
./node_modules/.bin/vitest run
  Test Files  147 passed | 2 skipped (149)
  Tests       1884 passed | 2 skipped (1886)

./node_modules/.bin/tsc -p tsconfig.build.json --noEmit
  （无输出，通过）
```

评估专项：`evaluation-settings.test.tsx` 5 passed（总览+Runs 表、新建运行载荷
`{suite_id, case_ids, agent_profile_id, judge_profile_id, target, run_timeout_s}`、
详情 Sheet + Trace 分页 `getTrace("a1", 0, 200)`、数据集管理、xlsx 导入 + 行错误）；
`evaluation-api.test.ts` 4 passed（自动登录 + CSRF 复用、401 重试一次、契约错误体、
Idempotency-Key）；`i18n.test.tsx` 27 passed（十语言形状一致）。

## 真实浏览器验证（2026-10-24）

环境：Vite dev 5173（HMR 加载新组件）+ gateway 8765 + 内嵌评估服务 8790（含 CORS
修复后进程）。browser-use 隐藏视口，经 evaluate_script + aria 查询验证。

| 步骤 | 结果 |
| --- | --- |
| 侧栏出现 Evaluation 导入项（ClipboardCheck，Channels 与 System 之间） | ✓ |
| 评估页三 Tab：Overview / Runs / Datasets | ✓ |
| 服务徽标 **Online**（bootstrap secret 自动登录 → csrf 就绪） | ✓ |
| 总览 7 个指标块（Planned/Passed/Failed/Needs review/Not evaluated/Pass rate/Coverage）空态「—」+「No runs yet.」 | ✓（评估库当前无数据，空态正确） |
| Runs Tab：状态筛选 + New run + Refresh 按钮 | ✓ |
| New run 对话框：Target type（Agent/Team/Skill/Dataset）、Target ID、Dataset、Cases、Model、Judge、不限时 + 总时限 600；Model 下拉为**真实 profiles**（deepseek-flash、受控执行器 eval-model；Judge 含受控评分器）；Cancel 正常关闭 | ✓ |
| Datasets Tab：Download template / Import 按钮 + 「No datasets yet.」空态 | ✓ |
| Download template 实际触发（无 actionError 告警） | ✓ |
| 页面 fetch `/api/eval/v1/suites` 200（空列表，与 UI 空态一致） | ✓ |
| console 无错误（唯一 401 为 `/auth/session` 探测，是自动登录预期第一步） | ✓ |

## 已知限制

1. 评估库当前无套件/运行，Runs 表、Trace、导入回环未在真实浏览器跑通——组件级行为由
   vitest 契约测试覆盖；WU-13（真实跑分端到端）待做。
2. deepseek key 配额耗尽（账号问题），真实 run 的 LLM 调用会失败——配额恢复后才能执行
   WU-13 的「跑完看到 verdict/Trace」。
3. 提交未 push，等用户确认。

## 与计划的偏差

见执行日志 GROUP-2 §偏差与处置（i18n 前移至 WU-11、SettingsPage 总闸外渲染、
PreviewApp eval→overview、徽标键 camelCase、requestBlob 删除、下载按钮 await 化）。
