# HANDOFF — 阶段 A 收口 → 阶段 B 启动

> 上下文重置检查点（覆盖写）。**给阶段 B 的 Leader / Worker 看的，不是给用户看的。**

## 1. 当前 git 状态

- 工作分支：`feature/desktop-app`（本地领先 `origin/feature/desktop-app` 共 17 个 commit：1 个 artifacts 落盘 + 16 个 A 落码）
- 工作树：
  - `.harness-worktrees/Zhixu/wt-writing-phase-a`（A1-A3.5，已合并到 desktop-app，可选 `git worktree remove`）
  - `.harness-worktrees/Zhixu/wt-writing-phase-a-2`（A3.6-A3.8 + FIX-02 + 收口，**已合并到 desktop-app，可选 `git worktree remove`**）
- 下批 base：`feature/desktop-app` HEAD = `59697e8`（含 2 个 merge commit）
- 16 个落码 commit（按时间序）：
  ```
  e6db1db feat(writing): 新增写作域 SQLite 存储与 schema v1
  afa1722 feat(writing): 文章字段规范化、上限与内容摘要
  74b9675 feat(writing): 文章快照仓储支持版本 CAS、摘要短路与请求幂等
  d0f0153 feat(writing): 写作域 services 与 payload 层
  e68f883 feat(webui): 写作域 action handler 与契约错误口径
  342dd89 feat(webui): 写作域接入 settings 路由、WS-only 门禁与 gateway 装配
  d1a3eb4 test(config): 暴露 get_writing_dir 并锁定写作库路径
  aec3667 fix(writing): 上限判定收归 models 单一出口并统一库路径推导
  d5b775b refactor(creative): 文章状态与素材类型改为英文枚举并抽出写作模板
  9716e5f feat(webui): 写作域读写 API 封装、冲突解析与本地恢复稿缓存
  c52892a refactor(creative): 拆分服务端 revision 与本地编辑计数
  8131e01 feat(creative): 创作空间接入真实写作 API 与保存状态机
  67b86be feat(creative): 正文编辑三模式与中文输入法安全
  a62ce29 feat(creative): 素材与提纲面板对齐持久化字段
  efe23aa feat(creative): 文章版本冲突的三条处理路径
  9ffdc26 feat(creative): 阶段 A3 收尾：恢复条、Markdown 导出、删除/复制与晚到请求防护
  6761cda chore(creative): 清理 A3 引入的未用 import / 变量
  3a5f4eb merge: 知序写作能力阶段 A 落地（A1+A2+A3.1-A3.5，A3.6-A3.8/A4 见交接文档）
  8f27532 merge: 知序写作能力阶段 A 收口（A3.6+A3.7+A3.8）
  59697e8 merge: 知序写作能力阶段 A TS 清理与 build 收口
  ```

## 2. 阶段 A 的关键交付（可被 B 直接使用）

### 2.1 后端

- 五个 Python 文件：`nanobot/writing/{__init__,database,models,repository}.py`、`nanobot/config/paths.py::get_writing_dir`
- 三个 webui 文件：`nanobot/webui/writing_{services,api,routes}.py`
- 接线点：`nanobot/webui/{settings_routes,ws_http,gateway_services}.py`（A1.6 改了）
- 6 个 WS mutation action（点号双口径）：
  - 读：`article.list` / `article.get`（HTTP 也允许走 `GET /api/writing/articles` 与 `GET /api/writing/articles/get`）
  - 写：`article.create` / `article.save` / `article.duplicate` / `article.delete`（仅 WS，HTTP 直连 405）
- 6 个契约 §6 错误 message 逐字实现；六条 §5.3 上限 message 收归 `models.py` 单一出口
- `client_request_id` 业务幂等键：`<action>[:<article_id>][:<payload 指纹>]`

### 2.2 前端

- 三个 lib 文件：`webui/src/lib/{writing-api,writing-draft-cache,writing-export}.ts`
- 一个 hook：`webui/src/components/creative/use-article-save-flow.ts`（含 `applyRecovery` / `discardRecovery` / `storageHealthy` / `tryLeave` / `forceConflict`）
- 5 个 creative 组件（ArticleList / ArticleEditor / MaterialPanel / OutlinePanel / CreativeWorkspace） + 2 个对话框（ConflictDialog / DeleteConfirm 范式）
- 一个 new 组件：`ArticleRecoveryBanner.tsx`
- 类型：英文枚举 + `revision` / `updatedAt` 字段 + `ARTICLE_DELETED_REVISION = -1` 哨兵
- 10 份 locale 全部更新（i18n parity 绿）

### 2.3 类型 / 接口（给 B 端接线的最短路径）

