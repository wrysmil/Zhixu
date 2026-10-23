"""成员产物 envelope：解析、契约级校验与**完整长稿**落库（plan Task 7 / 契约 §1/§5）。

设计口径
--------

成员（LLM）只能通过一段约定格式的 JSON 交付产物，本模块是它唯一的解析与落库出口：

- **只认服务端字段**：``kind`` / ``roleId`` / ``summary`` / ``sections`` / ``sourceIds`` /
  ``gaps``。客户端自称的 ``articleId`` / ``runId`` / ``version`` / ``revision`` /
  ``digest`` 一律**不读**，身份由服务端补齐（契约 §1）。
- **配方即白名单**：``write-collab`` 的角色→产物类型配套（契约 §7）与动作配方
  （``collab.recipe``）共同决定哪份 envelope 可采纳；配方外交付一律拒绝。
- **不静默裁切**：正文超过 :data:`MAX_ENVELOPE_BODY_CHARS` 时按**固定 message** 拒绝，
  由上层重派一次格式/分节修正；绝不把长稿截断后当成完整产物落库。
- **坏输出只作诊断**：解析不出来就返回 ``None`` 并打结构化日志，绝不抛异常打断
  团队事件循环（``CollabRunner.on_team_event`` 的闸门在它之前）。

落库形态：``writing_collab_artifacts.content_json`` 存 ``{kind, role_id, summary, body,
sections, notes, gaps}``，其中 ``body`` 是逐字正文；列表投影
（``Artifact.to_listing_dict``）不含它，只有详情查询才返回（契约 §3）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Any, cast

from loguru import logger as default_logger

from nanobot.writing.collab.models import (
    ARTIFACT_KINDS,
    ARTIFACT_ROLE_IDS,
    Artifact,
)
from nanobot.writing.collab.recipe import (
    EDITOR_ROLE,
    RESEARCHER_ROLE,
    RecipeError,
    require_artifact_kind,
    require_role,
)
from nanobot.writing.collab.repository import CollabRepository

__all__ = [
    "ENVELOPE_FENCE",
    "ENVELOPE_INVALID_MESSAGE",
    "ENVELOPE_TOO_LARGE_MESSAGE",
    "MAX_ENVELOPE_BODY_CHARS",
    "MAX_ENVELOPE_SECTIONS",
    "MAX_ENVELOPE_SUMMARY_CHARS",
    "ROLE_ARTIFACT_KINDS",
    "Envelope",
    "EnvelopeArtifactSink",
    "EnvelopeError",
    "IngestedArtifact",
    "Section",
    "envelope_output_contract",
    "parse_envelope",
    "validate_envelope",
]

#: 成员交付用的代码块标记（三反引号围栏，语言标记为 writing-envelope）。
ENVELOPE_FENCE = "writing-envelope"

#: 单份产物的正文上限。低于 Team 侧 ``MAX_TASK_RESULT_CHARS=64000``，这样「超限」永远
#: 由本模块**明确拒绝**（而不是被 Team 侧静默截断）暴露出来。
MAX_ENVELOPE_BODY_CHARS = 32_000
#: 分节数量上限：超过按「超限」拒绝并提示分节。
MAX_ENVELOPE_SECTIONS = 64
#: 摘要上限；它只是投影字段，超限同样走固定 message，避免「悄悄变短」。
MAX_ENVELOPE_SUMMARY_CHARS = 2_000

#: 超限的固定 message（契约 §4 的 413 文案由传输层翻译，语义在领域层固定）。
ENVELOPE_TOO_LARGE_MESSAGE = "writing envelope is too large; split the draft into sections"
#: 结构不合法（解析成功但字段不成立）的固定前缀。
ENVELOPE_INVALID_MESSAGE = "writing envelope is not a valid collaboration artifact"

#: 角色 → 允许交付的产物类型（契约 §7 的 Skill→角色配套，转成产物类型）。
ROLE_ARTIFACT_KINDS: dict[str, tuple[str, ...]] = {
    EDITOR_ROLE: ("brief", "review"),
    RESEARCHER_ROLE: ("material_list",),
    "writing-architect": ("outline",),
    "writing-author": ("draft", "rewrite"),
    "writing-reviewer": ("review",),
}

#: 客户端**不得**影响服务端归属的键；出现即丢弃（不报错，也不采信）。
_IDENTITY_KEYS = frozenset(
    {
        "id", "artifactId", "artifact_id", "articleId", "article_id", "runId", "run_id",
        "collaborationId", "collaboration_id", "batchId", "batch_id", "version", "revision",
        "digest", "contentDigest", "content_digest", "workspaceKey", "workspace_key",
        "status", "createdAt", "created_at",
    }
)

_FENCE_RE = re.compile(
    r"```[ \t]*" + re.escape(ENVELOPE_FENCE) + r"[ \t]*\r?\n(.*?)```",
    re.DOTALL,
)


class EnvelopeError(ValueError):
    """envelope 校验失败；``status`` 供传输层翻译（超限为 413，其余 400）。"""

    status = 400
    code = "envelope_invalid"

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status


@dataclass(frozen=True, slots=True)
class Section:
    """一段正文；``id`` 与 ``title`` 只用于展示与定位，正文才是交付物。"""

    id: str
    text: str
    title: str = ""


@dataclass(frozen=True, slots=True)
class Envelope:
    """一份可采纳的成员产物。"""

    kind: str
    role_id: str
    summary: str
    sections: tuple[Section, ...]
    source_ids: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    notes: str = ""

    def body_text(self) -> str:
        """完整正文：分节按顺序拼接，**不截断**。"""
        return "\n\n".join(section.text for section in self.sections)

    def content(self) -> dict[str, Any]:
        """落库载荷（``content_json``）：只含服务端认下的字段。"""
        return {
            "kind": self.kind,
            "role_id": self.role_id,
            "summary": self.summary,
            "body": self.body_text(),
            "sections": [
                {"id": section.id, "title": section.title, "text": section.text}
                for section in self.sections
            ],
            "notes": self.notes,
            "gaps": list(self.gaps),
        }


@dataclass(frozen=True, slots=True)
class IngestedArtifact(Artifact):
    """落库产物 + 一行**摘要**。

    ``Artifact`` 是仓储行投影，没有 ``summary`` 字段；``CollabRunner`` 读产物时会取
    ``summary`` 去投递 ``writing_artifact`` 事件（契约 §5），所以这里在行投影上补一行
    摘要——正文仍然只在 :attr:`Artifact.content` 里，事件负载永远拿不到全文。
    """

    summary: str = ""


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _extract_payload(text: str) -> Mapping[str, Any] | None:
    """从成员输出里找出 envelope JSON 对象；找不到返回 ``None``。"""
    candidates: list[str] = [match.strip() for match in _FENCE_RE.findall(text or "")]
    stripped = (text or "").strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    for candidate in candidates:
        try:
            parsed: Any = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, Mapping):
            return cast("Mapping[str, Any]", parsed)
    return None


def _sections(raw: Any, *, body: str) -> tuple[Section, ...] | None:
    if raw is None:
        return (Section(id="body", text=body),) if body else None
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return None
    sections: list[Section] = []
    for index, item in enumerate(cast("Sequence[Any]", raw)):
        if isinstance(item, str):
            sections.append(Section(id=f"s{index + 1}", text=item))
            continue
        if not isinstance(item, Mapping):
            return None
        entry = cast("Mapping[str, Any]", item)
        text = _text(entry.get("text")) or _text(entry.get("content")) or _text(entry.get("body"))
        if not text:
            return None
        sections.append(
            Section(
                id=_text(entry.get("id")) or f"s{index + 1}",
                text=text,
                title=_text(entry.get("title")),
            )
        )
    return tuple(sections) or None


def _source_ids(raw: Any) -> tuple[str, ...] | None:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return None
    values: list[str] = []
    for item in cast("Sequence[Any]", raw):
        if not isinstance(item, str) or not item.strip():
            return None
        if item not in values:
            values.append(item)
    return tuple(values)


def _gaps(raw: Any) -> tuple[str, ...] | None:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return None
    values: list[str] = []
    for item in cast("Sequence[Any]", raw):
        if not isinstance(item, str):
            return None
        values.append(item)
    return tuple(values)


def parse_envelope(text: str) -> Envelope | None:
    """解析成员输出；不是 envelope 就返回 ``None``（只作诊断，不抛错）。

    ``None`` 与 :class:`EnvelopeError` 的区别：``None`` 表示「这段输出我读不懂」，
    上层可修正一次格式；``EnvelopeError`` 表示「读懂了但不合法」，需要作者侧处理。
    """
    payload = _extract_payload(text)
    if payload is None:
        return None
    kind = payload.get("kind")
    role_id = payload.get("roleId", payload.get("role_id"))
    if not isinstance(kind, str) or kind not in ARTIFACT_KINDS:
        return None
    if not isinstance(role_id, str) or role_id not in ARTIFACT_ROLE_IDS:
        return None
    body = _text(payload.get("body"))
    sections = _sections(payload.get("sections"), body=body)
    if sections is None:
        return None
    source_ids = _source_ids(payload.get("sourceIds", payload.get("source_ids")))
    if source_ids is None:
        return None
    gaps = _gaps(payload.get("gaps"))
    if gaps is None:
        return None
    summary = _text(payload.get("summary")) or sections[0].text[:200]
    return Envelope(
        kind=kind,
        role_id=role_id,
        summary=summary,
        sections=sections,
        source_ids=source_ids,
        gaps=gaps,
        notes=_text(payload.get("notes")),
    )


def _check_size(envelope: Envelope) -> None:
    if len(envelope.sections) > MAX_ENVELOPE_SECTIONS:
        raise EnvelopeError(ENVELOPE_TOO_LARGE_MESSAGE, status=413)
    if len(envelope.body_text()) > MAX_ENVELOPE_BODY_CHARS:
        raise EnvelopeError(ENVELOPE_TOO_LARGE_MESSAGE, status=413)
    if len(envelope.summary) > MAX_ENVELOPE_SUMMARY_CHARS:
        raise EnvelopeError(ENVELOPE_TOO_LARGE_MESSAGE, status=413)


def validate_envelope(
    envelope: Envelope,
    *,
    action: str,
    known_source_ids: Collection[str] | None = None,
) -> Envelope:
    """按角色配套 + 动作配方 + 来源范围校验；不合法抛 :class:`EnvelopeError`。"""
    if envelope.kind not in ARTIFACT_KINDS:
        raise EnvelopeError(f"{ENVELOPE_INVALID_MESSAGE}: unsupported kind {envelope.kind!r}")
    if envelope.role_id not in ARTIFACT_ROLE_IDS:
        raise EnvelopeError(f"{ENVELOPE_INVALID_MESSAGE}: unsupported roleId {envelope.role_id!r}")
    allowed = ROLE_ARTIFACT_KINDS.get(envelope.role_id, ())
    if envelope.kind not in allowed:
        raise EnvelopeError(
            f"{ENVELOPE_INVALID_MESSAGE}: role {envelope.role_id} cannot deliver"
            f" artifact kind {envelope.kind}"
        )
    try:
        require_artifact_kind(action, envelope.kind)
        require_role(action, envelope.role_id)
    except RecipeError as error:
        raise EnvelopeError(f"{ENVELOPE_INVALID_MESSAGE}: {error.message}") from error
    if known_source_ids is not None:
        unknown = [item for item in envelope.source_ids if item not in known_source_ids]
        if unknown:
            raise EnvelopeError(
                f"{ENVELOPE_INVALID_MESSAGE}: unknown sourceIds {', '.join(unknown)}"
            )
    _check_size(envelope)
    return envelope


def envelope_output_contract(*, role_id: str, kind: str, action: str) -> str:
    """给成员的交付格式说明（写进任务指令；说话人身份与产物类型由服务端固定）。"""
    template = {
        "kind": kind,
        "roleId": role_id,
        "summary": "一句话摘要（≤2000 字符）",
        "sections": [{"id": "s1", "title": "小节标题", "text": "该节正文"}],
        "sourceIds": ["引用的素材或已交付产物 id"],
        "gaps": ["仍需作者确认的疑点"],
    }
    return (
        "## 交付格式（必须遵守）\n\n"
        f"你在本批次里扮演 `{role_id}`，只允许交付 `{kind}`。"
        "请把成果放在一个 ```" + ENVELOPE_FENCE + "``` 代码块里，内容是 JSON：\n\n"
        "```" + ENVELOPE_FENCE + "\n"
        + json.dumps(template, ensure_ascii=False, indent=2)
        + "\n```\n\n"
        "约定：\n"
        f"- `kind` 固定为 `{kind}`，`roleId` 固定为 `{role_id}`，不要改；\n"
        "- 正文逐字写在 `sections[].text`（长稿请分节，不要压缩或省略）；\n"
        "- 不要在 JSON 里写 id / 版本 / 时间等身份字段，服务端会补齐；\n"
        f"- 单份正文合计不超过 {MAX_ENVELOPE_BODY_CHARS} 字符；超出请分节交付。\n"
        f"- 本批次动作是 `{action}`；产出不属于该动作的产物类型一律不会被采纳。\n"
    )


# ---- 落库出口（``CollabRunner`` 的 ``ArtifactSink``） ---------------------------


def _get(source: Any, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return cast("Mapping[str, Any]", source).get(name)
    return getattr(source, name, None)


def _event_task(event: Any) -> Any:
    """从团队事件里取任务快照；不认识的事件返回 ``None``。"""
    return _get(_get(event, "data"), "task")


def _artifact_from_row(row: Artifact, *, summary: str) -> IngestedArtifact:
    values = {
        field.name: getattr(row, field.name) for field in fields(Artifact)
    }
    values["summary"] = summary
    return IngestedArtifact(**values)


class EnvelopeArtifactSink:
    """把 ``task.finished`` 事件里的成员输出落成契约 §1 的类型化产物。

    ``ingest`` **永不抛异常**：runner 的事件闸门在它之前，落库出口的任何失败都只降级成
    「这一条没有采纳」。需要外部判定「是否采纳」的调用方看返回值是不是 ``None``。
    """

    def __init__(
        self,
        *,
        repository: CollabRepository,
        teams: Any = None,
        articles: Any = None,
        workspace_key: str = "",
        logger: Any = default_logger,
    ) -> None:
        self._repository = repository
        self._teams = teams
        self._articles = articles
        self._workspace_key = workspace_key
        self._logger = logger

    # ---- 公开入口 -----------------------------------------------------------

    def ingest(
        self,
        *,
        collaboration_id: str,
        batch_id: str,
        attempt: int,
        event: Any,
    ) -> IngestedArtifact | None:
        try:
            return self._ingest(
                collaboration_id=collaboration_id,
                batch_id=batch_id,
                attempt=attempt,
                event=event,
            )
        except Exception as error:  # noqa: BLE001 - 落库出口不得打断事件循环
            self._log(
                "warning",
                event="writing_collab_envelope_ingest_failed",
                collaboration_id=collaboration_id,
                batch_id=batch_id,
                attempt=attempt,
                error_type=type(error).__name__,
                error=str(error),
            )
            return None

    # ---- 内部 ---------------------------------------------------------------

    def _ingest(
        self,
        *,
        collaboration_id: str,
        batch_id: str,
        attempt: int,
        event: Any,
    ) -> IngestedArtifact | None:
        task = _event_task(event)
        if task is None:
            self._log(
                "debug",
                event="writing_collab_envelope_no_task",
                collaboration_id=collaboration_id,
                batch_id=batch_id,
                attempt=attempt,
            )
            return None
        result = _get(task, "result")
        status = _text(_get(result, "status")) or _text(_get(task, "status"))
        if result is None or status != "succeeded":
            # 失败/取消/超时的成员输出只作诊断，不生成可采纳卡。
            self._log(
                "debug",
                event="writing_collab_envelope_not_succeeded",
                collaboration_id=collaboration_id,
                batch_id=batch_id,
                attempt=attempt,
                task_id=_text(_get(task, "task_id")),
                status=status,
            )
            return None

        task_id = _text(_get(task, "task_id"))
        batch = self._repository.get_batch(batch_id)
        text = self._member_text(task)
        envelope = parse_envelope(text)
        if envelope is None:
            self._log(
                "warning",
                event="writing_collab_envelope_unparsed",
                collaboration_id=collaboration_id,
                batch_id=batch_id,
                attempt=attempt,
                task_id=task_id,
                chars=len(text),
            )
            return None
        try:
            validate_envelope(
                envelope,
                action=batch.action,
                known_source_ids=self._known_source_ids(collaboration_id),
            )
        except EnvelopeError as error:
            self._log(
                "warning",
                event="writing_collab_envelope_rejected",
                collaboration_id=collaboration_id,
                batch_id=batch_id,
                attempt=attempt,
                task_id=task_id,
                kind=envelope.kind,
                role_id=envelope.role_id,
                reason=error.message,
            )
            return None

        stored = self._repository.add_artifact(
            collaboration_id=collaboration_id,
            batch_id=batch_id,
            attempt=attempt,
            kind=envelope.kind,
            role_id=envelope.role_id,
            content=envelope.content(),
            source_ids=list(envelope.source_ids),
            gaps=list(envelope.gaps),
        )
        self._log(
            "info",
            event="writing_collab_envelope_ingested",
            collaboration_id=collaboration_id,
            batch_id=batch_id,
            attempt=attempt,
            task_id=task_id,
            artifact_id=stored.id,
            kind=stored.kind,
            role_id=stored.role_id,
            version=stored.version,
            body_chars=len(envelope.body_text()),
        )
        return _artifact_from_row(stored, summary=envelope.summary)

    def _member_text(self, task: Any) -> str:
        """成员完整产出：优先读 Team 侧 ``fullResult``，否则用摘要（短输出两者等价）。"""
        task_id = _text(_get(task, "task_id"))
        if self._teams is not None and task_id:
            full = self._teams.get_task_full_result(task_id)
            if full:
                return full
        return _text(_get(_get(task, "result"), "summary"))

    def _known_source_ids(self, collaboration_id: str) -> set[str]:
        """本批次可引用的来源：文章素材 id + 本协作已交付的产物 id。"""
        known: set[str] = set()
        if self._articles is not None:
            try:
                collaboration = self._repository.get_collaboration(collaboration_id)
                payload = self._articles.get_article(collaboration.article_id)
            except Exception:  # noqa: BLE001 - 读不到就退化成「只认产物 id」
                payload = {}
            article = payload.get("article") if isinstance(payload, Mapping) else None
            materials = _get(article, "materials")
            if isinstance(materials, Sequence) and not isinstance(materials, (str, bytes)):
                for item in cast("Sequence[Any]", materials):
                    material_id = _text(_get(item, "id"))
                    if material_id:
                        known.add(material_id)
        try:
            known.update(self._repository.list_artifacts(collaboration_id))
        except Exception:  # noqa: BLE001
            pass
        return known

    def _log(self, level: str, **fields: Any) -> None:
        """结构化日志：字段即查询键，message 只作人读标语（正文与全文一律不记）。"""
        emit = getattr(self._logger.bind(**fields), level, None)
        if callable(emit):
            emit(str(fields.get("event") or "writing collaboration envelope"))
