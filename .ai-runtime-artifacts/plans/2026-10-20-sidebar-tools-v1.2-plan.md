---
artifact: implementation-plan
route: writing-plans
skills: [writing-plans, source-driven-development, test-driven-development]
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
  - harness-kit/.agents/skills/test-driven-development/SKILL.md
source:
  - '用户请求：优化知序 WebUI 侧边栏的文件、终端、浏览器功能（前后端）'
  - '参考仓库：D:/studyspace/源码学习/deepseek-harness-master/deepseek-harness-master'
  - '现有实现：webui/src/components/dock、webui/src/components/workbench、nanobot/webui、desktop'
created_at: 2026-10-20
status: implemented
approved: true
dispatch: 2026-10-20-sidebar-tools-v1.2-dispatch.md
tier: 2
---

# 侧边栏文件 / 终端 / 浏览器 v1.2 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `orchestration`（推荐）或逐任务执行本计划。步骤使用 `- [ ]` 复选框跟踪。每个工作单元开始前必须先读对应文件当前内容，禁止按行号盲改。

**Goal:** 在不改变现有 dock 信息架构的前提下，补齐文件树的可控可见性/搜索、终端的重连回放/复制粘贴/后台保活、浏览器的地址补全/状态持久化/桌面独立窗口，并让文件/浏览器标签在刷新后恢复。

**Architecture:** 后端保持只读 HTTP + WebSocket 事件模型：文件搜索新增有界、无符号链接、带截止时间的同步遍历；终端在两条 bridge（PTY / 管道）内各维护一份 64 KiB UTF-8 环形尾巴，接管时先重绑 publisher 再补发 `terminal_snapshot`。前端把 dock 面板改为“全部挂载 + 绝对定位叠放 + `invisible`”的 keep-alive，终端用 `viewEpoch` 驱动全量重绘；文件/浏览器标签持久化到 `sessionStorage`，终端仍走既有接管条语义。

**Tech Stack:** Python 3.11 / asyncio / pytest / ruff；React 18 + TypeScript + Vite + Vitest + happy-dom；xterm 6 + addon-fit；Electron 33（`contextIsolation` / `sandbox`）。

**TDD Required:** YES。每个工作单元先写失败测试，确认 RED，再写最小实现，确认 GREEN，最后提交。

---

## 1. 调研结论（参考仓库）

参考仓库根目录实际多嵌套一层：`D:/studyspace/源码学习/deepseek-harness-master/deepseek-harness-master/`。

| 参考点 | 参考文件 | 可借鉴结论 |
| --- | --- | --- |
| 终端有界回放 | `packages/terminal/terminal-bash/src/session.ts` | `BoundedTextBuffer` 只保留尾部字节，`snapshot(): {text, truncated}`；UTF-8 截断后要丢掉头部续字节，避免首字符乱码。 |
| 终端分页读取契约 | `packages/terminal/terminal/src/types.ts` | `TerminalReadResult` 明确区分“内容 + 是否截断”，重连不应无限重放。 |
| 隐藏文件开关 | `packages/client/ui-directory-picker-browse/src/client/DirectoryBrowser.tsx` | `showHidden` 是显式开关；默认隐藏点文件，开启后完整展示（含 `.git` / `node_modules`），并对隐藏项做弱化标记。 |
| 内容搜索 | `packages/client/ui-workspace/src/client/WorkspaceBrowser.tsx` | 250 ms 防抖、查询长度上限、扁平结果列表；搜索必须与树浏览分离，避免污染展开状态。 |
| 内嵌浏览器 | 参考仓库无对应 UI | 浏览器部分为知序既有能力，按本项目安全边界独立设计，不宣称有参考实现。 |

**现状关键事实：**

- `webui/src/components/dock/StartDock.tsx` 只渲染 `activeTab`，切走即卸载 `TerminalSurface` / `DockFileTab`，终端输出虽在 store 内，但 xterm 实例和滚动位置丢失。
- 终端 store 已有 200k 字符 / 4000 行前端上限，但刷新或跨连接接管时后端不回放任何历史。
- 文件树当前只隐藏 `.git` 与 `node_modules`，`.env` 等点文件反而暴露；没有刷新、折叠、搜索。
- 浏览器历史只存在组件内存；地址栏不接受裸域名；桌面端只能“外部浏览器打开”，不能独立窗口。
- `zhixu-client.ts` 的 `TERMINAL_EVENT_NAMES` 是白名单，新增 `terminal_snapshot` 必须同步加入，否则帧会被丢弃。

---

## 2. 范围

### 2.1 本期目标

1. **文件**：默认隐藏所有点文件 + `node_modules`；`show_hidden=1` 完整展示并标记 `hidden`；工具栏刷新 / 折叠 / 搜索；搜索覆盖文件名与文本内容，2 s 截止、最多 100 条；预览保持只读。
2. **终端**：后端每终端保留 64 KiB / 32768 字符尾部；接管时补发 `terminal_snapshot`；前端支持清屏、复制（优先选区）、粘贴；非活动标签保持挂载且不抢 stdin/resize。
3. **浏览器**：支持 `example.com` / `localhost:5173` / `127.0.0.1:8765` 等裸地址；按 scope 持久化历史；桌面端新增受控独立窗口（无 preload、无 Node、无 webview）。
4. **dock 外壳**：文件/浏览器标签刷新后恢复；标签双击重命名、中键关闭；所有标签面板 keep-alive。
5. **i18n**：10 个 locale 同步新增 17 个 `dock.*` 键，parity 测试保持全绿。

### 2.2 明确非目标

- 不做文件写入、删除、重命名、上传。
- 不做终端多路复用、分屏、搜索终端历史。
- 不做浏览器书签管理、下载管理、代理设置、DevTools。
- 不把浏览器 iframe 改为 Electron `<webview>`；iframe 必须继续使用 `sandbox="allow-scripts allow-forms allow-popups"`，禁止 `allow-same-origin`。
- 不持久化终端标签本身（刷新后仍由接管条显式重绑），只持久化文件/浏览器标签。
- 不改动 `home` / session dock 的既有 scope 隔离规则。

---

## 3. 全局契约

### 3.1 文件 HTTP API

**目录列表（扩展）**

```
GET /api/sessions/{key}/files?path=<abs>&show_hidden=1
GET /api/workspace/files?scope=default&path=<abs>&show_hidden=1
```

- `show_hidden` 取值 `1/true/yes/on`（大小写不敏感）为真，缺省为假。
- 每个 entry 新增 `hidden: boolean`；`hidden` 仅表示名称隐藏语义，不代表不可访问。
- 默认隐藏规则：`name.startswith(".")` 或 `name in {"node_modules"}`。
- `show_hidden=1` 时全部返回，包括 `.git`、`node_modules`、`.env`。

**内容搜索（新增）**

```
GET /api/sessions/{key}/files/search?q=<query>&show_hidden=1
GET /api/workspace/files/search?scope=default&q=<query>&show_hidden=1
```

响应：

```json
{
  "query": "needle",
  "results": [
    {
      "name": "main.py",
      "path": "/abs/ws/src/main.py",
      "display_path": "src/main.py",
      "line": 12,
      "column": 8,
      "snippet": "print('needle')",
      "match": "needle",
      "hidden": false
    }
  ],
  "truncated": false,
  "scanned_files": 37
}
```

- 文件名命中：`line = 0`、`column = 0`、`snippet = match = name`。
- 内容命中：`line` / `column` 从 1 开始；`match` 是原始大小写子串。
- 上限：query 200 字符；100 条结果；2000 个文件；单文件 256 KiB；单文件 5 条；总截止 2.0 s。
- 跳过目录：`.git`、`.next`、`.turbo`、`.idea`、`.cache`、`node_modules`、`.venv`、`venv`、`__pycache__`、`dist`、`build`、`target`。
- 跳过符号链接、二进制文件（前 8192 字节含 `\x00`）、超限文件。
- 错误：空 query / 超长 → `400 {"error":"invalid_query"}`；无默认工作区 → `404 {"error":"no_default_workspace"}`。

### 3.2 终端 snapshot 事件

后端 → 前端：

```json
{
  "event": "terminal_snapshot",
  "type": "terminal_snapshot",
  "terminal_id": "term-1",
  "seq": 42,
  "data": "...tail...",
  "truncated": true
}
```

- `seq` 是发送该快照时的终端序号；前端若已有 `lastSeq > seq`，必须整帧丢弃，避免旧快照覆盖新输出。
- 快照只在接管（`already_running`）且尾部非空时发送；发送顺序在 publisher 重绑之后、`terminal_started` 返回之前。
- 快照内容统一按 `stdout` 处理（PTY 合流输出；管道桥快照可能混合 stdout/stderr，终端显示无需区分）。
- 前端收到后：`lines` 替换为单条 `{stream:"stdout", data}`，`lastSeq = max(lastSeq, seq)`，`viewEpoch += 1`，`historyTruncated = truncated`。

### 3.3 sessionStorage 键

| 键 | 内容 | 归属 |
| --- | --- | --- |
| `nanobot.dock.terminals.<scope>` | `[{id, shell}]`，既有接管清单 | 不变 |
| `nanobot.dock.tabs.<scope>` | `{tabs:[{id,kind,title,customTitle?}], activeId}`，仅 file/browser | 新增 |
| `zhixu.dock.browser.<scope>` | `{history:string[], index:number}` | 新增 |

所有读取都必须容错：JSON 损坏、字段类型错误、越界 index 一律回退为空状态，禁止抛异常。

### 3.4 Electron 独立浏览器窗口

- IPC：`mira:open-browser-window`，入参 URL，返回 boolean。
- 仅允许 `http:` / `https:`，长度 ≤ 4096，禁止 username/password。
- 独立窗口：`contextIsolation:true`、`nodeIntegration:false`、`sandbox:true`、`webviewTag:false`、**无 preload**。
- `setWindowOpenHandler` 一律 deny，合法 http(s) 交给系统浏览器；`will-navigate` 非 http(s) 阻止；`will-attach-webview` 阻止。
- 窗口复用：已存在则 `loadURL` + focus，不无限开窗。

---

## 4. 文件地图

| 工作单元 | 新建 | 修改 |
| --- | --- | --- |
| WU-01 文件后端 | — | `nanobot/webui/workspace_files.py`、`nanobot/webui/ws_http.py`、`tests/webui/test_workspace_files.py`、`tests/test_webui_workspace_files.py` |
| WU-02 终端后端 | `nanobot/webui/terminal_scrollback.py`、`tests/webui/test_terminal_scrollback.py` | `nanobot/webui/pty_terminal.py`、`nanobot/webui/terminal_bridge.py`、`nanobot/webui/outbound_wire.py`、`tests/test_webui_pty_terminal.py`、`tests/test_webui_terminal.py` |
| WU-03 文件前端 | `webui/src/components/dock/DockFileSearch.tsx` | `webui/src/lib/api.ts`、`webui/src/lib/types.ts`、`webui/src/components/workbench/WorkspaceFileTree.tsx`、`webui/src/components/dock/DockFileTab.tsx`、`webui/src/tests/dock-file-tab.test.tsx` |
| WU-04 终端前端 | — | `webui/src/lib/types.ts`、`webui/src/lib/zhixu-client.ts`、`webui/src/hooks/useZhixuStream.ts`、`webui/src/components/workbench/TerminalSurface.tsx`、`webui/src/lib/clipboard.ts`、`webui/src/tests/terminal-store.test.ts`、`webui/src/tests/terminal-surface.test.tsx`、`webui/src/tests/zhixu-client.test.ts` |
| WU-05 浏览器/桌面 | `desktop/browser-policy.cjs`、`desktop/browser-policy.test.cjs` | `webui/src/lib/browser-url.ts`、`webui/src/lib/runtime.ts`、`webui/src/components/workbench/BrowserSection.tsx`、`desktop/preload.cjs`、`desktop/main.cjs`、`desktop/package.json`、`webui/src/tests/browser-url.test.ts`、`webui/src/tests/browser-section.test.tsx` |
| WU-06 dock 外壳 | — | `webui/src/lib/dock-store.ts`、`webui/src/components/dock/StartDock.tsx`、`webui/src/tests/dock-store.test.ts`、`webui/src/tests/start-dock.test.tsx` |
| WU-07 i18n/验收 | — | 10 个 `webui/src/i18n/locales/*/common.json`、`webui/src/tests/i18n-dock-parity.test.ts`、验证产物 |

**写入冲突裁决：**

- `webui/src/lib/types.ts`：WU-03 先写文件搜索类型；WU-04 在 WU-03 合入后再写 `terminal_snapshot` 与 `TerminalRecord` 字段。两单元不得并行改同一文件。
- `webui/src/components/dock/StartDock.tsx`：只归 WU-06。
- `webui/src/lib/clipboard.ts`：只归 WU-04。
- `desktop/package.json`：只归 WU-05。

---

## 5. 执行顺序

```
WU-01 ─┐
WU-02 ─┼─> WU-03 ─> WU-04 ─┐
WU-05 ─┘                    ├─> WU-06 ─> WU-07
                            ┘
```

- WU-01 / WU-02 / WU-05 可并行。
- WU-03 依赖 WU-01 的接口真实可用。
- WU-04 依赖 WU-02 与 WU-03（`types.ts` 串行）。
- WU-06 依赖 WU-04 的 `TerminalSurface.active` 与 WU-05 的 `BrowserSection.scope`。
- WU-07 最后执行，统一跑全量验收。

---

## 6. 任务

### Task 1（WU-01）：文件后端——隐藏开关与有界搜索

「Harness：orchestration.dispatch」
Skills: test-driven-development@harness-kit/.agents/skills/test-driven-development/SKILL.md loaded

**Files:**
- Modify: `nanobot/webui/workspace_files.py`
- Modify: `nanobot/webui/ws_http.py`
- Test: `tests/webui/test_workspace_files.py`
- Test: `tests/test_webui_workspace_files.py`

- [ ] **Step 1：写纯函数失败测试**

在 `tests/webui/test_workspace_files.py` 顶部补充导入：

```python
from nanobot.webui.workspace_files import (
    MAX_SEARCH_FILE_BYTES,
    MAX_SEARCH_QUERY_CHARS,
    workspace_search_payload,
)
```

追加测试：

