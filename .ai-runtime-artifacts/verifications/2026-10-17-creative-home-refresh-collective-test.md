---
artifact: collective-test
route: orchestration.collective-closeout
skills: [verification-before-completion, test-driven-development, browser-testing-with-devtools]
skills_evidence:
  - .claude/skills/verification-before-completion/SKILL.md
  - .claude/skills/test-driven-development/SKILL.md
  - .claude/skills/browser-testing-with-devtools/SKILL.md
source:
  - 2026-10-17-creative-home-refresh-plan.md
  - 2026-10-17-contract-creative-home-refresh.md
created_at: 2026-10-17
status: pass
---
# 创作空间首页集体测试

## 范围与基线
- worktree: /Users/mima0000/.codex/worktrees/creative-home-refresh/zhixu
- branch: codex/creative-home-refresh；HEAD: 2d7513fecf29fa3f5157103d9f793278e3d04589
- 用户前端 WIP 导入后基线 tree: d0c501ec07a735f621d9dfb3e865f86eda60934b。
- 本次仅 15 文件：首页 ArticleList、10 locale、3 测试与 1 PNG。原工作区无业务源码编辑。

## 自动验证（本轮新鲜证据）
| 命令（cwd=worktree/webui，另注除外） | 结果 |
| --- | --- |
| 初始 vitest run creative-workspace + creative-workspace-loading | 2 files / 23 tests pass |
| 新首页测试 RED | 首轮 9 tests，8 个预期失败、1 个既有行为通过；失败原因是缺失 label/Enter/IME/防重/模板说明/本地化 |
| 新首页 + 工作区测试 GREEN | 3 files / 36 tests pass；新首页 13 tests 含 Safari229、异常、空态 |
| 创作相关测试 | 10 files / 86 tests pass |
| vitest run --maxWorkers=4 --minWorkers=2 --reporter=dot | exit 0；133 passed / 2 skipped files；1731 passed / 2 skipped tests；37.69s |
| npm run build | exit 0；包含 tsc；Vite 2970 modules，5.99s |
| npm run lint | exit 0；全 WebUI+渠道前端 |
| 改动 TSX 四文件 ESLint --max-warnings 0 | exit 0 |
| git diff d0c501ec07a735f621d9dfb3e865f86eda60934b --check（cwd=worktree） | exit 0 |

TDD：先观察 RED，再实现 GREEN；本轮未提交，故无 test/code commit hash。保留行为测试，更新旧测试失效的语言定位器。
既有日志：KaTeX quirks、React act、happy-dom iframe 取消请求、Browserslist 数据陈旧；构建有既有 Tailwind ease 歧义、StartDock 混合导入、chunk 体积提示。测试和构建均 exit 0，不将这些提示声称为已修复。

## 浏览器验证
使用 CUA 的 Chrome 连接与隔离 localhost 预览 http://127.0.0.1:5177/?preview=1#/creative。无真实文章/API/模型调用，创建的是内存示例。
- 1440×1000 中文浅色：首页文章区域标题 top=568px，创建表单高292px，完整首行文章可见；main 无横向溢出，brokenImages=0。
- 英文首页：所有操作与提示已英文显示，不再混用中文 fallback；示例文章正文仍是既有中文数据。
- 点源码阅读模板 → 填标题 → Enter：进入编辑器，标题正确，提纲5条。
- 返回后搜索不存在标题：空态和清空筛选按钮；点清空恢复4条（3演示+1验收草稿）。
- 深色桌面：文字、模板、文章卡片与插画清晰，main无横溢。
- 390×844：浅/深色均无横向溢出，按钮独占行、模板2列，文章标题top≈683px。
- Console error/warn：空数组。
- 验证结束恢复浅色、清除 viewport override，reload恢复3条原始预览文章；最终ready=true、zh-CN、无溢出；保留Chrome交付页。

## 截图
- 2026-10-17-creative-home/desktop-light.png
- 2026-10-17-creative-home/desktop-dark.png
- 2026-10-17-creative-home/mobile-light.png
- 2026-10-17-creative-home/mobile-dark.png

## 自检
- 创建/选择模板/重试/同步防重/IME：PASS
- 搜索/状态筛选/清空/打开/复制/删除确认/未保存提醒：单测覆盖PASS
- 语言键完整性与插值、TypeScript、Lint：PASS
- 不新增依赖；保留 Props/API/编辑器与既有权限边界。
- 列表既有50条上限未扩大，属于独立数据分页需求；本轮未涉及真实后端持久化或Safari设备实测（IME229由测试验证）。
- 独立代码与安全审查：APPROVE，见 reviews/ 对应报告。

## Next
交付保留 worktree 与本地预览，不自动合并、提交或推送。
