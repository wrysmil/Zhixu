---
artifact: execution-log
route: orchestration
wu_id: WU-05
wu_type: ui
agent_role: coder
created_at: 2026-11-11
status: done
scope: 统一文案与动效联调收口（10 语言词条 + 知识工作区专用浮层接入 + 交互/外观矩阵自检）
skills: [source-driven-development, frontend-ui-engineering, verification-before-completion]
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/frontend-ui-engineering/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - .ai-runtime-artifacts/plans/2026-11-11-knowledge-experience-plan.md
  - .ai-runtime-artifacts/contracts/2026-11-11-contract-knowledge-experience.md
  - .ai-runtime-artifacts/execution-logs/WU-01-knowledge-experience.md
  - .ai-runtime-artifacts/execution-logs/WU-02-knowledge-experience.md
  - .ai-runtime-artifacts/execution-logs/WU-03-knowledge-experience.md
  - .ai-runtime-artifacts/execution-logs/WU-04-knowledge-experience.md
  - webui/src/components/workspace/knowledge-shared/KnowledgeSurfaces.tsx
---

# WU-05：统一文案与动效联调收口

worktree `D:/studyspace/project/.harness-worktrees/Zhixu/wt-2026-11-11-knowledge-experience`，分支 `codex/knowledge-experience`。全程只在该 worktree 写入；未 commit、未 push、未新建分支、未 `git checkout/stash`，未派发子 agent 或 reviewer。

先读了计划、接口契约与 WU-01/02/03/04 四份执行日志，再改代码（source-driven-development Step 0）。共用夹具与验收 runner 由 WU-06A 预先提供（`.ai-runtime-artifacts/verifications/knowledge-experience/`）。

## 1. 实际改动文件清单

### 1.1 文案（10 个文件，纯增量）

| 文件 | 新增键数 | diff |
| --- | --- | --- |
| `webui/src/i18n/locales/en/common.json` | 46 | +48 / -0 |
| `webui/src/i18n/locales/es/common.json` | 46 | +48 / -0 |
| `webui/src/i18n/locales/fr/common.json` | 46 | +48 / -0 |
| `webui/src/i18n/locales/id/common.json` | 46 | +48 / -0 |
| `webui/src/i18n/locales/ja/common.json` | 46 | +48 / -0 |
| `webui/src/i18n/locales/ko/common.json` | 46 | +48 / -0 |
| `webui/src/i18n/locales/pt-BR/common.json` | 46 | +48 / -0 |
| `webui/src/i18n/locales/vi/common.json` | 46 | +48 / -0 |
| `webui/src/i18n/locales/zh-CN/common.json` | 46 | +48 / -0 |
| `webui/src/i18n/locales/zh-TW/common.json` | 46 | +48 / -0 |

合计 **46 键 × 10 语言 = 460 条译文**，全部写入 `knowledge.experience` 命名空间。写入方式是「解析 → 追加 `knowledge.experience` → `JSON.stringify(obj,null,2)` + CRLF」，这 10 个文件原本就与该序列化结果逐字节一致（已用 round-trip 校验），所以 diff 只有新增块，**既没有重排既有键，也没有格式化整个文件**。

### 1.2 浮层接入（6 个文件）

全部由 `@/components/ui/sheet` 换成 `@/components/workspace/knowledge-shared/KnowledgeSurfaces`：

| 文件 | 改动 |
| --- | --- |
| `knowledge/KnowledgeImportSheet.tsx` | `Sheet/SheetContent/SheetTitle/SheetDescription` → `KnowledgeSheet*` |
| `knowledge/KnowledgeDocumentSheet.tsx` | 同上 |
| `knowledge-eval/KnowledgeEvalCaseSheet.tsx` | 同上 |
| `knowledge-eval/KnowledgeEvalDatasetSheet.tsx` | 同上 |
| `knowledge-eval/KnowledgeEvalRunSheet.tsx` | 同上 |
| `KnowledgeEvalPage.tsx`（判定隐私说明浮层） | 同上 |