```python
def test_listing_hides_dotfiles_by_default_and_marks_hidden(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / ".env").write_text("SECRET=1", encoding="utf-8")
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)

    default = workspace_directory_payload(None, scope=scope)
    assert [entry["name"] for entry in default["entries"]] == ["src", "a.md", "b.txt"]

    revealed = workspace_directory_payload(None, scope=scope, show_hidden=True)
    by_name = {entry["name"]: entry for entry in revealed["entries"]}
    assert by_name[".env"]["hidden"] is True
    assert by_name[".git"]["hidden"] is True
    assert by_name["node_modules"]["hidden"] is True
    assert by_name["a.md"]["hidden"] is False


def test_search_reports_name_and_content_hits_but_skips_hidden(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / "src" / "main.py").write_text("first\nprint('needle')\n", encoding="utf-8")
    (workspace / ".env").write_text("needle=1", encoding="utf-8")
    (workspace / "needle.txt").write_text("name hit\n", encoding="utf-8")
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)

    payload = workspace_search_payload("needle", scope=scope)
    paths = {hit["display_path"].replace("\\", "/") for hit in payload["results"]}
    assert payload["query"] == "needle"
    assert payload["truncated"] is False
    assert payload["scanned_files"] >= 3
    assert {"src/main.py", "needle.txt"} <= paths
    assert ".env" not in paths

    content = next(
        hit for hit in payload["results"]
        if hit["display_path"].replace("\\", "/") == "src/main.py"
    )
    assert content["line"] == 2
    assert content["column"] == 8
    assert content["match"] == "needle"
    assert "needle" in content["snippet"]

    name = next(
        hit for hit in payload["results"]
        if hit["display_path"].replace("\\", "/") == "needle.txt"
    )
    assert (name["line"], name["column"]) == (0, 0)
    assert name["match"] == "needle.txt"

    revealed = workspace_search_payload("needle", scope=scope, show_hidden=True)
    assert any(
        hit["display_path"].replace("\\", "/") == ".env" for hit in revealed["results"]
    )


def test_search_rejects_empty_and_oversized_queries(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)

    with pytest.raises(WebUIFilePreviewError) as empty:
        workspace_search_payload("   ", scope=scope)
    assert empty.value.status == 400

    with pytest.raises(WebUIFilePreviewError) as oversized:
        workspace_search_payload("x" * (MAX_SEARCH_QUERY_CHARS + 1), scope=scope)
    assert oversized.value.status == 400


def test_search_skips_binary_and_oversized_files(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / "blob.bin").write_bytes(b"needle\x00binary")
    (workspace / "huge.txt").write_bytes(b"needle\n" + b"x" * MAX_SEARCH_FILE_BYTES)
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)

    payload = workspace_search_payload("needle", scope=scope)

    paths = {hit["display_path"].replace("\\", "/") for hit in payload["results"]}
    assert "blob.bin" not in paths
    assert "huge.txt" not in paths
```

- [ ] **Step 2：运行并确认 RED**

```bash
.venv/Scripts/python.exe -m pytest tests/webui/test_workspace_files.py -q
```

Expected: `ImportError: cannot import name 'workspace_search_payload'`；若导入先报错，属预期。

- [ ] **Step 3：实现 `workspace_files.py`**

把文件改成以下内容（保留原有 `resolve_workspace_anchor` 语义）：

```python
"""Workspace-scoped directory listing and bounded text search for the WebUI."""

from __future__ import annotations

import os
import time
from collections import deque
from pathlib import Path
from typing import Any

from nanobot.config.paths import get_media_dir
from nanobot.security.workspace_access import WorkspaceScope
from nanobot.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path
from nanobot.webui.file_preview import (
    WebUIFilePreviewError,
    _clean_preview_path,
    _display_path,
)

MAX_WORKSPACE_LIST_ENTRIES = 400

HIDDEN_ENTRY_NAMES = frozenset({"node_modules"})
SEARCH_SKIP_DIR_NAMES = frozenset({
    ".git", ".next", ".turbo", ".idea", ".cache",
    "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", "target",
})

MAX_SEARCH_QUERY_CHARS = 200
MAX_SEARCH_RESULTS = 100
MAX_SEARCH_FILES = 2000
MAX_SEARCH_FILE_BYTES = 256 * 1024
MAX_SEARCH_SNIPPET_CHARS = 240
MAX_SEARCH_MATCHES_PER_FILE = 5
SEARCH_DEADLINE_S = 2.0

WORKSPACE_DEFAULT_SCOPE_PARAM = "default"


def is_hidden_entry_name(name: str) -> bool:
    """Dot-prefixed names and dependency noise are hidden by default."""
    return name in HIDDEN_ENTRY_NAMES or name.startswith(".")


def resolve_workspace_anchor(
    scope_param: str | None,
    *,
    default_scope: WorkspaceScope,
) -> WorkspaceScope | None:
    requested = (scope_param or WORKSPACE_DEFAULT_SCOPE_PARAM).strip().lower()
    if requested != WORKSPACE_DEFAULT_SCOPE_PARAM:
        return None
    if not default_scope.project_path.is_dir():
        return None
    return default_scope


def workspace_directory_payload(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
    show_hidden: bool = False,
) -> dict[str, Any]:
    """List one directory allowed by the session workspace scope (directories first)."""

    resolved = _resolve_directory_path(raw_path, scope=scope)

    try:
        with os.scandir(resolved) as scan:
            scanned = list(scan)
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to read directory") from e

    scanned.sort(key=lambda entry: (not entry.is_dir(), entry.name.lower()))

    entries: list[dict[str, Any]] = []
    truncated = False
    for entry in scanned:
        hidden = is_hidden_entry_name(entry.name)
        if hidden and not show_hidden:
            continue
        if len(entries) >= MAX_WORKSPACE_LIST_ENTRIES:
            truncated = True
            break
        try:
            is_dir = entry.is_dir()
            size = 0 if is_dir else entry.stat().st_size
        except OSError:
            continue
        entry_path = Path(entry.path)
        entries.append(
            {
                "name": entry.name,
                "path": str(entry_path),
                "display_path": _display_path(entry_path, scope.project_path),
                "is_dir": is_dir,
                "size": size,
                "hidden": hidden,
            }
        )

    return {
        "path": str(resolved),
        "display_path": _display_path(resolved, scope.project_path),
        "project_path": str(scope.project_path),
        "parent_path": _parent_path(resolved, scope.project_path),
        "entries": entries,
        "truncated": truncated,
    }


def workspace_search_payload(
    raw_query: str | None,
    *,
    scope: WorkspaceScope,
    show_hidden: bool = False,
) -> dict[str, Any]:
    """Search file names and UTF-8 text under the scoped workspace, bounded."""

    query = (raw_query or "").strip()
    if not query:
        raise WebUIFilePreviewError(400, "query is required")
    if len(query) > MAX_SEARCH_QUERY_CHARS:
        raise WebUIFilePreviewError(400, "query is too long")

    root = scope.project_path
    if not root.is_dir():
        raise WebUIFilePreviewError(404, "workspace not found")

    needle = query.casefold()
    deadline = time.monotonic() + SEARCH_DEADLINE_S
    results: list[dict[str, Any]] = []
    scanned_files = 0
    truncated = False
    queue: deque[Path] = deque([root])

    while queue and not truncated:
        directory = queue.popleft()
        if time.monotonic() >= deadline:
            truncated = True
            break
        try:
            with os.scandir(directory) as scan:
                entries = sorted(scan, key=lambda item: item.name.casefold())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.name in SEARCH_SKIP_DIR_NAMES:
                continue
            hidden = is_hidden_entry_name(entry.name)
            if hidden and not show_hidden:
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                queue.append(Path(entry.path))
                continue
            if scanned_files >= MAX_SEARCH_FILES:
                truncated = True
                break
            scanned_files += 1
            entry_path = Path(entry.path)

            if needle in entry.name.casefold():
                results.append(
                    {
                        "name": entry.name,
                        "path": str(entry_path),
                        "display_path": _display_path(entry_path, root),
                        "line": 0,
                        "column": 0,
                        "snippet": entry.name,
                        "match": entry.name,
                        "hidden": hidden,
                    }
                )
                if len(results) >= MAX_SEARCH_RESULTS:
                    truncated = True
                    break

            file_hits, timed_out = _search_file(
                entry_path, needle, query, hidden, root, deadline
            )
            results.extend(file_hits)
            if timed_out or len(results) >= MAX_SEARCH_RESULTS:
                truncated = True
                break

    return {
        "query": query,
        "results": results[:MAX_SEARCH_RESULTS],
        "truncated": truncated,
        "scanned_files": scanned_files,
    }


def _search_file(
    path: Path,
    needle: str,
    query: str,
    hidden: bool,
    root: Path,
    deadline: float,
) -> tuple[list[dict[str, Any]], bool]:
    try:
        if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
            return [], False
        raw = path.read_bytes()
    except OSError:
        return [], False
    if b"\x00" in raw[:8192]:
        return [], False

    hits: list[dict[str, Any]] = []
    text = raw.decode("utf-8", "replace")
    for line_number, line in enumerate(text.splitlines(), start=1):
        if time.monotonic() >= deadline:
            return hits, True
        index = line.casefold().find(needle)
        if index < 0:
            continue
        hits.append(
            {
                "name": path.name,
                "path": str(path),
                "display_path": _display_path(path, root),
                "line": line_number,
                "column": index + 1,
                "snippet": _search_snippet(line, index),
                "match": line[index:index + len(query)],
                "hidden": hidden,
            }
        )
        if len(hits) >= MAX_SEARCH_MATCHES_PER_FILE:
            break
    return hits, False


def _search_snippet(line: str, index: int) -> str:
    if len(line) <= MAX_SEARCH_SNIPPET_CHARS:
        return line.strip()
    start = max(0, index - MAX_SEARCH_SNIPPET_CHARS // 3)
    window = line[start:start + MAX_SEARCH_SNIPPET_CHARS].strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if start + MAX_SEARCH_SNIPPET_CHARS < len(line) else ""
    return f"{prefix}{window}{suffix}"


def _resolve_directory_path(raw_path: str | None, *, scope: WorkspaceScope) -> Path:
    path = _clean_preview_path(raw_path)
    if not path:
        if not scope.project_path.is_dir():
            raise WebUIFilePreviewError(404, "workspace not found")
        return scope.project_path
    if len(path) > 4096:
        raise WebUIFilePreviewError(400, "path is too long")

    try:
        extra_roots = [get_media_dir()] if scope.restrict_to_workspace else None
        resolved = resolve_allowed_path(
            path,
            workspace=scope.project_path,
            allowed_root=scope.project_path if scope.restrict_to_workspace else None,
            extra_allowed_roots=extra_roots,
            strict=True,
        )
    except FileNotFoundError as e:
        raise WebUIFilePreviewError(404, "directory not found") from e
    except WorkspaceBoundaryError as e:
        raise WebUIFilePreviewError(
            403,
            "directory is outside the current workspace",
        ) from e
    except OSError as e:
        raise WebUIFilePreviewError(400, "invalid path") from e

    if not resolved.is_dir():
        raise WebUIFilePreviewError(400, "path is not a directory")
    return resolved


def _parent_path(path: Path, root: Path) -> str | None:
    if path == root:
        return None
    parent = path.parent
    if parent == path:
        return None
    return str(parent)
```

- [ ] **Step 4：运行纯函数测试确认 GREEN**

```bash
.venv/Scripts/python.exe -m pytest tests/webui/test_workspace_files.py -q
```

Expected: 全部 PASS（含既有 4 条）。

- [ ] **Step 5：写路由失败测试**

在 `tests/test_webui_workspace_files.py` 追加：

```python
@pytest.mark.asyncio
async def test_workspace_files_route_honors_show_hidden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / ".env").write_text("SECRET=1", encoding="utf-8")
    monkeypatch.setattr("nanobot.webui.workspace_files.get_media_dir", lambda: tmp_path / "media")
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)
    handler = _handler(_controller(scope))

    hidden_resp = await handler._dispatch_misc_routes(
        None,
        _request("/api/workspace/files?scope=default"),
        "/api/workspace/files",
    )
    shown_resp = await handler._dispatch_misc_routes(
        None,
        _request("/api/workspace/files?scope=default&show_hidden=1"),
        "/api/workspace/files",
    )

    assert hidden_resp is not None and hidden_resp.status_code == 200
    assert shown_resp is not None and shown_resp.status_code == 200
    hidden_names = {entry["name"] for entry in json.loads(hidden_resp.body)["entries"]}
    shown = {entry["name"]: entry for entry in json.loads(shown_resp.body)["entries"]}
    assert ".env" not in hidden_names
    assert shown[".env"]["hidden"] is True


@pytest.mark.asyncio
async def test_workspace_search_route_returns_hits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setattr("nanobot.webui.workspace_files.get_media_dir", lambda: tmp_path / "media")
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)
    handler = _handler(_controller(scope))

    response = await handler._dispatch_misc_routes(
        None,
        _request("/api/workspace/files/search?scope=default&q=root"),
        "/api/workspace/files/search",
    )

    assert response is not None and response.status_code == 200
    payload = json.loads(response.body)
    assert payload["query"] == "root"
    assert any(
        hit["display_path"].replace("\\", "/") == "a.md" for hit in payload["results"]
    )


@pytest.mark.asyncio
async def test_workspace_search_route_rejects_empty_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setattr("nanobot.webui.workspace_files.get_media_dir", lambda: tmp_path / "media")
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)
    handler = _handler(_controller(scope))

    response = await handler._dispatch_misc_routes(
        None,
        _request("/api/workspace/files/search?scope=default&q="),
        "/api/workspace/files/search",
    )

    assert response is not None and response.status_code == 400
    assert json.loads(response.body) == {"error": "invalid_query"}


@pytest.mark.asyncio
async def test_workspace_search_route_without_default_workspace_returns_404(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "not-created"
    scope = default_workspace_scope(missing, restrict_to_workspace=True)
    handler = _handler(_controller(scope))

    response = await handler._dispatch_misc_routes(
        None,
        _request("/api/workspace/files/search?scope=default&q=x"),
        "/api/workspace/files/search",
    )

    assert response is not None and response.status_code == 404
    assert json.loads(response.body) == {"error": "no_default_workspace"}


@pytest.mark.asyncio
async def test_session_search_route_returns_hits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setattr("nanobot.webui.workspace_files.get_media_dir", lambda: tmp_path / "media")
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)
    handler = _handler(_controller(scope))
    path = f"/api/sessions/{quote(_SESSION_KEY, safe='')}/files/search?q=root"

    response = await handler._dispatch_session_routes(_request(path), path.split("?")[0])

    assert response is not None and response.status_code == 200
    payload = json.loads(response.body)
    assert payload["results"]
    assert payload["results"][0]["display_path"].replace("\\", "/") == "a.md"
```

- [ ] **Step 6：运行确认 RED**

```bash
.venv/Scripts/python.exe -m pytest tests/test_webui_workspace_files.py -q
```

Expected: `show_hidden` 不生效、`/search` 返回 `None` 导致断言失败。

