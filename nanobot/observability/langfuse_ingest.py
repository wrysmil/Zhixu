"""Fail-open HTTP client for Langfuse public ingestion APIs."""
from __future__ import annotations

import os
from typing import Any

import httpx
from loguru import logger


def _base_url() -> str | None:
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return None
    return os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com").rstrip("/")


def _auth() -> tuple[str, str]:
    return os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]


async def _post(path: str, payload: dict[str, Any]) -> bool:
    base = _base_url()
    if base is None:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(f"{base}{path}", json=payload, auth=_auth())
            response.raise_for_status()
        return True
    except Exception:
        logger.exception("Langfuse ingestion failed: {}", path)
        return False


async def create_feedback_score(
    *,
    trace_id: str,
    helpful: bool,
    comment: str | None = None,
) -> bool:
    payload: dict[str, Any] = {
        "id": f"zhixu-feedback-{trace_id}",
        "traceId": trace_id,
        "name": os.environ.get("LANGFUSE_FEEDBACK_SCORE", "user_feedback"),
        "value": 1 if helpful else 0,
        "dataType": "BOOLEAN",
    }
    if comment:
        payload["comment"] = comment[:500]
    return await _post("/api/public/scores", payload)


def _message_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("message")
    return str(value or "")


async def fetch_trace_io(trace_id: str) -> tuple[str, str] | None:
    """Return (input message, output message) for a trace.

    Langfuse v4 no longer serves trace input/output from the trace endpoints, so the
    live turn cache is the primary source; the legacy endpoint stays as a fallback.
    """
    from nanobot.observability.langfuse_tracing import lookup_turn_content

    cached = lookup_turn_content(trace_id)
    if cached is not None:
        return cached
    base = _base_url()
    if base is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{base}/api/public/traces/{trace_id}",
                auth=_auth(),
            )
            response.raise_for_status()
            data = response.json()
        return _message_text(data.get("input")), _message_text(data.get("output"))
    except Exception:
        logger.exception("Langfuse trace lookup failed")
        return None


async def add_trace_to_queue(*, queue_id: str, trace_id: str) -> bool:
    return await _post(
        f"/api/public/annotation-queues/{queue_id}/items",
        {"objectId": trace_id, "objectType": "TRACE", "status": "PENDING"},
    )


async def create_dataset_item(
    *,
    dataset_name: str,
    trace_id: str,
    input_text: str,
    expected_output: str,
    metadata: dict[str, Any] | None = None,
) -> bool:
    return await _post(
        "/api/public/dataset-items",
        {
            "datasetName": dataset_name,
            "input": input_text,
            "expectedOutput": expected_output,
            "sourceTraceId": trace_id,
            "metadata": metadata or {},
        },
    )


async def resolve_queue_id(name: str) -> str | None:
    base = _base_url()
    if base is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{base}/api/public/annotation-queues",
                params={"limit": 100},
                auth=_auth(),
            )
            response.raise_for_status()
            for item in response.json().get("data", []):
                if item.get("name") == name:
                    return item.get("id")
    except Exception:
        logger.exception("Langfuse annotation queue lookup failed")
    return None
