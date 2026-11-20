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
