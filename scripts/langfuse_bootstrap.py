"""Create the Langfuse score configs and annotation queue used by Zhixu.

Idempotent: safe to run repeatedly against the local self-hosted instance.
"""
from __future__ import annotations

import os
import sys
from typing import Any

import httpx

BASE = os.environ.get("LANGFUSE_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
QUEUE_NAME = os.environ.get("LANGFUSE_ANNOTATION_QUEUE", "zhixu-review")
DATASET_NAME = os.environ.get("LANGFUSE_DATASET", "zhixu-evals")

SCORE_CONFIGS: list[dict[str, Any]] = [
    {"name": "user_feedback", "dataType": "BOOLEAN", "description": "用户点赞/点踩"},
    {
        "name": "judge_quality",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "LLM 评审：回答质量 0-1",
    },
    {
        "name": "human_quality",
        "dataType": "NUMERIC",
        "minValue": 1,
        "maxValue": 5,
        "description": "人工复核：回答质量 1-5",
    },
    {
        "name": "human_failure_type",
        "dataType": "CATEGORICAL",
        "description": "人工复核：失败类型",
        "categories": [
            {"value": 0, "label": "retrieval"},
            {"value": 1, "label": "model"},
            {"value": 2, "label": "tool"},
            {"value": 3, "label": "other"},
        ],
    },
]


def _auth() -> tuple[str, str]:
    public = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public or not secret:
        raise SystemExit("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are required")
    return public, secret


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE, auth=_auth(), timeout=15.0)


def _field(item: dict[str, Any], camel: str, snake: str) -> Any:
    return item.get(camel, item.get(snake))


def ensure_score_config(client: httpx.Client, spec: dict[str, Any]) -> str:
    name = spec["name"]
    response = client.get("/api/public/score-configs", params={"name": name, "limit": 100})
    response.raise_for_status()
    for item in response.json().get("data", []):
        if item.get("name") == name:
            print(f"score-config exists: {name} ({item['id']})")
            return str(item["id"])
    response = client.post("/api/public/score-configs", json=spec)
    response.raise_for_status()
    created = response.json()
    print(f"score-config created: {name} ({created['id']})")
    return str(created["id"])


def ensure_queue(client: httpx.Client, score_config_ids: list[str]) -> str:
    response = client.get("/api/public/annotation-queues", params={"limit": 100})
    response.raise_for_status()
    for item in response.json().get("data", []):
        if item.get("name") == QUEUE_NAME:
            print(f"annotation-queue exists: {QUEUE_NAME} ({item['id']})")
            return str(item["id"])
    response = client.post(
        "/api/public/annotation-queues",
        json={
            "name": QUEUE_NAME,
            "description": "知序人工复核队列",
            "scoreConfigIds": score_config_ids,
        },
    )
    response.raise_for_status()
    created = response.json()
    print(f"annotation-queue created: {QUEUE_NAME} ({created['id']})")
    return str(created["id"])


def ensure_dataset(client: httpx.Client) -> None:
    response = client.get(f"/api/public/datasets/{DATASET_NAME}")
    if response.status_code == 200:
        print(f"dataset exists: {DATASET_NAME}")
        return
    response = client.post(
        "/api/public/datasets",
        json={"name": DATASET_NAME, "description": "知序评估数据集"},
    )
    response.raise_for_status()
    print(f"dataset created: {DATASET_NAME}")


def main() -> int:
    with _client() as client:
        ids = {spec["name"]: ensure_score_config(client, spec) for spec in SCORE_CONFIGS}
        queue_id = ensure_queue(
            client,
            [ids["human_quality"], ids["human_failure_type"]],
        )
        ensure_dataset(client)
    print()
    print("LANGFUSE_DATASET=" + DATASET_NAME)
    print("LANGFUSE_ANNOTATION_QUEUE_NAME=" + QUEUE_NAME)
    print("LANGFUSE_ANNOTATION_QUEUE_ID=" + queue_id)
    print("score-config ids:", ", ".join(f"{name}={value}" for name, value in ids.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
