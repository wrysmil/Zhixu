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
    "COMPARISON_NOT_FOUND": 404,
    "COMPARISON_RUN_NOT_TERMINAL": 409,
    "SKILL_NOT_FOUND": 404,
    "AGENT_NOT_FOUND": 404,
    "TARGET_TOO_LARGE": 413,
    "TARGET_PATH_ESCAPE": 422,
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
    "COMPARISON_METADATA_MISSING": 422,
    "COMPARISON_INCOMPATIBLE": 409,
    "PAYLOAD_TOO_LARGE": 413,
    "UNSUPPORTED_MEDIA_TYPE": 415,
    "METHOD_NOT_ALLOWED": 405,
    "NOT_FOUND": 404,
    # ----- 记忆专项（契约 §6 错误表） -----
    # 资源不存在：四个专项资源各有自己的 404 码，前端才能分辨该刷新哪一页。
    "MEMORY_DATASET_NOT_FOUND": 404,
    "MEMORY_SCENARIO_NOT_FOUND": 404,
    "MEMORY_EXPERIMENT_NOT_FOUND": 404,
    "MEMORY_IMPORT_NOT_FOUND": 404,
    "MEMORY_COMPARISON_NOT_FOUND": 404,
    # 状态冲突：能读到资源，但当前状态不允许这个动作。
    "MEMORY_DATASET_NOT_READY": 409,
    "MEMORY_EXPERIMENT_NOT_TERMINAL": 409,
    "MEMORY_GRADING_REVISION_CONFLICT": 409,
    "MEMORY_IMPORT_COUNT_MISMATCH": 409,
    # 限额：单文件/单场景/条数超上限。
    "MEMORY_IMPORT_TOO_LARGE": 413,
    # 输入不合法（契约 §1/§2/§5 的各类门禁）。
    "MEMORY_SCHEMA_INVALID": 422,
    "MEMORY_EVIDENCE_INVALID": 422,
    "MEMORY_BUDGET_EXCEEDED": 422,
    "MEMORY_MODE_CONFLICT": 422,
    "MEMORY_TARGET_UNSUPPORTED": 422,
    "UNSUPPORTED_MEMORY_SETTING": 422,
    "MEMORY_CAPABILITY_REQUIRED": 422,
    "MEMORY_SOURCE_HASH_MISMATCH": 422,
    "MEMORY_SPLIT_LEAK": 422,
    "MEMORY_SCENARIO_REF_REQUIRED": 422,
    "MEMORY_RESULT_MISSING": 422,
    "MEMORY_RESULT_INVALID": 422,
    "MEMORY_CANDIDATES_INVALID": 422,
    "MEMORY_CANDIDATES_DUPLICATED": 422,
    "MEMORY_CANDIDATE_UNKNOWN": 422,
    "MEMORY_REPETITIONS_INVALID": 422,
    "MEMORY_SCENARIOS_EMPTY": 422,
    "MEMORY_SCENARIO_ID_MISMATCH": 422,
    "MEMORY_TRACK_UNSUPPORTED": 422,
    "TEMPLATE_FAMILY_CROSS_SPLIT": 422,
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
        "MEMORY_DATASET_NOT_FOUND": "记忆集合修订不存在",
        "MEMORY_SCENARIO_NOT_FOUND": "记忆场景不存在",
        "MEMORY_EXPERIMENT_NOT_FOUND": "记忆实验不存在",
        "MEMORY_IMPORT_NOT_FOUND": "记忆集合导入作业不存在",
        "MEMORY_COMPARISON_NOT_FOUND": "记忆比较不存在",
        "MEMORY_DATASET_NOT_READY": "集合修订尚未就绪",
        "MEMORY_EXPERIMENT_NOT_TERMINAL": "实验尚未进入终态",
        "MEMORY_GRADING_REVISION_CONFLICT": "评分版本已更新，请刷新后重试",
        "MEMORY_IMPORT_TOO_LARGE": "上传内容超过记忆集合限额",
        "MEMORY_BUDGET_EXCEEDED": "实验预算不足",
        "MEMORY_CAPABILITY_REQUIRED": "被测后端缺少该轨道所需能力",
        "UNSUPPORTED_MEMORY_SETTING": "存在无法落到真实配置的记忆参数",
        "MEMORY_SOURCE_HASH_MISMATCH": "上传内容与声明的摘要不一致",
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