行为语义逐项保留：`open`/`onOpenChange` 守卫（`if(!v && !busy)`、`if(!v)close()`）、`onOpenAutoFocus` 记录触发元素、`onCloseAutoFocus` 阻止默认回退并手动 `focus()`、`data-testid="knowledge-chunk-preview"`、`showCloseButton={false}`、`className` 中的 `p-6 sm:p-8` / `gap-0 p-0` 全部原样传入。共享组件已自带 `knowledge-experience-surface knowledge-motion knowledge-sheet` 三个类，调用点里重复的同名字符串已删除，避免 DOM 上出现重复 class token。

### 1.3 矩阵自检发现并修复的组件缺陷（1 处）

`KnowledgePage.tsx` 文档面板补 `min-w-0`：

```
- <div className="h-fit rounded-panel border border-border/60 bg-background p-4">
+ <div className="h-fit min-w-0 rounded-panel border border-border/60 bg-background p-4">
```

原因见 §4.2。这是本轮唯一一处产品代码修复，落在我的写集内。

### 1.4 新增的验证产物（非业务代码）

`.ai-runtime-artifacts/verifications/knowledge-experience/` 下新增：`wu05-i18n-parity-check.mjs`、`wu05-i18n-parity-output.txt`、`wu05-matrix-zhCN-results.json`、`wu05-matrix-en-results.json`、`wu05-keyboard-results.json`、`wu05-i18n-coupled-test-patch.diff`、`wu05-probes/`（6 个探针脚本）。WU-06A 原有的 `acceptance/` 产物未被覆盖。

## 2. 文案键落地统计

### 2.1 键集合核对

用脚本扫描 `webui/src/components/workspace/` 下所有 `.ts/.tsx` 的 `t("knowledge.experience.<key>", …)` 调用点，得到 **46 个唯一键**，与 Leader 给出的清单（38 + WU-04 补的 8）完全一致：**没有遗漏、没有多余、没有拼写偏差**。同时提取每个调用点的中文 `defaultValue`，与任务表逐条比对，全部一致。

`retry` 在 WU-02/WU-03 两处是同一个键，`retryUpload` 是独立键，未重复计数。

### 2.2 每种语言补了多少键

| 语言 | 补齐键数 | 键集合与代码一致 |
| --- | --- | --- |
| en | 46 | 是 |
| es | 46 | 是 |
| fr | 46 | 是 |
| id | 46 | 是 |
| ja | 46 | 是 |
| ko | 46 | 是 |
| pt-BR | 46 | 是 |
| vi | 46 | 是 |
| zh-CN | 46 | 是 |
| zh-TW | 46 | 是 |

译文不是中文原样复制：ja/ko 使用本地汉字与外来语（例：`graphNoRelations` ja = 「関連ノードはまだありません。下にある文書の根拠はそのまま確認できます。」）；zh-TW 全部转繁体并贴合既有 zh-TW 术语（文档→文件、数据集→資料集、题集→題集、重新整理→重新整理）；en/es/fr/id/pt-BR/vi 按各语言习惯表达（例：`importStages` en = "Read file → Submit → Queue for processing. Results will update in the document list."，fr = "Lecture du fichier → Envoi → Mise en file d’attente…"）。

### 2.3 键集合一致性校验脚本（验证证据）

脚本落盘于 `.ai-runtime-artifacts/verifications/knowledge-experience/wu05-i18n-parity-check.mjs`，做四件事：① 从代码调用点提取键集合；② 逐语言解析 `common.json` 取 `knowledge.experience` 键集合；③ 断言 zh-CN 与代码 `defaultValue` 逐字一致；④ 断言非中文语言没有整段照抄 zh-CN。

