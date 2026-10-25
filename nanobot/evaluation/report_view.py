"""离线 HTML 报告渲染器（契约 §5 导出边界 + 附录 A.4 / A.5）。

这是一个**纯函数**渲染器：只消费传入的 ``snapshot``（普通 dict，只含 JSON 可序列化值），
不读数据库、不读环境变量、不发网络请求、不依赖全局状态；同样的输入两次调用输出逐字节相同。

输出是自包含静态 HTML：无脚本、无外链、无 CDN、无远程字体、无网络请求；
不含 ``foreignObject``、事件属性、``javascript:``；文本与属性全部转义。
规模处理遵守契约 §4：工具节点超过阈值时流程图按分组渲染，明细表按预算分段，
达到导出上限时显式写明「已导出 N / 总 M」与截断范围，绝不谎称完整。
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["render_run_report"]


#: 数字缺失时统一显示的破折号（与真实 0 区分）。
DASH = "—"

DEFAULT_MAX_TOOL_ROWS_PER_TABLE = 200
DEFAULT_MAX_TOTAL_TOOL_ROWS = 10000
DEFAULT_TOOL_GRAPH_THRESHOLD = 40
DEFAULT_MAX_GRAPH_NODES = 80

#: 流程图布局参数（纯几何常量，不来自外部输入）。
_NODE_WIDTH = 172
_NODE_HEIGHT = 54
_COL_GAP = 18
_ROW_GAP = 30
_PAD = 18
_MAX_COLUMNS = 6

_KIND_LABELS = {
    "agent_activity": "智能体",
    "model_call": "模型",
    "tool_call": "工具",
    "judge": "评分",
    "output": "输出",
}
_KIND_ORDER = ("agent_activity", "model_call", "tool_call", "judge", "output")
_KIND_FILL = {
    "agent_activity": "#eaf3ef",
    "model_call": "#edf3fa",
    "tool_call": "#f3eff9",
    "judge": "#fbf3e8",
    "output": "#eef1f4",
}
_KIND_STROKE = {
    "agent_activity": "#4b7f6f",
    "model_call": "#4f7cb4",
    "tool_call": "#8a72b5",
    "judge": "#a97a3c",
    "output": "#78838f",
}
_STATUS_COLORS = {
    "succeeded": "#4b7f6f",
    "failed": "#c05f5c",
    "running": "#a97a3c",
    "cancelled": "#8b939c",
    "unknown": "#b0b7bf",
}

_EVENT_ATTR_RE = re.compile(r"\bon([A-Za-z][A-Za-z0-9_-]*)=")
_WHITESPACE_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------
# 基础工具：转义与格式化
# --------------------------------------------------------------------------


def _escape_text(value: Any) -> str:
    """把任意值转成可安全放进 HTML 文本或属性位置的字符串。

    除了 ``& < > " '`` 的常规转义，还把协议冒号、事件属性赋值号编码成实体：
    输出源码里因此不会出现 ``http://``、``javascript:``、``onerror=`` 这类字面量，
    而浏览器渲染出来的文字与原文完全一致。
    """
    if value is None:
        return ""
    escaped = html.escape(str(value), quote=True)
    escaped = escaped.replace("http://", "http&#58;//")
    escaped = escaped.replace("https://", "https&#58;//")
    escaped = escaped.replace("javascript:", "javascript&#58;")
    escaped = escaped.replace("foreignObject", "foreign&#79;bject")
    return _EVENT_ATTR_RE.sub(r"on\1&#61;", escaped)


def _cell(value: Any) -> str:
    """表格单元格：缺失显示 ``—``，其余转义。"""
    if value is None:
        return DASH
    return _escape_text(value)


def _number(value: Any) -> str:
    """数字单元格：缺失显示 ``—``，真实 0 显示 ``0``。"""
    if value is None:
        return DASH
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return _escape_text(value)


def _duration(value: Any) -> str:
    """耗时单元格（毫秒）：缺失显示 ``—``。"""
    if value is None or isinstance(value, bool):
        return DASH
    if isinstance(value, (int, float)):
        return f"{int(value):,} ms"
    return _escape_text(value)


def _percent(value: float | None) -> str:
    if value is None:
        return DASH
    return f"{value:.1f}%"


def _plain(value: Any) -> str:
    """折叠空白后的纯文本（用于 SVG 标签与摘要），仍做转义。"""
    if value is None:
        return ""
    return _WHITESPACE_RE.sub(" ", str(value)).strip()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _positive_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value > 0:
        return value
    return default


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value >= 0:
        return int(value)
    return None


# --------------------------------------------------------------------------
# HTML 片段构造
# --------------------------------------------------------------------------


def _table(
    headers: Sequence[tuple[str, str]],
    rows: Sequence[Sequence[str]],
    *,
    css_class: str = "",
) -> str:
    parts = [f'<table class="{_escape_text(css_class)}">', "<thead><tr>"]
    for label, cell_class in headers:
        parts.append(
            f'<th scope="col" class="{_escape_text(cell_class)}">{_escape_text(label)}</th>'
        )
    parts.append("</tr></thead><tbody>")
    if rows:
        for row in rows:
            parts.append("<tr>")
            for index, value in enumerate(row):
                cell_class = headers[index][1] if index < len(headers) else ""
                parts.append(f'<td class="{_escape_text(cell_class)}">{value}</td>')
            parts.append("</tr>")
    else:
        parts.append(f'<tr><td class="empty" colspan="{len(headers)}">暂无数据</td></tr>')
    parts.append("</tbody></table>")
    return "".join(parts)


def _section(section_id: str, number: str, title: str, body: str) -> str:
    return (
        f'<section class="report-section" id="{_escape_text(section_id)}">'
        f'<h2><span class="section-number">{_escape_text(number)}</span> {_escape_text(title)}</h2>'
        f"{body}</section>"
    )


def _definition_table(pairs: Sequence[tuple[str, str]]) -> str:
    rows = [[_escape_text(key), value] for key, value in pairs]
    return _table((("项目", ""), ("说明", "")), rows, css_class="kv-table")


def _metric_cards(metrics: Sequence[tuple[str, str, str, str]]) -> str:
    parts = ['<div class="metric-grid">']
    for key, label, value, note in metrics:
        parts.append(
            f'<div class="metric" data-metric="{_escape_text(key)}">'
            f'<div class="metric-label">{_escape_text(label)}</div>'
            f'<div class="metric-value">{value}</div>'
            f'<div class="metric-note">{_escape_text(note)}</div>'
            "</div>"
        )
    parts.append("</div>")
    return "".join(parts)


# --------------------------------------------------------------------------
# 统计口径
# --------------------------------------------------------------------------


def _verdict_counts(attempts: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {
        "passed": 0,
        "failed": 0,
        "review_required": 0,
        "not_evaluated": 0,
        "unknown": 0,
    }
    for attempt in attempts:
        verdict = attempt.get("verdict")
        key = str(verdict) if verdict is not None else "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _sum_tokens(rows: Sequence[Mapping[str, Any]]) -> int | None:
    """求和已知 Token；只要有一行未知就整体标未知（不拿 0 冒充未知）。"""
    if not rows:
        return None
    total = 0
    for row in rows:
        value = row.get("total_tokens")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        total += int(value)
    return total


def _unique_agents(
    usage_rows: Sequence[Mapping[str, Any]], nodes: Sequence[Mapping[str, Any]]
) -> int | None:
    ids: set[str] = set()
    for row in usage_rows:
        if str(row.get("role") or "") != "agent":
            continue
        agent_id = row.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            ids.add(agent_id)
    if usage_rows:
        return len(ids)
    for node in nodes:
        if str(node.get("kind") or "") != "agent_activity":
            continue
        agent_id = node.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            ids.add(agent_id)
    return len(ids) if ids else None


def _role_token_split(
    usage_rows: Sequence[Mapping[str, Any]],
    attempts: Sequence[Mapping[str, Any]],
) -> tuple[int | None, int | None]:
    """Agent / Judge 分账：优先用运行级账本行，缺失时回落到用例级台账。"""
    agent_rows = [row for row in usage_rows if str(row.get("role") or "") == "agent"]
    judge_rows = [row for row in usage_rows if str(row.get("role") or "") == "judge"]
    if usage_rows:
        return _sum_tokens(agent_rows), _sum_tokens(judge_rows)
    agent_values: list[Mapping[str, Any]] = []
    judge_values: list[Mapping[str, Any]] = []
    for attempt in attempts:
        usage = _mapping(attempt.get("usage"))
        agent_part = _mapping(usage.get("agent"))
        judge_part = _mapping(usage.get("judge"))
        if agent_part:
            agent_values.append(agent_part)
        if judge_part:
            judge_values.append(judge_part)
    agent_total = _sum_tokens(agent_values) if agent_values else None
    judge_total = _sum_tokens(judge_values) if judge_values else None
    return agent_total, judge_total


# --------------------------------------------------------------------------
# 工具分组
# --------------------------------------------------------------------------


def _group_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("attempt_id") or ""),
        str(row.get("execution_id") or ""),
        str(row.get("phase_id") or ""),
        str(row.get("tool_name") or ""),
    )


def _group_tool_calls(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """按 (用例, execution, 阶段, 工具名) 归并；计数按 call_id，不重复计入。"""
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = _group_key(row)
        group = groups.get(key)
        if group is None:
            group = {
                "attempt_id": key[0],
                "execution_id": key[1],
                "phase_id": key[2],
                "tool_name": key[3],
                "count": 0,
                "succeeded": 0,
                "failed": 0,
                "running": 0,
                "cancelled": 0,
                "unknown": 0,
                "duration_ms": 0,
                "unknown_duration": 0,
                "call_ids": [],
            }
            groups[key] = group
        group["count"] += 1
        status = str(row.get("status") or "unknown")
        if status in ("succeeded", "failed", "running", "cancelled"):
            group[status] += 1
        else:
            group["unknown"] += 1
        duration = row.get("duration_ms")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            group["duration_ms"] += int(duration)
        else:
            group["unknown_duration"] += 1
        call_id = row.get("call_id")
        if isinstance(call_id, str):
            group["call_ids"].append(call_id)
    return list(groups.values())


# --------------------------------------------------------------------------
# 静态 SVG 流程图
# --------------------------------------------------------------------------


def _node_label(node: Mapping[str, Any]) -> str:
    kind = str(node.get("kind") or "")
    if kind == "tool_call":
        return str(node.get("tool_name") or node.get("label") or "工具")
    if kind == "model_call":
        return str(node.get("model") or node.get("label") or "模型")
    if kind == "agent_activity":
        return str(node.get("agent_name") or node.get("agent_id") or node.get("label") or "智能体")
    return str(node.get("label") or _KIND_LABELS.get(kind, "节点"))


def _node_sublabel(node: Mapping[str, Any]) -> str:
    parts: list[str] = []
    status = node.get("status")
    if status:
        parts.append(str(status))
    duration = node.get("duration_ms")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        parts.append(f"{int(duration)} ms")
    return " · ".join(parts)


def _collect_graph_items(
    nodes: Sequence[Mapping[str, Any]],
    *,
    threshold: int,
    max_nodes: int,
) -> tuple[list[dict[str, Any]], bool, int]:
    """把节点整理成可绘制条目；工具节点超过阈值或节点总数超预算时按分组折叠。"""
    tool_nodes = [node for node in nodes if str(node.get("kind") or "") == "tool_call"]
    grouped = len(tool_nodes) > threshold or len(nodes) > max_nodes
    items: list[dict[str, Any]] = []
    alias: dict[str, str] = {}
    if not grouped:
        for node in nodes:
            node_id = str(node.get("id") or "")
            if not node_id:
                continue
            items.append(
                {
                    "id": node_id,
                    "kind": str(node.get("kind") or "output"),
                    "label": _node_label(node),
                    "sublabel": _node_sublabel(node),
                    "status": str(node.get("status") or "unknown"),
                    "count": 1,
                }
            )
            alias[node_id] = node_id
        return items, grouped, len(tool_nodes)

    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for node in nodes:
        kind = str(node.get("kind") or "")
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        if kind != "tool_call":
            items.append(
                {
                    "id": node_id,
                    "kind": kind,
                    "label": _node_label(node),
                    "sublabel": _node_sublabel(node),
                    "status": str(node.get("status") or "unknown"),
                    "count": 1,
                }
            )
            alias[node_id] = node_id
            continue
        key = (
            str(node.get("execution_id") or ""),
            str(node.get("phase_id") or ""),
            str(node.get("tool_name") or ""),
        )
        groups.setdefault(key, []).append(node)

    for key, members in groups.items():
        group_id = "group:" + "|".join(key)
        statuses = [str(member.get("status") or "unknown") for member in members]
        failed = sum(1 for status in statuses if status == "failed")
        duration = 0
        for member in members:
            value = member.get("duration_ms")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                duration += int(value)
        status = (
            "failed"
            if failed
            else ("unknown" if all(s == "unknown" for s in statuses) else "succeeded")
        )
        items.append(
            {
                "id": group_id,
                "kind": "tool_call",
                "label": f"{key[2] or '工具'} ×{len(members)}",
                "sublabel": f"{len(members)} 次 · 失败 {failed} · {duration} ms",
                "status": status,
                "count": len(members),
                "group": True,
            }
        )
        for member in members:
            member_id = str(member.get("id") or "")
            if member_id:
                alias[member_id] = group_id
    return items, grouped, len(tool_nodes)


def _layout_items(
    items: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, tuple[float, float]], list[list[Mapping[str, Any]]]]:
    """按「行 = 泳道、列 = 行内序号」布局，返回 (id → (x, y), 行分组)。"""
    lanes: dict[str, list[Mapping[str, Any]]] = {}
    for item in items:
        kind = str(item.get("kind") or "output")
        lanes.setdefault(kind, []).append(item)
    ordered: list[Mapping[str, Any]] = []
    for kind in _KIND_ORDER:
        ordered.extend(lanes.pop(kind, []))
    for kind in sorted(lanes):
        ordered.extend(lanes[kind])

    chunks = _lane_chunks(ordered)
    positions: dict[str, tuple[float, float]] = {}
    for row_index, lane_items in enumerate(chunks):
        for column, item in enumerate(lane_items):
            x = _PAD + column * (_NODE_WIDTH + _COL_GAP)
            y = _PAD + row_index * (_NODE_HEIGHT + _ROW_GAP)
            positions[str(item.get("id"))] = (x, y)
    return positions, chunks


def _lane_chunks(items: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    """按泳道切分行：同一 kind 的节点在同一行内排列，超出列上限换行。"""
    chunks: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    current_kind: str | None = None
    for item in items:
        kind = str(item.get("kind") or "output")
        if current_kind is not None and kind != current_kind and len(current) >= _MAX_COLUMNS:
            chunks.append(current)
            current = []
        elif current_kind is not None and kind != current_kind:
            chunks.append(current)
            current = []
        current_kind = kind
        current.append(item)
        if len(current) >= _MAX_COLUMNS:
            chunks.append(current)
            current = []
            current_kind = None
    if current:
        chunks.append(current)
    return chunks


def _render_svg(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    *,
    threshold: int,
    max_nodes: int,
) -> str:
    items, grouped, tool_count = _collect_graph_items(
        nodes, threshold=threshold, max_nodes=max_nodes
    )
    truncated = 0
    if len(items) > max_nodes:
        truncated = len(items) - max_nodes
        items = items[:max_nodes]

    alias: dict[str, str] = {}
    for item in items:
        alias[str(item.get("id"))] = str(item.get("id"))
    if grouped:
        # 重新计算别名（分组模式下原节点 id 指向分组条目）
        alias = _group_alias(nodes)

    positions, chunks = _layout_items(items)
    columns = max((len(chunk) for chunk in chunks), default=0)
    rows = len(chunks)
    width = int(_PAD * 2 + max(1, columns) * _NODE_WIDTH + max(0, columns - 1) * _COL_GAP)
    height = int(_PAD * 2 + max(1, rows) * _NODE_HEIGHT + max(0, rows - 1) * _ROW_GAP)

    parts = [
        f'<svg class="flow-svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="用例执行流程静态图">',
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="10" fill="#fbfcfd" stroke="#e3e7ec" />',
    ]

    for edge in edges:
        source_id = alias.get(str(edge.get("from") or ""))
        target_id = alias.get(str(edge.get("to") or ""))
        if not source_id or not target_id:
            continue
        source = positions.get(source_id)
        target = positions.get(target_id)
        if source is None or target is None:
            continue
        x1, y1 = source
        x2, y2 = target
        if x2 >= x1:
            start = (x1 + _NODE_WIDTH, y1 + _NODE_HEIGHT / 2)
            end = (x2, y2 + _NODE_HEIGHT / 2)
        else:
            start = (x1, y1 + _NODE_HEIGHT / 2)
            end = (x2 + _NODE_WIDTH, y2 + _NODE_HEIGHT / 2)
        mid = (start[0] + end[0]) / 2
        path = (
            f"M {start[0]:.1f} {start[1]:.1f} "
            f"C {mid:.1f} {start[1]:.1f}, {mid:.1f} {end[1]:.1f}, {end[0]:.1f} {end[1]:.1f}"
        )
        parts.append(f'<path d="{path}" fill="none" stroke="#c3cad2" stroke-width="1.2" />')

    for item in items:
        item_id = str(item.get("id"))
        position = positions.get(item_id)
        if position is None:
            continue
        x, y = position
        kind = str(item.get("kind") or "output")
        status = str(item.get("status") or "unknown")
        fill = _KIND_FILL.get(kind, "#eef1f4")
        stroke = _KIND_STROKE.get(kind, "#78838f")
        label = _truncate(_plain(item.get("label")) or _KIND_LABELS.get(kind, "节点"), 16)
        sublabel = _truncate(_plain(item.get("sublabel")), 24)
        parts.append('<g class="flow-node">')
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{_NODE_WIDTH}" height="{_NODE_HEIGHT}" rx="8" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.1" />'
        )
        parts.append(
            f'<circle cx="{x + 14:.1f}" cy="{y + 17:.1f}" r="4" '
            f'fill="{_STATUS_COLORS.get(status, "#b0b7bf")}" />'
        )
        parts.append(
            f'<text x="{x + 25:.1f}" y="{y + 21:.1f}" font-size="12" font-weight="600" '
            f'fill="#1f262e">{_escape_text(label)}</text>'
        )
        kind_text = _KIND_LABELS.get(kind, "节点")
        parts.append(
            f'<text x="{x + 12:.1f}" y="{y + 40:.1f}" font-size="10.5" fill="#78838f">'
            f"{_escape_text(f'{kind_text} · {sublabel}')}</text>"
        )
        parts.append("</g>")

    parts.append("</svg>")
    notes = [f"节点 {len(items)}", f"工具节点 {tool_count}"]
    notes.append("分组渲染" if grouped else "完整渲染")
    if truncated:
        notes.append(f"超出可见上限 {max_nodes}，省略 {truncated} 个节点")
    caption = " · ".join(notes)
    return (
        f'<figure class="flowchart"><figcaption>{_escape_text(caption)}</figcaption>'
        + "".join(parts)
        + "</figure>"
    )


def _group_alias(nodes: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """分组模式下，把原始节点 id 映射到分组条目 id。"""
    alias: dict[str, str] = {}
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for node in nodes:
        kind = str(node.get("kind") or "")
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        if kind != "tool_call":
            alias[node_id] = node_id
            continue
        key = (
            str(node.get("execution_id") or ""),
            str(node.get("phase_id") or ""),
            str(node.get("tool_name") or ""),
        )
        groups.setdefault(key, []).append(node)
    for key, members in groups.items():
        group_id = "group:" + "|".join(key)
        for member in members:
            member_id = str(member.get("id") or "")
            if member_id:
                alias[member_id] = group_id
    return alias


# --------------------------------------------------------------------------
# 报告区块
# --------------------------------------------------------------------------


def _render_header(run: Mapping[str, Any], generated_at: Any) -> str:
    run_id = run.get("id")
    title = f"知序评估运行报告 · {run_id}" if run_id else "知序评估运行报告"
    meta_rows = [
        ("运行 ID", _cell(run.get("id"))),
        ("数据集", _cell(run.get("suite_name") or run.get("suite_id"))),
        ("数据集 ID", _cell(run.get("suite_id"))),
        ("运行状态", _cell(run.get("status"))),
        ("模型", _cell(run.get("model"))),
        ("模式", _cell(run.get("mode"))),
        ("创建时间", _cell(run.get("created_at"))),
        ("开始时间", _cell(run.get("started_at"))),
        ("结束时间", _cell(run.get("finished_at"))),
        ("生成时间", _cell(generated_at)),
    ]
    return (
        '<header class="report-head">'
        f"<h1>{_escape_text(title)}</h1>"
        '<p class="report-sub">自包含离线静态报告 · 无脚本 · 无外部资源</p>'
        + _definition_table(meta_rows)
        + "</header>"
    )


def _render_scope(
    run: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    tool_calls: Sequence[Mapping[str, Any]],
    *,
    attempts_known: bool = True,
    tool_calls_known: bool = True,
) -> str:
    attempt_count: int | None = len(attempts) if attempts_known else None
    tool_call_count: int | None = len(tool_calls) if tool_calls_known else None
    scope_rows = [
        ("数据集", _cell(run.get("suite_name") or run.get("suite_id"))),
        ("运行 ID", _cell(run.get("id"))),
        ("用例数", _number(attempt_count)),
        ("已结束用例数", _number(_terminal_count(run, attempts) if attempts_known else None)),
        ("工具调用数", _number(tool_call_count)),
        ("计划用例数", _number(run.get("planned_count"))),
    ]
    statement = (
        f"本报告仅覆盖运行 {_plain(run.get('id')) or '（未知运行）'} 的 "
        f"{attempt_count if attempt_count is not None else '未知'} 个用例与 "
        f"{tool_call_count if tool_call_count is not None else '未知'} 次工具调用；"
        "数据集历史记录不在本报告范围内，报告内任何单个用例的调用数都不代表整轮规模。"
    )
    return _definition_table(scope_rows) + f'<p class="scope-note">{_escape_text(statement)}</p>'


def _terminal_count(run: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]]) -> int | None:
    value = run.get("terminal_count")
    count = _non_negative_int(value)
    if count is not None:
        return count
    if not attempts:
        return 0
    terminal = 0
    for attempt in attempts:
        status = str(attempt.get("execution_status") or "")
        if status in ("completed", "failed", "cancelled", "timeout", "skipped"):
            terminal += 1
    return terminal


def _render_metrics(
    attempts: Sequence[Mapping[str, Any]],
    tool_calls: Sequence[Mapping[str, Any]],
    usage_rows: Sequence[Mapping[str, Any]],
    totals: Mapping[str, Any],
    nodes: Sequence[Mapping[str, Any]],
    *,
    attempts_known: bool = True,
    tool_calls_known: bool = True,
) -> str:
    counts = _verdict_counts(attempts)
    evaluated = counts["passed"] + counts["failed"]
    pass_rate = (counts["passed"] / evaluated) if evaluated else None
    agent_tokens, judge_tokens = _role_token_split(usage_rows, attempts)
    unique_agents = _unique_agents(usage_rows, nodes)
    total_tokens = totals.get("total_tokens")
    physical_requests = totals.get("physical_requests")
    unknown_metric = None if attempts_known else DASH

    metrics = [
        (
            "total_cases",
            "总用例数",
            _number(len(attempts) if attempts_known else None),
            "本次运行记录的用例",
        ),
        (
            "passed",
            "通过",
            unknown_metric if unknown_metric else _number(counts["passed"]),
            "verdict = passed",
        ),
        (
            "failed",
            "失败",
            unknown_metric if unknown_metric else _number(counts["failed"]),
            "verdict = failed",
        ),
        (
            "review_required",
            "待复核",
            unknown_metric if unknown_metric else _number(counts["review_required"]),
            "verdict = review_required",
        ),
        (
            "not_evaluated",
            "未评测",
            unknown_metric
            if unknown_metric
            else _number(counts["not_evaluated"] + counts["unknown"]),
            "未评测或结论缺失",
        ),
        (
            "pass_rate",
            "通过率",
            _percent(pass_rate) if attempts_known else DASH,
            "通过 ÷（通过 + 失败）",
        ),
        ("unique_agents", "唯一执行智能体数", _number(unique_agents), "按 agent_id 去重"),
        (
            "tool_calls",
            "工具调用数",
            _number(len(tool_calls) if tool_calls_known else None),
            "按 call_id 去重",
        ),
        (
            "physical_requests",
            "物理请求数",
            _number(physical_requests),
            "整轮唯一物理请求主账本",
        ),
        ("token_total", "Token 合计", _number(total_tokens), "输入 + 输出，未知显示 —"),
        ("agent_tokens", "Agent Token 合计", _number(agent_tokens), "按 role = agent 分账"),
        ("judge_tokens", "Judge Token 合计", _number(judge_tokens), "按 role = judge 分账"),
    ]
    return _metric_cards(metrics)


def _render_usage_table(
    usage_rows: Sequence[Mapping[str, Any]],
    totals: Mapping[str, Any],
) -> str:
    headers = (
        ("维度键", ""),
        ("角色", ""),
        ("智能体", ""),
        ("提供方", ""),
        ("模型", ""),
        ("输入 Token", "num"),
        ("输出 Token", "num"),
        ("合计 Token", "num"),
        ("物理请求", "num"),
        ("缺失用量调用", "num"),
        ("来源", ""),
        ("覆盖", ""),
    )
    rows: list[list[str]] = []
    for row in usage_rows:
        rows.append(
            [
                _cell(row.get("key")),
                _cell(row.get("role")),
                _cell(row.get("agent_name") or row.get("agent_id")),
                _cell(row.get("provider")),
                _cell(row.get("model")),
                _number(row.get("input_tokens")),
                _number(row.get("output_tokens")),
                _number(row.get("total_tokens")),
                _number(row.get("physical_requests")),
                _number(row.get("missing_usage_calls")),
                _cell(row.get("source")),
                _cell(row.get("coverage")),
            ]
        )
    total_row = [
        "合计",
        DASH,
        DASH,
        DASH,
        DASH,
        _number(totals.get("input_tokens")),
        _number(totals.get("output_tokens")),
        _number(totals.get("total_tokens")),
        _number(totals.get("physical_requests")),
        _number(totals.get("missing_usage_calls")),
        DASH,
        DASH,
    ]
    body = _table(headers, rows, css_class="usage-table")
    total_table = _table(headers, [total_row], css_class="usage-table usage-total")
    return body + total_table


def _render_agent_table(
    nodes: Sequence[Mapping[str, Any]],
    tool_calls: Sequence[Mapping[str, Any]],
    usage_rows: Sequence[Mapping[str, Any]],
) -> str:
    """参与智能体表：谁参与了本轮、执行了几次、用了什么模型、消耗多少 Token。

    身份只来自真实采集到的 agent_id / execution_id：没采到就是「未归属」，
    不按模型名或工具名反推智能体。
    """
    agents: dict[str, dict[str, Any]] = {}

    def _bucket(key: str) -> dict[str, Any]:
        return agents.setdefault(
            key,
            {
                "name": None,
                "agent_id": None,
                "executions": set(),
                "parents": set(),
                "models": set(),
                "tool_calls": 0,
                "requests": 0,
                "tokens": [],
                "roles": set(),
                "coverage": set(),
            },
        )

    for node in nodes:
        if str(node.get("kind") or "") != "agent_activity":
            continue
        agent_id = node.get("agent_id")
        key = str(agent_id) if isinstance(agent_id, str) and agent_id else "unassigned"
        bucket = _bucket(key)
        bucket["agent_id"] = agent_id if isinstance(agent_id, str) else None
        name = node.get("agent_name")
        if isinstance(name, str) and name:
            bucket["name"] = name
        execution_id = node.get("execution_id")
        if isinstance(execution_id, str) and execution_id:
            bucket["executions"].add(execution_id)
        parent_id = node.get("parent_execution_id")
        if isinstance(parent_id, str) and parent_id:
            bucket["parents"].add(parent_id)

    for call in tool_calls:
        agent_id = call.get("agent_id")
        key = str(agent_id) if isinstance(agent_id, str) and agent_id else "unassigned"
        bucket = _bucket(key)
        bucket["tool_calls"] += 1
        name = call.get("agent_name")
        if isinstance(name, str) and name and not bucket["name"]:
            bucket["name"] = name

    for row in usage_rows:
        agent_id = row.get("agent_id")
        key = str(agent_id) if isinstance(agent_id, str) and agent_id else "unassigned"
        bucket = _bucket(key)
        bucket["requests"] += int(row.get("physical_requests") or 0)
        total = row.get("total_tokens")
        if isinstance(total, int) and not isinstance(total, bool):
            bucket["tokens"].append(total)
        model = row.get("model")
        if isinstance(model, str) and model:
            bucket["models"].add(model)
        role = row.get("role")
        if isinstance(role, str) and role:
            bucket["roles"].add(role)
        coverage = row.get("coverage")
        if isinstance(coverage, str) and coverage:
            bucket["coverage"].add(coverage)

    headers = (
        ("智能体", ""),
        ("agent_id", ""),
        ("执行次数", "num"),
        ("父执行", ""),
        ("模型", ""),
        ("角色", ""),
        ("工具调用", "num"),
        ("物理请求", "num"),
        ("合计 Token", "num"),
        ("覆盖", ""),
    )
    rows: list[list[str]] = []
    for key in sorted(agents):
        bucket = agents[key]
        tokens = bucket["tokens"]
        rows.append(
            [
                _cell(bucket["name"] or ("未归属" if key == "unassigned" else key)),
                _cell(bucket["agent_id"]),
                _number(len(bucket["executions"])),
                _cell(", ".join(sorted(bucket["parents"])) or None),
                _cell(", ".join(sorted(bucket["models"])) or None),
                _cell(", ".join(sorted(bucket["roles"])) or None),
                _number(bucket["tool_calls"]),
                _number(bucket["requests"]),
                _number(sum(tokens)) if tokens else DASH,
                _cell(", ".join(sorted(bucket["coverage"])) or None),
            ]
        )
    return _table(headers, rows, css_class="agent-table")


def _render_attempt_table(attempts: Sequence[Mapping[str, Any]]) -> str:
    headers = (
        ("用例名", ""),
        ("case_id", ""),
        ("revision", "num"),
        ("执行状态", ""),
        ("判分状态", ""),
        ("结论", ""),
        ("错误数", "num"),
        ("耗时", "num"),
    )
    rows: list[list[str]] = []
    for attempt in attempts:
        errors = attempt.get("errors")
        if isinstance(errors, list):
            error_count: int | None = len(errors)
        else:
            error_count = None
        timing = _mapping(attempt.get("timing"))
        total_ms = timing.get("total_ms")
        rows.append(
            [
                _cell(attempt.get("case_name")),
                _cell(attempt.get("case_id")),
                _number(attempt.get("case_revision")),
                _cell(attempt.get("execution_status")),
                _cell(attempt.get("grading_status")),
                _cell(attempt.get("verdict")),
                _number(error_count),
                _duration(total_ms),
            ]
        )
    return _table(headers, rows, css_class="attempt-table")


def _render_attempt_details(attempts: Sequence[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for index, attempt in enumerate(attempts, start=1):
        summary = _mapping(attempt.get("judge_summary"))
        rows = [
            ["模型", _cell(summary.get("model"))],
            ["状态", _cell(summary.get("status"))],
            ["评分", _cell(summary.get("score"))],
            ["校准", _cell(summary.get("calibrated"))],
        ]
        errors = attempt.get("errors")
        error_html = '<p class="muted">无错误记录</p>'
        if isinstance(errors, list) and errors:
            items = []
            for error in errors:
                error_map = _mapping(error)
                items.append(
                    "<li>"
                    f"{_escape_text(error_map.get('phase'))} · {_escape_text(error_map.get('code'))}"
                    f"：{_escape_text(error_map.get('message'))}</li>"
                )
            error_html = '<ul class="error-list">' + "".join(items) + "</ul>"
        parts.append(
            f'<details class="aux"><summary>用例 {index} 的判分摘要与错误明细'
            f"（{_escape_text(attempt.get('case_name'))}）</summary>"
            + _definition_table(rows)
            + error_html
            + "</details>"
        )
    return "".join(parts)


def _render_flowcharts(
    attempts: Sequence[Mapping[str, Any]],
    execution: Sequence[Mapping[str, Any]],
    *,
    threshold: int,
    max_nodes: int,
) -> str:
    by_attempt: dict[str, Mapping[str, Any]] = {}
    for entry in execution:
        attempt_id = str(entry.get("attempt_id") or "")
        if attempt_id:
            by_attempt.setdefault(attempt_id, entry)

    parts: list[str] = []
    if not attempts:
        parts.append('<p class="empty-state">本次运行没有用例记录，无执行流程可绘制。</p>')
        return "".join(parts)

    for index, attempt in enumerate(attempts, start=1):
        attempt_id = str(attempt.get("id") or "")
        case_name = attempt.get("case_name")
        heading = f"用例 {index} · {case_name}" if case_name else f"用例 {index}"
        parts.append(f'<article class="flow-case"><h3>{_escape_text(heading)}</h3>')
        parts.append(
            f'<p class="muted">用例 ID：{_escape_text(attempt.get("id"))} · '
            f"case_id：{_escape_text(attempt.get('case_id'))}</p>"
        )
        entry = by_attempt.get(attempt_id)
        if entry is None:
            parts.append('<p class="empty-state">该用例没有可用的执行流程数据。</p>')
            parts.append("</article>")
            continue
        nodes = [node for node in _sequence(entry.get("nodes")) if isinstance(node, Mapping)]
        edges = [edge for edge in _sequence(entry.get("edges")) if isinstance(edge, Mapping)]
        if not nodes:
            parts.append('<p class="empty-state">该用例没有可用的执行节点。</p>')
            parts.append("</article>")
            continue
        parts.append(_render_svg(nodes, edges, threshold=threshold, max_nodes=max_nodes))
        coverage = _mapping(entry.get("coverage"))
        parts.append(
            '<p class="muted">覆盖：trace '
            f"{_escape_text(coverage.get('trace'))} · identity {_escape_text(coverage.get('identity'))}"
            f" · usage {_escape_text(coverage.get('usage_attribution'))}</p>"
        )
        parts.append("</article>")
    return "".join(parts)


def _render_tool_group_table(rows: Sequence[Mapping[str, Any]]) -> str:
    headers = (
        ("用例", ""),
        ("execution", ""),
        ("阶段", ""),
        ("工具名", ""),
        ("数量", "num"),
        ("成功", "num"),
        ("失败", "num"),
        ("运行中", "num"),
        ("已取消", "num"),
        ("未知", "num"),
        ("耗时", "num"),
    )
    groups = _group_tool_calls(rows)
    table_rows: list[list[str]] = []
    for group in groups:
        duration = group["duration_ms"]
        table_rows.append(
            [
                _cell(group["attempt_id"] or None),
                _cell(group["execution_id"] or None),
                _cell(group["phase_id"] or None),
                _cell(group["tool_name"] or None),
                _number(group["count"]),
                _number(group["succeeded"]),
                _number(group["failed"]),
                _number(group["running"]),
                _number(group["cancelled"]),
                _number(group["unknown"]),
                _duration(duration) if group["count"] else DASH,
            ]
        )
    return _table(headers, table_rows, css_class="tool-group-table")


def _render_abnormal_table(rows: Sequence[Mapping[str, Any]]) -> str:
    headers = (
        ("call_id", ""),
        ("用例", ""),
        ("execution", ""),
        ("阶段", ""),
        ("工具名", ""),
        ("智能体", ""),
        ("状态", ""),
        ("耗时", "num"),
        ("错误", ""),
    )
    abnormal = [
        row
        for row in rows
        if str(row.get("status") or "unknown") in ("failed", "cancelled", "unknown")
    ]
    table_rows: list[list[str]] = []
    for row in abnormal:
        table_rows.append(
            [
                _cell(row.get("call_id")),
                _cell(row.get("attempt_id")),
                _cell(row.get("execution_id")),
                _cell(row.get("phase_id")),
                _cell(row.get("tool_name")),
                _cell(row.get("agent_name") or row.get("agent_id")),
                _cell(row.get("status")),
                _duration(row.get("duration_ms")),
                _cell(row.get("error")),
            ]
        )
    note = (
        f'<p class="muted">异常调用共 {len(abnormal)} 条'
        f"（failed / cancelled / unknown），明细不因分组而隐藏。</p>"
    )
    return note + _table(headers, table_rows, css_class="abnormal-table")


def _render_tool_call_tables(
    rows: Sequence[Mapping[str, Any]],
    *,
    max_rows_per_table: int,
    max_total_rows: int,
) -> tuple[str, str]:
    """返回（表格 HTML, 导出说明）。"""
    total = len(rows)
    exported = min(total, max_total_rows)
    selected = rows[:exported]
    headers = (
        ("#", "num"),
        ("call_id", ""),
        ("用例", ""),
        ("execution", ""),
        ("阶段", ""),
        ("工具名", ""),
        ("智能体", ""),
        ("状态", ""),
        ("耗时", "num"),
        ("参数摘要", ""),
        ("结果摘要", ""),
        ("错误", ""),
    )
    chunks: list[list[Mapping[str, Any]]] = [
        selected[index : index + max_rows_per_table]
        for index in range(0, len(selected), max_rows_per_table)
    ]
    parts: list[str] = []
    segment_total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        start_no = (index - 1) * max_rows_per_table + 1
        end_no = start_no + len(chunk) - 1
        caption = (
            f"第 {index} / {segment_total} 段 · 本段第 {start_no}–{end_no} 行 · "
            f"每表最多 {max_rows_per_table} 行"
        )
        table_rows = [
            [
                _number(start_no + offset),
                _cell(row.get("call_id")),
                _cell(row.get("attempt_id")),
                _cell(row.get("execution_id")),
                _cell(row.get("phase_id")),
                _cell(row.get("tool_name")),
                _cell(row.get("agent_name") or row.get("agent_id")),
                _cell(row.get("status")),
                _duration(row.get("duration_ms")),
                _cell(row.get("input_summary")),
                _cell(row.get("output_summary")),
                _cell(row.get("error")),
            ]
            for offset, row in enumerate(chunk)
        ]
        parts.append(
            f'<h3 class="segment-title">{_escape_text(caption)}</h3>'
            + _table(headers, table_rows, css_class="tool-table")
        )
    if not parts:
        parts.append('<p class="empty-state">本次运行没有工具调用记录。</p>')
    summary = f"已导出 {exported} / 总 {total} 次工具调用"
    if exported < total:
        summary += (
            f"；已按导出预算上限截断，第 {exported + 1}–{total} 行未导出"
            f"（上限 {max_total_rows} 行）。本报告不声称包含全部调用。"
        )
    else:
        summary += "，本次导出无截断。"
    return "".join(parts), summary


def _render_methodology(
    run: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    execution: Sequence[Mapping[str, Any]],
    usage_rows: Sequence[Mapping[str, Any]],
    totals: Mapping[str, Any],
    *,
    exported: int,
    total_calls: int,
    max_total_rows: int,
    max_rows_per_table: int,
    threshold: int,
    max_nodes: int,
) -> str:
    counts = _verdict_counts(attempts)
    agent_tokens, judge_tokens = _role_token_split(usage_rows, attempts)
    missing_calls = totals.get("missing_usage_calls")
    unattributed = _sum_tokens(
        [row for row in usage_rows if str(row.get("source") or "") == "unavailable"]
    )
    trace_coverage = sorted(
        {
            str(_mapping(entry.get("coverage")).get("trace"))
            for entry in execution
            if _mapping(entry.get("coverage")).get("trace")
        }
    )
    usage_coverage = sorted({str(row.get("coverage")) for row in usage_rows if row.get("coverage")})
    reasons: list[str] = []
    for entry in execution:
        coverage = _mapping(entry.get("coverage"))
        for reason in _sequence(coverage.get("reasons")):
            reasons.append(str(reason))

    rows = [
        (
            "范围边界",
            f"本报告只描述单次运行 {_plain(run.get('id')) or '（未知运行）'}；"
            f"数据集历史记录、跨运行对比与历史失败统计不在本报告范围内。",
        ),
        (
            "用例口径",
            f"本次运行记录 {len(attempts)} 个用例："
            f"通过 {counts['passed']}、失败 {counts['failed']}、"
            f"待复核 {counts['review_required']}、未评测 {counts['not_evaluated']}。",
        ),
        (
            "未知与零",
            "“—”表示未知、未采集或不可归因；真实为零的数字显示“0”。"
            "未知用量绝不用 0 代替，缺失的耗时不会被当作 0 毫秒。",
        ),
        (
            "覆盖与缺失",
            f"Trace 覆盖：{('、'.join(trace_coverage) if trace_coverage else DASH)}；"
            f"用量覆盖：{('、'.join(usage_coverage) if usage_coverage else DASH)}。"
            "覆盖不完整时，异常计数与耗时里的未知部分不会补零。",
        ),
        (
            "工具调用计数",
            "按 call_id 去重计数，started/completed 事件合并为一次调用；"
            "分组键为 execution × 阶段 × 工具名，跨用例、跨执行、跨阶段的同名工具不合并。",
        ),
        (
            "流程图口径",
            f"工具节点超过 {threshold} 个时流程图按分组渲染（不逐条铺开），"
            f"可见节点上限 {max_nodes} 个；超出部分在明细表与分组概览中可查。",
        ),
        (
            "Token 分账",
            f"Agent 合计 {_number(agent_tokens)}，Judge 合计 {_number(judge_tokens)}；"
            "Agent 与 Judge 不共享去重桶，重放同一 model_call_id 不重复计数。",
        ),
        (
            "未归属 Token",
            f"来源为 unavailable 的账本行合计 {_number(unattributed)} Token；"
            f"缺失用量调用 {missing_calls if isinstance(missing_calls, int) else DASH} 次"
            "（未归属的调用不摊派到任何智能体）。",
        ),
        (
            "导出完整性",
            f"已导出 {exported} / 总 {total_calls} 次工具调用（指逐次调用明细表）；"
            f"单表最多 {max_rows_per_table} 行，整轮导出上限 {max_total_rows} 行。"
            + (
                "本次导出未被截断。"
                if exported >= total_calls
                else "超出上限的调用未出现在逐次明细表中。"
            ),
        ),
        (
            "聚合与明细的关系",
            "工具分组概览与异常调用表按全部可用调用统计，"
            "逐次调用明细表按导出预算截断；两者口径不同，不可互相替代。",
        ),
        (
            "离线阅读",
            "报告是自包含静态 HTML：无脚本、无外链、无 CDN、无远程字体，"
            "流程图为原生静态 SVG，离线可直接打开与打印。",
        ),
    ]
    if reasons:
        rows.append(("缺口原因", "；".join(dict.fromkeys(reasons))))
    return _definition_table(rows)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def render_run_report(snapshot: Mapping[str, Any]) -> str:
    """把评估运行快照渲染成自包含的静态离线 HTML 报告。

    参数是附录 A.5 定义的快照形状；函数不访问数据库、不读环境变量、不发网络请求，
    同样输入两次调用输出完全一致。
    """
    data = _mapping(snapshot)
    run = _mapping(data.get("run"))
    attempts = [item for item in _sequence(data.get("attempts")) if isinstance(item, Mapping)]
    execution = [item for item in _sequence(data.get("execution")) if isinstance(item, Mapping)]
    tool_calls = [item for item in _sequence(data.get("tool_calls")) if isinstance(item, Mapping)]
    usage = _mapping(data.get("usage"))
    usage_rows = [item for item in _sequence(usage.get("rows")) if isinstance(item, Mapping)]
    totals = _mapping(usage.get("totals"))
    options = _mapping(data.get("options"))

    attempts_known = "attempts" in data
    tool_calls_known = "tool_calls" in data
    max_rows_per_table = _positive_int(
        options.get("max_tool_rows_per_table"), DEFAULT_MAX_TOOL_ROWS_PER_TABLE
    )
    max_total_rows = _positive_int(options.get("max_total_tool_rows"), DEFAULT_MAX_TOTAL_TOOL_ROWS)
    threshold = _positive_int(options.get("tool_graph_threshold"), DEFAULT_TOOL_GRAPH_THRESHOLD)
    max_nodes = _positive_int(options.get("max_graph_nodes"), DEFAULT_MAX_GRAPH_NODES)
    generated_at = options.get("generated_at")

    all_nodes = [
        node
        for entry in execution
        for node in _sequence(entry.get("nodes"))
        if isinstance(node, Mapping)
    ]

    tool_tables, export_summary = _render_tool_call_tables(
        tool_calls,
        max_rows_per_table=max_rows_per_table,
        max_total_rows=max_total_rows,
    )
    exported = min(len(tool_calls), max_total_rows)

    body = [
        _render_header(run, generated_at),
        _section(
            "section-scope",
            "01",
            "报告范围",
            _render_scope(
                run,
                attempts,
                tool_calls,
                attempts_known=attempts_known,
                tool_calls_known=tool_calls_known,
            ),
        ),
        _section(
            "section-metrics",
            "02",
            "摘要指标",
            _render_metrics(
                attempts,
                tool_calls,
                usage_rows,
                totals,
                all_nodes,
                attempts_known=attempts_known,
                tool_calls_known=tool_calls_known,
            ),
        ),
        _section(
            "section-agents",
            "03",
            "参与智能体",
            _render_agent_table(all_nodes, tool_calls, usage_rows),
        ),
        _section(
            "section-usage",
            "04",
            "Token 分账",
            _render_usage_table(usage_rows, totals),
        ),
        _section(
            "section-attempts",
            "05",
            "用例结果",
            _render_attempt_table(attempts) + _render_attempt_details(attempts),
        ),
        _section(
            "section-flowcharts",
            "06",
            "执行流程图",
            _render_flowcharts(attempts, execution, threshold=threshold, max_nodes=max_nodes),
        ),
        _section(
            "section-tool-groups",
            "07",
            "工具分组概览",
            _render_tool_group_table(tool_calls),
        ),
        _section(
            "section-abnormal",
            "08",
            "异常调用",
            _render_abnormal_table(tool_calls),
        ),
        _section(
            "section-tool-calls",
            "09",
            "全量工具调用",
            f'<p class="export-summary">{_escape_text(export_summary)}</p>' + tool_tables,
        ),
        _section(
            "section-methodology",
            "10",
            "口径说明",
            _render_methodology(
                run,
                attempts,
                execution,
                usage_rows,
                totals,
                exported=exported,
                total_calls=len(tool_calls),
                max_total_rows=max_total_rows,
                max_rows_per_table=max_rows_per_table,
                threshold=threshold,
                max_nodes=max_nodes,
            ),
        ),
    ]

    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_escape_text(_document_title(run))}</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        '<main class="paper">\n' + "\n".join(body) + "\n</main>\n"
        "</body>\n"
        "</html>\n"
    )


def _document_title(run: Mapping[str, Any]) -> str:
    run_id = run.get("id")
    if run_id:
        return f"知序评估运行报告 · {run_id}"
    return "知序评估运行报告"


_CSS = """
:root {
  --bg: #f4f6f8;
  --paper: #ffffff;
  --ink: #1f262e;
  --muted: #78838f;
  --line: #e3e7ec;
  --brand: #4b7f6f;
  --brand-soft: #eaf3ef;
  --blue-soft: #edf3fa;
  --purple-soft: #f3eff9;
  --red: #c05f5c;
  --radius: 10px;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-size: 13px;
  line-height: 1.6;
  font-family: system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
}
.paper {
  max-width: 1080px;
  margin: 0 auto;
  background: var(--paper);
  padding: 36px 40px 56px;
  border-left: 1px solid var(--line);
  border-right: 1px solid var(--line);
}
h1, h2, h3, p, figure, ul { margin: 0; }
h1 { font-size: 24px; font-weight: 650; letter-spacing: -0.4px; }
h2 {
  font-size: 15px;
  font-weight: 620;
  margin: 34px 0 14px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--line);
}
h3 { font-size: 13px; font-weight: 600; margin: 18px 0 8px; }
.section-number { color: var(--brand); font-variant-numeric: tabular-nums; margin-right: 8px; }
.report-sub { color: var(--muted); margin-top: 6px; }
.report-head { padding-bottom: 4px; }
.muted { color: var(--muted); }
.empty-state { color: var(--muted); padding: 10px 0; }
.scope-note { margin-top: 10px; color: var(--muted); }
.export-summary { font-weight: 600; margin-bottom: 10px; }
.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.metric { border: 1px solid var(--line); border-radius: var(--radius); padding: 12px 14px; }
.metric-label { color: var(--muted); font-size: 11px; }
.metric-value { font-size: 22px; font-weight: 600; margin: 4px 0 2px; font-variant-numeric: tabular-nums; }
.metric-note { color: var(--muted); font-size: 10.5px; }
table {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0 16px;
  font-variant-numeric: tabular-nums;
}
caption { caption-side: top; text-align: left; color: var(--muted); padding-bottom: 6px; }
th, td { border: 1px solid var(--line); padding: 7px 9px; text-align: left; vertical-align: top; }
th { background: #f7f9fa; font-weight: 600; font-size: 11.5px; }
td.num, th.num { text-align: right; }
td.empty { color: var(--muted); text-align: center; }
td { word-break: break-word; }
.kv-table th:first-child { width: 190px; }
.usage-total td { font-weight: 600; background: #fbfcfd; }
.flow-case { border-top: 1px solid var(--line); padding-top: 14px; margin-top: 14px; }
.flowchart { margin: 10px 0 6px; }
.flowchart figcaption { color: var(--muted); font-size: 11px; margin-bottom: 6px; }
.flow-svg { max-width: 100%; height: auto; }
details.aux { border: 1px solid var(--line); border-radius: var(--radius); padding: 8px 12px; margin: 10px 0; }
details.aux summary { cursor: pointer; color: var(--muted); }
.error-list { margin: 8px 0 4px 18px; }
@media print {
  body { background: #ffffff; font-size: 11px; }
  .paper { max-width: 100%; padding: 0; border: 0; }
  thead { display: table-header-group; }
  tr { break-inside: avoid; }
  h2 { break-after: avoid; }
  .flow-svg { max-width: 100%; }
}
"""
