"""离线 HTML 报告渲染器（契约 §5 导出边界 + 附录 A.4 / A.5）。

只覆盖 ``nanobot.evaluation.report_view.render_run_report`` 这一个纯函数：
不碰 HTTP 路由、不碰数据库。快照形状严格按附录 A.5 的键定义构造。
"""

from __future__ import annotations

import re
from typing import Any

from nanobot.evaluation.report_view import render_run_report

# --------------------------------------------------------------------------
# 快照构造（局部 fixture，不污染共享 conftest）
# --------------------------------------------------------------------------


def _node(node_id: str, kind: str, **overrides: Any) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": node_id,
        "kind": kind,
        "agent_id": None,
        "agent_name": None,
        "execution_id": None,
        "parent_execution_id": None,
        "call_id": None,
        "model": None,
        "status": "succeeded",
        "started_at": None,
        "duration_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "usage_source": "reported",
        "evidence_refs": [],
        "label": None,
        "tool_name": None,
        "phase_id": None,
        "input_summary": None,
        "output_summary": None,
    }
    node.update(overrides)
    return node


def _edge(edge_id: str, source: str, target: str, kind: str = "invocation") -> dict[str, Any]:
    return {"id": edge_id, "from": source, "to": target, "kind": kind}


def _attempt(position: int = 1, **overrides: Any) -> dict[str, Any]:
    attempt: dict[str, Any] = {
        "id": f"attempt-{position}",
        "position": position,
        "case_id": "case-1",
        "case_revision": 3,
        "case_name": "读取输入文件并复述标识",
        "execution_status": "completed",
        "grading_status": "graded",
        "verdict": "passed",
        "errors": [],
        "judge_summary": {"model": "judge-model", "score": 4},
        "usage": {
            "agent": {
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "source": "reported",
                "coverage": "complete",
                "physical_requests": 2,
                "iterations": 3,
                "missing_usage_calls": 0,
            },
            "judge": {
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "source": "unavailable",
                "coverage": "unavailable",
                "physical_requests": 0,
                "iterations": 0,
                "missing_usage_calls": 1,
            },
        },
        "timing": {"queue_ms": 5, "agent_ms": 900, "grading_ms": 40, "total_ms": 945},
        "trace_complete": True,
    }
    attempt.update(overrides)
    return attempt


def _execution(attempt_id: str = "attempt-1", tool_nodes: int = 3) -> dict[str, Any]:
    nodes = [
        _node("agent:1", "agent_activity", agent_id="agent-1", agent_name="研究智能体"),
        _node(
            "model:1",
            "model_call",
            model="doubao-pro",
            duration_ms=800,
            total_tokens=1200,
            input_tokens=800,
            output_tokens=400,
        ),
    ]
    edges = [_edge("e1", "agent:1", "model:1", "sequence")]
    for index in range(tool_nodes):
        node_id = f"tool:{index}"
        nodes.append(
            _node(
                node_id,
                "tool_call",
                call_id=f"tool-call-{index:04d}",
                tool_name=f"tool_{index % 3}",
                status="failed" if index == 1 else "succeeded",
                duration_ms=50 + index,
                execution_id="exec-1",
                phase_id="phase-1",
            )
        )
        edges.append(_edge(f"e-tool-{index}", "model:1", node_id, "invocation"))
    nodes.append(_node("output:1", "output", status="succeeded"))
    edges.append(_edge("e-out", "model:1", "output:1", "return"))
    return {
        "attempt_id": attempt_id,
        "nodes": nodes,
        "edges": edges,
        "coverage": {
            "trace": "complete",
            "identity": "complete",
            "usage_attribution": "partial",
            "reasons": [],
        },
    }


def _tool_call(index: int, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "call_id": f"tool-call-{index:04d}",
        "attempt_id": "attempt-1",
        "execution_id": "exec-1",
        "phase_id": "phase-1",
        "tool_name": "web_fetch",
        "agent_id": "agent-1",
        "agent_name": "研究智能体",
        "status": "succeeded",
        "started_at": "2026-10-25T01:00:00+00:00",
        "duration_ms": 100 + index,
        "input_summary": '{"q":"alpha"}',
        "output_summary": "ok",
        "error": None,
        "retry_of_call_id": None,
    }
    row.update(overrides)
    return row


