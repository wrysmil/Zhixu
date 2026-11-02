"""记忆比较报告：同一不可变快照的 API 视图与自包含 HTML（契约 §5 / §10）。

三条硬边界：

1. **同一份快照**：:func:`build_comparison_snapshot` 把比较结果规范化成纯 JSON 并
   计算摘要；API 返回的 ``snapshot.to_dict()`` 与 :func:`render_memory_comparison_report`
   渲染的 HTML 来自同一个摘要。渲染器不接受"边读边算"的可变数据。
2. **转义一切用户文本**：场景 ID、原因、候选名、指标原因全部按文本转义；不输出
   ``<script>``、事件属性、``javascript:`` 或任何外链/远程资源（与 report_view 的
   导出边界一致）。
3. **未知不是 0**：成本分项缺失显示"未知"；改善/退化/未知三组逐题全部列出，
   分页只影响逐题列表，不影响统计口径。

渲染器是纯函数：不读数据库、不读环境变量、不发网络请求、不拼绝对路径；只渲染
白名单字段，因此快照里即便混入 gold/artifact 路径也不会出现在 HTML 里。
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

COMPARISON_SNAPSHOT_VERSION = 1

DASH = "—"
UNKNOWN = "未知"

_EVENT_ATTR_RE = re.compile(r"\bon([A-Za-z][A-Za-z0-9_-]*)=")

_METRIC_LABELS: dict[str, str] = {
    "write_latency_p50_ms": "写入时延 p50",
    "write_latency_p95_ms": "写入时延 p95",
    "index_latency_p50_ms": "索引时延 p50",
    "index_latency_p95_ms": "索引时延 p95",
    "retrieve_latency_p50_ms": "召回时延 p50",
    "retrieve_latency_p95_ms": "召回时延 p95",
    "inject_latency_p50_ms": "注入时延 p50",
    "inject_latency_p95_ms": "注入时延 p95",
    "extraction_tokens": "抽取 Token",
    "derivation_tokens": "派生 Token",
    "embedding_tokens": "嵌入 Token",
    "reader_tokens": "阅读/回答 Token",
    "judge_tokens": "Judge Token",
    "embedding_calls": "嵌入调用次数",
    "model_requests": "模型请求数",
    "storage_bytes": "存储字节",
    "estimated_cost": "估算费用",
}

_UNIT_LABELS = {
    "ratio": "比例",
    "ms": "毫秒",
    "token": "Token",
    "count": "次数",
    "bytes": "字节",
    "currency": "费用",
}

_STATUS_LABELS = {
    "completed": "完整",
    "incomplete": "不完整",
    "improved": "改善",
    "regressed": "退化",
    "unchanged": "持平",
    "incomparable": "未知/不可比",
}

_DIRECTION_LABELS = {
    "improved": "统计支持提升",
    "regressed": "统计支持退化",
    "inconclusive": "尚不能确认",
}

_DECISION_LABELS = {
    "adoptable": "可采用",
    "blocked": "阻止放行",
    "rejected": "明确不采用",
    "review_required": "需复核",
    "insufficient": "证据不足",
    "diagnostic_only": "仅诊断（未配置阈值）",
}


# =========================================================================== 基础


def _escape(value: Any) -> str:
    """任意值 → 可安全放进 HTML 文本/属性的字符串（含协议与事件属性编码）。"""
    if value is None:
        return UNKNOWN
    escaped = html.escape(str(value), quote=True)
    escaped = escaped.replace("http://", "http&#58;//")
    escaped = escaped.replace("https://", "https&#58;//")
    escaped = escaped.replace("javascript:", "javascript&#58;")
    escaped = escaped.replace("foreignObject", "foreign&#79;bject")
    return _EVENT_ATTR_RE.sub(r"on\1&#61;", escaped)


def _text(value: Any) -> str:
    return _escape("" if value is None else value)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _fmt_percent(value: Any) -> str:
    number = _number(value)
    if number is None:
        return UNKNOWN
    return f"{number * 100:.1f}%"


def _fmt_pp(value: Any) -> str:
    number = _number(value)
    if number is None:
        return UNKNOWN
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.1f} 个百分点"


def _fmt_metric(metric_id: str, unit: Any, value: Any) -> str:
    """按单位格式化；未知一律"未知"，绝不渲染成 0。"""
    number = _number(value)
    if number is None:
        return UNKNOWN
    unit_text = str(unit or "")
    if unit_text == "ratio" or metric_id.startswith("qa_") or metric_id in {
        "scope_leak_rate",
        "grounded_memory_success",
        "forget_behavior_success",
    }:
        return f"{number * 100:.1f}%"
    if unit_text == "ms":
        return f"{number:.0f} ms"
    if unit_text == "token":
        return f"{number:.0f} Token"
    if unit_text == "count":
        return f"{number:.0f}"
    if unit_text == "bytes":
        return f"{number:.0f} B"
    if unit_text == "currency":
        return f"{number:.6f}"
    return f"{number:.4g}"


def _fmt_ratio(value: Any) -> str:
    number = _number(value)
    if number is None:
        return UNKNOWN
    return f"{number:.2f}×"


def _status_label(status: Any) -> str:
    text = str(status or "")
    return _STATUS_LABELS.get(text, text or UNKNOWN)


def _decision_label(decision: Any) -> str:
    text = str(decision or "")
    return _DECISION_LABELS.get(text, text or UNKNOWN)


def _direction_label(direction: Any) -> str:
    text = str(direction or "")
    return _DIRECTION_LABELS.get(text, text or UNKNOWN)


# =========================================================================== 快照


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest_of(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemoryComparisonSnapshot:
    """不可变比较快照：内部只保存规范化后的 JSON 文本与摘要。"""

    _comparison_json: str
    digest: str

    @property
    def comparison(self) -> dict[str, Any]:
        return json.loads(self._comparison_json)

    @property
    def version(self) -> int:
        return COMPARISON_SNAPSHOT_VERSION

    def to_dict(self) -> dict[str, Any]:
        """API 响应形状：同一摘要 + 比较正文（深拷贝）。"""
        return {
            "snapshot_version": COMPARISON_SNAPSHOT_VERSION,
            "digest": self.digest,
            "comparison": self.comparison,
        }

    def to_html(self) -> str:
        return _render(self.comparison)


def build_comparison_snapshot(
    comparison: Any,
) -> MemoryComparisonSnapshot:
    """把比较结果（或其 API 快照）规范化为不可变快照。

    接受 ``MemoryComparisonResult``（``to_dict()``）、
    ``compare_memory_experiments(...).to_dict()``、已经包装过的
    ``{"snapshot_version", "digest", "comparison"}`` 或快照本身；重复调用摘要稳定。
    """
    if isinstance(comparison, MemoryComparisonSnapshot):
        return comparison
    if not isinstance(comparison, Mapping):
        to_dict = getattr(comparison, "to_dict", None)
        if not callable(to_dict):
            raise TypeError("COMPARISON_SNAPSHOT_INVALID:comparison must be a mapping")
        comparison = to_dict()
    if not isinstance(comparison, Mapping):  # pragma: no cover - 防御性分支
        raise TypeError("COMPARISON_SNAPSHOT_INVALID:comparison must be a mapping")
    inner = comparison.get("comparison")
    payload = inner if isinstance(inner, Mapping) else comparison
    normalized = json.loads(_canonical_json(dict(payload)))
    return MemoryComparisonSnapshot(
        _comparison_json=_canonical_json(normalized),
        digest=_digest_of(normalized),
    )


# =========================================================================== HTML 片段


def _section(section_id: str, number: str, title: str, body: str) -> str:
    return (
        f'<section class="report-section" id="{_text(section_id)}">'
        f'<h2><span class="section-number">{_text(number)}</span> {_text(title)}</h2>'
        f"{body}</section>"
    )


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    if not rows:
        return '<p class="muted">无记录</p>'
    head = "".join(f'<th scope="col">{_text(item)}</th>' for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f'<table class="report-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _definition_table(pairs: Sequence[tuple[str, str]]) -> str:
    rows = [[_text(key), value] for key, value in pairs]
    return _table(("项", "值"), rows)


def _render_header(comparison: Mapping[str, Any]) -> str:
    primary = _mapping(comparison.get("primary"))
    decision = _mapping(comparison.get("adoption_decision"))
    title = (
        f"记忆版本比较 · {comparison.get('track') or UNKNOWN} · "
        f"{primary.get('metric_id') or UNKNOWN}"
    )
    badges = "".join(
        f'<span class="badge">{_text(label)}：{_text(_status_label(value))}</span>'
        for label, value in (
            ("比较状态", comparison.get("status")),
            ("测量状态", comparison.get("measurement_status")),
            ("统计方向", _direction_label(comparison.get("statistical_direction"))),
            ("采用建议", _decision_label(decision.get("decision"))),
        )
    )
    return (
        f'<header class="report-header"><h1>{_text(title)}</h1>'
        f'<p class="muted">统计方向只回答"这份数据支持哪个方向"；采用建议另看阈值、'
        f"分项容忍度、成本预算与关键门禁，两者不可互相替代。</p>"
        f'<div class="badges">{badges}</div></header>'
    )


def _render_conditions(comparison: Mapping[str, Any]) -> str:
    attribution = _mapping(comparison.get("attribution"))
    applied = [str(item) for item in _sequence(comparison.get("applied_changes"))]
    declared = [str(item) for item in _sequence(comparison.get("changed_fields"))]
    model_changes = [str(item) for item in _sequence(comparison.get("model_changes"))]
    single_factor = attribution.get("single_factor")
    pairs = [
        ("基线实验", _text(comparison.get("baseline_experiment_id"))),
        ("候选实验", _text(comparison.get("candidate_experiment_id"))),
        ("基线候选", _text(comparison.get("baseline_candidate_id"))),
        ("候选候选", _text(comparison.get("candidate_candidate_id"))),
        ("轨道 / split", f"{_text(comparison.get('track'))} / {_text(comparison.get('split'))}"),
        ("重复次数", _text(comparison.get("repetitions"))),
        ("主指标", _text(_mapping(comparison.get("primary")).get("metric_id"))),
        ("配对场景", f"{_text(comparison.get('paired'))} / {_text(comparison.get('planned'))}"),
        ("声明变化项", _text(", ".join(declared) if declared else "无")),
        ("实际条件变化", _text(", ".join(applied) if applied else "无")),
        (
            "单因素归因",
            "可以" if single_factor is True else (
                "不可以（模型/抽取/嵌入发生变化）" if model_changes else _text("未知")
            ),
        ),
    ]
    if model_changes:
        pairs.append(("模型变化项", _text(", ".join(model_changes))))
    source_changes = [str(item) for item in _sequence(attribution.get("source_changes"))]
    if source_changes:
        pairs.append(
            (
                "源码/依赖变化（仅记录）",
                _text(", ".join(source_changes)) + "；源码差异是被测变量，不要求声明",
            )
        )
    return _definition_table(pairs)


def _render_primary(comparison: Mapping[str, Any]) -> str:
    primary = _mapping(comparison.get("primary"))
    macro = _mapping(comparison.get("scenario_macro"))
    micro = _mapping(comparison.get("probe_micro"))
    rows = [
        [
            _text("场景宏平均"),
            _fmt_percent(macro.get("baseline")),
            _fmt_percent(macro.get("candidate")),
            _fmt_pp(macro.get("delta_pp")),
        ],
        [
            _text("题级 micro（补充）"),
            _fmt_percent(micro.get("baseline")),
            _fmt_percent(micro.get("candidate")),
            _fmt_pp(micro.get("delta_pp")),
        ],
    ]
    interval = _mapping(comparison.get("interval"))
    if interval:
        interval_text = (
            f"{_fmt_pp(interval.get('low_pp'))} ~ {_fmt_pp(interval.get('high_pp'))}"
            f"（{_text(interval.get('clusters'))} 个独立 cluster，seed="
            f"{_text(interval.get('seed'))}，{_text(interval.get('rounds'))} 轮，"
            f"{_text(interval.get('version'))}）"
        )
    else:
        interval_text = (
            f"不输出区间：独立 cluster 少于 {_text(_mapping(comparison.get('bootstrap')).get('min_clusters'))} "
            "或样本不足；区间跨 0 不能解释为「没有变化」。"
        )
    body = (
        _table(("口径", "基线", "候选", "差值（百分点）"), rows)
        + f'<p class="interval" data-interval="primary">{_text(interval_text)}</p>'
        + f'<p class="muted">主指标统计方向：{_text(_direction_label(primary.get("statistical_direction")))}；'
        f"子集诊断方向（不用于采用）：{_text(primary.get('subset_direction'))}。</p>"
    )
    categories = _mapping(comparison.get("category_macro"))
    category_rows = [
        [
            _text(_mapping(row).get("category")),
            _text(_mapping(row).get("scenes")),
            _fmt_percent(_mapping(row).get("baseline")),
            _fmt_percent(_mapping(row).get("candidate")),
            _fmt_pp(_mapping(row).get("delta_pp")),
        ]
        for row in _sequence(categories.get("categories"))
    ]
    if category_rows:
        body += "<h3>类别宏平均</h3>" + _table(
            ("类别", "场景数", "基线", "候选", "差值（百分点）"), category_rows
        )
    return body


def _render_interval_and_adoption(comparison: Mapping[str, Any]) -> str:
    decision = _mapping(comparison.get("adoption_decision"))
    checks = _mapping(decision.get("checks"))
    interval = _mapping(comparison.get("interval"))
    rows = [
        ["采用建议", _text(_decision_label(decision.get("decision")))],
        [
            "minimum_effect_pp",
            _text(checks.get("minimum_effect_pp") if checks.get("minimum_effect_pp") is not None else "未配置"),
        ],
        [
            "主指标区间",
            (
                "未知"
                if not interval
                else f"{_fmt_pp(interval.get('low_pp'))} ~ {_fmt_pp(interval.get('high_pp'))}"
            ),
        ],
        ["主指标差值", _fmt_pp(_mapping(comparison.get("primary")).get("delta_pp"))],
        ["统计方向", _text(_direction_label(comparison.get("statistical_direction")))],
        ["未校准所需指标", _text(", ".join(
            str(item) for item in _sequence(comparison.get("uncalibrated_metrics"))
        ) or "无")],
    ]
    body = _table(("检查项", "结论"), rows)
    reasons = [str(item) for item in _sequence(decision.get("reasons"))]
    if reasons:
        body += "<h3>判定原因</h3><ul class=\"reason-list\">" + "".join(
            f"<li>{_text(item)}</li>" for item in reasons
        ) + "</ul>"
    regressions = _mapping(checks.get("regressions"))
    if regressions:
        body += "<h3>分项退化容忍度</h3>" + _table(
            ("指标", "容忍度（百分点）", "差值区间", "结论"),
            [
                [
                    _text(metric_id),
                    _text(_mapping(entry).get("tolerance_pp")),
                    _fmt_pp_list(_mapping(entry).get("interval_pp")),
                    _text(_mapping(entry).get("verdict")),
                ]
                for metric_id, entry in sorted(regressions.items())
            ],
        )
    budgets = _mapping(checks.get("budgets"))
    if budgets:
        body += "<h3>成本 / 延迟预算</h3>" + _table(
            ("预算", "上限", "实际", "结论"),
            [
                [
                    _text(name),
                    _text(_mapping(entry).get("limit")),
                    _text(_mapping(entry).get("actual")),
                    _text(_mapping(entry).get("verdict")),
                ]
                for name, entry in sorted(budgets.items())
            ],
        )
    return body


def _fmt_pp_list(value: Any) -> str:
    values = [item for item in _sequence(value) if _number(item) is not None]
    if len(values) != 2:
        return UNKNOWN
    return f"{_fmt_pp(values[0])} ~ {_fmt_pp(values[1])}"


def _render_secondary(comparison: Mapping[str, Any]) -> str:
    rows = []
    for item in _sequence(comparison.get("secondary")):
        row = _mapping(item)
        improvement = _fmt_pp(row.get("improvement_pp"))
        if _sequence(row.get("improvement_interval_pp")):
            improvement += f"（区间 {_fmt_pp_list(row.get('improvement_interval_pp'))}）"
        rows.append(
            [
                f'<span data-metric="{_text(row.get("metric_id"))}">{_text(row.get("metric_id"))}</span>',
                _fmt_metric(str(row.get("metric_id") or ""), row.get("unit"), row.get("baseline")),
                _fmt_metric(str(row.get("metric_id") or ""), row.get("unit"), row.get("candidate")),
                improvement,
                _text("有区间" if row.get("interval_pp") is not None else "样本不足/不输出区间"),
            ]
        )
    return _table(("指标", "基线", "候选", "改善（方向已归一）", "区间"), rows)


def _pair_rows(pairs: Sequence[Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in pairs:
        row = _mapping(item)
        reasons = ", ".join(str(entry) for entry in _sequence(row.get("reasons")))
        rows.append(
            [
                _text(row.get("scenario_id")),
                _text(row.get("cluster_id")),
                _text(row.get("category")),
                _fmt_percent(row.get("baseline")),
                _fmt_percent(row.get("candidate")),
                _fmt_pp(row.get("delta_pp")),
                _text(f"{row.get('baseline_trials')}/{row.get('candidate_trials')}"),
                _text(reasons or DASH),
            ]
        )
    return rows


def _render_changes(comparison: Mapping[str, Any]) -> str:
    pairs = [_mapping(item) for item in _sequence(comparison.get("scene_pairs"))]
    groups: dict[str, list[Mapping[str, Any]]] = {
        "improved": [],
        "regressed": [],
        "incomparable": [],
        "unchanged": [],
    }
    for pair in pairs:
        status = str(pair.get("status") or "")
        groups.setdefault(status, []).append(pair)
    headers = ("场景", "cluster", "类别", "基线", "候选", "差值（百分点）", "trial 数", "原因")
    body = ""
    for status, label in (
        ("improved", "改善"),
        ("regressed", "退化"),
        ("incomparable", "未知/不可比"),
        ("unchanged", "持平"),
    ):
        rows = _pair_rows(groups.get(status, []))
        body += f'<h3 data-group="{_text(status)}">{_text(label)}（{_text(len(rows))}）</h3>'
        body += _table(headers, rows)
    excluded = [_mapping(item) for item in _sequence(comparison.get("excluded"))]
    if excluded:
        body += "<h3>被排除场景</h3>" + _table(
            ("场景", "原因", "细节"),
            [
                [
                    _text(item.get("scenario_id")),
                    _text(item.get("reason")),
                    _text(", ".join(str(entry) for entry in _sequence(item.get("details"))) or DASH),
                ]
                for item in excluded
            ],
        )
    body += (
        '<p class="muted">统计基于全量配对场景；逐题列表分页不会重算或缩减分母。'
        "未判定/缺 trial 的场景进入「未知」，不会被当作 0 分。</p>"
    )
    return body


def _render_cost(comparison: Mapping[str, Any]) -> str:
    cost = _mapping(comparison.get("cost"))
    summary = _mapping(cost.get("summary"))
    body = (
        "<p class=\"muted\">成本拆分为写入 / 查询 / Judge；未知保持「未知」，不填 0。"
        f"费用比：{_fmt_ratio(summary.get('cost_ratio'))}；"
        f"p95 时延比：{_fmt_ratio(summary.get('p95_latency_ratio'))}。</p>"
    )
    group_labels = {"write": "写入", "query": "查询", "judge": "Judge", "totals": "合计"}
    for group in ("write", "query", "judge", "totals"):
        entries = _mapping(cost.get(group))
        if not entries:
            continue
        rows = []
        for metric_id, entry in entries.items():
            row = _mapping(entry)
            coverage = _mapping(row.get("coverage"))
            base_cov = _mapping(coverage.get("baseline"))
            cand_cov = _mapping(coverage.get("candidate"))
            rows.append(
                [
                    f'<span data-metric="{_text(metric_id)}">'
                    f"{_text(_METRIC_LABELS.get(str(metric_id), metric_id))}</span>",
                    _fmt_metric(str(metric_id), row.get("unit"), row.get("baseline")),
                    _fmt_metric(str(metric_id), row.get("unit"), row.get("candidate")),
                    _fmt_ratio(row.get("ratio")),
                    _text(f"{base_cov.get('scored')}/{base_cov.get('planned')}"),
                    _text(f"{cand_cov.get('scored')}/{cand_cov.get('planned')}"),
                    _text("完整" if row.get("complete") else "不完整"),
                ]
            )
        body += f"<h3>{_text(group_labels[group])}</h3>" + _table(
            ("指标", "基线", "候选", "候选/基线", "基线覆盖", "候选覆盖", "完整性"), rows
        )
    return body


def _render_gates(comparison: Mapping[str, Any]) -> str:
    gates = _mapping(comparison.get("gates"))
    rows = []
    for name, label in (("scope_leak", "关键作用域泄漏"), ("deletion_resurrection", "删除复活")):
        entry = _mapping(gates.get(name))
        rows.append(
            [
                _text(label),
                "失败" if entry.get("candidate_failed") else "通过/未知",
                _text(", ".join(str(item) for item in _sequence(entry.get("evidence"))) or DASH),
            ]
        )
    body = _table(("门禁", "候选结论", "证据"), rows)
    body += (
        '<p class="muted">0 次可观察违规只代表本次样本没有触发，不等于安全保证；'
        "关键项缺证按需复核处理，不以「未显著」豁免。</p>"
    )
    if gates.get("blocked"):
        body += '<p class="blocked">关键门禁失败：即使平均分上升也阻止放行。</p>'
    return body


def _render_official(comparison: Mapping[str, Any]) -> str:
    official = _mapping(comparison.get("official_protocol"))
    rows = []
    for item in _sequence(official.get("metrics")):
        row = _mapping(item)
        rows.append(
            [
                _text(row.get("metric_id")),
                "否（平台近似判定）" if row.get("official") is False else _text(row.get("official")),
                _fmt_metric(str(row.get("metric_id") or ""), "ratio", row.get("baseline")),
                _fmt_metric(str(row.get("metric_id") or ""), "ratio", row.get("candidate")),
                _text(row.get("note")),
            ]
        )
    note = '<p class="muted">平台近似判定不等于官方分；官方分与平台分数不混算。</p>'
    if not rows:
        return '<p class="muted">无官方协议指标。</p>' + note
    return _table(("指标", "官方口径", "基线", "候选", "说明"), rows) + note


def _render_footer(comparison: Mapping[str, Any]) -> str:
    bootstrap = _mapping(comparison.get("bootstrap"))
    reasons = [str(item) for item in _sequence(comparison.get("reasons"))]
    reason_html = (
        '<ul class="reason-list">'
        + "".join(f"<li>{_text(item)}</li>" for item in reasons)
        + "</ul>"
        if reasons
        else '<p class="muted">无额外原因。</p>'
    )
    return (
        '<footer class="report-footer">'
        "<h2>口径与限制</h2>"
        f"<p>区间算法 {_text(bootstrap.get('version'))}：按 cluster 整体有放回重采样、"
        f"seed={_text(bootstrap.get('seed'))}、{_text(bootstrap.get('rounds'))} 轮、"
        f"type7 分位数；独立 cluster 少于 {_text(bootstrap.get('min_clusters'))} 不输出区间。"
        "区间不是确定性保证，跨 0 只表示方向未确定。</p>"
        "<p>差值单位为百分点（pp）；错误率等越低越好的指标已按方向归一为「正值代表改善」。"
        "成本/延迟不与正确率加权成总分。</p>"
        "<p>报告只读同一份不可变比较快照；HTML 与 API 统计完全一致。不含外部脚本、"
        "远程资源或私有 gold 路径。</p>"
        f"<h3>比较原因</h3>{reason_html}"
        "</footer>"
    )


_CSS = """
:root { color-scheme: light; }
body { margin: 0; background: #f5f6f8; color: #1f262e; font: 14px/1.65
  "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 32px 24px 64px; }
.report-header h1 { margin: 0 0 8px; font-size: 24px; }
.muted { color: #5d6873; }
.badges { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.badge { background: #fff; border: 1px solid #d7dde4; border-radius: 999px;
  padding: 4px 12px; font-size: 13px; }
.report-section { background: #fff; border: 1px solid #e1e6eb; border-radius: 10px;
  margin: 20px 0; padding: 20px 24px; }
.report-section h2 { margin: 0 0 14px; font-size: 18px; }
.report-section h3 { margin: 18px 0 8px; font-size: 15px; }
.section-number { color: #78838f; margin-right: 6px; }
.report-table { width: 100%; border-collapse: collapse; margin: 8px 0; }
.report-table th, .report-table td { border-bottom: 1px solid #edf0f3;
  padding: 6px 8px; text-align: left; vertical-align: top; }
.report-table th { background: #f8f9fb; font-weight: 600; }
.reason-list { margin: 6px 0; padding-left: 20px; }
.blocked { color: #a33a35; font-weight: 600; }
.interval { background: #f3f6f9; border-left: 3px solid #4f7cb4; padding: 8px 12px; }
.report-footer { margin-top: 28px; padding: 18px 24px; color: #4a555f;
  background: #fff; border: 1px solid #e1e6eb; border-radius: 10px; }
@media print {
  body { background: #fff; }
  .report-section, .report-footer { border: none; padding: 0; }
}
"""


def _render(comparison: Mapping[str, Any]) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>记忆版本比较报告</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n"
        "<body>\n<main>\n"
        + _render_header(comparison)
        + _section("conditions", "01", "条件与变化项", _render_conditions(comparison))
        + _section("primary", "02", "主指标与区间", _render_primary(comparison))
        + _section("adoption", "03", "采用判定", _render_interval_and_adoption(comparison))
        + _section("secondary", "04", "分项指标（诊断/门禁）", _render_secondary(comparison))
        + _section("changes", "05", "改善 / 退化 / 未知", _render_changes(comparison))
        + _section("cost", "06", "成本拆分", _render_cost(comparison))
        + _section("gates", "07", "关键门禁", _render_gates(comparison))
        + _section("official", "08", "官方协议单列", _render_official(comparison))
        + _render_footer(comparison)
        + "</main>\n</body>\n</html>\n"
    )


def render_memory_comparison_report(snapshot: Any) -> str:
    """把比较快照渲染成自包含 HTML（同一份输入逐字节稳定）。"""
    return build_comparison_snapshot(snapshot).to_html()


__all__ = [
    "COMPARISON_SNAPSHOT_VERSION",
    "MemoryComparisonSnapshot",
    "build_comparison_snapshot",
    "render_memory_comparison_report",
]