- [ ] **Step 7：实现 `ws_http.py`**

导入区改为：

```python
from nanobot.webui.workspace_files import (
    resolve_workspace_anchor,
    workspace_directory_payload,
    workspace_search_payload,
)
```

`_dispatch_session_routes` 在 `file-preview` 与 `files` 之间插入：

```python
        m = re.match(r"^/api/sessions/([^/]+)/files/search$", got)
        if m:
            return self._handle_session_search(request, m.group(1))
```

`_handle_session_files` 中：

```python
        query = _parse_query(request.path)
        path = _query_first(query, "path")
        show_hidden = (_query_first(query, "show_hidden") or "").lower() in {
            "1", "true", "yes", "on",
        }
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = workspace_directory_payload(
                path, scope=scope, show_hidden=show_hidden
            )
```

`_handle_workspace_files` 中同样解析并传入 `show_hidden`。

新增两个 handler（放在 `_handle_workspace_files` 之后）：

```python
    def _handle_session_search(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        show_hidden = (_query_first(query, "show_hidden") or "").lower() in {
            "1", "true", "yes", "on",
        }
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = workspace_search_payload(
                _query_first(query, "q"),
                scope=scope,
                show_hidden=show_hidden,
            )
        except WebUIFilePreviewError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(
            payload,
            accept_encoding=_combined_list_header(request.headers, "Accept-Encoding"),
        )

    def _handle_workspace_search(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        anchor = resolve_workspace_anchor(
            _query_first(query, "scope"),
            default_scope=self.workspaces.default_scope(),
        )
        if anchor is None:
            return _http_json_response({"error": "no_default_workspace"}, status=404)
        show_hidden = (_query_first(query, "show_hidden") or "").lower() in {
            "1", "true", "yes", "on",
        }
        try:
            payload = workspace_search_payload(
                _query_first(query, "q"),
                scope=anchor,
                show_hidden=show_hidden,
            )
        except WebUIFilePreviewError as e:
            if e.status in {400, 403}:
                return _http_json_response({"error": "invalid_query"}, status=400)
            return _http_json_response({"error": e.message}, status=e.status)
        return _http_json_response(
            payload,
            accept_encoding=_combined_list_header(request.headers, "Accept-Encoding"),
        )
```

`_dispatch_misc_routes` 在 `/api/workspace/files` 之前插入：

```python
        if got == "/api/workspace/files/search":
            return self._handle_workspace_search(request)
```

- [ ] **Step 8：运行后端测试确认 GREEN**

```bash
.venv/Scripts/python.exe -m pytest tests/webui/test_workspace_files.py tests/test_webui_workspace_files.py -q
.venv/Scripts/python.exe -m ruff check nanobot/webui/workspace_files.py nanobot/webui/ws_http.py tests/webui/test_workspace_files.py tests/test_webui_workspace_files.py
```

Expected: pytest 全 PASS；ruff `All checks passed!`。

- [ ] **Step 9：提交**

```bash
git add nanobot/webui/workspace_files.py nanobot/webui/ws_http.py tests/webui/test_workspace_files.py tests/test_webui_workspace_files.py
git commit -m "feat(webui): 文件树隐藏开关与有界工作区搜索"
```

---

### Task 2（WU-02）：终端后端——有界回放快照

「Harness：orchestration.dispatch」
Skills: test-driven-development@harness-kit/.agents/skills/test-driven-development/SKILL.md loaded

**Files:**
- Create: `nanobot/webui/terminal_scrollback.py`
- Create: `tests/webui/test_terminal_scrollback.py`
- Modify: `nanobot/webui/outbound_wire.py`
- Modify: `nanobot/webui/pty_terminal.py`
- Modify: `nanobot/webui/terminal_bridge.py`
- Test: `tests/test_webui_pty_terminal.py`
- Test: `tests/test_webui_terminal.py`

- [ ] **Step 1：写 `TerminalScrollback` 失败测试**

创建 `tests/webui/test_terminal_scrollback.py`：

```python
from nanobot.webui.terminal_scrollback import (
    MAX_SCROLLBACK_BYTES,
    MAX_SCROLLBACK_CHARS,
    TerminalScrollback,
)


def test_small_text_roundtrips_without_truncation() -> None:
    buffer = TerminalScrollback()
    buffer.append("hello\nworld\n")
    text, truncated = buffer.snapshot()
    assert text == "hello\nworld\n"
    assert truncated is False


def test_byte_cap_keeps_tail_and_flags_truncation() -> None:
    buffer = TerminalScrollback()
    buffer.append("a" * (MAX_SCROLLBACK_BYTES + 1024))
    text, truncated = buffer.snapshot()
    assert len(text) <= MAX_SCROLLBACK_BYTES
    assert text.endswith("a" * 128)
    assert truncated is True


def test_char_cap_keeps_tail() -> None:
    buffer = TerminalScrollback()
    buffer.append("b" * (MAX_SCROLLBACK_CHARS + 512))
    text, truncated = buffer.snapshot()
    assert len(text) == MAX_SCROLLBACK_CHARS
    assert set(text) == {"b"}
    assert truncated is True


def test_utf8_boundary_never_starts_with_replacement_char() -> None:
    buffer = TerminalScrollback()
    buffer.append("é" * (MAX_SCROLLBACK_BYTES // 2 + 64))
    text, truncated = buffer.snapshot()
    assert truncated is True
    assert text
    assert not text.startswith("\ufffd")


def test_empty_snapshot() -> None:
    text, truncated = TerminalScrollback().snapshot()
    assert text == ""
    assert truncated is False
```

- [ ] **Step 2：运行确认 RED**

```bash
.venv/Scripts/python.exe -m pytest tests/webui/test_terminal_scrollback.py -q
```

Expected: `ModuleNotFoundError: No module named 'nanobot.webui.terminal_scrollback'`。

- [ ] **Step 3：实现 `terminal_scrollback.py`**

```python
"""Bounded UTF-8 tail buffer used to replay terminal output on takeover."""

from __future__ import annotations

MAX_SCROLLBACK_BYTES = 64 * 1024
MAX_SCROLLBACK_CHARS = 32_768


class TerminalScrollback:
    """Keep the newest output only; snapshot never begins mid-codepoint."""

    def __init__(
        self,
        max_bytes: int = MAX_SCROLLBACK_BYTES,
        max_chars: int = MAX_SCROLLBACK_CHARS,
    ) -> None:
        self._max_bytes = max(1, int(max_bytes))
        self._max_chars = max(1, int(max_chars))
        self._buffer = bytearray()
        self._truncated = False

    def append(self, text: str) -> None:
        if not text:
            return
        self._buffer.extend(text.encode("utf-8"))
        if len(self._buffer) > self._max_bytes:
            overflow = len(self._buffer) - self._max_bytes
            self._buffer = self._buffer[overflow:]
            while self._buffer and (self._buffer[0] & 0xC0) == 0x80:
                del self._buffer[0]
            self._truncated = True
        if len(self._buffer) > self._max_chars:
            text_now = self._buffer.decode("utf-8", "replace")
            if len(text_now) > self._max_chars:
                trimmed = text_now[-self._max_chars:]
                self._buffer = bytearray(trimmed.encode("utf-8"))
                self._truncated = True

    def snapshot(self) -> tuple[str, bool]:
        return self._buffer.decode("utf-8", "replace"), self._truncated
```

- [ ] **Step 4：运行确认 GREEN**

```bash
.venv/Scripts/python.exe -m pytest tests/webui/test_terminal_scrollback.py -q
```

Expected: 5 passed。

- [ ] **Step 5：添加线协议与 bridge 失败测试**

`tests/test_webui_pty_terminal.py` 追加：

```python
async def test_pty_takeover_replays_bounded_scrollback(scope):
    bridge = PtyTerminalBridge()
    first, second = Collector(), Collector()
    try:
        await bridge.start(
            terminal_id="term-replay", scope=scope, chat_id="chat-1",
            session_key="websocket:chat-1", publisher=first,
        )
        await bridge.write(terminal_id="term-replay", data="echo BEFORE_TAKEOVER\n")
        await _wait_until(
            lambda: "BEFORE_TAKEOVER" in first.text(),
            message="pre-takeover output",
        )

        result = await bridge.start(
            terminal_id="term-replay", scope=scope, chat_id="chat-2",
            session_key="websocket:chat-2", publisher=second,
        )

        assert result["status"] == "already_running"
        snapshots = second.of("terminal_snapshot")
        assert len(snapshots) == 1
        assert "BEFORE_TAKEOVER" in snapshots[0]["data"]
        assert snapshots[0]["truncated"] is False
        assert snapshots[0]["seq"] >= 1
        assert first.of("terminal_snapshot") == []
    finally:
        await bridge.aclose()
```

`tests/test_webui_terminal.py` 追加：

```python
@_SKIP_WINDOWS
async def test_bridge_takeover_replays_bounded_scrollback(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    first, second = Collector(), Collector()
    scope = build_workspace_scope(tmp_path, "full")
    try:
        await bridge.start(
            terminal_id="t-replay",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=first,
        )
        await bridge.write(terminal_id="t-replay", data="echo BEFORE_TAKEOVER\n")
        await _wait_until(
            lambda: "BEFORE_TAKEOVER" in first.text(),
            message="pre-takeover output",
        )

        result = await bridge.start(
            terminal_id="t-replay",
            scope=scope,
            chat_id="chat2",
            session_key="websocket:chat2",
            publisher=second,
        )

        assert result["status"] == "already_running"
        snapshots = second.of("terminal_snapshot")
        assert len(snapshots) == 1
        assert "BEFORE_TAKEOVER" in snapshots[0]["data"]
        assert first.of("terminal_snapshot") == []
    finally:
        await bridge.aclose()
```

- [ ] **Step 6：运行确认 RED**

```bash
.venv/Scripts/python.exe -m pytest tests/test_webui_pty_terminal.py::test_pty_takeover_replays_bounded_scrollback tests/test_webui_terminal.py::test_bridge_takeover_replays_bounded_scrollback -q
```

Expected: `terminal_snapshot` 列表为空导致断言失败；Windows 下两条 skip。

- [ ] **Step 7：实现线协议**

`nanobot/webui/outbound_wire.py` 在 `TerminalOutputWirePayload` 后加：

```python
class TerminalSnapshotWirePayload(TypedDict):
    event: Literal["terminal_snapshot"]
    type: Literal["terminal_snapshot"]
    terminal_id: str
    seq: int
    data: str
    truncated: bool
```

在 `encode_terminal_output` 后加：

```python
def encode_terminal_snapshot(
    *,
    terminal_id: str,
    seq: int,
    data: str,
    truncated: bool,
) -> TerminalSnapshotWirePayload:
    """Bounded tail replay sent to the chat that just took the terminal over."""
    return {
        "event": "terminal_snapshot",
        "type": "terminal_snapshot",
        "terminal_id": terminal_id,
        "seq": int(seq),
        "data": data,
        "truncated": bool(truncated),
    }
```

- [ ] **Step 8：接入 PTY bridge**

`nanobot/webui/pty_terminal.py`：

```python
from nanobot.webui.outbound_wire import (
    encode_terminal_exit,
    encode_terminal_output,
    encode_terminal_snapshot,
)
from nanobot.webui.terminal_scrollback import TerminalScrollback
```

`_PtyTerminal` 末尾加字段：

```python
    scrollback: TerminalScrollback = field(default_factory=TerminalScrollback)
```

接管分支在重绑 `publisher` / `chat_id` / `session_key` 后、`return` 前加：

```python
            snapshot, truncated = existing.scrollback.snapshot()
            if snapshot:
                await self._publish(
                    existing,
                    encode_terminal_snapshot(
                        terminal_id=existing.terminal_id,
                        seq=existing.seq,
                        data=snapshot,
                        truncated=truncated,
                    ),
                )
```

`_feed_loop` 中每个 chunk 发布前记录：

```python
                for chunk in split_output_chunks(text):
                    terminal.scrollback.append(chunk)
                    terminal.seq += 1
                    await self._publish(
```

- [ ] **Step 9：接入管道 bridge**

`nanobot/webui/terminal_bridge.py` 增加导入 `encode_terminal_snapshot` 与 `TerminalScrollback`；`_ShellTerminal` 末尾加：

```python
    scrollback: TerminalScrollback = field(default_factory=TerminalScrollback)
```

接管分支在重绑后加与 PTY 相同的 snapshot 发布块。

`_emit` 改为：

```python
        for chunk in split_output_chunks(text):
            terminal.scrollback.append(chunk)
            terminal.seq += 1
            await self._publish(
```

- [ ] **Step 10：运行后端测试确认 GREEN**

```bash
.venv/Scripts/python.exe -m pytest tests/webui/test_terminal_scrollback.py tests/test_webui_pty_terminal.py tests/test_webui_terminal.py -q
.venv/Scripts/python.exe -m ruff check nanobot/webui/terminal_scrollback.py nanobot/webui/outbound_wire.py nanobot/webui/pty_terminal.py nanobot/webui/terminal_bridge.py tests/webui/test_terminal_scrollback.py tests/test_webui_pty_terminal.py tests/test_webui_terminal.py
```

Expected: 全 PASS 或 Windows 下 POSIX 用例 skip；ruff 通过。

- [ ] **Step 11：提交**

```bash
git add nanobot/webui/terminal_scrollback.py nanobot/webui/outbound_wire.py nanobot/webui/pty_terminal.py nanobot/webui/terminal_bridge.py tests/webui/test_terminal_scrollback.py tests/test_webui_pty_terminal.py tests/test_webui_terminal.py
git commit -m "feat(webui): 终端接管回放有界 scrollback 快照"
```

---

### Task 3（WU-03）：文件前端——工具栏、隐藏、搜索

「Harness：orchestration.dispatch」
Skills: test-driven-development@harness-kit/.agents/skills/test-driven-development/SKILL.md loaded

**Files:**
- Modify: `webui/src/lib/types.ts`
- Modify: `webui/src/lib/api.ts`
- Modify: `webui/src/components/workbench/WorkspaceFileTree.tsx`
- Create: `webui/src/components/dock/DockFileSearch.tsx`
- Modify: `webui/src/components/dock/DockFileTab.tsx`
- Test: `webui/src/tests/dock-file-tab.test.tsx`

- [ ] **Step 1：写失败测试**

在 `dock-file-tab.test.tsx` 的 `mocks` 加 `fetchWorkspaceSearch: vi.fn()`，`beforeEach` 加：