def _usage(**overrides: Any) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "totals": {
            "input_tokens": 800,
            "output_tokens": 400,
            "total_tokens": 1200,
            "physical_requests": 3,
            "missing_usage_calls": 1,
        },
        "rows": [
            {
                "key": "agent-1",
                "agent_id": "agent-1",
                "agent_name": "研究智能体",
                "role": "agent",
                "provider": "volc",
                "model": "doubao-pro",
                "input_tokens": 800,
                "output_tokens": 400,
                "total_tokens": 1200,
                "physical_requests": 3,
                "missing_usage_calls": 0,
                "source": "reported",
                "coverage": "complete",
            },
            {
                "key": "judge",
                "agent_id": None,
                "agent_name": None,
                "role": "judge",
                "provider": "volc",
                "model": "judge-model",
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "physical_requests": 0,
                "missing_usage_calls": 1,
                "source": "unavailable",
                "coverage": "unavailable",
            },
        ],
    }
    usage.update(overrides)
    return usage


def _snapshot(**overrides: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "run": {
            "id": "run-1",
            "suite_id": "suite-1",
            "suite_name": "基础回归数据集",
            "status": "completed",
            "model": "doubao-pro",
            "mode": "fixture",
            "created_at": "2026-10-25T00:00:00+00:00",
            "started_at": "2026-10-25T00:00:05+00:00",
            "finished_at": "2026-10-25T00:10:00+00:00",
            "planned_count": 2,
            "terminal_count": 2,
        },
        "attempts": [_attempt(1)],
        "execution": [_execution()],
        "tool_calls": [_tool_call(1)],
        "usage": _usage(),
        "options": {
            "max_tool_rows_per_table": 200,
            "max_total_tool_rows": 10000,
            "generated_at": "2026-10-25T06:35:00Z",
        },
    }
    snapshot.update(overrides)
    return snapshot


def _section_body(html: str, section_id: str) -> str:
    """取出 ``id="section_id"`` 段落内容（到下一个 section 或文末）。"""
    start = html.index(f'id="{section_id}"')
    start = html.index(">", start) + 1
    tail = html[start:]
    match = re.search(r'<section class="report-section"', tail)
    return tail[: match.start()] if match else tail


def _metric_value(html: str, metric: str) -> str:
    match = re.search(
        rf'data-metric="{metric}".*?class="metric-value">([^<]*)<',
        html,
        re.S,
    )
    assert match is not None, f"缺少指标：{metric}"
    return match.group(1)


def _details_ranges(html: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(r"<details.*?</details>", html, re.S)]


# --------------------------------------------------------------------------
# 1. 文档骨架
# --------------------------------------------------------------------------


def test_report_is_complete_html_document() -> None:
    html = render_run_report(_snapshot())
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in html
    assert '<meta name="viewport" content="width=device-width' in html
    assert "<title>" in html and "</title>" in html
    assert html.rstrip().endswith("</html>")


# --------------------------------------------------------------------------
# 2. 无脚本、无外链、无事件属性
# --------------------------------------------------------------------------


def test_report_has_no_script_no_external_resource_no_event_attribute() -> None:
    html = render_run_report(_snapshot())
    lowered = html.lower()
    assert "<script" not in lowered
    assert "</script" not in lowered
    assert "<link" not in lowered
    assert "<iframe" not in lowered
    assert "<object" not in lowered
    assert "<embed" not in lowered
    assert "http://" not in html
    assert "https://" not in html
    assert "foreignobject" not in lowered
    assert "javascript:" not in lowered
    assert not re.search(r"\son[a-z]+\s*=", html, re.IGNORECASE)
    assert not re.search(r"url\s*\(", html, re.IGNORECASE)


# --------------------------------------------------------------------------
# 3. 全量转义
# --------------------------------------------------------------------------


def test_untrusted_payload_is_escaped_everywhere() -> None:
    payload = "<img src=x onerror=alert(1)>"
    snapshot = _snapshot(
        attempts=[_attempt(1, case_name=payload)],
        execution=[
            _execution(
                tool_nodes=1,
            )
        ],
        tool_calls=[
            _tool_call(
                1,
                tool_name=payload,
                status="failed",
                error=payload,
                input_summary=payload,
                output_summary=payload,
            )
        ],
    )
    html = render_run_report(snapshot)
    assert "<img" not in html
    assert "&lt;img" in html
    assert "onerror=" not in html
    assert "<script" not in html.lower()
    # 载荷不得以原文形式出现在任何位置
    assert payload not in html


