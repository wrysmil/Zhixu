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
from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.errors import code_from_exception, error_body, status_for
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.scheduler import Scheduler, SchedulerConfig
from nanobot.evaluation.schemas import (
    CalibrationCreate,
    CaseWrite,
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

    app = web.Application(middlewares=[guard], client_max_size=MAX_BODY_BYTES)
    app["eval_context"] = context
    routes = _routes()
    app.add_routes(routes)
    # 兜底路由最后注册：aiohttp 按注册顺序匹配，未匹配的请求才会落到这里。
    app.router.add_route("*", "/{tail:.*}", _fallback_handler(routes))

    async def _lifecycle(app: web.Application) -> AsyncIterator[None]:
        # 进入时先起调度器（它持服务独占锁），退出时 aiohttp 自动逆序回收。
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
        r("POST", "/runs/{id}/cancel", cancel_run),
        r("POST", "/runs/{id}/rerun", rerun_run),
        r("GET", "/runs/{id}/attempts", list_run_attempts),
        r("GET", "/runs/{id}/export", export_run),
        r("GET", "/attempts/{id}", get_attempt),
        r("GET", "/attempts/{id}/trace", get_trace),
        r("POST", "/attempts/{id}/reviews", add_review),
        r("GET", "/artifacts/{id}", get_artifact),
    ]


def create_eval_app(
    *,
    data_dir: Any,
    passphrase: str,
    allowed_hosts: frozenset[str] = frozenset({"127.0.0.1", "localhost"}),
    dev_origin: str | None = None,
    profiles: tuple[Any, ...] = (),
) -> web.Application:
    """CLI 入口用的便捷装配：库、服务、调度器一次到位。"""
    from pathlib import Path

    root = Path(data_dir)
    database = EvaluationDatabase(root / "evaluation.db")
    database.init_schema()
    repository = EvaluationRepository(database)
    service = EvaluationService(
        repository=repository, data_dir=root, profiles=profiles
    )
    return create_app(
        repository=repository,
        service=service,
        auth_config=AuthConfig(
            passphrase=passphrase,
            allowed_hosts=allowed_hosts,
            dev_origin=dev_origin,
        ),
        scheduler_config=SchedulerConfig(data_dir=root),
        profiles=profiles,
    )
