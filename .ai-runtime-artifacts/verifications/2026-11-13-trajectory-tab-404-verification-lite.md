---
route: Tier 1 Leader 直做
artifact: verification-lite
date: 2026-11-13
skills: [systematic-debugging, source-driven-development, verification-before-completion]
topic: 轨迹页 TRAJECTORY_NOT_FOUND（无轨迹库时误报 404）
---

# 验证：轨迹页在「尚无轨迹库」时返回空页而非 404

## 现象与定位

用户切到「轨迹」页看到黄色报错条 `TRAJECTORY_NOT_FOUND: trajectory is not available for this session`。

日志证据（`tmp/gateway-real.log` 18:28:44）：

```text
ERROR | trajectory store creation failed for C:\Users\Huangqh\Documents\Cursor使用规范\使用说明
  File "nanobot\trajectory\store.py", line 188, in __init__
    directory.mkdir(parents=True, exist_ok=True)
PermissionError: [WinError 5] 拒绝访问。: 'C:\Users\Huangqh\Documents\Cursor使用规范\使用说明\runtime'
```

两条独立问题：

1. 采集侧（环境）：该回合的工作区在 `Documents\...\使用说明`，启动 gateway 的进程令牌无权创建 `runtime/`，`store_for_write` 标记 degraded，该回合没有落盘。
2. 读取侧（代码）：`TrajectoryService._reader()` 在库文件不存在时返回 `None`，路由统一转成 404，前端把 404 当 error 渲染报错条；插入/契约口径是「空 session 的 durableSeq=0」，即应返回 200 空页（既有 `test_handler_hides_other_sessions_and_rejects_bad_limit` 已按此断言）。

## 改动

- `nanobot/trajectory/service.py`
  - 抽出 `_key()` / `_db_path()` / `_has_db()` / `_is_degraded()`。
  - `summary()` / `records_page()` / `events_page()`：库文件从未创建时返回契约空页
    （`durableSeq=0`、`snapshotSeq=0`、`retainedFromSeq=1`、`historyGeneration=1`、`records/events=[]`）；
    `summary.available` 取 `not degraded`，前端据此渲染既有「轨迹不可用」空态。
  - 库存在但打不开 / 无权限 / `span`、`body` 查询：语义不变（仍 404 / 410）。
- `tests/webui/test_trajectory_routes.py`
  - 新增 `test_missing_database_serves_empty_pages`（无库 → 200 空页，span 仍 404）。
  - 新增 `test_unwritable_workspace_reports_unavailable`（`runtime` 被文件占位 → begin_run 失败 → `available=false`）。

## 验证证据

```text
.venv/Scripts/python.exe -m pytest tests/webui/test_trajectory_routes.py tests/trajectory -q
  → 15 passed in 3.72s
.venv/Scripts/python.exe -m ruff check nanobot/trajectory/service.py tests/webui/test_trajectory_routes.py
  → All checks passed!
```

真实工作区读取（进程内，同一 operations 装配）：

```text
SUMMARY: {"mode": "standard", "available": true, "durableSeq": 0, "retainedFromSeq": 1, ...}
RECORDS: {"records": [], "snapshotSeq": 0, "hasOlder": false, ...}
EVENTS : {"events": [], "durableSeq": 0, "nextAfterSeq": 0, ...}
```

## 运行侧待办

- 该修复需重启 gateway 才生效（当前进程 18:31:41 启动，只加载了 import 阶段的中间态）。
- 采集侧权限问题不在代码内：需用正常用户令牌重启 gateway；`runtime/` 目录已由本次排查创建，sqlite 写入已用同一解释器实测可写（`SQLITE WRITE OK`）。
- `_degraded` 目前一旦写入就不会自动重试（重启才清），若权限恢复前已触发过失败，需重启 gateway 才会重新建库。