```ts
mocks.fetchWorkspaceSearch.mockReset().mockResolvedValue({
  query: "README",
  results: [{
    name: "README.md",
    path: "README.md",
    display_path: "README.md",
    line: 0,
    column: 0,
    snippet: "README.md",
    match: "README.md",
    hidden: false,
  }],
  truncated: false,
  scanned_files: 1,
});
```

追加：

```tsx
it("toggles hidden files through the scoped workspace endpoint", async () => {
  renderTab();
  await screen.findByTestId("dock-file-toolbar");
  fireEvent.click(screen.getByTestId("dock-file-hidden-toggle"));
  await waitFor(() => {
    expect(mocks.fetchWorkspaceFiles).toHaveBeenCalledWith("tok", null, "", true);
  });
});

it("searches file names and contents from the toolbar", async () => {
  renderTab();
  await screen.findByTestId("dock-file-toolbar");
  fireEvent.click(screen.getByTestId("dock-file-search-toggle"));
  fireEvent.change(screen.getByTestId("dock-file-search-input"), {
    target: { value: "README" },
  });
  expect(
    await screen.findByTestId("dock-file-search-hit-README.md", {}, { timeout: 2000 }),
  ).toBeInTheDocument();
  await waitFor(() => {
    expect(mocks.fetchWorkspaceSearch).toHaveBeenCalledWith("tok", "README", "", false);
  });
});
```

- [ ] **Step 2：运行确认 RED**

```bash
cd webui && npm run test -- src/tests/dock-file-tab.test.tsx
```

Expected: 找不到 `dock-file-toolbar` / `dock-file-hidden-toggle`。

- [ ] **Step 3：补类型**

`webui/src/lib/types.ts`：

```ts
export interface SessionDirectoryEntry {
  name: string;
  path: string;
  display_path: string;
  is_dir: boolean;
  size: number;
  /** True for dot-prefixed names and node_modules; only surfaced with show_hidden. */
  hidden?: boolean;
}

export interface WorkspaceSearchHit {
  name: string;
  path: string;
  display_path: string;
  /** 1-based for content hits; 0 for a file-name hit. */
  line: number;
  column: number;
  snippet: string;
  match: string;
  hidden: boolean;
}

export interface WorkspaceSearchPayload {
  query: string;
  results: WorkspaceSearchHit[];
  truncated: boolean;
  scanned_files: number;
}
```

- [ ] **Step 4：补 API**

`webui/src/lib/api.ts` 追加 `showHidden` 参数（放在末尾，避免破坏既有调用）：