def test_escaping_covers_quotes_and_ampersand() -> None:
    snapshot = _snapshot(
        attempts=[_attempt(1, case_name="a & b \" c ' d")],
        tool_calls=[_tool_call(1, tool_name="a & b", input_summary="<b>x</b>")],
    )
    html = render_run_report(snapshot)
    assert "a &amp; b" in html
    assert "&quot;" in html or "&#x27;" in html
    assert "<b>" not in html


# --------------------------------------------------------------------------
# 4. 固定区块顺序
# --------------------------------------------------------------------------


def test_sections_render_in_fixed_order() -> None:
    html = render_run_report(_snapshot())
    markers = [
        "报告范围",
        "摘要指标",
        "Token 分账",
        "用例结果",
        "执行流程图",
        "工具分组概览",
        "异常调用",
        "全量工具调用",
        "口径说明",
    ]
    positions = [html.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert html.index("<title>") < positions[0]


def test_scope_section_states_range_and_case_counts() -> None:
    html = render_run_report(_snapshot())
    scope = _section_body(html, "section-scope")
    assert "run-1" in scope
    assert "基础回归数据集" in scope
    assert "用例数" in scope
    assert "1" in scope


# --------------------------------------------------------------------------
# 5. 大规模口径：分段表 + 已导出/总量
# --------------------------------------------------------------------------


def test_large_tool_volume_is_segmented_and_states_exported_total() -> None:
    calls = [_tool_call(index) for index in range(160)]
    html = render_run_report(
        _snapshot(
            tool_calls=calls,
            options={
                "max_tool_rows_per_table": 50,
                "max_total_tool_rows": 10000,
                "generated_at": "2026-10-25T06:35:00Z",
            },
        )
    )
    body = _section_body(html, "section-tool-calls")
    assert body.count("<table") == 4
    assert "已导出 160 / 总 160" in html
    assert "第 1 / 4 段" in body
    assert "第 4 / 4 段" in body
    # 每段最多 50 行（表头之外）
    for chunk in re.findall(r"<tbody>(.*?)</tbody>", body, re.S):
        assert chunk.count("<tr>") <= 50


def test_export_budget_truncation_is_explicit() -> None:
    calls = [_tool_call(index) for index in range(160)]
    html = render_run_report(
        _snapshot(
            tool_calls=calls,
            options={
                "max_tool_rows_per_table": 50,
                "max_total_tool_rows": 100,
                "generated_at": "2026-10-25T06:35:00Z",
            },
        )
    )
    assert "已导出 100 / 总 160" in html
    assert "截断" in html
    body = _section_body(html, "section-tool-calls")
    rows = sum(chunk.count("<tr>") for chunk in re.findall(r"<tbody>(.*?)</tbody>", body, re.S))
    assert rows == 100


def test_large_tool_volume_switches_flowchart_to_grouped_rendering() -> None:
    html = render_run_report(
        _snapshot(
            execution=[_execution(tool_nodes=60)],
            tool_calls=[_tool_call(index) for index in range(60)],
        )
    )
    assert "分组" in html
    flow = _section_body(html, "section-flowcharts")
    svg = re.search(r"<svg.*?</svg>", flow, re.S)
    assert svg is not None
    # 60 个工具节点按分组渲染，矩形数量必须明显少于逐条铺开
    assert svg.group(0).count("<rect") < 60


# --------------------------------------------------------------------------
# 6. 缺失显示 —、0 显示 0
# --------------------------------------------------------------------------


def test_missing_numbers_show_dash_and_zero_shows_zero() -> None:
    html = render_run_report(
        _snapshot(
            attempts=[],
            execution=[],
            tool_calls=[],
            usage={
                "totals": {
                    "input_tokens": None,
                    "output_tokens": None,
                    "total_tokens": None,
                    "physical_requests": 0,
                    "missing_usage_calls": 0,
                },
                "rows": [],
            },
        )
    )
    assert _metric_value(html, "token_total") == "—"
    assert _metric_value(html, "agent_tokens") == "—"
    assert _metric_value(html, "tool_calls") == "0"
    assert _metric_value(html, "physical_requests") == "0"
    assert _metric_value(html, "total_cases") == "0"


def test_unknown_token_total_is_not_reported_as_zero() -> None:
    html = render_run_report(_snapshot())
    assert _metric_value(html, "token_total") == "1,200"
    unknown = render_run_report(
        _snapshot(
            usage={
                "totals": {
                    "input_tokens": None,
                    "output_tokens": None,
                    "total_tokens": None,
                    "physical_requests": 0,
                    "missing_usage_calls": 2,
                },
                "rows": [],
            }
        )
    )
    assert _metric_value(unknown, "token_total") == "—"
    assert _metric_value(unknown, "token_total") != "0"


# --------------------------------------------------------------------------
# 7. 打印 CSS 与折叠约束
# --------------------------------------------------------------------------


def test_print_css_repeats_header_and_keeps_rows_intact() -> None:
    html = render_run_report(_snapshot())
    assert "@media print" in html
    assert "display: table-header-group" in html
    assert "break-inside: avoid" in html
    assert "max-width" in html


def test_key_sections_are_not_hidden_in_details() -> None:
    html = render_run_report(
        _snapshot(
            tool_calls=[
                _tool_call(1, status="failed", error="boom"),
                _tool_call(2, status="unknown"),
            ]
        )
    )
    ranges = _details_ranges(html)
    for marker in ("摘要指标", "异常调用", "口径说明"):
        position = html.index(marker)
        assert not any(start <= position <= end for start, end in ranges)


# --------------------------------------------------------------------------
# 8. 静态 SVG
# --------------------------------------------------------------------------


def test_flowchart_svg_uses_native_elements_only() -> None:
    html = render_run_report(_snapshot())
    svg = re.search(r"<svg.*?</svg>", html, re.S)
    assert svg is not None
    markup = svg.group(0)
    assert "<rect" in markup
    assert "<text" in markup
    assert "<path" in markup
    assert "<image" not in markup
    assert "xlink" not in markup
    assert "url(" not in markup
    assert "@font-face" not in html
    assert "http" not in markup


def test_svg_attributes_are_escaped() -> None:
    html = render_run_report(
        _snapshot(
            execution=[
                {
                    "attempt_id": "attempt-1",
                    "nodes": [
                        _node(
                            'a" onload="alert(1)',
                            "tool_call",
                            tool_name="web_fetch",
                            call_id="call-1",
                        )
                    ],
                    "edges": [],
                    "coverage": {"trace": "complete"},
                }
            ]
        )
    )
    assert 'onload="alert(1)"' not in html
    assert "onload=&#61;" in html or "onload" not in html


# --------------------------------------------------------------------------
# 9. 纯函数与一致性
# --------------------------------------------------------------------------


def test_rendering_is_deterministic() -> None:
    snapshot = _snapshot(tool_calls=[_tool_call(index) for index in range(20)])
    first = render_run_report(snapshot)
    second = render_run_report(snapshot)
    assert first == second
    assert render_run_report(_snapshot()) == render_run_report(_snapshot())


def test_renderer_does_not_mutate_input() -> None:
    import copy

    snapshot = _snapshot()
    before = copy.deepcopy(snapshot)
    render_run_report(snapshot)
    assert snapshot == before


# --------------------------------------------------------------------------
# 10. 空数据与缺字段
# --------------------------------------------------------------------------


def test_empty_snapshot_still_produces_valid_report() -> None:
    html = render_run_report({})
    assert "<title>" in html
    assert "—" in html
    assert "暂无" in html
    assert "<script" not in html.lower()


def test_snapshot_missing_fields_renders_dash_not_crash() -> None:
    html = render_run_report(
        {
            "run": {"id": "run-x"},
            "attempts": [{"id": "attempt-x"}],
            "execution": [{"attempt_id": "attempt-x"}],
        }
    )
    assert "<title>" in html
    assert "—" in html


def test_usage_role_split_distinguishes_agent_and_judge() -> None:
    html = render_run_report(_snapshot())
    assert _metric_value(html, "agent_tokens") == "1,200"
    assert _metric_value(html, "judge_tokens") == "—"
    table = _section_body(html, "section-usage")
    assert "research-agent" in table or "agent-1" in table
    assert "judge" in table


def test_report_contains_no_hyperlink_or_resource_reference() -> None:
    html = render_run_report(_snapshot())
    assert "href=" not in html
    assert "src=" not in html
    assert "@import" not in html
    assert "background-image" not in html


def test_renderer_source_has_no_io_env_or_network_access() -> None:
    import inspect

    from nanobot.evaluation import report_view

    source = inspect.getsource(report_view)
    for forbidden in (
        "import os",
        "import socket",
        "import subprocess",
        "import urllib",
        "import requests",
        "import httpx",
        "sqlite3",
        "getenv",
        "os.environ",
        "nanobot.evaluation.database",
        "nanobot.evaluation.repository",
    ):
        assert forbidden not in source


def test_empty_snapshot_keeps_all_sections() -> None:
    html = render_run_report({})
    for section_id in (
        "section-scope",
        "section-metrics",
        "section-agents",
        "section-usage",
        "section-attempts",
        "section-flowcharts",
        "section-tool-groups",
        "section-abnormal",
        "section-tool-calls",
        "section-methodology",
    ):
        assert f'id="{section_id}"' in html


def test_grouped_flowchart_shows_group_label_and_counts() -> None:
    html = render_run_report(
        _snapshot(
            execution=[_execution(tool_nodes=45)],
            tool_calls=[_tool_call(index) for index in range(45)],
        )
    )
    flow = _section_body(html, "section-flowcharts")
    assert "×15" in flow  # 45 次调用按 execution × 阶段 × 工具名折成 3 组
    assert "分组渲染" in flow


def test_export_budget_boundary_claims_complete_export() -> None:
    calls = [_tool_call(index) for index in range(20)]
    html = render_run_report(
        _snapshot(
            tool_calls=calls,
            options={
                "max_tool_rows_per_table": 10,
                "max_total_tool_rows": 20,
                "generated_at": "2026-10-25T06:35:00Z",
            },
        )
    )
    assert "已导出 20 / 总 20" in html
    assert "无截断" in html
    detail_section = html.split("全量工具调用", 1)[1].split("口径说明", 1)[0]
    assert "已按导出预算上限截断" not in detail_section


def test_node_budget_also_switches_to_grouped_rendering() -> None:
    """工具阈值没超，但节点总数超可见预算时同样按分组渲染。"""
    html = render_run_report(
        _snapshot(
            execution=[_execution(tool_nodes=90)],
            tool_calls=[_tool_call(index) for index in range(90)],
            options={
                "max_tool_rows_per_table": 200,
                "max_total_tool_rows": 10000,
                "generated_at": "2026-10-25T06:35:00Z",
                "tool_graph_threshold": 1000,
                "max_graph_nodes": 80,
            },
        )
    )
    flow = _section_body(html, "section-flowcharts")
    assert "分组渲染" in flow
    svg = re.search(r"<svg.*?</svg>", flow, re.S)
    assert svg is not None
    assert svg.group(0).count("<rect") < 90


def test_agent_table_lists_participating_agents_and_keeps_unattributed_visible() -> None:
    html = render_run_report(
        _snapshot(
            execution=[
                {
                    "attempt_id": "attempt-1",
                    "nodes": [
                        _node(
                            "agent:exec-1",
                            "agent_activity",
                            agent_id="agent-1",
                            agent_name="研究智能体",
                            execution_id="exec-1",
                            parent_execution_id="exec-root",
                        )
                    ],
                    "edges": [],
                    "coverage": {
                        "trace": "complete",
                        "identity": "complete",
                        "usage_attribution": "complete",
                        "reasons": [],
                    },
                }
            ],
            tool_calls=[
                _tool_call(0, agent_id="agent-1", agent_name="研究智能体"),
            ],
            usage={
                "totals": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150,
                    "physical_requests": 2,
                    "missing_usage_calls": 0,
                },
                "rows": [
                    {
                        "key": "agent-1",
                        "agent_id": "agent-1",
                        "agent_name": "研究智能体",
                        "role": "agent",
                        "provider": "controlled",
                        "model": "doubao-pro",
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "total_tokens": 150,
                        "physical_requests": 2,
                        "missing_usage_calls": 0,
                        "source": "reported",
                        "coverage": "complete",
                    }
                ],
            },
        )
    )
    body = _section_body(html, "section-agents")
    assert "研究智能体" in body
    assert "agent-1" in body
    assert "exec-root" in body
    assert "doubao-pro" in body
    assert "150" in body
