"""T10 HTTP 层：路由、认证中间件与 aiohttp 应用装配。

契约 §3 的路由全部在这里。两条口径贯穿全文件：

1. **入队即返回 202**。创建运行只写库，真正执行由调度器接手；客户端断开
   不取消运行——否则用户关掉页面就会把评估弄丢。
2. **错误体只含白名单字段**（见 :mod:`nanobot.evaluation.errors`）。未登记的
   异常一律 500 且不回显原文。

生命周期用 ``cleanup_ctx``：退出时**逆序**等待回收——先停 HTTP，再停调度器，
最后放开服务独占锁。
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Any, NoReturn

from aiohttp import web
from pydantic import Field, StrictStr, ValidationError

from nanobot.evaluation.artifacts import ArtifactError, attachment_headers, resolve_artifact
from nanobot.evaluation.auth import SESSION_COOKIE, AuthConfig, AuthError, AuthManager
from nanobot.evaluation.coverage import summarize_coverage
from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.errors import code_from_exception, error_body, status_for
from nanobot.evaluation.reports import aggregate_run, normalize_attempt_for_metrics
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.scheduler import Scheduler, SchedulerConfig
from nanobot.evaluation.schemas import (
    CalibrationCreate,
    CaseWrite,
    ComparisonCreate,
    EmptyBody,
    GoldReview,
    ImportSuiteRequest,
    JsonModel,
    ManualReviewCreate,
    RerunCreate,
    RunCreate,
    SampleImportRequest,
    SuiteWrite,
)
from nanobot.evaluation.services import EvaluationService

API_PREFIX = "/api/eval/v1"

#: 契约 §3 的导入上限。
MAX_BODY_BYTES = 5 * 1024 * 1024

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class ServiceContext:
    """一次请求里到处都要用的三样东西：仓储、服务、认证。"""

    def __init__(
        self,
        *,
        repository: EvaluationRepository,
        service: EvaluationService,
        scheduler: Scheduler,
        auth: AuthManager,
    ) -> None:
        self.repository = repository
        self.service = service
        self.scheduler = scheduler
        self.auth = auth

    @property
    def runtime_root(self) -> Any:
        return self.service.runtime_root


def context_of(request: web.Request) -> ServiceContext:
    context = request.app["eval_context"]
    assert isinstance(context, ServiceContext)
    return context


# --------------------------------------------------------------------------- 中间件


def _cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Vary": "Origin",
    }


def _cors_middleware(
    allowed_origins: frozenset[str],
) -> Callable[[web.Request, Handler], Awaitable[web.StreamResponse]]:
    """精确 Origin 的 CORS：桌面 WebUI 与评估服务不同端口，但仍只信任显式白名单。

    预检必须在 ``guard`` 之前处理：浏览器不会在 OPTIONS 上带会话 Cookie，
    交给准入中间件只会得到 401，页面永远发不出写请求。
    """

    @web.middleware
    async def cors(request: web.Request, handler: Handler) -> web.StreamResponse:
        origin = request.headers.get("Origin")
        allowed = bool(origin and origin in allowed_origins)
        if request.method == "OPTIONS" and allowed:
            assert origin is not None
            return web.Response(
                status=204,
                headers={
                    **_cors_headers(origin),
                    "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
                    "Access-Control-Allow-Headers": (
                        request.headers.get("Access-Control-Request-Headers")
                        or "Content-Type, X-CSRF-Token, Idempotency-Key"
                    ),
                    "Access-Control-Max-Age": "600",
                },
            )
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            # guard 会把 HTTPException 原样抛出（aiohttp 协议层负责转响应），
            # 这条路径不经过下方的 response 分支；错误体也必须带 CORS 头，
            # 否则浏览器把 4xx/5xx 当网络错误，前端读不到契约错误 JSON。
            if allowed:
                assert origin is not None
                exc.headers.update(_cors_headers(origin))
            raise
        if allowed:
            assert origin is not None
            response.headers.update(_cors_headers(origin))
        return response

    return cors


@web.middleware
async def guard(request: web.Request, handler: Handler) -> web.StreamResponse:
    """准入：Host/Origin → 会话 → 写操作再加 CSRF 与限流。未登记异常一律 500 不回显。"""
    context = context_of(request)
    try:
        context.auth.check_host(request.headers.get("Host"))
        context.auth.check_origin(request.headers.get("Origin"))
        if request.path == f"{API_PREFIX}/health":
            # health 免登录，但 Host 准入照做：DNS rebinding 不因端点免认证就失效。
            return await handler(request)
        if _is_login(request):
            # 登录本身不能要求已登录：只做 Host/Origin 准入。
            return await handler(request)
        session = context.auth.session_for(_cookie(request, context.auth))
        if request.method not in SAFE_METHODS:
            context.auth.check_csrf(session, _csrf(request))
            context.auth.check_write_rate(session.user, request.path)
        request["session"] = session
        return await handler(request)
    except AuthError as exc:
        return _error(exc.code, exc.code)
    except web.HTTPException as exc:
        # 必须重新抛出：把异常实例当返回值会让 aiohttp 把它算成未处理错误（500）。
        raise _as_json_error(exc) from exc
    except Exception as exc:  # noqa: BLE001 - 兜底：内部错误不回显原文
        # 只用异常自己声明的 message：str(exc) 可能带 SQL、路径或别的 attempt 的正文。
        return _error(code_from_exception(exc), str(getattr(exc, "message", "") or ""))


def _is_login(request: web.Request) -> bool:
    return request.path == f"{API_PREFIX}/auth/login"


def _cookie(request: web.Request, auth: AuthManager) -> str | None:
    return request.cookies.get(auth.cookie_name) or request.cookies.get(SESSION_COOKIE)


def _csrf(request: web.Request) -> str | None:
    return request.headers.get("X-CSRF-Token")


def _error(code: str, message: str = "", status: int | None = None) -> web.Response:
    return web.json_response(
        error_body(code, message), status=status or status_for(code)
    )


#: aiohttp 的默认错误页是 text/plain，契约 §1 要求错误体恒为 JSON。按状态码换码。
_STATUS_TO_CODE = {
    400: "VALIDATION_ERROR",
    401: "AUTH_REQUIRED",
    403: "CSRF_INVALID",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
}

#: 状态码 → 异常类缓存。aiohttp 的具体类各有必填参数（413 要 max_size、405 要
#: method/allowed_methods），统一用 status_code 子类才不会漏参变成 500。
_STATUS_EXCEPTIONS: dict[int, type[web.HTTPException]] = {}


def _http_error(code: str, status: int, message: str = "") -> web.HTTPException:
    body = json.dumps(error_body(code, message), ensure_ascii=False)
    exc_class = _STATUS_EXCEPTIONS.get(status)
    if exc_class is None:
        exc_class = type(f"HTTPEval{status}", (web.HTTPException,), {"status_code": status})
        _STATUS_EXCEPTIONS[status] = exc_class
    return exc_class(text=body, content_type="application/json")


def _fail(
    code: str, message: str = "", cause: BaseException | None = None
) -> NoReturn:
    """抛出契约错误。用 ``text=`` 构造 aiohttp 异常会得到 text/plain 响应体，
    前端只能按「非 JSON」分支处理，所以这里统一带 JSON 体抛出。"""
    raise _http_error(code, status_for(code), message) from cause


def _as_json_error(exc: web.HTTPException) -> web.HTTPException:
    if (exc.content_type or "").startswith("application/json"):
        return exc
    code = _STATUS_TO_CODE.get(exc.status)
    if code is None:
        code = "INTERNAL_ERROR" if exc.status >= 500 else "VALIDATION_ERROR"
    return _http_error(code, exc.status)


def _method_table(routes: list[web.RouteDef]) -> tuple[tuple[re.Pattern[str], frozenset[str]], ...]:
    grouped: dict[str, set[str]] = {}
    for definition in routes:
        grouped.setdefault(definition.path, set()).add(definition.method)
    return tuple(
        (
            re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)) + "$"),
            frozenset(methods),
        )
        for path, methods in grouped.items()
    )


def _fallback_handler(routes: list[web.RouteDef]) -> Handler:
    """未匹配路由的兜底：路由层自带的 404/405 是纯文本页，不是契约错误体。"""
    table = _method_table(routes)

    async def _fallback(request: web.Request) -> web.Response:
        for pattern, methods in table:
            if pattern.match(request.path) and request.method not in methods:
                return _error("METHOD_NOT_ALLOWED", status=405)
        return _error("NOT_FOUND", status=404)

    return _fallback


async def _json_body(request: web.Request, model: type[Any]) -> Any:
    if request.content_length and request.content_length > MAX_BODY_BYTES:
        _fail("PAYLOAD_TOO_LARGE", f"请求体上限 {MAX_BODY_BYTES} 字节")
    raw = await request.read()
    if len(raw) > MAX_BODY_BYTES:
        _fail("PAYLOAD_TOO_LARGE", f"请求体上限 {MAX_BODY_BYTES} 字节")
    try:
        body = await request.json() if raw else {}
    except ValueError as exc:
        _fail("VALIDATION_ERROR", "请求体不是合法 JSON", exc)
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        # 只回字段级 loc，不回 pydantic 的模型堆栈。
        fields = sorted(
            ".".join(str(part) for part in item["loc"]) for item in exc.errors()
        )
        _fail("VALIDATION_ERROR", ", ".join(fields) or "body", exc)


def _scope(request: web.Request) -> str:
    session = request.get("session")
    return getattr(session, "user", "local-user")


# --------------------------------------------------------------------------- 路由


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def login(request: web.Request) -> web.Response:
    """登录：口令只在服务端比对，响应只回 csrf_token 与会话 cookie。"""
    context = context_of(request)
    payload = await _json_body(request, LoginRequest)
    try:
        session = context.auth.verify_passphrase(payload.passphrase)
    except AuthError as exc:
        return _error(exc.code, exc.code)
    return web.json_response(
        {"csrf_token": session.csrf_token}, headers=context.auth.set_cookie(session)
    )


async def session_info(request: web.Request) -> web.Response:
    context = context_of(request)
    session = request.get("session") or context.auth.session_for(
        _cookie(request, context.auth)
    )
    return web.json_response({"authenticated": True, "csrf_token": session.csrf_token})


async def logout(request: web.Request) -> web.Response:
    context = context_of(request)
    context.auth.logout(_cookie(request, context.auth))
    return web.Response(status=204, headers=context.auth.clear_cookie())


async def list_suites(request: web.Request) -> web.Response:
    context = context_of(request)
    return web.json_response({"data": context.repository.list_suites()})


async def create_suite(request: web.Request) -> web.Response:
    context = context_of(request)
    body = await _json_body(request, SuiteWrite)
    return web.json_response(
        context.repository.create_suite(body.model_dump(mode="json")), status=201
    )


async def list_cases(request: web.Request) -> web.Response:
    context = context_of(request)
    suite_id = request.query.get("suite_id")
    if not suite_id:
        return web.json_response({"data": []})
    return web.json_response({"data": context.repository.list_cases(suite_id)})


async def create_case(request: web.Request) -> web.Response:
    context = context_of(request)
    body = await _json_body(request, CaseWrite)
    return web.json_response(
        context.repository.save_case(body.model_dump(mode="json")), status=201
    )


async def get_case(request: web.Request) -> web.Response:
    """单条用例的完整定义：判据编辑页要看到 evaluation、assertions 与量表。"""
    context = context_of(request)
    return web.json_response(
        context.repository.get_case(request.match_info["id"])
    )


async def update_case(request: web.Request) -> web.Response:
    """保存用例判据：走 base_revision 的 CAS，落库即冻结一份新 revision。"""
    context = context_of(request)
    body = await _json_body(request, CaseWrite)
    return web.json_response(
        context.repository.save_case(
            body.model_dump(mode="json"), case_id=request.match_info["id"]
        )
    )


async def create_run(request: web.Request) -> web.Response:
    """入队后立即 202：客户端断开不会取消运行。"""
    context = context_of(request)
    key = request.headers.get("Idempotency-Key")
    if not key:
        _fail("VALIDATION_ERROR", "缺少 Idempotency-Key")
    body = await _json_body(request, RunCreate)
    run = context.service.create_run(body, idempotency_key=key, scope=_scope(request))
    return web.json_response(
        context.service.run_read(run["id"]),
        status=202,
        headers={"Location": f"{API_PREFIX}/runs/{run['id']}"},
    )


async def create_comparison(request: web.Request) -> web.Response:
    """创建严格比较：不可比时直接报错，不生成记录。"""
    context = context_of(request)
    key = request.headers.get("Idempotency-Key")
    if not key:
        _fail("VALIDATION_ERROR", "缺少 Idempotency-Key")
    body = await _json_body(request, ComparisonCreate)
    result = context.service.create_comparison(
        body, idempotency_key=key, scope=_scope(request)
    )
    return web.json_response(
        result,
        status=201,
        headers={"Location": f"{API_PREFIX}/comparisons/{result.get('id')}"},
    )


async def get_comparison(request: web.Request) -> web.Response:
    context = context_of(request)
    return web.json_response(context.service.comparison_read(request.match_info["id"]))


async def get_comparison_report(request: web.Request) -> web.Response:
    context = context_of(request)
    body = context.service.comparison_report(request.match_info["id"])
    return web.Response(
        text=body, content_type="text/html", charset="utf-8"
    )


async def get_run_metrics(request: web.Request) -> web.Response:
    """v2 指标读模型：metrics_version、指标、覆盖度与统计范围。"""
    context = context_of(request)
    run_id = request.match_info["id"]
    detail = context.repository.get_run(run_id)
    attempts = context.repository.list_run_attempts(run_id)
    metrics = aggregate_run(
        [normalize_attempt_for_metrics(item) for item in attempts],
        len(detail["attempts"]),
    )
    coverages = [
        dict((item.get("result") or {}).get("coverage") or {})
        for item in detail["attempts"]
    ]
    snapshot = detail.get("snapshot") or {}
    return web.json_response(
        {
            "metrics_version": 2,
            "metrics": metrics,
            "coverage": summarize_coverage(coverages),
            "scope": {
                "run_id": run_id,
                "case_count": len(detail["attempts"]),
                "planned_trials": len(detail["attempts"]),
                "valid_trials": sum(
                    1
                    for item in attempts
                    if str(item.get("verdict") or "") in ("passed", "failed")
                ),
                "repetitions": int(snapshot.get("repetitions") or 1),
                "model_mode": str(snapshot.get("model_mode") or "real"),
            },
        }
    )


async def get_run(request: web.Request) -> web.Response:
    context = context_of(request)
    return web.json_response(context.service.run_read(request.match_info["id"]))


async def list_runs(request: web.Request) -> web.Response:
    context = context_of(request)
    rows = context.repository.list_runs(
        status=request.query.get("status"),
        suite_id=request.query.get("suite_id"),
        parent_run_id=request.query.get("parent_run_id"),
    )
    return web.json_response({"data": [context.service.run_summary(row["id"]) for row in rows]})


async def list_run_attempts(request: web.Request) -> web.Response:
    context = context_of(request)
    rows = context.repository.list_run_attempts(
        request.match_info["id"],
        verdict=request.query.get("verdict"),
        execution_status=request.query.get("execution_status"),
        grading_status=request.query.get("grading_status"),
    )
    return web.json_response({"data": rows})


async def cancel_run(request: web.Request) -> web.Response:
    context = context_of(request)
    await _json_body(request, EmptyBody)
    run = await context.scheduler.cancel_run(request.match_info["id"])
    return web.json_response(context.service.run_read(run["id"]), status=202)


async def rerun_run(request: web.Request) -> web.Response:
    """重跑同运行的终态尝试：输入引用原冻结快照，不重新读当前用例正文。"""
    context = context_of(request)
    key = request.headers.get("Idempotency-Key")
    if not key:
        _fail("VALIDATION_ERROR", "缺少 Idempotency-Key")
    body = await _json_body(request, RerunCreate)
    run = context.service.rerun(
        request.match_info["id"],
        [str(item) for item in body.attempt_ids],
        idempotency_key=key,
        scope=_scope(request),
    )
    return web.json_response(
        context.service.run_read(run["id"]),
        status=202,
        headers={"Location": f"{API_PREFIX}/runs/{run['id']}"},
    )


async def get_attempt(request: web.Request) -> web.Response:
    context = context_of(request)
    return web.json_response(context.service.attempt_read(request.match_info["id"]))


async def get_trace(request: web.Request) -> web.Response:
    context = context_of(request)
    try:
        after_seq = int(request.query.get("after_seq", "0"))
        limit = int(request.query.get("limit", "200"))
    except ValueError as exc:
        _fail("VALIDATION_ERROR", "after_seq/limit 必须是整数", exc)
    if after_seq < 0 or not 1 <= limit <= 200:
        _fail("VALIDATION_ERROR", "after_seq/limit 超出范围")
    return web.json_response(
        context.service.trace_page(request.match_info["id"], after_seq=after_seq, limit=limit)
    )


async def get_execution(request: web.Request) -> web.Response:
    """执行图分页。``after_seq`` 严格递增，旧 Trace 缺身份时 coverage 会降级。"""
    context = context_of(request)
    try:
        after_seq = int(request.query.get("after_seq", "0"))
        limit = int(request.query.get("limit", "200"))
    except ValueError as exc:
        _fail("VALIDATION_ERROR", "after_seq/limit 必须是整数", exc)
    if after_seq < 0 or not 1 <= limit <= 1000:
        _fail("VALIDATION_ERROR", "after_seq/limit 超出范围")
    return web.json_response(
        context.service.execution_page(
            request.match_info["id"], after_seq=after_seq, limit=limit
        )
    )


async def list_tool_calls(request: web.Request) -> web.Response:
    """工具调用分页。``q`` 只匹配工具名、调用 ID 与智能体名称。"""
    context = context_of(request)
    try:
        limit = int(request.query.get("limit", "50"))
    except ValueError as exc:
        _fail("VALIDATION_ERROR", "limit 必须是整数", exc)
    if not 1 <= limit <= 200:
        _fail("VALIDATION_ERROR", "limit 超出范围")
    status = request.query.get("status")
    if status is not None and status not in {
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "unknown",
    }:
        _fail("VALIDATION_ERROR", "status 取值非法")
    try:
        page = context.service.tool_calls_page(
            request.match_info["id"],
            execution_id=request.query.get("execution_id"),
            phase_id=request.query.get("phase_id"),
            name=request.query.get("name"),
            status=status,
            q=request.query.get("q"),
            after_key=request.query.get("after_key"),
            limit=limit,
        )
    except ValueError as exc:
        _fail("VALIDATION_ERROR", "after_key 非法", exc)
    return web.json_response(page)


async def get_run_usage(request: web.Request) -> web.Response:
    """整轮 Token 主账本；``totals`` 覆盖全轮，不是当前页求和。"""
    context = context_of(request)
    group_by = request.query.get("group_by", "role")
    if group_by not in {"role", "agent", "model"}:
        _fail("VALIDATION_ERROR", "group_by 取值非法")
    try:
        limit = int(request.query.get("limit", "50"))
    except ValueError as exc:
        _fail("VALIDATION_ERROR", "limit 必须是整数", exc)
    if not 1 <= limit <= 200:
        _fail("VALIDATION_ERROR", "limit 超出范围")
    try:
        page = context.service.run_usage_page(
            request.match_info["id"],
            group_by=group_by,
            after_key=request.query.get("after_key"),
            limit=limit,
        )
    except ValueError as exc:
        _fail("VALIDATION_ERROR", "after_key 非法", exc)
    return web.json_response(page)


async def get_run_report(request: web.Request) -> web.Response:
    """单次运行的离线 HTML 报告：自包含静态页面，无脚本、无外链。"""
    from nanobot.evaluation.report_view import render_run_report

    context = context_of(request)
    run_id = request.match_info["id"]
    snapshot = context.service.run_report_snapshot(run_id)
    html = render_run_report(snapshot)
    filename = _safe_report_filename(run_id)
    return web.Response(
        text=html,
        content_type="text/html",
        charset="utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def _safe_report_filename(run_id: str) -> str:
    """文件名只由运行 ID 派生：用户输入不得进入 Content-Disposition。"""
    cleaned = "".join(
        char for char in run_id if char.isalnum() or char in {".", "_", "-"}
    )
    return f"{cleaned[:64] or 'run'}-report.html"


async def add_review(request: web.Request) -> web.Response:
    context = context_of(request)
    body = await _json_body(request, ManualReviewCreate)
    review = context.repository.append_review(
        request.match_info["id"],
        verdict=body.verdict,
        comment=body.comment,
        reviewer=_scope(request),
    )
    return web.json_response(review, status=201)


async def get_artifact(request: web.Request) -> web.StreamResponse:
    context = context_of(request)
    try:
        artifact = resolve_artifact(
            context.repository, request.match_info["id"],
            runtime_root=context.runtime_root,
        )
    except ArtifactError as exc:
        return _error(exc.code, exc.code)
    return web.FileResponse(
        artifact.path,
        headers=attachment_headers(artifact),
    )


async def export_run(request: web.Request) -> web.StreamResponse:
    context = context_of(request)
    fmt = request.query.get("format", "json")
    if fmt not in ("json", "csv"):
        _fail("VALIDATION_ERROR", "format 只能是 json 或 csv")
    body, media, filename = context.service.export_run(request.match_info["id"], fmt=fmt)
    return web.Response(
        body=body.encode("utf-8"),
        content_type=media.split(";")[0],
        charset="utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


async def get_overview(request: web.Request) -> web.Response:
    return web.json_response(context_of(request).service.overview())


async def get_profiles(request: web.Request) -> web.Response:
    data = context_of(request).service.list_profiles()
    return web.json_response({"data": data, "next_cursor": None})


async def import_suite(request: web.Request) -> web.Response:
    context = context_of(request)
    body = await _json_body(request, ImportSuiteRequest)
    return web.json_response(
        context.repository.import_suite(body.model_dump(mode="json")), status=201
    )


async def import_sample(request: web.Request) -> web.Response:
    context = context_of(request)
    body = await _json_body(request, SampleImportRequest)
    result = context.service.import_sample(body.sample_id)
    return web.json_response(result, status=201)


async def list_calibration_samples(request: web.Request) -> web.Response:
    return web.json_response(context_of(request).service.list_gold_samples())


async def review_calibration_sample(request: web.Request) -> web.Response:
    context = context_of(request)
    body = await _json_body(request, GoldReview)
    return web.json_response(
        context.repository.review_gold_sample(
            request.match_info["id"], body.model_dump(mode="json")
        )
    )


async def create_calibration(request: web.Request) -> web.Response:
    context = context_of(request)
    key = request.headers.get("Idempotency-Key")
    if not key:
        _fail("VALIDATION_ERROR", "缺少 Idempotency-Key")
    body = await _json_body(request, CalibrationCreate)
    if body.gold_revision != context.repository.gold_revision():
        return _error("REVISION_CONFLICT", "gold 版本已变化")
    confirmed = [
        row for row in context.repository.list_gold_samples()["data"]
        if row["label_status"] == "confirmed" and row["id"] in set(body.sample_ids)
    ]
    if len(confirmed) < len(body.sample_ids):
        return _error("CALIBRATION_GOLD_UNCONFIRMED", f"已确认 {len(confirmed)}/{len(body.sample_ids)}")
    job = context.repository.create_calibration(
        snapshot={
            "judge_profile_id": body.judge_profile_id,
            "gold_revision": body.gold_revision,
            "sample_ids": list(body.sample_ids),
        },
        sample_ids=body.sample_ids,
    )
    return web.json_response(
        _calibration_read(context, job["id"]), status=202,
        headers={"Location": f"{API_PREFIX}/calibrations/{job['id']}"},
    )


async def get_calibration(request: web.Request) -> web.Response:
    context = context_of(request)
    return web.json_response(_calibration_read(context, request.match_info["id"]))


async def cancel_calibration(request: web.Request) -> web.Response:
    context = context_of(request)
    await _json_body(request, EmptyBody)
    context.repository.cancel_calibration(request.match_info["id"])
    return web.json_response(
        _calibration_read(context, request.match_info["id"]), status=202
    )


# ------------------------------------------------------------------- 数据集与报告


_XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


async def dataset_template(request: web.Request) -> web.Response:
    from nanobot.evaluation.datasets import build_template_bytes

    return web.Response(
        body=build_template_bytes(),
        content_type=_XLSX_CONTENT_TYPE,
        headers={
            "Content-Disposition": 'attachment; filename="evaluation-dataset-template.xlsx"',
            "X-Content-Type-Options": "nosniff",
        },
    )


async def import_dataset(request: web.Request) -> web.Response:
    """multipart 上传 xlsx：全部合法才建 suite+cases，逐行错误随 201/422 返回。"""
    from nanobot.evaluation.datasets import parse_workbook

    context = context_of(request)
    reader = await request.multipart()
    data = b""
    while part := await reader.next():
        if part.name == "file":
            while chunk := await part.read_chunk(65536):
                data += chunk
                if len(data) > MAX_BODY_BYTES:
                    _fail("PAYLOAD_TOO_LARGE", "Excel 超过 5MB")
            break
    if not data:
        _fail("VALIDATION_ERROR", "缺少 file 字段")
    parsed = parse_workbook(data)
    if not parsed["cases"]:
        return web.json_response(
            {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "没有可用用例",
                },
                "rows": parsed["errors"],
            },
            status=422,
        )
    suite = context.repository.create_suite(parsed["suite"])
    saved = [
        context.repository.save_case({**case, "suite_id": suite["id"]})
        for case in parsed["cases"]
    ]
    return web.json_response(
        {"suite": suite, "case_count": len(saved), "errors": parsed["errors"]},
        status=201,
    )


async def export_suite_xlsx(request: web.Request) -> web.Response:
    from nanobot.evaluation.datasets import export_suite_workbook

    context = context_of(request)
    suite = context.repository.get_suite(request.match_info["id"])
    cases = context.repository.list_cases(str(suite["id"]))
    return web.Response(
        body=export_suite_workbook(suite, cases),
        content_type=_XLSX_CONTENT_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="dataset-{suite["id"]}.xlsx"',
            "X-Content-Type-Options": "nosniff",
        },
    )


async def summary_report_route(request: web.Request) -> web.Response:
    context = context_of(request)
    suite_id = request.query.get("suite_id", "")
    if not suite_id:
        _fail("VALIDATION_ERROR", "缺少 suite_id")
    html = context.service.summary_report(suite_id)
    return web.Response(
        text=html,
        content_type="text/html",
        charset="utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="evaluation-summary-{suite_id[:8]}.html"'
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


def _calibration_read(context: ServiceContext, job_id: str) -> dict[str, Any]:
    detail = context.repository.get_calibration(job_id)
    result = detail.get("result") or {}
    snapshot = detail.get("snapshot") or {}
    return {
        "id": detail["id"],
        "status": detail["status"],
        "judge_fingerprint": str(result.get("judge_fingerprint", "")),
        "gold_revision": int(snapshot.get("gold_revision", 0)),
        "total": int(detail["total"]),
        "completed": int(detail["completed"]),
        "matched": int(result.get("matched", 0)),
        "agreement": result.get("agreement"),
        "passed": result.get("passed"),
        "created_at": detail["created_at"],
        "finished_at": detail["finished_at"],
        "error": result.get("error"),
    }


# --------------------------------------------------------------------------- 装配


class LoginRequest(JsonModel):
    """登录体只认 passphrase；多余字段按 extra="forbid" 直接拒。"""

    passphrase: Annotated[StrictStr, Field(min_length=1, max_length=512)]


def create_app(
    *,
    repository: EvaluationRepository,
    service: EvaluationService,
    auth_config: AuthConfig,
    scheduler_config: SchedulerConfig,
    profiles: tuple[Any, ...] = (),
) -> web.Application:
    """装配应用；``cleanup_ctx`` 负责逆序回收。"""
    auth = AuthManager(auth_config)
    scheduler = Scheduler(
        repository=repository, pipeline=service, config=scheduler_config
    )
    context = ServiceContext(
        repository=repository, service=service, scheduler=scheduler, auth=auth
    )
    del profiles

    cors_origins = set(auth_config.allowed_origins)
    if auth_config.dev_origin:
        cors_origins.add(auth_config.dev_origin)
    app = web.Application(
        middlewares=[_cors_middleware(frozenset(cors_origins)), guard],
        client_max_size=MAX_BODY_BYTES,
    )
    app["eval_context"] = context
    routes = _routes()
    app.add_routes(routes)
    # 兜底路由最后注册：aiohttp 按注册顺序匹配，未匹配的请求才会落到这里。
    app.router.add_route("*", "/{tail:.*}", _fallback_handler(routes))

    async def _lifecycle(app: web.Application) -> AsyncIterator[None]:
        # 进入时先写内置 gold 建议标签（仅空表，重启不覆盖用户确认结果），再起调度器
        # （它持服务独占锁），退出时 aiohttp 自动逆序回收。
        service.seed_builtin_gold_samples()
        await scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()

    app.cleanup_ctx.append(_lifecycle)
    return app


def _routes() -> list[web.RouteDef]:
    def r(method: str, path: str, handler: Handler) -> web.RouteDef:
        return web.route(method, f"{API_PREFIX}{path}", handler)

    return [
        r("GET", "/health", health),
        r("POST", "/auth/login", login),
        r("GET", "/auth/session", session_info),
        r("POST", "/auth/logout", logout),
        r("GET", "/profiles", get_profiles),
        r("GET", "/suites", list_suites),
        r("POST", "/suites", create_suite),
        r("POST", "/suites/import", import_suite),
        r("GET", "/cases", list_cases),
        r("POST", "/cases", create_case),
    r("GET", "/cases/{id}", get_case),
    r("PATCH", "/cases/{id}", update_case),
        r("POST", "/samples/import", import_sample),
        r("GET", "/calibration-samples", list_calibration_samples),
        r("PATCH", "/calibration-samples/{id}", review_calibration_sample),
        r("POST", "/calibrations", create_calibration),
        r("GET", "/calibrations/{id}", get_calibration),
        r("POST", "/calibrations/{id}/cancel", cancel_calibration),
        r("GET", "/overview", get_overview),
        r("POST", "/runs", create_run),
        r("GET", "/runs", list_runs),
        r("GET", "/runs/{id}", get_run),
        r("GET", "/runs/{id}/metrics", get_run_metrics),
        r("POST", "/comparisons", create_comparison),
        r("GET", "/comparisons/{id}", get_comparison),
        r("GET", "/comparisons/{id}/report", get_comparison_report),
        r("POST", "/runs/{id}/cancel", cancel_run),
        r("POST", "/runs/{id}/rerun", rerun_run),
        r("GET", "/runs/{id}/attempts", list_run_attempts),
        r("GET", "/runs/{id}/export", export_run),
        r("GET", "/runs/{id}/usage", get_run_usage),
        r("GET", "/runs/{id}/report", get_run_report),
        r("GET", "/attempts/{id}", get_attempt),
        r("GET", "/attempts/{id}/trace", get_trace),
        r("GET", "/attempts/{id}/execution", get_execution),
        r("GET", "/attempts/{id}/tool-calls", list_tool_calls),
        r("POST", "/attempts/{id}/reviews", add_review),
        r("GET", "/artifacts/{id}", get_artifact),
        r("GET", "/datasets/template", dataset_template),
        r("POST", "/datasets/import", import_dataset),
        r("GET", "/suites/{id}/export.xlsx", export_suite_xlsx),
        r("GET", "/reports/summary", summary_report_route),
    ]


def create_eval_app(
    *,
    data_dir: Any,
    passphrase: str,
    allowed_hosts: frozenset[str] = frozenset({"127.0.0.1", "localhost"}),
    allowed_origins: frozenset[str] = frozenset(),
    dev_origin: str | None = None,
    profiles: tuple[Any, ...] = (),
    team_runner: Any | None = None,
) -> web.Application:
    """CLI/网关入口用的便捷装配：库、服务、调度器一次到位。"""
    from pathlib import Path

    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    database = EvaluationDatabase(root / "evaluation.db")
    database.init_schema()
    repository = EvaluationRepository(database)
    service = EvaluationService(
        repository=repository, data_dir=root, profiles=profiles,
        team_runner=team_runner,
    )
    return create_app(
        repository=repository,
        service=service,
        auth_config=AuthConfig(
            passphrase=passphrase,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            dev_origin=dev_origin,
        ),
        scheduler_config=SchedulerConfig(data_dir=root),
        profiles=profiles,
    )