```ts
export async function fetchSessionDirectory(
  token: string,
  key: string,
  path: string | null,
  base: string = "",
  showHidden: boolean = false,
): Promise<SessionDirectoryPayload> {
  const query = new URLSearchParams();
  if (path) query.set("path", path);
  if (showHidden) query.set("show_hidden", "1");
  return request<SessionDirectoryPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/files?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchWorkspaceFiles(
  token: string,
  path: string | null = null,
  base: string = "",
  showHidden: boolean = false,
): Promise<SessionDirectoryPayload> {
  const query = new URLSearchParams({ scope: "default" });
  if (path) query.set("path", path);
  if (showHidden) query.set("show_hidden", "1");
  return request<SessionDirectoryPayload>(
    `${base}/api/workspace/files?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchSessionSearch(
  token: string,
  key: string,
  text: string,
  base: string = "",
  showHidden: boolean = false,
): Promise<WorkspaceSearchPayload> {
  const query = new URLSearchParams({ q: text });
  if (showHidden) query.set("show_hidden", "1");
  return request<WorkspaceSearchPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/files/search?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchWorkspaceSearch(
  token: string,
  text: string,
  base: string = "",
  showHidden: boolean = false,
): Promise<WorkspaceSearchPayload> {
  const query = new URLSearchParams({ scope: "default", q: text });
  if (showHidden) query.set("show_hidden", "1");
  return request<WorkspaceSearchPayload>(
    `${base}/api/workspace/files/search?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}
```

确保文件顶部类型导入包含 `WorkspaceSearchPayload`。

- [ ] **Step 5：改造 `WorkspaceFileTree.tsx`**

导入不变。Props 改为：

```ts
interface WorkspaceFileTreeProps {
  sessionKey?: string;
  getToken: () => string;
  onOpenFilePreview?: (path: string) => void;
  showHidden?: boolean;
  refreshToken?: number;
  collapseToken?: number;
  fetchDirectory?: (
    token: string,
    path: string | null,
    showHidden: boolean,
  ) => Promise<SessionDirectoryPayload>;
}
```

函数签名加默认值：

```tsx
export function WorkspaceFileTree({
  sessionKey,
  getToken,
  onOpenFilePreview,
  showHidden = false,
  refreshToken = 0,
  collapseToken = 0,
  fetchDirectory,
}: WorkspaceFileTreeProps) {
```

`load` 改为：

```tsx
    async (path: string | null, nodeKey: string) => {
      setNodes((current) => ({ ...current, [nodeKey]: { status: "loading" } }));
      try {
        const payload = fetchDirectory
          ? await fetchDirectory(getTokenRef.current(), path, showHidden)
          : await fetchSessionDirectory(
              getTokenRef.current(),
              sessionKey ?? "",
              path,
              "",
              showHidden,
            );
        setNodes((current) => ({
          ...current,
          [nodeKey]: {
            status: "ready",
            entries: payload.entries,
            truncated: payload.truncated,
          },
        }));
      } catch {
        setNodes((current) => ({ ...current, [nodeKey]: { status: "error" } }));
      }
    },
    [sessionKey, fetchDirectory, showHidden],
```

挂载/刷新/折叠：

```tsx
  useEffect(() => {
    setNodes({});
    setExpanded({});
    void load(null, ROOT_KEY);
  }, [load, refreshToken]);

  useEffect(() => {
    if (collapseToken > 0) setExpanded({});
  }, [collapseToken]);
```

目录按钮和文件按钮都加：

```tsx
                  data-hidden={entry.hidden ? "true" : undefined}
```

并在 `className` 末尾加：

```tsx
                  entry.hidden && "opacity-60",
```

目录与文件两处都要加，使用 `cn(...)` 合并。

- [ ] **Step 6：新增 `DockFileSearch.tsx`**

```tsx
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";

import { fetchSessionSearch, fetchWorkspaceSearch } from "@/lib/api";
import type { WorkspaceSearchPayload } from "@/lib/types";

const SEARCH_DEBOUNCE_MS = 250;
const MAX_QUERY_CHARS = 200;

type SearchState =
  | { status: "idle" }
  | { status: "loading"; query: string }
  | { status: "ready"; query: string; payload: WorkspaceSearchPayload }
  | { status: "error"; query: string };

interface DockFileSearchProps {
  sessionKey: string | null;
  getToken: () => string;
  showHidden: boolean;
  onOpen: (path: string) => void;
  onClose: () => void;
}

export function DockFileSearch({
  sessionKey,
  getToken,
  showHidden,
  onOpen,
  onClose,
}: DockFileSearchProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [state, setState] = useState<SearchState>({ status: "idle" });
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setState({ status: "idle" });
      return;
    }
    let cancelled = false;
    setState({ status: "loading", query: trimmed });
    const timer = window.setTimeout(() => {
      const pending = sessionKey
        ? fetchSessionSearch(getTokenRef.current(), sessionKey, trimmed, "", showHidden)
        : fetchWorkspaceSearch(getTokenRef.current(), trimmed, "", showHidden);
      pending
        .then((payload) => {
          if (!cancelled) setState({ status: "ready", query: trimmed, payload });
        })
        .catch(() => {
          if (!cancelled) setState({ status: "error", query: trimmed });
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query, sessionKey, showHidden]);

  const results = state.status === "ready" ? state.payload.results : [];

  return (
    <div
      data-testid="dock-file-search"
      className="flex shrink-0 flex-col gap-1 rounded-md border border-border/50 bg-background/70 p-1.5"
    >
      <div className="flex items-center gap-1">
        <input
          autoFocus
          data-testid="dock-file-search-input"
          type="search"
          value={query}
          maxLength={MAX_QUERY_CHARS}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") onClose();
          }}
          placeholder={t("dock.searchPlaceholder", {
            defaultValue: "Search file names and contents",
          })}
          spellCheck={false}
          autoComplete="off"
          className="h-6 min-w-0 flex-1 rounded border border-border/50 bg-background px-1.5 text-[11.5px] text-foreground outline-none focus-visible:ring-1 focus-visible:ring-ring/60"
        />
        <button
          type="button"
          aria-label={t("filePreview.close", { defaultValue: "Close file preview" })}
          onClick={onClose}
          className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/70 hover:bg-muted/60 hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>

      {state.status === "loading" ? (
        <p className="px-1 text-[11.5px] text-muted-foreground/70">
          {t("dock.searching", { defaultValue: "Searching…" })}
        </p>
      ) : null}

      {state.status === "error" ? (
        <p role="alert" className="px-1 text-[11.5px] text-destructive">
          {t("dock.searchFailed", { defaultValue: "Search failed" })}
        </p>
      ) : null}

      {state.status === "ready" && results.length === 0 ? (
        <p className="px-1 text-[11.5px] text-muted-foreground/70">
          {t("dock.noMatches", { defaultValue: "No matches" })}
        </p>
      ) : null}

      {results.length > 0 ? (
        <ul className="max-h-48 overflow-y-auto scrollbar-thin">
          {results.map((hit) => (
            <li key={`${hit.path}:${hit.line}:${hit.column}`}>
              <button
                type="button"
                data-testid={`dock-file-search-hit-${hit.path}`}
                onClick={() => onOpen(hit.path)}
                className="flex w-full min-w-0 flex-col gap-0.5 rounded px-1 py-1 text-left hover:bg-muted/50"
              >
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className="min-w-0 flex-1 truncate text-[11.5px] font-medium text-foreground/90">
                    {hit.name}
                  </span>
                  {hit.line > 0 ? (
                    <span className="shrink-0 text-[10.5px] tabular-nums text-muted-foreground/70">
                      {hit.line}:{hit.column}
                    </span>
                  ) : null}
                </span>
                <span className="min-w-0 truncate font-mono text-[10.5px] text-muted-foreground">
                  {hit.display_path}
                </span>
                <code className="line-clamp-2 break-all text-[11px] text-foreground/70">
                  {hit.snippet}
                </code>
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {state.status === "ready" && state.payload.truncated ? (
        <p className="px-1 text-[10.5px] text-amber-700 dark:text-amber-200">
          {t("dock.searchTruncated", { defaultValue: "Showing partial results" })}
        </p>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 7：改造 `DockFileTab.tsx`**

导入改为：

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronsDownUp, Eye, EyeOff, RefreshCw, RotateCw, Search, X } from "lucide-react";

import { CodeBlock } from "@/components/CodeBlock";
import { DockFileSearch } from "@/components/dock/DockFileSearch";
import { WorkspaceFileTree } from "@/components/workbench/WorkspaceFileTree";
import { ApiError, fetchFilePreview, fetchWorkspaceFiles } from "@/lib/api";
import type { FilePreviewPayload, SessionDirectoryPayload } from "@/lib/types";
import { cn } from "@/lib/utils";
```

删除 `rootPayloadRef`，把 `DockFileTab` 函数整体替换为：

```tsx
export function DockFileTab({
  scope,
  sessionKey,
  getToken,
  onOpenFilePreview,
}: DockFileTabProps) {
  const { t } = useTranslation();
  const [filesState, setFilesState] = useState<FilesState>("idle");
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const [collapseToken, setCollapseToken] = useState(0);
  const [searchOpen, setSearchOpen] = useState(false);
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  const homeAnchored = sessionKey === null;

  const probeWorkspaceRoot = useCallback(async () => {
    setFilesState("loading");
    try {
      await fetchWorkspaceFiles(getTokenRef.current(), null, "", false);
      setFilesState("ready");
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setFilesState("noWorkspace");
      else setFilesState("error");
    }
  }, []);

  useEffect(() => {
    if (homeAnchored && filesState === "idle") {
      void probeWorkspaceRoot();
    }
  }, [homeAnchored, filesState, probeWorkspaceRoot]);

  const fetchDirectory = useCallback(
    (token: string, path: string | null, hidden: boolean) =>
      fetchWorkspaceFiles(token, path, "", hidden),
    [],
  );

  const openPreview = onOpenFilePreview ?? setPreviewPath;
  const inlinePreview = !onOpenFilePreview;

  const toolButton = (active = false) =>
    cn(
      "grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground",
      active && "bg-muted/70 text-foreground",
    );

  const toolbar = (
    <div
      data-testid="dock-file-toolbar"
      className="flex shrink-0 items-center gap-0.5"
    >
      <button
        type="button"
        data-testid="dock-file-refresh"
        aria-label={t("dock.refresh", { defaultValue: "Refresh" })}
        title={t("dock.refresh", { defaultValue: "Refresh" })}
        onClick={() => setRefreshToken((value) => value + 1)}
        className={toolButton()}
      >
        <RefreshCw className="h-3.5 w-3.5" aria-hidden />
      </button>
      <button
        type="button"
        data-testid="dock-file-hidden-toggle"
        aria-pressed={showHidden}
        aria-label={
          showHidden
            ? t("dock.hideHidden", { defaultValue: "Hide hidden files" })
            : t("dock.showHidden", { defaultValue: "Show hidden files" })
        }
        title={
          showHidden
            ? t("dock.hideHidden", { defaultValue: "Hide hidden files" })
            : t("dock.showHidden", { defaultValue: "Show hidden files" })
        }
        onClick={() => setShowHidden((value) => !value)}
        className={toolButton(showHidden)}
      >
        {showHidden ? (
          <EyeOff className="h-3.5 w-3.5" aria-hidden />
        ) : (
          <Eye className="h-3.5 w-3.5" aria-hidden />
        )}
      </button>
      <button
        type="button"
        data-testid="dock-file-collapse"
        aria-label={t("dock.collapseAll", { defaultValue: "Collapse all" })}
        title={t("dock.collapseAll", { defaultValue: "Collapse all" })}
        onClick={() => setCollapseToken((value) => value + 1)}
        className={toolButton()}
      >
        <ChevronsDownUp className="h-3.5 w-3.5" aria-hidden />
      </button>
      <button
        type="button"
        data-testid="dock-file-search-toggle"
        aria-pressed={searchOpen}
        aria-label={t("dock.search", { defaultValue: "Search files" })}
        title={t("dock.search", { defaultValue: "Search files" })}
        onClick={() => setSearchOpen((value) => !value)}
        className={toolButton(searchOpen)}
      >
        <Search className="h-3.5 w-3.5" aria-hidden />
      </button>
    </div>
  );

  const renderTree = () => (
    <div className="flex min-h-0 flex-1 gap-2">
      {inlinePreview ? (
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {previewPath ? (
            <DockFilePreview
              getToken={getToken}
              sessionKey={sessionKey}
              path={previewPath}
              onClose={() => setPreviewPath(null)}
            />
          ) : (
            <p
              data-testid="dock-file-preview-empty"
              className="px-1 py-1 text-[12px] text-muted-foreground/60"
            >
              {t("filePreview.pickFile", {
                defaultValue: "Pick a file to preview it here.",
              })}
            </p>
          )}
        </div>
      ) : null}
      <div
        data-testid="dock-file-tree-column"
        className={
          inlinePreview
            ? "flex min-h-0 w-[46%] shrink-0 flex-col gap-2"
            : "flex min-h-0 flex-1 flex-col gap-2"
        }
      >
        {homeAnchored ? (
          <WorkspaceFileTree
            getToken={getToken}
            fetchDirectory={fetchDirectory}
            showHidden={showHidden}
            refreshToken={refreshToken}
            collapseToken={collapseToken}
            onOpenFilePreview={openPreview}
          />
        ) : (
          <WorkspaceFileTree
            sessionKey={sessionKey ?? ""}
            getToken={getToken}
            showHidden={showHidden}
            refreshToken={refreshToken}
            collapseToken={collapseToken}
            onOpenFilePreview={openPreview}
          />
        )}
      </div>
    </div>
  );

  const content = () => {
    if (!homeAnchored) return renderTree();
    if (filesState === "loading" || filesState === "idle") {
      return (
        <p className="py-1 text-[12px] text-muted-foreground/60">
          {t("workbench.treeLoading", { defaultValue: "Loading…" })}
        </p>
      );
    }
    if (filesState === "noWorkspace") {
      return (
        <p role="status" className="py-1 text-[12px] text-muted-foreground">
          {t("startPanel.openProjectFirst", {
            defaultValue: "Available after you pick a project",
          })}
        </p>
      );
    }
    if (filesState === "error") {
      return (
        <button
          type="button"
          onClick={() => void probeWorkspaceRoot()}
          className="flex items-center gap-1.5 py-1 text-[12px] text-muted-foreground hover:text-foreground"
        >
          <RotateCw className="h-3 w-3" aria-hidden />
          {t("startPanel.filesRetry", {
            defaultValue: "Could not load the workspace — retry",
          })}
        </button>
      );
    }
    return renderTree();
  };

  return (
    <div
      data-scope={scope}
      className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto px-3 py-2 scrollbar-thin"
    >
      {toolbar}
      {searchOpen ? (
        <DockFileSearch
          sessionKey={sessionKey}
          getToken={getToken}
          showHidden={showHidden}
          onOpen={openPreview}
          onClose={() => setSearchOpen(false)}
        />
      ) : null}
      {content()}
    </div>
  );
}
```

- [ ] **Step 8：运行测试确认 GREEN**

```bash
cd webui && npm run test -- src/tests/dock-file-tab.test.tsx
cd webui && npm run build
```

Expected: 5 条测试全 PASS；build 通过。

- [ ] **Step 9：提交**

```bash
git add webui/src/lib/types.ts webui/src/lib/api.ts webui/src/components/workbench/WorkspaceFileTree.tsx webui/src/components/dock/DockFileSearch.tsx webui/src/components/dock/DockFileTab.tsx webui/src/tests/dock-file-tab.test.tsx
git commit -m "feat(webui): 文件树工具栏、隐藏开关与工作区搜索"
```

---

### Task 4（WU-04）：终端前端——快照回放、复制/清屏/粘贴、活动态

「Harness：orchestration.dispatch」
Skills: test-driven-development@harness-kit/.agents/skills/test-driven-development/SKILL.md loaded

**Files:**
- Modify: `webui/src/lib/types.ts`
- Modify: `webui/src/lib/zhixu-client.ts`
- Modify: `webui/src/hooks/useZhixuStream.ts`
- Modify: `webui/src/components/workbench/TerminalSurface.tsx`
- Modify: `webui/src/lib/clipboard.ts`
- Test: `webui/src/tests/terminal-store.test.ts`
- Test: `webui/src/tests/terminal-surface.test.tsx`
- Test: `webui/src/tests/zhixu-client.test.ts`

- [ ] **Step 1：写 store / client 失败测试**

`terminal-store.test.ts` 追加：

```ts
function snapshot(id: string, seq: number, data: string, truncated = false): TerminalEvent {
  return {
    event: "terminal_snapshot",
    type: "terminal_snapshot",
    terminal_id: id,
    seq,
    data,
    truncated,
  } as unknown as TerminalEvent;
}

describe("createTerminalStore terminal_snapshot", () => {
  it("replaces the buffer and bumps viewEpoch", () => {
    const store = createTerminalStore();
    openTerminal(store, "term-1");
    store.applyEvent(output("term-1", 1, "old\n"));
    store.applyEvent(snapshot("term-1", 1, "restored\n", true));
    const record = store.getState().byId["term-1"];
    expect(record?.lines.map((line) => line.data)).toEqual(["restored\n"]);
    expect(record?.lastSeq).toBe(1);
    expect(record?.viewEpoch).toBe(1);
    expect(record?.historyTruncated).toBe(true);
  });

  it("drops a stale snapshot that predates already-seen output", () => {
    const store = createTerminalStore();
    openTerminal(store, "term-1");
    store.applyEvent(output("term-1", 5, "newer\n"));
    store.applyEvent(snapshot("term-1", 4, "older\n"));
    expect(store.getState().byId["term-1"]?.lines.map((line) => line.data)).toEqual(["newer\n"]);
  });

  it("clear empties the buffer without resetting the sequence", () => {
    const store = createTerminalStore();
    openTerminal(store, "term-1");
    store.applyEvent(output("term-1", 3, "line\n"));
    store.clear("term-1");
    const record = store.getState().byId["term-1"];
    expect(record?.lines).toEqual([]);
    expect(record?.lastSeq).toBe(3);
  });
});
```

`zhixu-client.test.ts` 追加一条路由测试（按该文件既有 fake socket 构造方式）：

```ts
it("routes terminal_snapshot frames to terminalEventHandlers", async () => {
  const client = makeClient();
  const received: TerminalEvent[] = [];
  client.onTerminalEvent((event) => received.push(event));
  openSocket();
  send({
    type: "terminal_snapshot",
    event: "terminal_snapshot",
    terminal_id: "term-1",
    seq: 1,
    data: "restored\n",
    truncated: false,
  });
  await flush();
  expect(received.map((event) => event.event)).toContain("terminal_snapshot");
});
```

若 `zhixu-client.test.ts` 无 `makeClient/openSocket/send/flush` 同名 helper，使用该文件已有的等价 helper，不得新造第二套连接。

- [ ] **Step 2：写 surface 失败测试**

`terminal-surface.test.tsx` 顶部补：

```tsx
import { CLEAR_SCREEN } from "@/components/workbench/terminal-shared";
```

`makeFakeTerminal` 增加：

```tsx
  selection: "",
  getSelection() {
    return instance.selection;
  },
```

`RecordedTerminal` 增加 `selection: string`。

追加：

```tsx
describe("TerminalSurface toolbar and replay", () => {
  it("replaces the buffer when terminal_snapshot lands", () => {
    const { client, latest } = setup();
    client.emit(startedEvent(TERMINAL_ID, "ok", undefined, true));
    client.emit(outputEvent(TERMINAL_ID, 1, "old\n"));
    client.emit({
      event: "terminal_snapshot",
      type: "terminal_snapshot",
      terminal_id: TERMINAL_ID,
      seq: 1,
      data: "restored\n",
      truncated: true,
    } as unknown as TerminalEvent);

    const emulator = latest()!;
    expect(emulator.writes).toContain(CLEAR_SCREEN);
    expect(emulator.writes.at(-1)).toBe("restored\n");
    expect(screen.getByText(/truncated/i)).toBeInTheDocument();
  });

  it("clears buffered output from the toolbar", () => {
    const { client, latest } = setup();
    client.emit(startedEvent(TERMINAL_ID, "ok", undefined, true));
    client.emit(outputEvent(TERMINAL_ID, 1, "hello\n"));
    fireEvent.click(screen.getByTestId(`terminal-clear-${TERMINAL_ID}`));
    expect(latest()!.writes.at(-1)).toBe(CLEAR_SCREEN);
    expect(storeLines(latest())).toBeUndefined();
  });

  it("copies the xterm selection when present", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const { client, latest } = setup();
    client.emit(startedEvent(TERMINAL_ID, "ok", undefined, true));
    latest()!.selection = "selected text";
    fireEvent.click(screen.getByTestId(`terminal-copy-${TERMINAL_ID}`));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("selected text"));
    expect(await screen.findByText("Copied")).toBeInTheDocument();
    Reflect.deleteProperty(navigator, "clipboard");
  });

  it("pastes clipboard text into a PTY terminal", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { readText: vi.fn().mockResolvedValue("echo hi\n") },
    });
    const { client } = setup();
    client.emit(startedEvent(TERMINAL_ID, "ok", undefined, true));
    fireEvent.click(screen.getByTestId(`terminal-paste-${TERMINAL_ID}`));
    await waitFor(() =>
      expect(client.terminalWrite).toHaveBeenCalledWith(TERMINAL_ID, "echo hi\n"),
    );
    Reflect.deleteProperty(navigator, "clipboard");
  });
});
```

`storeLines` 不需要；删掉该断言，改为：

```tsx
    expect(latest()!.writes.at(-1)).toBe(CLEAR_SCREEN);
```

- [ ] **Step 3：运行确认 RED**

```bash
cd webui && npm run test -- src/tests/terminal-store.test.ts src/tests/terminal-surface.test.tsx src/tests/zhixu-client.test.ts
cd webui && npm run build
```

Expected: 类型/行为失败（`clear`、`viewEpoch`、`terminal_snapshot` 未知、toolbar 不存在）。

- [ ] **Step 4：补类型与客户端白名单**

`types.ts`：

```ts
export interface TerminalRecordEventFields {
  viewEpoch: number;
  historyTruncated: boolean;
}
```

不需要该辅助接口；直接在 `TerminalEvent` 联合中加入：

```ts
  | {
      event: "terminal_snapshot";
      terminal_id: string;
      seq: number;
      data: string;
      truncated: boolean;
    }
```

`zhixu-client.ts` 的 `TERMINAL_EVENT_NAMES` 加入 `"terminal_snapshot"`。

- [ ] **Step 5：改 store**

`TerminalRecord` 加：

```ts
  viewEpoch: number;
  historyTruncated: boolean;
```

`createEmptyRecord` 加：

```ts
    viewEpoch: 0,
    historyTruncated: false,
```

`TerminalStore` 接口加：

```ts
  /** Clear buffered output locally; the server sequence keeps increasing. */
  clear(id: string): void;
```

实现：

```ts
    clear(id) {
      const existing = state.byId[id];
      if (!existing || existing.lines.length === 0) return;
      upsert(id, (record) => ({ ...record, lines: [], historyTruncated: false }));
    },
```

`applyEvent` 在 `terminal_output` 前加：

```ts
        case "terminal_snapshot": {
          if (removedIds.has(event.terminal_id)) break;
          const record = state.byId[event.terminal_id];
          if (record && event.seq < record.lastSeq) break;
          upsert(event.terminal_id, (current) => ({
            ...current,
            status: current.status === "starting" ? "running" : current.status,
            lastSeq: Math.max(current.lastSeq, event.seq),
            lines: event.data
              ? [{ stream: "stdout" as const, data: event.data }]
              : [],
            viewEpoch: current.viewEpoch + 1,
            historyTruncated: event.truncated,
          }));
          break;
        }
```

- [ ] **Step 6：改 clipboard**

`clipboard.ts` 追加：

```ts
export async function readTextFromClipboard(): Promise<string | null> {
  try {
    if (navigator.clipboard?.readText) {
      return await navigator.clipboard.readText();
    }
  } catch {
    return null;
  }
  return null;
}
```

- [ ] **Step 7：改 `TerminalSurface.tsx`**

导入补：

```tsx
import { ClipboardPaste, Copy, Eraser } from "lucide-react";
import { CLEAR_SCREEN, failureLabel } from "@/components/workbench/terminal-shared";
import { copyTextToClipboard, readTextFromClipboard } from "@/lib/clipboard";
```

`TerminalEmulator` 加：

```ts
  getSelection?(): string;
```

两个工厂的返回对象都加：

```ts
    getSelection: () => terminal.getSelection(),
```

Props 加：

```ts
  /** False while the dock tab is hidden; stops stdin/resize work without unmounting. */
  active?: boolean;
```

签名加 `active = true`，并把：

```tsx
  const interactive = ptyMode && record?.status === "running";
```

改为：

```tsx
  const interactive = ptyMode && record?.status === "running" && active;
```

在创建 effect 之后、增量 effect 之前插入：

```tsx
  const viewEpoch = record?.viewEpoch ?? 0;

  // Snapshot replay: a takeover snapshot replaces the local buffer wholesale.
  useEffect(() => {
    const terminal = emulatorRef.current;
    if (!terminal) return;
    terminal.write(CLEAR_SCREEN);
    const lines = recordRef.current?.lines ?? [];
    for (const line of lines) terminal.write(line.data);
    writtenRef.current = lines.length;
    terminal.scrollToBottom();
  }, [viewEpoch]);
```

在 `handleLine` 前加状态与动作：

```tsx
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 1200);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const copyOutput = async () => {
    const selected = emulatorRef.current?.getSelection?.() ?? "";
    const text = selected || (recordRef.current?.lines ?? []).map((line) => line.data).join("");
    if (!text) return;
    if (await copyTextToClipboard(text)) {
      setNotice(t("dock.copied", { defaultValue: "Copied" }));
    }
  };

  const pasteInput = async () => {
    const current = recordRef.current;
    if (!current || current.status !== "running") return;
    const text = await readTextFromClipboard();
    if (text === null) return;
    if (current.pty) {
      client.terminalWrite(terminalId, text);
    } else {
      client.terminalWrite(terminalId, `${text}\n`);
      store.appendEcho(terminalId, text);
    }
  };
```

根节点内、状态提示之前插入工具栏：

```tsx
      <div className="flex shrink-0 items-center justify-end gap-0.5">
        <button
          type="button"
          data-testid={`terminal-copy-${terminalId}`}
          aria-label={t("dock.copyOutput", { defaultValue: "Copy output" })}
          title={t("dock.copyOutput", { defaultValue: "Copy output" })}
          onClick={() => void copyOutput()}
          className="grid h-6 w-6 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground"
        >
          <Copy className="h-3.5 w-3.5" aria-hidden />
        </button>
        <button
          type="button"
          data-testid={`terminal-clear-${terminalId}`}
          aria-label={t("dock.clearOutput", { defaultValue: "Clear terminal" })}
          title={t("dock.clearOutput", { defaultValue: "Clear terminal" })}
          onClick={() => store.clear(terminalId)}
          className="grid h-6 w-6 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground"
        >
          <Eraser className="h-3.5 w-3.5" aria-hidden />
        </button>
        <button
          type="button"
          data-testid={`terminal-paste-${terminalId}`}
          aria-label={t("dock.pasteInput", { defaultValue: "Paste" })}
          title={t("dock.pasteInput", { defaultValue: "Paste" })}
          disabled={!record || record.status !== "running"}
          onClick={() => void pasteInput()}
          className="grid h-6 w-6 place-items-center rounded text-muted-foreground/75 enabled:hover:bg-muted/60 enabled:hover:text-foreground disabled:opacity-40"
        >
          <ClipboardPaste className="h-3.5 w-3.5" aria-hidden />
        </button>
        {notice ? (
          <span role="status" className="px-1 text-[11px] text-muted-foreground">
            {notice}
          </span>
        ) : null}
      </div>
```

在退出提示之后加：

```tsx
      {record?.historyTruncated ? (
        <p role="status" className="text-[11.5px] text-amber-700 dark:text-amber-200">
          {t("dock.historyTruncated", {
            defaultValue: "Earlier output was truncated",
          })}
        </p>
      ) : null}
```

- [ ] **Step 8：运行确认 GREEN**

```bash
cd webui && npm run test -- src/tests/terminal-store.test.ts src/tests/terminal-surface.test.tsx src/tests/zhixu-client.test.ts
cd webui && npm run build
```

Expected: 全 PASS；build 通过。

- [ ] **Step 9：提交**

```bash
git add webui/src/lib/types.ts webui/src/lib/zhixu-client.ts webui/src/hooks/useZhixuStream.ts webui/src/components/workbench/TerminalSurface.tsx webui/src/lib/clipboard.ts webui/src/tests/terminal-store.test.ts webui/src/tests/terminal-surface.test.tsx webui/src/tests/zhixu-client.test.ts
git commit -m "feat(webui): 终端快照回放与复制清屏粘贴"
```

---

### Task 5（WU-05）：浏览器与桌面独立窗口

「Harness：orchestration.dispatch」
Skills: test-driven-development@harness-kit/.agents/skills/test-driven-development/SKILL.md loaded

**Files:**
- Modify: `webui/src/lib/browser-url.ts`
- Modify: `webui/src/lib/runtime.ts`
- Modify: `webui/src/components/workbench/BrowserSection.tsx`
- Create: `desktop/browser-policy.cjs`
- Create: `desktop/browser-policy.test.cjs`
- Modify: `desktop/preload.cjs`
- Modify: `desktop/main.cjs`
- Modify: `desktop/package.json`
- Test: `webui/src/tests/browser-url.test.ts`
- Test: `webui/src/tests/browser-section.test.tsx`

- [ ] **Step 1：写 URL 与 policy 失败测试**

`browser-url.test.ts` 追加：

```ts
it("accepts bare public hosts with https", () => {
  expect(normalizeBrowserUrl("example.com")).toEqual({
    ok: true,
    url: "https://example.com/",
  });
  expect(normalizeBrowserUrl("example.com:8080/docs")).toEqual({
    ok: true,
    url: "https://example.com:8080/docs",
  });
});

it("accepts bare loopback hosts with http", () => {
  expect(normalizeBrowserUrl("localhost:5173")).toEqual({
    ok: true,
    url: "http://localhost:5173/",
  });
  expect(normalizeBrowserUrl("127.0.0.1:8765")).toEqual({
    ok: true,
    url: "http://127.0.0.1:8765/",
  });
});

it("keeps single-label input malformed and rejects bare private hosts", () => {
  expect(normalizeBrowserUrl("notaurl")).toEqual({ ok: false, reason: "malformed" });
  expect(normalizeBrowserUrl("192.168.1.1")).toEqual({ ok: false, reason: "private" });
});

it("keeps non-http schemes rejected", () => {
  expect(normalizeBrowserUrl("mailto:user@example.com")).toEqual({
    ok: false,
    reason: "scheme",
  });
});
```

创建 `desktop/browser-policy.test.cjs`：

```js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { MAX_URL_LENGTH, normalizeBrowserWindowUrl } = require("./browser-policy.cjs");

test("accepts http and https", () => {
  assert.equal(normalizeBrowserWindowUrl("https://example.com/a"), "https://example.com/a");
  assert.equal(normalizeBrowserWindowUrl("http://127.0.0.1:8765/"), "http://127.0.0.1:8765/");
});

test("rejects non-http schemes", () => {
  for (const value of ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,x"]) {
    assert.equal(normalizeBrowserWindowUrl(value), null);
  }
});

test("rejects credentials and oversized input", () => {
  assert.equal(normalizeBrowserWindowUrl("https://user:pass@example.com"), null);
  assert.equal(normalizeBrowserWindowUrl(`https://example.com/${"a".repeat(MAX_URL_LENGTH)}`), null);
});

test("rejects non-strings and blanks", () => {
  for (const value of [null, undefined, 42, {}, "", "   "]) {
    assert.equal(normalizeBrowserWindowUrl(value), null);
  }
});
```

- [ ] **Step 2：运行确认 RED**

```bash
cd webui && npm run test -- src/tests/browser-url.test.ts
node --test desktop/browser-policy.test.cjs
```

Expected: 裸域名仍 malformed；`Cannot find module './browser-policy.cjs'`。

- [ ] **Step 3：改 `browser-url.ts`**

在 `normalizeBrowserUrl` 前加：

```ts
const BARE_HOST = /^(?:localhost|\[[0-9a-f:.]+\]|(?:\d{1,3}\.){3}\d{1,3}|[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+)(?::\d{1,5})?(?:[/?#].*)?$/i;

function looksLikeBareHost(value: string): boolean {
  return BARE_HOST.test(value);
}

function withImplicitScheme(value: string): string {
  const host = value.match(/^(?:\[[^\]]+\]|[^:/?#]+)/)?.[0]?.toLowerCase() ?? "";
  const loopback =
    host === "localhost" ||
    host === "[::1]" ||
    host.startsWith("127.");
  return `${loopback ? "http" : "https"}://${value}`;
}
```

把 `normalizeBrowserUrl` 的解析段替换为：

```ts
  let url: URL | null = null;
  try {
    url = new URL(trimmed);
  } catch {
    url = null;
  }

  if (url && url.protocol !== "http:" && url.protocol !== "https:") {
    if (!looksLikeBareHost(trimmed)) return { ok: false, reason: "scheme" };
    url = null;
  }
  if (!url) {
    if (!looksLikeBareHost(trimmed)) return { ok: false, reason: "malformed" };
    try {
      url = new URL(withImplicitScheme(trimmed));
    } catch {
      return { ok: false, reason: "malformed" };
    }
  }

  if (url.username || url.password) {
    return { ok: false, reason: "malformed" };
  }
```

后续 hostname / 私有网段判断保持不变。

- [ ] **Step 4：新增桌面 policy**

`desktop/browser-policy.cjs`：

```js
"use strict";

const MAX_URL_LENGTH = 4096;

function normalizeBrowserWindowUrl(raw) {
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  if (!trimmed || trimmed.length > MAX_URL_LENGTH) return null;

  let parsed;
  try {
    parsed = new URL(trimmed);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  if (parsed.username || parsed.password) return null;
  return parsed.toString();
}

module.exports = { MAX_URL_LENGTH, normalizeBrowserWindowUrl };
```

- [ ] **Step 5：运行 policy 测试确认 GREEN**

```bash
node --test desktop/browser-policy.test.cjs
```

Expected: 4 tests passed。

- [ ] **Step 6：写浏览器持久化/独立窗口失败测试**

`browser-section.test.tsx` 追加：

```tsx
it("restores the browser history for its dock scope", () => {
  sessionStorage.setItem(
    "zhixu.dock.browser.websocket:home",
    JSON.stringify({ history: ["https://example.com/"], index: 0 }),
  );
  render(<BrowserSection scope="websocket:home" />);
  expect(screen.getByTestId(IFRAME)).toHaveAttribute(
    "src",
    "https://example.com/",
  );
  sessionStorage.clear();
});

it("opens the current page in a detached desktop window", () => {
  const openBrowserWindow = vi.fn().mockResolvedValue(true);
  (window as { mira?: { openBrowserWindow?: typeof openBrowserWindow } }).mira = {
    openBrowserWindow,
  };
  render(<BrowserSection scope="websocket:home" />);
  navigateTo("https://detached.example");
  fireEvent.click(screen.getByTestId("browser-detached"));
  expect(openBrowserWindow).toHaveBeenCalledWith(
    expect.stringContaining("detached.example"),
  );
});
```

- [ ] **Step 7：运行确认 RED**

```bash
cd webui && npm run test -- src/tests/browser-section.test.tsx
```

Expected: prop `scope` 不存在、`browser-detached` 找不到。

- [ ] **Step 8：改 runtime / preload**

`runtime.ts` 的 `MiraShellApi` 加：

```ts
  openBrowserWindow?: (url: string) => Promise<boolean>;
```

`desktop/preload.cjs` 加：

```js
  openBrowserWindow: (targetUrl) => ipcRenderer.invoke("mira:open-browser-window", targetUrl),
```

- [ ] **Step 9：改 `BrowserSection.tsx`**

导入补 `useRef` 与 `AppWindow`：

```tsx
import { useEffect, useRef, useState, type FormEvent } from "react";
import { AppWindow, ArrowLeft, ArrowRight, ExternalLink, RotateCw } from "lucide-react";
```

在组件外加：

```ts
const BROWSER_STATE_PREFIX = "zhixu.dock.browser.";
const MAX_STORED_HISTORY = 50;

interface StoredBrowserState {
  history: string[];
  index: number;
}

function loadBrowserState(scope?: string): StoredBrowserState {
  if (!scope) return { history: [], index: -1 };
  try {
    const raw = sessionStorage.getItem(BROWSER_STATE_PREFIX + scope);
    if (!raw) return { history: [], index: -1 };
    const parsed = JSON.parse(raw) as { history?: unknown; index?: unknown };
    if (!Array.isArray(parsed.history)) return { history: [], index: -1 };
    const history: string[] = [];
    for (const value of parsed.history) {
      if (typeof value !== "string") continue;
      const result = normalizeBrowserUrl(value);
      if (result.ok) history.push(result.url);
      if (history.length >= MAX_STORED_HISTORY) break;
    }
    const requested = typeof parsed.index === "number" ? Math.trunc(parsed.index) : -1;
    const index = Math.min(Math.max(requested, -1), history.length - 1);
    return { history, index };
  } catch {
    return { history: [], index: -1 };
  }
}
```

Props 与签名：

```ts
interface BrowserSectionProps {
  fullHeight?: boolean;
  /** dock scope; enables per-scope history persistence. */
  scope?: string;
}

export function BrowserSection({ fullHeight = false, scope }: BrowserSectionProps = {}) {
```

状态初始化改为：

```tsx
  const [initial] = useState(() => loadBrowserState(scope));
  const [draft, setDraft] = useState(
    initial.index >= 0 ? initial.history[initial.index] ?? "" : "",
  );
  const [historyStack, setHistoryStack] = useState<string[]>(initial.history);
  const [index, setIndex] = useState(initial.index);
```

在现有状态 effect 后加持久化 effect：

```tsx
  useEffect(() => {
    if (!scope) return;
    try {
      if (historyStack.length === 0) {
        sessionStorage.removeItem(BROWSER_STATE_PREFIX + scope);
      } else {
        sessionStorage.setItem(
          BROWSER_STATE_PREFIX + scope,
          JSON.stringify({ history: historyStack, index }),
        );
      }
    } catch {
      /* sessionStorage may be unavailable in private mode */
    }
  }, [scope, historyStack, index]);
```

在 `navigate` 后加：

```tsx
  const canDetach =
    typeof window !== "undefined" &&
    typeof window.mira?.openBrowserWindow === "function";

  const openInWindow = async (url: string) => {
    const shell = window.mira;
    if (!shell?.openBrowserWindow) return;
    const opened = await shell.openBrowserWindow(url).catch(() => false);
    if (!opened) openExternally(url);
  };
```

工具栏中保持既有“external 仅在 loaded 显示”，并在其后加：

```tsx
        {current && canDetach ? (
          <button
            type="button"
            data-testid="browser-detached"
            aria-label={t("dock.browserWindow", {
              defaultValue: "Open in browser window",
            })}
            title={t("dock.browserWindow", {
              defaultValue: "Open in browser window",
            })}
            onClick={() => void openInWindow(current)}
            className={buttonClass}
          >
            <AppWindow className="h-3.5 w-3.5" aria-hidden />
          </button>
        ) : null}
```

- [ ] **Step 10：改 Electron 主进程**

`desktop/main.cjs` 顶部加：

```js
const { normalizeBrowserWindowUrl } = require("./browser-policy.cjs");
```

变量区加 `let browserWindow = null;`。

在 `createWindow` 前加：

```js
function openBrowserWindow(rawUrl) {
  const target = normalizeBrowserWindowUrl(rawUrl);
  if (!target) return false;

  if (browserWindow && !browserWindow.isDestroyed()) {
    browserWindow.loadURL(target).catch(() => {});
    browserWindow.show();
    browserWindow.focus();
    return true;
  }

  browserWindow = new BrowserWindow({
    width: 1100,
    height: 760,
    minWidth: 640,
    minHeight: 420,
    title: "知序浏览器",
    autoHideMenuBar: true,
    backgroundColor: "#ffffff",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webviewTag: false,
    },
  });
  browserWindow.webContents.setWindowOpenHandler(({ url }) => {
    const safe = normalizeBrowserWindowUrl(url);
    if (safe) void shell.openExternal(safe);
    return { action: "deny" };
  });
  browserWindow.webContents.on("will-navigate", (event, url) => {
    if (!normalizeBrowserWindowUrl(url)) event.preventDefault();
  });
  browserWindow.webContents.on("will-attach-webview", (event) => {
    event.preventDefault();
  });
  browserWindow.on("closed", () => {
    browserWindow = null;
  });
  browserWindow.loadURL(target).catch(() => {});
  return true;
}
```

`app.whenReady` 的 IPC 注册区加：

```js
    ipcMain.handle("mira:open-browser-window", (_event, targetUrl) =>
      openBrowserWindow(targetUrl),
    );
```

`desktop/package.json`：

```json
"files": ["main.cjs", "preload.cjs", "browser-policy.cjs", "boot.html", "assets/**"],
"scripts": {
  "start": "node scripts/run.cjs start",
  "dev": "node scripts/run.cjs dev",
  "smoke": "node scripts/run.cjs smoke",
  "test:browser-policy": "node --test browser-policy.test.cjs",
  "dist": "electron-builder --win nsis",
  "dist:all": "electron-builder"
}
```

- [ ] **Step 11：运行确认 GREEN**

```bash
cd webui && npm run test -- src/tests/browser-url.test.ts src/tests/browser-section.test.tsx
cd webui && npm run build
node --test desktop/browser-policy.test.cjs
```

Expected: 全 PASS；build 通过；policy 4 passed。

- [ ] **Step 12：提交**

```bash
git add webui/src/lib/browser-url.ts webui/src/lib/runtime.ts webui/src/components/workbench/BrowserSection.tsx webui/src/tests/browser-url.test.ts webui/src/tests/browser-section.test.tsx desktop/browser-policy.cjs desktop/browser-policy.test.cjs desktop/preload.cjs desktop/main.cjs desktop/package.json
git commit -m "feat(desktop): 浏览器裸地址、状态恢复与独立窗口"
```

---

### Task 6（WU-06）：dock 外壳——标签持久化、重命名、keep-alive

「Harness：orchestration.dispatch」
Skills: test-driven-development@harness-kit/.agents/skills/test-driven-development/SKILL.md loaded

**Files:**
- Modify: `webui/src/lib/dock-store.ts`
- Modify: `webui/src/components/dock/StartDock.tsx`
- Test: `webui/src/tests/dock-store.test.ts`
- Test: `webui/src/tests/start-dock.test.tsx`

- [ ] **Step 1：写 store 失败测试**

`dock-store.test.ts` 追加：

```ts
it("persists file/browser tabs and restores them per scope", () => {
  dockStore.openFile(HOME_DOCK_SCOPE);
  dockStore.openBrowser(HOME_DOCK_SCOPE);
  dockStore.renameTab(HOME_DOCK_SCOPE, "file", "Docs");
  dockStore.focusTab(HOME_DOCK_SCOPE, "browser");

  dockStore.resetForTests();
  const restored = dockStore.getSnapshot(HOME_DOCK_SCOPE);

  expect(restored.tabs.map((tab) => [tab.kind, tab.customTitle])).toEqual([
    ["file", "Docs"],
    ["browser", undefined],
  ]);
  expect(restored.activeId).toBe("browser");
});

it("renameTab clears back to the localized default on blank input", () => {
  dockStore.openFile(HOME_DOCK_SCOPE);
  dockStore.renameTab(HOME_DOCK_SCOPE, "file", "Docs");
  dockStore.renameTab(HOME_DOCK_SCOPE, "file", "   ");
  expect(
    dockStore.getSnapshot(HOME_DOCK_SCOPE).tabs[0].customTitle,
  ).toBeUndefined();
});
```

- [ ] **Step 2：写 StartDock 失败测试**

`start-dock.test.tsx` 追加：

```tsx
describe("StartDock tab shell v1.2", () => {
  it("renames a tab on double click", () => {
    renderDock();
    fireEvent.click(screen.getByTestId("dock-card-files"));
    fireEvent.doubleClick(screen.getByTestId("dock-tab-file"));
    const input = screen.getByTestId("dock-tab-rename-file");
    fireEvent.change(input, { target: { value: "Docs" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByTestId("dock-tab-file")).toHaveTextContent("Docs");
    expect(
      dockStore.getSnapshot(HOME_DOCK_SCOPE).tabs[0].customTitle,
    ).toBe("Docs");
  });

  it("closes a tab with the middle mouse button", () => {
    renderDock();
    fireEvent.click(screen.getByTestId("dock-card-files"));
    fireEvent.auxClick(screen.getByTestId("dock-tab-file"), { button: 1 });
    expect(screen.queryByTestId("dock-tab-file")).toBeNull();
  });

  it("keeps inactive panels mounted without stealing focus", async () => {
    const client = renderDock();
    await waitForShells();
    fireEvent.click(screen.getByTestId("dock-card-files"));
    expect(await screen.findByText("README.md")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("dock-card-terminal"));
    const id = client.terminalStart.mock.calls[0][0] as string;

    expect(screen.getByTestId("dock-panel-file")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByTestId(`dock-panel-${id}`)).toHaveAttribute("aria-hidden", "false");
    expect(screen.getByText("README.md")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("dock-tab-file"));
    expect(screen.getByTestId("dock-panel-file")).toHaveAttribute("aria-hidden", "false");
  });
});
```

- [ ] **Step 3：运行确认 RED**

```bash
cd webui && npm run test -- src/tests/dock-store.test.ts src/tests/start-dock.test.tsx
```

Expected: `renameTab` 不存在；`dock-tab-rename-file` / `dock-panel-*` 找不到。

- [ ] **Step 4：改 `dock-store.ts`**

`DockTab` 加：

```ts
  customTitle?: string;
```

常量区加：

```ts
const PERSIST_TABS_PREFIX = "nanobot.dock.tabs.";
```

文件顶部加持久化 tabs 的读写（放在 `savePersisted` 后）：

```ts
interface PersistedTab {
  id: string;
  kind: "file" | "browser";
  title: string;
  customTitle?: string;
}

interface PersistedTabs {
  tabs: PersistedTab[];
  activeId: string | null;
}

function loadPersistedTabs(scope: string): DockScopeSnapshot | null {
  try {
    const raw = sessionStorage.getItem(PERSIST_TABS_PREFIX + scope);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { tabs?: unknown; activeId?: unknown };
    if (!Array.isArray(parsed.tabs)) return null;

    const tabs: DockTab[] = [];
    for (const item of parsed.tabs) {
      if (!item || typeof item !== "object") continue;
      const entry = item as Partial<PersistedTab>;
      if (entry.kind !== "file" && entry.kind !== "browser") continue;
      if (entry.id !== entry.kind) continue;
      if (tabs.some((tab) => tab.id === entry.id)) continue;
      const customTitle =
        typeof entry.customTitle === "string" && entry.customTitle.trim()
          ? entry.customTitle.trim().slice(0, 80)
          : undefined;
      tabs.push({
        id: entry.id,
        kind: entry.kind,
        title: typeof entry.title === "string" ? entry.title : "",
        customTitle,
      });
    }
    if (tabs.length === 0) return null;

    const activeId =
      typeof parsed.activeId === "string" &&
      tabs.some((tab) => tab.id === parsed.activeId)
        ? parsed.activeId
        : tabs[0]?.id ?? null;
    return { tabs, activeId };
  } catch {
    return null;
  }
}

function savePersistedTabs(scope: string, snapshot: DockScopeSnapshot): void {
  const tabs: PersistedTab[] = snapshot.tabs
    .filter((tab) => tab.kind !== "terminal")
    .map((tab) => ({
      id: tab.id,
      kind: tab.kind as "file" | "browser",
      title: tab.title,
      ...(tab.customTitle ? { customTitle: tab.customTitle } : {}),
    }));
  const activeId =
    snapshot.activeId && tabs.some((tab) => tab.id === snapshot.activeId)
      ? snapshot.activeId
      : null;
  try {
    if (tabs.length === 0) {
      sessionStorage.removeItem(PERSIST_TABS_PREFIX + scope);
    } else {
      sessionStorage.setItem(
        PERSIST_TABS_PREFIX + scope,
        JSON.stringify({ tabs, activeId }),
      );
    }
  } catch {
    /* private mode */
  }
}
```

`DockStore` 加字段：

```ts
  private hydratedScopes = new Set<string>();
```

`getSnapshot` 改为：

```ts
  getSnapshot(scope: string): DockScopeSnapshot {
    if (!this.hydratedScopes.has(scope)) {
      this.hydratedScopes.add(scope);
      if (!this.snapshots.has(scope)) {
        const restored = loadPersistedTabs(scope);
        if (restored) this.snapshots.set(scope, restored);
      }
    }
    return this.snapshots.get(scope) ?? EMPTY_SNAPSHOT;
  }
```

`openSingleton` 中 `title` 改为空串（渲染时本地化）：

```ts
      this.setSnapshot(scope, {
        tabs: [...current.tabs, { id, kind, title: "" }],
        activeId: id,
      });
```

新增方法：

```ts
  renameTab(scope: string, tabId: string, title: string): void {
    const current = this.getSnapshot(scope);
    if (!current.tabs.some((tab) => tab.id === tabId)) return;
    const customTitle = title.trim().slice(0, 80);
    const tabs = current.tabs.map((tab) =>
      tab.id === tabId
        ? { ...tab, customTitle: customTitle || undefined }
        : tab,
    );
    this.setSnapshot(scope, { tabs, activeId: current.activeId });
  }
```

`setSnapshot` 末尾加：

```ts
    savePersistedTabs(scope, next);
```

`resetForTests` 加：

```ts
    this.hydratedScopes.clear();
```

- [ ] **Step 5：改 `StartDock.tsx`**

导入加 `DockTabKind` 与图标已存在，新增一个小图标组件（放在 `CARD_SPECS` 后）：

```tsx
function TabKindIcon({ kind }: { kind: DockTabKind }) {
  const Icon = kind === "file" ? Files : kind === "browser" ? Globe : SquareTerminal;
  return <Icon className="h-3 w-3 shrink-0 opacity-70" aria-hidden />;
}
```

导入：

```tsx
  type DockTabKind,
```

状态加：

```tsx
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
```

在 `closeTab` 后加：

```tsx
  const tabLabel = (tab: DockTab) => {
    if (tab.customTitle) return tab.customTitle;
    if (tab.kind === "file") return t("dock.tabFile", { defaultValue: "Files" });
    if (tab.kind === "browser") return t("dock.tabBrowser", { defaultValue: "Browser" });
    return tab.title;
  };

  const beginRename = (tab: DockTab) => {
    setRenamingId(tab.id);
    setRenameDraft(tabLabel(tab));
  };

  const commitRename = () => {
    if (!renamingId) return;
    dockStore.renameTab(scope, renamingId, renameDraft);
    setRenamingId(null);
  };
```

标签渲染块替换为：

```tsx
              <span
                key={tab.id}
                className={cn(
                  "flex shrink-0 items-center gap-0.5 rounded px-1",
                  isActive && "bg-muted/70",
                )}
              >
                {renamingId === tab.id ? (
                  <input
                    autoFocus
                    data-testid={`dock-tab-rename-${tab.id}`}
                    aria-label={t("dock.renameTab", { defaultValue: "Rename tab" })}
                    value={renameDraft}
                    maxLength={80}
                    onChange={(event) => setRenameDraft(event.target.value)}
                    onBlur={commitRename}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        commitRename();
                      } else if (event.key === "Escape") {
                        event.preventDefault();
                        setRenamingId(null);
                      }
                    }}
                    className="w-28 rounded border border-border/60 bg-background px-1 py-0.5 text-[11.5px] text-foreground outline-none focus-visible:ring-1 focus-visible:ring-ring/60"
                  />
                ) : (
                  <button
                    type="button"
                    role="tab"
                    aria-selected={isActive}
                    data-testid={`dock-tab-${tab.id}`}
                    onClick={() => dockStore.focusTab(scope, tab.id)}
                    onDoubleClick={() => beginRename(tab)}
                    onAuxClick={(event) => {
                      if (event.button !== 1) return;
                      event.preventDefault();
                      closeTab(tab);
                    }}
                    className={cn(
                      "flex max-w-[10rem] items-center gap-1 truncate rounded px-1.5 py-0.5 text-[11.5px] text-muted-foreground/80 hover:bg-muted/60 hover:text-foreground",
                      isActive && "font-medium text-foreground",
                    )}
                  >
                    <TabKindIcon kind={tab.kind} />
                    <span className="truncate">{tabLabel(tab)}</span>
                  </button>
                )}
                <button
                  type="button"
                  aria-label={t("dock.closeTab", { defaultValue: "Close tab" })}
                  data-testid={`dock-close-${tab.id}`}
                  onClick={() => closeTab(tab)}
                  className="grid h-4 w-4 shrink-0 place-items-center rounded text-muted-foreground/60 hover:bg-muted/60 hover:text-foreground"
                >
                  <X className="h-3 w-3" aria-hidden />
                </button>
              </span>
```

active 面板块替换为：

```tsx
        <div className="relative flex min-h-0 flex-1 flex-col">
          {snapshot.tabs.map((tab) => {
            const isActive = tab.id === snapshot.activeId;
            return (
              <div
                key={tab.id}
                data-testid={`dock-panel-${tab.id}`}
                aria-hidden={!isActive}
                className={cn(
                  "absolute inset-0 flex min-h-0 flex-col",
                  !isActive && "invisible pointer-events-none",
                )}
              >
                {tab.kind === "file" ? (
                  <DockFileTab
                    scope={scope}
                    sessionKey={sessionKey}
                    getToken={getToken}
                    onOpenFilePreview={onOpenFilePreview}
                  />
                ) : null}
                {tab.kind === "browser" ? (
                  <BrowserSection key={scope} fullHeight scope={scope} />
                ) : null}
                {tab.kind === "terminal" ? (
                  <TerminalSurface
                    terminalId={tab.id}
                    sessionKey={sessionKey}
                    active={isActive}
                  />
                ) : null}
              </div>
            );
          })}
        </div>
```

`activeTab` 仅保留给标题跟随 effect；删除不再使用的“仅渲染活动面板”分支。

- [ ] **Step 6：运行确认 GREEN**

```bash
cd webui && npm run test -- src/tests/dock-store.test.ts src/tests/start-dock.test.tsx
cd webui && npm run build
```

Expected: 全 PASS；build 通过。

- [ ] **Step 7：提交**

```bash
git add webui/src/lib/dock-store.ts webui/src/components/dock/StartDock.tsx webui/src/tests/dock-store.test.ts webui/src/tests/start-dock.test.tsx
git commit -m "feat(webui): dock 标签持久化、重命名与面板保活"
```

---

### Task 7（WU-07）：i18n 与集体验收

「Harness：orchestration.dispatch」
Skills: test-driven-development@harness-kit/.agents/skills/test-driven-development/SKILL.md loaded

**Files:**
- Modify: `webui/src/tests/i18n-dock-parity.test.ts`
- Modify: `webui/src/i18n/locales/{en,zh-CN,zh-TW,ja,ko,vi,fr,es,pt-BR,id}/common.json`
- Verification: `.ai-runtime-artifacts/verifications/2026-10-20-sidebar-tools-v1.2.md`

- [ ] **Step 1：更新 parity 键表**

`DOCK_KEYS` 增加：

```ts
  "renameTab",
  "copyOutput",
  "clearOutput",
  "pasteInput",
  "browserWindow",
  "showHidden",
  "hideHidden",
  "refresh",
  "collapseAll",
  "search",
  "searchPlaceholder",
  "searching",
  "noMatches",
  "searchTruncated",
  "searchFailed",
  "copied",
  "historyTruncated",
```

- [ ] **Step 2：运行确认 RED**

```bash
cd webui && npm run test -- src/tests/i18n-dock-parity.test.ts
```

Expected: 10 个 locale 的 dock 键集不等，列出缺失键。

- [ ] **Step 3：写入 10 个 locale**

在每个 `common.json` 的 `dock` 对象中追加以下键（保持既有键不变）：

**en**

```json
"renameTab": "Rename tab",
"copyOutput": "Copy output",
"clearOutput": "Clear terminal",
"pasteInput": "Paste",
"browserWindow": "Open in browser window",
"showHidden": "Show hidden files",
"hideHidden": "Hide hidden files",
"refresh": "Refresh",
"collapseAll": "Collapse all",
"search": "Search files",
"searchPlaceholder": "Search file names and contents",
"searching": "Searching…",
"noMatches": "No matches",
"searchTruncated": "Showing partial results",
"searchFailed": "Search failed",
"copied": "Copied",
"historyTruncated": "Earlier output was truncated"
```

**zh-CN**

```json
"renameTab": "重命名标签",
"copyOutput": "复制输出",
"clearOutput": "清空终端",
"pasteInput": "粘贴",
"browserWindow": "在浏览器窗口打开",
"showHidden": "显示隐藏文件",
"hideHidden": "隐藏隐藏文件",
"refresh": "刷新",
"collapseAll": "全部折叠",
"search": "搜索文件",
"searchPlaceholder": "搜索文件名与内容",
"searching": "搜索中…",
"noMatches": "无匹配结果",
"searchTruncated": "仅显示部分结果",
"searchFailed": "搜索失败",
"copied": "已复制",
"historyTruncated": "较早的输出已被截断"
```

**zh-TW**

```json
"renameTab": "重新命名分頁",
"copyOutput": "複製輸出",
"clearOutput": "清除終端機",
"pasteInput": "貼上",
"browserWindow": "在瀏覽器視窗開啟",
"showHidden": "顯示隱藏檔案",
"hideHidden": "隱藏隱藏檔案",
"refresh": "重新整理",
"collapseAll": "全部收合",
"search": "搜尋檔案",
"searchPlaceholder": "搜尋檔案名稱與內容",
"searching": "搜尋中…",
"noMatches": "沒有符合結果",
"searchTruncated": "僅顯示部分結果",
"searchFailed": "搜尋失敗",
"copied": "已複製",
"historyTruncated": "較早的輸出已被截斷"
```

**ja**

```json
"renameTab": "タブ名を変更",
"copyOutput": "出力をコピー",
"clearOutput": "ターミナルをクリア",
"pasteInput": "貼り付け",
"browserWindow": "ブラウザーウィンドウで開く",
"showHidden": "隠しファイルを表示",
"hideHidden": "隠しファイルを非表示",
"refresh": "更新",
"collapseAll": "すべて折りたたむ",
"search": "ファイルを検索",
"searchPlaceholder": "ファイル名と内容を検索",
"searching": "検索中…",
"noMatches": "一致なし",
"searchTruncated": "一部の結果のみ表示",
"searchFailed": "検索に失敗しました",
"copied": "コピーしました",
"historyTruncated": "以前の出力は切り捨てられました"
```

**ko**

```json
"renameTab": "탭 이름 바꾸기",
"copyOutput": "출력 복사",
"clearOutput": "터미널 지우기",
"pasteInput": "붙여넣기",
"browserWindow": "브라우저 창에서 열기",
"showHidden": "숨김 파일 표시",
"hideHidden": "숨김 파일 숨기기",
"refresh": "새로 고침",
"collapseAll": "모두 접기",
"search": "파일 검색",
"searchPlaceholder": "파일 이름 및 내용 검색",
"searching": "검색 중…",
"noMatches": "일치 항목 없음",
"searchTruncated": "일부 결과만 표시",
"searchFailed": "검색 실패",
"copied": "복사됨",
"historyTruncated": "이전 출력이 잘렸습니다"
```

**vi**

```json
"renameTab": "Đổi tên tab",
"copyOutput": "Sao chép đầu ra",
"clearOutput": "Xóa terminal",
"pasteInput": "Dán",
"browserWindow": "Mở trong cửa sổ trình duyệt",
"showHidden": "Hiện tệp ẩn",
"hideHidden": "Ẩn tệp ẩn",
"refresh": "Làm mới",
"collapseAll": "Thu gọn tất cả",
"search": "Tìm kiếm tệp",
"searchPlaceholder": "Tìm theo tên và nội dung tệp",
"searching": "Đang tìm…",
"noMatches": "Không có kết quả",
"searchTruncated": "Chỉ hiển thị một phần kết quả",
"searchFailed": "Tìm kiếm thất bại",
"copied": "Đã sao chép",
"historyTruncated": "Đầu ra trước đó đã bị cắt"
```

**fr**

```json
"renameTab": "Renommer l’onglet",
"copyOutput": "Copier la sortie",
"clearOutput": "Effacer le terminal",
"pasteInput": "Coller",
"browserWindow": "Ouvrir dans une fenêtre de navigateur",
"showHidden": "Afficher les fichiers cachés",
"hideHidden": "Masquer les fichiers cachés",
"refresh": "Actualiser",
"collapseAll": "Tout réduire",
"search": "Rechercher des fichiers",
"searchPlaceholder": "Rechercher par nom et contenu",
"searching": "Recherche…",
"noMatches": "Aucun résultat",
"searchTruncated": "Résultats partiels",
"searchFailed": "Échec de la recherche",
"copied": "Copié",
"historyTruncated": "La sortie précédente a été tronquée"
```

**es**

```json
"renameTab": "Renombrar pestaña",
"copyOutput": "Copiar salida",
"clearOutput": "Limpiar terminal",
"pasteInput": "Pegar",
"browserWindow": "Abrir en ventana del navegador",
"showHidden": "Mostrar archivos ocultos",
"hideHidden": "Ocultar archivos ocultos",
"refresh": "Actualizar",
"collapseAll": "Contraer todo",
"search": "Buscar archivos",
"searchPlaceholder": "Buscar por nombre y contenido",
"searching": "Buscando…",
"noMatches": "Sin coincidencias",
"searchTruncated": "Mostrando resultados parciales",
"searchFailed": "Error de búsqueda",
"copied": "Copiado",
"historyTruncated": "La salida anterior se truncó"
```

**pt-BR**

```json
"renameTab": "Renomear aba",
"copyOutput": "Copiar saída",
"clearOutput": "Limpar terminal",
"pasteInput": "Colar",
"browserWindow": "Abrir em janela do navegador",
"showHidden": "Mostrar arquivos ocultos",
"hideHidden": "Ocultar arquivos ocultos",
"refresh": "Atualizar",
"collapseAll": "Recolher tudo",
"search": "Pesquisar arquivos",
"searchPlaceholder": "Pesquisar por nome e conteúdo",
"searching": "Pesquisando…",
"noMatches": "Nenhum resultado",
"searchTruncated": "Mostrando resultados parciais",
"searchFailed": "Falha na pesquisa",
"copied": "Copiado",
"historyTruncated": "A saída anterior foi truncada"
```

**id**

```json
"renameTab": "Ganti nama tab",
"copyOutput": "Salin keluaran",
"clearOutput": "Bersihkan terminal",
"pasteInput": "Tempel",
"browserWindow": "Buka di jendela browser",
"showHidden": "Tampilkan file tersembunyi",
"hideHidden": "Sembunyikan file tersembunyi",
"refresh": "Segarkan",
"collapseAll": "Ciutkan semua",
"search": "Cari file",
"searchPlaceholder": "Cari nama dan isi file",
"searching": "Mencari…",
"noMatches": "Tidak ada hasil",
"searchTruncated": "Menampilkan sebagian hasil",
"searchFailed": "Pencarian gagal",
"copied": "Disalin",
"historyTruncated": "Keluaran sebelumnya terpotong"
```

- [ ] **Step 4：运行 i18n 与全量前端测试**

```bash
cd webui && npm run test -- src/tests/i18n-dock-parity.test.ts
cd webui && npm run test
cd webui && npm run build
```

Expected: parity PASS；全量 Vitest PASS；build 通过。

- [ ] **Step 5：全量后端验收**

```bash
.venv/Scripts/python.exe -m pytest tests/webui/test_workspace_files.py tests/test_webui_workspace_files.py tests/webui/test_terminal_scrollback.py tests/test_webui_pty_terminal.py tests/test_webui_terminal.py -q
.venv/Scripts/python.exe -m ruff check nanobot/webui/ tests/webui/test_workspace_files.py tests/webui/test_terminal_scrollback.py tests/test_webui_workspace_files.py tests/test_webui_pty_terminal.py tests/test_webui_terminal.py
.venv/Scripts/python.exe -m basedpyright nanobot/webui/workspace_files.py nanobot/webui/terminal_scrollback.py nanobot/webui/pty_terminal.py nanobot/webui/terminal_bridge.py nanobot/webui/outbound_wire.py
node --test desktop/browser-policy.test.cjs
```

Expected: 全 PASS；ruff/basedpyright 无新增错误；Windows 下 POSIX PTY 用例 skip 属正常。

- [ ] **Step 6：真实 GUI 冒烟**

启动 gateway 与 WebUI，按以下路径逐项操作并截图：

1. 首页 dock → Files：默认不出现 `.env` / `.git`；点“显示隐藏文件”后出现且样式弱化；点“搜索”输入仓库内真实字符串，结果可点击预览；点“全部折叠”后树收起；点“刷新”后根目录重新加载。
2. 首页 dock → Terminal：执行 `echo ALPHA`；切到 Files 再切回，输出仍在；点清屏后 xterm 清空；复制选区/全部输出；粘贴一行命令可执行。
3. 首页 dock → Browser：输入 `example.com` 可加载；刷新页面后历史仍在；桌面端出现独立窗口按钮，点击后打开无地址栏的独立窗口；`file://` 地址被拒绝。
4. 标签：双击 Files 标签改名为“文档”，中键关闭；刷新页面后文件/浏览器标签恢复、名字保留。
5. 会话页 embedded dock 重复 1–4 的关键路径，确认 scope 隔离（首页与会话互不影响）。

截图保存到：

```
.ai-runtime-artifacts/verifications/2026-10-20-sidebar-tools-v1.2-ui/
```

- [ ] **Step 7：写验证记录**

创建 `.ai-runtime-artifacts/verifications/2026-10-20-sidebar-tools-v1.2.md`，必须包含：

- 每条命令的原始输出摘要与退出码；
- GUI 五项的操作步骤、预期、实际结果、截图文件名；
- 未覆盖项与原因（例如 Windows 下 PTY 用例 skip、Electron 打包未执行）；
- 结论只能是 `PASS` / `PASS WITH NOTES` / `FAIL`。

- [ ] **Step 8：提交**

```bash
git add webui/src/tests/i18n-dock-parity.test.ts webui/src/i18n/locales .ai-runtime-artifacts/verifications/2026-10-20-sidebar-tools-v1.2.md
git commit -m "i18n(webui): 侧边栏工具 v1.2 十语言键表与验收记录"
```

---

## 7. 自审

### 7.1 需求覆盖

| 用户诉求 | 对应任务 |
| --- | --- |
| 文件默认隐藏敏感点文件 | WU-01 `is_hidden_entry_name` + WU-03 工具栏 |
| 文件搜索 | WU-01 后端有界搜索 + WU-03 防抖搜索面板 |
| 文件刷新/折叠 | WU-03 `refreshToken` / `collapseToken` |
| 终端重连后不丢输出 | WU-02 双 bridge scrollback + WU-04 快照重绘 |
| 终端复制/清屏/粘贴 | WU-04 工具栏 + `clipboard.ts` |
| 终端切换标签不丢滚动位置 | WU-06 keep-alive + WU-04 `active` |
| 浏览器裸域名 | WU-05 `browser-url.ts` |
| 浏览器刷新后历史保留 | WU-05 sessionStorage |
| 桌面端独立浏览器窗口 | WU-05 policy + main/preload |
| 标签重命名/中键关闭/刷新恢复 | WU-06 dock-store + StartDock |
| 多语言一致 | WU-07 |

### 7.2 占位符检查

- 无 `TBD` / `TODO` / “类似 Task N” / “补充适当错误处理”。
- 新增模块均给出完整代码；既有文件均给出可定位的替换块。
- 所有测试命令均带预期结果。

### 7.3 类型一致性

- `WorkspaceSearchPayload` / `WorkspaceSearchHit` 在 WU-03 定义一次，WU-04 不改。
- `TerminalRecord.viewEpoch` / `historyTruncated` 在 WU-04 定义，WU-06 只消费。
- `BrowserSection.scope` 在 WU-05 定义，WU-06 传入。
- `TerminalSurface.active` 在 WU-04 定义，WU-06 传入。
- `dockStore.renameTab` 在 WU-06 定义，StartDock 同单元消费。
- `terminal_snapshot` 同时出现在后端 TypedDict、前端 `TerminalEvent`、client 白名单，三处名称一致。

---

## 8. 风险与回滚

| 风险 | 处置 |
| --- | --- |
| keep-alive 让最多 8 个 xterm 同时存在，内存上升 | 每终端前端上限仍是 200k 字符 / 4000 行；必要时后续增加“非活动终端暂停渲染” |
| 搜索 2 s 截止可能截断大仓库结果 | 响应带 `truncated`，前端明确提示“仅显示部分结果”；不阻塞事件循环（同步遍历但受截止与文件上限约束） |
| 快照与实时输出竞争 | 前端用 `event.seq < lastSeq` 丢弃旧快照；快照整帧替换，不拼接 |
| Electron 独立窗口被导航到恶意协议 | `browser-policy.cjs` 白名单 + `will-navigate` + `will-attach-webview` + 无 preload |
| 旧网关不返回 `hidden` / 不支持搜索 | `hidden` 为可选字段；搜索 404 时前端显示失败态，不影响树浏览 |
| sessionStorage 损坏 | 所有读取 try/catch 并回退空状态 |

**回滚：** 每个工作单元独立提交，可单独 `git revert`。WU-01/02 为纯增量契约；WU-06 若 keep-alive 引发问题，可回退到“只渲染 activeTab”，其余功能不受影响。
