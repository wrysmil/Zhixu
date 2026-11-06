"""经验库：把“这次发生过什么”写进 SQLite episodes，按需检索。

与长期记忆（MEMORY.md）严格分离：
- 经验只进 ``episodes``（``source=experience``），不写记忆文件；
- 长期规则只进 MEMORY.md，不写经验库；
- 两者之间只允许经用户确认后的“经验升级为规则”，不做内容镜像。

类型只存在于 ``episodes.metadata.kind``，不新增表：
``experience`` / ``skill`` / ``pitfall`` / ``decision`` / ``reference``。
缺省（旧数据）按 ``experience`` 处理。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Episode, EpisodeOutcome, EpisodeSource
from nanobot.memory.repository import add_episode

EXPERIENCE_SOURCE = EpisodeSource.EXPERIENCE.value

KINDS: tuple[str, ...] = ("experience", "skill", "pitfall", "decision", "reference")
STATUSES: tuple[str, ...] = ("active", "deprecated", "superseded")
DEFAULT_KIND = "experience"
DEFAULT_STATUS = "active"


@dataclass(frozen=True)
class ExperienceRow:
    """经验列表/检索的轻量返回行。"""

    id: str
    title: str
    summary: str
    outcome: str
    workspace_id: str
    ended_at: str
    root_cause: str = ""
    solution: str = ""
    kind: str = DEFAULT_KIND
    status: str = DEFAULT_STATUS
    version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _text_list(value: Sequence[Any] | None) -> list[str]:
    out: list[str] = []
    for item in value or ():
        cleaned = _text(item)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def kind_of_metadata(metadata: dict[str, Any]) -> str:
    """读取条目类型；旧数据没有 kind 时按 experience 处理。"""
    raw = _text(metadata.get("kind")) or DEFAULT_KIND
    return raw if raw in KINDS else DEFAULT_KIND


def status_of_metadata(metadata: dict[str, Any]) -> str:
    raw = _text(metadata.get("status")) or DEFAULT_STATUS
    return raw if raw in STATUSES else DEFAULT_STATUS


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_kind_fields(
    *,
    kind: str,
    title: str,
    situation: str,
    capability: str,
    steps: list[str],
    symptom: str,
    fix: str,
    context: str,
    choice: str,
    pointer: str,
) -> None:
    """按 kind 校验必填项；保证 summary 一定有可检索内容。"""
    _require(bool(title), "title 不能为空")
    if kind == "experience":
        _require(bool(situation), "experience 需要 situation（发生了什么）")
    elif kind == "skill":
        _require(bool(capability), "skill 需要 capability（这项技能解决什么）")
        _require(bool(steps), "skill 需要至少一个步骤")
    elif kind == "pitfall":
        _require(bool(symptom), "pitfall 需要 symptom（症状/报错）")
        _require(bool(fix), "pitfall 需要 fix（修复方式）")
    elif kind == "decision":
        _require(bool(context), "decision 需要 context（背景）")
        _require(bool(choice), "decision 需要 choice（结论）")
    elif kind == "reference":
        _require(bool(pointer), "reference 需要 pointer（文件/链接）")


def _compose_summary(
    *,
    kind: str,
    title: str,
    situation: str,
    root_cause: str,
    solution: str,
    verification: str,
    capability: str,
    prerequisites: list[str],
    steps: list[str],
    symptom: str,
    fix: str,
    context: str,
    options: list[dict[str, Any]],
    choice: str,
    rationale: str,
    pointer: str,
    what_for: str,
) -> str:
    if kind == "skill":
        lines = [f"能力：{capability}"]
        if prerequisites:
            lines.append(f"前置：{'；'.join(prerequisites)}")
        lines.append("步骤：")
        lines.extend(f"{index}. {step}" for index, step in enumerate(steps, start=1))
        if verification:
            lines.append(f"验证：{verification}")
        return "\n".join(lines)
    if kind == "pitfall":
        lines = [f"症状：{symptom}"]
        if root_cause:
            lines.append(f"根因：{root_cause}")
        lines.append(f"修复：{fix}")
        if verification:
            lines.append(f"验证：{verification}")
        return "\n".join(lines)
    if kind == "decision":
        lines = [f"背景：{context}"]
        if options:
            rendered = "；".join(
                f"{_text(option.get('name'))}"
                f"（优点：{_text(option.get('pros')) or '—'}；"
                f"缺点：{_text(option.get('cons')) or '—'}）"
                for option in options
            )
            lines.append(f"选项：{rendered}")
        lines.append(f"结论：{choice}")
        if rationale:
            lines.append(f"理由：{rationale}")
        return "\n".join(lines)
    if kind == "reference":
        lines = [f"用途：{what_for or title}", f"指针：{pointer}"]
        return "\n".join(lines)
    return f"{situation}\n根因：{root_cause}\n解法：{solution}"


def _compose_goal(
    *, kind: str, title: str, situation: str, capability: str,
    symptom: str, context: str, what_for: str,
) -> str:
    primary = {
        "skill": capability,
        "pitfall": symptom,
        "decision": context,
        "reference": what_for,
    }.get(kind, situation)
    return _text(primary) or title


def record_experience(
    db: MemoryDatabase,
    *,
    workspace_id: str,
    session_id: str,
    title: str,
    kind: str = DEFAULT_KIND,
    situation: str = "",
    action: str = "",
    outcome: str | None = None,
    root_cause: str = "",
    solution: str = "",
    verification: str = "",
    tags: Sequence[str] = (),
    status: str = DEFAULT_STATUS,
    capability: str = "",
    prerequisites: Sequence[str] = (),
    steps: Sequence[str] = (),
    symptom: str = "",
    fix: str = "",
    context: str = "",
    options: Sequence[dict[str, Any]] = (),
    choice: str = "",
    rationale: str = "",
    pointer: str = "",
    what_for: str = "",
    version: str = "",
    supersedes: str = "",
) -> Episode:
    """写一条沉淀记录（experience/skill/pitfall/decision/reference）。

    只写 ``episodes``，不写长期记忆；类型保存在 ``metadata.kind``。
    """
    title = _text(title)
    kind = _text(kind) or DEFAULT_KIND
    if kind not in KINDS:
        raise ValueError(f"kind 必须是 {' / '.join(KINDS)}")
    status = _text(status) or DEFAULT_STATUS
    if status not in STATUSES:
        raise ValueError(f"status 必须是 {' / '.join(STATUSES)}")
    situation = _text(situation)
    capability = _text(capability)
    symptom = _text(symptom)
    fix = _text(fix)
    context = _text(context)
    choice = _text(choice)
    pointer = _text(pointer)
    what_for = _text(what_for)
    steps_list = _text_list(steps)
    prerequisites_list = _text_list(prerequisites)
    _validate_kind_fields(
        kind=kind,
        title=title,
        situation=situation,
        capability=capability,
        steps=steps_list,
        symptom=symptom,
        fix=fix,
        context=context,
        choice=choice,
        pointer=pointer,
    )

    resolved_outcome = _text(outcome) or ("failed" if kind == "experience" else "completed")
    summary = _compose_summary(
        kind=kind,
        title=title,
        situation=situation,
        root_cause=_text(root_cause),
        solution=_text(solution),
        verification=_text(verification),
        capability=capability,
        prerequisites=prerequisites_list,
        steps=steps_list,
        symptom=symptom,
        fix=fix,
        context=context,
        options=[option for option in options if isinstance(option, dict)],
        choice=choice,
        rationale=_text(rationale),
        pointer=pointer,
        what_for=what_for,
    )
    now = datetime.now(timezone.utc).isoformat()
    metadata: dict[str, Any] = {
        "title": title,
        "kind": kind,
        "status": status,
        "action": _text(action),
        "root_cause": _text(root_cause),
        "solution": _text(solution),
        "verification": _text(verification),
    }
    if kind == "skill":
        metadata.update({
            "capability": capability,
            "prerequisites": prerequisites_list,
            "steps": steps_list,
            "version": _text(version),
            "supersedes": _text(supersedes),
        })
    elif kind == "pitfall":
        metadata.update({"symptom": symptom, "fix": fix})
    elif kind == "decision":
        metadata.update({
            "context": context,
            "options": [option for option in options if isinstance(option, dict)],
            "choice": choice,
            "rationale": _text(rationale),
            "supersedes": _text(supersedes),
        })
    elif kind == "reference":
        metadata.update({"pointer": pointer, "what_for": what_for})
    tag_list = _text_list(tags)
    episode = Episode(
        id=uuid4().hex,
        session_id=session_id,
        summary=summary,
        goal=_compose_goal(
            kind=kind,
            title=title,
            situation=situation,
            capability=capability,
            symptom=symptom,
            context=context,
            what_for=what_for,
        ),
        outcome=EpisodeOutcome(resolved_outcome),
        source=EpisodeSource.EXPERIENCE,
        started_at=now,
        ended_at=now,
        entities=list(dict.fromkeys([*tag_list, title, kind])),
        tools_used=[],
        tags=list(dict.fromkeys(["experience", resolved_outcome, kind, status, *tag_list])),
        importance_score=0.6,
        workspace_id=workspace_id,
        metadata=metadata,
    )
    db.ensure_schema()
    with db.connect() as conn:
        add_episode(conn, episode)
    # 向量索引是派生数据，best-effort；未启用向量时是 no-op。
    try:
        from nanobot.memory.vector.indexer import index_episode_best_effort

        index_episode_best_effort(episode)
    except Exception:  # noqa: BLE001 - 索引故障绝不影响落库
        pass
    return episode


def list_experiences(
    db: MemoryDatabase,
    *,
    workspace_ids: Sequence[str] | None = None,
    query: str = "",
    outcome: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[ExperienceRow]:
    """列出/检索沉淀条目；``query`` 为空时按时间倒序列出最近记录。"""
    db.ensure_schema()
    conditions = ["source = ?"]
    params: list[Any] = [EXPERIENCE_SOURCE]
    workspace_ids = tuple(workspace_ids or ())
    if workspace_ids:
        placeholders = ",".join("?" for _ in workspace_ids)
        conditions.append(f"workspace_id IN ({placeholders})")
        params.extend(workspace_ids)
    if kind is not None:
        if kind not in KINDS:
            raise ValueError(f"kind 必须是 {' / '.join(KINDS)}")
        if kind == DEFAULT_KIND:
            conditions.append(
                "(json_extract(metadata,'$.kind') = ? "
                "OR json_extract(metadata,'$.kind') IS NULL)"
            )
        else:
            conditions.append("json_extract(metadata,'$.kind') = ?")
        params.append(kind)
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"status 必须是 {' / '.join(STATUSES)}")
        if status == DEFAULT_STATUS:
            conditions.append(
                "(json_extract(metadata,'$.status') = ? "
                "OR json_extract(metadata,'$.status') IS NULL)"
            )
        else:
            conditions.append("json_extract(metadata,'$.status') = ?")
        params.append(status)
    if outcome:
        conditions.append("outcome = ?")
        params.append(outcome)
        if not kind:
            conditions.append(
                "(json_extract(metadata,'$.kind') IN ('experience','pitfall') "
                "OR json_extract(metadata,'$.kind') IS NULL)"
            )
    query = query.strip()
    if query:
        like = f"%{query}%"
        conditions.append(
            "(summary LIKE ? OR goal LIKE ? OR entities LIKE ? OR tools_used LIKE ?)"
        )
        params.extend([like, like, like, like])
    params.append(max(1, int(limit)))
    sql = (
        "SELECT id, summary, outcome, ended_at, workspace_id, metadata "
        "FROM episodes "
        f"WHERE {' AND '.join(conditions)} "
        "ORDER BY datetime(ended_at) DESC, id DESC LIMIT ?"
    )
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    results: list[ExperienceRow] = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        results.append(
            ExperienceRow(
                id=str(row["id"]),
                title=str(metadata.get("title") or ""),
                summary=str(row["summary"] or ""),
                outcome=str(row["outcome"] or ""),
                workspace_id=str(row["workspace_id"] or ""),
                ended_at=str(row["ended_at"] or ""),
                root_cause=str(metadata.get("root_cause") or ""),
                solution=str(metadata.get("solution") or ""),
                kind=kind_of_metadata(metadata),
                status=status_of_metadata(metadata),
                version=str(metadata.get("version") or ""),
                metadata=metadata,
            )
        )
    return results


__all__ = [
    "DEFAULT_KIND",
    "DEFAULT_STATUS",
    "EXPERIENCE_SOURCE",
    "KINDS",
    "STATUSES",
    "ExperienceRow",
    "kind_of_metadata",
    "list_experiences",
    "record_experience",
    "status_of_metadata",
]
