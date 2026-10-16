"""HTTP 错误码到响应体的唯一映射点。

契约 §1 的约定：**错误响应只回白名单字段**。本模块是那张白名单的定义处——路由层
不允许自己拼错误体，否则迟早有人把内部异常消息（可能带路径或配置片段）透出去。

仓储与判分层抛的是错误码字符串，这里把码映射成状态码；未登记的码一律按 500 处理
且**不回显原文**，避免「忘记登记的错误码」变成信息泄露通道。
"""

from __future__ import annotations

from typing import Any

#: 错误码 → HTTP 状态码。未列出的码一律 500。
ERROR_STATUS: dict[str, int] = {
    # 认证与会话
    "AUTH_REQUIRED": 401,
    "AUTH_INVALID": 401,
    "AUTH_FAILED": 401,
    "CSRF_INVALID": 403,
    "ORIGIN_NOT_ALLOWED": 403,
    "HOST_NOT_ALLOWED": 403,
    "RATE_LIMITED": 429,
    # 资源与状态
    "SUITE_NOT_FOUND": 404,
    "CASE_NOT_FOUND": 404,
    "RUN_NOT_FOUND": 404,
    "ATTEMPT_NOT_FOUND": 404,
    "ARTIFACT_NOT_FOUND": 404,
    "ARTIFACT_TOO_LARGE": 413,
    "CALIBRATION_NOT_FOUND": 404,
    "GOLD_SAMPLE_NOT_FOUND": 404,
    "PROFILE_NOT_FOUND": 404,
    "SAMPLE_NOT_FOUND": 404,
    "RECORD_NOT_FOUND": 404,
    # 状态冲突
    "REVISION_CONFLICT": 409,
    "RUN_NOT_CANCELLABLE": 409,
    "RUN_NOT_TERMINAL": 409,
    "ATTEMPT_NOT_FINISHABLE": 409,
    "IDEMPOTENCY_IN_PROGRESS": 409,
    "ATTEMPT_NOT_RERUNNABLE": 409,
    # 输入不合法
    "IDEMPOTENCY_MISMATCH": 422,
    "PATH_OUTSIDE_ROOT": 422,
    "JUDGE_PROFILE_REQUIRED": 422,
    "CALIBRATION_GOLD_UNCONFIRMED": 422,
    "DUPLICATE_CASE_IDS": 422,
    "DUPLICATE_ATTEMPT_IDS": 422,
    "DUPLICATE_SAMPLE_IDS": 422,
    # 请求体不合契约
    "VALIDATION_ERROR": 422,
    "PAYLOAD_TOO_LARGE": 413,
    "UNSUPPORTED_MEDIA_TYPE": 415,
    "METHOD_NOT_ALLOWED": 405,
    "NOT_FOUND": 404,
}

#: 服务内部故障：这些码**不回显 message**，只给一个固定文案。
INTERNAL_CODES = frozenset({"INTERNAL_ERROR", "WORKER_SETUP_FAILED"})


def status_for(code: str) -> int:
    return ERROR_STATUS.get(code, 500)


def error_body(code: str, message: str = "") -> dict[str, Any]:
    """错误体只含 code / message / 白名单补充字段。

    内部错误一律替换 message：原异常可能带绝对路径、SQL 或配置片段。
    """
    if code in INTERNAL_CODES or code not in ERROR_STATUS:
        return {"error": {"code": "INTERNAL_ERROR", "message": "服务内部错误"}}
    body: dict[str, Any] = {
        "error": {"code": code, "message": message or _default_message(code)}
    }
    return body


def _default_message(code: str) -> str:
    return {
        "AUTH_REQUIRED": "请先登录",
        "CSRF_INVALID": "缺少或无效的 CSRF 令牌",
        "ORIGIN_NOT_ALLOWED": "来源不被允许",
        "RATE_LIMITED": "请求过于频繁，请稍后再试",
    }.get(code, code)


def code_from_exception(exc: BaseException) -> str:
    """把异常归一成错误码。解析不出就当 500，绝不把未知异常当成 4xx 放出去。"""
    # 服务层与产物层自带错误码；码没登记时仍然按 500 处理，不因为「有个 code 属性」就放行。
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in ERROR_STATUS:
        return code
    name = type(exc).__name__
    known = {
        "RevisionConflict": "REVISION_CONFLICT",
        "StateConflict": "RUN_NOT_CANCELLABLE",
        "IdempotencyMismatch": "IDEMPOTENCY_MISMATCH",
        "IdempotencyInProgress": "IDEMPOTENCY_IN_PROGRESS",
        "RecordNotFound": "RECORD_NOT_FOUND",
    }
    if name in known:
        return known[name]
    return "INTERNAL_ERROR"
