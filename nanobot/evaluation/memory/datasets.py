"""记忆集合的流式导入、CAS 编辑与导出（契约 §1/§6/§7，plan §8 Task 6）。

三条硬约束在这里落地：

1. **增量解码 + 缓冲受限**：JSON 数组用标准库 ``JSONDecoder.raw_decode`` 逐条消费，
   JSONL 逐行；单条缓冲与单文件缓冲都有上限，UTF-8 跨块不乱码。**尾部不完整的 JSON
   明确报错**，绝不静默丢数据。
2. **staging → 原子发布 ready**：任一条场景不合法，整批都不进入 ready（作业记
   ``invalid`` 并留下诊断）；重复导入幂等；同 ``dataset_version`` 内容不一致报冲突。
3. **错误信息不回显本机路径**：诊断只给位置与错误码，路径一律替换成占位符。

跨 split 隔离（契约 §1）在两层执行：同一批内的 template_family/人物必须同 split；
同一 suite 的既有修订里出现过的 template_family/人物不能出现在别的 split。
"""

from __future__ import annotations

import codecs
import hashlib
import json
import re
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Literal

from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.memory.models import MemoryDatasetSource, MemoryScenario
from nanobot.evaluation.memory.repository import (
    MemoryRecordNotFound,
    MemoryRepository,
    MemoryRevisionConflict,
)

# ---------------------------------------------------------------- 契约 §6 限额

#: 单文件上限 2GiB。
MAX_FILE_BYTES = 2 * 1024**3
#: 单个场景上限 32MiB。
MAX_SCENARIO_BYTES = 32 * 1024**2
#: 单个集合最多 2000 个场景。
MAX_SCENARIOS = 2000
#: 单个场景最多 100000 条消息。
MAX_MESSAGES_PER_SCENARIO = 100_000

#: 读取块大小：够小以免缓冲爆掉，够大以免系统调用过密。
CHUNK_BYTES = 64 * 1024

# ---------------------------------------------------------------- 错误码

CODE_SCHEMA_INVALID = "MEMORY_SCHEMA_INVALID"
CODE_TOO_LARGE = "MEMORY_IMPORT_TOO_LARGE"
CODE_REVISION_CONFLICT = "REVISION_CONFLICT"
CODE_SPLIT_LEAK = "MEMORY_SPLIT_LEAK"
CODE_SOURCE_HASH_MISMATCH = "MEMORY_SOURCE_HASH_MISMATCH"
CODE_DISK_FULL = "MEMORY_IMPORT_DISK_FULL"
CODE_NOT_READY = "MEMORY_DATASET_NOT_READY"


class MemoryDatasetError(RuntimeError):
    """带错误码与诊断的集合导入错误。

    ``diagnostics`` 是结构化列表（每条含 code/位置/原因），供 ``GET /memory/imports``
    分页返回；异常消息本身已经过路径脱敏。
    """

    def __init__(
        self,
        code: str,
        message: str = "",
        diagnostics: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self.code = code
        self.message = message
        self.diagnostics = [dict(item) for item in diagnostics]
        super().__init__(f"{code}: {message}" if message else code)


#: 绝对路径（Windows 盘符、UNC、POSIX 绝对路径）→ 占位符。
_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\)[^\s\"']*|(?<![\w:])/(?:[\w.\-]+/)+[\w.\-]*"
)


def redact_paths(text: str) -> str:
    """把本机路径换成 ``<path>``：导入错误不能泄露服务端目录结构。"""
    return _PATH_PATTERN.sub("<path>", text)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------- 流式解析


class _HashingReader:
    """读取时同步累计 sha256：上传内容必须与来源声明一致。"""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._hash = hashlib.sha256()
        self._length = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._stream.read(size)
        if chunk:
            self._hash.update(chunk)
            self._length += len(chunk)
        return chunk

    @property
    def hexdigest(self) -> str:
        return self._hash.hexdigest()

    @property
    def length(self) -> int:
        return self._length


