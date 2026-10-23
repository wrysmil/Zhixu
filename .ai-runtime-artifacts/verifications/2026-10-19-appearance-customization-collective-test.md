---
artifact: verification
route: orchestration:dispatcher-workflow -> batch-closeout
skills: [verification-before-completion]
skills_evidence: [.claude/skills/verification-before-completion/SKILL.md]
source: [.ai-runtime-artifacts/specs/2026-10-19-appearance-customization-spec.md, harness-kit/project.verification.md]
created_at: 2026-10-19
batch_id: GROUP-1
worktree_path: C:/Users/Huangqh/.codex/worktrees/appearance-customization/Zhixu
verdict: PASS
scope: 本次功能验证通过；全仓测试存在已复现的既有失败，不表示全套通过
---

# 外观自定义集体测试

## 范围与执行

WU-01 核心、WU-02 设置与预览、WU-03 字体/代码/终端与页面适配已整合。以下命令由 Leader 重新执行；本机无 bun，采用同一 package.json 中的 npm scripts。Vitest 使用 --no-cache 绕过共享 node_modules 的 .vite 写权限问题。

| 检查 | 结果 |
| --- | --- |
| npm run build，隔离 worktree/webui | exit 0；TypeScript 与 Vite 生产构建通过 |
| 本次相关十个测试文件，--no-cache --silent --maxWorkers=1 | exit 0；76/76 通过，报告 appearance-targeted-tests.json |
| 全套 --no-cache --silent --maxWorkers=4 --minWorkers=1 | exit 1；1835 通过、4 失败、2 跳过，报告 appearance-final-tests.json |
| 修改及新增 TS/TSX 的 ESLint --max-warnings 0 | exit 0，无警告/错误 |
| git diff --check | exit 0，仅换行转换提示 |
| 生产构建真实浏览器 | 预设、HEX 输入、浅深隔离、字体、390px 窄屏通过 |

## 全套测试边界

未改业务代码的主 checkout 基线全套为 1798 通过、5 失败、2 跳过，涉及 add-project-dialog、app-layout 自动化本地化、start-dock 默认 shell；详见 appearance-baseline-tests.json。另在主 checkout 定向复现 app-layout 删除自定义 skill 失败，报告 appearance-baseline-recheck.json。

最终全套四项失败：add-project-dialog 的 sidebar add-project entry；app-layout 的 deletes a custom skill、opens Automations、localizes the Automations surface。随后串行定向复验五项：3 通过，仅删除 skill 与自动化本地化仍失败，二者均在未改代码中出现。新增接口造成的 useAppearance mock 缺导出、xterm mock 缺 options 已修复，没有以生产兼容分支迁就测试替身。

结论为本次功能验证 PASS，不能据此声称全仓测试全绿。未修改不相关的项目、自动化、skill 删除或 shell 业务。

## 浏览器证据

通过 IAB 在生产构建 http://127.0.0.1:5185/?preview=1#/settings?section=appearance 操作实际界面。海蓝预设、三位 HEX 归一化、双模式自定义、无效颜色提示、本机字体名称和代码字体均生效。生产构建 computed body 字体为所选 Georgia/自定义 Microsoft YaHei；pre 字体以 Consolas 开头。390px documentWidth=390，无横向溢出。

开发服务器在配置热更时曾保留旧 Tailwind 字体输出，独立生产构建已验证正确；未为临时开发缓存改动业务实现。早期 HMR 缺接口日志属于组件尚在并行实现时的记录，最终干净加载另查。

截图：2026-10-19-appearance-light.jpg、appearance-dark.jpg、appearance-mobile.jpg，均在本目录。

## 未验证与限制

没有启动真实 gateway 或读取连接密钥；本地保存/重挂载、首屏恢复、跨窗口 storage、system 模式和终端保留实例由实际单元测试覆盖。桌面进程重启、真实 PTY 连接及 Teams/Agent 编辑弹窗完整端到端未实测。未更改 Python/TUI，无新增依赖和字体请求。未创建测试/实现提交，遵守本次不提交范围；红绿证据来自实际测试执行。

## Next

独立代码、安全和性能审查均通过，无阻塞。两项代码审查建议已修正并经原独立 reviewer 复核；未实测性能建议保留后续。36个文件通过 apply_patch 同步至主 checkout，归一换行逐文件比较完全一致。主 checkout 再次执行本功能76tests、生产构建、变更TS lint、diff --check 均exit0。真实浏览器干净加载无error/warn；390/640/768px均无横向溢出。实现已可使用，未提交或推送。
