"""记忆专项 HTTP 层（契约 §6，plan Task 9 / WU-09）。

职责边界只有一条：**把契约 §6 的路由矩阵接进现有应用**。

- 路由表由 :func:`memory_route_defs` 返回，交给 ``server._routes`` 统一挂载；因此
  guard（Host/Origin → 会话 → CSRF → 限流）与未匹配方法的 405 兜底自动覆盖这里，
  本模块不复制任何准入逻辑。
- 错误响应只经 ``server._fail`` / ``server._error`` 产出：错误码 → 状态码的唯一映射
  点在 :mod:`nanobot.evaluation.errors`，这里不自己拼 ``{"error": ...}``。
- 请求体在**边界解析一次**（严格 Pydantic 模型），服务层只消费模型；读接口的游标是
  HTTP 概念，服务层只给稳定排序的完整序列。
- 上传走流式 multipart：文件分块落临时文件，**不提高全站默认正文上限**；导入本身
  只做校验与发布（不跑模型），202 之后用 ``GET /memory/imports/{id}`` 读状态。
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from aiohttp import web
from pydantic import ValidationError

from nanobot.evaluation.memory.datasets import CHUNK_BYTES, MAX_FILE_BYTES, MemoryDatasetError
from nanobot.evaluation.schemas import (
    EmptyBody,
    MemoryComparisonCreate,
    MemoryDatasetImportMetadata,
    MemoryExperimentCreate,
    MemoryRevisionCreate,
)
from nanobot.evaluation.server import (
    API_PREFIX,
    _fail,
    _json_body,
    _scope,
    context_of,
)

#: 单次上传的字节上限（契约 §6：单文件 2GiB）。测试可把它调小以覆盖 413 分支；
#: 全站默认正文上限（``server.MAX_BODY_BYTES``）保持不变。
MAX_UPLOAD_BYTES = MAX_FILE_BYTES

#: multipart 里 ``metadata`` 文本字段的上限：元数据不该和数据集一样大。
MAX_METADATA_BYTES = 64 * 1024

#: 契约 §1：split 闭集。
_SPLITS = frozenset({"development", "calibration", "regression"})

#: 比较配对的状态闭集（过滤当前页用，不改全量统计）。
_PAIR_STATUSES = frozenset({"improved", "regressed", "unchanged", "incomparable"})


# --------------------------------------------------------------------------- 分页


def _limit(request: web.Request, *, default: int = 50, maximum: int = 200) -> int:
    """契约 §6：limit 默认 50、1..200；越界或非整数都是 422。"""
    raw = request.query.get("limit")
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        _fail("VALIDATION_ERROR", "limit 必须是整数")
    if not 1 <= value <= maximum:
        _fail("VALIDATION_ERROR", "limit 超出范围")
    return value


def _int_query(
    request: web.Request, name: str, *, default: int | None, minimum: int, maximum: int
) -> int | None:
    raw = request.query.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        _fail("VALIDATION_ERROR", f"{name} 必须是整数")
    if not minimum <= value <= maximum:
        _fail("VALIDATION_ERROR", f"{name} 超出范围")
    return value


def _encode_cursor(scope: str, filters: Mapping[str, Any], position: Sequence[str]) -> str:
    """服务端不透明游标：同时绑定端点作用域与筛选指纹。"""
    payload = json.dumps(
        {"s": scope, "f": dict(filters), "p": list(position)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(
    token: str, scope: str, filters: Mapping[str, Any]
) -> list[str]:
    """解游标；篡改、跨端点复用、换筛选条件一律 422。"""
    padded = token + "=" * (-len(token) % 4)
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        )
    except (ValueError, UnicodeError) as exc:
        _fail("VALIDATION_ERROR", "after_key 不是有效游标", exc)
    if (
        not isinstance(payload, dict)
        or payload.get("s") != scope
        or payload.get("f") != dict(filters)
    ):
        _fail("VALIDATION_ERROR", "游标与筛选条件不匹配")
    position = payload.get("p")
    if not isinstance(position, list) or not all(isinstance(item, str) for item in position):
        _fail("VALIDATION_ERROR", "after_key 不是有效游标")
    return [str(item) for item in position]


def _page(
    request: web.Request,
    items: Sequence[Any],
    *,
    key_of: Callable[[Any], Sequence[str]],
    scope: str,
    filters: Mapping[str, Any],
    limit: int,
) -> dict[str, Any]:
    """按 ``(id/seq 稳定键)`` 取下一页；``next_after_key`` 为空表示到底。"""
    after_key = request.query.get("after_key")
    position = _decode_cursor(after_key, scope, filters) if after_key else None
    remaining = [
        item
        for item in items
        if position is None or tuple(key_of(item)) > tuple(position)
    ]
    page = list(remaining[:limit])
    next_key = (
        _encode_cursor(scope, filters, key_of(page[-1]))
        if len(remaining) > limit
        else None
    )
    return {"data": page, "next_after_key": next_key}


def _idempotency_key(request: web.Request) -> str:
    """写请求的幂等键：缺失即 422（不能静默生成一个，那样重放就没有意义）。"""
    key = request.headers.get("Idempotency-Key")
    if not key:
        _fail("VALIDATION_ERROR", "缺少 Idempotency-Key")
    if len(key) > 200:
        _fail("VALIDATION_ERROR", "Idempotency-Key 过长")
    return key


def _uuid_query(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        _fail("VALIDATION_ERROR", f"{name} 必须是 UUID")
    return None


# --------------------------------------------------------------------------- 上传


class _SpooledUpload:
    """一次上传的落盘结果：退出时**一定**删除临时文件。"""

    def __init__(self, metadata: MemoryDatasetImportMetadata, path: Path) -> None:
        self.metadata = metadata
        self.path = path

    def close(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - 清理失败不应盖掉真正的错误
            pass


async def _read_metadata(part: Any) -> MemoryDatasetImportMetadata:
    raw = b""
    while chunk := await part.read_chunk(CHUNK_BYTES):
        raw += chunk
        if len(raw) > MAX_METADATA_BYTES:
            _fail("MEMORY_SCHEMA_INVALID", "metadata 超过 64KiB")
    if not raw:
        _fail("MEMORY_SCHEMA_INVALID", "缺少 metadata")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        _fail("MEMORY_SCHEMA_INVALID", "metadata 不是合法 JSON", exc)
    if not isinstance(payload, dict):
        _fail("MEMORY_SCHEMA_INVALID", "metadata 必须是 JSON 对象")
    try:
        return MemoryDatasetImportMetadata.model_validate(payload)
    except ValidationError as exc:
        fields = sorted(
            ".".join(str(part) for part in item["loc"]) for item in exc.errors()
        )
        _fail("MEMORY_SCHEMA_INVALID", ", ".join(fields) or "metadata", exc)


async def _spool_file(part: Any, staging: Path) -> Path:
    """把 file 分块落到评估私有的临时目录；超限立即中止并删掉半截文件。"""
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / f"{uuid.uuid4().hex}.upload"
    try:
        written = 0
        with path.open("wb") as handle:
            while chunk := await part.read_chunk(CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    _fail("MEMORY_IMPORT_TOO_LARGE", "上传超过单文件上限")
                handle.write(chunk)
        if written == 0:
            _fail("MEMORY_SCHEMA_INVALID", "缺少 file 内容")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


async def _read_import_body(request: web.Request, staging: Path) -> _SpooledUpload:
    reader = await request.multipart()
    metadata: MemoryDatasetImportMetadata | None = None
    path: Path | None = None
    try:
        while part := await reader.next():
            if part.name == "metadata":
                metadata = await _read_metadata(part)
            elif part.name == "file":
                path = await _spool_file(part, staging)
            else:
                # 未知字段直接读空，避免连接因未消费的分片挂住。
                while await part.read_chunk(CHUNK_BYTES):
                    continue
    except BaseException:
        if path is not None:
            path.unlink(missing_ok=True)
        raise
    if metadata is None:
        _fail("MEMORY_SCHEMA_INVALID", "缺少 metadata")
    if path is None:
        _fail("MEMORY_SCHEMA_INVALID", "缺少 file 字段")
    return _SpooledUpload(metadata, path)


# --------------------------------------------------------------------------- 集合


async def list_memory_datasets(request: web.Request) -> web.Response:
    """``GET /memory/datasets``：修订列表（不带场景正文与 gold）。"""
    context = context_of(request)
    split = request.query.get("split")
    if split is not None and split not in _SPLITS:
        _fail("VALIDATION_ERROR", "split 取值非法")
    limit = _limit(request)
    filters = {"split": split}
    revisions = context.service.memory_dataset_list(split=split)
    return web.json_response(
        _page(
            request,
            revisions,
            key_of=lambda item: [str(item.get("created_at") or ""), str(item["id"])],
            scope="memory.datasets",
            filters=filters,
            limit=limit,
        )
    )


async def import_memory_dataset(request: web.Request) -> web.Response:
    """``POST /memory/datasets/import``：流式 multipart → 版本化导入器 → 202。"""
    context = context_of(request)
    key = _idempotency_key(request)
    if not (request.content_type or "").startswith("multipart/"):
        _fail("UNSUPPORTED_MEDIA_TYPE", "需要 multipart/form-data")
    staging = Path(context.service.data_dir) / "memory-imports"
    upload = await _read_import_body(request, staging)
    try:
        result = context.service.memory_import_dataset(
            upload.metadata,
            file_path=upload.path,
            idempotency_key=key,
            scope=_scope(request),
        )
    except MemoryDatasetError as exc:
        # 限额/摘要不一致等带码错误：状态码仍由 errors 的白名单决定。
        _fail(exc.code, exc.message, exc)
    finally:
        upload.close()
    return web.json_response(
        {
            "import_id": result["import_id"],
            "status": result["status"],
            "dataset_revision_id": result["dataset_revision_id"],
            "dataset_version": result["dataset_version"],
            "scenario_count": result["scenario_count"],
            "errors": result["errors"],
        },
        status=202,
        headers={"Location": f"{API_PREFIX}/memory/imports/{result['import_id']}"},
    )


async def get_memory_import(request: web.Request) -> web.Response:
    """``GET /memory/imports/{id}``：状态、计数与错误分页。"""
    context = context_of(request)
    limit = _limit(request)
    detail = context.service.memory_import_read(request.match_info["id"])
    errors = [
        {**dict(item), "_position": f"{index:08d}"}
        for index, item in enumerate(detail["errors"])
    ]
    page = _page(
        request,
        errors,
        key_of=lambda item: [str(item["_position"])],
        scope="memory.imports.errors",
        filters={"import_id": detail["id"]},
        limit=limit,
    )
    data = [
        {key: value for key, value in item.items() if key != "_position"}
        for item in page["data"]
    ]
    return web.json_response(
        {
            "id": detail["id"],
            "suite_id": detail["suite_id"],
            "status": detail["status"],
            "counts": detail["counts"],
            "errors": data,
            "next_after_key": page["next_after_key"],
            "dataset_revision_id": detail["dataset_revision_id"],
            "dataset_version": detail["dataset_version"],
            "created_at": detail["created_at"],
            "updated_at": detail["updated_at"],
        }
    )


async def list_memory_scenarios(request: web.Request) -> web.Response:
    """``GET /memory/datasets/{id}/scenarios``：场景摘要分页。"""
    context = context_of(request)
    limit = _limit(request)
    dataset_revision_id = request.match_info["id"]
    summaries = context.service.memory_scenario_list(dataset_revision_id)
    return web.json_response(
        _page(
            request,
            summaries,
            key_of=lambda item: [str(item["scenario_id"]), f"{int(item['revision']):08d}"],
            scope="memory.scenarios",
            filters={"dataset_revision_id": dataset_revision_id},
            limit=limit,
        )
    )


async def get_memory_scenario(request: web.Request) -> web.Response:
    """``GET /memory/datasets/{id}/scenarios/{scenario_id}``：``{execution,gold,revision}``。

    gold 只在评估授权的管理面可见：本路由与其它路由一样经过 guard，未登录一律 401。
    """
    context = context_of(request)
    revision = _int_query(request, "revision", default=None, minimum=1, maximum=10**9)
    detail = context.service.memory_scenario_detail(
        request.match_info["id"],
        request.match_info["scenario_id"],
        revision=revision,
    )
    return web.json_response(detail)


async def create_memory_revision(request: web.Request) -> web.Response:
    """``POST /memory/datasets/{id}/revisions``：CAS 编辑 → 新修订（原修订不变）。"""
    context = context_of(request)
    body = await _json_body(request, MemoryRevisionCreate)
    revision = context.service.memory_edit_dataset(
        request.match_info["id"],
        base_revision=body.base_revision,
        changes=body.changes,
    )
    return web.json_response(revision, status=201)


async def memory_capabilities(request: web.Request) -> web.Response:
    """``GET /memory/capabilities``：轨道支持、设置白名单、候选 profile 与缺失能力。"""
    return web.json_response(context_of(request).service.memory_capabilities_view())


# --------------------------------------------------------------------------- 实验


async def create_memory_experiment(request: web.Request) -> web.Response:
    """``POST /memory/experiments``：入队即 202，绝不在请求里跑 trial。"""
    context = context_of(request)
    key = _idempotency_key(request)
    body = await _json_body(request, MemoryExperimentCreate)
    result = context.service.memory_create_experiment(
        body, idempotency_key=key, scope=_scope(request)
    )
    return web.json_response(
        result,
        status=202,
        headers={"Location": f"{API_PREFIX}/memory/experiments/{result['id']}"},
    )


async def list_memory_experiments(request: web.Request) -> web.Response:
    """``GET /memory/experiments``：实验摘要分页（可按键数据集修订过滤）。"""
    context = context_of(request)
    limit = _limit(request)
    dataset_revision_id = _uuid_query(
        request.query.get("dataset_revision_id"), "dataset_revision_id"
    )
    experiments = context.service.memory_experiment_list(
        dataset_revision_id=dataset_revision_id
    )
    return web.json_response(
        _page(
            request,
            experiments,
            key_of=lambda item: [str(item.get("created_at") or ""), str(item["id"])],
            scope="memory.experiments",
            filters={"dataset_revision_id": dataset_revision_id},
            limit=limit,
        )
    )


async def get_memory_experiment(request: web.Request) -> web.Response:
    """``GET /memory/experiments/{id}``：冻结清单、预算、覆盖与进度。"""
    return web.json_response(
        context_of(request).service.memory_experiment_read(request.match_info["id"])
    )


async def cancel_memory_experiment(request: web.Request) -> web.Response:
    """``POST /memory/experiments/{id}/cancel``：终态重复取消 200，其余 202。"""
    context = context_of(request)
    await _json_body(request, EmptyBody)
    detail, changed = await context.service.memory_cancel_experiment(
        request.match_info["id"], scheduler=context.scheduler
    )
    return web.json_response(detail, status=202 if changed else 200)


async def get_memory_experiment_metrics(request: web.Request) -> web.Response:
    """``GET /memory/experiments/{id}/metrics``：全实验范围的指标读模型。"""
    return web.json_response(
        context_of(request).service.memory_experiment_metrics(request.match_info["id"])
    )


# --------------------------------------------------------------------------- attempt


async def list_memory_attempt_probes(request: web.Request) -> web.Response:
    """``GET /memory/attempts/{id}/probes``：逐 probe 摘要（不含私有 gold 路径）。"""
    context = context_of(request)
    limit = _limit(request)
    attempt_id = request.match_info["id"]
    probes = context.service.memory_attempt_probes(attempt_id)
    return web.json_response(
        _page(
            request,
            probes,
            key_of=lambda item: [
                str(item.get("scenario_id") or ""),
                f"{int(item.get('trial_index') or 0):08d}",
                str(item.get("probe_id") or ""),
            ],
            scope="memory.probes",
            filters={"attempt_id": attempt_id},
            limit=limit,
        )
    )


async def list_memory_attempt_events(request: web.Request) -> web.Response:
    """``GET /memory/attempts/{id}/events``：``memory.event`` 投影（after_seq）。"""
    context = context_of(request)
    after_seq = _int_query(request, "after_seq", default=0, minimum=0, maximum=10**9)
    limit = _int_query(request, "limit", default=200, minimum=1, maximum=200)
    return web.json_response(
        context.service.memory_attempt_event_page(
            request.match_info["id"], after_seq=int(after_seq or 0), limit=int(limit or 200)
        )
    )


# --------------------------------------------------------------------------- 比较


async def create_memory_comparison(request: web.Request) -> web.Response:
    """``POST /memory/comparisons``：终态/元数据/变更白名单门禁 → 201 摘要。"""
    context = context_of(request)
    key = _idempotency_key(request)
    body = await _json_body(request, MemoryComparisonCreate)
    summary = context.service.memory_create_comparison(
        body, idempotency_key=key, scope=_scope(request)
    )
    return web.json_response(
        summary,
        status=201,
        headers={"Location": f"{API_PREFIX}/memory/comparisons/{summary['id']}"},
    )


async def get_memory_comparison(request: web.Request) -> web.Response:
    """``GET /memory/comparisons/{id}``：全量统计 + 当前页配对。"""
    context = context_of(request)
    limit = _limit(request)
    status = request.query.get("status")
    if status is not None and status not in _PAIR_STATUSES:
        _fail("VALIDATION_ERROR", "status 取值非法")
    # 分页与统计都由比较引擎的 ``page_comparison_pairs`` 统一给出：统计是全量，
    # 游标是稳定的 scenario_id，翻页不会缩小任何分母。
    detail = context.service.memory_comparison_read(
        request.match_info["id"],
        status=status,
        after_key=request.query.get("after_key"),
        limit=limit,
    )
    return web.json_response(detail)


async def get_memory_comparison_report(request: web.Request) -> web.Response:
    """``GET /memory/comparisons/{id}/report``：同一快照的自包含 HTML。"""
    body = context_of(request).service.memory_comparison_report(
        request.match_info["id"]
    )
    comparison_id = "".join(
        char
        for char in request.match_info["id"]
        if char.isalnum() or char in {".", "_", "-"}
    )
    return web.Response(
        text=body,
        content_type="text/html",
        charset="utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="memory-comparison-{comparison_id[:64] or "report"}.html"'
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


# --------------------------------------------------------------------------- 挂载


def memory_route_defs(prefix: str = API_PREFIX) -> list[web.RouteDef]:
    """契约 §6 的路由清单；由 ``server._routes`` 与既有路由合并后统一挂载。"""

    def r(method: str, path: str, handler: Any) -> web.RouteDef:
        return web.route(method, f"{prefix}{path}", handler)

    return [
        r("GET", "/memory/datasets", list_memory_datasets),
        r("POST", "/memory/datasets/import", import_memory_dataset),
        r("GET", "/memory/imports/{id}", get_memory_import),
        r("GET", "/memory/datasets/{id}/scenarios", list_memory_scenarios),
        r(
            "GET",
            "/memory/datasets/{id}/scenarios/{scenario_id}",
            get_memory_scenario,
        ),
        r("POST", "/memory/datasets/{id}/revisions", create_memory_revision),
        r("GET", "/memory/capabilities", memory_capabilities),
        r("POST", "/memory/experiments", create_memory_experiment),
        r("GET", "/memory/experiments", list_memory_experiments),
        r("GET", "/memory/experiments/{id}", get_memory_experiment),
        r("POST", "/memory/experiments/{id}/cancel", cancel_memory_experiment),
        r("GET", "/memory/experiments/{id}/metrics", get_memory_experiment_metrics),
        r("GET", "/memory/attempts/{id}/probes", list_memory_attempt_probes),
        r("GET", "/memory/attempts/{id}/events", list_memory_attempt_events),
        r("POST", "/memory/comparisons", create_memory_comparison),
        r("GET", "/memory/comparisons/{id}", get_memory_comparison),
        r("GET", "/memory/comparisons/{id}/report", get_memory_comparison_report),
    ]


__all__ = ["MAX_METADATA_BYTES", "MAX_UPLOAD_BYTES", "memory_route_defs"]
