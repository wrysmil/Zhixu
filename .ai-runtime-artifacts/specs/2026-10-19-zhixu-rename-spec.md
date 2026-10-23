---
artifact: spec
route: superpowers:brainstorming
skills: [brainstorming, source-driven-development, api-and-interface-design]
skills_evidence:
  - .claude/skills/brainstorming/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/api-and-interface-design/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - research/2026-10-19-nanobot-rename-scope-research.md
  - stack/2026-10-19-zhixu-rename-stack.md
  - contracts/2026-10-19-contract-zhixu-rename-mapping.md
  - decisions/2026-10-09-mira-rebrand-scope.md
  - specs/2026-09-30-brand-rebrand-spec.md
created_at: 2026-10-19
status: draft
approved: false
---

# 方案：nanobot → zhixu 全仓改名（三期）

## 1. 背景与决策沿革

仓内已有两份相关产物，都**刻意**把引擎改名排除在外：

| 产物 | 结论 |
| --- | --- |
| `specs/2026-09-30-brand-rebrand-spec.md` | 换品牌层 / 界面文案 / CLI 命令名；**不换** Python 包目录、数据目录、环境变量 |
| `decisions/2026-10-09-mira-rebrand-scope.md` | 「Mira 品牌重塑范围 = 用户可见层，不改 Python 包名」；末句写明「未来若做引擎改名，另立 spec」 |

**本 spec 就是那份「另立的 spec」**，补做被刻意留下的引擎改名，并额外把提示词层纳入统一口径。

现状割裂：UI 标题、桌面端 `productName`、仓库目录已是「知序 / Zhixu」，但 README、Python 包、CLI、提示词全是 nanobot。

## 2. 目标

把仓内所有 `nanobot` 标识符统一改为 `zhixu`，覆盖**包**（Python 包 / npm 包 / PyPI 分发名）与**提示词**（系统提示词模板 / skill 文档 / CLI 提示串 / i18n 文案），同时**不破坏**已有部署的数据、环境变量与客户端协议。

## 3. 非目标

- ❌ 不做任何功能变更、性能优化或无关重构（brainstorming skill 明确要求）。
- ❌ 不调整 ruff / basedpyright / pytest / Vitest 的任何质量门禁。
- ❌ 不引入新依赖或新包管理器。
- ❌ 不改 `.ai-runtime-artifacts/` 的 230 个历史产物（改会破坏与已归档 spec/plan 的引用一致性）。
- ❌ 不改向第三方服务自报客户端身份的 8 处标识符（§2.8 of contract）。
- ❌ 不改 Docker 系统用户 `nanobot`（§4.3 of contract）。

## 4. 现状事实（来自调研产物）

| 指标 | 数值 |
| --- | --- |
| 命中文件 | 1429 |
| 命中行 | 14278 |
| 变体 | `nanobot` 14300 / `Nanobot` 741 / `NANOBOT` 307 / `NanoBot` 2 |
| Python 中纯 import 行 | 4887（866 文件）→ 占 Python 命中的 74% |

## 5. 命名裁决

新名字 = **`zhixu`**（用户 2026-10-19 确认）。

PyPI 占用核查：`https://pypi.org/pypi/zhixu/json` 与 `https://pypi.org/pypi/zhixu-ai/json` 均返回 404 → **两个名字均未被占用，可注册**。

界面文案用中文「知序」，标识符一律用 ASCII 小写 `zhixu`。

完整映射表见 **`contracts/2026-10-19-contract-zhixu-rename-mapping.md`**，本期三份 spec 共享该表为唯一真相源。

## 6. 三期拆分

按**兼容风险**而非工作量拆分。每一期独立可发布、独立可回滚。

