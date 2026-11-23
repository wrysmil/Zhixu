"""Attach Chinese annotations to the registered Langfuse prompts (prompt comments).

Uses the native Langfuse MCP endpoint so comments land on the prompt object
(objectType=PROMPT), next to the version commit messages written by
scripts/langfuse_prompt_sync.py.

Usage:
  LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=... LANGFUSE_BASE_URL=http://127.0.0.1:3000 \
    python scripts/langfuse_prompt_comments.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
from typing import Any

import httpx

PROMPT_TYPE = "PROMPT"


def _mcp_client() -> tuple[httpx.Client, dict[str, str]]:
    base = os.environ.get("LANGFUSE_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
    public = os.environ["LANGFUSE_PUBLIC_KEY"]
    secret = os.environ["LANGFUSE_SECRET_KEY"]
    token = base64.b64encode(f"{public}:{secret}".encode()).decode()
    client = httpx.Client(
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        timeout=30.0,
        follow_redirects=True,
    )
    client.base_url = httpx.URL(base)
    return client, {}


def mcp_call(client: httpx.Client, session_id: str | None, method: str, params: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
    is_notification = method.startswith("notifications/")
    if not is_notification:
        payload["id"] = 1
    headers = {}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    response = client.post("/api/public/mcp", json=payload, headers=headers)
    response.raise_for_status()
    if is_notification:
        return {}
    body = response.json()
    if "error" in body:
        raise RuntimeError(body["error"])
    return body.get("result", {})


def list_prompt_names(client: httpx.Client, session_id: str | None) -> list[str]:
    names: list[str] = []
    page = 1
    while True:
        result = mcp_call(
            client,
            session_id,
            "tools/call",
            {"name": "listPrompts", "arguments": {"page": page, "limit": 100}},
        )
        payload = _tool_json(result)
        for item in payload.get("data", []):
            name = item.get("name", "")
            if name.startswith("zhixu/"):
                names.append(name)
        meta = payload.get("meta", {})
        if page >= meta.get("totalPages", 1):
            break
        page += 1
    return names


def _tool_json(result: dict[str, Any]) -> dict[str, Any]:
    content = result.get("content") or []
    if content and isinstance(content[0], dict):
        try:
            return json.loads(content[0].get("text") or "{}")
        except (ValueError, TypeError):
            return {}
    return {}


def comment_text(config: dict[str, Any]) -> str:
    description = config.get("description") or "知序系统提示词片段"
    used_in = config.get("usedIn") or "system prompt"
    source = config.get("source") or "nanobot/templates"
    return (
        f"【用途】{description}\n"
        f"【装配位置】{used_in}\n"
        f"【来源】{source}\n"
        f"【同步方式】scripts/langfuse_prompt_sync.py：--check 只读比对本地与线上，"
        f"--sync 生成新版本并移动 production 标签；线上改动不会自动生效，"
        f"需知序接入运行时拉取后才会影响对话。"
    )


def main() -> int:
    client, _ = _mcp_client()
    init = client.post(
        "/api/public/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "zhixu-prompt-comments", "version": "1.0"},
            },
        },
    )
    init.raise_for_status()
    session_id = init.headers.get("mcp-session-id")
    mcp_call(client, session_id, "notifications/initialized", {})

    names = list_prompt_names(client, session_id)
    if not names:
        print("no zhixu prompts found; run scripts/langfuse_prompt_sync.py --sync first")
        return 1

    created = 0
    skipped = 0
    for name in names:
        prompt = _tool_json(
            mcp_call(
                client,
                session_id,
                "tools/call",
                {
                    "name": "getPrompt",
                    "arguments": {"name": name, "label": "production"},
                },
            )
        )
        prompt_id = prompt.get("id")
        if not prompt_id:
            print(f"skip {name}: no id")
            continue
        listed = _tool_json(
            mcp_call(
                client,
                session_id,
                "tools/call",
                {
                    "name": "listComments",
                    "arguments": {
                        "objectType": PROMPT_TYPE,
                        "objectId": prompt_id,
                        "page": 1,
                        "limit": 10,
                    },
                },
            )
        )
        if listed.get("data"):
            skipped += 1
            continue
        mcp_call(
            client,
            session_id,
            "tools/call",
            {
                "name": "createComment",
                "arguments": {
                    "objectType": PROMPT_TYPE,
                    "objectId": prompt_id,
                    "content": comment_text(prompt.get("config") or {}),
                },
            },
        )
        created += 1
        print(f"commented {name}")

    print(f"\nprompts={len(names)} comments_created={created} skipped_existing={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
