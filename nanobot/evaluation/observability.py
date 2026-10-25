"""从 Trace v1 派生执行图、工具调用和 Token 账本的只读视图。

读模型只认 Trace 里真实存在的事件：工具调用按 ``call_id`` 折叠生命周期，模型请求按
``model_call_id``（旧记录回退 ``event_id``）去重，因此重放事件不会重复计数。旧事件没有
智能体身份、``execution_id`` 或稳定模型请求 ID 时保留为空并降低 coverage，绝不按时间戳
猜测父子关系，也不虚构父节点。

字段名与 ``webui/src/lib/evaluation-api.ts`` 的类型一一对应（契约附录 A.1–A.3）：
服务端只产出 ``tool_call`` / ``model_call`` / ``judge`` / ``agent_activity`` 节点，
工具分组由前端投影层完成，避免两套归并口径。
"""

from __future__ import annotations

import base64
import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from nanobot.evaluation.schemas import TraceEvent

Coverage = Literal["complete", "partial", "unavailable"]
ToolCallStatus = Literal["running", "succeeded", "failed", "cancelled", "unknown"]

_TOOL_TYPES = frozenset({"tool.started", "tool.completed", "tool.failed"})
_TOOL_ERROR_TYPES = frozenset({"tool.failed"})
_MODEL_EVENT = "model.call_completed"
#: 单个摘要字段的最大字符数：报告和表格都不该把整段工具输出塞进浏览器。
SUMMARY_MAX_CHARS = 240


# --------------------------------------------------------------------------- 基础工具