```text
$ node .ai-runtime-artifacts/verifications/knowledge-experience/wu05-i18n-parity-check.mjs <webui>
代码调用点键数: 46
┌─────────┬────────┬──────┬─────────────┐
│ (index) │ lang   │ keys │ matchesCode │
├─────────┼────────┼──────┼─────────────┤
│ 0       │ 'en'   │ 46   │ true        │
│ 1       │ 'es'   │ 46   │ true        │
│ 2       │ 'fr'   │ 46   │ true        │
│ 3       │ 'id'   │ 46   │ true        │
│ 4       │ 'ja'   │ 46   │ true        │
│ 5       │ 'ko'   │ 46   │ true        │
│ 6       │ 'pt-BR'│ 46   │ true        │
│ 7       │ 'vi'   │ 46   │ true        │
│ 8       │ 'zh-CN'│ 46   │ true        │
│ 9       │ 'zh-TW'│ 46   │ true        │
└─────────┴────────┴──────┴─────────────┘
OK: 10 种语言的 knowledge.experience 键集合与代码调用点完全一致，zh-CN 与 defaultValue 逐字一致。
EXIT=0
```

## 3. 浮层接入核对

| 核对项 | 结果 | 证据 |
| --- | --- | --- |
| `rg 'components/ui/(sheet\|dialog)' src/components/workspace/` | 无匹配 | 6 处全部改完，workspace 下不再直接依赖共享 sheet |
| 页面根节点有 `knowledge-experience` | 是 | `KnowledgePage.tsx:544`、`KnowledgeEvalPage.tsx:335` |
| 每个浮层根有 `knowledge-experience-surface` | 是 | 矩阵探针在每个浮层阶段读 `dialogHasSurface`，24 个组合全部 `true` |
| 共享 `ui/sheet`、`ui/dialog` 未被修改 | 是 | `git diff --stat -- webui/src/components/ui/sheet.tsx webui/src/components/ui/dialog.tsx` 输出为空；`git status --short -- webui/src/components/ui/` 无输出 |
| 进出场动画由共享层 CSS 负责 | 是 | 浮层根的 `animation-name` 为 `knowledge-sheet-in`（220ms），遮罩为 `knowledge-fade-in`；无内联 style、无逐页私有动画 |
| `prefers-reduced-motion: reduce` 下动画关闭 | 是（实测） | reduce 下浮层 `animation-name: none`、`animation-duration: 0s`，遮罩同样 `none`；no-preference 下分别为 `knowledge-sheet-in / 0.22s` 与 `knowledge-fade-in` |

## 4. 验证命令与真实输出

所有命令 cwd 均为 `webui/`。本机无 Bun，用 Node 20.20.2 直跑。

### 4.1 类型检查

```text
$ node node_modules/typescript/bin/tsc -p tsconfig.build.json --noEmit --pretty false
EXIT=0
```

```text
$ node node_modules/typescript/bin/tsc --noEmit -p tsconfig.json --pretty false
EXIT=2（整仓 120 处既存报错，全部集中在 src/tests/** 与 import.meta.glob 类型缺失等历史问题）
```

