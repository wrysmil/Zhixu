---
artifact: collective-test
route: verification-before-completion
skills: [verification-before-completion, browser-testing-with-devtools]
source:
  - .ai-runtime-artifacts/execution-logs/2026-11-02-memory-evaluation-quickstart-execution-log.md
  - tests/evaluation/test_memory_default_setup.py
  - webui/src/tests/memory-evaluation-form.test.tsx
created_at: 2026-11-02
status: passed_with_limits
---

# 记忆评测接线与简化入口验证

## 范围与结果

默认能力来源改为真实 Worker adapter 的共用配置工厂；无向量 FTS 与时钟覆盖继续标 partial，显式空能力覆盖继续拒绝运行。前端默认仅显示测试集、被测模型、运行摘要和必要限制提示，高级功能折叠；默认优先内置20题与完整记忆效果，主要指标随轨道联动。离线 controlled 模型不再进入真实评测的默认选择，独立抽取模型禁用并解释共用被测模型。

18个业务与测试文件在隔离 worktree 和原 checkout 字节一致，整合前逐文件验证原 checkout 与基点一致；保留其他任务的 memory 算法/测试/数据库改动。原 checkout 已重新构建静态资源，未提交、未推送。

## 新鲜命令证据

- worktree：Python pytest default_setup/routes/adapter/worker/profiles/end_to_end 六文件，**99 passed**，47.25s。
- 原 checkout：pytest default_setup/routes 两文件，**56 passed**，8.85s。
- 原 checkout：npm run test -- memory-evaluation-form/entry/api/datasets/report/replay 与 evaluation-i18n-parity，**47 passed，7个文件**，2.72s。
- 原 checkout：npm run build，**exit 0**，TypeScript 构建检查与 Vite 均通过，最终构建6.66s。
- 修改的4个TS/TSX文件 ESLint --max-warnings 0：**exit 0**。
- 修改的4个Python文件 ruff check：**All checks passed**。
- git diff --check：通过。
- 后端两份产品文件 basedpyright：**254 errors，未通过**；子任务按行映射报告新增/修改行诊断0。本轮未取得干净基线对照，不宣称严格类型检查通过。

RED/GREEN：后端新增默认装配回归修复前2 failed/1 passed，修复后3 passed。前端新增测试复现默认120题、未折叠与错误主指标，修复后9条表单测试通过；整合新增语言键时 parity 先失败，补齐其余8语言包后3条 parity 通过。其余语言的 memory 区域既有约定为英文，本轮沿用该约定补全新键，非完整语言翻译。

## 浏览器证据与边界

通过 CUA 在独立端口5176/19791验收真实 MemoryExperimentForm 组件；数据来自独立数据库的真实 /memory/capabilities 与 /memory/datasets API。测试入口醒目标记“隔离验收环境，提交仅检查参数，不调用模型”。端口8790及18790属于现有桌面进程，未替换、未占用或中止。

1. 默认两个下拉选择，自动选择 memory-smoke-v1、end_to_end；高级设置折叠。
2. 确认可见限制后，开始测试按钮可用；提交参数为20场景、1次重复、1个候选、qa_accuracy_scenario_macro。
3. 展开高级设置，切换 extraction 自动选择对应指标；总预算设10后收起，仍显示20>10并禁用启动。
4. 独立抽取模型显示禁用及共用模型说明。
5. 截图：`/Users/mima0000/.codex/worktrees/memory-evaluation-quickstart/zhixu/.ai-runtime-artifacts/verifications/memory-quickstart/browser/default-form.png`。

截图只证明真实组件界面；隔离预览的提交回调只显示参数，不是一次真实模型评测。真实 HTTP 创建和入队由 default_setup 集成测试覆盖，真实产品链路的受控子进程回归由 end_to_end 测试覆盖。

## 限制

- 未调用付费模型，不证明实际记忆质量改善。
- 当前桌面 gateway 仍是修改前启动的进程，未强制退出用户会话；新后端在下次启动时生效。静态资源已经构建到原 checkout。
- aiohttp AppKey、Browserslist、Tailwind歧义类与大bundle提示仍存在，构建未因其失败。
- 独立安全/代码审查子任务在中途遭遇工作区额度错误，仅有部分只读结论；Leader另行进行了静态复核，不把部分结果冒充完整独立审查。
- visual-verdict skill 未安装，使用真实截图与AX交互验证；未生成虚假的视觉评分。