```ts
// webui/src/lib/writing-api.ts
type ArticleStatus = "idea" | "draft" | "review" | "done";
type MaterialType = "note" | "text" | "code" | "link";
interface Article { id: string; revision: number; updatedAt: string; /* ... */ }
interface ArticleSnapshotPayload { title; writing_type; template_id?; status; summary; brief; body; outline; materials; tags }
interface CreateArticleRequest { clientRequestId; title; templateId?; writingType?; summary?; brief?; outline?; materials?; tags? }
interface SaveArticleRequest { clientRequestId; articleId; baseRevision; snapshot }
interface SaveArticleResult { article_id; revision; updated_at; digest; no_change }
interface ArticleConflict { currentRevision: number; articleId: string }

// webui/src/components/creative/use-article-save-flow.ts
type SavePhase = "saved" | "dirty" | "saving" | "failed" | "conflict" | "offline";
interface SaveFlowState { phase: SavePhase; baseRevision: number; localEditSeq: number; lastSavedAt: string | null; lastError: string | null; conflict: ArticleConflict | null }
const AUTOSAVE_DEBOUNCE_MS = 1500;
const ARTICLE_DELETED_REVISION = -1;
function mergeImeValue(original: string, previousRendered: string, current: string): string;
function useArticleSaveFlow(options: {
  getToken: () => string;
  transport: WebUIMutationTransport;
  article: Article;
  workspacePath: string;
}): {
  draft: ArticleSnapshotPayload; dirty: boolean; state: SaveFlowState;
  storageHealthy: boolean;
  setField: <K extends keyof ArticleSnapshotPayload>(key: K, value: ArticleSnapshotPayload[K]) => void;
  saveNow: () => Promise<void>;
  acknowledgeConflict: (resolution: "reload" | "savedCopy" | "discardLocal") => Promise<void>;
  applyRecovery: (snapshot: ArticleSnapshotPayload) => void;
  discardRecovery: () => void;
  forceConflict: (conflict: ArticleConflict) => void;
  tryLeave: () => Promise<LeaveDecision>;
};
```

## 3. 阶段 B 启动前必做（来自执行日志 §4）

1. **SPEC 修订**：在 spec §5.3 给 `brief`（goal/audience/style）加上限，沿用 `title is too long` / `summary is too long` 同族 message；改 `_brief` 为拒绝而非截断。
2. **代码前置**：`use-article-save-flow.ts::tryLeave` 的 `saving` 分支加 timeout fallback（让 A3.8 「late-arriving save」用例能真在飞验证 abandonedRef）。
3. **基线清理**：`nanobot/cli/gateway_runtime.py` 11 条 `I001` import 排序修掉（约 30 分钟）。
4. **基线补跑**：`cd webui && npm run lint`（plan A4.1 漏跑）；`npx vitest run` 跑一次确认 4 个 pre-existing 失败用例仍是原状（不是 A 引入的回归）。
5. **worktree 清理**：`git worktree remove .harness-worktrees/Zhixu/wt-writing-phase-a .harness-worktrees/Zhixu/wt-writing-phase-a-2`。

## 4. 阶段 B 的 dispatch 需要重审的点（避免与 A 重复造轮子）

- B 计划中所有提到「前端要新加 X 状态机 / 缓存 / 编辑器」的位置，**先 grep 阶段 A 的 `use-article-save-flow.ts` 与 `webui/src/lib/`**，确认是否已有可用出口。
- B 计划的「ArticleEditor 接 server 真实 API」已经由 A 阶段 A3.1-A3.3 完成；B 只需扩 `useArticleSaveFlow` 的方法，不要重写 ArticleEditor。
- B 计划的素材 B-08 与 SPEC §A-05 字段对齐已在 A3.4 完成（稳定 id + http(s) URL + outline order）；B 如需扩字段，先动契约。
- B 的协作仓 `nanobot/writing/collab/` 不与 A 阶段 `nanobot/writing/{database,models,repository}.py` 抢文件；A 端 B1.6 已声明「`save_snapshot` 拆出 `save_snapshot_on` 供 B 复用」（在 FIX-01 偏差 1 的 comment 里），B 工作前 grep 一次。

## 5. 阶段 B 的基线命令（开工作树后第一步）

```bash
# 工作树
git worktree add -b harness/wt-writing-phase-b \
  /d/studyspace/project/.harness-worktrees/Zhixu/wt-writing-phase-b feature/desktop-app

# 进工作树后第一件事
cd /d/studyspace/project/.harness-worktrees/Zhixu/wt-writing-phase-b

# 跑 A 的回归面确认 base 没有坏
python -m pytest tests/writing tests/webui tests/test_writing_paths.py -q
cd webui && npx vitest run src/tests/creative-workspace.test.tsx \
  src/tests/creative-recovery.test.tsx src/tests/writing-export.test.ts \
  src/tests/article-save-flow.test.ts src/tests/i18n.test.tsx && cd ..

# 跑全量门禁（取 A4 一次通过的口径）
ruff check nanobot/
npm --prefix webui run build
```

## 6. 引用

- 阶段 A 计划：`.ai-runtime-artifacts/plans/2026-10-12-zhixu-writing-phase-a-plan.md`
- 阶段 A 调度：`.ai-runtime-artifacts/plans/2026-10-12-zhixu-writing-phase-a-dispatch.md`
- 阶段 A 集体测试：`.ai-runtime-artifacts/verifications/2026-10-12-zhixu-writing-phase-a-collective-test.md`
- 阶段 A 代码审查：`.ai-runtime-artifacts/reviews/2026-10-12-zhixu-writing-phase-a-code-review.md`
- 阶段 A 安全审查：`.ai-runtime-artifacts/reviews/2026-10-12-zhixu-writing-phase-a-security-review.md`
- 阶段 A 执行日志：`.ai-runtime-artifacts/execution-logs/2026-10-12-zhixu-writing-phase-a-execution-log.md`
- 阶段 A 追踪：`.ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-12-zhixu-writing-phase-a.md`
- 阶段 B 计划：`.ai-runtime-artifacts/plans/2026-10-12-zhixu-writing-phase-b-plan.md`
- 阶段 B 调度：`.ai-runtime-artifacts/plans/2026-10-12-zhixu-writing-phase-b-dispatch.md`
- 阶段 B 契约：`.ai-runtime-artifacts/contracts/2026-10-12-contract-writing-collaboration.md`