| 期 | 名称 | 风险 | 兼容负担 | 触及标识符 |
| --- | --- | :---: | --- | --- |
| **Phase 1** | 文案与提示词 | 🟢 低 | 无 | 自然语言 + 文件名 + 品牌资产 |
| **Phase 2** | 前端与协议 | 🟠 中 | 双读 3 处 | 前端符号、HTTP 路由、npm 包名、构建路径 |
| **Phase 3** | 引擎与数据 | 🔴 高 | 迁移 1 处 + 回退 4 处 | Python 包、CLI、数据目录、环境变量 |

**依赖顺序严格串行 `1 → 2 → 3`**。Phase 2 必须在 Phase 3 之前，因为前端 glob（`webui/src/channel-plugins/registry.ts:25` 的硬编码正则 `nanobot\/channels\/([^/]+)\/`）依赖 Python 包目录名——Phase 2 先把前端符号理顺，Phase 3 再动包目录，避免两期在同一个正则上打架。

每期的详细设计、文件清单、验收命令分别见：

- Phase 1 → `specs/2026-10-19-zhixu-rename-phase1-low-spec.md`
- Phase 2 → `specs/2026-10-19-zhixu-rename-phase2-medium-spec.md`
- Phase 3 → `specs/2026-10-19-zhixu-rename-phase3-high-spec.md`

## 7. 方案对比与选型

brainstorming skill 要求对关键取舍给出 2–3 个方案。本轮有 3 个真正的决策点：

### 7.1 数据目录 `~/.nanobot` 的处理

| 方案 | 做法 | 评价 |
| --- | --- | --- |
| **A. 只改品牌，目录不动** | `~/.nanobot` 保持原样 | 用户零风险，但改名不彻底；`rg -i nanobot` 永远有残留，Phase 3 的「残留自检」无法收敛 |
| **B. 改 + 自动迁移 + 回退（**选中**） | 启动时检测旧目录 → `rename`，失败退化 `copytree`；旧目录读取保留 ≥1 个大版本 | 兼顾彻底与安全。已有抓手：`config/paths.py:20` `get_data_dir()` 已等于配置文件所在目录，`--config` 早已能整体搬迁 |
| C. 改 + 提供独立迁移 CLI | `zhixu migrate-data` 子命令 | 更显式，但多一个用户要记的命令；且忘记执行就等于数据丢失 |

**选 B。** C 的问题在于「需要用户主动执行」本身就是失败模式——绝大多数用户不会执行，静默变成空环境。

### 7.2 环境变量 `NANOBOT_*` 的处理

仓内 **131 处** `os.environ` / `os.getenv` 调用，**无中心访问器**。

| 方案 | 做法 | 评价 |
| --- | --- | --- |
| A. 逐点加 fallback | 131 处改成 `os.environ.get("ZHIXU_X") or os.environ.get("NANOBOT_X")` | 正确但改动面巨大，每处都引入分支，review 噪音高 |
| **B. 启动期归一（**选中**） | 4 个进程入口调用同一个 `normalize_legacy_env()`，把旧名映射进新名；读取点只改字面量 | 读取点零分支，新增逻辑集中在一处、可单测。唯一例外是构建期的 `hatch_build.py` / `Dockerfile`（不经过 Python 入口），计 2 处 |
| C. 强制用户改配置 | 直接硬切，文档写清楚 | 50 个变量静默失效（`loader.py:68` 只在 JSON 解析失败时才提示 `NANOBOT_*`，变量名拼错不报错），最难排查 |

**选 B。** 依据 Hyrum's Law——环境变量名是可观察行为，一定有人依赖。

### 7.3 SQLite 旧 key 的处理

| 方案 | 做法 | 评价 |
| --- | --- | --- |
| A. 一次性迁移脚本 | 扫描所有 session DB 改写 key | 要停机、有失败态、DB 文件可能正在被写 |
| **B. 惰性回退（**选中**） | 写新 key，读时缺失回退旧 key，首次读到即回写 | 无停机、O(1) 成本、天然幂等。代价是双查分支，但只有 4 个 key |
| C. 硬切 | 直接改名 | 老会话的模型预设/推理强度静默重置，用户不易察觉 |

**选 B。**

## 8. 设计要点（跨期共用）