整仓 120 条错误按路径过滤后，**落在本 WU 写集（components/workspace/**、tests/knowledge-experience.test.tsx）内的错误为 0 条**，本轮未新增类型错误。整仓 tsc 的既存失败未处理，也不属于本 WU 范围。

### 4.2 Lint（本 WU 写集限定）

```text
$ node node_modules/eslint/bin/eslint.js --config eslint.config.js --max-warnings 0 \
    src/components/workspace/KnowledgePage.tsx \
    src/components/workspace/KnowledgeEvalPage.tsx \
    src/components/workspace/knowledge/KnowledgeImportSheet.tsx \
    src/components/workspace/knowledge/KnowledgeDocumentSheet.tsx \
    src/components/workspace/knowledge-eval/KnowledgeEvalRunSheet.tsx \
    src/components/workspace/knowledge-eval/KnowledgeEvalDatasetSheet.tsx \
    src/components/workspace/knowledge-eval/KnowledgeEvalCaseSheet.tsx \
    src/components/workspace/knowledge-shared/ \
    src/tests/knowledge-experience.test.tsx
ESLINT_EXIT=0
```

### 4.3 行为测试

Leader 修复测试与词条的耦合后（本轮未修改那两个测试文件）：

```text
$ node node_modules/vitest/vitest.mjs run --config ../.ai-runtime-artifacts/verifications/knowledge-experience/vite.config.ts \
    src/tests/knowledge-experience.test.tsx src/tests/knowledge-page.test.tsx \
    src/tests/knowledge-eval-page.test.tsx src/tests/knowledge-graph.test.tsx src/tests/knowledge-graph-view.test.tsx
 ✓ src/tests/knowledge-graph-view.test.tsx (4 tests) 2208ms
 ✓ src/tests/knowledge-graph.test.tsx (12 tests) 2249ms
 ✓ src/tests/knowledge-experience.test.tsx (30 tests) 3499ms
 ✓ src/tests/knowledge-eval-page.test.tsx (20 tests) 7485ms
 ✓ src/tests/knowledge-page.test.tsx (24 tests) 8399ms
 Test Files  5 passed (5)
      Tests  90 passed (90)
EXIT=0
```

**浮层替换的零回归对照实验**：把 10 个 locale 文件临时还原为 HEAD 版本（其余改动保留）后，同一命令为 `Test Files 3 passed / Tests 74 passed (74) / EXIT=0`，证明 74 条通过与浮层替换无关；随后把 locale 恢复为带词条版本即回到 16 failed，根因见 §6。

### 4.4 隔离浏览器夹具（5186）矩阵自检

用 WU-06A 的隔离夹具（`fixture.html` + 内存 mock，不连真实后端、不调模型、不用真实凭据），自写探针实跑，脚本与结果落盘于 `.ai-runtime-artifacts/verifications/knowledge-experience/wu05-probes/` 与 `wu05-matrix-*.json`。每个组合覆盖 6 个阶段：知识库列表 / 文档列表 / 文档浮层 / 评测记录 / 评测报告 / 单题浮层。

**中文界面（zh-CN）12/12 通过**（深/浅 × 1440×900 / 1280×800 / 390×844 × reduced-motion: no-preference / reduce）：

| 组合 | 横向溢出 | 浮层动画（no-preference → reduce） |
| --- | --- | --- |
| light-1440x900 | 无 | knowledge-sheet-in 0.22s → none 0s |
| light-1280x800 | 无 | 同上 |
| light-390x844 | 无 | 同上 |
| dark-1440x900 | 无 | 同上 |
| dark-1280x800 | 无 | 同上 |
| dark-390x844 | 无 | 同上 |

**英文界面 12/12 通过**：用页面内 `import('/src/i18n/index.ts') → setAppLanguage('en')` 切换（夹具启动时硬编码 `setAppLanguage("zh-CN")`，需运行期切换），24 个阶段横向溢出全部为 0；英文长文案（如 "Search knowledge bases by name"、"Filter scope"、"Updated"、"No matching knowledge bases"）在 390px 下未被裁切。唯一被裁切探针命中的元素是共享浮层关闭按钮的 `sr-only`「Close」文本（clientWidth 1px），属无障碍隐藏文本，是探针的误报，不是排版缺陷。

**纯键盘与 20 次开关（`wu05-keyboard-results.json`）**：

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| Tab 进入浮层 | 通过 | 焦点落到浮层内（关闭按钮） |
| Shift+Tab 回绕不逃逸 | 通过 | 回绕后焦点仍在浮层内 |
| focus-visible 可见 | 通过 | `outline-style: solid`、`outline-width: 2px` |
| Esc 关闭并归还焦点 | 通过 | 关闭后 activeElement 仍连接且文本 = 触发器原文 |
| 文档 Sheet 连续开关 20 次 | 通过 | 40 项采样中异常 0：无残留遮罩、无 `data-scroll-locked`、body 指针未锁、焦点始终回到「部署与回滚操作指南」 |
| 评测新建 Sheet 连续开关 20 次 | 通过 | 40 项采样中异常 0：焦点始终回到「新建评测」 |
| 快速切换 Tab 10 次 | 通过 | 期间新增请求 0 条，最终 Tab 归属正确 |
| 快速切换知识库 10 次 | 通过 | 11 次文档请求对应 10 次切换，最终归属 `kb-team` 且无串页 |

### 4.5 390px 横向溢出：定位与修复（真实缺陷）

首轮矩阵在 390px 的 `documents` / `document-sheet` 阶段测得 `clientWidth=390` 而 `scrollWidth=833`。DOM 量测定位到根因：

```text
DIV.h-fit.rounded-panel.border.border-border\/60.bg-background.p-4   width=794  right=833
└ DIV.knowledge-table-scroll   width=760  overflow-x=auto
  └ TABLE.knowledge-table.min-w-[760px]   width=760
```

该面板是 `<div className="grid gap-4">` 的 grid item，grid item 默认 `min-width:auto`，无法收缩到 `min-w-[760px]` 表格之下，于是外层被撑到 794px，`.knowledge-table-scroll` 的 `overflow-x:auto` 永远不需要生效，整页被撑破。给该 grid item 补 `min-w-0` 后重测：`scrollWidth` 由 833 降为 **390**，表格保留 760px 并在容器内横向滚动，符合共享 CSS 「表格局部横向滚动」的设计意图。修复后 zh-CN 与 en 两套矩阵在 390px 全部通过。

### 4.6 WU-06A 验收 runner 的复用与修正

直接跑仓库自带的 `acceptance-runner.mjs` 时有 3 条未通过，逐条核对后**全部属于 runner 自身缺陷，不是产品回归**：

| runner 检查 | 现象 | 判定 |
| --- | --- | --- |
| 深色矩阵（dark-1440x900-reduce 起 5 条） | 等不到「切换深色」按钮 | runner 用 hash 导航 `page.goto` 不会重载页面，夹具的深色开关状态残留，按钮名已变成「切换浅色」。每轮先 `about:blank` 再进入即可复现通过 |
| 文档 Sheet 20 次开关 | `Execution context was destroyed` | 同上，runner 上一检查结束后仍停留在库详情页，`open()` 未重置视图 |
| 评测 Sheet 键盘焦点 | Esc 后浮层未隐藏 | 同上状态残留。**单独隔离复现**：Esc 正常关闭、遮罩清零、滚动锁释放、焦点回到「新建评测」 |
| 空库/筛选/请求失败可区分 | 等不到名为「搜索」的按钮 | runner 选择器写错：实际按钮文案是 `knowledge.search` =「检索」 |
| 处理中/失败重试 | 等不到名为「重建索引」的按钮 | runner 选择器写错：实际可访问名是 `knowledge.reindex` =「重建」 |

修正 runner 的 `open()` 语义（在临时副本上改，未改仓库文件）后重跑，10 条检查中通过 7 条，剩余 2 条为上表的选择器写错，1 条为状态残留，均已定位到具体字符串。

## 5. 矩阵自检结论汇总

| 检查项 | 结论 |
| --- | --- |
| 深色模式 | 通过（zh-CN 与 en 各 12 组合无横向溢出） |
| 浅色模式 | 通过（同上） |
| 窄屏 390px | 通过（修复 1 处 `min-w-0` 溢出后） |
| 桌面 1280 / 1440 | 通过 |
| `prefers-reduced-motion: reduce` | 通过（实测动画关闭：浮层 `none`/`0s`，遮罩 `none`） |
| 纯键盘：Tab 顺序 / 焦点可见 / Esc 关闭并归还焦点 | 通过 |
| 连续开关 20 次无残留遮罩 / 滚动锁 / 焦点卡死 | 通过（文档 Sheet 与评测 Sheet 各 20 次，异常 0） |
| 快速切换 Tab / 知识库 10 次无串页、无重复请求 | 通过（新增请求 0 条 / 11 次文档请求对应 10 次切换） |
| 英文文案排版 / 截断 / 溢出 | 通过（390px 下英文长文案未裁切） |

## 6. 未解项与交接事项

1. **测试与词条耦合（Leader 正在修复）**：新增真实英文词条后，`knowledge-page.test.tsx` / `knowledge-eval-page.test.tsx` 里硬编码中文可访问名的查找失配（此前 en 缺该键 → 回落中文 `defaultValue` → 测试能命中）。本 WU 未修改这两个文件，也未回退词条。Leader 已改为 `i18n.t("knowledge.experience.xxx")` 写法后 5 文件 90/90 全绿。本 WU 曾把「把硬编码中文名换成 `i18n.t(...)`」的完整补丁留在 `.ai-runtime-artifacts/verifications/knowledge-experience/wu05-i18n-coupled-test-patch.diff`，备查。
2. **整仓 `tsc -p tsconfig.json` 仍有 120 处既存报错**（`src/tests/**`、`import.meta.glob` 类型缺失等），本 WU 写集内 0 条；生产构建 `tsconfig.build.json` exit 0。未越界处理，建议留给整仓收口。
3. **夹具本身的局限（沿用 WU-06A 声明，本轮未扩）**：文档导入只创建 processing 占位、不解析文件；重索引停留 processing；新建运行的报告沿用固定样本。矩阵与行为结论只覆盖「界面与交互」，不构成检索/评测算法正确性的证据。
4. **未做的检查（如实标注）**：真实浏览器（非 headless Chrome）的人工视觉走查；1440 以上的超宽屏；多点触控与屏幕阅读器实机；`prefers-contrast` / 高对比度模式；i18n 键在「页面文本 → 无障碍树」上的逐语言朗读核对（en/es/fr/… 的实际朗读只做了溢出与裁切检查，未做 TTS 级校验）。
5. **给 WU-06 的事项**：5186 端口在我开始前已由他人占用（PID 79868，17:06 启动，用同一份 `verifications/knowledge-experience/vite.config.ts`，服务的正是本 worktree 代码）。我尝试启动时被 "Port 5186 is already in use" 挡回，从未占用或重启该端口；我的 Chrome 实例已全部退出，可直接接管验收。
6. **建议 WU-06 复用的产物**：`wu05-i18n-parity-check.mjs`（键集合校验）、`wu05-probes/`（6 个探针）、`wu05-matrix-zhCN-results.json` / `wu05-matrix-en-results.json` / `wu05-keyboard-results.json`。

## 7. Skills 使用

| Skill | 路径 | 状态 | 实际应用 |
| --- | --- | --- | --- |
| source-driven-development | `.claude/skills/source-driven-development/SKILL.md` | loaded | Step 0：改码前先读计划、接口契约与 WU-01/02/03/04 四份执行日志，确认共享层冻结导出与各 WU 写集边界，再动手 |
| frontend-ui-engineering | `C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/frontend-ui-engineering/SKILL.md` | loaded | 响应式断点实测（390/1280/1440）、`focus-visible` 焦点可见性、reduced-motion、长文案溢出与截断检查；按「真实内容暴露溢出问题」的原则用夹具真实长标题/长英文文案压测 |
| verification-before-completion | `.claude/skills/verification-before-completion/SKILL.md` | loaded | 每条结论都附新鲜命令输出与退出码；未做的检查一律进「未解项」，不宣称已验收 |

参考的官方资料（用于判定，不是新增依赖）：Radix UI Dialog 动画与 Presence（动画节点须为 Portal 直接子元素，退出动画结束才卸载）、W3C ARIA APG Button 键盘约定、MDN `prefers-reduced-motion`。本轮未引入任何新依赖，未改动 `nanobot/**`。

### 自查

- self_check: PASS；wu_status: done；done_criteria_met: 是（46 键 × 10 语言齐备且 JSON 可解析并通过一致性校验脚本；6 处浮层改为专用组合且行为测试 90/90 全绿；写集内 tsc 与 lint 无新增错误；报告已如实标注未做项）。
- 未 commit / 未 push / 未新建分支 / 未派发子 agent；未修改 `ui/sheet`、`ui/dialog`、`GraphPanel`、`GraphView`、`lib/api.ts`、`knowledge-eval-api.ts`、后端与其它测试文件（后两者经 SHA256 校验与 `git diff` 确认未被本 WU 写入）。