def _jsonl_line(
    line: str, line_no: int, max_scenario_bytes: int
) -> dict[str, Any] | None:
    """解析一行 JSONL；空行返回 ``None``（跳过），畸形行明确报错。"""
    if len(line) > max_scenario_bytes or len(line.encode("utf-8")) > max_scenario_bytes:
        raise MemoryDatasetError(
            CODE_TOO_LARGE, f"line {line_no} exceeds the per-scenario size limit"
        )
    stripped = line.strip()
    if not stripped:
        return None
    try:
        item = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise MemoryDatasetError(
            CODE_SCHEMA_INVALID,
            f"line {line_no} is not valid JSON: {exc.msg} (column {exc.colno})",
        ) from exc
    if not isinstance(item, dict):
        raise MemoryDatasetError(CODE_SCHEMA_INVALID, f"line {line_no} is not a JSON object")
    return item


def _read_jsonl(
    stream: BinaryIO, *, max_file_bytes: int, max_scenario_bytes: int
) -> Iterator[dict[str, Any]]:
    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    line_no = 0
    total = 0
    while True:
        chunk = stream.read(CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_file_bytes:
            raise MemoryDatasetError(CODE_TOO_LARGE, "upload exceeds the per-file size limit")
        try:
            buffer += decoder.decode(chunk)
        except UnicodeDecodeError as exc:
            raise MemoryDatasetError(
                CODE_SCHEMA_INVALID, f"invalid utf-8 near line {line_no + 1}"
            ) from exc
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line_no += 1
            item = _jsonl_line(line, line_no, max_scenario_bytes)
            if item is not None:
                yield item
    try:
        buffer += decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise MemoryDatasetError(CODE_SCHEMA_INVALID, "invalid utf-8 at end of file") from exc
    line_no += 1
    item = _jsonl_line(buffer, line_no, max_scenario_bytes)
    if item is not None:
        yield item


def _looks_incomplete(exc: json.JSONDecodeError, buffer: str, pos: int) -> bool:
    """判断解码错误是"数据还没到齐"还是"这一条就是坏的"。

    未闭合字符串与"错误点在缓冲区末尾"都算没到齐——前者是 JSON 规范里唯一会
    明确标注"未闭合"的情形，后者覆盖被截断的对象/数组。
    """
    if exc.msg.startswith("Unterminated string"):
        return True
    return exc.pos >= max(len(buffer) - 1, pos)


def _read_json_array(
    stream: BinaryIO, *, max_file_bytes: int, max_scenario_bytes: int
) -> Iterator[dict[str, Any]]:
    decoder = codecs.getincrementaldecoder("utf-8")()
    json_decoder = json.JSONDecoder()
    buffer = ""
    pos = 0
    total = 0
    expect_open = True
    finished = False
    tail = ""
    last_error: json.JSONDecodeError | None = None
    seen_item = False
    pending_comma = False

    while True:
        chunk = stream.read(CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_file_bytes:
            raise MemoryDatasetError(CODE_TOO_LARGE, "upload exceeds the per-file size limit")
        try:
            buffer += decoder.decode(chunk)
        except UnicodeDecodeError as exc:
            raise MemoryDatasetError(CODE_SCHEMA_INVALID, "invalid utf-8 in json array") from exc
        if finished:
            # 数组已经读完：剩下的内容只允许是空白。
            tail += buffer[pos:]
            pos = len(buffer)
            if len(tail) > 4096:
                raise MemoryDatasetError(
                    CODE_SCHEMA_INVALID, "unexpected content after the json array"
                )
            continue
        if pos > CHUNK_BYTES:
            buffer = buffer[pos:]
            pos = 0
        while True:
            while pos < len(buffer) and buffer[pos] in " \t\r\n":
                pos += 1
            if expect_open:
                if pos >= len(buffer):
                    break
                if buffer[pos] != "[":
                    raise MemoryDatasetError(
                        CODE_SCHEMA_INVALID, "json array must start with '['"
                    )
                pos += 1
                expect_open = False
                continue
            if pos >= len(buffer):
                break
            if buffer[pos] == "]":
                if pending_comma:
                    raise MemoryDatasetError(
                        CODE_SCHEMA_INVALID, "json array must not end with ','"
                    )
                pos += 1
                finished = True
                break
            if buffer[pos] == ",":
                # 元素之间的分隔符：吃掉它再解下一个元素（不能拿它去解 JSON）。
                if not seen_item:
                    raise MemoryDatasetError(
                        CODE_SCHEMA_INVALID, "json array must not start with ','"
                    )
                pos += 1
                pending_comma = True
                continue
            if len(buffer) - pos > max_scenario_bytes + CHUNK_BYTES:
                raise MemoryDatasetError(
                    CODE_TOO_LARGE, "a json array item exceeds the per-scenario size limit"
                )
            try:
                item, end = json_decoder.raw_decode(buffer, pos)
            except json.JSONDecodeError as exc:
                last_error = exc
                if _looks_incomplete(exc, buffer, pos):
                    break  # 数据可能还没到齐，等下一块
                raise MemoryDatasetError(
                    CODE_SCHEMA_INVALID,
                    f"json array item is invalid: {exc.msg} (column {exc.colno})",
                ) from exc
            if end - pos > max_scenario_bytes:
                raise MemoryDatasetError(
                    CODE_TOO_LARGE, "a json array item exceeds the per-scenario size limit"
                )
            pos = end
            if not isinstance(item, dict):
                raise MemoryDatasetError(
                    CODE_SCHEMA_INVALID, "a json array item is not a JSON object"
                )
            seen_item = True
            pending_comma = False
            last_error = None
            yield item
    try:
        buffer += decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise MemoryDatasetError(CODE_SCHEMA_INVALID, "invalid utf-8 at end of file") from exc
    if finished:
        if (tail + buffer[pos:]).strip():
            raise MemoryDatasetError(
                CODE_SCHEMA_INVALID, "unexpected content after the json array"
            )
        return
    if last_error is not None:
        raise MemoryDatasetError(
            CODE_SCHEMA_INVALID, f"json array is incomplete: {last_error.msg}"
        )
    raise MemoryDatasetError(CODE_SCHEMA_INVALID, "json array is incomplete")


def read_scenario_stream(
    stream: BinaryIO,
    format: Literal["json", "jsonl"],
    *,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_scenario_bytes: int = MAX_SCENARIO_BYTES,
) -> Iterator[dict[str, Any]]:
    """增量解码一个集合文件，逐条产出场景 dict。

    - ``jsonl``：逐行解析，空白行跳过，畸形行报 ``MEMORY_SCHEMA_INVALID``；
    - ``json``：用标准库 ``raw_decode`` 逐条消费，缓冲只保留当前未消费的部分。

    两种格式都要求文件尾完整：尾部被截断时明确报错，不静默丢弃已读内容。
    """
    if format == "jsonl":
        yield from _read_jsonl(
            stream, max_file_bytes=max_file_bytes, max_scenario_bytes=max_scenario_bytes
        )
        return
    if format == "json":
        yield from _read_json_array(
            stream, max_file_bytes=max_file_bytes, max_scenario_bytes=max_scenario_bytes
        )
        return
    raise MemoryDatasetError(CODE_SCHEMA_INVALID, f"unsupported dataset format: {format}")


# ---------------------------------------------------------------- 校验与诊断


def _scenario_diagnostics(index: int, row: Mapping[str, Any]) -> dict[str, Any]:
    """把 pydantic 的校验错误压成一行诊断（不回显原始正文）。"""
    scenario_id = row.get("scenario_id")
    return {
        "code": CODE_SCHEMA_INVALID,
        "index": index,
        "scenario_id": scenario_id if isinstance(scenario_id, str) else "",
        "message": "",
    }


def _validate_scenarios(
    rows: Sequence[Mapping[str, Any]], *, max_messages_per_scenario: int
) -> tuple[list[MemoryScenario], list[dict[str, Any]]]:
    """逐条校验场景；任一条不合法都进诊断，整批都不发布。"""
    scenarios: list[MemoryScenario] = []
    errors: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        try:
            scenario = MemoryScenario.model_validate(row)
        except Exception as exc:  # noqa: BLE001 - pydantic 的多种校验错误统一收敛
            detail = _scenario_diagnostics(index, row)
            detail["message"] = redact_paths(_first_error_line(exc))
            errors.append(detail)
            continue
        message_count = sum(
            len(event.messages) for event in scenario.events if event.type == "session"
        )
        if message_count > max_messages_per_scenario:
            detail = _scenario_diagnostics(index, row)
            detail["code"] = CODE_TOO_LARGE
            detail["message"] = "scenario exceeds the per-scenario message limit"
            errors.append(detail)
            continue
        scenarios.append(scenario)
    return scenarios, errors


def _first_error_line(exc: Exception) -> str:
    """取校验错误的第一行：足够定位，且不把整份正文塞进诊断。"""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            items = errors()
        except Exception:  # noqa: BLE001 - 防御性：自定义异常可能没有 errors()
            items = []
        if items:
            first = items[0]
            location = ".".join(str(part) for part in first.get("loc", ()))
            message = str(first.get("msg", "invalid"))
            return f"{location}: {message}" if location else message
    return str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__


def _existing_split_index(
    database: EvaluationDatabase, suite_id: str, dataset_version: str
) -> tuple[dict[str, str], dict[str, str]]:
    """取同 suite 既有修订里的 (template_family → split, 人物 → split)。

    同一 ``dataset_version`` 的既有修订被排除：重导同一版本是幂等操作，
    不能把自己当成串题。
    """
    families: dict[str, str] = {}
    subjects: dict[str, str] = {}
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT s.template_family AS family, s.split AS split, s.execution_json AS body"
            " FROM memory_scenarios s"
            " JOIN memory_dataset_revisions r ON r.id = s.dataset_revision_id"
            " WHERE r.suite_id = ? AND r.dataset_version <> ?",
            (str(suite_id), str(dataset_version)),
        ).fetchall()
    for row in rows:
        families.setdefault(str(row["family"]), str(row["split"]))
        try:
            execution = json.loads(row["body"])
        except (TypeError, ValueError):
            continue
        scope = execution.get("scope") if isinstance(execution, dict) else None
        subject = scope.get("subject_id") if isinstance(scope, dict) else None
        if isinstance(subject, str):
            subjects.setdefault(subject, str(row["split"]))
    return families, subjects


def _split_leaks(
    scenarios: Sequence[MemoryScenario],
    existing_families: Mapping[str, str],
    existing_subjects: Mapping[str, str],
) -> list[dict[str, Any]]:
    """找出跨 split 复用的模板族/人物。

    契约 §1：相同人物/对话/事件模板不能横跨 split——否则调参集会串题。
    """
    leaks: list[dict[str, Any]] = []
    families: dict[str, str] = dict(existing_families)
    subjects: dict[str, str] = dict(existing_subjects)
    for scenario in scenarios:
        for kind, table, key in (
            ("template_family", families, scenario.template_family),
            ("subject", subjects, scenario.scope.subject_id),
        ):
            seen = table.setdefault(key, scenario.split)
            if seen != scenario.split:
                leaks.append(
                    {
                        "code": CODE_SPLIT_LEAK,
                        "scenario_id": scenario.scenario_id,
                        "message": (
                            f"{kind} {key} is used by split {seen} and {scenario.split}"
                        ),
                    }
                )
    return leaks


#: 必须**直接抛出**的错误码：限额类与跨 split 隔离类（契约 §1/§6）。
#: 这两类不是"某条场景不合法"，而是整批上传被拒：既不进入 ready，也不留半成品修订。
HARD_ERROR_CODES = (CODE_TOO_LARGE, CODE_SPLIT_LEAK)


def _hard_failure(errors: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """挑出必须抛出的错误；其余错误走 ``invalid`` 作业 + 诊断分页。"""
    for error in errors:
        if str(error.get("code", "")) in HARD_ERROR_CODES:
            return error
    return None


# ---------------------------------------------------------------- 导入


def _record_invalid_import(
    database: EvaluationDatabase,
    *,
    suite_id: str,
    source: MemoryDatasetSource,
    observed_count: int,
    errors: Sequence[Mapping[str, Any]],
) -> str:
    """把一次无效导入记成 ``invalid`` 作业并留下诊断摘要。

    ``MemoryRepository.import_revision`` 只在成功路径写作业行（失败时整个事务回滚），
    无效导入需要可查的诊断记录，因此这里按同一张表写一行作业记录。
    """
    job_id = str(uuid.uuid4())
    now = _now_iso()
    with database.connect(immediate=True) as conn:
        if conn.execute("SELECT 1 FROM suites WHERE id = ?", (str(suite_id),)).fetchone() is None:
            raise MemoryRecordNotFound(f"SUITE_NOT_FOUND {suite_id}")
        conn.execute(
            "INSERT INTO memory_import_jobs (id, suite_id, status, expected_count,"
            " observed_count, dataset_revision_id, source_json, errors_json,"
            " created_at, updated_at) VALUES (?, ?, 'invalid', NULL, ?, NULL, ?, ?, ?, ?)",
            (
                job_id,
                str(suite_id),
                int(observed_count),
                _dump(source.model_dump(mode="json")),
                _dump([dict(item) for item in errors]),
                now,
                now,
            ),
        )
    return job_id


def import_dataset(
    database: EvaluationDatabase,
    *,
    suite_id: str,
    dataset_version: str,
    source: Mapping[str, Any],
    stream: BinaryIO,
    format: Literal["json", "jsonl"],
    expected_sha256: str | None = None,
    max_scenarios: int = MAX_SCENARIOS,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_scenario_bytes: int = MAX_SCENARIO_BYTES,
    max_messages_per_scenario: int = MAX_MESSAGES_PER_SCENARIO,
) -> dict[str, Any]:
    """导入一个集合修订：staging → 校验 → 同一事务内发布 ready。

    - 任一条场景不合法 → 整批不进 ready，作业记 ``invalid`` 并返回诊断；
    - 超限额（文件/场景/条数）或跨 split 复用模板族/人物 → 直接抛
      ``MEMORY_IMPORT_TOO_LARGE`` / ``MEMORY_SPLIT_LEAK``，整批被拒；
    - 同 ``dataset_version`` 且内容摘要一致 → 返回原修订（幂等）；
    - 摘要不一致 → ``REVISION_CONFLICT``，不静默覆盖；
    - 错误消息只给错误码与位置，**不回显本机路径**。
    """
    try:
        parsed_source = MemoryDatasetSource.model_validate(dict(source))
    except Exception as exc:  # noqa: BLE001 - 统一收敛为带码错误
        raise MemoryDatasetError(
            CODE_SCHEMA_INVALID, redact_paths(_first_error_line(exc))
        ) from exc

    reader = _HashingReader(stream)
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(
        read_scenario_stream(
            reader,
            format,
            max_file_bytes=max_file_bytes,
            max_scenario_bytes=max_scenario_bytes,
        )
    ):
        if index >= max_scenarios:
            raise MemoryDatasetError(
                CODE_TOO_LARGE, f"a dataset may contain at most {max_scenarios} scenarios"
            )
        rows.append(row)

    if expected_sha256 is not None and reader.hexdigest != expected_sha256:
        raise MemoryDatasetError(
            CODE_SOURCE_HASH_MISMATCH, "uploaded content does not match the declared hash"
        )

    scenarios, errors = _validate_scenarios(
        rows, max_messages_per_scenario=max_messages_per_scenario
    )
    existing_families, existing_subjects = _existing_split_index(
        database, suite_id, dataset_version
    )
    errors.extend(_split_leaks(scenarios, existing_families, existing_subjects))
    if errors:
        hard = _hard_failure(errors)
        if hard is not None:
            # 限额（413）与跨 split 隔离是**硬拒绝**：不写 invalid 作业，也不留半成品。
            raise MemoryDatasetError(
                str(hard.get("code", CODE_SCHEMA_INVALID)),
                redact_paths(str(hard.get("message", ""))),
                diagnostics=errors,
            )
        job_id = _record_invalid_import(
            database,
            suite_id=suite_id,
            source=parsed_source,
            observed_count=len(rows),
            errors=errors,
        )
        return {
            "status": "invalid",
            "dataset_revision": None,
            "dataset_revision_id": None,
            "import_job_id": job_id,
            "scenario_count": 0,
            "split_counts": {},
            "errors": errors,
        }

    repository = MemoryRepository(database)
    try:
        result = repository.import_revision(
            {
                "suite_id": str(suite_id),
                "dataset_version": str(dataset_version),
                "source": parsed_source.model_dump(mode="json"),
                "scenarios": [scenario.model_dump(mode="json") for scenario in scenarios],
            }
        )
    except MemoryRevisionConflict as exc:
        raise MemoryDatasetError(CODE_REVISION_CONFLICT, redact_paths(str(exc))) from exc
    except MemoryRecordNotFound as exc:
        raise MemoryRecordNotFound(redact_paths(str(exc))) from exc

    revision = result["dataset_revision"]
    return {
        "status": "ready",
        "dataset_revision": revision,
        "dataset_revision_id": str(revision["id"]),
        "import_job_id": result.get("import_job_id"),
        "scenario_count": int(result["scenario_count"]),
        "split_counts": dict(revision.get("split_counts") or {}),
        "errors": [],
    }


# ---------------------------------------------------------------- CAS 编辑


def _load_revision_scenarios(
    database: EvaluationDatabase, dataset_revision_id: str
) -> list[dict[str, Any]]:
    """一次查询读出某个修订的全部场景（避免逐场景开连接）。

    ``execution_json`` 与 ``gold_json`` 分列存储，这里按 WU-01 的模型重新拼回完整场景。
    """
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT scenario_id, revision, execution_json, gold_json FROM memory_scenarios"
            " WHERE dataset_revision_id = ? ORDER BY scenario_id, revision",
            (str(dataset_revision_id),),
        ).fetchall()
    scenarios: list[dict[str, Any]] = []
    for row in rows:
        execution = json.loads(row["execution_json"])
        gold = json.loads(row["gold_json"])
        scenarios.append({**execution, "gold": gold})
    return scenarios


def edit_dataset_revision(
    database: EvaluationDatabase,
    *,
    dataset_revision_id: str,
    base_dataset_version: str,
    new_dataset_version: str,
    changes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """用 CAS 编辑集合：生成**新修订**，原 ready 修订及其 gold 保持不变。

    CAS 条件有两个，缺一不可：

    - ``base_dataset_version`` 必须等于当前修订的版本号（别人改过就拒）；
    - 每条改动的场景 revision 必须正好是原修订 +1（不允许跳号或覆盖既有修订）。
    """
    repository = MemoryRepository(database)
    base = repository.get_dataset_revision(str(dataset_revision_id))
    if str(base["dataset_version"]) != str(base_dataset_version):
        raise MemoryDatasetError(
            CODE_REVISION_CONFLICT,
            "the base dataset revision changed since it was read",
        )
    if str(base["status"]) != "ready":
        raise MemoryDatasetError(CODE_NOT_READY, "only a ready revision can be edited")

    base_rows = _load_revision_scenarios(database, dataset_revision_id)
    base_by_id: dict[str, dict[str, Any]] = {}
    for row in base_rows:
        base_by_id[str(row["scenario_id"])] = row

    updated: dict[str, dict[str, Any]] = {}
    for change in changes:
        scenario_id = str(change.get("scenario_id", ""))
        current = base_by_id.get(scenario_id)
        if current is None:
            raise MemoryDatasetError(
                CODE_REVISION_CONFLICT, f"scenario {scenario_id} is not in the base revision"
            )
        if int(change.get("revision", 0)) != int(current["revision"]) + 1:
            raise MemoryDatasetError(
                CODE_REVISION_CONFLICT,
                f"scenario {scenario_id} must be revised to {int(current['revision']) + 1}",
            )
        updated[scenario_id] = dict(change)

    merged: list[dict[str, Any]] = []
    for row in base_rows:
        merged.append(updated.get(str(row["scenario_id"]), row))

    payload_lines = "".join(
        _dump(row) + "\n" for row in sorted(merged, key=lambda item: str(item["scenario_id"]))
    )
    source = dict(base["source"])
    source["revision"] = str(new_dataset_version)
    source["sha256"] = hashlib.sha256(payload_lines.encode("utf-8")).hexdigest()
    source["original_ids"] = sorted(str(row["scenario_id"]) for row in merged)

    try:
        result = repository.import_revision(
            {
                "suite_id": str(base["suite_id"]),
                "dataset_version": str(new_dataset_version),
                "source": source,
                "scenarios": merged,
            }
        )
    except MemoryRevisionConflict as exc:
        raise MemoryDatasetError(CODE_REVISION_CONFLICT, redact_paths(str(exc))) from exc
    revision = result["dataset_revision"]
    return {
        "status": "ready",
        "dataset_revision": revision,
        "dataset_revision_id": str(revision["id"]),
        "base_dataset_revision_id": str(dataset_revision_id),
        "scenario_count": int(result["scenario_count"]),
    }


# ---------------------------------------------------------------- 导出


def export_dataset_revision(
    database: EvaluationDatabase,
    dataset_revision_id: str,
    *,
    workspace_root: Path,
    artifact_repository: Any | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    """把一个修订导出成 JSONL，并复用既有 artifact 登记机制。

    导出文件落在调用方给的 ``workspace_root`` 下；传入 ``artifact_repository`` 与
    ``attempt_id`` 时，额外按既有 ``EvaluationRepository.append_artifact`` 的口径登记
    一行，让 ``GET /artifacts/{id}`` 能直接下载——**不另建一套产物通道**。
    """
    rows = _load_revision_scenarios(database, dataset_revision_id)
    if not rows:
        raise MemoryRecordNotFound(f"MEMORY_DATASET_NOT_FOUND {dataset_revision_id}")
    body = "".join(
        _dump(row) + "\n" for row in sorted(rows, key=lambda item: str(item["scenario_id"]))
    )
    relative = f"memory-datasets/{dataset_revision_id}.jsonl"
    target = Path(workspace_root) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = body.encode("utf-8")
    # 写**字节**而不是文本：文本模式在 Windows 上会把 "\n" 翻成 "\r\n"，
    # 落盘字节与登记的摘要会对不上。sha256 一律对实际落盘字节求。
    target.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    size = len(payload)

    artifact_id: str | None = None
    if artifact_repository is not None and attempt_id is not None:
        registered = artifact_repository.append_artifact(
            str(attempt_id),
            relative_path=relative,
            media_type="application/x-ndjson",
            size_bytes=size,
            sha256=digest,
        )
        artifact_id = str(registered["id"])
    return {
        "path": str(target),
        "relative_path": relative,
        "media_type": "application/x-ndjson",
        "size_bytes": size,
        "sha256": digest,
        "artifact_id": artifact_id,
        "scenario_count": len(rows),
    }


# ---------------------------------------------------------------- 临时上传


def _unlink_quietly(path: Path) -> None:
    """删除临时文件：清理失败不应盖掉真正的错误。"""
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - 清理失败不应盖掉真正的错误
        pass


@contextmanager
def temporary_upload(
    staging_dir: Path, stream: BinaryIO, *, max_bytes: int = MAX_FILE_BYTES
) -> Iterator[Path]:
    """受控临时上传：临时文件只评估服务可读，退出时**一定**清理。

    落盘失败（磁盘配额不足 ``OSError``）或超过大小上限时：

    1. 立刻删掉半截文件——调用方不能把半成品当成完整上传；
    2. 把错误记下来，在 ``with`` 块结束时抛出（``MEMORY_IMPORT_DISK_FULL`` /
       ``MEMORY_IMPORT_TOO_LARGE``），块内的异常不会盖掉根因；
    3. 临时文件在退出时一定被删除（取消/异常/成功三条路径都走清理）。

    错误正文不外泄底层异常细节，也**不回显本机路径**。
    """
    staging = Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / f"{uuid.uuid4().hex}.upload"
    failure: MemoryDatasetError | None = None
    try:
        try:
            written = 0
            with path.open("wb") as handle:
                while True:
                    chunk = stream.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise MemoryDatasetError(
                            CODE_TOO_LARGE, "upload exceeds the per-file size limit"
                        )
                    handle.write(chunk)
        except MemoryDatasetError as exc:
            failure = exc
        except OSError as exc:
            failure = MemoryDatasetError(
                CODE_DISK_FULL, redact_paths(f"cannot persist the upload: {exc}")
            )
            failure.__cause__ = exc
        if failure is not None:
            _unlink_quietly(path)
        yield path
    finally:
        _unlink_quietly(path)
        if failure is not None:
            raise failure


__all__ = [
    "CHUNK_BYTES",
    "CODE_DISK_FULL",
    "CODE_NOT_READY",
    "CODE_REVISION_CONFLICT",
    "CODE_SCHEMA_INVALID",
    "CODE_SOURCE_HASH_MISMATCH",
    "CODE_SPLIT_LEAK",
    "CODE_TOO_LARGE",
    "HARD_ERROR_CODES",
    "MAX_FILE_BYTES",
    "MAX_MESSAGES_PER_SCENARIO",
    "MAX_SCENARIOS",
    "MAX_SCENARIO_BYTES",
    "MemoryDatasetError",
    "edit_dataset_revision",
    "export_dataset_revision",
    "import_dataset",
    "read_scenario_stream",
    "redact_paths",
    "temporary_upload",
]