1. **替换脚本必须区分大小写**，按 `NANOBOT_` → `NanoBot` → `Nanobot` → `nanobot` 四步顺序执行，最后一步把「不改清单」改回来。单条 `sed s/nanobot/zhixu/g` 会把 `NANOBOT` 变成 `zhixu`（错）、`Nanobot` 变成 `zhixu`（错）。
2. **`_NANOBOT_COMPLETE` 不改**——typer shell 补全的约定变量名。
3. **弃用告警**：兼容层命中旧标识符时打一次 warning，含旧名与替换建议，不刷屏。
4. **兼容期 ≥ 1 个大版本**，三期的兼容代码都在同一版本下线，不分期。
5. **每期独立提交、独立可回滚**；三期之间不合并 commit。
6. **残留自检**统一用契约 §6 的断言，且 `.ai-runtime-artifacts/` 明确排除。

## 9. 验收（三期共用 + 各自追加）

共用：

```bash
rg -i nanobot --glob '!.git/**' --glob '!.harness-worktrees/**' \
   --glob '!.venv/**' --glob '!**/node_modules/**' \
   --glob '!.ai-runtime-artifacts/**'
# 残留必须逐条归入契约 §6 的七类之一
```

各期追加见对应 phase spec。

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| `registry.ts` 硬编码正则漏改 | 渠道面板**静默消失**，不报错 | Phase 3 验收项：启 gateway 后逐个渠道确认面板可见 |
| 构建五连漏改 | wheel 缺 WebUI / Docker 构建失败 | `stack/2026-10-19-zhixu-rename-stack.md` §「构建耦合点」逐条勾 |
| `conftest.py` monkeypatch 字符串漏改 | 测试写进真实 home 目录 | 验收项：pytest 跑完检查 `~/.nanobot`/`~/.zhixu` 无新增文件 |
| bun 不在 PATH | Phase 2 无法重建 webui dist | `stack` 已标注为**执行前置**；提前确认或改用 npm 等价命令 |
| `.claude/settings.json` 漏改 | 后续 AI 会话大量命令被拒 | 列入 Phase 1 清单（它是配置不是代码，成本极低） |
| 数据迁移在跨设备场景失败 | 数据看似丢失 | `rename` 失败自动退化 `copytree` + 留标记文件；迁移函数单测覆盖两种路径 |
| 已装 `nanobot-ai` 的环境不自动升级 | 用户停留在旧版 | Phase 3 追加发一个只改名的占位包 |

## 11. Spec 自检

- **占位符**：无 TBD / TODO，三期范围与验收命令均已具体到文件与行号。
- **内部一致**：三期的边界与契约 §5 的跨期边界表一致；Phase 2 不碰 Python 包目录、Phase 3 才动，与 §6 的依赖顺序一致。
- **歧义**：「双读」在契约 §4.1/§4.2/§4.8 均已给出「新优先、旧回退」的具体写法；「惰性回退」在 §4.6 给出代码形态。
- **范围**：三期可各自独立成 plan，无需再拆。
- **取舍**：§7 的三个决策点各给了 2–3 个方案并说明选型理由。

## 12. 待确认

1. 是否确认「兼容期 ≥ 1 个大版本」？当前 `pyproject.toml` 版本为 `0.3.0`。
2. Phase 3 是否需要提供回滚开关（如 `ZHIXU_NO_MIGRATE=1` 跳过数据迁移）？
3. 旧 PyPI 包 `nanobot-ai` 的占位包，是发在同一个包上（覆盖），还是新发 `zhixu-ai` 并让旧包只留说明？

## Next

**（写入后须暂停，等用户明确继续 — 见 `harness-kit/core/routing.md` § 阶段门禁）**

- 三份 phase spec 与契约均确认无误 → 说「写计划」或「制定实施计划」
- 只做某一期 → 说「实现 Phase 1」等（仍需先过该期 plan 门禁）
- 需要调整方案 → 直接说修改意见