def _payload_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _payload_int(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _cursor(data: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(data), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(value: str | None, expected: Mapping[str, Any]) -> int:
    if value is None:
        return 0
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(decoded, dict) or any(decoded.get(k) != v for k, v in expected.items()):
            raise ValueError
        offset = decoded.get("offset")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError
        return offset
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("CURSOR_INVALID") from exc


def _execution_id(event: TraceEvent) -> str | None:
    return _payload_text(event.payload, "execution_id")


def _agent_id(event: TraceEvent) -> str | None:
    return _payload_text(event.payload, "agent_id")


def _agent_name(event: TraceEvent) -> str | None:
    return _payload_text(event.payload, "agent_name")


def _parent_execution_id(event: TraceEvent) -> str | None:
    return _payload_text(event.payload, "parent_execution_id")


def _phase_id(event: TraceEvent) -> str:
    explicit = _payload_text(event.payload, "phase_id")
    if explicit:
        return explicit
    return f"turn-{event.turn or 0}:iteration-{event.iteration or 0}"


def _summarize(value: Any) -> str | None:
    """把任意载荷压成一行摘要文本；不返回结构体，避免把原始 payload 带进页面。"""
    if value is None:
        return None
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float, bool)):
        text = str(value)
    elif isinstance(value, Mapping):
        try:
            text = json.dumps(dict(value), ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # pragma: no cover - default=str 兜底
            text = str(value)
    else:
        text = str(value)
    text = text.strip()
    if not text:
        return None
    if len(text) > SUMMARY_MAX_CHARS:
        return text[: SUMMARY_MAX_CHARS - 1] + "…"
    return text


def _usage_source(payload: Mapping[str, Any]) -> str:
    if payload.get("reported_tokens") is True:
        return "reported"
    if payload.get("estimated_tokens") is True:
        return "estimated"
    if any(payload.get(key) is not None for key in ("input_tokens", "output_tokens", "total_tokens")):
        return "reported"
    return "unavailable"


def _model_status(payload: Mapping[str, Any]) -> str:
    if payload.get("error_status_code") is not None or payload.get("error_kind"):
        return "failed"
    return "succeeded"


def _iso_from_epoch_ms(value: int | None) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


# --------------------------------------------------------------------------- 工具调用


def build_tool_calls(events: Sequence[TraceEvent], *, attempt_id: str) -> list[dict[str, Any]]:
    """把工具事件折叠成调用级明细；started/completed 只形成一行。"""
    calls: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for event in sorted(events, key=lambda item: item.seq):
        if event.type not in _TOOL_TYPES:
            continue
        name = _payload_text(event.payload, "name")
        if name is None:
            continue
        call_id = event.call_id or f"seq-{event.seq}"
        row = calls.setdefault(
            call_id,
            {
                "call_id": call_id,
                "attempt_id": attempt_id,
                "execution_id": None,
                "phase_id": _phase_id(event),
                "tool_name": name,
                "agent_id": None,
                "agent_name": None,
                "status": "running" if event.type == "tool.started" else "unknown",
                "started_at": event.ts,
                "duration_ms": None,
                "input_summary": None,
                "output_summary": None,
                "error": None,
                "retry_of_call_id": None,
                "started_seq": event.seq,
                "finished_seq": None,
                "evidence_refs": [],
            },
        )
        row["started_seq"] = min(int(row["started_seq"]), event.seq)
        row["execution_id"] = row["execution_id"] or _execution_id(event)
        row["agent_id"] = row["agent_id"] or _agent_id(event)
        row["agent_name"] = row["agent_name"] or _agent_name(event)
        row["retry_of_call_id"] = row["retry_of_call_id"] or _payload_text(
            event.payload, "retry_of_call_id"
        )
        evidence = str(event.event_id)
        if evidence not in row["evidence_refs"]:
            row["evidence_refs"].append(evidence)
        arguments = event.payload.get("arguments")
        if arguments is not None and row["input_summary"] is None:
            row["input_summary"] = _summarize(arguments)
        if event.type in {"tool.completed", "tool.failed"}:
            failed = event.type in _TOOL_ERROR_TYPES or event.payload.get("is_error") is True
            row["status"] = "failed" if failed else "succeeded"
            row["finished_seq"] = event.seq
            duration = _payload_int(event.payload, "duration_ms")
            row["duration_ms"] = duration if duration is not None and duration >= 0 else None
            result = event.payload.get("result")
            summary = _summarize(result)
            if summary is not None:
                row["output_summary"] = summary
            if failed:
                row["error"] = summary or "TOOL_FAILED"
    rows: list[dict[str, Any]] = []
    for row in calls.values():
        rows.append({key: value for key, value in row.items() if key not in {"started_seq", "finished_seq"}})
    return rows


def build_tool_calls_page(
    events: Sequence[TraceEvent],
    *,
    attempt_id: str,
    execution_id: str | None = None,
    phase_id: str | None = None,
    name: str | None = None,
    status: str | None = None,
    q: str | None = None,
    after_key: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """返回调用级分页；``q`` 只匹配工具名、调用 ID 与智能体名称。"""
    if not 1 <= limit <= 200:
        raise ValueError("LIMIT_INVALID")
    filters = {
        "attempt_id": attempt_id,
        "execution_id": execution_id,
        "phase_id": phase_id,
        "name": name,
        "status": status,
        "q": q,
    }
    offset = _decode_cursor(after_key, filters)
    needle = (q or "").casefold()
    rows = []
    for row in build_tool_calls(events, attempt_id=attempt_id):
        if execution_id is not None and row["execution_id"] != execution_id:
            continue
        if phase_id is not None and row["phase_id"] != phase_id:
            continue
        if name is not None and row["tool_name"] != name:
            continue
        if status is not None and row["status"] != status:
            continue
        searchable = " ".join(
            str(row.get(key) or "") for key in ("tool_name", "call_id", "agent_name")
        ).casefold()
        if needle and needle not in searchable:
            continue
        rows.append(row)
    page = rows[offset : offset + limit]
    next_offset = offset + len(page)
    return {
        "data": page,
        "next_after_key": (
            _cursor({**filters, "offset": next_offset}) if next_offset < len(rows) else None
        ),
        "total_count": len(rows),
        "coverage": "complete",
    }


# --------------------------------------------------------------------------- 执行图


def _agent_nodes(events: Sequence[TraceEvent]) -> list[dict[str, Any]]:
    """按 execution 归并智能体活动节点；同一 execution 的多次活动只算一个身份。"""
    nodes: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for event in sorted(events, key=lambda item: item.seq):
        execution_id = _execution_id(event)
        agent_id = _agent_id(event)
        if execution_id is None and agent_id is None:
            continue
        key = execution_id or f"agent:{agent_id}"
        node = nodes.get(key)
        if node is None:
            node = {
                "id": f"agent:{key}",
                "kind": "agent_activity",
                "agent_id": agent_id,
                "agent_name": _agent_name(event),
                "execution_id": execution_id,
                "parent_execution_id": _parent_execution_id(event),
                "call_id": None,
                "model": None,
                "status": "unknown",
                "started_at": event.ts,
                "duration_ms": None,
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "usage_source": "unavailable",
                "evidence_refs": [],
                "label": _agent_name(event) or agent_id or execution_id,
                "tool_name": None,
                "phase_id": None,
                "input_summary": None,
                "output_summary": None,
            }
            nodes[key] = node
        node["agent_id"] = node["agent_id"] or agent_id
        node["agent_name"] = node["agent_name"] or _agent_name(event)
        node["parent_execution_id"] = node["parent_execution_id"] or _parent_execution_id(event)
        node["started_at"] = min(str(node["started_at"]), event.ts)
        evidence = str(event.event_id)
        if evidence not in node["evidence_refs"]:
            node["evidence_refs"].append(evidence)
    return list(nodes.values())


def _model_nodes(events: Sequence[TraceEvent], *, attempt_id: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for event in sorted(events, key=lambda item: item.seq):
        if event.type != _MODEL_EVENT:
            continue
        payload = event.payload
        request_id = _payload_text(payload, "model_call_id") or str(event.event_id)
        role = _payload_text(payload, "role") or "agent"
        dedupe = (attempt_id, request_id)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        input_tokens = _payload_int(payload, "input_tokens")
        output_tokens = _payload_int(payload, "output_tokens")
        total_tokens = _payload_int(payload, "total_tokens")
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        nodes.append(
            {
                "id": f"model:{attempt_id}:{request_id}",
                "kind": "judge" if role == "judge" else "model_call",
                "agent_id": _agent_id(event),
                "agent_name": _agent_name(event),
                "execution_id": _execution_id(event),
                "parent_execution_id": _parent_execution_id(event),
                "call_id": request_id,
                "model": _payload_text(payload, "model"),
                "status": _model_status(payload),
                "started_at": _iso_from_epoch_ms(_payload_int(payload, "started_at_ms")) or event.ts,
                "duration_ms": _payload_int(payload, "duration_ms"),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "usage_source": _usage_source(payload),
                "evidence_refs": [str(event.event_id)],
                "label": _payload_text(payload, "model"),
                "tool_name": None,
                "phase_id": _phase_id(event),
                "input_summary": None,
                "output_summary": None,
            }
        )
    return nodes


def _tool_nodes(calls: Sequence[Mapping[str, Any]], *, attempt_id: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for row in calls:
        nodes.append(
            {
                "id": f"tool:{attempt_id}:{row['call_id']}",
                "kind": "tool_call",
                "agent_id": row["agent_id"],
                "agent_name": row["agent_name"],
                "execution_id": row["execution_id"],
                "parent_execution_id": None,
                "call_id": row["call_id"],
                "model": None,
                "status": row["status"],
                "started_at": row["started_at"],
                "duration_ms": row["duration_ms"],
                # 工具本体不消耗模型 Token：返回文本长度不得折算成模型用量。
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "usage_source": "unavailable",
                "evidence_refs": list(row.get("evidence_refs") or []),
                "label": row["tool_name"],
                "tool_name": row["tool_name"],
                "phase_id": row["phase_id"],
                "input_summary": row["input_summary"],
                "output_summary": row["output_summary"],
            }
        )
    return nodes


def _edges(
    events: Sequence[TraceEvent],
    nodes: Sequence[Mapping[str, Any]],
    *,
    attempt_id: str,
) -> list[dict[str, Any]]:
    """边只能来自真实事件：委派看 parent_execution_id，调用看同执行身份，顺序看事件先后。"""
    by_id = {str(node["id"]): node for node in nodes}
    edges: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(from_id: str, to_id: str, kind: str) -> None:
        if from_id == to_id or from_id not in by_id or to_id not in by_id:
            return
        key = f"{from_id}->{to_id}:{kind}"
        if key in seen:
            return
        seen.add(key)
        edges.append(
            {"id": f"{kind}:{from_id}->{to_id}", "from": from_id, "to": to_id, "kind": kind}
        )

    for event in sorted(events, key=lambda item: item.seq):
        execution_id = _execution_id(event)
        parent_execution_id = _parent_execution_id(event)
        if not execution_id or not parent_execution_id:
            continue
        _add(f"agent:{parent_execution_id}", f"agent:{execution_id}", "delegation")

    for node in nodes:
        execution_id = node.get("execution_id")
        if not execution_id:
            continue
        _add(f"agent:{execution_id}", str(node["id"]), "invocation")

    previous: dict[str, str] = {}
    for node in nodes:
        execution_id = node.get("execution_id")
        if not execution_id:
            continue
        prior = previous.get(str(execution_id))
        if prior is not None and prior != str(node["id"]):
            _add(prior, str(node["id"]), "sequence")
        previous[str(execution_id)] = str(node["id"])
    return edges


def _identity_coverage(events: Sequence[TraceEvent]) -> str:
    identity_events = [
        event
        for event in events
        if event.type in _TOOL_TYPES or event.type == _MODEL_EVENT
    ]
    if not identity_events:
        return "unavailable"
    known = sum(
        1 for event in identity_events if _execution_id(event) or _agent_id(event)
    )
    if known == 0:
        return "unavailable"
    if known < len(identity_events):
        return "partial"
    return "complete"


def _usage_attribution_coverage(nodes: Sequence[Mapping[str, Any]]) -> str:
    model_nodes = [
        node for node in nodes if node["kind"] in {"model_call", "judge"}
    ]
    if not model_nodes:
        return "unavailable"
    attributed = sum(1 for node in model_nodes if node.get("agent_id"))
    if attributed == 0:
        return "unavailable"
    if attributed < len(model_nodes):
        return "partial"
    return "complete"


def build_execution_page(
    events: Sequence[TraceEvent],
    *,
    after_seq: int = 0,
    limit: int = 200,
    sealed: bool = False,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    """以事件游标返回稳定节点；旧 Trace 缺关系时明确降级，不虚构拓扑。"""
    if after_seq < 0 or not 1 <= limit <= 1000:
        raise ValueError("PAGE_INVALID")
    ordered = sorted(events, key=lambda item: item.seq)
    selected = [event for event in ordered if event.seq > after_seq][:limit]
    last = selected[-1].seq if selected else after_seq
    resolved_attempt = attempt_id or (
        str(events[0].attempt_id) if events else "unknown"
    )
    nodes = _agent_nodes(selected) + _model_nodes(selected, attempt_id=resolved_attempt)
    nodes += _tool_nodes(
        build_tool_calls(selected, attempt_id=resolved_attempt), attempt_id=resolved_attempt
    )
    edges = _edges(selected, nodes, attempt_id=resolved_attempt)
    reasons: list[str] = []
    identity = _identity_coverage(selected)
    if identity != "complete":
        reasons.append("identity_unavailable")
    usage_attribution = _usage_attribution_coverage(nodes)
    if usage_attribution != "complete":
        reasons.append("usage_attribution_unavailable")
    if not sealed:
        reasons.append("trace_not_sealed")
    return {
        "data": {"nodes": nodes, "edges": edges},
        "next_after_seq": last,
        "has_more": any(event.seq > last for event in ordered),
        "sealed": sealed,
        "coverage": {
            "trace": "complete" if sealed else "partial",
            "identity": identity,
            "usage_attribution": usage_attribution,
            "reasons": reasons,
        },
    }


# --------------------------------------------------------------------------- Token 账本


def _sum_known(rows: Sequence[Mapping[str, Any]], key: str) -> int | None:
    values = [row.get(key) for row in rows]
    known = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
    return sum(known) if known else None


def _usage_aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    input_tokens = _sum_known(rows, "input_tokens")
    output_tokens = _sum_known(rows, "output_tokens")
    total_tokens = _sum_known(rows, "total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    sources = {str(row["usage_source"]) for row in rows}
    source = next(iter(sources)) if len(sources) == 1 else "mixed"
    missing = sum(1 for row in rows if row.get("total_tokens") is None)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "physical_requests": len(rows),
        "missing_usage_calls": missing,
        "source": source if rows else "unavailable",
        "coverage": "unavailable" if not rows else "complete" if missing == 0 else "partial",
    }


def build_usage_page(
    attempts: Sequence[tuple[str, Sequence[TraceEvent]]],
    *,
    run_id: str,
    group_by: Literal["role", "agent", "model"] = "role",
    after_key: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """从物理请求事件生成唯一主账本；totals 始终覆盖整轮而非当前页。"""
    if group_by not in {"role", "agent", "model"} or not 1 <= limit <= 200:
        raise ValueError("USAGE_QUERY_INVALID")
    offset = _decode_cursor(after_key, {"run_id": run_id, "group_by": group_by})
    physical: OrderedDict[tuple[str, str, str], dict[str, Any]] = OrderedDict()
    for attempt_id, events in attempts:
        for event in sorted(events, key=lambda item: item.seq):
            if event.type != _MODEL_EVENT:
                continue
            payload = event.payload
            role = _payload_text(payload, "role") or "unknown"
            request_id = _payload_text(payload, "model_call_id") or str(event.event_id)
            dedupe = (attempt_id, role, request_id)
            physical.setdefault(
                dedupe,
                {
                    "attempt_id": attempt_id,
                    "role": role,
                    "agent_id": _agent_id(event),
                    "agent_name": _agent_name(event),
                    "provider": _payload_text(payload, "provider"),
                    "model": _payload_text(payload, "model"),
                    "input_tokens": _payload_int(payload, "input_tokens"),
                    "output_tokens": _payload_int(payload, "output_tokens"),
                    "total_tokens": _payload_int(payload, "total_tokens"),
                    "usage_source": _usage_source(payload),
                },
            )
    requests = list(physical.values())
    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for row in requests:
        role = str(row["role"])
        if group_by == "agent":
            # Judge 必须单独成组：同一执行智能体既跑执行又跑评分时，
            # 两路的 Token 不能并进同一行而被当成一份。
            key = (
                str(row["agent_id"] or "unassigned")
                if role == "agent"
                else f"judge:{row['agent_id'] or 'unassigned'}"
            )
        else:
            key = {
                "role": role,
                "model": str(row["model"] or "unassigned"),
            }[group_by]
        grouped.setdefault(key, []).append(row)
    data = []
    for key in sorted(grouped):
        rows = grouped[key]
        first = rows[0]
        data.append(
            {
                "key": key,
                "agent_id": first["agent_id"] if group_by == "agent" else None,
                "agent_name": first["agent_name"] if group_by == "agent" else None,
                "role": first["role"] if group_by in {"role", "agent"} else "mixed",
                "provider": first["provider"],
                "model": first["model"] if group_by in {"agent", "model"} else None,
                **_usage_aggregate(rows),
            }
        )
    page = data[offset : offset + limit]
    next_offset = offset + len(page)
    totals = _usage_aggregate(requests)
    return {
        "data": page,
        "totals": {
            "input_tokens": totals["input_tokens"],
            "output_tokens": totals["output_tokens"],
            "total_tokens": totals["total_tokens"],
            "physical_requests": totals["physical_requests"],
            "missing_usage_calls": totals["missing_usage_calls"],
        },
        "next_after_key": (
            _cursor({"run_id": run_id, "group_by": group_by, "offset": next_offset})
            if next_offset < len(data)
            else None
        ),
        "coverage": {
            "usage": totals["coverage"],
            "attribution": (
                "unavailable"
                if not requests
                else "complete"
                if all(row["agent_id"] for row in requests)
                else "partial"
            ),
            "reasons": [] if requests else ["未采集 model.call_completed 事件"],
        },
    }
